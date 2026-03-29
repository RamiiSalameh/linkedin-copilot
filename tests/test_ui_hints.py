from __future__ import annotations

from unittest.mock import patch

from linkedin_copilot.ui_hints import get_ranked_selectors, record_selector_success


def test_get_ranked_selectors_prioritizes_user_then_global_then_fallback():
    fallback = ["a", "b", "c"]
    with patch("linkedin_copilot.ui_hints.get_ui_hints_user") as mock_user, patch(
        "linkedin_copilot.ui_hints.get_ui_hints_global"
    ) as mock_global:
        mock_user.return_value = {
            "hints": [
                {"selector": "u2", "success_count": 1, "last_seen_at": "2026-01-02T00:00:00"},
                {"selector": "u1", "success_count": 3, "last_seen_at": "2026-01-01T00:00:00"},
            ]
        }
        mock_global.return_value = {
            "hints": [
                {"selector": "g1", "success_count": 2, "last_seen_at": "2026-01-01T00:00:00"},
                {"selector": "b", "success_count": 5, "last_seen_at": "2026-01-03T00:00:00"},
            ]
        }

        ranked = get_ranked_selectors("k", "User", fallback)

    # user sorted: u1, u2; global sorted: b, g1; then fallback c (a/b already present)
    assert ranked[:4] == ["u1", "u2", "b", "g1"]
    assert ranked[-1] == "c"


def test_record_selector_success_upserts_and_increments():
    with patch("linkedin_copilot.ui_hints.get_ui_hints_global", return_value={"hints": []}), patch(
        "linkedin_copilot.ui_hints.get_ui_hints_user", return_value={"hints": []}
    ), patch("linkedin_copilot.ui_hints.upsert_ui_hints_global") as upg, patch(
        "linkedin_copilot.ui_hints.upsert_ui_hints_user"
    ) as upu, patch("linkedin_copilot.ui_hints.increment_ui_hints_global_success") as ig, patch(
        "linkedin_copilot.ui_hints.increment_ui_hints_user_success"
    ) as iu:
        record_selector_success("k", "sel", "Me", meta={"x": 1})

        upg.assert_called()
        upu.assert_called()
        ig.assert_called_with("k")
        iu.assert_called()

