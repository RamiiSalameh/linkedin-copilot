"""
LinkedIn Easy Apply session management.

This module provides the core engine for managing in-app job application sessions,
including browser automation, screenshot streaming, and action execution.
"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from ..config import get_settings
from ..db import (
    create_apply_session,
    get_apply_session,
    update_apply_session,
    update_apply_session_status,
    update_apply_session_fields,
    save_session_action,
    update_session_action,
    get_job_by_id,
)
from ..llm import get_llm
from ..logging_setup import logger
from ..models import (
    ApplySession,
    ApplySessionStatus,
    FormField,
    ApplicationAction,
    ActionType,
    ActionStatus,
    WebSocketMessage,
)
from ..scoring.matcher import load_profile
from ..ui_hints import EASY_APPLY_BUTTON_KEY, get_ranked_selectors, record_selector_success, fingerprint_from_text
from .auth import apply_session_to_context, session_exists
from .form_detector import (
    detect_form_fields,
    detect_form_buttons,
    get_form_progress,
    suggest_field_values,
    EASY_APPLY_SELECTORS,
)


# Session timeout in seconds
SESSION_TIMEOUT = 600  # 10 minutes

# Screenshot capture interval in milliseconds
SCREENSHOT_INTERVAL_MS = 200  # 5 FPS


class ApplySessionEngine:
    """
    Engine for managing an in-app application session.
    
    This class handles the lifecycle of an application session, including:
    - Browser automation with Playwright
    - Screenshot capture and streaming via WebSocket
    - Form field detection and AI-assisted suggestions
    - Action execution with user confirmation
    """
    
    def __init__(
        self,
        job_id: int,
        on_message: Optional[Callable[[WebSocketMessage], Any]] = None,
    ):
        self.job_id = job_id
        self.on_message = on_message
        
        self.session: Optional[ApplySession] = None
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Optional interactive popup mode (escape hatch)
        self._popup_browser: Optional[Browser] = None
        self._popup_context: Optional[BrowserContext] = None
        self._popup_page: Optional[Page] = None
        
        self._screenshot_task: Optional[asyncio.Task] = None
        self._running = False
        self._paused = False
        self._connected_clients: Set[Any] = set()
        
        self._settings = get_settings()
        self._profile: Optional[Dict[str, Any]] = None
        self._resume_text: Optional[str] = None
        self._profile_name: Optional[str] = None
        self._last_manual_click: Optional[Dict[str, Any]] = None
    
    async def initialize(self) -> ApplySession:
        """
        Initialize the session and browser.
        
        Returns:
            The created ApplySession
        """
        job = get_job_by_id(self.job_id)
        if not job:
            raise ValueError(f"Job {self.job_id} not found")
        
        if not job.easy_apply:
            raise ValueError(f"Job {self.job_id} does not support Easy Apply")
        
        # Create session
        session_id = str(uuid.uuid4())
        screenshots_dir = Path(self._settings.data.get("screenshots_dir", "./data/screenshots"))
        screenshots_dir = screenshots_dir / f"session_{session_id}"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        self.session = ApplySession(
            id=session_id,
            job_id=job.id,
            job_title=job.title,
            company=job.company,
            job_url=str(job.url),
            status=ApplySessionStatus.IDLE,
            screenshots_dir=str(screenshots_dir),
        )
        
        # Save to database
        create_apply_session(self.session)
        
        # Load profile and resume
        try:
            self._profile = load_profile()
            if hasattr(self._profile, "model_dump"):
                self._profile = self._profile.model_dump()
        except Exception as e:
            logger.warning("Failed to load profile: {}", e)
            self._profile = {}

        # Identify current user (used for per-user UI hints)
        try:
            from .auth import get_session_profile_name
            self._profile_name = get_session_profile_name()
        except Exception:
            self._profile_name = None
        
        try:
            resume_path = Path(self._settings.env.default_resume_path)
            if resume_path.exists():
                self._resume_text = resume_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to load resume: {}", e)
        
        logger.info("Initialized apply session {} for job {}", session_id, job.id)
        
        return self.session
    
    async def start(self) -> None:
        """
        Start the application session.
        
        This opens the browser, navigates to the job, and begins the application flow.
        """
        if not self.session:
            raise RuntimeError("Session not initialized. Call initialize() first.")
        
        self._running = True
        
        try:
            # Launch browser
            await self._launch_browser()
            
            # Start screenshot capture IMMEDIATELY so user can see what's happening
            self._screenshot_task = asyncio.create_task(self._screenshot_loop())
            
            # Update status
            self.session.status = ApplySessionStatus.NAVIGATING
            update_apply_session_status(self.session.id, ApplySessionStatus.NAVIGATING)
            await self._broadcast_status()
            
            # Navigate to job URL
            await self._navigate_to_job()
            
            # Send initial screenshot after navigation
            await asyncio.sleep(0.5)
            screenshot = await self.get_screenshot()
            if screenshot:
                await self._broadcast_message("screenshot", {"image": screenshot})
            
            # Click Easy Apply button
            self.session.status = ApplySessionStatus.CLICKING_APPLY
            update_apply_session_status(self.session.id, ApplySessionStatus.CLICKING_APPLY)
            await self._broadcast_status()

            try:
                await self._click_easy_apply()
            except RuntimeError as e:
                # If automation can't click Easy Apply, fall back to manual action.
                msg = str(e)
                if "Easy Apply button not found" in msg:
                    await self._set_manual_action(
                        ApplySessionStatus.MANUAL_EASY_APPLY_NEEDED,
                        instruction="Auto-click failed. Click LinkedIn’s “Easy Apply” button in the in-app preview, then click “I clicked Easy Apply” below.",
                        action_label="I clicked Easy Apply",
                        action="retry_after_manual_easy_apply",
                    )
                    return
                raise

            # Detect form fields
            await self._detect_and_suggest_fields()

            # If we can't detect fields, fall back to manual detection guidance.
            if not self.session.detected_fields:
                await self._set_manual_action(
                    ApplySessionStatus.MANUAL_FORM_DETECT_NEEDED,
                    instruction="We opened the application, but couldn’t detect form fields. If the form is visible in the in-app preview, fill required fields manually (or click Retry), then continue.",
                    action_label="Retry detection",
                    action="retry_form_detection",
                )
                return
            
            # Update status to ready
            self.session.status = ApplySessionStatus.FORM_READY
            update_apply_session_status(self.session.id, ApplySessionStatus.FORM_READY)
            await self._broadcast_status()
            
        except Exception as e:
            logger.error("Error starting apply session: {}", e)
            # Take a diagnostic screenshot before reporting error
            try:
                screenshot = await self.get_screenshot()
                if screenshot:
                    await self._broadcast_message("screenshot", {"image": screenshot})
            except Exception:
                pass
            await self._handle_error(str(e))
    
    async def stop(self) -> None:
        """Stop the session and cleanup resources."""
        self._running = False
        
        if self._screenshot_task:
            self._screenshot_task.cancel()
            try:
                await self._screenshot_task
            except asyncio.CancelledError:
                pass
        
        await self._cleanup_browser()
        await self._cleanup_popup()
        
        if self.session and self.session.is_active():
            self.session.status = ApplySessionStatus.CANCELLED
            self.session.ended_at = datetime.utcnow()
            update_apply_session(self.session)
            await self._broadcast_status()
        
        logger.info("Stopped apply session {}", self.session.id if self.session else "unknown")
    
    async def pause(self) -> None:
        """Pause screenshot streaming."""
        self._paused = True
        await self._broadcast_message("status", {"paused": True})
    
    async def resume(self) -> None:
        """Resume screenshot streaming."""
        self._paused = False
        await self._broadcast_message("status", {"paused": False})
    
    async def fill_field(self, field_id: str, value: str) -> bool:
        """
        Fill a specific form field.
        
        Args:
            field_id: The ID of the field to fill
            value: The value to fill
        
        Returns:
            True if successful, False otherwise
        """
        if not self.page or not self.session:
            return False
        
        # Find the field
        field = next((f for f in self.session.detected_fields if f.field_id == field_id), None)
        if not field:
            logger.warning("Field {} not found in session", field_id)
            return False
        
        # Create action
        action = ApplicationAction(
            session_id=self.session.id,
            action_type=ActionType.FILL_FIELD,
            target_field_id=field_id,
            target_selector=field.selector,
            value=value,
            status=ActionStatus.EXECUTING,
        )
        action_id = save_session_action(action)
        
        try:
            self.session.status = ApplySessionStatus.FILLING
            await self._broadcast_status()
            
            # Fill the field
            success = await self._execute_fill(field, value)
            
            if success:
                action.status = ActionStatus.COMPLETED
                action.executed_at = datetime.utcnow()
                field.current_value = value
                update_session_action(action_id, ActionStatus.COMPLETED)
                
                # Update fields in DB
                update_apply_session_fields(self.session.id, self.session.detected_fields)
                
                # Broadcast field update
                await self._broadcast_message("field_updated", {
                    "field_id": field_id,
                    "value": value,
                })
            else:
                action.status = ActionStatus.FAILED
                action.error_message = "Failed to fill field"
                update_session_action(action_id, ActionStatus.FAILED, "Failed to fill field")
            
            self.session.status = ApplySessionStatus.FORM_READY
            await self._broadcast_status()
            
            return success
            
        except Exception as e:
            logger.error("Error filling field {}: {}", field_id, e)
            update_session_action(action_id, ActionStatus.FAILED, str(e))
            return False
    
    async def fill_all_suggested(self) -> Dict[str, bool]:
        """
        Fill all fields that have suggestions.
        
        Returns:
            Dict mapping field_id to success status
        """
        results = {}
        
        if not self.session:
            return results
        
        for field in self.session.detected_fields:
            if field.suggested_value and not field.current_value:
                success = await self.fill_field(field.field_id, field.suggested_value)
                results[field.field_id] = success
                await asyncio.sleep(0.3)  # Small delay between fills
        
        return results
    
    async def next_step(self) -> bool:
        """
        Click the Next button to proceed to the next form page.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.page or not self.session:
            return False
        
        action = ApplicationAction(
            session_id=self.session.id,
            action_type=ActionType.NEXT_STEP,
            status=ActionStatus.EXECUTING,
        )
        action_id = save_session_action(action)
        
        try:
            self.session.status = ApplySessionStatus.NEXT_PAGE
            await self._broadcast_status()
            
            # Click next button
            clicked = False
            for selector in EASY_APPLY_SELECTORS["next_button"]:
                try:
                    button = self.page.locator(selector)
                    if await button.count() > 0 and await button.first.is_visible():
                        await button.first.click()
                        clicked = True
                        break
                except Exception:
                    continue
            
            if not clicked:
                update_session_action(action_id, ActionStatus.FAILED, "Next button not found")
                await self._set_manual_action(
                    ApplySessionStatus.MANUAL_NEXT_NEEDED,
                    instruction="Auto-click failed. Click LinkedIn’s “Next/Continue” button in the in-app preview, then click “I clicked Next” below.",
                    action_label="I clicked Next",
                    action="retry_after_manual_next",
                )
                return False
            
            # Wait for page to update
            await asyncio.sleep(1)
            
            # Update progress
            step, total = await get_form_progress(self.page)
            self.session.current_step = step
            self.session.total_steps = total
            
            # Re-detect fields
            await self._detect_and_suggest_fields()
            
            # Check if we're on review page
            buttons = await detect_form_buttons(self.page)
            if buttons.get("submit"):
                self.session.status = ApplySessionStatus.REVIEWING
            else:
                self.session.status = ApplySessionStatus.FORM_READY
            
            update_apply_session(self.session)
            update_session_action(action_id, ActionStatus.COMPLETED)
            await self._broadcast_status()
            
            return True
            
        except Exception as e:
            logger.error("Error clicking next: {}", e)
            update_session_action(action_id, ActionStatus.FAILED, str(e))
            await self._handle_error(str(e))
            return False
    
    async def submit(self, confirmed: bool = False) -> bool:
        """
        Submit the application.
        
        Args:
            confirmed: Must be True to actually submit (safety check)
        
        Returns:
            True if successful, False otherwise
        """
        if not confirmed:
            logger.warning("Submit called without confirmation")
            return False
        
        if not self.page or not self.session:
            return False
        
        action = ApplicationAction(
            session_id=self.session.id,
            action_type=ActionType.SUBMIT,
            status=ActionStatus.EXECUTING,
        )
        action_id = save_session_action(action)
        
        try:
            self.session.status = ApplySessionStatus.SUBMITTING
            await self._broadcast_status()
            
            # Click submit button
            clicked = False
            for selector in EASY_APPLY_SELECTORS["submit_button"]:
                try:
                    button = self.page.locator(selector)
                    if await button.count() > 0 and await button.first.is_visible():
                        await button.first.click()
                        clicked = True
                        break
                except Exception:
                    continue
            
            if not clicked:
                update_session_action(action_id, ActionStatus.FAILED, "Submit button not found")
                await self._set_manual_action(
                    ApplySessionStatus.MANUAL_SUBMIT_NEEDED,
                    instruction="Auto-click failed. Click LinkedIn’s “Submit application” button in the in-app preview. When you’re done, click “I submitted” below.",
                    action_label="I submitted",
                    action="confirm_manual_submit",
                )
                return False
            
            # Wait for submission to complete
            await asyncio.sleep(2)
            
            # Check for success indicator
            success_selectors = [
                "h2:has-text('Application sent')",
                "div:has-text('Your application was sent')",
                "div[data-test-success-message]",
            ]
            
            submitted = False
            for selector in success_selectors:
                try:
                    if await self.page.locator(selector).count() > 0:
                        submitted = True
                        break
                except Exception:
                    continue
            
            if submitted:
                self.session.status = ApplySessionStatus.SUBMITTED
                self.session.ended_at = datetime.utcnow()
                update_apply_session(self.session)
                update_session_action(action_id, ActionStatus.COMPLETED)
                await self._broadcast_message("submitted", {"success": True})
                
                # Update job status to applied
                from ..db import update_job_status
                from ..models import JobStatus
                update_job_status(self.job_id, JobStatus.APPLIED)
                
                logger.info("Successfully submitted application for job {}", self.job_id)
                return True
            else:
                update_session_action(action_id, ActionStatus.FAILED, "Submission not confirmed")
                return False
            
        except Exception as e:
            logger.error("Error submitting application: {}", e)
            update_session_action(action_id, ActionStatus.FAILED, str(e))
            await self._handle_error(str(e))
            return False

    async def retry_after_manual_easy_apply(self) -> bool:
        """After user manually clicks Easy Apply, retry form detection."""
        if not self.page or not self.session:
            return False

        # Give LinkedIn a moment to render the modal.
        await asyncio.sleep(1.0)
        await self._detect_and_suggest_fields()

        if self.session.detected_fields:
            # If manual click led to success, store a lightweight hint for future ranking.
            try:
                if self.page:
                    meta: Dict[str, Any] = {}
                    if self._last_manual_click:
                        meta["manual_click"] = self._last_manual_click
                    meta["url"] = self.page.url
                    meta["title"] = await self.page.title()
                    # Avoid storing HTML; fingerprint helps correlate layout variants.
                    meta["fingerprint"] = fingerprint_from_text(meta["url"] + "|" + (meta.get("title") or ""))
                    record_selector_success(
                        EASY_APPLY_BUTTON_KEY,
                        "manual_click_proxy",
                        self._profile_name,
                        meta=meta,
                    )
            except Exception:
                pass
            self.session.status = ApplySessionStatus.FORM_READY
            update_apply_session_status(self.session.id, ApplySessionStatus.FORM_READY)
            await self._broadcast_status()
            return True

        await self._set_manual_action(
            ApplySessionStatus.MANUAL_FORM_DETECT_NEEDED,
            instruction="Still couldn’t detect fields. If the form is visible, fill required fields manually in the browser view, then click Next there, or try Retry detection again.",
            action_label="Retry detection",
            action="retry_form_detection",
        )
        return False

    async def retry_form_detection(self) -> bool:
        """Retry detecting fields without changing the page state."""
        if not self.page or not self.session:
            return False

        await asyncio.sleep(0.5)
        await self._detect_and_suggest_fields()

        if self.session.detected_fields:
            self.session.status = ApplySessionStatus.FORM_READY
            update_apply_session_status(self.session.id, ApplySessionStatus.FORM_READY)
            await self._broadcast_status()
            return True

        await self._set_manual_action(
            ApplySessionStatus.MANUAL_FORM_DETECT_NEEDED,
            instruction="No fields detected yet. If the modal is open, you can proceed manually in the browser view and then click “I clicked Next” after you advance.",
            action_label="I clicked Next",
            action="retry_after_manual_next",
        )
        return False

    async def retry_after_manual_next(self) -> bool:
        """After user manually clicks Next/Continue, refresh progress and fields."""
        if not self.page or not self.session:
            return False

        await asyncio.sleep(1.0)

        step, total = await get_form_progress(self.page)
        self.session.current_step = step
        self.session.total_steps = total

        await self._detect_and_suggest_fields()

        buttons = await detect_form_buttons(self.page)
        if buttons.get("submit"):
            self.session.status = ApplySessionStatus.REVIEWING
        elif self.session.detected_fields:
            self.session.status = ApplySessionStatus.FORM_READY
        else:
            await self._set_manual_action(
                ApplySessionStatus.MANUAL_FORM_DETECT_NEEDED,
                instruction="We advanced a step, but still couldn’t detect fields. Continue manually in the browser view; you can retry detection at any time.",
                action_label="Retry detection",
                action="retry_form_detection",
            )
            return False

        update_apply_session(self.session)
        await self._broadcast_status()
        return True

    async def confirm_manual_submit(self) -> bool:
        """User claims they submitted manually; mark applied and end session."""
        if not self.session:
            return False

        from ..db import update_job_status
        from ..models import JobStatus

        self.session.status = ApplySessionStatus.SUBMITTED
        self.session.ended_at = datetime.utcnow()
        update_apply_session(self.session)
        update_job_status(self.job_id, JobStatus.APPLIED)
        await self._broadcast_message("submitted", {"success": True, "manual": True})
        await self._broadcast_status()
        await self.stop()
        return True

    async def click_at(self, x: float, y: float) -> bool:
        """Proxy a UI click to the Playwright page (viewport coordinates)."""
        if not self.page or not self.session:
            return False
        try:
            if self.session.status == ApplySessionStatus.MANUAL_EASY_APPLY_NEEDED:
                self._last_manual_click = {
                    "x": x,
                    "y": y,
                    "at": datetime.utcnow().isoformat(),
                }
            await self.page.mouse.click(x, y)
            return True
        except Exception as e:
            logger.debug("Click proxy failed at ({}, {}): {}", x, y, e)
            return False
    
    def add_websocket_client(self, client: Any) -> None:
        """Add a WebSocket client for broadcasting."""
        self._connected_clients.add(client)
    
    def remove_websocket_client(self, client: Any) -> None:
        """Remove a WebSocket client."""
        self._connected_clients.discard(client)
    
    async def get_screenshot(self) -> Optional[str]:
        """
        Capture and return current screenshot as base64.
        
        Returns:
            Base64 encoded JPEG image, or None if failed
        """
        if not self.page:
            return None
        
        try:
            screenshot_bytes = await self.page.screenshot(
                type="jpeg",
                quality=70,
                full_page=False,
            )
            return base64.b64encode(screenshot_bytes).decode("utf-8")
        except Exception as e:
            logger.debug("Error capturing screenshot: {}", e)
            return None
    
    # Private methods
    
    async def _launch_browser(self) -> None:
        """Launch Playwright browser with LinkedIn session.
        
        Note: The in-app browser view is a screenshot stream. For manual fallback
        steps, we support click-through by proxying UI clicks to Playwright.
        """
        self.playwright = await async_playwright().start()
        
        # Keep apply sessions headless for stability; UI can proxy manual clicks.
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        
        # Apply LinkedIn session cookies
        if session_exists():
            await apply_session_to_context(self.context)
            logger.info("Applied LinkedIn session to browser context")
        else:
            raise RuntimeError("LinkedIn session not found. Please login first.")
        
        self.page = await self.context.new_page()
    
    async def _cleanup_browser(self) -> None:
        """Cleanup browser resources."""
        if self.page:
            try:
                await self.page.close()
            except Exception:
                pass
            self.page = None
        
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None
        
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

    async def _cleanup_popup(self) -> None:
        """Cleanup interactive popup resources."""
        if self._popup_page:
            try:
                await self._popup_page.close()
            except Exception:
                pass
            self._popup_page = None

        if self._popup_context:
            try:
                await self._popup_context.close()
            except Exception:
                pass
            self._popup_context = None

        if self._popup_browser:
            try:
                await self._popup_browser.close()
            except Exception:
                pass
            self._popup_browser = None

    async def open_interactive_popup(self) -> bool:
        """
        Open an interactive (headful) browser window as an escape hatch.

        This reuses the same Playwright instance and LinkedIn cookies, navigates to the
        current session URL, and switches the streaming/click-proxy page to the popup.
        """
        if not self.session:
            return False

        # Ensure playwright is initialized
        if not self.playwright:
            self.playwright = await async_playwright().start()

        # Already opened
        if self._popup_page:
            try:
                await self._popup_page.bring_to_front()
            except Exception:
                pass
            return True

        try:
            self._popup_browser = await self.playwright.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            self._popup_context = await self._popup_browser.new_context(
                viewport={"width": 1280, "height": 800},
                device_scale_factor=1,
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )

            if session_exists():
                await apply_session_to_context(self._popup_context)
            else:
                return False

            self._popup_page = await self._popup_context.new_page()
            await self._popup_page.goto(self.session.job_url, wait_until="domcontentloaded")

            # Switch streaming/click-proxy to popup to keep UI consistent with user actions.
            self.page = self._popup_page

            await self._broadcast_message("status", {
                "status": self.session.status.value,
                "popup_open": True,
            })
            return True
        except Exception as e:
            logger.warning("Failed to open interactive popup: {}", e)
            return False
    
    async def _navigate_to_job(self) -> None:
        """Navigate to the job URL."""
        if not self.page or not self.session:
            return
        
        await self.page.goto(self.session.job_url, wait_until="domcontentloaded")
        
        # Wait for page to be fully interactive
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass  # Continue even if networkidle times out
        
        await asyncio.sleep(2)  # Additional wait for dynamic content
        
        logger.info("Navigated to job URL: {}", self.session.job_url)
    
    async def _click_easy_apply(self) -> None:
        """Click the Easy Apply button on the job page."""
        if not self.page:
            return
        
        # Wait a bit more for the button to appear (LinkedIn loads dynamically)
        await asyncio.sleep(1)
        
        # Extended list of selectors for Easy Apply button (fallback)
        easy_apply_selectors_fallback = [
            # Primary selectors
            "button.jobs-apply-button",
            "button.jobs-apply-button--top-card",
            # Aria-based selectors
            "button[aria-label*='Easy Apply']",
            "button[aria-label*='easy apply']",
            # Text-based selectors
            "button:has-text('Easy Apply')",
            "button:has-text('easy apply')",
            # Container-based selectors
            ".jobs-apply-button--top-card button",
            ".jobs-s-apply button",
            ".jobs-unified-top-card__content--two-pane button.jobs-apply-button",
            # Generic apply button that might be Easy Apply
            "button.artdeco-button--primary:has-text('Apply')",
            # SVG icon based (LinkedIn uses icons)
            "button:has(svg[data-test-icon='linkedin-bug-small'])",
            # Job details page specific
            ".job-details-jobs-unified-top-card__container button.jobs-apply-button",
            ".jobs-details__main-content button:has-text('Easy Apply')",
        ]

        easy_apply_selectors = get_ranked_selectors(
            EASY_APPLY_BUTTON_KEY,
            self._profile_name,
            easy_apply_selectors_fallback,
        )
        
        # Try each selector
        for selector in easy_apply_selectors:
            try:
                button = self.page.locator(selector)
                count = await button.count()
                if count > 0:
                    # Check if any of them are visible
                    for i in range(count):
                        btn = button.nth(i)
                        if await btn.is_visible():
                            # Check button text to confirm it's Easy Apply
                            text = await btn.inner_text()
                            logger.debug("Found button with text: '{}' using selector: {}", text, selector)
                            
                            await btn.click()
                            await asyncio.sleep(2)  # Wait for modal to open
                            
                            # Verify modal opened
                            modal_selectors = [
                                "div.jobs-easy-apply-modal",
                                "div[data-test-modal]",
                                "div.artdeco-modal",
                                ".jobs-easy-apply-content",
                            ]
                            for modal_sel in modal_selectors:
                                if await self.page.locator(modal_sel).count() > 0:
                                    logger.info("Clicked Easy Apply button and modal opened")
                                    record_selector_success(
                                        EASY_APPLY_BUTTON_KEY,
                                        selector,
                                        self._profile_name,
                                        meta={"url": self.page.url, "button_text": text.strip() if text else ""},
                                    )
                                    return
                            
                            # Modal might take longer to open
                            await asyncio.sleep(1)
                            for modal_sel in modal_selectors:
                                if await self.page.locator(modal_sel).count() > 0:
                                    logger.info("Clicked Easy Apply button and modal opened (delayed)")
                                    record_selector_success(
                                        EASY_APPLY_BUTTON_KEY,
                                        selector,
                                        self._profile_name,
                                        meta={"url": self.page.url, "button_text": text.strip() if text else "", "delayed": True},
                                    )
                                    return
                            
                            logger.warning("Clicked button but modal did not open, trying next selector")
            except Exception as e:
                logger.debug("Selector {} failed: {}", selector, e)
                continue
        
        # Log page state for debugging
        try:
            page_title = await self.page.title()
            url = self.page.url
            logger.warning("Easy Apply button not found. Page: '{}', URL: {}", page_title, url)
            
            # Check if we need to scroll
            await self.page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)
            
            # Try one more time after scroll
            for selector in ["button.jobs-apply-button", "button:has-text('Easy Apply')"]:
                button = self.page.locator(selector)
                if await button.count() > 0 and await button.first.is_visible():
                    await button.first.click()
                    await asyncio.sleep(2)
                    logger.info("Clicked Easy Apply button after scroll")
                    return
        except Exception as e:
            logger.debug("Debug info gathering failed: {}", e)
        
        raise RuntimeError("Easy Apply button not found")
    
    async def _detect_and_suggest_fields(self) -> None:
        """Detect form fields and add AI suggestions."""
        if not self.page or not self.session:
            return
        
        # Detect fields
        fields = await detect_form_fields(self.page)
        
        # Add profile-based suggestions
        if self._profile:
            fields = suggest_field_values(fields, self._profile)
        
        # For fields without suggestions, try AI
        for field in fields:
            if not field.suggested_value and field.label:
                suggestion = await self._get_ai_suggestion(field)
                if suggestion:
                    field.suggested_value = suggestion
                    field.suggestion_source = "ai"
        
        # Update session
        self.session.detected_fields = fields
        
        # Get progress
        step, total = await get_form_progress(self.page)
        self.session.current_step = step
        self.session.total_steps = total
        
        # Save to DB
        update_apply_session_fields(self.session.id, fields)
        
        # Broadcast fields
        await self._broadcast_message("fields", {
            "fields": [f.to_dict() for f in fields],
            "current_step": step,
            "total_steps": total,
        })
        
        logger.info("Detected {} form fields", len(fields))
    
    async def _get_ai_suggestion(self, field: FormField) -> Optional[str]:
        """Get AI-generated suggestion for a form field."""
        if not self._profile or not self._resume_text:
            return None
        
        try:
            llm = get_llm()
            answer = llm.generate_screening_answer(
                json.dumps(self._profile, ensure_ascii=False),
                self._resume_text,
                field.label,
            )
            return answer if answer else None
        except Exception as e:
            logger.debug("Failed to get AI suggestion for {}: {}", field.label, e)
            return None
    
    async def _execute_fill(self, field: FormField, value: str) -> bool:
        """Execute filling a field with a value."""
        if not self.page:
            return False
        
        try:
            # Try selector first
            if field.selector:
                element = self.page.locator(field.selector)
                if await element.count() > 0:
                    await self._fill_element(element.first, field, value)
                    return True
            
            # Try finding by label
            if field.label:
                # Try aria-label
                element = self.page.locator(f"[aria-label*='{field.label}']")
                if await element.count() > 0:
                    await self._fill_element(element.first, field, value)
                    return True
                
                # Try label text
                label = self.page.locator(f"label:has-text('{field.label}')")
                if await label.count() > 0:
                    label_for = await label.first.get_attribute("for")
                    if label_for:
                        element = self.page.locator(f"#{label_for}")
                        if await element.count() > 0:
                            await self._fill_element(element.first, field, value)
                            return True
            
            return False
            
        except Exception as e:
            logger.error("Error filling field {}: {}", field.field_id, e)
            return False
    
    async def _fill_element(self, element: Any, field: FormField, value: str) -> None:
        """Fill a specific element based on field type."""
        from ..models import FormFieldType
        
        if field.field_type == FormFieldType.SELECT:
            await element.select_option(value)
        elif field.field_type == FormFieldType.CHECKBOX:
            if value.lower() in ("true", "yes", "checked", "1"):
                if not await element.is_checked():
                    await element.click()
            else:
                if await element.is_checked():
                    await element.click()
        elif field.field_type == FormFieldType.RADIO:
            # Find radio with matching value
            radio = element.locator(f"input[value='{value}']")
            if await radio.count() > 0:
                await radio.first.click()
            else:
                # Try clicking label with value text
                label = element.locator(f"label:has-text('{value}')")
                if await label.count() > 0:
                    await label.first.click()
        else:
            # Text-based fields
            await element.fill("")
            await element.fill(value)
    
    async def _screenshot_loop(self) -> None:
        """Continuous screenshot capture loop."""
        while self._running:
            if not self._paused:
                screenshot = await self.get_screenshot()
                if screenshot:
                    self.session.last_screenshot = screenshot
                    await self._broadcast_message("screenshot", {
                        "image": screenshot,
                    })
            
            await asyncio.sleep(SCREENSHOT_INTERVAL_MS / 1000)
    
    async def _handle_error(self, error: str) -> None:
        """Handle an error during the session."""
        if self.session:
            self.session.status = ApplySessionStatus.FAILED
            self.session.error_message = error
            self.session.ended_at = datetime.utcnow()
            update_apply_session(self.session)
        
        await self._broadcast_message("error", {"message": error})
        
        # Keep screenshot streaming for a bit so user can see the page state
        # Don't stop immediately - let user see what happened
        await asyncio.sleep(3)
        
        await self.stop()
    
    async def _broadcast_status(self) -> None:
        """Broadcast current session status."""
        if self.session:
            await self._broadcast_message("status", {
                "status": self.session.status.value,
                "current_step": self.session.current_step,
                "total_steps": self.session.total_steps,
                "error": self.session.error_message,
            })

    async def _set_manual_action(
        self,
        status: ApplySessionStatus,
        instruction: str,
        action_label: str,
        action: str,
    ) -> None:
        """Enter a manual fallback state and instruct the user how to proceed."""
        if not self.session:
            return

        self.session.status = status
        self.session.error_message = None
        update_apply_session_status(self.session.id, status)
        await self._broadcast_message("status", {
            "status": self.session.status.value,
            "current_step": self.session.current_step,
            "total_steps": self.session.total_steps,
            "manual_action": {
                "instruction": instruction,
                "action_label": action_label,
                "action": action,
            },
        })
    
    async def _broadcast_message(self, msg_type: str, data: Dict[str, Any]) -> None:
        """Broadcast a message to all connected WebSocket clients."""
        message = WebSocketMessage(type=msg_type, data=data)
        
        # Call callback if set
        if self.on_message:
            try:
                await self.on_message(message)
            except Exception as e:
                logger.debug("Error in message callback: {}", e)
        
        # Send to connected clients
        msg_json = message.to_json()
        disconnected = set()
        
        for client in self._connected_clients:
            try:
                await client.send_text(msg_json)
            except Exception:
                disconnected.add(client)
        
        # Remove disconnected clients
        for client in disconnected:
            self._connected_clients.discard(client)


# Global session registry
_active_sessions: Dict[str, ApplySessionEngine] = {}


def get_active_session(session_id: str) -> Optional[ApplySessionEngine]:
    """Get an active session engine by ID."""
    return _active_sessions.get(session_id)


def get_session_for_job(job_id: int) -> Optional[ApplySessionEngine]:
    """Get active session engine for a job."""
    for engine in _active_sessions.values():
        if engine.session and engine.session.job_id == job_id:
            return engine
    return None


async def create_session(job_id: int, on_message: Optional[Callable] = None) -> ApplySessionEngine:
    """
    Create a new apply session for a job.
    
    Args:
        job_id: The job ID to apply for
        on_message: Optional callback for WebSocket messages
    
    Returns:
        The created ApplySessionEngine
    """
    # Check for existing active session
    existing = get_session_for_job(job_id)
    if existing and existing.session and existing.session.is_active():
        return existing
    
    engine = ApplySessionEngine(job_id, on_message)
    await engine.initialize()
    
    _active_sessions[engine.session.id] = engine
    
    return engine


async def cleanup_session(session_id: str) -> None:
    """Cleanup and remove a session."""
    engine = _active_sessions.pop(session_id, None)
    if engine:
        await engine.stop()
