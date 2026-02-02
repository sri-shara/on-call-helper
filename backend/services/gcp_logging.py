"""
GCP Cloud Logging service for On Call Helper.

Handles ingestion of error logs from GCP Cloud Logging via Pub/Sub push.

GCP Setup Required:
1. Create Pub/Sub topic: gcloud pubsub topics create oncall-helper-errors
2. Create push subscription pointing to /webhook/gcp-logs
3. Create log sink with filter: severity>=ERROR

Reference: /Users/sri/nucleus - The Nucleus MDR platform being monitored
"""

import base64
import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel

from backend.models import Incident, Severity, IncidentStatus


class GCPLogEntry(BaseModel):
    """Parsed GCP Cloud Logging entry."""

    insert_id: str
    timestamp: datetime
    severity: str
    log_name: str
    resource_type: str
    resource_labels: Dict[str, str]

    # Payload - one of these will be populated
    text_payload: Optional[str] = None
    json_payload: Optional[Dict[str, Any]] = None

    # Extracted fields
    error_message: str
    stack_trace: Optional[str] = None
    file_path: Optional[str] = None
    service_name: Optional[str] = None
    tenant_id: Optional[str] = None
    tenant_name: Optional[str] = None


class PubSubMessage(BaseModel):
    """Pub/Sub push message wrapper."""

    message: Dict[str, Any]
    subscription: str


def generate_incident_id() -> str:
    """
    Generate a unique incident ID in format OCH-{8chars}.

    Uses timestamp + random component for uniqueness.
    """
    import secrets
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    random_part = secrets.token_hex(4)
    hash_input = f"{timestamp}{random_part}"
    hash_value = hashlib.sha256(hash_input.encode()).hexdigest()[:8].upper()
    return f"OCH-{hash_value}"


def _map_gcp_severity(gcp_severity: str) -> Severity:
    """Map GCP log severity to incident severity."""
    severity_map = {
        "EMERGENCY": Severity.CRITICAL,
        "ALERT": Severity.CRITICAL,
        "CRITICAL": Severity.CRITICAL,
        "ERROR": Severity.HIGH,
        "WARNING": Severity.MEDIUM,
        "NOTICE": Severity.LOW,
        "INFO": Severity.LOW,
        "DEBUG": Severity.LOW,
        "DEFAULT": Severity.MEDIUM,
    }
    return severity_map.get(gcp_severity.upper(), Severity.MEDIUM)


def _extract_error_message(log_entry: Dict[str, Any]) -> str:
    """Extract error message from log entry payload."""
    # Try textPayload first
    if "textPayload" in log_entry:
        return log_entry["textPayload"]

    # Try jsonPayload
    json_payload = log_entry.get("jsonPayload", {})

    # Common message fields in order of preference
    message_fields = [
        "message",
        "error",
        "errorMessage",
        "error_message",
        "msg",
        "description",
        "details",
    ]

    for field in message_fields:
        if field in json_payload:
            value = json_payload[field]
            if isinstance(value, str):
                return value
            elif isinstance(value, dict):
                # Nested error object
                return json.dumps(value)

    # Fallback: stringify the entire jsonPayload
    if json_payload:
        return json.dumps(json_payload)

    return "Unknown error"


def _extract_stack_trace(log_entry: Dict[str, Any]) -> Optional[str]:
    """Extract stack trace from log entry."""
    json_payload = log_entry.get("jsonPayload", {})

    # Common stack trace fields
    stack_fields = [
        "stack_trace",
        "stackTrace",
        "traceback",
        "stack",
        "exception",
    ]

    for field in stack_fields:
        if field in json_payload:
            value = json_payload[field]
            if isinstance(value, str):
                return value
            elif isinstance(value, list):
                return "\n".join(str(line) for line in value)

    # Check textPayload for stack trace patterns
    text_payload = log_entry.get("textPayload", "")
    if "goroutine" in text_payload or "at " in text_payload:
        # Looks like a stack trace
        lines = text_payload.split("\n")
        if len(lines) > 3:
            return text_payload

    return None


def _extract_file_path(log_entry: Dict[str, Any]) -> Optional[str]:
    """Extract file path from log entry."""
    # Check sourceLocation
    source_location = log_entry.get("sourceLocation", {})
    if source_location.get("file"):
        return source_location["file"]

    # Check jsonPayload
    json_payload = log_entry.get("jsonPayload", {})
    file_fields = ["file", "filename", "source", "path", "caller"]

    for field in file_fields:
        if field in json_payload and isinstance(json_payload[field], str):
            return json_payload[field]

    # Try to extract from stack trace
    stack_trace = _extract_stack_trace(log_entry)
    if stack_trace:
        # Look for Go file paths like /backend/services/caseservice/handler.go:142
        match = re.search(r'(/[\w/.-]+\.go):\d+', stack_trace)
        if match:
            return match.group(1)

        # Look for Python file paths
        match = re.search(r'File "([^"]+\.py)"', stack_trace)
        if match:
            return match.group(1)

    return None


def _extract_service_name(log_entry: Dict[str, Any]) -> str:
    """Extract service name from log entry."""
    # From resource labels (most reliable)
    resource = log_entry.get("resource", {})
    labels = resource.get("labels", {})

    service_label_keys = [
        "service_name",
        "service",
        "container_name",
        "job_name",
        "function_name",
        "module_id",
        "pod_name",
    ]

    for key in service_label_keys:
        if key in labels and labels[key]:
            return labels[key]

    # From top-level labels
    top_labels = log_entry.get("labels", {})
    for key in service_label_keys:
        if key in top_labels and top_labels[key]:
            return top_labels[key]

    # From jsonPayload
    json_payload = log_entry.get("jsonPayload", {})
    payload_service_keys = ["service", "serviceName", "service_name", "component", "app", "application"]
    for key in payload_service_keys:
        if key in json_payload and json_payload[key]:
            return str(json_payload[key])

    # From log name
    log_name = log_entry.get("logName", "")
    # Format: projects/{project}/logs/{log_name}
    if "/logs/" in log_name:
        log_name_part = log_name.split("/logs/")[-1]
        # Clean up URL encoding
        log_name_part = log_name_part.replace("%2F", "/").replace("%3A", ":")
        # Extract meaningful part
        parts = log_name_part.split("/")
        if parts and parts[0]:
            return parts[0]

    # From resource type as last resort
    resource_type = resource.get("type", "")
    if resource_type and resource_type != "unknown":
        return resource_type

    return "unknown-service"


def _extract_tenant_info(log_entry: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Extract tenant ID and name from log entry."""
    json_payload = log_entry.get("jsonPayload", {})

    # Direct tenant fields
    tenant_id = json_payload.get("tenant_id") or json_payload.get("tenantId")
    tenant_name = json_payload.get("tenant_name") or json_payload.get("tenantName")

    # Check labels
    labels = log_entry.get("labels", {})
    if not tenant_id:
        tenant_id = labels.get("tenant_id") or labels.get("tenantId")
    if not tenant_name:
        tenant_name = labels.get("tenant_name") or labels.get("tenantName")

    # Check resource labels
    resource_labels = log_entry.get("resource", {}).get("labels", {})
    if not tenant_id:
        tenant_id = resource_labels.get("tenant_id")

    return tenant_id, tenant_name


def _generate_title(error_message: str, service_name: str) -> str:
    """Generate a concise incident title."""
    # Take first line of error
    first_line = error_message.split("\n")[0].strip()

    # Truncate if too long
    if len(first_line) > 100:
        first_line = first_line[:97] + "..."

    # Clean up common prefixes
    prefixes_to_remove = [
        "Error: ",
        "ERROR: ",
        "error: ",
        "panic: ",
        "fatal: ",
        "FATAL: ",
    ]
    for prefix in prefixes_to_remove:
        if first_line.startswith(prefix):
            first_line = first_line[len(prefix):]
            break

    return f"[{service_name}] {first_line}"


def parse_pubsub_message(data: Dict[str, Any]) -> GCPLogEntry:
    """
    Parse a Pub/Sub push message containing a GCP log entry.

    Args:
        data: The raw request body from Pub/Sub push

    Returns:
        Parsed GCPLogEntry

    Raises:
        ValueError: If the message format is invalid
    """
    # Extract the Pub/Sub message
    if "message" not in data:
        raise ValueError("Missing 'message' field in Pub/Sub push")

    message = data["message"]

    # Decode base64 data
    if "data" not in message:
        raise ValueError("Missing 'data' field in Pub/Sub message")

    try:
        decoded_data = base64.b64decode(message["data"]).decode("utf-8")
        log_entry = json.loads(decoded_data)
    except (base64.binascii.Error, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to decode Pub/Sub message data: {e}")

    # Parse timestamp
    timestamp_str = log_entry.get("timestamp", "")
    try:
        # GCP timestamp format: 2024-01-15T10:30:00.123456789Z
        if "." in timestamp_str:
            # Truncate nanoseconds to microseconds
            timestamp_str = re.sub(r'\.(\d{6})\d*Z', r'.\1Z', timestamp_str)
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        else:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        timestamp = datetime.utcnow()

    # Extract resource info
    resource = log_entry.get("resource", {})
    resource_type = resource.get("type", "unknown")
    resource_labels = resource.get("labels", {})

    # Extract tenant info
    tenant_id, tenant_name = _extract_tenant_info(log_entry)

    return GCPLogEntry(
        insert_id=log_entry.get("insertId", ""),
        timestamp=timestamp,
        severity=log_entry.get("severity", "ERROR"),
        log_name=log_entry.get("logName", ""),
        resource_type=resource_type,
        resource_labels=resource_labels,
        text_payload=log_entry.get("textPayload"),
        json_payload=log_entry.get("jsonPayload"),
        error_message=_extract_error_message(log_entry),
        stack_trace=_extract_stack_trace(log_entry),
        file_path=_extract_file_path(log_entry),
        service_name=_extract_service_name(log_entry),
        tenant_id=tenant_id,
        tenant_name=tenant_name,
    )


def create_incident_from_log(log_entry: GCPLogEntry) -> Incident:
    """
    Create an Incident from a parsed GCP log entry.

    Args:
        log_entry: Parsed GCP log entry

    Returns:
        New Incident object
    """
    return Incident(
        id=generate_incident_id(),
        title=_generate_title(log_entry.error_message, log_entry.service_name or "unknown"),
        error_message=log_entry.error_message,
        stack_trace=log_entry.stack_trace,
        file_path=log_entry.file_path,
        service_name=log_entry.service_name or "unknown",
        severity=_map_gcp_severity(log_entry.severity),
        tenant_name=log_entry.tenant_name,
        environment="production",  # Assuming production for now
        status=IncidentStatus.ACTIVE,
        created_at=log_entry.timestamp,
        gcp_insert_id=log_entry.insert_id,
        gcp_resource_type=log_entry.resource_type,
        gcp_log_name=log_entry.log_name,
    )


class GCPLoggingService:
    """
    Service for handling GCP Cloud Logging integration.

    Supports both Pub/Sub push (webhook) and polling modes.
    """

    def __init__(self, project_id: Optional[str] = None, credentials_path: Optional[str] = None):
        from backend.config import settings
        self.project_id = project_id or settings.gcp_project_id
        self.credentials_path = credentials_path or settings.gcp_credentials_path
        self.log_filter = settings.gcp_log_filter
        self._polling_active = False
        self._polling_task = None
        self._seen_insert_ids: set = set()  # Track processed logs
        self._last_poll_time: Optional[datetime] = None
        self._client = None

    def _get_client(self):
        """Get or create the Cloud Logging client."""
        if self._client is None:
            from google.cloud import logging as cloud_logging
            import os

            if self.credentials_path and os.path.exists(self.credentials_path):
                self._client = cloud_logging.Client(project=self.project_id)
            else:
                # Use default credentials (ADC)
                self._client = cloud_logging.Client(project=self.project_id)

        return self._client

    async def handle_webhook(self, data: Dict[str, Any]) -> Tuple[GCPLogEntry, Incident]:
        """
        Handle incoming Pub/Sub push webhook.

        Args:
            data: Raw request body from Pub/Sub

        Returns:
            Tuple of (parsed log entry, created incident)
        """
        log_entry = parse_pubsub_message(data)
        incident = create_incident_from_log(log_entry)
        return log_entry, incident

    def _parse_log_entry_direct(self, entry) -> Optional[GCPLogEntry]:
        """Parse a Cloud Logging entry directly (not from Pub/Sub)."""
        try:
            # Convert to dict format similar to Pub/Sub
            log_dict = {
                "insertId": entry.insert_id,
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else datetime.utcnow().isoformat(),
                "severity": entry.severity or "ERROR",
                "logName": entry.log_name or "",
                "resource": {
                    "type": entry.resource.type if entry.resource else "unknown",
                    "labels": dict(entry.resource.labels) if entry.resource and entry.resource.labels else {},
                },
                "labels": dict(entry.labels) if entry.labels else {},
            }

            # Add payload
            if entry.payload:
                if isinstance(entry.payload, str):
                    log_dict["textPayload"] = entry.payload
                elif isinstance(entry.payload, dict):
                    log_dict["jsonPayload"] = entry.payload

            # Extract tenant info
            tenant_id, tenant_name = _extract_tenant_info(log_dict)

            return GCPLogEntry(
                insert_id=entry.insert_id or "",
                timestamp=entry.timestamp or datetime.utcnow(),
                severity=entry.severity or "ERROR",
                log_name=entry.log_name or "",
                resource_type=log_dict["resource"]["type"],
                resource_labels=log_dict["resource"]["labels"],
                text_payload=log_dict.get("textPayload"),
                json_payload=log_dict.get("jsonPayload"),
                error_message=_extract_error_message(log_dict),
                stack_trace=_extract_stack_trace(log_dict),
                file_path=_extract_file_path(log_dict),
                service_name=_extract_service_name(log_dict),
                tenant_id=tenant_id,
                tenant_name=tenant_name,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to parse log entry: {e}")
            return None

    async def poll_once(self, callback) -> int:
        """
        Poll Cloud Logging once and process new errors.

        Args:
            callback: Async function to call with each new incident

        Returns:
            Number of new errors processed
        """
        import asyncio
        from datetime import timedelta
        import logging

        logger = logging.getLogger(__name__)

        client = self._get_client()

        # Build time filter - look back 5 minutes on first poll, then from last poll
        if self._last_poll_time:
            start_time = self._last_poll_time - timedelta(seconds=10)  # Small overlap
        else:
            start_time = datetime.utcnow() - timedelta(minutes=5)

        time_filter = f'timestamp>="{start_time.isoformat()}Z"'
        full_filter = f"{self.log_filter} AND {time_filter}"

        logger.info(f"Polling GCP logs with filter: {full_filter}")

        processed = 0
        try:
            # Run in thread pool since the client is synchronous
            def fetch_entries():
                return list(client.list_entries(
                    filter_=full_filter,
                    order_by="timestamp desc",
                    max_results=100,
                ))

            entries = await asyncio.get_event_loop().run_in_executor(None, fetch_entries)

            for entry in entries:
                # Skip if already processed
                if entry.insert_id in self._seen_insert_ids:
                    continue

                self._seen_insert_ids.add(entry.insert_id)

                # Keep seen IDs bounded
                if len(self._seen_insert_ids) > 10000:
                    # Remove oldest half
                    self._seen_insert_ids = set(list(self._seen_insert_ids)[-5000:])

                # Parse and create incident
                log_entry = self._parse_log_entry_direct(entry)
                if log_entry:
                    incident = create_incident_from_log(log_entry)
                    await callback(incident)
                    processed += 1
                    logger.info(f"Created incident from polled log: {incident.id}")

        except Exception as e:
            logger.error(f"Error polling GCP logs: {e}")

        self._last_poll_time = datetime.utcnow()
        return processed

    async def start_polling(self, callback, interval_seconds: int = 30):
        """
        Start polling Cloud Logging API for errors.

        Args:
            callback: Async function to call with each new incident
            interval_seconds: How often to poll (default 30s)
        """
        import asyncio
        import logging

        logger = logging.getLogger(__name__)

        if self._polling_active:
            logger.warning("Polling already active")
            return

        self._polling_active = True
        logger.info(f"Starting GCP log polling every {interval_seconds}s")

        async def poll_loop():
            while self._polling_active:
                try:
                    count = await self.poll_once(callback)
                    if count > 0:
                        logger.info(f"Processed {count} new errors from GCP")
                except Exception as e:
                    logger.error(f"Polling error: {e}")

                await asyncio.sleep(interval_seconds)

        self._polling_task = asyncio.create_task(poll_loop())

    async def stop_polling(self):
        """Stop polling Cloud Logging API."""
        import asyncio
        import logging
        logger = logging.getLogger(__name__)

        self._polling_active = False
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        logger.info("Stopped GCP log polling")

    @property
    def is_polling(self) -> bool:
        """Check if polling is active."""
        return self._polling_active
