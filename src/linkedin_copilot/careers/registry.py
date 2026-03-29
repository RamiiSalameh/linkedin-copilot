"""
Scraper registry for dynamic dispatch based on ATS type.

Provides a central registry of available scrapers and factory functions
to get the appropriate scraper for a company.
"""

from typing import Dict, Optional, Type

from .base import JobSourceBase
from .greenhouse import GreenhouseScraper
from .lever import LeverScraper
from .workday import WorkdayScraper
from ..models import ATSType, Company
from ..logging_setup import logger


SCRAPER_REGISTRY: Dict[ATSType, Type[JobSourceBase]] = {
    ATSType.GREENHOUSE: GreenhouseScraper,
    ATSType.LEVER: LeverScraper,
    ATSType.WORKDAY: WorkdayScraper,
}


def register_scraper(ats_type: ATSType, scraper_class: Type[JobSourceBase]) -> None:
    """
    Register a scraper class for an ATS type.
    
    Args:
        ats_type: The ATS type to register for
        scraper_class: The scraper class to use
    """
    SCRAPER_REGISTRY[ats_type] = scraper_class
    logger.info("Registered scraper {} for ATS type {}", 
               scraper_class.__name__, ats_type.value)


def get_scraper(ats_type: ATSType) -> Optional[JobSourceBase]:
    """
    Get a scraper instance for the given ATS type.
    
    Args:
        ats_type: The ATS type to get a scraper for
    
    Returns:
        Scraper instance or None if not supported
    """
    scraper_class = SCRAPER_REGISTRY.get(ats_type)
    if scraper_class is None:
        logger.warning("No scraper registered for ATS type: {}", ats_type.value)
        return None
    
    return scraper_class()


def get_scraper_for_company(company: Company) -> Optional[JobSourceBase]:
    """
    Get the appropriate scraper for a company.
    
    Args:
        company: The company to get a scraper for
    
    Returns:
        Scraper instance or None if ATS type is not supported
    """
    return get_scraper(company.ats_type)


def is_ats_supported(ats_type: ATSType) -> bool:
    """Check if an ATS type has a registered scraper."""
    return ats_type in SCRAPER_REGISTRY


def get_supported_ats_types() -> list:
    """Get list of supported ATS types."""
    return list(SCRAPER_REGISTRY.keys())
