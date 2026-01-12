"""Unit tests for TCP control server."""

import json
import os
import socket
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from squid.backend.services.tcp_control_server import (
    CommandResult,
    TCPControlServer,
    send_command,
    DEFAULT_PORT,
)
from squid.core.events import (
    AcquisitionFinished,
    AcquisitionStarted,
    EventBus,
)


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_success_result(self):
        """Test successful command result."""
        result = CommandResult(success=True, data={"key": "value"})
        assert result.success is True
        assert result.data == {"key": "value"}
        assert result.error is None

    def test_error_result(self):
        """Test error command result."""
        result = CommandResult(success=False, error="Something went wrong")
        assert result.success is False
        assert result.data is None
        assert result.error == "Something went wrong"


class TestTCPControlServerBasics:
    """Tests for TCPControlServer initialization and lifecycle."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies for the server."""
        event_bus = MagicMock(spec=EventBus)
        objective_store = MagicMock()
        objective_store.current_objective = "20x"
        objective_store.objective_pixel_size_um = 0.5
        camera = MagicMock()
        camera.get_binning.return_value = (2, 2)
        camera.get_frame_width.return_value = 2048
        camera.get_frame_height.return_value = 2048
        stage = MagicMock()
        stage.get_pos.return_value = MagicMock(z_mm=0.0)
        channel_config = MagicMock()
        channel_config.get_channel_configurations_for_objective.return_value = []

        return {
            "event_bus": event_bus,
            "objective_store": objective_store,
            "camera": camera,
            "stage": stage,
            "channel_config_manager": channel_config,
        }

    def test_server_initialization(self, mock_dependencies):
        """Test server initializes correctly."""
        server = TCPControlServer(**mock_dependencies, port=0)  # Port 0 = ephemeral
        assert server.is_running is False
        assert server._commands is not None
        assert "run_acquisition_from_yaml" in server._commands
        assert "get_status" in server._commands

    def test_server_start_stop(self, mock_dependencies):
        """Test server can start and stop."""
        server = TCPControlServer(**mock_dependencies, port=0)

        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        server._port = port
        server.start()

        try:
            assert server.is_running is True
            assert server.address == ("127.0.0.1", port)
        finally:
            server.stop()

        assert server.is_running is False

    def test_server_double_start(self, mock_dependencies):
        """Test starting server twice is safe."""
        server = TCPControlServer(**mock_dependencies, port=0)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        server._port = port
        server.start()
        server.start()  # Second start should be no-op

        try:
            assert server.is_running is True
        finally:
            server.stop()


class TestTCPControlServerCommands:
    """Tests for TCP server command handling."""

    @pytest.fixture
    def server_with_port(self, mock_dependencies):
        """Create a server with a specific port and start it."""
        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        # Create real EventBus for event testing
        event_bus = EventBus()
        event_bus.start()

        deps = {
            "event_bus": event_bus,
            "objective_store": mock_dependencies["objective_store"],
            "camera": mock_dependencies["camera"],
            "stage": mock_dependencies["stage"],
            "channel_config_manager": mock_dependencies["channel_config_manager"],
        }

        server = TCPControlServer(**deps, port=port)
        server.start()

        yield server, port, event_bus

        server.stop()
        event_bus.stop()

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies for the server."""
        objective_store = MagicMock()
        objective_store.current_objective = "20x"
        objective_store.objective_pixel_size_um = 0.5
        camera = MagicMock()
        camera.get_binning.return_value = (2, 2)
        camera.get_frame_width.return_value = 2048
        camera.get_frame_height.return_value = 2048
        stage = MagicMock()
        stage.get_pos.return_value = MagicMock(z_mm=0.0)
        channel_config = MagicMock()
        channel_configs = [MagicMock(name="DAPI"), MagicMock(name="GFP")]
        for cfg, name in zip(channel_configs, ["DAPI", "GFP"]):
            cfg.name = name
        channel_config.get_channel_configurations_for_objective.return_value = channel_configs

        return {
            "objective_store": objective_store,
            "camera": camera,
            "stage": stage,
            "channel_config_manager": channel_config,
        }

    def test_get_status_command(self, server_with_port):
        """Test get_status command."""
        server, port, _ = server_with_port

        result = send_command("127.0.0.1", port, "get_status")

        assert result["success"] is True
        assert "current_objective" in result

    def test_unknown_command(self, server_with_port):
        """Test unknown command returns error."""
        server, port, _ = server_with_port

        result = send_command("127.0.0.1", port, "unknown_command")

        assert result["success"] is False
        assert "Unknown command" in result["error"]
        assert "available_commands" in result

    def test_missing_command_field(self, server_with_port):
        """Test request without command field."""
        server, port, _ = server_with_port

        # Send raw request without command field
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        try:
            sock.connect(("127.0.0.1", port))
            sock.sendall(json.dumps({"param": "value"}).encode("utf-8"))

            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                try:
                    result = json.loads(data.decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue

            assert result["success"] is False
            assert "Missing 'command'" in result["error"]
        finally:
            sock.close()


class TestRunAcquisitionFromYAML:
    """Tests for run_acquisition_from_yaml command."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies for the server."""
        objective_store = MagicMock()
        objective_store.current_objective = "20x"
        objective_store.objective_pixel_size_um = 0.5
        camera = MagicMock()
        camera.get_binning.return_value = (2, 2)
        camera.get_frame_width.return_value = 2048
        camera.get_frame_height.return_value = 2048
        stage = MagicMock()
        stage.get_pos.return_value = MagicMock(z_mm=0.0)
        channel_config = MagicMock()
        channel_configs = [MagicMock(name="DAPI"), MagicMock(name="GFP")]
        for cfg, name in zip(channel_configs, ["DAPI", "GFP"]):
            cfg.name = name
        channel_config.get_channel_configurations_for_objective.return_value = channel_configs

        return {
            "objective_store": objective_store,
            "camera": camera,
            "stage": stage,
            "channel_config_manager": channel_config,
        }

    @pytest.fixture
    def yaml_file(self, tmp_path):
        """Create a test YAML file."""
        yaml_content = """
acquisition:
  widget_type: wellplate
  xy_mode: Select Wells

objective:
  name: 20x

z_stack:
  nz: 5
  delta_z_um: 2.0

time_series:
  nt: 1

channels:
  - name: DAPI
  - name: GFP

wellplate_scan:
  scan_size_mm: 1.5
  overlap_percent: 15.0
  regions:
    - name: A1
      center_mm: [10.0, 20.0, 0.5]
      shape: Square
"""
        yaml_path = tmp_path / "test_acquisition.yaml"
        yaml_path.write_text(yaml_content)
        return str(yaml_path)

    def test_missing_yaml_path(self, mock_dependencies):
        """Test command fails without yaml_path."""
        event_bus = EventBus()
        event_bus.start()

        try:
            server = TCPControlServer(event_bus=event_bus, **mock_dependencies, port=0)
            result = server._cmd_run_acquisition_from_yaml({})

            assert result.success is False
            assert "yaml_path" in result.error
        finally:
            event_bus.stop()

    def test_nonexistent_yaml_file(self, mock_dependencies):
        """Test command fails with nonexistent file."""
        event_bus = EventBus()
        event_bus.start()

        try:
            server = TCPControlServer(event_bus=event_bus, **mock_dependencies, port=0)
            result = server._cmd_run_acquisition_from_yaml({"yaml_path": "/nonexistent/file.yaml"})

            assert result.success is False
            assert "not found" in result.error
        finally:
            event_bus.stop()

    def test_flexible_mode_rejected(self, mock_dependencies, tmp_path):
        """Test that flexible mode is rejected."""
        event_bus = EventBus()
        event_bus.start()

        # Create flexible mode YAML
        yaml_content = """
acquisition:
  widget_type: flexible
"""
        yaml_path = tmp_path / "flexible.yaml"
        yaml_path.write_text(yaml_content)

        try:
            server = TCPControlServer(event_bus=event_bus, **mock_dependencies, port=0)
            result = server._cmd_run_acquisition_from_yaml({"yaml_path": str(yaml_path)})

            assert result.success is False
            assert "wellplate" in result.error.lower() or "flexible" in result.error.lower()
        finally:
            event_bus.stop()

    def test_hardware_validation_mismatch(self, mock_dependencies, yaml_file):
        """Test that hardware mismatch is detected."""
        event_bus = EventBus()
        event_bus.start()

        # Set wrong objective
        mock_dependencies["objective_store"].current_objective = "10x"

        try:
            server = TCPControlServer(event_bus=event_bus, **mock_dependencies, port=0)
            result = server._cmd_run_acquisition_from_yaml({"yaml_path": yaml_file})

            assert result.success is False
            assert "mismatch" in result.error.lower() or "objective" in result.error.lower()
        finally:
            event_bus.stop()

    def test_channel_validation(self, mock_dependencies, yaml_file):
        """Test that invalid channels are detected."""
        event_bus = EventBus()
        event_bus.start()

        # Set available channels to not include DAPI
        mock_dependencies["channel_config_manager"].get_channel_configurations_for_objective.return_value = [
            MagicMock(name="RFP")
        ]

        try:
            server = TCPControlServer(event_bus=event_bus, **mock_dependencies, port=0)
            result = server._cmd_run_acquisition_from_yaml({"yaml_path": yaml_file})

            assert result.success is False
            assert "channel" in result.error.lower() or "invalid" in result.error.lower()
        finally:
            event_bus.stop()


class TestSendCommand:
    """Tests for send_command helper function."""

    def test_connection_refused(self):
        """Test connection refused error."""
        with pytest.raises(ConnectionError):
            send_command("127.0.0.1", 59999, "test", timeout=1.0)

    def test_timeout(self):
        """Test connection timeout."""
        # Use a non-routable IP to trigger timeout
        with pytest.raises((TimeoutError, ConnectionError)):
            send_command("10.255.255.1", 5050, "test", timeout=0.5)
