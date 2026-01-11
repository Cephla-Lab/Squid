"""
Fault injection for error scenario testing.

This module provides tools for injecting faults into simulated hardware
to test error handling and recovery in the acquisition pipeline.
"""

from __future__ import annotations

import functools
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from tests.harness.core.backend_context import BackendContext


class StageFaultInjector:
    """Inject faults into stage movement."""

    def __init__(self, ctx: "BackendContext"):
        self._ctx = ctx
        self._move_count = 0
        self._fail_after_moves: Optional[int] = None
        self._original_methods: dict = {}
        self._installed = False

    def fail_after(self, n_moves: int) -> None:
        """
        Configure stage to fail after N moves.

        Args:
            n_moves: Number of successful moves before failure
        """
        self._fail_after_moves = n_moves
        self._move_count = 0
        self._install_hooks()

    def _install_hooks(self) -> None:
        """Install fault injection hooks on stage methods."""
        if self._installed:
            return

        stage = self._ctx.microscope.stage
        if stage is None:
            return

        # Wrap both relative and absolute move methods
        methods_to_wrap = [
            ("move_x", "X"),
            ("move_y", "Y"),
            ("move_z", "Z"),
            ("move_x_to", "X"),
            ("move_y_to", "Y"),
            ("move_z_to", "Z"),
        ]

        for method_name, axis in methods_to_wrap:
            if hasattr(stage, method_name):
                original = getattr(stage, method_name)
                self._original_methods[method_name] = original
                setattr(stage, method_name, self._wrap_move(original, axis))

        self._installed = True

    def _wrap_move(self, original: Callable, axis: str) -> Callable:
        """Create a wrapper that can inject faults."""
        @functools.wraps(original)
        def wrapper(*args, **kwargs):
            self._move_count += 1
            if (
                self._fail_after_moves is not None
                and self._move_count > self._fail_after_moves
            ):
                raise RuntimeError(
                    f"Injected stage {axis} fault after {self._fail_after_moves} moves"
                )
            return original(*args, **kwargs)
        return wrapper

    def reset(self) -> None:
        """Remove fault injection hooks and reset state."""
        if not self._installed:
            return

        stage = self._ctx.microscope.stage
        if stage is None:
            return

        for method_name, original in self._original_methods.items():
            if hasattr(stage, method_name):
                setattr(stage, method_name, original)

        self._original_methods = {}
        self._installed = False
        self._move_count = 0
        self._fail_after_moves = None


class CameraFaultInjector:
    """Inject faults into camera frame capture."""

    def __init__(self, ctx: "BackendContext"):
        self._ctx = ctx
        self._frame_count = 0
        self._fail_after_frames: Optional[int] = None
        self._timeout_frames: set = set()
        self._corrupt_frames: set = set()
        self._original_read_frame = None
        self._installed = False

    def fail_after(self, n_frames: int) -> None:
        """
        Configure camera to fail after N frames.

        Args:
            n_frames: Number of successful frames before failure
        """
        self._fail_after_frames = n_frames
        self._frame_count = 0
        self._install_hooks()

    def timeout_at(self, frame_indices: list) -> None:
        """
        Configure specific frames to timeout.

        Args:
            frame_indices: List of frame indices that should timeout
        """
        self._timeout_frames = set(frame_indices)
        self._install_hooks()

    def corrupt_at(self, frame_indices: list) -> None:
        """
        Configure specific frames to return corrupt data.

        Args:
            frame_indices: List of frame indices that should be corrupted
        """
        self._corrupt_frames = set(frame_indices)
        self._install_hooks()

    def _install_hooks(self) -> None:
        """Install fault injection hooks on camera service."""
        if self._installed:
            return

        # Hook into the camera service's read_frame method
        camera_service = self._ctx.camera_service
        if camera_service is None:
            return

        self._original_read_frame = camera_service.read_frame

        @functools.wraps(self._original_read_frame)
        def wrapped_read_frame(*args, **kwargs):
            self._frame_count += 1

            # Check for configured failures
            if (
                self._fail_after_frames is not None
                and self._frame_count > self._fail_after_frames
            ):
                raise RuntimeError(
                    f"Injected camera fault after {self._fail_after_frames} frames"
                )

            if self._frame_count - 1 in self._timeout_frames:
                # Simulate timeout by returning None
                return None

            frame = self._original_read_frame(*args, **kwargs)

            if self._frame_count - 1 in self._corrupt_frames:
                # Corrupt the frame by zeroing it
                if frame is not None:
                    frame[:] = 0

            return frame

        camera_service.read_frame = wrapped_read_frame
        self._installed = True

    def reset(self) -> None:
        """Remove fault injection hooks and reset state."""
        if not self._installed:
            return

        camera_service = self._ctx.camera_service
        if camera_service is None:
            return

        if self._original_read_frame:
            camera_service.read_frame = self._original_read_frame

        self._installed = False
        self._frame_count = 0
        self._fail_after_frames = None
        self._timeout_frames = set()
        self._corrupt_frames = set()


class AutofocusFaultInjector:
    """Inject faults into autofocus operations."""

    def __init__(self, ctx: "BackendContext"):
        self._ctx = ctx
        self._af_count = 0
        self._fail_at_indices: set = set()
        self._timeout_at_indices: set = set()
        self._original_autofocus = None
        self._installed = False

    def fail_at(self, indices: list) -> None:
        """
        Configure autofocus to fail at specific invocations.

        Args:
            indices: List of AF invocation indices to fail
        """
        self._fail_at_indices = set(indices)
        self._install_hooks()

    def timeout_at(self, indices: list) -> None:
        """
        Configure autofocus to timeout at specific invocations.

        Args:
            indices: List of AF invocation indices to timeout
        """
        self._timeout_at_indices = set(indices)
        self._install_hooks()

    def _install_hooks(self) -> None:
        """Install fault injection hooks on autofocus controller."""
        if self._installed:
            return

        af_controller = self._ctx.autofocus_controller
        if af_controller is None:
            return

        self._original_autofocus = af_controller.autofocus

        def wrapped_autofocus(*args, **kwargs):
            self._af_count += 1
            idx = self._af_count - 1

            if idx in self._fail_at_indices:
                raise RuntimeError(f"Injected autofocus failure at index {idx}")

            if idx in self._timeout_at_indices:
                import time

                # Simulate timeout by sleeping longer than typical timeout
                time.sleep(30)  # This would typically be caught by a timeout
                return None

            if self._original_autofocus is not None:
                return self._original_autofocus(*args, **kwargs)
            return None

        af_controller.autofocus = wrapped_autofocus
        self._installed = True

    def reset(self) -> None:
        """Remove fault injection hooks and reset state."""
        if not self._installed:
            return

        af_controller = self._ctx.autofocus_controller
        if af_controller is None:
            return

        if self._original_autofocus:
            af_controller.autofocus = self._original_autofocus

        self._installed = False
        self._af_count = 0
        self._fail_at_indices = set()
        self._timeout_at_indices = set()


class FaultInjector:
    """
    Unified fault injection interface for error testing.

    This class provides a convenient interface for injecting faults
    into various hardware components during acquisition testing.

    Usage:
        with BackendContext() as ctx:
            faults = FaultInjector(ctx)

            # Configure faults
            faults.stage.fail_after(10)  # Stage fails after 10 moves
            faults.camera.timeout_at([0, 5])  # Frames 0 and 5 timeout

            # Run acquisition...

            # Clean up
            faults.reset()
    """

    def __init__(self, ctx: "BackendContext"):
        """
        Initialize fault injector.

        Args:
            ctx: BackendContext instance
        """
        self._ctx = ctx
        self.stage = StageFaultInjector(ctx)
        self.camera = CameraFaultInjector(ctx)
        self.autofocus = AutofocusFaultInjector(ctx)

    def reset(self) -> None:
        """Reset all fault injectors to normal operation."""
        self.stage.reset()
        self.camera.reset()
        self.autofocus.reset()

    def __enter__(self) -> "FaultInjector":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - automatically reset faults."""
        self.reset()
