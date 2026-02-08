"""
Warning system for experiment orchestration.

Provides structured warnings with categories, severity levels, and
threshold-based pause support for monitoring acquisition quality.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, Optional, Tuple


class WarningCategory(Enum):
    """Categories of warnings that can occur during acquisition."""

    FOCUS = auto()  # Autofocus failures, drift detected
    HARDWARE = auto()  # Stage errors, camera issues, illumination
    FLUIDICS = auto()  # Flow issues, pressure warnings
    IMAGE_QUALITY = auto()  # Saturation, low signal, blur
    TIMING = auto()  # Delays, timeouts, scheduling issues
    STORAGE = auto()  # Disk space, write failures
    SYSTEM = auto()  # Memory, CPU, general system warnings
    EXECUTION = auto()  # V2: Step execution failures (handled via error_handling)
    OTHER = auto()  # Uncategorized warnings


class WarningSeverity(Enum):
    """Severity levels for warnings."""

    INFO = auto()  # Informational, no action needed
    LOW = auto()  # Minor issue, worth noting
    MEDIUM = auto()  # Significant issue, may need attention
    HIGH = auto()  # Serious issue, likely needs intervention
    CRITICAL = auto()  # Critical issue, should pause acquisition


@dataclass(frozen=True)
class AcquisitionWarning:
    """A warning generated during acquisition.

    Warnings capture issues that occur during experiment execution.
    They can be filtered by category, FOV, or round for analysis.

    Attributes:
        timestamp: When the warning occurred
        category: Type of warning (focus, hardware, etc.)
        severity: How serious the warning is
        message: Human-readable description
        round_index: Which round this occurred in (0-indexed)
        round_name: Name of the round
        operation_type: Type of operation (imaging, fluidics, etc.)
        operation_index: Index within the operation
        fov_id: Optional FOV identifier if warning is FOV-specific
        context: Additional context data for debugging
    """

    timestamp: datetime
    category: WarningCategory
    severity: WarningSeverity
    message: str
    round_index: int = 0
    round_name: str = ""
    time_point: int = 0
    operation_type: str = ""
    operation_index: int = 0
    fov_id: Optional[str] = None
    fov_index: Optional[int] = None
    context: Tuple[Tuple[str, Any], ...] = ()  # Frozen-compatible dict alternative

    @classmethod
    def create(
        cls,
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
    ) -> "AcquisitionWarning":
        """Factory method to create a warning with current timestamp.

        Args:
            category: Warning category
            severity: Warning severity
            message: Human-readable description
            round_index: Round index (0-based)
            round_name: Round name
            operation_type: Type of operation
            operation_index: Operation index
            fov_id: Optional FOV identifier
            context: Additional context dict (converted to frozen tuple)

        Returns:
            New AcquisitionWarning instance
        """
        context_tuple = tuple(context.items()) if context else ()
        return cls(
            timestamp=datetime.now(),
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
            context=context_tuple,
        )

    def get_context(self) -> Dict[str, Any]:
        """Get context as a dictionary."""
        return dict(self.context)


@dataclass
class WarningThresholds:
    """Configuration for warning-based pause thresholds.

    Defines when to automatically pause acquisition based on
    accumulated warnings.

    Attributes:
        pause_after_count: Pause after this many stored warnings (None = disabled)
        pause_on_severity: Pause immediately on warnings of these severities
        pause_on_categories: Pause immediately on warnings in these categories
        max_stored_warnings: Maximum warnings to keep in memory (FIFO)
        category_thresholds: Per-category pause thresholds
    """

    pause_after_count: Optional[int] = None
    pause_on_severity: Tuple[WarningSeverity, ...] = (WarningSeverity.CRITICAL,)
    pause_on_categories: Tuple[WarningCategory, ...] = ()
    max_stored_warnings: int = 1000
    category_thresholds: Tuple[Tuple[WarningCategory, int], ...] = ()

    def should_pause_on_warning(self, warning: AcquisitionWarning) -> bool:
        """Check if this warning should trigger an immediate pause.

        Args:
            warning: The warning to check

        Returns:
            True if acquisition should pause
        """
        # Check severity-based pause
        if warning.severity in self.pause_on_severity:
            return True

        # Check category-based pause
        if warning.category in self.pause_on_categories:
            return True

        return False

    def get_category_threshold(self, category: WarningCategory) -> Optional[int]:
        """Get the pause threshold for a specific category.

        Args:
            category: The warning category

        Returns:
            Threshold count, or None if no threshold set
        """
        for cat, threshold in self.category_thresholds:
            if cat == category:
                return threshold
        return None


# Default thresholds for common use cases
DEFAULT_THRESHOLDS = WarningThresholds(
    pause_after_count=None,  # Don't pause on total count by default
    pause_on_severity=(WarningSeverity.CRITICAL,),  # Pause on critical
    pause_on_categories=(),  # No category-based pause by default
    max_stored_warnings=1000,
)

STRICT_THRESHOLDS = WarningThresholds(
    pause_after_count=10,  # Pause after 10 warnings
    pause_on_severity=(WarningSeverity.HIGH, WarningSeverity.CRITICAL),
    pause_on_categories=(WarningCategory.FOCUS, WarningCategory.HARDWARE),
    max_stored_warnings=1000,
    category_thresholds=(
        (WarningCategory.FOCUS, 5),  # Pause after 5 focus warnings
        (WarningCategory.IMAGE_QUALITY, 10),  # Pause after 10 image quality warnings
    ),
)
