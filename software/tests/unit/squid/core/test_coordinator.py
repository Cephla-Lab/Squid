"""Tests for ResourceCoordinator."""

import threading
import time

import pytest

from squid.core.coordinator import (
    GlobalMode,
    Resource,
    ResourceCoordinator,
    ResourceLease,
)


class TestResource:
    """Tests for Resource enum."""

    def test_resource_values_exist(self):
        """All expected resource types should exist."""
        assert Resource.CAMERA_CONTROL
        assert Resource.STAGE_CONTROL
        assert Resource.ILLUMINATION_CONTROL
        assert Resource.FLUIDICS_CONTROL
        assert Resource.FOCUS_AUTHORITY


class TestGlobalMode:
    """Tests for GlobalMode enum."""

    def test_global_mode_values_exist(self):
        """All expected mode values should exist."""
        assert GlobalMode.IDLE
        assert GlobalMode.LIVE
        assert GlobalMode.ACQUIRING
        assert GlobalMode.ABORTING
        assert GlobalMode.ERROR


class TestResourceLease:
    """Tests for ResourceLease dataclass."""

    def test_create_lease(self):
        """Should create a lease with required fields."""
        lease = ResourceLease(
            lease_id="test-123",
            owner="TestController",
            resources=frozenset({Resource.CAMERA_CONTROL}),
            acquired_at=time.time(),
        )

        assert lease.lease_id == "test-123"
        assert lease.owner == "TestController"
        assert Resource.CAMERA_CONTROL in lease.resources
        assert lease.expires_at is None
        assert lease.mode == GlobalMode.IDLE

    def test_lease_with_expiration(self):
        """Should create a lease with expiration."""
        now = time.time()
        lease = ResourceLease(
            lease_id="test-456",
            owner="TestController",
            resources=frozenset({Resource.STAGE_CONTROL}),
            acquired_at=now,
            expires_at=now + 60.0,
            mode=GlobalMode.LIVE,
        )

        assert lease.expires_at is not None
        assert lease.mode == GlobalMode.LIVE


class TestResourceCoordinator:
    """Tests for ResourceCoordinator."""

    @pytest.fixture
    def coordinator(self):
        """Create a ResourceCoordinator for testing."""
        coord = ResourceCoordinator()
        yield coord
        coord.stop()

    def test_initial_mode_is_idle(self, coordinator):
        """Initial mode should be IDLE."""
        assert coordinator.mode == GlobalMode.IDLE

    def test_acquire_single_resource(self, coordinator):
        """Should acquire a single resource."""
        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="TestController",
            mode=GlobalMode.LIVE,
        )

        assert lease is not None
        assert lease.owner == "TestController"
        assert Resource.CAMERA_CONTROL in lease.resources
        assert coordinator.mode == GlobalMode.LIVE

    def test_acquire_multiple_resources(self, coordinator):
        """Should acquire multiple resources at once."""
        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL, Resource.ILLUMINATION_CONTROL},
            owner="LiveController",
            mode=GlobalMode.LIVE,
        )

        assert lease is not None
        assert len(lease.resources) == 2
        assert Resource.CAMERA_CONTROL in lease.resources
        assert Resource.ILLUMINATION_CONTROL in lease.resources

    def test_acquire_fails_when_resource_held(self, coordinator):
        """Should fail to acquire resource held by another."""
        lease1 = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Controller1",
            mode=GlobalMode.LIVE,
        )
        assert lease1 is not None

        lease2 = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Controller2",
            mode=GlobalMode.ACQUIRING,
        )
        assert lease2 is None

    def test_acquire_partial_conflict(self, coordinator):
        """Should fail if any requested resource is held."""
        lease1 = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Controller1",
        )
        assert lease1 is not None

        # Try to acquire camera (held) + stage (free)
        lease2 = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL, Resource.STAGE_CONTROL},
            owner="Controller2",
        )
        assert lease2 is None

        # Stage should still be free
        lease3 = coordinator.acquire(
            resources={Resource.STAGE_CONTROL},
            owner="Controller2",
        )
        assert lease3 is not None

    def test_acquire_empty_resources_returns_none(self, coordinator):
        """Should return None for empty resource set."""
        lease = coordinator.acquire(
            resources=set(),
            owner="TestController",
        )
        assert lease is None

    def test_release_lease(self, coordinator):
        """Should release a lease and free resources."""
        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="TestController",
            mode=GlobalMode.LIVE,
        )
        assert lease is not None
        assert coordinator.mode == GlobalMode.LIVE

        released = coordinator.release(lease)
        assert released is True
        assert coordinator.mode == GlobalMode.IDLE

        # Resource should be available again
        lease2 = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="AnotherController",
        )
        assert lease2 is not None

    def test_release_unknown_lease_returns_false(self, coordinator):
        """Should return False when releasing unknown lease."""
        fake_lease = ResourceLease(
            lease_id="unknown-id",
            owner="FakeController",
            resources=frozenset({Resource.CAMERA_CONTROL}),
            acquired_at=time.time(),
        )

        released = coordinator.release(fake_lease)
        assert released is False

    def test_can_acquire_when_available(self, coordinator):
        """can_acquire should return True when resources available."""
        assert coordinator.can_acquire({Resource.CAMERA_CONTROL}) is True
        assert coordinator.can_acquire({Resource.CAMERA_CONTROL, Resource.STAGE_CONTROL}) is True

    def test_can_acquire_when_held(self, coordinator):
        """can_acquire should return False when resources held."""
        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Controller1",
        )
        assert lease is not None

        assert coordinator.can_acquire({Resource.CAMERA_CONTROL}) is False
        assert coordinator.can_acquire({Resource.CAMERA_CONTROL, Resource.STAGE_CONTROL}) is False
        assert coordinator.can_acquire({Resource.STAGE_CONTROL}) is True

    def test_can_acquire_empty_returns_true(self, coordinator):
        """can_acquire with empty set should return True."""
        assert coordinator.can_acquire(set()) is True

    def test_get_lease_owner(self, coordinator):
        """Should return lease owner for held resource."""
        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="TestController",
        )
        assert lease is not None

        owner = coordinator.get_lease_owner(Resource.CAMERA_CONTROL)
        assert owner == "TestController"

        # Unheld resource
        owner2 = coordinator.get_lease_owner(Resource.STAGE_CONTROL)
        assert owner2 is None

    def test_get_active_leases(self, coordinator):
        """Should return list of active leases."""
        lease1 = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Controller1",
        )
        lease2 = coordinator.acquire(
            resources={Resource.STAGE_CONTROL},
            owner="Controller2",
        )
        assert lease1 is not None
        assert lease2 is not None

        leases = coordinator.get_active_leases()
        assert len(leases) == 2
        lease_ids = {l.lease_id for l in leases}
        assert lease1.lease_id in lease_ids
        assert lease2.lease_id in lease_ids

    def test_force_release_all(self, coordinator):
        """force_release_all should release all leases."""
        lease1 = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Controller1",
            mode=GlobalMode.LIVE,
        )
        lease2 = coordinator.acquire(
            resources={Resource.STAGE_CONTROL},
            owner="Controller2",
            mode=GlobalMode.ACQUIRING,
        )
        assert lease1 is not None
        assert lease2 is not None

        count = coordinator.force_release_all("test cleanup")
        assert count == 2
        assert coordinator.mode == GlobalMode.IDLE
        assert len(coordinator.get_active_leases()) == 0

    def test_mode_priority_error_highest(self, coordinator):
        """ERROR mode should have highest priority."""
        coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Live",
            mode=GlobalMode.LIVE,
        )
        coordinator.acquire(
            resources={Resource.STAGE_CONTROL},
            owner="Acquisition",
            mode=GlobalMode.ACQUIRING,
        )
        coordinator.acquire(
            resources={Resource.FLUIDICS_CONTROL},
            owner="Error",
            mode=GlobalMode.ERROR,
        )

        assert coordinator.mode == GlobalMode.ERROR

    def test_mode_priority_acquiring_over_live(self, coordinator):
        """ACQUIRING should take precedence over LIVE."""
        coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Live",
            mode=GlobalMode.LIVE,
        )
        coordinator.acquire(
            resources={Resource.STAGE_CONTROL},
            owner="Acquisition",
            mode=GlobalMode.ACQUIRING,
        )

        assert coordinator.mode == GlobalMode.ACQUIRING

    def test_mode_priority_aborting_over_acquiring(self, coordinator):
        """ABORTING should take precedence over ACQUIRING."""
        coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Acquisition",
            mode=GlobalMode.ACQUIRING,
        )
        coordinator.acquire(
            resources={Resource.STAGE_CONTROL},
            owner="Abort",
            mode=GlobalMode.ABORTING,
        )

        assert coordinator.mode == GlobalMode.ABORTING


class TestResourceCoordinatorCallbacks:
    """Tests for ResourceCoordinator callbacks."""

    @pytest.fixture
    def coordinator(self):
        """Create a ResourceCoordinator for testing."""
        coord = ResourceCoordinator()
        yield coord
        coord.stop()

    def test_mode_change_callback(self, coordinator):
        """Should fire mode change callback on acquire/release."""
        changes = []
        coordinator.on_mode_change(lambda old, new: changes.append((old, new)))

        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Test",
            mode=GlobalMode.LIVE,
        )
        assert (GlobalMode.IDLE, GlobalMode.LIVE) in changes

        coordinator.release(lease)
        assert (GlobalMode.LIVE, GlobalMode.IDLE) in changes

    def test_lease_acquired_callback(self, coordinator):
        """Should fire lease acquired callback."""
        acquired = []
        coordinator.on_lease_acquired(lambda lease: acquired.append(lease))

        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Test",
        )

        assert len(acquired) == 1
        assert acquired[0].lease_id == lease.lease_id

    def test_lease_released_callback(self, coordinator):
        """Should fire lease released callback."""
        released = []
        coordinator.on_lease_released(lambda lease: released.append(lease))

        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Test",
        )
        coordinator.release(lease)

        assert len(released) == 1
        assert released[0].lease_id == lease.lease_id

    def test_lease_revoked_callback_on_force_release(self, coordinator):
        """Should fire lease revoked callback on force_release_all."""
        revoked = []
        coordinator.on_lease_revoked(lambda lease, reason: revoked.append((lease, reason)))

        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Test",
        )
        coordinator.force_release_all("test cleanup")

        assert len(revoked) == 1
        assert revoked[0][0].lease_id == lease.lease_id
        assert revoked[0][1] == "test cleanup"


class TestResourceCoordinatorWatchdog:
    """Tests for ResourceCoordinator watchdog thread."""

    @pytest.fixture
    def coordinator(self):
        """Create a ResourceCoordinator with fast watchdog."""
        coord = ResourceCoordinator(watchdog_interval_s=0.05)
        coord.start()
        yield coord
        coord.stop()

    def test_expired_lease_is_revoked(self, coordinator):
        """Expired leases should be automatically revoked."""
        revoked = []
        coordinator.on_lease_revoked(lambda lease, reason: revoked.append((lease, reason)))

        # Create a lease that expires quickly
        lease = coordinator.acquire(
            resources={Resource.CAMERA_CONTROL},
            owner="Test",
            timeout_s=0.1,  # 100ms expiration
        )
        assert lease is not None
        assert coordinator.can_acquire({Resource.CAMERA_CONTROL}) is False

        # Wait for expiration + watchdog cycle
        time.sleep(0.2)

        # Lease should be revoked
        assert len(revoked) == 1
        assert revoked[0][0].lease_id == lease.lease_id
        assert revoked[0][1] == "expired"

        # Resource should be available again
        assert coordinator.can_acquire({Resource.CAMERA_CONTROL}) is True

    def test_start_stop_lifecycle(self, coordinator):
        """Start and stop should work correctly."""
        # Already started in fixture
        coordinator.stop()

        # Can restart
        coordinator.start()
        coordinator.start()  # Safe to call twice

        coordinator.stop()
        coordinator.stop()  # Safe to call twice


class TestResourceCoordinatorThreadSafety:
    """Thread safety tests for ResourceCoordinator."""

    def test_concurrent_acquire_attempts(self):
        """Multiple threads trying to acquire same resource."""
        coordinator = ResourceCoordinator()
        results = []
        barrier = threading.Barrier(5)

        def try_acquire(thread_id):
            barrier.wait()
            lease = coordinator.acquire(
                resources={Resource.CAMERA_CONTROL},
                owner=f"Thread-{thread_id}",
            )
            results.append((thread_id, lease is not None))

        threads = [
            threading.Thread(target=try_acquire, args=(i,))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        coordinator.stop()

        # Exactly one thread should have succeeded
        successes = [r for r in results if r[1]]
        failures = [r for r in results if not r[1]]

        assert len(successes) == 1
        assert len(failures) == 4
