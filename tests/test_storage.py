"""
Tests for in-memory storage.

Verifies storage operations and metrics calculations.
"""

import pytest
from datetime import datetime, timedelta

from backend.storage import Storage
from backend.models import (
    Incident,
    IncidentStatus,
    Severity,
    TriageResult,
    TriageClassification,
    FixResult,
    TestResult,
)


@pytest.fixture
def storage():
    """Create a fresh storage instance for each test."""
    s = Storage()
    yield s
    s.clear()


@pytest.fixture
def sample_incident():
    """Create a sample incident for testing."""
    return Incident(
        id="OCH-12345678",
        title="Test error",
        error_message="Test message",
        service_name="testservice",
        severity=Severity.HIGH,
        gcp_insert_id="gcp-123",
    )


class TestIncidentStorage:
    """Tests for incident storage operations."""

    def test_save_and_get_incident(self, storage, sample_incident):
        """Test saving and retrieving an incident."""
        storage.save_incident(sample_incident)

        retrieved = storage.get_incident(sample_incident.id)
        assert retrieved is not None
        assert retrieved.id == sample_incident.id
        assert retrieved.title == sample_incident.title

    def test_get_nonexistent_incident(self, storage):
        """Test getting an incident that doesn't exist."""
        result = storage.get_incident("nonexistent")
        assert result is None

    def test_update_incident_status(self, storage, sample_incident):
        """Test updating an incident's status."""
        storage.save_incident(sample_incident)

        updated = storage.update_incident_status(
            sample_incident.id,
            IncidentStatus.FIXED,
            resolved_at=datetime.utcnow(),
        )

        assert updated is not None
        assert updated.status == IncidentStatus.FIXED
        assert updated.resolved_at is not None

    def test_list_incidents(self, storage):
        """Test listing incidents."""
        # Add multiple incidents
        for i in range(5):
            incident = Incident(
                id=f"OCH-{i:08d}",
                title=f"Test error {i}",
                error_message="Test",
                service_name="testservice",
                severity=Severity.MEDIUM,
            )
            storage.save_incident(incident)

        incidents = storage.list_incidents()
        assert len(incidents) == 5

    def test_list_incidents_with_status_filter(self, storage):
        """Test listing incidents filtered by status."""
        # Add incidents with different statuses
        for i, status in enumerate([
            IncidentStatus.ACTIVE,
            IncidentStatus.FIXED,
            IncidentStatus.FIXED,
            IncidentStatus.ESCALATED,
        ]):
            incident = Incident(
                id=f"OCH-{i:08d}",
                title=f"Test error {i}",
                error_message="Test",
                service_name="testservice",
                severity=Severity.MEDIUM,
                status=status,
            )
            storage.save_incident(incident)

        fixed = storage.list_incidents(status=IncidentStatus.FIXED)
        assert len(fixed) == 2

        escalated = storage.list_incidents(status=IncidentStatus.ESCALATED)
        assert len(escalated) == 1

    def test_list_incidents_with_limit(self, storage):
        """Test listing incidents with limit."""
        for i in range(10):
            incident = Incident(
                id=f"OCH-{i:08d}",
                title=f"Test error {i}",
                error_message="Test",
                service_name="testservice",
                severity=Severity.MEDIUM,
            )
            storage.save_incident(incident)

        incidents = storage.list_incidents(limit=5)
        assert len(incidents) == 5

    def test_is_duplicate(self, storage, sample_incident):
        """Test duplicate detection."""
        assert not storage.is_duplicate("gcp-123")

        storage.save_incident(sample_incident)

        assert storage.is_duplicate("gcp-123")
        assert not storage.is_duplicate("gcp-456")


class TestTriageResultStorage:
    """Tests for triage result storage."""

    def test_save_and_get_triage_result(self, storage):
        """Test saving and retrieving a triage result."""
        result = TriageResult(
            incident_id="OCH-12345678",
            classification=TriageClassification.FIXABLE,
            root_cause="Test root cause",
            confidence=0.8,
        )

        storage.save_triage_result(result)

        retrieved = storage.get_triage_result("OCH-12345678")
        assert retrieved is not None
        assert retrieved.root_cause == "Test root cause"

    def test_get_nonexistent_triage_result(self, storage):
        """Test getting a triage result that doesn't exist."""
        result = storage.get_triage_result("nonexistent")
        assert result is None


class TestFixResultStorage:
    """Tests for fix result storage."""

    def test_save_and_get_fix_result(self, storage):
        """Test saving and retrieving a fix result."""
        result = FixResult(
            incident_id="OCH-12345678",
            file_path="test.go",
            original_code="x",
            fixed_code="y",
            explanation="test",
            diff_summary="test",
        )

        storage.save_fix_result(result)

        retrieved = storage.get_fix_result("OCH-12345678")
        assert retrieved is not None
        assert retrieved.file_path == "test.go"


class TestTestResultStorage:
    """Tests for test result storage."""

    def test_save_and_get_test_result(self, storage):
        """Test saving and retrieving a test result."""
        result = TestResult(
            incident_id="OCH-12345678",
            passed=True,
            tests_run=100,
            tests_passed=100,
            tests_failed=0,
            duration_ms=5000,
        )

        storage.save_test_result(result)

        retrieved = storage.get_test_result("OCH-12345678")
        assert retrieved is not None
        assert retrieved.passed is True


class TestMetrics:
    """Tests for metrics calculation."""

    def test_empty_metrics(self, storage):
        """Test metrics with no incidents."""
        metrics = storage.get_metrics()

        assert metrics.total_incidents == 0
        assert metrics.auto_fixed == 0
        assert metrics.escalated == 0
        assert metrics.mttr_seconds is None
        assert metrics.success_rate is None

    def test_metrics_calculation(self, storage):
        """Test metrics calculation with various incidents."""
        # Add incidents with different statuses
        statuses = [
            IncidentStatus.FIXED,
            IncidentStatus.FIXED,
            IncidentStatus.FIXED,
            IncidentStatus.ESCALATED,
            IncidentStatus.FILTERED,
            IncidentStatus.TRIAGING,  # Processing state
        ]

        for i, status in enumerate(statuses):
            incident = Incident(
                id=f"OCH-{i:08d}",
                title=f"Test {i}",
                error_message="Test",
                service_name="test",
                severity=Severity.MEDIUM,
                status=status,
            )
            storage.save_incident(incident)

        metrics = storage.get_metrics()

        assert metrics.total_incidents == 6
        assert metrics.auto_fixed == 3
        assert metrics.escalated == 1
        assert metrics.filtered == 1
        assert metrics.processing == 1  # PROCESSING status

    def test_mttr_calculation(self, storage):
        """Test MTTR calculation."""
        # Create and resolve an incident
        incident = Incident(
            id="OCH-12345678",
            title="Test",
            error_message="Test",
            service_name="test",
            severity=Severity.MEDIUM,
            created_at=datetime.utcnow() - timedelta(minutes=5),
        )
        storage.save_incident(incident)

        # Resolve it
        storage.update_incident_status(
            incident.id,
            IncidentStatus.FIXED,
            resolved_at=datetime.utcnow(),
        )

        metrics = storage.get_metrics()

        # MTTR should be approximately 5 minutes (300 seconds)
        assert metrics.mttr_seconds is not None
        assert 290 <= metrics.mttr_seconds <= 310

    def test_success_rate_calculation(self, storage):
        """Test success rate calculation."""
        # 3 fixed, 1 escalated = 75% success rate
        for i in range(3):
            incident = Incident(
                id=f"OCH-FIXED{i:03d}",
                title="Fixed",
                error_message="Test",
                service_name="test",
                severity=Severity.MEDIUM,
                status=IncidentStatus.FIXED,
            )
            storage.save_incident(incident)

        escalated = Incident(
            id="OCH-ESCALATED",
            title="Escalated",
            error_message="Test",
            service_name="test",
            severity=Severity.MEDIUM,
            status=IncidentStatus.ESCALATED,
        )
        storage.save_incident(escalated)

        metrics = storage.get_metrics()

        assert metrics.success_rate == 75.0


class TestStorageClear:
    """Tests for storage clear operation."""

    def test_clear(self, storage, sample_incident):
        """Test clearing all storage."""
        storage.save_incident(sample_incident)
        assert len(storage.list_incidents()) == 1

        storage.clear()

        assert len(storage.list_incidents()) == 0
        assert not storage.is_duplicate(sample_incident.gcp_insert_id)
