"""
Search strategy generators for job exploration.

Provides multiple query generation strategies:
- Profile-based variations
- Skill combination permutations
- Domain expansion
- Technology adjacency
- Location variations
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from ..logging_setup import logger
from ..scoring.matcher import load_profile


class QueryStrategy(str, Enum):
    """Types of query generation strategies."""
    PROFILE = "profile"
    SKILL_COMBO = "skill_combo"
    DOMAIN = "domain"
    TECHNOLOGY = "technology"
    ALTERNATIVE = "alternative"
    LEARNING = "learning"


@dataclass
class GeneratedQuery:
    """A generated search query with metadata."""
    query: str
    strategy: QueryStrategy
    priority: int = 2  # 1=high, 2=medium, 3=low
    source_terms: List[str] = field(default_factory=list)
    
    def __hash__(self):
        return hash(self.query.lower())
    
    def __eq__(self, other):
        if isinstance(other, GeneratedQuery):
            return self.query.lower() == other.query.lower()
        return False


# Role word variations for title expansion
ROLE_WORDS = [
    "Engineer", "Developer", "Architect", "Lead", "Manager",
    "Specialist", "Analyst", "Consultant", "Expert"
]

SENIORITY_PREFIXES = [
    "Senior", "Staff", "Principal", "Lead", "Junior", ""
]

# Domain adjacency mappings
DOMAIN_EXPANSIONS: Dict[str, List[str]] = {
    "fintech": ["finance technology", "banking software", "trading systems", "payments", "financial services"],
    "healthcare": ["medtech", "health IT", "biotech software", "medical devices", "healthtech"],
    "adtech": ["advertising technology", "marketing tech", "martech", "digital advertising"],
    "ecommerce": ["retail technology", "marketplace", "online retail", "digital commerce"],
    "edtech": ["education technology", "learning platforms", "e-learning"],
    "saas": ["cloud software", "B2B software", "enterprise software"],
    "cybersecurity": ["security", "infosec", "information security", "appsec"],
    "gaming": ["game development", "interactive entertainment", "game tech"],
    "ai": ["artificial intelligence", "machine learning", "ML", "deep learning"],
    "data": ["data engineering", "data science", "analytics", "big data"],
}

# Technology adjacency mappings
TECHNOLOGY_EXPANSIONS: Dict[str, List[str]] = {
    "python": ["Django", "FastAPI", "Flask", "Python Developer", "Python Backend"],
    "java": ["Spring", "Spring Boot", "Java Developer", "JVM"],
    "javascript": ["Node.js", "React", "TypeScript", "Frontend", "Full Stack"],
    "typescript": ["Node.js", "React", "Angular", "Frontend Developer"],
    "go": ["Golang", "Go Developer", "Backend Go"],
    "rust": ["Rust Developer", "Systems Programming"],
    "kubernetes": ["K8s", "Container Orchestration", "DevOps", "Platform Engineer"],
    "aws": ["Cloud Engineer", "AWS Developer", "Cloud Infrastructure"],
    "gcp": ["Google Cloud", "Cloud Engineer", "GCP Developer"],
    "azure": ["Microsoft Azure", "Cloud Engineer", "Azure Developer"],
    "docker": ["Containers", "DevOps", "Container Engineer"],
    "kafka": ["Event Streaming", "Data Pipeline", "Kafka Developer"],
    "spark": ["Data Engineering", "Big Data", "Spark Developer"],
    "terraform": ["Infrastructure as Code", "IaC", "Platform Engineer"],
    "react": ["React Developer", "Frontend Engineer", "React Native"],
    "node": ["Node.js Developer", "Backend JavaScript", "Full Stack"],
    "sql": ["Database", "Data Engineer", "SQL Developer"],
    "postgresql": ["Postgres", "Database Engineer", "Backend"],
    "mongodb": ["NoSQL", "Database", "MongoDB Developer"],
    "redis": ["Caching", "In-Memory Database", "Backend"],
    "elasticsearch": ["Search Engineer", "ELK Stack", "Data Engineer"],
    "graphql": ["API Developer", "GraphQL Developer", "Backend"],
}

# Title variations for common roles
TITLE_VARIATIONS: Dict[str, List[str]] = {
    "backend engineer": ["Backend Developer", "Server Engineer", "API Developer", "Platform Engineer"],
    "frontend engineer": ["Frontend Developer", "UI Developer", "Web Developer", "React Developer"],
    "full stack engineer": ["Full Stack Developer", "Software Engineer", "Web Developer"],
    "devops engineer": ["Site Reliability Engineer", "SRE", "Platform Engineer", "Infrastructure Engineer"],
    "data engineer": ["Data Platform Engineer", "ETL Developer", "Data Infrastructure"],
    "ml engineer": ["Machine Learning Engineer", "AI Engineer", "Deep Learning Engineer"],
    "software engineer": ["Software Developer", "Application Developer", "Programmer"],
    "cloud engineer": ["Cloud Architect", "Cloud Developer", "Infrastructure Engineer"],
    "security engineer": ["Security Analyst", "AppSec Engineer", "Cybersecurity Engineer"],
    "qa engineer": ["Test Engineer", "SDET", "Quality Engineer", "Automation Engineer"],
}


def generate_profile_queries(
    keywords: Optional[List[str]] = None,
    titles: Optional[List[str]] = None,
    skills: Optional[List[str]] = None,
) -> List[GeneratedQuery]:
    """
    Generate queries based on user profile data.
    
    Expands profile keywords, titles, and skills into varied search queries.
    """
    queries: Set[GeneratedQuery] = set()
    
    # Load profile if not provided
    if keywords is None or titles is None or skills is None:
        try:
            profile = load_profile()
            keywords = keywords or profile.keywords_for_search or []
            titles = titles or profile.target_titles or []
            skills = skills or profile.top_skills or []
        except Exception as e:
            logger.warning("Could not load profile for query generation: {}", e)
            return []
    
    # Generate from keywords (high priority - user specified)
    for kw in keywords[:10]:
        queries.add(GeneratedQuery(
            query=kw,
            strategy=QueryStrategy.PROFILE,
            priority=1,
            source_terms=[kw],
        ))
    
    # Generate from target titles with variations
    for title in titles[:5]:
        queries.add(GeneratedQuery(
            query=title,
            strategy=QueryStrategy.PROFILE,
            priority=1,
            source_terms=[title],
        ))
        
        # Add variations for known titles
        title_lower = title.lower()
        for key, variations in TITLE_VARIATIONS.items():
            if key in title_lower:
                for var in variations[:3]:
                    queries.add(GeneratedQuery(
                        query=var,
                        strategy=QueryStrategy.PROFILE,
                        priority=2,
                        source_terms=[title, var],
                    ))
    
    # Generate from skills with role words
    for skill in skills[:8]:
        queries.add(GeneratedQuery(
            query=f"{skill} Engineer",
            strategy=QueryStrategy.PROFILE,
            priority=2,
            source_terms=[skill],
        ))
        queries.add(GeneratedQuery(
            query=f"{skill} Developer",
            strategy=QueryStrategy.PROFILE,
            priority=2,
            source_terms=[skill],
        ))
    
    logger.info("Generated {} profile-based queries", len(queries))
    return list(queries)


def generate_skill_combination_queries(
    skills: Optional[List[str]] = None,
    max_combinations: int = 20,
) -> List[GeneratedQuery]:
    """
    Generate queries by combining skills with role words.
    
    Creates permutations like "Python AWS Engineer", "Kubernetes DevOps", etc.
    """
    queries: Set[GeneratedQuery] = set()
    
    # Load profile if skills not provided
    if skills is None:
        try:
            profile = load_profile()
            skills = profile.top_skills or []
        except Exception:
            return []
    
    if not skills:
        return []
    
    # Take top skills
    top_skills = skills[:6]
    
    # Single skill + role combinations
    for skill in top_skills:
        for role in ["Engineer", "Developer"]:
            queries.add(GeneratedQuery(
                query=f"{skill} {role}",
                strategy=QueryStrategy.SKILL_COMBO,
                priority=2,
                source_terms=[skill, role],
            ))
    
    # Two skill combinations
    for skill1, skill2 in itertools.combinations(top_skills[:4], 2):
        queries.add(GeneratedQuery(
            query=f"{skill1} {skill2}",
            strategy=QueryStrategy.SKILL_COMBO,
            priority=2,
            source_terms=[skill1, skill2],
        ))
        queries.add(GeneratedQuery(
            query=f"{skill1} {skill2} Engineer",
            strategy=QueryStrategy.SKILL_COMBO,
            priority=3,
            source_terms=[skill1, skill2],
        ))
    
    # Limit to max_combinations
    result = list(queries)[:max_combinations]
    logger.info("Generated {} skill combination queries", len(result))
    return result


def generate_domain_expansion_queries(
    domains: Optional[List[str]] = None,
    industries: Optional[List[str]] = None,
) -> List[GeneratedQuery]:
    """
    Generate queries by expanding into adjacent domains.
    
    Takes current domain focus and expands to related industries.
    """
    queries: Set[GeneratedQuery] = set()
    
    # Load profile if not provided
    if domains is None:
        try:
            profile = load_profile()
            domains = profile.industries or []
        except Exception:
            domains = []
    
    # Expand known domains
    for domain in domains[:5]:
        domain_lower = domain.lower()
        
        # Check if we have expansions for this domain
        for key, expansions in DOMAIN_EXPANSIONS.items():
            if key in domain_lower or domain_lower in key:
                for exp in expansions[:4]:
                    queries.add(GeneratedQuery(
                        query=exp,
                        strategy=QueryStrategy.DOMAIN,
                        priority=2,
                        source_terms=[domain, exp],
                    ))
                    # Combine with "engineer" or "developer"
                    queries.add(GeneratedQuery(
                        query=f"{exp} engineer",
                        strategy=QueryStrategy.DOMAIN,
                        priority=3,
                        source_terms=[domain, exp],
                    ))
    
    logger.info("Generated {} domain expansion queries", len(queries))
    return list(queries)


def generate_technology_expansion_queries(
    technologies: Optional[List[str]] = None,
) -> List[GeneratedQuery]:
    """
    Generate queries by expanding technologies to related terms.
    
    Maps technologies to adjacent tools and roles.
    """
    queries: Set[GeneratedQuery] = set()
    
    # Load profile if not provided
    if technologies is None:
        try:
            profile = load_profile()
            technologies = profile.top_skills or []
        except Exception:
            technologies = []
    
    for tech in technologies[:10]:
        tech_lower = tech.lower()
        
        # Check if we have expansions for this technology
        for key, expansions in TECHNOLOGY_EXPANSIONS.items():
            if key in tech_lower or tech_lower == key:
                for exp in expansions[:3]:
                    queries.add(GeneratedQuery(
                        query=exp,
                        strategy=QueryStrategy.TECHNOLOGY,
                        priority=2,
                        source_terms=[tech, exp],
                    ))
    
    logger.info("Generated {} technology expansion queries", len(queries))
    return list(queries)


def generate_alternative_title_queries(
    current_title: Optional[str] = None,
) -> List[GeneratedQuery]:
    """
    Generate alternative job title queries for career pivots.
    """
    queries: Set[GeneratedQuery] = set()
    
    # Load profile if not provided
    if current_title is None:
        try:
            profile = load_profile()
            current_title = profile.current_title or ""
        except Exception:
            return []
    
    if not current_title:
        return []
    
    title_lower = current_title.lower()
    
    # Find matching title variations
    for key, variations in TITLE_VARIATIONS.items():
        if key in title_lower:
            for var in variations:
                queries.add(GeneratedQuery(
                    query=var,
                    strategy=QueryStrategy.ALTERNATIVE,
                    priority=2,
                    source_terms=[current_title, var],
                ))
            
            # Add seniority variations
            for prefix in SENIORITY_PREFIXES:
                if prefix:
                    for var in variations[:2]:
                        queries.add(GeneratedQuery(
                            query=f"{prefix} {var}",
                            strategy=QueryStrategy.ALTERNATIVE,
                            priority=3,
                            source_terms=[current_title, prefix, var],
                        ))
    
    logger.info("Generated {} alternative title queries", len(queries))
    return list(queries)


def generate_all_strategies(
    strategies: Optional[List[QueryStrategy]] = None,
    max_per_strategy: int = 15,
) -> List[GeneratedQuery]:
    """
    Generate queries using all specified strategies.
    
    Args:
        strategies: List of strategies to use (defaults to all)
        max_per_strategy: Maximum queries per strategy
    
    Returns:
        Combined list of generated queries, deduplicated
    """
    if strategies is None:
        strategies = [
            QueryStrategy.PROFILE,
            QueryStrategy.SKILL_COMBO,
            QueryStrategy.DOMAIN,
            QueryStrategy.TECHNOLOGY,
            QueryStrategy.ALTERNATIVE,
        ]
    
    all_queries: Set[GeneratedQuery] = set()
    
    strategy_generators = {
        QueryStrategy.PROFILE: generate_profile_queries,
        QueryStrategy.SKILL_COMBO: generate_skill_combination_queries,
        QueryStrategy.DOMAIN: generate_domain_expansion_queries,
        QueryStrategy.TECHNOLOGY: generate_technology_expansion_queries,
        QueryStrategy.ALTERNATIVE: generate_alternative_title_queries,
    }
    
    for strategy in strategies:
        if strategy in strategy_generators:
            try:
                queries = strategy_generators[strategy]()
                # Sort by priority and take top N
                queries = sorted(queries, key=lambda q: q.priority)[:max_per_strategy]
                all_queries.update(queries)
            except Exception as e:
                logger.error("Error generating {} queries: {}", strategy.value, e)
    
    # Sort final list by priority
    result = sorted(list(all_queries), key=lambda q: (q.priority, q.query))
    logger.info("Generated {} total queries across {} strategies", len(result), len(strategies))
    
    return result


def filter_explored_queries(
    queries: List[GeneratedQuery],
    explored_keywords: Set[str],
) -> List[GeneratedQuery]:
    """
    Filter out queries that have already been explored.
    
    Args:
        queries: List of generated queries
        explored_keywords: Set of already-searched keywords (lowercase)
    
    Returns:
        Filtered list of unexplored queries
    """
    return [
        q for q in queries 
        if q.query.lower() not in explored_keywords
    ]
