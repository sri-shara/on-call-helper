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
    pr_url: Optional[str] = Field(None, description="Pull request URL if created")

    # Metadata from GCP
    gcp_insert_id: Optional[str] = Field(None, description="GCP log insertId for dedup")
    gcp_resource_type: Optional[str] = Field(None, description="GCP resource type")
    gcp_log_name: Optional[str] = Field(None, description="GCP log name")

    # Aggregation fields - for deduplication
    occurrence_count: int = Field(1, description="Number of times this error occurred")
    last_occurrence: Optional[datetime] = Field(None, description="Time of most recent occurrence")
    error_signature: Optional[str] = Field(None, description="Dedup signature for aggregation")

    # Auto-resolution fields - for transient errors
    auto_resolved: bool = Field(False, description="Was auto-resolved as transient")
    auto_resolve_reason: Optional[str] = Field(None, description="Why it was auto-resolved")

    # Source tracking
    source: str = Field("gcp", description="Incident source: 'gcp' or 'gchat'")
    gchat_metadata: Optional[Dict[str, Any]] = Field(None, description="Google Chat metadata: space_id, thread_id, message_id, sender")

    # Feedback tracking
    feedback_given: Optional[str] = Field(None, description="User feedback: 'not_needs_human' etc.")

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
    gcloud_commands: Optional[List[str]] = Field(None, description="Copy-paste gcloud/SQL commands for diagnosis")

    # Metadata
    related_context: List[str] = Field(default_factory=list, description="Related patterns/warnings")
    gcp_context: Optional[Dict[str, Any]] = Field(None, description="Additional context fetched from GCP logs")
    gcp_queries: Optional[List[str]] = Field(None, description="GCP queries used during triage")

    # Pre-analysis results from pattern matching and health checks
    pre_analysis: Optional[Dict[str, Any]] = Field(None, description="Pre-analysis results (patterns, tenant, infra)")
    tenant_type: Optional[str] = Field(None, description="Tenant type: production, demo, or unknown")

    # Pattern learning fields
    pattern_suggestion: Optional[Dict[str, Any]] = Field(None, description="Historical pattern match suggestion")
    override_reason: Optional[str] = Field(None, description="Reason if classification was overridden by pattern learning")

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
    processing: int = Field(0, description="Currently being processed")
    no_action_needed: int = Field(0, description="Self-healing errors (transient)")
    review_needed: int = Field(0, description="Requires human review")
    pr_raised: int = Field(0, description="PRs created for fixes")
    mttr_seconds: Optional[float] = Field(None, description="Mean time to resolution")


# Pattern Learning models


class FixRecord(BaseModel):
    """Record of a successful fix for a pattern."""

    incident_id: str = Field(..., description="Incident that was fixed")
    file_path: str = Field(..., description="File that was fixed")
    fix_explanation: str = Field(..., description="Explanation of the fix")
    pr_url: Optional[str] = Field(None, description="PR URL if available")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PatternRecord(BaseModel):
    """A learned error pattern with historical outcomes."""

    pattern_id: str = Field(..., description="MD5 signature from error_aggregator")
    error_template: str = Field(..., description="Normalized error message (first 200 chars)")
    service_name: str = Field(..., description="Service where pattern was observed")
    classifications: Dict[str, int] = Field(
        default_factory=dict,
        description="Count of each classification: {'fixable': 5, 'transient': 2}"
    )
    success_count: int = Field(0, description="Number of successful outcomes")
    failure_count: int = Field(0, description="Number of failed outcomes")
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)
    successful_fixes: List[FixRecord] = Field(
        default_factory=list,
        description="Recent successful fixes (limited to 10)"
    )

    @property
    def occurrence_count(self) -> int:
        """Total number of times this pattern was observed."""
        return sum(self.classifications.values())

    @property
    def most_common_classification(self) -> Optional[str]:
        """Most frequently assigned classification."""
        if not self.classifications:
            return None
        return max(self.classifications.keys(), key=lambda k: self.classifications[k])

    @property
    def success_rate(self) -> float:
        """Success rate of outcomes (0.0 to 1.0)."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.success_count / total


class PatternSuggestion(BaseModel):
    """Suggestion from pattern learning for a new incident."""

    pattern_id: str = Field(..., description="ID of the matched pattern")
    classification: str = Field(..., description="Suggested classification")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in suggestion")
    occurrence_count: int = Field(..., description="How many times this pattern was seen")
    success_rate: float = Field(..., ge=0.0, le=1.0, description="Historical success rate")
    suggested_fix: Optional[FixRecord] = Field(None, description="Most recent successful fix")


class HealthCheckRun(BaseModel):
    """A single execution of the oncall-checkout health check script."""

    id: str = Field(..., description="Run ID (hc-<uuid>)")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    status: str = Field("running", description="running | completed | failed | timeout")
    exit_code: Optional[int] = None
    duration_seconds: Optional[float] = None
    output: Optional[str] = None
