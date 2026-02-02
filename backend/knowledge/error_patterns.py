"""
Error Pattern Recognition Module.

Parses and matches known error patterns from the oncall repository
for faster and more accurate triage classification.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class PatternSeverity(str, Enum):
    """Severity levels for error patterns."""
    CRITICAL = "critical"  # Data loss, requires immediate action
    SELF_RESOLVING = "self_resolving"  # Transient, will self-heal
    INVESTIGATE = "investigate"  # Needs investigation but not urgent


@dataclass
class ErrorPattern:
    """A known error pattern with classification."""
    pattern: str  # Regex or substring pattern
    severity: PatternSeverity
    reason: str  # Why this pattern has this severity
    action: Optional[str] = None  # Recommended action


# Critical patterns - data loss, immediate action required
CRITICAL_PATTERNS: List[ErrorPattern] = [
    ErrorPattern(
        pattern=r"SQLSTATE 22P05|unsupported Unicode escape sequence",
        severity=PatternSeverity.CRITICAL,
        reason="Alert silently dropped due to invalid Unicode. Never reaches Nucleus.",
        action="Find detection ID via Chronicle API, notify SOC, create fix PR"
    ),
    ErrorPattern(
        pattern=r"silently dropped|not stored|acked without storage",
        severity=PatternSeverity.CRITICAL,
        reason="Data loss indicator - alerts not persisted",
        action="Investigate data loss, check Chronicle for original alert"
    ),
]

# Self-resolving patterns - transient issues that retry/heal
SELF_RESOLVING_PATTERNS: List[ErrorPattern] = [
    ErrorPattern(
        pattern=r"Timed out connecting to the backend cluster",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Transient AlloyDB routing. Pub/Sub retries."
    ),
    ErrorPattern(
        pattern=r"Routing deadline expired",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Cloud Run gRPC routing failure. Retries succeed."
    ),
    ErrorPattern(
        pattern=r"firestore.*BulkWriter received duplicate write",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Idempotency working correctly."
    ),
    ErrorPattern(
        pattern=r"context deadline exceeded",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Transient timeout to external APIs (Cisco AMP, Gemini, SOAR)."
    ),
    ErrorPattern(
        pattern=r"case number already exists",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Race condition in generateCaseNumber(). Retry wins."
    ),
    ErrorPattern(
        pattern=r"SQLSTATE 23505|duplicate key value violates unique constraint",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Race condition on insert. Unique constraint prevents duplicate, retry succeeds."
    ),
    ErrorPattern(
        pattern=r"no JSON found in agent response",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Non-blocking. Request continues."
    ),
    ErrorPattern(
        pattern=r"agent failed to return valid JSON response|case precedent agent call failed",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Gemini not following instructions. Has retries."
    ),
    ErrorPattern(
        pattern=r"should_alert.*false.*secops",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Explicitly flagged as non-alerting."
    ),
    ErrorPattern(
        pattern=r"failed to get case.*not found.*race condition handling complete",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Casemaker consolidated duplicate cases."
    ),
    ErrorPattern(
        pattern=r"failed to publish tier one message",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Non-blocking by design."
    ),
    ErrorPattern(
        pattern=r"SOAR case not found for Tenex case",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Case never synced to SOAR. Historical issue."
    ),
    ErrorPattern(
        pattern=r"VirusTotal.*error|VirusTotal.*fail",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Rate limits or transient API failures. Non-blocking enrichment."
    ),
    ErrorPattern(
        pattern=r"http_transport_failure",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Transient network issues. Has retries."
    ),
    ErrorPattern(
        pattern=r"no unique or exclusion constraint matching the ON CONFLICT",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Dedup working as intended."
    ),
    ErrorPattern(
        pattern=r"failed to execute udm search.*400.*INVALID_ARGUMENT",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="User-submitted malformed UDM query. Expected behavior."
    ),
    ErrorPattern(
        pattern=r"secops_id.*null.*SOAR",
        severity=PatternSeverity.SELF_RESOLVING,
        reason="Orphaned state - tenant has no SOAR integration. No customer impact."
    ),
]

# Patterns requiring investigation
INVESTIGATE_PATTERNS: List[ErrorPattern] = [
    ErrorPattern(
        pattern=r"should_alert.*true.*secops",
        severity=PatternSeverity.INVESTIGATE,
        reason="Flagged for attention. Check tenant config.",
        action="Check tenant SOAR configuration"
    ),
    ErrorPattern(
        pattern=r"wait_count.*Lock.*[2-9]\d{3}|wait_count.*Lock.*\d{4,}",
        severity=PatternSeverity.INVESTIGATE,
        reason="Active lock contention (>2000). Check entity processing.",
        action="See Scenario 7 in runbooks/alloydb.md. Consider pausing entity subscriptions."
    ),
    ErrorPattern(
        pattern=r"property.*not supported|failed to convert property name",
        severity=PatternSeverity.INVESTIGATE,
        reason="Backwards-compat break in alert properties.",
        action="Check recent PRs touching alertproperties. See Issue #1264."
    ),
    ErrorPattern(
        pattern=r"column not found|relation not found",
        severity=PatternSeverity.INVESTIGATE,
        reason="Schema migration issue.",
        action="Check recent Atlas migrations."
    ),
    ErrorPattern(
        pattern=r"SQLSTATE 42P10",
        severity=PatternSeverity.INVESTIGATE,
        reason="Invalid column reference.",
        action="Check demo-refresher or recent schema changes. See Issue #1304."
    ),
    ErrorPattern(
        pattern=r"deadlock detected.*entityenricher",
        severity=PatternSeverity.INVESTIGATE,
        reason="Entity upsert contention.",
        action="May need to pause subscriptions. See runbooks/alloydb.md Scenario 7."
    ),
]


class ErrorPatternMatcher:
    """
    Matches error messages against known patterns for quick classification.
    """

    def __init__(self):
        """Initialize the pattern matcher with compiled regexes."""
        self._critical_compiled = [
            (re.compile(p.pattern, re.IGNORECASE), p)
            for p in CRITICAL_PATTERNS
        ]
        self._self_resolving_compiled = [
            (re.compile(p.pattern, re.IGNORECASE), p)
            for p in SELF_RESOLVING_PATTERNS
        ]
        self._investigate_compiled = [
            (re.compile(p.pattern, re.IGNORECASE), p)
            for p in INVESTIGATE_PATTERNS
        ]

    def match(self, error_message: str) -> Optional[Tuple[ErrorPattern, str]]:
        """
        Match an error message against known patterns.

        Args:
            error_message: The error message to match

        Returns:
            Tuple of (matched pattern, match text) or None if no match
        """
        # Check critical patterns first (highest priority)
        for regex, pattern in self._critical_compiled:
            match = regex.search(error_message)
            if match:
                return (pattern, match.group(0))

        # Check self-resolving patterns
        for regex, pattern in self._self_resolving_compiled:
            match = regex.search(error_message)
            if match:
                return (pattern, match.group(0))

        # Check investigation patterns
        for regex, pattern in self._investigate_compiled:
            match = regex.search(error_message)
            if match:
                return (pattern, match.group(0))

        return None

    def get_classification_hint(
        self, error_message: str
    ) -> Tuple[Optional[str], float, Optional[str], Optional[str]]:
        """
        Get a classification hint based on pattern matching.

        Args:
            error_message: The error message to analyze

        Returns:
            Tuple of (classification, confidence_boost, reason, action)
            - classification: "TRANSIENT", "INFRA_ISSUE", "NEEDS_HUMAN", or None
            - confidence_boost: How much to boost confidence (0.0-0.3)
            - reason: Why this classification was suggested
            - action: Recommended action if any
        """
        result = self.match(error_message)
        if not result:
            return (None, 0.0, None, None)

        pattern, match_text = result

        if pattern.severity == PatternSeverity.CRITICAL:
            # Critical patterns need human attention immediately
            return ("NEEDS_HUMAN", 0.3, pattern.reason, pattern.action)

        elif pattern.severity == PatternSeverity.SELF_RESOLVING:
            return ("TRANSIENT", 0.3, pattern.reason, None)

        elif pattern.severity == PatternSeverity.INVESTIGATE:
            # Investigation needed - could be infra or needs human
            return ("INFRA_ISSUE", 0.2, pattern.reason, pattern.action)

        return (None, 0.0, None, None)


# Global instance for easy access
_matcher: Optional[ErrorPatternMatcher] = None


def get_pattern_matcher() -> ErrorPatternMatcher:
    """Get the global pattern matcher instance."""
    global _matcher
    if _matcher is None:
        _matcher = ErrorPatternMatcher()
    return _matcher


def match_error_pattern(error_message: str) -> Optional[Tuple[ErrorPattern, str]]:
    """
    Convenience function to match an error message.

    Args:
        error_message: The error message to match

    Returns:
        Tuple of (matched pattern, match text) or None
    """
    return get_pattern_matcher().match(error_message)


def get_pattern_classification(
    error_message: str
) -> Tuple[Optional[str], float, Optional[str], Optional[str]]:
    """
    Convenience function to get classification hint.

    Returns:
        Tuple of (classification, confidence_boost, reason, action)
    """
    return get_pattern_matcher().get_classification_hint(error_message)
