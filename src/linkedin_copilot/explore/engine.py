"""
Exploration engine - Core orchestrator for continuous job discovery.

Manages exploration sessions, coordinates search execution,
and tracks progress with persistence for resumability.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from ..logging_setup import logger


class ExplorationStatus(str, Enum):
    """Status of an exploration session."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ExplorationIntensity(str, Enum):
    """Exploration intensity levels - controls search frequency."""
    SLOW = "slow"      # ~10 searches per hour (6 min delay)
    MEDIUM = "medium"  # ~20 searches per hour (3 min delay)
    FAST = "fast"      # ~40 searches per hour (1.5 min delay)


# Delay between searches in seconds for each intensity
INTENSITY_DELAYS = {
    ExplorationIntensity.SLOW: 360,     # 6 minutes
    ExplorationIntensity.MEDIUM: 180,   # 3 minutes
    ExplorationIntensity.FAST: 90,      # 1.5 minutes
}


@dataclass
class ExplorationConfig:
    """Configuration for an exploration session."""
    intensity: ExplorationIntensity = ExplorationIntensity.MEDIUM
    max_searches: int = 100
    max_duration_hours: float = 4.0
    strategies: List[str] = field(default_factory=lambda: [
        "profile", "skill_combo", "domain", "technology", "learning"
    ])
    skip_recent_hours: int = 24
    auto_process: bool = True  # Auto scrape + match new jobs
    locations: List[str] = field(default_factory=list)
    
    # Search filters
    easy_apply: bool = False
    date_posted: Optional[str] = None  # "24h", "week", "month"
    experience_level: Optional[str] = None
    remote: Optional[str] = None
    job_type: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "intensity": self.intensity.value if isinstance(self.intensity, ExplorationIntensity) else self.intensity,
            "max_searches": self.max_searches,
            "max_duration_hours": self.max_duration_hours,
            "strategies": self.strategies,
            "skip_recent_hours": self.skip_recent_hours,
            "auto_process": self.auto_process,
            "locations": self.locations,
            "easy_apply": self.easy_apply,
            "date_posted": self.date_posted,
            "experience_level": self.experience_level,
            "remote": self.remote,
            "job_type": self.job_type,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExplorationConfig":
        """Create from dictionary."""
        intensity_str = data.get("intensity", "medium")
        try:
            intensity = ExplorationIntensity(intensity_str)
        except ValueError:
            intensity = ExplorationIntensity.MEDIUM
        
        return cls(
            intensity=intensity,
            max_searches=data.get("max_searches", 100),
            max_duration_hours=data.get("max_duration_hours", 4.0),
            strategies=data.get("strategies", ["profile", "skill_combo", "domain", "technology", "learning"]),
            skip_recent_hours=data.get("skip_recent_hours", 24),
            auto_process=data.get("auto_process", True),
            locations=data.get("locations", []),
            easy_apply=data.get("easy_apply", False),
            date_posted=data.get("date_posted"),
            experience_level=data.get("experience_level"),
            remote=data.get("remote"),
            job_type=data.get("job_type"),
        )


@dataclass
class ExplorationSession:
    """
    Represents an exploration session with state management.
    
    Tracks progress, handles pause/resume, and persists state to database.
    """
    id: Optional[int] = None
    status: ExplorationStatus = ExplorationStatus.IDLE
    config: ExplorationConfig = field(default_factory=ExplorationConfig)
    
    # Timing
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    
    # Progress tracking
    total_searches: int = 0
    completed_searches: int = 0
    total_jobs_found: int = 0
    unique_jobs: int = 0
    duplicates: int = 0
    
    # Current state
    current_query: Optional[str] = None
    current_location: Optional[str] = None
    
    # Explored keywords (for deduplication within session)
    explored_keywords: Set[str] = field(default_factory=set)
    
    # Search queue
    query_queue: List[Dict[str, Any]] = field(default_factory=list)
    
    # Insights gathered during exploration
    insights_data: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_running(self) -> bool:
        """Check if session is actively running."""
        return self.status == ExplorationStatus.RUNNING
    
    @property
    def is_paused(self) -> bool:
        """Check if session is paused."""
        return self.status == ExplorationStatus.PAUSED
    
    @property
    def can_resume(self) -> bool:
        """Check if session can be resumed."""
        return self.status == ExplorationStatus.PAUSED
    
    @property
    def progress_percent(self) -> float:
        """Calculate progress percentage."""
        if self.total_searches == 0:
            return 0.0
        return round((self.completed_searches / self.total_searches) * 100, 1)
    
    @property
    def elapsed_time_seconds(self) -> float:
        """Calculate elapsed time in seconds."""
        if not self.started_at:
            return 0.0
        
        end_time = self.ended_at or self.paused_at or datetime.utcnow()
        return (end_time - self.started_at).total_seconds()
    
    @property
    def time_remaining_seconds(self) -> Optional[float]:
        """Estimate remaining time based on progress."""
        if self.completed_searches == 0 or self.total_searches == 0:
            return None
        
        elapsed = self.elapsed_time_seconds
        rate = elapsed / self.completed_searches  # seconds per search
        remaining_searches = self.total_searches - self.completed_searches
        
        return rate * remaining_searches
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "status": self.status.value,
            "config": self.config.to_dict(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "paused_at": self.paused_at.isoformat() if self.paused_at else None,
            "total_searches": self.total_searches,
            "completed_searches": self.completed_searches,
            "total_jobs_found": self.total_jobs_found,
            "unique_jobs": self.unique_jobs,
            "duplicates": self.duplicates,
            "current_query": self.current_query,
            "current_location": self.current_location,
            "progress_percent": self.progress_percent,
            "elapsed_time_seconds": self.elapsed_time_seconds,
            "time_remaining_seconds": self.time_remaining_seconds,
            "explored_keywords": list(self.explored_keywords),
            "insights": self.insights_data,
        }
    
    def to_status_dict(self) -> Dict[str, Any]:
        """Get a lightweight status dictionary for API responses."""
        return {
            "id": self.id,
            "status": self.status.value,
            "running": self.is_running,
            "paused": self.is_paused,
            "total_searches": self.total_searches,
            "completed_searches": self.completed_searches,
            "total_jobs_found": self.total_jobs_found,
            "unique_jobs": self.unique_jobs,
            "duplicates": self.duplicates,
            "current_query": self.current_query,
            "current_location": self.current_location,
            "progress_percent": self.progress_percent,
            "elapsed_time_seconds": self.elapsed_time_seconds,
            "time_remaining_seconds": self.time_remaining_seconds,
            "queue_size": len(self.query_queue),
        }


# Global session instance (singleton for current exploration)
_current_session: Optional[ExplorationSession] = None
_exploration_task: Optional[asyncio.Task] = None


def get_current_session() -> Optional[ExplorationSession]:
    """Get the current exploration session if one exists."""
    return _current_session


def get_exploration_status() -> Dict[str, Any]:
    """Get the current exploration status."""
    if _current_session is None:
        return {
            "status": ExplorationStatus.IDLE.value,
            "running": False,
            "paused": False,
            "total_searches": 0,
            "completed_searches": 0,
            "total_jobs_found": 0,
            "unique_jobs": 0,
            "duplicates": 0,
            "current_query": None,
            "current_location": None,
            "progress_percent": 0.0,
            "queue_size": 0,
        }
    
    return _current_session.to_status_dict()


async def start_exploration(config: Optional[ExplorationConfig] = None) -> ExplorationSession:
    """
    Start a new exploration session.
    
    Args:
        config: Exploration configuration (uses defaults if not provided)
    
    Returns:
        The created ExplorationSession
    
    Raises:
        RuntimeError: If an exploration is already running
    """
    global _current_session, _exploration_task
    
    if _current_session and _current_session.is_running:
        raise RuntimeError("An exploration session is already running")
    
    # Create new session
    _current_session = ExplorationSession(
        status=ExplorationStatus.RUNNING,
        config=config or ExplorationConfig(),
        started_at=datetime.utcnow(),
    )
    
    # Save to database and get ID
    from ..db import create_exploration_session
    session_id = create_exploration_session(_current_session)
    _current_session.id = session_id
    
    logger.info(
        "Starting exploration session {} with config: intensity={}, max_searches={}, strategies={}",
        session_id,
        _current_session.config.intensity.value,
        _current_session.config.max_searches,
        _current_session.config.strategies,
    )
    
    # Start background exploration task
    _exploration_task = asyncio.create_task(_run_exploration_loop())
    
    return _current_session


async def stop_exploration(reason: str = "user_requested") -> Optional[ExplorationSession]:
    """
    Stop the current exploration session.
    
    Args:
        reason: Reason for stopping
    
    Returns:
        The stopped session, or None if no session was running
    """
    global _current_session, _exploration_task
    
    if _current_session is None:
        return None
    
    session = _current_session
    
    # Update status
    if session.status == ExplorationStatus.RUNNING:
        session.status = ExplorationStatus.CANCELLED
    
    session.ended_at = datetime.utcnow()
    
    # Cancel the background task
    if _exploration_task and not _exploration_task.done():
        _exploration_task.cancel()
        try:
            await _exploration_task
        except asyncio.CancelledError:
            pass
    
    # Save final state to database
    from ..db import update_exploration_session
    update_exploration_session(session)
    
    logger.info(
        "Stopped exploration session {}: {} searches, {} jobs found (reason: {})",
        session.id,
        session.completed_searches,
        session.total_jobs_found,
        reason,
    )
    
    _current_session = None
    _exploration_task = None
    
    return session


async def pause_exploration() -> Optional[ExplorationSession]:
    """
    Pause the current exploration session.
    
    The session can be resumed later with resume_exploration().
    
    Returns:
        The paused session, or None if no session was running
    """
    global _current_session, _exploration_task
    
    if _current_session is None or not _current_session.is_running:
        return None
    
    session = _current_session
    session.status = ExplorationStatus.PAUSED
    session.paused_at = datetime.utcnow()
    
    # Cancel the background task (but keep session state)
    if _exploration_task and not _exploration_task.done():
        _exploration_task.cancel()
        try:
            await _exploration_task
        except asyncio.CancelledError:
            pass
    
    _exploration_task = None
    
    # Save state to database
    from ..db import update_exploration_session
    update_exploration_session(session)
    
    logger.info(
        "Paused exploration session {} at {} searches",
        session.id,
        session.completed_searches,
    )
    
    return session


async def resume_exploration() -> Optional[ExplorationSession]:
    """
    Resume a paused exploration session.
    
    Returns:
        The resumed session, or None if no paused session exists
    """
    global _current_session, _exploration_task
    
    if _current_session is None or not _current_session.can_resume:
        return None
    
    session = _current_session
    session.status = ExplorationStatus.RUNNING
    session.paused_at = None
    
    logger.info(
        "Resuming exploration session {} from {} searches",
        session.id,
        session.completed_searches,
    )
    
    # Restart background task
    _exploration_task = asyncio.create_task(_run_exploration_loop())
    
    return session


async def _run_exploration_loop() -> None:
    """
    Main exploration loop - runs searches continuously until stopped.
    
    This is the background task that executes the exploration.
    """
    global _current_session
    
    if _current_session is None:
        return
    
    session = _current_session
    config = session.config
    
    try:
        # Import here to avoid circular imports
        from .strategies import (
            QueryStrategy,
            generate_all_strategies,
            filter_explored_queries,
        )
        from .intelligence import (
            analyze_search_effectiveness,
            generate_optimized_queries,
        )
        from ..linkedin.search import search_jobs
        from ..db import (
            save_search_history,
            search_was_run_recently,
            get_search_history,
            update_exploration_session,
        )
        from ..config import get_settings
        from pathlib import Path
        
        def _load_resume_text() -> str:
            """Load resume text from configured path."""
            settings = get_settings()
            resume_path = Path(settings.env.default_resume_path)
            if resume_path.exists():
                return resume_path.read_text(encoding="utf-8")
            return ""
        
        # Get locations from config or profile
        locations = config.locations
        if not locations:
            from ..scoring.matcher import load_profile
            try:
                profile = load_profile()
                locations = [
                    loc for loc in (profile.preferred_locations or [])
                    if loc.lower() not in ("remote", "hybrid", "on-site", "onsite")
                ]
            except Exception:
                pass
        
        if not locations:
            locations = ["Israel"]  # Default fallback
        
        # Map strategy strings to enums
        strategy_map = {
            "profile": QueryStrategy.PROFILE,
            "skill_combo": QueryStrategy.SKILL_COMBO,
            "domain": QueryStrategy.DOMAIN,
            "technology": QueryStrategy.TECHNOLOGY,
            "alternative": QueryStrategy.ALTERNATIVE,
            "learning": QueryStrategy.LEARNING,
        }
        
        strategies = [
            strategy_map[s] for s in config.strategies 
            if s in strategy_map and s != "learning"  # Learning is handled separately
        ]
        
        # Generate initial query queue
        all_queries = generate_all_strategies(strategies=strategies)
        
        # Filter already explored
        all_queries = filter_explored_queries(all_queries, session.explored_keywords)
        
        # Build queue: (query, location) pairs
        session.query_queue = []
        for location in locations:
            for q in all_queries:
                session.query_queue.append({
                    "query": q.query,
                    "location": location,
                    "strategy": q.strategy.value,
                    "priority": q.priority,
                })
        
        session.total_searches = min(len(session.query_queue), config.max_searches)
        
        logger.info(
            "Exploration queue built: {} searches across {} locations",
            session.total_searches,
            len(locations),
        )
        
        # Get delay based on intensity
        search_delay = INTENSITY_DELAYS.get(config.intensity, 180)
        
        # Calculate max end time
        max_end_time = None
        if config.max_duration_hours > 0 and session.started_at:
            from datetime import timedelta
            max_end_time = session.started_at + timedelta(hours=config.max_duration_hours)
        
        # Main search loop
        while (
            session.status == ExplorationStatus.RUNNING
            and session.completed_searches < session.total_searches
            and session.query_queue
        ):
            # Check time limit
            if max_end_time and datetime.utcnow() >= max_end_time:
                logger.info("Exploration reached time limit of {} hours", config.max_duration_hours)
                break
            
            # Get next query from queue
            query_item = session.query_queue.pop(0)
            query = query_item["query"]
            location = query_item["location"]
            
            session.current_query = query
            session.current_location = location
            
            # Check if already searched recently
            if search_was_run_recently(query, location, config.skip_recent_hours):
                logger.debug("Skipping recent search: '{}' in '{}'", query, location)
                session.completed_searches += 1
                continue
            
            # Mark as explored
            session.explored_keywords.add(query.lower())
            
            logger.info(
                "Exploration search {}/{}: '{}' in '{}'",
                session.completed_searches + 1,
                session.total_searches,
                query,
                location,
            )
            
            try:
                # Execute search
                search_result = await search_jobs(
                    keywords=query,
                    location=location,
                    easy_apply_only=config.easy_apply,
                    limit=30,
                    date_posted=config.date_posted,
                    experience_level=config.experience_level,
                    remote=config.remote,
                    job_type=config.job_type,
                    anonymous=False,
                )
                
                jobs = search_result.jobs
                
                # Update session stats
                session.total_jobs_found += len(jobs)
                session.unique_jobs += len(jobs)
                session.duplicates += search_result.duplicates
                
                # Save to search history with strategy source
                filters = {
                    "easy_apply": config.easy_apply,
                    "date_posted": config.date_posted,
                    "experience_level": config.experience_level,
                    "remote": config.remote,
                    "job_type": config.job_type,
                    "strategy_source": query_item.get("strategy", "explore"),
                }
                save_search_history(query, location, len(jobs), filters)
                
                logger.info(
                    "Found {} jobs ({} duplicates) for '{}'",
                    len(jobs),
                    search_result.duplicates,
                    query,
                )
                
            except Exception as e:
                logger.error("Search error for '{}' in '{}': {}", query, location, e)
            
            session.completed_searches += 1
            
            # Save progress to database periodically
            if session.completed_searches % 5 == 0:
                update_exploration_session(session)
            
            # Check for learning-based expansion at 25% and 50% progress
            if (
                "learning" in config.strategies
                and session.completed_searches in [
                    session.total_searches // 4,
                    session.total_searches // 2,
                ]
            ):
                try:
                    # Generate new queries based on learnings
                    history = get_search_history(limit=50)
                    resume_text = _load_resume_text()
                    
                    new_queries = generate_optimized_queries(
                        resume_text=resume_text,
                        search_history=history,
                    )
                    
                    # Add to queue (deduplicated)
                    for q in new_queries[:10]:
                        query_text = q.get("query", "")
                        if query_text.lower() not in session.explored_keywords:
                            for loc in locations[:2]:  # Limit locations for new queries
                                session.query_queue.append({
                                    "query": query_text,
                                    "location": loc,
                                    "strategy": "learning",
                                    "priority": q.get("priority", 2),
                                })
                    
                    # Update total
                    session.total_searches = min(
                        len(session.query_queue) + session.completed_searches,
                        config.max_searches,
                    )
                    
                    logger.info(
                        "Added {} learning-based queries to exploration queue",
                        len(new_queries),
                    )
                    
                except Exception as e:
                    logger.error("Error generating learning queries: {}", e)
            
            # Delay between searches
            if session.query_queue and session.status == ExplorationStatus.RUNNING:
                await asyncio.sleep(search_delay)
        
        # Exploration complete
        if session.status == ExplorationStatus.RUNNING:
            session.status = ExplorationStatus.COMPLETED
        
        session.ended_at = datetime.utcnow()
        session.current_query = None
        session.current_location = None
        
        # Final analysis
        try:
            history = get_search_history(limit=100)
            insights = analyze_search_effectiveness(history)
            session.insights_data = insights.to_dict()
        except Exception as e:
            logger.error("Error analyzing exploration results: {}", e)
        
        # Save final state
        update_exploration_session(session)
        
        logger.info(
            "Exploration session {} completed: {} searches, {} jobs found, {} unique",
            session.id,
            session.completed_searches,
            session.total_jobs_found,
            session.unique_jobs,
        )
        
        # Auto-process new jobs if configured
        if config.auto_process and session.unique_jobs > 0:
            logger.info("Starting auto-processing of {} new jobs...", session.unique_jobs)
            # This will be handled by the existing web.py background tasks
        
    except asyncio.CancelledError:
        logger.info("Exploration loop cancelled")
        raise
    except Exception as e:
        logger.error("Exploration error: {}", e)
        if session:
            session.status = ExplorationStatus.ERROR
            session.ended_at = datetime.utcnow()
            from ..db import update_exploration_session
            update_exploration_session(session)
