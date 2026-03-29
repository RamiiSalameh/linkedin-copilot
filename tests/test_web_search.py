from __future__ import annotations

import pytest

from linkedin_copilot.search.web_search import TavilyWebSearchClient


class _FakeTavilyClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> dict:
        return {
            "results": [
                {"content": f"{query} snippet 1"},
                {"content": f"{query} snippet 2"},
            ]
        }


@pytest.mark.asyncio
async def test_search_returns_snippets(monkeypatch):
    monkeypatch.setattr(
        "linkedin_copilot.search.web_search.TavilyClient",
        _FakeTavilyClient,
    )
    client = TavilyWebSearchClient(api_key="tvly-test")
    snippets = await client.search("python jobs 2026", max_results=2)
    assert snippets == ["python jobs 2026 snippet 1", "python jobs 2026 snippet 2"]


@pytest.mark.asyncio
async def test_search_returns_empty_when_key_missing():
    client = TavilyWebSearchClient(api_key=None)
    snippets = await client.search("anything")
    assert snippets == []


@pytest.mark.asyncio
async def test_search_returns_empty_on_provider_exception(monkeypatch):
    class _BoomClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def search(self, query: str, max_results: int = 5) -> dict:
            raise RuntimeError("network down")

    monkeypatch.setattr(
        "linkedin_copilot.search.web_search.TavilyClient",
        _BoomClient,
    )
    client = TavilyWebSearchClient(api_key="tvly-test")
    snippets = await client.search("python jobs 2026", max_results=2)
    assert snippets == []
