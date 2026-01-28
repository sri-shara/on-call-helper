"""
Production Verification Service for On Call Helper.

Monitors production after fix deployment to verify the error is resolved.
Checks Cloud Logging for recurrence of the same error signature.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.models import Incident, VerificationResult, VerificationStatus

logger = logging.getLogger(__name__)


class ProductionMonitorError(Exception):
    """Base exception for production monitor errors."""
    pass


class MonitoringNotConfiguredError(ProductionMonitorError):
    """Raised when GCP is not configured for monitoring."""
    pass


class MonitoringState(str, Enum):
    """State of a monitoring task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class MonitoringTask:
    """Represents an active monitoring task."""

    incident_id: str
    error_filter: str
    start_time: datetime
    end_time: datetime
    pr_url: Optional[str] = None
    state: MonitoringState = MonitoringState.PENDING
    error_counts: List[int] = field(default_factory=list)
    baseline_count: int = 0
    result: Optional[VerificationResult] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "incident_id": self.incident_id,
            "error_filter": self.error_filter,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "pr_url": self.pr_url,
            "state": self.state.value,
            "error_counts": self.error_counts,
            "baseline_count": self.baseline_count,
            "result": self.result.dict() if self.result else None,
        }


class ProductionMonitorService:
    """
    Monitor production after fix deployment to verify resolution.

    Watches Cloud Logging for recurrence of the same error signature
    and determines if the fix was successful.
    """

    # Success thresholds
    COMPLETE_RESOLUTION_THRESHOLD = 0  # No errors = complete success
    SIGNIFICANT_REDUCTION_THRESHOLD = 0.1  # 90% reduction = success

    def __init__(
        self,
        gcp_project_id: Optional[str] = None,
        monitoring_duration_hours: Optional[int] = None,
        check_interval_minutes: Optional[int] = None,
    ):
        """
        Initialize the production monitor service.

        Args:
            gcp_project_id: GCP project ID for Cloud Logging
            monitoring_duration_hours: How long to monitor after deployment
            check_interval_minutes: How often to check for errors
        """
        self.project_id = gcp_project_id or settings.gcp_project_id
        self.monitoring_duration_hours = (
            monitoring_duration_hours or settings.verification_duration_hours
        )
        self.check_interval_minutes = (
            check_interval_minutes or settings.verification_check_interval_minutes
        )

        # Active monitoring tasks
        self._tasks: Dict[str, MonitoringTask] = {}

        # GCP client (lazy loaded)
        self._logging_client = None

    def _check_configured(self) -> None:
        """Check if GCP is configured for monitoring."""
        if not self.project_id:
            raise MonitoringNotConfiguredError(
                "GCP project ID not configured. "
                "Set GCP_PROJECT_ID environment variable."
            )

    def _get_logging_client(self):
        """Get or create the GCP Logging client."""
        if self._logging_client is None:
            try:
                from google.cloud import logging as gcp_logging

                self._logging_client = gcp_logging.Client(project=self.project_id)
            except ImportError:
                raise MonitoringNotConfiguredError(
                    "google-cloud-logging not installed. "
                    "Run: pip install google-cloud-logging"
                )
            except Exception as e:
                raise ProductionMonitorError(f"Failed to create GCP client: {e}")

        return self._logging_client

    def _build_error_filter(self, incident: Incident) -> str:
        """
        Build a Cloud Logging filter for the error signature.

        Creates a filter that matches the same error pattern in the same service.

        Args:
            incident: The incident to build a filter for

        Returns:
            Cloud Logging filter string
        """
        # Escape special characters in error message
        # Take first 100 chars to avoid overly specific matching
        error_snippet = incident.error_message[:100]
        # Escape quotes and backslashes
        error_snippet = error_snippet.replace("\\", "\\\\").replace('"', '\\"')

        # Build filter components
        filters = [
            'severity>=ERROR',
            f'resource.labels.service_name="{incident.service_name}"',
        ]

        # Add error message pattern
        # Use textPayload or jsonPayload.message depending on log format
        filters.append(f'(textPayload:"{error_snippet}" OR jsonPayload.message:"{error_snippet}")')

        return " AND ".join(filters)

    async def _count_errors(
        self,
        error_filter: str,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        """
        Count errors matching the filter in the time range.

        Args:
            error_filter: Cloud Logging filter
            start_time: Start of time range
            end_time: End of time range

        Returns:
            Number of matching log entries
        """
        self._check_configured()

        # Format timestamps for Cloud Logging
        start_str = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        full_filter = f'{error_filter} AND timestamp>="{start_str}" AND timestamp<="{end_str}"'

        logger.debug(f"Counting errors with filter: {full_filter}")

        try:
            client = self._get_logging_client()

            # Use list_entries to count matching logs
            count = 0
            entries = client.list_entries(filter_=full_filter, page_size=1000)

            for _ in entries:
                count += 1
                # Cap at reasonable limit to avoid long counts
                if count >= 10000:
                    break

            logger.debug(f"Found {count} errors matching filter")
            return count

        except Exception as e:
            logger.error(f"Failed to count errors: {e}")
            raise ProductionMonitorError(f"Failed to count errors: {e}")

    async def _count_errors_mock(
        self,
        error_filter: str,
        start_time: datetime,
        end_time: datetime,
    ) -> int:
        """
        Mock error counting for testing without GCP.

        Returns a simulated count based on time elapsed.
        """
        # Simulate decreasing errors over time (fix working)
        time_elapsed = (datetime.utcnow() - start_time).total_seconds()
        hours_elapsed = time_elapsed / 3600

        # Start with 10 errors/hour, decrease to 0 over monitoring period
        if hours_elapsed < 0.5:
            return 5
        elif hours_elapsed < 1:
            return 2
        else:
            return 0

    def _analyze_results(
        self,
        task: MonitoringTask,
    ) -> VerificationResult:
        """
        Analyze monitoring results and determine verification status.

        Args:
            task: The completed monitoring task

        Returns:
            VerificationResult with status and details
        """
        total_errors_after = sum(task.error_counts)
        baseline = task.baseline_count

        # Determine status
        if total_errors_after == 0:
            status = VerificationStatus.SUCCESS
            message = "Error completely resolved - no occurrences since deployment"
        elif baseline > 0 and total_errors_after < baseline * self.SIGNIFICANT_REDUCTION_THRESHOLD:
            status = VerificationStatus.SUCCESS
            reduction = ((baseline - total_errors_after) / baseline) * 100
            message = f"Error reduced by {reduction:.0f}% ({baseline} → {total_errors_after})"
        elif baseline > 0 and total_errors_after < baseline:
            status = VerificationStatus.PARTIAL
            reduction = ((baseline - total_errors_after) / baseline) * 100
            message = f"Error reduced by {reduction:.0f}% but not eliminated ({baseline} → {total_errors_after})"
        elif baseline == 0 and total_errors_after > 0:
            status = VerificationStatus.FAILED
            message = f"New errors detected after deployment ({total_errors_after} errors)"
        else:
            status = VerificationStatus.FAILED
            message = f"Error persists or increased ({baseline} → {total_errors_after})"

        return VerificationResult(
            incident_id=task.incident_id,
            status=status,
            message=message,
            errors_before=baseline,
            errors_after=total_errors_after,
            monitoring_duration_hours=self.monitoring_duration_hours,
            pr_url=task.pr_url,
        )

    async def start_monitoring(
        self,
        incident: Incident,
        pr_merged_at: datetime,
        pr_url: Optional[str] = None,
    ) -> MonitoringTask:
        """
        Start monitoring production for error recurrence.

        Args:
            incident: The incident that was fixed
            pr_merged_at: When the fix PR was merged
            pr_url: URL of the merged PR

        Returns:
            MonitoringTask representing the monitoring job
        """
        self._check_configured()

        # Build error filter
        error_filter = self._build_error_filter(incident)

        # Calculate monitoring window
        end_time = pr_merged_at + timedelta(hours=self.monitoring_duration_hours)

        # Get baseline (errors before fix in same time window)
        baseline_start = pr_merged_at - timedelta(hours=self.monitoring_duration_hours)
        baseline_count = await self._count_errors(error_filter, baseline_start, pr_merged_at)

        # Create monitoring task
        task = MonitoringTask(
            incident_id=incident.id,
            error_filter=error_filter,
            start_time=pr_merged_at,
            end_time=end_time,
            pr_url=pr_url,
            state=MonitoringState.RUNNING,
            baseline_count=baseline_count,
        )

        self._tasks[incident.id] = task

        logger.info(
            f"Started monitoring for {incident.id}: "
            f"baseline={baseline_count}, duration={self.monitoring_duration_hours}h"
        )

        return task

    async def check_status(self, incident_id: str) -> Optional[MonitoringTask]:
        """
        Check the status of a monitoring task.

        Args:
            incident_id: The incident ID to check

        Returns:
            MonitoringTask if found, None otherwise
        """
        return self._tasks.get(incident_id)

    async def collect_sample(self, incident_id: str) -> Optional[int]:
        """
        Collect an error count sample for a monitoring task.

        Args:
            incident_id: The incident ID to collect sample for

        Returns:
            Error count for the current interval, or None if task not found
        """
        task = self._tasks.get(incident_id)
        if not task or task.state != MonitoringState.RUNNING:
            return None

        # Calculate current interval
        now = datetime.utcnow()

        # Determine interval start (last sample or task start)
        if task.error_counts:
            interval_start = task.start_time + timedelta(
                minutes=self.check_interval_minutes * len(task.error_counts)
            )
        else:
            interval_start = task.start_time

        # Count errors in this interval
        try:
            count = await self._count_errors(task.error_filter, interval_start, now)
            task.error_counts.append(count)

            logger.debug(
                f"Collected sample for {incident_id}: "
                f"interval {len(task.error_counts)}, count={count}"
            )

            return count

        except Exception as e:
            logger.error(f"Failed to collect sample for {incident_id}: {e}")
            return None

    async def complete_monitoring(self, incident_id: str) -> Optional[VerificationResult]:
        """
        Complete monitoring and return verification result.

        Args:
            incident_id: The incident ID to complete

        Returns:
            VerificationResult if task found, None otherwise
        """
        task = self._tasks.get(incident_id)
        if not task:
            return None

        # Analyze results
        result = self._analyze_results(task)
        task.result = result
        task.state = MonitoringState.COMPLETED

        logger.info(
            f"Completed monitoring for {incident_id}: "
            f"status={result.status.value}, message={result.message}"
        )

        return result

    async def cancel_monitoring(self, incident_id: str) -> bool:
        """
        Cancel an active monitoring task.

        Args:
            incident_id: The incident ID to cancel

        Returns:
            True if cancelled, False if not found
        """
        task = self._tasks.get(incident_id)
        if not task:
            return False

        task.state = MonitoringState.CANCELLED
        logger.info(f"Cancelled monitoring for {incident_id}")
        return True

    async def verify_fix(
        self,
        incident: Incident,
        pr_merged_at: datetime,
        pr_url: Optional[str] = None,
        callback=None,
    ) -> VerificationResult:
        """
        Run the full verification process.

        Monitors production for the configured duration and returns
        the verification result.

        Args:
            incident: The incident that was fixed
            pr_merged_at: When the fix PR was merged
            pr_url: URL of the merged PR
            callback: Optional async callback for status updates

        Returns:
            VerificationResult with final status
        """
        # Start monitoring
        task = await self.start_monitoring(incident, pr_merged_at, pr_url)

        # Calculate check times
        check_interval = timedelta(minutes=self.check_interval_minutes)
        next_check = datetime.utcnow() + check_interval

        # Monitor until end time
        while datetime.utcnow() < task.end_time:
            # Wait until next check
            wait_seconds = (next_check - datetime.utcnow()).total_seconds()
            if wait_seconds > 0:
                await asyncio.sleep(min(wait_seconds, 60))  # Cap at 60s for responsiveness

            # Check if cancelled
            if task.state == MonitoringState.CANCELLED:
                return VerificationResult(
                    incident_id=incident.id,
                    status=VerificationStatus.FAILED,
                    message="Monitoring was cancelled",
                    errors_before=task.baseline_count,
                    errors_after=sum(task.error_counts),
                    monitoring_duration_hours=self.monitoring_duration_hours,
                    pr_url=pr_url,
                )

            # Collect sample if it's time
            if datetime.utcnow() >= next_check:
                count = await self.collect_sample(incident.id)

                # Call callback if provided
                if callback and count is not None:
                    await callback(incident.id, count, len(task.error_counts))

                next_check = datetime.utcnow() + check_interval

        # Complete monitoring
        return await self.complete_monitoring(incident.id)

    async def get_active_tasks(self) -> List[MonitoringTask]:
        """Get all active monitoring tasks."""
        return [
            task for task in self._tasks.values()
            if task.state == MonitoringState.RUNNING
        ]

    async def check_health(self) -> Dict[str, Any]:
        """
        Check production monitor service health.

        Returns:
            Dict with configuration and status
        """
        configured = bool(self.project_id)

        # Check GCP connectivity
        gcp_connected = False
        if configured:
            try:
                self._get_logging_client()
                gcp_connected = True
            except Exception:
                pass

        return {
            "configured": configured,
            "gcp_project_id": self.project_id if configured else None,
            "gcp_connected": gcp_connected,
            "monitoring_duration_hours": self.monitoring_duration_hours,
            "check_interval_minutes": self.check_interval_minutes,
            "active_tasks": len([t for t in self._tasks.values() if t.state == MonitoringState.RUNNING]),
        }


# Module-level convenience function


async def verify_production_fix(
    incident: Incident,
    pr_merged_at: datetime,
    pr_url: Optional[str] = None,
) -> VerificationResult:
    """
    Verify a fix in production.

    Args:
        incident: The incident that was fixed
        pr_merged_at: When the fix PR was merged
        pr_url: URL of the merged PR

    Returns:
        VerificationResult with status
    """
    service = ProductionMonitorService()
    return await service.verify_fix(incident, pr_merged_at, pr_url)
