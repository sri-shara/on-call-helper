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
    TriageClassification,
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

        self._health_check_runs: Dict[str, Any] = {}

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

    def find_incident_by_gchat_thread(self, thread_id: str) -> Optional[Incident]:
        """Find an active gchat incident by thread ID."""
        for inc in self._incidents.values():
            if inc.source == "gchat" and inc.gchat_metadata and inc.gchat_metadata.get("thread_id") == thread_id:
                return inc
        return None

    def list_incidents(
        self,
        status: Optional[IncidentStatus] = None,
        source: Optional[str] = None,
        limit: int = 100
    ) -> List[Incident]:
        """List incidents, optionally filtered by status and/or source."""
        incidents = list(self._incidents.values())
        if status:
            incidents = [i for i in incidents if i.status == status]
        if source:
            incidents = [i for i in incidents if i.source == source]
        # Sort by created_at descending (handle mixed timezone-aware/naive datetimes)
        def sort_key(i):
            dt = i.created_at
            # Convert to naive UTC for comparison
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt
        incidents.sort(key=sort_key, reverse=True)
        return incidents[:limit]

    def is_duplicate(self, gcp_insert_id: str) -> bool:
        """Check if we've already processed this GCP log entry."""
        return gcp_insert_id in self._seen_gcp_insert_ids

    def increment_incident_count(self, incident_id: str, new_count: int) -> bool:
        """
        Increment the occurrence count for an aggregated incident.

        Args:
            incident_id: The incident to update
            new_count: The new occurrence count

        Returns:
            True if updated, False if incident not found
        """
        from datetime import datetime
        incident = self._incidents.get(incident_id)
        if incident:
            incident.occurrence_count = new_count
            incident.last_occurrence = datetime.utcnow()
            return True
        return False

    # ═══════════════ Triage Results ═══════════════

    def get_triage_classifications_batch(self, incident_ids: List[str], source: Optional[str] = None) -> Dict[str, str]:
        """Batch-fetch triage classifications for multiple incidents."""
        results = {}
        for iid in incident_ids:
            triage = self._triage_results.get(iid)
            if triage:
                results[iid] = triage.classification.value
        return results

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

    def get_metrics(self, source: Optional[str] = None) -> Metrics:
        """Calculate and return current metrics, optionally filtered by source."""
        incidents = list(self._incidents.values())
        if source:
            incidents = [i for i in incidents if i.source == source]

        processing = 0
        no_action_needed = 0
        review_needed = 0
        pr_raised = 0

        processing_statuses = {
            IncidentStatus.ACTIVE,
            IncidentStatus.TRIAGING,
            IncidentStatus.FIXING,
            IncidentStatus.REVIEWING,
            IncidentStatus.TESTING,
            IncidentStatus.VERIFYING,
        }

        for incident in incidents:
            # Processing: any active pipeline status
            if incident.status in processing_statuses:
                processing += 1

            # PR Raised: has PR or is fixed
            if incident.pr_url or incident.status in {IncidentStatus.PR_CREATED, IncidentStatus.FIXED}:
                pr_raised += 1

            # For classification-based metrics, check triage results
            triage = self._triage_results.get(incident.id)
            if triage:
                if triage.classification == TriageClassification.TRANSIENT:
                    no_action_needed += 1
                elif triage.classification in {TriageClassification.NEEDS_HUMAN, TriageClassification.INFRA_ISSUE}:
                    review_needed += 1

        # Calculate MTTR
        resolved_count = pr_raised
        mttr_seconds = None
        if resolved_count > 0 and self._total_resolution_time_ms > 0:
            mttr_seconds = (self._total_resolution_time_ms / resolved_count) / 1000

        return Metrics(
            total_incidents=len(incidents),
            processing=processing,
            no_action_needed=no_action_needed,
            review_needed=review_needed,
            pr_raised=pr_raised,
            mttr_seconds=mttr_seconds,
        )

    # ═══════════════ Health Check Runs ═══════════════

    def save_health_check_run(self, run) -> None:
        """Save a health check run and prune to last 50."""
        self._health_check_runs[run.id] = run
        if len(self._health_check_runs) > 50:
            sorted_runs = sorted(
                self._health_check_runs.values(),
                key=lambda r: r.started_at,
                reverse=True,
            )
            self._health_check_runs = {r.id: r for r in sorted_runs[:50]}

    def list_health_check_runs(self, limit: int = 50) -> list:
        """List recent health check runs (newest first)."""
        runs = sorted(
            self._health_check_runs.values(),
            key=lambda r: r.started_at,
            reverse=True,
        )
        return runs[:limit]

    def get_health_check_run(self, run_id: str):
        """Get a single health check run by ID."""
        return self._health_check_runs.get(run_id)

    # ═══════════════ Utility ═══════════════

    def clear(self) -> None:
        """Clear all storage (for testing)."""
        self._incidents.clear()
        self._triage_results.clear()
        self._fix_results.clear()
        self._test_results.clear()
        self._verification_results.clear()
        self._seen_gcp_insert_ids.clear()
        self._health_check_runs.clear()
        self._total_resolution_time_ms = 0


# Global storage instance - choose backend based on config
def _create_storage():
    """Create storage instance based on configuration."""
    from backend.config import settings
    import logging

    logger = logging.getLogger(__name__)

    if settings.storage_backend == "firestore":
        try:
            from backend.storage_firestore import FirestoreStorage
            logger.info("Using Firestore storage backend")
            return FirestoreStorage()
        except ImportError as e:
            logger.warning(f"Firestore not available ({e}), falling back to memory storage")
        except Exception as e:
            logger.warning(f"Failed to initialize Firestore ({e}), falling back to memory storage")

    logger.info("Using in-memory storage backend")
    return Storage()


storage = _create_storage()
