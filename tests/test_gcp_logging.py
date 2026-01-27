"""
Tests for GCP Cloud Logging service.

Verifies log parsing, incident creation, and webhook handling.
"""

import base64
import json
import pytest
from datetime import datetime

from backend.services.gcp_logging import (
    parse_pubsub_message,
    create_incident_from_log,
    generate_incident_id,
    GCPLogEntry,
)
from backend.models import Severity, IncidentStatus


class TestGenerateIncidentId:
    """Tests for incident ID generation."""

    def test_format(self):
        """Test incident ID has correct format."""
        incident_id = generate_incident_id()
        assert incident_id.startswith("OCH-")
        assert len(incident_id) == 12  # OCH- + 8 chars

    def test_uniqueness(self):
        """Test generated IDs are unique."""
        ids = [generate_incident_id() for _ in range(100)]
        assert len(set(ids)) == 100  # All unique


class TestParsePubSubMessage:
    """Tests for Pub/Sub message parsing."""

    def _create_pubsub_message(self, log_entry: dict) -> dict:
        """Helper to create a Pub/Sub push message."""
        encoded_data = base64.b64encode(
            json.dumps(log_entry).encode()
        ).decode()
        return {
            "message": {
                "data": encoded_data,
                "messageId": "12345",
                "publishTime": "2024-01-15T10:30:00Z",
            },
            "subscription": "projects/test/subscriptions/test-sub",
        }

    def test_parse_text_payload(self):
        """Test parsing log entry with textPayload."""
        log_entry = {
            "insertId": "abc123",
            "timestamp": "2024-01-15T10:30:00.123456Z",
            "severity": "ERROR",
            "logName": "projects/test/logs/run.googleapis.com",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {
                    "service_name": "caseservice",
                }
            },
            "textPayload": "panic: runtime error: nil pointer dereference",
        }

        result = parse_pubsub_message(self._create_pubsub_message(log_entry))

        assert result.insert_id == "abc123"
        assert result.severity == "ERROR"
        assert result.service_name == "caseservice"
        assert "nil pointer" in result.error_message

    def test_parse_json_payload(self):
        """Test parsing log entry with jsonPayload."""
        log_entry = {
            "insertId": "def456",
            "timestamp": "2024-01-15T10:30:00Z",
            "severity": "ERROR",
            "logName": "projects/test/logs/caseservice",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {
                    "service_name": "caseservice",
                }
            },
            "jsonPayload": {
                "message": "Database connection failed",
                "error": "connection refused",
                "stack_trace": "goroutine 1 [running]:\nmain.main()",
                "tenant_id": "abc-123",
                "tenant_name": "Whitney",
            },
        }

        result = parse_pubsub_message(self._create_pubsub_message(log_entry))

        assert result.insert_id == "def456"
        assert "Database connection failed" in result.error_message
        assert result.stack_trace is not None
        assert result.tenant_id == "abc-123"
        assert result.tenant_name == "Whitney"

    def test_parse_with_source_location(self):
        """Test parsing log entry with sourceLocation."""
        log_entry = {
            "insertId": "ghi789",
            "timestamp": "2024-01-15T10:30:00Z",
            "severity": "ERROR",
            "logName": "projects/test/logs/test",
            "resource": {"type": "cloud_run_revision", "labels": {}},
            "textPayload": "Error occurred",
            "sourceLocation": {
                "file": "/backend/services/caseservice/handler.go",
                "line": "142",
                "function": "processCase",
            },
        }

        result = parse_pubsub_message(self._create_pubsub_message(log_entry))

        assert result.file_path == "/backend/services/caseservice/handler.go"

    def test_extract_file_path_from_stack_trace(self):
        """Test extracting file path from Go stack trace."""
        log_entry = {
            "insertId": "jkl012",
            "timestamp": "2024-01-15T10:30:00Z",
            "severity": "ERROR",
            "logName": "projects/test/logs/test",
            "resource": {"type": "cloud_run_revision", "labels": {}},
            "textPayload": """panic: runtime error: invalid memory address
goroutine 1 [running]:
main.processCase()
	/backend/services/caseservice/handler.go:142 +0x45
main.main()
	/backend/main.go:50 +0x123""",
        }

        result = parse_pubsub_message(self._create_pubsub_message(log_entry))

        assert result.file_path == "/backend/services/caseservice/handler.go"
        assert result.stack_trace is not None

    def test_missing_message_field(self):
        """Test error on missing message field."""
        with pytest.raises(ValueError, match="Missing 'message' field"):
            parse_pubsub_message({})

    def test_missing_data_field(self):
        """Test error on missing data field."""
        with pytest.raises(ValueError, match="Missing 'data' field"):
            parse_pubsub_message({"message": {}})

    def test_invalid_base64_data(self):
        """Test error on invalid base64 data."""
        with pytest.raises(ValueError, match="Failed to decode"):
            parse_pubsub_message({
                "message": {
                    "data": "not-valid-base64!!!",
                }
            })

    def test_invalid_json_data(self):
        """Test error on invalid JSON in data."""
        encoded = base64.b64encode(b"not valid json").decode()
        with pytest.raises(ValueError, match="Failed to decode"):
            parse_pubsub_message({
                "message": {
                    "data": encoded,
                }
            })

    def test_extract_service_from_log_name(self):
        """Test extracting service name from logName."""
        log_entry = {
            "insertId": "test123",
            "timestamp": "2024-01-15T10:30:00Z",
            "severity": "ERROR",
            "logName": "projects/nucleus/logs/alertservice",
            "resource": {"type": "cloud_run_revision", "labels": {}},
            "textPayload": "Error",
        }

        result = parse_pubsub_message(self._create_pubsub_message(log_entry))

        assert result.service_name == "alertservice"

    def test_severity_mapping(self):
        """Test various GCP severity levels."""
        severities = [
            ("CRITICAL", "CRITICAL"),
            ("ERROR", "ERROR"),
            ("WARNING", "WARNING"),
            ("INFO", "INFO"),
        ]

        for gcp_severity, expected in severities:
            log_entry = {
                "insertId": f"test-{gcp_severity}",
                "timestamp": "2024-01-15T10:30:00Z",
                "severity": gcp_severity,
                "logName": "projects/test/logs/test",
                "resource": {"type": "cloud_run_revision", "labels": {}},
                "textPayload": "Error",
            }
            result = parse_pubsub_message(self._create_pubsub_message(log_entry))
            assert result.severity == expected


class TestCreateIncidentFromLog:
    """Tests for incident creation from log entries."""

    def test_create_basic_incident(self):
        """Test creating a basic incident."""
        log_entry = GCPLogEntry(
            insert_id="abc123",
            timestamp=datetime(2024, 1, 15, 10, 30, 0),
            severity="ERROR",
            log_name="projects/test/logs/caseservice",
            resource_type="cloud_run_revision",
            resource_labels={"service_name": "caseservice"},
            error_message="Database connection failed",
            service_name="caseservice",
        )

        incident = create_incident_from_log(log_entry)

        assert incident.id.startswith("OCH-")
        assert "caseservice" in incident.title
        assert incident.error_message == "Database connection failed"
        assert incident.service_name == "caseservice"
        assert incident.severity == Severity.HIGH
        assert incident.status == IncidentStatus.ACTIVE
        assert incident.gcp_insert_id == "abc123"

    def test_severity_mapping(self):
        """Test GCP severity is mapped to incident severity."""
        test_cases = [
            ("CRITICAL", Severity.CRITICAL),
            ("EMERGENCY", Severity.CRITICAL),
            ("ALERT", Severity.CRITICAL),
            ("ERROR", Severity.HIGH),
            ("WARNING", Severity.MEDIUM),
            ("INFO", Severity.LOW),
        ]

        for gcp_severity, expected_severity in test_cases:
            log_entry = GCPLogEntry(
                insert_id="test",
                timestamp=datetime.utcnow(),
                severity=gcp_severity,
                log_name="test",
                resource_type="test",
                resource_labels={},
                error_message="Test error",
                service_name="test",
            )

            incident = create_incident_from_log(log_entry)
            assert incident.severity == expected_severity, f"Failed for {gcp_severity}"

    def test_incident_with_stack_trace(self):
        """Test incident includes stack trace."""
        log_entry = GCPLogEntry(
            insert_id="test",
            timestamp=datetime.utcnow(),
            severity="ERROR",
            log_name="test",
            resource_type="test",
            resource_labels={},
            error_message="Panic occurred",
            stack_trace="goroutine 1 [running]:\nmain.main()",
            service_name="test",
        )

        incident = create_incident_from_log(log_entry)

        assert incident.stack_trace is not None
        assert "goroutine" in incident.stack_trace

    def test_incident_with_file_path(self):
        """Test incident includes file path."""
        log_entry = GCPLogEntry(
            insert_id="test",
            timestamp=datetime.utcnow(),
            severity="ERROR",
            log_name="test",
            resource_type="test",
            resource_labels={},
            error_message="Error",
            file_path="/backend/services/handler.go",
            service_name="test",
        )

        incident = create_incident_from_log(log_entry)

        assert incident.file_path == "/backend/services/handler.go"

    def test_incident_with_tenant(self):
        """Test incident includes tenant info."""
        log_entry = GCPLogEntry(
            insert_id="test",
            timestamp=datetime.utcnow(),
            severity="ERROR",
            log_name="test",
            resource_type="test",
            resource_labels={},
            error_message="Error",
            service_name="test",
            tenant_name="Whitney",
        )

        incident = create_incident_from_log(log_entry)

        assert incident.tenant_name == "Whitney"

    def test_title_generation(self):
        """Test incident title is generated correctly."""
        log_entry = GCPLogEntry(
            insert_id="test",
            timestamp=datetime.utcnow(),
            severity="ERROR",
            log_name="test",
            resource_type="test",
            resource_labels={},
            error_message="Error: connection refused to database",
            service_name="caseservice",
        )

        incident = create_incident_from_log(log_entry)

        assert "[caseservice]" in incident.title
        assert "connection refused" in incident.title

    def test_title_truncation(self):
        """Test long error messages are truncated in title."""
        long_message = "Error: " + "x" * 200

        log_entry = GCPLogEntry(
            insert_id="test",
            timestamp=datetime.utcnow(),
            severity="ERROR",
            log_name="test",
            resource_type="test",
            resource_labels={},
            error_message=long_message,
            service_name="test",
        )

        incident = create_incident_from_log(log_entry)

        # Title should be truncated
        assert len(incident.title) < 150


class TestWebhookEndpoint:
    """Tests for the webhook endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from fastapi.testclient import TestClient
        from backend.main import app
        return TestClient(app)

    @pytest.fixture
    def sample_pubsub_message(self):
        """Create a sample Pub/Sub message."""
        log_entry = {
            "insertId": "webhook-test-123",
            "timestamp": "2024-01-15T10:30:00Z",
            "severity": "ERROR",
            "logName": "projects/test/logs/caseservice",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "caseservice"}
            },
            "textPayload": "panic: nil pointer dereference",
        }
        encoded_data = base64.b64encode(
            json.dumps(log_entry).encode()
        ).decode()
        return {
            "message": {
                "data": encoded_data,
                "messageId": "12345",
            },
            "subscription": "projects/test/subscriptions/test-sub",
        }

    def test_webhook_creates_incident(self, client, sample_pubsub_message):
        """Test webhook creates incident for valid error."""
        response = client.post("/webhook/gcp-logs", json=sample_pubsub_message)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert "incident_id" in data
        assert data["incident_id"].startswith("OCH-")

    def test_webhook_filters_transient_error(self, client):
        """Test webhook filters transient errors."""
        log_entry = {
            "insertId": "transient-test",
            "timestamp": "2024-01-15T10:30:00Z",
            "severity": "ERROR",
            "logName": "projects/test/logs/test",
            "resource": {"type": "cloud_run_revision", "labels": {}},
            "textPayload": "Routing deadline expired",
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        message = {"message": {"data": encoded_data}}

        response = client.post("/webhook/gcp-logs", json=message)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "filtered"
        assert data["filter"] == "transient"

    def test_webhook_filters_demo_tenant(self, client):
        """Test webhook filters demo tenant errors."""
        log_entry = {
            "insertId": "demo-tenant-test",
            "timestamp": "2024-01-15T10:30:00Z",
            "severity": "ERROR",
            "logName": "projects/test/logs/test",
            "resource": {"type": "cloud_run_revision", "labels": {}},
            "jsonPayload": {
                "message": "Real error",
                "tenant_id": "04d3229f-7097-4af3-86df-37e29775d146",  # TENEX POC Demo
            },
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        message = {"message": {"data": encoded_data}}

        response = client.post("/webhook/gcp-logs", json=message)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "filtered"
        assert data["filter"] == "tenant"

    def test_webhook_invalid_json(self, client):
        """Test webhook rejects invalid JSON."""
        response = client.post(
            "/webhook/gcp-logs",
            content="not json",
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 400

    def test_webhook_invalid_pubsub_format(self, client):
        """Test webhook rejects invalid Pub/Sub format."""
        response = client.post("/webhook/gcp-logs", json={"invalid": "format"})

        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    def test_webhook_duplicate_detection(self, client):
        """Test webhook detects duplicate log entries."""
        import uuid

        # Create a unique log entry for this test
        unique_id = f"dup-test-{uuid.uuid4().hex[:8]}"
        log_entry = {
            "insertId": unique_id,
            "timestamp": "2024-01-15T10:30:00Z",
            "severity": "ERROR",
            "logName": "projects/test/logs/caseservice",
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"service_name": "caseservice"}
            },
            "textPayload": "panic: nil pointer dereference",
        }
        encoded_data = base64.b64encode(json.dumps(log_entry).encode()).decode()
        message = {"message": {"data": encoded_data, "messageId": "12345"}}

        # First request should succeed
        response1 = client.post("/webhook/gcp-logs", json=message)
        assert response1.status_code == 200
        assert response1.json()["status"] == "processing"

        # Second request with same insert_id should be detected as duplicate
        response2 = client.post("/webhook/gcp-logs", json=message)
        assert response2.status_code == 200
        assert response2.json()["status"] == "duplicate"


class TestTestWebhookEndpoint:
    """Tests for the test webhook endpoint."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from fastapi.testclient import TestClient
        from backend.main import app
        return TestClient(app)

    def test_test_webhook_creates_incident(self, client):
        """Test test webhook creates incident."""
        response = client.post("/webhook/test", json={
            "error_message": "Test error message",
            "service_name": "test-service",
            "severity": "ERROR",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert "incident_id" in data

    def test_test_webhook_filters_transient(self, client):
        """Test test webhook filters transient errors."""
        response = client.post("/webhook/test", json={
            "error_message": "Routing deadline expired",
            "service_name": "test-service",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "filtered"

    def test_test_webhook_filters_demo_tenant(self, client):
        """Test test webhook filters demo tenants."""
        response = client.post("/webhook/test", json={
            "error_message": "Real error",
            "service_name": "test-service",
            "tenant_name": "Demo Environment",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "filtered"
