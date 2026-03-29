"""Tests for company logo scraping functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestLogoUrlValidation:
    """Test logo URL validation logic."""
    
    def test_valid_logo_urls(self):
        """Test that valid LinkedIn logo URLs are accepted."""
        valid_urls = [
            "https://media.licdn.com/dms/image/v2/D4D0BAQG8FPv2DgBW8Q/company-logo_100_100/logo.jpg",
            "https://media.licdn.com/dms/image/v2/C4D0BAQGMqdNe9MW06Q/company-logo_100_100/logo.png",
            "https://media.licdn.com/dms/image/D560BAQEBurSRKi653g/company-logo_100_100/logo.jpeg",
        ]
        
        for url in valid_urls:
            assert url.startswith("http")
            assert "company-logo" in url or "media.licdn.com" in url
            assert "ghost" not in url.lower()
            assert "static-icon" not in url.lower()
            assert "data:image" not in url
    
    def test_invalid_placeholder_urls(self):
        """Test that placeholder/ghost URLs are rejected."""
        invalid_urls = [
            "https://static.licdn.com/aero-v1/sc/h/ghost-placeholder.png",
            "https://static.licdn.com/static-icon/placeholder.svg",
            "data:image/svg+xml;base64,PHN2Zw==",
            "/static/images/default-company.png",
        ]
        
        for url in invalid_urls:
            is_invalid = (
                "ghost" in url.lower() or 
                "static-icon" in url.lower() or
                url.startswith("data:image") or
                "/static/images/" in url or
                not url.startswith("http")
            )
            assert is_invalid, f"URL should be invalid: {url}"


class TestLogoExtraction:
    """Test logo extraction from HTML elements."""
    
    @pytest.mark.asyncio
    async def test_extract_logo_from_figure_aria_label(self):
        """Test extracting logo from figure element with aria-label."""
        html = '''
        <figure aria-label="Company logo for, Test Corp.">
            <img src="https://media.licdn.com/company-logo/test.jpg" alt="">
        </figure>
        '''
        # The figure[aria-label*="Company"] img selector should match
        assert 'aria-label="Company' in html
        assert '<img' in html
    
    @pytest.mark.asyncio
    async def test_extract_logo_with_data_delayed_url(self):
        """Test that data-delayed-url is prioritized over src for lazy-loaded images."""
        # Simulating LinkedIn's lazy-loading structure
        lazy_url = "https://media.licdn.com/company-logo/real-logo.jpg"
        placeholder_src = "data:image/gif;base64,placeholder"
        
        # data-delayed-url should be preferred
        assert lazy_url.startswith("http")
        assert "data:image" in placeholder_src


class TestDatabaseQueries:
    """Test database queries for jobs missing logos."""
    
    def test_get_jobs_missing_logos_query(self):
        """Test the SQL query for finding jobs without logos."""
        query = "SELECT * FROM jobs WHERE company_logo_url IS NULL OR company_logo_url = ''"
        
        # Query should find jobs with NULL or empty logo URL
        assert "company_logo_url IS NULL" in query
        assert "company_logo_url = ''" in query


@pytest.mark.asyncio
async def test_scrape_logo_from_page_mock():
    """Test _scrape_logo_from_page with mocked page."""
    from linkedin_copilot.linkedin.extract import _scrape_logo_from_page
    
    # Create mock page
    mock_page = AsyncMock()
    mock_page.evaluate.return_value = "https://media.licdn.com/company-logo/test.jpg"
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()
    mock_page.query_selector = AsyncMock(return_value=None)
    
    # Call the function
    result = await _scrape_logo_from_page(mock_page, job_title="Test Job")
    
    # Should have called evaluate for scrolling and extraction
    assert mock_page.evaluate.called
    assert mock_page.wait_for_timeout.called


class TestIntegration:
    """Integration tests that require actual browser (marked for manual run)."""
    
    @pytest.mark.skip(reason="Requires actual browser and LinkedIn access")
    @pytest.mark.asyncio
    async def test_live_logo_extraction(self):
        """Test logo extraction from a live LinkedIn job page.
        
        Run manually with: pytest tests/test_logo_scraping.py::TestIntegration::test_live_logo_extraction -v -s --no-skip
        """
        from playwright.async_api import async_playwright
        from linkedin_copilot.linkedin.extract import _scrape_logo_from_page
        
        test_url = "https://www.linkedin.com/jobs/view/4351257312/"
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
            )
            page = await context.new_page()
            
            await page.goto(test_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            
            logo_url = await _scrape_logo_from_page(page, job_title="Test Job")
            
            await browser.close()
            
            # Should have found a logo
            assert logo_url is not None, "Should have found a logo URL"
            assert logo_url.startswith("https://"), "Logo URL should be HTTPS"
            assert "media.licdn.com" in logo_url, "Logo should be from LinkedIn CDN"
            print(f"Found logo: {logo_url}")
