from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from playwright.async_api import async_playwright

from ..config import get_settings
from ..db import get_job_full_description, update_job_description, update_job_logo, update_job_company
from ..logging_setup import logger
from ..models import JobDetail, JobRecord
from ..utils import save_json, timestamped_filename


# Directory for storing downloaded company logos
LOGOS_DIR = Path("data/logos")


def ensure_logos_dir():
    """Ensure the logos directory exists."""
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)


async def download_logo_image(page, logo_url: str, job_id: int) -> Optional[str]:
    """Download a logo image using Playwright and save it locally.
    
    Returns the local path (relative to data/) if successful, None otherwise.
    """
    if not logo_url or not logo_url.startswith("http"):
        return None
    
    ensure_logos_dir()
    
    try:
        # Use Playwright to fetch the image (has proper session context)
        response = await page.request.get(logo_url)
        
        if response.status == 200:
            content = await response.body()
            
            # Determine file extension from content type or URL
            content_type = response.headers.get("content-type", "")
            if "png" in content_type or logo_url.lower().endswith(".png"):
                ext = ".png"
            elif "gif" in content_type or logo_url.lower().endswith(".gif"):
                ext = ".gif"
            elif "webp" in content_type or logo_url.lower().endswith(".webp"):
                ext = ".webp"
            else:
                ext = ".jpg"
            
            # Create unique filename using job_id and hash of URL
            url_hash = hashlib.md5(logo_url.encode()).hexdigest()[:8]
            filename = f"logo_{job_id}_{url_hash}{ext}"
            filepath = LOGOS_DIR / filename
            
            # Write the image
            filepath.write_bytes(content)
            
            # Return path relative to data folder for serving
            local_path = f"/static/logos/{filename}"
            logger.info("Downloaded logo for job {}: {} -> {}", job_id, logo_url[:50], local_path)
            return local_path
        else:
            logger.warning("Failed to download logo for job {}: HTTP {}", job_id, response.status)
            return None
            
    except Exception as e:
        logger.error("Error downloading logo for job {}: {}", job_id, str(e))
        return None


def _is_valid_local_logo(url: str) -> bool:
    """Check if URL is a valid local logo path (not a placeholder or remote URL).
    
    Returns True only for locally downloaded logos stored in /static/logos/.
    Returns False for NULL, empty, LinkedIn URLs, or placeholder images.
    """
    if not url:
        return False
    if url.startswith('/static/logos/'):
        return True
    return False


async def scrape_job_description(job: JobRecord) -> Optional[str]:
    """
    Scrape the full job description from a LinkedIn job URL using Playwright.
    
    Returns the description text if found, None otherwise.
    Updates the database with the scraped description.
    """
    if job.id is None:
        logger.warning("Job has no ID, cannot update description")
        return None
    
    # Check if we already have the description cached
    existing = get_job_full_description(job.id)
    if existing:
        logger.debug("Using cached description for job {}", job.id)
        return existing
    
    settings = get_settings()
    description: Optional[str] = None
    
    async with async_playwright() as p:
        # Launch browser in headless mode to avoid disrupting user
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            logger.info("Scraping description for job {}: {}", job.id, job.url)
            await page.goto(str(job.url), wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            
            # Try multiple selectors for the job description
            description_selectors = [
                "div.jobs-description__content",
                "div.jobs-box__html-content",
                "section.description",
                "div.description__text",
                "div[class*='description']",
                "article.jobs-description",
                "#job-details",
            ]
            
            for sel in description_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if text and len(text.strip()) > 100:
                            description = text.strip()
                            logger.debug("Found description using selector: {}", sel)
                            break
                except Exception:
                    continue
            
            if not description:
                # Fallback: try to get all text from the main content area
                try:
                    main_content = await page.query_selector("main")
                    if main_content:
                        full_text = await main_content.inner_text()
                        if len(full_text) > 200:
                            description = full_text[:5000]
                            logger.debug("Using fallback main content extraction")
                except Exception:
                    pass
            
            if description:
                update_job_description(job.id, description)
                logger.info("Saved description ({} chars) for job {}", len(description), job.id)
            else:
                logger.warning("Could not extract description for job {}", job.id)
            
            # Try to scrape company logo if not already a valid local logo
            if not _is_valid_local_logo(job.company_logo_url):
                logo_url = await _scrape_logo_from_page(page, job_title=job.title)
                if logo_url:
                    # Download the logo and save locally
                    local_path = await download_logo_image(page, logo_url, job.id)
                    if local_path:
                        update_job_logo(job.id, local_path)
                        logger.info("Saved logo locally for job {} '{}': {}", job.id, job.title[:30], local_path)
                    else:
                        # Fallback: store the LinkedIn URL (won't display but better than nothing)
                        update_job_logo(job.id, logo_url)
                        logger.warning("Could not download logo for job {}, stored URL instead", job.id)
            else:
                logger.debug("Job {} already has local logo, skipping", job.id)
            
            # Fix company name if it's "Unknown"
            if job.company == "Unknown" or not job.company:
                company_name = await _scrape_company_from_page(page)
                if company_name and company_name != "Unknown":
                    update_job_company(job.id, company_name)
                    logger.info("Fixed company name for job {}: {}", job.id, company_name)
                
        except Exception as exc:
            logger.error("Error scraping job {}: {}", job.id, exc)
        finally:
            await browser.close()
    
    return description


async def _scrape_company_from_page(page) -> Optional[str]:
    """Extract company name from a LinkedIn job detail page."""
    try:
        # Method 1: Company link (most reliable on job detail page)
        company = await page.evaluate("""
            () => {
                // Look for company link
                const companyLinks = document.querySelectorAll('a[href*="/company/"]');
                for (const link of companyLinks) {
                    const text = link.innerText?.trim();
                    // Filter out navigation/menu links - company name should be short
                    if (text && text.length > 0 && text.length < 80 && 
                        !text.toLowerCase().includes('follow') &&
                        !text.toLowerCase().includes('show')) {
                        return text;
                    }
                }
                
                // Look for aria-label containing company name
                const ariaEls = document.querySelectorAll('[aria-label*="Company"], [aria-label*="company"]');
                for (const el of ariaEls) {
                    const ariaLabel = el.getAttribute('aria-label') || '';
                    const patterns = [
                        /Company,?\\s*(.+?)\\.?$/i,
                        /Company logo for,?\\s*(.+?)\\.?$/i
                    ];
                    for (const pattern of patterns) {
                        const match = ariaLabel.match(pattern);
                        if (match && match[1] && match[1].length < 80) {
                            return match[1].trim();
                        }
                    }
                }
                
                // Look in the top card area
                const topCard = document.querySelector('.jobs-unified-top-card, .job-details-jobs-unified-top-card');
                if (topCard) {
                    const companyEl = topCard.querySelector('a[href*="/company/"]');
                    if (companyEl) {
                        const text = companyEl.innerText?.trim();
                        if (text && text.length > 0 && text.length < 80) {
                            return text;
                        }
                    }
                }
                
                return null;
            }
        """)
        return company if company else None
    except Exception as e:
        logger.error("Error extracting company from page: {}", e)
        return None


async def _scrape_logo_from_page(page, job_title: str = "") -> Optional[str]:
    """Try to scrape company logo from a LinkedIn job detail page.
    
    Handles lazy-loading by scrolling and waiting, prioritizes data-delayed-url.
    """
    # Scroll to trigger lazy loading of images
    try:
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)
        await page.evaluate("window.scrollTo(0, 300)")
        await page.wait_for_timeout(500)
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    
    # Wait for lazy-loaded images (increased from 2s to 3s)
    await page.wait_for_timeout(3000)
    
    # Try to wait explicitly for images to be present
    try:
        await page.wait_for_selector("figure img, [aria-label*='Company'] img, img[alt*='logo']", timeout=2000)
    except Exception:
        pass  # Continue even if no specific image selector found
    
    # Use JavaScript for reliable extraction with lazy-load priority
    try:
        logo_url = await page.evaluate("""
            () => {
                // Helper to check if URL is valid (not placeholder)
                const isValidLogoUrl = (url) => {
                    if (!url) return false;
                    if (!url.startsWith('http')) return false;
                    if (url.includes('data:image')) return false;
                    if (url.includes('ghost')) return false;
                    if (url.includes('static-icon')) return false;
                    if (url.includes('/static/images/')) return false;
                    return true;
                };
                
                // Helper to get best image src - PRIORITIZE lazy-load attributes
                const getBestSrc = (img) => {
                    // Check data-delayed-url FIRST (LinkedIn's lazy loading)
                    const delayedUrl = img.getAttribute('data-delayed-url');
                    if (isValidLogoUrl(delayedUrl)) return delayedUrl;
                    
                    const dataSrc = img.getAttribute('data-src');
                    if (isValidLogoUrl(dataSrc)) return dataSrc;
                    
                    const src = img.getAttribute('src');
                    if (isValidLogoUrl(src)) return src;
                    
                    return null;
                };
                
                // Method 1: Figure with Company logo aria-label
                const figures = document.querySelectorAll('figure[aria-label*="Company"], figure[aria-label*="logo"], figure[aria-label*="Logo"]');
                for (const fig of figures) {
                    const img = fig.querySelector('img');
                    if (img) {
                        const src = getBestSrc(img);
                        if (src) return src;
                    }
                }
                
                // Method 2: Company logo in top card area
                const topCardSelectors = [
                    '.jobs-unified-top-card',
                    '.job-details-jobs-unified-top-card',
                    '[class*="top-card"]',
                    '[class*="company-logo"]'
                ];
                for (const sel of topCardSelectors) {
                    const container = document.querySelector(sel);
                    if (container) {
                        const img = container.querySelector('img');
                        if (img) {
                            const src = getBestSrc(img);
                            if (src) return src;
                        }
                    }
                }
                
                // Method 3: Any element with aria-label containing logo
                const ariaEls = document.querySelectorAll('[aria-label*="logo"], [aria-label*="Logo"], [aria-label*="Company"], [aria-label*="company"]');
                for (const el of ariaEls) {
                    const img = el.querySelector('img') || (el.tagName === 'IMG' ? el : null);
                    if (img) {
                        const src = getBestSrc(img);
                        if (src) return src;
                    }
                }
                
                // Method 4: Images with logo/company in alt text
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const alt = (img.getAttribute('alt') || '').toLowerCase();
                    const ariaLabel = (img.getAttribute('aria-label') || '').toLowerCase();
                    if (alt.includes('logo') || alt.includes('company') ||
                        ariaLabel.includes('logo') || ariaLabel.includes('company')) {
                        const src = getBestSrc(img);
                        if (src) return src;
                    }
                }
                
                // Method 5: First image with company-logo pattern in URL
                for (const img of imgs) {
                    const src = getBestSrc(img);
                    if (src && (src.includes('company-logo') || src.includes('/shrink_'))) {
                        return src;
                    }
                }
                
                return null;
            }
        """)
        if logo_url:
            logger.info("Logo found for '{}' via JS: {}", job_title[:30] if job_title else "job", logo_url[:60])
            return logo_url
    except Exception as e:
        logger.warning("JS logo extraction failed for '{}': {}", job_title[:30] if job_title else "job", e)
    
    # Fallback to CSS selectors with lazy-load priority
    logo_selectors = [
        "figure[aria-label*='Company'] img",
        "figure[aria-label*='logo'] img",
        "figure[aria-label*='Logo'] img",
        ".job-details-jobs-unified-top-card__company-logo img",
        ".jobs-unified-top-card__company-logo img",
        "[class*='company-logo'] img",
        "img[alt*='logo']",
        "img[alt*='company']",
    ]
    
    for sel in logo_selectors:
        try:
            logo_el = await page.query_selector(sel)
            if logo_el:
                # Check data-delayed-url FIRST
                for attr in ["data-delayed-url", "data-src", "src"]:
                    logo_url = await logo_el.get_attribute(attr)
                    if logo_url and logo_url.startswith("http") and "data:image" not in logo_url:
                        if "ghost" not in logo_url.lower() and "static-icon" not in logo_url.lower():
                            logger.info("Logo found for '{}' via CSS: {}", job_title[:30] if job_title else "job", logo_url[:60])
                            return logo_url
        except Exception:
            continue
    
    logger.warning("No logo found for '{}' on detail page", job_title[:30] if job_title else "job")
    return None


async def scrape_job_descriptions_batch(jobs: List[JobRecord]) -> dict[int, str]:
    """
    Scrape descriptions for multiple jobs efficiently using a single browser instance.
    
    Returns a dict mapping job_id to description text.
    """
    settings = get_settings()
    results: dict[int, str] = {}
    
    async with async_playwright() as p:
        # Launch browser in headless mode to avoid disrupting user
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        for job in jobs:
            if job.id is None:
                continue
                
            # Check cache first
            existing = get_job_full_description(job.id)
            if existing:
                results[job.id] = existing
                continue
            
            try:
                logger.info("Scraping description for job {}: {}", job.id, job.title)
                await page.goto(str(job.url), wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
                
                description: Optional[str] = None
                description_selectors = [
                    "div.jobs-description__content",
                    "div.jobs-box__html-content",
                    "section.description",
                    "div.description__text",
                    "div[class*='description']",
                    "#job-details",
                ]
                
                for sel in description_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            text = await el.inner_text()
                            if text and len(text.strip()) > 100:
                                description = text.strip()
                                break
                    except Exception:
                        continue
                
                if description:
                    update_job_description(job.id, description)
                    results[job.id] = description
                    logger.debug("Scraped {} chars for job {}", len(description), job.id)
                
                # Also try to scrape logo
                logo_url = await _scrape_logo_from_page(page)
                if logo_url:
                    update_job_logo(job.id, logo_url)
                
                if not description:
                    logger.warning("No description found for job {}", job.id)
                    
            except Exception as exc:
                logger.error("Error scraping job {}: {}", job.id, exc)
                continue
        
        await browser.close()
    
    logger.info("Scraped descriptions for {}/{} jobs", len(results), len(jobs))
    return results


async def extract_job_detail(job: JobRecord) -> Optional[JobDetail]:
    """
    Open a job listing and extract structured details and raw text.

    The Browser Use agent is prompted to return a JSON object with keys:
      title, company, location, employment_type, seniority, full_description, recruiter_info.
    
    NOTE: This function uses browser_use which may have compatibility issues.
    Prefer using scrape_job_description() for simpler description extraction.
    """
    # Lazy imports to avoid crash on module load
    try:
        from browser_use import Agent
        from ..browser import create_browser_agent
    except Exception as exc:
        logger.error("Failed to import browser_use: {}. Use scrape_job_description instead.", exc)
        return None

    s = get_settings()
    raw_dir = Path(s.data.get("raw_jobs_dir", "./data/raw_jobs"))
    raw_dir.mkdir(parents=True, exist_ok=True)

    task = (
        "Open the following LinkedIn job URL and read its content carefully.\n"
        f"URL: {job.url}\n\n"
        "Extract structured information:\n"
        "- title\n"
        "- company\n"
        "- location\n"
        "- employment_type if visible\n"
        "- seniority if visible\n"
        "- full job description text\n"
        "- recruiter or hiring team info if visible\n\n"
        "Return ONLY JSON with these keys and no additional commentary."
    )
    agent: Agent = create_browser_agent(task)
    logger.info("Extracting details for job {} - {}", job.id, job.url)
    result = await agent.run()

    try:
        data = result.extracted_content if hasattr(result, "extracted_content") else None
        if not isinstance(data, dict):
            logger.warning("Job detail agent did not return dict for job {}", job.id)
            return None
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to parse job detail result: {}", exc)
        return None

    full_description = str(data.get("full_description", "")).strip()
    filename = timestamped_filename(f"job_{job.id}", ".json")
    raw_path = raw_dir / filename
    save_json(raw_path, {"job_id": job.id, "url": str(job.url), "data": data})

    detail = JobDetail(
        job=job,
        employment_type=data.get("employment_type"),
        seniority=data.get("seniority"),
        full_description=full_description,
        recruiter_info=data.get("recruiter_info"),
        raw_html_path=str(raw_path),
    )
    logger.info("Saved raw job detail at {}", raw_path)
    return detail

