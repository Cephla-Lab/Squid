import control._def


class ObjectiveStore:
    # Dry objectives reach NA 0.95 at most; from 1.0 an objective images through oil, water or silicone.
    IMMERSION_MIN_NA = 1.0

    def __init__(self, objectives_dict=control._def.OBJECTIVES, default_objective=control._def.DEFAULT_OBJECTIVE):
        self.objectives_dict = objectives_dict
        self.default_objective = default_objective
        self.current_objective = default_objective
        objective = self.objectives_dict[self.current_objective]
        self.pixel_size_factor = ObjectiveStore.calculate_pixel_size_factor(objective, control._def.TUBE_LENS_MM)

    def get_pixel_size_factor(self):
        return self.pixel_size_factor

    @staticmethod
    def calculate_pixel_size_factor(objective, tube_lens_mm):
        """pixel_size_um = sensor_pixel_size * binning_factor * lens_factor"""
        magnification = objective["magnification"]
        objective_tube_lens_mm = objective["tube_lens_f_mm"]
        lens_factor = objective_tube_lens_mm / magnification / tube_lens_mm
        return lens_factor

    def set_current_objective(self, objective_name):
        if objective_name in self.objectives_dict:
            self.current_objective = objective_name
            objective = self.objectives_dict[objective_name]
            self.pixel_size_factor = ObjectiveStore.calculate_pixel_size_factor(objective, control._def.TUBE_LENS_MM)
        else:
            raise ValueError(f"Objective {objective_name} not found in the store.")

    def get_current_objective_info(self):
        return self.objectives_dict[self.current_objective]

    @staticmethod
    def is_immersion(objective) -> bool:
        """True for an objective that images through an immersion liquid (oil, water or silicone): NA >= 1.0."""
        try:
            return float(objective.get("NA", 0)) >= ObjectiveStore.IMMERSION_MIN_NA
        except (AttributeError, TypeError, ValueError):
            return False
