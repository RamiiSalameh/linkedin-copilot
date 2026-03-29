from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, HttpUrl


def extract_linkedin_job_id(url: str) -> Optional[str]:
    """Extract LinkedIn job ID from various URL formats.
    
    Examples:
        https://www.linkedin.com/jobs/view/4384802223/
        https://linkedin.com/jobs/view/4384802223?trk=...
        https://www.linkedin.com/jobs/view/senior-engineer-at-company-4384802223
    
    Returns the job ID as string, or None if not found.
    """
    if not url:
        return None
    
    # Pattern 1: /jobs/view/NUMBER or /jobs/view/NUMBER/
    match = re.search(r'/jobs/view/(\d+)', url)
    if match:
        return match.group(1)
    
    # Pattern 2: /jobs/view/title-slug-NUMBER (job ID at end of slug)
    match = re.search(r'/jobs/view/[^/]+-(\d{8,})', url)
    if match:
        return match.group(1)
    
    # Pattern 3: currentJobId query parameter
    match = re.search(r'currentJobId=(\d+)', url)
    if match:
        return match.group(1)
    
    return None


class JobStatus(str, Enum):
    # Three-phase workflow statuses
    PENDING_SCRAPE = "pending_scrape"      # Just discovered, no description yet
    PENDING_MATCH = "pending_match"        # Has description, awaiting LLM match
    MATCHED = "matched"                    # Has match score
    # Application workflow statuses
    DISCOVERED = "discovered"              # Legacy - maps to PENDING_SCRAPE
    SHORTLISTED = "shortlisted"
    SKIPPED = "skipped"
    OPENED = "opened"
    APPLIED = "applied"                      # User confirmed they applied
    PARTIALLY_APPLIED = "partially_applied"
    AWAITING_REVIEW = "awaiting_review"
    SUBMITTED_MANUAL = "submitted_manual"
    FAILED = "failed"
    DELETED = "deleted"                       # Soft-deleted; hidden from normal UI lists


class JobSource(str, Enum):
    """Source where the job was discovered."""
    LINKEDIN = "linkedin"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    CUSTOM = "custom"


class ATSType(str, Enum):
    """Applicant Tracking System types supported for career site scraping."""
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    ASHBY = "ashby"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class UserProfile(BaseModel):
    full_name: str
    email: str
    phone: str
    city: str
    country: str
    linkedin_url: HttpUrl
    github_url: Optional[HttpUrl] = None
    portfolio_url: Optional[HttpUrl] = None
    authorized_to_work_regions: List[str] = Field(default_factory=list)
    years_experience_by_skill: Dict[str, int] = Field(default_factory=dict)
    years_experience_total: Optional[int] = None
    top_skills: List[str] = Field(default_factory=list)
    preferred_titles: List[str] = Field(default_factory=list)
    target_titles: List[str] = Field(default_factory=list)
    preferred_locations: List[str] = Field(default_factory=list)
    salary_preferences: Optional[Dict[str, object]] = None
    work_preferences: Optional[Union[Dict[str, Any], List[str]]] = None
    education: List[Dict[str, object]] = Field(default_factory=list)
    past_roles: List[Dict[str, object]] = Field(default_factory=list)
    canned_answers: Dict[str, str] = Field(default_factory=dict)
    # Additional fields for search generation
    summary: Optional[str] = None
    seniority_guess: Optional[str] = None
    programming_languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    cloud_platforms: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    architecture_experience: List[str] = Field(default_factory=list)
    keywords_for_search: List[str] = Field(default_factory=list)


class JobRecord(BaseModel):
    id: Optional[int] = None
    title: str
    company: str
    location: str
    url: HttpUrl
    linkedin_job_id: Optional[str] = None  # Unique LinkedIn job ID for deduplication
    external_job_id: Optional[str] = None  # Job ID from external sources (Greenhouse, Lever, etc.)
    date_found: datetime
    date_posted: Optional[datetime] = None  # When job was posted on LinkedIn (scraped)
    easy_apply: bool = False
    description_snippet: Optional[str] = None
    company_logo_url: Optional[str] = None
    status: JobStatus = JobStatus.PENDING_SCRAPE
    source: JobSource = JobSource.LINKEDIN  # Where this job was discovered
    company_id: Optional[int] = None  # Reference to tracked company (for career site jobs)
    
    def model_post_init(self, __context: Any) -> None:
        """Extract LinkedIn job ID from URL if not provided."""
        if self.linkedin_job_id is None and self.url:
            self.linkedin_job_id = extract_linkedin_job_id(str(self.url))


class Company(BaseModel):
    """A tracked company for career site job scraping."""
    id: Optional[int] = None
    name: str
    careers_url: str
    ats_type: ATSType
    board_token: Optional[str] = None  # e.g., 'stripe' for Greenhouse boards
    logo_url: Optional[str] = None
    enabled: bool = True
    last_scraped: Optional[datetime] = None
    total_jobs: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "careers_url": self.careers_url,
            "ats_type": self.ats_type.value,
            "board_token": self.board_token,
            "logo_url": self.logo_url,
            "enabled": self.enabled,
            "last_scraped": self.last_scraped.isoformat() if self.last_scraped else None,
            "total_jobs": self.total_jobs,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ScrapeRun(BaseModel):
    """A single scrape run for a tracked company; holds staging jobs until user approves."""
    id: Optional[int] = None
    company_id: int = 0
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    total_found: int = 0
    new_count: int = 0
    duplicates_count: int = 0
    errors: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Pending count (staging rows) filled by DB when listing runs
    pending_count: Optional[int] = None


class JobDetail(BaseModel):
    job: JobRecord
    employment_type: Optional[str] = None
    seniority: Optional[str] = None
    full_description: Optional[str] = None
    recruiter_info: Optional[str] = None
    raw_html_path: Optional[str] = None


class ScreeningQuestion(BaseModel):
    question_text: str
    field_name: Optional[str] = None
    answer_draft: Optional[str] = None


class MatchResult(BaseModel):
    job_id: int
    match_score: int
    top_reasons: List[str]
    missing_requirements: List[str]
    inferred_qualifications: List[str] = []  # Requirements satisfied by inference
    suggested_resume_bullets: List[str]
    summary_markdown_path: Optional[str] = None
    raw_json_path: Optional[str] = None

    @property
    def recommendation(self) -> str:
        """Return a recommendation based on the match score."""
        if self.match_score >= 70:
            return "Apply"
        elif self.match_score >= 50:
            return "Consider"
        else:
            return "Skip"

    @property
    def recommendation_color(self) -> str:
        """Return a CSS color class for the recommendation."""
        if self.match_score >= 70:
            return "green"
        elif self.match_score >= 50:
            return "yellow"
        else:
            return "red"


@dataclass
class ApplicationState:
    job_url: str
    started_at: datetime
    status: JobStatus = JobStatus.PARTIALLY_APPLIED
    last_step: Optional[str] = None
    last_error: Optional[str] = None
    screenshots: List[str] = None

    def __post_init__(self) -> None:
        if self.screenshots is None:
            self.screenshots = []


class ApplySessionStatus(str, Enum):
    """Status of an in-app application session."""
    IDLE = "idle"                    # Session created but not started
    NAVIGATING = "navigating"        # Navigating to job page
    CLICKING_APPLY = "clicking_apply"  # Clicking Easy Apply button
    MANUAL_EASY_APPLY_NEEDED = "manual_easy_apply_needed"  # User must click Easy Apply in browser view
    FORM_READY = "form_ready"        # Form detected, waiting for user
    MANUAL_FORM_DETECT_NEEDED = "manual_form_detect_needed"  # Form open but we couldn't detect fields
    FILLING = "filling"              # Filling form fields
    NEXT_PAGE = "next_page"          # Moving to next form page
    MANUAL_NEXT_NEEDED = "manual_next_needed"  # User must click Next in browser view
    REVIEWING = "reviewing"          # On review/submit page
    SUBMITTING = "submitting"        # User confirmed, submitting
    MANUAL_SUBMIT_NEEDED = "manual_submit_needed"  # User must submit in browser view
    SUBMITTED = "submitted"          # Application submitted successfully
    FAILED = "failed"                # Error occurred
    CANCELLED = "cancelled"          # User cancelled
    TIMEOUT = "timeout"              # Session timed out


class FormFieldType(str, Enum):
    """Types of form fields detected in LinkedIn Easy Apply."""
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    FILE = "file"
    DATE = "date"
    NUMBER = "number"
    PHONE = "phone"
    EMAIL = "email"
    URL = "url"


class ActionType(str, Enum):
    """Types of actions that can be performed in an application session."""
    FILL_FIELD = "fill_field"
    CLICK_BUTTON = "click_button"
    SELECT_OPTION = "select_option"
    CHECK_CHECKBOX = "check_checkbox"
    UPLOAD_FILE = "upload_file"
    NEXT_STEP = "next_step"
    SUBMIT = "submit"


class ActionStatus(str, Enum):
    """Status of an application action."""
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class FormField(BaseModel):
    """A detected form field in the LinkedIn Easy Apply form."""
    field_id: str
    label: str
    field_type: FormFieldType
    required: bool = False
    current_value: Optional[str] = None
    suggested_value: Optional[str] = None
    suggestion_source: Optional[str] = None  # "profile", "resume", "ai", "canned"
    options: List[str] = Field(default_factory=list)  # For select/radio fields
    placeholder: Optional[str] = None
    validation_error: Optional[str] = None
    selector: Optional[str] = None  # CSS selector for the field

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_id": self.field_id,
            "label": self.label,
            "field_type": self.field_type.value,
            "required": self.required,
            "current_value": self.current_value,
            "suggested_value": self.suggested_value,
            "suggestion_source": self.suggestion_source,
            "options": self.options,
            "placeholder": self.placeholder,
            "validation_error": self.validation_error,
        }


class ApplicationAction(BaseModel):
    """An action to be performed or that was performed in an application session."""
    id: Optional[int] = None
    session_id: str
    action_type: ActionType
    target_field_id: Optional[str] = None
    target_selector: Optional[str] = None
    value: Optional[str] = None
    status: ActionStatus = ActionStatus.PENDING
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    executed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "action_type": self.action_type.value,
            "target_field_id": self.target_field_id,
            "value": self.value,
            "status": self.status.value,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


class ApplySession(BaseModel):
    """An in-app application session for a specific job."""
    id: str  # UUID
    job_id: int
    job_title: Optional[str] = None
    company: Optional[str] = None
    job_url: str
    status: ApplySessionStatus = ApplySessionStatus.IDLE
    current_step: int = 1
    total_steps: Optional[int] = None
    detected_fields: List[FormField] = Field(default_factory=list)
    completed_actions: List[ApplicationAction] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    error_message: Optional[str] = None
    screenshots_dir: Optional[str] = None
    last_screenshot: Optional[str] = None  # Base64 encoded last screenshot

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "job_title": self.job_title,
            "company": self.company,
            "job_url": self.job_url,
            "status": self.status.value,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "detected_fields": [f.to_dict() for f in self.detected_fields],
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "error_message": self.error_message,
        }

    def is_active(self) -> bool:
        """Check if the session is still active (not ended)."""
        return self.status not in [
            ApplySessionStatus.SUBMITTED,
            ApplySessionStatus.FAILED,
            ApplySessionStatus.CANCELLED,
            ApplySessionStatus.TIMEOUT,
        ]


class WebSocketMessage(BaseModel):
    """Message format for WebSocket communication."""
    type: str  # "screenshot", "status", "fields", "action", "error"
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def to_json(self) -> str:
        import json
        return json.dumps({
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        })


class PipelineTaskType(str, Enum):
    """Persisted background tasks processed by the pipeline worker."""
    SCRAPE_JOB_DESCRIPTION = "scrape_job_description"
    MATCH_JOB = "match_job"


class PipelineTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY = "retry"

