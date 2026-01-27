"""
Transient error filter for On Call Helper.

Identifies self-healing errors that don't need automated fixes.
These patterns are sourced from the oncall repository:
/Users/sri/oncall/.claude/commands/sre-triage/error-patterns.md

Transient errors typically:
- Have automatic retry mechanisms
- Self-resolve within minutes
- Don't indicate code bugs requiring fixes
"""

import re
from typing import List, Optional, Tuple

from pydantic import BaseModel


class TransientPattern(BaseModel):
    """A pattern that identifies transient/self-healing errors."""

    pattern: str  # Regex pattern to match
    reason: str  # Why this error self-resolves
    category: str  # Category for grouping (network, race_condition, rate_limit, etc.)


# Patterns sourced from /Users/sri/oncall/.claude/commands/sre-triage/error-patterns.md
TRANSIENT_PATTERNS: List[TransientPattern] = [
    # Network/Connection Issues (auto-retry)
    TransientPattern(
        pattern=r"Timed out connecting to the backend cluster",
        reason="Transient AlloyDB routing issue. Pub/Sub has automatic retries.",
        category="network",
    ),
    TransientPattern(
        pattern=r"Routing deadline expired",
        reason="Cloud Run gRPC routing failure. Automatic retries succeed.",
        category="network",
    ),
    TransientPattern(
        pattern=r"context deadline exceeded",
        reason="Transient timeout on external APIs (Cisco AMP, Gemini, SOAR). Has retries.",
        category="network",
    ),
    TransientPattern(
        pattern=r"http_transport_failure",
        reason="Transient network issues. Has automatic retries.",
        category="network",
    ),
    TransientPattern(
        pattern=r"connection reset by peer",
        reason="Network connection dropped. Auto-reconnects.",
        category="network",
    ),

    # Race Conditions (idempotency handles it)
    TransientPattern(
        pattern=r"case number already exists",
        reason="Race condition in generateCaseNumber(). Retry mechanism handles it.",
        category="race_condition",
    ),
    TransientPattern(
        pattern=r"firestore: BulkWriter received duplicate write",
        reason="Idempotency working correctly. Not an error.",
        category="race_condition",
    ),
    TransientPattern(
        pattern=r"duplicate key value violates unique constraint.*case_events",
        reason="Deduplication catching duplicates. Expected behavior.",
        category="race_condition",
    ),
    TransientPattern(
        pattern=r"no unique or exclusion constraint matching the ON CONFLICT",
        reason="Deduplication working as intended. Expected with certain PRs.",
        category="race_condition",
    ),
    TransientPattern(
        pattern=r"failed to get case: not found.*race condition handling complete",
        reason="Casemaker consolidated duplicate cases. Expected behavior.",
        category="race_condition",
    ),

    # AI/LLM Issues (non-blocking, has retries)
    TransientPattern(
        pattern=r"no JSON found in agent response",
        reason="Non-blocking AI response issue. Request continues.",
        category="ai",
    ),
    TransientPattern(
        pattern=r"agent failed to return valid JSON response",
        reason="Gemini instruction-following issue. Has retries, usually self-resolves.",
        category="ai",
    ),
    TransientPattern(
        pattern=r"case precedent agent call failed",
        reason="Gemini issue. Has retries, happens rarely.",
        category="ai",
    ),

    # SOAR Integration (expected states)
    TransientPattern(
        pattern=r"SOAR.*secops_id.*null",
        reason="Orphaned state with no customer impact. Expected.",
        category="soar",
    ),
    TransientPattern(
        pattern=r"should_alert.*false|false.*should_alert",
        reason="Explicitly flagged as non-alerting. Expected.",
        category="soar",
    ),
    TransientPattern(
        pattern=r"SOAR case not found for Tenex case",
        reason="Case never synced to SOAR. Historical issue, not actionable.",
        category="soar",
    ),

    # Non-blocking Operations
    TransientPattern(
        pattern=r"failed to publish tier one message",
        reason="Non-blocking by design. Doesn't affect main flow.",
        category="non_blocking",
    ),
    TransientPattern(
        pattern=r"VirusTotal.*(?:rate limit|error|failed)",
        reason="Rate limits or transient API failures. Non-blocking enrichment.",
        category="rate_limit",
    ),

    # Resource Exhaustion (temporary)
    TransientPattern(
        pattern=r"RESOURCE_EXHAUSTED.*[Qq]uota exceeded",
        reason="Rate limiting. Backs off automatically.",
        category="rate_limit",
    ),
]


def is_transient_error(error_message: str) -> Tuple[bool, str, Optional[str]]:
    """
    Check if an error message matches known transient/self-healing patterns.

    Args:
        error_message: The error message to check

    Returns:
        Tuple of (is_transient, reason, category)
        - is_transient: True if the error matches a transient pattern
        - reason: Explanation of why this error self-resolves (empty if not transient)
        - category: Category of the transient error (None if not transient)
    """
    if not error_message:
        return False, "", None

    for pattern in TRANSIENT_PATTERNS:
        if re.search(pattern.pattern, error_message, re.IGNORECASE):
            return True, pattern.reason, pattern.category

    return False, "", None


def get_transient_patterns_by_category(category: str) -> List[TransientPattern]:
    """Get all transient patterns for a specific category."""
    return [p for p in TRANSIENT_PATTERNS if p.category == category]


def get_all_categories() -> List[str]:
    """Get list of all transient error categories."""
    return list(set(p.category for p in TRANSIENT_PATTERNS))
