"""Free-space watchdog for long ("large") acquisitions.

A long acquisition can outrun its save disk. Rather than crashing mid-plate when a write fails, the
worker asks a ``DiskSpaceGuard`` at safe checkpoints (FOV and timepoint boundaries, and every poll
while already paused) whether there is still room, and the guard holds the acquisition's
:class:`~control.core.pause_gate.PauseGate` while there is not. Once space is freed - the operator
deletes something, or a network volume drains - the guard releases its hold and the acquisition
resumes where it stopped.

"Room" means more than the bytes of the next image: the reserve the operator wants left on the disk,
the bytes already queued for saving but not yet written (backpressure's pending bytes), and two FOVs
of headroom so the pause happens *before* the in-flight FOV runs out of disk. Release needs one extra
FOV of headroom on top of that, so a disk hovering right at the threshold does not flap between
paused and running.

Settings live in ``control._def`` and are read live on every call (never bound at import time) so an
operator or MCP client can change them during a run:

    DISK_SPACE_RESERVE_GB       free space to keep on the save disk
    DISK_SPACE_POLL_INTERVAL_S  how often the simulated-capacity path re-walks the save directory
    SIMULATED_DISK_CAPACITY_GB  dev only: pretend the save disk has this capacity (0 = real disk)

Stdlib plus ``control.utils``/``control._def`` only: no Qt, no hardware, importable anywhere.
"""

import dataclasses
import pathlib
import time
from dataclasses import dataclass
from typing import Callable, Optional

import control._def
import control.utils
import squid.logging
from control.core.pause_gate import PauseGate

_log = squid.logging.get_logger(__name__)

_BYTES_PER_GB = 1024**3


def _to_gb(n_bytes: int) -> float:
    return n_bytes / _BYTES_PER_GB


@dataclass(frozen=True)
class DiskStatus:
    """Snapshot of the save disk as of one guard evaluation.

    Attributes:
        free_bytes: Space available for new files (simulated capacity minus usage in dev mode).
        required_bytes: reserve + pending + two FOVs - the level below which the guard pauses.
        reserve_bytes: ``DISK_SPACE_RESERVE_GB`` in bytes, at the time of the evaluation.
        pending_bytes: Bytes queued for saving but not yet written.
        fov_bytes: Estimated bytes of one FOV (planes per FOV x current frame size).
        holding: True while the guard is holding the pause gate.
    """

    free_bytes: int
    required_bytes: int
    reserve_bytes: int
    pending_bytes: int
    fov_bytes: int
    holding: bool

    @property
    def ok(self) -> bool:
        return self.free_bytes >= self.required_bytes


class DiskSpaceGuard:
    """Pauses an acquisition while its save disk is too full to keep going safely."""

    REASON = "disk_space"

    def __init__(
        self,
        directory,
        pending_bytes_fn: Callable[[], int],
        frame_bytes_fn: Callable[[], int],
        planes_per_fov: int,
        free_bytes_fn: Optional[Callable[[pathlib.Path], int]] = None,
        refresh_interval_s: Optional[float] = None,
    ) -> None:
        """
        Args:
            directory: The acquisition's save directory. It need not exist yet.
            pending_bytes_fn: Bytes queued for saving (``BackpressureController.get_pending_bytes``).
            frame_bytes_fn: Bytes of one captured frame.
            planes_per_fov: Images written per FOV (z levels x channels), used to size one FOV.
            free_bytes_fn: Free-space probe, for tests. Defaults to ``utils.get_available_disk_space``.
            refresh_interval_s: Overrides ``DISK_SPACE_POLL_INTERVAL_S`` for the simulated-capacity
                directory walk, for tests.
        """
        self._directory = pathlib.Path(directory)
        self._pending_bytes_fn = pending_bytes_fn
        self._frame_bytes_fn = frame_bytes_fn
        self._planes_per_fov = int(planes_per_fov)
        self._free_bytes_fn = free_bytes_fn if free_bytes_fn is not None else control.utils.get_available_disk_space
        self._refresh_interval_s = refresh_interval_s

        self._holding = False
        self._last_status: Optional[DiskStatus] = None
        self._usage_bytes = 0
        self._usage_checked_at: Optional[float] = None

    @property
    def directory(self) -> pathlib.Path:
        return self._directory

    @property
    def last_status(self) -> Optional[DiskStatus]:
        """The status returned by the most recent ``update``, or None before the first one."""
        return self._last_status

    def fov_bytes(self) -> int:
        return self._planes_per_fov * int(self._frame_bytes_fn())

    def reserve_bytes(self) -> int:
        return int(control._def.DISK_SPACE_RESERVE_GB * _BYTES_PER_GB)

    def required_bytes(self) -> int:
        """Free space the disk must have to keep acquiring: reserve + pending + two FOVs."""
        return self.reserve_bytes() + int(self._pending_bytes_fn()) + 2 * self.fov_bytes()

    def free_bytes(self) -> int:
        """Space available for new files, from the simulated capacity in dev mode or the real disk."""
        capacity_gb = control._def.SIMULATED_DISK_CAPACITY_GB
        if capacity_gb > 0:
            return self._simulated_free_bytes(capacity_gb)
        return int(self._free_bytes_fn(self._existing_directory()))

    def status(self) -> DiskStatus:
        """Evaluate the disk. Pure computation: it neither holds nor releases the gate."""
        fov_bytes = self.fov_bytes()
        reserve_bytes = self.reserve_bytes()
        pending_bytes = int(self._pending_bytes_fn())
        return DiskStatus(
            free_bytes=self.free_bytes(),
            required_bytes=reserve_bytes + pending_bytes + 2 * fov_bytes,
            reserve_bytes=reserve_bytes,
            pending_bytes=pending_bytes,
            fov_bytes=fov_bytes,
            holding=self._holding,
        )

    def update(self, gate: PauseGate) -> DiskStatus:
        """Evaluate the disk and hold or release ``gate`` accordingly. Returns the new status.

        Holds when free space drops below ``required_bytes``; releases only once free space reaches
        ``required_bytes`` plus one more FOV, so the acquisition does not flap at the threshold.
        Only this guard's own hold is touched - other pause holders (an operator pause) are untouched.
        """
        status = self.status()

        if not self._holding and not status.ok:
            gate.hold(self.REASON)
            self._holding = True
            _log.warning(
                f"Pausing acquisition: {_to_gb(status.free_bytes):.2f} GB free on {self._directory} is below the "
                f"{_to_gb(status.required_bytes):.2f} GB required "
                f"(reserve {_to_gb(status.reserve_bytes):.2f} GB + pending {_to_gb(status.pending_bytes):.2f} GB "
                f"+ 2 FOVs {_to_gb(2 * status.fov_bytes):.2f} GB). Free up space to resume."
            )
        elif self._holding and status.free_bytes >= status.required_bytes + status.fov_bytes:
            gate.release(self.REASON)
            self._holding = False
            _log.info(
                f"Disk space recovered: {_to_gb(status.free_bytes):.2f} GB free on {self._directory} "
                f"(need {_to_gb(status.required_bytes):.2f} GB + one FOV). Resuming acquisition."
            )

        status = dataclasses.replace(status, holding=self._holding)
        self._last_status = status
        return status

    def _existing_directory(self) -> pathlib.Path:
        """The save directory, or its nearest existing parent while it has not been created yet."""
        directory = self._directory
        for candidate in (directory, *directory.parents):
            if candidate.is_dir():
                return candidate
        return directory

    def _simulated_free_bytes(self, capacity_gb: float) -> int:
        capacity_bytes = int(capacity_gb * _BYTES_PER_GB)
        interval_s = (
            self._refresh_interval_s
            if self._refresh_interval_s is not None
            else float(control._def.DISK_SPACE_POLL_INTERVAL_S)
        )
        now = time.monotonic()
        if self._usage_checked_at is None or (now - self._usage_checked_at) >= interval_s:
            self._usage_bytes = self._directory_usage()
            self._usage_checked_at = now
        return max(0, capacity_bytes - self._usage_bytes)

    def _directory_usage(self) -> int:
        if not self._directory.is_dir():
            return 0
        return control.utils.get_directory_disk_usage(self._directory)
