"""TCP Control Server for Squid Microscope.

A TCP socket server that accepts JSON commands and executes them via EventBus.
This follows the arch_v2 pattern of decoupled communication - the server
never directly manipulates widgets or hardware, only publishes commands
and listens for events.

Usage:
    from squid.backend.services.tcp_control_server import TCPControlServer

    server = TCPControlServer(event_bus, scan_coordinates, ...)
    server.start()
    # ...
    server.stop()
"""

import json
import os
import socket
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

import squid.core.logging

from squid.backend.io.acquisition_yaml import (
    AcquisitionYAMLData,
    parse_acquisition_yaml,
    validate_hardware,
)
from squid.backend.managers.scan_coordinates.grid import (
    GridConfig,
    generate_circular_grid,
    generate_square_grid,
)
from squid.core.events import (
    AutofocusMode,
    AcquisitionFinished,
    AcquisitionStarted,
    ClearScanCoordinatesCommand,
    EventBus,
    FocusLockSettings,
    LoadScanCoordinatesCommand,
    SetAcquisitionChannelsCommand,
    SetAcquisitionParametersCommand,
    SetAcquisitionPathCommand,
    StartAcquisitionCommand,
    StartNewExperimentCommand,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050
MAX_BUFFER_SIZE = 1 * 1024 * 1024  # 1 MB
MAX_CONNECTIONS = 5


class AcquisitionResult(TypedDict):
    """Result from run_acquisition_from_yaml command."""

    started: bool
    yaml_path: str
    widget_type: str
    region_count: int
    channels: List[str]
    nz: int
    nt: int
    total_fovs: int
    total_images: int
    experiment_id: str
    save_dir: str


@dataclass
class CommandResult:
    """Generic result wrapper for TCP commands."""

    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class TCPControlServer:
    """TCP server for remote microscope control via EventBus.

    This server follows arch_v2's decoupled communication pattern:
    - Commands are received as JSON over TCP
    - Execution happens via EventBus publish/subscribe
    - No direct widget or hardware manipulation

    Thread Safety:
    - Server runs in its own thread
    - Client connections handled in separate threads
    - EventBus ensures thread-safe command dispatch
    """

    def __init__(
        self,
        event_bus: EventBus,
        objective_store: Any,
        camera: Any,
        stage: Any,
        channel_config_manager: Any,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ):
        """Initialize the TCP control server.

        Args:
            event_bus: The application's EventBus for command dispatch
            objective_store: ObjectiveStore for hardware validation
            camera: Camera instance for FOV calculations
            stage: Stage instance for current position
            channel_config_manager: ChannelConfigService for channel validation
            host: Server host address
            port: Server port
        """
        self._log = squid.core.logging.get_logger(self.__class__.__name__)
        self._event_bus = event_bus
        self._objective_store = objective_store
        self._camera = camera
        self._stage = stage
        self._channel_config_manager = channel_config_manager
        self._host = host
        self._port = port

        self._server_socket: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.RLock()

        # For waiting on acquisition events
        self._acquisition_started_event = threading.Event()
        self._acquisition_finished_event = threading.Event()
        self._last_acquisition_started: Optional[AcquisitionStarted] = None
        self._last_acquisition_finished: Optional[AcquisitionFinished] = None

        # Command registry
        self._commands: Dict[str, Callable[[Dict[str, Any]], CommandResult]] = {
            "run_acquisition_from_yaml": self._cmd_run_acquisition_from_yaml,
            "get_status": self._cmd_get_status,
            "stop_acquisition": self._cmd_stop_acquisition,
        }

        # Subscribe to acquisition events
        self._event_bus.subscribe(AcquisitionStarted, self._on_acquisition_started)
        self._event_bus.subscribe(AcquisitionFinished, self._on_acquisition_finished)

    def start(self) -> None:
        """Start the TCP server in a background thread."""
        with self._lock:
            if self._running:
                self._log.warning("Server already running")
                return

            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            try:
                self._server_socket.bind((self._host, self._port))
                self._server_socket.listen(MAX_CONNECTIONS)
                self._server_socket.settimeout(1.0)  # Allow periodic shutdown checks
                self._running = True

                self._server_thread = threading.Thread(
                    target=self._server_loop,
                    name="TCPControlServer",
                    daemon=True,
                )
                self._server_thread.start()
                self._log.info(f"TCP control server started on {self._host}:{self._port}")

            except OSError as e:
                self._log.error(f"Failed to start server: {e}")
                if self._server_socket:
                    self._server_socket.close()
                    self._server_socket = None
                raise

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the TCP server gracefully."""
        with self._lock:
            if not self._running:
                return

            self._running = False

            if self._server_socket:
                try:
                    self._server_socket.close()
                except Exception:
                    pass
                self._server_socket = None

        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=timeout)
            if self._server_thread.is_alive():
                self._log.warning("Server thread did not stop cleanly")

        self._server_thread = None
        self._log.info("TCP control server stopped")

    @property
    def is_running(self) -> bool:
        """Return whether the server is running."""
        return self._running

    @property
    def address(self) -> Tuple[str, int]:
        """Return the server address (host, port)."""
        return (self._host, self._port)

    def _server_loop(self) -> None:
        """Main server loop - accepts connections and spawns handler threads."""
        while self._running:
            try:
                client_socket, client_address = self._server_socket.accept()
                self._log.debug(f"Connection from {client_address}")

                handler_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, client_address),
                    daemon=True,
                )
                handler_thread.start()

            except socket.timeout:
                continue  # Allow checking _running flag
            except OSError:
                if self._running:
                    self._log.error("Server socket error")
                break

    def _handle_client(
        self, client_socket: socket.socket, client_address: Tuple[str, int]
    ) -> None:
        """Handle a single client connection."""
        try:
            client_socket.settimeout(30.0)
            data = b""

            while True:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_BUFFER_SIZE:
                    self._send_error(client_socket, "Request too large")
                    return
                # Check for complete JSON object
                try:
                    json.loads(data.decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue

            if not data:
                return

            try:
                request = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError as e:
                self._send_error(client_socket, f"Invalid JSON: {e}")
                return

            # Process command
            result = self._process_command(request)
            self._send_response(client_socket, result)

        except socket.timeout:
            self._log.warning(f"Client {client_address} timed out")
        except Exception as e:
            self._log.error(f"Error handling client: {e}")
            self._log.debug(traceback.format_exc())
        finally:
            try:
                client_socket.close()
            except Exception:
                pass

    def _process_command(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a command request and return the response."""
        command = request.get("command")
        if not command:
            return {"success": False, "error": "Missing 'command' field"}

        handler = self._commands.get(command)
        if not handler:
            return {
                "success": False,
                "error": f"Unknown command: {command}",
                "available_commands": list(self._commands.keys()),
            }

        try:
            # Extract parameters (everything except 'command')
            params = {k: v for k, v in request.items() if k != "command"}
            result = handler(params)

            if result.success:
                return {"success": True, **(result.data or {})}
            else:
                return {"success": False, "error": result.error}

        except Exception as e:
            self._log.error(f"Command '{command}' failed: {e}")
            self._log.debug(traceback.format_exc())
            return {"success": False, "error": str(e)}

    def _send_response(self, client_socket: socket.socket, response: Dict[str, Any]) -> None:
        """Send a JSON response to the client."""
        try:
            data = json.dumps(response).encode("utf-8")
            client_socket.sendall(data)
        except Exception as e:
            self._log.error(f"Failed to send response: {e}")

    def _send_error(self, client_socket: socket.socket, error: str) -> None:
        """Send an error response to the client."""
        self._send_response(client_socket, {"success": False, "error": error})

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _on_acquisition_started(self, event: AcquisitionStarted) -> None:
        """Handle AcquisitionStarted event."""
        self._last_acquisition_started = event
        self._acquisition_started_event.set()

    def _on_acquisition_finished(self, event: AcquisitionFinished) -> None:
        """Handle AcquisitionFinished event."""
        self._last_acquisition_finished = event
        self._acquisition_finished_event.set()

    # =========================================================================
    # Command Handlers
    # =========================================================================

    def _cmd_get_status(self, params: Dict[str, Any]) -> CommandResult:
        """Get current server and acquisition status."""
        return CommandResult(
            success=True,
            data={
                "server_running": self._running,
                "current_objective": self._objective_store.current_objective,
            },
        )

    def _cmd_stop_acquisition(self, params: Dict[str, Any]) -> CommandResult:
        """Stop the current acquisition."""
        from squid.core.events import StopAcquisitionCommand

        self._event_bus.publish(StopAcquisitionCommand())
        return CommandResult(success=True, data={"message": "Stop command sent"})

    def _cmd_run_acquisition_from_yaml(self, params: Dict[str, Any]) -> CommandResult:
        """Run acquisition from a YAML configuration file.

        This command:
        1. Parses the YAML file
        2. Validates hardware configuration
        3. Generates FOV coordinates from regions
        4. Publishes EventBus commands to configure and start acquisition
        5. Returns the acquisition result

        Args:
            params: Must contain 'yaml_path'. Optional: 'experiment_id', 'base_path', 'wells'

        Returns:
            AcquisitionResult with acquisition details
        """
        yaml_path = params.get("yaml_path")
        if not yaml_path:
            return CommandResult(success=False, error="Missing 'yaml_path' parameter")

        if not os.path.exists(yaml_path):
            return CommandResult(success=False, error=f"YAML file not found: {yaml_path}")

        # Parse YAML
        try:
            yaml_data = parse_acquisition_yaml(yaml_path)
        except Exception as e:
            return CommandResult(success=False, error=f"Failed to parse YAML: {e}")

        # Only wellplate mode supported via TCP (flexible requires GUI interaction)
        if yaml_data.widget_type != "wellplate":
            return CommandResult(
                success=False,
                error=(
                    f"TCP command only supports wellplate mode acquisitions. "
                    f"Got widget_type='{yaml_data.widget_type}'. "
                    f"FlexibleMultiPoint acquisitions must be run from the GUI."
                ),
            )

        # Validate hardware
        try:
            current_binning = None
            if self._camera and hasattr(self._camera, "get_binning"):
                current_binning = tuple(self._camera.get_binning())

            current_objective = self._objective_store.current_objective
            validation = validate_hardware(yaml_data, current_objective, current_binning)

            if not validation.is_valid:
                return CommandResult(
                    success=False,
                    error=f"Hardware configuration mismatch:\n{validation.message}",
                )
        except Exception as e:
            self._log.warning(f"Hardware validation error (continuing): {e}")

        # Validate channels exist
        try:
            channel_configs = self._channel_config_manager.get_configurations(
                self._objective_store.current_objective
            )
            available_channels = [ch.name for ch in channel_configs] if channel_configs else []
            invalid_channels = [ch for ch in yaml_data.channel_names if ch not in available_channels]

            if invalid_channels:
                return CommandResult(
                    success=False,
                    error=f"Invalid channels: {invalid_channels}. Available: {available_channels}",
                )
        except Exception as e:
            self._log.warning(f"Channel validation error (continuing): {e}")

        # Generate FOV coordinates from YAML regions
        try:
            region_fov_coordinates, region_centers = self._generate_fov_coordinates(yaml_data)
        except Exception as e:
            return CommandResult(success=False, error=f"Failed to generate FOV coordinates: {e}")

        # Determine paths
        base_path = params.get("base_path")
        if not base_path:
            import _def

            base_path = getattr(_def, "DEFAULT_SAVING_PATH", None)
            if not base_path:
                return CommandResult(
                    success=False,
                    error="No base_path provided and DEFAULT_SAVING_PATH not configured",
                )

        experiment_id = params.get("experiment_id")
        if not experiment_id:
            experiment_id = f"YAML_acquisition_{int(time.time())}"

        # Clear any acquisition events from previous runs
        self._acquisition_started_event.clear()
        self._acquisition_finished_event.clear()
        self._last_acquisition_started = None
        self._last_acquisition_finished = None

        # Configure acquisition via EventBus
        # 1. Clear existing coordinates
        self._event_bus.publish(ClearScanCoordinatesCommand())

        # 2. Load new coordinates
        self._event_bus.publish(
            LoadScanCoordinatesCommand(
                region_fov_coordinates=region_fov_coordinates,
                region_centers=region_centers,
            )
        )

        # 3. Set acquisition parameters
        autofocus_mode = AutofocusMode(yaml_data.autofocus_mode)

        self._event_bus.publish(
            SetAcquisitionParametersCommand(
                n_z=yaml_data.nz,
                delta_z_um=yaml_data.delta_z_um,
                n_t=yaml_data.nt,
                delta_t_s=yaml_data.delta_t_s,
                use_piezo=yaml_data.use_piezo,
                autofocus_mode=autofocus_mode,
                autofocus_interval_fovs=yaml_data.autofocus_interval_fovs,
                focus_lock_settings=FocusLockSettings(
                    buffer_length=yaml_data.focus_lock_buffer_length,
                    recovery_attempts=yaml_data.focus_lock_recovery_attempts,
                    min_spot_snr=yaml_data.focus_lock_min_spot_snr,
                    acquire_threshold_um=yaml_data.focus_lock_acquire_threshold_um,
                    maintain_threshold_um=yaml_data.focus_lock_maintain_threshold_um,
                    auto_search_enabled=yaml_data.focus_lock_enabled,
                ),
                widget_type=yaml_data.widget_type,
                scan_size_mm=yaml_data.scan_size_mm,
                overlap_percent=yaml_data.overlap_percent,
            )
        )

        # 4. Set channels
        self._event_bus.publish(
            SetAcquisitionChannelsCommand(channel_names=yaml_data.channel_names)
        )

        # 5. Set base path
        self._event_bus.publish(SetAcquisitionPathCommand(base_path=base_path))

        # 6. Start new experiment
        self._event_bus.publish(StartNewExperimentCommand(experiment_id=experiment_id))

        # 7. Start acquisition
        self._event_bus.publish(
            StartAcquisitionCommand(
                experiment_id=experiment_id,
                xy_mode=yaml_data.xy_mode or "Select Wells",
            )
        )

        # Wait for acquisition to start (with timeout)
        if not self._acquisition_started_event.wait(timeout=10.0):
            return CommandResult(
                success=False, error="Acquisition did not start within timeout"
            )

        # Calculate totals
        total_fovs = sum(len(coords) for coords in region_fov_coordinates.values())
        total_images = total_fovs * len(yaml_data.channel_names) * yaml_data.nz * yaml_data.nt

        save_dir = os.path.join(base_path, experiment_id)

        self._log.info(
            f"Acquisition started: {total_fovs} FOVs, {len(yaml_data.channel_names)} channels, "
            f"nz={yaml_data.nz}, nt={yaml_data.nt}, total_images={total_images}"
        )

        return CommandResult(
            success=True,
            data={
                "started": True,
                "yaml_path": yaml_path,
                "widget_type": yaml_data.widget_type,
                "region_count": len(region_fov_coordinates),
                "channels": yaml_data.channel_names,
                "nz": yaml_data.nz,
                "nt": yaml_data.nt,
                "total_fovs": total_fovs,
                "total_images": total_images,
                "experiment_id": experiment_id,
                "save_dir": save_dir,
            },
        )

    def _generate_fov_coordinates(
        self, yaml_data: AcquisitionYAMLData
    ) -> Tuple[Dict[str, Tuple[Tuple[float, ...], ...]], Dict[str, Tuple[float, ...]]]:
        """Generate FOV coordinates from YAML region data.

        Args:
            yaml_data: Parsed YAML data with wellplate_regions

        Returns:
            Tuple of (region_fov_coordinates, region_centers)
        """
        # Get FOV dimensions from camera
        fov_width_mm = 1.0
        fov_height_mm = 1.0

        try:
            pixel_size_um = self._objective_store.objective_pixel_size_um
            if self._camera:
                width_px = self._camera.get_frame_width()
                height_px = self._camera.get_frame_height()
                fov_width_mm = (width_px * pixel_size_um) / 1000.0
                fov_height_mm = (height_px * pixel_size_um) / 1000.0
        except Exception as e:
            self._log.warning(f"Could not get FOV size, using defaults: {e}")

        grid_config = GridConfig(
            fov_width_mm=fov_width_mm,
            fov_height_mm=fov_height_mm,
            overlap_percent=yaml_data.overlap_percent or 10.0,
        )

        scan_size_mm = yaml_data.scan_size_mm or 2.0
        current_z = 0.0

        try:
            pos = self._stage.get_pos()
            current_z = pos.z_mm
        except Exception:
            pass

        region_fov_coordinates: Dict[str, Tuple[Tuple[float, ...], ...]] = {}
        region_centers: Dict[str, Tuple[float, ...]] = {}

        for region in yaml_data.wellplate_regions or []:
            name = region.get("name", "region")
            center = region.get("center_mm", [0, 0, 0])
            center_x = center[0] if len(center) > 0 else 0.0
            center_y = center[1] if len(center) > 1 else 0.0
            center_z = center[2] if len(center) > 2 else current_z
            shape = region.get("shape", "Square")

            # Generate grid based on shape
            if shape == "Circle":
                xy_coords = generate_circular_grid(
                    center_x=center_x,
                    center_y=center_y,
                    diameter_mm=scan_size_mm,
                    config=grid_config,
                )
            else:  # Square or default
                xy_coords = generate_square_grid(
                    center_x=center_x,
                    center_y=center_y,
                    scan_size_mm=scan_size_mm,
                    config=grid_config,
                )

            # Add Z coordinate to each position
            fov_coords = tuple((x, y, center_z) for x, y in xy_coords)

            region_fov_coordinates[name] = fov_coords
            region_centers[name] = (center_x, center_y, center_z)

        return region_fov_coordinates, region_centers


def send_command(
    host: str,
    port: int,
    command: str,
    timeout: float = 30.0,
    **params: Any,
) -> Dict[str, Any]:
    """Send a command to the TCP control server.

    Args:
        host: Server host address
        port: Server port
        command: Command name
        timeout: Socket timeout in seconds
        **params: Command parameters

    Returns:
        Server response as dictionary

    Raises:
        ConnectionError: If connection fails
        TimeoutError: If operation times out
    """
    request = {"command": command, **params}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)

    try:
        sock.connect((host, port))
        sock.sendall(json.dumps(request).encode("utf-8"))

        # Receive response
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            try:
                return json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                continue

        if not data:
            raise ConnectionError("Empty response from server")

        return json.loads(data.decode("utf-8"))

    except socket.timeout:
        raise TimeoutError("Connection timed out")
    except ConnectionRefusedError:
        raise ConnectionError(f"Connection refused: {host}:{port}")
    finally:
        sock.close()
