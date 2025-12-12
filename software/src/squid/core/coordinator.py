"""Resource Coordinator for managing shared resources and global mode.

The ResourceCoordinator prevents resource conflicts by managing leases
on shared resources (camera, stage, illumination, etc.). Only one owner
can hold a resource at a time, and the global mode is derived from
active leases.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, FrozenSet, Optional, Set

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


class Resource(Enum):
    """Shared hardware resources that can be leased."""

    CAMERA_CONTROL = auto()
    STAGE_CONTROL = auto()
    ILLUMINATION_CONTROL = auto()
    FLUIDICS_CONTROL = auto()
    FOCUS_AUTHORITY = auto()


class GlobalMode(Enum):
    """Global operating mode derived from active leases."""

    IDLE = auto()
    LIVE = auto()
    ACQUIRING = auto()
    ABORTING = auto()
    ERROR = auto()


@dataclass
class ResourceLease:
    """A lease on one or more resources.

    Attributes:
        lease_id: Unique identifier for this lease
        owner: Name/identifier of the owner (e.g., "LiveController")
        resources: Set of resources held by this lease
        acquired_at: Timestamp when lease was acquired
        expires_at: Optional expiration timestamp (None = no expiration)
        mode: The GlobalMode this lease represents
    """

    lease_id: str
    owner: str
    resources: FrozenSet[Resource]
    acquired_at: float
    expires_at: Optional[float] = None
    mode: GlobalMode = GlobalMode.IDLE


# Type alias for mode change callbacks
ModeChangeCallback = Callable[[GlobalMode, GlobalMode], None]
LeaseCallback = Callable[[ResourceLease], None]
LeaseRevokedCallback = Callable[[ResourceLease, str], None]


class ResourceCoordinator:
    """Coordinates access to shared resources and tracks global mode.

    The coordinator ensures:
    1. Only one owner can hold a resource at a time
    2. Global mode is derived from active leases
    3. Expired leases are automatically cleaned up
    4. Callbacks are invoked for mode changes and lease events

    Usage:
        coordinator = ResourceCoordinator()
        coordinator.start()

        # Acquire resources for live view
        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL, Resource.ILLUMINATION_CONTROL},
            owner="LiveController",
            mode=GlobalMode.LIVE,
        )

        # Release when done
        coordinator.release(lease)

        coordinator.stop()
    """

    def __init__(
        self,
        watchdog_interval_s: float = 1.0,
        default_lease_timeout_s: Optional[float] = None,
    ):
        """Initialize the ResourceCoordinator.

        Args:
            watchdog_interval_s: How often to check for expired leases
            default_lease_timeout_s: Default timeout for new leases (None = no timeout)
        """
        self._lock = threading.RLock()
        self._leases: Dict[str, ResourceLease] = {}
        self._resource_owners: Dict[Resource, str] = {}
        self._current_mode = GlobalMode.IDLE

        self._watchdog_interval_s = watchdog_interval_s
        self._default_lease_timeout_s = default_lease_timeout_s

        self._watchdog_thread: Optional[threading.Thread] = None
        self._running = False

        # Callbacks
        self._mode_change_callbacks: list[ModeChangeCallback] = []
        self._lease_acquired_callbacks: list[LeaseCallback] = []
        self._lease_released_callbacks: list[LeaseCallback] = []
        self._lease_revoked_callbacks: list[LeaseRevokedCallback] = []

    def start(self) -> None:
        """Start the watchdog thread for expired lease cleanup."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                name="ResourceCoordinatorWatchdog",
                daemon=True,
            )
            self._watchdog_thread.start()
            _log.info("ResourceCoordinator started")

    def stop(self, timeout_s: float = 2.0) -> None:
        """Stop the watchdog thread.

        Args:
            timeout_s: Timeout for thread join
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=timeout_s)
            if self._watchdog_thread.is_alive():
                _log.warning("Watchdog thread did not stop in time")
            self._watchdog_thread = None

        _log.info("ResourceCoordinator stopped")

    @property
    def mode(self) -> GlobalMode:
        """Get the current global mode."""
        with self._lock:
            return self._current_mode

    def acquire(
        self,
        resources: Set[Resource],
        owner: str,
        mode: GlobalMode = GlobalMode.IDLE,
        timeout_s: Optional[float] = None,
    ) -> Optional[ResourceLease]:
        """Acquire a lease on the specified resources.

        Args:
            resources: Set of resources to acquire
            owner: Identifier for the owner
            mode: The GlobalMode this lease represents
            timeout_s: Lease timeout (None uses default, 0 = no timeout)

        Returns:
            ResourceLease if successful, None if resources unavailable
        """
        if not resources:
            _log.warning(f"acquire() called with empty resources by {owner}")
            return None

        frozen_resources = frozenset(resources)
        now = time.time()

        with self._lock:
            # Check if all resources are available
            conflicts = self._get_conflicts(frozen_resources)
            if conflicts:
                conflict_info = ", ".join(
                    f"{r.name} (held by {self._resource_owners[r]})"
                    for r in conflicts
                )
                _log.debug(
                    f"Cannot acquire resources for {owner}: conflicts with {conflict_info}"
                )
                return None

            # Create lease
            lease_id = str(uuid.uuid4())
            lease_timeout = timeout_s if timeout_s is not None else self._default_lease_timeout_s
            expires_at = now + lease_timeout if lease_timeout else None

            lease = ResourceLease(
                lease_id=lease_id,
                owner=owner,
                resources=frozen_resources,
                acquired_at=now,
                expires_at=expires_at,
                mode=mode,
            )

            # Register lease
            self._leases[lease_id] = lease
            for resource in frozen_resources:
                self._resource_owners[resource] = lease_id

            _log.info(
                f"Lease {lease_id[:8]} acquired by {owner} for "
                f"{[r.name for r in frozen_resources]}"
            )

            # Update mode
            old_mode = self._current_mode
            self._current_mode = self._derive_mode()
            new_mode = self._current_mode

        # Callbacks outside lock
        self._fire_lease_acquired(lease)
        if old_mode != new_mode:
            self._fire_mode_change(old_mode, new_mode)

        return lease

    def release(self, lease: ResourceLease) -> bool:
        """Release a lease.

        Args:
            lease: The lease to release

        Returns:
            True if lease was released, False if not found
        """
        with self._lock:
            if lease.lease_id not in self._leases:
                _log.warning(f"Attempted to release unknown lease {lease.lease_id[:8]}")
                return False

            self._remove_lease(lease.lease_id)
            old_mode = self._current_mode
            self._current_mode = self._derive_mode()
            new_mode = self._current_mode

        _log.info(f"Lease {lease.lease_id[:8]} released by {lease.owner}")

        # Callbacks outside lock
        self._fire_lease_released(lease)
        if old_mode != new_mode:
            self._fire_mode_change(old_mode, new_mode)

        return True

    def can_acquire(self, resources: Set[Resource]) -> bool:
        """Check if resources can be acquired without actually acquiring.

        Args:
            resources: Set of resources to check

        Returns:
            True if all resources are available
        """
        if not resources:
            return True

        with self._lock:
            conflicts = self._get_conflicts(frozenset(resources))
            return len(conflicts) == 0

    def get_lease_owner(self, resource: Resource) -> Optional[str]:
        """Get the owner of a resource lease.

        Args:
            resource: The resource to check

        Returns:
            Owner name if leased, None otherwise
        """
        with self._lock:
            lease_id = self._resource_owners.get(resource)
            if lease_id is None:
                return None
            lease = self._leases.get(lease_id)
            return lease.owner if lease else None

    def get_active_leases(self) -> list[ResourceLease]:
        """Get all active leases.

        Returns:
            List of active leases
        """
        with self._lock:
            return list(self._leases.values())

    def force_release_all(self, reason: str = "forced release") -> int:
        """Force release all leases (emergency cleanup).

        Args:
            reason: Reason for the forced release

        Returns:
            Number of leases released
        """
        with self._lock:
            leases_to_revoke = list(self._leases.values())
            for lease in leases_to_revoke:
                self._remove_lease(lease.lease_id)

            old_mode = self._current_mode
            self._current_mode = GlobalMode.IDLE
            new_mode = self._current_mode

        # Fire callbacks outside lock
        for lease in leases_to_revoke:
            _log.warning(f"Lease {lease.lease_id[:8]} revoked: {reason}")
            self._fire_lease_revoked(lease, reason)

        if old_mode != new_mode:
            self._fire_mode_change(old_mode, new_mode)

        return len(leases_to_revoke)

    # Callback registration methods

    def on_mode_change(self, callback: ModeChangeCallback) -> None:
        """Register a callback for mode changes."""
        self._mode_change_callbacks.append(callback)

    def on_lease_acquired(self, callback: LeaseCallback) -> None:
        """Register a callback for lease acquisition."""
        self._lease_acquired_callbacks.append(callback)

    def on_lease_released(self, callback: LeaseCallback) -> None:
        """Register a callback for lease release."""
        self._lease_released_callbacks.append(callback)

    def on_lease_revoked(self, callback: LeaseRevokedCallback) -> None:
        """Register a callback for lease revocation."""
        self._lease_revoked_callbacks.append(callback)

    # Private methods

    def _get_conflicts(self, resources: FrozenSet[Resource]) -> Set[Resource]:
        """Get resources that are already held."""
        return {r for r in resources if r in self._resource_owners}

    def _remove_lease(self, lease_id: str) -> None:
        """Remove a lease from internal tracking. Must hold lock."""
        lease = self._leases.pop(lease_id, None)
        if lease:
            for resource in lease.resources:
                if self._resource_owners.get(resource) == lease_id:
                    del self._resource_owners[resource]

    def _derive_mode(self) -> GlobalMode:
        """Derive global mode from active leases. Must hold lock."""
        if not self._leases:
            return GlobalMode.IDLE

        # Priority: ERROR > ABORTING > ACQUIRING > LIVE > IDLE
        modes = {lease.mode for lease in self._leases.values()}

        if GlobalMode.ERROR in modes:
            return GlobalMode.ERROR
        if GlobalMode.ABORTING in modes:
            return GlobalMode.ABORTING
        if GlobalMode.ACQUIRING in modes:
            return GlobalMode.ACQUIRING
        if GlobalMode.LIVE in modes:
            return GlobalMode.LIVE
        return GlobalMode.IDLE

    def _watchdog_loop(self) -> None:
        """Watchdog thread loop - checks for expired leases."""
        while self._running:
            time.sleep(self._watchdog_interval_s)
            self._check_expired_leases()

    def _check_expired_leases(self) -> None:
        """Check for and revoke expired leases."""
        now = time.time()
        leases_to_revoke: list[ResourceLease] = []

        with self._lock:
            for lease_id, lease in list(self._leases.items()):
                if lease.expires_at is not None and now > lease.expires_at:
                    leases_to_revoke.append(lease)
                    self._remove_lease(lease_id)

            if leases_to_revoke:
                old_mode = self._current_mode
                self._current_mode = self._derive_mode()
                new_mode = self._current_mode
            else:
                old_mode = new_mode = self._current_mode

        # Fire callbacks outside lock
        for lease in leases_to_revoke:
            _log.warning(f"Lease {lease.lease_id[:8]} expired for {lease.owner}")
            self._fire_lease_revoked(lease, "expired")

        if old_mode != new_mode:
            self._fire_mode_change(old_mode, new_mode)

    def _fire_mode_change(self, old_mode: GlobalMode, new_mode: GlobalMode) -> None:
        """Fire mode change callbacks."""
        _log.info(f"Global mode changed: {old_mode.name} -> {new_mode.name}")
        for callback in self._mode_change_callbacks:
            try:
                callback(old_mode, new_mode)
            except Exception as e:
                _log.exception(f"Error in mode change callback: {e}")

    def _fire_lease_acquired(self, lease: ResourceLease) -> None:
        """Fire lease acquired callbacks."""
        for callback in self._lease_acquired_callbacks:
            try:
                callback(lease)
            except Exception as e:
                _log.exception(f"Error in lease acquired callback: {e}")

    def _fire_lease_released(self, lease: ResourceLease) -> None:
        """Fire lease released callbacks."""
        for callback in self._lease_released_callbacks:
            try:
                callback(lease)
            except Exception as e:
                _log.exception(f"Error in lease released callback: {e}")

    def _fire_lease_revoked(self, lease: ResourceLease, reason: str) -> None:
        """Fire lease revoked callbacks."""
        for callback in self._lease_revoked_callbacks:
            try:
                callback(lease, reason)
            except Exception as e:
                _log.exception(f"Error in lease revoked callback: {e}")
