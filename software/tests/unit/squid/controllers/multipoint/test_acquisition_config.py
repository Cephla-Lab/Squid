"""Tests for acquisition_config module."""

import pytest

from squid.backend.controllers.multipoint.acquisition_config import (
    AcquisitionConfig,
    FocusConfig,
    FocusLockSettings,
    GridConfig,
    TimingConfig,
    ZStackConfig,
)
from squid.core.events import AutofocusMode


class TestGridConfig:
    """Tests for GridConfig dataclass."""

    def test_default_values(self):
        """Default values are reasonable."""
        config = GridConfig()
        assert config.nx == 1
        assert config.ny == 1
        assert config.dx_mm > 0
        assert config.dy_mm > 0

    def test_custom_values(self):
        """Custom values are stored correctly."""
        config = GridConfig(nx=5, ny=3, dx_mm=1.0, dy_mm=2.0)
        assert config.nx == 5
        assert config.ny == 3
        assert config.dx_mm == 1.0
        assert config.dy_mm == 2.0

    def test_total_positions(self):
        """Total positions calculated correctly."""
        config = GridConfig(nx=4, ny=3)
        assert config.total_positions == 12

    def test_rejects_zero_nx(self):
        """Rejects nx < 1."""
        with pytest.raises(ValueError, match="nx must be >= 1"):
            GridConfig(nx=0)

    def test_rejects_negative_nx(self):
        """Rejects negative nx."""
        with pytest.raises(ValueError, match="nx must be >= 1"):
            GridConfig(nx=-1)

    def test_rejects_zero_ny(self):
        """Rejects ny < 1."""
        with pytest.raises(ValueError, match="ny must be >= 1"):
            GridConfig(ny=0)

    def test_rejects_zero_dx(self):
        """Rejects dx_mm <= 0."""
        with pytest.raises(ValueError, match="dx_mm must be > 0"):
            GridConfig(dx_mm=0)

    def test_rejects_negative_dx(self):
        """Rejects negative dx_mm."""
        with pytest.raises(ValueError, match="dx_mm must be > 0"):
            GridConfig(dx_mm=-1.0)

    def test_rejects_zero_dy(self):
        """Rejects dy_mm <= 0."""
        with pytest.raises(ValueError, match="dy_mm must be > 0"):
            GridConfig(dy_mm=0)

    def test_is_frozen(self):
        """GridConfig is immutable."""
        config = GridConfig()
        with pytest.raises(AttributeError):
            config.nx = 5


class TestZStackConfig:
    """Tests for ZStackConfig dataclass."""

    def test_default_values(self):
        """Default values are reasonable."""
        config = ZStackConfig()
        assert config.nz == 1
        assert config.delta_z_um > 0
        assert config.stacking_direction in ("FROM BOTTOM", "FROM CENTER", "FROM TOP")
        assert config.z_range is None

    def test_custom_values(self):
        """Custom values are stored correctly."""
        config = ZStackConfig(
            nz=10,
            delta_z_um=2.0,
            stacking_direction="FROM CENTER",
            z_range=(0.0, 1.0),
            use_piezo=True,
        )
        assert config.nz == 10
        assert config.delta_z_um == 2.0
        assert config.stacking_direction == "FROM CENTER"
        assert config.z_range == (0.0, 1.0)
        assert config.use_piezo is True

    def test_delta_z_mm_property(self):
        """delta_z_mm converts correctly."""
        config = ZStackConfig(delta_z_um=1500.0)
        assert config.delta_z_mm == pytest.approx(1.5)

    def test_total_range_um_single_slice(self):
        """Total range is 0 for single slice."""
        config = ZStackConfig(nz=1, delta_z_um=2.0)
        assert config.total_range_um == 0.0

    def test_total_range_um_multiple_slices(self):
        """Total range calculated correctly."""
        config = ZStackConfig(nz=5, delta_z_um=2.0)
        assert config.total_range_um == pytest.approx(8.0)

    def test_rejects_zero_nz(self):
        """Rejects nz < 1."""
        with pytest.raises(ValueError, match="nz must be >= 1"):
            ZStackConfig(nz=0)

    def test_rejects_zero_delta_z(self):
        """Rejects delta_z_um <= 0."""
        with pytest.raises(ValueError, match="delta_z_um must be > 0"):
            ZStackConfig(delta_z_um=0)

    def test_rejects_invalid_stacking_direction(self):
        """Rejects invalid stacking direction."""
        with pytest.raises(ValueError, match="stacking_direction must be one of"):
            ZStackConfig(stacking_direction="INVALID")

    def test_rejects_invalid_z_range_length(self):
        """Rejects z_range with wrong length."""
        with pytest.raises(ValueError, match="z_range must be a tuple"):
            ZStackConfig(z_range=(0.0,))  # type: ignore[arg-type]

    def test_rejects_inverted_z_range(self):
        """Rejects z_range where min > max."""
        with pytest.raises(ValueError, match="z_range min"):
            ZStackConfig(z_range=(2.0, 1.0))

    def test_is_frozen(self):
        """ZStackConfig is immutable."""
        config = ZStackConfig()
        with pytest.raises(AttributeError):
            config.nz = 10


class TestTimingConfig:
    """Tests for TimingConfig dataclass."""

    def test_default_values(self):
        """Default values are reasonable."""
        config = TimingConfig()
        assert config.nt == 1
        assert config.dt_s == 0.0

    def test_custom_values(self):
        """Custom values are stored correctly."""
        config = TimingConfig(nt=10, dt_s=60.0)
        assert config.nt == 10
        assert config.dt_s == 60.0

    def test_is_time_lapse_single_point(self):
        """Single time point is not time-lapse."""
        config = TimingConfig(nt=1)
        assert config.is_time_lapse is False

    def test_is_time_lapse_multiple_points(self):
        """Multiple time points is time-lapse."""
        config = TimingConfig(nt=5)
        assert config.is_time_lapse is True

    def test_total_duration_single_point(self):
        """Total duration is 0 for single point."""
        config = TimingConfig(nt=1, dt_s=60.0)
        assert config.total_duration_s == 0.0

    def test_total_duration_multiple_points(self):
        """Total duration calculated correctly."""
        config = TimingConfig(nt=5, dt_s=60.0)
        assert config.total_duration_s == pytest.approx(240.0)

    def test_rejects_zero_nt(self):
        """Rejects nt < 1."""
        with pytest.raises(ValueError, match="nt must be >= 1"):
            TimingConfig(nt=0)

    def test_rejects_negative_dt(self):
        """Rejects negative dt_s."""
        with pytest.raises(ValueError, match="dt_s must be >= 0"):
            TimingConfig(dt_s=-1.0)

    def test_is_frozen(self):
        """TimingConfig is immutable."""
        config = TimingConfig()
        with pytest.raises(AttributeError):
            config.nt = 10


class TestFocusConfig:
    """Tests for FocusConfig dataclass."""

    def test_default_values(self):
        """Default values are reasonable."""
        config = FocusConfig()
        assert config.mode == AutofocusMode.NONE
        assert config.interval_fovs == 1
        assert config.gen_focus_map is False
        assert config.use_manual_focus_map is False
        assert config.focus_map_dx_mm > 0
        assert config.focus_map_dy_mm > 0
        assert isinstance(config.focus_lock, FocusLockSettings)

    def test_any_autofocus_enabled_false(self):
        """No autofocus when both disabled."""
        config = FocusConfig()
        assert config.any_autofocus_enabled is False

    def test_any_autofocus_enabled_contrast_mode(self):
        """Autofocus enabled with contrast AF mode."""
        config = FocusConfig(mode=AutofocusMode.CONTRAST)
        assert config.any_autofocus_enabled is True

    def test_any_autofocus_enabled_reflection_mode(self):
        """Autofocus enabled with reflection AF mode."""
        config = FocusConfig(mode=AutofocusMode.LASER_REFLECTION)
        assert config.any_autofocus_enabled is True

    def test_any_autofocus_enabled_focus_lock_mode(self):
        """Autofocus enabled with focus lock mode."""
        config = FocusConfig(mode=AutofocusMode.FOCUS_LOCK)
        assert config.any_autofocus_enabled is True

    def test_rejects_invalid_interval(self):
        """Rejects interval_fovs < 1."""
        with pytest.raises(ValueError, match="interval_fovs must be >= 1"):
            FocusConfig(interval_fovs=0)

    def test_rejects_zero_focus_map_dx(self):
        """Rejects focus_map_dx_mm <= 0."""
        with pytest.raises(ValueError, match="focus_map_dx_mm must be > 0"):
            FocusConfig(focus_map_dx_mm=0)

    def test_rejects_zero_focus_map_dy(self):
        """Rejects focus_map_dy_mm <= 0."""
        with pytest.raises(ValueError, match="focus_map_dy_mm must be > 0"):
            FocusConfig(focus_map_dy_mm=0)

    def test_is_frozen(self):
        """FocusConfig is immutable."""
        config = FocusConfig()
        with pytest.raises(AttributeError):
            config.mode = AutofocusMode.CONTRAST


class TestAcquisitionConfig:
    """Tests for AcquisitionConfig dataclass."""

    def test_default_values(self):
        """Default values are reasonable."""
        config = AcquisitionConfig()
        assert config.grid is not None
        assert config.zstack is not None
        assert config.timing is not None
        assert config.focus is not None
        assert config.selected_channels == ()
        assert 0 < config.display_resolution_scaling <= 1

    def test_from_defaults(self):
        """from_defaults creates valid config."""
        config = AcquisitionConfig.from_defaults()
        config.validate()  # Should not raise

    def test_custom_sub_configs(self):
        """Custom sub-configs are used."""
        grid = GridConfig(nx=5, ny=3)
        zstack = ZStackConfig(nz=10)
        config = AcquisitionConfig(grid=grid, zstack=zstack)
        assert config.grid.nx == 5
        assert config.grid.ny == 3
        assert config.zstack.nz == 10

    def test_total_images_basic(self):
        """Total images calculated for basic config."""
        config = AcquisitionConfig(
            grid=GridConfig(nx=2, ny=2),
            zstack=ZStackConfig(nz=3),
            timing=TimingConfig(nt=2),
            selected_channels=("BF", "FL"),
        )
        # 4 positions * 3 z * 2 timepoints * 2 channels = 48
        assert config.total_images == 48

    def test_total_images_no_channels(self):
        """Total images with no channels uses 1 as minimum."""
        config = AcquisitionConfig(
            grid=GridConfig(nx=2, ny=2),
            zstack=ZStackConfig(nz=1),
            timing=TimingConfig(nt=1),
            selected_channels=(),
        )
        # 4 positions * 1 z * 1 timepoint * max(1, 0) = 4
        assert config.total_images == 4

    def test_validate_passes_for_valid_config(self):
        """Validation passes for valid config."""
        config = AcquisitionConfig.from_defaults()
        config.validate()  # Should not raise

    def test_validate_rejects_invalid_scaling(self):
        """Validation rejects invalid display scaling."""
        # Create config then manually check validation
        # Note: Can't directly create with invalid scaling due to frozen
        config = AcquisitionConfig(display_resolution_scaling=0)
        with pytest.raises(ValueError, match="display_resolution_scaling"):
            config.validate()

    def test_validate_rejects_conflicting_focus_map(self):
        """Validation rejects both gen and manual focus map."""
        config = AcquisitionConfig(
            focus=FocusConfig(gen_focus_map=True, use_manual_focus_map=True)
        )
        with pytest.raises(ValueError, match="Cannot both generate focus map"):
            config.validate()

    def test_with_updates_top_level(self):
        """with_updates modifies top-level fields."""
        config = AcquisitionConfig()
        new_config = config.with_updates(skip_saving=True, xy_mode="Select Wells")
        assert new_config.skip_saving is True
        assert new_config.xy_mode == "Select Wells"
        assert config.skip_saving is False  # Original unchanged

    def test_with_updates_nested_grid(self):
        """with_updates modifies nested grid fields."""
        config = AcquisitionConfig()
        new_config = config.with_updates(**{"grid.nx": 10, "grid.ny": 5})
        assert new_config.grid.nx == 10
        assert new_config.grid.ny == 5
        assert config.grid.nx == 1  # Original unchanged

    def test_with_updates_nested_zstack(self):
        """with_updates modifies nested zstack fields."""
        config = AcquisitionConfig()
        new_config = config.with_updates(**{"zstack.nz": 20, "zstack.delta_z_um": 3.0})
        assert new_config.zstack.nz == 20
        assert new_config.zstack.delta_z_um == 3.0

    def test_with_updates_nested_timing(self):
        """with_updates modifies nested timing fields."""
        config = AcquisitionConfig()
        new_config = config.with_updates(**{"timing.nt": 5, "timing.dt_s": 30.0})
        assert new_config.timing.nt == 5
        assert new_config.timing.dt_s == 30.0

    def test_with_updates_nested_focus(self):
        """with_updates modifies nested focus fields."""
        config = AcquisitionConfig()
        new_config = config.with_updates(
            **{
                "focus.mode": AutofocusMode.CONTRAST,
                "focus.interval_fovs": 7,
            }
        )
        assert new_config.focus.mode == AutofocusMode.CONTRAST
        assert new_config.focus.interval_fovs == 7

    def test_with_updates_mixed(self):
        """with_updates handles mixed top-level and nested."""
        config = AcquisitionConfig()
        new_config = config.with_updates(
            skip_saving=True, **{"grid.nx": 3, "zstack.nz": 5}
        )
        assert new_config.skip_saving is True
        assert new_config.grid.nx == 3
        assert new_config.zstack.nz == 5

    def test_with_updates_invalid_prefix(self):
        """with_updates rejects unknown prefix."""
        config = AcquisitionConfig()
        with pytest.raises(ValueError, match="Unknown config prefix"):
            config.with_updates(**{"unknown.field": 1})

    def test_is_frozen(self):
        """AcquisitionConfig is immutable."""
        config = AcquisitionConfig()
        with pytest.raises(AttributeError):
            config.skip_saving = True

    def test_selected_channels_tuple(self):
        """Selected channels stored as tuple."""
        config = AcquisitionConfig(selected_channels=("BF", "FL"))
        assert config.selected_channels == ("BF", "FL")
        assert isinstance(config.selected_channels, tuple)
