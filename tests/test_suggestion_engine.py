from __future__ import annotations

import pytest

from linkedin_copilot.search.suggestion_engine import SuggestionEngine


class _FakeLLM:
    def generate_exploration_queries(
        self,
        resume_text: str,
        search_history_context: str = "",
        successful_terms: str = "",
        job_context: str = "",
    ):
        return [
            {"query": "Staff Engineer", "category": "role", "priority": 1, "rationale": "fit"},
            {"query": "Python AWS Engineer", "category": "skill", "priority": 2, "rationale": "fit"},
        ]


class _FakeWebSearch:
    async def search(self, query: str, max_results: int = 5):
        return [f"snippet: {query}"]


@pytest.mark.asyncio
async def test_generate_suggestions_uses_multiple_sources_and_llm():
    engine = SuggestionEngine(
        llm_client=_FakeLLM(),
        web_search_client=_FakeWebSearch(),
        cache_ttl_minutes=30,
        suggestion_count=10,
    )
    suggestions = await engine.generate_suggestions(
        resume_text="Senior backend engineer with Python and AWS",
        applied_job_titles=["Infrastructure Engineer", "Platform Engineer"],
        search_history=[],
        force_refresh=True,
    )
    assert len(suggestions) == 2
    assert suggestions[0]["query"] == "Staff Engineer"
    assert "source" in suggestions[0]


@pytest.mark.asyncio
async def test_generate_suggestions_uses_cache_until_refresh():
    class _CountingLLM(_FakeLLM):
        def __init__(self) -> None:
            self.calls = 0

        def generate_exploration_queries(self, *args, **kwargs):
            self.calls += 1
            return super().generate_exploration_queries(*args, **kwargs)

    llm = _CountingLLM()
    engine = SuggestionEngine(
        llm_client=llm,
        web_search_client=_FakeWebSearch(),
        cache_ttl_minutes=30,
        suggestion_count=10,
    )
    first = await engine.generate_suggestions("cv", [], [], force_refresh=False)
    second = await engine.generate_suggestions("cv", [], [], force_refresh=False)
    assert first == second
    assert llm.calls == 1

    await engine.generate_suggestions("cv", [], [], force_refresh=True)
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_generate_suggestions_falls_back_when_llm_fails():
    class _FailingLLM:
        def generate_exploration_queries(self, *args, **kwargs):
            raise RuntimeError("llm down")

    engine = SuggestionEngine(
        llm_client=_FailingLLM(),
        web_search_client=_FakeWebSearch(),
        cache_ttl_minutes=30,
        suggestion_count=6,
    )
    suggestions = await engine.generate_suggestions(
        resume_text="Backend engineer Python Kafka",
        applied_job_titles=["Backend Engineer"],
        search_history=[],
        force_refresh=True,
    )
    assert suggestions
    assert all("query" in s for s in suggestions)
