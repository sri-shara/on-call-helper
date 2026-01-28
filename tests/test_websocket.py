"""
Tests for WebSocket Manager.

Tests the WebSocket connection management and event broadcasting
for real-time dashboard updates.
"""

import pytest
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from backend.websocket_manager import (
    WebSocketManager,
    WebSocketMessage,
    ConnectionInfo,
    EventType,
    ws_manager,
    create_pipeline_event_callback,
)


class TestEventType:
    """Tests for EventType enum."""

    def test_event_type_values(self):
        """Test event type enum values."""
        assert EventType.WELCOME.value == "welcome"
        assert EventType.INCIDENT_CREATED.value == "incident_created"
        assert EventType.TRIAGE_COMPLETE.value == "triage_complete"
        assert EventType.FIX_GENERATED.value == "fix_generated"
        assert EventType.CODE_DIFF.value == "code_diff"
        assert EventType.PR_CREATED.value == "pr_created"
        assert EventType.INCIDENT_RESOLVED.value == "incident_resolved"
        assert EventType.INCIDENT_ESCALATED.value == "incident_escalated"


class TestWebSocketMessage:
    """Tests for WebSocketMessage dataclass."""

    def test_message_creation(self):
        """Test creating a WebSocket message."""
        msg = WebSocketMessage(
            type=EventType.INCIDENT_CREATED,
            data={"incident_id": "OCH-TEST"},
        )

        assert msg.type == EventType.INCIDENT_CREATED
        assert msg.data["incident_id"] == "OCH-TEST"
        assert msg.timestamp is not None

    def test_message_to_json(self):
        """Test serializing message to JSON."""
        msg = WebSocketMessage(
            type=EventType.TRIAGE_COMPLETE,
            data={"classification": "fixable", "confidence": 0.85},
        )

        json_str = msg.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "triage_complete"
        assert parsed["data"]["classification"] == "fixable"
        assert parsed["data"]["confidence"] == 0.85
        assert "timestamp" in parsed

    def test_message_from_dict(self):
        """Test creating message from dictionary."""
        data = {
            "type": "incident_created",
            "data": {"incident_id": "OCH-123"},
            "timestamp": "2024-01-15T10:30:00",
        }

        msg = WebSocketMessage.from_dict(data)

        assert msg.type == EventType.INCIDENT_CREATED
        assert msg.data["incident_id"] == "OCH-123"


class TestConnectionInfo:
    """Tests for ConnectionInfo dataclass."""

    def test_connection_info_creation(self):
        """Test creating connection info."""
        mock_ws = MagicMock()
        conn = ConnectionInfo(
            client_id="abc123",
            websocket=mock_ws,
        )

        assert conn.client_id == "abc123"
        assert conn.websocket is mock_ws
        assert conn.connected_at is not None
        assert conn.last_ping is None

    def test_connection_info_to_dict(self):
        """Test converting connection info to dict."""
        mock_ws = MagicMock()
        conn = ConnectionInfo(
            client_id="abc123",
            websocket=mock_ws,
        )
        conn.subscriptions.add("OCH-TEST")

        result = conn.to_dict()

        assert result["client_id"] == "abc123"
        assert "connected_at" in result
        assert "OCH-TEST" in result["subscriptions"]
        assert "websocket" not in result  # Should not include websocket


class TestWebSocketManager:
    """Tests for WebSocketManager class."""

    @pytest.fixture
    def manager(self):
        """Create a fresh WebSocketManager for each test."""
        return WebSocketManager()

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        ws.close = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_connect(self, manager, mock_websocket):
        """Test connecting a WebSocket client."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})

            client_id = await manager.connect(mock_websocket)

        assert client_id is not None
        assert len(client_id) == 8  # UUID prefix
        assert manager.connection_count == 1
        mock_websocket.accept.assert_called_once()
        mock_websocket.send_text.assert_called_once()  # Welcome message

    @pytest.mark.asyncio
    async def test_disconnect(self, manager, mock_websocket):
        """Test disconnecting a client."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            client_id = await manager.connect(mock_websocket)

        assert manager.connection_count == 1

        await manager.disconnect(client_id)

        assert manager.connection_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self, manager):
        """Test disconnecting a non-existent client."""
        # Should not raise
        await manager.disconnect("nonexistent")

    @pytest.mark.asyncio
    async def test_broadcast(self, manager, mock_websocket):
        """Test broadcasting to all clients."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        # Reset mock to clear welcome message
        mock_websocket.send_text.reset_mock()

        count = await manager.broadcast(
            EventType.INCIDENT_CREATED,
            {"incident_id": "OCH-TEST", "title": "Test incident"},
        )

        assert count == 1
        mock_websocket.send_text.assert_called_once()

        # Verify message content
        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "incident_created"
        assert parsed["data"]["incident_id"] == "OCH-TEST"

    @pytest.mark.asyncio
    async def test_broadcast_multiple_clients(self, manager):
        """Test broadcasting to multiple clients."""
        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_text = AsyncMock()

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_text = AsyncMock()

        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(ws1)
            await manager.connect(ws2)

        ws1.send_text.reset_mock()
        ws2.send_text.reset_mock()

        count = await manager.broadcast(
            EventType.METRICS_UPDATE,
            {"metrics": {}},
        )

        assert count == 2
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_exclude_client(self, manager):
        """Test broadcasting with client exclusion."""
        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_text = AsyncMock()

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_text = AsyncMock()

        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            client1 = await manager.connect(ws1)
            await manager.connect(ws2)

        ws1.send_text.reset_mock()
        ws2.send_text.reset_mock()

        count = await manager.broadcast(
            EventType.INCIDENT_CREATED,
            {"incident_id": "OCH-TEST"},
            exclude_client=client1,
        )

        assert count == 1
        ws1.send_text.assert_not_called()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_removes_disconnected(self, manager, mock_websocket):
        """Test that broadcast removes disconnected clients."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        # Simulate disconnection
        mock_websocket.send_text.side_effect = Exception("Connection closed")

        count = await manager.broadcast(
            EventType.INCIDENT_CREATED,
            {"incident_id": "OCH-TEST"},
        )

        assert count == 0
        assert manager.connection_count == 0

    @pytest.mark.asyncio
    async def test_handle_ping_message(self, manager, mock_websocket):
        """Test handling ping message."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            client_id = await manager.connect(mock_websocket)

        response = await manager.handle_client_message(
            client_id,
            '{"type": "ping"}',
        )

        assert response is not None
        assert response.type == EventType.PONG
        assert response.data["client_id"] == client_id

    @pytest.mark.asyncio
    async def test_handle_subscribe_message(self, manager, mock_websocket):
        """Test handling subscribe message."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            client_id = await manager.connect(mock_websocket)

        await manager.handle_client_message(
            client_id,
            '{"type": "subscribe", "incident_id": "OCH-TEST"}',
        )

        conn = manager._connections.get(client_id)
        assert "OCH-TEST" in conn.subscriptions

    @pytest.mark.asyncio
    async def test_handle_unsubscribe_message(self, manager, mock_websocket):
        """Test handling unsubscribe message."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            client_id = await manager.connect(mock_websocket)

        # Subscribe first
        await manager.handle_client_message(
            client_id,
            '{"type": "subscribe", "incident_id": "OCH-TEST"}',
        )

        # Then unsubscribe
        await manager.handle_client_message(
            client_id,
            '{"type": "unsubscribe", "incident_id": "OCH-TEST"}',
        )

        conn = manager._connections.get(client_id)
        assert "OCH-TEST" not in conn.subscriptions

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self, manager, mock_websocket):
        """Test handling invalid JSON message."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            client_id = await manager.connect(mock_websocket)

        # Should not raise
        response = await manager.handle_client_message(
            client_id,
            "not valid json",
        )

        assert response is None

    def test_get_connection_info(self, manager):
        """Test getting connection info."""
        # Non-existent
        assert manager.get_connection_info("nonexistent") is None

    def test_get_all_connections_empty(self, manager):
        """Test getting all connections when empty."""
        assert manager.get_all_connections() == []

    @pytest.mark.asyncio
    async def test_close_all(self, manager):
        """Test closing all connections."""
        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_text = AsyncMock()
        ws1.close = AsyncMock()

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_text = AsyncMock()
        ws2.close = AsyncMock()

        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(ws1)
            await manager.connect(ws2)

        assert manager.connection_count == 2

        await manager.close_all()

        assert manager.connection_count == 0
        ws1.close.assert_called_once()
        ws2.close.assert_called_once()


class TestBroadcastHelpers:
    """Tests for broadcast helper methods."""

    @pytest.fixture
    def manager(self):
        """Create a fresh WebSocketManager for each test."""
        return WebSocketManager()

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_broadcast_incident_created(self, manager, mock_websocket):
        """Test incident created broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        await manager.broadcast_incident_created(
            incident_id="OCH-TEST",
            title="Test error",
            service="caseservice",
            severity="high",
        )

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "incident_created"
        assert parsed["data"]["incident_id"] == "OCH-TEST"
        assert parsed["data"]["service"] == "caseservice"

    @pytest.mark.asyncio
    async def test_broadcast_triage_complete(self, manager, mock_websocket):
        """Test triage complete broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        await manager.broadcast_triage_complete(
            incident_id="OCH-TEST",
            classification="fixable",
            confidence=0.85,
            root_cause="Nil pointer dereference",
        )

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "triage_complete"
        assert parsed["data"]["classification"] == "fixable"
        assert parsed["data"]["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_broadcast_fix_generated(self, manager, mock_websocket):
        """Test fix generated broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        await manager.broadcast_fix_generated(
            incident_id="OCH-TEST",
            file_path="backend/handler.go",
            diff_summary="Added nil check",
            iteration=1,
        )

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "fix_generated"
        assert parsed["data"]["file_path"] == "backend/handler.go"
        assert parsed["data"]["iteration"] == 1

    @pytest.mark.asyncio
    async def test_broadcast_code_diff(self, manager, mock_websocket):
        """Test code diff broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        await manager.broadcast_code_diff(
            incident_id="OCH-TEST",
            file_path="handler.go",
            original_code="func test() {}",
            fixed_code="func test() { return nil }",
        )

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "code_diff"
        assert parsed["data"]["original_code"] == "func test() {}"
        assert parsed["data"]["fixed_code"] == "func test() { return nil }"

    @pytest.mark.asyncio
    async def test_broadcast_pr_created(self, manager, mock_websocket):
        """Test PR created broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        await manager.broadcast_pr_created(
            incident_id="OCH-TEST",
            pr_number=123,
            pr_url="https://github.com/org/repo/pull/123",
        )

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "pr_created"
        assert parsed["data"]["pr_number"] == 123
        assert "github.com" in parsed["data"]["pr_url"]

    @pytest.mark.asyncio
    async def test_broadcast_incident_resolved(self, manager, mock_websocket):
        """Test incident resolved broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        await manager.broadcast_incident_resolved(
            incident_id="OCH-TEST",
            pr_url="https://github.com/org/repo/pull/123",
            verification_status="success",
        )

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "incident_resolved"
        assert parsed["data"]["verification_status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcast_incident_escalated(self, manager, mock_websocket):
        """Test incident escalated broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        await manager.broadcast_incident_escalated(
            incident_id="OCH-TEST",
            reason="Fix generation failed",
            classification="needs_human",
        )

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "incident_escalated"
        assert parsed["data"]["reason"] == "Fix generation failed"

    @pytest.mark.asyncio
    async def test_broadcast_agent_thinking(self, manager, mock_websocket):
        """Test agent thinking broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(model_dump=lambda: {})
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        await manager.broadcast_agent_thinking(
            incident_id="OCH-TEST",
            agent="triage",
            message="Analyzing stack trace...",
        )

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "agent_thinking"
        assert parsed["data"]["agent"] == "triage"
        assert parsed["data"]["message"] == "Analyzing stack trace..."

    @pytest.mark.asyncio
    async def test_broadcast_metrics_update(self, manager, mock_websocket):
        """Test metrics update broadcast helper."""
        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(
                model_dump=lambda: {"total_incidents": 10, "auto_fixed": 5}
            )
            await manager.connect(mock_websocket)

        mock_websocket.send_text.reset_mock()

        with patch("backend.websocket_manager.storage") as mock_storage:
            mock_storage.get_metrics.return_value = MagicMock(
                model_dump=lambda: {"total_incidents": 10, "auto_fixed": 5}
            )
            await manager.broadcast_metrics_update()

        call_args = mock_websocket.send_text.call_args[0][0]
        parsed = json.loads(call_args)
        assert parsed["type"] == "metrics_update"
        assert parsed["data"]["metrics"]["total_incidents"] == 10


class TestGlobalManager:
    """Tests for the global ws_manager instance."""

    def test_global_manager_exists(self):
        """Test that global manager exists."""
        assert ws_manager is not None
        assert isinstance(ws_manager, WebSocketManager)


class TestPipelineEventCallback:
    """Tests for the pipeline event callback creator."""

    @pytest.mark.asyncio
    async def test_create_callback(self):
        """Test creating pipeline event callback."""
        callback = create_pipeline_event_callback()
        assert callable(callback)

    @pytest.mark.asyncio
    async def test_callback_broadcasts_event(self):
        """Test that callback broadcasts events."""
        from backend.agents.orchestrator import PipelineEvent, PipelineStage

        event = PipelineEvent(
            incident_id="OCH-TEST",
            stage=PipelineStage.TRIAGING,
            message="Analyzing incident",
            data={"classification": "fixable"},
        )

        with patch("backend.websocket_manager.ws_manager.broadcast") as mock_broadcast:
            mock_broadcast.return_value = 1
            callback = create_pipeline_event_callback()
            await callback(event)

            mock_broadcast.assert_called_once()
            call_args = mock_broadcast.call_args
            assert call_args[0][0] == EventType.TRIAGE_STARTED
            assert call_args[0][1]["incident_id"] == "OCH-TEST"
