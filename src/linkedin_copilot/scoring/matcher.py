from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from ..config import get_settings
from ..db import get_job_full_description, get_match_result, save_match_result
from ..linkedin.extract import scrape_job_descriptions_batch
from ..logging_setup import logger
from ..llm import get_llm
from ..models import JobDetail, JobRecord, MatchResult, UserProfile
from ..utils import save_json, timestamped_filename


def load_profile(path: Path | None = None) -> UserProfile:
    """Load the user profile JSON into a `UserProfile` model."""
    if path is None:
        s = get_settings()
        path = Path(s.env.default_profile_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return UserProfile.model_validate(data)


def quick_filter_job(
    job: JobRecord,
    description: str,
    profile: UserProfile,
    min_skill_matches: int = 2,
) -> tuple[bool, int, List[str]]:
    """
    Fast keyword-based pre-filtering before expensive LLM matching.
    
    Returns:
        tuple of (should_match, skill_count, matched_skills)
        - should_match: True if job passes pre-filter and should be LLM-scored
        - skill_count: Number of profile skills found in job
        - matched_skills: List of matched skill names
    """
    # Combine job title and description for matching
    text = f"{job.title} {job.company} {description}".lower()
    
    # Collect all relevant skills from profile
    all_skills = set()
    all_skills.update(s.lower() for s in profile.top_skills)
    all_skills.update(s.lower() for s in profile.programming_languages)
    all_skills.update(s.lower() for s in profile.frameworks)
    all_skills.update(s.lower() for s in profile.tools)
    all_skills.update(s.lower() for s in profile.cloud_platforms)
    
    # Find matching skills
    matched_skills = []
    for skill in all_skills:
        if skill in text:
            matched_skills.append(skill)
    
    # Check if job title matches any target titles
    title_match = False
    target_titles = profile.target_titles or profile.preferred_titles or []
    for target in target_titles:
        # Fuzzy match: check if key words from target are in job title
        target_words = target.lower().split()
        matches = sum(1 for w in target_words if w in job.title.lower())
        if matches >= len(target_words) * 0.5:  # At least 50% of words match
            title_match = True
            break
    
    # Check seniority match
    seniority_match = True
    job_title_lower = job.title.lower()
    profile_seniority = (profile.seniority_guess or "").lower()
    
    # Avoid junior roles if profile is senior
    if profile_seniority in ("senior", "lead", "staff", "principal"):
        if "junior" in job_title_lower or "entry" in job_title_lower or "intern" in job_title_lower:
            seniority_match = False
    
    # Determine if job should be matched
    should_match = (
        len(matched_skills) >= min_skill_matches or 
        title_match
    ) and seniority_match
    
    return should_match, len(matched_skills), matched_skills


def filter_jobs_for_matching(
    jobs: List[JobRecord],
    profile: UserProfile,
    descriptions: dict[int, str],
    min_skill_matches: int = 2,
) -> tuple[List[JobRecord], List[JobRecord]]:
    """
    Filter jobs using quick keyword matching before LLM scoring.
    
    Args:
        jobs: List of jobs to filter
        profile: User profile with skills
        descriptions: Dict mapping job_id to description text
        min_skill_matches: Minimum number of skill matches required
        
    Returns:
        tuple of (jobs_to_match, jobs_skipped)
    """
    jobs_to_match = []
    jobs_skipped = []
    
    for job in jobs:
        description = descriptions.get(job.id, job.description_snippet or "")
        should_match, skill_count, matched_skills = quick_filter_job(
            job, description, profile, min_skill_matches
        )
        
        if should_match:
            jobs_to_match.append(job)
            logger.debug(
                "Pre-filter PASS: {} @ {} ({} skills: {})",
                job.title, job.company, skill_count, ", ".join(matched_skills[:5])
            )
        else:
            jobs_skipped.append(job)
            logger.debug(
                "Pre-filter SKIP: {} @ {} ({} skills)",
                job.title, job.company, skill_count
            )
    
    logger.info(
        "Pre-filter results: {} jobs to match, {} skipped (of {} total)",
        len(jobs_to_match), len(jobs_skipped), len(jobs)
    )
    
    return jobs_to_match, jobs_skipped


def score_job(detail: JobDetail, profile: UserProfile, resume_path: Path | None = None) -> MatchResult:
    """
    Use LLM to score job fit based on CV/resume only.
    """
    s = get_settings()
    if resume_path is None:
        resume_path = Path(s.env.default_resume_path)
    resume_text = resume_path.read_text(encoding="utf-8") if resume_path.exists() else ""

    description = detail.full_description or detail.job.description_snippet or ""

    llm = get_llm()
    data = llm.score_match(resume_text, description)

    exports_dir = Path(s.data.get("exports_dir", "./data/exports"))
    exports_dir.mkdir(parents=True, exist_ok=True)

    summary_filename = timestamped_filename(f"job_{detail.job.id}_summary", ".md")
    json_filename = timestamped_filename(f"job_{detail.job.id}_match", ".json")
    summary_path = exports_dir / summary_filename
    json_path = exports_dir / json_filename

    top_reasons: List[str] = data.get("top_reasons", [])
    missing: List[str] = data.get("missing_requirements", [])
    inferred: List[str] = data.get("inferred_qualifications", [])
    bullets: List[str] = data.get("suggested_resume_bullets", [])

    summary_md = ["# Job Match Summary", "", f"Job: {detail.job.title} @ {detail.job.company}", ""]
    summary_md.append(f"Match score: **{data.get('match_score', 0)} / 100**")
    summary_md.append("")
    if top_reasons:
        summary_md.append("## Top reasons it matches")
        summary_md.extend(f"- {r}" for r in top_reasons)
        summary_md.append("")
    if inferred:
        summary_md.append("## Inferred qualifications")
        summary_md.extend(f"- {i}" for i in inferred)
        summary_md.append("")
    if missing:
        summary_md.append("## Missing requirements")
        summary_md.extend(f"- {m}" for m in missing)
        summary_md.append("")
    if bullets:
        summary_md.append("## Suggested resume emphasis bullets")
        summary_md.extend(f"- {b}" for b in bullets)
        summary_md.append("")

    summary_path.write_text("\n".join(summary_md), encoding="utf-8")
    save_json(json_path, data)

    result = MatchResult(
        job_id=detail.job.id or 0,
        match_score=int(data.get("match_score", 0)),
        top_reasons=top_reasons,
        missing_requirements=missing,
        inferred_qualifications=inferred,
        suggested_resume_bullets=bullets,
        summary_markdown_path=str(summary_path),
        raw_json_path=str(json_path),
    )
    save_match_result(result)
    logger.info("Saved match result for job {} at {}", detail.job.id, json_path)
    return result


def score_job_from_description(
    job: JobRecord,
    description: str,
    profile: UserProfile,
    resume_path: Path | None = None,
) -> MatchResult:
    """
    Score a job match using just the job record and description text.
    
    This is a simpler version of score_job that doesn't require a full JobDetail.
    Matching is based on CV/resume only.
    """
    s = get_settings()
    if resume_path is None:
        resume_path = Path(s.env.default_resume_path)
    
    if resume_path.exists():
        resume_text = resume_path.read_text(encoding="utf-8")
        logger.debug("Loaded resume from {} ({} chars)", resume_path, len(resume_text))
    else:
        resume_text = ""
        logger.warning("Resume file not found at {}", resume_path)

    llm = get_llm()
    data = llm.score_match(resume_text, description)

    exports_dir = Path(s.data.get("exports_dir", "./data/exports"))
    exports_dir.mkdir(parents=True, exist_ok=True)

    top_reasons: List[str] = data.get("top_reasons", [])
    missing: List[str] = data.get("missing_requirements", [])
    inferred: List[str] = data.get("inferred_qualifications", [])
    bullets: List[str] = data.get("suggested_resume_bullets", [])

    result = MatchResult(
        job_id=job.id or 0,
        match_score=int(data.get("match_score", 0)),
        top_reasons=top_reasons,
        missing_requirements=missing,
        inferred_qualifications=inferred,
        suggested_resume_bullets=bullets,
    )
    save_match_result(result)
    logger.info(
        "Match result for '{}' @ {}: score={}, recommendation={}, inferred={}",
        job.title,
        job.company,
        result.match_score,
        result.recommendation,
        len(inferred),
    )
    return result


async def match_all_jobs(
    jobs: List[JobRecord],
    profile: Optional[UserProfile] = None,
    resume_path: Optional[Path] = None,
) -> List[MatchResult]:
    """
    Match all given jobs against the user's profile and CV.
    
    This function:
    1. Scrapes full descriptions for jobs that don't have them
    2. Runs LLM matching for each job
    3. Returns all match results
    
    Jobs that already have match results are skipped.
    """
    if profile is None:
        profile = load_profile()
    
    s = get_settings()
    if resume_path is None:
        resume_path = Path(s.env.default_resume_path)
    
    # Filter to jobs that need matching
    jobs_to_match: List[JobRecord] = []
    for job in jobs:
        if job.id is None:
            continue
        existing_match = get_match_result(job.id)
        if existing_match is None:
            jobs_to_match.append(job)
    
    if not jobs_to_match:
        logger.info("All {} jobs already have match results", len(jobs))
        return []
    
    logger.info("Matching {} jobs (skipping {} already matched)", len(jobs_to_match), len(jobs) - len(jobs_to_match))
    
    # Scrape descriptions for jobs that need them
    jobs_needing_description = [
        j for j in jobs_to_match
        if j.id and not get_job_full_description(j.id)
    ]
    
    if jobs_needing_description:
        logger.info("Scraping descriptions for {} jobs...", len(jobs_needing_description))
        await scrape_job_descriptions_batch(jobs_needing_description)
    
    # Now run matching for each job
    results: List[MatchResult] = []
    for job in jobs_to_match:
        if job.id is None:
            continue
        
        # Get description (from DB cache or snippet as fallback)
        description = get_job_full_description(job.id) or job.description_snippet or ""
        
        if not description:
            logger.warning("No description available for job {}, skipping match", job.id)
            continue
        
        try:
            result = score_job_from_description(job, description, profile, resume_path)
            results.append(result)
        except Exception as exc:
            logger.error("Failed to match job {}: {}", job.id, exc)
            continue
    
    logger.info("Completed matching: {} results", len(results))
    return results

