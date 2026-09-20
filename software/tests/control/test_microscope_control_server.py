"""
Unit tests for control/microscope_control_server.py

Tests for the _cmd_run_acquisition_from_yaml command.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def create_mock_channel(name: str) -> MagicMock:
    """Create a mock channel configuration."""
    mock = MagicMock()
    mock.name = name
    return mock


def create_mock_server(objective: str = "20x", channels: list = None):
    """Create a mock MicroscopeControlServer with configurable attributes."""
    from control.microscope_control_server import MicroscopeControlServer

    if channels is None:
        channels = ["BF LED matrix full", "Fluorescence 488 nm Ex"]

    mock_microscope = MagicMock()
    mock_microscope.objective_store.current_objective = objective
    mock_microscope.stage.get_pos.return_value = MagicMock(z_mm=1.0)
    mock_microscope.camera = MagicMock()
    mock_microscope.camera.get_binning.return_value = (1, 1)

    mock_channels = [create_mock_channel(name) for name in channels]
    mock_microscope.config_repo.get_merged_channels.return_value = mock_channels

    mock_multipoint = MagicMock()
    mock_multipoint.acquisition_in_progress.return_value = False
    mock_multipoint.experiment_ID = "test_experiment"
    # Pre-flight disk check inputs: a tiny acquisition that saves, with large acquisition mode off.
    mock_multipoint.get_estimated_acquisition_disk_storage.return_value = 1000
    mock_multipoint.get_acquisition_image_count.return_value = 12
    mock_multipoint.skip_saving = False
    mock_multipoint.large_acquisition_mode = False
    mock_multipoint.set_large_acquisition_mode.side_effect = lambda on: setattr(
        mock_multipoint, "large_acquisition_mode", on
    )
    mock_multipoint.set_skip_saving.side_effect = lambda on: setattr(mock_multipoint, "skip_saving", on)
    # What set_selected_configurations would have resolved for these channels
    mock_multipoint.selected_configurations = mock_channels

    mock_scan_coords = MagicMock()
    mock_scan_coords.region_fov_coordinates = {"B6": [(0, 0)], "B7": [(0, 0)]}

    with patch.object(MicroscopeControlServer, "__init__", lambda self: None):
        server = MicroscopeControlServer()
        server._log = MagicMock()
        server.microscope = mock_microscope
        server.multipoint_controller = mock_multipoint
        server.scan_coordinates = mock_scan_coords
        server.gui = None

    return server


SAMPLE_WELLPLATE_YAML = """
acquisition:
  widget_type: wellplate
  xy_mode: Select Wells
objective:
  name: 20x
  magnification: 20.0
  pixel_size_um: 0.188
  camera_binning:
    - 1
    - 1
z_stack:
  nz: 3
  delta_z_mm: 0.002
  use_piezo: true
time_series:
  nt: 2
  delta_t_s: 30.0
channels:
  - name: BF LED matrix full
  - name: Fluorescence 488 nm Ex
autofocus:
  contrast_af: false
  laser_af: true
sample:
  wellplate_format: 96 well plate
wellplate_scan:
  scan_size_mm: 2.0
  overlap_percent: 10.0
  regions:
    - name: B6
      center_mm: [56.31, 19.75, 1.2]
      shape: Square
    - name: B7
      center_mm: [65.31, 19.75, 1.2]
      shape: Square
"""


class TestRunAcquisitionFromYAML:
    """Tests for _cmd_run_acquisition_from_yaml command."""

    @pytest.fixture
    def sample_yaml_content(self):
        """Sample YAML content for testing."""
        return SAMPLE_WELLPLATE_YAML

    @pytest.fixture
    def yaml_file(self, tmp_path, sample_yaml_content):
        """Create a temporary YAML file for testing."""
        yaml_path = tmp_path / "test_acquisition.yaml"
        yaml_path.write_text(sample_yaml_content)
        return str(yaml_path)

    @pytest.fixture
    def mock_server(self):
        """Create a mock MicroscopeControlServer with necessary attributes."""
        return create_mock_server()

    def test_file_not_found(self, mock_server):
        """Test that FileNotFoundError is raised for missing YAML file."""
        with pytest.raises(FileNotFoundError, match="YAML file not found"):
            mock_server._cmd_run_acquisition_from_yaml(yaml_path="/nonexistent/path/acquisition.yaml")

    def test_parse_yaml_success(self, mock_server, yaml_file):
        """Test successful YAML parsing and acquisition start."""
        result = mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

        assert result["started"] is True
        assert result["widget_type"] == "wellplate"
        assert result["channels"] == ["BF LED matrix full", "Fluorescence 488 nm Ex"]
        assert result["nz"] == 3
        assert result["nt"] == 2
        assert "experiment_id" in result

    def test_acquisition_in_progress_error(self, mock_server, yaml_file):
        """Test error when acquisition is already running."""
        mock_server.multipoint_controller.acquisition_in_progress.return_value = True

        with pytest.raises(RuntimeError, match="Acquisition already in progress"):
            mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

    def test_hardware_mismatch_objective(self, mock_server, yaml_file):
        """Test error when objective doesn't match YAML."""
        mock_server.microscope.objective_store.current_objective = "10x"  # Different from YAML's 20x

        with pytest.raises(RuntimeError, match="Hardware configuration mismatch"):
            mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

    def test_hardware_mismatch_binning(self, mock_server, yaml_file):
        """Test error when camera binning doesn't match YAML."""
        mock_server.microscope.camera.get_binning.return_value = (2, 2)  # Different from YAML's (1,1)

        with pytest.raises(RuntimeError, match="Hardware configuration mismatch"):
            mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

    def test_invalid_channel_error(self, mock_server, yaml_file):
        """Test error when YAML specifies channels that don't exist."""
        # Only return one channel, so the second one will be invalid
        mock_channel = MagicMock()
        mock_channel.name = "BF LED matrix full"
        mock_server.microscope.config_repo.get_merged_channels.return_value = [mock_channel]

        with pytest.raises(ValueError, match="Invalid channels"):
            mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

    def test_wells_override(self, mock_server, yaml_file):
        """Test that wells parameter overrides YAML regions."""
        result = mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file, wells="A1:A2")

        assert result["started"] is True
        # Verify scan_coordinates.clear_regions was called
        mock_server.scan_coordinates.clear_regions.assert_called_once()
        # Verify add_region was called (for wellplate mode with wells override)
        assert mock_server.scan_coordinates.add_region.called

    def test_multipoint_controller_settings(self, mock_server, yaml_file):
        """Test that MultiPointController is configured correctly from YAML."""
        mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

        # Verify controller settings were applied
        mock_server.multipoint_controller.set_NZ.assert_called_with(3)
        mock_server.multipoint_controller.set_deltaZ.assert_called_with(2.0)  # 0.002 mm * 1000 = 2.0 um
        mock_server.multipoint_controller.set_Nt.assert_called_with(2)
        mock_server.multipoint_controller.set_deltat.assert_called_with(30.0)
        mock_server.multipoint_controller.set_selected_configurations.assert_called_with(
            ["BF LED matrix full", "Fluorescence 488 nm Ex"]
        )

    def test_autofocus_settings(self, mock_server, yaml_file):
        """Test that autofocus settings are applied from YAML."""
        mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

        mock_server.multipoint_controller.set_af_flag.assert_called_with(False)
        mock_server.multipoint_controller.set_reflection_af_flag.assert_called_with(True)

    def test_run_acquisition_called(self, mock_server, yaml_file):
        """Test that run_acquisition is called after configuration."""
        mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

        mock_server.multipoint_controller.run_acquisition.assert_called_once()

    def test_no_regions_error(self, mock_server, tmp_path):
        """Test error when YAML has no regions and no wells override."""
        yaml_content = """
acquisition:
  widget_type: wellplate
  xy_mode: Select Wells
objective:
  name: 20x
  camera_binning: [1, 1]
z_stack:
  nz: 1
  delta_z_mm: 0.001
time_series:
  nt: 1
channels:
  - name: BF LED matrix full
autofocus:
  contrast_af: false
  laser_af: false
wellplate_scan:
  overlap_percent: 10.0
"""
        yaml_path = tmp_path / "no_regions.yaml"
        yaml_path.write_text(yaml_content)

        # The ValueError is wrapped in RuntimeError by the exception handler
        with pytest.raises(RuntimeError, match="No wells or regions specified"):
            mock_server._cmd_run_acquisition_from_yaml(yaml_path=str(yaml_path))


class TestRunAcquisitionFromYAMLIntegration:
    """Integration-style tests that use more realistic mocking."""

    @pytest.fixture
    def flexible_yaml_content(self):
        """Sample flexible widget YAML content."""
        return """
acquisition:
  widget_type: flexible
  xy_mode: Manual
objective:
  name: 10x
  magnification: 10.0
  camera_binning: [1, 1]
z_stack:
  nz: 1
  delta_z_mm: 0.001
  use_piezo: false
time_series:
  nt: 1
  delta_t_s: 0.0
channels:
  - name: BF LED matrix full
autofocus:
  contrast_af: false
  laser_af: false
flexible_scan:
  nx: 2
  ny: 2
  delta_x_mm: 0.5
  delta_y_mm: 0.5
  overlap_percent: 10.0
  positions:
    - name: pos1
      center_mm: [10.0, 20.0, 1.0]
    - name: pos2
      center_mm: [15.0, 25.0, 1.0]
"""

    @pytest.fixture
    def flexible_yaml_file(self, tmp_path, flexible_yaml_content):
        """Create a temporary flexible YAML file for testing."""
        yaml_path = tmp_path / "test_flexible.yaml"
        yaml_path.write_text(flexible_yaml_content)
        return str(yaml_path)

    @pytest.fixture
    def mock_flexible_server(self):
        """Create a mock server for flexible widget tests."""
        return create_mock_server(objective="10x", channels=["BF LED matrix full"])

    def test_flexible_widget_type_is_accepted(self, mock_flexible_server, flexible_yaml_file):
        """Flexible YAMLs run through the shared settings function (positions -> add_flexible_region)."""
        result = mock_flexible_server._cmd_run_acquisition_from_yaml(yaml_path=flexible_yaml_file)

        assert result["started"] is True
        assert result["widget_type"] == "flexible"
        assert mock_flexible_server.scan_coordinates.add_flexible_region.called
        mock_flexible_server.multipoint_controller.set_widget_type.assert_called_with("flexible")


SIMPLE_WELLPLATE_YAML = """
acquisition:
  widget_type: wellplate
  xy_mode: Select Wells
objective:
  name: 20x
  camera_binning: [1, 1]
z_stack:
  nz: 1
  delta_z_mm: 0.001
  use_piezo: true
time_series:
  nt: 1
  delta_t_s: 0.0
channels:
  - name: BF LED matrix full
autofocus:
  contrast_af: false
  laser_af: false
wellplate_scan:
  scan_size_mm: 2.0
  overlap_percent: 10.0
  regions:
    - name: B6
      center_mm: [56.31, 19.75, 1.2]
"""


class TestRunAcquisitionFromYAMLOverrides:
    """Tests for parameter overrides in run_acquisition_from_yaml."""

    @pytest.fixture
    def yaml_file(self, tmp_path):
        """Create a temporary YAML file for testing."""
        yaml_path = tmp_path / "test_acquisition.yaml"
        yaml_path.write_text(SIMPLE_WELLPLATE_YAML)
        return str(yaml_path)

    @pytest.fixture
    def mock_server(self):
        """Create a mock MicroscopeControlServer."""
        server = create_mock_server(channels=["BF LED matrix full"])
        server.scan_coordinates.region_fov_coordinates = {"B6": [(0, 0)]}
        return server

    def test_experiment_id_override(self, mock_server, yaml_file):
        """Test that experiment_id parameter overrides auto-generated ID."""
        custom_id = "my_custom_experiment_123"
        result = mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file, experiment_id=custom_id)

        assert result["started"] is True
        mock_server.multipoint_controller.start_new_experiment.assert_called_with(custom_id)

    def test_base_path_override(self, mock_server, yaml_file):
        """Test that base_path parameter overrides default path."""
        custom_path = "/custom/save/path"
        result = mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file, base_path=custom_path)

        assert result["started"] is True
        mock_server.multipoint_controller.set_base_path.assert_called_with(custom_path)
        assert custom_path in result["save_dir"]

    def test_piezo_setting_applied(self, mock_server, yaml_file):
        """Test that use_piezo setting from YAML is applied to controller."""
        mock_server.multipoint_controller.use_piezo = False  # Initial value

        mock_server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file)

        # Verify piezo was set to True (from YAML) through the setter
        mock_server.multipoint_controller.set_use_piezo.assert_called_with(True)


class TestHelperMethods:
    """Tests for helper methods extracted from run_acquisition_from_yaml."""

    @pytest.fixture
    def mock_server(self):
        """Create a mock MicroscopeControlServer."""
        return create_mock_server(channels=["Channel1", "Channel2"])

    def test_validate_channels_success(self, mock_server):
        """Test _validate_channels returns available channels when all requested exist."""
        result = mock_server._validate_channels(["Channel1", "Channel2"], "20x")
        assert result == ["Channel1", "Channel2"]

    def test_validate_channels_invalid(self, mock_server):
        """Test _validate_channels raises ValueError for invalid channels."""
        with pytest.raises(ValueError, match="Invalid channels"):
            mock_server._validate_channels(["Channel1", "NonexistentChannel"], "20x")

    def test_update_gui_from_yaml_no_gui(self, mock_server):
        """Test _update_gui_from_yaml handles missing GUI gracefully."""
        yaml_data = MagicMock()
        yaml_data.widget_type = "wellplate"

        # Should not raise, just return early
        mock_server._update_gui_from_yaml(yaml_data, "/path/to/yaml")

    def test_apply_acquisition_settings_drives_every_setter(self, mock_server):
        from control.acquisition_yaml_loader import AcquisitionYAMLData
        from control.core.acquisition_settings import apply_acquisition_settings

        data = AcquisitionYAMLData(
            widget_type="wellplate",
            nz=5,
            delta_z_um=10.0,
            nt=3,
            delta_t_s=60.0,
            contrast_af=True,
            laser_af=False,
            use_piezo=True,
            channel_names=["Channel1"],
            wellplate_regions=[{"name": "A1", "center_mm": [1.0, 2.0, 3.0]}],
        )
        channel = MagicMock()
        channel.name = "Channel1"
        mock_server.multipoint_controller.selected_configurations = [channel]

        apply_acquisition_settings(
            mock_server.multipoint_controller, mock_server.scan_coordinates, mock_server.microscope, data
        )

        mpc = mock_server.multipoint_controller
        mpc.set_NZ.assert_called_with(5)
        mpc.set_deltaZ.assert_called_with(10.0)
        mpc.set_Nt.assert_called_with(3)
        mpc.set_deltat.assert_called_with(60.0)
        mpc.set_af_flag.assert_called_with(True)
        mpc.set_reflection_af_flag.assert_called_with(False)
        mpc.set_use_piezo.assert_called_with(True)
        mpc.set_focus_map.assert_called_with(None)
        mpc.set_region_laser_af_offsets.assert_called_with({})
        mpc.set_widget_type.assert_called_with("wellplate")
        mpc.set_selected_configurations.assert_called_with(["Channel1"])
        mock_server.scan_coordinates.clear_regions.assert_called_once()
        assert mock_server.scan_coordinates.add_region.called


def create_pause_capable_controller(
    in_progress: bool = True,
    paused: bool = False,
    reasons: tuple = (),
    since: float = None,
    paused_total_s: float = 0.0,
    request_result: bool = True,
    disk_status=None,
):
    """Mock MultiPointController implementing the large-acquisition pause contract.

    The controller itself is written by another engineer; this mirrors the agreed API:
    request_pause()/request_resume() -> bool, pause_state -> Optional[PauseState],
    disk_status -> Optional[DiskStatus].
    """
    from control.core.pause_gate import PauseState

    controller = MagicMock()
    controller.acquisition_in_progress.return_value = in_progress
    controller.request_pause.return_value = request_result
    controller.request_resume.return_value = request_result
    controller.pause_state = PauseState(
        paused=paused, reasons=tuple(reasons), since=since, paused_total_s=paused_total_s
    )
    controller.disk_status = disk_status
    controller.multiPointWorker = None
    controller.experiment_ID = "exp1"
    controller.base_path = "/data"
    return controller


def make_disk_status(holding: bool = False):
    """Stand-in for control.core.disk_space.DiskStatus (module written concurrently)."""
    return SimpleNamespace(
        free_bytes=10_000_000,
        required_bytes=50_000_000,
        reserve_bytes=5_000_000,
        pending_bytes=1_000_000,
        fov_bytes=2_000_000,
        holding=holding,
    )


def server_with_controller(controller):
    server = create_mock_server()
    server.multipoint_controller = controller
    return server


class TestPauseAcquisition:
    """Tests for the _cmd_pause_acquisition command."""

    def test_no_acquisition_in_progress(self):
        server = server_with_controller(create_pause_capable_controller(in_progress=False))

        with pytest.raises(RuntimeError, match="No acquisition in progress"):
            server._cmd_pause_acquisition()

        server.multipoint_controller.request_pause.assert_not_called()

    def test_large_acquisition_mode_disabled(self):
        server = server_with_controller(create_pause_capable_controller(request_result=False))

        with pytest.raises(RuntimeError, match="Large acquisition mode is not enabled for this acquisition"):
            server._cmd_pause_acquisition()

    def test_pause_requested(self):
        server = server_with_controller(
            create_pause_capable_controller(paused=True, reasons=("operator",), since=1234.5)
        )

        result = server._cmd_pause_acquisition()

        server.multipoint_controller.request_pause.assert_called_once_with()
        assert result == {"paused": True, "pause_reasons": ["operator"], "paused_since": 1234.5}

    def test_controller_missing(self):
        server = server_with_controller(None)

        with pytest.raises(RuntimeError, match="MultiPointController not available"):
            server._cmd_pause_acquisition()


class TestResumeAcquisition:
    """Tests for the _cmd_resume_acquisition command."""

    def test_no_acquisition_in_progress(self):
        server = server_with_controller(create_pause_capable_controller(in_progress=False))

        with pytest.raises(RuntimeError, match="No acquisition in progress"):
            server._cmd_resume_acquisition()

        server.multipoint_controller.request_resume.assert_not_called()

    def test_large_acquisition_mode_disabled(self):
        server = server_with_controller(create_pause_capable_controller(request_result=False))

        with pytest.raises(RuntimeError, match="Large acquisition mode is not enabled for this acquisition"):
            server._cmd_resume_acquisition()

    def test_resume_releases_operator_hold(self):
        server = server_with_controller(create_pause_capable_controller(paused=False))

        result = server._cmd_resume_acquisition()

        server.multipoint_controller.request_resume.assert_called_once_with()
        assert result == {"paused": False, "pause_reasons": [], "paused_since": None}

    def test_resume_still_held_by_disk_guard(self):
        """A resume that does not actually resume must say so, with the remaining holders."""
        server = server_with_controller(
            create_pause_capable_controller(paused=True, reasons=("disk_space",), since=99.0)
        )

        result = server._cmd_resume_acquisition()

        assert result["paused"] is True
        assert result["pause_reasons"] == ["disk_space"]
        assert result["paused_since"] == 99.0

    def test_controller_missing(self):
        server = server_with_controller(None)

        with pytest.raises(RuntimeError, match="MultiPointController not available"):
            server._cmd_resume_acquisition()


class TestAcquisitionStatusWithPause:
    """Tests for the pause/disk additions to _cmd_get_acquisition_status."""

    def test_status_unchanged_when_mode_off(self):
        """Acquisitions without pause support keep exactly the payload they had before."""
        controller = create_pause_capable_controller()
        controller.pause_state = None
        controller.disk_status = None
        server = server_with_controller(controller)

        result = server._cmd_get_acquisition_status()

        assert result == {
            "in_progress": True,
            "status": "running",
            "experiment_id": "exp1",
            "base_path": "/data",
        }
        for key in ("pause_reasons", "paused_since", "paused_total_s", "disk"):
            assert key not in result

    def test_status_running_with_pause_support(self):
        server = server_with_controller(create_pause_capable_controller(paused=False, paused_total_s=12.5))

        result = server._cmd_get_acquisition_status()

        assert result["status"] == "running"
        assert result["pause_reasons"] == []
        assert result["paused_since"] is None
        assert result["paused_total_s"] == 12.5

    def test_status_paused(self):
        server = server_with_controller(
            create_pause_capable_controller(
                paused=True, reasons=("disk_space", "operator"), since=555.0, paused_total_s=60.0
            )
        )

        result = server._cmd_get_acquisition_status()

        assert result["status"] == "paused"
        assert result["in_progress"] is True
        assert result["pause_reasons"] == ["disk_space", "operator"]
        assert result["paused_since"] == 555.0
        assert result["paused_total_s"] == 60.0

    def test_status_includes_disk_block(self):
        server = server_with_controller(
            create_pause_capable_controller(paused=True, reasons=("disk_space",), disk_status=make_disk_status(True))
        )

        result = server._cmd_get_acquisition_status()

        assert result["disk"] == {
            "free_bytes": 10_000_000,
            "required_bytes": 50_000_000,
            "reserve_bytes": 5_000_000,
            "pending_bytes": 1_000_000,
            "fov_bytes": 2_000_000,
            "holding": True,
        }

    def test_status_without_disk_guard_has_no_disk_block(self):
        server = server_with_controller(create_pause_capable_controller(disk_status=None))

        result = server._cmd_get_acquisition_status()

        assert "disk" not in result


class TestPauseCommandDiscovery:
    """The new commands must be auto-registered and documented for MCP consumers."""

    @pytest.fixture
    def real_server(self):
        from control.microscope_control_server import MicroscopeControlServer

        return MicroscopeControlServer(microscope=MagicMock(), multipoint_controller=MagicMock())

    def test_commands_registered(self, real_server):
        assert "pause_acquisition" in real_server._commands
        assert "resume_acquisition" in real_server._commands

    def test_schemas_document_the_commands(self, real_server):
        schemas = real_server._cmd_get_schemas()["schemas"]

        for name in ("pause_acquisition", "resume_acquisition"):
            assert name in schemas
            assert schemas[name]["parameters"] == {}
            assert schemas[name]["required"] == []
            assert len(schemas[name]["description"]) > 20


class TestTcpDiskPreflight:
    """Both TCP run commands refuse an acquisition the save disk cannot hold unless large acquisition mode is
    on, mirroring the GUI's "Not Enough Disk Space" check (which a TCP client never sees)."""

    @pytest.fixture
    def yaml_file(self, tmp_path):
        path = tmp_path / "acq.yaml"
        path.write_text(SAMPLE_WELLPLATE_YAML)
        return str(path)

    @pytest.fixture
    def opt_in_yaml_file(self, tmp_path):
        path = tmp_path / "acq_large.yaml"
        path.write_text(
            SAMPLE_WELLPLATE_YAML.replace("acquisition:\n", "acquisition:\n  large_acquisition_mode: true\n", 1)
        )
        assert "large_acquisition_mode: true" in path.read_text()
        return str(path)

    @pytest.fixture
    def tiny_disk(self, monkeypatch):
        import control._def
        import control.utils

        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", False)
        monkeypatch.setattr(control.utils, "get_available_disk_space", lambda d: 500)  # the mock run needs 1030

    def test_yaml_run_that_does_not_fit_is_refused_before_anything_is_created(self, tiny_disk, yaml_file, tmp_path):
        server = create_mock_server()
        with pytest.raises(RuntimeError) as err:
            server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file, base_path=str(tmp_path))
        message = str(err.value)
        assert "large_acquisition_mode: true" in message, "the error must name the opt-in"
        assert "12 images" in message and str(tmp_path) in message
        server.multipoint_controller.start_new_experiment.assert_not_called()
        server.multipoint_controller.run_acquisition.assert_not_called()

    def test_yaml_opt_in_lets_the_run_start(self, tiny_disk, opt_in_yaml_file, tmp_path):
        server = create_mock_server()
        result = server._cmd_run_acquisition_from_yaml(yaml_path=opt_in_yaml_file, base_path=str(tmp_path))
        assert result["started"] is True
        server.multipoint_controller.run_acquisition.assert_called_once()

    def test_global_setting_lets_the_run_start(self, tiny_disk, yaml_file, tmp_path, monkeypatch):
        import control._def

        monkeypatch.setattr(control._def, "LARGE_ACQUISITION_MODE", True)
        server = create_mock_server()
        assert server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file, base_path=str(tmp_path))["started"] is True

    def test_runs_that_save_nothing_are_not_checked(self, tiny_disk, tmp_path):
        path = tmp_path / "acq_skip.yaml"
        path.write_text(SAMPLE_WELLPLATE_YAML.replace("acquisition:\n", "acquisition:\n  skip_saving: true\n", 1))
        server = create_mock_server()
        assert server._cmd_run_acquisition_from_yaml(yaml_path=str(path), base_path=str(tmp_path))["started"] is True

    def test_a_run_that_fits_starts_as_before(self, yaml_file, tmp_path, monkeypatch):
        import control.utils

        monkeypatch.setattr(control.utils, "get_available_disk_space", lambda d: 10**9)
        server = create_mock_server()
        assert server._cmd_run_acquisition_from_yaml(yaml_path=yaml_file, base_path=str(tmp_path))["started"] is True

    def test_the_wells_command_is_refused_too_and_names_the_setting(self, tiny_disk, tmp_path):
        server = create_mock_server()
        # This command validates channels against the live controller rather than the config repo.
        server.microscope.live_controller.get_channels.return_value = [create_mock_channel("BF LED matrix full")]
        with pytest.raises(RuntimeError) as err:
            server._cmd_run_acquisition(wells="B6", channels=["BF LED matrix full"], base_path=str(tmp_path))
        assert "Large Acquisitions" in str(err.value)
        server.multipoint_controller.run_acquisition.assert_not_called()
