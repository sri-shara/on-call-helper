"""
Sample triage results for testing fixer agent.

These represent realistic triage outputs for various bug types in Nucleus.
"""

from backend.models import TriageResult, TriageClassification


def create_nil_pointer_triage() -> TriageResult:
    """Create triage result for a nil pointer dereference bug."""
    return TriageResult(
        incident_id="OCH-TEST001",
        classification=TriageClassification.FIXABLE,
        root_cause="Nil pointer dereference in processCase function. The function doesn't check if the case parameter is nil before accessing its fields, causing a panic when nil is passed.",
        confidence=0.85,
        service_name="caseservice",
        file_path="backend/services/caseservice/handler.go",
        function_name="processCase",
        code_snippet="func processCase(c *Case) error {\n    result := c.GetStatus()  // nil check missing\n    return result\n}",
        suggested_fix="Add nil check before accessing case fields: if c == nil { return ErrNilCase }",
    )


def create_index_out_of_bounds_triage() -> TriageResult:
    """Create triage result for an index out of bounds bug."""
    return TriageResult(
        incident_id="OCH-TEST002",
        classification=TriageClassification.FIXABLE,
        root_cause="Index out of bounds when accessing alerts slice. The code assumes at least one alert exists without checking the slice length.",
        confidence=0.90,
        service_name="alertprocessor",
        file_path="backend/services/alertprocessor/processor.go",
        function_name="processAlerts",
        code_snippet="func processAlerts(alerts []*Alert) error {\n    firstAlert := alerts[0]  // no length check\n    return process(firstAlert)\n}",
        suggested_fix="Add length check before accessing slice: if len(alerts) == 0 { return ErrNoAlerts }",
    )


def create_json_unmarshal_triage() -> TriageResult:
    """Create triage result for a JSON unmarshal type error."""
    return TriageResult(
        incident_id="OCH-TEST003",
        classification=TriageClassification.FIXABLE,
        root_cause="JSON unmarshal fails because Alert.severity field is defined as int but API returns string. Type mismatch causes unmarshal error.",
        confidence=0.88,
        service_name="tenantapi",
        file_path="backend/services/tenantapi/handler.go",
        function_name="CreateAlert",
        code_snippet="type Alert struct {\n    Severity int `json:\"severity\"`  // should be string\n}",
        suggested_fix="Change Severity field type from int to string to match API response",
    )


def create_missing_error_handling_triage() -> TriageResult:
    """Create triage result for missing error handling."""
    return TriageResult(
        incident_id="OCH-TEST004",
        classification=TriageClassification.FIXABLE,
        root_cause="Database query error is ignored, causing nil pointer dereference when result is used. The error from db.Query is not checked.",
        confidence=0.92,
        service_name="caseservice",
        file_path="backend/services/caseservice/repository.go",
        function_name="GetCaseByID",
        code_snippet="func (r *Repository) GetCaseByID(id string) (*Case, error) {\n    row := r.db.QueryRow(query, id)\n    var c Case\n    row.Scan(&c.ID, &c.Title)  // error ignored\n    return &c, nil\n}",
        suggested_fix="Check and return error from row.Scan: if err := row.Scan(...); err != nil { return nil, err }",
    )


def create_race_condition_triage() -> TriageResult:
    """Create triage result for a race condition bug."""
    return TriageResult(
        incident_id="OCH-TEST005",
        classification=TriageClassification.FIXABLE,
        root_cause="Race condition in counter increment. Multiple goroutines access shared counter without synchronization, causing data races.",
        confidence=0.78,
        service_name="metrics",
        file_path="backend/services/metrics/counter.go",
        function_name="Increment",
        code_snippet="func (c *Counter) Increment() {\n    c.value++  // not thread-safe\n}",
        suggested_fix="Use atomic operations or mutex: atomic.AddInt64(&c.value, 1)",
    )


def create_infra_issue_triage() -> TriageResult:
    """Create triage result for an infrastructure issue (non-fixable)."""
    return TriageResult(
        incident_id="OCH-TEST006",
        classification=TriageClassification.INFRA_ISSUE,
        root_cause="AlloyDB connection pool exhausted due to connection leak or insufficient pool size for current load.",
        confidence=0.92,
        runbook_reference="runbooks/alloydb.md",
        manual_steps=[
            "Check current connection count: SELECT count(*) FROM pg_stat_activity",
            "Identify long-running queries: SELECT * FROM pg_stat_activity WHERE state != 'idle'",
            "Consider increasing max_connections or adding read replicas",
        ],
    )


def create_needs_human_triage() -> TriageResult:
    """Create triage result requiring human analysis."""
    return TriageResult(
        incident_id="OCH-TEST007",
        classification=TriageClassification.NEEDS_HUMAN,
        root_cause="Data inconsistency between local and remote case counts. Could be race condition, network partition, or data corruption. Requires investigation of both systems.",
        confidence=0.55,
        manual_steps=[
            "Compare case IDs in both systems",
            "Check sync logs for errors",
            "Verify no manual deletions occurred",
        ],
    )


# Sample source code for mocking GitHub responses

SAMPLE_HANDLER_GO = '''package caseservice

import (
    "context"
    "errors"
)

var ErrNilCase = errors.New("case is nil")

type Case struct {
    ID     string
    Title  string
    Status string
}

func (c *Case) GetStatus() string {
    return c.Status
}

func processCase(c *Case) error {
    result := c.GetStatus()
    if result == "" {
        return errors.New("empty status")
    }
    return nil
}

func HandleCaseRequest(ctx context.Context, caseID string) error {
    c, err := fetchCase(ctx, caseID)
    if err != nil {
        return err
    }
    return processCase(c)
}

func fetchCase(ctx context.Context, id string) (*Case, error) {
    // Simulated fetch
    return &Case{ID: id, Title: "Test", Status: "open"}, nil
}
'''

SAMPLE_PROCESSOR_GO = '''package alertprocessor

import (
    "errors"
)

var ErrNoAlerts = errors.New("no alerts to process")

type Alert struct {
    ID       string
    Severity string
    Message  string
}

func processAlerts(alerts []*Alert) error {
    firstAlert := alerts[0]
    return process(firstAlert)
}

func process(alert *Alert) error {
    if alert == nil {
        return errors.New("nil alert")
    }
    // Process the alert
    return nil
}
'''

SAMPLE_REPOSITORY_GO = '''package caseservice

import (
    "database/sql"
)

type Repository struct {
    db *sql.DB
}

func (r *Repository) GetCaseByID(id string) (*Case, error) {
    query := "SELECT id, title FROM cases WHERE id = $1"
    row := r.db.QueryRow(query, id)
    var c Case
    row.Scan(&c.ID, &c.Title)
    return &c, nil
}
'''

# Sample Claude responses for mocking

SAMPLE_NIL_CHECK_FIX_RESPONSE = '''{
    "file_path": "backend/services/caseservice/handler.go",
    "original_code": "func processCase(c *Case) error {\\n    result := c.GetStatus()\\n    if result == \\"\\" {\\n        return errors.New(\\"empty status\\")\\n    }\\n    return nil\\n}",
    "fixed_code": "func processCase(c *Case) error {\\n    if c == nil {\\n        return ErrNilCase\\n    }\\n    result := c.GetStatus()\\n    if result == \\"\\" {\\n        return errors.New(\\"empty status\\")\\n    }\\n    return nil\\n}",
    "explanation": "Added nil check at the beginning of the function to prevent panic when a nil Case pointer is passed. The function now returns ErrNilCase early if the case is nil, before attempting to call any methods on it.",
    "diff_summary": "Added nil check for case parameter"
}'''

SAMPLE_BOUNDS_CHECK_FIX_RESPONSE = '''{
    "file_path": "backend/services/alertprocessor/processor.go",
    "original_code": "func processAlerts(alerts []*Alert) error {\\n    firstAlert := alerts[0]\\n    return process(firstAlert)\\n}",
    "fixed_code": "func processAlerts(alerts []*Alert) error {\\n    if len(alerts) == 0 {\\n        return ErrNoAlerts\\n    }\\n    firstAlert := alerts[0]\\n    return process(firstAlert)\\n}",
    "explanation": "Added length check before accessing the first element of the alerts slice. This prevents index out of bounds panic when an empty slice is passed to the function.",
    "diff_summary": "Added slice length check before index access"
}'''

SAMPLE_ERROR_HANDLING_FIX_RESPONSE = '''{
    "file_path": "backend/services/caseservice/repository.go",
    "original_code": "func (r *Repository) GetCaseByID(id string) (*Case, error) {\\n    query := \\"SELECT id, title FROM cases WHERE id = $1\\"\\n    row := r.db.QueryRow(query, id)\\n    var c Case\\n    row.Scan(&c.ID, &c.Title)\\n    return &c, nil\\n}",
    "fixed_code": "func (r *Repository) GetCaseByID(id string) (*Case, error) {\\n    query := \\"SELECT id, title FROM cases WHERE id = $1\\"\\n    row := r.db.QueryRow(query, id)\\n    var c Case\\n    if err := row.Scan(&c.ID, &c.Title); err != nil {\\n        return nil, err\\n    }\\n    return &c, nil\\n}",
    "explanation": "Added proper error handling for the row.Scan call. The error is now checked and returned if scanning fails, preventing nil pointer issues when the query returns no results or encounters an error.",
    "diff_summary": "Added error handling for database scan operation"
}'''

SAMPLE_FIX_WITH_RETRY_RESPONSE = '''{
    "file_path": "backend/services/caseservice/handler.go",
    "original_code": "func processCase(c *Case) error {\\n    result := c.GetStatus()\\n    if result == \\"\\" {\\n        return errors.New(\\"empty status\\")\\n    }\\n    return nil\\n}",
    "fixed_code": "func processCase(c *Case) error {\\n    if c == nil {\\n        return ErrNilCase\\n    }\\n    result := c.GetStatus()\\n    if result == \\"\\" {\\n        return fmt.Errorf(\\"case %s has empty status\\", c.ID)\\n    }\\n    return nil\\n}",
    "explanation": "Added nil check and improved error message to include case ID for better debugging. Addresses CodeRabbit feedback about error context.",
    "diff_summary": "Added nil check and improved error context"
}'''
