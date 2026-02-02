"""
Infrastructure Health Checker Module.

Runs GCP monitoring queries to check infrastructure health
during triage for better classification.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Infrastructure health status levels."""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class InfraCheck:
    """Result of an infrastructure health check."""
    component: str
    status: HealthStatus
    value: Optional[float] = None
    threshold_warning: Optional[float] = None
    threshold_critical: Optional[float] = None
    message: str = ""
    recommendation: Optional[str] = None


@dataclass
class InfraHealthReport:
    """Complete infrastructure health report."""
    timestamp: datetime
    checks: List[InfraCheck]
    overall_status: HealthStatus
    is_infrastructure_issue: bool
    recommendations: List[str]
    cross_tenant_affected: bool = False
    affected_tenant_count: int = 0


class InfrastructureChecker:
    """
    Checks GCP infrastructure health for better triage decisions.

    Monitors:
    - AlloyDB connection count
    - AlloyDB wait time (lock contention)
    - AlloyDB wait count (active lock contention)
    - Pub/Sub backlog age
    - Cross-tenant error correlation
    """

    # Thresholds based on oncall runbooks
    ALLOYDB_CONNECTIONS_WARNING = 70
    ALLOYDB_CONNECTIONS_CRITICAL = 90
    ALLOYDB_WAIT_TIME_WARNING = 2000  # 2s in ms
    ALLOYDB_WAIT_TIME_CRITICAL = 5000  # 5s in ms
    ALLOYDB_WAIT_COUNT_WARNING = 500
    ALLOYDB_WAIT_COUNT_CRITICAL = 2000
    PUBSUB_BACKLOG_WARNING = 300  # 5 minutes
    PUBSUB_BACKLOG_CRITICAL = 900  # 15 minutes

    def __init__(self, project_id: str = "nucleus-449303"):
        """
        Initialize the infrastructure checker.

        Args:
            project_id: GCP project ID to monitor
        """
        self.project_id = project_id
        self._monitoring_client = None
        self._logging_client = None

    def _get_monitoring_client(self):
        """Get or create the GCP Monitoring client."""
        if self._monitoring_client is None:
            try:
                from google.cloud import monitoring_v3
                self._monitoring_client = monitoring_v3.MetricServiceClient()
            except Exception as e:
                logger.warning(f"Failed to initialize Monitoring client: {e}")
                return None
        return self._monitoring_client

    def _get_logging_client(self):
        """Get or create the GCP Logging client."""
        if self._logging_client is None:
            try:
                from google.cloud import logging as cloud_logging
                self._logging_client = cloud_logging.Client(project=self.project_id)
            except Exception as e:
                logger.warning(f"Failed to initialize Logging client: {e}")
                return None
        return self._logging_client

    async def check_alloydb_connections(self) -> InfraCheck:
        """Check AlloyDB connection count."""
        client = self._get_monitoring_client()
        if not client:
            return InfraCheck(
                component="AlloyDB Connections",
                status=HealthStatus.UNKNOWN,
                message="Monitoring client not available"
            )

        try:
            from google.cloud.monitoring_v3 import ListTimeSeriesRequest
            from google.protobuf.timestamp_pb2 import Timestamp

            now = datetime.utcnow()
            start_time = now - timedelta(minutes=5)

            # Build the request
            interval = {
                "start_time": {"seconds": int(start_time.timestamp())},
                "end_time": {"seconds": int(now.timestamp())},
            }

            request = ListTimeSeriesRequest(
                name=f"projects/{self.project_id}",
                filter='metric.type="alloydb.googleapis.com/database/postgresql/backends_for_top_databases"',
                interval=interval,
            )

            # Execute in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: list(client.list_time_series(request=request))
            )

            if results and results[0].points:
                value = results[0].points[0].value.int64_value
                status = HealthStatus.HEALTHY
                if value >= self.ALLOYDB_CONNECTIONS_CRITICAL:
                    status = HealthStatus.CRITICAL
                elif value >= self.ALLOYDB_CONNECTIONS_WARNING:
                    status = HealthStatus.WARNING

                return InfraCheck(
                    component="AlloyDB Connections",
                    status=status,
                    value=value,
                    threshold_warning=self.ALLOYDB_CONNECTIONS_WARNING,
                    threshold_critical=self.ALLOYDB_CONNECTIONS_CRITICAL,
                    message=f"Current connections: {value}",
                    recommendation="Check for connection leaks" if status != HealthStatus.HEALTHY else None
                )

            return InfraCheck(
                component="AlloyDB Connections",
                status=HealthStatus.UNKNOWN,
                message="No data available"
            )

        except Exception as e:
            logger.warning(f"Failed to check AlloyDB connections: {e}")
            return InfraCheck(
                component="AlloyDB Connections",
                status=HealthStatus.UNKNOWN,
                message=f"Check failed: {str(e)}"
            )

    async def check_alloydb_wait_count(self) -> InfraCheck:
        """
        Check AlloyDB wait count for lock contention.

        This is the PREFERRED metric for detecting lock contention
        as it shows active waiting queries.
        """
        client = self._get_monitoring_client()
        if not client:
            return InfraCheck(
                component="AlloyDB Wait Count (Lock)",
                status=HealthStatus.UNKNOWN,
                message="Monitoring client not available"
            )

        try:
            from google.cloud.monitoring_v3 import ListTimeSeriesRequest

            now = datetime.utcnow()
            start_time = now - timedelta(minutes=10)

            interval = {
                "start_time": {"seconds": int(start_time.timestamp())},
                "end_time": {"seconds": int(now.timestamp())},
            }

            request = ListTimeSeriesRequest(
                name=f"projects/{self.project_id}",
                filter='metric.type="alloydb.googleapis.com/instance/postgresql/wait_count" AND metric.labels.wait_type="Lock"',
                interval=interval,
            )

            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: list(client.list_time_series(request=request))
            )

            if results and results[0].points:
                value = results[0].points[0].value.int64_value
                status = HealthStatus.HEALTHY
                recommendation = None

                if value >= self.ALLOYDB_WAIT_COUNT_CRITICAL:
                    status = HealthStatus.CRITICAL
                    recommendation = "CRITICAL: Active lock contention detected. Consider pausing entity processing. See runbooks/alloydb.md Scenario 7."
                elif value >= self.ALLOYDB_WAIT_COUNT_WARNING:
                    status = HealthStatus.WARNING
                    recommendation = "WARNING: Elevated lock contention. Monitor closely and identify blocking queries."

                return InfraCheck(
                    component="AlloyDB Wait Count (Lock)",
                    status=status,
                    value=value,
                    threshold_warning=self.ALLOYDB_WAIT_COUNT_WARNING,
                    threshold_critical=self.ALLOYDB_WAIT_COUNT_CRITICAL,
                    message=f"Current wait count: {value}",
                    recommendation=recommendation
                )

            return InfraCheck(
                component="AlloyDB Wait Count (Lock)",
                status=HealthStatus.HEALTHY,
                value=0,
                message="No lock contention detected"
            )

        except Exception as e:
            logger.warning(f"Failed to check AlloyDB wait count: {e}")
            return InfraCheck(
                component="AlloyDB Wait Count (Lock)",
                status=HealthStatus.UNKNOWN,
                message=f"Check failed: {str(e)}"
            )

    async def check_pubsub_backlog(self, subscription_pattern: Optional[str] = None) -> InfraCheck:
        """
        Check Pub/Sub backlog age.

        Args:
            subscription_pattern: Optional subscription name pattern to check
        """
        client = self._get_monitoring_client()
        if not client:
            return InfraCheck(
                component="Pub/Sub Backlog",
                status=HealthStatus.UNKNOWN,
                message="Monitoring client not available"
            )

        try:
            from google.cloud.monitoring_v3 import ListTimeSeriesRequest

            now = datetime.utcnow()
            start_time = now - timedelta(minutes=10)

            interval = {
                "start_time": {"seconds": int(start_time.timestamp())},
                "end_time": {"seconds": int(now.timestamp())},
            }

            filter_str = 'metric.type="pubsub.googleapis.com/subscription/oldest_unacked_message_age"'
            if subscription_pattern:
                filter_str += f' AND resource.labels.subscription_id=~"{subscription_pattern}"'

            request = ListTimeSeriesRequest(
                name=f"projects/{self.project_id}",
                filter=filter_str,
                interval=interval,
            )

            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: list(client.list_time_series(request=request))
            )

            # Find the maximum backlog across all subscriptions
            max_backlog = 0
            worst_subscription = None

            for ts in results:
                if ts.points:
                    value = ts.points[0].value.int64_value
                    if value > max_backlog:
                        max_backlog = value
                        worst_subscription = ts.resource.labels.get("subscription_id", "unknown")

            status = HealthStatus.HEALTHY
            recommendation = None

            if max_backlog >= self.PUBSUB_BACKLOG_CRITICAL:
                status = HealthStatus.CRITICAL
                recommendation = f"CRITICAL: Large backlog on {worst_subscription}. Check consumer health, memory, and CPU."
            elif max_backlog >= self.PUBSUB_BACKLOG_WARNING:
                status = HealthStatus.WARNING
                recommendation = f"WARNING: Growing backlog on {worst_subscription}. Monitor consumer performance."

            return InfraCheck(
                component="Pub/Sub Backlog",
                status=status,
                value=max_backlog,
                threshold_warning=self.PUBSUB_BACKLOG_WARNING,
                threshold_critical=self.PUBSUB_BACKLOG_CRITICAL,
                message=f"Max backlog age: {max_backlog}s" + (f" ({worst_subscription})" if worst_subscription else ""),
                recommendation=recommendation
            )

        except Exception as e:
            logger.warning(f"Failed to check Pub/Sub backlog: {e}")
            return InfraCheck(
                component="Pub/Sub Backlog",
                status=HealthStatus.UNKNOWN,
                message=f"Check failed: {str(e)}"
            )

    async def check_cross_tenant_errors(
        self,
        error_message: str,
        time_window_minutes: int = 5
    ) -> tuple[bool, int, List[str]]:
        """
        Check if the same error is affecting multiple tenants.

        Multiple tenants with same error = infrastructure issue, not tenant-specific.

        Args:
            error_message: The error message to search for
            time_window_minutes: Time window to search

        Returns:
            Tuple of (is_cross_tenant, tenant_count, tenant_ids)
        """
        client = self._get_logging_client()
        if not client:
            return (False, 0, [])

        try:
            # Extract key phrase from error for searching
            # Take first 100 chars, remove timestamps and IDs
            import re
            search_phrase = error_message[:100]
            # Remove common variable parts
            search_phrase = re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '', search_phrase)
            search_phrase = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '', search_phrase)
            search_phrase = search_phrase.strip()[:50]  # Use first 50 chars after cleanup

            if len(search_phrase) < 10:
                return (False, 0, [])

            now = datetime.utcnow()
            start_time = now - timedelta(minutes=time_window_minutes)

            # Query for this error pattern
            filter_str = (
                f'resource.type="cloud_run_revision" '
                f'severity>=ERROR '
                f'timestamp>="{start_time.isoformat()}Z" '
                f'textPayload:"{search_phrase}"'
            )

            loop = asyncio.get_event_loop()
            entries = await loop.run_in_executor(
                None,
                lambda: list(client.list_entries(
                    filter_=filter_str,
                    max_results=50,
                ))
            )

            # Extract unique tenant IDs
            tenant_ids = set()
            for entry in entries:
                payload = entry.payload
                if isinstance(payload, dict):
                    tenant_id = payload.get("tenant_id") or payload.get("tenantId")
                    if tenant_id:
                        tenant_ids.add(tenant_id)

            is_cross_tenant = len(tenant_ids) > 1
            return (is_cross_tenant, len(tenant_ids), list(tenant_ids))

        except Exception as e:
            logger.warning(f"Failed to check cross-tenant errors: {e}")
            return (False, 0, [])

    async def run_health_checks(
        self,
        error_message: Optional[str] = None,
        include_cross_tenant: bool = True
    ) -> InfraHealthReport:
        """
        Run all infrastructure health checks.

        Args:
            error_message: Optional error message for cross-tenant check
            include_cross_tenant: Whether to check for cross-tenant errors

        Returns:
            Complete health report
        """
        checks = []
        recommendations = []

        # Run checks in parallel
        tasks = [
            self.check_alloydb_connections(),
            self.check_alloydb_wait_count(),
            self.check_pubsub_backlog(),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Health check failed: {result}")
                continue
            checks.append(result)
            if result.recommendation:
                recommendations.append(result.recommendation)

        # Check cross-tenant if error message provided
        cross_tenant = False
        tenant_count = 0
        if include_cross_tenant and error_message:
            cross_tenant, tenant_count, _ = await self.check_cross_tenant_errors(error_message)
            if cross_tenant:
                recommendations.append(
                    f"IMPORTANT: Same error affecting {tenant_count} tenants - indicates infrastructure issue, not tenant-specific."
                )

        # Determine overall status
        statuses = [c.status for c in checks]
        if HealthStatus.CRITICAL in statuses:
            overall_status = HealthStatus.CRITICAL
        elif HealthStatus.WARNING in statuses:
            overall_status = HealthStatus.WARNING
        elif all(s == HealthStatus.HEALTHY for s in statuses):
            overall_status = HealthStatus.HEALTHY
        else:
            overall_status = HealthStatus.UNKNOWN

        # Determine if this is an infrastructure issue
        is_infra_issue = (
            overall_status in (HealthStatus.CRITICAL, HealthStatus.WARNING)
            or cross_tenant
        )

        return InfraHealthReport(
            timestamp=datetime.utcnow(),
            checks=checks,
            overall_status=overall_status,
            is_infrastructure_issue=is_infra_issue,
            recommendations=recommendations,
            cross_tenant_affected=cross_tenant,
            affected_tenant_count=tenant_count,
        )


# Global instance
_checker: Optional[InfrastructureChecker] = None


def get_infrastructure_checker(project_id: str = "nucleus-449303") -> InfrastructureChecker:
    """Get the global infrastructure checker instance."""
    global _checker
    if _checker is None:
        _checker = InfrastructureChecker(project_id)
    return _checker


async def run_quick_health_check(
    error_message: Optional[str] = None
) -> InfraHealthReport:
    """
    Convenience function to run a quick health check.

    Args:
        error_message: Optional error message for context

    Returns:
        Health report
    """
    checker = get_infrastructure_checker()
    return await checker.run_health_checks(error_message)
