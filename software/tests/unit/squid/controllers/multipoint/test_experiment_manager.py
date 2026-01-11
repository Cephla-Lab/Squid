"""Unit tests for ExperimentManager."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from squid.backend.controllers.multipoint.experiment_manager import (
    ExperimentManager,
    ExperimentContext,
    build_acquisition_parameters,
)


@dataclass
class FakeChannelMode:
    """Fake ChannelMode for testing."""
    name: str = "Test Channel"
    exposure_time: float = 100.0


class FakeObjectiveStore:
    """Fake ObjectiveStore for testing."""
    current_objective: str = "10x"
    objectives_dict: Dict = None

    def __init__(self):
        self.objectives_dict = {
            "10x": {"magnification": 10, "NA": 0.3},
        }


class FakeChannelConfigManager:
    """Fake ChannelConfigurationManager for testing."""
    def __init__(self):
        self.write_calls: List = []

    def write_configuration_selected(
        self, objective: str, configs: List, path: str
    ) -> None:
        self.write_calls.append((objective, configs, path))


class FakeCameraService:
    """Fake CameraService for testing."""
    def get_pixel_size_binned_um(self) -> float:
        return 3.45


class TestExperimentManager:
    """Tests for ExperimentManager."""

    def test_start_experiment_creates_folder(self):
        """Test that start_experiment creates the experiment folder."""
        manager = ExperimentManager()

        with tempfile.TemporaryDirectory() as temp_dir:
            context = manager.start_experiment(
                base_path=temp_dir,
                experiment_name="test_experiment",
                configurations=[FakeChannelMode()],
                acquisition_params={"Nx": 1, "Ny": 1},
                objective_store=FakeObjectiveStore(),
                channel_config_manager=FakeChannelConfigManager(),
                camera_service=FakeCameraService(),
            )

            assert os.path.isdir(context.experiment_path)
            assert context.base_path == temp_dir
            assert "test_experiment" in context.experiment_id

    def test_start_experiment_generates_unique_id(self):
        """Test that experiment IDs are unique with timestamps."""
        manager = ExperimentManager()

        with tempfile.TemporaryDirectory() as temp_dir:
            context1 = manager.start_experiment(
                base_path=temp_dir,
                experiment_name="test",
                configurations=[],
                acquisition_params={},
                objective_store=FakeObjectiveStore(),
                channel_config_manager=FakeChannelConfigManager(),
                camera_service=FakeCameraService(),
            )

            context2 = manager.start_experiment(
                base_path=temp_dir,
                experiment_name="test",
                configurations=[],
                acquisition_params={},
                objective_store=FakeObjectiveStore(),
                channel_config_manager=FakeChannelConfigManager(),
                camera_service=FakeCameraService(),
            )

            assert context1.experiment_id != context2.experiment_id

    def test_start_experiment_sanitizes_name(self):
        """Test that spaces in experiment names are replaced with underscores."""
        manager = ExperimentManager()

        with tempfile.TemporaryDirectory() as temp_dir:
            context = manager.start_experiment(
                base_path=temp_dir,
                experiment_name="my test experiment",
                configurations=[],
                acquisition_params={},
                objective_store=FakeObjectiveStore(),
                channel_config_manager=FakeChannelConfigManager(),
                camera_service=FakeCameraService(),
            )

            assert " " not in context.experiment_id
            assert "my_test_experiment" in context.experiment_id

    def test_start_experiment_writes_configurations(self):
        """Test that channel configurations are written."""
        manager = ExperimentManager()
        config_manager = FakeChannelConfigManager()

        with tempfile.TemporaryDirectory() as temp_dir:
            configs = [FakeChannelMode(name="Channel1"), FakeChannelMode(name="Channel2")]

            manager.start_experiment(
                base_path=temp_dir,
                experiment_name="test",
                configurations=configs,
                acquisition_params={},
                objective_store=FakeObjectiveStore(),
                channel_config_manager=config_manager,
                camera_service=FakeCameraService(),
            )

            assert len(config_manager.write_calls) == 1
            objective, written_configs, path = config_manager.write_calls[0]
            assert objective == "10x"
            assert written_configs == configs
            assert path.endswith("configurations.xml")

    def test_start_experiment_writes_acquisition_parameters(self):
        """Test that acquisition parameters JSON is written."""
        manager = ExperimentManager()

        with tempfile.TemporaryDirectory() as temp_dir:
            params = {"Nx": 5, "Ny": 3, "Nz": 10}

            context = manager.start_experiment(
                base_path=temp_dir,
                experiment_name="test",
                configurations=[],
                acquisition_params=params,
                objective_store=FakeObjectiveStore(),
                channel_config_manager=FakeChannelConfigManager(),
                camera_service=FakeCameraService(),
            )

            params_path = os.path.join(context.experiment_path, "acquisition parameters.json")
            assert os.path.isfile(params_path)

            with open(params_path) as f:
                saved_params = json.load(f)

            assert saved_params["Nx"] == 5
            assert saved_params["Ny"] == 3
            assert saved_params["Nz"] == 10
            assert "objective" in saved_params
            assert "sensor_pixel_size_um" in saved_params

    def test_finalize_experiment_creates_done_marker(self):
        """Test that finalize_experiment creates a done marker file."""
        manager = ExperimentManager()

        with tempfile.TemporaryDirectory() as temp_dir:
            context = manager.start_experiment(
                base_path=temp_dir,
                experiment_name="test",
                configurations=[],
                acquisition_params={},
                objective_store=FakeObjectiveStore(),
                channel_config_manager=FakeChannelConfigManager(),
                camera_service=FakeCameraService(),
            )

            manager.finalize_experiment(context, create_done_marker=True)

            done_path = os.path.join(context.experiment_path, "done")
            assert os.path.isfile(done_path)

    def test_finalize_experiment_no_marker_when_disabled(self):
        """Test that done marker is not created when disabled."""
        manager = ExperimentManager()

        with tempfile.TemporaryDirectory() as temp_dir:
            context = manager.start_experiment(
                base_path=temp_dir,
                experiment_name="test",
                configurations=[],
                acquisition_params={},
                objective_store=FakeObjectiveStore(),
                channel_config_manager=FakeChannelConfigManager(),
                camera_service=FakeCameraService(),
            )

            manager.finalize_experiment(context, create_done_marker=False)

            done_path = os.path.join(context.experiment_path, "done")
            assert not os.path.exists(done_path)


class TestBuildAcquisitionParameters:
    """Tests for build_acquisition_parameters helper function."""

    def test_builds_correct_structure(self):
        """Test that the function builds the expected parameter structure."""
        params = build_acquisition_parameters(
            dx_mm=0.5,
            nx=10,
            dy_mm=0.5,
            ny=10,
            dz_um=1.0,
            nz=5,
            dt_s=60.0,
            nt=3,
            do_autofocus=True,
            do_reflection_af=False,
            use_manual_focus_map=True,
        )

        assert params["dx(mm)"] == 0.5
        assert params["Nx"] == 10
        assert params["dy(mm)"] == 0.5
        assert params["Ny"] == 10
        assert params["dz(um)"] == 1.0
        assert params["Nz"] == 5
        assert params["dt(s)"] == 60.0
        assert params["Nt"] == 3
        assert params["with AF"] is True
        assert params["with reflection AF"] is False
        assert params["with manual focus map"] is True

    def test_handles_zero_dz(self):
        """Test that zero dz is replaced with 1."""
        params = build_acquisition_parameters(
            dx_mm=0.5,
            nx=1,
            dy_mm=0.5,
            ny=1,
            dz_um=0,  # Zero
            nz=1,
            dt_s=0,
            nt=1,
            do_autofocus=False,
            do_reflection_af=False,
            use_manual_focus_map=False,
        )

        assert params["dz(um)"] == 1  # Should be 1, not 0


class TestExperimentContext:
    """Tests for ExperimentContext dataclass."""

    def test_context_holds_expected_fields(self):
        """Test that ExperimentContext has all expected fields."""
        context = ExperimentContext(
            experiment_id="test_2024-01-01_12-00-00",
            experiment_path="/data/test_2024-01-01_12-00-00",
            base_path="/data",
            start_time=1234567890.0,
            log_handler=None,
        )

        assert context.experiment_id == "test_2024-01-01_12-00-00"
        assert context.experiment_path == "/data/test_2024-01-01_12-00-00"
        assert context.base_path == "/data"
        assert context.start_time == 1234567890.0
        assert context.log_handler is None
