"""
AI-powered intelligence layer for exploration optimization.

Analyzes search history effectiveness and generates optimized queries
based on learnings from successful matches.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..logging_setup import logger


@dataclass
class SearchEffectiveness:
    """Analysis of search query effectiveness."""
    keywords: str
    location: str
    jobs_found: int
    avg_match_score: float
    high_matches: int  # Jobs with score >= 70
    effectiveness_score: float  # Composite score
    
    @classmethod
    def calculate_effectiveness(
        cls,
        keywords: str,
        location: str,
        jobs_found: int,
        avg_score: float,
        high_matches: int,
    ) -> "SearchEffectiveness":
        """Calculate composite effectiveness score."""
        if jobs_found == 0:
            effectiveness = 0.0
        else:
            # Weight: 40% jobs found, 30% avg score, 30% high matches ratio
            jobs_component = min(jobs_found / 10, 1.0) * 0.4  # Normalize to 10 jobs max
            score_component = (avg_score / 100) * 0.3
            high_ratio = (high_matches / jobs_found) if jobs_found > 0 else 0
            high_component = high_ratio * 0.3
            
            effectiveness = (jobs_component + score_component + high_component) * 100
        
        return cls(
            keywords=keywords,
            location=location,
            jobs_found=jobs_found,
            avg_match_score=avg_score,
            high_matches=high_matches,
            effectiveness_score=round(effectiveness, 2),
        )


@dataclass
class ExplorationInsights:
    """Insights gathered from exploration analysis."""
    top_performing_queries: List[SearchEffectiveness] = field(default_factory=list)
    common_successful_terms: List[Tuple[str, int]] = field(default_factory=list)
    recommended_queries: List[str] = field(default_factory=list)
    total_searches: int = 0
    total_jobs_found: int = 0
    avg_effectiveness: float = 0.0
    best_locations: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "top_performing_queries": [
                {
                    "keywords": q.keywords,
                    "location": q.location,
                    "jobs_found": q.jobs_found,
                    "avg_match_score": q.avg_match_score,
                    "high_matches": q.high_matches,
                    "effectiveness_score": q.effectiveness_score,
                }
                for q in self.top_performing_queries
            ],
            "common_successful_terms": self.common_successful_terms,
            "recommended_queries": self.recommended_queries,
            "total_searches": self.total_searches,
            "total_jobs_found": self.total_jobs_found,
            "avg_effectiveness": self.avg_effectiveness,
            "best_locations": self.best_locations,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExplorationInsights":
        """Create from dictionary."""
        insights = cls()
        insights.top_performing_queries = [
            SearchEffectiveness(
                keywords=q["keywords"],
                location=q["location"],
                jobs_found=q["jobs_found"],
                avg_match_score=q["avg_match_score"],
                high_matches=q["high_matches"],
                effectiveness_score=q["effectiveness_score"],
            )
            for q in data.get("top_performing_queries", [])
        ]
        insights.common_successful_terms = [
            tuple(t) for t in data.get("common_successful_terms", [])
        ]
        insights.recommended_queries = data.get("recommended_queries", [])
        insights.total_searches = data.get("total_searches", 0)
        insights.total_jobs_found = data.get("total_jobs_found", 0)
        insights.avg_effectiveness = data.get("avg_effectiveness", 0.0)
        insights.best_locations = data.get("best_locations", [])
        return insights


def analyze_search_effectiveness(
    search_history: List[Dict[str, Any]],
    match_results: Optional[List[Dict[str, Any]]] = None,
) -> ExplorationInsights:
    """
    Analyze search history to determine effectiveness of different queries.
    
    Args:
        search_history: List of search history records from database
        match_results: Optional list of match results for deeper analysis
    
    Returns:
        ExplorationInsights with analysis results
    """
    if not search_history:
        return ExplorationInsights()
    
    # Build effectiveness scores for each search
    effectiveness_list: List[SearchEffectiveness] = []
    location_jobs: Dict[str, int] = Counter()
    all_terms: List[str] = []
    
    for search in search_history:
        keywords = search.get("keywords", "")
        location = search.get("location", "")
        jobs_found = search.get("jobs_found", 0)
        avg_score = search.get("avg_match_score", 50.0) or 50.0
        high_matches = search.get("high_matches", 0) or 0
        
        eff = SearchEffectiveness.calculate_effectiveness(
            keywords=keywords,
            location=location,
            jobs_found=jobs_found,
            avg_score=avg_score,
            high_matches=high_matches,
        )
        effectiveness_list.append(eff)
        
        # Track location performance
        if jobs_found > 0:
            location_jobs[location] += jobs_found
            
            # Extract terms from successful searches
            terms = _extract_search_terms(keywords)
            all_terms.extend(terms)
    
    # Sort by effectiveness
    effectiveness_list.sort(key=lambda x: x.effectiveness_score, reverse=True)
    
    # Calculate insights
    insights = ExplorationInsights()
    insights.top_performing_queries = effectiveness_list[:10]
    insights.total_searches = len(search_history)
    insights.total_jobs_found = sum(s.get("jobs_found", 0) for s in search_history)
    
    if effectiveness_list:
        insights.avg_effectiveness = round(
            sum(e.effectiveness_score for e in effectiveness_list) / len(effectiveness_list),
            2
        )
    
    # Get most common successful terms
    term_counts = Counter(all_terms)
    insights.common_successful_terms = term_counts.most_common(20)
    
    # Get best performing locations
    insights.best_locations = [
        loc for loc, _ in location_jobs.most_common(5)
    ]
    
    # Generate recommended queries based on successful patterns
    insights.recommended_queries = _generate_recommendations(insights)
    
    logger.info(
        "Analyzed {} searches: avg effectiveness {:.1f}, top terms: {}",
        insights.total_searches,
        insights.avg_effectiveness,
        insights.common_successful_terms[:5],
    )
    
    return insights


def _extract_search_terms(query: str) -> List[str]:
    """Extract individual terms from a search query."""
    # Remove common noise words
    noise_words = {
        "engineer", "developer", "senior", "junior", "lead", "staff",
        "in", "at", "the", "a", "an", "and", "or", "for", "with"
    }
    
    # Split and clean
    terms = re.findall(r'\b\w+\b', query.lower())
    return [t for t in terms if t not in noise_words and len(t) > 2]


def _generate_recommendations(insights: ExplorationInsights) -> List[str]:
    """Generate recommended queries based on successful patterns."""
    recommendations: List[str] = []
    
    if not insights.common_successful_terms:
        return recommendations
    
    # Combine top 2-3 terms into new queries
    top_terms = [term for term, _ in insights.common_successful_terms[:8]]
    
    # Single term + role
    for term in top_terms[:4]:
        recommendations.append(f"{term.title()} Engineer")
        recommendations.append(f"{term.title()} Developer")
    
    # Term combinations
    for i, term1 in enumerate(top_terms[:4]):
        for term2 in top_terms[i+1:6]:
            recommendations.append(f"{term1.title()} {term2.title()}")
    
    # Deduplicate and limit
    seen = set()
    unique_recs = []
    for rec in recommendations:
        rec_lower = rec.lower()
        if rec_lower not in seen:
            seen.add(rec_lower)
            unique_recs.append(rec)
    
    return unique_recs[:15]


def get_top_performing_queries(
    search_history: List[Dict[str, Any]],
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Get the top performing search queries based on effectiveness.
    
    Returns queries sorted by composite effectiveness score.
    """
    insights = analyze_search_effectiveness(search_history)
    
    return [
        {
            "keywords": q.keywords,
            "location": q.location,
            "jobs_found": q.jobs_found,
            "effectiveness_score": q.effectiveness_score,
        }
        for q in insights.top_performing_queries[:limit]
    ]


def generate_optimized_queries(
    resume_text: str,
    search_history: List[Dict[str, Any]],
    top_job_descriptions: Optional[List[str]] = None,
    llm_client: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Generate optimized exploration queries using AI.
    
    Uses LLM to analyze resume, search history, and successful job descriptions
    to generate new query ideas.
    
    Args:
        resume_text: User's resume text
        search_history: Past search history with effectiveness data
        top_job_descriptions: Descriptions from high-scoring jobs
        llm_client: LLM client instance
    
    Returns:
        List of query dictionaries with query, category, priority
    """
    if llm_client is None:
        from ..llm import get_llm
        llm_client = get_llm()
    
    # Build context from search history
    insights = analyze_search_effectiveness(search_history)
    
    # Prepare context strings
    top_queries_str = ""
    if insights.top_performing_queries:
        top_queries_str = "Top performing searches:\n"
        for q in insights.top_performing_queries[:5]:
            top_queries_str += f"- '{q.keywords}' in {q.location}: {q.jobs_found} jobs, score {q.effectiveness_score}\n"
    
    common_terms_str = ""
    if insights.common_successful_terms:
        terms = [t for t, _ in insights.common_successful_terms[:10]]
        common_terms_str = f"Common terms in successful searches: {', '.join(terms)}"
    
    top_jobs_str = ""
    if top_job_descriptions:
        top_jobs_str = "Sample requirements from high-match jobs:\n"
        for i, desc in enumerate(top_job_descriptions[:3], 1):
            # Extract first 300 chars of each
            snippet = desc[:300].replace('\n', ' ')
            top_jobs_str += f"{i}. {snippet}...\n"
    
    try:
        # Use LLM to generate queries
        queries = llm_client.generate_exploration_queries(
            resume_text=resume_text,
            search_history_context=top_queries_str,
            successful_terms=common_terms_str,
            job_context=top_jobs_str,
        )
        
        logger.info("LLM generated {} exploration queries", len(queries))
        return queries
        
    except Exception as e:
        logger.error("Error generating optimized queries: {}", e)
        # Fall back to recommendations from analysis
        return [
            {"query": q, "category": "recommended", "priority": 2}
            for q in insights.recommended_queries[:10]
        ]


def extract_job_patterns(
    job_descriptions: List[str],
    min_frequency: int = 2,
) -> Dict[str, Any]:
    """
    Extract common patterns from job descriptions.
    
    Useful for understanding what skills/terms appear frequently
    in jobs the user matches well with.
    """
    all_terms: List[str] = []
    
    # Common tech/skill patterns to look for
    tech_patterns = [
        r'\b(python|java|javascript|typescript|go|rust|c\+\+|ruby|scala)\b',
        r'\b(aws|gcp|azure|kubernetes|docker|terraform|ansible)\b',
        r'\b(react|angular|vue|node\.?js|django|flask|spring)\b',
        r'\b(postgresql|mysql|mongodb|redis|elasticsearch|kafka)\b',
        r'\b(machine learning|ml|ai|deep learning|nlp|computer vision)\b',
        r'\b(microservices|api|rest|graphql|grpc)\b',
        r'\b(agile|scrum|ci/cd|devops|sre)\b',
    ]
    
    for desc in job_descriptions:
        desc_lower = desc.lower()
        for pattern in tech_patterns:
            matches = re.findall(pattern, desc_lower)
            all_terms.extend(matches)
    
    term_counts = Counter(all_terms)
    
    # Filter by minimum frequency
    frequent_terms = {
        term: count 
        for term, count in term_counts.items() 
        if count >= min_frequency
    }
    
    return {
        "frequent_terms": dict(term_counts.most_common(30)),
        "high_frequency_terms": frequent_terms,
        "total_jobs_analyzed": len(job_descriptions),
    }
