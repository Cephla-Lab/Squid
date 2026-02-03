"""
Warning manager for experiment orchestration.

Provides thread-safe warning collection, filtering, and threshold monitoring.
"""

import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, TYPE_CHECKING

from squid.backend.controllers.orchestrator.warnings import (
    AcquisitionWarning,
    WarningCategory,
    WarningSeverity,
    WarningThresholds,
    DEFAULT_THRESHOLDS,
)
from squid.backend.controllers.orchestrator.state import (
    WarningRaised,
    WarningThresholdReached,
    WarningsCleared,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


@dataclass
class WarningStats:
    """Statistics about accumulated warnings."""

    total_count: int
    by_category: Dict[WarningCategory, int]
    by_severity: Dict[WarningSeverity, int]
    by_fov: Dict[str, int]


class WarningManager:
    """Thread-safe manager for acquisition warnings.

    Collects warnings during acquisition, provides filtering and querying,
    and monitors thresholds for pause triggers.

    Attributes:
        experiment_id: Current experiment identifier
        thresholds: Warning threshold configuration
    """

    def __init__(
        self,
        event_bus: Optional["EventBus"] = None,
        thresholds: Optional[WarningThresholds] = None,
        experiment_id: str = "",
    ):
        """Initialize the warning manager.

        Args:
            event_bus: Optional event bus for publishing warning events
            thresholds: Warning thresholds (uses defaults if not provided)
            experiment_id: Current experiment identifier
        """
        self._event_bus = event_bus
        self._thresholds = thresholds or DEFAULT_THRESHOLDS
        self._experiment_id = experiment_id

        self._lock = threading.RLock()
        self._warnings: List[AcquisitionWarning] = []
        self._total_warning_count: int = 0
        self._category_counts: Dict[WarningCategory, int] = defaultdict(int)
        self._severity_counts: Dict[WarningSeverity, int] = defaultdict(int)
        self._fov_counts: Dict[str, int] = defaultdict(int)

    @property
    def experiment_id(self) -> str:
        """Get the current experiment ID."""
        return self._experiment_id

    @experiment_id.setter
    def experiment_id(self, value: str) -> None:
        """Set the experiment ID."""
        self._experiment_id = value

    @property
    def thresholds(self) -> WarningThresholds:
        """Get the current thresholds."""
        return self._thresholds

    def set_thresholds(self, thresholds: WarningThresholds) -> None:
        """Update the warning thresholds.

        Args:
            thresholds: New threshold configuration
        """
        with self._lock:
            self._thresholds = thresholds

    def add_warning(
        self,
        category: WarningCategory,
        severity: WarningSeverity,
        message: str,
        *,
        round_index: int = 0,
        round_name: str = "",
        time_point: int = 0,
        operation_type: str = "",
        operation_index: int = 0,
        fov_id: Optional[str] = None,
        fov_index: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Add a new warning.

        Args:
            category: Warning category
            severity: Warning severity
            message: Human-readable description
            round_index: Round index (0-based)
            round_name: Round name
            operation_type: Type of operation
            operation_index: Operation index
            fov_id: Optional FOV identifier
            context: Additional context data

        Returns:
            True if a threshold was reached and pause is recommended
        """
        warning = AcquisitionWarning.create(
            category=category,
            severity=severity,
            message=message,
            round_index=round_index,
            round_name=round_name,
            time_point=time_point,
            operation_type=operation_type,
            operation_index=operation_index,
            fov_id=fov_id,
            fov_index=fov_index,
            context=context,
        )

        with self._lock:
            # Add warning
            self._warnings.append(warning)
            self._total_warning_count += 1
            self._category_counts[category] += 1
            self._severity_counts[severity] += 1
            if fov_id:
                self._fov_counts[fov_id] += 1

            # Enforce max stored warnings (FIFO)
            while len(self._warnings) > self._thresholds.max_stored_warnings:
                removed = self._warnings.pop(0)
                # Note: counts remain accumulated even when warnings are removed

            total_count = self._total_warning_count
            category_count = self._category_counts[category]

            # Check if should pause
            should_pause = self._check_thresholds(warning)

        # Publish events outside lock
        if self._event_bus:
            self._event_bus.publish(
                WarningRaised(
                    experiment_id=self._experiment_id,
                    category=category.name,
                    severity=severity.name,
                    message=message,
                    round_index=round_index,
                    round_name=round_name,
                    time_point=time_point,
                    fov_id=fov_id,
                    fov_index=fov_index,
                    total_warnings=total_count,
                    warnings_in_category=category_count,
                )
            )

            if should_pause:
                threshold_type, threshold_value = self._get_reached_threshold(warning)
                self._event_bus.publish(
                    WarningThresholdReached(
                        experiment_id=self._experiment_id,
                        threshold_type=threshold_type,
                        threshold_value=threshold_value,
                        current_count=total_count,
                        category=category.name if threshold_type == "category" else None,
                        should_pause=True,
                    )
                )

        _log.warning(
            f"[{category.name}] {severity.name}: {message} "
            f"(total={total_count}, category={category_count})"
        )

        return should_pause

    def _check_thresholds(self, warning: AcquisitionWarning) -> bool:
        """Check if any threshold has been reached.

        Must be called with lock held.

        Args:
            warning: The warning that was just added

        Returns:
            True if pause is recommended
        """
        # Check immediate pause conditions
        if self._thresholds.should_pause_on_warning(warning):
            return True

        # Check total count threshold
        if (
            self._thresholds.pause_after_count is not None
            and self._total_warning_count >= self._thresholds.pause_after_count
        ):
            return True

        # Check category-specific thresholds
        cat_threshold = self._thresholds.get_category_threshold(warning.category)
        if (
            cat_threshold is not None
            and self._category_counts[warning.category] >= cat_threshold
        ):
            return True

        return False

    def _get_reached_threshold(
        self, warning: AcquisitionWarning
    ) -> tuple:
        """Get the type and value of the reached threshold.

        Args:
            warning: The warning that triggered the threshold

        Returns:
            Tuple of (threshold_type, threshold_value)
        """
        # Check severity first
        if warning.severity in self._thresholds.pause_on_severity:
            return ("severity", 1)

        # Check category
        if warning.category in self._thresholds.pause_on_categories:
            return ("category", 1)

        # Check total count
        if (
            self._thresholds.pause_after_count is not None
            and self._total_warning_count >= self._thresholds.pause_after_count
        ):
            return ("total", self._thresholds.pause_after_count)

        # Check category threshold
        cat_threshold = self._thresholds.get_category_threshold(warning.category)
        if cat_threshold is not None:
            return ("category", cat_threshold)

        return ("unknown", 0)

    def get_warnings(
        self,
        *,
        category: Optional[WarningCategory] = None,
        severity: Optional[WarningSeverity] = None,
        round_index: Optional[int] = None,
        fov_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[AcquisitionWarning]:
        """Get warnings matching the specified filters.

        Args:
            category: Filter by category
            severity: Filter by severity
            round_index: Filter by round index
            fov_id: Filter by FOV ID
            limit: Maximum number of warnings to return (most recent first)

        Returns:
            List of matching warnings
        """
        with self._lock:
            result = list(self._warnings)

        # Apply filters
        if category is not None:
            result = [w for w in result if w.category == category]
        if severity is not None:
            result = [w for w in result if w.severity == severity]
        if round_index is not None:
            result = [w for w in result if w.round_index == round_index]
        if fov_id is not None:
            result = [w for w in result if w.fov_id == fov_id]

        # Apply limit (most recent first)
        if limit is not None:
            result = result[-limit:]

        return result

    def get_stats(self) -> WarningStats:
        """Get statistics about accumulated warnings.

        Returns:
            WarningStats with counts by category, severity, and FOV
        """
        with self._lock:
            return WarningStats(
                total_count=self._total_warning_count,
                by_category=dict(self._category_counts),
                by_severity=dict(self._severity_counts),
                by_fov=dict(self._fov_counts),
            )

    def clear(
        self,
        *,
        categories: Optional[tuple] = None,
    ) -> int:
        """Clear warnings.

        Args:
            categories: If provided, only clear warnings in these categories.
                       If None, clear all warnings.

        Returns:
            Number of warnings cleared
        """
        with self._lock:
            if categories is None:
                # Clear all
                cleared_count = len(self._warnings)
                self._warnings.clear()
                self._total_warning_count = 0
                self._category_counts.clear()
                self._severity_counts.clear()
                self._fov_counts.clear()
            else:
                # Clear specific categories
                category_set = set(categories)
                original_count = len(self._warnings)
                self._warnings = [
                    w for w in self._warnings if w.category not in category_set
                ]
                cleared_count = original_count - len(self._warnings)

                # Recompute ALL counts from remaining stored warnings
                self._category_counts.clear()
                self._severity_counts.clear()
                self._fov_counts.clear()
                for w in self._warnings:
                    self._category_counts[w.category] += 1
                    self._severity_counts[w.severity] += 1
                    if w.fov_id:
                        self._fov_counts[w.fov_id] += 1
                self._total_warning_count = len(self._warnings)

        # Publish event outside lock
        if self._event_bus and cleared_count > 0:
            self._event_bus.publish(
                WarningsCleared(
                    experiment_id=self._experiment_id,
                    cleared_count=cleared_count,
                    categories_cleared=categories,
                )
            )

        _log.info(f"Cleared {cleared_count} warnings")
        return cleared_count

    def __len__(self) -> int:
        """Get total number of stored warnings."""
        with self._lock:
            return len(self._warnings)

    def __iter__(self) -> Iterator[AcquisitionWarning]:
        """Iterate over warnings (thread-safe copy)."""
        with self._lock:
            return iter(list(self._warnings))
