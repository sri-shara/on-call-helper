"""
Firestore storage for On Call Helper.

Persists incidents and agent responses to Google Cloud Firestore for
historical tracking and user review.

Collections:
- incidents: Incident documents
- triage_results: Triage analysis from Claude
- fix_results: Generated code fixes
- test_results: Sandbox test results
- verification_results: Production verification results
- metrics: Aggregated metrics (single document)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from backend.models import (
    Incident,
    IncidentStatus,
    TriageResult,
    FixResult,
    TestResult,
    VerificationResult,
    Metrics,
    TriageClassification,
    Severity,
    PatternRecord,
    FixRecord,
)

logger = logging.getLogger(__name__)


class FirestoreStorage:
    """
    Firestore-backed storage with the same interface as in-memory Storage.

    Provides persistent storage for incidents and all pipeline results.
    """

    # Collection names (GCP — default)
    INCIDENTS = "incidents"
    TRIAGE_RESULTS = "triage_results"
    FIX_RESULTS = "fix_results"
    TEST_RESULTS = "test_results"
    VERIFICATION_RESULTS = "verification_results"
    SEEN_IDS = "seen_gcp_ids"
    METRICS = "metrics"
    INCIDENT_PATTERNS = "incident_patterns"

    # GChat collection names (separate from GCP to avoid cross-contamination)
    GCHAT_INCIDENTS = "gchat_incidents"
    GCHAT_TRIAGE_RESULTS = "gchat_triage_results"
    GCHAT_FIX_RESULTS = "gchat_fix_results"
    GCHAT_TEST_RESULTS = "gchat_test_results"
    GCHAT_VERIFICATION_RESULTS = "gchat_verification_results"

    def _col(self, base: str, source: Optional[str] = None) -> str:
        """Return the correct collection name based on source."""
        if source == "gchat":
            mapping = {
                self.INCIDENTS: self.GCHAT_INCIDENTS,
                self.TRIAGE_RESULTS: self.GCHAT_TRIAGE_RESULTS,
                self.FIX_RESULTS: self.GCHAT_FIX_RESULTS,
                self.TEST_RESULTS: self.GCHAT_TEST_RESULTS,
                self.VERIFICATION_RESULTS: self.GCHAT_VERIFICATION_RESULTS,
            }
            return mapping.get(base, base)
        return base

    def __init__(self, project_id: Optional[str] = None):
        """
        Initialize Firestore storage.

        Args:
            project_id: GCP project ID (uses default if not specified)
        """
        from backend.config import settings

        # Use firestore_project_id if set, otherwise fall back to gcp_project_id
        # This allows Firestore to be in a different project than Cloud Logging
        self.project_id = (
            project_id
            or settings.firestore_project_id
            or settings.gcp_project_id
        )
        self.database_id = settings.firestore_database_id or "(default)"
        self._db: Optional[firestore.Client] = None

        # Local cache for deduplication (also backed by Firestore)
        self._seen_gcp_insert_ids: Set[str] = set()
        self._cache_loaded = False

    @property
    def db(self) -> firestore.Client:
        """Get or create Firestore client."""
        if self._db is None:
            self._db = firestore.Client(
                project=self.project_id,
                database=self.database_id
            )
            logger.info(f"Connected to Firestore project: {self.project_id}, database: {self.database_id}")
        return self._db

    def _load_seen_ids_cache(self) -> None:
        """Load seen GCP insert IDs into local cache."""
        if self._cache_loaded:
            return

        try:
            docs = self.db.collection(self.SEEN_IDS).stream()
            for doc in docs:
                self._seen_gcp_insert_ids.add(doc.id)
            self._cache_loaded = True
            logger.info(f"Loaded {len(self._seen_gcp_insert_ids)} seen GCP IDs from Firestore")
        except Exception as e:
            logger.warning(f"Failed to load seen IDs cache: {e}")

    # ═══════════════ Serialization Helpers ═══════════════

    def _incident_to_dict(self, incident: Incident) -> Dict[str, Any]:
        """Convert Incident to Firestore document."""
        data = incident.model_dump()
        # Convert enums to strings
        data["status"] = incident.status.value
        data["severity"] = incident.severity.value
        # Convert datetime to Firestore timestamp
        data["created_at"] = incident.created_at
        if incident.resolved_at:
            data["resolved_at"] = incident.resolved_at
        return data

    def _dict_to_incident(self, data: Dict[str, Any]) -> Incident:
        """Convert Firestore document to Incident."""
        # Convert string enums back
        data["status"] = IncidentStatus(data["status"])
        data["severity"] = Severity(data["severity"])
        return Incident(**data)

    def _triage_to_dict(self, result: TriageResult) -> Dict[str, Any]:
        """Convert TriageResult to Firestore document."""
        data = result.model_dump()
        data["classification"] = result.classification.value
        data["created_at"] = result.created_at
        return data

    def _dict_to_triage(self, data: Dict[str, Any]) -> TriageResult:
        """Convert Firestore document to TriageResult."""
        data["classification"] = TriageClassification(data["classification"])
        return TriageResult(**data)

    def _fix_to_dict(self, result: FixResult) -> Dict[str, Any]:
        """Convert FixResult to Firestore document."""
        data = result.model_dump()
        data["created_at"] = result.created_at
        return data

    def _dict_to_fix(self, data: Dict[str, Any]) -> FixResult:
        """Convert Firestore document to FixResult."""
        return FixResult(**data)

    def _test_to_dict(self, result: TestResult) -> Dict[str, Any]:
        """Convert TestResult to Firestore document."""
        data = result.model_dump()
        data["created_at"] = result.created_at
        return data

    def _dict_to_test(self, data: Dict[str, Any]) -> TestResult:
        """Convert Firestore document to TestResult."""
        return TestResult(**data)

    def _verification_to_dict(self, result: VerificationResult) -> Dict[str, Any]:
        """Convert VerificationResult to Firestore document."""
        data = result.model_dump()
        data["status"] = result.status.value
        data["created_at"] = result.created_at
        return data

    def _dict_to_verification(self, data: Dict[str, Any]) -> VerificationResult:
        """Convert Firestore document to VerificationResult."""
        from backend.models import VerificationStatus
        data["status"] = VerificationStatus(data["status"])
        return VerificationResult(**data)

    # ═══════════════ Incidents ═══════════════

    def save_incident(self, incident: Incident) -> None:
        """Save an incident to Firestore (routes to gchat collection if source=gchat)."""
        try:
            col = self._col(self.INCIDENTS, incident.source)
            doc_ref = self.db.collection(col).document(incident.id)
            doc_ref.set(self._incident_to_dict(incident))

            # Track GCP insert ID for deduplication
            if incident.gcp_insert_id:
                self._seen_gcp_insert_ids.add(incident.gcp_insert_id)
                self.db.collection(self.SEEN_IDS).document(incident.gcp_insert_id).set({
                    "incident_id": incident.id,
                    "created_at": datetime.utcnow(),
                })

            logger.debug(f"Saved incident {incident.id} to Firestore ({col})")
        except Exception as e:
            logger.error(f"Failed to save incident {incident.id}: {e}")
            raise

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Get an incident by ID from Firestore (checks both GCP and GChat collections)."""
        try:
            doc = self.db.collection(self.INCIDENTS).document(incident_id).get()
            if doc.exists:
                return self._dict_to_incident(doc.to_dict())
            # Fall through to check gchat collection
            doc = self.db.collection(self.GCHAT_INCIDENTS).document(incident_id).get()
            if doc.exists:
                return self._dict_to_incident(doc.to_dict())
            return None
        except Exception as e:
            logger.error(f"Failed to get incident {incident_id}: {e}")
            return None

    def update_incident_status(
        self,
        incident_id: str,
        status: IncidentStatus,
        resolved_at: Optional[datetime] = None
    ) -> Optional[Incident]:
        """Update an incident's status in Firestore (checks both collections)."""
        try:
            update_data = {"status": status.value}
            if resolved_at:
                update_data["resolved_at"] = resolved_at

            # Try GCP collection first, then GChat
            for col in [self.INCIDENTS, self.GCHAT_INCIDENTS]:
                doc_ref = self.db.collection(col).document(incident_id)
                doc = doc_ref.get()
                if doc.exists:
                    doc_ref.update(update_data)
                    return self.get_incident(incident_id)

            logger.warning(f"Incident {incident_id} not found in any collection")
            return None
        except Exception as e:
            logger.error(f"Failed to update incident {incident_id}: {e}")
            return None

    def find_incident_by_gchat_thread(self, thread_id: str) -> Optional[Incident]:
        """Find an active gchat incident by thread ID."""
        try:
            query = (
                self.db.collection(self.GCHAT_INCIDENTS)
                .where(filter=FieldFilter("gchat_metadata.thread_id", "==", thread_id))
                .limit(1)
            )
            for doc in query.stream():
                return self._dict_to_incident(doc.to_dict())
            return None
        except Exception as e:
            logger.warning(f"Failed to find gchat thread incident: {e}")
            return None

    def list_incidents(
        self,
        status: Optional[IncidentStatus] = None,
        source: Optional[str] = None,
        limit: int = 100
    ) -> List[Incident]:
        """List incidents from Firestore, optionally filtered by status and/or source.

        GChat and GCP incidents live in separate collections, so source determines
        which collection to query — no cross-collection filtering needed.
        """
        try:
            # Route to the correct collection based on source
            if source == "gchat":
                col = self.GCHAT_INCIDENTS
            else:
                col = self.INCIDENTS

            query = self.db.collection(col)

            if status:
                query = query.where(filter=FieldFilter("status", "==", status.value))

            query = query.order_by("created_at", direction=firestore.Query.DESCENDING)
            query = query.limit(limit)

            incidents = []
            for doc in query.stream():
                try:
                    inc = self._dict_to_incident(doc.to_dict())
                    incidents.append(inc)
                except Exception as e:
                    logger.warning(f"Failed to parse incident {doc.id}: {e}")

            return incidents[:limit]
        except Exception as e:
            logger.error(f"Failed to list incidents: {e}")
            return []

    def is_duplicate(self, gcp_insert_id: str) -> bool:
        """Check if we've already processed this GCP log entry."""
        # Check local cache first
        self._load_seen_ids_cache()
        if gcp_insert_id in self._seen_gcp_insert_ids:
            return True

        # Double-check Firestore (in case another instance processed it)
        try:
            doc = self.db.collection(self.SEEN_IDS).document(gcp_insert_id).get()
            if doc.exists:
                self._seen_gcp_insert_ids.add(gcp_insert_id)
                return True
        except Exception as e:
            logger.warning(f"Failed to check duplicate: {e}")

        return False

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
        try:
            # Check both collections
            for col in [self.INCIDENTS, self.GCHAT_INCIDENTS]:
                doc_ref = self.db.collection(col).document(incident_id)
                doc = doc_ref.get()
                if doc.exists:
                    doc_ref.update({
                        "occurrence_count": new_count,
                        "last_occurrence": datetime.utcnow().isoformat(),
                    })
                    logger.debug(f"Incremented incident count: {incident_id} -> {new_count} ({col})")
                    return True
            return False
        except Exception as e:
            logger.error(f"Failed to increment incident count: {e}")
            return False

    # ═══════════════ Triage Results ═══════════════

    def get_triage_classifications_batch(self, incident_ids: List[str], source: Optional[str] = None) -> Dict[str, str]:
        """Batch-fetch triage classifications for multiple incidents at once.

        Uses Firestore get_all() for a single round-trip instead of N individual reads.
        Returns a dict mapping incident_id -> {"classification": str, "service_name": str|None}.
        For backwards compat, values can also be just a string (classification).
        """
        if not incident_ids:
            return {}
        try:
            col = self._col(self.TRIAGE_RESULTS, source)
            doc_refs = [
                self.db.collection(col).document(iid)
                for iid in incident_ids
            ]
            results = {}
            for doc in self.db.get_all(doc_refs):
                if doc.exists:
                    data = doc.to_dict()
                    classification = data.get("classification")
                    if classification:
                        results[doc.id] = {
                            "classification": classification,
                            "service_name": data.get("service_name"),
                        }
            return results
        except Exception as e:
            logger.error(f"Failed to batch-fetch triage classifications: {e}")
            return {}

    def _detect_source(self, incident_id: str) -> Optional[str]:
        """Detect which source an incident belongs to by checking collections."""
        doc = self.db.collection(self.GCHAT_INCIDENTS).document(incident_id).get()
        if doc.exists:
            return "gchat"
        return None  # Default (gcp) — no need to check, it's the fallback

    def save_triage_result(self, result: TriageResult) -> None:
        """Save a triage result to Firestore (routes to gchat collection if needed)."""
        try:
            source = self._detect_source(result.incident_id)
            col = self._col(self.TRIAGE_RESULTS, source)
            doc_ref = self.db.collection(col).document(result.incident_id)
            doc_ref.set(self._triage_to_dict(result))
            logger.debug(f"Saved triage result for {result.incident_id} ({col})")
        except Exception as e:
            logger.error(f"Failed to save triage result: {e}")
            raise

    def get_triage_result(self, incident_id: str) -> Optional[TriageResult]:
        """Get triage result for an incident from Firestore (checks both collections)."""
        try:
            for col in [self.TRIAGE_RESULTS, self.GCHAT_TRIAGE_RESULTS]:
                doc = self.db.collection(col).document(incident_id).get()
                if doc.exists:
                    return self._dict_to_triage(doc.to_dict())
            return None
        except Exception as e:
            logger.error(f"Failed to get triage result: {e}")
            return None

    # ═══════════════ Fix Results ═══════════════

    def save_fix_result(self, result: FixResult) -> None:
        """Save a fix result to Firestore (routes to gchat collection if needed)."""
        try:
            source = self._detect_source(result.incident_id)
            col = self._col(self.FIX_RESULTS, source)
            doc_ref = self.db.collection(col).document(result.incident_id)
            doc_ref.set(self._fix_to_dict(result))
            logger.debug(f"Saved fix result for {result.incident_id} ({col})")
        except Exception as e:
            logger.error(f"Failed to save fix result: {e}")
            raise

    def get_fix_result(self, incident_id: str) -> Optional[FixResult]:
        """Get fix result for an incident from Firestore (checks both collections)."""
        try:
            for col in [self.FIX_RESULTS, self.GCHAT_FIX_RESULTS]:
                doc = self.db.collection(col).document(incident_id).get()
                if doc.exists:
                    return self._dict_to_fix(doc.to_dict())
            return None
        except Exception as e:
            logger.error(f"Failed to get fix result: {e}")
            return None

    # ═══════════════ Test Results ═══════════════

    def save_test_result(self, result: TestResult) -> None:
        """Save a test result to Firestore (routes to gchat collection if needed)."""
        try:
            source = self._detect_source(result.incident_id)
            col = self._col(self.TEST_RESULTS, source)
            doc_ref = self.db.collection(col).document(result.incident_id)
            doc_ref.set(self._test_to_dict(result))
            logger.debug(f"Saved test result for {result.incident_id} ({col})")
        except Exception as e:
            logger.error(f"Failed to save test result: {e}")
            raise

    def get_test_result(self, incident_id: str) -> Optional[TestResult]:
        """Get test result for an incident from Firestore (checks both collections)."""
        try:
            for col in [self.TEST_RESULTS, self.GCHAT_TEST_RESULTS]:
                doc = self.db.collection(col).document(incident_id).get()
                if doc.exists:
                    return self._dict_to_test(doc.to_dict())
            return None
        except Exception as e:
            logger.error(f"Failed to get test result: {e}")
            return None

    # ═══════════════ Verification Results ═══════════════

    def save_verification_result(self, result: VerificationResult) -> None:
        """Save a verification result to Firestore (routes to gchat collection if needed)."""
        try:
            source = self._detect_source(result.incident_id)
            col = self._col(self.VERIFICATION_RESULTS, source)
            doc_ref = self.db.collection(col).document(result.incident_id)
            doc_ref.set(self._verification_to_dict(result))
            logger.debug(f"Saved verification result for {result.incident_id} ({col})")
        except Exception as e:
            logger.error(f"Failed to save verification result: {e}")
            raise

    def get_verification_result(self, incident_id: str) -> Optional[VerificationResult]:
        """Get verification result for an incident from Firestore (checks both collections)."""
        try:
            for col in [self.VERIFICATION_RESULTS, self.GCHAT_VERIFICATION_RESULTS]:
                doc = self.db.collection(col).document(incident_id).get()
                if doc.exists:
                    return self._dict_to_verification(doc.to_dict())
            return None
        except Exception as e:
            logger.error(f"Failed to get verification result: {e}")
            return None

    # ═══════════════ Metrics ═══════════════

    def get_metrics(self, source: Optional[str] = None) -> Metrics:
        """Calculate metrics by querying Firestore, optionally filtered by source."""
        try:
            # Route to correct collection based on source
            if source == "gchat":
                incident_col = self.GCHAT_INCIDENTS
                triage_col = self.GCHAT_TRIAGE_RESULTS
            else:
                incident_col = self.INCIDENTS
                triage_col = self.TRIAGE_RESULTS

            all_incidents = list(self.db.collection(incident_col).stream())

            # Build a lookup of triage results by incident_id
            triage_by_incident = {}
            for doc in self.db.collection(triage_col).stream():
                data = doc.to_dict()
                incident_id = data.get("incident_id")
                if incident_id:
                    triage_by_incident[incident_id] = data

            processing = 0
            no_action_needed = 0
            review_needed = 0
            pr_raised = 0
            total_resolution_time_ms = 0

            processing_statuses = {
                IncidentStatus.ACTIVE.value,
                IncidentStatus.TRIAGING.value,
                IncidentStatus.FIXING.value,
                IncidentStatus.REVIEWING.value,
                IncidentStatus.TESTING.value,
                IncidentStatus.VERIFYING.value,
            }

            for doc in all_incidents:
                data = doc.to_dict()
                incident_id = doc.id
                status = data.get("status", "")

                # Processing: any active pipeline status
                if status in processing_statuses:
                    processing += 1

                # PR Raised: has PR or is fixed
                if data.get("pr_url") or status in {IncidentStatus.PR_CREATED.value, IncidentStatus.FIXED.value}:
                    pr_raised += 1
                    # Calculate resolution time
                    if data.get("resolved_at") and data.get("created_at"):
                        delta = data["resolved_at"] - data["created_at"]
                        total_resolution_time_ms += int(delta.total_seconds() * 1000)

                # For classification-based metrics, check triage results
                triage = triage_by_incident.get(incident_id)
                if triage:
                    classification = triage.get("classification", "")
                    if classification == TriageClassification.TRANSIENT.value:
                        no_action_needed += 1
                    elif classification in {TriageClassification.NEEDS_HUMAN.value, TriageClassification.INFRA_ISSUE.value}:
                        review_needed += 1

            # Calculate MTTR
            mttr_seconds = None
            if pr_raised > 0 and total_resolution_time_ms > 0:
                mttr_seconds = (total_resolution_time_ms / pr_raised) / 1000

            return Metrics(
                total_incidents=len(all_incidents),
                processing=processing,
                no_action_needed=no_action_needed,
                review_needed=review_needed,
                pr_raised=pr_raised,
                mttr_seconds=mttr_seconds,
            )
        except Exception as e:
            logger.error(f"Failed to get metrics: {e}")
            return Metrics()

    # ═══════════════ Health Check Runs ═══════════════

    HEALTH_CHECK_RUNS = "health_check_runs"

    def _health_check_to_dict(self, run) -> Dict[str, Any]:
        """Convert HealthCheckRun to Firestore document."""
        data = run.model_dump()
        data["started_at"] = run.started_at
        if run.completed_at:
            data["completed_at"] = run.completed_at
        return data

    def _dict_to_health_check(self, data: Dict[str, Any]):
        """Convert Firestore document to HealthCheckRun."""
        from backend.models import HealthCheckRun
        return HealthCheckRun(**data)

    def save_health_check_run(self, run) -> None:
        """Save a health check run to Firestore and prune old entries."""
        try:
            doc_ref = self.db.collection(self.HEALTH_CHECK_RUNS).document(run.id)
            doc_ref.set(self._health_check_to_dict(run))
            logger.debug(f"Saved health check run {run.id}")
            self._prune_health_check_runs()
        except Exception as e:
            logger.error(f"Failed to save health check run {run.id}: {e}")
            raise

    def _prune_health_check_runs(self) -> None:
        """Keep only the 50 most recent health check runs."""
        try:
            query = (
                self.db.collection(self.HEALTH_CHECK_RUNS)
                .order_by("started_at", direction=firestore.Query.DESCENDING)
                .offset(50)
            )
            for doc in query.stream():
                doc.reference.delete()
                logger.debug(f"Pruned old health check run {doc.id}")
        except Exception as e:
            logger.warning(f"Failed to prune health check runs: {e}")

    def list_health_check_runs(self, limit: int = 50) -> list:
        """List recent health check runs (newest first)."""
        try:
            query = (
                self.db.collection(self.HEALTH_CHECK_RUNS)
                .order_by("started_at", direction=firestore.Query.DESCENDING)
                .limit(limit)
            )
            runs = []
            for doc in query.stream():
                try:
                    runs.append(self._dict_to_health_check(doc.to_dict()))
                except Exception as e:
                    logger.warning(f"Failed to parse health check run {doc.id}: {e}")
            return runs
        except Exception as e:
            logger.error(f"Failed to list health check runs: {e}")
            return []

    def get_health_check_run(self, run_id: str):
        """Get a single health check run by ID."""
        try:
            doc = self.db.collection(self.HEALTH_CHECK_RUNS).document(run_id).get()
            if doc.exists:
                return self._dict_to_health_check(doc.to_dict())
            return None
        except Exception as e:
            logger.error(f"Failed to get health check run {run_id}: {e}")
            return None

    # ═══════════════ Utility ═══════════════

    def clear(self) -> None:
        """Clear all storage (for testing). USE WITH CAUTION."""
        logger.warning("Clearing all Firestore collections!")

        collections = [
            self.INCIDENTS,
            self.TRIAGE_RESULTS,
            self.FIX_RESULTS,
            self.TEST_RESULTS,
            self.VERIFICATION_RESULTS,
            self.SEEN_IDS,
            self.GCHAT_INCIDENTS,
            self.GCHAT_TRIAGE_RESULTS,
            self.GCHAT_FIX_RESULTS,
            self.GCHAT_TEST_RESULTS,
            self.GCHAT_VERIFICATION_RESULTS,
            self.HEALTH_CHECK_RUNS,
        ]

        for collection_name in collections:
            try:
                docs = self.db.collection(collection_name).stream()
                for doc in docs:
                    doc.reference.delete()
            except Exception as e:
                logger.error(f"Failed to clear {collection_name}: {e}")

        self._seen_gcp_insert_ids.clear()
        self._cache_loaded = False

    # ═══════════════ Query Helpers ═══════════════

    def get_incidents_by_service(
        self,
        service_name: str,
        limit: int = 50
    ) -> List[Incident]:
        """Get incidents for a specific service."""
        try:
            query = (
                self.db.collection(self.INCIDENTS)
                .where(filter=FieldFilter("service_name", "==", service_name))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(limit)
            )

            return [self._dict_to_incident(doc.to_dict()) for doc in query.stream()]
        except Exception as e:
            logger.error(f"Failed to get incidents by service: {e}")
            return []

    def get_incidents_by_classification(
        self,
        classification: TriageClassification,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get incidents with a specific triage classification."""
        try:
            query = (
                self.db.collection(self.TRIAGE_RESULTS)
                .where(filter=FieldFilter("classification", "==", classification.value))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(limit)
            )

            results = []
            for doc in query.stream():
                triage = self._dict_to_triage(doc.to_dict())
                incident = self.get_incident(triage.incident_id)
                if incident:
                    results.append({
                        "incident": incident,
                        "triage": triage,
                    })
            return results
        except Exception as e:
            logger.error(f"Failed to get incidents by classification: {e}")
            return []

    def get_recent_errors_summary(
        self,
        hours: int = 24,
        limit: int = 100
    ) -> Dict[str, Any]:
        """Get a summary of recent errors for reporting."""
        from datetime import timedelta

        try:
            cutoff = datetime.utcnow() - timedelta(hours=hours)

            query = (
                self.db.collection(self.INCIDENTS)
                .where(filter=FieldFilter("created_at", ">=", cutoff))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(limit)
            )

            incidents = [self._dict_to_incident(doc.to_dict()) for doc in query.stream()]

            # Group by service
            by_service = {}
            by_status = {}

            for incident in incidents:
                # By service
                svc = incident.service_name
                if svc not in by_service:
                    by_service[svc] = []
                by_service[svc].append(incident.id)

                # By status
                status = incident.status.value
                by_status[status] = by_status.get(status, 0) + 1

            return {
                "total": len(incidents),
                "hours": hours,
                "by_service": {k: len(v) for k, v in by_service.items()},
                "by_status": by_status,
                "most_affected_service": max(by_service.keys(), key=lambda k: len(by_service[k])) if by_service else None,
            }
        except Exception as e:
            logger.error(f"Failed to get recent errors summary: {e}")
            return {"total": 0, "hours": hours, "by_service": {}, "by_status": {}}

    # ═══════════════ Pattern Learning ═══════════════

    def _pattern_to_dict(self, pattern: PatternRecord) -> Dict[str, Any]:
        """Convert PatternRecord to Firestore document."""
        data = pattern.model_dump()
        data["first_seen"] = pattern.first_seen
        data["last_seen"] = pattern.last_seen
        # Convert FixRecord list to dicts
        data["successful_fixes"] = [
            {
                "incident_id": f.incident_id,
                "file_path": f.file_path,
                "fix_explanation": f.fix_explanation,
                "pr_url": f.pr_url,
                "created_at": f.created_at,
            }
            for f in pattern.successful_fixes
        ]
        return data

    def _dict_to_pattern(self, data: Dict[str, Any]) -> PatternRecord:
        """Convert Firestore document to PatternRecord."""
        # Convert fix dicts back to FixRecord objects
        if "successful_fixes" in data and data["successful_fixes"]:
            data["successful_fixes"] = [
                FixRecord(**f) for f in data["successful_fixes"]
            ]
        return PatternRecord(**data)

    def save_pattern(self, pattern: PatternRecord) -> None:
        """Save a pattern to Firestore."""
        try:
            doc_ref = self.db.collection(self.INCIDENT_PATTERNS).document(pattern.pattern_id)
            doc_ref.set(self._pattern_to_dict(pattern))
            logger.debug(f"Saved pattern {pattern.pattern_id}")
        except Exception as e:
            logger.error(f"Failed to save pattern {pattern.pattern_id}: {e}")
            raise

    def get_pattern(self, pattern_id: str) -> Optional[PatternRecord]:
        """Get a pattern by ID from Firestore."""
        try:
            doc = self.db.collection(self.INCIDENT_PATTERNS).document(pattern_id).get()
            if doc.exists:
                return self._dict_to_pattern(doc.to_dict())
            return None
        except Exception as e:
            logger.error(f"Failed to get pattern {pattern_id}: {e}")
            return None

    def find_patterns_by_service(
        self,
        service_name: str,
        limit: int = 50
    ) -> List[PatternRecord]:
        """Find patterns for a specific service."""
        try:
            query = (
                self.db.collection(self.INCIDENT_PATTERNS)
                .where(filter=FieldFilter("service_name", "==", service_name))
                .order_by("last_seen", direction=firestore.Query.DESCENDING)
                .limit(limit)
            )

            patterns = []
            for doc in query.stream():
                try:
                    patterns.append(self._dict_to_pattern(doc.to_dict()))
                except Exception as e:
                    logger.warning(f"Failed to parse pattern {doc.id}: {e}")

            return patterns
        except Exception as e:
            logger.error(f"Failed to find patterns by service: {e}")
            return []

    def update_pattern_outcome(
        self,
        pattern_id: str,
        classification: str,
        success: bool,
        fix: Optional[FixRecord] = None
    ) -> bool:
        """
        Update a pattern with a new outcome.

        Args:
            pattern_id: Pattern to update
            classification: Classification that was assigned
            success: Whether the outcome was successful
            fix: Optional fix record to add to successful_fixes

        Returns:
            True if updated, False if pattern not found
        """
        try:
            doc_ref = self.db.collection(self.INCIDENT_PATTERNS).document(pattern_id)
            doc = doc_ref.get()

            if not doc.exists:
                return False

            data = doc.to_dict()

            # Update classification count
            classifications = data.get("classifications", {})
            classifications[classification] = classifications.get(classification, 0) + 1

            # Update success/failure counts
            if success:
                success_count = data.get("success_count", 0) + 1
                failure_count = data.get("failure_count", 0)
            else:
                success_count = data.get("success_count", 0)
                failure_count = data.get("failure_count", 0) + 1

            # Update timestamp
            update_data = {
                "classifications": classifications,
                "success_count": success_count,
                "failure_count": failure_count,
                "last_seen": datetime.utcnow(),
            }

            # Add fix to successful_fixes if provided (limit to 10)
            if fix and success:
                fixes = data.get("successful_fixes", [])
                fix_dict = {
                    "incident_id": fix.incident_id,
                    "file_path": fix.file_path,
                    "fix_explanation": fix.fix_explanation,
                    "pr_url": fix.pr_url,
                    "created_at": fix.created_at,
                }
                fixes.insert(0, fix_dict)  # Add to front
                fixes = fixes[:10]  # Keep only 10 most recent
                update_data["successful_fixes"] = fixes

            doc_ref.update(update_data)
            logger.debug(f"Updated pattern outcome: {pattern_id} -> {classification} (success={success})")
            return True

        except Exception as e:
            logger.error(f"Failed to update pattern outcome: {e}")
            return False

    def get_pattern_stats(self) -> Dict[str, Any]:
        """Get statistics about learned patterns."""
        try:
            patterns = list(self.db.collection(self.INCIDENT_PATTERNS).stream())

            total_patterns = len(patterns)
            total_occurrences = 0
            patterns_with_fixes = 0
            total_success = 0
            total_outcomes = 0
            by_classification = {}

            for doc in patterns:
                data = doc.to_dict()
                classifications = data.get("classifications", {})

                for cls, count in classifications.items():
                    total_occurrences += count
                    by_classification[cls] = by_classification.get(cls, 0) + count

                if data.get("successful_fixes"):
                    patterns_with_fixes += 1

                success = data.get("success_count", 0)
                failure = data.get("failure_count", 0)
                total_success += success
                total_outcomes += success + failure

            return {
                "total_patterns": total_patterns,
                "total_occurrences": total_occurrences,
                "patterns_with_successful_fixes": patterns_with_fixes,
                "average_success_rate": total_success / total_outcomes if total_outcomes > 0 else 0,
                "most_common_classification": max(by_classification.keys(), key=lambda k: by_classification[k]) if by_classification else None,
                "patterns_by_classification": by_classification,
            }
        except Exception as e:
            logger.error(f"Failed to get pattern stats: {e}")
            return {
                "total_patterns": 0,
                "total_occurrences": 0,
                "patterns_with_successful_fixes": 0,
                "average_success_rate": 0,
                "most_common_classification": None,
                "patterns_by_classification": {},
            }
