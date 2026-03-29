"""LinkedIn session/cookie management for authenticated searches."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import BrowserContext, Page, async_playwright

from ..config import get_settings
from ..logging_setup import logger


# Default session file location
SESSION_FILE = Path("data/linkedin_session.json")


def get_session_path() -> Path:
    """Get the path to the session file."""
    settings = get_settings()
    session_path = settings.data.get("session_path", str(SESSION_FILE))
    return Path(session_path)


def session_exists() -> bool:
    """Check if a saved LinkedIn session exists."""
    return get_session_path().exists()


def load_session() -> Optional[List[Dict[str, Any]]]:
    """Load saved LinkedIn session cookies."""
    path = get_session_path()
    if not path.exists():
        return None
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
        if cookies:
            logger.info("Loaded {} cookies from session file", len(cookies))
            return cookies
        return None
    except Exception as exc:
        logger.error("Failed to load session: {}", exc)
        return None


def get_session_profile_name() -> Optional[str]:
    """Get the stored profile name from the session file."""
    path = get_session_path()
    if not path.exists():
        return None
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("profile_name")
    except Exception:
        return None


def save_session(cookies: List[Dict[str, Any]], profile_name: str | None = None) -> None:
    """Save LinkedIn session cookies to file."""
    path = get_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing data to preserve profile_name if not provided
    existing_data = {}
    if path.exists() and profile_name is None:
        try:
            existing_data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    
    data = {
        "cookies": cookies,
        "profile_name": profile_name or existing_data.get("profile_name"),
        "note": "LinkedIn session cookies. Delete this file to force re-login.",
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Saved {} cookies to session file", len(cookies))


def clear_session() -> bool:
    """Clear the saved session."""
    path = get_session_path()
    if path.exists():
        path.unlink()
        logger.info("Cleared session file")
        return True
    return False


async def apply_session_to_context(context: BrowserContext) -> bool:
    """Apply saved session cookies to a browser context."""
    cookies = load_session()
    if not cookies:
        return False
    
    try:
        await context.add_cookies(cookies)
        logger.info("Applied {} cookies to browser context", len(cookies))
        return True
    except Exception as exc:
        logger.error("Failed to apply session cookies: {}", exc)
        return False


async def save_session_from_context(context: BrowserContext, profile_name: str | None = None) -> None:
    """Save cookies from browser context to session file."""
    try:
        cookies = await context.cookies()
        # Filter to LinkedIn cookies only
        linkedin_cookies = [c for c in cookies if "linkedin.com" in c.get("domain", "")]
        if linkedin_cookies:
            save_session(linkedin_cookies, profile_name)
    except Exception as exc:
        logger.error("Failed to save session from context: {}", exc)


async def extract_profile_name(page: Page) -> Optional[str]:
    """Extract the logged-in user's profile name from the LinkedIn page."""
    try:
        # Navigate to /in/me which redirects to the user's profile
        await page.goto("https://www.linkedin.com/in/me/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        
        # The URL should now contain the user's public identifier
        current_url = page.url
        
        # Get page content for extraction
        content = await page.content()
        
        # Pattern 1: Look for the profile name in the h1 heading (most reliable on profile page)
        try:
            h1_element = await page.query_selector("h1")
            if h1_element:
                name = await h1_element.text_content()
                if name and name.strip() and len(name.strip()) > 2:
                    cleaned_name = name.strip()
                    logger.info("Found profile name from h1: {}", cleaned_name)
                    return cleaned_name
        except Exception:
            pass
        
        # Pattern 2: Look for name in title tag (profile pages show "Name | LinkedIn")
        title_match = re.search(r'<title>([^|]+)\s*\|\s*LinkedIn', content)
        if title_match:
            potential_name = title_match.group(1).strip()
            if potential_name and len(potential_name) > 2 and ' ' in potential_name:
                logger.info("Found profile name from title: {}", potential_name)
                return potential_name
        
        # Pattern 3: Look for profile data in page JSON
        profile_patterns = [
            r'"firstName":"([^"]+)","lastName":"([^"]+)"',
            r'"firstName":\s*\{"text":"([^"]+)"\}[^}]*"lastName":\s*\{"text":"([^"]+)"\}',
        ]
        
        for pattern in profile_patterns:
            match = re.search(pattern, content)
            if match:
                first_name = match.group(1)
                last_name = match.group(2)
                if len(first_name) > 0 and len(last_name) > 0:
                    full_name = f"{first_name} {last_name}"
                    logger.info("Found profile name from page data: {}", full_name)
                    return full_name
        
        # Pattern 4: Try various DOM selectors
        selectors = [
            ".pv-text-details__left-panel h1",
            ".text-heading-xlarge",
            "[data-anonymize='person-name']",
            ".profile-card h1",
        ]
        
        for selector in selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    name = await element.text_content()
                    if name and name.strip() and len(name.strip()) > 2:
                        cleaned_name = name.strip()
                        logger.info("Found profile name via selector '{}': {}", selector, cleaned_name)
                        return cleaned_name
            except Exception:
                continue
        
        logger.info("Could not extract profile name from LinkedIn page")
        return None
        
    except Exception as exc:
        logger.warning("Error extracting profile name: {}", exc)
        return None


async def check_login_status(page: Page) -> bool:
    """Check if the current page indicates the user is logged in."""
    url = page.url
    
    logger.debug("Checking login status for URL: {}", url)
    
    # If we're on a login or auth wall page, not logged in
    if "/login" in url or "/authwall" in url or "/checkpoint" in url or "/uas/" in url:
        logger.debug("On login/auth page - not logged in")
        return False
    
    # If we're on the feed, we're definitely logged in
    if "/feed" in url:
        logger.debug("On feed page - logged in!")
        return True
    
    # If we're on the homepage and NOT on login, likely logged in
    if url.rstrip("/") == "https://www.linkedin.com" or "/home" in url:
        # Double-check by looking for login elements
        try:
            login_btn = await page.query_selector("a[href*='login'], button[data-tracking-control-name='guest_homepage-basic_sign-in-submit']")
            if login_btn:
                logger.debug("Found login button on homepage - not logged in")
                return False
            else:
                logger.debug("On homepage without login button - logged in")
                return True
        except Exception:
            pass
    
    # Try to find elements that indicate logged-in state
    try:
        # Multiple selectors for the "Me" dropdown/icon (logged-in indicator)
        logged_in_selectors = [
            # Modern LinkedIn selectors
            "[data-control-name='nav.settings']",
            ".global-nav__me",
            "#ember[class*='global-nav__me']",
            "button[aria-label*='menu']",
            # Profile photo in nav
            "img.global-nav__me-photo",
            ".feed-identity-module",
            # Any nav element with profile
            "nav [href*='/in/']",
            # Messaging indicator
            ".msg-overlay-bubble-header",
            ".messaging-container",
            # Feed elements (only visible when logged in)
            ".feed-shared-update-v2",
            ".share-box-feed-entry",
        ]
        
        for selector in logged_in_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    logger.debug("Found logged-in indicator: {}", selector)
                    return True
            except Exception:
                continue
        
        # Check if page contains typical logged-in content
        page_content = await page.content()
        logged_in_indicators = [
            '"isLoggedIn":true',
            'data-member-id=',
            'voyager/api/me',
            'feed-identity-module',
        ]
        for indicator in logged_in_indicators:
            if indicator in page_content:
                logger.debug("Found logged-in indicator in page content: {}", indicator)
                return True
                
    except Exception as exc:
        logger.debug("Error checking login status: {}", exc)
    
    logger.debug("No login indicators found")
    return False


async def interactive_login() -> bool:
    """
    Open a browser window for manual LinkedIn login.
    
    The user manually logs in, and we save the session cookies afterward.
    Returns True if login was successful.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # Must be visible for manual login
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        try:
            logger.info("Opening LinkedIn login page for manual authentication...")
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            
            logger.info("Please log in to LinkedIn in the browser window...")
            logger.info("The session will be saved automatically after successful login.")
            logger.info("You can also close the browser window after logging in.")
            
            # Poll for login completion (up to 5 minutes)
            last_url = page.url
            for i in range(150):  # 5 minutes with 2-second intervals
                try:
                    # Check if page is still available
                    current_url = page.url
                except Exception:
                    # Page might have been closed
                    logger.info("Browser connection lost - checking if cookies were saved")
                    break
                
                # URL changed - user might have logged in
                if current_url != last_url:
                    logger.info("URL changed to: {}", current_url)
                    last_url = current_url
                
                # Direct URL check - patterns that indicate logged-in state
                logged_in_url_patterns = ["/feed", "/mynetwork", "/jobs", "/messaging", "/notifications", "/in/"]
                if any(pattern in current_url for pattern in logged_in_url_patterns):
                    logger.info("Redirected to logged-in area ({})! Saving session...", current_url[:50])
                    # Extract profile name before saving
                    profile_name = await extract_profile_name(page)
                    await save_session_from_context(context, profile_name)
                    await browser.close()
                    return True
                
                # If no longer on login page and not on authwall, likely logged in
                if "/login" not in current_url and "/authwall" not in current_url and "/checkpoint" not in current_url:
                    if "linkedin.com" in current_url:
                        # Give it a moment to settle
                        await page.wait_for_timeout(1000)
                        # Do a full check
                        if await check_login_status(page):
                            logger.info("Login detected! Saving session...")
                            # Extract profile name before saving
                            profile_name = await extract_profile_name(page)
                            await save_session_from_context(context, profile_name)
                            await browser.close()
                            return True
                
                # If still on login page after 30 seconds, log a reminder
                if i == 15:
                    logger.info("Still waiting for login... Please complete the login in the browser.")
                
                await page.wait_for_timeout(2000)
            
            # Timeout reached
            logger.warning("Login timeout - no login detected within 5 minutes")
            try:
                await browser.close()
            except Exception:
                pass
            return False
            
        except Exception as exc:
            # Check if this is a disconnection (user closed browser after logging in)
            exc_str = str(exc).lower()
            if "target closed" in exc_str or "connection closed" in exc_str or "browser" in exc_str:
                # Browser was closed - check if we have valid cookies
                logger.info("Browser was closed. Checking if login was successful...")
                if session_exists():
                    # Validate the session
                    is_valid = await validate_session()
                    if is_valid:
                        logger.info("Session validated successfully!")
                        return True
                    else:
                        logger.warning("Session exists but is invalid")
                        return False
            
            logger.error("Error during interactive login: {}", exc)
            try:
                await browser.close()
            except Exception:
                pass
            return False


async def validate_session() -> tuple[bool, str | None]:
    """
    Validate that the saved session is still working.
    
    Opens a headless browser, applies cookies, and checks if we're logged in.
    Returns a tuple of (is_valid, profile_name).
    """
    if not session_exists():
        return False, None
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # Apply saved cookies
        if not await apply_session_to_context(context):
            await browser.close()
            return False, None
        
        page = await context.new_page()
        
        try:
            # Navigate to LinkedIn and check if we're logged in
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            
            is_logged_in = await check_login_status(page)
            profile_name = None
            
            if is_logged_in:
                logger.info("Session is valid - user is logged in")
                # Extract and save profile name
                profile_name = await extract_profile_name(page)
                # Update cookies in case they were refreshed
                await save_session_from_context(context, profile_name)
            else:
                logger.warning("Session is invalid or expired")
            
            await browser.close()
            return is_logged_in, profile_name
            
        except Exception as exc:
            logger.error("Error validating session: {}", exc)
            await browser.close()
            return False, None
