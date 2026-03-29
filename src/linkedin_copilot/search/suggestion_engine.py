from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..explore.strategies import generate_all_strategies
from ..logging_setup import logger


@dataclass
class _SuggestionCache:
    suggestions: List[Dict[str, Any]] = field(default_factory=list)
    expires_at: Optional[datetime] = None


class SuggestionEngine:
    """Generates multi-source search suggestions with cache and fallback."""

    def __init__(
        self,
        *,
        llm_client: Any,
        web_search_client: Any,
        cache_ttl_minutes: int = 30,
        suggestion_count: int = 14,
    ) -> None:
        self._llm = llm_client
        self._web_search = web_search_client
        self._ttl_minutes = cache_ttl_minutes
        self._suggestion_count = suggestion_count
        self._cache = _SuggestionCache()
        self._recent_queries: List[str] = []

    async def generate_suggestions(
        self,
        resume_text: str,
        applied_job_titles: List[str],
        search_history: List[Dict[str, Any]],
        *,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        now = datetime.utcnow()
        if (
            not force_refresh
            and self._cache.expires_at is not None
            and self._cache.expires_at > now
            and self._cache.suggestions
        ):
            return self._cache.suggestions

        seed = random.randint(1, 1_000_000)
        successful_terms = self._build_successful_terms(search_history)
        history_context = self._build_history_context(search_history, applied_job_titles, seed)
        web_context = await self._build_web_context(resume_text, applied_job_titles, successful_terms)

        try:
            if hasattr(self._llm, "generate_suggestion_engine_queries"):
                raw = self._llm.generate_suggestion_engine_queries(
                    resume_text=resume_text,
                    applied_titles=", ".join(applied_job_titles[:20]),
                    search_history_context=history_context,
                    successful_terms=successful_terms,
                    web_context=web_context,
                    random_seed=seed,
                    banned_queries=", ".join(self._recent_queries),
                    suggestion_count=self._suggestion_count,
                )
            else:
                raw = self._llm.generate_exploration_queries(
                    resume_text=resume_text,
                    search_history_context=history_context,
                    successful_terms=successful_terms,
                    job_context=web_context,
                )
            suggestions = self._normalize_suggestions(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Suggestion generation failed, using static fallback: {}", exc)
            suggestions = self._fallback_suggestions()

        self._cache = _SuggestionCache(
            suggestions=suggestions,
            expires_at=now + timedelta(minutes=self._ttl_minutes),
        )
        self._recent_queries = [s["query"].lower() for s in suggestions][:20]
        return suggestions

    def _build_history_context(
        self,
        search_history: List[Dict[str, Any]],
        applied_job_titles: List[str],
        seed: int,
    ) -> str:
        parts = [f"random_seed={seed}"]
        if self._recent_queries:
            parts.append("banned_queries=" + ", ".join(self._recent_queries))
        if applied_job_titles:
            parts.append("applied_titles=" + ", ".join(applied_job_titles[:10]))
        if search_history:
            top = search_history[:5]
            rows = [
                f"{s.get('keywords', '')} in {s.get('location', '')} ({s.get('jobs_found', 0)} jobs)"
                for s in top
            ]
            parts.append("history=" + " | ".join(rows))
        return "\n".join(parts)

    def _build_successful_terms(self, search_history: List[Dict[str, Any]]) -> str:
        terms: List[str] = []
        for item in search_history[:10]:
            keywords = str(item.get("keywords", "")).strip()
            if keywords:
                terms.append(keywords)
        return ", ".join(terms)

    async def _build_web_context(
        self,
        resume_text: str,
        applied_job_titles: List[str],
        successful_terms: str,
    ) -> str:
        query_a = (successful_terms.split(",")[0].strip() if successful_terms else "") or "software engineer jobs trends"
        query_b = applied_job_titles[0] if applied_job_titles else "backend engineer demand"
        snippets_a = await self._web_search.search(f"{query_a} 2026", max_results=3)
        snippets_b = await self._web_search.search(f"{query_b} hiring market", max_results=3)
        combined = (snippets_a + snippets_b)[:6]
        if not combined:
            return "No web context available."
        return "\n".join(combined)

    def _normalize_suggestions(self, raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            query = str(item.get("query", "")).strip()
            if not query:
                continue
            key = query.lower()
            if key in seen or key in self._recent_queries:
                continue
            seen.add(key)
            normalized.append(
                {
                    "id": f"ai-{len(normalized)+1}",
                    "query": query,
                    "category": str(item.get("category", "exploratory")),
                    "priority": int(item.get("priority", 2)),
                    "rationale": str(item.get("rationale", "")),
                    "source": "ai",
                }
            )
            if len(normalized) >= self._suggestion_count:
                break
        return normalized

    def _fallback_suggestions(self) -> List[Dict[str, Any]]:
        queries = generate_all_strategies(max_per_strategy=5)
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for q in queries:
            key = q.query.lower().strip()
            if not key or key in seen or key in self._recent_queries:
                continue
            seen.add(key)
            out.append(
                {
                    "id": f"fallback-{len(out)+1}",
                    "query": q.query,
                    "category": q.strategy.value,
                    "priority": q.priority,
                    "rationale": "Generated from profile/search strategy fallback",
                    "source": "fallback",
                }
            )
            if len(out) >= self._suggestion_count:
                break
        # If the recent-query ban list is too restrictive, top up from remaining queries.
        if len(out) < self._suggestion_count:
            for q in queries:
                key = q.query.lower().strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "id": f"fallback-{len(out)+1}",
                        "query": q.query,
                        "category": q.strategy.value,
                        "priority": q.priority,
                        "rationale": "Generated from profile/search strategy fallback",
                        "source": "fallback",
                    }
                )
                if len(out) >= self._suggestion_count:
                    break
        return out
