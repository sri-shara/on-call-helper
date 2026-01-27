"""
Tests for Pydantic data models.

Verifies that all models can be instantiated and serialized correctly.
"""

import pytest
from datetime import datetime

from backend.models import (
    Severity,
    IncidentStatus,
    TriageClassification,
    Incident,
    TriageResult,
    FixResult,
    ReviewResult,
    ReviewIssue,
    TestResult,
    VerificationResult,
    VerificationStatus,
    Metrics,
    WebSocketEvent,
)


class TestIncident:
    """Tests for Incident model."""

    def test_create_incident(self):
        """Test creating a basic incident."""
        incident = Incident(
            id="OCH-12345678",
            title="NullPointerException in caseservice",
            error_message="panic: runtime error: invalid memory address",
            service_name="caseservice",
            severity=Severity.HIGH,
        )

        assert incident.id == "OCH-12345678"
        assert incident.status == IncidentStatus.ACTIVE
        assert incident.environment == "production"
        assert incident.resolved_at is None

    def test_incident_with_all_fields(self):
        """Test creating an incident with all optional fields."""
        incident = Incident(
            id="OCH-87654321",
            title="Database connection error",
            error_message="connection refused",
            stack_trace="goroutine 1 [running]:\nmain.main()",
            file_path="backend/services/caseservice/handler.go",
            service_name="caseservice",
            severity=Severity.CRITICAL,
            tenant_name="Whitney",
            environment="production",
            status=IncidentStatus.TRIAGING,
            gcp_insert_id="abc123",
            gcp_resource_type="cloud_run_revision",
            gcp_log_name="projects/test/logs/run.googleapis.com",
        )

        assert incident.stack_trace is not None
        assert incident.tenant_name == "Whitney"
        assert incident.gcp_insert_id == "abc123"

    def test_incident_json_serialization(self):
        """Test incident can be serialized to JSON."""
        incident = Incident(
            id="OCH-12345678",
            title="Test error",
            error_message="Test message",
            service_name="testservice",
            severity=Severity.LOW,
        )

        json_data = incident.model_dump_json()
        assert "OCH-12345678" in json_data
        assert "testservice" in json_data


class TestTriageResult:
    """Tests for TriageResult model."""

    def test_fixable_triage_result(self):
        """Test creating a FIXABLE triage result."""
        result = TriageResult(
            incident_id="OCH-12345678",
            classification=TriageClassification.FIXABLE,
            root_cause="Nil pointer dereference when case is None",
            confidence=0.85,
            service_name="caseservice",
            file_path="backend/services/caseservice/handler.go",
            function_name="processCase",
            code_snippet="case.ID",
            suggested_fix="Add nil check before accessing case.ID",
        )

        assert result.classification == TriageClassification.FIXABLE
        assert result.confidence == 0.85
        assert result.file_path is not None

    def test_infra_issue_triage_result(self):
        """Test creating an INFRA_ISSUE triage result."""
        result = TriageResult(
            incident_id="OCH-12345678",
            classification=TriageClassification.INFRA_ISSUE,
            root_cause="AlloyDB CPU at 95%",
            confidence=0.9,
            runbook_reference="runbooks/alloydb.md",
            manual_steps=[
                "Check AlloyDB metrics in GCP Console",
                "Identify slow queries",
                "Consider scaling up",
            ],
        )

        assert result.classification == TriageClassification.INFRA_ISSUE
        assert result.runbook_reference is not None
        assert len(result.manual_steps) == 3

    def test_confidence_validation(self):
        """Test confidence score validation."""
        # Valid confidence
        result = TriageResult(
            incident_id="OCH-12345678",
            classification=TriageClassification.FIXABLE,
            root_cause="Test",
            confidence=0.5,
        )
        assert result.confidence == 0.5

        # Invalid confidence (should raise)
        with pytest.raises(ValueError):
            TriageResult(
                incident_id="OCH-12345678",
                classification=TriageClassification.FIXABLE,
                root_cause="Test",
                confidence=1.5,  # > 1.0
            )


class TestFixResult:
    """Tests for FixResult model."""

    def test_create_fix_result(self):
        """Test creating a fix result."""
        result = FixResult(
            incident_id="OCH-12345678",
            file_path="backend/services/caseservice/handler.go",
            original_code="case.ID",
            fixed_code="if case != nil { case.ID }",
            explanation="Added nil check to prevent panic",
            diff_summary="Added nil guard",
        )

        assert result.iteration == 1  # Default
        assert result.file_path.endswith(".go")

    def test_iteration_validation(self):
        """Test iteration count validation."""
        # Valid iteration
        result = FixResult(
            incident_id="OCH-12345678",
            file_path="test.go",
            original_code="x",
            fixed_code="y",
            explanation="test",
            diff_summary="test",
            iteration=3,
        )
        assert result.iteration == 3

        # Invalid iteration (should raise)
        with pytest.raises(ValueError):
            FixResult(
                incident_id="OCH-12345678",
                file_path="test.go",
                original_code="x",
                fixed_code="y",
                explanation="test",
                diff_summary="test",
                iteration=4,  # > 3
            )


class TestReviewResult:
    """Tests for ReviewResult model."""

    def test_passed_review(self):
        """Test creating a passed review result."""
        result = ReviewResult(
            passed=True,
            issues=[],
            suggestions=["Consider adding a comment"],
            summary="",
        )

        assert result.passed is True
        assert len(result.issues) == 0

    def test_failed_review(self):
        """Test creating a failed review result."""
        result = ReviewResult(
            passed=False,
            issues=[
                ReviewIssue(
                    severity="high",
                    message="Missing error handling",
                    line=42,
                    suggestion="Add error check",
                )
            ],
            suggestions=[],
            summary="CodeRabbit found 1 blocking issue",
        )

        assert result.passed is False
        assert len(result.issues) == 1
        assert result.issues[0].severity == "high"


class TestTestResult:
    """Tests for TestResult model."""

    def test_passed_tests(self):
        """Test creating a passed test result."""
        result = TestResult(
            incident_id="OCH-12345678",
            passed=True,
            unit_tests_passed=True,
            unit_tests_output="ok\tall tests passed",
            smoke_tests_passed=True,
            smoke_tests_output="smoke tests passed",
            tests_run=100,
            tests_passed=100,
            tests_failed=0,
            duration_ms=5000,
            coverage_percent=85.5,
        )

        assert result.passed is True
        assert result.tests_failed == 0

    def test_failed_tests(self):
        """Test creating a failed test result."""
        result = TestResult(
            incident_id="OCH-12345678",
            passed=False,
            unit_tests_passed=False,
            unit_tests_output="FAIL: TestCase",
            tests_run=100,
            tests_passed=95,
            tests_failed=5,
            duration_ms=4500,
        )

        assert result.passed is False
        assert result.tests_failed == 5


class TestVerificationResult:
    """Tests for VerificationResult model."""

    def test_successful_verification(self):
        """Test creating a successful verification result."""
        result = VerificationResult(
            incident_id="OCH-12345678",
            status=VerificationStatus.SUCCESS,
            message="Error completely resolved",
            errors_before=50,
            errors_after=0,
            pr_url="https://github.com/org/repo/pull/123",
        )

        assert result.status == VerificationStatus.SUCCESS
        assert result.errors_after == 0

    def test_failed_verification(self):
        """Test creating a failed verification result."""
        result = VerificationResult(
            incident_id="OCH-12345678",
            status=VerificationStatus.FAILED,
            message="Error persists",
            errors_before=50,
            errors_after=55,
        )

        assert result.status == VerificationStatus.FAILED
        assert result.errors_after > result.errors_before


class TestMetrics:
    """Tests for Metrics model."""

    def test_create_metrics(self):
        """Test creating metrics."""
        metrics = Metrics(
            total_incidents=100,
            auto_fixed=70,
            escalated=20,
            filtered=5,
            processing=5,
            mttr_seconds=300.5,
            success_rate=77.8,
        )

        assert metrics.total_incidents == 100
        assert metrics.success_rate == 77.8


class TestWebSocketEvent:
    """Tests for WebSocketEvent model."""

    def test_create_event(self):
        """Test creating a WebSocket event."""
        event = WebSocketEvent(
            type="incident_created",
            data={"incident_id": "OCH-12345678"},
        )

        assert event.type == "incident_created"
        assert event.timestamp is not None
        assert event.data["incident_id"] == "OCH-12345678"
