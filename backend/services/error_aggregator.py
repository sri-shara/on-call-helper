"""
Error Aggregator for On Call Helper.

Aggregates similar errors into single incidents to reduce noise.
Uses error signatures (service + normalized error message) to detect duplicates.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional


@dataclass
class AggregatedIncident:
    """Tracks an active incident for aggregation."""
    incident_id: str
    signature: str
    first_seen: datetime
    count: int
    last_seen: datetime = None

    def __post_init__(self):
        if self.last_seen is None:
            self.last_seen = self.first_seen


class ErrorAggregator:
    """
    Aggregates similar errors into single incidents.

    When the same error (same service + similar message) occurs multiple times
    within a time window, we increment the count on the existing incident
    instead of creating duplicates.
    """

    def __init__(self, window_minutes: int = 10, max_signatures: int = 1000):
        """
        Initialize the aggregator.

        Args:
            window_minutes: Time window for aggregation (default 10 min)
            max_signatures: Maximum signatures to track before cleanup
        """
        self.window_minutes = window_minutes
        self.max_signatures = max_signatures
        self._active_signatures: Dict[str, AggregatedIncident] = {}

    def get_error_signature(self, service: str, error: str) -> str:
        """
        Generate a signature from service name and normalized error message.

        Normalization removes:
        - Timestamps (2024-01-15T10:30:00)
        - UUIDs (550e8400-e29b-41d4-a716-446655440000)
        - Line numbers and memory addresses
        - Specific numeric values

        Args:
            service: The service name
            error: The error message

        Returns:
            16-character hex signature
        """
        # Normalize the error message
        normalized = error

        # Remove timestamps in various formats
        normalized = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?Z?', '<TIME>', normalized)

        # Remove UUIDs
        normalized = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<UUID>', normalized, flags=re.IGNORECASE)

        # Remove hex addresses (0x7fff5fbff8c0)
        normalized = re.sub(r'0x[0-9a-f]+', '<ADDR>', normalized, flags=re.IGNORECASE)

        # Remove line numbers (file.go:142)
        normalized = re.sub(r':\d+', ':<LINE>', normalized)

        # Remove specific numeric values (keep structure)
        normalized = re.sub(r'\b\d{5,}\b', '<NUM>', normalized)

        # Take first 200 chars for signature (enough to identify unique errors)
        normalized = normalized[:200].strip().lower()

        # Generate hash
        signature_input = f"{service.lower()}:{normalized}"
        return hashlib.md5(signature_input.encode()).hexdigest()[:16]

    def should_aggregate(self, signature: str) -> Optional[str]:
        """
        Check if an error with this signature should be aggregated.

        Args:
            signature: The error signature

        Returns:
            The incident_id to aggregate into, or None if new incident needed
        """
        self._cleanup_expired()

        if signature not in self._active_signatures:
            return None

        agg = self._active_signatures[signature]
        elapsed = (datetime.utcnow() - agg.first_seen).total_seconds()

        if elapsed < self.window_minutes * 60:
            return agg.incident_id

        # Signature expired, remove it
        del self._active_signatures[signature]
        return None

    def register_incident(self, signature: str, incident_id: str):
        """
        Register a new incident for a signature.

        Args:
            signature: The error signature
            incident_id: The incident ID to track
        """
        now = datetime.utcnow()
        self._active_signatures[signature] = AggregatedIncident(
            incident_id=incident_id,
            signature=signature,
            first_seen=now,
            last_seen=now,
            count=1
        )

        # Cleanup if too many signatures
        if len(self._active_signatures) > self.max_signatures:
            self._cleanup_oldest()

    def increment_count(self, signature: str) -> int:
        """
        Increment the count for an existing signature.

        Args:
            signature: The error signature

        Returns:
            The new count
        """
        if signature not in self._active_signatures:
            return 1

        self._active_signatures[signature].count += 1
        self._active_signatures[signature].last_seen = datetime.utcnow()
        return self._active_signatures[signature].count

    def get_count(self, signature: str) -> int:
        """Get the current count for a signature."""
        if signature not in self._active_signatures:
            return 0
        return self._active_signatures[signature].count

    def _cleanup_expired(self):
        """Remove expired signatures."""
        now = datetime.utcnow()
        window_seconds = self.window_minutes * 60
        expired = [
            sig for sig, agg in self._active_signatures.items()
            if (now - agg.first_seen).total_seconds() > window_seconds
        ]
        for sig in expired:
            del self._active_signatures[sig]

    def _cleanup_oldest(self):
        """Remove oldest half of signatures when limit exceeded."""
        if len(self._active_signatures) <= self.max_signatures // 2:
            return

        # Sort by first_seen and keep newest half
        sorted_sigs = sorted(
            self._active_signatures.items(),
            key=lambda x: x[1].first_seen,
            reverse=True
        )
        keep_count = self.max_signatures // 2
        self._active_signatures = dict(sorted_sigs[:keep_count])


# Global instance
_aggregator: Optional[ErrorAggregator] = None


def get_error_aggregator(window_minutes: int = 10) -> ErrorAggregator:
    """Get the global error aggregator instance."""
    global _aggregator
    if _aggregator is None:
        _aggregator = ErrorAggregator(window_minutes=window_minutes)
    return _aggregator


def reset_aggregator():
    """Reset the global aggregator (for testing)."""
    global _aggregator
    _aggregator = None
