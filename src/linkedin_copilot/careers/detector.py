"""
ATS detection from career page URLs.

Automatically detects which ATS platform a company uses based on their careers URL.
Supports resolving redirects so branded URLs (e.g. careers.philips.com) work when
they redirect to Workday or other ATS.
"""

import re
from typing import Optional, Tuple
from urllib.parse import urlparse

from ..models import ATSType
from ..logging_setup import logger

RESOLVE_TIMEOUT = 10.0
RESOLVE_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


ATS_URL_PATTERNS = {
    ATSType.GREENHOUSE: [
        r"boards\.greenhouse\.io/([\w-]+)",
        r"([\w-]+)\.greenhouse\.io",
        r"greenhouse\.io/([\w-]+)/jobs",
    ],
    ATSType.LEVER: [
        r"jobs\.lever\.co/([^/]+)",
        r"lever\.co/([^/]+)",
    ],
    ATSType.WORKDAY: [
        r"([\w-]+)\.wd\d+\.myworkdayjobs\.com",
        r"([\w-]+)\.workday\.com",
        r"myworkdayjobs\.com/.*?/([^/]+)",
    ],
    ATSType.ASHBY: [
        r"jobs\.ashbyhq\.com/([^/]+)",
        r"ashbyhq\.com/([^/]+)",
    ],
}


def detect_ats_type(url: str) -> Tuple[ATSType, Optional[str]]:
    """
    Detect ATS type and board token from a careers URL.
    
    Args:
        url: The careers page URL
    
    Returns:
        Tuple of (ATSType, board_token or None)
    
    Examples:
        >>> detect_ats_type("https://boards.greenhouse.io/stripe")
        (ATSType.GREENHOUSE, "stripe")
        
        >>> detect_ats_type("https://jobs.lever.co/netflix")
        (ATSType.LEVER, "netflix")
        
        >>> detect_ats_type("https://amazon.wd5.myworkdayjobs.com")
        (ATSType.WORKDAY, "amazon")
    """
    if not url:
        return ATSType.UNKNOWN, None
    
    url_lower = url.lower()
    
    for ats_type, patterns in ATS_URL_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, url_lower)
            if match:
                board_token = match.group(1) if match.lastindex else None
                logger.debug("Detected ATS {} with token {} from URL: {}", 
                           ats_type.value, board_token, url)
                return ats_type, board_token
    
    return ATSType.UNKNOWN, None


def extract_board_token(url: str, ats_type: ATSType) -> Optional[str]:
    """
    Extract board token for a specific ATS type.
    
    Args:
        url: The careers page URL
        ats_type: The ATS type to extract token for
    
    Returns:
        Board token string or None
    """
    if ats_type not in ATS_URL_PATTERNS:
        return None
    
    url_lower = url.lower()
    
    for pattern in ATS_URL_PATTERNS[ats_type]:
        match = re.search(pattern, url_lower)
        if match and match.lastindex:
            return match.group(1)
    
    return None


def extract_company_name_from_url(url: str) -> Optional[str]:
    """
    Try to extract company name from careers URL.
    
    This is a best-effort extraction that may need user correction.
    """
    ats_type, token = detect_ats_type(url)
    
    if token:
        name = token.replace("-", " ").replace("_", " ").title()
        return name
    
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    
    parts = hostname.split(".")
    if len(parts) >= 2:
        domain = parts[0] if parts[0] not in ("www", "jobs", "careers") else parts[1]
        return domain.replace("-", " ").replace("_", " ").title()
    
    return None


def normalize_careers_url(url: str) -> str:
    """
    Normalize careers URL to a consistent format.
    
    Removes trailing slashes, query parameters, and fragments.
    """
    parsed = urlparse(url)
    
    path = parsed.path.rstrip("/")
    
    normalized = f"{parsed.scheme}://{parsed.netloc}{path}"
    
    return normalized


async def resolve_careers_url(url: str) -> Tuple[str, Optional[str]]:
    """
    Follow redirects and return the final URL for a careers page.
    
    Enables branded URLs (e.g. careers.philips.com) to be resolved to their
    underlying ATS URL (e.g. myworkdayjobs.com) before detection.
    
    Args:
        url: The careers page URL (may redirect).
    
    Returns:
        Tuple of (final_url, error_message).
        On success: (final_url, None).
        On failure: (original_url, error_message).
    """
    if not url or not url.strip():
        return "", "URL is empty"
    
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    
    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=RESOLVE_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": RESOLVE_USER_AGENT},
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                return url, f"URL returned status {response.status_code}"
            final_url = str(response.url)
            logger.debug("Resolved {} -> {}", url, final_url)
            return final_url, None
    except httpx.TimeoutException:
        logger.warning("Timeout resolving URL: {}", url)
        return url, "Request timed out"
    except httpx.ConnectError as e:
        logger.warning("Connect error resolving URL {}: {}", url, e)
        return url, "Connection failed"
    except Exception as e:
        logger.warning("Error resolving URL {}: {}", url, e)
        return url, f"Resolution error: {str(e)}"


def is_supported_ats(url: str) -> bool:
    """Check if the URL is from a supported ATS."""
    ats_type, _ = detect_ats_type(url)
    return ats_type in (ATSType.GREENHOUSE, ATSType.LEVER, ATSType.WORKDAY)


def get_api_url(url: str) -> Optional[str]:
    """
    Get the API URL for fetching jobs from a careers URL.
    
    Returns None if ATS is not supported or URL is invalid.
    """
    ats_type, token = detect_ats_type(url)
    
    if not token:
        return None
    
    if ats_type == ATSType.GREENHOUSE:
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    
    elif ats_type == ATSType.LEVER:
        return f"https://api.lever.co/v0/postings/{token}?mode=json"
    
    return None


async def validate_careers_url(url: str) -> Tuple[bool, str]:
    """
    Validate that a careers URL is accessible and returns job data.
    
    Resolves redirects first so branded URLs (e.g. careers.philips.com) work.
    For Workday there is no GET API; we accept if the URL resolves and detects as Workday.
    
    Returns:
        Tuple of (is_valid, error_message or success message)
    """
    import httpx

    # Resolve redirects so branded URLs become direct ATS URLs
    final_url, resolve_error = await resolve_careers_url(url)
    if resolve_error:
        # Use original URL for detection so direct Workday URLs still work
        final_url = url.strip()
        if not final_url.startswith(("http://", "https://")):
            final_url = "https://" + final_url
    url_for_detection = final_url

    ats_type, board_token = detect_ats_type(url_for_detection)

    if ats_type == ATSType.UNKNOWN:
        return False, "Could not detect ATS type from URL"

    if ats_type == ATSType.WORKDAY:
        # No GET API; accept if we detected Workday (resolution already attempted)
        return True, "Workday career site detected; will be validated when scraping"

    if ats_type == ATSType.ASHBY:
        return False, "Ashby is not yet supported"

    api_url = get_api_url(url_for_detection)
    if not api_url:
        return False, "Could not extract board token from URL"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(api_url)

            if response.status_code == 404:
                return False, "Career board not found (404). Check the URL."

            if response.status_code == 403:
                return False, "Access denied (403). Board may be private."

            if response.status_code != 200:
                return False, f"API returned status {response.status_code}"

            data = response.json()

            if ats_type == ATSType.GREENHOUSE:
                jobs = data.get("jobs", [])
                return True, f"Found {len(jobs)} jobs on Greenhouse"
            elif ats_type == ATSType.LEVER:
                jobs = data if isinstance(data, list) else []
                return True, f"Found {len(jobs)} jobs on Lever"

            return True, "URL is valid"

    except httpx.TimeoutException:
        return False, "Request timed out"
    except Exception as e:
        return False, f"Validation error: {str(e)}"
