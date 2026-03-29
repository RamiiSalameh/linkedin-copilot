from __future__ import annotations

from typing import Any, Dict, List

from ..logging_setup import logger
from ..models import ScreeningQuestion


async def fill_standard_fields(page: Any, profile: Dict[str, Any]) -> None:
    """
    Best-effort filling of common form fields using the profile data.

    This is intentionally conservative and should be extended over time.
    """
    mapping = {
        "name": profile.get("full_name"),
        "full name": profile.get("full_name"),
        "email": profile.get("email"),
        "phone": profile.get("phone"),
        "city": profile.get("city"),
        "location": f"{profile.get('city', '')}, {profile.get('country', '')}",
        "linkedin": profile.get("linkedin_url"),
        "github": profile.get("github_url"),
        "portfolio": profile.get("portfolio_url"),
    }

    for label, value in mapping.items():
        if not value:
            continue
        try:
            locator = page.get_by_label(label, exact=False)
            await locator.fill(str(value))
            logger.info("Filled field '{}' with profile value.", label)
        except Exception:  # noqa: BLE001
            continue


async def collect_screening_questions(page: Any) -> List[ScreeningQuestion]:
    """
    Inspect the current page for potential screening questions.

    This is intentionally heuristic-based and can be improved over time.
    """
    questions: List[ScreeningQuestion] = []
    try:
        labels = await page.locator("label").all()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to inspect labels for screening questions: {}", exc)
        return questions

    for label in labels:
        try:
            text = (await label.inner_text()).strip()
            if not text:
                continue
            questions.append(ScreeningQuestion(question_text=text))
        except Exception:  # noqa: BLE001
            continue

    logger.info("Detected {} potential screening questions on page.", len(questions))
    return questions

