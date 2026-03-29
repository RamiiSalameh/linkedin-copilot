"""
Abstract base class for job source scrapers.

Provides common functionality for rate limiting, error handling, and job normalization.

Location filter: optional list of strings passed as ``location_filters`` to ``fetch_jobs``.
A job is included if its ``location`` contains any term (case-insensitive substring).
Empty or missing list means no filter (all jobs included).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import asyncio
import time

import httpx

from ..models import Company, JobRecord, JobSource, JobStatus
from ..logging_setup import logger


@dataclass
class SearchResult:
    """Container for search results with statistics."""
    
    jobs: List[JobRecord] = field(default_factory=list)
    duplicates: int = 0
    total_found: int = 0
    errors: List[str] = field(default_factory=list)
    
    @property
    def new_jobs(self) -> int:
        return len(self.jobs)
    
    def add_job(self, job: JobRecord) -> None:
        """Add a new job (will be inserted). total_found = new + duplicates seen this run."""
        self.jobs.append(job)
        self.total_found += 1

    def add_duplicate(self) -> None:
        """Record a job already in DB (skipped insert). total_found still incremented."""
        self.duplicates += 1
        self.total_found += 1
    
    def add_error(self, error: str) -> None:
        self.errors.append(error)


def normalize_location_filters(
    location_filters: Optional[List[str]],
) -> Optional[List[str]]:
    """
    Normalize location_filters: strip each term, drop empty strings.
    Returns None if the result would be empty (so "no filter" semantics).
    """
    if not location_filters:
        return None
    normalized = [s.strip() for s in location_filters if s and s.strip()]
    return normalized if normalized else None


def job_matches_location_filter(
    job_location: str,
    location_filters: Optional[List[str]],
) -> bool:
    """
    Return True if the job should be included given optional location filters.

    - If location_filters is None or empty, returns True (no filter).
    - Otherwise returns True if job_location (case-insensitive) contains
      any of the normalized filter terms as a substring.
    """
    filters = normalize_location_filters(location_filters)
    if not filters:
        return True
    job_loc_lower = (job_location or "").lower()
    return any(term.lower() in job_loc_lower for term in filters)


class RateLimiter:
    """Per-domain rate limiter with exponential backoff."""
    
    def __init__(self, requests_per_second: float = 0.1, max_retries: int = 3):
        self.min_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self._domain_last_request: Dict[str, float] = {}
        self._lock = asyncio.Lock()
    
    async def acquire(self, domain: str) -> None:
        """Wait until we can make a request to the given domain."""
        async with self._lock:
            now = time.time()
            last_request = self._domain_last_request.get(domain, 0)
            elapsed = now - last_request
            
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                logger.debug("Rate limiting: waiting {:.2f}s for {}", wait_time, domain)
                await asyncio.sleep(wait_time)
            
            self._domain_last_request[domain] = time.time()
    
    def get_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        import random
        base_delay = 2 ** attempt
        jitter = random.uniform(0, base_delay * 0.1)
        return min(base_delay + jitter, 60.0)


class JobSourceBase(ABC):
    """Abstract base class for job source scrapers."""
    
    source_name: str = "unknown"
    source_type: JobSource = JobSource.CUSTOM
    
    def __init__(
        self,
        rate_limit: float = 0.1,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.rate_limiter = RateLimiter(requests_per_second=rate_limit, max_retries=max_retries)
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "User-Agent": "JobSearchCopilot/1.0 (Career Site Scraper)",
                    "Accept": "application/json",
                },
                follow_redirects=True,
            )
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
    
    async def _request_with_retry(
        self,
        url: str,
        method: str = "GET",
        **kwargs,
    ) -> Optional[httpx.Response]:
        """Make HTTP request with rate limiting and retry logic."""
        from urllib.parse import urlparse
        
        domain = urlparse(url).netloc
        client = await self._get_client()
        
        for attempt in range(self.max_retries):
            try:
                await self.rate_limiter.acquire(domain)
                
                response = await client.request(method, url, **kwargs)
                
                if response.status_code == 429:
                    delay = self.rate_limiter.get_backoff_delay(attempt)
                    logger.warning("Rate limited (429) for {}, waiting {:.1f}s", url, delay)
                    await asyncio.sleep(delay)
                    continue
                
                if response.status_code >= 500:
                    delay = self.rate_limiter.get_backoff_delay(attempt)
                    logger.warning("Server error ({}) for {}, retrying in {:.1f}s", 
                                 response.status_code, url, delay)
                    await asyncio.sleep(delay)
                    continue
                
                response.raise_for_status()
                return response
                
            except httpx.TimeoutException:
                logger.warning("Timeout for {}, attempt {}/{}", url, attempt + 1, self.max_retries)
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.rate_limiter.get_backoff_delay(attempt))
                    
            except httpx.HTTPStatusError as e:
                logger.error("HTTP error for {}: {}", url, e)
                return None
                
            except Exception as e:
                logger.error("Request error for {}: {}", url, e)
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.rate_limiter.get_backoff_delay(attempt))
        
        return None
    
    @abstractmethod
    async def fetch_jobs(self, company: Company, **filters) -> SearchResult:
        """
        Fetch all jobs from a company's career page.
        
        Args:
            company: The company to scrape jobs from
            **filters: Optional filters. Supported: location_filters (list of str);
            job is included only if its location contains any term (case-insensitive).
            Empty or missing location_filters means no filter.
        
        Returns:
            SearchResult containing jobs found and statistics
        """
        pass
    
    @abstractmethod
    async def fetch_job_details(self, job: JobRecord) -> Optional[str]:
        """
        Fetch full job description for a specific job.
        
        Args:
            job: The job record to fetch details for
        
        Returns:
            Full job description text, or None if failed
        """
        pass
    
    @abstractmethod
    def normalize_job(self, raw_job: Dict[str, Any], company: Company) -> JobRecord:
        """
        Convert raw API response to normalized JobRecord.
        
        Args:
            raw_job: Raw job data from API
            company: The company this job belongs to
        
        Returns:
            Normalized JobRecord
        """
        pass
    
    def extract_job_id(self, raw_job: Dict[str, Any]) -> Optional[str]:
        """Extract unique job ID from raw job data."""
        for key in ["id", "job_id", "jobId", "posting_id"]:
            if key in raw_job:
                return str(raw_job[key])
        return None
    
    def clean_html(self, html: Optional[str]) -> str:
        """Remove HTML tags and clean up text."""
        if not html:
            return ""
        
        import re
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        return text
    
    def extract_snippet(self, description: str, max_length: int = 300) -> str:
        """Extract a snippet from the full description."""
        clean = self.clean_html(description)
        if len(clean) <= max_length:
            return clean
        return clean[:max_length].rsplit(' ', 1)[0] + "..."
