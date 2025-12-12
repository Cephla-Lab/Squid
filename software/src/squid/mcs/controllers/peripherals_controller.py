"""Peripherals controller.

Handles simple peripheral hardware that doesn't need complex orchestration:
objective changer, spinning disk, piezo Z stage.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional

from squid.core.events import (
    SetObjectiveCommand,
    SetSpinningDiskPositionCommand,
    SetSpinningDiskSpinningCommand,
    SetDiskDichroicCommand,
    SetDiskEmissionFilterCommand,
    SetPiezoPositionCommand,
    MovePiezoRelativeCommand,
    ObjectiveChanged,
    SpinningDiskStateChanged,
    PiezoPositionChanged,
    PixelSizeChanged,
)

if TYPE_CHECKING:
    from squid.core.events import EventBus
    from squid.ops.navigation.objective_store import ObjectiveStore
    from squid.mcs.services.objective_changer_service import ObjectiveChangerService
    from squid.mcs.services.spinning_disk_service import SpinningDiskService
    from squid.mcs.services.piezo_service import PiezoService


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


class PeripheralsController:
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
        subscribe_to_bus: bool = True,
    ) -> None:
        self._objective_service = objective_service
        self._spinning_disk_service = spinning_disk_service
        self._piezo_service = piezo_service
        self._objective_store = objective_store
        self._bus = event_bus
        self._lock = threading.RLock()
        self._bus_enabled = subscribe_to_bus

        self._state = self._read_initial_state()

        # Subscribe to commands
        self._subscribe_to_bus()

    def _subscribe_to_bus(self) -> None:
        if not self._bus_enabled or self._bus is None:
            return
        if self._objective_service:
            self._bus.subscribe(SetObjectiveCommand, self._on_set_objective)
        if self._spinning_disk_service:
            self._bus.subscribe(SetSpinningDiskPositionCommand, self._on_set_disk_position)
            self._bus.subscribe(SetSpinningDiskSpinningCommand, self._on_set_spinning)
            self._bus.subscribe(SetDiskDichroicCommand, self._on_set_dichroic)
            self._bus.subscribe(SetDiskEmissionFilterCommand, self._on_set_emission)
        if self._piezo_service:
            self._bus.subscribe(SetPiezoPositionCommand, self._on_set_piezo)
            self._bus.subscribe(MovePiezoRelativeCommand, self._on_move_piezo_relative)

    def detach_event_bus_commands(self) -> None:
        """Unsubscribe command handlers for actor routing."""
        if self._bus is None:
            return
        self._bus_enabled = False
        try:
            self._bus.unsubscribe(SetObjectiveCommand, self._on_set_objective)
            self._bus.unsubscribe(SetSpinningDiskPositionCommand, self._on_set_disk_position)
            self._bus.unsubscribe(SetSpinningDiskSpinningCommand, self._on_set_spinning)
            self._bus.unsubscribe(SetDiskDichroicCommand, self._on_set_dichroic)
            self._bus.unsubscribe(SetDiskEmissionFilterCommand, self._on_set_emission)
            self._bus.unsubscribe(SetPiezoPositionCommand, self._on_set_piezo)
            self._bus.unsubscribe(MovePiezoRelativeCommand, self._on_move_piezo_relative)
        except Exception:
            pass

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
        self._bus.publish(
            ObjectiveChanged(
                position=actual,
                objective_name=obj_name,
                pixel_size_um=pixel_size,
            )
        )

        if pixel_size:
            self._bus.publish(PixelSizeChanged(pixel_size_um=pixel_size))

    # --- Spinning Disk ---

    def _on_set_disk_position(self, cmd: SetSpinningDiskPositionCommand) -> None:
        if not self._spinning_disk_service:
            return

        with self._lock:
            self._spinning_disk_service.set_disk_position(cmd.in_beam)
        self._update_disk_state()

    def _on_set_spinning(self, cmd: SetSpinningDiskSpinningCommand) -> None:
        if not self._spinning_disk_service:
            return

        with self._lock:
            self._spinning_disk_service.set_spinning(cmd.spinning)
        self._update_disk_state()

    def _on_set_dichroic(self, cmd: SetDiskDichroicCommand) -> None:
        if not self._spinning_disk_service:
            return

        with self._lock:
            self._spinning_disk_service.set_dichroic(cmd.position)
        self._update_disk_state()

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
        self._bus.publish(
            SpinningDiskStateChanged(
                is_disk_in=disk_state.is_disk_in,
                is_spinning=disk_state.is_spinning,
                motor_speed=disk_state.motor_speed,
                dichroic=disk_state.dichroic,
                emission_filter=disk_state.emission_filter,
            )
        )

    # --- Piezo ---

    def _on_set_piezo(self, cmd: SetPiezoPositionCommand) -> None:
        if not self._piezo_service:
            return

        with self._lock:
            min_pos, max_pos = self._piezo_service.get_range()
            clamped = max(min_pos, min(max_pos, cmd.position_um))
            self._piezo_service.move_to(clamped)
            actual = self._piezo_service.get_position()
            # Update state inside lock
            self._state = replace(self._state, piezo_position_um=actual)

        # Publish outside lock
        self._bus.publish(PiezoPositionChanged(position_um=actual))

    def _on_move_piezo_relative(self, cmd: MovePiezoRelativeCommand) -> None:
        if not self._piezo_service:
            return

        with self._lock:
            self._piezo_service.move_relative(cmd.delta_um)
            actual = self._piezo_service.get_position()
            # Update state inside lock
            self._state = replace(self._state, piezo_position_um=actual)

        # Publish outside lock
        self._bus.publish(PiezoPositionChanged(position_um=actual))

    # --- Convenience methods ---

    def has_objective_changer(self) -> bool:
        return self._objective_service is not None

    def has_spinning_disk(self) -> bool:
        return self._spinning_disk_service is not None

    def has_piezo(self) -> bool:
        return self._piezo_service is not None
