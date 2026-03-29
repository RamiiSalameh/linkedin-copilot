"""
Workday ATS scraper using Playwright (browser-based).

Workday career sites are JavaScript-heavy and do not expose a public REST API.
We load the job search page, wait for the job list, and extract job cards.
Supports both direct URLs (e.g. philips.wd3.myworkdayjobs.com) and branded
URLs that redirect to Workday.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse
import asyncio
import re

from .base import (
    JobSourceBase,
    SearchResult,
    job_matches_location_filter,
    normalize_location_filters,
)
from ..models import Company, JobRecord, JobSource, JobStatus
from ..logging_setup import logger
from ..db import job_exists_by_external_id


# Selectors tried in order; first match wins
WORKDAY_JOB_LIST_SELECTORS = [
    '[data-automation-id="jobPosting"]',
    '[data-automation-id="compositeContainer"]',
    'li[data-automation-id="jobPosting"]',
    'section[data-automation-id="jobResults"] li',
    '.job post',
    '.job-posting',
    'article[data-automation-id="jobPosting"]',
]

WORKDAY_JOB_TITLE_SELECTORS = [
    '[data-automation-id="jobPostingTitle"]',
    'a[data-automation-id="jobTitle"]',
    'h3 a',
    '.job-title a',
    'a[href*="/job/"]',
]

WORKDAY_LOCATION_SELECTOR = '[data-automation-id="locations"]'
# Location facet (filter sidebar): try to open and select a location (e.g. Israel)
WORKDAY_LOCATION_FACET_SELECTORS = [
    'button:has-text("Location")',
    'button:has-text("Locations")',
    'a:has-text("Location")',
    '[data-automation-id*="location"]',
    '[aria-label*="Location"]',
]
DELAY_AFTER_FACET_APPLY_SEC = 2.0
WORKDAY_SHOW_MORE_SELECTORS = [
    'button[data-automation-id="showMoreButton"]',
    'button:has-text("Show More")',
    'a:has-text("Show More")',
    '[aria-label="Show more jobs"]',
]

MAX_JOBS_PER_COMPANY = 200
PAGE_LOAD_TIMEOUT_MS = 30000
WAIT_FOR_SELECTOR_TIMEOUT_MS = 15000
DELAY_BETWEEN_PAGES_SEC = 2.0
# Job detail page: wait for content to be present (tenant-dependent)
WORKDAY_DETAIL_WAIT_MS = 5000
WORKDAY_DESC_SELECTORS = [
    '[data-automation-id="jobPostingDescription"]',
    '[data-automation-id="jobDescription"]',
    '[data-automation-id="jobPostingBody"]',
    '[data-automation-id="compositeContainer"]',
    '.job-description',
    '[class*="jobPostingDescription"]',
    '[class*="job-description"]',
    'section[data-automation-id="jobPosting"]',
    'div[data-automation-id="jobPosting"]',
    'main',
    '[role="main"]',
]


class WorkdayScraper(JobSourceBase):
    """Scraper for Workday job boards (browser-based)."""

    source_name = "workday"
    source_type = JobSource.WORKDAY

    def __init__(
        self,
        rate_limit: float = 0.2,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        super().__init__(rate_limit=rate_limit, timeout=timeout, max_retries=max_retries)

    def _job_list_url(self, company: Company, use_en_us: bool = False) -> str:
        """Build the job list URL for a Workday tenant.

        Many tenants use /en-US/TenantName (e.g. Nvidia) or /TenantName; some use /jobs.
        If use_en_us is True, insert /en-US before the path for locale-specific listing.
        Preserves query string (e.g. locationHierarchy1=...) so pre-filtered URLs work as-is.
        """
        base = company.careers_url.strip()
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        parsed = urlparse(base)
        path = (parsed.path or "").rstrip("/")
        netloc = parsed.netloc
        scheme = parsed.scheme or "https"
        query = (parsed.query or "").strip()
        suffix = ("?" + query) if query else ""

        # Root or empty path -> standard /en-US/jobs
        if not path or path == "/":
            return f"{scheme}://{netloc}/en-US/jobs{suffix}"
        # Path already has /jobs or "job" in it -> use as-is (maybe add en-US)
        if "/jobs" in path or "job" in path.lower():
            if use_en_us and not path.startswith("/en-US") and not path.startswith("/en-us"):
                path = "/en-US" + (path if path.startswith("/") else "/" + path)
            return f"{scheme}://{netloc}{path}{suffix}"
        # Tenant path like /NVIDIAExternalCareerSite -> use as-is; optional /en-US for locale
        if use_en_us and not path.startswith("/en-US") and not path.startswith("/en-us"):
            path = "/en-US" + (path if path.startswith("/") else "/" + path)
        return f"{scheme}://{netloc}{path}{suffix}"

    async def _apply_location_facet(
        self, page: Any, location_filters: List[str], current_url: str
    ) -> bool:
        """Try to apply location filter on the Workday job list page via the location facet.
        Returns True if a facet was applied, False otherwise (no filters, already in URL, or failed).
        """
        if not location_filters:
            return False
        current_lower = (current_url or "").lower()
        if "locationhierarchy" in current_lower or "refreshfacet" in current_lower:
            return False
        first_term = (location_filters[0] or "").strip()
        if not first_term:
            return False
        try:
            # Open the location facet (button or link "Location" / "Locations")
            facet_btn = None
            for sel in WORKDAY_LOCATION_FACET_SELECTORS:
                try:
                    facet_btn = await page.query_selector(sel)
                    if facet_btn:
                        break
                except Exception:
                    continue
            if not facet_btn:
                logger.debug("Workday: no location facet button found")
                return False
            await facet_btn.click()
            await asyncio.sleep(0.8)
            # Find and click an option that contains the location term (e.g. Israel)
            option = await page.query_selector(f'text="{first_term}"')
            if not option:
                option = await page.query_selector(f'a:has-text("{first_term}")')
            if not option:
                option = await page.query_selector(f'button:has-text("{first_term}")')
            if not option:
                option = await page.query_selector(f'[role="option"]:has-text("{first_term}")')
            if not option:
                logger.debug("Workday: location option '{}' not found in facet", first_term)
                return False
            await option.click()
            await asyncio.sleep(DELAY_AFTER_FACET_APPLY_SEC)
            logger.info("Workday: applied location facet for '{}'", first_term)
            return True
        except Exception as e:
            logger.warning("Workday: could not apply location facet: {}", e)
            return False

    async def fetch_jobs(self, company: Company, **filters) -> SearchResult:
        """
        Fetch job list from a Workday career site via Playwright.

        **filters: location_filters (list of str) — only jobs whose location
            contains any term (case-insensitive) are included.
        """
        result = SearchResult()
        location_filters = normalize_location_filters(
            filters.get("location_filters") or filters.get("locations")
        )
        from playwright.async_api import async_playwright

        urls_to_try = [
            self._job_list_url(company),
            self._job_list_url(company, use_en_us=True),
        ]
        # Deduplicate so we only try /en-US if it's different (e.g. tenant path like Nvidia)
        urls_to_try = list(dict.fromkeys(urls_to_try))
        collected: List[Dict[str, Any]] = []

        # Always run headless for career scraping (background task). Global HEADLESS
        # is for manual flows (e.g. LinkedIn login); Workday scrape should not open a window.
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )
                page = await context.new_page()
                page.set_default_timeout(PAGE_LOAD_TIMEOUT_MS)

                for job_list_url in urls_to_try:
                    if len(collected) > 0:
                        break
                    logger.info("Workday fetching job list: {} for {}", job_list_url, company.name)
                    await page.goto(job_list_url, wait_until="domcontentloaded")
                    await asyncio.sleep(2)

                    # Wait for job list with fallback selectors
                    job_container = None
                    for selector in WORKDAY_JOB_LIST_SELECTORS:
                        try:
                            job_container = await page.wait_for_selector(
                                selector, timeout=WAIT_FOR_SELECTOR_TIMEOUT_MS
                            )
                            if job_container:
                                logger.debug("Workday job list matched selector: {}", selector)
                                break
                        except Exception:
                            continue
                    if not job_container:
                        # Try to find any job link
                        job_links = await page.query_selector_all('a[href*="/job/"]')
                        if not job_links:
                            logger.warning(
                                "Workday page did not load job list at {}; trying next URL if any",
                                job_list_url,
                            )
                            continue
                        logger.debug("Workday using fallback job links: {} links", len(job_links))

                    # Apply location filter on the career site when possible (e.g. Israel)
                    if location_filters:
                        current_url = page.url
                        await self._apply_location_facet(
                            page, location_filters, current_url
                        )

                    seen_urls: set = set()
                    iterations = 0
                    max_iterations = 50

                    while len(collected) < MAX_JOBS_PER_COMPANY and iterations < max_iterations:
                        iterations += 1
                        # Extract job cards from current view
                        cards = await page.query_selector_all(
                            ', '.join(WORKDAY_JOB_LIST_SELECTORS[:4])
                        )
                        if not cards:
                            cards = await page.query_selector_all('a[href*="/job/"]')

                        for card in cards:
                            if len(collected) >= MAX_JOBS_PER_COMPANY:
                                break
                            try:
                                link_el = await card.query_selector("a[href*='/job/']")
                                if not link_el:
                                    link_el = await card.query_selector("a")
                                href = None
                                if link_el:
                                    href = await link_el.get_attribute("href")
                                if not href:
                                    href = await card.get_attribute("href")
                                if not href or "/job/" not in href:
                                    continue
                                if not href:
                                    continue
                                full_url = urljoin(job_list_url, href)
                                if full_url in seen_urls:
                                    continue
                                seen_urls.add(full_url)
                                title_el = await card.query_selector(
                                    '[data-automation-id="jobPostingTitle"], '
                                    '[data-automation-id="jobTitle"], h3, .job-title'
                                )
                                title = await title_el.inner_text() if title_el else "Unknown"
                                title = title.strip() or "Unknown"
                                loc_el = await card.query_selector(
                                    '[data-automation-id="locations"]'
                                )
                                location = (
                                    (await loc_el.inner_text()).strip()
                                    if loc_el
                                    else "Remote"
                                )
                                external_id = self._external_id_from_url(full_url)
                                collected.append(
                                    {
                                        "url": full_url,
                                        "title": title,
                                        "location": location,
                                        "external_id": external_id or full_url,
                                    }
                                )
                            except Exception as e:
                                logger.debug("Workday card parse skip: {}", e)
                                continue

                        # Pagination: Show more
                        show_more = None
                        for sel in WORKDAY_SHOW_MORE_SELECTORS:
                            try:
                                show_more = await page.query_selector(sel)
                                if show_more:
                                    break
                            except Exception:
                                continue
                        if not show_more:
                            break
                        await show_more.click()
                        await asyncio.sleep(DELAY_BETWEEN_PAGES_SEC)

                    if not collected and len(urls_to_try) > 1:
                        logger.warning(
                            "Workday found 0 jobs at {} for {}; will try next URL",
                            job_list_url,
                            company.name,
                        )

                if not collected:
                    result.add_error(
                        "Workday page did not load job list; tenant may use a different layout"
                    )
                    await browser.close()
                    return result

                logger.info(
                    "Found {} jobs on Workday for {}",
                    len(collected),
                    company.name,
                )

                for raw in collected:
                    try:
                        eid = raw.get("external_id") or self._external_id_from_url(
                            raw["url"]
                        )
                        if eid and job_exists_by_external_id(eid, self.source_type):
                            result.add_duplicate()
                            continue
                        job = self.normalize_job(raw, company)
                        if not job_matches_location_filter(job.location, location_filters):
                            continue
                        result.add_job(job)
                    except Exception as e:
                        logger.warning("Failed to normalize Workday job: {}", e)
                        result.add_error(str(e))

                await browser.close()
        except Exception as e:
            logger.error("Workday scrape failed for {}: {}", company.name, e)
            result.add_error(f"Workday scrape failed: {str(e)}")

        return result

    def _external_id_from_url(self, url: str) -> Optional[str]:
        """Extract a stable, unique job ID from Workday job URL.

        Workday URLs look like: .../job/[location]/[job-title]_[job-id]-[suffix]
        We must use the full path after /job/ so different jobs (e.g. same location)
        get different IDs. Using only the first segment (e.g. "Israel") caused 9 of
        14 jobs to be wrongly treated as duplicates.
        """
        if not url:
            return None
        # Full path after /job/ up to query string (unique per job)
        match = re.search(r"/job/([^?]+)", url)
        if match:
            return match.group(1).rstrip("/")
        match = re.search(r"/([a-f0-9-]{20,})", url)
        if match:
            return match.group(1)
        return None

    async def fetch_job_details(self, job: JobRecord) -> Optional[str]:
        """Fetch full job description from Workday job page.

        Workday is JS-heavy; we wait for load, then try multiple tenant-specific
        selectors. Philips and other tenants may use jobPostingBody or
        compositeContainer instead of jobPostingDescription.
        """
        from playwright.async_api import async_playwright

        url = str(job.url)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()
                page.set_default_timeout(PAGE_LOAD_TIMEOUT_MS)
                await page.goto(url, wait_until="load")
                await asyncio.sleep(2.0)

                # Wait once for main content so JS has rendered (tenant-dependent)
                try:
                    await page.wait_for_selector("main, [role=\"main\"], [data-automation-id=\"jobPostingDescription\"], [data-automation-id=\"compositeContainer\"]", timeout=WORKDAY_DETAIL_WAIT_MS)
                except Exception:
                    pass
                await asyncio.sleep(0.5)

                content = ""
                for sel in WORKDAY_DESC_SELECTORS:
                    el = await page.query_selector(sel)
                    if el:
                        raw = await el.inner_text()
                        if raw and len(raw.strip()) > 150:
                            content = raw.strip()
                            logger.debug("Workday description matched selector: {}", sel)
                            break

                if not content or len(content) <= 150:
                    for sel in ["main", '[role="main"]', "body"]:
                        el = await page.query_selector(sel)
                        if el:
                            raw = await el.inner_text()
                            if raw and len(raw.strip()) > 300:
                                content = raw.strip()
                                break

                if not content or len(content) <= 150:
                    # Fallback: largest text block in main content (handles custom tenant markup)
                    content = await page.evaluate("""() => {
                        const main = document.querySelector('main') || document.querySelector('[role="main"]') || document.body;
                        let best = '';
                        const walk = (el) => {
                            if (!el || el.tagName === 'SCRIPT' || el.tagName === 'STYLE' || el.tagName === 'NAV' || el.tagName === 'HEADER' || el.tagName === 'FOOTER') return;
                            const text = (el.innerText || '').trim();
                            if (text.length > 200 && text.length > best.length) best = text;
                            for (const c of el.children) walk(c);
                        };
                        walk(main);
                        return best;
                    }""")
                    content = (content or "").strip()

                await browser.close()
                if content and len(content) > 100:
                    return content
                return None
        except Exception as e:
            logger.warning("Workday job details failed for {}: {}", url, e)
            return None

    def normalize_job(self, raw_job: Dict[str, Any], company: Company) -> JobRecord:
        """Convert scraped Workday job dict to JobRecord."""
        url = raw_job.get("url", "")
        title = raw_job.get("title", "Unknown Position")
        location = raw_job.get("location", "Remote")
        external_id = raw_job.get("external_id") or self._external_id_from_url(url)
        if not external_id:
            external_id = url

        return JobRecord(
            title=title,
            company=company.name,
            location=location,
            url=url,
            external_job_id=str(external_id),
            date_found=datetime.utcnow(),
            date_posted=None,
            easy_apply=False,
            description_snippet=None,
            company_logo_url=company.logo_url,
            status=JobStatus.PENDING_SCRAPE,
            source=JobSource.WORKDAY,
            company_id=company.id,
        )


async def scrape_workday_company(company: Company) -> SearchResult:
    """Convenience function to scrape a Workday company."""
    scraper = WorkdayScraper()
    return await scraper.fetch_jobs(company)
