"""Peripherals controller.

Handles simple peripheral hardware that doesn't need complex orchestration:
objective changer, spinning disk, piezo Z stage.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional

from squid.backend.controllers.base import BaseController
from squid.core.events import (
    SetObjectiveCommand,
    SetSpinningDiskPositionCommand,
    SetSpinningDiskSpinningCommand,
    SetDiskDichroicCommand,
    SetDiskEmissionFilterCommand,
    ObjectiveChanged,
    SpinningDiskStateChanged,
    PiezoPositionChanged,
    PixelSizeChanged,
    handles,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus
    from squid.backend.managers.objective_store import ObjectiveStore
    from squid.backend.services.objective_changer_service import ObjectiveChangerService
    from squid.backend.services.spinning_disk_service import SpinningDiskService
    from squid.backend.services.piezo_service import PiezoService


@dataclass(frozen=True)
class SpinningDiskState:
    """State of spinning disk confocal."""

    is_available: bool
    is_disk_in: bool = False
    is_spinning: bool = False
    motor_speed: int = 0
    dichroic: int = 0
    emission_filter: int = 0


@dataclass
class PeripheralsState:
    """State managed by PeripheralsController."""

    objective_position: Optional[int] = None
    objective_name: Optional[str] = None
    pixel_size_um: Optional[float] = None
    spinning_disk: Optional[SpinningDiskState] = None
    piezo_position_um: Optional[float] = None


class PeripheralsController(BaseController):
    """Handles peripheral hardware control.

    Manages: objective changer, spinning disk, piezo Z stage.

    Subscribes to: SetObjectiveCommand, SetSpinningDisk*, SetPiezo*
    Publishes: ObjectiveChanged, SpinningDiskStateChanged, PiezoPositionChanged
    """

    def __init__(
        self,
        objective_service: Optional["ObjectiveChangerService"],
        spinning_disk_service: Optional["SpinningDiskService"],
        piezo_service: Optional["PiezoService"],
        objective_store: Optional["ObjectiveStore"],
        event_bus: "EventBus",
    ) -> None:
        super().__init__(event_bus)
        self._objective_service = objective_service
        self._spinning_disk_service = spinning_disk_service
        self._piezo_service = piezo_service
        self._objective_store = objective_store
        self._lock = threading.RLock()

        self._state = self._read_initial_state()

    @property
    def state(self) -> PeripheralsState:
        """Get current state."""
        return self._state

    def _read_initial_state(self) -> PeripheralsState:
        """Read initial state from hardware."""
        obj_pos = None
        obj_name = None
        pixel_size = None

        if self._objective_service:
            with self._lock:
                try:
                    obj_pos = self._objective_service.get_current_position()
                    info = self._objective_service.get_objective_info(obj_pos)
                except Exception:
                    info = None
                if info:
                    obj_name = info.name
                    pixel_size = getattr(info, "pixel_size_um", None)

        disk_state = None
        if self._spinning_disk_service:
            with self._lock:
                disk_state = SpinningDiskState(
                    is_available=self._spinning_disk_service.is_available(),
                    is_disk_in=self._spinning_disk_service.is_disk_in(),
                    is_spinning=self._spinning_disk_service.is_spinning(),
                    motor_speed=self._spinning_disk_service.motor_speed(),
                    dichroic=self._spinning_disk_service.current_dichroic(),
                    emission_filter=self._spinning_disk_service.current_emission_filter(),
                )

        piezo_pos = None
        if self._piezo_service:
            with self._lock:
                piezo_pos = getattr(self._piezo_service, "get_position", lambda: None)()

        return PeripheralsState(
            objective_position=obj_pos,
            objective_name=obj_name,
            pixel_size_um=pixel_size,
            spinning_disk=disk_state,
            piezo_position_um=piezo_pos,
        )

    # --- Objective ---

    @handles(SetObjectiveCommand)
    def _on_set_objective(self, cmd: SetObjectiveCommand) -> None:
        """Handle SetObjectiveCommand."""
        if not self._objective_service:
            return

        with self._lock:
            self._objective_service.set_position(cmd.position)
            try:
                actual = self._objective_service.get_current_position()
            except Exception:
                actual = cmd.position
            info = self._objective_service.get_objective_info(actual)

            obj_name = info.name if info else None
            pixel_size = info.pixel_size_um if info else None

            # Update state inside lock
            self._state = replace(
                self._state,
                objective_position=actual,
                objective_name=obj_name,
                pixel_size_um=pixel_size,
            )

            # Update objective store if available
            if self._objective_store and obj_name:
                try:
                    self._objective_store.set_current_objective(obj_name)
                except ValueError:
                    pass  # Objective name not in store

        # Publish outside lock
        self._event_bus.publish(
            ObjectiveChanged(
                position=actual,
                objective_name=obj_name,
                pixel_size_um=pixel_size,
            )
        )

        if pixel_size:
            self._event_bus.publish(PixelSizeChanged(pixel_size_um=pixel_size))

    # --- Spinning Disk ---

    @handles(SetSpinningDiskPositionCommand)
    def _on_set_disk_position(self, cmd: SetSpinningDiskPositionCommand) -> None:
        if not self._spinning_disk_service:
            return

        with self._lock:
            self._spinning_disk_service.set_disk_position(cmd.in_beam)
        self._update_disk_state()

    @handles(SetSpinningDiskSpinningCommand)
    def _on_set_spinning(self, cmd: SetSpinningDiskSpinningCommand) -> None:
        if not self._spinning_disk_service:
            return

        with self._lock:
            self._spinning_disk_service.set_spinning(cmd.spinning)
        self._update_disk_state()

    @handles(SetDiskDichroicCommand)
    def _on_set_dichroic(self, cmd: SetDiskDichroicCommand) -> None:
        if not self._spinning_disk_service:
            return

        with self._lock:
            self._spinning_disk_service.set_dichroic(cmd.position)
        self._update_disk_state()

    @handles(SetDiskEmissionFilterCommand)
    def _on_set_emission(self, cmd: SetDiskEmissionFilterCommand) -> None:
        if not self._spinning_disk_service:
            return

        with self._lock:
            self._spinning_disk_service.set_emission_filter(cmd.position)
        self._update_disk_state()

    def _update_disk_state(self) -> None:
        if not self._spinning_disk_service:
            return

        with self._lock:
            disk_state = SpinningDiskState(
                is_available=self._spinning_disk_service.is_available(),
                is_disk_in=self._spinning_disk_service.is_disk_in(),
                is_spinning=self._spinning_disk_service.is_spinning(),
                motor_speed=self._spinning_disk_service.motor_speed(),
                dichroic=self._spinning_disk_service.current_dichroic(),
                emission_filter=self._spinning_disk_service.current_emission_filter(),
            )
            # Update state inside lock
            self._state = replace(self._state, spinning_disk=disk_state)

        # Publish outside lock
        self._event_bus.publish(
            SpinningDiskStateChanged(
                is_disk_in=disk_state.is_disk_in,
                is_spinning=disk_state.is_spinning,
                motor_speed=disk_state.motor_speed,
                dichroic=disk_state.dichroic,
                emission_filter=disk_state.emission_filter,
            )
        )

    # --- Piezo ---
    # PiezoService is the single source of truth for piezo movement.
    # We only track state reactively from PiezoPositionChanged events.

    @handles(PiezoPositionChanged)
    def _on_piezo_position_changed(self, event: PiezoPositionChanged) -> None:
        with self._lock:
            self._state = replace(self._state, piezo_position_um=event.position_um)

    # --- Convenience methods ---

    def has_objective_changer(self) -> bool:
        return self._objective_service is not None

    def has_spinning_disk(self) -> bool:
        return self._spinning_disk_service is not None

    def has_piezo(self) -> bool:
        return self._piezo_service is not None
