"""
Runbook Suggestion Module.

Maps error patterns to relevant runbooks for quick remediation guidance.
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class RunbookSuggestion:
    """A suggested runbook for an error."""
    runbook_path: str
    runbook_name: str
    relevance_score: float  # 0.0-1.0
    reason: str
    specific_section: Optional[str] = None
    manual_steps: Optional[List[str]] = None


# Runbook keyword mappings
RUNBOOK_MAPPINGS = [
    # AlloyDB / Database issues
    {
        "patterns": [
            r"alloydb", r"postgresql", r"postgres", r"database",
            r"wait_count", r"wait_time", r"lock contention", r"deadlock",
            r"connection.*exhaust", r"too many connections",
            r"SQLSTATE", r"relation.*not found", r"column.*not found",
            r"entity.*upsert", r"entityenricher"
        ],
        "runbook": "runbooks/alloydb.md",
        "name": "AlloyDB Troubleshooting",
        "section_hints": {
            r"wait_count|lock contention|deadlock": "Scenario 7: Entity Upsert Lock Contention",
            r"connection": "Connection Pool Monitoring",
            r"slow.*query|timeout.*query": "Scenario 1: High CPU from Slow Queries",
            r"entity": "Scenario 7: Entity Processing Issues",
        }
    },
    # Pub/Sub issues
    {
        "patterns": [
            r"pubsub", r"pub/sub", r"subscription", r"backlog",
            r"message.*age", r"unacked", r"redelivery",
            r"ack.*deadline", r"poison.*pill",
            r"batchwriter", r"eventwriter", r"processor"
        ],
        "runbook": "runbooks/pubsub-backlogs.md",
        "name": "Pub/Sub Backlogs",
        "section_hints": {
            r"backlog|unacked": "Checking Backlog Status",
            r"redelivery|ack.*deadline": "Scenario 3: Redelivery Storm",
            r"poison|stuck": "Scenario 4: Stuck/Poison Pill Messages",
            r"crash|oom|memory": "Scenario 1: Processor Crashes",
        }
    },
    # Cloud Run / Service issues
    {
        "patterns": [
            r"cloud.*run", r"container", r"instance",
            r"5[0-9]{2}.*error", r"service.*unavailable",
            r"timeout", r"deadline.*exceeded",
            r"memory.*limit", r"cpu.*limit",
            r"cold.*start", r"scaling"
        ],
        "runbook": "runbooks/cloud-run.md",
        "name": "Cloud Run Troubleshooting",
        "section_hints": {
            r"timeout|deadline": "Timeout Issues",
            r"memory|oom": "Memory Configuration",
            r"5[0-9]{2}": "Error Response Codes",
            r"scaling|instance": "Scaling and Instances",
        }
    },
    # Integration issues
    {
        "patterns": [
            r"cisco.*amp", r"secops", r"soar",
            r"integration", r"api.*key", r"credential",
            r"chronicle", r"virustotal", r"external.*api"
        ],
        "runbook": "runbooks/integrations.md",
        "name": "Integration Troubleshooting",
        "section_hints": {
            r"cisco|amp": "Cisco AMP Integration",
            r"chronicle": "Chronicle Integration",
            r"soar": "SOAR Integration",
            r"virustotal": "VirusTotal Integration",
        }
    },
    # SecOps specific
    {
        "patterns": [
            r"secops.*integration", r"soar.*sync",
            r"case.*sync", r"secops_id",
            r"soar.*case.*not found"
        ],
        "runbook": "runbooks/secops-integration.md",
        "name": "SecOps Integration",
        "section_hints": {
            r"sync": "Sync Issues",
            r"secops_id": "ID Mapping",
        }
    },
]


class RunbookSuggester:
    """
    Suggests relevant runbooks based on error patterns.
    """

    def __init__(self):
        """Initialize the suggester with compiled patterns."""
        self._compiled_mappings = []
        for mapping in RUNBOOK_MAPPINGS:
            compiled_patterns = [
                re.compile(p, re.IGNORECASE) for p in mapping["patterns"]
            ]
            compiled_sections = {
                re.compile(k, re.IGNORECASE): v
                for k, v in mapping.get("section_hints", {}).items()
            }
            self._compiled_mappings.append({
                "patterns": compiled_patterns,
                "sections": compiled_sections,
                "runbook": mapping["runbook"],
                "name": mapping["name"],
            })

    def suggest(self, error_message: str, service_name: Optional[str] = None) -> List[RunbookSuggestion]:
        """
        Suggest runbooks based on error message and service name.

        Args:
            error_message: The error message to analyze
            service_name: Optional service name for additional context

        Returns:
            List of runbook suggestions, sorted by relevance
        """
        suggestions = []
        combined_text = error_message
        if service_name:
            combined_text = f"{service_name} {error_message}"

        for mapping in self._compiled_mappings:
            match_count = 0
            matched_terms = []

            # Count pattern matches
            for pattern in mapping["patterns"]:
                match = pattern.search(combined_text)
                if match:
                    match_count += 1
                    matched_terms.append(match.group(0))

            if match_count == 0:
                continue

            # Calculate relevance score
            relevance = min(1.0, match_count * 0.25)

            # Find specific section if possible
            specific_section = None
            for section_pattern, section_name in mapping["sections"].items():
                if section_pattern.search(combined_text):
                    specific_section = section_name
                    relevance += 0.1  # Boost for specific section match
                    break

            suggestions.append(RunbookSuggestion(
                runbook_path=mapping["runbook"],
                runbook_name=mapping["name"],
                relevance_score=min(1.0, relevance),
                reason=f"Matched: {', '.join(matched_terms[:3])}",
                specific_section=specific_section,
            ))

        # Sort by relevance
        suggestions.sort(key=lambda x: x.relevance_score, reverse=True)
        return suggestions

    def get_best_suggestion(
        self,
        error_message: str,
        service_name: Optional[str] = None
    ) -> Optional[RunbookSuggestion]:
        """
        Get the single best runbook suggestion.

        Args:
            error_message: The error message to analyze
            service_name: Optional service name

        Returns:
            Best runbook suggestion or None
        """
        suggestions = self.suggest(error_message, service_name)
        return suggestions[0] if suggestions else None

    def get_manual_steps(
        self,
        error_message: str,
        classification: str
    ) -> List[str]:
        """
        Get manual investigation steps based on error and classification.

        Args:
            error_message: The error message
            classification: The triage classification

        Returns:
            List of manual steps
        """
        steps = []
        error_lower = error_message.lower()

        # Database-related steps
        if any(term in error_lower for term in ["alloydb", "database", "sql", "lock", "deadlock"]):
            steps.extend([
                "Check AlloyDB wait_count metric for lock contention",
                "Review connection count - approaching 100 indicates exhaustion",
                "Identify slow or blocking queries in Cloud Logging",
            ])

        # Pub/Sub-related steps
        if any(term in error_lower for term in ["pubsub", "backlog", "subscription", "message"]):
            steps.extend([
                "Check Pub/Sub backlog age for affected subscriptions",
                "Verify consumer service is healthy (not OOMing)",
                "Check for poison pill messages stuck in queue",
            ])

        # Cross-service steps
        if any(term in error_lower for term in ["timeout", "deadline", "connection"]):
            steps.extend([
                "Check if error is isolated to one tenant or widespread",
                "Verify downstream services are healthy",
                "Check recent deployments that may have introduced issues",
            ])

        # Entity processing steps
        if any(term in error_lower for term in ["entity", "enricher", "extractor"]):
            steps.extend([
                "Check entity processing subscription status",
                "Monitor wait_count metric - >2000 requires pause",
                "Consider pausing entity subscriptions if lock contention persists",
            ])

        # Default steps if nothing specific matched
        if not steps:
            steps = [
                "Review full error stack trace in Cloud Logging",
                "Check if error is isolated or affecting multiple tenants",
                "Review recent deployments for related changes",
                "Check infrastructure dashboards for anomalies",
            ]

        return steps


# Global instance
_suggester: Optional[RunbookSuggester] = None


def get_runbook_suggester() -> RunbookSuggester:
    """Get the global runbook suggester instance."""
    global _suggester
    if _suggester is None:
        _suggester = RunbookSuggester()
    return _suggester


def suggest_runbook(
    error_message: str,
    service_name: Optional[str] = None
) -> Optional[RunbookSuggestion]:
    """
    Convenience function to get the best runbook suggestion.

    Args:
        error_message: The error message
        service_name: Optional service name

    Returns:
        Best runbook suggestion or None
    """
    return get_runbook_suggester().get_best_suggestion(error_message, service_name)


def get_investigation_steps(
    error_message: str,
    classification: str
) -> List[str]:
    """
    Convenience function to get investigation steps.

    Args:
        error_message: The error message
        classification: The triage classification

    Returns:
        List of investigation steps
    """
    return get_runbook_suggester().get_manual_steps(error_message, classification)
