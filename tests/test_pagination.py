"""Tests for jobs pagination functionality."""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from linkedin_copilot.models import JobRecord, JobStatus, MatchResult
from linkedin_copilot.db import (
    get_jobs_paginated,
    get_match_results_for_jobs,
    init_db,
    insert_job,
    save_match_result,
    delete_jobs,
    db_connection,
)


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database."""
    db_path = tmp_path / "test.sqlite3"
    
    with patch('linkedin_copilot.db._get_db_path', return_value=db_path):
        init_db()
        yield db_path


@pytest.fixture
def sample_jobs(test_db):
    """Create sample jobs for testing."""
    jobs = []
    
    with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
        for i in range(55):
            job = JobRecord(
                title=f"Software Engineer {i}",
                company=f"Company {chr(65 + (i % 26))}",
                location="Tel Aviv" if i % 2 == 0 else "Remote",
                url=f"https://linkedin.com/jobs/{i}",
                linkedin_job_id=str(1000 + i),
                date_found=datetime.now(),
                date_posted=datetime.now() if i % 3 == 0 else None,
                easy_apply=i % 2 == 0,
                description_snippet=f"Job description {i}",
                status=JobStatus.MATCHED if i < 30 else (
                    JobStatus.PENDING_MATCH if i < 45 else JobStatus.PENDING_SCRAPE
                ),
            )
            inserted = insert_job(job)
            jobs.append(inserted)
            
            if i < 30:
                score = 80 - (i * 2) if i < 10 else (60 - i) if i < 20 else (30 - (i - 20))
                result = MatchResult(
                    job_id=inserted.id,
                    match_score=max(10, min(95, score)),
                    top_reasons=[f"Reason {i}"],
                    missing_requirements=[],
                    inferred_qualifications=[],
                    suggested_resume_bullets=[],
                )
                save_match_result(result)
    
    return jobs


class TestGetJobsPaginated:
    """Test the get_jobs_paginated function."""
    
    def test_basic_pagination(self, test_db, sample_jobs):
        """Test basic pagination with default settings."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(page=1, per_page=25)
            
            assert len(jobs) == 25
            assert total == 55
            assert counts['total'] == 55
    
    def test_second_page(self, test_db, sample_jobs):
        """Test getting the second page."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(page=2, per_page=25)
            
            assert len(jobs) == 25
            assert total == 55
    
    def test_last_page(self, test_db, sample_jobs):
        """Test getting the last page with partial results."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(page=3, per_page=25)
            
            assert len(jobs) == 5
            assert total == 55
    
    def test_page_beyond_range(self, test_db, sample_jobs):
        """Test requesting a page beyond available data."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(page=10, per_page=25)
            
            assert len(jobs) == 0
            assert total == 55
    
    def test_status_filter(self, test_db, sample_jobs):
        """Test filtering by status."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(
                page=1, per_page=100, status_filter='matched'
            )
            
            assert total == 30
            assert all(j.status == JobStatus.MATCHED for j in jobs)
    
    def test_search_query(self, test_db, sample_jobs):
        """Test search in title, company, location."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(
                page=1, per_page=100, search_query='Tel Aviv'
            )
            
            assert total > 0
            assert all('tel aviv' in j.location.lower() for j in jobs)
    
    def test_recommendation_filter_apply(self, test_db, sample_jobs):
        """Test filtering by 'apply' recommendation (score >= 70)."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(
                page=1, per_page=100, recommendation_filter='apply'
            )
            
            job_ids = [j.id for j in jobs]
            match_results = get_match_results_for_jobs(job_ids)
            
            for job in jobs:
                if job.id in match_results:
                    assert match_results[job.id].match_score >= 70
    
    def test_recommendation_filter_skip(self, test_db, sample_jobs):
        """Test filtering by 'skip' recommendation (score < 50)."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(
                page=1, per_page=100, recommendation_filter='skip'
            )
            
            job_ids = [j.id for j in jobs]
            match_results = get_match_results_for_jobs(job_ids)
            
            for job in jobs:
                if job.id in match_results:
                    assert match_results[job.id].match_score < 50
    
    def test_sort_by_score_desc(self, test_db, sample_jobs):
        """Test sorting by score descending."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(
                page=1, per_page=10, sort_by='score', sort_dir='desc'
            )
            
            job_ids = [j.id for j in jobs]
            match_results = get_match_results_for_jobs(job_ids)
            
            scores = []
            for job in jobs:
                if job.id in match_results:
                    scores.append(match_results[job.id].match_score)
                else:
                    scores.append(-1)
            
            assert scores == sorted(scores, reverse=True)
    
    def test_sort_by_company_asc(self, test_db, sample_jobs):
        """Test sorting by company ascending."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(
                page=1, per_page=10, sort_by='company', sort_dir='asc'
            )
            
            companies = [j.company for j in jobs]
            assert companies == sorted(companies)
    
    def test_status_counts(self, test_db, sample_jobs):
        """Test that status counts are accurate."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(page=1, per_page=25)
            
            assert counts['matched'] == 30
            assert counts['pending_match'] == 15
            assert counts['pending_scrape'] == 10
            assert counts['total'] == 55
    
    def test_combined_filters(self, test_db, sample_jobs):
        """Test combining multiple filters."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(
                page=1,
                per_page=100,
                status_filter='matched',
                search_query='Tel Aviv',
            )
            
            assert all(j.status == JobStatus.MATCHED for j in jobs)
            assert all('tel aviv' in j.location.lower() for j in jobs)
    
    def test_invalid_page_number(self, test_db, sample_jobs):
        """Test that invalid page numbers are handled."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(page=0, per_page=25)
            assert len(jobs) == 25
            
            jobs, total, counts = get_jobs_paginated(page=-5, per_page=25)
            assert len(jobs) == 25
    
    def test_per_page_limits(self, test_db, sample_jobs):
        """Test that per_page has reasonable limits."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(page=1, per_page=200)
            assert len(jobs) == 55
            
            jobs, total, counts = get_jobs_paginated(page=1, per_page=0)
            assert len(jobs) <= 25


class TestGetMatchResultsForJobs:
    """Test the get_match_results_for_jobs function."""
    
    def test_get_results_for_job_ids(self, test_db, sample_jobs):
        """Test getting match results for specific job IDs."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            job_ids = [sample_jobs[0].id, sample_jobs[1].id, sample_jobs[2].id]
            results = get_match_results_for_jobs(job_ids)
            
            assert len(results) == 3
            for job_id in job_ids:
                assert job_id in results
    
    def test_empty_job_ids(self, test_db, sample_jobs):
        """Test with empty job IDs list."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            results = get_match_results_for_jobs([])
            assert results == {}
    
    def test_nonexistent_job_ids(self, test_db, sample_jobs):
        """Test with non-existent job IDs."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            results = get_match_results_for_jobs([99999, 99998])
            assert results == {}


class TestPaginationEdgeCases:
    """Test edge cases in pagination."""
    
    def test_empty_database(self, test_db):
        """Test pagination with no jobs."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            jobs, total, counts = get_jobs_paginated(page=1, per_page=25)
            
            assert len(jobs) == 0
            assert total == 0
            assert counts['total'] == 0
    
    def test_single_job(self, test_db):
        """Test pagination with a single job."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            job = JobRecord(
                title="Single Job",
                company="Single Company",
                location="Location",
                url="https://linkedin.com/jobs/single",
                linkedin_job_id="single123",
                date_found=datetime.now(),
                status=JobStatus.MATCHED,
            )
            insert_job(job)
            
            jobs, total, counts = get_jobs_paginated(page=1, per_page=25)
            
            assert len(jobs) == 1
            assert total == 1
            assert counts['total'] == 1
    
    def test_exact_page_boundary(self, test_db):
        """Test when total items exactly match page size."""
        with patch('linkedin_copilot.db._get_db_path', return_value=test_db):
            for i in range(25):
                job = JobRecord(
                    title=f"Job {i}",
                    company=f"Company {i}",
                    location="Location",
                    url=f"https://linkedin.com/jobs/{i}",
                    linkedin_job_id=str(i),
                    date_found=datetime.now(),
                    status=JobStatus.MATCHED,
                )
                insert_job(job)
            
            jobs, total, counts = get_jobs_paginated(page=1, per_page=25)
            assert len(jobs) == 25
            assert total == 25
            
            jobs, total, counts = get_jobs_paginated(page=2, per_page=25)
            assert len(jobs) == 0
