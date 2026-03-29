"""Tests for web navigation: home vs search, index tab param, /search redirect, side menu context."""

import pytest


class TestHomeVsSearch:
    """GET / with no tab shows dashboard (home); with tab=search|careers shows Search page."""

    def test_index_no_tab_returns_dashboard_as_home(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Pipeline dashboard" in resp.text
        assert "Pending scrape" in resp.text

    def test_index_tab_search_returns_search_page(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/?tab=search")
        assert resp.status_code == 200
        assert "Search" in resp.text

    def test_index_tab_careers_returns_search_page(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/?tab=careers")
        assert resp.status_code == 200
        assert "Careers" in resp.text

    def test_index_tab_invalid_returns_dashboard_as_home(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/?tab=invalid")
        assert resp.status_code == 200
        assert "Pipeline dashboard" in resp.text


class TestIndexSearchTab:
    """GET /?tab= param and search_tab in template (Search page only)."""

    def test_index_tab_smart_maps_to_search_tab(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/?tab=smart")
        assert resp.status_code == 200
        assert 'id="tab-search"' in resp.text


class TestSearchRedirect:
    """GET /search redirects to /?tab=search."""

    def test_search_redirects_to_tab_search(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/search", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/?tab=search"

    def test_search_follow_redirect_returns_search_page(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/search", follow_redirects=True)
        assert resp.status_code == 200
        assert "Search" in resp.text


class TestGettingStarted:
    """GET /getting-started shows the guide page."""

    def test_getting_started_returns_200(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/getting-started")
        assert resp.status_code == 200
        assert "Getting started" in resp.text
        assert "How it works" in resp.text

    def test_getting_started_contains_guide_content(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/getting-started")
        assert resp.status_code == 200
        assert "Set up your profile" in resp.text
        assert "Go to Home" in resp.text


class TestDashboardRedirect:
    """GET /dashboard redirects to /."""

    def test_dashboard_redirects_to_root(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"


class TestProfileSection:
    """GET /profile with ?section= param."""

    def test_profile_returns_200(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/profile")
        assert resp.status_code == 200
        assert "LinkedIn Connection" in resp.text

    def test_profile_section_cv_returns_200(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/profile?section=cv")
        assert resp.status_code == 200
        assert "section-cv" in resp.text

    def test_profile_section_linkedin_returns_200(self):
        from fastapi.testclient import TestClient
        from linkedin_copilot.web import app
        client = TestClient(app)
        resp = client.get("/profile?section=linkedin")
        assert resp.status_code == 200
        assert "section-linkedin" in resp.text
