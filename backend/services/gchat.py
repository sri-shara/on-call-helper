"""
Google Chat integration for On Call Helper.

Parses structured alert messages from Google Chat into Incident objects.
Google Chat App sends interaction events to POST /webhook/gchat.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel

from backend.models import Incident, Severity, IncidentStatus
from backend.services.gcp_logging import generate_incident_id

logger = logging.getLogger(__name__)


class GChatEvent(BaseModel):
    """Parsed Google Chat interaction event."""
    event_type: str
    space_id: str
    space_name: Optional[str] = None
    message_id: str
    thread_id: Optional[str] = None
    sender_name: Optional[str] = None
    sender_type: Optional[str] = None
    text: str
    create_time: Optional[datetime] = None


class ParsedAlert(BaseModel):
    """Structured alert parsed from chat message text."""
    title: str
    error_message: str
    service_name: str = "unknown"
    severity: Severity = Severity.MEDIUM
    stack_trace: Optional[str] = None
    file_path: Optional[str] = None
    tenant_name: Optional[str] = None
    environment: str = "production"


def parse_gchat_event(data: Dict[str, Any]) -> GChatEvent:
    """Parse raw Google Chat interaction event JSON into GChatEvent."""
    event_type = data.get("type", "UNKNOWN")

    space = data.get("space", {})
    space_id = space.get("name", "")
    space_name = space.get("displayName")

    message = data.get("message", {})
    message_id = message.get("name", "")

    thread = message.get("thread", {})
    thread_id = thread.get("name") if thread else None

    sender = message.get("sender", {})
    sender_name = sender.get("displayName")
    sender_type = sender.get("type")

    text = message.get("argumentText") or message.get("text", "")

    create_time = None
    ct_str = message.get("createTime")
    if ct_str:
        try:
            create_time = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return GChatEvent(
        event_type=event_type,
        space_id=space_id,
        space_name=space_name,
        message_id=message_id,
        thread_id=thread_id,
        sender_name=sender_name,
        sender_type=sender_type,
        text=text.strip(),
        create_time=create_time,
    )


def _parse_severity(text: str) -> Severity:
    """Map severity/priority strings to Severity enum."""
    t = text.upper().strip()
    if t in ("CRITICAL", "P1", "SEV1", "EMERGENCY", "CRIT"):
        return Severity.CRITICAL
    if t in ("HIGH", "P2", "SEV2", "ERROR", "MAJOR"):
        return Severity.HIGH
    if t in ("MEDIUM", "P3", "SEV3", "WARNING", "WARN", "MINOR"):
        return Severity.MEDIUM
    return Severity.LOW


def _extract_field(text: str, keys: list) -> Optional[str]:
    """Extract a field value from key-value formatted text.

    Supports formats:
    - Key: value
    - **Key**: value
    - Key = value
    """
    for key in keys:
        # Key: value (possibly with ** markdown bold)
        pattern = rf'(?:\*\*)?{re.escape(key)}(?:\*\*)?[\s]*[:=]\s*(.+)'
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return None


def parse_alert_text(text: str) -> ParsedAlert:
    """Parse structured alert text from chat message into ParsedAlert.

    Handles multiple formats:
    1. Key-value format: "Service: foo\\nSeverity: HIGH\\nError: ..."
    2. Markdown format: "**Service**: foo"
    3. Plain text fallback: first line = title, full text = error_message
    """
    # Extract structured fields
    service = _extract_field(text, ["Service", "Service Name", "App", "Application"])
    severity_str = _extract_field(text, ["Severity", "Priority", "Sev", "Level"])
    error = _extract_field(text, ["Error", "Message", "Description", "Details", "Alert"])
    stack_trace = _extract_field(text, ["Stack Trace", "Stacktrace", "Traceback"])
    file_path = _extract_field(text, ["File", "File Path", "Source"])
    tenant = _extract_field(text, ["Tenant", "Customer", "Org", "Organization"])
    env = _extract_field(text, ["Environment", "Env"])
    title_field = _extract_field(text, ["Title", "Summary", "Subject", "Alert Name"])

    # Build title
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if title_field:
        title = title_field
    elif error:
        # Use error message as title when structured fields are present
        title = error if len(error) <= 120 else error[:117] + "..."
    elif lines:
        # Use first non-key-value line as title
        first_line = lines[0]
        # Skip lines that look like "Key: value" structured data
        if re.match(r'^[\w\s]+:\s', first_line) and len(lines) > 1:
            for line in lines:
                if not re.match(r'^[\w\s]+:\s', line):
                    first_line = line
                    break
        title = re.sub(r'^[#*\s]+', '', first_line).strip()
        if len(title) > 120:
            title = title[:117] + "..."
    else:
        title = "Google Chat Alert"

    # Build error message
    if error:
        error_message = error
    elif len(lines) > 1:
        # Skip the title line, use rest as error message
        error_message = "\n".join(lines[1:])
    else:
        error_message = text.strip()

    # Parse severity
    severity = _parse_severity(severity_str) if severity_str else Severity.MEDIUM

    return ParsedAlert(
        title=title,
        error_message=error_message,
        service_name=service or "unknown",
        severity=severity,
        stack_trace=stack_trace,
        file_path=file_path,
        tenant_name=tenant,
        environment=env or "production",
    )


def create_incident_from_gchat(event: GChatEvent, alert: ParsedAlert) -> Incident:
    """Create an Incident object from a parsed GChat event and alert."""
    return Incident(
        id=generate_incident_id(),
        title=alert.title,
        error_message=alert.error_message,
        stack_trace=alert.stack_trace,
        file_path=alert.file_path,
        service_name=alert.service_name,
        severity=alert.severity,
        tenant_name=alert.tenant_name,
        environment=alert.environment,
        status=IncidentStatus.ACTIVE,
        created_at=event.create_time or datetime.utcnow(),
        source="gchat",
        gchat_metadata={
            "space_id": event.space_id,
            "thread_id": event.thread_id,
            "message_id": event.message_id,
            "sender_name": event.sender_name,
            "sender_type": event.sender_type,
            "space_name": event.space_name,
        },
    )
