from pathlib import Path

from linkedin_copilot.models import JobDetail, JobRecord, UserProfile
from linkedin_copilot.scoring.matcher import score_job


class _FakeLLM:
    @property
    def provider_name(self) -> str:  # matches BaseLLM interface used by UI
        return "FakeLLM"

    def score_match(self, resume_text: str, job_description: str) -> dict:
        return {
            "match_score": 88,
            "top_reasons": ["Test reason"],
            "missing_requirements": [],
            "suggested_resume_bullets": ["Test bullet"],
        }


def test_score_job_basic(tmp_path: Path, monkeypatch) -> None:
    # Avoid hitting a real Ollama server during tests.
    monkeypatch.setenv("DEFAULT_RESUME_PATH", str(tmp_path / "resume.txt"))
    monkeypatch.setenv("DEFAULT_PROFILE_PATH", str(tmp_path / "profile.json"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))

    # Patch LLM client to a fake implementation.
    import linkedin_copilot.llm as llm_module

    llm_module._client = _FakeLLM()  # type: ignore[assignment]

    job = JobRecord(
        id=1,
        title="Python Engineer",
        company="Example",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/123",
        date_found=__import__("datetime").datetime.utcnow(),
        easy_apply=True,
    )
    detail = JobDetail(job=job, full_description="We need a Python engineer.")
    profile = UserProfile(
        full_name="Test User",
        email="test@example.com",
        phone="123",
        city="City",
        country="Country",
        linkedin_url="https://www.linkedin.com/in/test",
        authorized_to_work_regions=["World"],
        years_experience_by_skill={"python": 5},
        top_skills=["python"],
        preferred_titles=["Python Engineer"],
        preferred_locations=["Remote"],
    )

    # Use a temporary resume file
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text("Python experience.", encoding="utf-8")

    result = score_job(detail, profile, resume_path=resume_path)
    assert result.match_score == 88
    assert "Test reason" in result.top_reasons


