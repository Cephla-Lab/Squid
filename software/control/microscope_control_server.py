"""
TCP Control Server for Squid Microscope

This module provides a TCP socket server that runs inside the GUI process,
allowing external tools (like Claude Code via MCP) to control the microscope
while the GUI is running.

The server accepts JSON commands and returns JSON responses.
"""

import functools
import inspect
import json
import socket
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, get_type_hints

import squid.logging

import control._def  # Module import for runtime access to MCP-modifiable settings

# Qt imports for thread-safe GUI operations
try:
    from qtpy.QtCore import QTimer

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

try:
    from pydantic import Field
    from pydantic.fields import FieldInfo

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    FieldInfo = None

    # Fallback Field implementation when pydantic is not available
    def Field(
        default=...,
        description: str = "",
        ge: float = None,
        le: float = None,
        **kwargs,
    ):
        """Fallback Field function when pydantic is not installed."""
        return {"default": default, "description": description, "ge": ge, "le": le}


def resolve_field_value(value, default=None):
    """
    Extract actual value from a pydantic FieldInfo object.

    When Field() is used as a default parameter in a regular Python function,
    the default becomes the FieldInfo object itself. This helper extracts
    the actual default value.
    """
    if PYDANTIC_AVAILABLE and isinstance(value, FieldInfo):
        field_default = value.default
        # Check for PydanticUndefined or Ellipsis (required field)
        if field_default is ... or (
            hasattr(field_default, "__class__") and "PydanticUndefinedType" in field_default.__class__.__name__
        ):
            return default
        return field_default
    elif isinstance(value, dict) and "default" in value:
        # Fallback Field dict
        return value.get("default", default)
    return value


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050
MAX_BUFFER_SIZE = 1 * 1024 * 1024  # 1 MB limit to prevent memory exhaustion
MAX_CONNECTIONS = 5  # Maximum concurrent client connections


def schema_method(func: Callable) -> Callable:
    """
    Decorator that marks a method as a schema-documented command.

    This decorator extracts parameter information from type hints and Field
    annotations to generate JSON schema for the command. This enables:
    - Automatic documentation generation
    - Parameter validation
    - MCP tool schema generation for AI agents

    Usage:
        @schema_method
        def my_command(
            self,
            param1: str = Field(..., description="Required string parameter"),
            param2: float = Field(0.0, description="Optional float", ge=0, le=100),
        ) -> Dict[str, Any]:
            ...
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    # Extract schema information from function signature
    sig = inspect.signature(func)
    hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}

    schema = {
        "name": func.__name__.replace("_cmd_", ""),
        "description": func.__doc__.strip().split("\n")[0] if func.__doc__ else "",
        "parameters": {},
        "required": [],
    }

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        param_info = {"type": "string"}  # default type

        # Get type hint
        if param_name in hints:
            hint = hints[param_name]
            # Handle Optional types (Union[X, None])
            actual_type = hint
            if hasattr(hint, "__origin__"):
                import typing

                # Check for Optional (Union with None)
                if hint.__origin__ is typing.Union:
                    args = [a for a in hint.__args__ if a is not None]
                    if len(args) == 1:
                        actual_type = args[0]

            # Map Python types to JSON Schema types
            if actual_type == float:
                param_info["type"] = "number"
            elif actual_type == int:
                param_info["type"] = "integer"
            elif actual_type == bool:
                param_info["type"] = "boolean"
            elif actual_type == str:
                param_info["type"] = "string"
            elif hasattr(actual_type, "__origin__"):  # List, Dict, etc.
                if actual_type.__origin__ == list:
                    param_info["type"] = "array"
                    # Extract item type if available (e.g., List[str] -> items: {type: string})
                    if hasattr(actual_type, "__args__") and actual_type.__args__:
                        item_type = actual_type.__args__[0]
                        if item_type == str:
                            param_info["items"] = {"type": "string"}
                        elif item_type == int:
                            param_info["items"] = {"type": "integer"}
                        elif item_type == float:
                            param_info["items"] = {"type": "number"}
                        elif item_type == bool:
                            param_info["items"] = {"type": "boolean"}
                elif actual_type.__origin__ == dict:
                    param_info["type"] = "object"

        # Get Field information
        default = param.default
        if PYDANTIC_AVAILABLE and isinstance(default, FieldInfo):
            if default.description:
                param_info["description"] = default.description
            # Pydantic v2 stores constraints in metadata
            for meta in default.metadata:
                if hasattr(meta, "ge") and meta.ge is not None:
                    param_info["minimum"] = meta.ge
                if hasattr(meta, "le") and meta.le is not None:
                    param_info["maximum"] = meta.le
            # Check if field is required (default is PydanticUndefined or Ellipsis)
            field_default = default.default
            is_required = field_default is ... or (
                hasattr(field_default, "__class__") and "PydanticUndefinedType" in field_default.__class__.__name__
            )
            if is_required:
                schema["required"].append(param_name)
            elif field_default is not None:
                param_info["default"] = field_default
        elif isinstance(default, dict) and "description" in default:
            # Fallback Field dict
            if default.get("description"):
                param_info["description"] = default["description"]
            if default.get("ge") is not None:
                param_info["minimum"] = default["ge"]
            if default.get("le") is not None:
                param_info["maximum"] = default["le"]
            if default.get("default") not in (None, ...):
                param_info["default"] = default["default"]
            else:
                schema["required"].append(param_name)
        elif default is inspect.Parameter.empty:
            schema["required"].append(param_name)
        elif default is not None:
            param_info["default"] = default

        schema["parameters"][param_name] = param_info

    wrapper._schema = schema
    return wrapper


class MicroscopeControlServer:
    """
    TCP server that exposes microscope control functions to external clients.

    Runs in a background thread within the GUI process, allowing external
    tools to send commands while the GUI remains responsive.

    The server uses a simple JSON-over-TCP protocol:
    - Client sends: {"command": "cmd_name", "params": {...}}
    - Server responds: {"success": true/false, "result": {...}} or {"error": "..."}

    Commands are automatically documented via the @schema_method decorator,
    which extracts parameter info from type hints and pydantic Field annotations.

    Example usage:
        server = MicroscopeControlServer(microscope, multipoint_controller=mpc)
        server.start()
        # ... server runs in background ...
        server.stop()

    Attributes:
        microscope: The Microscope instance to control.
        host: Server bind address (default: 127.0.0.1).
        port: Server port (default: 5050).
        multipoint_controller: Optional MultiPointController for acquisitions.
        scan_coordinates: Optional ScanCoordinates for well plate scanning.
        gui: Optional GUI reference for UI-specific operations.
    """

    def __init__(
        self,
        microscope,  # control.microscope.Microscope
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        multipoint_controller=None,  # Optional: GUI's multipoint controller for acquisitions
        scan_coordinates=None,  # Optional: GUI's scan coordinates
        gui=None,  # Optional: GUI reference for performance mode toggle
    ):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.microscope = microscope
        self.host = host
        self.port = port
        self.multipoint_controller = multipoint_controller
        self.scan_coordinates = scan_coordinates
        self.gui = gui
        self._server_socket: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._python_exec_enabled = False  # Disabled by default for security
        self._connection_semaphore = threading.Semaphore(MAX_CONNECTIONS)

        # Auto-discover commands by finding all _cmd_* methods
        self._commands: Dict[str, Callable] = {}
        for name, method in inspect.getmembers(self, predicate=callable):
            if name.startswith("_cmd_"):
                command_name = name[len("_cmd_") :]
                self._commands[command_name] = method

    def start(self):
        """Start the control server in a background thread."""
        if self._running:
            self._log.warning("Control server is already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_server, daemon=True, name="MicroscopeControlServer")
        self._thread.start()
        self._log.info(f"Microscope control server started on {self.host}:{self.port}")

    def stop(self):
        """Stop the control server."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass  # Ignore errors during socket cleanup on shutdown
        if self._thread:
            self._thread.join(timeout=2.0)
        self._log.info("Microscope control server stopped")

    def is_running(self) -> bool:
        """Check if the control server is currently running."""
        return self._running

    def _run_server(self):
        """Main server loop - runs in background thread."""
        try:
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind((self.host, self.port))
            self._server_socket.listen(5)
            self._server_socket.settimeout(1.0)  # Allow periodic check of _running flag

            while self._running:
                try:
                    client_socket, address = self._server_socket.accept()
                    self._log.debug(f"Connection from {address}")
                    # Limit concurrent connections to prevent resource exhaustion
                    if self._connection_semaphore.acquire(blocking=False):
                        client_thread = threading.Thread(target=self._handle_client, args=(client_socket,), daemon=True)
                        client_thread.start()
                    else:
                        # Too many connections, reject this one
                        self._log.warning(f"Connection limit reached, rejecting {address}")
                        try:
                            response = {"success": False, "error": "Server busy, too many connections"}
                            client_socket.sendall((json.dumps(response) + "\n").encode("utf-8"))
                        finally:
                            client_socket.close()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self._running:
                        self._log.error(f"Error accepting connection: {e}")
        except Exception as e:
            self._log.error(f"Server error: {e}")
        finally:
            if self._server_socket:
                self._server_socket.close()

    def _handle_client(self, client_socket: socket.socket):
        """Handle a single client connection."""
        try:
            client_socket.settimeout(30.0)

            # Receive data (simple protocol: newline-delimited JSON)
            buffer = b""
            while True:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_BUFFER_SIZE:
                    response = {"success": False, "error": "Request too large"}
                    client_socket.sendall((json.dumps(response) + "\n").encode("utf-8"))
                    return
                if b"\n" in buffer:
                    break

            if not buffer:
                return

            # Parse command
            try:
                request = json.loads(buffer.decode("utf-8").strip())
            except json.JSONDecodeError as e:
                response = {"success": False, "error": f"Invalid JSON: {e}"}
                client_socket.sendall((json.dumps(response) + "\n").encode("utf-8"))
                return

            # Execute command
            command = request.get("command")
            params = request.get("params", {})

            if command not in self._commands:
                response = {"success": False, "error": f"Unknown command: {command}"}
            else:
                try:
                    result = self._commands[command](**params)
                    response = {"success": True, "result": result}
                except Exception as e:
                    self._log.error(f"Command '{command}' failed: {e}\n{traceback.format_exc()}")
                    response = {"success": False, "error": str(e)}

            # Send response
            client_socket.sendall((json.dumps(response) + "\n").encode("utf-8"))

        except Exception as e:
            self._log.error(f"Client handler error: {e}")
        finally:
            try:
                client_socket.close()
            except Exception:
                pass  # Ignore errors during client socket cleanup
            self._connection_semaphore.release()

    # ==========================================================================
    # Command implementations
    # ==========================================================================

    @schema_method
    def _cmd_ping(self) -> Dict[str, Any]:
        """Check if the microscope control server is running and responsive."""
        return {"status": "ok", "message": "Microscope control server is running"}

    @schema_method
    def _cmd_get_position(self) -> Dict[str, float]:
        """Get current XYZ stage position in millimeters."""
        pos = self.microscope.stage.get_pos()
        return {
            "x_mm": pos.x_mm,
            "y_mm": pos.y_mm,
            "z_mm": pos.z_mm,
        }

    @schema_method
    def _cmd_move_to(
        self,
        x_mm: Optional[float] = Field(None, description="Target X position in millimeters"),
        y_mm: Optional[float] = Field(None, description="Target Y position in millimeters"),
        z_mm: Optional[float] = Field(None, description="Target Z position in millimeters"),
        blocking: bool = Field(True, description="Wait for move to complete before returning"),
    ) -> Dict[str, Any]:
        """Move stage to absolute XYZ position in millimeters."""
        if x_mm is not None:
            self.microscope.move_x_to(x_mm, blocking=blocking)
        if y_mm is not None:
            self.microscope.move_y_to(y_mm, blocking=blocking)
        if z_mm is not None:
            self.microscope.move_z_to(z_mm, blocking=blocking)

        pos = self.microscope.stage.get_pos()
        return {"moved_to": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}}

    @schema_method
    def _cmd_move_relative(
        self,
        dx_mm: float = Field(0, description="Relative X movement in millimeters"),
        dy_mm: float = Field(0, description="Relative Y movement in millimeters"),
        dz_mm: float = Field(0, description="Relative Z movement in millimeters"),
        blocking: bool = Field(True, description="Wait for move to complete before returning"),
    ) -> Dict[str, Any]:
        """Move stage by relative amount in millimeters."""
        if dx_mm != 0:
            self.microscope.move_x(dx_mm, blocking=blocking)
        if dy_mm != 0:
            self.microscope.move_y(dy_mm, blocking=blocking)
        if dz_mm != 0:
            self.microscope.stage.move_z(dz_mm, blocking=blocking)

        pos = self.microscope.stage.get_pos()
        return {"new_position": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}}

    @schema_method
    def _cmd_home(self) -> Dict[str, Any]:
        """Home all stage axes (X, Y, Z) to reference position."""
        self.microscope.home_xyz()
        pos = self.microscope.stage.get_pos()
        return {"homed": True, "position": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}}

    @schema_method
    def _cmd_start_live(self) -> Dict[str, Any]:
        """Start live imaging mode with continuous camera streaming."""
        self.microscope.start_live()
        return {"live": True}

    @schema_method
    def _cmd_stop_live(self) -> Dict[str, Any]:
        """Stop live imaging mode."""
        self.microscope.stop_live()
        return {"live": False}

    @schema_method
    def _cmd_acquire_image(
        self,
        save_path: Optional[str] = Field(None, description="File path to save the image (TIFF format)"),
    ) -> Dict[str, Any]:
        """Acquire a single image from the camera, optionally saving to disk."""
        image = self.microscope.acquire_image()

        result = {
            "acquired": image is not None,
        }

        if image is not None and save_path:
            import numpy as np

            try:
                # Try to save as TIFF
                import tifffile

                tifffile.imwrite(save_path, image)
                result["saved_to"] = save_path
            except ImportError:
                # Fallback to numpy
                np.save(save_path, image)
                result["saved_to"] = save_path + ".npy"

        if image is not None:
            result["shape"] = list(image.shape)
            result["dtype"] = str(image.dtype)

        return result

    @schema_method
    def _cmd_set_channel(
        self,
        channel_name: str = Field(
            ..., description="Name of the channel to activate (e.g., 'BF LED matrix full', 'Fluorescence 488 nm Ex')"
        ),
    ) -> Dict[str, Any]:
        """Set the current imaging channel/illumination mode."""
        objective = self.microscope.objective_store.current_objective
        channel_config = self.microscope.channel_configuration_mananger.get_channel_configuration_by_name(
            objective, channel_name
        )
        if channel_config:
            self.microscope.live_controller.set_microscope_mode(channel_config)
            return {"channel": channel_name, "objective": objective}
        else:
            raise ValueError(f"Channel '{channel_name}' not found for objective '{objective}'")

    @schema_method
    def _cmd_get_channels(self) -> Dict[str, Any]:
        """Get list of available imaging channels for the current objective."""
        objective = self.microscope.objective_store.current_objective
        channels = self.microscope.channel_configuration_mananger.get_channel_configurations_for_objective(objective)
        return {"objective": objective, "channels": [ch.name for ch in channels] if channels else []}

    @schema_method
    def _cmd_set_exposure(
        self,
        exposure_ms: float = Field(..., description="Exposure time in milliseconds", ge=0.1, le=10000),
        channel: Optional[str] = Field(
            None, description="Channel to set exposure for (applies to current if not specified)"
        ),
    ) -> Dict[str, Any]:
        """Set camera exposure time in milliseconds."""
        if channel:
            objective = self.microscope.objective_store.current_objective
            self.microscope.set_exposure_time(channel, exposure_ms, objective)
        else:
            self.microscope.camera.set_exposure_time(exposure_ms)
        return {"exposure_ms": exposure_ms}

    @schema_method
    def _cmd_set_illumination_intensity(
        self,
        channel: str = Field(..., description="Channel name (e.g., 'Fluorescence 488 nm Ex')"),
        intensity: float = Field(..., description="Intensity value (0-100%)", ge=0, le=100),
    ) -> Dict[str, Any]:
        """Set illumination/laser intensity for a specific channel (0-100%)."""
        self.microscope.set_illumination_intensity(channel, intensity)
        return {"channel": channel, "intensity": intensity}

    @schema_method
    def _cmd_get_objectives(self) -> Dict[str, Any]:
        """Get list of available objectives and the currently selected one."""
        objectives = list(self.microscope.objective_store.objectives_dict.keys())
        current = self.microscope.objective_store.current_objective
        return {"objectives": objectives, "current": current}

    @schema_method
    def _cmd_set_objective(
        self,
        objective_name: str = Field(
            ..., description="Name of the objective to select (e.g., '4x', '10x', '20x', '40x')"
        ),
    ) -> Dict[str, Any]:
        """Set the current objective lens."""
        self.microscope.set_objective(objective_name)
        return {"objective": objective_name}

    @schema_method
    def _cmd_get_current_objective(self) -> Dict[str, Any]:
        """Get the current objective."""
        return {"objective": self.microscope.objective_store.current_objective}

    @schema_method
    def _cmd_turn_on_illumination(self) -> Dict[str, Any]:
        """Turn on the illumination for the current channel."""
        self.microscope.live_controller.turn_on_illumination()
        return {"illumination": "on"}

    @schema_method
    def _cmd_turn_off_illumination(self) -> Dict[str, Any]:
        """Turn off all illumination."""
        self.microscope.live_controller.turn_off_illumination()
        return {"illumination": "off"}

    @schema_method
    def _cmd_get_status(self) -> Dict[str, Any]:
        """Get comprehensive microscope status including position, objective, and camera settings."""
        pos = self.microscope.stage.get_pos()
        objective = self.microscope.objective_store.current_objective

        status = {
            "position": {
                "x_mm": pos.x_mm,
                "y_mm": pos.y_mm,
                "z_mm": pos.z_mm,
            },
            "objective": objective,
            "camera": {
                "exposure_ms": self.microscope.camera.get_exposure_time(),
            },
            "live_controller": {
                "is_live": (
                    self.microscope.live_controller.is_live
                    if hasattr(self.microscope.live_controller, "is_live")
                    else None
                ),
            },
        }

        return status

    @schema_method
    def _cmd_autofocus(self) -> Dict[str, Any]:
        """Run autofocus routine (not yet implemented)."""
        # This would need to be implemented based on the autofocus controller
        # For now, raise not implemented
        raise NotImplementedError("Autofocus via control server not yet implemented")

    @schema_method
    def _cmd_acquire_laser_af_image(
        self,
        save_path: Optional[str] = Field(None, description="File path to save the image"),
        use_last_frame: bool = Field(True, description="Use last captured frame instead of triggering new capture"),
    ) -> Dict[str, Any]:
        """Acquire an image from the laser autofocus camera."""
        if not self.microscope.addons.camera_focus:
            raise RuntimeError("Laser AF camera not available")

        camera_focus = self.microscope.addons.camera_focus
        image = None

        if use_last_frame:
            # Get the last captured frame from the camera's buffer
            current_frame = getattr(camera_focus, "_current_frame", None)
            if current_frame is not None:
                import numpy as np

                image = np.squeeze(current_frame.frame)
        else:
            # Trigger a new capture
            camera_focus.send_trigger()
            image = camera_focus.read_frame()

        result = {
            "acquired": image is not None,
            "used_last_frame": use_last_frame,
        }

        if image is not None and save_path:
            try:
                import tifffile

                tifffile.imwrite(save_path, image)
                result["saved_to"] = save_path
            except ImportError:
                import numpy as np

                np.save(save_path, image)
                result["saved_to"] = save_path + ".npy"

        if image is not None:
            result["shape"] = list(image.shape)
            result["dtype"] = str(image.dtype)

        return result

    @schema_method
    def _cmd_run_acquisition(
        self,
        wells: str = Field(..., description="Well selection string (e.g., 'A1:B3' for range or 'A1,A2,B1' for list)"),
        channels: List[str] = Field(
            ...,
            description="List of channel names to acquire (e.g., ['Fluorescence 488 nm Ex', 'Fluorescence 561 nm Ex'])",
        ),
        nx: int = Field(2, description="Number of sites in X per well", ge=1, le=100),
        ny: int = Field(2, description="Number of sites in Y per well", ge=1, le=100),
        experiment_id: Optional[str] = Field(None, description="Experiment ID (auto-generated if not provided)"),
        base_path: Optional[str] = Field(None, description="Base path for saving images"),
        wellplate_format: str = Field(
            "96 well plate", description="Wellplate format (e.g., '6 well plate', '96 well plate', '384 well plate')"
        ),
        overlap_percent: float = Field(10.0, description="Overlap between FOVs in percent", ge=0, le=50),
    ) -> Dict[str, Any]:
        """Run a multi-point acquisition across wells using the MultiPointController."""
        import control._def

        # Resolve FieldInfo objects to actual values (fixes bug when params not provided)
        nx = resolve_field_value(nx, 2)
        ny = resolve_field_value(ny, 2)
        experiment_id = resolve_field_value(experiment_id, None)
        base_path = resolve_field_value(base_path, None)
        wellplate_format = resolve_field_value(wellplate_format, "96 well plate")
        overlap_percent = resolve_field_value(overlap_percent, 10.0)

        # Check requirements
        if not self.multipoint_controller:
            raise RuntimeError(
                "MultiPointController not available. Make sure the GUI is running with control server enabled."
            )

        if not self.scan_coordinates:
            raise RuntimeError(
                "ScanCoordinates not available. Make sure the GUI is running with control server enabled."
            )

        # Check if acquisition already running
        if self.multipoint_controller.acquisition_in_progress():
            raise RuntimeError("Acquisition already in progress")

        # Parse well coordinates
        wellplate_settings = control._def.get_wellplate_settings(wellplate_format)
        well_coords = self._parse_wells(wells, wellplate_settings)

        if not well_coords:
            raise ValueError(f"Could not parse wells: {wells}")

        # Validate channels exist
        objective = self.microscope.objective_store.current_objective
        available_channels = self.microscope.channel_configuration_mananger.get_channel_configurations_for_objective(
            objective
        )
        available_channel_names = [ch.name for ch in available_channels] if available_channels else []

        invalid_channels = [ch for ch in channels if ch not in available_channel_names]
        if invalid_channels:
            raise ValueError(f"Invalid channels: {invalid_channels}. Available: {available_channel_names}")

        # Set up paths
        if not base_path:
            base_path = (
                control._def.DEFAULT_SAVING_PATH
                if hasattr(control._def, "DEFAULT_SAVING_PATH")
                else "/tmp/squid_acquisitions"
            )
        if not experiment_id:
            experiment_id = "MCP_acquisition"

        # Configure the MultiPointController
        try:
            # Clear existing regions and set up new wells
            self.scan_coordinates.clear_regions()

            # Get current Z position for the regions
            current_z = self.microscope.stage.get_pos().z_mm

            # Add each well as a flexible region with NX x NY grid
            for well_id, (well_x, well_y) in well_coords.items():
                self.scan_coordinates.add_flexible_region(
                    region_id=well_id,
                    center_x=well_x,
                    center_y=well_y,
                    center_z=current_z,
                    Nx=nx,
                    Ny=ny,
                    overlap_percent=overlap_percent,
                )

            # Sort coordinates for efficient scanning pattern
            self.scan_coordinates.sort_coordinates()

            # Set acquisition parameters on the controller
            self.multipoint_controller.set_NX(1)  # Already handled by flexible regions
            self.multipoint_controller.set_NY(1)
            self.multipoint_controller.set_NZ(1)  # No Z-stack for now
            self.multipoint_controller.set_Nt(1)  # Single timepoint

            # Set the selected channels
            self.multipoint_controller.set_selected_configurations(channels)

            # Set the base path and start new experiment
            self.multipoint_controller.set_base_path(base_path)
            self.multipoint_controller.start_new_experiment(experiment_id)

            # Calculate total FOVs for status reporting
            total_fovs = sum(len(coords) for coords in self.scan_coordinates.region_fov_coordinates.values())
            total_images = total_fovs * len(channels)

            # Run the acquisition (non-blocking - runs in worker thread)
            self.multipoint_controller.run_acquisition()

            return {
                "started": True,
                "wells": wells,
                "well_count": len(well_coords),
                "channels": channels,
                "sites_per_well": nx * ny,
                "total_fovs": total_fovs,
                "total_images": total_images,
                "experiment_id": self.multipoint_controller.experiment_ID,
                "save_dir": f"{base_path}/{self.multipoint_controller.experiment_ID}",
            }

        except Exception as e:
            self._log.error(f"Failed to start acquisition: {e}")
            import traceback

            self._log.error(traceback.format_exc())
            raise RuntimeError(f"Failed to start acquisition: {str(e)}") from e

    def _parse_wells(self, wells: str, wellplate_settings: dict) -> Dict[str, tuple]:
        """
        Parse well string into stage coordinates.

        Supports two formats:
        - Range: 'A1:B3' expands to A1, A2, A3, B1, B2, B3
        - List: 'A1,A2,B1' for specific wells

        Args:
            wells: Well selection string (e.g., 'A1:B3' or 'A1,A2,B1').
            wellplate_settings: Dict with 'a1_x_mm', 'a1_y_mm', 'well_spacing_mm'.

        Returns:
            Dict mapping well IDs to (x_mm, y_mm) coordinates.
        """
        import re

        def row_to_index(row: str) -> int:
            index = 0
            for char in row.upper():
                index = index * 26 + (ord(char) - ord("A") + 1)
            return index - 1

        def index_to_row(index: int) -> str:
            index += 1
            row = ""
            while index > 0:
                index -= 1
                row = chr(index % 26 + ord("A")) + row
                index //= 26
            return row

        a1_x = wellplate_settings.get("a1_x_mm", 0)
        a1_y = wellplate_settings.get("a1_y_mm", 0)
        spacing = wellplate_settings.get("well_spacing_mm", 9)

        well_coords = {}
        pattern = r"([A-Za-z]+)(\d+):?([A-Za-z]*)(\d*)"

        for desc in wells.split(","):
            match = re.match(pattern, desc.strip())
            if not match:
                continue

            start_row, start_col, end_row, end_col = match.groups()
            start_row_idx = row_to_index(start_row)
            start_col_idx = int(start_col) - 1

            if end_row and end_col:
                # Range like A1:B3
                end_row_idx = row_to_index(end_row)
                end_col_idx = int(end_col) - 1

                for row_idx in range(start_row_idx, end_row_idx + 1):
                    for col_idx in range(start_col_idx, end_col_idx + 1):
                        well_id = index_to_row(row_idx) + str(col_idx + 1)
                        x_mm = a1_x + col_idx * spacing
                        y_mm = a1_y + row_idx * spacing
                        well_coords[well_id] = (x_mm, y_mm)
            else:
                # Single well like A1
                well_id = start_row.upper() + start_col
                x_mm = a1_x + start_col_idx * spacing
                y_mm = a1_y + start_row_idx * spacing
                well_coords[well_id] = (x_mm, y_mm)

        return well_coords

    @schema_method
    def _cmd_get_acquisition_status(self) -> Dict[str, Any]:
        """Get the status of the current acquisition including progress information."""
        if not self.multipoint_controller:
            raise RuntimeError("MultiPointController not available")

        in_progress = self.multipoint_controller.acquisition_in_progress()

        result = {
            "in_progress": in_progress,
            "status": "running" if in_progress else "idle",
        }

        # Add worker progress if available
        if self.multipoint_controller.multiPointWorker:
            worker = self.multipoint_controller.multiPointWorker
            # The worker may have progress attributes we can check
            if hasattr(worker, "current_fov_index"):
                result["current_fov"] = worker.current_fov_index
            if hasattr(worker, "total_fovs"):
                result["total_fovs"] = worker.total_fovs

        # Add experiment info if available
        if self.multipoint_controller.experiment_ID:
            result["experiment_id"] = self.multipoint_controller.experiment_ID
        if self.multipoint_controller.base_path:
            result["base_path"] = self.multipoint_controller.base_path

        return result

    @schema_method
    def _cmd_abort_acquisition(self) -> Dict[str, Any]:
        """Abort the current running acquisition."""
        if not self.multipoint_controller:
            raise RuntimeError("MultiPointController not available")

        if not self.multipoint_controller.acquisition_in_progress():
            raise RuntimeError("No acquisition in progress")

        # Use the controller's abort mechanism (handle both spellings for compatibility)
        if hasattr(self.multipoint_controller, "request_abort_acquisition"):
            self.multipoint_controller.request_abort_acquisition()
        else:
            # Legacy misspelled method name
            self.multipoint_controller.request_abort_aquisition()
        return {"aborted": True}

    @schema_method
    def _cmd_set_performance_mode(
        self,
        enabled: bool = Field(..., description="Enable (true) or disable (false) performance mode"),
    ) -> Dict[str, Any]:
        """Enable or disable performance mode (disables mosaic view to save RAM, ~14% faster)."""
        if not self.gui:
            raise RuntimeError("GUI reference not available")

        if not hasattr(self.gui, "performanceModeToggle"):
            raise RuntimeError("Performance mode toggle not available in GUI")

        # Use QTimer.singleShot to safely modify Qt widgets from the socket thread.
        # This schedules the call to run on the Qt main thread event loop.
        if QT_AVAILABLE:
            # Thread-safe: schedule toggle on main Qt thread
            QTimer.singleShot(0, lambda: self.gui.performanceModeToggle.setChecked(enabled))
            # Give Qt event loop time to process
            time.sleep(0.1)
        else:
            # Fallback: direct call (may cause issues if called from non-Qt thread)
            self.gui.performanceModeToggle.setChecked(enabled)

        return {
            "performance_mode": self.gui.performance_mode,
            "message": f"Performance mode {'enabled' if enabled else 'disabled'}",
        }

    @schema_method
    def _cmd_get_performance_mode(self) -> Dict[str, Any]:
        """Get the current performance mode state."""
        if not self.gui:
            raise RuntimeError("GUI reference not available")

        return {"performance_mode": getattr(self.gui, "performance_mode", False)}

    # ==========================================================================
    # View debug settings (for RAM usage debugging)
    # ==========================================================================

    @schema_method
    def _cmd_get_view_settings(self) -> Dict[str, Any]:
        """Get current view settings for RAM debugging (downsampled images, plate view, mosaic view)."""
        return {
            "save_downsampled_well_images": control._def.SAVE_DOWNSAMPLED_WELL_IMAGES,
            "display_plate_view": control._def.DISPLAY_PLATE_VIEW,
            "display_mosaic_view": control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY,
            "mosaic_view_target_pixel_size_um": control._def.MOSAIC_VIEW_TARGET_PIXEL_SIZE_UM,
            "downsampled_well_resolutions_um": control._def.DOWNSAMPLED_WELL_RESOLUTIONS_UM,
            "downsampled_plate_resolution_um": control._def.DOWNSAMPLED_PLATE_RESOLUTION_UM,
            "performance_mode": getattr(self.gui, "performance_mode", False) if self.gui else None,
        }

    @schema_method
    def _cmd_set_save_downsampled_images(
        self,
        enabled: bool = Field(..., description="Enable (true) or disable (false) saving downsampled well images"),
    ) -> Dict[str, Any]:
        """Enable or disable saving downsampled well images (affects next acquisition)."""
        if not isinstance(enabled, bool):
            raise TypeError(f"enabled must be a boolean, got {type(enabled).__name__}")
        control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = enabled
        return {
            "save_downsampled_well_images": control._def.SAVE_DOWNSAMPLED_WELL_IMAGES,
            "message": f"Saving downsampled well images {'enabled' if enabled else 'disabled'} (takes effect on next acquisition)",
        }

    @schema_method
    def _cmd_set_display_plate_view(
        self,
        enabled: bool = Field(..., description="Enable (true) or disable (false) plate view display"),
    ) -> Dict[str, Any]:
        """Enable or disable plate view display during acquisition (affects next acquisition)."""
        if not isinstance(enabled, bool):
            raise TypeError(f"enabled must be a boolean, got {type(enabled).__name__}")
        control._def.DISPLAY_PLATE_VIEW = enabled
        return {
            "display_plate_view": control._def.DISPLAY_PLATE_VIEW,
            "message": f"Plate view display {'enabled' if enabled else 'disabled'} (takes effect on next acquisition)",
        }

    @schema_method
    def _cmd_set_display_mosaic_view(
        self,
        enabled: bool = Field(..., description="Enable (true) or disable (false) mosaic view display"),
    ) -> Dict[str, Any]:
        """Enable or disable mosaic view display (takes effect immediately, checked on each updateMosaic call)."""
        if not isinstance(enabled, bool):
            raise TypeError(f"enabled must be a boolean, got {type(enabled).__name__}")
        control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = enabled
        return {
            "display_mosaic_view": control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY,
            "message": f"Mosaic view display {'enabled' if enabled else 'disabled'} (takes effect immediately)",
        }

    @schema_method
    def _cmd_set_view_settings(
        self,
        save_downsampled_well_images: Optional[bool] = Field(
            None, description="Enable/disable saving downsampled well images"
        ),
        display_plate_view: Optional[bool] = Field(None, description="Enable/disable plate view display"),
        display_mosaic_view: Optional[bool] = Field(None, description="Enable/disable mosaic view display"),
    ) -> Dict[str, Any]:
        """Set multiple view settings at once for RAM debugging (mosaic view: immediate; others: next acquisition)."""
        changes = []

        # Note: Use isinstance(x, bool) instead of "x is not None" because Field(None, ...)
        # returns a FieldInfo object (not None) when called directly without JSON parsing.
        if isinstance(save_downsampled_well_images, bool):
            control._def.SAVE_DOWNSAMPLED_WELL_IMAGES = save_downsampled_well_images
            changes.append(f"save_downsampled_well_images={'enabled' if save_downsampled_well_images else 'disabled'}")

        if isinstance(display_plate_view, bool):
            control._def.DISPLAY_PLATE_VIEW = display_plate_view
            changes.append(f"display_plate_view={'enabled' if display_plate_view else 'disabled'}")

        if isinstance(display_mosaic_view, bool):
            control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY = display_mosaic_view
            changes.append(f"display_mosaic_view={'enabled' if display_mosaic_view else 'disabled'}")

        return {
            "save_downsampled_well_images": control._def.SAVE_DOWNSAMPLED_WELL_IMAGES,
            "display_plate_view": control._def.DISPLAY_PLATE_VIEW,
            "display_mosaic_view": control._def.USE_NAPARI_FOR_MOSAIC_DISPLAY,
            "changes": changes,
            "message": "Settings updated (mosaic view: immediate; others: next acquisition)",
        }

    @schema_method
    def _cmd_get_schemas(self) -> Dict[str, Any]:
        """Get JSON schemas for all available commands (useful for AI agents)."""
        schemas = {}
        for cmd_name, cmd_func in self._commands.items():
            if hasattr(cmd_func, "_schema"):
                schemas[cmd_name] = cmd_func._schema
            else:
                # Basic schema for non-decorated methods
                schemas[cmd_name] = {
                    "name": cmd_name,
                    "description": cmd_func.__doc__.strip().split("\n")[0] if cmd_func.__doc__ else "",
                    "parameters": {},
                    "required": [],
                }
        return {"schemas": schemas}

    @schema_method
    def _cmd_python_exec(
        self,
        code: str = Field(
            ..., description="Python code to execute. Set 'result' for return value, 'image' for auto-saved images."
        ),
    ) -> Dict[str, Any]:
        """
        Execute Python code with direct access to microscope objects.

        Available objects in scope:
        - microscope: Main Microscope instance
        - stage: microscope.stage (shortcut)
        - camera: microscope.camera (shortcut)
        - live_controller: microscope.live_controller (shortcut)
        - objective_store: microscope.objective_store (shortcut)
        - multipoint_controller: MultiPointController (if available)
        - scan_coordinates: ScanCoordinates (if available)
        - np: numpy module

        Special variables:
        - result: Set this to return data (will be JSON serialized)
        - image: Set to ndarray to auto-save and return image path

        Example:
            code = '''
            pos = stage.get_pos()
            result = {'x': pos.x_mm, 'y': pos.y_mm}
            '''

        Example with image:
            code = '''
            image = camera.read_frame()
            result = f"Acquired {image.shape}"
            '''
        """
        # Security check
        if not self._python_exec_enabled:
            return {
                "error": "python_exec is disabled. Enable it in the GUI first for security.",
                "enabled": False,
            }

        import numpy as np
        import os
        import tempfile

        # Build execution namespace with microscope objects
        local_vars = {
            "microscope": self.microscope,
            "stage": self.microscope.stage,
            "camera": self.microscope.camera,
            "live_controller": self.microscope.live_controller,
            "objective_store": self.microscope.objective_store,
            "multipoint_controller": self.multipoint_controller,
            "scan_coordinates": self.scan_coordinates,
            "gui": self.gui,
            "np": np,
            "result": None,
            "image": None,
        }

        try:
            # Create a restricted builtins dict - exclude dangerous recursive execution functions
            # while keeping useful ones for microscope scripting
            # Note: __builtins__ can be a dict (main module) or module (imported modules)
            builtins_dict = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
            safe_builtins = {
                k: v
                for k, v in builtins_dict.items()
                if k
                not in (
                    "eval",
                    "exec",
                    "compile",
                    "__import__",
                    "open",  # Disabled; use tifffile/numpy or set 'image' variable for auto-save
                    "input",
                    "breakpoint",
                )
            }
            # Add back controlled imports
            safe_builtins["__import__"] = lambda name, *args, **kwargs: (
                __import__(name, *args, **kwargs)
                if name in ("numpy", "math", "time", "datetime", "json", "os.path")
                else (_ for _ in ()).throw(ImportError(f"Import of '{name}' not allowed"))
            )

            exec(code, {"__builtins__": safe_builtins, "np": np}, local_vars)

            response = {}

            # Handle result
            result = local_vars.get("result")
            if result is not None:
                # Try to serialize, fall back to str
                try:
                    json.dumps(result)  # Test if serializable
                    response["result"] = result
                except (TypeError, ValueError):
                    response["result"] = str(result)

            # Handle image auto-save
            img = local_vars.get("image")
            if img is not None and isinstance(img, np.ndarray):
                try:
                    import tifffile

                    path = os.path.join(tempfile.gettempdir(), "claude_microscope_image.tiff")
                    tifffile.imwrite(path, img)
                    response["image_path"] = path
                except ImportError:
                    # Fallback to numpy
                    path = os.path.join(tempfile.gettempdir(), "claude_microscope_image.npy")
                    np.save(path, img)
                    response["image_path"] = path

                response["image_shape"] = list(img.shape)
                response["image_dtype"] = str(img.dtype)

            return response

        except Exception as e:
            self._log.error(f"python_exec failed: {e}")
            import traceback

            self._log.error(traceback.format_exc())
            raise RuntimeError(f"python_exec failed: {str(e)}") from e

    @schema_method
    def _cmd_get_python_exec_status(self) -> Dict[str, Any]:
        """Check if python_exec is currently enabled."""
        return {"enabled": self._python_exec_enabled}

    def set_python_exec_enabled(self, enabled: bool):
        """
        Enable or disable python_exec command.

        This method should be called from the GUI (e.g., checkbox toggle).
        python_exec is disabled by default for security - users must
        explicitly enable it.

        Args:
            enabled: True to enable python_exec, False to disable.
        """
        self._python_exec_enabled = enabled
        if enabled:
            self._log.warning("python_exec has been ENABLED via GUI")
        else:
            self._log.info("python_exec has been disabled via GUI")


def send_command(
    command: str,
    params: Optional[Dict[str, Any]] = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """
    Send a command to the microscope control server.

    This is a helper function for testing or simple scripts.

    Args:
        command: Command name to execute
        params: Command parameters
        host: Server host
        port: Server port
        timeout: Socket timeout in seconds

    Returns:
        Server response as a dictionary
    """
    request = {"command": command, "params": params or {}}

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))

        # Receive response
        buffer = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > MAX_BUFFER_SIZE:
                raise ValueError("Response too large")
            if b"\n" in buffer:
                break

        return json.loads(buffer.decode("utf-8").strip())
