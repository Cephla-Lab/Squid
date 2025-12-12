"""Acquisition UI state coordinator.

Manages UI state transitions during acquisition lifecycle:
- Live scan grid state (off during acquisition, restored after)
- Acquisition tab enabled/disabled state
- Autolevel toggle
- Well selector visibility
- Progress bar display

This is a UI-layer coordinator (not a backend controller) because it directly
manages Qt widget state. It subscribes to EventBus events and coordinates
widget updates in response.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

import squid.core.logging
from squid.core.events import (
    AcquisitionUIToggleCommand,
    AcquisitionStateChanged,
    LiveScanGridCommand,
    WellSelectorVisibilityCommand,
)

if TYPE_CHECKING:
    from squid.ui.ui_event_bus import UIEventBus

_log = squid.core.logging.get_logger(__name__)


@dataclass
class AcquisitionUIState:
    """Current state of acquisition-related UI."""

    acquisition_active: bool = False
    live_scan_grid_was_on: bool = False
    current_experiment_id: Optional[str] = None


class AcquisitionUICoordinator:
    """Coordinates UI state during acquisition lifecycle.

    Subscribes to: AcquisitionUIToggleCommand, AcquisitionStateChanged
    Publishes: LiveScanGridCommand, WellSelectorVisibilityCommand

    This coordinator bridges the gap between EventBus events and UI widget state.
    It maintains state about what should be restored after acquisition completes.
    """

    def __init__(
        self,
        ui_event_bus: "UIEventBus",
        toggle_live_scan_grid_fn: Optional[Callable[[bool], None]] = None,
        toggle_autolevel_fn: Optional[Callable[[bool], None]] = None,
        set_tabs_enabled_fn: Optional[Callable[[bool, int], None]] = None,
        toggle_well_selector_fn: Optional[Callable[[bool, bool], None]] = None,
        display_progress_bar_fn: Optional[Callable[[bool], None]] = None,
        set_click_to_move_fn: Optional[Callable[[bool], None]] = None,
        get_live_scan_grid_state_fn: Optional[Callable[[], bool]] = None,
        is_wellplate_acquisition_fn: Optional[Callable[[], bool]] = None,
        get_wellplate_format_fn: Optional[Callable[[], str]] = None,
    ) -> None:
        """Initialize the coordinator.

        Args:
            ui_event_bus: UIEventBus for thread-safe subscriptions.
            toggle_live_scan_grid_fn: Function to toggle live scan grid.
            toggle_autolevel_fn: Function to toggle autolevel display.
            set_tabs_enabled_fn: Function to enable/disable tabs (enabled, current_index).
            toggle_well_selector_fn: Function to toggle well selector (visible, remember_state).
            display_progress_bar_fn: Function to show/hide progress bar.
            set_click_to_move_fn: Function to enable/disable click-to-move.
            get_live_scan_grid_state_fn: Function to get current live scan grid state.
            is_wellplate_acquisition_fn: Function to check if current acquisition is wellplate.
            get_wellplate_format_fn: Function to get current wellplate format.
        """
        self._bus = ui_event_bus
        self._lock = threading.RLock()

        # Callback functions to manipulate UI
        self._toggle_live_scan_grid = toggle_live_scan_grid_fn
        self._toggle_autolevel = toggle_autolevel_fn
        self._set_tabs_enabled = set_tabs_enabled_fn
        self._toggle_well_selector = toggle_well_selector_fn
        self._display_progress_bar = display_progress_bar_fn
        self._set_click_to_move = set_click_to_move_fn
        self._get_live_scan_grid_state = get_live_scan_grid_state_fn
        self._is_wellplate_acquisition = is_wellplate_acquisition_fn
        self._get_wellplate_format = get_wellplate_format_fn

        self._state = AcquisitionUIState()

        self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        """Subscribe to relevant events via UIEventBus (Qt thread-safe)."""
        if self._bus is None:
            return
        self._bus.subscribe(AcquisitionUIToggleCommand, self._on_acquisition_ui_toggle)
        # Also subscribe to AcquisitionStateChanged for redundancy
        self._bus.subscribe(AcquisitionStateChanged, self._on_acquisition_state_changed)

    def detach(self) -> None:
        """Unsubscribe from events."""
        if self._bus is None:
            return
        try:
            self._bus.unsubscribe(AcquisitionUIToggleCommand, self._on_acquisition_ui_toggle)
            self._bus.unsubscribe(AcquisitionStateChanged, self._on_acquisition_state_changed)
        except Exception:
            pass

    def _on_acquisition_ui_toggle(self, cmd: AcquisitionUIToggleCommand) -> None:
        """Handle AcquisitionUIToggleCommand - main entry point for UI state changes."""
        _log.debug(f"AcquisitionUIToggleCommand: acquisition_started={cmd.acquisition_started}")
        self._handle_acquisition_toggle(cmd.acquisition_started, cmd.experiment_id)

    def _on_acquisition_state_changed(self, event: AcquisitionStateChanged) -> None:
        """Handle AcquisitionStateChanged from backend.

        This provides redundant handling in case widgets use AcquisitionStateChanged
        instead of AcquisitionUIToggleCommand.
        """
        # Only handle if we haven't already processed via AcquisitionUIToggleCommand
        with self._lock:
            if self._state.acquisition_active == event.in_progress:
                return  # Already in sync

        _log.debug(f"AcquisitionStateChanged: in_progress={event.in_progress}")
        self._handle_acquisition_toggle(event.in_progress, event.experiment_id)

    def _handle_acquisition_toggle(
        self, acquisition_started: bool, experiment_id: Optional[str] = None
    ) -> None:
        """Handle acquisition start/stop transitions.

        Args:
            acquisition_started: Whether acquisition is starting (True) or stopping (False).
            experiment_id: Optional experiment ID for tracking.
        """
        with self._lock:
            if acquisition_started:
                _log.info("STARTING ACQUISITION - coordinating UI state")
                self._state.current_experiment_id = experiment_id

                # Save and disable live scan grid
                if self._get_live_scan_grid_state:
                    self._state.live_scan_grid_was_on = self._get_live_scan_grid_state()
                if self._state.live_scan_grid_was_on and self._toggle_live_scan_grid:
                    self._toggle_live_scan_grid(False)

            else:
                _log.info("FINISHED ACQUISITION - restoring UI state")

                # Restore live scan grid if it was on before
                if self._state.live_scan_grid_was_on and self._toggle_live_scan_grid:
                    self._toggle_live_scan_grid(True)
                    self._state.live_scan_grid_was_on = False

                self._state.current_experiment_id = None

            self._state.acquisition_active = acquisition_started

        # Perform UI updates outside lock to avoid deadlocks
        self._update_ui_for_acquisition(acquisition_started)

    def _update_ui_for_acquisition(self, acquisition_started: bool) -> None:
        """Update UI elements based on acquisition state.

        Args:
            acquisition_started: Whether acquisition is starting or stopping.
        """
        # Click to move off during acquisition
        if self._set_click_to_move:
            self._set_click_to_move(not acquisition_started)

        # Disable other acquisition tabs during acquisition
        if self._set_tabs_enabled:
            self._set_tabs_enabled(not acquisition_started, -1)  # -1 means current tab

        # Disable autolevel once acquisition started
        if acquisition_started and self._toggle_autolevel:
            self._toggle_autolevel(False)

        # Handle well selector visibility
        if self._toggle_well_selector:
            is_wellplate = self._is_wellplate_acquisition() if self._is_wellplate_acquisition else False
            format_name = self._get_wellplate_format() if self._get_wellplate_format else "glass slide"

            if is_wellplate and format_name != "glass slide":
                self._toggle_well_selector(not acquisition_started, False)
            else:
                self._toggle_well_selector(False, False)

        # Display progress bar during acquisition
        if self._display_progress_bar:
            self._display_progress_bar(acquisition_started)
