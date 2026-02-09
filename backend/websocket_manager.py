"""
WebSocket Manager for On Call Helper.

Manages WebSocket connections for real-time dashboard updates.
Broadcasts pipeline events to all connected clients.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from backend.models import Metrics
from backend.storage import storage

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """WebSocket event types."""

    # Connection events
    WELCOME = "welcome"
    PING = "ping"
    PONG = "pong"

    # Incident lifecycle events
    INCIDENT_CREATED = "incident_created"
    INCIDENT_UPDATED = "incident_updated"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_ESCALATED = "incident_escalated"

    # Pipeline stage events
    TRIAGE_STARTED = "triage_started"
    TRIAGE_COMPLETE = "triage_complete"
    FIX_STARTED = "fix_started"
    FIX_GENERATED = "fix_generated"
    REVIEW_STARTED = "review_started"
    REVIEW_COMPLETE = "review_complete"
    SANDBOX_STARTED = "sandbox_started"
    SANDBOX_COMPLETE = "sandbox_complete"
    PR_CREATED = "pr_created"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_COMPLETE = "verification_complete"

    # Agent thinking events (for real-time display)
    AGENT_THINKING = "agent_thinking"
    CODE_DIFF = "code_diff"

    # Metrics events
    METRICS_UPDATE = "metrics_update"


@dataclass
class WebSocketMessage:
    """A WebSocket message to broadcast."""

    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "type": self.type.value,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        })

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WebSocketMessage":
        """Create from dictionary."""
        return cls(
            type=EventType(data.get("type", "welcome")),
            data=data.get("data", {}),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.utcnow(),
        )


@dataclass
class ConnectionInfo:
    """Information about a connected WebSocket client."""

    client_id: str
    websocket: WebSocket
    connected_at: datetime = field(default_factory=datetime.utcnow)
    last_ping: Optional[datetime] = None
    subscriptions: Set[str] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (without websocket)."""
        return {
            "client_id": self.client_id,
            "connected_at": self.connected_at.isoformat(),
            "last_ping": self.last_ping.isoformat() if self.last_ping else None,
            "subscriptions": list(self.subscriptions),
        }


class WebSocketManager:
    """
    Manages WebSocket connections and message broadcasting.

    Features:
    - Connection tracking with unique client IDs
    - Automatic disconnection cleanup
    - Broadcast to all connected clients
    - Subscription-based filtering (optional)
    - Heartbeat/ping support
    """

    def __init__(self):
        """Initialize the WebSocket manager."""
        self._connections: Dict[str, ConnectionInfo] = {}
        self._lock = asyncio.Lock()

    @property
    def connection_count(self) -> int:
        """Get number of active connections."""
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> str:
        """
        Accept a new WebSocket connection.

        Args:
            websocket: The WebSocket connection

        Returns:
            Client ID for this connection
        """
        await websocket.accept()

        client_id = str(uuid.uuid4())[:8]

        async with self._lock:
            self._connections[client_id] = ConnectionInfo(
                client_id=client_id,
                websocket=websocket,
            )

        logger.info(f"WebSocket client connected: {client_id} (total: {self.connection_count})")

        # Send welcome message with current metrics
        await self._send_welcome(client_id)

        return client_id

    async def disconnect(self, client_id: str) -> None:
        """
        Remove a disconnected client.

        Args:
            client_id: The client to remove
        """
        async with self._lock:
            if client_id in self._connections:
                del self._connections[client_id]
                logger.info(f"WebSocket client disconnected: {client_id} (total: {self.connection_count})")

    async def _send_welcome(self, client_id: str) -> None:
        """Send welcome message with current state."""
        metrics = storage.get_metrics()

        message = WebSocketMessage(
            type=EventType.WELCOME,
            data={
                "client_id": client_id,
                "message": "Connected to On Call Helper",
                "metrics": metrics.model_dump() if metrics else {},
                "server_time": datetime.utcnow().isoformat(),
            },
        )

        await self._send_to_client(client_id, message)

    async def _send_to_client(self, client_id: str, message: WebSocketMessage) -> bool:
        """
        Send a message to a specific client.

        Args:
            client_id: Target client
            message: Message to send

        Returns:
            True if sent successfully, False otherwise
        """
        conn = self._connections.get(client_id)
        if not conn:
            return False

        try:
            await conn.websocket.send_text(message.to_json())
            return True
        except Exception as e:
            logger.warning(f"Failed to send to client {client_id}: {e}")
            # Client may have disconnected
            await self.disconnect(client_id)
            return False

    async def broadcast(
        self,
        event_type: EventType,
        data: Dict[str, Any],
        exclude_client: Optional[str] = None,
    ) -> int:
        """
        Broadcast a message to all connected clients.

        Args:
            event_type: Type of event
            data: Event data
            exclude_client: Optional client ID to exclude

        Returns:
            Number of clients that received the message
        """
        message = WebSocketMessage(type=event_type, data=data)

        sent_count = 0
        disconnected = []

        # Get snapshot of connections
        async with self._lock:
            connections = list(self._connections.items())

        for client_id, conn in connections:
            if exclude_client and client_id == exclude_client:
                continue

            try:
                await conn.websocket.send_text(message.to_json())
                sent_count += 1
            except Exception as e:
                logger.debug(f"Failed to send to {client_id}: {e}")
                disconnected.append(client_id)

        # Clean up disconnected clients
        for client_id in disconnected:
            await self.disconnect(client_id)

        logger.debug(f"Broadcast {event_type.value} to {sent_count} clients")
        return sent_count

    async def broadcast_incident_created(
        self,
        incident_id: str,
        title: str,
        service: str,
        severity: str,
        source: str = "gcp",
    ) -> int:
        """Broadcast incident created event."""
        return await self.broadcast(
            EventType.INCIDENT_CREATED,
            {
                "incident_id": incident_id,
                "title": title,
                "service": service,
                "severity": severity,
                "source": source,
            },
        )

    async def broadcast_triage_complete(
        self,
        incident_id: str,
        classification: str,
        confidence: float,
        root_cause: str,
    ) -> int:
        """Broadcast triage completion event."""
        return await self.broadcast(
            EventType.TRIAGE_COMPLETE,
            {
                "incident_id": incident_id,
                "classification": classification,
                "confidence": confidence,
                "root_cause": root_cause,
            },
        )

    async def broadcast_fix_generated(
        self,
        incident_id: str,
        file_path: str,
        diff_summary: str,
        iteration: int,
    ) -> int:
        """Broadcast fix generated event."""
        return await self.broadcast(
            EventType.FIX_GENERATED,
            {
                "incident_id": incident_id,
                "file_path": file_path,
                "diff_summary": diff_summary,
                "iteration": iteration,
            },
        )

    async def broadcast_code_diff(
        self,
        incident_id: str,
        file_path: str,
        original_code: str,
        fixed_code: str,
    ) -> int:
        """Broadcast code diff for real-time display."""
        return await self.broadcast(
            EventType.CODE_DIFF,
            {
                "incident_id": incident_id,
                "file_path": file_path,
                "original_code": original_code,
                "fixed_code": fixed_code,
            },
        )

    async def broadcast_sandbox_status(
        self,
        incident_id: str,
        status: str,
        tests_run: int = 0,
        tests_passed: int = 0,
    ) -> int:
        """Broadcast sandbox test status."""
        return await self.broadcast(
            EventType.SANDBOX_COMPLETE,
            {
                "incident_id": incident_id,
                "status": status,
                "tests_run": tests_run,
                "tests_passed": tests_passed,
            },
        )

    async def broadcast_pr_created(
        self,
        incident_id: str,
        pr_number: int,
        pr_url: str,
    ) -> int:
        """Broadcast PR created event."""
        return await self.broadcast(
            EventType.PR_CREATED,
            {
                "incident_id": incident_id,
                "pr_number": pr_number,
                "pr_url": pr_url,
            },
        )

    async def broadcast_incident_resolved(
        self,
        incident_id: str,
        pr_url: Optional[str] = None,
        verification_status: Optional[str] = None,
    ) -> int:
        """Broadcast incident resolved event."""
        return await self.broadcast(
            EventType.INCIDENT_RESOLVED,
            {
                "incident_id": incident_id,
                "pr_url": pr_url,
                "verification_status": verification_status,
            },
        )

    async def broadcast_incident_escalated(
        self,
        incident_id: str,
        reason: str,
        classification: Optional[str] = None,
    ) -> int:
        """Broadcast incident escalated event."""
        return await self.broadcast(
            EventType.INCIDENT_ESCALATED,
            {
                "incident_id": incident_id,
                "reason": reason,
                "classification": classification,
            },
        )

    async def broadcast_agent_thinking(
        self,
        incident_id: str,
        agent: str,
        message: str,
    ) -> int:
        """Broadcast agent thinking status."""
        return await self.broadcast(
            EventType.AGENT_THINKING,
            {
                "incident_id": incident_id,
                "agent": agent,
                "message": message,
            },
        )

    async def broadcast_metrics_update(self) -> int:
        """Broadcast current metrics to all clients."""
        metrics = storage.get_metrics()
        return await self.broadcast(
            EventType.METRICS_UPDATE,
            {"metrics": metrics.model_dump() if metrics else {}},
        )

    async def handle_client_message(
        self,
        client_id: str,
        raw_message: str,
    ) -> Optional[WebSocketMessage]:
        """
        Handle a message from a client.

        Args:
            client_id: The sender's client ID
            raw_message: Raw message text

        Returns:
            Response message if applicable
        """
        try:
            data = json.loads(raw_message)
            msg_type = data.get("type", "")

            if msg_type == "ping":
                # Update last ping time
                conn = self._connections.get(client_id)
                if conn:
                    conn.last_ping = datetime.utcnow()

                return WebSocketMessage(
                    type=EventType.PONG,
                    data={"client_id": client_id},
                )

            elif msg_type == "subscribe":
                # Subscribe to specific incident updates
                incident_id = data.get("incident_id")
                if incident_id:
                    conn = self._connections.get(client_id)
                    if conn:
                        conn.subscriptions.add(incident_id)
                        logger.debug(f"Client {client_id} subscribed to {incident_id}")

            elif msg_type == "unsubscribe":
                # Unsubscribe from incident updates
                incident_id = data.get("incident_id")
                if incident_id:
                    conn = self._connections.get(client_id)
                    if conn:
                        conn.subscriptions.discard(incident_id)

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from client {client_id}: {raw_message[:100]}")

        return None

    def get_connection_info(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific connection."""
        conn = self._connections.get(client_id)
        return conn.to_dict() if conn else None

    def get_all_connections(self) -> List[Dict[str, Any]]:
        """Get information about all connections."""
        return [conn.to_dict() for conn in self._connections.values()]

    async def close_all(self) -> None:
        """Close all connections gracefully."""
        async with self._lock:
            for client_id, conn in list(self._connections.items()):
                try:
                    await conn.websocket.close()
                except Exception:
                    pass

            self._connections.clear()

        logger.info("All WebSocket connections closed")


# Global WebSocket manager instance
ws_manager = WebSocketManager()


# Helper function to create event callback for orchestrator
def create_pipeline_event_callback():
    """
    Create an event callback function for the pipeline orchestrator.

    Returns a function that broadcasts pipeline events via WebSocket.
    """
    from backend.agents.orchestrator import PipelineEvent, PipelineStage

    async def callback(event: PipelineEvent) -> None:
        """Handle pipeline events and broadcast via WebSocket."""
        stage_to_event = {
            PipelineStage.RECEIVED: EventType.INCIDENT_CREATED,
            PipelineStage.TRIAGING: EventType.TRIAGE_STARTED,
            PipelineStage.FIXING: EventType.FIX_STARTED,
            PipelineStage.REVIEWING: EventType.REVIEW_STARTED,
            PipelineStage.TESTING: EventType.SANDBOX_STARTED,
            PipelineStage.CREATING_PR: EventType.PR_CREATED,
            PipelineStage.VERIFYING: EventType.VERIFICATION_STARTED,
            PipelineStage.COMPLETED: EventType.INCIDENT_RESOLVED,
            PipelineStage.ESCALATED: EventType.INCIDENT_ESCALATED,
        }

        event_type = stage_to_event.get(event.stage, EventType.INCIDENT_UPDATED)

        # Detect completion events for stages that emit twice (started + complete)
        # by checking for completion-specific data keys
        if event.stage == PipelineStage.TRIAGING and "classification" in event.data:
            event_type = EventType.TRIAGE_COMPLETE
        elif event.stage == PipelineStage.FIXING and "file_path" in event.data:
            event_type = EventType.FIX_GENERATED
        elif event.stage == PipelineStage.REVIEWING and "passed" in event.data:
            event_type = EventType.REVIEW_COMPLETE
        elif event.stage == PipelineStage.TESTING and "tests_run" in event.data:
            event_type = EventType.SANDBOX_COMPLETE
        elif event.stage == PipelineStage.VERIFYING and "status" in event.data:
            event_type = EventType.VERIFICATION_COMPLETE

        await ws_manager.broadcast(
            event_type,
            {
                "incident_id": event.incident_id,
                "stage": event.stage.value,
                "message": event.message,
                **event.data,
            },
        )

    return callback
