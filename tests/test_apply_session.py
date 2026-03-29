"""
Tests for the in-app application session module.

Tests cover:
- Session lifecycle (create, start, fill, submit, cancel)
- Form field detection
- Action execution
- Safety guards (no auto-submit)
- Database operations
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, AsyncMock
import json
import uuid

from linkedin_copilot.models import (
    ApplySession,
    ApplySessionStatus,
    FormField,
    FormFieldType,
    ApplicationAction,
    ActionType,
    ActionStatus,
    WebSocketMessage,
    JobRecord,
    JobStatus,
)
from linkedin_copilot.linkedin.form_detector import (
    map_field_to_profile,
    suggest_field_values,
    FIELD_PATTERNS,
)


class TestApplySessionModels:
    """Tests for ApplySession-related Pydantic models."""
    
    def test_apply_session_creation(self):
        """Test creating an ApplySession model."""
        session = ApplySession(
            id=str(uuid.uuid4()),
            job_id=123,
            job_title="Senior Backend Engineer",
            company="Test Company",
            job_url="https://linkedin.com/jobs/view/123",
        )
        
        assert session.status == ApplySessionStatus.IDLE
        assert session.current_step == 1
        assert session.is_active()
    
    def test_apply_session_is_active(self):
        """Test is_active returns correct values for different statuses."""
        session = ApplySession(
            id=str(uuid.uuid4()),
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
        )
        
        # Active statuses
        for status in [
            ApplySessionStatus.IDLE,
            ApplySessionStatus.NAVIGATING,
            ApplySessionStatus.FORM_READY,
            ApplySessionStatus.FILLING,
            ApplySessionStatus.REVIEWING,
        ]:
            session.status = status
            assert session.is_active(), f"Status {status} should be active"
        
        # Inactive statuses
        for status in [
            ApplySessionStatus.SUBMITTED,
            ApplySessionStatus.FAILED,
            ApplySessionStatus.CANCELLED,
            ApplySessionStatus.TIMEOUT,
        ]:
            session.status = status
            assert not session.is_active(), f"Status {status} should not be active"
    
    def test_apply_session_to_dict(self):
        """Test ApplySession to_dict conversion."""
        session = ApplySession(
            id="test-123",
            job_id=456,
            job_title="Test Job",
            company="Test Corp",
            job_url="https://linkedin.com/jobs/view/456",
            status=ApplySessionStatus.FORM_READY,
            current_step=2,
            total_steps=4,
        )
        
        result = session.to_dict()
        
        assert result["id"] == "test-123"
        assert result["job_id"] == 456
        assert result["status"] == "form_ready"
        assert result["current_step"] == 2
        assert result["total_steps"] == 4
    
    def test_form_field_creation(self):
        """Test creating a FormField model."""
        field = FormField(
            field_id="email_1",
            label="Email Address",
            field_type=FormFieldType.EMAIL,
            required=True,
            suggested_value="test@example.com",
            suggestion_source="profile",
        )
        
        assert field.field_id == "email_1"
        assert field.required
        assert field.field_type == FormFieldType.EMAIL
    
    def test_form_field_to_dict(self):
        """Test FormField to_dict conversion."""
        field = FormField(
            field_id="phone_1",
            label="Phone Number",
            field_type=FormFieldType.PHONE,
            suggested_value="555-1234",
            suggestion_source="profile",
            options=["Option 1", "Option 2"],
        )
        
        result = field.to_dict()
        
        assert result["field_id"] == "phone_1"
        assert result["field_type"] == "phone"
        assert result["suggested_value"] == "555-1234"
        assert result["options"] == ["Option 1", "Option 2"]
    
    def test_application_action_creation(self):
        """Test creating an ApplicationAction model."""
        action = ApplicationAction(
            session_id="session-123",
            action_type=ActionType.FILL_FIELD,
            target_field_id="email_1",
            value="test@example.com",
            status=ActionStatus.PENDING,
        )
        
        assert action.action_type == ActionType.FILL_FIELD
        assert action.status == ActionStatus.PENDING
    
    def test_websocket_message_to_json(self):
        """Test WebSocketMessage JSON conversion."""
        msg = WebSocketMessage(
            type="status",
            data={"status": "form_ready", "current_step": 2},
        )
        
        json_str = msg.to_json()
        parsed = json.loads(json_str)
        
        assert parsed["type"] == "status"
        assert parsed["data"]["status"] == "form_ready"
        assert "timestamp" in parsed


class TestFormFieldMapping:
    """Tests for form field to profile mapping."""
    
    @pytest.fixture
    def sample_profile(self):
        """Create a sample profile for testing."""
        return {
            "full_name": "John Doe",
            "email": "john.doe@example.com",
            "phone": "555-123-4567",
            "city": "San Francisco",
            "country": "USA",
            "linkedin_url": "https://linkedin.com/in/johndoe",
            "github_url": "https://github.com/johndoe",
            "years_experience_total": 8,
            "authorized_to_work_regions": ["USA", "EU"],
            "work_preferences": ["Remote", "Hybrid"],
            "canned_answers": {
                "work_authorization": "I am authorized to work in the USA.",
                "visa_sponsorship": "No sponsorship required.",
            },
        }
    
    def test_map_email_field(self, sample_profile):
        """Test mapping email field to profile."""
        field = FormField(
            field_id="email_1",
            label="Email Address",
            field_type=FormFieldType.EMAIL,
        )
        
        value, source = map_field_to_profile(field, sample_profile)
        
        assert value == "john.doe@example.com"
        assert source == "profile"
    
    def test_map_phone_field(self, sample_profile):
        """Test mapping phone field to profile."""
        field = FormField(
            field_id="phone_1",
            label="Phone Number",
            field_type=FormFieldType.PHONE,
        )
        
        value, source = map_field_to_profile(field, sample_profile)
        
        assert value == "555-123-4567"
        assert source == "profile"
    
    def test_map_name_field(self, sample_profile):
        """Test mapping name field to profile."""
        field = FormField(
            field_id="name_1",
            label="Full Name",
            field_type=FormFieldType.TEXT,
        )
        
        value, source = map_field_to_profile(field, sample_profile)
        
        assert value == "John Doe"
        assert source == "profile"
    
    def test_map_years_experience_field(self, sample_profile):
        """Test mapping years of experience field."""
        field = FormField(
            field_id="exp_1",
            label="How many years of experience do you have?",
            field_type=FormFieldType.NUMBER,
        )
        
        value, source = map_field_to_profile(field, sample_profile)
        
        assert value == "8"
        assert source == "profile"
    
    def test_map_location_field(self, sample_profile):
        """Test mapping location field."""
        field = FormField(
            field_id="loc_1",
            label="Current Location",
            field_type=FormFieldType.TEXT,
        )
        
        value, source = map_field_to_profile(field, sample_profile)
        
        assert value == "San Francisco, USA"
        assert source == "profile"
    
    def test_map_linkedin_field(self, sample_profile):
        """Test mapping LinkedIn field."""
        field = FormField(
            field_id="linkedin_1",
            label="LinkedIn Profile URL",
            field_type=FormFieldType.URL,
        )
        
        value, source = map_field_to_profile(field, sample_profile)
        
        assert value == "https://linkedin.com/in/johndoe"
        assert source == "profile"
    
    def test_map_canned_answer(self, sample_profile):
        """Test mapping to canned answers."""
        field = FormField(
            field_id="auth_1",
            label="work_authorization",
            field_type=FormFieldType.TEXT,
        )
        
        value, source = map_field_to_profile(field, sample_profile)
        
        assert value == "I am authorized to work in the USA."
        assert source == "canned"
    
    def test_map_unknown_field(self, sample_profile):
        """Test mapping unknown field returns None."""
        field = FormField(
            field_id="unknown_1",
            label="Some Random Field",
            field_type=FormFieldType.TEXT,
        )
        
        value, source = map_field_to_profile(field, sample_profile)
        
        assert value is None
        assert source == ""
    
    def test_suggest_field_values(self, sample_profile):
        """Test suggesting values for multiple fields."""
        fields = [
            FormField(field_id="email_1", label="Email", field_type=FormFieldType.EMAIL),
            FormField(field_id="phone_1", label="Phone", field_type=FormFieldType.PHONE),
            FormField(field_id="unknown_1", label="Unknown", field_type=FormFieldType.TEXT),
        ]
        
        updated_fields = suggest_field_values(fields, sample_profile)
        
        assert updated_fields[0].suggested_value == "john.doe@example.com"
        assert updated_fields[0].suggestion_source == "profile"
        
        assert updated_fields[1].suggested_value == "555-123-4567"
        assert updated_fields[1].suggestion_source == "profile"
        
        assert updated_fields[2].suggested_value is None


class TestDatabaseOperations:
    """Tests for apply session database operations."""
    
    @pytest.fixture
    def temp_db(self, tmp_path, monkeypatch):
        """Create a temporary database for testing."""
        db_path = tmp_path / "test_apply.sqlite3"
        
        # Mock the database path
        monkeypatch.setattr(
            "linkedin_copilot.db._get_db_path",
            lambda: db_path
        )
        
        from linkedin_copilot.db import init_db
        init_db()
        
        return db_path
    
    def test_create_and_get_session(self, temp_db):
        """Test creating and retrieving an apply session."""
        from linkedin_copilot.db import (
            create_apply_session,
            get_apply_session,
        )
        
        session = ApplySession(
            id=str(uuid.uuid4()),
            job_id=123,
            job_title="Test Job",
            company="Test Corp",
            job_url="https://linkedin.com/jobs/view/123",
            status=ApplySessionStatus.IDLE,
        )
        
        create_apply_session(session)
        
        retrieved = get_apply_session(session.id)
        
        assert retrieved is not None
        assert retrieved.id == session.id
        assert retrieved.job_id == 123
        assert retrieved.status == ApplySessionStatus.IDLE
    
    def test_update_session_status(self, temp_db):
        """Test updating session status."""
        from linkedin_copilot.db import (
            create_apply_session,
            update_apply_session_status,
            get_apply_session,
        )
        
        session = ApplySession(
            id=str(uuid.uuid4()),
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
        )
        
        create_apply_session(session)
        update_apply_session_status(session.id, ApplySessionStatus.FORM_READY)
        
        retrieved = get_apply_session(session.id)
        
        assert retrieved.status == ApplySessionStatus.FORM_READY
    
    def test_update_session_fields(self, temp_db):
        """Test updating detected fields."""
        from linkedin_copilot.db import (
            create_apply_session,
            update_apply_session_fields,
            get_apply_session,
        )
        
        session = ApplySession(
            id=str(uuid.uuid4()),
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
        )
        
        create_apply_session(session)
        
        fields = [
            FormField(field_id="email", label="Email", field_type=FormFieldType.EMAIL),
            FormField(field_id="phone", label="Phone", field_type=FormFieldType.PHONE),
        ]
        
        update_apply_session_fields(session.id, fields)
        
        retrieved = get_apply_session(session.id)
        
        assert len(retrieved.detected_fields) == 2
        assert retrieved.detected_fields[0].label == "Email"
    
    def test_save_and_get_actions(self, temp_db):
        """Test saving and retrieving session actions."""
        from linkedin_copilot.db import (
            create_apply_session,
            save_session_action,
            get_session_actions,
        )
        
        session = ApplySession(
            id=str(uuid.uuid4()),
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
        )
        
        create_apply_session(session)
        
        action = ApplicationAction(
            session_id=session.id,
            action_type=ActionType.FILL_FIELD,
            target_field_id="email_1",
            value="test@example.com",
            status=ActionStatus.PENDING,
        )
        
        action_id = save_session_action(action)
        
        actions = get_session_actions(session.id)
        
        assert len(actions) == 1
        assert actions[0].target_field_id == "email_1"
        assert actions[0].value == "test@example.com"
    
    def test_delete_session(self, temp_db):
        """Test deleting an apply session."""
        from linkedin_copilot.db import (
            create_apply_session,
            delete_apply_session,
            get_apply_session,
        )
        
        session = ApplySession(
            id=str(uuid.uuid4()),
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
        )
        
        create_apply_session(session)
        result = delete_apply_session(session.id)
        
        assert result is True
        assert get_apply_session(session.id) is None


class TestSafetyGuards:
    """Tests for safety mechanisms."""
    
    def test_submit_requires_confirmation(self):
        """Test that submit requires explicit confirmation flag."""
        from linkedin_copilot.linkedin.apply_session import ApplySessionEngine
        
        engine = ApplySessionEngine(job_id=123)
        engine.session = ApplySession(
            id="test-session",
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
            status=ApplySessionStatus.REVIEWING,
        )
        
        # Submit without confirmation should fail
        # Note: This is tested via the API endpoint behavior
        # The engine.submit() method requires confirmed=True
        
        # We can't call submit without page, but we verify the flag is required
        import inspect
        sig = inspect.signature(engine.submit)
        assert "confirmed" in sig.parameters
        assert sig.parameters["confirmed"].default is False
    
    def test_session_status_transitions(self):
        """Test that session statuses transition correctly."""
        session = ApplySession(
            id="test-session",
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
        )
        
        # Initial status
        assert session.status == ApplySessionStatus.IDLE
        
        # Valid transitions
        valid_transitions = [
            (ApplySessionStatus.IDLE, ApplySessionStatus.NAVIGATING),
            (ApplySessionStatus.NAVIGATING, ApplySessionStatus.CLICKING_APPLY),
            (ApplySessionStatus.CLICKING_APPLY, ApplySessionStatus.FORM_READY),
            (ApplySessionStatus.FORM_READY, ApplySessionStatus.FILLING),
            (ApplySessionStatus.FILLING, ApplySessionStatus.FORM_READY),
            (ApplySessionStatus.FORM_READY, ApplySessionStatus.NEXT_PAGE),
            (ApplySessionStatus.NEXT_PAGE, ApplySessionStatus.FORM_READY),
            (ApplySessionStatus.FORM_READY, ApplySessionStatus.REVIEWING),
            (ApplySessionStatus.REVIEWING, ApplySessionStatus.SUBMITTING),
            (ApplySessionStatus.SUBMITTING, ApplySessionStatus.SUBMITTED),
        ]
        
        for from_status, to_status in valid_transitions:
            session.status = from_status
            session.status = to_status
            assert session.status == to_status


class TestFormFieldPatterns:
    """Tests for form field pattern matching."""
    
    def test_email_patterns(self):
        """Test email field patterns."""
        email_patterns = FIELD_PATTERNS["email"]
        
        test_labels = [
            "email",
            "Email Address",
            "Your e-mail",
        ]
        
        import re
        for label in test_labels:
            matched = any(
                re.search(pattern, label, re.IGNORECASE)
                for pattern in email_patterns
            )
            assert matched, f"Email pattern should match '{label}'"
    
    def test_phone_patterns(self):
        """Test phone field patterns."""
        phone_patterns = FIELD_PATTERNS["phone"]
        
        test_labels = [
            "Phone Number",
            "telephone",
            "Mobile",
            "Cell Phone",
        ]
        
        import re
        for label in test_labels:
            matched = any(
                re.search(pattern, label, re.IGNORECASE)
                for pattern in phone_patterns
            )
            assert matched, f"Phone pattern should match '{label}'"
    
    def test_experience_patterns(self):
        """Test years of experience field patterns."""
        exp_patterns = FIELD_PATTERNS["years_experience"]
        
        test_labels = [
            "Years of experience",
            "How many years of experience do you have?",
            "Total experience in years",
        ]
        
        import re
        for label in test_labels:
            matched = any(
                re.search(pattern, label, re.IGNORECASE)
                for pattern in exp_patterns
            )
            assert matched, f"Experience pattern should match '{label}'"
    
    def test_work_authorization_patterns(self):
        """Test work authorization field patterns."""
        auth_patterns = FIELD_PATTERNS["work_authorization"]
        
        test_labels = [
            "Are you authorized to work in the US?",
            "Work authorization status",
            "Do you legally work in this country?",
            "Do you require visa sponsorship?",
        ]
        
        import re
        for label in test_labels:
            matched = any(
                re.search(pattern, label, re.IGNORECASE)
                for pattern in auth_patterns
            )
            assert matched, f"Work auth pattern should match '{label}'"


class TestApplySessionEngine:
    """Tests for the ApplySessionEngine class."""
    
    @pytest.fixture
    def mock_job(self):
        """Create a mock job for testing."""
        return JobRecord(
            id=123,
            title="Senior Backend Engineer",
            company="Test Corp",
            location="San Francisco, CA",
            url="https://linkedin.com/jobs/view/123",
            date_found=datetime.utcnow(),
            easy_apply=True,
            status=JobStatus.MATCHED,
        )
    
    @patch("linkedin_copilot.linkedin.apply_session.get_job_by_id")
    @patch("linkedin_copilot.linkedin.apply_session.create_apply_session")
    @patch("linkedin_copilot.linkedin.apply_session.load_profile")
    def test_initialize_session(
        self,
        mock_load_profile,
        mock_create_session,
        mock_get_job,
        mock_job,
    ):
        """Test initializing an apply session."""
        from linkedin_copilot.linkedin.apply_session import ApplySessionEngine
        
        mock_get_job.return_value = mock_job
        mock_load_profile.return_value = {"full_name": "Test User"}
        mock_create_session.return_value = "session-123"
        
        engine = ApplySessionEngine(job_id=123)
        
        # Run initialize in sync context for testing
        import asyncio
        loop = asyncio.new_event_loop()
        session = loop.run_until_complete(engine.initialize())
        loop.close()
        
        assert session is not None
        assert session.job_id == 123
        assert session.job_title == "Senior Backend Engineer"
        assert session.status == ApplySessionStatus.IDLE
    
    @patch("linkedin_copilot.linkedin.apply_session.get_job_by_id")
    def test_initialize_fails_for_non_easy_apply(self, mock_get_job):
        """Test that initialization fails for non-Easy Apply jobs."""
        from linkedin_copilot.linkedin.apply_session import ApplySessionEngine
        
        mock_get_job.return_value = JobRecord(
            id=123,
            title="Test Job",
            company="Test Corp",
            location="Remote",
            url="https://linkedin.com/jobs/view/123",
            date_found=datetime.utcnow(),
            easy_apply=False,  # Not Easy Apply
            status=JobStatus.MATCHED,
        )
        
        engine = ApplySessionEngine(job_id=123)
        
        import asyncio
        loop = asyncio.new_event_loop()
        
        with pytest.raises(ValueError, match="Easy Apply"):
            loop.run_until_complete(engine.initialize())
        
        loop.close()
    
    @patch("linkedin_copilot.linkedin.apply_session.get_job_by_id")
    def test_initialize_fails_for_missing_job(self, mock_get_job):
        """Test that initialization fails for missing job."""
        from linkedin_copilot.linkedin.apply_session import ApplySessionEngine
        
        mock_get_job.return_value = None
        
        engine = ApplySessionEngine(job_id=999)
        
        import asyncio
        loop = asyncio.new_event_loop()
        
        with pytest.raises(ValueError, match="not found"):
            loop.run_until_complete(engine.initialize())
        
        loop.close()
    
    def test_add_remove_websocket_client(self):
        """Test adding and removing WebSocket clients."""
        from linkedin_copilot.linkedin.apply_session import ApplySessionEngine
        
        engine = ApplySessionEngine(job_id=123)
        
        mock_client = MagicMock()
        
        engine.add_websocket_client(mock_client)
        assert mock_client in engine._connected_clients
        
        engine.remove_websocket_client(mock_client)
        assert mock_client not in engine._connected_clients


class TestActiveSessionRegistry:
    """Tests for the active session registry functions."""
    
    def test_get_session_for_job(self):
        """Test getting active session by job ID."""
        from linkedin_copilot.linkedin.apply_session import (
            _active_sessions,
            get_session_for_job,
            ApplySessionEngine,
        )
        
        # Clear registry
        _active_sessions.clear()
        
        # Create mock engine
        engine = ApplySessionEngine(job_id=456)
        engine.session = ApplySession(
            id="test-session",
            job_id=456,
            job_url="https://linkedin.com/jobs/view/456",
        )
        
        _active_sessions["test-session"] = engine
        
        result = get_session_for_job(456)
        assert result == engine
        
        result = get_session_for_job(999)
        assert result is None
        
        # Cleanup
        _active_sessions.clear()


class TestManualFallback:
    """Tests for manual-fallback behavior in ApplySessionEngine."""

    @pytest.mark.asyncio
    async def test_start_falls_back_to_manual_easy_apply_when_button_not_found(self):
        from linkedin_copilot.linkedin.apply_session import ApplySessionEngine

        engine = ApplySessionEngine(job_id=123)
        engine.session = ApplySession(
            id="sess-1",
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
        )

        engine._launch_browser = AsyncMock()
        engine._navigate_to_job = AsyncMock()
        engine.get_screenshot = AsyncMock(return_value=None)
        engine._screenshot_loop = AsyncMock(return_value=None)
        engine._click_easy_apply = AsyncMock(side_effect=RuntimeError("Easy Apply button not found"))
        engine._handle_error = AsyncMock()
        engine._broadcast_message = AsyncMock()

        with patch("linkedin_copilot.linkedin.apply_session.update_apply_session_status"):
            await engine.start()

        assert engine.session.status == ApplySessionStatus.MANUAL_EASY_APPLY_NEEDED
        engine._handle_error.assert_not_called()

        # Ensure a status broadcast contained manual_action instructions
        status_payloads = [
            call.args[1]
            for call in engine._broadcast_message.call_args_list
            if call.args and call.args[0] == "status"
        ]
        assert any(
            isinstance(p.get("manual_action"), dict)
            and p["manual_action"].get("action") == "retry_after_manual_easy_apply"
            for p in status_payloads
        )

    @pytest.mark.asyncio
    async def test_retry_after_manual_easy_apply_sets_form_ready_when_fields_detected(self):
        from linkedin_copilot.linkedin.apply_session import ApplySessionEngine

        engine = ApplySessionEngine(job_id=123)
        engine.page = MagicMock()
        engine.session = ApplySession(
            id="sess-2",
            job_id=123,
            job_url="https://linkedin.com/jobs/view/123",
        )

        async def _fake_detect():
            engine.session.detected_fields = [
                FormField(
                    field_id="text_0",
                    label="Email",
                    field_type=FormFieldType.EMAIL,
                    required=True,
                )
            ]

        engine._detect_and_suggest_fields = AsyncMock(side_effect=_fake_detect)
        engine._broadcast_status = AsyncMock()

        with patch("linkedin_copilot.linkedin.apply_session.update_apply_session_status") as mock_update_status:
            ok = await engine.retry_after_manual_easy_apply()

        assert ok is True
        assert engine.session.status == ApplySessionStatus.FORM_READY
        mock_update_status.assert_called_with(engine.session.id, ApplySessionStatus.FORM_READY)

    @pytest.mark.asyncio
    async def test_open_interactive_popup_returns_false_without_session(self):
        from linkedin_copilot.linkedin.apply_session import ApplySessionEngine

        engine = ApplySessionEngine(job_id=123)
        ok = await engine.open_interactive_popup()
        assert ok is False
