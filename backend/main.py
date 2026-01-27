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

from fastapi import FastAPI, Request
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

    yield

    # Shutdown
    logger.info("Shutting down On Call Helper")


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
    return {
        "incidents": [i.model_dump() for i in incidents],
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
