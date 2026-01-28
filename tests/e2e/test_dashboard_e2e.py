"""
End-to-end tests for the dashboard WebSocket integration.

Tests real-time event flow from backend to frontend via WebSocket.
"""

import pytest
import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.websocket_manager import (
    ws_manager,
    EventType,
    WebSocketMessage,
    WebSocketManager,
)
from backend.models import Incident, IncidentStatus
from backend.storage import storage


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def sample_incident():
    """Create a sample incident."""
    return Incident(
        id="INC-WS-001",
        title="Test error for WebSocket",
        error_message="Test error message",
        service_name="testservice",
        severity="medium",
        status=IncidentStatus.ACTIVE,
        created_at=datetime.utcnow(),
    )


@pytest.fixture
def ws_manager_test():
    """Create a fresh WebSocketManager for testing."""
    return WebSocketManager()


class TestWebSocketEventBroadcast:
    """Test WebSocket event broadcasting."""

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all_clients(self, ws_manager_test):
        """Test that broadcast sends to all connected clients."""
        # Create mock WebSockets
        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.send_text = AsyncMock()

        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.send_text = AsyncMock()

        # Connect clients
        client_id_1 = await ws_manager_test.connect(mock_ws1)
        client_id_2 = await ws_manager_test.connect(mock_ws2)

        # Reset send_text calls (from welcome message)
        mock_ws1.send_text.reset_mock()
        mock_ws2.send_text.reset_mock()

        # Broadcast event
        sent_count = await ws_manager_test.broadcast(
            EventType.INCIDENT_CREATED,
            {"incident_id": "INC-001", "title": "Test incident"},
        )

        # Verify both received the message
        assert sent_count == 2
        assert mock_ws1.send_text.called
        assert mock_ws2.send_text.called

        # Verify message content
        call_args_1 = json.loads(mock_ws1.send_text.call_args[0][0])
        assert call_args_1["type"] == "incident_created"
        assert call_args_1["data"]["incident_id"] == "INC-001"

    @pytest.mark.asyncio
    async def test_broadcast_handles_disconnected_client(self, ws_manager_test):
        """Test that broadcast handles disconnected clients gracefully."""
        # Create mock WebSocket that fails on send
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock(side_effect=Exception("Connection closed"))

        # Connect
        client_id = await ws_manager_test.connect(mock_ws)
        mock_ws.send_text.reset_mock()

        # Broadcast should not raise
        sent_count = await ws_manager_test.broadcast(
            EventType.INCIDENT_CREATED,
            {"incident_id": "test"},
        )

        # Connection should be removed after failed send
        assert sent_count == 0
        assert ws_manager_test.connection_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_with_exclude(self, ws_manager_test):
        """Test broadcast with excluded client."""
        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws1.send_text = AsyncMock()

        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        mock_ws2.send_text = AsyncMock()

        client_id_1 = await ws_manager_test.connect(mock_ws1)
        client_id_2 = await ws_manager_test.connect(mock_ws2)

        mock_ws1.send_text.reset_mock()
        mock_ws2.send_text.reset_mock()

        # Broadcast excluding client 1
        sent_count = await ws_manager_test.broadcast(
            EventType.TRIAGE_COMPLETE,
            {"incident_id": "INC-001"},
            exclude_client=client_id_1,
        )

        # Only client 2 should receive
        assert sent_count == 1
        assert not mock_ws1.send_text.called
        assert mock_ws2.send_text.called


class TestWebSocketHelperMethods:
    """Test WebSocket helper broadcast methods."""

    @pytest.mark.asyncio
    async def test_broadcast_incident_created(self, ws_manager_test):
        """Test broadcast_incident_created helper."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()

        await ws_manager_test.connect(mock_ws)
        mock_ws.send_text.reset_mock()

        await ws_manager_test.broadcast_incident_created(
            incident_id="INC-001",
            title="Test incident",
            service="testservice",
            severity="high",
        )

        assert mock_ws.send_text.called
        message = json.loads(mock_ws.send_text.call_args[0][0])
        assert message["type"] == "incident_created"
        assert message["data"]["incident_id"] == "INC-001"

    @pytest.mark.asyncio
    async def test_broadcast_agent_thinking(self, ws_manager_test):
        """Test broadcast_agent_thinking helper."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()

        await ws_manager_test.connect(mock_ws)
        mock_ws.send_text.reset_mock()

        await ws_manager_test.broadcast_agent_thinking(
            incident_id="INC-001",
            agent="triage",
            message="Analyzing stack trace...",
        )

        assert mock_ws.send_text.called
        message = json.loads(mock_ws.send_text.call_args[0][0])
        assert message["type"] == "agent_thinking"
        assert message["data"]["agent"] == "triage"
        assert message["data"]["message"] == "Analyzing stack trace..."

    @pytest.mark.asyncio
    async def test_broadcast_pr_created(self, ws_manager_test):
        """Test broadcast_pr_created helper."""
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.send_text = AsyncMock()

        await ws_manager_test.connect(mock_ws)
        mock_ws.send_text.reset_mock()

        await ws_manager_test.broadcast_pr_created(
            incident_id="INC-001",
            pr_number=123,
            pr_url="https://github.com/org/repo/pull/123",
        )

        assert mock_ws.send_text.called
        message = json.loads(mock_ws.send_text.call_args[0][0])
        assert message["type"] == "pr_created"
        assert message["data"]["pr_number"] == 123
        assert message["data"]["pr_url"] == "https://github.com/org/repo/pull/123"


class TestDashboardDataFlow:
    """Test data flow from backend to dashboard."""

    @pytest.mark.asyncio
    async def test_incident_lifecycle_events(self, ws_manager_test, sample_incident):
        """Test complete incident lifecycle event flow."""
        received_events = []
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()

        async def capture_send(data):
            received_events.append(json.loads(data))

        mock_ws.send_text = capture_send

        await ws_manager_test.connect(mock_ws)

        # Clear welcome message
        received_events.clear()

        # Simulate incident lifecycle events
        await ws_manager_test.broadcast_incident_created(
            incident_id=sample_incident.id,
            title=sample_incident.title,
            service=sample_incident.service_name,
            severity=sample_incident.severity,
        )

        await ws_manager_test.broadcast(
            EventType.TRIAGE_STARTED,
            {"incident_id": sample_incident.id},
        )

        await ws_manager_test.broadcast_agent_thinking(
            incident_id=sample_incident.id,
            agent="triage",
            message="Analyzing error...",
        )

        await ws_manager_test.broadcast_triage_complete(
            incident_id=sample_incident.id,
            classification="fixable",
            confidence=0.92,
            root_cause="Null pointer dereference",
        )

        await ws_manager_test.broadcast(
            EventType.FIX_STARTED,
            {"incident_id": sample_incident.id},
        )

        await ws_manager_test.broadcast_fix_generated(
            incident_id=sample_incident.id,
            file_path="handler.go",
            diff_summary="+5 -2 lines",
            iteration=1,
        )

        await ws_manager_test.broadcast_pr_created(
            incident_id=sample_incident.id,
            pr_number=123,
            pr_url="https://github.com/org/repo/pull/123",
        )

        await ws_manager_test.broadcast_incident_resolved(
            incident_id=sample_incident.id,
            pr_url="https://github.com/org/repo/pull/123",
        )

        # Verify event sequence
        event_types = [e["type"] for e in received_events]
        assert "incident_created" in event_types
        assert "triage_started" in event_types
        assert "agent_thinking" in event_types
        assert "triage_complete" in event_types
        assert "fix_started" in event_types
        assert "fix_generated" in event_types
        assert "pr_created" in event_types
        assert "incident_resolved" in event_types


class TestAPIEndpoints:
    """Test REST API endpoints for dashboard."""

    def test_get_incidents_list(self, client, sample_incident):
        """Test getting list of incidents."""
        storage.save_incident(sample_incident)

        response = client.get("/incidents")
        assert response.status_code == 200

        data = response.json()
        assert "incidents" in data

    def test_get_incident_details(self, client, sample_incident):
        """Test getting incident details."""
        storage.save_incident(sample_incident)

        response = client.get(f"/incidents/{sample_incident.id}")
        assert response.status_code == 200

        data = response.json()
        # The response might be wrapped or direct - handle both
        incident_data = data.get("incident", data)
        assert incident_data.get("id") == sample_incident.id

    def test_get_metrics(self, client):
        """Test getting metrics endpoint."""
        response = client.get("/metrics")
        assert response.status_code == 200

        data = response.json()
        assert "total_incidents" in data
        # Check for actual field names from the API
        assert "auto_fixed" in data or "fixed_count" in data
        assert "escalated" in data or "escalated_count" in data

    def test_health_endpoint(self, client):
        """Test health endpoint for frontend to check backend status."""
        response = client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"

    def test_websocket_connections_endpoint(self, client):
        """Test endpoint showing WebSocket connection count."""
        response = client.get("/ws/connections")
        assert response.status_code == 200

        data = response.json()
        # Check for actual field names from the API
        assert "count" in data or "active_connections" in data


class TestEventTypeValues:
    """Test that EventType enum has correct values."""

    def test_incident_event_types(self):
        """Test incident-related event types."""
        assert EventType.INCIDENT_CREATED.value == "incident_created"
        assert EventType.INCIDENT_UPDATED.value == "incident_updated"
        assert EventType.INCIDENT_RESOLVED.value == "incident_resolved"
        assert EventType.INCIDENT_ESCALATED.value == "incident_escalated"

    def test_pipeline_event_types(self):
        """Test pipeline stage event types."""
        assert EventType.TRIAGE_STARTED.value == "triage_started"
        assert EventType.TRIAGE_COMPLETE.value == "triage_complete"
        assert EventType.FIX_STARTED.value == "fix_started"
        assert EventType.FIX_GENERATED.value == "fix_generated"
        assert EventType.REVIEW_STARTED.value == "review_started"
        assert EventType.REVIEW_COMPLETE.value == "review_complete"
        assert EventType.PR_CREATED.value == "pr_created"

    def test_agent_event_types(self):
        """Test agent-related event types."""
        assert EventType.AGENT_THINKING.value == "agent_thinking"
        assert EventType.CODE_DIFF.value == "code_diff"


class TestWebSocketMessageSerialization:
    """Test WebSocketMessage serialization."""

    def test_message_to_json(self):
        """Test message serialization to JSON."""
        message = WebSocketMessage(
            type=EventType.INCIDENT_CREATED,
            data={"incident_id": "INC-001", "title": "Test"},
        )

        json_str = message.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "incident_created"
        assert parsed["data"]["incident_id"] == "INC-001"
        assert "timestamp" in parsed

    def test_message_from_dict(self):
        """Test message creation from dict."""
        data = {
            "type": "incident_created",
            "data": {"incident_id": "INC-001"},
            "timestamp": "2024-01-15T10:30:00",
        }

        message = WebSocketMessage.from_dict(data)

        assert message.type == EventType.INCIDENT_CREATED
        assert message.data["incident_id"] == "INC-001"
