"""
Experiment folder and metadata management for multipoint acquisitions.

This module provides:
- ExperimentContext: Dataclass holding experiment state
- ExperimentManager: Handles folder creation, metadata writing, and logging setup

Extracted from MultiPointController to enable reuse by the orchestrator.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json
import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import squid.core.logging
import squid.core.utils.hardware_utils as utils

if TYPE_CHECKING:
    from squid.backend.managers import ObjectiveStore, ChannelConfigurationManager
    from squid.backend.services import CameraService

_log = squid.core.logging.get_logger(__name__)


@dataclass
class ExperimentContext:
    """
    Context for an active experiment.

    Holds all state needed to manage an experiment's folder structure,
    metadata, and lifecycle.
    """

    experiment_id: str
    base_path: str
    experiment_path: str
    created_at: datetime = field(default_factory=datetime.now)
    recording_start_time: float = 0.0

    # Acquisition parameters recorded at experiment start
    acquisition_params: Dict[str, Any] = field(default_factory=dict)

    @property
    def coordinates_path(self) -> str:
        """Path to coordinates CSV file."""
        return os.path.join(self.experiment_path, "coordinates.csv")

    @property
    def config_path(self) -> str:
        """Path to configurations XML file."""
        return os.path.join(self.experiment_path, "configurations.xml")

    @property
    def params_path(self) -> str:
        """Path to acquisition parameters JSON file."""
        return os.path.join(self.experiment_path, "acquisition parameters.json")


class ExperimentManager:
    """
    Manages experiment folder creation and metadata.

    Responsibilities:
    - Create unique experiment folders with timestamps
    - Write configuration files (XML, JSON)
    - Setup per-acquisition logging
    - Write final metadata on completion

    Usage:
        manager = ExperimentManager(
            objective_store=objective_store,
            channel_config_manager=channel_config_manager,
            camera_service=camera_service,
        )

        # Start a new experiment
        context = manager.start_experiment(
            base_path="/data/acquisitions",
            experiment_id="my_experiment",
            configurations=selected_configs,
            acquisition_params=params,
        )

        # Access experiment paths
        print(context.experiment_path)

        # Finalize (write done file)
        manager.finalize_experiment(context, success=True)
    """

    def __init__(
        self,
        objective_store: "ObjectiveStore",
        channel_config_manager: "ChannelConfigurationManager",
        camera_service: "CameraService",
        *,
        tube_lens_mm: float = 50.0,  # Default from _def.TUBE_LENS_MM
    ):
        """
        Initialize the experiment manager.

        Args:
            objective_store: ObjectiveStore for objective metadata
            channel_config_manager: ChannelConfigurationManager for config writing
            camera_service: CameraService for pixel size info
            tube_lens_mm: Tube lens focal length in mm
        """
        self._objective_store = objective_store
        self._channel_config_manager = channel_config_manager
        self._camera_service = camera_service
        self._tube_lens_mm = tube_lens_mm

    def start_experiment(
        self,
        base_path: str,
        experiment_id: str,
        configurations: List[Any],
        acquisition_params: Optional[Dict[str, Any]] = None,
    ) -> ExperimentContext:
        """
        Start a new experiment.

        Creates the experiment folder, writes configuration files,
        and returns an ExperimentContext for tracking.

        Args:
            base_path: Base directory for experiments
            experiment_id: User-provided experiment identifier
            configurations: List of selected channel configurations
            acquisition_params: Optional acquisition parameters dict

        Returns:
            ExperimentContext for the new experiment
        """
        import time

        # Generate unique ID with timestamp
        unique_id = (
            experiment_id.replace(" ", "_")
            + "_"
            + datetime.now().strftime("%Y-%m-%d_%H-%M-%S.%f")
        )

        experiment_path = os.path.join(base_path, unique_id)

        # Create experiment folder
        utils.ensure_directory_exists(experiment_path)
        _log.info(f"Created experiment folder: {experiment_path}")

        # Create context
        context = ExperimentContext(
            experiment_id=unique_id,
            base_path=base_path,
            experiment_path=experiment_path,
            recording_start_time=time.time(),
            acquisition_params=acquisition_params or {},
        )

        # Write configurations
        self._write_configurations(context, configurations)

        # Write acquisition parameters
        self._write_acquisition_params(context, acquisition_params or {})

        return context

    def finalize_experiment(
        self,
        context: ExperimentContext,
        success: bool = True,
    ) -> None:
        """
        Finalize an experiment.

        Writes a done file to indicate completion status.

        Args:
            context: ExperimentContext to finalize
            success: Whether experiment completed successfully
        """
        if success:
            utils.create_done_file(context.experiment_path)
            _log.info(f"Experiment completed: {context.experiment_id}")
        else:
            # Write failure marker
            failure_path = os.path.join(context.experiment_path, "FAILED")
            Path(failure_path).touch()
            _log.warning(f"Experiment failed: {context.experiment_id}")

    def create_round_subfolder(
        self,
        context: ExperimentContext,
        round_name: str,
    ) -> str:
        """
        Create a subfolder for a specific round within an experiment.

        Used by the orchestrator for multi-round experiments.

        Args:
            context: Parent experiment context
            round_name: Name/identifier for the round

        Returns:
            Path to the round subfolder
        """
        round_path = os.path.join(context.experiment_path, round_name.replace(" ", "_"))
        utils.ensure_directory_exists(round_path)
        _log.info(f"Created round folder: {round_path}")
        return round_path

    def _write_configurations(
        self,
        context: ExperimentContext,
        configurations: List[Any],
    ) -> None:
        """Write channel configurations XML file."""
        try:
            current_objective = self._objective_store.current_objective
            self._channel_config_manager.write_configuration_selected(
                current_objective,
                configurations,
                context.config_path,
            )
            _log.debug(f"Wrote configurations to {context.config_path}")
        except Exception as e:
            _log.warning(f"Failed to write configurations: {e}")

    def _write_acquisition_params(
        self,
        context: ExperimentContext,
        params: Dict[str, Any],
    ) -> None:
        """Write acquisition parameters JSON file."""
        try:
            # Add objective info
            output_params = dict(params)
            try:
                current_objective = self._objective_store.current_objective
                objective_info = self._objective_store.get_current_objective_info()
                output_params["objective"] = dict(objective_info)
                output_params["objective"]["name"] = current_objective
            except Exception:
                _log.debug("Could not attach objective metadata")

            # Add camera info
            output_params["sensor_pixel_size_um"] = (
                self._camera_service.get_pixel_size_binned_um()
            )
            output_params["tube_lens_mm"] = self._tube_lens_mm

            # Write JSON
            with open(context.params_path, "w") as f:
                json.dump(output_params, f, indent=2, default=str)

            _log.debug(f"Wrote acquisition params to {context.params_path}")

        except Exception as e:
            _log.warning(f"Failed to write acquisition parameters: {e}")

    def write_experiment_metadata(
        self,
        context: ExperimentContext,
        metadata: Dict[str, Any],
        filename: str = "experiment_metadata.json",
    ) -> None:
        """
        Write additional experiment metadata.

        Useful for orchestrator to record protocol info, round completion, etc.

        Args:
            context: ExperimentContext
            metadata: Metadata dict to write
            filename: Output filename
        """
        try:
            path = os.path.join(context.experiment_path, filename)
            with open(path, "w") as f:
                json.dump(metadata, f, indent=2, default=str)
            _log.debug(f"Wrote experiment metadata to {path}")
        except Exception as e:
            _log.warning(f"Failed to write experiment metadata: {e}")
