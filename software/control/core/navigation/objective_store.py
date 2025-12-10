from typing import Any, Dict, Optional

import control._def
from squid.events import EventBus, ObjectiveChanged


class ObjectiveStore:
    def __init__(
        self,
        objectives_dict: Dict[str, Dict[str, Any]] = control._def.OBJECTIVES,
        default_objective: str = control._def.DEFAULT_OBJECTIVE,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.objectives_dict: Dict[str, Dict[str, Any]] = objectives_dict
        self.default_objective: str = default_objective
        self.current_objective: str = default_objective
        self._event_bus = event_bus
        objective = self.objectives_dict[self.current_objective]
        self.pixel_size_factor: float = ObjectiveStore.calculate_pixel_size_factor(
            objective, control._def.TUBE_LENS_MM
        )

    def get_pixel_size_factor(self) -> float:
        return self.pixel_size_factor

    @staticmethod
    def calculate_pixel_size_factor(
        objective: Dict[str, Any], tube_lens_mm: float
    ) -> float:
        """pixel_size_um = sensor_pixel_size * binning_factor * lens_factor"""
        magnification: float = objective["magnification"]
        objective_tube_lens_mm: float = objective["tube_lens_f_mm"]
        lens_factor: float = objective_tube_lens_mm / magnification / tube_lens_mm
        return lens_factor

    def set_current_objective(self, objective_name: str) -> None:
        if objective_name in self.objectives_dict:
            self.current_objective = objective_name
            objective = self.objectives_dict[objective_name]
            self.pixel_size_factor = ObjectiveStore.calculate_pixel_size_factor(
                objective, control._def.TUBE_LENS_MM
            )
            # Publish event for widgets to react
            if self._event_bus is not None:
                self._event_bus.publish(
                    ObjectiveChanged(
                        position=0,  # Position not tracked here
                        objective_name=objective_name,
                        magnification=objective.get("magnification"),
                        pixel_size_um=self.pixel_size_factor,
                    )
                )
        else:
            raise ValueError(f"Objective {objective_name} not found in the store.")

    def get_current_objective_info(self) -> Dict[str, Any]:
        return self.objectives_dict[self.current_objective]
