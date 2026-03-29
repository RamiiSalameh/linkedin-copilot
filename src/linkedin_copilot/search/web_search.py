from __future__ import annotations

import asyncio
from typing import List, Optional

from ..logging_setup import logger

try:
    from tavily import TavilyClient
except Exception:  # pragma: no cover - handled at runtime
    TavilyClient = None  # type: ignore[assignment]


class TavilyWebSearchClient:
    """Thin async wrapper over tavily-python with graceful fallback."""

    def __init__(self, api_key: Optional[str]) -> None:
        self._api_key = api_key

    async def search(self, query: str, max_results: int = 5) -> List[str]:
        if not self._api_key or not TavilyClient:
            return []

        def _run() -> List[str]:
            try:
                client = TavilyClient(api_key=self._api_key)
                response = client.search(query=query, max_results=max_results)
                results = response.get("results", []) if isinstance(response, dict) else []
                snippets: List[str] = []
                for item in results:
                    content = item.get("content") if isinstance(item, dict) else None
                    if isinstance(content, str) and content.strip():
                        snippets.append(content.strip())
                return snippets
            except Exception as exc:  # noqa: BLE001
                logger.warning("Web search failed for '{}': {}", query, exc)
                return []

        return await asyncio.to_thread(_run)
