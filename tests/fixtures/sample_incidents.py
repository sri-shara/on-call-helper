"""
Sample incidents for testing triage agent.

These represent realistic error scenarios from the Nucleus MDR platform.
"""

from backend.models import Incident, Severity


def create_null_pointer_incident() -> Incident:
    """Create a typical null pointer exception incident."""
    return Incident(
        id="OCH-TEST001",
        title="NullPointerException in caseservice",
        error_message="panic: runtime error: invalid memory address or nil pointer dereference",
        stack_trace="""goroutine 1 [running]:
main.processCase(0x0, 0xc0000b4000)
    /backend/services/caseservice/handler.go:142 +0x45
main.HandleCaseRequest(0xc0000a8000)
    /backend/services/caseservice/handler.go:89 +0x123
main.main()
    /backend/services/caseservice/main.go:45 +0x567""",
        service_name="caseservice",
        file_path="/backend/services/caseservice/handler.go",
        severity=Severity.HIGH,
        environment="production",
    )


def create_database_connection_incident() -> Incident:
    """Create an AlloyDB connection issue incident."""
    return Incident(
        id="OCH-TEST002",
        title="AlloyDB connection pool exhausted",
        error_message="failed to acquire connection from pool: pool exhausted, max connections: 100, current: 100",
        stack_trace="""goroutine 156 [running]:
database/sql.(*DB).conn(0xc0001b4000, 0x1, 0x0, 0x0)
    /usr/local/go/src/database/sql/sql.go:1234 +0x567
github.com/tenex/nucleus/backend/services/alertservice.(*Service).ProcessAlert(...)
    /backend/services/alertservice/service.go:67 +0x89""",
        service_name="alertservice",
        severity=Severity.CRITICAL,
        environment="production",
    )


def create_pubsub_backlog_incident() -> Incident:
    """Create a Pub/Sub backlog incident."""
    return Incident(
        id="OCH-TEST003",
        title="Pub/Sub subscription backlog growing",
        error_message="subscription projects/nucleus-prod/subscriptions/alert-processor has 50000 unacked messages, oldest: 30m",
        service_name="alert-processor",
        severity=Severity.HIGH,
        environment="production",
    )


def create_timeout_incident() -> Incident:
    """Create a transient timeout incident."""
    return Incident(
        id="OCH-TEST004",
        title="Context deadline exceeded",
        error_message="context deadline exceeded while waiting for response from secops API",
        stack_trace="""goroutine 234 [running]:
context.(*timerCtx).Err(...)
    /usr/local/go/src/context/context.go:456 +0x45""",
        service_name="secops-integration",
        severity=Severity.MEDIUM,
        environment="production",
    )


def create_json_parsing_incident() -> Incident:
    """Create a JSON parsing error incident."""
    return Incident(
        id="OCH-TEST005",
        title="JSON unmarshal error in tenant API",
        error_message="json: cannot unmarshal string into Go struct field Alert.severity of type int",
        stack_trace="""goroutine 78 [running]:
encoding/json.(*UnmarshalTypeError).Error(0xc0002b4000, 0x15, 0x0)
    /usr/local/go/src/encoding/json/decode.go:165 +0x123
github.com/tenex/nucleus/backend/services/tenantapi.(*Handler).CreateAlert(...)
    /backend/services/tenantapi/handler.go:234 +0x456""",
        service_name="tenantapi",
        file_path="/backend/services/tenantapi/handler.go",
        severity=Severity.HIGH,
        environment="production",
    )


def create_rate_limit_incident() -> Incident:
    """Create a rate limit incident (transient)."""
    return Incident(
        id="OCH-TEST006",
        title="VirusTotal API rate limited",
        error_message="RESOURCE_EXHAUSTED: API quota exceeded for VirusTotal, retry after 60s",
        service_name="enrichment-service",
        severity=Severity.LOW,
        environment="production",
    )


def create_index_out_of_bounds_incident() -> Incident:
    """Create an index out of bounds error."""
    return Incident(
        id="OCH-TEST007",
        title="Index out of range in alert processor",
        error_message="runtime error: index out of range [5] with length 3",
        stack_trace="""goroutine 45 [running]:
github.com/tenex/nucleus/backend/services/alertprocessor.processAlerts(0xc0001a8000, 0x3, 0x3)
    /backend/services/alertprocessor/processor.go:89 +0x234
github.com/tenex/nucleus/backend/services/alertprocessor.(*Worker).Run(...)
    /backend/services/alertprocessor/worker.go:56 +0x123""",
        service_name="alertprocessor",
        file_path="/backend/services/alertprocessor/processor.go",
        severity=Severity.HIGH,
        environment="production",
    )


def create_cloud_run_memory_incident() -> Incident:
    """Create a Cloud Run memory limit incident."""
    return Incident(
        id="OCH-TEST008",
        title="Cloud Run instance OOM killed",
        error_message="Container memory limit exceeded. Container was killed due to out of memory (OOM). Consider increasing memory limits.",
        service_name="ml-inference",
        severity=Severity.CRITICAL,
        environment="production",
    )


def create_demo_tenant_incident() -> Incident:
    """Create an incident from a demo tenant (should be filtered)."""
    return Incident(
        id="OCH-TEST009",
        title="Error in demo tenant",
        error_message="Something went wrong processing alert",
        service_name="alertservice",
        tenant_name="tenex-poc",
        severity=Severity.HIGH,
        environment="production",
    )


def create_complex_incident() -> Incident:
    """Create a complex incident requiring human analysis."""
    return Incident(
        id="OCH-TEST010",
        title="Intermittent data inconsistency in case sync",
        error_message="Case sync detected inconsistency: local case count (1523) differs from remote (1520) for tenant horizontal-dc. Difference persists after 3 retries.",
        stack_trace="""goroutine 89 [running]:
github.com/tenex/nucleus/backend/services/casesync.(*Syncer).ValidateSync(...)
    /backend/services/casesync/syncer.go:234 +0x567
github.com/tenex/nucleus/backend/services/casesync.(*Syncer).FullSync(...)
    /backend/services/casesync/syncer.go:89 +0x234""",
        service_name="casesync",
        tenant_name="horizontal-dc",
        severity=Severity.HIGH,
        environment="production",
    )


# Sample Claude responses for mocking

SAMPLE_FIXABLE_RESPONSE = """{
    "classification": "FIXABLE",
    "confidence": 0.85,
    "root_cause": "Null pointer dereference in processCase function. The function doesn't check if the case parameter is nil before accessing its fields.",
    "service_name": "caseservice",
    "file_path": "/backend/services/caseservice/handler.go",
    "function_name": "processCase",
    "code_snippet": "func processCase(c *Case) {\\n    result := c.GetStatus()  // nil check missing\\n}",
    "line_numbers": [142, 145],
    "suggested_fix": "Add nil check before accessing case fields: if c == nil { return ErrNilCase }",
    "related_context": ["Similar nil checks exist in other handlers", "This pattern caused 3 incidents this week"]
}"""

SAMPLE_INFRA_RESPONSE = """{
    "classification": "INFRA_ISSUE",
    "confidence": 0.92,
    "root_cause": "AlloyDB connection pool is exhausted due to connection leak or insufficient pool size for current load.",
    "runbook_reference": "runbooks/alloydb.md",
    "manual_steps": [
        "Check current connection count: SELECT count(*) FROM pg_stat_activity",
        "Identify long-running queries: SELECT * FROM pg_stat_activity WHERE state != 'idle'",
        "Consider increasing max_connections or adding read replicas",
        "Check for connection leaks in recent deployments"
    ],
    "related_context": ["Connection pool was last increased 2 months ago", "Traffic has grown 40% since then"]
}"""

SAMPLE_TRANSIENT_RESPONSE = """{
    "classification": "TRANSIENT",
    "confidence": 0.95,
    "root_cause": "Context deadline exceeded due to temporary network latency or SecOps API slowdown. This is a known transient pattern that self-resolves.",
    "related_context": ["SecOps API has P99 latency of 2s", "Current timeout is 5s", "Error rate is below 0.1%"]
}"""

SAMPLE_NEEDS_HUMAN_RESPONSE = """{
    "classification": "NEEDS_HUMAN",
    "confidence": 0.6,
    "root_cause": "Data inconsistency between local and remote case counts. This could be caused by race conditions in sync, network partitions, or data corruption. Requires investigation of both systems.",
    "manual_steps": [
        "Compare case IDs in both systems to find missing cases",
        "Check sync logs for errors during the affected time period",
        "Verify no manual case deletions occurred"
    ],
    "related_context": ["This tenant has high case volume", "Sync runs every 5 minutes"]
}"""
