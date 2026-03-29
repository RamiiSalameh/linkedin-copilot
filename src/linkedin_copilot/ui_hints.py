from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .db import (
    get_ui_hints_global,
    get_ui_hints_user,
    increment_ui_hints_global_success,
    increment_ui_hints_user_success,
    upsert_ui_hints_global,
    upsert_ui_hints_user,
)


EASY_APPLY_BUTTON_KEY = "linkedin_easy_apply_button"


@dataclass(frozen=True)
class HintSelector:
    selector: str
    success_count: int = 0
    last_seen_at: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _normalize_profile_name(profile_name: Optional[str]) -> str:
    name = (profile_name or "").strip()
    return name if name else "default"


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for s in items:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _parse_hint_selectors(hints: List[Dict[str, Any]]) -> List[HintSelector]:
    out: List[HintSelector] = []
    for h in hints or []:
        sel = (h.get("selector") or "").strip()
        if not sel:
            continue
        out.append(
            HintSelector(
                selector=sel,
                success_count=int(h.get("success_count") or 0),
                last_seen_at=h.get("last_seen_at"),
                meta=h.get("meta") if isinstance(h.get("meta"), dict) else None,
            )
        )
    return out


def get_ranked_selectors(key: str, profile_name: Optional[str], fallback: List[str]) -> List[str]:
    """Return a ranked selector list: user hints -> global hints -> fallback."""
    pn = _normalize_profile_name(profile_name)
    user_row = get_ui_hints_user(pn, key)
    global_row = get_ui_hints_global(key)

    user_hints = _parse_hint_selectors((user_row or {}).get("hints", []))
    global_hints = _parse_hint_selectors((global_row or {}).get("hints", []))

    def sort_key(h: HintSelector) -> Tuple[int, str]:
        return (h.success_count, h.last_seen_at or "")

    user_sorted = sorted(user_hints, key=sort_key, reverse=True)
    global_sorted = sorted(global_hints, key=sort_key, reverse=True)

    ranked = [h.selector for h in user_sorted] + [h.selector for h in global_sorted] + list(fallback)
    return _dedupe_preserve_order(ranked)


def record_selector_success(
    key: str,
    selector: str,
    profile_name: Optional[str],
    *,
    scope_global: bool = True,
    scope_user: bool = True,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Upsert selector into hint lists and increment success counters."""
    selector = (selector or "").strip()
    if not selector:
        return

    pn = _normalize_profile_name(profile_name)
    ts = _now_iso()

    if scope_global:
        row = get_ui_hints_global(key) or {"hints": []}
        hints = row.get("hints") or []
        _upsert_selector_hint(hints, selector, ts, meta)
        upsert_ui_hints_global(key, hints)
        increment_ui_hints_global_success(key)

    if scope_user:
        row = get_ui_hints_user(pn, key) or {"hints": []}
        hints = row.get("hints") or []
        _upsert_selector_hint(hints, selector, ts, meta)
        upsert_ui_hints_user(pn, key, hints)
        increment_ui_hints_user_success(pn, key)


def _upsert_selector_hint(hints: List[Dict[str, Any]], selector: str, ts: str, meta: Optional[Dict[str, Any]]) -> None:
    for h in hints:
        if h.get("selector") == selector:
            h["success_count"] = int(h.get("success_count") or 0) + 1
            h["last_seen_at"] = ts
            if meta:
                existing = h.get("meta") if isinstance(h.get("meta"), dict) else {}
                merged = {**existing, **meta}
                h["meta"] = merged
            return

    hints.append(
        {
            "selector": selector,
            "success_count": 1,
            "last_seen_at": ts,
            "meta": meta or {},
        }
    )


def fingerprint_from_text(text: str) -> str:
    """Stable fingerprint for lightweight correlation; avoid storing full HTML."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

