"""
Data models for On Call Helper.

These models represent all entities in the incident response pipeline:
- Incidents from GCP Cloud Logging
- Triage results from Claude analysis
- Fix results from code generation
- Review results from CodeRabbit
- Test results from sandbox execution
- Verification results from production monitoring
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Incident severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    """Incident lifecycle status."""

    ACTIVE = "active"  # Just created, waiting for processing
    TRIAGING = "triaging"  # Being analyzed by triage agent
    FIXING = "fixing"  # Fix being generated
    REVIEWING = "reviewing"  # CodeRabbit reviewing the fix
    TESTING = "testing"  # Running in sandbox
    PR_CREATED = "pr_created"  # PR created, awaiting merge
    VERIFYING = "verifying"  # Monitoring production post-merge
    FIXED = "fixed"  # Successfully resolved
    ESCALATED = "escalated"  # Escalated to human
    FILTERED = "filtered"  # Filtered out (transient/demo tenant)


class TriageClassification(str, Enum):
    """Classification of the incident by triage agent."""

    FIXABLE = "fixable"  # Code bug that can be auto-fixed
    INFRA_ISSUE = "infra_issue"  # Infrastructure problem (AlloyDB, Pub/Sub, etc.)
    TRANSIENT = "transient"  # Self-healing error
    NEEDS_HUMAN = "needs_human"  # Too complex for automated fix


class Incident(BaseModel):
    """
    A production incident detected from GCP Cloud Logging.

    Represents an error that occurred in the Nucleus MDR platform
    and needs to be triaged and potentially fixed.
    """

    id: str = Field(..., description="Unique ID in format OCH-{8chars}")
    title: str = Field(..., description="Brief error summary")
    error_message: str = Field(..., description="Full error message")
    stack_trace: Optional[str] = Field(None, description="Stack trace if available")
    file_path: Optional[str] = Field(None, description="File where error originated")
    service_name: str = Field(..., description="Nucleus service name")
    severity: Severity = Field(..., description="Incident severity")
    tenant_name: Optional[str] = Field(None, description="Tenant name if available")
    environment: str = Field("production", description="Environment (production/staging)")
    status: IncidentStatus = Field(IncidentStatus.ACTIVE, description="Current status")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = Field(None, description="When incident was resolved")

    # Metadata from GCP
    gcp_insert_id: Optional[str] = Field(None, description="GCP log insertId for dedup")
    gcp_resource_type: Optional[str] = Field(None, description="GCP resource type")
    gcp_log_name: Optional[str] = Field(None, description="GCP log name")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class TriageResult(BaseModel):
    """
    Result of triage analysis by Claude agent.

    Contains the classification of the incident and detailed analysis
    to guide the fix generation or escalation.
    """

    incident_id: str = Field(..., description="Reference to incident")
    classification: TriageClassification = Field(..., description="Incident classification")
    root_cause: str = Field(..., description="Detailed root cause explanation")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0-1")

    # For FIXABLE classification
    service_name: Optional[str] = Field(None, description="Affected service")
    file_path: Optional[str] = Field(None, description="File containing the bug")
    function_name: Optional[str] = Field(None, description="Function with the bug")
    code_snippet: Optional[str] = Field(None, description="Problematic code excerpt")
    line_numbers: Optional[Tuple[int, int]] = Field(None, description="Start/end line numbers")
    suggested_fix: Optional[str] = Field(None, description="High-level fix description")

    # For INFRA_ISSUE classification
    runbook_reference: Optional[str] = Field(None, description="Runbook to follow")
    manual_steps: Optional[List[str]] = Field(None, description="Manual intervention steps")

    # Metadata
    related_context: List[str] = Field(default_factory=list, description="Related patterns/warnings")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FixResult(BaseModel):
    """
    Result of fix generation by Claude agent.

    Contains the original and fixed code along with explanation
    of the changes made.
    """

    incident_id: str = Field(..., description="Reference to incident")
    file_path: str = Field(..., description="File being fixed")
    original_code: str = Field(..., description="The buggy code section")
    fixed_code: str = Field(..., description="The corrected code section")
    explanation: str = Field(..., description="Why this fix works")
    diff_summary: str = Field(..., description="Brief description of changes")
    iteration: int = Field(1, ge=1, le=3, description="CodeRabbit iteration (1-3)")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewIssue(BaseModel):
    """A single issue found by CodeRabbit."""

    severity: str = Field(..., description="Issue severity (critical, high, medium, low)")
    message: str = Field(..., description="Issue description")
    line: Optional[int] = Field(None, description="Line number if available")
    suggestion: Optional[str] = Field(None, description="Suggested fix")


class ReviewResult(BaseModel):
    """
    Result of CodeRabbit code review.

    Indicates whether the fix passed review and any issues found.
    """

    passed: bool = Field(..., description="Whether review passed (no blocking issues)")
    issues: List[ReviewIssue] = Field(default_factory=list, description="Issues found")
    suggestions: List[str] = Field(default_factory=list, description="Non-blocking suggestions")
    summary: str = Field("", description="Formatted feedback for Claude retry")


class TestResult(BaseModel):
    """
    Result of sandbox testing.

    Contains test execution results from the ephemeral Kind cluster.
    """

    incident_id: str = Field(..., description="Reference to incident")
    passed: bool = Field(..., description="Whether all tests passed")

    # Unit tests
    unit_tests_passed: Optional[bool] = Field(None, description="Unit tests passed")
    unit_tests_output: Optional[str] = Field(None, description="Unit test output")

    # Smoke tests
    smoke_tests_passed: Optional[bool] = Field(None, description="Smoke tests passed")
    smoke_tests_output: Optional[str] = Field(None, description="Smoke test output")

    # Metrics
    tests_run: int = Field(0, description="Total tests executed")
    tests_passed: int = Field(0, description="Tests that passed")
    tests_failed: int = Field(0, description="Tests that failed")
    duration_ms: int = Field(0, description="Total test duration in ms")
    coverage_percent: Optional[float] = Field(None, description="Code coverage if available")

    created_at: datetime = Field(default_factory=datetime.utcnow)


class VerificationStatus(str, Enum):
    """Status of production verification."""

    SUCCESS = "success"  # Error completely resolved or >90% reduction
    PARTIAL = "partial"  # Error reduced but not eliminated
    FAILED = "failed"  # Error persists or increased


class VerificationResult(BaseModel):
    """
    Result of production verification after PR merge.

    Confirms whether the fix actually resolved the issue in production.
    """

    incident_id: str = Field(..., description="Reference to incident")
    status: VerificationStatus = Field(..., description="Verification status")
    message: str = Field(..., description="Human-readable result")
    errors_before: int = Field(..., description="Error count before fix")
    errors_after: int = Field(..., description="Error count after fix")
    monitoring_duration_hours: int = Field(2, description="How long monitoring ran")
    pr_url: Optional[str] = Field(None, description="PR that was merged")
    created_at: datetime = Field(default_factory=datetime.utcnow)


# Event models for WebSocket broadcasting


class WebSocketEvent(BaseModel):
    """Base model for WebSocket events."""

    type: str = Field(..., description="Event type")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: Dict[str, Any] = Field(default_factory=dict, description="Event payload")


class Metrics(BaseModel):
    """Dashboard metrics."""

    total_incidents: int = Field(0, description="Total incidents received")
    auto_fixed: int = Field(0, description="Successfully auto-fixed")
    escalated: int = Field(0, description="Escalated to humans")
    filtered: int = Field(0, description="Filtered out")
    processing: int = Field(0, description="Currently processing")
    mttr_seconds: Optional[float] = Field(None, description="Mean time to resolution")
    success_rate: Optional[float] = Field(None, description="Success rate percentage")
