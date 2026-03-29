"""
Career site scraping module for pulling jobs from company career pages.

Supports multiple ATS platforms:
- Greenhouse (public JSON API)
- Lever (public JSON API)
- Workday (browser-based, Playwright)
- Custom (browser-based, future)
"""

from .base import (
    JobSourceBase,
    SearchResult,
    job_matches_location_filter,
    normalize_location_filters,
)
from .greenhouse import GreenhouseScraper
from .lever import LeverScraper
from .workday import WorkdayScraper
from .detector import detect_ats_type, extract_board_token
from .registry import get_scraper, register_scraper, SCRAPER_REGISTRY

__all__ = [
    "JobSourceBase",
    "SearchResult",
    "job_matches_location_filter",
    "normalize_location_filters",
    "GreenhouseScraper",
    "LeverScraper",
    "WorkdayScraper",
    "detect_ats_type",
    "extract_board_token",
    "get_scraper",
    "register_scraper",
    "SCRAPER_REGISTRY",
]
