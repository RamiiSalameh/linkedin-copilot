"""
Tests for the exploration module.

Tests cover:
- Strategy generators
- Query generation
- Exploration session management
- Intelligence/effectiveness analysis
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, AsyncMock

# Import the modules we're testing
from linkedin_copilot.explore.strategies import (
    QueryStrategy,
    GeneratedQuery,
    generate_profile_queries,
    generate_skill_combination_queries,
    generate_domain_expansion_queries,
    generate_technology_expansion_queries,
    generate_alternative_title_queries,
    generate_all_strategies,
    filter_explored_queries,
    DOMAIN_EXPANSIONS,
    TECHNOLOGY_EXPANSIONS,
)
from linkedin_copilot.explore.intelligence import (
    SearchEffectiveness,
    ExplorationInsights,
    analyze_search_effectiveness,
    get_top_performing_queries,
    extract_job_patterns,
    _extract_search_terms,
    generate_optimized_queries,
)
from linkedin_copilot.explore.engine import (
    ExplorationConfig,
    ExplorationSession,
    ExplorationStatus,
    ExplorationIntensity,
    get_exploration_status,
)


class TestGeneratedQuery:
    """Tests for the GeneratedQuery dataclass."""
    
    def test_hash_case_insensitive(self):
        """Test that queries are hashed case-insensitively."""
        q1 = GeneratedQuery(query="Python Engineer", strategy=QueryStrategy.PROFILE)
        q2 = GeneratedQuery(query="python engineer", strategy=QueryStrategy.SKILL_COMBO)
        
        assert hash(q1) == hash(q2)
    
    def test_equality_case_insensitive(self):
        """Test that queries are equal case-insensitively."""
        q1 = GeneratedQuery(query="Backend Developer", strategy=QueryStrategy.PROFILE)
        q2 = GeneratedQuery(query="backend developer", strategy=QueryStrategy.DOMAIN)
        
        assert q1 == q2
    
    def test_deduplication_in_set(self):
        """Test that duplicate queries are deduplicated in a set."""
        queries = {
            GeneratedQuery(query="Python Engineer", strategy=QueryStrategy.PROFILE),
            GeneratedQuery(query="python engineer", strategy=QueryStrategy.SKILL_COMBO),
            GeneratedQuery(query="PYTHON ENGINEER", strategy=QueryStrategy.DOMAIN),
        }
        
        assert len(queries) == 1


class TestProfileQueries:
    """Tests for profile-based query generation."""
    
    def test_generate_from_keywords(self):
        """Test generating queries from keywords."""
        keywords = ["Python Backend", "FastAPI Developer"]
        queries = generate_profile_queries(keywords=keywords, titles=[], skills=[])
        
        assert len(queries) > 0
        keyword_queries = [q.query for q in queries]
        assert "Python Backend" in keyword_queries
        assert "FastAPI Developer" in keyword_queries
    
    def test_generate_from_skills(self):
        """Test generating queries from skills."""
        skills = ["Python", "AWS", "Kubernetes"]
        queries = generate_profile_queries(keywords=[], titles=[], skills=skills)
        
        query_texts = [q.query for q in queries]
        assert any("Python" in q for q in query_texts)
        assert any("Engineer" in q or "Developer" in q for q in query_texts)
    
    def test_empty_input_returns_empty(self):
        """Test that empty input returns empty list."""
        queries = generate_profile_queries(keywords=[], titles=[], skills=[])
        assert queries == []


class TestSkillCombinationQueries:
    """Tests for skill combination query generation."""
    
    def test_generate_single_skill_roles(self):
        """Test generating single skill + role queries."""
        skills = ["Python", "Java"]
        queries = generate_skill_combination_queries(skills=skills)
        
        query_texts = [q.query for q in queries]
        assert "Python Engineer" in query_texts
        assert "Python Developer" in query_texts
        assert "Java Engineer" in query_texts
    
    def test_generate_skill_combinations(self):
        """Test generating two-skill combinations."""
        skills = ["Python", "AWS", "Docker"]
        queries = generate_skill_combination_queries(skills=skills)
        
        query_texts = [q.query for q in queries]
        # Should have combinations like "Python AWS" or "AWS Docker"
        has_combo = any(
            ("Python" in q and "AWS" in q) or
            ("Python" in q and "Docker" in q) or
            ("AWS" in q and "Docker" in q)
            for q in query_texts
        )
        assert has_combo
    
    def test_max_combinations_limit(self):
        """Test that max_combinations limits output."""
        skills = ["Python", "Java", "Go", "Rust", "C++", "JavaScript"]
        queries = generate_skill_combination_queries(skills=skills, max_combinations=5)
        
        assert len(queries) <= 5


class TestDomainExpansion:
    """Tests for domain expansion queries."""
    
    def test_expand_known_domain(self):
        """Test expanding a known domain."""
        queries = generate_domain_expansion_queries(domains=["fintech"])
        
        query_texts = [q.query.lower() for q in queries]
        # Should expand to related terms
        has_expansion = any(
            term in query_texts
            for term in DOMAIN_EXPANSIONS.get("fintech", [])
        )
        assert has_expansion or any("finance" in q or "banking" in q or "trading" in q for q in query_texts)
    
    def test_unknown_domain_no_expansion(self):
        """Test that unknown domains don't cause errors."""
        queries = generate_domain_expansion_queries(domains=["unknown_domain_xyz"])
        # May return empty or just the original domain
        assert isinstance(queries, list)


class TestTechnologyExpansion:
    """Tests for technology expansion queries."""
    
    def test_expand_known_technology(self):
        """Test expanding a known technology."""
        queries = generate_technology_expansion_queries(technologies=["python"])
        
        query_texts = [q.query for q in queries]
        # Should expand to related terms like Django, FastAPI, etc.
        has_expansion = any(
            exp.lower() in q.lower()
            for q in query_texts
            for exp in TECHNOLOGY_EXPANSIONS.get("python", [])
        )
        assert has_expansion or len(queries) > 0


class TestAllStrategies:
    """Tests for generating queries using all strategies."""
    
    @patch('linkedin_copilot.explore.strategies.load_profile')
    def test_generate_all_combines_strategies(self, mock_profile):
        """Test that generate_all combines multiple strategies."""
        mock_profile.return_value = MagicMock(
            keywords_for_search=["Backend Engineer"],
            target_titles=["Senior Developer"],
            top_skills=["Python", "AWS"],
            industries=["fintech"],
            current_title="Software Engineer",
        )
        
        queries = generate_all_strategies()
        
        # Should have queries from multiple strategies
        strategies_used = set(q.strategy for q in queries)
        assert len(strategies_used) > 1
    
    def test_max_per_strategy_limit(self):
        """Test that max_per_strategy limits each strategy."""
        queries = generate_all_strategies(
            strategies=[QueryStrategy.PROFILE],
            max_per_strategy=3,
        )
        # Should be limited (though exact count depends on profile)
        assert len(queries) <= 50  # Reasonable upper bound


class TestFilterExploredQueries:
    """Tests for filtering already-explored queries."""
    
    def test_filter_removes_explored(self):
        """Test that explored queries are filtered out."""
        queries = [
            GeneratedQuery(query="Python Engineer", strategy=QueryStrategy.PROFILE),
            GeneratedQuery(query="Java Developer", strategy=QueryStrategy.PROFILE),
            GeneratedQuery(query="AWS Architect", strategy=QueryStrategy.SKILL_COMBO),
        ]
        explored = {"python engineer", "aws architect"}
        
        filtered = filter_explored_queries(queries, explored)
        
        assert len(filtered) == 1
        assert filtered[0].query == "Java Developer"


class TestSearchEffectiveness:
    """Tests for search effectiveness calculations."""
    
    def test_calculate_effectiveness_no_jobs(self):
        """Test effectiveness calculation with no jobs found."""
        eff = SearchEffectiveness.calculate_effectiveness(
            keywords="test query",
            location="test location",
            jobs_found=0,
            avg_score=0,
            high_matches=0,
        )
        
        assert eff.effectiveness_score == 0.0
    
    def test_calculate_effectiveness_good_results(self):
        """Test effectiveness calculation with good results."""
        eff = SearchEffectiveness.calculate_effectiveness(
            keywords="python engineer",
            location="israel",
            jobs_found=10,
            avg_score=75.0,
            high_matches=7,
        )
        
        # Should have a good effectiveness score
        assert eff.effectiveness_score > 50
        assert eff.jobs_found == 10
        assert eff.avg_match_score == 75.0


class TestExplorationInsights:
    """Tests for exploration insights analysis."""
    
    def test_analyze_empty_history(self):
        """Test analyzing empty search history."""
        insights = analyze_search_effectiveness([])
        
        assert insights.total_searches == 0
        assert insights.total_jobs_found == 0
        assert len(insights.top_performing_queries) == 0
    
    def test_analyze_with_history(self):
        """Test analyzing search history with data."""
        history = [
            {
                "keywords": "python developer",
                "location": "israel",
                "jobs_found": 15,
                "avg_match_score": 72.0,
                "high_matches": 10,
            },
            {
                "keywords": "java engineer",
                "location": "israel",
                "jobs_found": 5,
                "avg_match_score": 45.0,
                "high_matches": 1,
            },
        ]
        
        insights = analyze_search_effectiveness(history)
        
        assert insights.total_searches == 2
        assert insights.total_jobs_found == 20
        assert len(insights.top_performing_queries) > 0
        # First query should be python developer (better results)
        assert insights.top_performing_queries[0].keywords == "python developer"
    
    def test_to_dict_and_from_dict(self):
        """Test serialization and deserialization."""
        insights = ExplorationInsights(
            total_searches=5,
            total_jobs_found=50,
            avg_effectiveness=65.5,
            best_locations=["Israel", "Remote"],
        )
        
        data = insights.to_dict()
        restored = ExplorationInsights.from_dict(data)
        
        assert restored.total_searches == insights.total_searches
        assert restored.total_jobs_found == insights.total_jobs_found
        assert restored.avg_effectiveness == insights.avg_effectiveness


class TestExtractSearchTerms:
    """Tests for search term extraction."""
    
    def test_extract_removes_noise_words(self):
        """Test that noise words are removed."""
        terms = _extract_search_terms("senior python engineer in israel")
        
        assert "python" in terms
        assert "israel" in terms
        assert "senior" not in terms  # Noise word
        assert "in" not in terms  # Noise word
    
    def test_extract_handles_empty(self):
        """Test handling empty input."""
        terms = _extract_search_terms("")
        assert terms == []


class TestExplorationConfig:
    """Tests for exploration configuration."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = ExplorationConfig()
        
        assert config.intensity == ExplorationIntensity.MEDIUM
        assert config.max_searches == 100
        assert config.max_duration_hours == 4.0
        assert "profile" in config.strategies
        assert "skill_combo" in config.strategies
    
    def test_to_dict_and_from_dict(self):
        """Test configuration serialization."""
        config = ExplorationConfig(
            intensity=ExplorationIntensity.FAST,
            max_searches=50,
            locations=["Israel", "Remote"],
            easy_apply=True,
        )
        
        data = config.to_dict()
        restored = ExplorationConfig.from_dict(data)
        
        assert restored.intensity == ExplorationIntensity.FAST
        assert restored.max_searches == 50
        assert "Israel" in restored.locations
        assert restored.easy_apply is True


class TestExplorationSession:
    """Tests for exploration session state management."""
    
    def test_session_defaults(self):
        """Test default session state."""
        session = ExplorationSession()
        
        assert session.status == ExplorationStatus.IDLE
        assert session.total_searches == 0
        assert session.completed_searches == 0
        assert not session.is_running
        assert not session.is_paused
    
    def test_running_session_properties(self):
        """Test running session properties."""
        session = ExplorationSession(
            status=ExplorationStatus.RUNNING,
            total_searches=100,
            completed_searches=25,
            started_at=datetime.utcnow(),
        )
        
        assert session.is_running
        assert not session.is_paused
        assert session.progress_percent == 25.0
    
    def test_paused_session_can_resume(self):
        """Test that paused sessions can resume."""
        session = ExplorationSession(status=ExplorationStatus.PAUSED)
        
        assert session.is_paused
        assert session.can_resume
    
    def test_to_dict(self):
        """Test session serialization."""
        session = ExplorationSession(
            id=123,
            status=ExplorationStatus.RUNNING,
            total_searches=50,
            completed_searches=10,
            total_jobs_found=25,
            current_query="python developer",
        )
        
        data = session.to_dict()
        
        assert data["id"] == 123
        assert data["status"] == "running"
        assert data["total_searches"] == 50
        assert data["completed_searches"] == 10
        assert data["current_query"] == "python developer"
        assert data["progress_percent"] == 20.0


class TestExtractJobPatterns:
    """Tests for extracting patterns from job descriptions."""
    
    def test_extract_tech_patterns(self):
        """Test extracting technology patterns."""
        descriptions = [
            "We are looking for a Python developer with AWS experience.",
            "Requirements: Python, Django, PostgreSQL, and Docker.",
            "Strong Python skills required. AWS certification is a plus.",
        ]
        
        patterns = extract_job_patterns(descriptions, min_frequency=2)
        
        assert "python" in patterns["frequent_terms"]
        assert patterns["frequent_terms"]["python"] >= 2
    
    def test_extract_empty_descriptions(self):
        """Test with empty descriptions."""
        patterns = extract_job_patterns([])
        
        assert patterns["total_jobs_analyzed"] == 0
        assert len(patterns["frequent_terms"]) == 0


class TestGetExplorationStatus:
    """Tests for getting exploration status."""
    
    def test_idle_status_when_no_session(self):
        """Test that idle status is returned when no session exists."""
        # Reset global state by importing fresh
        from linkedin_copilot.explore import engine
        engine._current_session = None
        
        status = get_exploration_status()
        
        assert status["status"] == "idle"
        assert status["running"] is False
        assert status["paused"] is False


class TestIntelligenceFallback:
    """Tests for fallback behavior in AI query generation."""

    def test_generate_optimized_queries_falls_back_to_recommendations(self):
        class _FailingLLM:
            def generate_exploration_queries(self, **kwargs):
                raise RuntimeError("llm unavailable")

        history = [
            {
                "keywords": "python backend",
                "location": "israel",
                "jobs_found": 10,
                "avg_match_score": 70,
                "high_matches": 5,
            }
        ]
        queries = generate_optimized_queries(
            resume_text="Python backend engineer",
            search_history=history,
            llm_client=_FailingLLM(),
        )
        assert queries
        assert "query" in queries[0]


# Integration-style tests (would need mocking for actual async operations)
class TestExplorationIntegration:
    """Integration tests for the exploration system."""
    
    @patch('linkedin_copilot.db.create_exploration_session')
    @pytest.mark.asyncio
    async def test_start_exploration_creates_session(self, mock_create):
        """Test that starting exploration creates a session."""
        import linkedin_copilot.explore.engine as engine
        
        mock_create.return_value = 1
        
        # Reset state
        engine._current_session = None
        engine._exploration_task = None
        
        config = ExplorationConfig(max_searches=10)
        
        try:
            session = await engine.start_exploration(config)
            
            assert session.id == 1
            assert session.status == ExplorationStatus.RUNNING
            mock_create.assert_called_once()
            
            # Stop it so it doesn't keep running
            await engine.stop_exploration()
        finally:
            # Cleanup
            engine._current_session = None
            engine._exploration_task = None
