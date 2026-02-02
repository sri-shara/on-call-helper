"""
Triage Agent for On Call Helper.

Uses Claude AI with embedded SRE knowledge to analyze production incidents
and classify them for appropriate action.

Enhanced with GCP log context fetching - instead of saying "need more details",
the agent actively queries GCP logs to gather additional context.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError
from pydantic import ValidationError

from backend.config import settings
from backend.knowledge import get_triage_system_prompt, load_sre_knowledge
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
            api_key: Anthropic API key (defaults to settings)
            model: Model to use (defaults to settings.triage_model)
        """
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.triage_model
        self.client: Optional[Anthropic] = None
        self._system_prompt: Optional[str] = None
        self._gcp_client = None

        # Lazy initialize client when needed
        if self.api_key:
            self.client = Anthropic(api_key=self.api_key)

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

            time_filter = f'timestamp>="{start_time.isoformat()}Z" AND timestamp<="{end_time.isoformat()}Z"'

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

    def _format_incident(self, incident: Incident, gcp_context: Optional[Dict[str, Any]] = None) -> str:
        """Format an incident for Claude analysis, including GCP context."""
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
            # Additional context
            related_context=data.get("related_context", []),
        )

        return result

    async def analyze(self, incident: Incident, fetch_gcp_context: bool = True) -> TriageResult:
        """
        Analyze an incident and return triage result.

        Enhanced to fetch additional context from GCP logs before analysis.

        Args:
            incident: The incident to analyze
            fetch_gcp_context: Whether to fetch additional context from GCP logs (default True)

        Returns:
            TriageResult with classification and analysis

        Raises:
            TriageError: If analysis fails
        """
        if not self.client:
            raise TriageError("Anthropic client not initialized. Check ANTHROPIC_API_KEY.")

        logger.info(f"Triaging incident {incident.id}: {incident.title}")

        # Fetch GCP context for better triage
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

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                messages=[
                    {"role": "user", "content": self._format_incident(incident, gcp_context)}
                ]
            )

            response_text = message.content[0].text
            logger.debug(f"Claude response for {incident.id}: {response_text[:500]}")

            result = self._parse_response(response_text, incident.id)

            # Add GCP context to the result for display
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
            raise TriageError(f"Failed to connect to Anthropic API: {e}")
        except RateLimitError as e:
            logger.error(f"Rate limited triaging {incident.id}: {e}")
            raise TriageError(f"Rate limited by Anthropic API: {e}")
        except APIError as e:
            logger.error(f"API error triaging {incident.id}: {e}")
            raise TriageError(f"Anthropic API error: {e}")
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
