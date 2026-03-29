"""Google Jobs search as an alternative data source."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from ..config import get_settings
from ..db import insert_job, job_exists
from ..logging_setup import logger
from ..models import JobRecord, JobStatus


async def search_google_jobs(
    keywords: str,
    location: str,
    limit: int = 30,
) -> List[JobRecord]:
    """
    Search Google Jobs for positions.
    
    Google Jobs aggregates from multiple sources (LinkedIn, Indeed, Glassdoor, etc.)
    and can find jobs not available through direct LinkedIn search.
    
    Args:
        keywords: Job search keywords
        location: Job location
        limit: Maximum number of jobs to return
    """
    settings = get_settings()
    
    # Build Google Jobs search URL
    # Google Jobs is accessed through Google Search with "jobs" in the query
    search_query = f"{keywords} jobs {location}"
    search_url = f"https://www.google.com/search?q={quote_plus(search_query)}&ibp=htl;jobs"
    
    jobs: List[JobRecord] = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            logger.info("Opening Google Jobs search: {}", search_url)
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            
            # Wait for job listings to load
            await page.wait_for_timeout(3000)
            
            # Take debug screenshot
            screenshots_dir = Path(settings.browser.get("screenshot_dir", "./data/screenshots"))
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = screenshots_dir / "google_jobs_debug.png"
            await page.screenshot(path=str(screenshot_path))
            logger.info("Saved debug screenshot to {}", screenshot_path)
            
            # Google Jobs uses a specific structure - job cards in the left panel
            job_card_selectors = [
                "li.iFjolb",  # Job listing items
                "[data-ved] li[jscontroller]",  # Alternative selector
                ".PwjeAc",  # Another possible job card class
            ]
            
            cards = []
            for sel in job_card_selectors:
                try:
                    cards = await page.query_selector_all(sel)
                    if cards:
                        logger.info("Found {} job cards using selector: {}", len(cards), sel)
                        break
                except Exception:
                    continue
            
            if not cards:
                logger.warning("No Google Jobs cards found. Check {} for page state.", screenshot_path)
                
                # Try to find jobs in the main content area as fallback
                content = await page.inner_text("body")
                if "jobs" in content.lower():
                    logger.debug("Page contains job content but couldn't parse cards")
                
                await browser.close()
                return []
            
            # Process job cards
            for idx, card in enumerate(cards[:limit]):
                try:
                    # Try to extract job information
                    title_el = await card.query_selector("[role='heading'], .BjJfJf, .jcs9Fc")
                    company_el = await card.query_selector(".vNEEBe, .nJlDiv")
                    location_el = await card.query_selector(".Qk80Jf, .zqCU1")
                    
                    title = ""
                    if title_el:
                        title = (await title_el.inner_text()).strip()
                    
                    company = ""
                    if company_el:
                        company = (await company_el.inner_text()).strip()
                    
                    loc = location
                    if location_el:
                        loc = (await location_el.inner_text()).strip()
                    
                    if not title:
                        continue
                    
                    # Create a pseudo-URL for deduplication
                    # Google Jobs doesn't directly give us the job URL
                    pseudo_url = f"https://google.com/jobs/{quote_plus(title)}/{quote_plus(company)}"
                    
                    if job_exists(pseudo_url):
                        continue
                    
                    job = JobRecord(
                        title=title,
                        company=company or "Unknown",
                        location=loc,
                        url=pseudo_url,
                        date_found=datetime.utcnow(),
                        easy_apply=False,
                        description_snippet=f"Found via Google Jobs search: {keywords}",
                        status=JobStatus.PENDING_SCRAPE,
                    )
                    job = insert_job(job)
                    jobs.append(job)
                    logger.debug("Added Google Jobs result: {} @ {}", title, company)
                    
                except Exception as exc:
                    logger.error("Error parsing Google Jobs card {}: {}", idx, exc)
                    continue
                    
        except Exception as exc:
            logger.error("Google Jobs search failed: {}", exc)
        finally:
            await browser.close()
    
    logger.info("Found {} jobs from Google Jobs search", len(jobs))
    return jobs


async def search_company_careers(
    company_name: str,
    keywords: str = "",
    careers_url: Optional[str] = None,
) -> List[JobRecord]:
    """
    Search a specific company's careers page.
    
    This is useful for "dream companies" where you want to find any relevant opening.
    
    Args:
        company_name: Name of the company
        keywords: Optional keywords to filter jobs
        careers_url: Direct URL to careers page (if known)
    """
    settings = get_settings()
    
    # If no direct URL, search for the company careers page
    if not careers_url:
        search_url = f"https://www.google.com/search?q={quote_plus(company_name)}+careers+jobs"
    else:
        search_url = careers_url
    
    jobs: List[JobRecord] = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            logger.info("Searching {} careers: {}", company_name, search_url)
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            
            # Take screenshot for debugging
            screenshots_dir = Path(settings.browser.get("screenshot_dir", "./data/screenshots"))
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = screenshots_dir / f"careers_{company_name.lower().replace(' ', '_')}.png"
            await page.screenshot(path=str(screenshot_path))
            
            # This is a generic approach - each company's careers page is different
            # We'll look for common job listing patterns
            
            # Common job listing selectors across career sites
            job_selectors = [
                "a[href*='job'], a[href*='career'], a[href*='position']",
                ".job-listing a, .job-card a, .position-card a",
                "[class*='job'] a, [class*='opening'] a, [class*='position'] a",
            ]
            
            found_links = set()
            for sel in job_selectors:
                try:
                    links = await page.query_selector_all(sel)
                    for link in links[:30]:
                        href = await link.get_attribute("href")
                        text = await link.inner_text()
                        if href and text and len(text) > 5:
                            found_links.add((text.strip(), href))
                except Exception:
                    continue
            
            # Filter by keywords if provided
            keywords_lower = keywords.lower().split() if keywords else []
            
            for title, url in found_links:
                if keywords_lower:
                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in keywords_lower):
                        continue
                
                # Make URL absolute if needed
                if not url.startswith("http"):
                    base_url = page.url.split("/")[0:3]
                    url = "/".join(base_url) + url
                
                if job_exists(url):
                    continue
                
                job = JobRecord(
                    title=title,
                    company=company_name,
                    location="See job listing",
                    url=url,
                    date_found=datetime.utcnow(),
                    easy_apply=False,
                    description_snippet=f"Found on {company_name} careers page",
                    status=JobStatus.PENDING_SCRAPE,
                )
                job = insert_job(job)
                jobs.append(job)
                logger.debug("Added {} job: {}", company_name, title)
                
        except Exception as exc:
            logger.error("Company careers search failed for {}: {}", company_name, exc)
        finally:
            await browser.close()
    
    logger.info("Found {} jobs from {} careers page", len(jobs), company_name)
    return jobs
