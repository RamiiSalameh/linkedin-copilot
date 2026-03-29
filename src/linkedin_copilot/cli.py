from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich import print as rprint

from .db import init_db, list_jobs_by_status
from .logging_setup import setup_logging, logger
from .models import JobStatus
from .scoring.matcher import load_profile
from .storage.files import export_jobs_csv
from .utils import ensure_data_dirs
from .linkedin.search import search_jobs

app = typer.Typer(help="Local LinkedIn job search copilot.")


def _bootstrap() -> None:
    ensure_data_dirs()
    setup_logging()
    init_db()


@app.command()
def search(
    keywords: str = typer.Option(..., help="Job search keywords"),
    location: str = typer.Option(..., help="Job location"),
    easy_apply: bool = typer.Option(False, "--easy-apply", help="Easy Apply only"),
    limit: int = typer.Option(50, help="Maximum number of jobs to store"),
) -> None:
    """Search LinkedIn jobs and store them locally."""
    _bootstrap()

    async def _run() -> None:
        result = await search_jobs(
            keywords=keywords, location=location, easy_apply_only=easy_apply, limit=limit
        )
        rprint(f"[green]Stored {result.new_jobs} new jobs ({result.duplicates} duplicates).[/green]")

    asyncio.run(_run())


@app.command()
def shortlist() -> None:
    """List discovered jobs that may be shortlisted manually."""
    _bootstrap()
    jobs = list_jobs_by_status([JobStatus.DISCOVERED, JobStatus.SHORTLISTED])
    for j in jobs:
        rprint(
            f"[bold]{j.id}[/bold]: {j.title} @ {j.company} "
            f"({j.location}) - Easy Apply: {j.easy_apply} [{j.status.value}]"
        )


@app.command()
def export() -> None:
    """Export job dataset to CSV."""
    _bootstrap()
    path = export_jobs_csv()
    rprint(f"[green]Exported jobs to {path}[/green]")


@app.command()
def show_profile() -> None:
    """Show the currently configured profile summary."""
    _bootstrap()
    profile = load_profile()
    rprint(profile.model_dump())


if __name__ == "__main__":
    app()

