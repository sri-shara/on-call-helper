"""
Tests for PagerDuty Service.

Tests the PagerDuty Events API v2 integration for incident notifications.
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from backend.services.pagerduty import (
    PagerDutyService,
    PagerDutyError,
    PagerDutyConfigError,
    PagerDutyAPIError,
    PagerDutyEvent,
    EventAction,
    PagerDutySeverity,
    trigger_incident,
    resolve_incident,
    escalate_incident,
)
from backend.models import Incident, Severity, IncidentStatus


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


class TestPagerDutyEventDataclass:
    """Tests for the PagerDutyEvent dataclass."""

    def test_event_creation(self):
        """Test creating a PagerDutyEvent."""
        event = PagerDutyEvent(
            status="success",
            message="Event processed",
            dedup_key="oncall-helper-OCH-TEST0001",
        )

        assert event.status == "success"
        assert event.message == "Event processed"
        assert event.dedup_key == "oncall-helper-OCH-TEST0001"

    def test_event_to_dict(self):
        """Test PagerDutyEvent to_dict method."""
        event = PagerDutyEvent(
            status="success",
            message="Event processed",
            dedup_key="oncall-helper-OCH-TEST0001",
        )

        result = event.to_dict()

        assert result["status"] == "success"
        assert result["message"] == "Event processed"
        assert result["dedup_key"] == "oncall-helper-OCH-TEST0001"


class TestPagerDutyServiceInit:
    """Tests for PagerDutyService initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default settings."""
        with patch("backend.services.pagerduty.settings") as mock_settings:
            mock_settings.pagerduty_routing_key = "test-routing-key"

            service = PagerDutyService()

            assert service.routing_key == "test-routing-key"
            assert service.timeout == 30.0

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        service = PagerDutyService(
            routing_key="custom-key",
            timeout=60.0,
        )

        assert service.routing_key == "custom-key"
        assert service.timeout == 60.0

    def test_check_configured_raises_when_not_configured(self):
        """Test that _check_configured raises when no routing key."""
        service = PagerDutyService(routing_key="")

        with pytest.raises(PagerDutyConfigError) as exc_info:
            service._check_configured()

        assert "not configured" in str(exc_info.value)

    def test_check_configured_passes_when_configured(self):
        """Test that _check_configured passes when routing key is set."""
        service = PagerDutyService(routing_key="test-key")

        # Should not raise
        service._check_configured()


class TestSeverityMapping:
    """Tests for severity mapping."""

    def test_map_critical_severity(self):
        """Test mapping critical severity."""
        service = PagerDutyService(routing_key="test")

        result = service._map_severity(Severity.CRITICAL)

        assert result == "critical"

    def test_map_high_severity(self):
        """Test mapping high severity."""
        service = PagerDutyService(routing_key="test")

        result = service._map_severity(Severity.HIGH)

        assert result == "error"

    def test_map_medium_severity(self):
        """Test mapping medium severity."""
        service = PagerDutyService(routing_key="test")

        result = service._map_severity(Severity.MEDIUM)

        assert result == "warning"

    def test_map_low_severity(self):
        """Test mapping low severity."""
        service = PagerDutyService(routing_key="test")

        result = service._map_severity(Severity.LOW)

        assert result == "info"


class TestGenerateDedupKey:
    """Tests for dedup key generation."""

    def test_generate_dedup_key_basic(self):
        """Test basic dedup key generation."""
        service = PagerDutyService(routing_key="test")

        result = service._generate_dedup_key("OCH-12345678")

        assert result == "oncall-helper-OCH-12345678"

    def test_generate_dedup_key_with_suffix(self):
        """Test dedup key generation with suffix."""
        service = PagerDutyService(routing_key="test")

        result = service._generate_dedup_key("OCH-12345678", "escalation")

        assert result == "oncall-helper-OCH-12345678-escalation"


class TestSendEvent:
    """Tests for _send_event method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = PagerDutyService(routing_key="test-routing-key")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_send_event_success(self, service, mock_client):
        """Test successful event sending."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "oncall-helper-test",
        }
        mock_client.post.return_value = mock_response

        result = await service._send_event(
            EventAction.TRIGGER,
            "oncall-helper-test",
            {"summary": "Test", "severity": "error", "source": "test"},
        )

        assert result.status == "success"
        assert result.dedup_key == "oncall-helper-test"

    @pytest.mark.asyncio
    async def test_send_event_not_configured(self):
        """Test error when not configured."""
        service = PagerDutyService(routing_key="")

        with pytest.raises(PagerDutyConfigError):
            await service._send_event(EventAction.TRIGGER, "test", {})

    @pytest.mark.asyncio
    async def test_send_event_timeout(self, service, mock_client):
        """Test timeout handling."""
        mock_client.post.side_effect = httpx.TimeoutException("Timeout")

        with pytest.raises(PagerDutyAPIError) as exc_info:
            await service._send_event(EventAction.TRIGGER, "test", {})

        assert "timed out" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_event_request_error(self, service, mock_client):
        """Test request error handling."""
        mock_client.post.side_effect = httpx.RequestError("Connection failed")

        with pytest.raises(PagerDutyAPIError) as exc_info:
            await service._send_event(EventAction.TRIGGER, "test", {})

        assert "request failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_event_bad_request(self, service, mock_client):
        """Test 400 bad request handling."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid payload"
        mock_client.post.return_value = mock_response

        with pytest.raises(PagerDutyAPIError) as exc_info:
            await service._send_event(EventAction.TRIGGER, "test", {})

        assert exc_info.value.status_code == 400
        assert "Invalid" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_event_rate_limited(self, service, mock_client):
        """Test 429 rate limit handling."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"
        mock_client.post.return_value = mock_response

        with pytest.raises(PagerDutyAPIError) as exc_info:
            await service._send_event(EventAction.TRIGGER, "test", {})

        assert exc_info.value.status_code == 429
        assert "rate limit" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_send_event_server_error(self, service, mock_client):
        """Test 500 server error handling."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client.post.return_value = mock_response

        with pytest.raises(PagerDutyAPIError) as exc_info:
            await service._send_event(EventAction.TRIGGER, "test", {})

        assert exc_info.value.status_code == 500


class TestTrigger:
    """Tests for trigger method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = PagerDutyService(routing_key="test-routing-key")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_trigger_success(self, service, mock_client, sample_incident):
        """Test successful incident trigger."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "oncall-helper-OCH-TEST0001",
        }
        mock_client.post.return_value = mock_response

        result = await service.trigger(sample_incident)

        assert result.status == "success"
        assert "OCH-TEST0001" in result.dedup_key

        # Verify payload structure
        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["event_action"] == "trigger"
        assert payload["routing_key"] == "test-routing-key"
        assert "summary" in payload["payload"]
        assert "severity" in payload["payload"]

    @pytest.mark.asyncio
    async def test_trigger_includes_incident_details(self, service, mock_client, sample_incident):
        """Test that trigger includes all incident details."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "test",
        }
        mock_client.post.return_value = mock_response

        await service.trigger(sample_incident)

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]["payload"]

        assert sample_incident.title in payload["summary"]
        assert payload["component"] == "caseservice"
        assert payload["custom_details"]["incident_id"] == "OCH-TEST0001"
        assert payload["custom_details"]["service"] == "caseservice"
        assert payload["custom_details"]["tenant"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_trigger_with_custom_details(self, service, mock_client, sample_incident):
        """Test trigger with additional custom details."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "test",
        }
        mock_client.post.return_value = mock_response

        await service.trigger(sample_incident, custom_details={"extra_info": "test"})

        call_args = mock_client.post.call_args
        custom = call_args[1]["json"]["payload"]["custom_details"]

        assert custom["extra_info"] == "test"


class TestAcknowledge:
    """Tests for acknowledge method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = PagerDutyService(routing_key="test-routing-key")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_acknowledge_success(self, service, mock_client):
        """Test successful acknowledgment."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "oncall-helper-OCH-TEST0001",
        }
        mock_client.post.return_value = mock_response

        result = await service.acknowledge("OCH-TEST0001")

        assert result.status == "success"

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["event_action"] == "acknowledge"
        assert payload["dedup_key"] == "oncall-helper-OCH-TEST0001"


class TestResolve:
    """Tests for resolve method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = PagerDutyService(routing_key="test-routing-key")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_resolve_success(self, service, mock_client):
        """Test successful resolution."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "oncall-helper-OCH-TEST0001",
        }
        mock_client.post.return_value = mock_response

        result = await service.resolve("OCH-TEST0001", pr_url="https://github.com/org/repo/pull/123")

        assert result.status == "success"

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["event_action"] == "resolve"
        assert payload["dedup_key"] == "oncall-helper-OCH-TEST0001"


class TestEscalate:
    """Tests for escalate method."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = PagerDutyService(routing_key="test-routing-key")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_escalate_success(self, service, mock_client):
        """Test successful escalation."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "oncall-helper-OCH-TEST0001-escalation",
        }
        mock_client.post.return_value = mock_response

        result = await service.escalate("OCH-TEST0001", "Fix generation failed after 3 attempts")

        assert result.status == "success"
        assert "escalation" in result.dedup_key

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["event_action"] == "trigger"
        assert "ESCALATION" in payload["payload"]["summary"]
        assert payload["payload"]["custom_details"]["original_incident_id"] == "OCH-TEST0001"

    @pytest.mark.asyncio
    async def test_escalate_uses_high_severity_by_default(self, service, mock_client):
        """Test that escalation uses high severity by default."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "test",
        }
        mock_client.post.return_value = mock_response

        await service.escalate("OCH-TEST0001", "Test escalation")

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]["payload"]
        assert payload["severity"] == "error"  # HIGH maps to error


class TestNotificationMethods:
    """Tests for notification helper methods."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock HTTP client."""
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def service(self, mock_client):
        """Create a service with mocked client."""
        service = PagerDutyService(routing_key="test-routing-key")
        service._client = mock_client
        mock_client.is_closed = False
        return service

    @pytest.mark.asyncio
    async def test_notify_triage_complete(self, service, mock_client):
        """Test triage complete notification."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "test",
        }
        mock_client.post.return_value = mock_response

        result = await service.notify_triage_complete(
            "OCH-TEST0001",
            classification="fixable",
            confidence=0.95,
            root_cause="Nil pointer dereference",
        )

        assert result.status == "success"

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["event_action"] == "acknowledge"

    @pytest.mark.asyncio
    async def test_notify_fix_generated(self, service, mock_client):
        """Test fix generated notification."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "test",
        }
        mock_client.post.return_value = mock_response

        result = await service.notify_fix_generated(
            "OCH-TEST0001",
            file_path="backend/handler.go",
            iteration=1,
        )

        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_notify_tests_passed(self, service, mock_client):
        """Test tests passed notification."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "status": "success",
            "message": "Event processed",
            "dedup_key": "test",
        }
        mock_client.post.return_value = mock_response

        result = await service.notify_tests_passed(
            "OCH-TEST0001",
            tests_run=10,
            tests_passed=10,
        )

        assert result.status == "success"


class TestCheckHealth:
    """Tests for health check method."""

    @pytest.mark.asyncio
    async def test_health_when_configured(self):
        """Test health check when configured."""
        service = PagerDutyService(routing_key="test-key")

        result = await service.check_health()

        assert result["configured"] is True
        assert result["routing_key_set"] is True
        assert "events.pagerduty.com" in result["events_api_url"]

    @pytest.mark.asyncio
    async def test_health_when_not_configured(self):
        """Test health check when not configured."""
        service = PagerDutyService(routing_key="")

        result = await service.check_health()

        assert result["configured"] is False
        assert result["routing_key_set"] is False


class TestClientManagement:
    """Tests for HTTP client management."""

    @pytest.mark.asyncio
    async def test_close_client(self):
        """Test closing the client."""
        service = PagerDutyService(routing_key="test")

        mock_client = AsyncMock()
        mock_client.is_closed = False
        service._client = mock_client

        await service.close()

        mock_client.aclose.assert_called_once()
        assert service._client is None


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    @pytest.mark.asyncio
    async def test_trigger_incident_function(self, sample_incident):
        """Test trigger_incident convenience function."""
        with patch("backend.services.pagerduty.PagerDutyService") as mock_class:
            mock_service = AsyncMock()
            mock_event = PagerDutyEvent(
                status="success",
                message="Event processed",
                dedup_key="test",
            )
            mock_service.trigger.return_value = mock_event
            mock_class.return_value = mock_service

            result = await trigger_incident(sample_incident)

            assert result.status == "success"
            mock_service.trigger.assert_called_once_with(sample_incident)
            mock_service.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_incident_function(self):
        """Test resolve_incident convenience function."""
        with patch("backend.services.pagerduty.PagerDutyService") as mock_class:
            mock_service = AsyncMock()
            mock_event = PagerDutyEvent(
                status="success",
                message="Event processed",
                dedup_key="test",
            )
            mock_service.resolve.return_value = mock_event
            mock_class.return_value = mock_service

            result = await resolve_incident("OCH-TEST0001", pr_url="https://github.com/pull/1")

            assert result.status == "success"
            mock_service.resolve.assert_called_once_with("OCH-TEST0001", pr_url="https://github.com/pull/1")
            mock_service.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_escalate_incident_function(self):
        """Test escalate_incident convenience function."""
        with patch("backend.services.pagerduty.PagerDutyService") as mock_class:
            mock_service = AsyncMock()
            mock_event = PagerDutyEvent(
                status="success",
                message="Event processed",
                dedup_key="test",
            )
            mock_service.escalate.return_value = mock_event
            mock_class.return_value = mock_service

            result = await escalate_incident("OCH-TEST0001", "Failed to generate fix")

            assert result.status == "success"
            mock_service.escalate.assert_called_once_with("OCH-TEST0001", "Failed to generate fix")
            mock_service.close.assert_called_once()


class TestEventAction:
    """Tests for EventAction enum."""

    def test_event_actions(self):
        """Test EventAction values."""
        assert EventAction.TRIGGER.value == "trigger"
        assert EventAction.ACKNOWLEDGE.value == "acknowledge"
        assert EventAction.RESOLVE.value == "resolve"


class TestPagerDutySeverity:
    """Tests for PagerDutySeverity enum."""

    def test_severity_values(self):
        """Test PagerDutySeverity values."""
        assert PagerDutySeverity.CRITICAL.value == "critical"
        assert PagerDutySeverity.ERROR.value == "error"
        assert PagerDutySeverity.WARNING.value == "warning"
        assert PagerDutySeverity.INFO.value == "info"
