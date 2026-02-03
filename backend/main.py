"""
On Call Helper - FastAPI Application

An AI-powered incident response agent that monitors the Nucleus MDR platform
for production errors, automatically triages issues, generates fixes, validates
them, and creates PRs.

Repository References:
- Nucleus: /Users/sri/nucleus - The MDR platform being monitored
- On-Call: /Users/sri/oncall - SRE knowledge, runbooks, triage procedures
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.config import settings
from backend.storage import storage
from backend.models import Metrics

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")

    # Validate settings in production
    if not settings.is_development:
        missing = settings.validate_required_for_production()
        if missing:
            logger.warning(f"Missing required settings: {missing}")

    logger.info(f"Nucleus repo: {settings.nucleus_repo_path}")
    logger.info(f"On-Call repo: {settings.oncall_repo_path}")

    # Auto-start GCP polling if enabled
    if settings.gcp_auto_poll and settings.gcp_project_id:
        logger.info(f"Auto-starting GCP polling (interval: {settings.gcp_poll_interval}s)")
        try:
            await _start_gcp_polling_internal(settings.gcp_poll_interval)
            logger.info("GCP polling auto-started successfully")
        except Exception as e:
            logger.error(f"Failed to auto-start GCP polling: {e}")

    yield

    # Shutdown
    logger.info("Shutting down On Call Helper")

    # Stop GCP polling if running
    try:
        gcp_service = get_gcp_service()
        if gcp_service.is_polling:
            await gcp_service.stop_polling()
            logger.info("GCP polling stopped")
    except Exception as e:
        logger.error(f"Error stopping GCP polling: {e}")

    # Close WebSocket connections
    from backend.websocket_manager import ws_manager
    await ws_manager.close_all()


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-powered incident response agent for Nucleus MDR platform",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════ Health Endpoints ═══════════════


@app.get("/health", tags=["Health"])
async def health():
    """Basic health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/health/ready", tags=["Health"])
async def ready():
    """
    Readiness check.

    Verifies the service is ready to accept traffic.
    """
    # Check required repository paths exist
    nucleus_exists = settings.nucleus_repo_path.exists()
    oncall_exists = settings.oncall_repo_path.exists()

    if not nucleus_exists or not oncall_exists:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "details": {
                    "nucleus_repo": "found" if nucleus_exists else "missing",
                    "oncall_repo": "found" if oncall_exists else "missing",
                }
            }
        )

    return {
        "status": "ready",
        "timestamp": datetime.utcnow().isoformat(),
        "details": {
            "nucleus_repo": "found",
            "oncall_repo": "found",
        }
    }


@app.get("/health/live", tags=["Health"])
async def live():
    """
    Liveness check.

    Simple check that the service is running.
    """
    return {"status": "alive"}


# ═══════════════ Info Endpoints ═══════════════


@app.get("/info", tags=["Info"])
async def info():
    """Get application information."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "environment": "development" if settings.is_development else "production",
        "repositories": {
            "nucleus": str(settings.nucleus_repo_path),
            "oncall": str(settings.oncall_repo_path),
        }
    }


# ═══════════════ Metrics Endpoint ═══════════════


@app.get("/metrics", tags=["Metrics"], response_model=Metrics)
async def get_metrics():
    """Get current incident processing metrics."""
    return storage.get_metrics()


# ═══════════════ Incidents Endpoints ═══════════════


from typing import Optional

@app.get("/incidents", tags=["Incidents"])
async def list_incidents(status: Optional[str] = None, limit: int = 100):
    """List incidents, optionally filtered by status."""
    from backend.models import IncidentStatus

    status_filter = None
    if status:
        try:
            status_filter = IncidentStatus(status)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid status: {status}"}
            )

    incidents = storage.list_incidents(status=status_filter, limit=limit)

    # Include triage classification for consistent status display
    incident_list = []
    for inc in incidents:
        inc_data = inc.model_dump()
        triage = storage.get_triage_result(inc.id)
        if triage:
            inc_data["triage_classification"] = triage.classification.value
        incident_list.append(inc_data)

    return {
        "incidents": incident_list,
        "count": len(incidents),
    }


@app.get("/incidents/{incident_id}", tags=["Incidents"])
async def get_incident(incident_id: str):
    """Get a specific incident by ID."""
    incident = storage.get_incident(incident_id)
    if not incident:
        return JSONResponse(
            status_code=404,
            content={"error": f"Incident not found: {incident_id}"}
        )

    # Also get related results
    triage = storage.get_triage_result(incident_id)
    fix = storage.get_fix_result(incident_id)
    test = storage.get_test_result(incident_id)
    verification = storage.get_verification_result(incident_id)

    return {
        "incident": incident.model_dump(),
        "triage": triage.model_dump() if triage else None,
        "fix": fix.model_dump() if fix else None,
        "test": test.model_dump() if test else None,
        "verification": verification.model_dump() if verification else None,
    }


@app.get("/incidents/all/details", tags=["Incidents"])
async def get_all_incidents_with_details(status: Optional[str] = None, limit: int = 100):
    """
    Get all incidents with complete details (triage, fix, test, verification).
    
    Useful for displaying in a table view with all AI agent outputs.
    """
    from backend.models import IncidentStatus

    status_filter = None
    if status:
        try:
            status_filter = IncidentStatus(status)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid status: {status}"}
            )

    incidents = storage.list_incidents(status=status_filter, limit=limit)
    
    # Build response with all related data
    result = []
    for incident in incidents:
        triage = storage.get_triage_result(incident.id)
        fix = storage.get_fix_result(incident.id)
        test = storage.get_test_result(incident.id)
        verification = storage.get_verification_result(incident.id)
        
        result.append({
            "incident": incident.model_dump(),
            "triage": triage.model_dump() if triage else None,
            "fix": fix.model_dump() if fix else None,
            "test": test.model_dump() if test else None,
            "verification": verification.model_dump() if verification else None,
        })
    
    return {
        "incidents": result,
        "count": len(result),
    }


@app.post("/incidents/{incident_id}/resolve", tags=["Incidents"])
async def resolve_incident(incident_id: str):
    """
    Manually resolve an incident (mark as fixed).

    Use this for escalated incidents that have been manually addressed.
    """
    from backend.websocket_manager import ws_manager
    from backend.models import IncidentStatus
    from datetime import datetime

    incident = storage.get_incident(incident_id)
    if not incident:
        return JSONResponse(
            status_code=404,
            content={"error": f"Incident not found: {incident_id}"}
        )

    # Update status to fixed
    storage.update_incident_status(
        incident_id,
        IncidentStatus.FIXED,
        resolved_at=datetime.utcnow()
    )

    # Broadcast update via WebSocket
    await ws_manager.broadcast(
        "incident_resolved",
        {
            "incident_id": incident_id,
            "status": "fixed",
            "resolved_at": datetime.utcnow().isoformat(),
        }
    )

    # Broadcast updated metrics
    await ws_manager.broadcast_metrics_update()

    return {
        "status": "resolved",
        "incident_id": incident_id,
        "message": f"Incident {incident_id} marked as resolved",
    }


# ═══════════════ History & Analytics Endpoints ═══════════════


@app.get("/history/by-service/{service_name}", tags=["History"])
async def get_incidents_by_service(service_name: str, limit: int = 50):
    """
    Get incident history for a specific service.

    Useful for understanding error patterns in a service.
    """
    # Check if using Firestore storage with query methods
    if hasattr(storage, 'get_incidents_by_service'):
        incidents = storage.get_incidents_by_service(service_name, limit)
    else:
        # Fallback for in-memory storage
        all_incidents = storage.list_incidents(limit=500)
        incidents = [i for i in all_incidents if i.service_name == service_name][:limit]

    return {
        "service": service_name,
        "incidents": [i.model_dump() for i in incidents],
        "count": len(incidents),
    }


@app.get("/history/by-classification/{classification}", tags=["History"])
async def get_incidents_by_classification(classification: str, limit: int = 50):
    """
    Get incidents by their triage classification.

    Classifications: fixable, infra_issue, transient, needs_human
    """
    from backend.models import TriageClassification

    try:
        classification_enum = TriageClassification(classification)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid classification: {classification}. Use: fixable, infra_issue, transient, needs_human"}
        )

    # Check if using Firestore storage with query methods
    if hasattr(storage, 'get_incidents_by_classification'):
        results = storage.get_incidents_by_classification(classification_enum, limit)
    else:
        # Fallback for in-memory storage
        all_incidents = storage.list_incidents(limit=500)
        results = []
        for incident in all_incidents:
            triage = storage.get_triage_result(incident.id)
            if triage and triage.classification == classification_enum:
                results.append({
                    "incident": incident,
                    "triage": triage,
                })
        results = results[:limit]

    return {
        "classification": classification,
        "results": [
            {
                "incident": r["incident"].model_dump() if hasattr(r["incident"], 'model_dump') else r["incident"],
                "triage": r["triage"].model_dump() if hasattr(r["triage"], 'model_dump') else r["triage"],
            }
            for r in results
        ],
        "count": len(results),
    }


@app.get("/history/summary", tags=["History"])
async def get_recent_errors_summary(hours: int = 24):
    """
    Get a summary of recent errors for reporting.

    Returns counts by service, by status, and identifies the most affected service.
    """
    # Check if using Firestore storage with query methods
    if hasattr(storage, 'get_recent_errors_summary'):
        return storage.get_recent_errors_summary(hours)
    else:
        # Fallback for in-memory storage
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=hours)
        all_incidents = storage.list_incidents(limit=500)

        # Filter by time
        recent = [i for i in all_incidents if i.created_at >= cutoff]

        # Group by service
        by_service = {}
        by_status = {}

        for incident in recent:
            svc = incident.service_name
            by_service[svc] = by_service.get(svc, 0) + 1
            status = incident.status.value
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "total": len(recent),
            "hours": hours,
            "by_service": by_service,
            "by_status": by_status,
            "most_affected_service": max(by_service.keys(), key=lambda k: by_service[k]) if by_service else None,
        }


@app.get("/history/triage-decisions", tags=["History"])
async def get_triage_decisions(limit: int = 50):
    """
    Get recent triage decisions with their reasoning.

    Useful for reviewing and auditing AI decisions.
    """
    incidents = storage.list_incidents(limit=limit)

    decisions = []
    for incident in incidents:
        triage = storage.get_triage_result(incident.id)
        if triage:
            decisions.append({
                "incident_id": incident.id,
                "title": incident.title,
                "service": incident.service_name,
                "classification": triage.classification.value,
                "confidence": triage.confidence,
                "root_cause": triage.root_cause,
                "gcp_context": triage.gcp_context,
                "created_at": incident.created_at.isoformat(),
            })

    return {
        "decisions": decisions,
        "count": len(decisions),
    }


# ═══════════════ Pattern Learning Endpoints ═══════════════


@app.get("/patterns/stats", tags=["Pattern Learning"])
async def get_pattern_stats():
    """
    Get statistics about learned error patterns.

    Returns total patterns, occurrence counts, success rates,
    and classification breakdown.
    """
    from backend.knowledge import get_pattern_learner

    try:
        pattern_learner = get_pattern_learner(storage)
        return pattern_learner.get_statistics()
    except Exception as e:
        logger.error(f"Failed to get pattern stats: {e}")
        return {
            "total_patterns": 0,
            "total_occurrences": 0,
            "patterns_with_successful_fixes": 0,
            "average_success_rate": 0,
            "most_common_classification": None,
            "patterns_by_classification": {},
            "error": str(e),
        }


@app.get("/patterns/similar", tags=["Pattern Learning"])
async def find_similar_patterns(error: str, service: Optional[str] = None):
    """
    Find historical patterns similar to the given error message.

    Returns a suggestion if a matching pattern exists with enough
    historical data to inform classification.
    """
    from backend.knowledge import get_pattern_learner

    if not error:
        return JSONResponse(
            status_code=400,
            content={"error": "error parameter is required"}
        )

    try:
        pattern_learner = get_pattern_learner(storage)
        suggestion = pattern_learner.get_pattern_suggestion(
            error_msg=error,
            service=service or "unknown"
        )

        if suggestion:
            return {
                "found": True,
                "suggestion": suggestion.model_dump(),
            }
        else:
            return {
                "found": False,
                "message": "No matching pattern found",
            }

    except Exception as e:
        logger.error(f"Failed to find similar patterns: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Pattern lookup failed: {str(e)}"}
        )


@app.get("/patterns/config", tags=["Pattern Learning"])
async def get_pattern_config():
    """Get current pattern learning configuration."""
    return {
        "enabled": settings.pattern_learning_enabled,
        "min_occurrences": settings.pattern_min_occurrences,
        "override_success_rate": settings.pattern_override_success_rate,
        "override_confidence": settings.pattern_override_confidence,
    }


# ═══════════════ Webhook Endpoints ═══════════════


@app.post("/webhook/gcp-logs", tags=["Webhooks"])
async def receive_gcp_logs(request: Request):
    """
    Receive GCP Cloud Logging errors via Pub/Sub push.

    This endpoint receives error logs from GCP Cloud Logging via a Pub/Sub
    push subscription. It parses the log entry, applies filters, and creates
    an incident if appropriate.

    GCP Setup:
    1. Create topic: gcloud pubsub topics create oncall-helper-errors
    2. Create push subscription pointing to this endpoint
    3. Create log sink with filter: severity>=ERROR
    """
    from backend.services.gcp_logging import parse_pubsub_message, create_incident_from_log
    from backend.filters import is_transient_error, should_process_tenant

    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook request body: {e}")
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON body"}
        )

    try:
        # Parse the Pub/Sub message
        log_entry = parse_pubsub_message(data)
        logger.info(f"Received log entry: {log_entry.insert_id} from {log_entry.service_name}")

        # Check for duplicates
        if log_entry.insert_id and storage.is_duplicate(log_entry.insert_id):
            logger.debug(f"Duplicate log entry: {log_entry.insert_id}")
            return {
                "status": "duplicate",
                "reason": "Log entry already processed",
                "insert_id": log_entry.insert_id,
            }

        # Apply transient error filter
        is_transient, transient_reason, category = is_transient_error(log_entry.error_message)
        if is_transient:
            logger.info(f"Filtered transient error [{category}]: {transient_reason}")
            return {
                "status": "filtered",
                "reason": transient_reason,
                "category": category,
                "filter": "transient",
            }

        # Apply tenant filter
        should_process, tenant_reason = should_process_tenant(
            tenant_id=log_entry.tenant_id,
            tenant_name=log_entry.tenant_name
        )
        if not should_process:
            logger.info(f"Filtered by tenant: {tenant_reason}")
            return {
                "status": "filtered",
                "reason": tenant_reason,
                "filter": "tenant",
            }

        # Create incident
        incident = create_incident_from_log(log_entry)
        storage.save_incident(incident)

        logger.info(f"Created incident {incident.id}: {incident.title}")

        # Broadcast to WebSocket clients
        from backend.websocket_manager import ws_manager, create_pipeline_event_callback
        await ws_manager.broadcast_incident_created(
            incident_id=incident.id,
            title=incident.title,
            service=incident.service_name,
            severity=incident.severity.value,
        )

        # Trigger pipeline processing in background
        import asyncio
        from backend.agents.orchestrator import PipelineOrchestrator

        async def run_pipeline():
            try:
                callback = create_pipeline_event_callback()
                orchestrator = PipelineOrchestrator(
                    event_callback=lambda e: asyncio.create_task(callback(e)),
                    skip_sandbox=True,  # Skip Kind cluster for now
                    skip_verification=True,  # Skip production monitoring for now
                )
                result = await orchestrator.process_incident(incident)
                logger.info(f"Pipeline completed for {incident.id}: success={result.success}")
            except Exception as e:
                logger.error(f"Pipeline failed for {incident.id}: {e}")

        asyncio.create_task(run_pipeline())

        return {
            "status": "processing",
            "incident_id": incident.id,
            "title": incident.title,
            "severity": incident.severity.value,
            "service": incident.service_name,
        }

    except ValueError as e:
        logger.error(f"Failed to parse log entry: {e}")
        return JSONResponse(
            status_code=400,
            content={"error": f"Invalid log entry format: {str(e)}"}
        )


@app.post("/webhook/test", tags=["Webhooks"])
async def test_webhook(request: Request):
    """
    Test endpoint to simulate receiving a GCP log entry.

    Useful for development and testing without actual GCP setup.
    """
    from backend.services.gcp_logging import create_incident_from_log, GCPLogEntry
    from backend.filters import is_transient_error, should_process_tenant
    from backend.models import Severity

    try:
        data = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON body"}
        )

    # Create a mock log entry from the test data
    log_entry = GCPLogEntry(
        insert_id=data.get("insert_id", f"test-{datetime.utcnow().timestamp()}"),
        timestamp=datetime.utcnow(),
        severity=data.get("severity", "ERROR"),
        log_name=data.get("log_name", "test-log"),
        resource_type=data.get("resource_type", "cloud_run_revision"),
        resource_labels=data.get("resource_labels", {}),
        error_message=data.get("error_message", "Test error message"),
        stack_trace=data.get("stack_trace"),
        file_path=data.get("file_path"),
        service_name=data.get("service_name", "test-service"),
        tenant_id=data.get("tenant_id"),
        tenant_name=data.get("tenant_name"),
    )

    # Apply filters
    is_transient, transient_reason, category = is_transient_error(log_entry.error_message)
    if is_transient:
        return {
            "status": "filtered",
            "reason": transient_reason,
            "filter": "transient",
        }

    should_process, tenant_reason = should_process_tenant(
        tenant_id=log_entry.tenant_id,
        tenant_name=log_entry.tenant_name
    )
    if not should_process:
        return {
            "status": "filtered",
            "reason": tenant_reason,
            "filter": "tenant",
        }

    # Create incident
    incident = create_incident_from_log(log_entry)
    storage.save_incident(incident)

    # Broadcast to WebSocket clients
    from backend.websocket_manager import ws_manager, create_pipeline_event_callback
    await ws_manager.broadcast_incident_created(
        incident_id=incident.id,
        title=incident.title,
        service=incident.service_name,
        severity=incident.severity.value,
    )

    # Trigger pipeline processing in background
    import asyncio
    from backend.agents.orchestrator import PipelineOrchestrator

    async def run_pipeline():
        try:
            callback = create_pipeline_event_callback()
            orchestrator = PipelineOrchestrator(
                event_callback=lambda e: asyncio.create_task(callback(e)),
                skip_sandbox=True,  # Skip Kind cluster for now
                skip_verification=True,  # Skip production monitoring for now
            )
            result = await orchestrator.process_incident(incident)
            logger.info(f"Pipeline completed for {incident.id}: success={result.success}")
        except Exception as e:
            logger.error(f"Pipeline failed for {incident.id}: {e}")

    asyncio.create_task(run_pipeline())

    return {
        "status": "processing",
        "incident_id": incident.id,
        "title": incident.title,
        "severity": incident.severity.value,
    }


# ═══════════════ GCP Polling Endpoints ═══════════════


# Global GCP logging service instance
_gcp_service = None


def get_gcp_service():
    """Get or create the GCP logging service."""
    global _gcp_service
    if _gcp_service is None:
        from backend.services.gcp_logging import GCPLoggingService
        _gcp_service = GCPLoggingService()
    return _gcp_service


async def _start_gcp_polling_internal(interval_seconds: int = 30):
    """Internal function to start GCP polling (used by both lifespan and endpoint)."""
    from backend.services.gcp_logging import GCPLoggingService, create_incident_from_log
    from backend.websocket_manager import ws_manager, create_pipeline_event_callback, EventType
    from backend.agents.orchestrator import PipelineOrchestrator
    import asyncio

    gcp_service = get_gcp_service()

    if gcp_service.is_polling:
        return {"status": "already_running", "message": "GCP polling is already active"}

    async def process_incident(incident):
        """
        Process a new incident from GCP logs with smart pre-processing.

        Pipeline:
        1. Service filter - skip K8s infrastructure noise
        2. Error signature - generate for deduplication
        3. Aggregation check - increment existing or create new
        4. Tenant filter - skip demo tenants
        5. Transient check - auto-resolve if transient pattern
        6. Save and broadcast (if not aggregated)
        7. Run triage pipeline (if not auto-resolved)
        """
        from backend.filters.transient import is_transient_error
        from backend.filters.tenant import should_process_tenant
        from backend.filters.service_filter import should_process_service
        from backend.services.error_aggregator import get_error_aggregator
        from backend.models import IncidentStatus

        # 1. Service filter - skip K8s infrastructure noise
        should_process, service_reason = should_process_service(
            incident.service_name,
            incident.severity.value.upper(),
            incident.gcp_resource_type
        )
        if not should_process:
            logger.info(f"Filtered: {service_reason}")
            return

        # 2. Generate error signature for deduplication
        aggregator = get_error_aggregator(window_minutes=10)
        signature = aggregator.get_error_signature(
            incident.service_name,
            incident.error_message
        )
        incident.error_signature = signature

        # 3. Check if we should aggregate with existing incident
        existing_incident_id = aggregator.should_aggregate(signature)
        if existing_incident_id:
            # Increment count on existing incident instead of creating new
            new_count = aggregator.increment_count(signature)
            storage.increment_incident_count(existing_incident_id, new_count)
            logger.info(f"Aggregated into {existing_incident_id} (count: {new_count})")
            # Broadcast count update
            await ws_manager.broadcast(
                EventType.INCIDENT_UPDATED,
                {
                    "incident_id": existing_incident_id,
                    "occurrence_count": new_count,
                }
            )
            return

        # 4. Apply tenant filter
        should_process, tenant_reason = should_process_tenant(tenant_name=incident.tenant_name)
        if not should_process:
            logger.info(f"Filtered by tenant: {tenant_reason} (incident: {incident.id})")
            return

        # 5. Check for transient patterns - create but auto-resolve
        is_transient, transient_reason, category = is_transient_error(incident.error_message)
        if is_transient:
            incident.status = IncidentStatus.FILTERED
            incident.auto_resolved = True
            incident.auto_resolve_reason = f"[{category}] {transient_reason}"
            logger.info(f"Auto-resolving transient [{category}]: {transient_reason}")

        # 6. Save incident and register for aggregation
        storage.save_incident(incident)
        aggregator.register_incident(signature, incident.id)

        # 7. Broadcast to WebSocket clients
        await ws_manager.broadcast_incident_created(
            incident_id=incident.id,
            title=incident.title,
            service=incident.service_name,
            severity=incident.severity.value,
        )

        # 8. Run pipeline (skip for auto-resolved transient errors)
        if incident.auto_resolved:
            logger.info(f"Skipping pipeline for auto-resolved incident: {incident.id}")
            return

        callback = create_pipeline_event_callback()
        orchestrator = PipelineOrchestrator(
            event_callback=lambda e: asyncio.create_task(callback(e)),
            skip_sandbox=True,
            skip_verification=True,
        )
        try:
            result = await orchestrator.process_incident(incident)
            logger.info(f"Pipeline completed for {incident.id}: success={result.success}")
        except Exception as e:
            logger.error(f"Pipeline failed for {incident.id}: {e}")

    await gcp_service.start_polling(process_incident, interval_seconds)

    return {
        "status": "started",
        "message": f"GCP polling started (every {interval_seconds}s)",
        "project_id": gcp_service.project_id,
        "filter": gcp_service.log_filter,
    }


@app.post("/gcp/polling/start", tags=["GCP"])
async def start_gcp_polling(interval_seconds: int = 30):
    """
    Start polling GCP Cloud Logging for errors.

    This requires read access to GCP Cloud Logging (Logging Viewer role).
    Errors will be automatically processed through the incident pipeline.
    """
    return await _start_gcp_polling_internal(interval_seconds)


@app.post("/gcp/polling/stop", tags=["GCP"])
async def stop_gcp_polling():
    """Stop polling GCP Cloud Logging."""
    gcp_service = get_gcp_service()

    if not gcp_service.is_polling:
        return {"status": "not_running", "message": "GCP polling is not active"}

    await gcp_service.stop_polling()
    return {"status": "stopped", "message": "GCP polling stopped"}


@app.get("/gcp/polling/status", tags=["GCP"])
async def gcp_polling_status():
    """Get current GCP polling status."""
    gcp_service = get_gcp_service()
    return {
        "is_polling": gcp_service.is_polling,
        "project_id": gcp_service.project_id,
        "filter": gcp_service.log_filter,
    }


# ═══════════════ WebSocket Endpoint ═══════════════


from backend.websocket_manager import ws_manager, EventType


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time updates.

    Clients receive:
    - Welcome message with current metrics on connect
    - Incident lifecycle events (created, resolved, escalated)
    - Pipeline stage updates (triage, fix, review, test, PR)
    - Agent thinking messages
    - Code diff previews

    Clients can send:
    - {"type": "ping"} - heartbeat, server responds with pong
    - {"type": "subscribe", "incident_id": "..."} - subscribe to specific incident
    - {"type": "unsubscribe", "incident_id": "..."} - unsubscribe from incident
    """
    client_id = await ws_manager.connect(websocket)

    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_text()

            # Handle client message
            response = await ws_manager.handle_client_message(client_id, data)

            if response:
                await websocket.send_text(response.to_json())

    except WebSocketDisconnect:
        await ws_manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"WebSocket error for client {client_id}: {e}")
        await ws_manager.disconnect(client_id)


@app.get("/ws/connections", tags=["WebSocket"])
async def get_websocket_connections():
    """Get information about active WebSocket connections."""
    return {
        "count": ws_manager.connection_count,
        "connections": ws_manager.get_all_connections(),
    }


# ═══════════════ Error Handlers ═══════════════


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc) if settings.is_development else None,
        }
    )


# ═══════════════ Main ═══════════════


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.is_development,
    )
