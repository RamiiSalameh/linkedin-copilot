"""
Explore module for continuous AI-powered job discovery.

This module provides intelligent job exploration capabilities that:
- Generate diverse search queries using multiple strategies
- Learn from search effectiveness to optimize future queries
- Run continuous background exploration sessions
"""

from .engine import (
    ExplorationSession,
    ExplorationConfig,
    ExplorationStatus,
    ExplorationIntensity,
    start_exploration,
    stop_exploration,
    pause_exploration,
    resume_exploration,
    get_exploration_status,
)
from .strategies import (
    QueryStrategy,
    generate_profile_queries,
    generate_skill_combination_queries,
    generate_domain_expansion_queries,
    generate_all_strategies,
)
from .intelligence import (
    analyze_search_effectiveness,
    get_top_performing_queries,
    generate_optimized_queries,
)

__all__ = [
    # Engine
    "ExplorationSession",
    "ExplorationConfig",
    "ExplorationStatus",
    "ExplorationIntensity",
    "start_exploration",
    "stop_exploration",
    "pause_exploration",
    "resume_exploration",
    "get_exploration_status",
    # Strategies
    "QueryStrategy",
    "generate_profile_queries",
    "generate_skill_combination_queries",
    "generate_domain_expansion_queries",
    "generate_all_strategies",
    # Intelligence
    "analyze_search_effectiveness",
    "get_top_performing_queries",
    "generate_optimized_queries",
]
