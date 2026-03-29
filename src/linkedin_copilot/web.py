from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
import httpx
import base64
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .llm import get_llm_provider_name
from .db import (
    clear_job_descriptions,
    clear_match_results,
    delete_jobs,
    get_all_jobs,
    get_all_match_results,
    get_job_by_id,
    get_job_full_description,
    get_jobs_by_ids,
    get_jobs_missing_logos,
    get_jobs_facets,
    get_jobs_paginated,
    get_match_result,
    get_match_results_for_jobs,
    get_search_history,
    get_search_history_with_effectiveness,
    init_db,
    list_jobs_by_status,
    save_match_result,
    save_search_history,
    search_was_run_recently,
    update_job_description,
    update_job_status,
    update_job_logo,
    # Company CRUD for career sites
    insert_company,
    get_company_by_id,
    get_all_companies,
    update_company,
    update_company_last_scraped,
    get_job_count_by_company,
    careers_job_exists_in_jobs_or_staging,
    delete_company,
    toggle_company_enabled,
    insert_job,
    create_scrape_run,
    insert_staging_job,
    get_runs,
    get_staging_jobs,
    get_run_by_id,
    approve_staging_jobs,
    discard_run,
    get_staging_count_by_company,
    get_pending_jobs_count,
    get_pending_scrape_count,
    get_pending_match_count,
    get_total_staging_jobs_count,
    get_matched_count,
    get_applied_count,
    # Pipeline task queue
    create_task_group_id,
    enqueue_process_pending_tasks,
    enqueue_pipeline_task,
    get_task_group_summary,
    request_cancel_task_group,
)
from .logging_setup import setup_logging, logger
from .models import JobRecord, JobSource, JobStatus, PipelineTaskType
from .scoring.matcher import load_profile, score_job_from_description, filter_jobs_for_matching
from .linkedin.extract import scrape_job_description, _scrape_logo_from_page, download_logo_image
from .utils import ensure_data_dirs, timestamped_filename
from .linkedin.search import search_jobs
from .linkedin.auth import (
    session_exists,
    validate_session,
    interactive_login,
    clear_session,
    get_session_profile_name,
)
from .llm import get_llm
from .pipeline_worker import WorkerHandle, start_pipeline_workers, stop_pipeline_workers
from .search import SuggestionEngine, TavilyWebSearchClient


BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="LinkedIn Copilot Demo", version="0.1.0")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Background task tracking with phase support
_progress_status: Dict[str, Any] = {
    "running": False,
    "phase": None,           # "scraping" | "matching" | None
    "total": 0,
    "completed": 0,
    "current_job": None,
    "current_job_id": None,  # Job ID for UI highlighting (single job)
    "active_jobs": [],       # List of job IDs currently being processed (parallel)
}

# Keep old name for backward compatibility
_matching_status = _progress_status

# Pipeline worker handle (persistent task queue)
_pipeline_worker_handle: Optional[WorkerHandle] = None
_suggestion_engine: Optional[SuggestionEngine] = None


def _web_state_path() -> Path:
    return Path("data/logs/web_state.json")


def _load_web_state() -> Dict[str, Any]:
    path = _web_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_web_state(state: Dict[str, Any]) -> None:
    path = _web_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_suggestion_engine() -> SuggestionEngine:
    global _suggestion_engine
    if _suggestion_engine is None:
        settings = get_settings()
        _suggestion_engine = SuggestionEngine(
            llm_client=get_llm(),
            web_search_client=TavilyWebSearchClient(settings.env.tavily_api_key),
            cache_ttl_minutes=settings.env.suggestion_cache_ttl_minutes,
            suggestion_count=settings.env.suggestion_count,
        )
    return _suggestion_engine


@app.on_event("startup")
async def on_startup() -> None:
    """Initialize data directories, logging, and database when the web app starts."""
    global _pipeline_worker_handle
    ensure_data_dirs()
    setup_logging()
    init_db()
    # Start persistent pipeline workers (scrape/match queue)
    _pipeline_worker_handle = start_pipeline_workers()
    logger.info("FastAPI web demo started.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _pipeline_worker_handle
    await stop_pipeline_workers(_pipeline_worker_handle)
    _pipeline_worker_handle = None


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount logos directory for serving downloaded company logos
LOGOS_DIR = Path("data/logos")
LOGOS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/logos", StaticFiles(directory=str(LOGOS_DIR)), name="logos")

VALID_SEARCH_TABS = frozenset({"search", "explore", "careers"})


@app.get("/search", response_class=RedirectResponse)
async def search_redirect() -> RedirectResponse:
    """Redirect /search to Search tab for canonical URL."""
    return RedirectResponse(url="/?tab=search", status_code=302)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, tab: Optional[str] = None) -> HTMLResponse:
    """Home (dashboard) or Search page. With tab=search|careers render Search; else render dashboard as home."""
    # Map legacy tabs (quick/smart/ai/explore) into the unified "search" tab
    legacy_to_new = {
        "quick": "search",
        "smart": "search",
        "ai": "search",
        "search": "search",
        "explore": "explore",
        "careers": "careers",
    }
    normalized_tab = legacy_to_new.get(tab or "", None)
    search_tab = normalized_tab if normalized_tab in VALID_SEARCH_TABS else None

    if search_tab is not None:
        # Render Search/Careers page (index.html). search_tab is one of search|explore|careers.
        display_tab = search_tab
        state = _load_web_state()
        resume_preview: str = state.get("resume_preview", "")
        last_search: Dict[str, Any] = state.get("last_search", {})
        try:
            profile = load_profile()
        except Exception:
            profile = None
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "resume_preview": resume_preview,
                "last_search": last_search,
                "llm_provider": get_llm_provider_name(),
                "profile": profile,
                "search_tab": display_tab,
                "is_home": False,
            },
        )

    # Render dashboard as the main home page
    data = _dashboard_data()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "pending_scrape": data["pending_scrape"],
            "pending_match": data["pending_match"],
            "jobs_pending": data["jobs_pending"],
            "review_pending": data["review_pending"],
            "matched_count": data["matched_count"],
            "applied_count": data["applied_count"],
            "suggested_action": data["suggested_action"],
            "top_jobs": data["top_jobs"],
            "top_match_results": data["top_match_results"],
            "is_home": True,
        },
    )


@app.post("/upload-cv", response_class=HTMLResponse)
async def upload_cv(request: Request, file: UploadFile = File(...)) -> RedirectResponse:
    """
    Upload a CV file and store a plaintext version plus a short preview.

    For now we treat the file as UTF-8 text; binary/PDF uploads are decoded
    best-effort with replacement to avoid crashes.
    """
    contents = await file.read()
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        text = contents.decode("utf-8", errors="replace")

    resumes_dir = Path("data/resumes")
    resumes_dir.mkdir(parents=True, exist_ok=True)
    filename = timestamped_filename("uploaded_cv", ".txt")
    path = resumes_dir / filename
    path.write_text(text, encoding="utf-8")

    preview = text[:2000]
    state = _load_web_state()
    state["resume_path"] = str(path)
    state["resume_preview"] = preview
    _save_web_state(state)

    logger.info("Uploaded CV saved to {}", path)
    return RedirectResponse(url="/profile", status_code=303)


@app.get("/profile", response_class=HTMLResponse)
async def view_profile(request: Request, section: Optional[str] = None) -> HTMLResponse:
    """Show the uploaded CV and LinkedIn session."""
    state = _load_web_state()
    resume_preview: str = state.get("resume_preview", "")
    resume_path: Optional[str] = state.get("resume_path")
    profile_section = section if section in ("linkedin", "cv") else None
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "resume_preview": resume_preview,
            "resume_path": resume_path,
            "profile_section": profile_section,
        },
    )


@app.get("/apply/{job_id}", response_class=HTMLResponse)
async def apply_page(request: Request, job_id: int) -> HTMLResponse:
    """In-app application page with live browser view."""
    job = get_job_by_id(job_id)
    if not job:
        return templates.TemplateResponse(
            "apply.html",
            {
                "request": request,
                "job": None,
                "error": f"Job {job_id} not found",
            },
        )
    
    return templates.TemplateResponse(
        "apply.html",
        {
            "request": request,
            "job": job,
        },
    )


@app.post("/run-search")
async def run_search(
    request: Request,
    keywords: str = Form(...),
    location: str = Form(...),
    easy_apply: bool = Form(False),
    date_posted: str = Form(None),
    experience_level: str = Form(None),
    remote: str = Form(None),
    job_type: str = Form(None),
    anonymous_search: bool = Form(False),
) -> RedirectResponse:
    """
    Trigger a LinkedIn job search. Matching runs in background after search completes.
    """
    state = _load_web_state()
    state["last_search"] = {
        "keywords": keywords,
        "location": location,
        "easy_apply": easy_apply,
        "date_posted": date_posted,
        "experience_level": experience_level,
        "remote": remote,
        "job_type": job_type,
    }
    _save_web_state(state)

    logger.info(
        "Running web-initiated search: keywords='{}', location='{}', easy_apply={}, date_posted={}, experience={}, remote={}, job_type={}, anonymous={}",
        keywords,
        location,
        easy_apply,
        date_posted,
        experience_level,
        remote,
        job_type,
        anonymous_search,
    )
    
    # Search for jobs (this still blocks, but is usually fast)
    search_result = await search_jobs(
        keywords=keywords,
        location=location,
        easy_apply_only=easy_apply,
        limit=50,
        date_posted=date_posted if date_posted else None,
        experience_level=experience_level if experience_level else None,
        remote=remote if remote else None,
        job_type=job_type if job_type else None,
        anonymous=anonymous_search,
    )
    
    jobs = search_result.jobs
    logger.info("Search result: {} new, {} duplicates", search_result.new_jobs, search_result.duplicates)
    
    # Start background matching if jobs found
    if jobs and not _matching_status["running"]:
        job_ids = [j.id for j in jobs if j.id]
        if job_ids:
            task_group_id = create_task_group_id()
            enqueue_process_pending_tasks(task_group_id, job_ids)
            state = _load_web_state()
            state["active_task_group_id"] = task_group_id
            _save_web_state(state)
            logger.info(
                "Enqueued persistent processing for {} new jobs (task_group_id={})",
                len(job_ids),
                task_group_id,
            )
    
    return RedirectResponse(url="/jobs", status_code=303)


# Batch search progress tracking
_batch_search_status: Dict[str, Any] = {
    "running": False,
    "total_searches": 0,
    "completed_searches": 0,
    "current_search": None,
    "total_jobs_found": 0,
    "total_duplicates": 0,
    "searches": [],  # List of {keywords, location, jobs_found, duplicates}
}


@app.post("/batch-search")
async def batch_search(
    request: Request,
    use_profile_keywords: bool = Form(True),
    use_target_titles: bool = Form(False),
    locations: str = Form(None),
    easy_apply: bool = Form(False),
    date_posted: str = Form(None),
    experience_level: str = Form(None),
    remote: str = Form(None),
    job_type: str = Form(None),
    anonymous_search: bool = Form(False),
) -> RedirectResponse:
    """
    Run multiple searches using profile keywords and/or target titles across locations.
    """
    global _batch_search_status
    
    if _batch_search_status["running"] or _progress_status["running"]:
        logger.warning("A search or processing task is already running")
        return RedirectResponse(url="/jobs", status_code=303)
    
    profile = load_profile()
    
    # Collect search keywords from profile
    search_keywords = []
    if use_profile_keywords and profile.keywords_for_search:
        search_keywords.extend(profile.keywords_for_search)
    if use_target_titles and profile.target_titles:
        search_keywords.extend(profile.target_titles)
    
    # If no keywords from profile, fall back to top skills + "engineer"
    if not search_keywords and profile.top_skills:
        search_keywords = [f"{skill} Engineer" for skill in profile.top_skills[:5]]
    
    if not search_keywords:
        logger.warning("No search keywords available in profile")
        return RedirectResponse(url="/jobs", status_code=303)
    
    # Parse locations - from form input or profile
    search_locations = []
    if locations and locations.strip():
        search_locations = [loc.strip() for loc in locations.split(",") if loc.strip()]
    elif profile.preferred_locations:
        # Filter out non-location preferences like "Remote", "Hybrid"
        search_locations = [
            loc for loc in profile.preferred_locations 
            if loc.lower() not in ("remote", "hybrid", "on-site", "onsite")
        ]
    
    if not search_locations:
        search_locations = ["Israel"]  # Default fallback
    
    logger.info(
        "Starting batch search: {} keywords x {} locations = {} searches",
        len(search_keywords),
        len(search_locations),
        len(search_keywords) * len(search_locations),
    )
    
    # Start batch search in background
    asyncio.create_task(_background_batch_search(
        keywords_list=search_keywords,
        locations_list=search_locations,
        easy_apply=easy_apply,
        date_posted=date_posted if date_posted else None,
        experience_level=experience_level if experience_level else None,
        remote=remote if remote else None,
        job_type=job_type if job_type else None,
        anonymous=anonymous_search,
    ))
    
    return RedirectResponse(url="/jobs", status_code=303)


async def _background_batch_search(
    keywords_list: List[str],
    locations_list: List[str],
    easy_apply: bool = False,
    date_posted: str = None,
    experience_level: str = None,
    remote: str = None,
    job_type: str = None,
    skip_recent_hours: int = 12,
    anonymous: bool = False,
) -> None:
    """Background task to run multiple searches and then match all jobs."""
    global _batch_search_status, _progress_status
    
    total_searches = len(keywords_list) * len(locations_list)
    
    _batch_search_status = {
        "running": True,
        "total_searches": total_searches,
        "completed_searches": 0,
        "current_search": None,
        "total_jobs_found": 0,
        "total_duplicates": 0,
        "searches": [],
        "skipped_recent": 0,
    }
    
    all_jobs: List[JobRecord] = []
    filters = {
        "easy_apply": easy_apply,
        "date_posted": date_posted,
        "experience_level": experience_level,
        "remote": remote,
        "job_type": job_type,
    }
    
    for location in locations_list:
        for keywords in keywords_list:
            if not _batch_search_status["running"]:
                logger.info("Batch search cancelled")
                break
            
            _batch_search_status["current_search"] = f"{keywords} in {location}"
            
            # Check if this search was run recently
            if skip_recent_hours > 0 and search_was_run_recently(keywords, location, skip_recent_hours):
                logger.info("Skipping recent search: '{}' in '{}'", keywords, location)
                _batch_search_status["searches"].append({
                    "keywords": keywords,
                    "location": location,
                    "jobs_found": 0,
                    "skipped": True,
                })
                _batch_search_status["skipped_recent"] += 1
                _batch_search_status["completed_searches"] += 1
                continue
            
            logger.info("Batch search: '{}' in '{}'", keywords, location)
            
            try:
                search_result = await search_jobs(
                    keywords=keywords,
                    location=location,
                    easy_apply_only=easy_apply,
                    limit=30,  # Lower limit per search to avoid rate limiting
                    date_posted=date_posted,
                    experience_level=experience_level,
                    remote=remote,
                    job_type=job_type,
                    anonymous=anonymous,
                )
                
                jobs = search_result.jobs
                
                # Save search to history
                save_search_history(keywords, location, len(jobs), filters)
                
                _batch_search_status["searches"].append({
                    "keywords": keywords,
                    "location": location,
                    "jobs_found": len(jobs),
                    "duplicates": search_result.duplicates,
                })
                _batch_search_status["total_jobs_found"] += len(jobs)
                _batch_search_status["total_duplicates"] = _batch_search_status.get("total_duplicates", 0) + search_result.duplicates
                all_jobs.extend(jobs)
                
                logger.info("Found {} jobs ({} duplicates) for '{}' in '{}'", 
                           len(jobs), search_result.duplicates, keywords, location)
                
            except Exception as exc:
                logger.error("Error in batch search '{}' in '{}': {}", keywords, location, exc)
                _batch_search_status["searches"].append({
                    "keywords": keywords,
                    "location": location,
                    "jobs_found": 0,
                    "error": str(exc),
                })
            
            _batch_search_status["completed_searches"] += 1
            
            # Brief delay between searches to avoid rate limiting
            await asyncio.sleep(2)
        
        if not _batch_search_status["running"]:
            break
    
    _batch_search_status["running"] = False
    _batch_search_status["current_search"] = None
    
    logger.info(
        "Batch search complete: {} searches, {} total jobs found",
        _batch_search_status["completed_searches"],
        _batch_search_status["total_jobs_found"],
    )
    
    # Start background scraping and matching for all found jobs
    if all_jobs and not _progress_status["running"]:
        job_ids = [j.id for j in all_jobs if j.id]
        if job_ids:
            task_group_id = create_task_group_id()
            enqueue_process_pending_tasks(task_group_id, job_ids)
            state = _load_web_state()
            state["active_task_group_id"] = task_group_id
            _save_web_state(state)
            logger.info(
                "Enqueued persistent processing for {} jobs from batch search (task_group_id={})",
                len(job_ids),
                task_group_id,
            )


@app.get("/api/batch-search-status")
async def get_batch_search_status() -> JSONResponse:
    """API endpoint to get current batch search progress."""
    return JSONResponse(_batch_search_status)


@app.post("/api/stop-batch-search")
async def stop_batch_search() -> JSONResponse:
    """Stop the current batch search process."""
    global _batch_search_status
    _batch_search_status["running"] = False
    return JSONResponse({"status": "stopped"})


def _parse_hide_applied(value: Optional[str], default: bool = True) -> bool:
    """Parse hide_applied query param; default True when not provided."""
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in ("false", "0", "no", "off")


@app.get("/jobs", response_class=HTMLResponse)
async def list_jobs(
    request: Request,
    page: int = 1,
    per_page: int = 25,
    status: Optional[str] = None,
    search: Optional[str] = None,
    recommendation: Optional[str] = None,
    hide_applied_param: Optional[str] = Query(None, alias="hide_applied"),
    sort_by: str = "score",
    sort_dir: str = "desc",
    source: Optional[str] = None,
) -> HTMLResponse:
    """Display jobs with pagination and match results."""
    hide_applied = _parse_hide_applied(hide_applied_param, default=True)
    status_filters = [status] if status else None
    recommendation_filters = [recommendation] if recommendation else None
    jobs, total_filtered, status_counts = get_jobs_paginated(
        page=page,
        per_page=per_page,
        status_filters=status_filters,
        search_query=search,
        recommendation_filters=recommendation_filters,
        hide_applied=hide_applied,
        sort_by=sort_by,
        sort_dir=sort_dir,
        source_filter=source,
    )
    
    job_ids = [j.id for j in jobs if j.id]
    match_results = get_match_results_for_jobs(job_ids)
    
    total_pages = (total_filtered + per_page - 1) // per_page if per_page > 0 else 1
    
    pagination = {
        "page": page,
        "per_page": per_page,
        "total_items": total_filtered,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }
    
    filters = {
        "status": status or "",
        "search": search or "",
        "recommendation": recommendation or "",
        "hide_applied": hide_applied,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "source": source or "",
    }
    
    return templates.TemplateResponse(
        "jobs.html",
        {
            "request": request,
            "jobs": jobs,
            "match_results": match_results,
            "pagination": pagination,
            "filters": filters,
            "pending_scrape": status_counts.get("pending_scrape", 0),
            "pending_match": status_counts.get("pending_match", 0),
            "matched_count": status_counts.get("matched", 0),
            "llm_provider": get_llm_provider_name(),
        },
    )


@app.get("/careers/review", response_class=HTMLResponse)
async def review_pulled_jobs_page(request: Request) -> HTMLResponse:
    """Review and approve pulled jobs from career site scrapes."""
    return templates.TemplateResponse(
        "review_pulled.html",
        {"request": request},
    )


def _match_single_job_sync(job: JobRecord, profile: Any) -> bool:
    """Match a single job synchronously. Runs in thread pool."""
    global _matching_status
    
    try:
        _matching_status["current_job"] = f"{job.title} @ {job.company}"
        
        # Get description from cache
        description = None
        if job.id:
            description = get_job_full_description(job.id)
        
        if not description:
            description = job.description_snippet or ""
        
        if not description or len(description) < 50:
            logger.warning("Skipping job {} - no description available", job.id)
            return False
        
        # Run LLM matching (this is the slow blocking call)
        result = score_job_from_description(job, description, profile)
        logger.info(
            "Matched job {}: {} @ {} -> score={}, recommendation={}",
            job.id, job.title, job.company, result.match_score, result.recommendation
        )
        return True
        
    except Exception as exc:
        logger.error("Failed to match job {}: {}", job.id, exc)
        return False


async def _scrape_descriptions_for_jobs(jobs: List[JobRecord], update_progress: bool = True) -> int:
    """Scrape descriptions for jobs that don't have them. Runs async. Returns count scraped."""
    global _progress_status
    
    jobs_to_scrape = [j for j in jobs if j.id and not get_job_full_description(j.id)]
    
    if update_progress and jobs_to_scrape:
        _progress_status["phase"] = "scraping"
        _progress_status["total"] = len(jobs_to_scrape)
        _progress_status["completed"] = 0
    
    scraped = 0
    for job in jobs_to_scrape:
        if not _progress_status["running"]:
            break
        
        if update_progress:
            _progress_status["current_job"] = f"Scraping: {job.title} @ {job.company}"
            _progress_status["current_job_id"] = job.id
            _progress_status["active_jobs"] = [job.id]  # Single job active during scraping
        
        logger.info("Scraping description for job {}: {}", job.id, job.title)
        try:
            if job.source == JobSource.WORKDAY:
                # Workday pages use different DOM; use Workday scraper instead of LinkedIn extract
                from .careers.workday import WorkdayScraper
                scraper = WorkdayScraper()
                description = await scraper.fetch_job_details(job)
                if description and job.id:
                    update_job_description(job.id, description)
                    logger.info("Saved Workday description ({} chars) for job {}", len(description), job.id)
                    update_job_status(job.id, JobStatus.PENDING_MATCH)
                    scraped += 1
                else:
                    logger.warning("Could not extract Workday description for job {}", job.id)
            else:
                await scrape_job_description(job)
                update_job_status(job.id, JobStatus.PENDING_MATCH)
                scraped += 1
        except Exception as exc:
            logger.error("Failed to scrape job {}: {}", job.id, exc)
        
        if update_progress:
            _progress_status["completed"] += 1
            _progress_status["active_jobs"] = []  # Clear when done with this job
        
        await asyncio.sleep(0.1)
    
    return scraped


async def _background_match_jobs(jobs: List[JobRecord]) -> None:
    """Background task to match jobs in parallel with progress tracking."""
    global _progress_status
    
    settings = get_settings()
    max_concurrent = settings.env.llm_max_concurrent
    semaphore = asyncio.Semaphore(max_concurrent)
    
    _progress_status["running"] = True
    _progress_status["phase"] = "matching"
    _progress_status["total"] = len(jobs)
    _progress_status["completed"] = 0
    _progress_status["current_job"] = f"Starting parallel LLM matching ({max_concurrent} workers)..."
    _progress_status["active_jobs"] = []
    
    profile = load_profile()
    
    async def match_with_semaphore(job: JobRecord) -> bool:
        """Match a single job, limited by semaphore for parallel control."""
        async with semaphore:
            if not _progress_status["running"]:
                return False
            
            # Track this job as active
            _progress_status["active_jobs"].append(job.id)
            _progress_status["current_job"] = f"Matching: {job.title} @ {job.company}"
            _progress_status["current_job_id"] = job.id
            
            try:
                # Run blocking LLM call in thread pool
                success = await asyncio.to_thread(_match_single_job_sync, job, profile)
                
                if success and job.id:
                    update_job_status(job.id, JobStatus.MATCHED)
                
                _progress_status["completed"] += 1
                return success
            finally:
                # Remove from active jobs
                if job.id in _progress_status["active_jobs"]:
                    _progress_status["active_jobs"].remove(job.id)
    
    # Run all jobs in parallel (limited by semaphore)
    logger.info("Starting parallel matching for {} jobs with {} concurrent workers", 
                len(jobs), max_concurrent)
    tasks = [match_with_semaphore(job) for job in jobs]
    await asyncio.gather(*tasks, return_exceptions=True)
    
    _progress_status["running"] = False
    _progress_status["phase"] = None
    _progress_status["current_job"] = None
    _progress_status["current_job_id"] = None
    _progress_status["active_jobs"] = []
    logger.info("Background matching complete: {}/{} jobs processed", 
                _progress_status["completed"], _progress_status["total"])


async def _background_scrape_and_match(jobs: List[JobRecord], use_prefilter: bool = True) -> None:
    """Background task to scrape descriptions and then match jobs."""
    global _progress_status
    
    _progress_status["running"] = True
    
    # Phase 1: Scrape descriptions
    await _scrape_descriptions_for_jobs(jobs, update_progress=True)
    
    if not _progress_status["running"]:
        _progress_status["phase"] = None
        _progress_status["current_job"] = None
        _progress_status["current_job_id"] = None
        _progress_status["active_jobs"] = []
        return
    
    # Phase 2: Pre-filter jobs (optional)
    jobs_to_match = jobs
    if use_prefilter:
        _progress_status["phase"] = "filtering"
        _progress_status["current_job"] = "Pre-filtering jobs by keywords..."
        
        try:
            profile = load_profile()
            
            # Build descriptions dict
            descriptions = {}
            for job in jobs:
                if job.id:
                    desc = get_job_full_description(job.id) or job.description_snippet or ""
                    descriptions[job.id] = desc
            
            # Apply pre-filter
            jobs_to_match, jobs_skipped = filter_jobs_for_matching(
                jobs, profile, descriptions, min_skill_matches=2
            )
            
            logger.info(
                "Pre-filter: {} jobs pass, {} skipped",
                len(jobs_to_match), len(jobs_skipped)
            )
            
            # Mark skipped jobs as matched with a low score
            for job in jobs_skipped:
                if job.id:
                    # Create a minimal match result for skipped jobs
                    from .models import MatchResult
                    from .db import save_match_result
                    
                    skip_result = MatchResult(
                        job_id=job.id,
                        match_score=25,  # Low score for pre-filtered jobs
                        top_reasons=["Pre-filtered: Insufficient skill matches"],
                        missing_requirements=["Job did not match enough profile skills"],
                        inferred_qualifications=[],
                        suggested_resume_bullets=[],
                    )
                    save_match_result(skip_result)
                    update_job_status(job.id, JobStatus.MATCHED)
                    
        except Exception as exc:
            logger.error("Pre-filter failed, matching all jobs: {}", exc)
            jobs_to_match = jobs
    
    if not jobs_to_match:
        logger.info("No jobs passed pre-filter, skipping LLM matching")
        _progress_status["running"] = False
        _progress_status["phase"] = None
        _progress_status["current_job"] = None
        _progress_status["current_job_id"] = None
        _progress_status["active_jobs"] = []
        return
    
    # Phase 3: Match remaining jobs with LLM
    await _background_match_jobs(jobs_to_match)


@app.post("/match-pending")
async def match_pending_jobs(request: Request) -> RedirectResponse:
    """Enqueue persistent scraping + matching for all pending jobs."""
    
    # Get jobs that need scraping or matching
    all_jobs = get_all_jobs()
    pending_scrape = [j for j in all_jobs if j.status == JobStatus.PENDING_SCRAPE]
    pending_match = [j for j in all_jobs if j.status == JobStatus.PENDING_MATCH]
    
    # Also include legacy discovered status and jobs without match results
    match_results = get_all_match_results()
    legacy_unmatched = [j for j in all_jobs 
                        if j.status in (JobStatus.DISCOVERED, JobStatus.SHORTLISTED) 
                        and j.id not in match_results]
    
    # Combine all pending jobs
    all_pending = pending_scrape + pending_match + legacy_unmatched
    # Remove duplicates by job id
    seen_ids = set()
    unique_pending = []
    for j in all_pending:
        if j.id not in seen_ids:
            seen_ids.add(j.id)
            unique_pending.append(j)
    
    if unique_pending:
        job_ids = [j.id for j in unique_pending if j.id]
        task_group_id = create_task_group_id()
        enqueue_process_pending_tasks(task_group_id, job_ids)

        # Track active group in web state so /api/progress can render it
        state = _load_web_state()
        state["active_task_group_id"] = task_group_id
        _save_web_state(state)

        logger.info(
            "Enqueued persistent processing for {} pending jobs (task_group_id={})",
            len(job_ids),
            task_group_id,
        )
    else:
        logger.info("No pending jobs to process.")
    
    return RedirectResponse(url="/jobs", status_code=303)


@app.get("/api/matching-status")
async def get_matching_status() -> JSONResponse:
    """API endpoint to get current matching progress."""
    return JSONResponse(_matching_status)


@app.post("/api/stop-matching")
async def stop_matching() -> JSONResponse:
    """Stop the current background matching process."""
    global _matching_status
    # Cancel persistent tasks for the active group if present
    state = _load_web_state()
    task_group_id = state.get("active_task_group_id")
    if task_group_id:
        request_cancel_task_group(task_group_id)
    _matching_status["running"] = False
    return JSONResponse({"status": "stopped"})


@app.get("/api/job/{job_id}")
async def get_job_detail(job_id: int) -> JSONResponse:
    """Get job details and match result for the popup modal."""
    job = get_job_by_id(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    
    # Get full description
    full_description = get_job_full_description(job_id)
    
    # Get match result
    match = get_match_result(job_id)
    
    response = {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": str(job.url),
        "easy_apply": job.easy_apply,
        "company_logo_url": job.company_logo_url,
        "status": job.status.value,
        "date_found": job.date_found.isoformat() if job.date_found else None,
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "full_description": full_description or job.description_snippet or "No description available.",
        "match": None,
    }
    
    if match:
        response["match"] = {
            "score": match.match_score,
            "recommendation": match.recommendation,
            "top_reasons": match.top_reasons,
            "missing_requirements": match.missing_requirements,
            "inferred_qualifications": match.inferred_qualifications,
            "suggested_resume_bullets": match.suggested_resume_bullets,
        }
    
    return JSONResponse(response)


@app.get("/api/progress")
async def get_progress() -> JSONResponse:
    """API endpoint to get current progress (scraping or matching)."""
    # Prefer persistent task-group progress (survives reloads)
    state = _load_web_state()
    task_group_id = state.get("active_task_group_id")
    if task_group_id:
        try:
            summary = get_task_group_summary(task_group_id)
            if summary.get("running"):
                active_jobs = summary.get("active_jobs") or []
                return JSONResponse(
                    {
                        "running": True,
                        "phase": summary.get("phase"),
                        "total": summary.get("total", 0),
                        "completed": summary.get("completed", 0),
                        "current_job": summary.get("current_job"),
                        "current_job_id": active_jobs[0] if active_jobs else None,
                        "active_jobs": active_jobs,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to compute task-group progress: {}", exc)

    return JSONResponse(_progress_status)


@app.get("/api/jobs")
async def get_jobs_api(
    page: int = 1,
    per_page: int = 25,
    status: Optional[str] = None,
    status_list: Optional[List[str]] = Query(None, alias="status[]"),
    search: Optional[str] = None,
    recommendation: Optional[str] = None,
    recommendation_list: Optional[List[str]] = Query(None, alias="recommendation[]"),
    hide_applied_param: Optional[str] = Query(None, alias="hide_applied"),
    sort_by: str = "id",
    sort_dir: str = "desc",
    source: Optional[str] = None,
    company_list: Optional[List[str]] = Query(None, alias="company[]"),
    title_list: Optional[List[str]] = Query(None, alias="title[]"),
    location_list: Optional[List[str]] = Query(None, alias="location[]"),
) -> JSONResponse:
    """
    API endpoint to get jobs with pagination and filtering.
    
    Query params:
        page, per_page, search, hide_applied, sort_by, sort_dir, source
        status / status[]: Filter by status (single or multi)
        recommendation / recommendation[]: Filter by recommendation (single or multi)
        company[]: Filter by company (multi, exact match)
        title[]: Filter by title (multi, substring)
        location[]: Filter by location (multi, substring)
    """
    hide_applied = _parse_hide_applied(hide_applied_param, default=True)
    status_filters = list(status_list) if (status_list and len(status_list)) else ([status] if status else None)
    recommendation_filters = list(recommendation_list) if (recommendation_list and len(recommendation_list)) else ([recommendation] if recommendation else None)
    jobs, total_filtered, status_counts = get_jobs_paginated(
        page=page,
        per_page=per_page,
        status_filters=status_filters,
        search_query=search,
        recommendation_filters=recommendation_filters,
        hide_applied=hide_applied,
        sort_by=sort_by,
        sort_dir=sort_dir,
        source_filter=source,
        company_filters=company_list,
        title_filters=title_list,
        location_filters=location_list,
    )
    
    job_ids = [j.id for j in jobs if j.id]
    match_results = get_match_results_for_jobs(job_ids)
    
    jobs_data = []
    for job in jobs:
        match = match_results.get(job.id)
        jobs_data.append({
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": str(job.url),
            "easy_apply": job.easy_apply,
            "company_logo_url": job.company_logo_url,
            "status": job.status.value,
            "source": job.source.value if job.source else "linkedin",
            "date_found": job.date_found.isoformat() if job.date_found else None,
            "date_posted": job.date_posted.isoformat() if job.date_posted else None,
            "score": match.match_score if match else None,
            "recommendation": ("apply" if match and match.match_score >= 70 
                              else "consider" if match and match.match_score >= 50 
                              else "skip" if match else None),
        })
    
    total_pages = (total_filtered + per_page - 1) // per_page if per_page > 0 else 1
    
    return JSONResponse({
        "jobs": jobs_data,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_items": total_filtered,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        "counts": status_counts,
    })


@app.get("/api/jobs/facets")
async def get_jobs_facets_api(
    column: str = Query(..., description="Column name: company, title, location, status"),
    limit: int = Query(200, ge=1, le=500),
    search: Optional[str] = None,
    status_list: Optional[List[str]] = Query(None, alias="status[]"),
    recommendation_list: Optional[List[str]] = Query(None, alias="recommendation[]"),
    hide_applied_param: Optional[str] = Query(None, alias="hide_applied"),
    source: Optional[str] = None,
    company_list: Optional[List[str]] = Query(None, alias="company[]"),
    title_list: Optional[List[str]] = Query(None, alias="title[]"),
    location_list: Optional[List[str]] = Query(None, alias="location[]"),
) -> JSONResponse:
    """Return distinct values for a column (for column filter dropdowns)."""
    hide_applied = _parse_hide_applied(hide_applied_param, default=True)
    if column not in ("company", "title", "location", "status"):
        return JSONResponse({"error": "Invalid column"}, status_code=400)
    values = get_jobs_facets(
        column=column,
        limit=limit,
        search_query=search,
        status_filters=status_list,
        recommendation_filters=recommendation_list,
        hide_applied=hide_applied,
        source_filter=source,
        company_filters=company_list,
        title_filters=title_list,
        location_filters=location_list,
    )
    return JSONResponse({"column": column, "values": values})


@app.post("/api/jobs/delete")
async def api_delete_jobs(request: Request) -> JSONResponse:
    """Soft-delete selected jobs (kept for dedupe)."""
    data = await request.json()
    job_ids = data.get("job_ids", [])
    if not job_ids:
        return JSONResponse({"error": "No job IDs provided"}, status_code=400)
    
    count = delete_jobs(job_ids)
    logger.info("Soft-deleted {} jobs", count)
    return JSONResponse({"deleted": count})


@app.post("/api/jobs/rescrape")
async def api_rescrape_jobs(request: Request) -> JSONResponse:
    """Clear descriptions for selected jobs and trigger re-scraping."""
    global _progress_status
    
    data = await request.json()
    job_ids = data.get("job_ids", [])
    if not job_ids:
        return JSONResponse({"error": "No job IDs provided"}, status_code=400)
    
    if _progress_status["running"]:
        return JSONResponse({"error": "Processing already in progress"}, status_code=409)
    
    # Clear descriptions
    count = clear_job_descriptions(job_ids)
    logger.info("Cleared descriptions for {} jobs", count)
    
    # Get the jobs and start scraping
    jobs = get_jobs_by_ids(job_ids)
    if jobs:
        ids = [j.id for j in jobs if j.id]
        if ids:
            task_group_id = create_task_group_id()
            enqueue_process_pending_tasks(task_group_id, ids)
            state = _load_web_state()
            state["active_task_group_id"] = task_group_id
            _save_web_state(state)
    
    return JSONResponse({"cleared": count, "scraping": len(jobs)})


@app.post("/api/job/{job_id}/applied")
async def mark_job_applied(job_id: int) -> JSONResponse:
    """Mark a job as applied by the user."""
    update_job_status(job_id, JobStatus.APPLIED)
    logger.info("Job {} marked as applied by user", job_id)
    return JSONResponse({"success": True, "job_id": job_id, "status": "applied"})


@app.post("/api/jobs/rematch")
async def api_rematch_jobs(request: Request) -> JSONResponse:
    """Clear match results for selected jobs and trigger re-matching."""
    global _progress_status
    
    data = await request.json()
    job_ids = data.get("job_ids", [])
    if not job_ids:
        return JSONResponse({"error": "No job IDs provided"}, status_code=400)
    
    if _progress_status["running"]:
        return JSONResponse({"error": "Processing already in progress"}, status_code=409)
    
    # Clear match results
    count = clear_match_results(job_ids)
    logger.info("Cleared match results for {} jobs", count)
    
    # Get the jobs and start matching
    jobs = get_jobs_by_ids(job_ids)
    if jobs:
        ids = [j.id for j in jobs if j.id]
        if ids:
            task_group_id = create_task_group_id()
            for jid in ids:
                enqueue_pipeline_task(
                    task_group_id=task_group_id,
                    task_type=PipelineTaskType.MATCH_JOB,
                    payload={"job_id": jid},
                    priority=0,
                    max_attempts=3,
                )
            state = _load_web_state()
            state["active_task_group_id"] = task_group_id
            _save_web_state(state)
    
    return JSONResponse({"cleared": count, "matching": len(jobs)})


# Global status for logo rescraping
_logo_rescrape_status: Dict[str, Any] = {
    "running": False,
    "total": 0,
    "processed": 0,
    "found": 0,
    "failed": 0,
}


@app.post("/api/rescrape-logos")
async def rescrape_missing_logos(request: Request) -> JSONResponse:
    """Rescrape logos for jobs that are missing them.
    
    Can accept optional JSON body with job_ids to rescrape specific jobs.
    If no job_ids provided, rescrapes all jobs missing logos.
    """
    global _logo_rescrape_status
    
    if _logo_rescrape_status["running"]:
        return JSONResponse({"error": "Logo rescraping already in progress"}, status_code=409)
    
    # Check if specific job_ids were provided
    job_ids = None
    try:
        body = await request.json()
        job_ids = body.get("job_ids", None)
    except Exception:
        pass  # No body or invalid JSON, proceed with all missing logos
    
    if job_ids:
        # Get specific jobs and filter to those missing logos
        all_jobs = get_jobs_by_ids(job_ids)
        jobs = [j for j in all_jobs if not j.company_logo_url]
    else:
        # Get all jobs missing logos
        jobs = get_jobs_missing_logos()
    
    if not jobs:
        return JSONResponse({"message": "No jobs missing logos", "count": 0})
    
    # Start background task
    _logo_rescrape_status = {
        "running": True,
        "total": len(jobs),
        "processed": 0,
        "found": 0,
        "failed": 0,
    }
    
    asyncio.create_task(_background_rescrape_logos(jobs))
    
    return JSONResponse({
        "message": f"Started rescraping logos for {len(jobs)} jobs",
        "count": len(jobs),
    })


@app.get("/api/rescrape-logos/status")
async def get_rescrape_logos_status() -> JSONResponse:
    """Get the status of logo rescraping."""
    return JSONResponse(_logo_rescrape_status)


async def _background_rescrape_logos(jobs: List[JobRecord]) -> None:
    """Background task to rescrape logos for jobs missing them."""
    global _logo_rescrape_status
    
    from playwright.async_api import async_playwright
    
    logger.info("Starting logo rescrape for {} jobs", len(jobs))
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        for job in jobs:
            if not _logo_rescrape_status["running"]:
                logger.info("Logo rescrape stopped by user")
                break
                
            try:
                logger.info("Rescraping logo for job {}: {}", job.id, job.title[:40])
                await page.goto(str(job.url), wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                
                logo_url = await _scrape_logo_from_page(page, job_title=job.title)
                
                if logo_url:
                    # Download and save the logo locally
                    local_path = await download_logo_image(page, logo_url, job.id)
                    if local_path:
                        update_job_logo(job.id, local_path)
                        _logo_rescrape_status["found"] += 1
                        logger.info("Downloaded logo for job {}: {}", job.id, local_path)
                    else:
                        _logo_rescrape_status["failed"] += 1
                        logger.warning("Failed to download logo for job {}", job.id)
                else:
                    _logo_rescrape_status["failed"] += 1
                    logger.warning("No logo found for job {}", job.id)
                    
            except Exception as exc:
                _logo_rescrape_status["failed"] += 1
                logger.error("Error rescraping logo for job {}: {}", job.id, exc)
            
            _logo_rescrape_status["processed"] += 1
            
            # Small delay between requests
            await asyncio.sleep(1)
        
        await browser.close()
    
    _logo_rescrape_status["running"] = False
    logger.info("Logo rescrape complete: {} found, {} failed", 
                _logo_rescrape_status["found"], _logo_rescrape_status["failed"])


@app.post("/api/rescrape-logos/stop")
async def stop_rescrape_logos() -> JSONResponse:
    """Stop the logo rescraping process."""
    global _logo_rescrape_status
    _logo_rescrape_status["running"] = False
    return JSONResponse({"status": "stopped"})


# Simple in-memory cache for proxied images
_image_cache: Dict[str, bytes] = {}


@app.get("/api/proxy-image")
async def proxy_image(url: str) -> Response:
    """Proxy LinkedIn images to avoid CORS/hotlinking issues.
    
    LinkedIn blocks direct image access, so we fetch the image server-side
    and serve it to the browser.
    """
    if not url or not url.startswith("https://media.licdn.com/"):
        return Response(content=b"", status_code=400)
    
    # Check cache first
    if url in _image_cache:
        return Response(
            content=_image_cache[url],
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=86400"}
        )
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": "https://www.linkedin.com/",
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                },
                timeout=10.0,
                follow_redirects=True,
            )
            
            if resp.status_code == 200:
                content = resp.content
                # Cache the image (limit cache size)
                if len(_image_cache) < 500:
                    _image_cache[url] = content
                
                content_type = resp.headers.get("content-type", "image/jpeg")
                return Response(
                    content=content,
                    media_type=content_type,
                    headers={"Cache-Control": "public, max-age=86400"}
                )
            else:
                logger.debug("Failed to fetch image {}: status {}", url[:50], resp.status_code)
                return Response(content=b"", status_code=404)
                
    except Exception as e:
        logger.debug("Error proxying image: {}", str(e))
        return Response(content=b"", status_code=500)


# ============================================================================
# LinkedIn Session Management Endpoints
# ============================================================================

@app.get("/api/session/status")
async def get_session_status() -> JSONResponse:
    """Check if a LinkedIn session exists and is valid."""
    has_session = session_exists()
    is_valid = False
    profile_name = None
    
    if has_session:
        # Validate session in background to avoid blocking
        try:
            is_valid, profile_name = await validate_session()
            # If validation didn't return a name, try to get it from stored session
            if is_valid and not profile_name:
                profile_name = get_session_profile_name()
        except Exception as exc:
            logger.error("Error validating session: {}", exc)
            is_valid = False
    
    return JSONResponse({
        "has_session": has_session,
        "is_valid": is_valid,
        "profile_name": profile_name,
    })


@app.post("/api/session/login")
async def start_interactive_login() -> JSONResponse:
    """
    Start interactive LinkedIn login.
    
    Opens a browser window where the user can log in manually.
    The session is saved automatically upon successful login.
    """
    try:
        success = await interactive_login()
        if success:
            return JSONResponse({
                "success": True,
                "message": "Login successful. Session saved.",
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "Login was not completed within the timeout period.",
            })
    except Exception as exc:
        logger.error("Error during interactive login: {}", exc)
        return JSONResponse({
            "success": False,
            "message": f"Login failed: {str(exc)}",
        }, status_code=500)


@app.post("/api/session/clear")
async def clear_linkedin_session() -> JSONResponse:
    """Clear the saved LinkedIn session."""
    cleared = clear_session()
    return JSONResponse({
        "cleared": cleared,
        "message": "Session cleared" if cleared else "No session to clear",
    })


# ============================================================================
# LLM-Generated Search Queries
# ============================================================================

def _load_resume_text_for_suggestions() -> Optional[str]:
    state = _load_web_state()
    resume_path = state.get("resume_path")
    if not resume_path:
        return None

    resume_path = Path(resume_path)
    if not resume_path.exists():
        return None

    try:
        return resume_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None


def _get_applied_job_titles(limit: int = 20) -> List[str]:
    jobs = get_all_jobs()
    titles: List[str] = []
    for job in jobs:
        if job.status == JobStatus.APPLIED and job.title:
            titles.append(job.title)
            if len(titles) >= limit:
                break
    return titles


@app.get("/api/search/suggestions")
async def get_search_suggestions() -> JSONResponse:
    resume_text = _load_resume_text_for_suggestions()
    if not resume_text:
        return JSONResponse({
            "error": "No CV uploaded. Please upload your CV first.",
            "searches": [],
        }, status_code=400)

    try:
        suggestions = await _get_suggestion_engine().generate_suggestions(
            resume_text=resume_text,
            applied_job_titles=_get_applied_job_titles(),
            search_history=get_search_history_with_effectiveness(limit=100),
            force_refresh=False,
        )
        return JSONResponse({"searches": suggestions, "count": len(suggestions)})
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load search suggestions: {}", exc)
        return JSONResponse({"error": str(exc), "searches": []}, status_code=500)


@app.post("/api/search/suggestions/refresh")
async def refresh_search_suggestions() -> JSONResponse:
    resume_text = _load_resume_text_for_suggestions()
    if not resume_text:
        return JSONResponse({
            "error": "No CV uploaded. Please upload your CV first.",
            "searches": [],
        }, status_code=400)

    try:
        suggestions = await _get_suggestion_engine().generate_suggestions(
            resume_text=resume_text,
            applied_job_titles=_get_applied_job_titles(),
            search_history=get_search_history_with_effectiveness(limit=100),
            force_refresh=True,
        )
        return JSONResponse({"searches": suggestions, "count": len(suggestions)})
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to refresh search suggestions: {}", exc)
        return JSONResponse({"error": str(exc), "searches": []}, status_code=500)


@app.get("/api/search/autocomplete")
async def search_autocomplete(q: str = Query("", min_length=0)) -> JSONResponse:
    suggestions: List[str] = []
    seen: set[str] = set()

    try:
        profile = load_profile()
        for term in (profile.keywords_for_search or []) + (profile.target_titles or []):
            key = (term or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                suggestions.append(term.strip())
    except Exception:  # noqa: BLE001
        pass

    for row in get_search_history(limit=30):
        keyword = str(row.get("keywords", "")).strip()
        key = keyword.lower()
        if key and key not in seen:
            seen.add(key)
            suggestions.append(keyword)

    needle = q.strip().lower()
    if needle:
        suggestions = [s for s in suggestions if needle in s.lower()]

    return JSONResponse({"suggestions": suggestions[:30]})


@app.post("/api/search/run-batch")
async def run_batch_api(request: Request) -> JSONResponse:
    global _batch_search_status
    if _batch_search_status["running"] or _progress_status["running"]:
        return JSONResponse({"error": "A search or processing task is already running"}, status_code=409)

    data = await request.json()
    queries = [str(q).strip() for q in data.get("queries", []) if str(q).strip()]
    locations = [str(l).strip() for l in data.get("locations", []) if str(l).strip()]
    if not queries:
        return JSONResponse({"error": "At least one query is required"}, status_code=400)
    if not locations:
        locations = ["Israel"]

    filters = data.get("filters", {}) or {}
    anonymous_search = bool(data.get("anonymous_search", False))

    asyncio.create_task(
        _background_batch_search(
            keywords_list=queries,
            locations_list=locations,
            easy_apply=bool(filters.get("easy_apply", False)),
            date_posted=filters.get("date_posted"),
            experience_level=filters.get("experience_level"),
            remote=filters.get("remote"),
            job_type=filters.get("job_type"),
            anonymous=anonymous_search,
        )
    )

    return JSONResponse({
        "status": "started",
        "query_count": len(queries),
        "location_count": len(locations),
        "total_searches": len(queries) * len(locations),
    })


@app.post("/api/generate-searches")
async def generate_searches_from_cv() -> JSONResponse:
    """Legacy endpoint: proxies to unified suggestions API."""
    return await get_search_suggestions()


@app.post("/batch-search-generated")
async def batch_search_generated(
    request: Request,
    queries: str = Form(...),
    locations: str = Form(None),
    easy_apply: bool = Form(False),
    date_posted: str = Form(None),
    experience_level: str = Form(None),
    remote: str = Form(None),
    job_type: str = Form(None),
    anonymous_search: bool = Form(False),
) -> RedirectResponse:
    """Legacy form endpoint: parse form and proxy to unified batch runner."""
    try:
        search_queries = json.loads(queries)
        keywords_list = [q.get("query") for q in search_queries if q.get("query")]
    except json.JSONDecodeError:
        keywords_list = [q.strip() for q in queries.split(",") if q.strip()]

    search_locations: List[str] = []
    if locations and locations.strip():
        search_locations = [loc.strip() for loc in locations.split(",") if loc.strip()]

    if not search_locations:
        profile = load_profile()
        if profile and profile.preferred_locations:
            search_locations = [
                loc for loc in profile.preferred_locations 
                if loc.lower() not in ("remote", "hybrid", "on-site", "onsite")
            ]

    if not search_locations:
        search_locations = ["Israel"]

    if not keywords_list:
        return RedirectResponse(url="/jobs", status_code=303)

    # Proxy into unified JSON endpoint behavior.
    fake_request = type("BatchRequest", (), {})()
    payload = {
        "queries": keywords_list,
        "locations": search_locations,
        "filters": {
            "easy_apply": easy_apply,
            "date_posted": date_posted if date_posted else None,
            "experience_level": experience_level if experience_level else None,
            "remote": remote if remote else None,
            "job_type": job_type if job_type else None,
        },
        "anonymous_search": anonymous_search,
    }
    async def _json():
        return payload
    fake_request.json = _json
    await run_batch_api(fake_request)

    return RedirectResponse(url="/jobs", status_code=303)


# ============================================================================
# Alternative Job Sources
# ============================================================================

@app.post("/api/search/google-jobs")
async def search_google_jobs_api(request: Request) -> JSONResponse:
    """
    Search Google Jobs for positions.
    
    Google Jobs aggregates from multiple sources and can find jobs
    not available through direct LinkedIn search.
    """
    global _batch_search_status, _progress_status
    
    if _batch_search_status["running"] or _progress_status["running"]:
        return JSONResponse({
            "error": "A search or processing task is already running",
        }, status_code=409)
    
    data = await request.json()
    keywords = data.get("keywords", "")
    location = data.get("location", "Israel")
    
    if not keywords:
        return JSONResponse({
            "error": "Keywords are required",
        }, status_code=400)
    
    try:
        from .linkedin.google_jobs import search_google_jobs
        
        jobs = await search_google_jobs(
            keywords=keywords,
            location=location,
            limit=30,
        )
        
        # Start background matching if jobs found
        if jobs and not _progress_status["running"]:
            asyncio.create_task(_background_scrape_and_match(jobs))
        
        return JSONResponse({
            "jobs_found": len(jobs),
            "message": f"Found {len(jobs)} jobs from Google Jobs",
        })
        
    except Exception as exc:
        logger.error("Google Jobs search failed: {}", exc)
        return JSONResponse({
            "error": f"Search failed: {str(exc)}",
        }, status_code=500)


@app.post("/api/search/company-careers")
async def search_company_careers_api(request: Request) -> JSONResponse:
    """
    Search a specific company's careers page.
    """
    global _batch_search_status, _progress_status
    
    if _batch_search_status["running"] or _progress_status["running"]:
        return JSONResponse({
            "error": "A search or processing task is already running",
        }, status_code=409)
    
    data = await request.json()
    company_name = data.get("company_name", "")
    keywords = data.get("keywords", "")
    careers_url = data.get("careers_url")
    
    if not company_name:
        return JSONResponse({
            "error": "Company name is required",
        }, status_code=400)
    
    try:
        from .linkedin.google_jobs import search_company_careers
        
        jobs = await search_company_careers(
            company_name=company_name,
            keywords=keywords,
            careers_url=careers_url,
        )
        
        # Start background matching if jobs found
        if jobs and not _progress_status["running"]:
            asyncio.create_task(_background_scrape_and_match(jobs))
        
        return JSONResponse({
            "jobs_found": len(jobs),
            "message": f"Found {len(jobs)} jobs from {company_name} careers",
        })
        
    except Exception as exc:
        logger.error("Company careers search failed: {}", exc)
        return JSONResponse({
            "error": f"Search failed: {str(exc)}",
        }, status_code=500)


@app.get("/api/search/history")
async def get_search_history_api(limit: int = 50) -> JSONResponse:
    """Get recent search history."""
    history = get_search_history(limit)
    return JSONResponse({
        "history": history,
        "count": len(history),
    })


# ============================================================================
# Job Exploration API
# ============================================================================

@app.post("/api/explore/start")
async def start_explore_session(request: Request) -> JSONResponse:
    """
    Start a new exploration session.
    
    Accepts configuration options for the exploration:
    - intensity: slow, medium, fast
    - max_searches: maximum number of searches
    - max_duration_hours: time limit
    - strategies: list of strategies to use
    - locations: list of locations to search
    - filters: easy_apply, date_posted, experience_level, remote, job_type
    """
    from .explore import (
        start_exploration,
        get_exploration_status,
        ExplorationConfig,
        ExplorationIntensity,
    )
    
    # Check if already running
    status = get_exploration_status()
    if status.get("running"):
        return JSONResponse({
            "error": "An exploration session is already running",
            "status": status,
        }, status_code=409)
    
    # Check if batch search or processing is running
    if _batch_search_status.get("running") or _progress_status.get("running"):
        return JSONResponse({
            "error": "Another search or processing task is running. Please wait.",
        }, status_code=409)
    
    try:
        data = await request.json()
    except Exception:
        data = {}
    
    # Build configuration
    intensity_str = data.get("intensity", "medium")
    try:
        intensity = ExplorationIntensity(intensity_str)
    except ValueError:
        intensity = ExplorationIntensity.MEDIUM
    
    config = ExplorationConfig(
        intensity=intensity,
        max_searches=data.get("max_searches", 100),
        max_duration_hours=data.get("max_duration_hours", 4.0),
        strategies=data.get("strategies", ["profile", "skill_combo", "domain", "technology", "learning"]),
        skip_recent_hours=data.get("skip_recent_hours", 24),
        auto_process=data.get("auto_process", True),
        locations=data.get("locations", []),
        easy_apply=data.get("easy_apply", False),
        date_posted=data.get("date_posted"),
        experience_level=data.get("experience_level"),
        remote=data.get("remote"),
        job_type=data.get("job_type"),
    )
    
    try:
        session = await start_exploration(config)
        logger.info("Started exploration session {} with intensity {}", session.id, intensity.value)
        
        return JSONResponse({
            "success": True,
            "session_id": session.id,
            "status": session.to_status_dict(),
            "message": f"Exploration started with {session.total_searches} planned searches",
        })
    except RuntimeError as e:
        return JSONResponse({
            "error": str(e),
        }, status_code=409)
    except Exception as e:
        logger.error("Failed to start exploration: {}", e)
        return JSONResponse({
            "error": f"Failed to start exploration: {str(e)}",
        }, status_code=500)


@app.post("/api/explore/stop")
async def stop_explore_session() -> JSONResponse:
    """Stop the current exploration session."""
    from .explore import stop_exploration, get_exploration_status
    
    status = get_exploration_status()
    if not status.get("running") and not status.get("paused"):
        return JSONResponse({
            "error": "No exploration session is running",
        }, status_code=400)
    
    try:
        session = await stop_exploration(reason="user_requested")
        if session:
            return JSONResponse({
                "success": True,
                "session_id": session.id,
                "status": session.to_status_dict(),
                "message": f"Exploration stopped after {session.completed_searches} searches",
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "No session to stop",
            })
    except Exception as e:
        logger.error("Failed to stop exploration: {}", e)
        return JSONResponse({
            "error": f"Failed to stop exploration: {str(e)}",
        }, status_code=500)


@app.post("/api/explore/pause")
async def pause_explore_session() -> JSONResponse:
    """Pause the current exploration session (can be resumed)."""
    from .explore import pause_exploration, get_exploration_status
    
    status = get_exploration_status()
    if not status.get("running"):
        return JSONResponse({
            "error": "No exploration session is running",
        }, status_code=400)
    
    try:
        session = await pause_exploration()
        if session:
            return JSONResponse({
                "success": True,
                "session_id": session.id,
                "status": session.to_status_dict(),
                "message": f"Exploration paused at {session.completed_searches}/{session.total_searches} searches",
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "No session to pause",
            })
    except Exception as e:
        logger.error("Failed to pause exploration: {}", e)
        return JSONResponse({
            "error": f"Failed to pause exploration: {str(e)}",
        }, status_code=500)


@app.post("/api/explore/resume")
async def resume_explore_session() -> JSONResponse:
    """Resume a paused exploration session."""
    from .explore import resume_exploration, get_exploration_status
    
    status = get_exploration_status()
    if not status.get("paused"):
        return JSONResponse({
            "error": "No paused exploration session to resume",
        }, status_code=400)
    
    # Check if batch search or processing is running
    if _batch_search_status.get("running") or _progress_status.get("running"):
        return JSONResponse({
            "error": "Another search or processing task is running. Please wait.",
        }, status_code=409)
    
    try:
        session = await resume_exploration()
        if session:
            return JSONResponse({
                "success": True,
                "session_id": session.id,
                "status": session.to_status_dict(),
                "message": f"Exploration resumed from {session.completed_searches}/{session.total_searches}",
            })
        else:
            return JSONResponse({
                "success": False,
                "message": "No session to resume",
            })
    except Exception as e:
        logger.error("Failed to resume exploration: {}", e)
        return JSONResponse({
            "error": f"Failed to resume exploration: {str(e)}",
        }, status_code=500)


@app.get("/api/explore/status")
async def get_explore_status() -> JSONResponse:
    """Get the current exploration status."""
    from .explore import get_exploration_status
    
    status = get_exploration_status()
    return JSONResponse(status)


@app.get("/api/explore/insights")
async def get_explore_insights() -> JSONResponse:
    """
    Get exploration insights and search effectiveness analysis.
    
    Returns:
    - Top performing queries
    - Common successful terms
    - Recommended queries
    - Overall statistics
    """
    from .explore.intelligence import analyze_search_effectiveness
    from .db import get_search_history_with_effectiveness
    
    try:
        # Get search history with effectiveness metrics
        history = get_search_history_with_effectiveness(limit=100)
        
        # Analyze effectiveness
        insights = analyze_search_effectiveness(history)
        
        return JSONResponse({
            "insights": insights.to_dict(),
            "search_count": len(history),
        })
    except Exception as e:
        logger.error("Failed to get exploration insights: {}", e)
        return JSONResponse({
            "error": f"Failed to analyze insights: {str(e)}",
            "insights": {},
        }, status_code=500)


@app.get("/api/explore/sessions")
async def get_explore_sessions(limit: int = 20) -> JSONResponse:
    """Get list of past exploration sessions."""
    from .db import get_exploration_sessions
    
    try:
        sessions = get_exploration_sessions(limit)
        return JSONResponse({
            "sessions": sessions,
            "count": len(sessions),
        })
    except Exception as e:
        logger.error("Failed to get exploration sessions: {}", e)
        return JSONResponse({
            "error": f"Failed to get sessions: {str(e)}",
            "sessions": [],
        }, status_code=500)


@app.get("/api/explore/session/{session_id}")
async def get_explore_session(session_id: int) -> JSONResponse:
    """Get details of a specific exploration session."""
    from .db import get_exploration_session
    
    try:
        session = get_exploration_session(session_id)
        if session:
            return JSONResponse(session)
        else:
            return JSONResponse({
                "error": f"Session {session_id} not found",
            }, status_code=404)
    except Exception as e:
        logger.error("Failed to get exploration session {}: {}", session_id, e)
        return JSONResponse({
            "error": f"Failed to get session: {str(e)}",
        }, status_code=500)


# ============================================================================
# In-App Application Session Endpoints
# ============================================================================

@app.post("/api/apply/start/{job_id}")
async def start_apply_session(job_id: int) -> JSONResponse:
    """
    Start an in-app application session for a job.
    
    This creates a new browser session and navigates to the job's Easy Apply form.
    """
    from .linkedin.apply_session import create_session, get_session_for_job
    from .db import get_job_by_id
    
    job = get_job_by_id(job_id)
    if not job:
        return JSONResponse({
            "error": f"Job {job_id} not found",
        }, status_code=404)
    
    if not job.easy_apply:
        return JSONResponse({
            "error": "This job does not support Easy Apply",
        }, status_code=400)
    
    # Check for existing active session
    existing = get_session_for_job(job_id)
    if existing and existing.session and existing.session.is_active():
        return JSONResponse({
            "session_id": existing.session.id,
            "status": existing.session.status.value,
            "message": "Existing active session found",
            "session": existing.session.to_dict(),
        })
    
    try:
        engine = await create_session(job_id)
        
        # Start the session in background
        asyncio.create_task(engine.start())
        
        return JSONResponse({
            "session_id": engine.session.id,
            "status": engine.session.status.value,
            "message": "Application session started",
            "session": engine.session.to_dict(),
        })
    except Exception as e:
        logger.error("Failed to start apply session for job {}: {}", job_id, e)
        return JSONResponse({
            "error": f"Failed to start session: {str(e)}",
        }, status_code=500)


@app.get("/api/apply/session/{session_id}")
async def get_apply_session_status(session_id: str) -> JSONResponse:
    """Get the current status of an apply session."""
    from .linkedin.apply_session import get_active_session
    from .db import get_apply_session as db_get_session
    
    # Check active sessions first
    engine = get_active_session(session_id)
    if engine and engine.session:
        screenshot = await engine.get_screenshot() if engine.page else None
        return JSONResponse({
            "session": engine.session.to_dict(),
            "active": True,
            "screenshot": screenshot,
        })
    
    # Fall back to database
    session = db_get_session(session_id)
    if session:
        return JSONResponse({
            "session": session.to_dict(),
            "active": False,
        })
    
    return JSONResponse({
        "error": f"Session {session_id} not found",
    }, status_code=404)


@app.post("/api/apply/session/{session_id}/fill-field")
async def fill_apply_field(session_id: str, request: Request) -> JSONResponse:
    """Fill a specific form field in the apply session."""
    from .linkedin.apply_session import get_active_session
    
    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({
            "error": "Session not active",
        }, status_code=404)
    
    data = await request.json()
    field_id = data.get("field_id")
    value = data.get("value")
    
    if not field_id or value is None:
        return JSONResponse({
            "error": "field_id and value are required",
        }, status_code=400)
    
    try:
        success = await engine.fill_field(field_id, value)
        return JSONResponse({
            "success": success,
            "field_id": field_id,
        })
    except Exception as e:
        logger.error("Failed to fill field {}: {}", field_id, e)
        return JSONResponse({
            "error": f"Failed to fill field: {str(e)}",
        }, status_code=500)


@app.post("/api/apply/session/{session_id}/fill-all")
async def fill_all_suggested(session_id: str) -> JSONResponse:
    """Fill all fields with their suggested values."""
    from .linkedin.apply_session import get_active_session
    
    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({
            "error": "Session not active",
        }, status_code=404)
    
    try:
        results = await engine.fill_all_suggested()
        return JSONResponse({
            "success": True,
            "results": results,
            "filled_count": sum(1 for v in results.values() if v),
            "failed_count": sum(1 for v in results.values() if not v),
        })
    except Exception as e:
        logger.error("Failed to fill all fields: {}", e)
        return JSONResponse({
            "error": f"Failed to fill fields: {str(e)}",
        }, status_code=500)


@app.post("/api/apply/session/{session_id}/next-step")
async def next_apply_step(session_id: str) -> JSONResponse:
    """Navigate to the next form page."""
    from .linkedin.apply_session import get_active_session
    
    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({
            "error": "Session not active",
        }, status_code=404)
    
    try:
        success = await engine.next_step()
        if engine.session:
            return JSONResponse({
                "success": success,
                "session": engine.session.to_dict(),
            })
        return JSONResponse({
            "success": success,
        })
    except Exception as e:
        logger.error("Failed to go to next step: {}", e)
        return JSONResponse({
            "error": f"Failed to proceed: {str(e)}",
        }, status_code=500)


@app.post("/api/apply/session/{session_id}/submit")
async def submit_apply_session(session_id: str, request: Request) -> JSONResponse:
    """
    Submit the application.
    
    Requires explicit confirmation in the request body: {"confirmed": true}
    """
    from .linkedin.apply_session import get_active_session
    
    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({
            "error": "Session not active",
        }, status_code=404)
    
    data = await request.json()
    confirmed = data.get("confirmed", False)
    
    if not confirmed:
        return JSONResponse({
            "error": "Submit requires explicit confirmation. Send {\"confirmed\": true}",
        }, status_code=400)
    
    try:
        success = await engine.submit(confirmed=True)
        return JSONResponse({
            "success": success,
            "submitted": success,
            "job_id": engine.job_id,
        })
    except Exception as e:
        logger.error("Failed to submit application: {}", e)
        return JSONResponse({
            "error": f"Failed to submit: {str(e)}",
        }, status_code=500)


@app.post("/api/apply/session/{session_id}/retry-after-manual-easy-apply")
async def retry_after_manual_easy_apply(session_id: str) -> JSONResponse:
    """Retry field detection after the user manually clicked Easy Apply in the browser view."""
    from .linkedin.apply_session import get_active_session

    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({"error": "Session not active"}, status_code=404)

    try:
        success = await engine.retry_after_manual_easy_apply()
        return JSONResponse({
            "success": success,
            "session": engine.session.to_dict() if engine.session else None,
        })
    except Exception as e:
        logger.error("Failed retry-after-manual-easy-apply for {}: {}", session_id, e)
        return JSONResponse({"error": f"Failed to retry: {str(e)}"}, status_code=500)


@app.post("/api/apply/session/{session_id}/retry-form-detection")
async def retry_form_detection(session_id: str) -> JSONResponse:
    """Retry detecting form fields without changing page state."""
    from .linkedin.apply_session import get_active_session

    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({"error": "Session not active"}, status_code=404)

    try:
        success = await engine.retry_form_detection()
        return JSONResponse({
            "success": success,
            "session": engine.session.to_dict() if engine.session else None,
        })
    except Exception as e:
        logger.error("Failed retry-form-detection for {}: {}", session_id, e)
        return JSONResponse({"error": f"Failed to retry detection: {str(e)}"}, status_code=500)


@app.post("/api/apply/session/{session_id}/retry-after-manual-next")
async def retry_after_manual_next(session_id: str) -> JSONResponse:
    """Refresh progress and fields after the user manually clicked Next/Continue in the browser view."""
    from .linkedin.apply_session import get_active_session

    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({"error": "Session not active"}, status_code=404)

    try:
        success = await engine.retry_after_manual_next()
        return JSONResponse({
            "success": success,
            "session": engine.session.to_dict() if engine.session else None,
        })
    except Exception as e:
        logger.error("Failed retry-after-manual-next for {}: {}", session_id, e)
        return JSONResponse({"error": f"Failed to retry next: {str(e)}"}, status_code=500)


@app.post("/api/apply/session/{session_id}/confirm-manual-submit")
async def confirm_manual_submit(session_id: str) -> JSONResponse:
    """Mark job as applied after the user submits manually in the browser view."""
    from .linkedin.apply_session import get_active_session

    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({"error": "Session not active"}, status_code=404)

    try:
        success = await engine.confirm_manual_submit()
        return JSONResponse({
            "success": success,
            "submitted": success,
            "job_id": engine.job_id,
        })
    except Exception as e:
        logger.error("Failed confirm-manual-submit for {}: {}", session_id, e)
        return JSONResponse({"error": f"Failed to confirm manual submit: {str(e)}"}, status_code=500)


@app.post("/api/apply/session/{session_id}/open-popup")
async def open_apply_popup(session_id: str) -> JSONResponse:
    """Open an interactive popup window for the apply session."""
    from .linkedin.apply_session import get_active_session

    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({"error": "Session not active"}, status_code=404)

    try:
        ok = await engine.open_interactive_popup()
        return JSONResponse({"success": ok})
    except Exception as e:
        logger.error("Failed to open popup for {}: {}", session_id, e)
        return JSONResponse({"error": f"Failed to open popup: {str(e)}"}, status_code=500)


@app.post("/api/apply/session/{session_id}/click")
async def click_apply_session(session_id: str, request: Request) -> JSONResponse:
    """Proxy a click on the in-app screenshot to the Playwright page."""
    from .linkedin.apply_session import get_active_session

    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({"error": "Session not active"}, status_code=404)

    data = await request.json()
    x = data.get("x")
    y = data.get("y")

    if x is None or y is None:
        return JSONResponse({"error": "x and y are required"}, status_code=400)

    try:
        ok = await engine.click_at(float(x), float(y))
        return JSONResponse({"success": ok})
    except Exception as e:
        logger.error("Failed click proxy for {}: {}", session_id, e)
        return JSONResponse({"error": f"Failed to click: {str(e)}"}, status_code=500)


@app.post("/api/apply/session/{session_id}/cancel")
async def cancel_apply_session(session_id: str) -> JSONResponse:
    """Cancel and cleanup an apply session."""
    from .linkedin.apply_session import get_active_session, cleanup_session
    
    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({
            "error": "Session not found or not active",
        }, status_code=404)
    
    try:
        await cleanup_session(session_id)
        return JSONResponse({
            "success": True,
            "message": "Session cancelled",
        })
    except Exception as e:
        logger.error("Failed to cancel session: {}", e)
        return JSONResponse({
            "error": f"Failed to cancel: {str(e)}",
        }, status_code=500)


@app.post("/api/apply/session/{session_id}/pause")
async def pause_apply_session(session_id: str) -> JSONResponse:
    """Pause screenshot streaming for the session."""
    from .linkedin.apply_session import get_active_session
    
    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({
            "error": "Session not active",
        }, status_code=404)
    
    await engine.pause()
    return JSONResponse({"success": True, "paused": True})


@app.post("/api/apply/session/{session_id}/resume")
async def resume_apply_session(session_id: str) -> JSONResponse:
    """Resume screenshot streaming for the session."""
    from .linkedin.apply_session import get_active_session
    
    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({
            "error": "Session not active",
        }, status_code=404)
    
    await engine.resume()
    return JSONResponse({"success": True, "paused": False})


@app.get("/api/apply/session/{session_id}/screenshot")
async def get_apply_screenshot(session_id: str) -> JSONResponse:
    """Get the latest screenshot from the session."""
    from .linkedin.apply_session import get_active_session
    
    engine = get_active_session(session_id)
    if not engine:
        return JSONResponse({
            "error": "Session not active",
        }, status_code=404)
    
    screenshot = await engine.get_screenshot()
    if screenshot:
        return JSONResponse({
            "screenshot": screenshot,
            "status": engine.session.status.value if engine.session else "unknown",
        })
    
    return JSONResponse({
        "error": "Failed to capture screenshot",
    }, status_code=500)


@app.websocket("/ws/apply/{session_id}")
async def websocket_apply_session(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for live apply session streaming.
    
    Receives real-time updates including:
    - Screenshots (base64 encoded)
    - Status changes
    - Field detections
    - Action results
    - Errors
    """
    from .linkedin.apply_session import get_active_session
    
    await websocket.accept()
    
    engine = get_active_session(session_id)
    if not engine:
        await websocket.send_json({
            "type": "error",
            "data": {"message": "Session not found or not active"},
        })
        await websocket.close()
        return
    
    # Register client
    engine.add_websocket_client(websocket)
    
    try:
        # Send initial status
        if engine.session:
            await websocket.send_json({
                "type": "status",
                "data": {
                    "status": engine.session.status.value,
                    "current_step": engine.session.current_step,
                    "total_steps": engine.session.total_steps,
                },
            })
            
            # Send current fields
            if engine.session.detected_fields:
                await websocket.send_json({
                    "type": "fields",
                    "data": {
                        "fields": [f.to_dict() for f in engine.session.detected_fields],
                    },
                })
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()
                
                # Handle client commands
                if data.get("command") == "fill_field":
                    field_id = data.get("field_id")
                    value = data.get("value")
                    if field_id and value is not None:
                        await engine.fill_field(field_id, value)
                
                elif data.get("command") == "fill_all":
                    await engine.fill_all_suggested()
                
                elif data.get("command") == "next_step":
                    await engine.next_step()
                
                elif data.get("command") == "submit":
                    if data.get("confirmed"):
                        await engine.submit(confirmed=True)
                
                elif data.get("command") == "pause":
                    await engine.pause()
                
                elif data.get("command") == "resume":
                    await engine.resume()
                
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug("WebSocket message error: {}", e)
                break
    
    finally:
        engine.remove_websocket_client(websocket)


@app.get("/api/apply/active-sessions")
async def get_active_apply_sessions() -> JSONResponse:
    """Get list of all active apply sessions."""
    from .linkedin.apply_session import _active_sessions
    
    sessions = []
    for session_id, engine in _active_sessions.items():
        if engine.session:
            sessions.append({
                "session_id": session_id,
                "job_id": engine.session.job_id,
                "job_title": engine.session.job_title,
                "company": engine.session.company,
                "status": engine.session.status.value,
                "started_at": engine.session.started_at.isoformat(),
            })
    
    return JSONResponse({
        "sessions": sessions,
        "count": len(sessions),
    })


# ============================================================================
# Career Site API Endpoints
# ============================================================================

_careers_scrape_status: Dict[str, Any] = {
    "running": False,
    "company_id": None,
    "company_name": None,
    "total_companies": 0,
    "completed_companies": 0,
    "jobs_found": 0,
    "duplicates": 0,
    "errors": [],
}
_careers_scrape_start_lock = asyncio.Lock()


def _set_careers_status_running(*, total_companies: int, company_id: Optional[int], company_name: Optional[str]) -> None:
    """Initialize careers scrape status at scrape start."""
    global _careers_scrape_status
    _careers_scrape_status = {
        "running": True,
        "company_id": company_id,
        "company_name": company_name,
        "total_companies": total_companies,
        "completed_companies": 0,
        "jobs_found": 0,
        "duplicates": 0,
        "errors": [],
        "pending_review_total": 0,
    }


def _partition_jobs_for_staging(jobs: List[JobRecord]) -> tuple[List[JobRecord], int]:
    """
    Split scraped jobs into:
    - jobs safe to stage
    - duplicates already present in jobs or pending staging
    """
    jobs_to_stage: List[JobRecord] = []
    skipped_known = 0
    for job in jobs:
        if careers_job_exists_in_jobs_or_staging(job):
            skipped_known += 1
            continue
        jobs_to_stage.append(job)
    return jobs_to_stage, skipped_known


@app.get("/api/companies")
async def list_companies(enabled_only: bool = False) -> JSONResponse:
    """List all tracked companies for career site scraping."""
    companies = get_all_companies(enabled_only=enabled_only)
    out = []
    for c in companies:
        d = c.to_dict()
        d["pending_review_count"] = get_staging_count_by_company(c.id) if c.id else 0
        out.append(d)
    return JSONResponse({
        "companies": out,
        "count": len(companies),
    })


@app.post("/api/companies")
async def add_company(request: Request) -> JSONResponse:
    """
    Add a company for career site tracking.
    
    Request body:
    {
        "careers_url": "https://boards.greenhouse.io/stripe",
        "name": "Stripe"  // optional, auto-detected if not provided
    }
    Supports branded URLs (e.g. careers.philips.com); redirects are resolved before detection.
    """
    from .careers.detector import (
        detect_ats_type,
        extract_company_name_from_url,
        validate_careers_url,
        resolve_careers_url,
    )
    from .models import Company, ATSType
    from urllib.parse import urlparse

    data = await request.json()
    careers_url = data.get("careers_url", "").strip()
    name = data.get("name", "").strip()

    if not careers_url:
        return JSONResponse({
            "error": "careers_url is required",
        }, status_code=400)

    # Resolve redirects so branded URLs (e.g. careers.philips.com) become direct ATS URLs
    final_url, resolve_error = await resolve_careers_url(careers_url)
    if resolve_error:
        final_url = careers_url if careers_url.startswith(("http://", "https://")) else "https://" + careers_url
    url_for_detection = final_url

    ats_type, board_token = detect_ats_type(url_for_detection)

    if ats_type == ATSType.UNKNOWN:
        return JSONResponse({
            "error": "Could not detect ATS type from URL. Supported: Greenhouse, Lever, Workday",
        }, status_code=400)

    if ats_type not in (ATSType.GREENHOUSE, ATSType.LEVER, ATSType.WORKDAY):
        return JSONResponse({
            "error": f"{ats_type.value.title()} is not yet supported. Supported: Greenhouse, Lever, Workday",
        }, status_code=400)

    # For Workday, derive board_token from hostname if regex did not capture it
    if not board_token and ats_type == ATSType.WORKDAY:
        parsed = urlparse(url_for_detection)
        host = (parsed.netloc or "").lower()
        if "myworkdayjobs" in host:
            board_token = host.split(".")[0] if host else None
    if not board_token and ats_type != ATSType.WORKDAY:
        return JSONResponse({
            "error": "Could not extract board token from URL",
        }, status_code=400)

    if not name:
        name = extract_company_name_from_url(url_for_detection) or (board_token or "Company").title()

    is_valid, message = await validate_careers_url(url_for_detection)
    if not is_valid:
        return JSONResponse({
            "error": message,
        }, status_code=400)

    company = Company(
        name=name,
        careers_url=url_for_detection,
        ats_type=ats_type,
        board_token=board_token,
        enabled=True,
    )

    company = insert_company(company)

    logger.info("Added company {} with ATS {} (token: {})", name, ats_type.value, board_token)

    return JSONResponse({
        "success": True,
        "company": company.to_dict(),
        "validation_message": message,
    })


@app.get("/api/companies/{company_id}")
async def get_company(company_id: int) -> JSONResponse:
    """Get a single company by ID."""
    company = get_company_by_id(company_id)
    if not company:
        return JSONResponse({
            "error": "Company not found",
        }, status_code=404)
    
    return JSONResponse({
        "company": company.to_dict(),
    })


@app.delete("/api/companies/{company_id}")
async def remove_company(company_id: int) -> JSONResponse:
    """Delete a company from tracking."""
    company = get_company_by_id(company_id)
    if not company:
        return JSONResponse({
            "error": "Company not found",
        }, status_code=404)
    
    deleted = delete_company(company_id)
    
    return JSONResponse({
        "success": deleted,
        "message": f"Deleted company: {company.name}" if deleted else "Failed to delete",
    })


@app.post("/api/companies/{company_id}/toggle")
async def toggle_company(company_id: int, request: Request) -> JSONResponse:
    """Enable or disable a company."""
    data = await request.json()
    enabled = data.get("enabled", True)
    
    company = get_company_by_id(company_id)
    if not company:
        return JSONResponse({
            "error": "Company not found",
        }, status_code=404)
    
    toggle_company_enabled(company_id, enabled)
    
    return JSONResponse({
        "success": True,
        "enabled": enabled,
    })


@app.post("/api/companies/{company_id}/scrape")
async def scrape_company(company_id: int, request: Request) -> JSONResponse:
    """
    Scrape jobs from a single company's career site.

    Optional JSON body: {"location_filters": ["Israel", "Remote"]}.
    For Workday, filter is applied on the career site when possible; for others,
    only jobs matching location are added to staging.
    """
    from .careers.base import normalize_location_filters
    from .careers.registry import get_scraper_for_company

    location_filters = None
    try:
        body = await request.json()
        if body and isinstance(body.get("location_filters"), list):
            location_filters = normalize_location_filters(body["location_filters"])
    except Exception:
        pass

    company = get_company_by_id(company_id)
    if not company:
        return JSONResponse({
            "error": "Company not found",
        }, status_code=404)
    
    if not company.enabled:
        return JSONResponse({
            "error": "Company is disabled. Enable it first.",
        }, status_code=400)
    
    scraper = get_scraper_for_company(company)
    if not scraper:
        return JSONResponse({
            "error": f"No scraper available for ATS type: {company.ats_type.value}",
        }, status_code=400)

    async with _careers_scrape_start_lock:
        if _careers_scrape_status["running"]:
            return JSONResponse({
                "error": "A scrape is already in progress",
            }, status_code=409)
        _set_careers_status_running(
            total_companies=1,
            company_id=company.id,
            company_name=company.name,
        )

    try:
        result = await scraper.fetch_jobs(company, location_filters=location_filters)
        jobs_to_stage, skipped_known = _partition_jobs_for_staging(result.jobs)
        total_duplicates = result.duplicates + skipped_known

        # Write to staging only; user reviews and approves before jobs enter pipeline
        run_id = create_scrape_run(
            company_id=company_id,
            total_found=result.total_found,
            new_count=len(jobs_to_stage),
            duplicates_count=total_duplicates,
            errors=result.errors,
        )
        for job in jobs_to_stage:
            insert_staging_job(run_id, job)

        pending_review_count = len(jobs_to_stage)
        return JSONResponse({
            "success": True,
            "company": company.name,
            "total_found": result.total_found,
            "new_jobs": pending_review_count,
            "duplicates": total_duplicates,
            "errors": result.errors,
            "run_id": run_id,
            "pending_review_count": pending_review_count,
        })

    except Exception as e:
        logger.error("Failed to scrape company {}: {}", company.name, e)
        return JSONResponse({
            "error": f"Scrape failed: {str(e)}",
        }, status_code=500)
    finally:
        _careers_scrape_status["running"] = False
        _careers_scrape_status["company_id"] = None
        _careers_scrape_status["company_name"] = None
        await scraper.close()


@app.post("/api/careers/scrape-all")
async def scrape_all_companies(request: Request) -> JSONResponse:
    """
    Scrape jobs from all enabled companies (background task).
    Optional JSON body: {"location_filters": ["Israel", "Remote"]}.
    """
    global _careers_scrape_status

    location_filters = None
    try:
        body = await request.json()
        if body and isinstance(body.get("location_filters"), list):
            from .careers.base import normalize_location_filters
            location_filters = normalize_location_filters(body["location_filters"])
    except Exception:
        pass

    companies = get_all_companies(enabled_only=True)
    if not companies:
        return JSONResponse({
            "error": "No enabled companies to scrape",
        }, status_code=400)
    async with _careers_scrape_start_lock:
        if _careers_scrape_status["running"]:
            return JSONResponse({
                "error": "A scrape is already in progress",
            }, status_code=409)
        _set_careers_status_running(
            total_companies=len(companies),
            company_id=None,
            company_name=None,
        )
    asyncio.create_task(_background_scrape_all_companies(companies, location_filters))
    
    return JSONResponse({
        "success": True,
        "message": f"Started scraping {len(companies)} companies",
        "company_count": len(companies),
    })


async def _background_scrape_all_companies(
    companies: List,
    location_filters: Optional[List[str]] = None,
) -> None:
    """Background task to scrape all companies. Optional location_filters applied at source (Workday) or after fetch (others)."""
    global _careers_scrape_status
    from .careers.registry import get_scraper_for_company

    try:
        for company in companies:
            if not _careers_scrape_status["running"]:
                break
            _careers_scrape_status["company_id"] = company.id
            _careers_scrape_status["company_name"] = company.name
            
            scraper = get_scraper_for_company(company)
            if not scraper:
                _careers_scrape_status["errors"].append(f"No scraper for {company.name}")
                _careers_scrape_status["completed_companies"] += 1
                continue
            
            try:
                result = await scraper.fetch_jobs(company, location_filters=location_filters)
                jobs_to_stage, skipped_known = _partition_jobs_for_staging(result.jobs)
                total_duplicates = result.duplicates + skipped_known

                # Write to staging only; user reviews and approves before jobs enter pipeline
                run_id = create_scrape_run(
                    company_id=company.id,
                    total_found=result.total_found,
                    new_count=len(jobs_to_stage),
                    duplicates_count=total_duplicates,
                    errors=result.errors,
                )
                for job in jobs_to_stage:
                    insert_staging_job(run_id, job)
                inserted_count = len(jobs_to_stage)

                _careers_scrape_status["jobs_found"] += inserted_count
                _careers_scrape_status["pending_review_total"] += inserted_count
                _careers_scrape_status["duplicates"] += total_duplicates
                _careers_scrape_status["errors"].extend(result.errors)
                
                logger.info("Scraped {}: {} new jobs (staging), {} duplicates",
                          company.name, inserted_count, total_duplicates)
                
            except Exception as e:
                _careers_scrape_status["errors"].append(f"{company.name}: {str(e)}")
                logger.error("Failed to scrape {}: {}", company.name, e)
            finally:
                await scraper.close()
            
            _careers_scrape_status["completed_companies"] += 1
            
    finally:
        _careers_scrape_status["running"] = False
        _careers_scrape_status["company_id"] = None
        _careers_scrape_status["company_name"] = None
        logger.info("Completed scraping all companies: {} jobs in staging",
                   _careers_scrape_status["jobs_found"])


@app.get("/api/careers/status")
async def get_careers_scrape_status() -> JSONResponse:
    """Get the current status of career site scraping."""
    return JSONResponse(_careers_scrape_status)


@app.post("/api/careers/stop")
async def stop_careers_scrape() -> JSONResponse:
    """Request to stop the current scrape (not immediately effective)."""
    global _careers_scrape_status
    
    if not _careers_scrape_status["running"]:
        return JSONResponse({
            "error": "No scrape in progress",
        }, status_code=400)
    
    _careers_scrape_status["running"] = False
    
    return JSONResponse({
        "success": True,
        "message": "Stop requested. Scrape will stop after current company.",
    })


@app.get("/api/careers/runs")
async def list_careers_runs(
    company_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    pending_only: bool = Query(True),
) -> JSONResponse:
    """List scrape runs with pending staging jobs. Optional filter by company_id."""
    runs = get_runs(company_id=company_id, limit=limit, pending_only=pending_only)
    out = []
    for run in runs:
        company = get_company_by_id(run.company_id)
        out.append({
            "id": run.id,
            "company_id": run.company_id,
            "company_name": company.name if company else None,
            "scraped_at": run.scraped_at.isoformat() if run.scraped_at else None,
            "total_found": run.total_found,
            "new_count": run.new_count,
            "duplicates_count": run.duplicates_count,
            "errors": run.errors,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "pending_count": run.pending_count,
        })
    return JSONResponse({"runs": out})


@app.get("/api/careers/runs/{run_id}/jobs")
async def get_careers_run_jobs(run_id: int) -> JSONResponse:
    """List staging jobs for a run (for review UI)."""
    run = get_run_by_id(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    jobs = get_staging_jobs(run_id)
    company = get_company_by_id(run.company_id)
    return JSONResponse({
        "run_id": run_id,
        "company_name": company.name if company else None,
        "company_id": run.company_id,
        "scraped_at": run.scraped_at.isoformat() if run.scraped_at else None,
        "pending_count": run.pending_count,
        "jobs": jobs,
    })


@app.post("/api/careers/runs/{run_id}/approve")
async def approve_careers_run(run_id: int, request: Request) -> JSONResponse:
    """
    Approve selected staging jobs (or all). Moves them into the jobs table.
    Body: {"job_ids": [1,2,3]} or {"approve_all": true}.
    """
    run = get_run_by_id(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    try:
        body = await request.json() or {}
    except Exception:
        body = {}
    if body.get("approve_all"):
        staging_jobs = get_staging_jobs(run_id)
        job_ids = [j["id"] for j in staging_jobs]
    else:
        job_ids = body.get("job_ids") or []
    if not job_ids:
        return JSONResponse({
            "error": "Provide job_ids or approve_all: true",
        }, status_code=400)
    approved, skipped = approve_staging_jobs(run_id, job_ids)
    return JSONResponse({
        "success": True,
        "approved": approved,
        "skipped_duplicates": skipped,
    })


@app.post("/api/careers/runs/{run_id}/discard")
async def discard_careers_run(run_id: int) -> JSONResponse:
    """Discard all staging jobs for this run."""
    run = get_run_by_id(run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    discard_run(run_id)
    return JSONResponse({"success": True}, status_code=200)


@app.post("/api/careers/validate-url")
async def validate_career_url(request: Request) -> JSONResponse:
    """
    Validate a career site URL before adding.
    
    Request body: {"url": "https://boards.greenhouse.io/stripe"}
    """
    from .careers.detector import detect_ats_type, validate_careers_url, extract_company_name_from_url
    
    data = await request.json()
    url = data.get("url", "").strip()
    
    if not url:
        return JSONResponse({
            "error": "URL is required",
        }, status_code=400)
    
    ats_type, board_token = detect_ats_type(url)
    suggested_name = extract_company_name_from_url(url)
    
    is_valid, message = await validate_careers_url(url)
    
    return JSONResponse({
        "valid": is_valid,
        "message": message,
        "ats_type": ats_type.value if ats_type else None,
        "board_token": board_token,
        "suggested_name": suggested_name,
    })


def _build_suggested_action(
    jobs_pending: int,
    review_pending: int,
    pending_scrape: int,
    pending_match: int,
) -> Optional[Dict[str, Any]]:
    """
    Next-best-action rule: prefer review_pulled when review_pending > 0,
    else process_pending when jobs_pending > 0; otherwise null.
    """
    if review_pending > 0:
        return {
            "action": "review_pulled",
            "label": "Review pulled",
            "url": "/careers/review",
            "message": f"{review_pending} job{'s' if review_pending != 1 else ''} pending review",
        }
    if jobs_pending > 0:
        return {
            "action": "process_pending",
            "label": "Process All Pending",
            "url": "/jobs",
            "message": f"{jobs_pending} job{'s' if jobs_pending != 1 else ''} pending processing",
        }
    return None


@app.get("/api/badges")
async def get_nav_badges() -> JSONResponse:
    """
    Return lightweight counts for navigation badges and next-best-action notification.
    
    - jobs_pending: number of jobs in the main pipeline (PENDING_SCRAPE or PENDING_MATCH).
    - review_pending: total number of staging jobs pending review across all companies.
    - pending_scrape: count of jobs in PENDING_SCRAPE only.
    - pending_match: count of jobs in PENDING_MATCH only.
    - suggested_action: when non-null, { action, label, url, message } for the notification strip.
      Rule: review_pulled if review_pending > 0, else process_pending if jobs_pending > 0, else null.
    """
    jobs_pending = get_pending_jobs_count()
    review_pending = get_total_staging_jobs_count()
    pending_scrape = get_pending_scrape_count()
    pending_match = get_pending_match_count()
    suggested_action = _build_suggested_action(
        jobs_pending, review_pending, pending_scrape, pending_match
    )
    payload: Dict[str, Any] = {
        "jobs_pending": jobs_pending,
        "review_pending": review_pending,
        "pending_scrape": pending_scrape,
        "pending_match": pending_match,
        "suggested_action": suggested_action,
    }
    return JSONResponse(payload)


def _dashboard_data() -> Dict[str, Any]:
    """Build dashboard counts, suggested action, and top high-match jobs. Reused by GET /dashboard and GET /api/dashboard."""
    jobs_pending = get_pending_jobs_count()
    review_pending = get_total_staging_jobs_count()
    pending_scrape = get_pending_scrape_count()
    pending_match = get_pending_match_count()
    matched_count = get_matched_count()
    applied_count = get_applied_count()
    suggested_action = _build_suggested_action(
        jobs_pending, review_pending, pending_scrape, pending_match
    )
    # Top jobs: Apply/Consider, exclude applied, sort by score desc, limit 10
    top_jobs: List[JobRecord] = []
    top_match_results: Dict[int, Any] = {}
    jobs_list, _, _ = get_jobs_paginated(
        page=1,
        per_page=10,
        recommendation_filters=["apply", "consider"],
        hide_applied=True,
        sort_by="score",
        sort_dir="desc",
    )
    if jobs_list:
        top_jobs = jobs_list
        job_ids = [j.id for j in jobs_list if j.id]
        match_results = get_match_results_for_jobs(job_ids)
        top_match_results = {
            jid: {"match_score": mr.match_score, "recommendation": mr.recommendation}
            for jid, mr in match_results.items()
        }
    return {
        "pending_scrape": pending_scrape,
        "pending_match": pending_match,
        "jobs_pending": jobs_pending,
        "review_pending": review_pending,
        "matched_count": matched_count,
        "applied_count": applied_count,
        "suggested_action": suggested_action,
        "top_jobs": top_jobs,
        "top_match_results": top_match_results,
    }


@app.get("/dashboard", response_class=RedirectResponse)
async def dashboard_redirect() -> RedirectResponse:
    """Redirect /dashboard to / so Home and dashboard are the same."""
    return RedirectResponse(url="/", status_code=302)


@app.get("/getting-started", response_class=HTMLResponse)
async def getting_started_page(request: Request) -> HTMLResponse:
    """Getting started / How it works guide (secondary to home dashboard)."""
    return templates.TemplateResponse(
        "getting_started.html",
        {"request": request},
    )


@app.get("/api/dashboard")
async def get_dashboard_api() -> JSONResponse:
    """Return dashboard counts, suggested action, and top jobs as JSON (for refresh or SPA)."""
    data = _dashboard_data()
    # Serialize top_jobs and top_match_results for JSON
    top_jobs_data = [
        {
            "id": j.id,
            "title": j.title,
            "company": j.company,
            "location": j.location or "",
            "url": str(j.url),
            "match_score": data["top_match_results"].get(j.id or 0, {}).get("match_score"),
            "recommendation": data["top_match_results"].get(j.id or 0, {}).get("recommendation"),
        }
        for j in data["top_jobs"]
    ]
    payload: Dict[str, Any] = {
        "pending_scrape": data["pending_scrape"],
        "pending_match": data["pending_match"],
        "jobs_pending": data["jobs_pending"],
        "review_pending": data["review_pending"],
        "matched_count": data["matched_count"],
        "applied_count": data["applied_count"],
        "suggested_action": data["suggested_action"],
        "top_jobs": top_jobs_data,
    }
    return JSONResponse(payload)
