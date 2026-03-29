from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from ..config import get_settings
from ..db import insert_job, job_exists
from ..logging_setup import logger
from ..models import JobRecord, JobStatus
from .auth import apply_session_to_context, save_session_from_context, session_exists


class SearchResult:
    """Container for search results with statistics."""
    
    def __init__(self):
        self.jobs: List[JobRecord] = []
        self.duplicates: int = 0
        self.total_found: int = 0
    
    @property
    def new_jobs(self) -> int:
        return len(self.jobs)


async def search_jobs(
    keywords: str,
    location: str,
    easy_apply_only: bool = True,
    limit: int = 50,
    date_posted: str = None,
    experience_level: str = None,
    remote: str = None,
    job_type: str = None,
    use_auth: bool = True,
    anonymous: bool = False,
) -> SearchResult:
    """
    Use Playwright to search LinkedIn jobs via URL parameters and scrape results.

    This approach constructs the search URL directly instead of filling forms,
    which is more reliable as LinkedIn's form selectors change frequently.

    Args:
        keywords: Search keywords
        location: Job location
        easy_apply_only: Filter for Easy Apply jobs only
        limit: Maximum number of jobs to return
        date_posted: Time filter - "24h", "week", "month", or None
        experience_level: "entry", "associate", "mid_senior", "director", or None
        remote: "onsite", "remote", "hybrid", or None
        job_type: "full_time", "part_time", "contract", "temporary", "internship", or None
    """
    settings = get_settings()

    # Build LinkedIn Jobs search URL with query parameters
    # Use sortBy=R for relevance sorting to get better keyword matches
    base_url = "https://www.linkedin.com/jobs/search/"
    params = f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}&sortBy=R"
    if easy_apply_only:
        params += "&f_AL=true"  # Easy Apply filter

    # Date posted filter (f_TPR = time posted range)
    date_posted_map = {
        "24h": "r86400",
        "week": "r604800",
        "month": "r2592000",
    }
    if date_posted and date_posted in date_posted_map:
        params += f"&f_TPR={date_posted_map[date_posted]}"

    # Experience level filter (f_E)
    experience_level_map = {
        "internship": "1",
        "entry": "2",
        "associate": "3",
        "mid_senior": "4",
        "director": "5",
        "executive": "6",
    }
    if experience_level and experience_level in experience_level_map:
        params += f"&f_E={experience_level_map[experience_level]}"

    # Remote filter (f_WT = workplace type)
    remote_map = {
        "onsite": "1",
        "remote": "2",
        "hybrid": "3",
    }
    if remote and remote in remote_map:
        params += f"&f_WT={remote_map[remote]}"

    # Job type filter (f_JT)
    job_type_map = {
        "full_time": "F",
        "part_time": "P",
        "contract": "C",
        "temporary": "T",
        "internship": "I",
        "volunteer": "V",
        "other": "O",
    }
    if job_type and job_type in job_type_map:
        params += f"&f_JT={job_type_map[job_type]}"

    search_url = base_url + params

    result = SearchResult()

    async with async_playwright() as p:
        # Launch browser in headless mode to avoid disrupting user
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # Apply saved session cookies if available and requested (not in anonymous mode)
        is_authenticated = False
        if anonymous:
            logger.info("Anonymous/incognito mode: not using saved session")
        elif use_auth and session_exists():
            is_authenticated = await apply_session_to_context(context)
            if is_authenticated:
                logger.info("Using authenticated session for search")
            else:
                logger.info("Session exists but failed to apply, using anonymous search")
        
        page = await context.new_page()

        try:
            logger.info("Opening LinkedIn Jobs search: {}", search_url)
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

            # Wait for page to stabilize after any redirects
            await page.wait_for_timeout(3000)
            
            # Log actual URL after redirects (useful for debugging)
            actual_url = page.url
            logger.info("Actual page URL after load: {}", actual_url)
            
            # Check if redirected to login page
            if "/login" in actual_url or "/authwall" in actual_url:
                if is_authenticated:
                    logger.warning("Session expired or invalid. Clearing saved session.")
                    from .auth import clear_session
                    clear_session()
                
                if anonymous:
                    # In anonymous mode, try LinkedIn's public guest jobs URL
                    logger.info("Anonymous mode: trying public guest jobs URL...")
                    guest_url = f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(keywords)}&location={quote_plus(location)}"
                    if easy_apply_only:
                        guest_url += "&f_AL=true"
                    
                    await page.goto(guest_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)
                    actual_url = page.url
                    logger.info("Guest page URL: {}", actual_url)
                    
                    # If still on authwall, guest access is blocked
                    if "/login" in actual_url or "/authwall" in actual_url:
                        logger.warning("Anonymous search failed: LinkedIn requires login. No jobs found.")
                        await browser.close()
                        return result
                else:
                    logger.warning("LinkedIn requires login. Jobs may be limited to public listings.")
            elif is_authenticated:
                # Save updated cookies if we're authenticated (they may have been refreshed)
                await save_session_from_context(context)
            
            # Wait for job listings to load
            await page.wait_for_timeout(2000)

            # Take a debug screenshot
            screenshots_dir = Path(settings.browser.get("screenshot_dir", "./data/screenshots"))
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = screenshots_dir / "linkedin_search_debug.png"
            await page.screenshot(path=str(screenshot_path))
            logger.info("Saved debug screenshot to {}", screenshot_path)

            # Try multiple selector strategies for job cards
            # Includes both authenticated and guest page selectors
            job_card_selectors = [
                "div.job-card-container",
                "li.jobs-search-results__list-item",
                "div.jobs-search-results-list li",
                "ul.jobs-search__results-list > li",
                "[data-job-id]",
                # Guest/public page selectors
                "ul.jobs-search__results-list li.jobs-search-results-list__list-item",
                "div.base-card",
                "li.result-card",
                "div.job-search-card",
            ]

            cards = []
            for sel in job_card_selectors:
                cards = await page.query_selector_all(sel)
                if cards:
                    logger.info("Found {} job cards using selector: {}", len(cards), sel)
                    break

            if not cards:
                logger.warning("No job cards found. Check {} for page state.", screenshot_path)
                # Try to extract jobs from the page text as fallback
                page_text = await page.inner_text("body")
                logger.debug("Page text preview: {}", page_text[:500])
                await browser.close()
                return []

            # Wait extra time for lazy-loaded images to appear
            await page.wait_for_timeout(2000)
            
            # Scroll down to trigger image loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await page.wait_for_timeout(1000)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)
            
            for card in cards[:limit]:
                try:
                    # Try multiple selectors for each field
                    title = await _get_text(card, [
                        "a.job-card-list__title",
                        "a.job-card-container__link",
                        "[data-control-name='job_card_title']",
                        "a[href*='/jobs/view/']",
                        "strong",
                    ])
                    # Use JavaScript extraction as primary method (more reliable with LinkedIn's dynamic classes)
                    company = await _extract_company_js(card)
                    
                    # Fallback to CSS selectors if JS extraction failed
                    if not company:
                        company = await _get_text(card, [
                            "a[href*='/company/']",
                            ".job-card-container__company-name",
                            ".artdeco-entity-lockup__subtitle",
                        ])
                    
                    if company:
                        logger.debug("Extracted company: {}", company)
                    loc = await _get_text(card, [
                        "li.job-card-container__metadata-item:first-child",
                        "span.job-card-container__metadata-item--location",
                        ".job-card-container__metadata-wrapper li:first-child",
                        "span.job-card-container__metadata-item",
                        "li.job-card-container__metadata-item",
                        "[class*='location']",
                        ".artdeco-entity-lockup__caption",
                    ])

                    # Get job URL
                    href = None
                    for link_sel in ["a.job-card-list__title", "a.job-card-container__link", "a[href*='/jobs/view/']"]:
                        link_el = await card.query_selector(link_sel)
                        if link_el:
                            href = await link_el.get_attribute("href")
                            if href:
                                break

                    url = ""
                    if href:
                        url = href.split("?")[0]
                        if not url.startswith("http"):
                            url = "https://www.linkedin.com" + url

                    # Get company logo - use JavaScript extraction as primary method
                    logo_url = await _extract_logo_js(card, job_title=title)
                    
                    # Fallback to CSS selectors if JS extraction failed
                    if not logo_url:
                        logo_selectors = [
                            "figure[aria-label*='Company logo'] img",
                            "img[alt*='logo']",
                            "img[alt*='company']",
                            "img[data-delayed-url]",
                        ]
                        for logo_sel in logo_selectors:
                            logo_el = await card.query_selector(logo_sel)
                            if logo_el:
                                for attr in ["src", "data-delayed-url", "data-src"]:
                                    logo_url = await logo_el.get_attribute(attr)
                                    if logo_url and logo_url.startswith("http") and "data:image" not in logo_url:
                                        if "ghost" not in logo_url.lower():
                                            break
                                        logo_url = None
                                if logo_url:
                                    break

                    # Check for Easy Apply badge
                    easy_el = await card.query_selector("span:has-text('Easy Apply'), svg[data-test-icon='job-easy-apply']")
                    easy = easy_el is not None
                    
                    # Extract date posted
                    date_posted_text = await _extract_date_posted_js(card)
                    date_posted_dt = _parse_relative_date(date_posted_text) if date_posted_text else None

                    if not title or not url:
                        continue
                    if easy_apply_only and not easy:
                        continue
                    
                    result.total_found += 1
                    
                    if job_exists(url):
                        result.duplicates += 1
                        logger.debug("Duplicate job found: {} @ {}", title, company)
                        continue

                    job = JobRecord(
                        title=title or "Unknown",
                        company=company or "Unknown",
                        location=loc or "Unknown",
                        url=url,
                        date_found=datetime.utcnow(),
                        date_posted=date_posted_dt,
                        easy_apply=easy,
                        description_snippet=None,
                        company_logo_url=logo_url,
                        status=JobStatus.PENDING_SCRAPE,
                    )
                    job = insert_job(job)
                    result.jobs.append(job)
                    logger.debug("Added job: {} @ {}", title, company)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error while parsing job card: {}", exc)
                    continue
        finally:
            await browser.close()

    logger.info("Search complete: {} new jobs, {} duplicates, {} total found", 
                result.new_jobs, result.duplicates, result.total_found)
    return result


async def _get_text(element, selectors: List[str]) -> str:
    """Try multiple selectors and return the first matching text."""
    for sel in selectors:
        try:
            el = await element.query_selector(sel)
            if el:
                text = await el.inner_text()
                if text and text.strip():
                    return text.strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


async def _extract_company_js(card) -> str:
    """Extract company name using JavaScript with comprehensive selector strategies."""
    try:
        result = await card.evaluate("""
            (card) => {
                const debug = [];
                let company = '';
                
                // Method 1: Direct company link (most reliable)
                const companyLinks = card.querySelectorAll('a[href*="/company/"]');
                debug.push('Company links found: ' + companyLinks.length);
                for (const link of companyLinks) {
                    const text = link.innerText?.trim();
                    debug.push('Link text: ' + (text || 'empty'));
                    if (text && text.length > 0 && text.length < 100) {
                        company = text;
                        break;
                    }
                }
                if (company) return { company, debug };
                
                // Method 2: Figure with aria-label containing company name
                const figures = card.querySelectorAll('figure[aria-label]');
                debug.push('Figures with aria-label: ' + figures.length);
                for (const fig of figures) {
                    const ariaLabel = fig.getAttribute('aria-label') || '';
                    debug.push('Figure aria-label: ' + ariaLabel);
                    // Match patterns like "Company logo for, Cato Networks." or "Company, Cato Networks."
                    const patterns = [
                        /Company logo for,?\\s*(.+?)\\.?$/i,
                        /Company,?\\s*(.+?)\\.?$/i,
                        /logo for,?\\s*(.+?)\\.?$/i
                    ];
                    for (const pattern of patterns) {
                        const match = ariaLabel.match(pattern);
                        if (match && match[1]) {
                            company = match[1].trim();
                            break;
                        }
                    }
                    if (company) break;
                }
                if (company) return { company, debug };
                
                // Method 3: Any element with aria-label containing company info
                const ariaEls = card.querySelectorAll('[aria-label*="Company"], [aria-label*="company"], [aria-label*="logo"]');
                debug.push('Aria-label elements: ' + ariaEls.length);
                for (const el of ariaEls) {
                    const ariaLabel = el.getAttribute('aria-label') || '';
                    debug.push('Aria element: ' + ariaLabel.substring(0, 50));
                    const patterns = [
                        /Company logo for,?\\s*(.+?)\\.?$/i,
                        /Company,?\\s*(.+?)\\.?$/i,
                        /logo for,?\\s*(.+?)\\.?$/i
                    ];
                    for (const pattern of patterns) {
                        const match = ariaLabel.match(pattern);
                        if (match && match[1] && match[1].length > 1 && match[1].length < 100) {
                            company = match[1].trim();
                            break;
                        }
                    }
                    if (company) break;
                }
                if (company) return { company, debug };
                
                // Method 4: Image alt text
                const imgs = card.querySelectorAll('img[alt]');
                debug.push('Images with alt: ' + imgs.length);
                for (const img of imgs) {
                    const alt = img.getAttribute('alt') || '';
                    if (alt.toLowerCase().includes('logo') || alt.toLowerCase().includes('company')) {
                        debug.push('Img alt: ' + alt);
                        const patterns = [
                            /logo for,?\\s*(.+?)\\.?$/i,
                            /Company,?\\s*(.+?)\\.?$/i
                        ];
                        for (const pattern of patterns) {
                            const match = alt.match(pattern);
                            if (match && match[1]) {
                                company = match[1].trim();
                                break;
                            }
                        }
                    }
                    if (company) break;
                }
                if (company) return { company, debug };
                
                // Method 5: Parse text content - look for company name pattern
                const allText = card.innerText || '';
                const lines = allText.split('\\n').map(l => l.trim()).filter(l => l.length > 1 && l.length < 80);
                debug.push('Text lines: ' + lines.length + ', first 5: ' + lines.slice(0, 5).join(' | '));
                
                // Skip patterns - things that are NOT company names
                const skipPatterns = [
                    'easy apply', 'promoted', 'viewed', 'applied', 'be an early',
                    'alumni', 'actively', 'reposted', 'ago', 'applicants',
                    'hybrid', 'remote', 'on-site', 'full-time', 'part-time',
                    'contract', 'internship', 'response', 'insights', 'match',
                    'save', 'share', 'report', 'premium', 'verify'
                ];
                
                // Company is typically line 2 (after job title)
                for (let i = 1; i < Math.min(lines.length, 6); i++) {
                    const line = lines[i];
                    const lineLower = line.toLowerCase();
                    
                    // Skip if matches skip patterns
                    if (skipPatterns.some(p => lineLower.includes(p))) continue;
                    
                    // Skip if looks like location (City, Country pattern)
                    if (/^[A-Z][a-z]+.*,\\s*[A-Z]/.test(line) && 
                        /(israel|remote|usa|uk|germany|france|india|district|region)/i.test(line)) continue;
                    
                    // Skip if looks like a date
                    if (/\\d+\\s*(day|hour|week|month)s?\\s*ago/i.test(line)) continue;
                    
                    // This might be the company name
                    if (line.length > 1) {
                        company = line;
                        break;
                    }
                }
                
                return { company, debug };
            }
        """)
        
        if result and result.get('debug'):
            from ..logging_setup import logger
            logger.debug("Company extraction debug: {}", result.get('debug', []))
        
        return result.get('company', '').strip() if result else ""
    except Exception as e:
        from ..logging_setup import logger
        logger.error("Company extraction error: {}", str(e))
        return ""


async def _extract_logo_js(card, job_title: str = "") -> str:
    """Extract company logo URL using JavaScript with comprehensive strategies.
    
    Prioritizes data-delayed-url over src since LinkedIn uses lazy loading.
    """
    from ..logging_setup import logger
    import asyncio
    
    try:
        # First, scroll the card into view to trigger lazy loading
        await card.evaluate("(el) => el.scrollIntoView({ behavior: 'instant', block: 'center' })")
        
        # Increased wait time for lazy-loaded images (was 0.3s, now 1.0s)
        await asyncio.sleep(1.0)
        
        result = await card.evaluate("""
            (card) => {
                const debug = [];
                
                // Helper to check if URL is valid (not placeholder)
                const isValidLogoUrl = (url) => {
                    if (!url) return false;
                    if (!url.startsWith('http')) return false;
                    if (url.includes('data:image')) return false;
                    if (url.includes('ghost')) return false;
                    if (url.includes('static-icon')) return false;
                    if (url.includes('/static/images/')) return false;
                    if (url.includes('feed-dsa')) return false;
                    // Reject LinkedIn's default placeholder images
                    if (url.includes('static.licdn.com/aero-v1/')) return false;
                    return true;
                };
                
                // Helper to get best image src (prioritize lazy-load attributes)
                const getBestSrc = (img) => {
                    // IMPORTANT: Check lazy-load attributes FIRST before src
                    const delayedUrl = img.getAttribute('data-delayed-url');
                    if (isValidLogoUrl(delayedUrl)) return delayedUrl;
                    
                    const dataSrc = img.getAttribute('data-src');
                    if (isValidLogoUrl(dataSrc)) return dataSrc;
                    
                    const src = img.getAttribute('src');
                    if (isValidLogoUrl(src)) return src;
                    
                    return null;
                };
                
                // Method 1: Figure with Company logo aria-label (LinkedIn's structure)
                const figures = card.querySelectorAll('figure[aria-label*="Company"], figure[aria-label*="logo"], figure[aria-label*="Logo"]');
                debug.push('Logo figures: ' + figures.length);
                for (const fig of figures) {
                    const img = fig.querySelector('img');
                    if (img) {
                        const src = getBestSrc(img);
                        debug.push('Figure img: ' + (src ? src.substring(0, 60) : 'none'));
                        if (src) return { logo: src, debug };
                    }
                }
                
                // Method 2: Any element with aria-label containing logo/Company + img inside
                const ariaEls = card.querySelectorAll('[aria-label*="logo"], [aria-label*="Logo"], [aria-label*="Company"], [aria-label*="company"]');
                debug.push('Aria logo elements: ' + ariaEls.length);
                for (const el of ariaEls) {
                    const img = el.querySelector('img') || (el.tagName === 'IMG' ? el : null);
                    if (img) {
                        const src = getBestSrc(img);
                        if (src) {
                            debug.push('Aria el img: ' + src.substring(0, 60));
                            return { logo: src, debug };
                        }
                    }
                }
                
                // Method 3: Images with logo/company in alt text
                const imgs = card.querySelectorAll('img');
                debug.push('Total images: ' + imgs.length);
                for (const img of imgs) {
                    const alt = (img.getAttribute('alt') || '').toLowerCase();
                    const ariaLabel = (img.getAttribute('aria-label') || '').toLowerCase();
                    if (alt.includes('logo') || alt.includes('company') || 
                        ariaLabel.includes('logo') || ariaLabel.includes('company')) {
                        const src = getBestSrc(img);
                        if (src) {
                            debug.push('Img with logo alt: ' + src.substring(0, 60));
                            return { logo: src, debug };
                        }
                    }
                }
                
                // Method 4: First image with company-logo or shrink_ in URL
                for (const img of imgs) {
                    const src = getBestSrc(img);
                    if (src && (src.includes('company-logo') || src.includes('/shrink_') || src.includes('profile-displayphoto'))) {
                        debug.push('Pattern match img: ' + src.substring(0, 60));
                        return { logo: src, debug };
                    }
                }
                
                // Method 5: First valid image (last resort - company logo is usually first)
                for (const img of imgs) {
                    const src = getBestSrc(img);
                    if (src) {
                        debug.push('First valid img: ' + src.substring(0, 60));
                        return { logo: src, debug };
                    }
                }
                
                debug.push('No logo found');
                return { logo: null, debug };
            }
        """)
        
        logo_url = result.get('logo') if result else None
        
        # Log at INFO level for visibility
        if logo_url:
            logger.info("Logo found for '{}': {}", job_title[:30] if job_title else "unknown", logo_url[:60])
        else:
            logger.warning("Logo NOT found for '{}'. Debug: {}", job_title[:30] if job_title else "unknown", result.get('debug', []) if result else [])
        
        return logo_url
    except Exception as e:
        logger.error("Logo extraction error for '{}': {}", job_title[:30] if job_title else "unknown", str(e))
        return None


def _parse_relative_date(text: str) -> Optional[datetime]:
    """Parse LinkedIn's relative date strings like '2 days ago', '1 week ago', etc.
    
    Returns approximate datetime when the job was posted.
    """
    if not text:
        return None
    
    text = text.lower().strip()
    now = datetime.utcnow()
    
    # Match patterns like "2 days ago", "1 week ago", "3 hours ago"
    patterns = [
        (r'(\d+)\s*minute', 'minutes'),
        (r'(\d+)\s*hour', 'hours'),
        (r'(\d+)\s*day', 'days'),
        (r'(\d+)\s*week', 'weeks'),
        (r'(\d+)\s*month', 'months'),
    ]
    
    for pattern, unit in patterns:
        match = re.search(pattern, text)
        if match:
            value = int(match.group(1))
            if unit == 'minutes':
                return now - timedelta(minutes=value)
            elif unit == 'hours':
                return now - timedelta(hours=value)
            elif unit == 'days':
                return now - timedelta(days=value)
            elif unit == 'weeks':
                return now - timedelta(weeks=value)
            elif unit == 'months':
                return now - timedelta(days=value * 30)
    
    # Handle special cases
    if 'just now' in text or 'moment' in text:
        return now
    if 'yesterday' in text:
        return now - timedelta(days=1)
    
    return None


async def _extract_date_posted_js(card) -> Optional[str]:
    """Extract 'Posted X days ago' text from job card using JavaScript."""
    try:
        result = await card.evaluate("""
            (card) => {
                // Look for time-related elements
                const timeEls = card.querySelectorAll('time, [datetime]');
                for (const el of timeEls) {
                    const text = el.innerText?.trim();
                    if (text && /ago|posted|just|moment/i.test(text)) {
                        return { text, source: 'time element' };
                    }
                }
                
                // Look for text containing ago patterns
                const allText = card.innerText || '';
                const patterns = [
                    /(?:posted|reposted)?\\s*(\\d+\\s*(?:minute|hour|day|week|month)s?\\s*ago)/i,
                    /(just\\s*now)/i,
                    /(moment ago)/i,
                    /(yesterday)/i
                ];
                
                for (const pattern of patterns) {
                    const match = allText.match(pattern);
                    if (match) {
                        return { text: match[1] || match[0], source: 'text match' };
                    }
                }
                
                // Look for specific LinkedIn metadata elements
                const metaEls = card.querySelectorAll('[class*="time"], [class*="posted"], [class*="metadata"]');
                for (const el of metaEls) {
                    const text = el.innerText?.trim();
                    if (text && /\\d+\\s*(minute|hour|day|week|month)/i.test(text)) {
                        return { text, source: 'meta element' };
                    }
                }
                
                return null;
            }
        """)
        
        if result and result.get('text'):
            logger.debug("Date posted extraction: '{}' via {}", result.get('text'), result.get('source'))
            return result.get('text')
        return None
    except Exception as e:
        logger.debug("Date posted extraction error: {}", str(e))
        return None

