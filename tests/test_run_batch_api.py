from __future__ import annotations

from fastapi.testclient import TestClient


def test_run_batch_api_requires_queries(monkeypatch):
    from linkedin_copilot.web import app

    client = TestClient(app)
    resp = client.post("/api/search/run-batch", json={"queries": [], "locations": ["Israel"]})
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_run_batch_api_starts_background_task(monkeypatch):
    import linkedin_copilot.web as web

    client = TestClient(web.app)
    web._batch_search_status["running"] = False
    web._progress_status["running"] = False

    captured = {}

    async def _fake_background_batch_search(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(web, "_background_batch_search", _fake_background_batch_search)

    class _FakeTask:
        def __init__(self, coro):
            self.coro = coro

    def _fake_create_task(coro):
        # Close coroutine to avoid un-awaited warnings in unit tests.
        coro.close()
        return _FakeTask(coro)

    monkeypatch.setattr(web.asyncio, "create_task", _fake_create_task)

    payload = {
        "queries": ["Staff Engineer", "Python AWS Engineer"],
        "locations": ["Israel", "Remote"],
        "filters": {"easy_apply": True, "date_posted": "week"},
        "anonymous_search": False,
    }
    resp = client.post("/api/search/run-batch", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert data["total_searches"] == 4
