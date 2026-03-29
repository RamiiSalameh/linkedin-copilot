from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class EnvSettings(BaseSettings):
    """Environment-driven settings."""

    # LLM Provider: "ollama" (local, default) or "openai" (cloud)
    llm_provider: str = "ollama"
    
    # Ollama settings (local)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    
    # OpenAI settings (cloud)
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"
    tavily_api_key: Optional[str] = None
    
    llm_max_concurrent: int = 3  # Number of parallel LLM matching calls
    worker_concurrency: int = 3  # Number of parallel pipeline tasks (scrape/match/etc.)
    worker_poll_interval_seconds: float = 0.5  # Sleep when no queued tasks found
    headless: bool = True  # Run browser invisibly to avoid disrupting user
    allow_final_submit: bool = False
    default_resume_path: str = "./data/resumes/sample_resume.txt"
    default_profile_path: str = "./data/profiles/profile.json"
    database_path: str = "./data/linkedin_copilot.sqlite3"
    dry_run: bool = True
    log_level: str = "INFO"
    suggestion_cache_ttl_minutes: int = 30
    suggestion_count: int = 14

    class Config:
        env_prefix = ""
        env_file = ".env"
        case_sensitive = False


class AppSettings(BaseModel):
    """Application settings loaded from YAML and env."""

    env: EnvSettings
    browser: Dict[str, Any]
    logging: Dict[str, Any]
    search_defaults: Dict[str, Any]
    safety: Dict[str, Any]
    data: Dict[str, Any]


def load_yaml_settings(path: Path) -> Dict[str, Any]:
    """Load settings YAML from the given path."""
    if not path.exists():
        raise FileNotFoundError(f"Settings file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw


def build_settings(config_dir: Optional[Path] = None) -> AppSettings:
    """Build `AppSettings` from YAML and environment variables."""
    base_dir = config_dir or Path.cwd() / "config"
    yaml_settings = load_yaml_settings(base_dir / "settings.yaml")

    env = EnvSettings()

    yaml_settings.setdefault("env", {})
    yaml_settings["env"].update(
        {
            "llm_provider": env.llm_provider,
            "ollama_base_url": env.ollama_base_url,
            "ollama_model": env.ollama_model,
            "openai_api_key": env.openai_api_key,
            "openai_model": env.openai_model,
            "tavily_api_key": env.tavily_api_key,
            "llm_max_concurrent": env.llm_max_concurrent,
            "worker_concurrency": env.worker_concurrency,
            "worker_poll_interval_seconds": env.worker_poll_interval_seconds,
            "headless": env.headless,
            "allow_final_submit": env.allow_final_submit,
            "default_resume_path": env.default_resume_path,
            "default_profile_path": env.default_profile_path,
            "database_path": env.database_path,
            "dry_run": env.dry_run,
            "log_level": env.log_level,
            "suggestion_cache_ttl_minutes": env.suggestion_cache_ttl_minutes,
            "suggestion_count": env.suggestion_count,
        }
    )

    app_settings = AppSettings(
        env=env,
        browser=yaml_settings.get("browser", {}),
        logging=yaml_settings.get("logging", {}),
        search_defaults=yaml_settings.get("search_defaults", {}),
        safety=yaml_settings.get("safety", {}),
        data=yaml_settings.get("data", {}),
    )
    return app_settings


settings: Optional[AppSettings] = None


def get_settings() -> AppSettings:
    """Get global `AppSettings` singleton."""
    global settings
    if settings is None:
        settings = build_settings()
    return settings

