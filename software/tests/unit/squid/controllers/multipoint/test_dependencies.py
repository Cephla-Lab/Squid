"""Tests for dependencies module."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from squid.backend.controllers.multipoint.dependencies import (
    AcquisitionControllers,
    AcquisitionDependencies,
    AcquisitionServices,
)


class TestAcquisitionServices:
    """Tests for AcquisitionServices dataclass."""

    def test_with_required_services_only(self):
        """Can create with only required services."""
        camera = MagicMock()
        stage = MagicMock()
        peripheral = MagicMock()
        event_bus = MagicMock()

        services = AcquisitionServices(
            camera=camera,
            stage=stage,
            peripheral=peripheral,
            event_bus=event_bus,
        )

        assert services.camera is camera
        assert services.stage is stage
        assert services.peripheral is peripheral
        assert services.event_bus is event_bus

    def test_optional_services_default_none(self):
        """Optional services default to None."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
        )

        assert services.illumination is None
        assert services.filter_wheel is None
        assert services.piezo is None
        assert services.fluidics is None
        assert services.nl5 is None
        assert services.stream_handler is None

    def test_with_all_services(self):
        """Can create with all services."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            illumination=MagicMock(),
            filter_wheel=MagicMock(),
            piezo=MagicMock(),
            fluidics=MagicMock(),
            nl5=MagicMock(),
            stream_handler=MagicMock(),
        )

        assert services.illumination is not None
        assert services.filter_wheel is not None
        assert services.piezo is not None
        assert services.fluidics is not None
        assert services.nl5 is not None
        assert services.stream_handler is not None

    def test_validate_passes_with_required(self):
        """Validation passes with all required services."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
        )
        services.validate()  # Should not raise

    def test_validate_fails_missing_camera(self):
        """Validation fails if camera is missing."""
        services = AcquisitionServices(
            camera=None,  # type: ignore[arg-type]
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
        )
        with pytest.raises(ValueError, match="camera"):
            services.validate()

    def test_validate_fails_missing_stage(self):
        """Validation fails if stage is missing."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=None,  # type: ignore[arg-type]
            peripheral=MagicMock(),
            event_bus=MagicMock(),
        )
        with pytest.raises(ValueError, match="stage"):
            services.validate()

    def test_validate_fails_missing_peripheral(self):
        """Validation fails if peripheral is missing."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=None,  # type: ignore[arg-type]
            event_bus=MagicMock(),
        )
        with pytest.raises(ValueError, match="peripheral"):
            services.validate()

    def test_validate_fails_missing_event_bus(self):
        """Validation fails if event_bus is missing."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=None,  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="event_bus"):
            services.validate()

    def test_validate_lists_all_missing(self):
        """Validation error lists all missing services."""
        services = AcquisitionServices(
            camera=None,  # type: ignore[arg-type]
            stage=None,  # type: ignore[arg-type]
            peripheral=MagicMock(),
            event_bus=MagicMock(),
        )
        with pytest.raises(ValueError, match="camera.*stage"):
            services.validate()

    def test_has_illumination_false(self):
        """has_illumination is False when None."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            illumination=None,
        )
        assert services.has_illumination is False

    def test_has_illumination_true(self):
        """has_illumination is True when set."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            illumination=MagicMock(),
        )
        assert services.has_illumination is True

    def test_has_filter_wheel_false_when_none(self):
        """has_filter_wheel is False when None."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            filter_wheel=None,
        )
        assert services.has_filter_wheel is False

    def test_has_filter_wheel_false_when_not_available(self):
        """has_filter_wheel is False when not available."""
        filter_wheel = MagicMock()
        filter_wheel.is_available.return_value = False

        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            filter_wheel=filter_wheel,
        )
        assert services.has_filter_wheel is False

    def test_has_filter_wheel_true(self):
        """has_filter_wheel is True when available."""
        filter_wheel = MagicMock()
        filter_wheel.is_available.return_value = True

        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            filter_wheel=filter_wheel,
        )
        assert services.has_filter_wheel is True

    def test_has_piezo_false_when_none(self):
        """has_piezo is False when None."""
        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            piezo=None,
        )
        assert services.has_piezo is False

    def test_has_piezo_false_when_not_available(self):
        """has_piezo is False when not available."""
        piezo = MagicMock()
        type(piezo).is_available = PropertyMock(return_value=False)

        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            piezo=piezo,
        )
        assert services.has_piezo is False

    def test_has_piezo_true(self):
        """has_piezo is True when available."""
        piezo = MagicMock()
        type(piezo).is_available = PropertyMock(return_value=True)

        services = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            piezo=piezo,
        )
        assert services.has_piezo is True

    def test_has_fluidics(self):
        """has_fluidics property."""
        services_without = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            fluidics=None,
        )
        assert services_without.has_fluidics is False

        services_with = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            fluidics=MagicMock(),
        )
        assert services_with.has_fluidics is True

    def test_has_nl5(self):
        """has_nl5 property."""
        services_without = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            nl5=None,
        )
        assert services_without.has_nl5 is False

        services_with = AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            nl5=MagicMock(),
        )
        assert services_with.has_nl5 is True


class TestAcquisitionControllers:
    """Tests for AcquisitionControllers dataclass."""

    def test_default_values(self):
        """All controllers default to None."""
        controllers = AcquisitionControllers()
        assert controllers.autofocus is None
        assert controllers.laser_autofocus is None
        assert controllers.focus_lock is None

    def test_with_all_controllers(self):
        """Can create with all controllers."""
        controllers = AcquisitionControllers(
            autofocus=MagicMock(),
            laser_autofocus=MagicMock(),
            focus_lock=MagicMock(),
        )
        assert controllers.autofocus is not None
        assert controllers.laser_autofocus is not None
        assert controllers.focus_lock is not None

    def test_validate_passes(self):
        """Validation passes (all optional)."""
        controllers = AcquisitionControllers()
        controllers.validate()  # Should not raise

    def test_has_contrast_af_false(self):
        """has_contrast_af is False when None."""
        controllers = AcquisitionControllers()
        assert controllers.has_contrast_af is False

    def test_has_contrast_af_true(self):
        """has_contrast_af is True when set."""
        controllers = AcquisitionControllers(autofocus=MagicMock())
        assert controllers.has_contrast_af is True

    def test_has_laser_af_false(self):
        """has_laser_af is False when None."""
        controllers = AcquisitionControllers()
        assert controllers.has_laser_af is False

    def test_has_laser_af_true(self):
        """has_laser_af is True when set."""
        controllers = AcquisitionControllers(laser_autofocus=MagicMock())
        assert controllers.has_laser_af is True

    def test_has_focus_lock_false(self):
        """has_focus_lock is False when None."""
        controllers = AcquisitionControllers()
        assert controllers.has_focus_lock is False

    def test_has_focus_lock_true(self):
        """has_focus_lock is True when set."""
        controllers = AcquisitionControllers(focus_lock=MagicMock())
        assert controllers.has_focus_lock is True

    def test_has_any_autofocus_none(self):
        """has_any_autofocus is False when none set."""
        controllers = AcquisitionControllers()
        assert controllers.has_any_autofocus is False

    def test_has_any_autofocus_contrast_only(self):
        """has_any_autofocus is True with contrast AF."""
        controllers = AcquisitionControllers(autofocus=MagicMock())
        assert controllers.has_any_autofocus is True

    def test_has_any_autofocus_laser_only(self):
        """has_any_autofocus is True with laser AF."""
        controllers = AcquisitionControllers(laser_autofocus=MagicMock())
        assert controllers.has_any_autofocus is True

    def test_has_any_autofocus_both(self):
        """has_any_autofocus is True with both."""
        controllers = AcquisitionControllers(
            autofocus=MagicMock(), laser_autofocus=MagicMock()
        )
        assert controllers.has_any_autofocus is True


class TestAcquisitionDependencies:
    """Tests for AcquisitionDependencies dataclass."""

    def _make_services(self):
        """Create a valid AcquisitionServices instance."""
        return AcquisitionServices(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
        )

    def test_basic_creation(self):
        """Can create with services and controllers."""
        services = self._make_services()
        controllers = AcquisitionControllers()

        deps = AcquisitionDependencies(services=services, controllers=controllers)

        assert deps.services is services
        assert deps.controllers is controllers

    def test_validate_passes(self):
        """Validation passes with valid services."""
        deps = AcquisitionDependencies(
            services=self._make_services(), controllers=AcquisitionControllers()
        )
        deps.validate()  # Should not raise

    def test_validate_fails_with_missing_service(self):
        """Validation fails if services validation fails."""
        services = AcquisitionServices(
            camera=None,  # type: ignore[arg-type]
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
        )
        deps = AcquisitionDependencies(
            services=services, controllers=AcquisitionControllers()
        )
        with pytest.raises(ValueError, match="camera"):
            deps.validate()

    def test_create_factory_method(self):
        """create() factory method builds dependencies."""
        camera = MagicMock()
        stage = MagicMock()
        peripheral = MagicMock()
        event_bus = MagicMock()
        autofocus = MagicMock()

        deps = AcquisitionDependencies.create(
            camera=camera,
            stage=stage,
            peripheral=peripheral,
            event_bus=event_bus,
            autofocus=autofocus,
        )

        assert deps.services.camera is camera
        assert deps.services.stage is stage
        assert deps.services.peripheral is peripheral
        assert deps.services.event_bus is event_bus
        assert deps.controllers.autofocus is autofocus

    def test_create_with_all_optional(self):
        """create() accepts all optional parameters."""
        deps = AcquisitionDependencies.create(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
            illumination=MagicMock(),
            filter_wheel=MagicMock(),
            piezo=MagicMock(),
            fluidics=MagicMock(),
            nl5=MagicMock(),
            stream_handler=MagicMock(),
            autofocus=MagicMock(),
            laser_autofocus=MagicMock(),
            focus_lock=MagicMock(),
        )

        assert deps.services.illumination is not None
        assert deps.services.filter_wheel is not None
        assert deps.services.piezo is not None
        assert deps.services.fluidics is not None
        assert deps.services.nl5 is not None
        assert deps.services.stream_handler is not None
        assert deps.controllers.autofocus is not None
        assert deps.controllers.laser_autofocus is not None
        assert deps.controllers.focus_lock is not None

    def test_create_validates(self):
        """Dependencies from create() can be validated."""
        deps = AcquisitionDependencies.create(
            camera=MagicMock(),
            stage=MagicMock(),
            peripheral=MagicMock(),
            event_bus=MagicMock(),
        )
        deps.validate()  # Should not raise
