"""
Triage Agent for On Call Helper.

Uses Claude AI with embedded SRE knowledge to analyze production incidents
and classify them for appropriate action.

Enhanced with:
- GCP log context fetching
- Error pattern recognition (47 known patterns)
- Tenant classification (production vs demo)
- Infrastructure health checks
- Runbook suggestions
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from anthropic import APIError, APIConnectionError, RateLimitError
from pydantic import ValidationError

from backend.ai_client import create_ai_client, get_triage_model, get_backend_name, AIClient
from backend.config import settings
from backend.knowledge import (
    get_triage_system_prompt,
    load_sre_knowledge,
    # Error patterns
    get_pattern_classification,
    match_error_pattern,
    PatternSeverity,
    # Tenant classification
    is_demo_tenant,
    is_production_tenant,
    get_tenant_priority,
    classify_tenant_by_name,
    TenantType,
    # Infrastructure checks
    run_quick_health_check,
    HealthStatus,
    # Runbooks
    suggest_runbook,
    get_investigation_steps,
    get_diagnostic_commands,
    # Pattern learning
    get_pattern_learner,
)
from backend.models import (
    Incident,
    TriageResult,
    TriageClassification,
)

logger = logging.getLogger(__name__)


class TriageError(Exception):
    """Error during triage analysis."""
    pass


class TriageAgent:
    """
    Claude-powered triage agent with embedded SRE knowledge.

    Analyzes production incidents and classifies them as:
    - FIXABLE: Code bug that can be auto-fixed
    - INFRA_ISSUE: Infrastructure problem requiring runbook
    - TRANSIENT: Self-healing error
    - NEEDS_HUMAN: Too complex for automated handling
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """
        Initialize the triage agent.

        Args:
            api_key: Anthropic API key override (ignored for Vertex AI)
            model: Model override (defaults to backend-specific model)
        """
        self.model = model  # Allow override, otherwise use backend-specific default
        self.client: Optional[AIClient] = None
        self._system_prompt: Optional[str] = None
        self._gcp_client = None
        self._pattern_learner = None  # Lazy initialized

        # Create client using factory (handles Anthropic vs Vertex AI)
        self.client = create_ai_client(api_key=api_key)

    @property
    def pattern_learner(self):
        """Get the pattern learner (lazy initialization)."""
        if self._pattern_learner is None:
            self._pattern_learner = get_pattern_learner()
        return self._pattern_learner

    async def _pre_analyze(self, incident: Incident) -> Dict[str, Any]:
        """
        Perform pre-analysis using pattern matching and tenant classification.

        This runs BEFORE calling Claude to:
        1. Quickly classify known error patterns
        2. Check tenant type (production vs demo)
        3. Run infrastructure health checks
        4. Suggest relevant runbooks

        Returns:
            Dict with pre-analysis results that can influence final triage
        """
        pre_analysis = {
            "pattern_match": None,
            "pattern_classification": None,
            "pattern_confidence_boost": 0.0,
            "pattern_reason": None,
            "pattern_action": None,
            "tenant_type": TenantType.UNKNOWN,
            "tenant_priority": 2,
            "is_demo_tenant": False,
            "infra_health": None,
            "is_infra_issue": False,
            "runbook_suggestion": None,
            "manual_steps": [],
        }

        # 1. Pattern matching for known errors
        logger.info(f"Running pattern matching for {incident.id}...")
        pattern_result = match_error_pattern(incident.error_message)
        if pattern_result:
            pattern, match_text = pattern_result
            pre_analysis["pattern_match"] = match_text
            pre_analysis["pattern_reason"] = pattern.reason

            classification, confidence_boost, reason, action = get_pattern_classification(incident.error_message)
            pre_analysis["pattern_classification"] = classification
            pre_analysis["pattern_confidence_boost"] = confidence_boost
            if action:
                pre_analysis["pattern_action"] = action

            logger.info(
                f"Pattern matched for {incident.id}: {match_text[:50]}... "
                f"-> {classification} (+{confidence_boost:.1f} confidence)"
            )

        # 2. Tenant classification
        tenant_type = classify_tenant_by_name(incident.tenant_name) if incident.tenant_name else TenantType.UNKNOWN
        pre_analysis["tenant_type"] = tenant_type
        pre_analysis["tenant_priority"] = get_tenant_priority(tenant_name=incident.tenant_name)
        pre_analysis["is_demo_tenant"] = is_demo_tenant(tenant_name=incident.tenant_name)

        if pre_analysis["is_demo_tenant"]:
            logger.info(f"Incident {incident.id} is from demo tenant: {incident.tenant_name}")

        # 3. Infrastructure health checks (async)
        try:
            logger.info(f"Running infrastructure health checks for {incident.id}...")
            health_report = await run_quick_health_check(incident.error_message)
            pre_analysis["infra_health"] = {
                "overall_status": health_report.overall_status.value,
                "is_infrastructure_issue": health_report.is_infrastructure_issue,
                "cross_tenant_affected": health_report.cross_tenant_affected,
                "affected_tenant_count": health_report.affected_tenant_count,
                "checks": [
                    {
                        "component": c.component,
                        "status": c.status.value,
                        "value": c.value,
                        "message": c.message,
                    }
                    for c in health_report.checks
                ],
                "recommendations": health_report.recommendations,
            }
            pre_analysis["is_infra_issue"] = health_report.is_infrastructure_issue

            if health_report.is_infrastructure_issue:
                logger.warning(
                    f"Infrastructure issue detected for {incident.id}: "
                    f"status={health_report.overall_status.value}, "
                    f"cross_tenant={health_report.cross_tenant_affected}"
                )

        except Exception as e:
            logger.warning(f"Infrastructure health check failed for {incident.id}: {e}")

        # 4. Runbook suggestion
        runbook = suggest_runbook(incident.error_message, incident.service_name)
        if runbook:
            pre_analysis["runbook_suggestion"] = {
                "path": runbook.runbook_path,
                "name": runbook.runbook_name,
                "relevance": runbook.relevance_score,
                "reason": runbook.reason,
                "section": runbook.specific_section,
            }
            logger.info(f"Suggested runbook for {incident.id}: {runbook.runbook_name}")

        # 5. Get manual investigation steps and diagnostic commands
        classification_hint = pre_analysis.get("pattern_classification", "NEEDS_HUMAN")
        pre_analysis["manual_steps"] = get_investigation_steps(
            incident.error_message,
            classification_hint
        )
        pre_analysis["gcloud_commands"] = get_diagnostic_commands(
            incident.error_message,
            classification_hint
        )

        # 6. Historical pattern lookup (Pattern Learning)
        try:
            pattern_suggestion = self.pattern_learner.get_pattern_suggestion(
                error_msg=incident.error_message,
                service=incident.service_name
            )
            if pattern_suggestion and pattern_suggestion.occurrence_count >= 3:
                pre_analysis["pattern_suggestion"] = pattern_suggestion.model_dump()
                logger.info(
                    f"Historical pattern found for {incident.id}: {pattern_suggestion.classification} "
                    f"({pattern_suggestion.occurrence_count} occurrences, {pattern_suggestion.success_rate:.0%} success)"
                )
        except Exception as e:
            logger.warning(f"Pattern lookup failed for {incident.id}: {e}")

        return pre_analysis

    def _get_gcp_client(self):
        """Get or create GCP Logging client for context fetching."""
        if self._gcp_client is None:
            try:
                from google.cloud import logging as cloud_logging
                self._gcp_client = cloud_logging.Client(project=settings.gcp_project_id)
            except Exception as e:
                logger.warning(f"Failed to initialize GCP client: {e}")
                return None
        return self._gcp_client

    async def _fetch_gcp_context(self, incident: Incident) -> Dict[str, Any]:
        """
        Fetch additional context from GCP logs for better triage.

        Queries for:
        1. Related errors from same service in past hour
        2. Error frequency/pattern
        3. Similar errors across other services
        4. Recent logs around the same timestamp
        """
        context = {
            "related_errors": [],
            "error_frequency": None,
            "similar_across_services": [],
            "recent_service_logs": [],
            "queries_used": [],  # Track GCP queries for debugging/display
        }

        client = self._get_gcp_client()
        if not client:
            logger.info("GCP client not available, skipping context fetch")
            return context

        try:
            # Calculate time window
            error_time = incident.created_at or datetime.utcnow()
            start_time = error_time - timedelta(hours=1)
            end_time = error_time + timedelta(minutes=5)

            # Format timestamps for GCP Cloud Logging (RFC3339 format)
            # Ensure UTC timezone and format correctly
            from datetime import timezone
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            
            # Format as RFC3339 - use isoformat() and replace +00:00 with Z
            start_str = start_time.isoformat().replace('+00:00', 'Z')
            end_str = end_time.isoformat().replace('+00:00', 'Z')
            
            time_filter = f'timestamp>="{start_str}" AND timestamp<="{end_str}"'

            # 1. Get related errors from same service
            if incident.service_name and incident.service_name != "unknown":
                service_filter = f'severity>=ERROR AND {time_filter} AND resource.labels.service_name="{incident.service_name}"'
                context["queries_used"].append(f"Related errors: {service_filter}")

                def fetch_service_errors():
                    return list(client.list_entries(
                        filter_=service_filter,
                        order_by="timestamp desc",
                        max_results=20,
                    ))

                entries = await asyncio.get_event_loop().run_in_executor(None, fetch_service_errors)

                # Summarize related errors
                error_counts = {}
                for entry in entries:
                    msg = self._extract_error_summary(entry)
                    error_counts[msg] = error_counts.get(msg, 0) + 1

                context["related_errors"] = [
                    {"message": msg, "count": count}
                    for msg, count in sorted(error_counts.items(), key=lambda x: -x[1])[:5]
                ]
                context["error_frequency"] = {
                    "total_errors_past_hour": len(entries),
                    "unique_error_types": len(error_counts),
                }

            # 2. Check for similar errors across all services
            if incident.error_message:
                # Extract key error phrase for searching
                error_phrase = self._extract_error_phrase(incident.error_message)
                if error_phrase:
                    cross_filter = f'severity>=ERROR AND {time_filter} AND textPayload:"{error_phrase}"'
                    context["queries_used"].append(f"Cross-service: {cross_filter}")

                    def fetch_cross_service():
                        return list(client.list_entries(
                            filter_=cross_filter,
                            order_by="timestamp desc",
                            max_results=10,
                        ))

                    try:
                        cross_entries = await asyncio.get_event_loop().run_in_executor(None, fetch_cross_service)

                        # Group by service
                        by_service = {}
                        for entry in cross_entries:
                            svc = self._extract_service_from_entry(entry)
                            by_service[svc] = by_service.get(svc, 0) + 1

                        context["similar_across_services"] = [
                            {"service": svc, "count": count}
                            for svc, count in sorted(by_service.items(), key=lambda x: -x[1])
                        ]
                    except Exception as e:
                        logger.debug(f"Cross-service search failed: {e}")

            # 3. Get recent INFO/WARNING logs from the service for context
            if incident.service_name and incident.service_name != "unknown":
                context_filter = f'severity>=INFO AND {time_filter} AND resource.labels.service_name="{incident.service_name}"'
                context["queries_used"].append(f"Context logs: {context_filter}")

                def fetch_context_logs():
                    return list(client.list_entries(
                        filter_=context_filter,
                        order_by="timestamp desc",
                        max_results=10,
                    ))

                try:
                    context_entries = await asyncio.get_event_loop().run_in_executor(None, fetch_context_logs)
                    context["recent_service_logs"] = [
                        {
                            "timestamp": str(e.timestamp),
                            "severity": e.severity,
                            "message": self._extract_error_summary(e)[:200],
                        }
                        for e in context_entries[:5]
                    ]
                except Exception as e:
                    logger.debug(f"Context log fetch failed: {e}")

        except Exception as e:
            logger.warning(f"Error fetching GCP context: {e}")

        return context

    def _extract_error_summary(self, entry) -> str:
        """Extract a short summary from a log entry."""
        if hasattr(entry, 'payload'):
            if isinstance(entry.payload, str):
                return entry.payload[:100]
            elif isinstance(entry.payload, dict):
                msg = entry.payload.get('message') or entry.payload.get('error') or str(entry.payload)
                return msg[:100] if isinstance(msg, str) else str(msg)[:100]
        return "Unknown error"

    def _extract_error_phrase(self, error_message: str) -> Optional[str]:
        """Extract key phrase from error message for searching."""
        # Get first line, strip common prefixes
        first_line = error_message.split('\n')[0].strip()

        # Remove prefixes
        for prefix in ['Error:', 'ERROR:', 'panic:', 'FATAL:', 'error:']:
            if first_line.startswith(prefix):
                first_line = first_line[len(prefix):].strip()

        # Take first 50 chars, avoid partial words
        if len(first_line) > 50:
            first_line = first_line[:50].rsplit(' ', 1)[0]

        return first_line if len(first_line) > 10 else None

    def _extract_service_from_entry(self, entry) -> str:
        """Extract service name from a log entry."""
        if hasattr(entry, 'resource') and entry.resource:
            labels = entry.resource.labels or {}
            return labels.get('service_name', 'unknown')
        return 'unknown'

    @property
    def system_prompt(self) -> str:
        """Get the system prompt with embedded SRE knowledge."""
        if self._system_prompt is None:
            self._system_prompt = get_triage_system_prompt()
        return self._system_prompt

    def _format_incident(
        self,
        incident: Incident,
        gcp_context: Optional[Dict[str, Any]] = None,
        pre_analysis: Optional[Dict[str, Any]] = None
    ) -> str:
        """Format an incident for Claude analysis, including GCP context and pre-analysis."""
        parts = [
            f"## Incident: {incident.id}",
            f"**Title**: {incident.title}",
            f"**Service**: {incident.service_name}",
            f"**Severity**: {incident.severity.value}",
            f"**Environment**: {incident.environment}",
            "",
            "### Error Message",
            f"```",
            incident.error_message,
            "```",
        ]

        # Add pre-analysis hints
        if pre_analysis:
            parts.extend(["", "### Pre-Analysis (Pattern Matching & Health Checks)"])

            # Pattern match info
            if pre_analysis.get("pattern_match"):
                parts.extend([
                    "",
                    f"**Known Pattern Detected**: `{pre_analysis['pattern_match']}`",
                    f"- Suggested Classification: **{pre_analysis.get('pattern_classification', 'UNKNOWN')}**",
                    f"- Reason: {pre_analysis.get('pattern_reason', 'N/A')}",
                ])
                if pre_analysis.get("pattern_action"):
                    parts.append(f"- Recommended Action: {pre_analysis['pattern_action']}")

            # Tenant info
            tenant_type = pre_analysis.get("tenant_type")
            if tenant_type:
                tenant_label = "DEMO (lower priority)" if pre_analysis.get("is_demo_tenant") else tenant_type.value.upper()
                parts.append(f"")
                parts.append(f"**Tenant Type**: {tenant_label}")

            # Infrastructure health
            if pre_analysis.get("infra_health"):
                health = pre_analysis["infra_health"]
                parts.extend([
                    "",
                    f"**Infrastructure Health**: {health['overall_status'].upper()}",
                ])
                if health.get("is_infrastructure_issue"):
                    parts.append("- **WARNING**: Infrastructure issue detected!")
                if health.get("cross_tenant_affected"):
                    parts.append(f"- Cross-tenant impact: {health['affected_tenant_count']} tenants affected")
                for check in health.get("checks", []):
                    if check["status"] != "healthy":
                        parts.append(f"- {check['component']}: {check['status'].upper()} - {check['message']}")
                for rec in health.get("recommendations", []):
                    parts.append(f"- {rec}")

            # Runbook suggestion
            if pre_analysis.get("runbook_suggestion"):
                rb = pre_analysis["runbook_suggestion"]
                parts.extend([
                    "",
                    f"**Suggested Runbook**: {rb['name']} ({rb['path']})",
                ])
                if rb.get("section"):
                    parts.append(f"- Specific Section: {rb['section']}")

            # Historical pattern learning
            if pre_analysis.get("pattern_suggestion"):
                ps = pre_analysis["pattern_suggestion"]
                parts.extend([
                    "",
                    f"**Historical Pattern Match**:",
                    f"- Found {ps['occurrence_count']} similar incidents in history",
                    f"- Most common classification: **{ps['classification'].upper()}**",
                    f"- Historical success rate: {ps['success_rate']:.0%}",
                ])
                if ps.get("suggested_fix"):
                    parts.append(f"- Previous fix approach: {ps['suggested_fix'].get('fix_explanation', 'N/A')}")

            parts.extend([
                "",
                "**IMPORTANT**: Use the pre-analysis above to inform your classification.",
                "If a known pattern was matched, weight that heavily in your decision.",
                "If historical patterns show consistent classification, consider that strongly.",
                "If infrastructure issues were detected, consider INFRA_ISSUE classification.",
            ])

        if incident.stack_trace:
            parts.extend([
                "",
                "### Stack Trace",
                "```",
                incident.stack_trace,
                "```",
            ])

        if incident.file_path:
            parts.extend([
                "",
                f"**File Path**: {incident.file_path}",
            ])

        if incident.tenant_name:
            parts.extend([
                "",
                f"**Tenant**: {incident.tenant_name}",
            ])

        # Add GCP context if available
        if gcp_context:
            parts.extend(["", "### Additional Context from GCP Logs"])

            # Error frequency
            if gcp_context.get("error_frequency"):
                freq = gcp_context["error_frequency"]
                parts.extend([
                    "",
                    f"**Error Frequency (past hour)**: {freq.get('total_errors_past_hour', 0)} total errors, {freq.get('unique_error_types', 0)} unique types",
                ])

            # Related errors from same service
            if gcp_context.get("related_errors"):
                parts.extend(["", "**Related Errors in Same Service**:"])
                for err in gcp_context["related_errors"][:5]:
                    parts.append(f"- ({err['count']}x) {err['message']}")

            # Similar errors across services
            if gcp_context.get("similar_across_services"):
                parts.extend(["", "**Same Error Across Services**:"])
                for svc in gcp_context["similar_across_services"]:
                    parts.append(f"- {svc['service']}: {svc['count']} occurrences")

            # Recent logs for context
            if gcp_context.get("recent_service_logs"):
                parts.extend(["", "**Recent Service Activity**:"])
                for log in gcp_context["recent_service_logs"][:3]:
                    parts.append(f"- [{log['severity']}] {log['message']}")

        parts.extend([
            "",
            "---",
            "",
            "## Instructions",
            "",
            "Based on the error details AND the additional GCP context above, provide your analysis.",
            "The GCP context shows you:",
            "- How frequently this error is occurring",
            "- Whether it's happening across multiple services (widespread issue)",
            "- What other activity is happening in the service around the same time",
            "",
            "Use this context to make a better-informed classification.",
            "If this is a TRANSIENT error, explain what pattern indicates it will self-resolve.",
            "If this is an INFRA_ISSUE, point to specific evidence from the logs.",
            "",
            "Respond with your classification as JSON.",
        ])

        return "\n".join(parts)

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """
        Extract JSON from Claude's response.

        Handles JSON in code blocks or raw JSON.
        """
        # Try to find JSON in code blocks first
        json_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(json_pattern, text)

        if matches:
            for match in matches:
                try:
                    return json.loads(match.strip())
                except json.JSONDecodeError:
                    continue

        # Try to find raw JSON object
        brace_pattern = r"\{[\s\S]*\}"
        matches = re.findall(brace_pattern, text)

        if matches:
            # Try each match, starting with the largest (most complete)
            for match in sorted(matches, key=len, reverse=True):
                try:
                    return json.loads(match)
                except json.JSONDecodeError:
                    continue

        raise TriageError(f"No valid JSON found in response: {text[:500]}")

    def _parse_classification(self, value: str) -> TriageClassification:
        """Parse classification string to enum."""
        value_upper = value.upper().replace("-", "_")

        mapping = {
            "FIXABLE": TriageClassification.FIXABLE,
            "INFRA_ISSUE": TriageClassification.INFRA_ISSUE,
            "INFRA": TriageClassification.INFRA_ISSUE,
            "INFRASTRUCTURE": TriageClassification.INFRA_ISSUE,
            "TRANSIENT": TriageClassification.TRANSIENT,
            "NEEDS_HUMAN": TriageClassification.NEEDS_HUMAN,
            "HUMAN": TriageClassification.NEEDS_HUMAN,
        }

        if value_upper in mapping:
            return mapping[value_upper]

        raise TriageError(f"Unknown classification: {value}")

    def _parse_line_numbers(self, data: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        """Parse line numbers from response data."""
        if "line_numbers" in data and data["line_numbers"]:
            ln = data["line_numbers"]
            if isinstance(ln, (list, tuple)) and len(ln) >= 2:
                return (int(ln[0]), int(ln[1]))
            elif isinstance(ln, dict):
                start = ln.get("start") or ln.get("begin") or ln.get("from")
                end = ln.get("end") or ln.get("to")
                if start and end:
                    return (int(start), int(end))
        return None

    def _parse_response(self, response_text: str, incident_id: str) -> TriageResult:
        """
        Parse Claude's response into a TriageResult.

        Args:
            response_text: Raw response from Claude
            incident_id: ID of the incident being triaged

        Returns:
            TriageResult with classification and analysis
        """
        data = self._extract_json(response_text)

        # Try multiple key names for classification (Claude might use different names)
        classification_value = (
            data.get("classification") or
            data.get("type") or
            data.get("category") or
            data.get("decision") or
            data.get("result_type")
        )

        if not classification_value:
            logger.warning(f"No classification found in response keys: {list(data.keys())}")
            # If we have enough info for a fix, classify as FIXABLE instead of NEEDS_HUMAN
            if data.get("file_path") and data.get("root_cause") and data.get("suggested_fix"):
                logger.info("Upgrading to FIXABLE: sufficient context available (file_path, root_cause, suggested_fix)")
                classification = TriageClassification.FIXABLE
            else:
                classification = TriageClassification.NEEDS_HUMAN
        else:
            try:
                classification = self._parse_classification(classification_value)
            except TriageError as e:
                logger.warning(f"Unknown classification value '{classification_value}': {e}")
                # Check if we have enough info for a fix
                if data.get("file_path") and data.get("root_cause") and data.get("suggested_fix"):
                    classification = TriageClassification.FIXABLE
                else:
                    classification = TriageClassification.NEEDS_HUMAN

        root_cause = data.get("root_cause", "Unable to determine root cause")
        confidence = float(data.get("confidence", 0.5))

        # Clamp confidence to valid range
        confidence = max(0.0, min(1.0, confidence))

        # Build result
        result = TriageResult(
            incident_id=incident_id,
            classification=classification,
            root_cause=root_cause,
            confidence=confidence,
            # Optional FIXABLE fields
            service_name=data.get("service_name"),
            file_path=data.get("file_path"),
            function_name=data.get("function_name"),
            code_snippet=data.get("code_snippet"),
            line_numbers=self._parse_line_numbers(data),
            suggested_fix=data.get("suggested_fix"),
            # Optional INFRA_ISSUE fields
            runbook_reference=data.get("runbook_reference"),
            manual_steps=data.get("manual_steps"),
            gcloud_commands=data.get("gcloud_commands"),
            # Additional context
            related_context=data.get("related_context", []),
        )

        return result

    async def analyze(self, incident: Incident, fetch_gcp_context: bool = True) -> TriageResult:
        """
        Analyze an incident and return triage result.

        Enhanced with:
        - Pre-analysis (pattern matching, tenant classification, infra checks)
        - GCP log context fetching
        - Runbook suggestions

        Args:
            incident: The incident to analyze
            fetch_gcp_context: Whether to fetch additional context from GCP logs (default True)

        Returns:
            TriageResult with classification and analysis

        Raises:
            TriageError: If analysis fails
        """
        if not self.client:
            raise TriageError(f"AI client not initialized. Backend: {get_backend_name()}. Check configuration.")

        logger.info(f"Triaging incident {incident.id}: {incident.title}")

        # 1. Run pre-analysis (pattern matching, tenant classification, infra checks)
        pre_analysis = None
        try:
            pre_analysis = await self._pre_analyze(incident)
        except Exception as e:
            logger.warning(f"Pre-analysis failed for {incident.id}: {e}")
            # Continue without pre-analysis

        # 2. Fetch GCP context for better triage
        gcp_context = None
        if fetch_gcp_context:
            try:
                logger.info(f"Fetching GCP context for {incident.id}...")
                gcp_context = await self._fetch_gcp_context(incident)
                if gcp_context.get("error_frequency"):
                    freq = gcp_context["error_frequency"]
                    logger.info(
                        f"GCP context for {incident.id}: {freq.get('total_errors_past_hour', 0)} errors in past hour, "
                        f"{len(gcp_context.get('similar_across_services', []))} other services affected"
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch GCP context for {incident.id}: {e}")
                # Continue without context - don't block triage

        # 3. Call Claude for analysis (run in thread pool to avoid blocking event loop)
        try:
            message = await asyncio.to_thread(
                self.client.messages.create,
                model=self.model or get_triage_model(),
                max_tokens=4096,
                system=self.system_prompt,
                messages=[
                    {"role": "user", "content": self._format_incident(incident, gcp_context, pre_analysis)}
                ]
            )

            response_text = message.content[0].text
            logger.debug(f"Claude response for {incident.id}: {response_text[:500]}")

            result = self._parse_response(response_text, incident.id)

            # 4. Apply pre-analysis enhancements to result
            if pre_analysis:
                # Apply confidence boost from pattern matching
                if pre_analysis.get("pattern_confidence_boost"):
                    original_confidence = result.confidence
                    result.confidence = min(1.0, result.confidence + pre_analysis["pattern_confidence_boost"])
                    logger.info(
                        f"Applied pattern confidence boost for {incident.id}: "
                        f"{original_confidence:.2f} -> {result.confidence:.2f}"
                    )

                # Apply historical pattern learning confidence boost
                if pre_analysis.get("pattern_suggestion"):
                    ps = pre_analysis["pattern_suggestion"]
                    if ps["classification"].lower() == result.classification.value:
                        # Pattern agrees with Claude - boost confidence
                        boost = 0.15 if ps["success_rate"] >= 0.7 else 0.10
                        original_confidence = result.confidence
                        result.confidence = min(0.95, result.confidence + boost)
                        logger.info(
                            f"Historical pattern confidence boost for {incident.id}: "
                            f"+{boost:.2f} ({original_confidence:.2f} -> {result.confidence:.2f})"
                        )

                    # Check if we should override Claude's classification
                    should_override = (
                        settings.pattern_learning_enabled and
                        ps["occurrence_count"] >= settings.pattern_min_occurrences and
                        ps["success_rate"] >= settings.pattern_override_success_rate and
                        ps["confidence"] >= settings.pattern_override_confidence and
                        ps["classification"].lower() != result.classification.value
                    )

                    if should_override:
                        original = result.classification.value
                        result.classification = TriageClassification(ps["classification"].lower())
                        result.confidence = min(0.95, ps["success_rate"])
                        result.override_reason = (
                            f"Overridden from {original.upper()} based on {ps['occurrence_count']} "
                            f"similar historical incidents ({ps['success_rate']:.0%} success rate)"
                        )
                        logger.warning(
                            f"Pattern override for {incident.id}: {original} -> {ps['classification']} "
                            f"({ps['occurrence_count']} occurrences, {ps['success_rate']:.0%} success)"
                        )

                    # Store pattern suggestion in result
                    result.pattern_suggestion = ps

                # Override classification if infra issue detected and Claude didn't catch it
                if pre_analysis.get("is_infra_issue") and result.classification != TriageClassification.INFRA_ISSUE:
                    logger.warning(
                        f"Overriding classification for {incident.id}: "
                        f"{result.classification.value} -> INFRA_ISSUE (infrastructure issue detected)"
                    )
                    result.classification = TriageClassification.INFRA_ISSUE

                # Add runbook reference if not already present
                if pre_analysis.get("runbook_suggestion") and not result.runbook_reference:
                    rb = pre_analysis["runbook_suggestion"]
                    result.runbook_reference = f"{rb['name']} ({rb['path']})"
                    if rb.get("section"):
                        result.runbook_reference += f" - {rb['section']}"

                # Add manual steps if not already present
                if pre_analysis.get("manual_steps") and not result.manual_steps:
                    result.manual_steps = pre_analysis["manual_steps"]

                # Add gcloud commands if not already present
                if pre_analysis.get("gcloud_commands") and not result.gcloud_commands:
                    result.gcloud_commands = pre_analysis["gcloud_commands"]

                # Store pre-analysis for display
                result.pre_analysis = pre_analysis

                # Add tenant info for display
                if pre_analysis.get("is_demo_tenant"):
                    result.tenant_type = "demo"
                    # Optionally downgrade priority for demo tenants
                    logger.info(f"Incident {incident.id} is from demo tenant - lower priority")

            # 5. Add GCP context to the result for display
            if gcp_context:
                result.gcp_context = gcp_context
                # Also save queries used for transparency
                if gcp_context.get("queries_used"):
                    result.gcp_queries = gcp_context["queries_used"]

            logger.info(
                f"Triaged {incident.id}: {result.classification.value} "
                f"(confidence: {result.confidence:.2f})"
            )

            return result

        except APIConnectionError as e:
            logger.error(f"API connection error triaging {incident.id}: {e}")
            raise TriageError(f"Failed to connect to {get_backend_name()}: {e}")
        except RateLimitError as e:
            logger.error(f"Rate limited triaging {incident.id}: {e}")
            raise TriageError(f"Rate limited by {get_backend_name()}: {e}")
        except APIError as e:
            logger.error(f"API error triaging {incident.id}: {e}")
            raise TriageError(f"{get_backend_name()} error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error triaging {incident.id}: {e}")
            raise TriageError(f"Triage failed: {e}")

    def analyze_sync(self, incident: Incident) -> TriageResult:
        """
        Synchronous version of analyze for non-async contexts.

        Note: The Anthropic client is synchronous, so this just wraps
        the async method for convenience.
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self.analyze(incident))


# Module-level convenience function
async def triage_incident(incident: Incident) -> TriageResult:
    """
    Triage an incident using the default agent.

    Args:
        incident: The incident to analyze

    Returns:
        TriageResult with classification and analysis
    """
    agent = TriageAgent()
    return await agent.analyze(incident)
