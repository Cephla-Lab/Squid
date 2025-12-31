"""
TCP Control Server for Squid Microscope

This module provides a TCP socket server that runs inside the GUI process,
allowing external tools (like Claude Code via MCP) to control the microscope
while the GUI is running.

The server accepts JSON commands and returns JSON responses.
"""

import json
import socket
import threading
import traceback
from typing import Any, Callable, Dict, Optional

import squid.logging

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050


class MicroscopeControlServer:
    """
    TCP server that exposes microscope control functions to external clients.

    Runs in a background thread within the GUI process, allowing external
    tools to send commands while the GUI remains responsive.
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

        # Register available commands
        self._commands: Dict[str, Callable] = {
            "ping": self._cmd_ping,
            "get_position": self._cmd_get_position,
            "move_to": self._cmd_move_to,
            "move_relative": self._cmd_move_relative,
            "home": self._cmd_home,
            "start_live": self._cmd_start_live,
            "stop_live": self._cmd_stop_live,
            "acquire_image": self._cmd_acquire_image,
            "set_channel": self._cmd_set_channel,
            "get_channels": self._cmd_get_channels,
            "set_exposure": self._cmd_set_exposure,
            "set_illumination_intensity": self._cmd_set_illumination_intensity,
            "get_objectives": self._cmd_get_objectives,
            "set_objective": self._cmd_set_objective,
            "get_current_objective": self._cmd_get_current_objective,
            "turn_on_illumination": self._cmd_turn_on_illumination,
            "turn_off_illumination": self._cmd_turn_off_illumination,
            "get_status": self._cmd_get_status,
            "autofocus": self._cmd_autofocus,
            "acquire_laser_af_image": self._cmd_acquire_laser_af_image,
            "run_acquisition": self._cmd_run_acquisition,
            "get_acquisition_status": self._cmd_get_acquisition_status,
            "abort_acquisition": self._cmd_abort_acquisition,
            "set_performance_mode": self._cmd_set_performance_mode,
            "get_performance_mode": self._cmd_get_performance_mode,
        }

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
                pass
        if self._thread:
            self._thread.join(timeout=2.0)
        self._log.info("Microscope control server stopped")

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
                    # Handle each client in a separate thread
                    client_thread = threading.Thread(target=self._handle_client, args=(client_socket,), daemon=True)
                    client_thread.start()
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
                pass

    # ==========================================================================
    # Command implementations
    # ==========================================================================

    def _cmd_ping(self) -> Dict[str, Any]:
        """Health check."""
        return {"status": "ok", "message": "Microscope control server is running"}

    def _cmd_get_position(self) -> Dict[str, float]:
        """Get current stage position."""
        pos = self.microscope.stage.get_pos()
        return {
            "x_mm": pos.x_mm,
            "y_mm": pos.y_mm,
            "z_mm": pos.z_mm,
        }

    def _cmd_move_to(
        self,
        x_mm: Optional[float] = None,
        y_mm: Optional[float] = None,
        z_mm: Optional[float] = None,
        blocking: bool = True,
    ) -> Dict[str, Any]:
        """Move stage to absolute position."""
        if x_mm is not None:
            self.microscope.move_x_to(x_mm, blocking=blocking)
        if y_mm is not None:
            self.microscope.move_y_to(y_mm, blocking=blocking)
        if z_mm is not None:
            self.microscope.move_z_to(z_mm, blocking=blocking)

        pos = self.microscope.stage.get_pos()
        return {"moved_to": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}}

    def _cmd_move_relative(
        self,
        dx_mm: float = 0,
        dy_mm: float = 0,
        dz_mm: float = 0,
        blocking: bool = True,
    ) -> Dict[str, Any]:
        """Move stage by relative amount."""
        if dx_mm != 0:
            self.microscope.move_x(dx_mm, blocking=blocking)
        if dy_mm != 0:
            self.microscope.move_y(dy_mm, blocking=blocking)
        if dz_mm != 0:
            self.microscope.stage.move_z(dz_mm, blocking=blocking)

        pos = self.microscope.stage.get_pos()
        return {"new_position": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}}

    def _cmd_home(self, x: bool = True, y: bool = True, z: bool = True) -> Dict[str, Any]:
        """Home the stage axes."""
        if x or y or z:
            self.microscope.home_xyz()
        pos = self.microscope.stage.get_pos()
        return {"homed": True, "position": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}}

    def _cmd_start_live(self) -> Dict[str, Any]:
        """Start live imaging."""
        self.microscope.start_live()
        return {"live": True}

    def _cmd_stop_live(self) -> Dict[str, Any]:
        """Stop live imaging."""
        self.microscope.stop_live()
        return {"live": False}

    def _cmd_acquire_image(self, save_path: Optional[str] = None) -> Dict[str, Any]:
        """Acquire a single image."""
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

    def _cmd_set_channel(self, channel_name: str) -> Dict[str, Any]:
        """Set the current imaging channel/mode."""
        objective = self.microscope.objective_store.current_objective
        channel_config = self.microscope.channel_configuration_mananger.get_channel_configuration_by_name(
            objective, channel_name
        )
        if channel_config:
            self.microscope.live_controller.set_microscope_mode(channel_config)
            return {"channel": channel_name, "objective": objective}
        else:
            raise ValueError(f"Channel '{channel_name}' not found for objective '{objective}'")

    def _cmd_get_channels(self) -> Dict[str, Any]:
        """Get available channels for current objective."""
        objective = self.microscope.objective_store.current_objective
        channels = self.microscope.channel_configuration_mananger.get_channel_configurations_for_objective(objective)
        return {"objective": objective, "channels": [ch.name for ch in channels] if channels else []}

    def _cmd_set_exposure(self, exposure_ms: float, channel: Optional[str] = None) -> Dict[str, Any]:
        """Set camera exposure time."""
        if channel:
            objective = self.microscope.objective_store.current_objective
            self.microscope.set_exposure_time(channel, exposure_ms, objective)
        else:
            self.microscope.camera.set_exposure_time(exposure_ms)
        return {"exposure_ms": exposure_ms}

    def _cmd_set_illumination_intensity(self, channel: str, intensity: float) -> Dict[str, Any]:
        """Set illumination intensity for a channel."""
        self.microscope.set_illumination_intensity(channel, intensity)
        return {"channel": channel, "intensity": intensity}

    def _cmd_get_objectives(self) -> Dict[str, Any]:
        """Get available objectives."""
        objectives = list(self.microscope.objective_store.objectives_dict.keys())
        current = self.microscope.objective_store.current_objective
        return {"objectives": objectives, "current": current}

    def _cmd_set_objective(self, objective_name: str) -> Dict[str, Any]:
        """Set the current objective."""
        self.microscope.set_objective(objective_name)
        return {"objective": objective_name}

    def _cmd_get_current_objective(self) -> Dict[str, Any]:
        """Get the current objective."""
        return {"objective": self.microscope.objective_store.current_objective}

    def _cmd_turn_on_illumination(self) -> Dict[str, Any]:
        """Turn on illumination."""
        self.microscope.live_controller.turn_on_illumination()
        return {"illumination": "on"}

    def _cmd_turn_off_illumination(self) -> Dict[str, Any]:
        """Turn off illumination."""
        self.microscope.live_controller.turn_off_illumination()
        return {"illumination": "off"}

    def _cmd_get_status(self) -> Dict[str, Any]:
        """Get comprehensive microscope status."""
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

    def _cmd_autofocus(self) -> Dict[str, Any]:
        """Run autofocus (if available)."""
        # This would need to be implemented based on the autofocus controller
        # For now, return not implemented
        return {"error": "Autofocus via control server not yet implemented"}

    def _cmd_acquire_laser_af_image(
        self, save_path: Optional[str] = None, use_last_frame: bool = True
    ) -> Dict[str, Any]:
        """Acquire an image from the laser autofocus camera.

        Args:
            save_path: Optional path to save the image
            use_last_frame: If True, get the last captured frame. If False, trigger a new capture.
        """
        if not self.microscope.addons.camera_focus:
            return {"error": "Laser AF camera not available"}

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

    def _cmd_run_acquisition(
        self,
        wells: str,
        channels: list,
        nx: int = 2,
        ny: int = 2,
        experiment_id: Optional[str] = None,
        base_path: Optional[str] = None,
        wellplate_format: str = "96 well plate",
        overlap_percent: float = 10.0,
    ) -> Dict[str, Any]:
        """Run a multi-point acquisition using the existing MultiPointController.

        This method uses the GUI's MultiPointController infrastructure for acquisitions,
        which handles image display, saving, autofocus, and other features automatically.

        Args:
            wells: Well selection string, e.g., "A1:B3" or "A1,A2,B1"
            channels: List of channel names to acquire
            nx: Number of sites in X per well (default: 2)
            ny: Number of sites in Y per well (default: 2)
            experiment_id: Optional experiment ID (auto-generated if not provided)
            base_path: Optional base path for saving
            wellplate_format: Wellplate format (default: "96 well plate")
            overlap_percent: Overlap between FOVs (default: 10%)
        """
        import control._def

        # Check requirements
        if not self.multipoint_controller:
            return {
                "error": "MultiPointController not available. Make sure the GUI is running with control server enabled."
            }

        if not self.scan_coordinates:
            return {"error": "ScanCoordinates not available. Make sure the GUI is running with control server enabled."}

        # Check if acquisition already running
        if self.multipoint_controller.acquisition_in_progress():
            return {"error": "Acquisition already in progress"}

        # Parse well coordinates
        wellplate_settings = control._def.get_wellplate_settings(wellplate_format)
        well_coords = self._parse_wells(wells, wellplate_settings)

        if not well_coords:
            return {"error": f"Could not parse wells: {wells}"}

        # Validate channels exist
        objective = self.microscope.objective_store.current_objective
        available_channels = self.microscope.channel_configuration_mananger.get_channel_configurations_for_objective(
            objective
        )
        available_channel_names = [ch.name for ch in available_channels] if available_channels else []

        invalid_channels = [ch for ch in channels if ch not in available_channel_names]
        if invalid_channels:
            return {"error": f"Invalid channels: {invalid_channels}. Available: {available_channel_names}"}

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
            return {"error": f"Failed to start acquisition: {str(e)}"}

    def _parse_wells(self, wells: str, wellplate_settings: dict) -> Dict[str, tuple]:
        """Parse well string like 'A1:B3' or 'A1,A2,B1' into coordinates."""
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

    def _cmd_get_acquisition_status(self) -> Dict[str, Any]:
        """Get the status of the current acquisition."""
        if not self.multipoint_controller:
            return {"error": "MultiPointController not available"}

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

    def _cmd_abort_acquisition(self) -> Dict[str, Any]:
        """Abort the current acquisition."""
        if not self.multipoint_controller:
            return {"error": "MultiPointController not available"}

        if not self.multipoint_controller.acquisition_in_progress():
            return {"error": "No acquisition in progress"}

        # Use the controller's abort mechanism
        self.multipoint_controller.request_abort_aquisition()
        return {"aborted": True}

    def _cmd_set_performance_mode(self, enabled: bool) -> Dict[str, Any]:
        """Enable or disable performance mode.

        Performance mode disables the mosaic view during acquisitions to save RAM.
        """
        if not self.gui:
            return {"error": "GUI reference not available"}

        if not hasattr(self.gui, "performanceModeToggle"):
            return {"error": "Performance mode toggle not available in GUI"}

        # Toggle the button which triggers the mode change
        self.gui.performanceModeToggle.setChecked(enabled)
        self.gui.togglePerformanceMode()

        return {
            "performance_mode": self.gui.performance_mode,
            "message": f"Performance mode {'enabled' if enabled else 'disabled'}",
        }

    def _cmd_get_performance_mode(self) -> Dict[str, Any]:
        """Get the current performance mode state."""
        if not self.gui:
            return {"error": "GUI reference not available"}

        return {"performance_mode": getattr(self.gui, "performance_mode", False)}


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
            if b"\n" in buffer:
                break

        return json.loads(buffer.decode("utf-8").strip())
