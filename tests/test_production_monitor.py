"""
Tests for Production Monitor Service.

Tests the production verification service that monitors Cloud Logging
after fix deployment to confirm the error is resolved.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.production_monitor import (
    ProductionMonitorService,
    ProductionMonitorError,
    MonitoringNotConfiguredError,
    MonitoringTask,
    MonitoringState,
    verify_production_fix,
)
from backend.models import Incident, Severity, IncidentStatus, VerificationStatus


@pytest.fixture
def sample_incident():
    """Create a sample incident for testing."""
    return Incident(
        id="OCH-TEST0001",
        title="Nil pointer dereference in handler",
        error_message="panic: runtime error: invalid memory address or nil pointer dereference",
        stack_trace="goroutine 1 [running]:\nmain.handler()\n\t/app/handler.go:42",
        file_path="backend/services/caseservice/handler.go",
        service_name="caseservice",
        severity=Severity.HIGH,
        tenant_name="Acme Corp",
        environment="production",
        status=IncidentStatus.ACTIVE,
        created_at=datetime(2024, 1, 15, 10, 30, 0),
    )


class TestMonitoringTaskDataclass:
    """Tests for the MonitoringTask dataclass."""

    def test_task_creation(self):
        """Test creating a MonitoringTask."""
        now = datetime.utcnow()
        task = MonitoringTask(
            incident_id="OCH-TEST0001",
            error_filter="severity>=ERROR",
            start_time=now,
            end_time=now + timedelta(hours=2),
            pr_url="https://github.com/org/repo/pull/123",
        )

        assert task.incident_id == "OCH-TEST0001"
        assert task.state == MonitoringState.PENDING
        assert task.error_counts == []
        assert task.baseline_count == 0

    def test_task_to_dict(self):
        """Test MonitoringTask to_dict method."""
        now = datetime.utcnow()
        task = MonitoringTask(
            incident_id="OCH-TEST0001",
            error_filter="severity>=ERROR",
            start_time=now,
            end_time=now + timedelta(hours=2),
            state=MonitoringState.RUNNING,
            error_counts=[5, 3, 1],
            baseline_count=10,
        )

        result = task.to_dict()

        assert result["incident_id"] == "OCH-TEST0001"
        assert result["state"] == "running"
        assert result["error_counts"] == [5, 3, 1]
        assert result["baseline_count"] == 10


class TestMonitoringState:
    """Tests for MonitoringState enum."""

    def test_state_values(self):
        """Test MonitoringState values."""
        assert MonitoringState.PENDING.value == "pending"
        assert MonitoringState.RUNNING.value == "running"
        assert MonitoringState.COMPLETED.value == "completed"
        assert MonitoringState.FAILED.value == "failed"
        assert MonitoringState.CANCELLED.value == "cancelled"


class TestProductionMonitorServiceInit:
    """Tests for ProductionMonitorService initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        with patch("backend.services.production_monitor.settings") as mock_settings:
            mock_settings.gcp_project_id = "test-project"
            mock_settings.verification_duration_hours = 2
            mock_settings.verification_check_interval_minutes = 5

            service = ProductionMonitorService()

            assert service.project_id == "test-project"
            assert service.monitoring_duration_hours == 2
            assert service.check_interval_minutes == 5

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        service = ProductionMonitorService(
            gcp_project_id="custom-project",
            monitoring_duration_hours=4,
            check_interval_minutes=10,
        )

        assert service.project_id == "custom-project"
        assert service.monitoring_duration_hours == 4
        assert service.check_interval_minutes == 10

    def test_check_configured_raises_when_not_configured(self):
        """Test that _check_configured raises when no project ID."""
        service = ProductionMonitorService(gcp_project_id="")

        with pytest.raises(MonitoringNotConfiguredError) as exc_info:
            service._check_configured()

        assert "not configured" in str(exc_info.value)

    def test_check_configured_passes_when_configured(self):
        """Test that _check_configured passes when project ID is set."""
        service = ProductionMonitorService(gcp_project_id="test-project")

        # Should not raise
        service._check_configured()


class TestBuildErrorFilter:
    """Tests for error filter building."""

    def test_build_filter_basic(self, sample_incident):
        """Test building a basic error filter."""
        service = ProductionMonitorService(gcp_project_id="test")

        result = service._build_error_filter(sample_incident)

        assert "severity>=ERROR" in result
        assert 'resource.labels.service_name="caseservice"' in result
        assert "panic" in result.lower()

    def test_build_filter_escapes_quotes(self):
        """Test that quotes in error message are escaped."""
        service = ProductionMonitorService(gcp_project_id="test")
        incident = Incident(
            id="OCH-TEST",
            title="Test",
            error_message='Error: "invalid value" received',
            service_name="testservice",
            severity=Severity.HIGH,
            status=IncidentStatus.ACTIVE,
        )

        result = service._build_error_filter(incident)

        # Should have escaped quotes
        assert '\\"' in result

    def test_build_filter_truncates_long_messages(self):
        """Test that long error messages are truncated."""
        service = ProductionMonitorService(gcp_project_id="test")
        incident = Incident(
            id="OCH-TEST",
            title="Test",
            error_message="z" * 500,  # Very long message (use 'z' to avoid filter keywords)
            service_name="testservice",
            severity=Severity.HIGH,
            status=IncidentStatus.ACTIVE,
        )

        result = service._build_error_filter(incident)

        # Filter should not contain full 500 char message
        # The message appears twice (textPayload and jsonPayload), so max 200 z's
        assert result.count("z") <= 200


class TestAnalyzeResults:
    """Tests for result analysis."""

    def test_complete_resolution(self):
        """Test analysis when error is completely resolved."""
        service = ProductionMonitorService(gcp_project_id="test")
        now = datetime.utcnow()

        task = MonitoringTask(
            incident_id="OCH-TEST",
            error_filter="test",
            start_time=now,
            end_time=now + timedelta(hours=2),
            baseline_count=20,
            error_counts=[0, 0, 0, 0],
        )

        result = service._analyze_results(task)

        assert result.status == VerificationStatus.SUCCESS
        assert "completely resolved" in result.message.lower()
        assert result.errors_before == 20
        assert result.errors_after == 0

    def test_significant_reduction(self):
        """Test analysis when error is significantly reduced (>90%)."""
        service = ProductionMonitorService(gcp_project_id="test")
        now = datetime.utcnow()

        task = MonitoringTask(
            incident_id="OCH-TEST",
            error_filter="test",
            start_time=now,
            end_time=now + timedelta(hours=2),
            baseline_count=100,
            error_counts=[3, 2, 1, 1],  # Total: 7, reduction: 93%
        )

        result = service._analyze_results(task)

        assert result.status == VerificationStatus.SUCCESS
        assert "reduced" in result.message.lower()

    def test_partial_reduction(self):
        """Test analysis when error is partially reduced."""
        service = ProductionMonitorService(gcp_project_id="test")
        now = datetime.utcnow()

        task = MonitoringTask(
            incident_id="OCH-TEST",
            error_filter="test",
            start_time=now,
            end_time=now + timedelta(hours=2),
            baseline_count=100,
            error_counts=[20, 15, 10, 10],  # Total: 55, reduction: 45%
        )

        result = service._analyze_results(task)

        assert result.status == VerificationStatus.PARTIAL
        assert "not eliminated" in result.message.lower()

    def test_error_persists(self):
        """Test analysis when error persists or increases."""
        service = ProductionMonitorService(gcp_project_id="test")
        now = datetime.utcnow()

        task = MonitoringTask(
            incident_id="OCH-TEST",
            error_filter="test",
            start_time=now,
            end_time=now + timedelta(hours=2),
            baseline_count=50,
            error_counts=[30, 25, 20, 30],  # Total: 105, increased
        )

        result = service._analyze_results(task)

        assert result.status == VerificationStatus.FAILED
        assert "persists" in result.message.lower() or "increased" in result.message.lower()

    def test_new_errors_detected(self):
        """Test analysis when no baseline but errors after."""
        service = ProductionMonitorService(gcp_project_id="test")
        now = datetime.utcnow()

        task = MonitoringTask(
            incident_id="OCH-TEST",
            error_filter="test",
            start_time=now,
            end_time=now + timedelta(hours=2),
            baseline_count=0,
            error_counts=[5, 3, 2, 1],  # New errors appeared
        )

        result = service._analyze_results(task)

        assert result.status == VerificationStatus.FAILED
        assert "new errors" in result.message.lower()


class TestStartMonitoring:
    """Tests for start_monitoring method."""

    @pytest.mark.asyncio
    async def test_start_monitoring_success(self, sample_incident):
        """Test successfully starting monitoring."""
        service = ProductionMonitorService(gcp_project_id="test-project")

        # Mock the count_errors method
        with patch.object(service, "_count_errors", return_value=10):
            now = datetime.utcnow()
            task = await service.start_monitoring(
                sample_incident,
                pr_merged_at=now,
                pr_url="https://github.com/org/repo/pull/123",
            )

        assert task.incident_id == "OCH-TEST0001"
        assert task.state == MonitoringState.RUNNING
        assert task.baseline_count == 10
        assert task.pr_url == "https://github.com/org/repo/pull/123"
        assert sample_incident.id in service._tasks

    @pytest.mark.asyncio
    async def test_start_monitoring_not_configured(self, sample_incident):
        """Test error when GCP not configured."""
        service = ProductionMonitorService(gcp_project_id="")

        with pytest.raises(MonitoringNotConfiguredError):
            await service.start_monitoring(
                sample_incident,
                pr_merged_at=datetime.utcnow(),
            )


class TestCheckStatus:
    """Tests for check_status method."""

    @pytest.mark.asyncio
    async def test_check_status_found(self, sample_incident):
        """Test checking status of existing task."""
        service = ProductionMonitorService(gcp_project_id="test")

        with patch.object(service, "_count_errors", return_value=5):
            await service.start_monitoring(sample_incident, datetime.utcnow())

        task = await service.check_status("OCH-TEST0001")

        assert task is not None
        assert task.incident_id == "OCH-TEST0001"

    @pytest.mark.asyncio
    async def test_check_status_not_found(self):
        """Test checking status of non-existent task."""
        service = ProductionMonitorService(gcp_project_id="test")

        task = await service.check_status("OCH-NONEXISTENT")

        assert task is None


class TestCollectSample:
    """Tests for collect_sample method."""

    @pytest.mark.asyncio
    async def test_collect_sample_success(self, sample_incident):
        """Test collecting a sample."""
        service = ProductionMonitorService(gcp_project_id="test")

        with patch.object(service, "_count_errors", return_value=5):
            await service.start_monitoring(sample_incident, datetime.utcnow())

        with patch.object(service, "_count_errors", return_value=3):
            count = await service.collect_sample("OCH-TEST0001")

        assert count == 3
        task = service._tasks["OCH-TEST0001"]
        assert 3 in task.error_counts

    @pytest.mark.asyncio
    async def test_collect_sample_task_not_found(self):
        """Test collecting sample for non-existent task."""
        service = ProductionMonitorService(gcp_project_id="test")

        count = await service.collect_sample("OCH-NONEXISTENT")

        assert count is None


class TestCompleteMonitoring:
    """Tests for complete_monitoring method."""

    @pytest.mark.asyncio
    async def test_complete_monitoring_success(self, sample_incident):
        """Test completing monitoring."""
        service = ProductionMonitorService(gcp_project_id="test")

        with patch.object(service, "_count_errors", return_value=10):
            await service.start_monitoring(sample_incident, datetime.utcnow())

        # Add some samples
        service._tasks["OCH-TEST0001"].error_counts = [2, 1, 0, 0]

        result = await service.complete_monitoring("OCH-TEST0001")

        assert result is not None
        assert result.incident_id == "OCH-TEST0001"
        assert result.errors_before == 10
        assert result.errors_after == 3
        assert service._tasks["OCH-TEST0001"].state == MonitoringState.COMPLETED

    @pytest.mark.asyncio
    async def test_complete_monitoring_not_found(self):
        """Test completing non-existent monitoring."""
        service = ProductionMonitorService(gcp_project_id="test")

        result = await service.complete_monitoring("OCH-NONEXISTENT")

        assert result is None


class TestCancelMonitoring:
    """Tests for cancel_monitoring method."""

    @pytest.mark.asyncio
    async def test_cancel_monitoring_success(self, sample_incident):
        """Test cancelling monitoring."""
        service = ProductionMonitorService(gcp_project_id="test")

        with patch.object(service, "_count_errors", return_value=5):
            await service.start_monitoring(sample_incident, datetime.utcnow())

        result = await service.cancel_monitoring("OCH-TEST0001")

        assert result is True
        assert service._tasks["OCH-TEST0001"].state == MonitoringState.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_monitoring_not_found(self):
        """Test cancelling non-existent monitoring."""
        service = ProductionMonitorService(gcp_project_id="test")

        result = await service.cancel_monitoring("OCH-NONEXISTENT")

        assert result is False


class TestGetActiveTasks:
    """Tests for get_active_tasks method."""

    @pytest.mark.asyncio
    async def test_get_active_tasks(self, sample_incident):
        """Test getting active tasks."""
        service = ProductionMonitorService(gcp_project_id="test")

        with patch.object(service, "_count_errors", return_value=5):
            await service.start_monitoring(sample_incident, datetime.utcnow())

        # Create another incident
        incident2 = Incident(
            id="OCH-TEST0002",
            title="Another error",
            error_message="Different error",
            service_name="otherservice",
            severity=Severity.MEDIUM,
            status=IncidentStatus.ACTIVE,
        )

        with patch.object(service, "_count_errors", return_value=3):
            await service.start_monitoring(incident2, datetime.utcnow())

        # Cancel one
        await service.cancel_monitoring("OCH-TEST0001")

        active = await service.get_active_tasks()

        assert len(active) == 1
        assert active[0].incident_id == "OCH-TEST0002"


class TestCheckHealth:
    """Tests for health check method."""

    @pytest.mark.asyncio
    async def test_health_when_configured(self):
        """Test health check when configured."""
        service = ProductionMonitorService(
            gcp_project_id="test-project",
            monitoring_duration_hours=2,
            check_interval_minutes=5,
        )

        # Mock GCP client to avoid real connection
        with patch.object(service, "_get_logging_client"):
            result = await service.check_health()

        assert result["configured"] is True
        assert result["gcp_project_id"] == "test-project"
        assert result["monitoring_duration_hours"] == 2
        assert result["check_interval_minutes"] == 5

    @pytest.mark.asyncio
    async def test_health_when_not_configured(self):
        """Test health check when not configured."""
        service = ProductionMonitorService(gcp_project_id="")

        result = await service.check_health()

        assert result["configured"] is False
        assert result["gcp_project_id"] is None


class TestCountErrorsMock:
    """Tests for mock error counting."""

    @pytest.mark.asyncio
    async def test_count_errors_mock_decreasing(self):
        """Test that mock returns decreasing errors over time."""
        service = ProductionMonitorService(gcp_project_id="test")

        start = datetime.utcnow() - timedelta(hours=2)

        count = await service._count_errors_mock("filter", start, datetime.utcnow())

        assert count == 0  # Should be 0 after 2 hours


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    @pytest.mark.asyncio
    async def test_verify_production_fix_function(self, sample_incident):
        """Test verify_production_fix convenience function."""
        with patch("backend.services.production_monitor.ProductionMonitorService") as mock_class:
            mock_service = MagicMock()

            from backend.models import VerificationResult, VerificationStatus

            mock_result = VerificationResult(
                incident_id="OCH-TEST0001",
                status=VerificationStatus.SUCCESS,
                message="Error resolved",
                errors_before=10,
                errors_after=0,
                monitoring_duration_hours=2,
            )

            async def mock_verify(*args, **kwargs):
                return mock_result

            mock_service.verify_fix = mock_verify
            mock_class.return_value = mock_service

            result = await verify_production_fix(
                sample_incident,
                pr_merged_at=datetime.utcnow(),
                pr_url="https://github.com/pull/123",
            )

            assert result.status == VerificationStatus.SUCCESS


class TestThresholds:
    """Tests for verification thresholds."""

    def test_threshold_constants(self):
        """Test that threshold constants are defined correctly."""
        assert ProductionMonitorService.COMPLETE_RESOLUTION_THRESHOLD == 0
        assert ProductionMonitorService.SIGNIFICANT_REDUCTION_THRESHOLD == 0.1


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_count_errors_handles_gcp_import_error(self):
        """Test handling when google-cloud-logging not installed."""
        service = ProductionMonitorService(gcp_project_id="test")

        with patch.dict("sys.modules", {"google.cloud": None}):
            with patch.object(
                service,
                "_get_logging_client",
                side_effect=MonitoringNotConfiguredError("google-cloud-logging not installed"),
            ):
                # The error is wrapped in ProductionMonitorError
                with pytest.raises(ProductionMonitorError) as exc_info:
                    await service._count_errors("filter", datetime.utcnow(), datetime.utcnow())

                assert "not installed" in str(exc_info.value)
