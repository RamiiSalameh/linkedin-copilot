from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple
import json
import uuid

from .config import get_settings
from .models import (
    JobRecord, JobStatus, MatchResult, extract_linkedin_job_id,
    ApplySession, ApplySessionStatus, FormField, ApplicationAction,
    ActionStatus, ActionType, FormFieldType,
    Company, ATSType, JobSource, ScrapeRun,
    PipelineTaskStatus, PipelineTaskType,
)
from .logging_setup import logger


def _get_db_path() -> Path:
    settings = get_settings()
    db_path = Path(settings.env.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


@contextmanager
def db_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database schema if not exists."""
    with db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                date_found TEXT NOT NULL,
                easy_apply INTEGER NOT NULL DEFAULT 0,
                description_snippet TEXT,
                full_description TEXT,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS match_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                match_score INTEGER NOT NULL,
                top_reasons TEXT,
                missing_requirements TEXT,
                suggested_resume_bullets TEXT,
                summary_markdown_path TEXT,
                raw_json_path TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
            """
        )
        # Search history table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                location TEXT NOT NULL,
                search_time TEXT NOT NULL,
                jobs_found INTEGER NOT NULL DEFAULT 0,
                filters TEXT,
                UNIQUE(keywords, location)
            )
            """
        )
        # Add full_description column if it doesn't exist (migration for existing DBs)
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN full_description TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Add company_logo_url column
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN company_logo_url TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Add inferred_qualifications column to match_results
        try:
            conn.execute("ALTER TABLE match_results ADD COLUMN inferred_qualifications TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Add linkedin_job_id column for deduplication
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN linkedin_job_id TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Add date_posted column for job posting date
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN date_posted TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Create index on linkedin_job_id for fast lookups
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_linkedin_job_id ON jobs(linkedin_job_id)")
        except sqlite3.OperationalError:
            pass
        # Migrate existing jobs: populate linkedin_job_id from URL
        cur = conn.execute("SELECT id, url FROM jobs WHERE linkedin_job_id IS NULL")
        rows = cur.fetchall()
        for row in rows:
            linkedin_id = extract_linkedin_job_id(row["url"])
            if linkedin_id:
                conn.execute(
                    "UPDATE jobs SET linkedin_job_id = ? WHERE id = ?",
                    (linkedin_id, row["id"])
                )
        if rows:
            logger.info("Migrated {} existing jobs with LinkedIn job IDs", len(rows))
        
        # Create exploration_sessions table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS exploration_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL,
                total_searches INTEGER DEFAULT 0,
                completed_searches INTEGER DEFAULT 0,
                total_jobs_found INTEGER DEFAULT 0,
                unique_jobs INTEGER DEFAULT 0,
                duplicates INTEGER DEFAULT 0,
                config TEXT,
                insights TEXT,
                explored_keywords TEXT
            )
            """
        )
        
        # Add effectiveness metrics to search_history
        try:
            conn.execute("ALTER TABLE search_history ADD COLUMN avg_match_score REAL")
        except sqlite3.OperationalError:
            pass
        
        try:
            conn.execute("ALTER TABLE search_history ADD COLUMN high_matches INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        
        try:
            conn.execute("ALTER TABLE search_history ADD COLUMN strategy_source TEXT")
        except sqlite3.OperationalError:
            pass
        
        # Create apply_sessions table for in-app application tracking
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS apply_sessions (
                id TEXT PRIMARY KEY,
                job_id INTEGER NOT NULL,
                job_title TEXT,
                company TEXT,
                job_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'idle',
                current_step INTEGER DEFAULT 1,
                total_steps INTEGER,
                detected_fields TEXT,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ended_at TEXT,
                error_message TEXT,
                screenshots_dir TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
            """
        )
        
        # Create session_actions table for action audit trail
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                target_field_id TEXT,
                target_selector TEXT,
                value TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                created_at TEXT NOT NULL,
                executed_at TEXT,
                FOREIGN KEY(session_id) REFERENCES apply_sessions(id)
            )
            """
        )

        # UI hints: global + per-user learned selectors/heuristics for LinkedIn UI components
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ui_hints_global (
                key TEXT PRIMARY KEY,
                hints_json TEXT NOT NULL,
                success_count INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ui_hints_user (
                profile_name TEXT NOT NULL,
                key TEXT NOT NULL,
                hints_json TEXT NOT NULL,
                success_count INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT,
                PRIMARY KEY(profile_name, key)
            )
            """
        )
        
        # Create index for faster session lookups
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_apply_sessions_job_id ON apply_sessions(job_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_actions_session_id ON session_actions(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ui_hints_user_key ON ui_hints_user(key)")
        except sqlite3.OperationalError:
            pass
        
        # Create companies table for career site tracking
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                careers_url TEXT NOT NULL UNIQUE,
                ats_type TEXT NOT NULL,
                board_token TEXT,
                logo_url TEXT,
                enabled INTEGER DEFAULT 1,
                last_scraped TEXT,
                total_jobs INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        
        # Add source column to jobs table for multi-source support
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN source TEXT DEFAULT 'linkedin'")
        except sqlite3.OperationalError:
            pass
        
        # Add company_id column to link jobs to tracked companies
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN company_id INTEGER REFERENCES companies(id)")
        except sqlite3.OperationalError:
            pass
        
        # Add external_job_id column for non-LinkedIn job IDs
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN external_job_id TEXT")
        except sqlite3.OperationalError:
            pass
        
        # Create indexes for career site jobs
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company_id ON jobs(company_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_external_id ON jobs(external_job_id)")
        except sqlite3.OperationalError:
            pass

        # Scrape runs: one row per scrape (single-company or one per company in scrape-all)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                scraped_at TEXT NOT NULL,
                total_found INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                duplicates_count INTEGER NOT NULL DEFAULT 0,
                errors TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES companies(id)
            )
            """
        )
        # Staging jobs: pulled jobs awaiting user approval before going into jobs table
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scraped_jobs_staging (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT NOT NULL,
                url TEXT NOT NULL,
                linkedin_job_id TEXT,
                external_job_id TEXT,
                date_found TEXT NOT NULL,
                date_posted TEXT,
                easy_apply INTEGER NOT NULL DEFAULT 0,
                description_snippet TEXT,
                company_logo_url TEXT,
                source TEXT NOT NULL DEFAULT 'linkedin',
                company_id INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES scrape_runs(id)
            )
            """
        )
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_run_id ON scraped_jobs_staging(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_company_id ON scraped_jobs_staging(company_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scrape_runs_company_id ON scrape_runs(company_id)")
        except sqlite3.OperationalError:
            pass

        # Persistent pipeline tasks (job queue)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_group_id TEXT,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                priority INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                retry_at TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                locked_by TEXT,
                locked_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_status ON pipeline_tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_group ON pipeline_tasks(task_group_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_locked ON pipeline_tasks(locked_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_retry ON pipeline_tasks(retry_at)")
        except sqlite3.OperationalError:
            pass

    logger.info("Database initialized at {}", _get_db_path())


def job_exists(url: str) -> bool:
    """Check if a job already exists by LinkedIn job ID or URL.
    
    Prioritizes LinkedIn job ID for deduplication since URLs can vary
    (trailing slashes, query params, etc.) but job IDs are unique.
    """
    linkedin_id = extract_linkedin_job_id(url)
    with db_connection() as conn:
        if linkedin_id:
            # Check by LinkedIn job ID first (more reliable)
            cur = conn.execute(
                "SELECT 1 FROM jobs WHERE linkedin_job_id = ?", 
                (linkedin_id,)
            )
            if cur.fetchone() is not None:
                return True
        # Fallback to URL check
        cur = conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,))
        return cur.fetchone() is not None


def insert_job(job: JobRecord) -> JobRecord:
    """Insert a job record, deduplicating by job ID (LinkedIn or external).
    
    If the job already exists (by linkedin_job_id or external_job_id), returns without inserting.
    """
    # Ensure linkedin_job_id is extracted for LinkedIn jobs
    if job.source == JobSource.LINKEDIN and job.linkedin_job_id is None:
        job.linkedin_job_id = extract_linkedin_job_id(str(job.url))
    
    with db_connection() as conn:
        # Check for existing job by linkedin_job_id first
        if job.linkedin_job_id:
            cur = conn.execute(
                "SELECT id FROM jobs WHERE linkedin_job_id = ?",
                (job.linkedin_job_id,)
            )
            existing = cur.fetchone()
            if existing:
                job.id = existing["id"]
                logger.debug("Job already exists with LinkedIn ID {}: DB id={}", 
                           job.linkedin_job_id, job.id)
                return job
        
        # Check for existing job by external_job_id (for career site jobs)
        if job.external_job_id and job.source != JobSource.LINKEDIN:
            cur = conn.execute(
                "SELECT id FROM jobs WHERE external_job_id = ? AND source = ?",
                (job.external_job_id, job.source.value)
            )
            existing = cur.fetchone()
            if existing:
                job.id = existing["id"]
                logger.debug("Job already exists with external ID {}: DB id={}", 
                           job.external_job_id, job.id)
                return job
        
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO jobs
            (title, company, location, url, linkedin_job_id, external_job_id, date_found, date_posted, 
             easy_apply, description_snippet, company_logo_url, status, source, company_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.title,
                job.company,
                job.location,
                str(job.url),
                job.linkedin_job_id,
                job.external_job_id,
                job.date_found.isoformat(),
                job.date_posted.isoformat() if job.date_posted else None,
                1 if job.easy_apply else 0,
                job.description_snippet,
                job.company_logo_url,
                job.status.value,
                job.source.value,
                job.company_id,
            ),
        )
        job_id = cur.lastrowid
        if job_id:
            job.id = job_id
            logger.debug("Inserted new job: {} (source: {}, ID: {})", 
                        job.title, job.source.value, job.external_job_id or job.linkedin_job_id)
    return job


def update_job_status(job_id: int, status: JobStatus) -> None:
    with db_connection() as conn:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status.value, job_id))


def _row_to_job(row: sqlite3.Row) -> JobRecord:
    """Convert a database row to a JobRecord."""
    keys = row.keys()
    date_posted = None
    if "date_posted" in keys and row["date_posted"]:
        date_posted = datetime.fromisoformat(row["date_posted"])
    
    source = JobSource.LINKEDIN
    if "source" in keys and row["source"]:
        try:
            source = JobSource(row["source"])
        except ValueError:
            source = JobSource.LINKEDIN
    
    return JobRecord(
        id=row["id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        url=row["url"],
        linkedin_job_id=row["linkedin_job_id"] if "linkedin_job_id" in keys else None,
        external_job_id=row["external_job_id"] if "external_job_id" in keys else None,
        date_found=datetime.fromisoformat(row["date_found"]),
        date_posted=date_posted,
        easy_apply=bool(row["easy_apply"]),
        description_snippet=row["description_snippet"],
        company_logo_url=row["company_logo_url"] if "company_logo_url" in keys else None,
        status=JobStatus(row["status"]),
        source=source,
        company_id=row["company_id"] if "company_id" in keys else None,
    )


def list_jobs_by_status(statuses: Iterable[JobStatus]) -> List[JobRecord]:
    placeholders = ",".join("?" for _ in statuses)
    with db_connection() as conn:
        cur = conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders})",
            tuple(s.value for s in statuses),
        )
        rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


def save_match_result(result: MatchResult) -> None:
    with db_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO match_results
            (job_id, match_score, top_reasons, missing_requirements, inferred_qualifications,
             suggested_resume_bullets, summary_markdown_path, raw_json_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.job_id,
                result.match_score,
                "|".join(result.top_reasons),
                "|".join(result.missing_requirements),
                "|".join(result.inferred_qualifications),
                "|".join(result.suggested_resume_bullets),
                result.summary_markdown_path,
                result.raw_json_path,
            ),
        )


def update_job_description(job_id: int, full_description: str) -> None:
    """Update a job's full description after scraping."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE jobs SET full_description = ? WHERE id = ?",
            (full_description, job_id),
        )


def update_job_logo(job_id: int, logo_url: str) -> None:
    """Update a job's company logo URL."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE jobs SET company_logo_url = ? WHERE id = ?",
            (logo_url, job_id),
        )


def update_job_company(job_id: int, company: str) -> None:
    """Update a job's company name (useful when fixing 'Unknown' companies)."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE jobs SET company = ? WHERE id = ?",
            (company, job_id),
        )
    logger.debug("Updated company for job {} to: {}", job_id, company)


def get_jobs_missing_logos() -> List[JobRecord]:
    """Get all jobs that don't have a valid local company logo.
    
    Includes jobs with NULL/empty logos and LinkedIn placeholder URLs.
    """
    with db_connection() as conn:
        cur = conn.execute(
            """SELECT * FROM jobs 
               WHERE company_logo_url IS NULL 
                  OR company_logo_url = '' 
                  OR company_logo_url LIKE 'https://media.licdn.com/%'
                  OR company_logo_url LIKE 'https://static.licdn.com/%'
               ORDER BY id DESC"""
        )
        rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


def clear_linkedin_logo_urls() -> int:
    """Clear LinkedIn logo URLs so they can be re-scraped locally.
    
    Returns the number of URLs cleared.
    """
    with db_connection() as conn:
        cur = conn.execute(
            "UPDATE jobs SET company_logo_url = NULL WHERE company_logo_url LIKE 'https://media.licdn.com/%'"
        )
        count = cur.rowcount
    logger.info("Cleared {} LinkedIn logo URLs", count)
    return count


def get_job_by_id(job_id: int) -> Optional[JobRecord]:
    """Retrieve a single job by ID."""
    with db_connection() as conn:
        cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def get_match_result(job_id: int) -> Optional[MatchResult]:
    """Retrieve the match result for a specific job."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM match_results WHERE job_id = ?", (job_id,)
        )
        row = cur.fetchone()
    if row is None:
        return None
    # Handle missing inferred_qualifications column for old data
    inferred = ""
    if "inferred_qualifications" in row.keys():
        inferred = row["inferred_qualifications"] or ""
    return MatchResult(
        job_id=row["job_id"],
        match_score=row["match_score"],
        top_reasons=row["top_reasons"].split("|") if row["top_reasons"] else [],
        missing_requirements=row["missing_requirements"].split("|") if row["missing_requirements"] else [],
        inferred_qualifications=inferred.split("|") if inferred else [],
        suggested_resume_bullets=row["suggested_resume_bullets"].split("|") if row["suggested_resume_bullets"] else [],
        summary_markdown_path=row["summary_markdown_path"],
        raw_json_path=row["raw_json_path"],
    )


def get_all_match_results() -> Dict[int, MatchResult]:
    """Get all match results as a dict keyed by job_id."""
    with db_connection() as conn:
        cur = conn.execute("SELECT * FROM match_results")
        rows = cur.fetchall()
    results: Dict[int, MatchResult] = {}
    for row in rows:
        # Handle missing inferred_qualifications column for old data
        inferred = ""
        if "inferred_qualifications" in row.keys():
            inferred = row["inferred_qualifications"] or ""
        results[row["job_id"]] = MatchResult(
            job_id=row["job_id"],
            match_score=row["match_score"],
            top_reasons=row["top_reasons"].split("|") if row["top_reasons"] else [],
            missing_requirements=row["missing_requirements"].split("|") if row["missing_requirements"] else [],
            inferred_qualifications=inferred.split("|") if inferred else [],
            suggested_resume_bullets=row["suggested_resume_bullets"].split("|") if row["suggested_resume_bullets"] else [],
            summary_markdown_path=row["summary_markdown_path"],
            raw_json_path=row["raw_json_path"],
        )
    return results


def get_job_full_description(job_id: int) -> Optional[str]:
    """Get the full description for a job if available."""
    with db_connection() as conn:
        cur = conn.execute("SELECT full_description FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return row["full_description"]


def get_all_jobs() -> List[JobRecord]:
    """Get all jobs regardless of status."""
    with db_connection() as conn:
        cur = conn.execute("SELECT * FROM jobs ORDER BY id DESC")
        rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


def get_jobs_paginated(
    page: int = 1,
    per_page: int = 25,
    status_filters: Optional[List[str]] = None,
    # Back-compat (older tests/callers)
    status_filter: Optional[str] = None,
    search_query: Optional[str] = None,
    recommendation_filters: Optional[List[str]] = None,
    # Back-compat (older tests/callers)
    recommendation_filter: Optional[str] = None,
    hide_applied: bool = False,
    sort_by: str = "id",
    sort_dir: str = "desc",
    source_filter: Optional[str] = None,
    company_filters: Optional[List[str]] = None,
    title_filters: Optional[List[str]] = None,
    location_filters: Optional[List[str]] = None,
) -> Tuple[List[JobRecord], int, Dict[str, int]]:
    """
    Get jobs with pagination, filtering, and sorting.
    
    Args:
        page: Page number (1-indexed)
        per_page: Number of items per page
        status_filters: Filter by job status (multi)
        search_query: Search in title, company, and location
        recommendation_filters: Filter by recommendation (multi: apply, consider, skip)
        hide_applied: If True, exclude jobs with 'applied' status
        sort_by: Column to sort by ('id', 'title', 'company', 'location', 'score', 'status', 'posted', 'added')
        sort_dir: Sort direction ('asc' or 'desc')
        source_filter: Filter by source
        company_filters: Include only these companies (exact match, case-insensitive)
        title_filters: Include only these titles (substring match, case-insensitive)
        location_filters: Include only these locations (substring match, case-insensitive)
    
    Returns:
        Tuple of (jobs list, total count matching filters, status counts dict)
    """
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 25
    if per_page > 100:
        per_page = 100

    # Back-compat mapping: accept single-value filters
    if status_filters is None and status_filter:
        status_filters = [status_filter]
    if recommendation_filters is None and recommendation_filter:
        recommendation_filters = [recommendation_filter]
    
    offset = (page - 1) * per_page
    
    valid_sort_columns = {
        "id": "j.id",
        "title": "j.title",
        "company": "j.company",
        "location": "j.location",
        "status": "j.status",
        "posted": "j.date_posted",
        "added": "j.date_found",
        "score": "COALESCE(m.match_score, -1)",
    }
    sort_column = valid_sort_columns.get(sort_by, "j.id")
    sort_direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    
    where_clauses = ["j.status != ?"]
    params: List = [JobStatus.DELETED.value]
    
    # Status: multi (API sends list)
    status_list = list(status_filters) if status_filters else []
    if status_list:
        placeholders = ",".join("?" for _ in status_list)
        where_clauses.append(f"j.status IN ({placeholders})")
        params.extend(status_list)
    
    if search_query:
        search_pattern = f"%{search_query}%"
        where_clauses.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)")
        params.extend([search_pattern, search_pattern, search_pattern])
    
    # Recommendation: multi (score bands)
    rec_list = list(recommendation_filters) if recommendation_filters else []
    if rec_list:
        rec_conditions = []
        for r in rec_list:
            if r == "apply":
                rec_conditions.append("m.match_score >= 70")
            elif r == "consider":
                rec_conditions.append("(m.match_score >= 50 AND m.match_score < 70)")
            elif r == "skip":
                rec_conditions.append("m.match_score < 50")
        if rec_conditions:
            where_clauses.append("(" + " OR ".join(rec_conditions) + ")")
    
    if hide_applied:
        where_clauses.append("j.status != 'applied'")
    
    if source_filter:
        where_clauses.append("j.source = ?")
        params.append(source_filter)
    
    if company_filters:
        placeholders = ",".join("?" for _ in company_filters)
        where_clauses.append(f"LOWER(j.company) IN ({placeholders})")
        params.extend([c.strip().lower() for c in company_filters if c and c.strip()])
    
    if title_filters:
        title_conditions = []
        for t in title_filters:
            if t and t.strip():
                title_conditions.append("LOWER(j.title) LIKE ?")
                params.append(f"%{t.strip().lower()}%")
        if title_conditions:
            where_clauses.append("(" + " OR ".join(title_conditions) + ")")
    
    if location_filters:
        location_conditions = []
        for loc in location_filters:
            if loc and loc.strip():
                location_conditions.append("LOWER(j.location) LIKE ?")
                params.append(f"%{loc.strip().lower()}%")
        if location_conditions:
            where_clauses.append("(" + " OR ".join(location_conditions) + ")")
    
    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)
    
    with db_connection() as conn:
        count_sql = f"""
            SELECT COUNT(*) as cnt FROM jobs j
            LEFT JOIN match_results m ON j.id = m.job_id
            {where_sql}
        """
        count_cur = conn.execute(count_sql, params)
        total_count = count_cur.fetchone()["cnt"]
        
        query_sql = f"""
            SELECT j.* FROM jobs j
            LEFT JOIN match_results m ON j.id = m.job_id
            {where_sql}
            ORDER BY {sort_column} {sort_direction}
            LIMIT ? OFFSET ?
        """
        cur = conn.execute(query_sql, params + [per_page, offset])
        rows = cur.fetchall()
        
        # Apply same filters to status counts so summary bar matches the filtered list
        status_counts_sql = f"""
            SELECT 
                SUM(CASE WHEN j.status = 'pending_scrape' THEN 1 ELSE 0 END) as pending_scrape,
                SUM(CASE WHEN j.status = 'pending_match' THEN 1 ELSE 0 END) as pending_match,
                SUM(CASE WHEN j.status = 'matched' THEN 1 ELSE 0 END) as matched,
                SUM(CASE WHEN j.status = 'applied' THEN 1 ELSE 0 END) as applied,
                COUNT(*) as total
            FROM jobs j
            LEFT JOIN match_results m ON j.id = m.job_id
            {where_sql}
        """
        counts_cur = conn.execute(status_counts_sql, params)
        counts_row = counts_cur.fetchone()
        status_counts = {
            "pending_scrape": counts_row["pending_scrape"] or 0,
            "pending_match": counts_row["pending_match"] or 0,
            "matched": counts_row["matched"] or 0,
            "applied": counts_row["applied"] or 0,
            "total": counts_row["total"] or 0,
        }
    
    jobs = [_row_to_job(row) for row in rows]
    return jobs, total_count, status_counts


def get_jobs_facets(
    column: str,
    limit: int = 200,
    search_query: Optional[str] = None,
    status_filters: Optional[List[str]] = None,
    recommendation_filters: Optional[List[str]] = None,
    hide_applied: bool = False,
    source_filter: Optional[str] = None,
    company_filters: Optional[List[str]] = None,
    title_filters: Optional[List[str]] = None,
    location_filters: Optional[List[str]] = None,
) -> List[str]:
    """
    Return distinct values for a column, respecting current filters.
    Used to populate column filter dropdowns.
    """
    valid_columns = {"company": "j.company", "title": "j.title", "location": "j.location", "status": "j.status"}
    if column not in valid_columns:
        return []
    col_expr = valid_columns[column]
    where_clauses = ["j.status != ?"]
    params: List = [JobStatus.DELETED.value]
    if status_filters:
        placeholders = ",".join("?" for _ in status_filters)
        where_clauses.append(f"j.status IN ({placeholders})")
        params.extend(status_filters)
    if search_query:
        search_pattern = f"%{search_query}%"
        where_clauses.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)")
        params.extend([search_pattern, search_pattern, search_pattern])
    if recommendation_filters:
        rec_conditions = []
        for r in recommendation_filters:
            if r == "apply":
                rec_conditions.append("m.match_score >= 70")
            elif r == "consider":
                rec_conditions.append("(m.match_score >= 50 AND m.match_score < 70)")
            elif r == "skip":
                rec_conditions.append("m.match_score < 50")
        if rec_conditions:
            where_clauses.append("(" + " OR ".join(rec_conditions) + ")")
    if hide_applied:
        where_clauses.append("j.status != 'applied'")
    if source_filter:
        where_clauses.append("j.source = ?")
        params.append(source_filter)
    if company_filters:
        placeholders = ",".join("?" for _ in company_filters)
        where_clauses.append(f"LOWER(j.company) IN ({placeholders})")
        params.extend([c.strip().lower() for c in company_filters if c and c.strip()])
    if title_filters:
        title_conditions = []
        for t in title_filters:
            if t and t.strip():
                title_conditions.append("LOWER(j.title) LIKE ?")
                params.append(f"%{t.strip().lower()}%")
        if title_conditions:
            where_clauses.append("(" + " OR ".join(title_conditions) + ")")
    if location_filters:
        location_conditions = []
        for loc in location_filters:
            if loc and loc.strip():
                location_conditions.append("LOWER(j.location) LIKE ?")
                params.append(f"%{loc.strip().lower()}%")
        if location_conditions:
            where_clauses.append("(" + " OR ".join(location_conditions) + ")")
    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    order = "ASC" if column == "status" else "ASC"
    with db_connection() as conn:
        if column == "status":
            cur = conn.execute(
                f"""
                SELECT DISTINCT j.status as val FROM jobs j
                LEFT JOIN match_results m ON j.id = m.job_id
                WHERE {where_sql}
                ORDER BY j.status {order}
                LIMIT ?
                """,
                params + [limit],
            )
        else:
            cur = conn.execute(
                f"""
                SELECT DISTINCT {col_expr} as val FROM jobs j
                LEFT JOIN match_results m ON j.id = m.job_id
                WHERE {where_sql} AND {col_expr} IS NOT NULL AND TRIM({col_expr}) != ''
                ORDER BY {col_expr} {order}
                LIMIT ?
                """,
                params + [limit],
            )
        rows = cur.fetchall()
    return [str(row["val"]) for row in rows]


def get_match_results_for_jobs(job_ids: List[int]) -> Dict[int, MatchResult]:
    """Get match results for a list of job IDs."""
    if not job_ids:
        return {}
    placeholders = ",".join("?" for _ in job_ids)
    with db_connection() as conn:
        cur = conn.execute(
            f"SELECT * FROM match_results WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        )
        rows = cur.fetchall()
    
    results: Dict[int, MatchResult] = {}
    for row in rows:
        inferred = ""
        if "inferred_qualifications" in row.keys():
            inferred = row["inferred_qualifications"] or ""
        results[row["job_id"]] = MatchResult(
            job_id=row["job_id"],
            match_score=row["match_score"],
            top_reasons=row["top_reasons"].split("|") if row["top_reasons"] else [],
            missing_requirements=row["missing_requirements"].split("|") if row["missing_requirements"] else [],
            inferred_qualifications=inferred.split("|") if inferred else [],
            suggested_resume_bullets=row["suggested_resume_bullets"].split("|") if row["suggested_resume_bullets"] else [],
            summary_markdown_path=row["summary_markdown_path"],
            raw_json_path=row["raw_json_path"],
        )
    return results


def delete_jobs(job_ids: List[int]) -> int:
    """
    Soft-delete jobs by setting status=deleted.

    We keep rows in DB so future search pulls can still deduplicate by URL/job id.
    Returns count updated.
    """
    if not job_ids:
        return 0
    placeholders = ",".join("?" for _ in job_ids)
    with db_connection() as conn:
        cur = conn.execute(
            f"UPDATE jobs SET status = ? WHERE id IN ({placeholders})",
            (JobStatus.DELETED.value, *job_ids),
        )
        return cur.rowcount


def clear_job_descriptions(job_ids: List[int]) -> int:
    """Clear descriptions to force re-scrape. Returns count updated."""
    if not job_ids:
        return 0
    placeholders = ",".join("?" for _ in job_ids)
    with db_connection() as conn:
        cur = conn.execute(
            f"UPDATE jobs SET full_description = NULL, status = ? WHERE id IN ({placeholders})",
            (JobStatus.PENDING_SCRAPE.value, *job_ids),
        )
        return cur.rowcount


def clear_match_results(job_ids: List[int]) -> int:
    """Delete match results to force re-match. Returns count deleted."""
    if not job_ids:
        return 0
    placeholders = ",".join("?" for _ in job_ids)
    with db_connection() as conn:
        # Delete match results
        conn.execute(
            f"DELETE FROM match_results WHERE job_id IN ({placeholders})",
            tuple(job_ids),
        )
        # Update job status to pending match (assuming they have descriptions)
        cur = conn.execute(
            f"UPDATE jobs SET status = ? WHERE id IN ({placeholders}) AND full_description IS NOT NULL",
            (JobStatus.PENDING_MATCH.value, *job_ids),
        )
        return cur.rowcount


def get_jobs_by_ids(job_ids: List[int]) -> List[JobRecord]:
    """Get jobs by their IDs."""
    if not job_ids:
        return []
    placeholders = ",".join("?" for _ in job_ids)
    with db_connection() as conn:
        cur = conn.execute(
            f"SELECT * FROM jobs WHERE id IN ({placeholders})",
            tuple(job_ids),
        )
        rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


# ============================================================================
# Search History Functions
# ============================================================================

def save_search_history(
    keywords: str,
    location: str,
    jobs_found: int,
    filters: Optional[Dict] = None,
) -> None:
    """
    Record a search in history. Updates existing entry if same keywords+location.
    """
    import json as json_module
    
    filters_str = json_module.dumps(filters) if filters else None
    
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO search_history (keywords, location, search_time, jobs_found, filters)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(keywords, location) DO UPDATE SET
                search_time = excluded.search_time,
                jobs_found = jobs_found + excluded.jobs_found,
                filters = excluded.filters
            """,
            (
                keywords.lower().strip(),
                location.lower().strip(),
                datetime.utcnow().isoformat(),
                jobs_found,
                filters_str,
            ),
        )


def get_search_history(limit: int = 50) -> List[Dict]:
    """Get recent search history."""
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT keywords, location, search_time, jobs_found, filters
            FROM search_history
            ORDER BY search_time DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    
    import json as json_module
    
    return [
        {
            "keywords": row["keywords"],
            "location": row["location"],
            "search_time": row["search_time"],
            "jobs_found": row["jobs_found"],
            "filters": json_module.loads(row["filters"]) if row["filters"] else None,
        }
        for row in rows
    ]


def search_was_run_recently(
    keywords: str,
    location: str,
    hours: int = 24,
) -> bool:
    """
    Check if a search with these keywords+location was run recently.
    
    Returns True if the same search was run within the specified hours.
    """
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT search_time FROM search_history
            WHERE keywords = ? AND location = ?
            """,
            (keywords.lower().strip(), location.lower().strip()),
        )
        row = cur.fetchone()
    
    if row is None:
        return False
    
    search_time = datetime.fromisoformat(row["search_time"])
    hours_since = (datetime.utcnow() - search_time).total_seconds() / 3600
    
    return hours_since < hours


def clear_search_history() -> int:
    """Clear all search history. Returns count deleted."""
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM search_history")
        return cur.rowcount


# ============================================================================
# Exploration Session Functions
# ============================================================================

def create_exploration_session(session) -> int:
    """
    Create a new exploration session record.
    
    Args:
        session: ExplorationSession object
    
    Returns:
        The ID of the created session
    """
    import json as json_module
    
    with db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO exploration_sessions
            (started_at, status, total_searches, completed_searches, total_jobs_found,
             unique_jobs, duplicates, config, insights, explored_keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.started_at.isoformat() if session.started_at else datetime.utcnow().isoformat(),
                session.status.value if hasattr(session.status, 'value') else session.status,
                session.total_searches,
                session.completed_searches,
                session.total_jobs_found,
                session.unique_jobs,
                session.duplicates,
                json_module.dumps(session.config.to_dict()) if hasattr(session.config, 'to_dict') else json_module.dumps(session.config),
                json_module.dumps(session.insights_data) if session.insights_data else None,
                json_module.dumps(list(session.explored_keywords)) if session.explored_keywords else "[]",
            ),
        )
        return cur.lastrowid


def update_exploration_session(session) -> None:
    """
    Update an existing exploration session.
    
    Args:
        session: ExplorationSession object with id set
    """
    import json as json_module
    
    if session.id is None:
        return
    
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE exploration_sessions SET
                ended_at = ?,
                status = ?,
                total_searches = ?,
                completed_searches = ?,
                total_jobs_found = ?,
                unique_jobs = ?,
                duplicates = ?,
                config = ?,
                insights = ?,
                explored_keywords = ?
            WHERE id = ?
            """,
            (
                session.ended_at.isoformat() if session.ended_at else None,
                session.status.value if hasattr(session.status, 'value') else session.status,
                session.total_searches,
                session.completed_searches,
                session.total_jobs_found,
                session.unique_jobs,
                session.duplicates,
                json_module.dumps(session.config.to_dict()) if hasattr(session.config, 'to_dict') else json_module.dumps(session.config),
                json_module.dumps(session.insights_data) if session.insights_data else None,
                json_module.dumps(list(session.explored_keywords)) if session.explored_keywords else "[]",
                session.id,
            ),
        )


def get_exploration_session(session_id: int) -> Optional[Dict]:
    """Get a single exploration session by ID."""
    import json as json_module
    
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM exploration_sessions WHERE id = ?",
            (session_id,),
        )
        row = cur.fetchone()
    
    if row is None:
        return None
    
    return _row_to_exploration_session(row)


def get_exploration_sessions(limit: int = 20) -> List[Dict]:
    """Get recent exploration sessions."""
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT * FROM exploration_sessions
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    
    return [_row_to_exploration_session(row) for row in rows]


def _row_to_exploration_session(row: sqlite3.Row) -> Dict:
    """Convert a database row to exploration session dict."""
    import json as json_module
    
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "status": row["status"],
        "total_searches": row["total_searches"],
        "completed_searches": row["completed_searches"],
        "total_jobs_found": row["total_jobs_found"],
        "unique_jobs": row["unique_jobs"],
        "duplicates": row["duplicates"],
        "config": json_module.loads(row["config"]) if row["config"] else {},
        "insights": json_module.loads(row["insights"]) if row["insights"] else {},
        "explored_keywords": json_module.loads(row["explored_keywords"]) if row["explored_keywords"] else [],
    }


def get_search_history_with_effectiveness(limit: int = 100) -> List[Dict]:
    """
    Get search history with effectiveness metrics.
    
    Returns search history enriched with match score data.
    """
    import json as json_module
    
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT 
                sh.keywords,
                sh.location,
                sh.search_time,
                sh.jobs_found,
                sh.filters,
                sh.avg_match_score,
                sh.high_matches,
                sh.strategy_source
            FROM search_history sh
            ORDER BY sh.search_time DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    
    return [
        {
            "keywords": row["keywords"],
            "location": row["location"],
            "search_time": row["search_time"],
            "jobs_found": row["jobs_found"],
            "filters": json_module.loads(row["filters"]) if row["filters"] else None,
            "avg_match_score": row["avg_match_score"],
            "high_matches": row["high_matches"] or 0,
            "strategy_source": row["strategy_source"],
        }
        for row in rows
    ]


def update_search_effectiveness(
    keywords: str,
    location: str,
    avg_match_score: float,
    high_matches: int,
) -> None:
    """
    Update effectiveness metrics for a search history entry.
    
    Args:
        keywords: Search keywords
        location: Search location
        avg_match_score: Average match score of jobs found
        high_matches: Count of jobs with score >= 70
    """
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE search_history SET
                avg_match_score = ?,
                high_matches = ?
            WHERE keywords = ? AND location = ?
            """,
            (
                avg_match_score,
                high_matches,
                keywords.lower().strip(),
                location.lower().strip(),
            ),
        )


def get_high_scoring_job_descriptions(
    min_score: int = 70,
    limit: int = 10,
) -> List[str]:
    """
    Get descriptions from high-scoring jobs for learning.
    
    Args:
        min_score: Minimum match score threshold
        limit: Maximum number of descriptions
    
    Returns:
        List of job descriptions
    """
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT j.full_description
            FROM jobs j
            JOIN match_results m ON j.id = m.job_id
            WHERE m.match_score >= ?
            AND j.full_description IS NOT NULL
            AND j.full_description != ''
            ORDER BY m.match_score DESC
            LIMIT ?
            """,
            (min_score, limit),
        )
        rows = cur.fetchall()
    
    return [row["full_description"] for row in rows if row["full_description"]]


# ============================================================================
# Apply Session Functions
# ============================================================================

def create_apply_session(session: ApplySession) -> str:
    """
    Create a new apply session record.
    
    Args:
        session: ApplySession object
    
    Returns:
        The ID of the created session
    """
    import json as json_module
    
    fields_json = json_module.dumps([f.model_dump() for f in session.detected_fields]) if session.detected_fields else "[]"
    
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO apply_sessions
            (id, job_id, job_title, company, job_url, status, current_step, total_steps,
             detected_fields, started_at, updated_at, ended_at, error_message, screenshots_dir)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.job_id,
                session.job_title,
                session.company,
                session.job_url,
                session.status.value,
                session.current_step,
                session.total_steps,
                fields_json,
                session.started_at.isoformat(),
                session.updated_at.isoformat(),
                session.ended_at.isoformat() if session.ended_at else None,
                session.error_message,
                session.screenshots_dir,
            ),
        )
    logger.info("Created apply session {} for job {}", session.id, session.job_id)
    return session.id


def get_apply_session(session_id: str) -> Optional[ApplySession]:
    """Get an apply session by ID."""
    import json as json_module
    
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM apply_sessions WHERE id = ?",
            (session_id,),
        )
        row = cur.fetchone()
    
    if row is None:
        return None
    
    return _row_to_apply_session(row)


def get_apply_session_by_job(job_id: int, active_only: bool = True) -> Optional[ApplySession]:
    """Get the most recent apply session for a job."""
    with db_connection() as conn:
        if active_only:
            cur = conn.execute(
                """
                SELECT * FROM apply_sessions 
                WHERE job_id = ? AND status NOT IN ('submitted', 'failed', 'cancelled', 'timeout')
                ORDER BY started_at DESC LIMIT 1
                """,
                (job_id,),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM apply_sessions WHERE job_id = ? ORDER BY started_at DESC LIMIT 1",
                (job_id,),
            )
        row = cur.fetchone()
    
    if row is None:
        return None
    
    return _row_to_apply_session(row)


def update_apply_session(session: ApplySession) -> None:
    """Update an existing apply session."""
    import json as json_module
    
    fields_json = json_module.dumps([f.model_dump() for f in session.detected_fields]) if session.detected_fields else "[]"
    
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE apply_sessions SET
                status = ?,
                current_step = ?,
                total_steps = ?,
                detected_fields = ?,
                updated_at = ?,
                ended_at = ?,
                error_message = ?,
                screenshots_dir = ?
            WHERE id = ?
            """,
            (
                session.status.value,
                session.current_step,
                session.total_steps,
                fields_json,
                datetime.utcnow().isoformat(),
                session.ended_at.isoformat() if session.ended_at else None,
                session.error_message,
                session.screenshots_dir,
                session.id,
            ),
        )
    logger.debug("Updated apply session {} - status: {}", session.id, session.status.value)


def update_apply_session_status(session_id: str, status: ApplySessionStatus, error_message: Optional[str] = None) -> None:
    """Quick status update for an apply session."""
    with db_connection() as conn:
        ended_at = datetime.utcnow().isoformat() if status in [
            ApplySessionStatus.SUBMITTED,
            ApplySessionStatus.FAILED,
            ApplySessionStatus.CANCELLED,
            ApplySessionStatus.TIMEOUT,
        ] else None
        
        conn.execute(
            """
            UPDATE apply_sessions SET
                status = ?,
                updated_at = ?,
                ended_at = COALESCE(?, ended_at),
                error_message = COALESCE(?, error_message)
            WHERE id = ?
            """,
            (status.value, datetime.utcnow().isoformat(), ended_at, error_message, session_id),
        )
    logger.debug("Updated session {} status to {}", session_id, status.value)


def update_apply_session_fields(session_id: str, fields: List[FormField]) -> None:
    """Update detected fields for a session."""
    import json as json_module
    
    fields_json = json_module.dumps([f.model_dump() for f in fields])
    
    with db_connection() as conn:
        conn.execute(
            "UPDATE apply_sessions SET detected_fields = ?, updated_at = ? WHERE id = ?",
            (fields_json, datetime.utcnow().isoformat(), session_id),
        )


def get_active_apply_sessions() -> List[ApplySession]:
    """Get all active (non-ended) apply sessions."""
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT * FROM apply_sessions 
            WHERE status NOT IN ('submitted', 'failed', 'cancelled', 'timeout')
            ORDER BY started_at DESC
            """
        )
        rows = cur.fetchall()
    
    return [_row_to_apply_session(row) for row in rows]


def get_apply_sessions_for_job(job_id: int, limit: int = 10) -> List[ApplySession]:
    """Get apply sessions for a specific job."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM apply_sessions WHERE job_id = ? ORDER BY started_at DESC LIMIT ?",
            (job_id, limit),
        )
        rows = cur.fetchall()
    
    return [_row_to_apply_session(row) for row in rows]


def delete_apply_session(session_id: str) -> bool:
    """Delete an apply session and its actions."""
    with db_connection() as conn:
        # Delete actions first
        conn.execute("DELETE FROM session_actions WHERE session_id = ?", (session_id,))
        # Delete session
        cur = conn.execute("DELETE FROM apply_sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


# ============================================================================
# UI Hint Functions (global + per-user)
# ============================================================================

def get_ui_hints_global(key: str) -> Optional[Dict[str, Any]]:
    """Get global UI hints for a given key."""
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ui_hints_global WHERE key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    return {
        "key": row["key"],
        "hints": json.loads(row["hints_json"]) if row["hints_json"] else [],
        "success_count": row["success_count"] or 0,
        "last_seen_at": row["last_seen_at"],
    }


def upsert_ui_hints_global(key: str, hints: List[Dict[str, Any]]) -> None:
    """Upsert global UI hints for a key."""
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO ui_hints_global(key, hints_json, success_count, last_seen_at)
            VALUES(?, ?, 0, ?)
            ON CONFLICT(key) DO UPDATE SET
                hints_json = excluded.hints_json,
                last_seen_at = excluded.last_seen_at
            """,
            (key, json.dumps(hints, ensure_ascii=False), now),
        )


def increment_ui_hints_global_success(key: str) -> None:
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE ui_hints_global
            SET success_count = success_count + 1, last_seen_at = ?
            WHERE key = ?
            """,
            (now, key),
        )


def get_ui_hints_user(profile_name: str, key: str) -> Optional[Dict[str, Any]]:
    """Get per-user UI hints for a given key."""
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ui_hints_user WHERE profile_name = ? AND key = ?",
            (profile_name, key),
        ).fetchone()
    if not row:
        return None
    return {
        "profile_name": row["profile_name"],
        "key": row["key"],
        "hints": json.loads(row["hints_json"]) if row["hints_json"] else [],
        "success_count": row["success_count"] or 0,
        "last_seen_at": row["last_seen_at"],
    }


def upsert_ui_hints_user(profile_name: str, key: str, hints: List[Dict[str, Any]]) -> None:
    """Upsert per-user UI hints for a key."""
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO ui_hints_user(profile_name, key, hints_json, success_count, last_seen_at)
            VALUES(?, ?, ?, 0, ?)
            ON CONFLICT(profile_name, key) DO UPDATE SET
                hints_json = excluded.hints_json,
                last_seen_at = excluded.last_seen_at
            """,
            (profile_name, key, json.dumps(hints, ensure_ascii=False), now),
        )


def increment_ui_hints_user_success(profile_name: str, key: str) -> None:
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE ui_hints_user
            SET success_count = success_count + 1, last_seen_at = ?
            WHERE profile_name = ? AND key = ?
            """,
            (now, profile_name, key),
        )


def _row_to_apply_session(row: sqlite3.Row) -> ApplySession:
    """Convert a database row to ApplySession."""
    import json as json_module
    
    fields_data = json_module.loads(row["detected_fields"]) if row["detected_fields"] else []
    detected_fields = []
    for f in fields_data:
        try:
            detected_fields.append(FormField(
                field_id=f.get("field_id", ""),
                label=f.get("label", ""),
                field_type=FormFieldType(f.get("field_type", "text")),
                required=f.get("required", False),
                current_value=f.get("current_value"),
                suggested_value=f.get("suggested_value"),
                suggestion_source=f.get("suggestion_source"),
                options=f.get("options", []),
                placeholder=f.get("placeholder"),
                validation_error=f.get("validation_error"),
                selector=f.get("selector"),
            ))
        except Exception as e:
            logger.warning("Failed to parse field: {} - {}", f, e)
    
    return ApplySession(
        id=row["id"],
        job_id=row["job_id"],
        job_title=row["job_title"],
        company=row["company"],
        job_url=row["job_url"],
        status=ApplySessionStatus(row["status"]),
        current_step=row["current_step"] or 1,
        total_steps=row["total_steps"],
        detected_fields=detected_fields,
        started_at=datetime.fromisoformat(row["started_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        error_message=row["error_message"],
        screenshots_dir=row["screenshots_dir"],
    )


# ============================================================================
# Session Action Functions
# ============================================================================

def save_session_action(action: ApplicationAction) -> int:
    """Save an action to the database. Returns the action ID."""
    with db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO session_actions
            (session_id, action_type, target_field_id, target_selector, value,
             status, error_message, created_at, executed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action.session_id,
                action.action_type.value,
                action.target_field_id,
                action.target_selector,
                action.value,
                action.status.value,
                action.error_message,
                action.created_at.isoformat(),
                action.executed_at.isoformat() if action.executed_at else None,
            ),
        )
        return cur.lastrowid


def update_session_action(action_id: int, status: ActionStatus, error_message: Optional[str] = None) -> None:
    """Update an action's status."""
    executed_at = datetime.utcnow().isoformat() if status in [ActionStatus.COMPLETED, ActionStatus.FAILED] else None
    
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE session_actions SET
                status = ?,
                error_message = COALESCE(?, error_message),
                executed_at = COALESCE(?, executed_at)
            WHERE id = ?
            """,
            (status.value, error_message, executed_at, action_id),
        )


def get_session_actions(session_id: str) -> List[ApplicationAction]:
    """Get all actions for a session."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM session_actions WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        rows = cur.fetchall()
    
    return [_row_to_action(row) for row in rows]


def get_pending_actions(session_id: str) -> List[ApplicationAction]:
    """Get pending actions for a session."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM session_actions WHERE session_id = ? AND status = 'pending' ORDER BY created_at ASC",
            (session_id,),
        )
        rows = cur.fetchall()
    
    return [_row_to_action(row) for row in rows]


def _row_to_action(row: sqlite3.Row) -> ApplicationAction:
    """Convert a database row to ApplicationAction."""
    return ApplicationAction(
        id=row["id"],
        session_id=row["session_id"],
        action_type=ActionType(row["action_type"]),
        target_field_id=row["target_field_id"],
        target_selector=row["target_selector"],
        value=row["value"],
        status=ActionStatus(row["status"]),
        error_message=row["error_message"],
        created_at=datetime.fromisoformat(row["created_at"]),
        executed_at=datetime.fromisoformat(row["executed_at"]) if row["executed_at"] else None,
    )


# ============================================================================
# Company Functions (Career Site Tracking)
# ============================================================================

def _row_to_company(row: sqlite3.Row) -> Company:
    """Convert a database row to Company."""
    return Company(
        id=row["id"],
        name=row["name"],
        careers_url=row["careers_url"],
        ats_type=ATSType(row["ats_type"]),
        board_token=row["board_token"],
        logo_url=row["logo_url"] if "logo_url" in row.keys() else None,
        enabled=bool(row["enabled"]),
        last_scraped=datetime.fromisoformat(row["last_scraped"]) if row["last_scraped"] else None,
        total_jobs=row["total_jobs"] or 0,
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.utcnow(),
    )


def insert_company(company: Company) -> Company:
    """Insert a new company for career site tracking."""
    with db_connection() as conn:
        # Check if company already exists by careers_url
        cur = conn.execute(
            "SELECT id FROM companies WHERE careers_url = ?",
            (company.careers_url,)
        )
        existing = cur.fetchone()
        if existing:
            company.id = existing["id"]
            logger.debug("Company already exists: {} (id={})", company.name, company.id)
            return company
        
        cur = conn.execute(
            """
            INSERT INTO companies
            (name, careers_url, ats_type, board_token, logo_url, enabled, last_scraped, total_jobs, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company.name,
                company.careers_url,
                company.ats_type.value,
                company.board_token,
                company.logo_url,
                1 if company.enabled else 0,
                company.last_scraped.isoformat() if company.last_scraped else None,
                company.total_jobs,
                company.created_at.isoformat() if company.created_at else datetime.utcnow().isoformat(),
            ),
        )
        company.id = cur.lastrowid
        logger.info("Inserted new company: {} (ATS: {})", company.name, company.ats_type.value)
    return company


def get_company_by_id(company_id: int) -> Optional[Company]:
    """Get a company by ID."""
    with db_connection() as conn:
        cur = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return _row_to_company(row)


def get_company_by_url(careers_url: str) -> Optional[Company]:
    """Get a company by careers URL."""
    with db_connection() as conn:
        cur = conn.execute("SELECT * FROM companies WHERE careers_url = ?", (careers_url,))
        row = cur.fetchone()
    if row is None:
        return None
    return _row_to_company(row)


def get_all_companies(enabled_only: bool = False) -> List[Company]:
    """Get all tracked companies."""
    with db_connection() as conn:
        if enabled_only:
            cur = conn.execute("SELECT * FROM companies WHERE enabled = 1 ORDER BY name ASC")
        else:
            cur = conn.execute("SELECT * FROM companies ORDER BY name ASC")
        rows = cur.fetchall()
    return [_row_to_company(row) for row in rows]


def update_company(company: Company) -> None:
    """Update an existing company."""
    if company.id is None:
        return
    
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE companies SET
                name = ?,
                careers_url = ?,
                ats_type = ?,
                board_token = ?,
                logo_url = ?,
                enabled = ?,
                last_scraped = ?,
                total_jobs = ?
            WHERE id = ?
            """,
            (
                company.name,
                company.careers_url,
                company.ats_type.value,
                company.board_token,
                company.logo_url,
                1 if company.enabled else 0,
                company.last_scraped.isoformat() if company.last_scraped else None,
                company.total_jobs,
                company.id,
            ),
        )
    logger.debug("Updated company: {} (id={})", company.name, company.id)


def update_company_last_scraped(company_id: int, total_jobs: int) -> None:
    """Update company's last_scraped timestamp and job count."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE companies SET last_scraped = ?, total_jobs = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), total_jobs, company_id),
        )


def delete_company(company_id: int) -> bool:
    """Delete a company. Returns True if deleted."""
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        return cur.rowcount > 0


def toggle_company_enabled(company_id: int, enabled: bool) -> None:
    """Enable or disable a company."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE companies SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, company_id),
        )


def get_job_count_by_company(company_id: int) -> int:
    """Return the number of jobs in the DB for this company."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE company_id = ?",
            (company_id,),
        )
        row = cur.fetchone()
    return row["cnt"] if row else 0


def get_jobs_by_company_id(company_id: int) -> List[JobRecord]:
    """Get all jobs from a specific company."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM jobs WHERE company_id = ? ORDER BY date_found DESC",
            (company_id,),
        )
        rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


def get_jobs_by_source(source: JobSource) -> List[JobRecord]:
    """Get all jobs from a specific source."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM jobs WHERE source = ? ORDER BY date_found DESC",
            (source.value,),
        )
        rows = cur.fetchall()
    return [_row_to_job(row) for row in rows]


def job_exists_by_external_id(external_id: str, source: JobSource) -> bool:
    """Check if a job exists by external ID and source."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT 1 FROM jobs WHERE external_job_id = ? AND source = ?",
            (external_id, source.value),
        )
        return cur.fetchone() is not None


def staging_job_exists_by_external_id(external_id: str, source: JobSource) -> bool:
    """Check if a staging job exists by external ID and source."""
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT 1
            FROM scraped_jobs_staging
            WHERE external_job_id = ? AND source = ?
            LIMIT 1
            """,
            (external_id, source.value),
        )
        return cur.fetchone() is not None


def careers_job_exists_in_jobs_or_staging(job: JobRecord) -> bool:
    """
    Check whether a careers job is already known in either the main jobs table
    or the pending staging table.

    Prefer external_job_id + source for ATS jobs; fallback to URL when external
    id is missing.
    """
    with db_connection() as conn:
        if job.external_job_id and job.source != JobSource.LINKEDIN:
            cur = conn.execute(
                """
                SELECT 1 FROM jobs
                WHERE external_job_id = ? AND source = ?
                LIMIT 1
                """,
                (job.external_job_id, job.source.value),
            )
            if cur.fetchone() is not None:
                return True
            cur = conn.execute(
                """
                SELECT 1 FROM scraped_jobs_staging
                WHERE external_job_id = ? AND source = ?
                LIMIT 1
                """,
                (job.external_job_id, job.source.value),
            )
            return cur.fetchone() is not None

        cur = conn.execute("SELECT 1 FROM jobs WHERE url = ? LIMIT 1", (str(job.url),))
        if cur.fetchone() is not None:
            return True
        cur = conn.execute(
            "SELECT 1 FROM scraped_jobs_staging WHERE url = ? LIMIT 1",
            (str(job.url),),
        )
        return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Scrape runs and staging (review-and-approve for career site pulls)
# ---------------------------------------------------------------------------

def create_scrape_run(
    company_id: int,
    total_found: int,
    new_count: int,
    duplicates_count: int,
    errors: Optional[List[str]] = None,
) -> int:
    """Create a scrape run record. Returns the new run id."""
    import json
    scraped_at = datetime.utcnow().isoformat()
    created_at = scraped_at
    errors_json = json.dumps(errors or []) if errors else "[]"
    with db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO scrape_runs
            (company_id, scraped_at, total_found, new_count, duplicates_count, errors, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (company_id, scraped_at, total_found, new_count, duplicates_count, errors_json, created_at),
        )
        return cur.lastrowid


def insert_staging_job(run_id: int, job: JobRecord) -> int:
    """Insert a job into scraped_jobs_staging for the given run. Returns staging row id."""
    created_at = datetime.utcnow().isoformat()
    with db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO scraped_jobs_staging
            (run_id, title, company, location, url, linkedin_job_id, external_job_id,
             date_found, date_posted, easy_apply, description_snippet, company_logo_url,
             source, company_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                job.title,
                job.company,
                job.location,
                str(job.url),
                job.linkedin_job_id,
                job.external_job_id,
                job.date_found.isoformat(),
                job.date_posted.isoformat() if job.date_posted else None,
                1 if job.easy_apply else 0,
                job.description_snippet,
                job.company_logo_url,
                job.source.value,
                job.company_id,
                created_at,
            ),
        )
        return cur.lastrowid


def _row_to_staging_job(row: sqlite3.Row) -> Dict:
    """Convert a staging row to a dict with id, run_id, and job fields for API."""
    keys = row.keys()
    date_posted = None
    if "date_posted" in keys and row["date_posted"]:
        date_posted = row["date_posted"]
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "title": row["title"],
        "company": row["company"],
        "location": row["location"],
        "url": row["url"],
        "external_job_id": row["external_job_id"] if "external_job_id" in keys else None,
        "date_found": row["date_found"],
        "date_posted": date_posted,
        "easy_apply": bool(row["easy_apply"]),
        "description_snippet": row["description_snippet"],
        "company_logo_url": row["company_logo_url"] if "company_logo_url" in keys else None,
        "source": row["source"] if "source" in keys else "linkedin",
        "company_id": row["company_id"] if "company_id" in keys else None,
        "created_at": row["created_at"],
    }


def get_runs(
    company_id: Optional[int] = None,
    limit: int = 50,
    pending_only: bool = True,
) -> List[ScrapeRun]:
    """List scrape runs, optionally filtered by company. pending_only: only runs that still have staging rows."""
    import json
    with db_connection() as conn:
        if pending_only:
            # Subquery: run has at least one staging row
            sub = "SELECT run_id FROM scraped_jobs_staging GROUP BY run_id"
            if company_id is not None:
                cur = conn.execute(
                    f"""
                    SELECT r.* FROM scrape_runs r
                    INNER JOIN ({sub}) s ON r.id = s.run_id
                    WHERE r.company_id = ?
                    ORDER BY r.scraped_at DESC
                    LIMIT ?
                    """,
                    (company_id, limit),
                )
            else:
                cur = conn.execute(
                    f"""
                    SELECT r.* FROM scrape_runs r
                    INNER JOIN ({sub}) s ON r.id = s.run_id
                    ORDER BY r.scraped_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
        else:
            if company_id is not None:
                cur = conn.execute(
                    "SELECT * FROM scrape_runs WHERE company_id = ? ORDER BY scraped_at DESC LIMIT ?",
                    (company_id, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM scrape_runs ORDER BY scraped_at DESC LIMIT ?",
                    (limit,),
                )
        rows = cur.fetchall()

        out = []
        for row in rows:
            run_id = row["id"]
            # Pending count for this run
            c = conn.execute(
                "SELECT COUNT(*) AS cnt FROM scraped_jobs_staging WHERE run_id = ?",
                (run_id,),
            )
            pending_count = c.fetchone()["cnt"]
            errors = []
            if row["errors"]:
                try:
                    errors = json.loads(row["errors"])
                except Exception:
                    errors = []
            out.append(
                ScrapeRun(
                    id=row["id"],
                    company_id=row["company_id"],
                    scraped_at=datetime.fromisoformat(row["scraped_at"]),
                    total_found=row["total_found"],
                    new_count=row["new_count"],
                    duplicates_count=row["duplicates_count"],
                    errors=errors,
                    created_at=datetime.fromisoformat(row["created_at"]),
                    pending_count=pending_count,
                )
            )
        return out


def get_staging_jobs(run_id: int) -> List[Dict]:
    """Return staging job dicts for a run (for review UI)."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM scraped_jobs_staging WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        rows = cur.fetchall()
    return [_row_to_staging_job(row) for row in rows]


def get_run_by_id(run_id: int) -> Optional[ScrapeRun]:
    """Get a single scrape run by id, with pending_count."""
    import json
    with db_connection() as conn:
        cur = conn.execute("SELECT * FROM scrape_runs WHERE id = ?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        c = conn.execute(
            "SELECT COUNT(*) AS cnt FROM scraped_jobs_staging WHERE run_id = ?",
            (run_id,),
        )
        pending_count = c.fetchone()["cnt"]
        errors = []
        if row["errors"]:
            try:
                errors = json.loads(row["errors"])
            except Exception:
                errors = []
        return ScrapeRun(
            id=row["id"],
            company_id=row["company_id"],
            scraped_at=datetime.fromisoformat(row["scraped_at"]),
            total_found=row["total_found"],
            new_count=row["new_count"],
            duplicates_count=row["duplicates_count"],
            errors=errors,
            created_at=datetime.fromisoformat(row["created_at"]),
            pending_count=pending_count,
        )


def approve_staging_jobs(run_id: int, staging_ids: List[int]) -> Tuple[int, int]:
    """
    Move selected staging jobs into the jobs table (status PENDING_SCRAPE), then delete those staging rows.
    Deduplicates by external_job_id+source: if job already exists in jobs, skip insert and count as skipped.
    Returns (approved_count, skipped_duplicates).
    """
    approved = 0
    skipped = 0
    if not staging_ids:
        return (0, 0)
    with db_connection() as conn:
        placeholders = ",".join("?" for _ in staging_ids)
        cur = conn.execute(
            f"SELECT * FROM scraped_jobs_staging WHERE run_id = ? AND id IN ({placeholders})",
            (run_id,) + tuple(staging_ids),
        )
        rows = cur.fetchall()
    for row in rows:
        job = JobRecord(
            title=row["title"],
            company=row["company"],
            location=row["location"],
            url=row["url"],
            linkedin_job_id=row["linkedin_job_id"],
            external_job_id=row["external_job_id"],
            date_found=datetime.fromisoformat(row["date_found"]),
            date_posted=datetime.fromisoformat(row["date_posted"]) if row["date_posted"] else None,
            easy_apply=bool(row["easy_apply"]),
            description_snippet=row["description_snippet"],
            company_logo_url=row["company_logo_url"],
            status=JobStatus.PENDING_SCRAPE,
            source=JobSource(row["source"]) if row["source"] else JobSource.LINKEDIN,
            company_id=row["company_id"],
        )
        if job.external_job_id and job.source != JobSource.LINKEDIN:
            if job_exists_by_external_id(job.external_job_id, job.source):
                skipped += 1
                with db_connection() as conn:
                    conn.execute("DELETE FROM scraped_jobs_staging WHERE id = ?", (row["id"],))
                continue
        inserted = insert_job(job)
        if inserted.id:
            approved += 1
        with db_connection() as conn:
            conn.execute("DELETE FROM scraped_jobs_staging WHERE id = ?", (row["id"],))
    # Update company total_jobs for this run's company
    run = get_run_by_id(run_id)
    if run and approved > 0:
        company_id = run.company_id
        db_count = get_job_count_by_company(company_id)
        update_company_last_scraped(company_id, db_count)
    return (approved, skipped)


def discard_run(run_id: int) -> None:
    """Remove all staging jobs for the run. Optionally keep run row for history."""
    with db_connection() as conn:
        conn.execute("DELETE FROM scraped_jobs_staging WHERE run_id = ?", (run_id,))
    logger.debug("Discarded staging jobs for run {}", run_id)


def get_staging_count_by_company(company_id: int) -> int:
    """Return number of staging jobs (pending review) for this company across all runs."""
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS cnt FROM scraped_jobs_staging WHERE company_id = ?",
            (company_id,),
        )
        row = cur.fetchone()
    return row["cnt"] if row else 0


def get_pending_jobs_count() -> int:
    """
    Return the number of jobs that are waiting to be handled in the main pipeline.
    
    For badge purposes this treats jobs in PENDING_SCRAPE or PENDING_MATCH
    as \"pending\" and ignores matched/applied jobs.
    """
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM jobs
            WHERE status IN (?, ?)
            """,
            (JobStatus.PENDING_SCRAPE.value, JobStatus.PENDING_MATCH.value),
        )
        row = cur.fetchone()
    return row["cnt"] if row else 0


def get_pending_scrape_count() -> int:
    """
    Return the number of jobs in PENDING_SCRAPE status.
    Used for notification strip and next-best-action messaging.
    """
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status = ?",
            (JobStatus.PENDING_SCRAPE.value,),
        )
        row = cur.fetchone()
    return row["cnt"] if row else 0


def get_pending_match_count() -> int:
    """
    Return the number of jobs in PENDING_MATCH status.
    Used for notification strip and next-best-action messaging.
    """
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status = ?",
            (JobStatus.PENDING_MATCH.value,),
        )
        row = cur.fetchone()
    return row["cnt"] if row else 0


def get_total_staging_jobs_count() -> int:
    """
    Return the total number of staging jobs pending review across all companies.
    
    Used for the \"Review pulled\" navigation badge.
    """
    with db_connection() as conn:
        cur = conn.execute("SELECT COUNT(*) AS cnt FROM scraped_jobs_staging")
        row = cur.fetchone()
    return row["cnt"] if row else 0


def get_matched_count() -> int:
    """
    Return the number of jobs with status MATCHED (have match score, not yet applied).
    Used for pipeline dashboard stage counts.
    """
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status = ?",
            (JobStatus.MATCHED.value,),
        )
        row = cur.fetchone()
    return row["cnt"] if row else 0


def get_applied_count() -> int:
    """
    Return the number of jobs with status APPLIED.
    Used for pipeline dashboard stage counts.
    """
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status = ?",
            (JobStatus.APPLIED.value,),
        )
        row = cur.fetchone()
    return row["cnt"] if row else 0


def create_task_group_id() -> str:
    """Create a new pipeline task group id (stable identifier for a run)."""
    return str(uuid.uuid4())


def enqueue_pipeline_task(
    *,
    task_group_id: Optional[str],
    task_type: PipelineTaskType,
    payload: Dict[str, Any],
    priority: int = 0,
    max_attempts: int = 3,
) -> int:
    """Insert a pipeline task. Returns task id."""
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO pipeline_tasks (
                task_group_id, task_type, status, priority, payload_json,
                attempts, max_attempts, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                task_group_id,
                task_type.value,
                PipelineTaskStatus.QUEUED.value,
                int(priority),
                json.dumps(payload, ensure_ascii=False),
                int(max_attempts),
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def enqueue_process_pending_tasks(task_group_id: str, job_ids: List[int]) -> None:
    """
    Enqueue an end-to-end processing run for jobs:
    - scrape description (if missing) then match.

    Tasks are idempotent; the worker will skip work if already completed.
    """
    # Priority: scrape before match; within each phase, keep stable ordering.
    for job_id in job_ids:
        enqueue_pipeline_task(
            task_group_id=task_group_id,
            task_type=PipelineTaskType.SCRAPE_JOB_DESCRIPTION,
            payload={"job_id": job_id},
            priority=10,
            max_attempts=3,
        )
    for job_id in job_ids:
        enqueue_pipeline_task(
            task_group_id=task_group_id,
            task_type=PipelineTaskType.MATCH_JOB,
            payload={"job_id": job_id},
            priority=0,
            max_attempts=3,
        )


def _parse_task_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "task_group_id": row["task_group_id"],
        "task_type": row["task_type"],
        "status": row["status"],
        "priority": int(row["priority"]),
        "payload": json.loads(row["payload_json"] or "{}"),
        "attempts": int(row["attempts"]),
        "max_attempts": int(row["max_attempts"]),
        "retry_at": row["retry_at"],
        "cancel_requested": bool(row["cancel_requested"]),
        "locked_by": row["locked_by"],
        "locked_at": row["locked_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def claim_next_pipeline_task(*, worker_id: str) -> Optional[Dict[str, Any]]:
    """
    Atomically claim the next runnable task.

    Uses a BEGIN IMMEDIATE transaction to avoid double-claims.
    """
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            SELECT *
            FROM pipeline_tasks
            WHERE status IN (?, ?)
              AND cancel_requested = 0
              AND (retry_at IS NULL OR retry_at <= ?)
            ORDER BY priority DESC, id ASC
            LIMIT 1
            """,
            (PipelineTaskStatus.QUEUED.value, PipelineTaskStatus.RETRY.value, now),
        )
        row = cur.fetchone()
        if row is None:
            return None
        task_id = int(row["id"])
        conn.execute(
            """
            UPDATE pipeline_tasks
            SET status = ?, locked_by = ?, locked_at = ?, started_at = COALESCE(started_at, ?),
                updated_at = ?
            WHERE id = ?
            """,
            (
                PipelineTaskStatus.RUNNING.value,
                worker_id,
                now,
                now,
                now,
                task_id,
            ),
        )
        cur2 = conn.execute("SELECT * FROM pipeline_tasks WHERE id = ?", (task_id,))
        row2 = cur2.fetchone()
        return _parse_task_row(row2) if row2 else None


def mark_pipeline_task_succeeded(task_id: int) -> None:
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE pipeline_tasks
            SET status = ?, finished_at = ?, updated_at = ?, last_error = NULL
            WHERE id = ?
            """,
            (PipelineTaskStatus.SUCCEEDED.value, now, now, task_id),
        )


def mark_pipeline_task_cancelled(task_id: int) -> None:
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE pipeline_tasks
            SET status = ?, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (PipelineTaskStatus.CANCELLED.value, now, now, task_id),
        )


def mark_pipeline_task_failed(task_id: int, error: str, *, retry_delay_seconds: int = 10) -> None:
    """
    Mark failed; if attempts remain, move to RETRY with a future retry_at.
    """
    now = datetime.utcnow()
    now_s = now.isoformat()
    with db_connection() as conn:
        cur = conn.execute(
            "SELECT attempts, max_attempts FROM pipeline_tasks WHERE id = ?",
            (task_id,),
        )
        row = cur.fetchone()
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        max_attempts = int(row["max_attempts"])
        if attempts < max_attempts:
            retry_at = (now.replace(microsecond=0)).isoformat()
            # simple backoff: retry_delay_seconds * attempts
            retry_at_dt = datetime.utcnow().timestamp() + (retry_delay_seconds * attempts)
            retry_at = datetime.utcfromtimestamp(retry_at_dt).isoformat()
            conn.execute(
                """
                UPDATE pipeline_tasks
                SET status = ?, attempts = ?, retry_at = ?, updated_at = ?, last_error = ?
                WHERE id = ?
                """,
                (
                    PipelineTaskStatus.RETRY.value,
                    attempts,
                    retry_at,
                    now_s,
                    error[:2000],
                    task_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE pipeline_tasks
                SET status = ?, attempts = ?, finished_at = ?, updated_at = ?, last_error = ?
                WHERE id = ?
                """,
                (
                    PipelineTaskStatus.FAILED.value,
                    attempts,
                    now_s,
                    now_s,
                    error[:2000],
                    task_id,
                ),
            )


def request_cancel_task_group(task_group_id: str) -> None:
    """
    Request cancellation for a task group.
    - queued/retry tasks are immediately cancelled
    - running tasks are marked cancel_requested=1 (worker will stop early when possible)
    """
    now = datetime.utcnow().isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE pipeline_tasks
            SET cancel_requested = 1, updated_at = ?
            WHERE task_group_id = ?
              AND status = ?
            """,
            (now, task_group_id, PipelineTaskStatus.RUNNING.value),
        )
        conn.execute(
            """
            UPDATE pipeline_tasks
            SET status = ?, cancel_requested = 1, finished_at = ?, updated_at = ?
            WHERE task_group_id = ?
              AND status IN (?, ?)
            """,
            (
                PipelineTaskStatus.CANCELLED.value,
                now,
                now,
                task_group_id,
                PipelineTaskStatus.QUEUED.value,
                PipelineTaskStatus.RETRY.value,
            ),
        )


def get_task_group_summary(task_group_id: str) -> Dict[str, Any]:
    """
    Return summary stats for a task group for progress UI.
    """
    with db_connection() as conn:
        cur = conn.execute(
            """
            SELECT status, COUNT(*) AS cnt
            FROM pipeline_tasks
            WHERE task_group_id = ?
            GROUP BY status
            """,
            (task_group_id,),
        )
        counts = {row["status"]: int(row["cnt"]) for row in cur.fetchall()}
        total = sum(counts.values())
        completed = counts.get(PipelineTaskStatus.SUCCEEDED.value, 0) + counts.get(
            PipelineTaskStatus.FAILED.value, 0
        ) + counts.get(PipelineTaskStatus.CANCELLED.value, 0)
        running = counts.get(PipelineTaskStatus.RUNNING.value, 0) > 0 or counts.get(
            PipelineTaskStatus.QUEUED.value, 0
        ) > 0 or counts.get(PipelineTaskStatus.RETRY.value, 0) > 0

        # Phase heuristic: if any scrape tasks unfinished -> scraping else matching if any match unfinished.
        cur2 = conn.execute(
            """
            SELECT task_type, status, payload_json
            FROM pipeline_tasks
            WHERE task_group_id = ?
              AND status = ?
            """,
            (task_group_id, PipelineTaskStatus.RUNNING.value),
        )
        running_rows = cur2.fetchall()
        active_jobs: List[int] = []
        current_job: Optional[str] = None
        phase: Optional[str] = None
        for r in running_rows:
            payload = json.loads(r["payload_json"] or "{}")
            job_id = payload.get("job_id")
            if isinstance(job_id, int):
                active_jobs.append(job_id)
            if current_job is None and job_id is not None:
                current_job = f"Processing job {job_id}..."
            if phase is None:
                phase = "scraping" if r["task_type"] == PipelineTaskType.SCRAPE_JOB_DESCRIPTION.value else "matching"

        if phase is None:
            # If nothing running but still pending, infer from remaining queued tasks
            cur3 = conn.execute(
                """
                SELECT task_type, COUNT(*) AS cnt
                FROM pipeline_tasks
                WHERE task_group_id = ?
                  AND status IN (?, ?)
                GROUP BY task_type
                """,
                (task_group_id, PipelineTaskStatus.QUEUED.value, PipelineTaskStatus.RETRY.value),
            )
            by_type = {row["task_type"]: int(row["cnt"]) for row in cur3.fetchall()}
            if by_type.get(PipelineTaskType.SCRAPE_JOB_DESCRIPTION.value, 0) > 0:
                phase = "scraping"
            elif by_type.get(PipelineTaskType.MATCH_JOB.value, 0) > 0:
                phase = "matching"

        return {
            "task_group_id": task_group_id,
            "running": bool(running),
            "total": int(total),
            "completed": int(completed),
            "counts": counts,
            "phase": phase,
            "current_job": current_job,
            "active_jobs": active_jobs,
        }

