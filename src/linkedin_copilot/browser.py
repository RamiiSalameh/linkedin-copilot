from __future__ import annotations

from pathlib import Path
from typing import Any

from browser_use import Agent
from langchain_ollama import ChatOllama

from .config import get_settings
from .logging_setup import logger
from .utils import timestamped_filename


def create_llm_for_browser() -> ChatOllama:
    """Create an Ollama-backed LLM instance suitable for Browser Use."""
    s = get_settings()
    return ChatOllama(
        model=s.env.ollama_model,
        base_url=s.env.ollama_base_url,
        temperature=0.0,
        num_ctx=8192,
    )


def create_browser_agent(task: str) -> Agent:
    """Factory for Browser Use agent with configured browser.

    Note: the current Browser Use API does not expose a `BrowserConfig` object
    at the top level, so we rely on its default browser configuration and use
    the environment (e.g. `HEADLESS`) only for higher-level behavior.
    """
    llm = create_llm_for_browser()
    agent = Agent(
        task=task,
        llm=llm,
    )
    return agent


async def take_screenshot(page: Any, prefix: str) -> Path:
    """Take a screenshot with a timestamped file name."""
    s = get_settings()
    screenshots_dir = Path(s.browser.get("screenshot_dir", "./data/screenshots"))
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    filename = timestamped_filename(prefix, ".png")
    path = screenshots_dir / filename
    try:
        await page.screenshot(path=str(path))
        logger.info("Saved screenshot at {}", path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to save screenshot: {}", exc)
    return path

