"""
ExperimentManager - Experiment setup and metadata management.

This module encapsulates experiment folder creation, metadata writing, and logging
setup for multipoint acquisitions.

Extracted from MultiPointController to provide a focused component for experiment
lifecycle management.
"""

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import squid.core.logging
import squid.core.utils.hardware_utils as utils
import _def

if TYPE_CHECKING:
    from squid.backend.managers import ChannelConfigurationManager, ObjectiveStore
    from squid.backend.services import CameraService
    from squid.core.utils.config_utils import ChannelMode


_log = squid.core.logging.get_logger(__name__)


@dataclass
class ExperimentContext:
    """Context for a running experiment.

    Holds all the information needed to track an experiment's state,
    including paths, IDs, and logging handlers.
    """
    experiment_id: str
    experiment_path: str
    base_path: str
    start_time: float
    log_handler: Optional[logging.Handler] = None


class ExperimentManager:
    """
    Manages experiment setup, metadata, and logging.

    Responsibilities:
    - Create experiment folders with unique timestamped IDs
    - Write configuration files (configurations.xml)
    - Write acquisition parameters (acquisition parameters.json)
    - Manage per-acquisition logging
    - Create completion marker files

    Usage:
        manager = ExperimentManager()

        # Start a new experiment
        context = manager.start_experiment(
            base_path="/data/acquisitions",
            experiment_name="my_experiment",
            configurations=[...],
            acquisition_params={...},
            objective_store=objective_store,
            channel_config_manager=channel_manager,
            camera_service=camera_service,
        )

        # ... run acquisition ...

        # Finalize
        manager.finalize_experiment(context, create_done_marker=True)
    """

    def start_experiment(
        self,
        base_path: str,
        experiment_name: str,
        configurations: List["ChannelMode"],
        acquisition_params: Dict[str, Any],
        objective_store: "ObjectiveStore",
        channel_config_manager: "ChannelConfigurationManager",
        camera_service: "CameraService",
    ) -> ExperimentContext:
        """
        Start a new experiment by creating folder structure and writing metadata.

        Args:
            base_path: Root directory for experiments
            experiment_name: User-provided experiment name (will be timestamped)
            configurations: List of channel configurations for this experiment
            acquisition_params: Dictionary of acquisition parameters (NX, NY, NZ, etc.)
            objective_store: ObjectiveStore for objective metadata
            channel_config_manager: Manager for writing channel configurations
            camera_service: Camera service for sensor information

        Returns:
            ExperimentContext with experiment paths and start time
        """
        import time

        # Generate unique experiment ID with timestamp
        experiment_id = self._generate_experiment_id(experiment_name)
        experiment_path = os.path.join(base_path, experiment_id)
        start_time = time.time()

        # Create experiment folder
        utils.ensure_directory_exists(experiment_path)
        _log.info(f"Created experiment folder: {experiment_path}")

        # Write channel configurations
        self._write_configurations(
            experiment_path,
            configurations,
            objective_store,
            channel_config_manager,
        )

        # Write acquisition parameters
        self._write_acquisition_parameters(
            experiment_path,
            acquisition_params,
            objective_store,
            camera_service,
        )

        # Start logging
        log_handler = self._start_logging(experiment_path)

        return ExperimentContext(
            experiment_id=experiment_id,
            experiment_path=experiment_path,
            base_path=base_path,
            start_time=start_time,
            log_handler=log_handler,
        )

    def finalize_experiment(
        self,
        context: ExperimentContext,
        create_done_marker: bool = True,
    ) -> None:
        """
        Finalize an experiment by stopping logging and optionally creating a done marker.

        Args:
            context: The experiment context from start_experiment
            create_done_marker: Whether to create a 'done' marker file
        """
        # Stop logging
        self._stop_logging(context.log_handler)

        # Create done marker
        if create_done_marker:
            self._create_done_marker(context.experiment_path)

    def _generate_experiment_id(self, experiment_name: str) -> str:
        """Generate a unique experiment ID with timestamp."""
        sanitized_name = experiment_name.replace(" ", "_")
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        return f"{sanitized_name}_{timestamp}"

    def _write_configurations(
        self,
        experiment_path: str,
        configurations: List["ChannelMode"],
        objective_store: "ObjectiveStore",
        channel_config_manager: "ChannelConfigurationManager",
    ) -> None:
        """Write channel configurations to XML file."""
        config_path = os.path.join(experiment_path, "configurations.xml")
        try:
            channel_config_manager.write_configuration_selected(
                objective_store.current_objective,
                configurations,
                config_path,
            )
            _log.debug(f"Wrote configurations to {config_path}")
        except Exception:
            _log.exception("Failed to write configurations.xml")

    def _write_acquisition_parameters(
        self,
        experiment_path: str,
        acquisition_params: Dict[str, Any],
        objective_store: "ObjectiveStore",
        camera_service: "CameraService",
    ) -> None:
        """Write acquisition parameters to JSON file."""
        # Add objective information
        params = acquisition_params.copy()
        params["objective"] = self._get_objective_info(objective_store)

        # Add sensor information
        try:
            params["sensor_pixel_size_um"] = camera_service.get_pixel_size_binned_um()
        except Exception:
            _log.debug("Could not get sensor pixel size")

        params["tube_lens_mm"] = _def.TUBE_LENS_MM

        # Write to file
        params_path = os.path.join(experiment_path, "acquisition parameters.json")
        try:
            with open(params_path, "w") as f:
                json.dump(params, f, indent=2)
            _log.debug(f"Wrote acquisition parameters to {params_path}")
        except Exception:
            _log.exception("Failed to write acquisition parameters.json")

    def _get_objective_info(self, objective_store: "ObjectiveStore") -> Dict[str, Any]:
        """Get objective information for metadata."""
        try:
            current_objective = objective_store.current_objective
            objective_info = objective_store.objectives_dict.get(current_objective, {})
            result = dict(objective_info)
            result["name"] = current_objective
            return result
        except Exception:
            # Fallback to default objective
            try:
                objective_info = _def.OBJECTIVES[_def.DEFAULT_OBJECTIVE]
                result = dict(objective_info)
                result["name"] = _def.DEFAULT_OBJECTIVE
                return result
            except Exception:
                return {}

    def _start_logging(self, experiment_path: str) -> Optional[logging.Handler]:
        """Start per-acquisition logging if enabled."""
        if not _def.ENABLE_PER_ACQUISITION_LOG:
            return None

        log_path = os.path.join(experiment_path, "acquisition.log")
        try:
            handler = squid.core.logging.add_file_handler(
                log_path,
                replace_existing=True,
                level=squid.core.logging.py_logging.DEBUG,
            )
            _log.debug(f"Started per-acquisition logging to {log_path}")
            return handler
        except Exception:
            _log.exception("Failed to start per-acquisition logging")
            return None

    def _stop_logging(self, handler: Optional[logging.Handler]) -> None:
        """Stop per-acquisition logging."""
        if handler is None:
            return

        try:
            squid.core.logging.remove_handler(handler)
            _log.debug("Stopped per-acquisition logging")
        except Exception:
            _log.exception("Failed to stop per-acquisition logging")

    def _create_done_marker(self, experiment_path: str) -> None:
        """Create a 'done' marker file to indicate successful completion."""
        marker_path = os.path.join(experiment_path, "done")
        try:
            with open(marker_path, "w") as f:
                f.write(datetime.now().isoformat())
            _log.debug(f"Created done marker: {marker_path}")
        except Exception:
            _log.exception("Failed to create done marker")


# Convenience function for building acquisition parameters dict
def build_acquisition_parameters(
    *,
    dx_mm: float,
    nx: int,
    dy_mm: float,
    ny: int,
    dz_um: float,
    nz: int,
    dt_s: float,
    nt: int,
    do_autofocus: bool,
    do_reflection_af: bool,
    use_manual_focus_map: bool,
) -> Dict[str, Any]:
    """
    Build an acquisition parameters dictionary for metadata.

    This is a convenience function to ensure consistent parameter naming.

    Args:
        dx_mm: X step size in mm
        nx: Number of X positions
        dy_mm: Y step size in mm
        ny: Number of Y positions
        dz_um: Z step size in um
        nz: Number of Z positions
        dt_s: Time interval in seconds
        nt: Number of timepoints
        do_autofocus: Whether contrast autofocus is enabled
        do_reflection_af: Whether laser reflection autofocus is enabled
        use_manual_focus_map: Whether manual focus map is used

    Returns:
        Dictionary suitable for ExperimentManager.start_experiment()
    """
    return {
        "dx(mm)": dx_mm,
        "Nx": nx,
        "dy(mm)": dy_mm,
        "Ny": ny,
        "dz(um)": dz_um if dz_um != 0 else 1,
        "Nz": nz,
        "dt(s)": dt_s,
        "Nt": nt,
        "with AF": do_autofocus,
        "with reflection AF": do_reflection_af,
        "with manual focus map": use_manual_focus_map,
    }
