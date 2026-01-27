"""
In-memory storage for On Call Helper.

This is a simple in-memory storage for development and demo purposes.
For production, replace with PostgreSQL or similar persistent storage.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from backend.models import (
    Incident,
    IncidentStatus,
    TriageResult,
    FixResult,
    TestResult,
    VerificationResult,
    Metrics,
)


class Storage:
    """In-memory storage with metrics tracking."""

    def __init__(self):
        self._incidents: Dict[str, Incident] = {}
        self._triage_results: Dict[str, TriageResult] = {}
        self._fix_results: Dict[str, FixResult] = {}
        self._test_results: Dict[str, TestResult] = {}
        self._verification_results: Dict[str, VerificationResult] = {}

        # Deduplication tracking
        self._seen_gcp_insert_ids: Set[str] = set()

        # Metrics
        self._total_resolution_time_ms: int = 0

    # ═══════════════ Incidents ═══════════════

    def save_incident(self, incident: Incident) -> None:
        """Save an incident."""
        self._incidents[incident.id] = incident
        if incident.gcp_insert_id:
            self._seen_gcp_insert_ids.add(incident.gcp_insert_id)

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Get an incident by ID."""
        return self._incidents.get(incident_id)

    def update_incident_status(
        self,
        incident_id: str,
        status: IncidentStatus,
        resolved_at: Optional[datetime] = None
    ) -> Optional[Incident]:
        """Update an incident's status."""
        incident = self._incidents.get(incident_id)
        if incident:
            incident.status = status
            if resolved_at:
                incident.resolved_at = resolved_at
                # Track resolution time
                delta = resolved_at - incident.created_at
                self._total_resolution_time_ms += int(delta.total_seconds() * 1000)
        return incident

    def list_incidents(
        self,
        status: Optional[IncidentStatus] = None,
        limit: int = 100
    ) -> List[Incident]:
        """List incidents, optionally filtered by status."""
        incidents = list(self._incidents.values())
        if status:
            incidents = [i for i in incidents if i.status == status]
        # Sort by created_at descending
        incidents.sort(key=lambda i: i.created_at, reverse=True)
        return incidents[:limit]

    def is_duplicate(self, gcp_insert_id: str) -> bool:
        """Check if we've already processed this GCP log entry."""
        return gcp_insert_id in self._seen_gcp_insert_ids

    # ═══════════════ Triage Results ═══════════════

    def save_triage_result(self, result: TriageResult) -> None:
        """Save a triage result."""
        self._triage_results[result.incident_id] = result

    def get_triage_result(self, incident_id: str) -> Optional[TriageResult]:
        """Get triage result for an incident."""
        return self._triage_results.get(incident_id)

    # ═══════════════ Fix Results ═══════════════

    def save_fix_result(self, result: FixResult) -> None:
        """Save a fix result."""
        self._fix_results[result.incident_id] = result

    def get_fix_result(self, incident_id: str) -> Optional[FixResult]:
        """Get fix result for an incident."""
        return self._fix_results.get(incident_id)

    # ═══════════════ Test Results ═══════════════

    def save_test_result(self, result: TestResult) -> None:
        """Save a test result."""
        self._test_results[result.incident_id] = result

    def get_test_result(self, incident_id: str) -> Optional[TestResult]:
        """Get test result for an incident."""
        return self._test_results.get(incident_id)

    # ═══════════════ Verification Results ═══════════════

    def save_verification_result(self, result: VerificationResult) -> None:
        """Save a verification result."""
        self._verification_results[result.incident_id] = result

    def get_verification_result(self, incident_id: str) -> Optional[VerificationResult]:
        """Get verification result for an incident."""
        return self._verification_results.get(incident_id)

    # ═══════════════ Metrics ═══════════════

    def get_metrics(self) -> Metrics:
        """Calculate and return current metrics."""
        incidents = list(self._incidents.values())

        total = len(incidents)
        auto_fixed = sum(1 for i in incidents if i.status == IncidentStatus.FIXED)
        escalated = sum(1 for i in incidents if i.status == IncidentStatus.ESCALATED)
        filtered = sum(1 for i in incidents if i.status == IncidentStatus.FILTERED)
        processing = sum(
            1 for i in incidents
            if i.status not in (
                IncidentStatus.FIXED,
                IncidentStatus.ESCALATED,
                IncidentStatus.FILTERED
            )
        )

        # Calculate MTTR
        resolved_count = auto_fixed + escalated
        mttr_seconds = None
        if resolved_count > 0 and self._total_resolution_time_ms > 0:
            mttr_seconds = (self._total_resolution_time_ms / resolved_count) / 1000

        # Calculate success rate
        success_rate = None
        processed = auto_fixed + escalated
        if processed > 0:
            success_rate = (auto_fixed / processed) * 100

        return Metrics(
            total_incidents=total,
            auto_fixed=auto_fixed,
            escalated=escalated,
            filtered=filtered,
            processing=processing,
            mttr_seconds=mttr_seconds,
            success_rate=success_rate,
        )

    # ═══════════════ Utility ═══════════════

    def clear(self) -> None:
        """Clear all storage (for testing)."""
        self._incidents.clear()
        self._triage_results.clear()
        self._fix_results.clear()
        self._test_results.clear()
        self._verification_results.clear()
        self._seen_gcp_insert_ids.clear()
        self._total_resolution_time_ms = 0


# Global storage instance
storage = Storage()
