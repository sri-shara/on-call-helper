"""
Pattern Learner for On Call Helper.

Learns from historical incidents to improve classification accuracy.
Uses error signatures for pattern matching and tracks success rates
to inform future classifications.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from backend.models import (
    PatternRecord,
    PatternSuggestion,
    FixRecord,
)
from backend.services.error_aggregator import ErrorAggregator

logger = logging.getLogger(__name__)


class PatternLearner:
    """
    Learns error patterns from incident history.

    Uses MD5-based error signatures (from ErrorAggregator) to match
    similar errors and track classification outcomes over time.
    """

    def __init__(self, storage):
        """
        Initialize the pattern learner.

        Args:
            storage: Storage backend (FirestoreStorage or Storage)
        """
        self.storage = storage
        self._aggregator = ErrorAggregator()

    def get_pattern_suggestion(
        self,
        error_msg: str,
        service: str
    ) -> Optional[PatternSuggestion]:
        """
        Get a classification suggestion based on historical patterns.

        Args:
            error_msg: The error message to match
            service: The service name

        Returns:
            PatternSuggestion if a matching pattern exists, None otherwise
        """
        try:
            # Generate signature for this error
            pattern_id = self._aggregator.get_error_signature(service, error_msg)

            # Look up pattern in storage
            pattern = self.storage.get_pattern(pattern_id)
            if not pattern:
                return None

            # Get the most common classification
            classification = pattern.most_common_classification
            if not classification:
                return None

            # Calculate confidence based on occurrence count and consistency
            total = pattern.occurrence_count
            if total < 1:
                return None

            # Get count of the most common classification
            most_common_count = pattern.classifications.get(classification, 0)
            classification_confidence = most_common_count / total

            # Get most recent successful fix if available
            suggested_fix = None
            if pattern.successful_fixes:
                suggested_fix = pattern.successful_fixes[0]

            return PatternSuggestion(
                pattern_id=pattern_id,
                classification=classification,
                confidence=classification_confidence,
                occurrence_count=total,
                success_rate=pattern.success_rate,
                suggested_fix=suggested_fix,
            )

        except Exception as e:
            logger.warning(f"Pattern lookup failed: {e}")
            return None

    def record_incident(
        self,
        incident_id: str,
        error_msg: str,
        service: str,
        classification: str
    ) -> str:
        """
        Record an incident for pattern learning.

        Creates or updates the pattern record for this error signature.

        Args:
            incident_id: The incident ID
            error_msg: The error message
            service: The service name
            classification: The classification assigned

        Returns:
            The pattern_id for this error
        """
        # Generate signature
        pattern_id = self._aggregator.get_error_signature(service, error_msg)

        try:
            # Check if pattern exists
            pattern = self.storage.get_pattern(pattern_id)

            if pattern:
                # Update existing pattern
                classifications = pattern.classifications.copy()
                classifications[classification] = classifications.get(classification, 0) + 1

                pattern.classifications = classifications
                pattern.last_seen = datetime.utcnow()
                self.storage.save_pattern(pattern)

                logger.info(
                    f"Updated pattern {pattern_id}: {classification} "
                    f"(total: {pattern.occurrence_count} occurrences)"
                )
            else:
                # Create new pattern
                # Normalize error message for template (first 200 chars)
                error_template = error_msg[:200].strip()

                pattern = PatternRecord(
                    pattern_id=pattern_id,
                    error_template=error_template,
                    service_name=service,
                    classifications={classification: 1},
                    success_count=0,
                    failure_count=0,
                    first_seen=datetime.utcnow(),
                    last_seen=datetime.utcnow(),
                    successful_fixes=[],
                )
                self.storage.save_pattern(pattern)

                logger.info(
                    f"Created new pattern {pattern_id} for {service}: {classification}"
                )

        except Exception as e:
            logger.error(f"Failed to record pattern for {incident_id}: {e}")
            # Don't raise - pattern learning is non-critical

        return pattern_id

    def record_outcome(
        self,
        pattern_id: str,
        success: bool,
        fix_details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record the outcome of an incident resolution.

        Updates the pattern's success/failure counts and optionally
        adds the fix to successful_fixes.

        Args:
            pattern_id: The pattern to update
            success: Whether the resolution was successful
            fix_details: Optional dict with fix information:
                - incident_id: str
                - file_path: str
                - fix_explanation: str
                - pr_url: str (optional)
        """
        if not pattern_id:
            return

        try:
            pattern = self.storage.get_pattern(pattern_id)
            if not pattern:
                logger.warning(f"Pattern {pattern_id} not found for outcome recording")
                return

            # Create FixRecord if details provided and successful
            fix_record = None
            if success and fix_details:
                fix_record = FixRecord(
                    incident_id=fix_details.get("incident_id", "unknown"),
                    file_path=fix_details.get("file_path", "unknown"),
                    fix_explanation=fix_details.get("fix_explanation", ""),
                    pr_url=fix_details.get("pr_url"),
                    created_at=datetime.utcnow(),
                )

            # Update pattern
            if success:
                pattern.success_count += 1
            else:
                pattern.failure_count += 1

            pattern.last_seen = datetime.utcnow()

            # Add fix to successful_fixes if provided
            if fix_record:
                pattern.successful_fixes.insert(0, fix_record)
                pattern.successful_fixes = pattern.successful_fixes[:10]  # Limit to 10

            self.storage.save_pattern(pattern)

            logger.info(
                f"Recorded outcome for pattern {pattern_id}: "
                f"success={success}, rate={pattern.success_rate:.0%}"
            )

        except Exception as e:
            logger.error(f"Failed to record outcome for {pattern_id}: {e}")
            # Don't raise - pattern learning is non-critical

    def get_statistics(self) -> Dict[str, Any]:
        """Get pattern learning statistics."""
        try:
            return self.storage.get_pattern_stats()
        except Exception as e:
            logger.error(f"Failed to get pattern stats: {e}")
            return {
                "total_patterns": 0,
                "total_occurrences": 0,
                "patterns_with_successful_fixes": 0,
                "average_success_rate": 0,
                "most_common_classification": None,
                "patterns_by_classification": {},
            }


# Module-level singleton (optional - can also instantiate per-use)
_pattern_learner: Optional[PatternLearner] = None


def get_pattern_learner(storage=None) -> PatternLearner:
    """Get the global pattern learner instance."""
    global _pattern_learner
    if _pattern_learner is None:
        if storage is None:
            from backend.storage import storage as default_storage
            storage = default_storage
        _pattern_learner = PatternLearner(storage)
    return _pattern_learner
