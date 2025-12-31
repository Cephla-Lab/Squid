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
    ):
        self._log = squid.logging.get_logger(self.__class__.__name__)
        self.microscope = microscope
        self.host = host
        self.port = port
        self.multipoint_controller = multipoint_controller
        self.scan_coordinates = scan_coordinates
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
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket,),
                        daemon=True
                    )
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
        return {
            "moved_to": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}
        }

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
        return {
            "new_position": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}
        }

    def _cmd_home(self, x: bool = True, y: bool = True, z: bool = True) -> Dict[str, Any]:
        """Home the stage axes."""
        if x or y or z:
            self.microscope.home_xyz()
        pos = self.microscope.stage.get_pos()
        return {
            "homed": True,
            "position": {"x_mm": pos.x_mm, "y_mm": pos.y_mm, "z_mm": pos.z_mm}
        }

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
        return {
            "objective": objective,
            "channels": [ch.name for ch in channels] if channels else []
        }

    def _cmd_set_exposure(self, exposure_ms: float, channel: Optional[str] = None) -> Dict[str, Any]:
        """Set camera exposure time."""
        if channel:
            objective = self.microscope.objective_store.current_objective
            self.microscope.set_exposure_time(channel, exposure_ms, objective)
        else:
            self.microscope.camera.set_exposure_time(exposure_ms)
        return {"exposure_ms": exposure_ms}

    def _cmd_set_illumination_intensity(
        self, channel: str, intensity: float
    ) -> Dict[str, Any]:
        """Set illumination intensity for a channel."""
        self.microscope.set_illumination_intensity(channel, intensity)
        return {"channel": channel, "intensity": intensity}

    def _cmd_get_objectives(self) -> Dict[str, Any]:
        """Get available objectives."""
        objectives = list(self.microscope.objective_store.objectives_dict.keys())
        current = self.microscope.objective_store.current_objective
        return {
            "objectives": objectives,
            "current": current
        }

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
                "is_live": self.microscope.live_controller.is_live if hasattr(self.microscope.live_controller, 'is_live') else None,
            }
        }

        return status

    def _cmd_autofocus(self) -> Dict[str, Any]:
        """Run autofocus (if available)."""
        # This would need to be implemented based on the autofocus controller
        # For now, return not implemented
        return {"error": "Autofocus via control server not yet implemented"}

    def _cmd_acquire_laser_af_image(self, save_path: Optional[str] = None, use_last_frame: bool = True) -> Dict[str, Any]:
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
            current_frame = getattr(camera_focus, '_current_frame', None)
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
    ) -> Dict[str, Any]:
        """Run a multi-point acquisition using the microscope object directly.

        Args:
            wells: Well selection string, e.g., "A1:B3" or "A1,A2,B1"
            channels: List of channel names to acquire
            nx: Number of sites in X per well (default: 2)
            ny: Number of sites in Y per well (default: 2)
            experiment_id: Optional experiment ID (auto-generated if not provided)
            base_path: Optional base path for saving
            wellplate_format: Wellplate format (default: "96 well plate")
        """
        import os
        import control._def
        from datetime import datetime

        # Check if acquisition already running
        if hasattr(self, '_acquisition_running') and self._acquisition_running:
            return {"error": "Acquisition already in progress"}

        # Parse well coordinates
        wellplate_settings = control._def.get_wellplate_settings(wellplate_format)
        well_coords = self._parse_wells(wells, wellplate_settings)

        if not well_coords:
            return {"error": f"Could not parse wells: {wells}"}

        # Set up paths
        if not base_path:
            base_path = "/tmp/squid_acquisitions"
        if not experiment_id:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            experiment_id = f"acquisition_{timestamp}"

        save_dir = os.path.join(base_path, experiment_id)
        os.makedirs(save_dir, exist_ok=True)

        # Calculate site offsets (simple grid within well)
        fov_size_mm = (
            self.microscope.objective_store.get_pixel_size_factor()
            * self.microscope.camera.get_fov_size_mm()
        )
        step_size_mm = fov_size_mm * 0.9  # 10% overlap

        # Run acquisition in background thread
        def run_acquisition():
            import tifffile
            self._acquisition_running = True
            self._acquisition_progress = {"completed": 0, "total": len(well_coords) * nx * ny * len(channels)}

            try:
                self.microscope.camera.start_streaming()
                image_count = 0

                for well_id, (well_x, well_y) in well_coords.items():
                    well_dir = os.path.join(save_dir, well_id)
                    os.makedirs(well_dir, exist_ok=True)

                    for site_y in range(ny):
                        for site_x in range(nx):
                            # Calculate site position (centered grid)
                            offset_x = (site_x - (nx - 1) / 2) * step_size_mm
                            offset_y = (site_y - (ny - 1) / 2) * step_size_mm
                            pos_x = well_x + offset_x
                            pos_y = well_y + offset_y

                            # Move to position
                            self.microscope.move_x_to(pos_x, blocking=True)
                            self.microscope.move_y_to(pos_y, blocking=True)

                            site_id = site_y * nx + site_x

                            for channel in channels:
                                # Set channel
                                objective = self.microscope.objective_store.current_objective
                                channel_config = self.microscope.channel_configuration_mananger.get_channel_configuration_by_name(
                                    objective, channel
                                )
                                if channel_config:
                                    self.microscope.live_controller.set_microscope_mode(channel_config)

                                # Acquire image
                                image = self.microscope.acquire_image()

                                if image is not None:
                                    # Save image
                                    filename = f"{well_id}_s{site_id:02d}_{channel.replace(' ', '_')}.tiff"
                                    filepath = os.path.join(well_dir, filename)
                                    tifffile.imwrite(filepath, image)

                                image_count += 1
                                self._acquisition_progress["completed"] = image_count

                self.microscope.camera.stop_streaming()
                self._acquisition_progress["status"] = "completed"

            except Exception as e:
                self._acquisition_progress["status"] = f"error: {str(e)}"
            finally:
                self._acquisition_running = False

        # Start in background thread
        acq_thread = threading.Thread(target=run_acquisition, daemon=True)
        acq_thread.start()

        return {
            "started": True,
            "wells": wells,
            "well_count": len(well_coords),
            "channels": channels,
            "sites_per_well": nx * ny,
            "total_images": len(well_coords) * nx * ny * len(channels),
            "experiment_id": experiment_id,
            "save_dir": save_dir,
        }

    def _parse_wells(self, wells: str, wellplate_settings: dict) -> Dict[str, tuple]:
        """Parse well string like 'A1:B3' or 'A1,A2,B1' into coordinates."""
        import re

        def row_to_index(row: str) -> int:
            index = 0
            for char in row.upper():
                index = index * 26 + (ord(char) - ord('A') + 1)
            return index - 1

        def index_to_row(index: int) -> str:
            index += 1
            row = ""
            while index > 0:
                index -= 1
                row = chr(index % 26 + ord('A')) + row
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
        in_progress = getattr(self, '_acquisition_running', False)
        progress = getattr(self, '_acquisition_progress', {})

        return {
            "in_progress": in_progress,
            "completed": progress.get("completed", 0),
            "total": progress.get("total", 0),
            "status": progress.get("status", "running" if in_progress else "idle"),
        }

    def _cmd_abort_acquisition(self) -> Dict[str, Any]:
        """Abort the current acquisition."""
        if not getattr(self, '_acquisition_running', False):
            return {"error": "No acquisition in progress"}

        # Signal abort (acquisition loop checks this)
        self._acquisition_running = False
        return {"aborted": True}


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
