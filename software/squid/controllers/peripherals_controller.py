"""Peripherals controller.

Handles simple peripheral hardware that doesn't need complex orchestration:
objective changer, spinning disk, piezo Z stage.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional

from squid.events import (
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
    from squid.abc import ObjectiveChanger, SpinningDiskController, PiezoStage
    from squid.events import EventBus
    from control.core.navigation.objective_store import ObjectiveStore


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
        objective_changer: Optional["ObjectiveChanger"],
        spinning_disk: Optional["SpinningDiskController"],
        piezo: Optional["PiezoStage"],
        objective_store: Optional["ObjectiveStore"],
        event_bus: "EventBus",
    ) -> None:
        self._objective_changer = objective_changer
        self._spinning_disk = spinning_disk
        self._piezo = piezo
        self._objective_store = objective_store
        self._bus = event_bus
        self._lock = threading.RLock()

        self._state = self._read_initial_state()

        # Subscribe to commands
        if objective_changer:
            self._bus.subscribe(SetObjectiveCommand, self._on_set_objective)

        if spinning_disk:
            self._bus.subscribe(SetSpinningDiskPositionCommand, self._on_set_disk_position)
            self._bus.subscribe(SetSpinningDiskSpinningCommand, self._on_set_spinning)
            self._bus.subscribe(SetDiskDichroicCommand, self._on_set_dichroic)
            self._bus.subscribe(SetDiskEmissionFilterCommand, self._on_set_emission)

        if piezo:
            self._bus.subscribe(SetPiezoPositionCommand, self._on_set_piezo)
            self._bus.subscribe(MovePiezoRelativeCommand, self._on_move_piezo_relative)

    @property
    def state(self) -> PeripheralsState:
        """Get current state."""
        return self._state

    def _read_initial_state(self) -> PeripheralsState:
        """Read initial state from hardware."""
        obj_pos = None
        obj_name = None
        pixel_size = None

        if self._objective_changer:
            with self._lock:
                try:
                    obj_pos = self._objective_changer.current_position
                    info = self._objective_changer.get_objective_info(obj_pos)
                except Exception:
                    info = None
                if info:
                    obj_name = info.name
                    pixel_size = getattr(info, "pixel_size_um", None)

        disk_state = None
        if self._spinning_disk:
            with self._lock:
                disk_state = SpinningDiskState(
                    is_available=True,
                    is_disk_in=getattr(self._spinning_disk, "is_disk_in", False),
                    is_spinning=getattr(self._spinning_disk, "is_spinning", False),
                    motor_speed=getattr(self._spinning_disk, "disk_motor_speed", 0),
                    dichroic=getattr(self._spinning_disk, "current_dichroic", 0),
                    emission_filter=getattr(
                        self._spinning_disk, "current_emission_filter", 0
                    ),
                )

        piezo_pos = None
        if self._piezo:
            with self._lock:
                piezo_pos = getattr(self._piezo, "position_um", None)

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
        if not self._objective_changer:
            return

        with self._lock:
            self._objective_changer.set_position(cmd.position)
            try:
                actual = self._objective_changer.current_position
            except Exception:
                actual = cmd.position
            info = self._objective_changer.get_objective_info(actual)

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
        if not self._spinning_disk:
            return

        with self._lock:
            self._spinning_disk.set_disk_position(cmd.in_beam)
        self._update_disk_state()

    def _on_set_spinning(self, cmd: SetSpinningDiskSpinningCommand) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            self._spinning_disk.set_spinning(cmd.spinning)
        self._update_disk_state()

    def _on_set_dichroic(self, cmd: SetDiskDichroicCommand) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            self._spinning_disk.set_dichroic(cmd.position)
        self._update_disk_state()

    def _on_set_emission(self, cmd: SetDiskEmissionFilterCommand) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            self._spinning_disk.set_emission_filter(cmd.position)
        self._update_disk_state()

    def _update_disk_state(self) -> None:
        if not self._spinning_disk:
            return

        with self._lock:
            disk_state = SpinningDiskState(
                is_available=True,
                is_disk_in=self._spinning_disk.is_disk_in,
                is_spinning=self._spinning_disk.is_spinning,
                motor_speed=self._spinning_disk.disk_motor_speed,
                dichroic=self._spinning_disk.current_dichroic,
                emission_filter=self._spinning_disk.current_emission_filter,
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
        if not self._piezo:
            return

        with self._lock:
            min_pos, max_pos = self._piezo.range_um
            clamped = max(min_pos, min(max_pos, cmd.position_um))
            self._piezo.move_to(clamped)
            actual = self._piezo.position_um
            # Update state inside lock
            self._state = replace(self._state, piezo_position_um=actual)

        # Publish outside lock
        self._bus.publish(PiezoPositionChanged(position_um=actual))

    def _on_move_piezo_relative(self, cmd: MovePiezoRelativeCommand) -> None:
        if not self._piezo:
            return

        with self._lock:
            self._piezo.move_relative(cmd.delta_um)
            actual = self._piezo.position_um
            # Update state inside lock
            self._state = replace(self._state, piezo_position_um=actual)

        # Publish outside lock
        self._bus.publish(PiezoPositionChanged(position_um=actual))

    # --- Convenience methods ---

    def has_objective_changer(self) -> bool:
        return self._objective_changer is not None

    def has_spinning_disk(self) -> bool:
        return self._spinning_disk is not None

    def has_piezo(self) -> bool:
        return self._piezo is not None
