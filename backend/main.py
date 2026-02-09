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

    # Migrate any gchat incidents from shared collection to separate collections
    await _migrate_gchat_to_separate_collections()

    # Recover incidents stuck in "triaging" status (lost tasks from previous restart)
    await _recover_stuck_incidents()

    # Auto-start Google Chat polling if enabled
    if settings.gchat_auto_poll and settings.gchat_space_id:
        logger.info(f"Auto-starting Google Chat polling (interval: {settings.gchat_poll_interval}s)")
        try:
            await _start_gchat_polling_internal(settings.gchat_poll_interval)
            logger.info("Google Chat polling auto-started successfully")
        except Exception as e:
            logger.error(f"Failed to auto-start Google Chat polling: {e}")

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

    # Stop Google Chat polling if running
    try:
        gchat_poller = get_gchat_poller()
        if gchat_poller.is_polling:
            await gchat_poller.stop_polling()
            logger.info("Google Chat polling stopped")
    except Exception as e:
        logger.error(f"Error stopping Google Chat polling: {e}")

    # Close WebSocket connections
    from backend.websocket_manager import ws_manager
    await ws_manager.close_all()


async def _migrate_gchat_to_separate_collections():
    """One-time migration: move gchat incidents from 'incidents' to 'gchat_incidents'.

    Also migrates triage_results for those incidents to gchat_triage_results.
    Safe to re-run — skips docs that already exist in the target collection.
    """
    import asyncio

    # Only relevant for Firestore backend
    from backend.storage_firestore import FirestoreStorage
    if not isinstance(storage, FirestoreStorage):
        return

    def _migrate():
        from google.cloud.firestore_v1 import FieldFilter
        db = storage.db
        migrated = 0

        # Find gchat incidents in the old collection
        query = db.collection("incidents").where(
            filter=FieldFilter("source", "==", "gchat")
        )
        for doc in query.stream():
            doc_id = doc.id
            data = doc.to_dict()

            # Copy to gchat_incidents (skip if already there)
            target_ref = db.collection("gchat_incidents").document(doc_id)
            if not target_ref.get().exists:
                target_ref.set(data)
                logger.info(f"Migrated incident {doc_id} to gchat_incidents")

            # Also migrate triage result if it exists
            triage_doc = db.collection("triage_results").document(doc_id).get()
            if triage_doc.exists:
                triage_target = db.collection("gchat_triage_results").document(doc_id)
                if not triage_target.get().exists:
                    triage_target.set(triage_doc.to_dict())
                    logger.info(f"Migrated triage result {doc_id} to gchat_triage_results")

            # Delete from old collection
            doc.reference.delete()
            old_triage = db.collection("triage_results").document(doc_id)
            if old_triage.get().exists:
                old_triage.delete()

            migrated += 1

        if migrated:
            logger.info(f"Migrated {migrated} gchat incidents to separate collections")
        else:
            logger.debug("No gchat incidents to migrate")

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _migrate)
    except Exception as e:
        logger.error(f"GChat migration failed (non-fatal): {e}")


async def _recover_stuck_incidents():
    """Recover incidents stuck in 'triaging' or 'fixing' status from a previous restart.

    When the server restarts, any in-flight pipeline tasks are lost. This finds
    incidents in transient statuses and re-triggers their pipeline.
    """
    import asyncio
    from backend.models import IncidentStatus
    from backend.agents.orchestrator import PipelineOrchestrator
    from backend.websocket_manager import create_pipeline_event_callback

    transient_statuses = [IncidentStatus.TRIAGING, IncidentStatus.FIXING]
    recovered = 0

    for status in transient_statuses:
        try:
            # Check both GCP and GChat collections
            stuck = storage.list_incidents(status=status, limit=50)
            stuck += storage.list_incidents(status=status, source="gchat", limit=50)
            for incident in stuck:
                # Check if triage result already exists (task completed before crash)
                triage = storage.get_triage_result(incident.id)
                if triage and status == IncidentStatus.TRIAGING:
                    # Triage finished but status wasn't updated — just fix status
                    storage.update_incident_status(incident.id, IncidentStatus.FIXED)
                    logger.info(f"Recovered {incident.id}: had triage result, marked fixed")
                    recovered += 1
                    continue

                # Re-run pipeline
                logger.info(f"Re-triggering pipeline for stuck incident {incident.id} (status={status.value})")
                storage.update_incident_status(incident.id, IncidentStatus.ACTIVE)

                async def _rerun(inc=incident):
                    try:
                        callback = create_pipeline_event_callback()
                        orchestrator = PipelineOrchestrator(
                            event_callback=lambda e: asyncio.create_task(callback(e)),
                            skip_sandbox=True,
                            skip_verification=True,
                        )
                        result = await orchestrator.process_incident(inc)
                        logger.info(f"Recovery pipeline done for {inc.id}: success={result.success}")
                    except Exception as e:
                        logger.error(f"Recovery pipeline failed for {inc.id}: {e}")
                        storage.update_incident_status(inc.id, IncidentStatus.ACTIVE)

                asyncio.create_task(_rerun())
                recovered += 1
        except Exception as e:
            logger.error(f"Error recovering stuck {status.value} incidents: {e}")

    if recovered:
        logger.info(f"Recovered {recovered} stuck incidents")
    else:
        logger.info("No stuck incidents found to recover")


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

from typing import Optional

@app.get("/metrics", tags=["Metrics"], response_model=Metrics)
async def get_metrics(source: Optional[str] = None):
    """Get current incident processing metrics, optionally filtered by source."""
    return storage.get_metrics(source=source)


# ═══════════════ Incidents Endpoints ═══════════════

@app.get("/incidents", tags=["Incidents"])
async def list_incidents(status: Optional[str] = None, source: Optional[str] = None, limit: int = 100):
    """List incidents, optionally filtered by status and/or source."""
    import asyncio
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

    # Run blocking Firestore calls in thread pool to avoid blocking the event loop
    loop = asyncio.get_event_loop()

    def _fetch_incidents_with_triage():
        incidents = storage.list_incidents(status=status_filter, source=source, limit=limit)
        # Batch-fetch all triage classifications in one Firestore call
        incident_ids = [inc.id for inc in incidents]
        triage_map = storage.get_triage_classifications_batch(incident_ids, source=source)
        incident_list = []
        for inc in incidents:
            inc_data = inc.model_dump()
            classification = triage_map.get(inc.id)
            if classification:
                inc_data["triage_classification"] = classification
            incident_list.append(inc_data)
        return incident_list

    incident_list = await loop.run_in_executor(None, _fetch_incidents_with_triage)

    return {
        "incidents": incident_list,
        "count": len(incident_list),
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
async def get_all_incidents_with_details(status: Optional[str] = None, source: Optional[str] = None, limit: int = 100):
    """
    Get all incidents with complete details (triage, fix, test, verification).

    Useful for displaying in a table view with all AI agent outputs.
    """
    import asyncio
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

    loop = asyncio.get_event_loop()

    def _fetch_all_details():
        incidents = storage.list_incidents(status=status_filter, source=source, limit=limit)

        # Batch-fetch triage classifications to avoid N individual reads
        incident_ids = [inc.id for inc in incidents]
        triage_map = storage.get_triage_classifications_batch(incident_ids, source=source)

        result = []
        for incident in incidents:
            inc_data = {
                "incident": incident.model_dump(),
                "triage": None,
                "fix": None,
                "test": None,
                "verification": None,
            }
            # Add classification from batch lookup
            classification = triage_map.get(incident.id)
            if classification:
                inc_data["incident"]["triage_classification"] = classification
            result.append(inc_data)

        return result

    result = await loop.run_in_executor(None, _fetch_all_details)

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
    from backend.websocket_manager import EventType
    await ws_manager.broadcast(
        EventType.INCIDENT_RESOLVED,
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


# ═══════════════ Google Chat Webhook ═══════════════


@app.post("/webhook/gchat", tags=["Webhooks"])
async def receive_gchat_message(request: Request):
    """
    Receive Google Chat interaction events.

    Google Chat App sends MESSAGE events when messages appear
    in the configured space. Structured alert messages are parsed
    into incidents and processed through the triage pipeline.
    """
    from backend.services.gchat import (
        parse_gchat_event,
        parse_alert_text,
        create_incident_from_gchat,
    )

    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse GChat webhook body: {e}")
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    # Parse the interaction event
    try:
        event = parse_gchat_event(data)
    except Exception as e:
        logger.error(f"Failed to parse GChat event: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)})

    # Only process MESSAGE events
    if event.event_type != "MESSAGE":
        logger.debug(f"Ignoring GChat event type: {event.event_type}")
        return {"status": "ignored", "reason": f"Event type {event.event_type} not processed"}

    # Only process messages from the configured space
    if event.space_id != settings.gchat_space_id:
        logger.debug(f"Ignoring message from space: {event.space_id}")
        return {"status": "ignored", "reason": "Message from unconfigured space"}

    # Check for thread-based deduplication
    if event.thread_id:
        existing = storage.find_incident_by_gchat_thread(event.thread_id)
        if existing:
            new_count = existing.occurrence_count + 1
            storage.increment_incident_count(existing.id, new_count)
            from backend.websocket_manager import ws_manager, EventType
            await ws_manager.broadcast(
                EventType.INCIDENT_UPDATED,
                {"incident_id": existing.id, "occurrence_count": new_count},
            )
            logger.info(f"GChat thread update: {existing.id} (count: {new_count})")
            return {"text": f"Updated case {existing.id} (occurrence #{new_count})"}

    # Parse alert from message text
    alert = parse_alert_text(event.text)
    incident = create_incident_from_gchat(event, alert)

    # Save incident
    storage.save_incident(incident)
    logger.info(f"GChat case created: {incident.id} - {incident.title} (service: {incident.service_name})")

    # Broadcast via WebSocket
    from backend.websocket_manager import ws_manager, create_pipeline_event_callback
    await ws_manager.broadcast_incident_created(
        incident_id=incident.id,
        title=incident.title,
        service=incident.service_name,
        severity=incident.severity.value,
        source="gchat",
    )

    # Run triage pipeline in background
    import asyncio
    from backend.agents.orchestrator import PipelineOrchestrator

    async def run_gchat_pipeline():
        try:
            callback = create_pipeline_event_callback()
            orchestrator = PipelineOrchestrator(
                event_callback=lambda e: asyncio.create_task(callback(e)),
                skip_sandbox=True,
                skip_verification=True,
            )
            result = await orchestrator.process_incident(incident)
            logger.info(f"GChat pipeline completed for {incident.id}: success={result.success}")
        except Exception as e:
            logger.error(f"GChat pipeline failed for {incident.id}: {e}")
            # Reset to active so it doesn't stay stuck in "triaging" forever
            from backend.models import IncidentStatus
            storage.update_incident_status(incident.id, IncidentStatus.ACTIVE)

    asyncio.create_task(run_gchat_pipeline())

    return {"text": f"Case {incident.id} created. Triaging..."}


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


# ═══════════════ Health Check Endpoints ═══════════════

_checkout_lock = False


@app.get("/health-checks", tags=["Health Checks"])
async def list_health_check_runs():
    """List recent health check runs (newest first, without output)."""
    import asyncio

    loop = asyncio.get_event_loop()
    runs = await loop.run_in_executor(None, lambda: storage.list_health_check_runs(limit=50))
    return {
        "runs": [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "exit_code": r.exit_code,
                "duration_seconds": r.duration_seconds,
            }
            for r in runs
        ],
        "count": len(runs),
    }


@app.get("/health-checks/{run_id}", tags=["Health Checks"])
async def get_health_check_run(run_id: str):
    """Get a single health check run with full output."""
    import asyncio

    loop = asyncio.get_event_loop()
    run = await loop.run_in_executor(None, lambda: storage.get_health_check_run(run_id))
    if not run:
        return JSONResponse(status_code=404, content={"error": f"Run not found: {run_id}"})
    return run.model_dump()


@app.post("/health-checks/{run_id}/summarize", tags=["Health Checks"])
async def summarize_health_check(run_id: str):
    """Summarize a health check run's output for sharing in Google Chat."""
    import asyncio
    from backend.ai_client import create_ai_client, get_triage_model

    loop = asyncio.get_event_loop()
    run = await loop.run_in_executor(None, lambda: storage.get_health_check_run(run_id))
    if not run:
        return JSONResponse(status_code=404, content={"error": f"Run not found: {run_id}"})
    if not run.output or not run.output.strip():
        return JSONResponse(status_code=400, content={"error": "No output to summarize"})

    client = create_ai_client()
    if not client:
        return JSONResponse(status_code=500, content={"error": "AI client not configured"})

    duration_str = f" in {run.duration_seconds}s" if run.duration_seconds else ""
    timestamp = run.started_at.strftime("%Y-%m-%d %H:%M UTC") if run.started_at else "unknown"

    system_prompt = (
        "You are an SRE assistant that produces structured on-call status reports from health check output. "
        "Extract the ACTUAL data and numbers from the output — never fabricate or approximate metrics.\n\n"
        "Format the report using these exact sections in order. "
        "Include EVERY section that has data in the output. Skip only if truly absent.\n\n"
        "INFRASTRUCTURE HEALTH\n"
        "  One line per service, left-aligned with consistent padding, ✅/❌/⚠️ prefix and key metrics.\n"
        "  Format exactly like:\n"
        "  AlloyDB:         ✅ CPU 5.5% | 21 conns | 0ms wait\n"
        "  Pub/Sub:         ✅ All backlogs 0s\n"
        "  Entity Enricher: ✅ Active (19/19 enricher, 21/21 extractor), 0 errors\n"
        "  Cloud Run Jobs:  ✅ 35 successful, 0 failed\n"
        "  Gemini API:      ⚠️ 1 x 503 overloaded\n"
        "  Incidents:       ✅ None open\n\n"
        "PRODUCTION CUSTOMER ACTIVITY (1hr / 24hr / 5d) — alerts as soc_actionable/total\n"
        "  One line per customer, aligned columns. Format:\n"
        "  Hawkeye    ✅ Active    3/3   | 97/98   | 515/519    |  3 cases/hr\n"
        "  RLI        ✅ Active   0/49   | 0/315   | 0/352      |  1 cases/hr\n"
        "  IPA        ⚠️ Quiet     0/0   | 0/140   | 0/168      |  0 cases/hr\n"
        "  Use ✅ Active, ⚠️ Quiet, Low, or Inactive based on the data. Sort by total activity desc.\n\n"
        "CASE EVENTS VOLUME (10min / 1hr / 24hr)\n"
        "  Show per-tenant if any have elevated volume. Include flood warnings if present.\n\n"
        "DEMO TENANT CHECK\n"
        "  Only include if demo tenants have event floods. Otherwise one line: ✅ No demo floods.\n\n"
        "CASES TODAY — soc_actionable breakdown\n"
        "  Per-customer: cases count, cases with soc_actionable, actionable/total alerts.\n"
        "  Format: Hawkeye:    72 cases (72 w/ soc_act, 72/72 alerts actionable)\n"
        "  Add a Notable line calling out customers with zero actionable alerts and their numbers.\n\n"
        "ANALYST ACTIVITY TODAY\n"
        "  Show active analysts and total actions per customer if data present.\n\n"
        "ANALYST TRIAGE TODAY\n"
        "  Per-customer: closed, false_positive, in_progress, awaiting_client, total.\n"
        "  Format: Hawkeye:   72 closed (73 total)\n\n"
        "5-DAY TREND\n"
        "  One line per day: date, cases, alerts, DAU. ✓ for normal, 🟡 for anomalies.\n"
        "  Note if current day is in progress.\n\n"
        "ALERT PIPELINE HEALTH\n"
        "  Alert-to-case: ✅/❌ with stuck alert count if any.\n\n"
        "OPEN CASE ACCUMULATION\n"
        "  Per-tenant open case counts if any are elevated. Include BQ sync drift notes.\n\n"
        "INTEGRATIONS\n"
        "  SOAR:      ✅/⚠️ with error count\n"
        "  GenAI:     ✅/⚠️ with 15min and 24hr error counts\n"
        "  Ingestion: ✅/❌ with JSONB failure count\n\n"
        "ERROR SUMMARY\n"
        "  Top services by error count (last 15min) if any errors exist.\n\n"
        "⚠️ ITEMS TO WATCH\n"
        "  Bullet list of EVERYTHING concerning found in the output:\n"
        "  • High open case counts (include tenant name and number)\n"
        "  • Error spikes (include service name and count)\n"
        "  • Zero-actionable customers with high alert volume\n"
        "  • Anomalous daily trends\n"
        "  • Approaching thresholds\n"
        "  • Demo tenant floods\n"
        "  • Any CRITICAL or WARNING messages from the output\n"
        "  Include specific numbers for every item.\n\n"
        "Rules:\n"
        "- PLAIN TEXT only — no markdown (no #, no **, no ```)\n"
        "- Use spaces for alignment to make columns line up\n"
        "- Preserve EXACT numbers from the output — never round, estimate, or fabricate\n"
        "- Use ✅ for healthy, ❌ for broken, ⚠️ for warning/degraded, 🟡 for elevated\n"
        "- This goes directly into a Google Chat message — keep it compact but data-complete\n"
        "- If data for a section is missing or the query failed, write: (no data available)"
    )

    user_msg = (
        f"Health check ran at {timestamp}, status: {run.status}{duration_str}.\n\n"
        f"Full output:\n{run.output[:16000]}"
    )

    try:
        message = await asyncio.to_thread(
            client.messages.create,
            model=get_triage_model(),
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        summary_text = message.content[0].text.strip()
        return {"summary": summary_text, "run_id": run_id, "status": run.status}
    except Exception as e:
        logger.error(f"Health check summarize failed: {e}")
        return JSONResponse(status_code=500, content={"error": f"Summarization failed: {str(e)}"})


@app.post("/health-checks/run", tags=["Health Checks"])
async def run_health_check_stream():
    """
    Trigger a new health check run and stream output via SSE.

    Returns text/event-stream with events:
      - event: metadata   data: {"id": "hc-xxx", "started_at": "..."}
      - event: output     data: {"line": "..."}
      - event: complete   data: {"id": "hc-xxx", "status": "completed", ...}
      - event: error      data: {"message": "..."}
    """
    import asyncio
    import json
    import os
    import time
    import uuid

    from fastapi.responses import StreamingResponse
    from backend.models import HealthCheckRun

    global _checkout_lock
    if _checkout_lock:
        return JSONResponse(status_code=409, content={"error": "Health check already running"})

    script_path = str(settings.oncall_repo_path / "scripts" / "oncall-checkout.sh")
    if not os.path.isfile(script_path):
        return JSONResponse(status_code=404, content={"error": f"Script not found: {script_path}"})

    run_id = f"hc-{uuid.uuid4().hex[:12]}"

    async def event_generator():
        global _checkout_lock
        _checkout_lock = True
        start = time.time()

        run = HealthCheckRun(id=run_id, started_at=datetime.utcnow(), status="running")
        storage.save_health_check_run(run)

        # Send metadata event
        yield f"event: metadata\ndata: {json.dumps({'id': run_id, 'started_at': run.started_at.isoformat()})}\n\n"

        output_lines = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "TERM": "dumb"},
            )

            # Read stdout line-by-line and stream as SSE
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=300)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise asyncio.TimeoutError()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                output_lines.append(decoded)
                yield f"event: output\ndata: {json.dumps({'line': decoded})}\n\n"

            await proc.wait()
            duration = time.time() - start
            status = "completed" if proc.returncode == 0 else "failed"

            # Save final result
            run.completed_at = datetime.utcnow()
            run.status = status
            run.exit_code = proc.returncode
            run.duration_seconds = round(duration, 1)
            run.output = "".join(output_lines)
            storage.save_health_check_run(run)

            yield f"event: complete\ndata: {json.dumps({'id': run_id, 'status': status, 'exit_code': proc.returncode, 'duration_seconds': round(duration, 1)})}\n\n"

        except asyncio.TimeoutError:
            duration = time.time() - start
            run.completed_at = datetime.utcnow()
            run.status = "timeout"
            run.duration_seconds = round(duration, 1)
            run.output = "".join(output_lines)
            storage.save_health_check_run(run)
            yield f"event: error\ndata: {json.dumps({'message': 'Script timed out after 5 minutes', 'id': run_id})}\n\n"

        except Exception as e:
            logger.error(f"Health check run failed: {e}")
            run.completed_at = datetime.utcnow()
            run.status = "failed"
            run.output = "".join(output_lines)
            storage.save_health_check_run(run)
            yield f"event: error\ndata: {json.dumps({'message': str(e), 'id': run_id})}\n\n"

        finally:
            _checkout_lock = False

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
        7. Run triage pipeline
        """
        from backend.filters.transient import is_transient_error
        from backend.filters.tenant import should_process_tenant
        from backend.filters.service_filter import should_process_service
        from backend.services.error_aggregator import get_error_aggregator
        from backend.knowledge.pattern_learner import get_pattern_learner
        from backend.models import IncidentStatus
        from backend.config import settings

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

        # 5. Check for transient patterns - discard entirely (Layer 1: regex)
        is_transient, transient_reason, category = is_transient_error(incident.error_message)
        if is_transient:
            logger.info(f"Discarding transient [{category}]: {transient_reason} (incident: {incident.id})")
            # Record pattern so system learns from discards
            try:
                pattern_learner = get_pattern_learner()
                pattern_learner.record_incident(
                    incident_id=incident.id,
                    error_msg=incident.error_message,
                    service=incident.service_name,
                    classification="transient",
                )
            except Exception as e:
                logger.warning(f"Pattern recording failed for discarded transient: {e}")
            return

        # 5b. Pattern-learning-based auto-discard (Layer 2: learned patterns)
        if settings.pattern_learning_enabled:
            try:
                pattern_learner = get_pattern_learner()
                suggestion = pattern_learner.get_pattern_suggestion(
                    incident.error_message, incident.service_name
                )
                if (
                    suggestion
                    and suggestion.classification == "transient"
                    and suggestion.occurrence_count >= settings.pattern_min_occurrences
                    and suggestion.success_rate >= settings.pattern_override_success_rate
                    and suggestion.confidence >= settings.pattern_override_confidence
                ):
                    logger.info(
                        f"Pattern-learned discard: {suggestion.pattern_id} "
                        f"({suggestion.occurrence_count} occurrences, "
                        f"{suggestion.success_rate:.0%} transient) (incident: {incident.id})"
                    )
                    pattern_learner.record_incident(
                        incident_id=incident.id,
                        error_msg=incident.error_message,
                        service=incident.service_name,
                        classification="transient",
                    )
                    return
            except Exception as e:
                logger.warning(f"Pattern-based discard check failed: {e}")

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

        # 8. Run triage pipeline
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


# ═══════════════ Google Chat Polling Endpoints ═══════════════


# Global Google Chat poller instance
_gchat_poller = None


def get_gchat_poller():
    """Get or create the Google Chat poller."""
    global _gchat_poller
    if _gchat_poller is None:
        from backend.services.gchat_poller import GoogleChatPoller
        creds_path = settings.gchat_credentials_path or None
        _gchat_poller = GoogleChatPoller(
            space_id=settings.gchat_space_id,
            credentials_path=creds_path,
        )
    return _gchat_poller


async def _start_gchat_polling_internal(interval_seconds: int = 30):
    """Internal function to start Google Chat polling."""
    from backend.services.gchat import (
        parse_gchat_event,
        parse_alert_text,
        create_incident_from_gchat,
    )
    from backend.websocket_manager import ws_manager, create_pipeline_event_callback, EventType
    from backend.agents.orchestrator import PipelineOrchestrator
    import asyncio

    poller = get_gchat_poller()

    if poller.is_polling:
        return {"status": "already_running", "message": "Google Chat polling is already active"}

    async def process_chat_message(raw_msg: dict):
        """
        Process a raw Chat API message dict.

        Wraps it into the webhook event format so parse_gchat_event can handle it,
        then follows the same flow as receive_gchat_message.
        """
        # Wrap raw message into webhook-style event dict
        event_data = {
            "type": "MESSAGE",
            "space": raw_msg.get("space", {"name": settings.gchat_space_id}),
            "message": raw_msg,
        }

        event = parse_gchat_event(event_data)

        # Thread-based deduplication
        if event.thread_id:
            existing = storage.find_incident_by_gchat_thread(event.thread_id)
            if existing:
                new_count = existing.occurrence_count + 1
                storage.increment_incident_count(existing.id, new_count)
                await ws_manager.broadcast(
                    EventType.INCIDENT_UPDATED,
                    {"incident_id": existing.id, "occurrence_count": new_count},
                )
                logger.info(f"GChat poll thread update: {existing.id} (count: {new_count})")
                return

        # Parse alert from message text
        alert = parse_alert_text(event.text)
        incident = create_incident_from_gchat(event, alert)

        # Save incident
        storage.save_incident(incident)
        logger.info(f"GChat poll case created: {incident.id} - {incident.title}")

        # Broadcast via WebSocket
        await ws_manager.broadcast_incident_created(
            incident_id=incident.id,
            title=incident.title,
            service=incident.service_name,
            severity=incident.severity.value,
            source="gchat",
        )

        # Run triage pipeline in background
        async def run_gchat_pipeline():
            try:
                callback = create_pipeline_event_callback()
                orchestrator = PipelineOrchestrator(
                    event_callback=lambda e: asyncio.create_task(callback(e)),
                    skip_sandbox=True,
                    skip_verification=True,
                )
                result = await orchestrator.process_incident(incident)
                logger.info(f"GChat poll pipeline completed for {incident.id}: success={result.success}")
            except Exception as e:
                logger.error(f"GChat poll pipeline failed for {incident.id}: {e}")
                from backend.models import IncidentStatus
                storage.update_incident_status(incident.id, IncidentStatus.ACTIVE)

        asyncio.create_task(run_gchat_pipeline())

    await poller.start_polling(process_chat_message, interval_seconds)

    return {
        "status": "started",
        "message": f"Google Chat polling started (every {interval_seconds}s)",
        "space_id": settings.gchat_space_id,
    }


@app.post("/gchat/polling/start", tags=["Google Chat"])
async def start_gchat_polling(interval_seconds: int = 30):
    """Start polling Google Chat for new messages."""
    return await _start_gchat_polling_internal(interval_seconds)


@app.post("/gchat/polling/stop", tags=["Google Chat"])
async def stop_gchat_polling():
    """Stop polling Google Chat."""
    poller = get_gchat_poller()

    if not poller.is_polling:
        return {"status": "not_running", "message": "Google Chat polling is not active"}

    await poller.stop_polling()
    return {"status": "stopped", "message": "Google Chat polling stopped"}


@app.get("/gchat/polling/status", tags=["Google Chat"])
async def gchat_polling_status():
    """Get current Google Chat polling status."""
    poller = get_gchat_poller()
    return {
        "is_polling": poller.is_polling,
        "space_id": settings.gchat_space_id,
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
