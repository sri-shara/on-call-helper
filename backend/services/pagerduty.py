"""
PagerDuty Service for On Call Helper.

Integrates with PagerDuty Events API v2 to notify on-call engineers
about incidents and their resolution status.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

from backend.config import settings
from backend.models import Incident, Severity

logger = logging.getLogger(__name__)


class PagerDutyError(Exception):
    """Base exception for PagerDuty service errors."""
    pass


class PagerDutyConfigError(PagerDutyError):
    """Raised when PagerDuty is not configured."""
    pass


class PagerDutyAPIError(PagerDutyError):
    """Raised when PagerDuty API returns an error."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.status_code = status_code
        super().__init__(message)


class EventAction(str, Enum):
    """PagerDuty event actions."""

    TRIGGER = "trigger"
    ACKNOWLEDGE = "acknowledge"
    RESOLVE = "resolve"


class PagerDutySeverity(str, Enum):
    """PagerDuty severity levels."""

    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class PagerDutyEvent:
    """Represents a PagerDuty event response."""

    status: str
    message: str
    dedup_key: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status,
            "message": self.message,
            "dedup_key": self.dedup_key,
        }


class PagerDutyService:
    """
    PagerDuty Events API v2 integration.

    Sends events to PagerDuty to alert on-call engineers about
    incidents detected by On Call Helper.
    """

    EVENTS_API_URL = "https://events.pagerduty.com/v2/enqueue"

    # Severity mapping from On Call Helper to PagerDuty
    SEVERITY_MAP = {
        Severity.CRITICAL: PagerDutySeverity.CRITICAL,
        Severity.HIGH: PagerDutySeverity.ERROR,
        Severity.MEDIUM: PagerDutySeverity.WARNING,
        Severity.LOW: PagerDutySeverity.INFO,
    }

    def __init__(
        self,
        routing_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """
        Initialize the PagerDuty service.

        Args:
            routing_key: PagerDuty Events API routing key (defaults to settings)
            timeout: Request timeout in seconds
        """
        self.routing_key = routing_key or settings.pagerduty_routing_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _check_configured(self) -> None:
        """Check if PagerDuty is configured."""
        if not self.routing_key:
            raise PagerDutyConfigError(
                "PagerDuty routing key not configured. "
                "Set PAGERDUTY_ROUTING_KEY environment variable."
            )

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _map_severity(self, severity: Severity) -> str:
        """Map On Call Helper severity to PagerDuty severity."""
        return self.SEVERITY_MAP.get(severity, PagerDutySeverity.WARNING).value

    def _generate_dedup_key(self, incident_id: str, suffix: str = "") -> str:
        """Generate a deduplication key for an incident."""
        if suffix:
            return f"oncall-helper-{incident_id}-{suffix}"
        return f"oncall-helper-{incident_id}"

    async def _send_event(
        self,
        event_action: EventAction,
        dedup_key: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> PagerDutyEvent:
        """
        Send an event to PagerDuty.

        Args:
            event_action: The action (trigger, acknowledge, resolve)
            dedup_key: Deduplication key for the incident
            payload: Event payload (required for trigger, optional otherwise)

        Returns:
            PagerDutyEvent with response details

        Raises:
            PagerDutyConfigError: If not configured
            PagerDutyAPIError: If API request fails
        """
        self._check_configured()

        body: Dict[str, Any] = {
            "routing_key": self.routing_key,
            "event_action": event_action.value,
            "dedup_key": dedup_key,
        }

        if payload:
            body["payload"] = payload

        client = await self._get_client()

        logger.debug(f"Sending PagerDuty event: {event_action.value} for {dedup_key}")

        try:
            response = await client.post(
                self.EVENTS_API_URL,
                json=body,
                headers={"Content-Type": "application/json"},
            )
        except httpx.TimeoutException:
            raise PagerDutyAPIError("PagerDuty API request timed out")
        except httpx.RequestError as e:
            raise PagerDutyAPIError(f"PagerDuty API request failed: {e}")

        if response.status_code == 202:
            data = response.json()
            logger.info(f"PagerDuty event sent: {event_action.value} ({dedup_key})")
            return PagerDutyEvent(
                status=data.get("status", "success"),
                message=data.get("message", "Event processed"),
                dedup_key=data.get("dedup_key", dedup_key),
            )

        # Handle errors
        if response.status_code == 400:
            raise PagerDutyAPIError(
                f"Invalid event payload: {response.text}",
                status_code=400,
            )
        elif response.status_code == 429:
            raise PagerDutyAPIError(
                "PagerDuty rate limit exceeded",
                status_code=429,
            )
        else:
            raise PagerDutyAPIError(
                f"PagerDuty API error ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )

    async def trigger(
        self,
        incident: Incident,
        custom_details: Optional[Dict[str, Any]] = None,
    ) -> PagerDutyEvent:
        """
        Trigger a new PagerDuty incident.

        Args:
            incident: The On Call Helper incident
            custom_details: Additional details to include

        Returns:
            PagerDutyEvent with dedup_key for future updates
        """
        dedup_key = self._generate_dedup_key(incident.id)

        details = {
            "incident_id": incident.id,
            "service": incident.service_name,
            "error_message": incident.error_message[:500],
            "file_path": incident.file_path,
            "tenant": incident.tenant_name,
            "environment": incident.environment,
            "created_at": incident.created_at.isoformat(),
            "dashboard_url": f"{settings.dashboard_url}/incidents/{incident.id}",
        }

        if custom_details:
            details.update(custom_details)

        payload = {
            "summary": f"[On Call Helper] {incident.title[:200]}",
            "severity": self._map_severity(incident.severity),
            "source": "on-call-helper",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "component": incident.service_name,
            "group": "nucleus",
            "class": incident.status.value,
            "custom_details": details,
        }

        return await self._send_event(EventAction.TRIGGER, dedup_key, payload)

    async def acknowledge(
        self,
        incident_id: str,
        message: Optional[str] = None,
    ) -> PagerDutyEvent:
        """
        Acknowledge an incident - pipeline is processing.

        Args:
            incident_id: The On Call Helper incident ID
            message: Optional acknowledgment message

        Returns:
            PagerDutyEvent
        """
        dedup_key = self._generate_dedup_key(incident_id)

        logger.info(f"Acknowledging PagerDuty incident: {incident_id}")

        return await self._send_event(EventAction.ACKNOWLEDGE, dedup_key)

    async def resolve(
        self,
        incident_id: str,
        pr_url: Optional[str] = None,
        message: Optional[str] = None,
    ) -> PagerDutyEvent:
        """
        Resolve an incident - fix was successful.

        Args:
            incident_id: The On Call Helper incident ID
            pr_url: URL of the fix PR
            message: Optional resolution message

        Returns:
            PagerDutyEvent
        """
        dedup_key = self._generate_dedup_key(incident_id)

        resolution_message = message or "Fix PR created by On Call Helper"
        if pr_url:
            resolution_message += f" - {pr_url}"

        logger.info(f"Resolving PagerDuty incident: {incident_id}")

        return await self._send_event(EventAction.RESOLVE, dedup_key)

    async def escalate(
        self,
        incident_id: str,
        reason: str,
        severity: Severity = Severity.HIGH,
    ) -> PagerDutyEvent:
        """
        Escalate an incident - automated fix failed, human attention needed.

        Creates a new high-priority incident for human intervention.

        Args:
            incident_id: The On Call Helper incident ID
            reason: Why the incident is being escalated
            severity: Escalation severity (default: HIGH)

        Returns:
            PagerDutyEvent for the escalation incident
        """
        dedup_key = self._generate_dedup_key(incident_id, "escalation")

        payload = {
            "summary": f"[On Call Helper] ESCALATION: {reason[:200]}",
            "severity": self._map_severity(severity),
            "source": "on-call-helper",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "component": "on-call-helper",
            "group": "nucleus",
            "class": "escalation",
            "custom_details": {
                "original_incident_id": incident_id,
                "escalation_reason": reason,
                "dashboard_url": f"{settings.dashboard_url}/incidents/{incident_id}",
                "action_required": "Manual investigation needed",
            },
        }

        logger.warning(f"Escalating incident {incident_id}: {reason}")

        return await self._send_event(EventAction.TRIGGER, dedup_key, payload)

    async def notify_triage_complete(
        self,
        incident_id: str,
        classification: str,
        confidence: float,
        root_cause: str,
    ) -> PagerDutyEvent:
        """
        Send a change event noting triage completion.

        Args:
            incident_id: The incident ID
            classification: Triage classification
            confidence: Confidence score
            root_cause: Root cause summary

        Returns:
            PagerDutyEvent
        """
        # For status updates, we acknowledge with context
        # This keeps the incident open but shows progress
        dedup_key = self._generate_dedup_key(incident_id)

        logger.info(f"Notifying triage complete for {incident_id}: {classification}")

        return await self._send_event(EventAction.ACKNOWLEDGE, dedup_key)

    async def notify_fix_generated(
        self,
        incident_id: str,
        file_path: str,
        iteration: int,
    ) -> PagerDutyEvent:
        """
        Send a change event noting fix generation.

        Args:
            incident_id: The incident ID
            file_path: Path to the file being fixed
            iteration: CodeRabbit iteration number

        Returns:
            PagerDutyEvent
        """
        dedup_key = self._generate_dedup_key(incident_id)

        logger.info(f"Notifying fix generated for {incident_id} (iteration {iteration})")

        return await self._send_event(EventAction.ACKNOWLEDGE, dedup_key)

    async def notify_tests_passed(
        self,
        incident_id: str,
        tests_run: int,
        tests_passed: int,
    ) -> PagerDutyEvent:
        """
        Send a change event noting tests passed.

        Args:
            incident_id: The incident ID
            tests_run: Number of tests run
            tests_passed: Number of tests passed

        Returns:
            PagerDutyEvent
        """
        dedup_key = self._generate_dedup_key(incident_id)

        logger.info(f"Notifying tests passed for {incident_id}: {tests_passed}/{tests_run}")

        return await self._send_event(EventAction.ACKNOWLEDGE, dedup_key)

    async def check_health(self) -> Dict[str, Any]:
        """
        Check PagerDuty service health.

        Returns:
            Dict with configuration status
        """
        configured = bool(self.routing_key)

        return {
            "configured": configured,
            "routing_key_set": configured,
            "events_api_url": self.EVENTS_API_URL,
        }


# Module-level convenience functions


async def trigger_incident(incident: Incident) -> PagerDutyEvent:
    """
    Trigger a PagerDuty incident.

    Args:
        incident: The incident to trigger

    Returns:
        PagerDutyEvent with dedup key
    """
    service = PagerDutyService()
    try:
        return await service.trigger(incident)
    finally:
        await service.close()


async def resolve_incident(incident_id: str, pr_url: Optional[str] = None) -> PagerDutyEvent:
    """
    Resolve a PagerDuty incident.

    Args:
        incident_id: Incident ID to resolve
        pr_url: Optional PR URL

    Returns:
        PagerDutyEvent
    """
    service = PagerDutyService()
    try:
        return await service.resolve(incident_id, pr_url=pr_url)
    finally:
        await service.close()


async def escalate_incident(incident_id: str, reason: str) -> PagerDutyEvent:
    """
    Escalate an incident to human attention.

    Args:
        incident_id: Incident ID to escalate
        reason: Escalation reason

    Returns:
        PagerDutyEvent
    """
    service = PagerDutyService()
    try:
        return await service.escalate(incident_id, reason)
    finally:
        await service.close()
