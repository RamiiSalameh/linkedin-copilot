from __future__ import annotations

from typing import Iterable, List

from ..config import get_settings
from ..logging_setup import logger
from ..utils import prompt_yes_no


def is_risky_button_text(text: str) -> bool:
    """Return True if button label is considered risky (submit-like)."""
    settings = get_settings()
    risky_texts: Iterable[str] = settings.safety.get("risky_button_texts", [])
    lowered = text.strip().lower()
    return any(token in lowered for token in risky_texts)


async def guard_before_submit(description: str) -> bool:
    """
    Guardrail before any high-risk submit-like action.

    Returns True if the action is allowed (i.e., user confirmed and config allows),
    otherwise False.
    """
    settings = get_settings()
    if not settings.env.allow_final_submit or not settings.safety.get("allow_final_submit", False):
        logger.warning(
            "Final submit is disabled by configuration. Action blocked: {}", description
        )
        return False

    logger.warning("MANUAL REVIEW REQUIRED: {}", description)
    allow = prompt_yes_no("High-risk action requested. Proceed?")
    if not allow:
        logger.info("User declined high-risk action.")
        return False
    return True

