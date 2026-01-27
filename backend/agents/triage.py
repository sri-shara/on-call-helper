"""
Triage Agent for On Call Helper.

Uses Claude AI with embedded SRE knowledge to analyze production incidents
and classify them for appropriate action.
"""

import json
import logging
import re
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

        # Lazy initialize client when needed
        if self.api_key:
            self.client = Anthropic(api_key=self.api_key)

    @property
    def system_prompt(self) -> str:
        """Get the system prompt with embedded SRE knowledge."""
        if self._system_prompt is None:
            self._system_prompt = get_triage_system_prompt()
        return self._system_prompt

    def _format_incident(self, incident: Incident) -> str:
        """Format an incident for Claude analysis."""
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

        parts.extend([
            "",
            "---",
            "Analyze this incident and provide your classification as JSON.",
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

        # Required fields
        classification = self._parse_classification(data.get("classification", "NEEDS_HUMAN"))
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

    async def analyze(self, incident: Incident) -> TriageResult:
        """
        Analyze an incident and return triage result.

        Args:
            incident: The incident to analyze

        Returns:
            TriageResult with classification and analysis

        Raises:
            TriageError: If analysis fails
        """
        if not self.client:
            raise TriageError("Anthropic client not initialized. Check ANTHROPIC_API_KEY.")

        logger.info(f"Triaging incident {incident.id}: {incident.title}")

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                messages=[
                    {"role": "user", "content": self._format_incident(incident)}
                ]
            )

            response_text = message.content[0].text
            logger.debug(f"Claude response for {incident.id}: {response_text[:500]}")

            result = self._parse_response(response_text, incident.id)

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
