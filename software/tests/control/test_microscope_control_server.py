"""
Unit tests for control/microscope_control_server.py

Tests for the _cmd_run_acquisition_from_yaml command.
"""

import pytest
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
    mock_microscope.channel_configuration_mananger.get_channel_configurations_for_objective.return_value = mock_channels

    mock_multipoint = MagicMock()
    mock_multipoint.acquisition_in_progress.return_value = False
    mock_multipoint.experiment_ID = "test_experiment"

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
        mock_server.microscope.channel_configuration_mananger.get_channel_configurations_for_objective.return_value = [
            mock_channel
        ]

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

        assert mock_server.multipoint_controller.do_autofocus is False
        assert mock_server.multipoint_controller.do_reflection_af is True

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

    def test_flexible_widget_type_rejected(self, mock_flexible_server, flexible_yaml_file):
        """Test that flexible widget YAML is rejected (TCP only supports wellplate)."""
        with pytest.raises(ValueError, match="TCP command only supports wellplate mode"):
            mock_flexible_server._cmd_run_acquisition_from_yaml(yaml_path=flexible_yaml_file)


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

        # Verify piezo was set to True (from YAML)
        assert mock_server.multipoint_controller.use_piezo is True


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

    def test_configure_controller_from_yaml(self, mock_server):
        """Test _configure_controller_from_yaml sets all parameters."""
        yaml_data = MagicMock()
        yaml_data.nz = 5
        yaml_data.delta_z_um = 10.0
        yaml_data.nt = 3
        yaml_data.delta_t_s = 60.0
        yaml_data.contrast_af = True
        yaml_data.laser_af = False
        yaml_data.use_piezo = True
        yaml_data.channel_names = ["Channel1"]

        mock_server._configure_controller_from_yaml(yaml_data)

        mock_server.multipoint_controller.set_NZ.assert_called_with(5)
        mock_server.multipoint_controller.set_deltaZ.assert_called_with(10.0)
        mock_server.multipoint_controller.set_Nt.assert_called_with(3)
        mock_server.multipoint_controller.set_deltat.assert_called_with(60.0)
        assert mock_server.multipoint_controller.do_autofocus is True
        assert mock_server.multipoint_controller.do_reflection_af is False
        assert mock_server.multipoint_controller.use_piezo is True
        mock_server.multipoint_controller.set_selected_configurations.assert_called_with(["Channel1"])
