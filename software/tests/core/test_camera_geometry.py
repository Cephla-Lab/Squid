"""Per-channel camera settings for the Time at XY tab (control.core.camera_geometry)."""

from types import SimpleNamespace

import pytest

from control.core import camera_geometry as cg
from control.core.camera_geometry import CameraGeometry, CameraSnapshot, ResolvedGeometry
from control.utils import clamp_roi
from squid.config import CameraPixelFormat


class FakeCamera:
    """A camera with a 4000 x 3000 sensor that tracks binning, ROI and pixel format like a driver would."""

    SENSOR = (4000, 3000)

    def __init__(self, binning=1, pixel_format=CameraPixelFormat.MONO16):
        self.binning = (binning, binning)
        self.pixel_format = pixel_format
        self.roi = (0, 0, *self.get_resolution())
        self.calls = []

    def get_binning(self):
        return self.binning

    def get_binning_options(self):
        return [(1, 1), (2, 2), (4, 4), (2, 1)]

    def set_binning(self, x, y):
        self.calls.append(("binning", x, y))
        self.binning = (x, y)
        self.roi = (0, 0, *self.get_resolution())  # a binning change resets the ROI to the full sensor

    def get_resolution(self):
        return self.SENSOR[0] // self.binning[0], self.SENSOR[1] // self.binning[1]

    def get_region_of_interest(self):
        return self.roi

    def set_region_of_interest(self, x, y, w, h):
        self.calls.append(("roi", x, y, w, h))
        self.roi = (x, y, w, h)

    def get_pixel_format(self):
        return self.pixel_format

    def set_pixel_format(self, pixel_format):
        self.calls.append(("format", pixel_format.name))
        self.pixel_format = pixel_format

    def get_available_pixel_formats(self):
        return [CameraPixelFormat.MONO8, CameraPixelFormat.MONO16]

    def get_pixel_size_binned_um(self):
        return 3.76 * self.binning[0]

    def get_crop_size(self):
        return (None, None)


def test_clamp_roi_snaps_to_the_grid_and_stays_on_the_sensor():
    assert clamp_roi(100, 50, 1000, 500, 4000, 3000) == (96, 50, 1000, 500)
    assert clamp_roi(-3, -3, 5000, 5000, 4000, 3000) == (0, 0, 4000, 3000)  # negative offsets clamp to 0, not -8
    assert clamp_roi(3900, 2900, 1000, 500, 4000, 3000) == (3000, 2500, 1000, 500)
    assert clamp_roi(0, 0, 1, 1, 4000, 3000) == (0, 0, 8, 2)
    x, y, w, h = clamp_roi(1501.7, 1001.2, 999.9, 777.7, 4000, 3000, x_interval=8, y_interval=8)
    assert (x % 8, y % 8, w % 8, h % 8) == (0, 0, 0, 0) and x + w <= 4000 and y + h <= 3000


def test_geometry_yaml_round_trip_and_inherit():
    assert CameraGeometry().is_inherit and CameraGeometry().to_yaml_dict() == {}
    g = CameraGeometry(binning=2, crop_w=1024, crop_h=512, pixel_format="MONO8")
    assert g.to_yaml_dict() == {"binning": 2, "crop_width": 1024, "crop_height": 512, "pixel_format": "MONO8"}
    assert CameraGeometry.from_yaml_dict(g.to_yaml_dict()) == g
    assert CameraGeometry.from_yaml_dict(None) == CameraGeometry()
    # Crop width and height are set together or not at all; extra keys (pixel_size_um) are ignored.
    assert CameraGeometry.from_yaml_dict({"crop_width": 100, "pixel_size_um": 0.7}) == CameraGeometry()
    assert CameraGeometry.from_yaml_dict({"pixel_format": "mono8"}).pixel_format == "MONO8"


def test_labels():
    assert cg.short_label(CameraGeometry()) == "—"
    assert cg.short_label(CameraGeometry(2, 1024, 1024, "MONO8")) == "2×2 1024² M8"
    assert cg.short_label(CameraGeometry(crop_w=800, crop_h=600, pixel_format="BAYER_RG8")) == "800×600 BAYER_RG8"
    assert cg.describe(CameraGeometry(binning=2)) == "binning 2×2, crop current, format current"
    assert cg.describe(CameraGeometry(crop_w=8, crop_h=16, pixel_format="MONO8")) == (
        "binning current, crop 8×16 centred, format MONO8"
    )
    assert cg.is_mono_format("Mono12") and not cg.is_mono_format("BAYER_RG8")


def test_camera_capabilities_with_fallbacks():
    camera = FakeCamera()
    assert cg.binning_option_values(camera) == [1, 2, 4]  # square factors only
    assert cg.available_pixel_format_names(camera) == ["MONO8", "MONO16"]
    assert cg.binning_option_values(object()) == [1]
    assert cg.available_pixel_format_names(object()) == list(cg.FALLBACK_PIXEL_FORMATS)
    assert cg.sensor_size_at(FakeCamera(binning=2), 4) == (1000, 750)
    assert cg.image_size_px(camera) == (4000, 3000)


def test_resolve_inherits_from_the_snapshot():
    snapshot = CameraSnapshot(binning=(2, 2), roi=(96, 0, 1808, 1500), pixel_format="MONO16")
    assert cg.resolve(CameraGeometry(), snapshot) == ResolvedGeometry(2, None, None, "MONO16")  # keeps the user's ROI
    assert cg.resolve(CameraGeometry(pixel_format="MONO8"), snapshot) == ResolvedGeometry(2, None, None, "MONO8")
    # Binning changes with the crop inherited: the ROI is scaled so the field of view stays the same.
    assert cg.resolve(CameraGeometry(binning=1), snapshot) == ResolvedGeometry(1, 3616, 3000, "MONO16")
    assert cg.resolve(CameraGeometry(binning=4), snapshot) == ResolvedGeometry(4, 904, 750, "MONO16")
    assert cg.resolve(CameraGeometry(crop_w=512, crop_h=256), snapshot) == ResolvedGeometry(2, 512, 256, "MONO16")
    # Rows that resolve to the same settings are the same group, however they were written.
    assert cg.resolve(CameraGeometry(binning=2), snapshot) == cg.resolve(CameraGeometry(), snapshot)


def test_group_directory_names():
    assert ResolvedGeometry(2, 1024, 1024, "MONO8").dir_name() == "bin2_1024x1024_mono8"
    assert ResolvedGeometry(1, None, None, "MONO16").dir_name() == "bin1_roi_mono16"
    assert ResolvedGeometry(1, None, None, None).dir_name() == "bin1_roi_fmt"


def test_apply_sets_format_then_binning_then_a_centred_crop_and_restore_puts_everything_back():
    camera = FakeCamera()
    camera.set_region_of_interest(96, 48, 3808, 2904)
    camera.calls.clear()
    snapshot = cg.snapshot_camera(camera)
    assert snapshot == CameraSnapshot(binning=(1, 1), roi=(96, 48, 3808, 2904), pixel_format="MONO16")

    resolved = cg.resolve(CameraGeometry(binning=2, crop_w=1000, crop_h=500, pixel_format="MONO8"), snapshot)
    cg.apply_geometry(camera, resolved, snapshot)

    # Order matters: binning resets the sensor size the ROI is measured against.
    assert [c[0] for c in camera.calls] == ["format", "binning", "roi"]
    assert camera.binning == (2, 2) and camera.pixel_format == CameraPixelFormat.MONO8
    x, y, w, h = camera.roi
    assert (w, h) == (1000, 496)  # snapped to the Camera tab's 8 px grid
    assert abs((x + w / 2) - 1000) <= 8 and abs((y + h / 2) - 750) <= 8  # centred on the 2000 x 1500 binned sensor

    camera.calls.clear()
    cg.apply_geometry(camera, resolved, snapshot)
    assert camera.calls == []  # already there: nothing is written

    cg.restore_snapshot(camera, snapshot)
    assert cg.snapshot_camera(camera) == snapshot


def test_apply_with_everything_inherited_keeps_the_users_roi():
    camera = FakeCamera(binning=2)
    camera.set_region_of_interest(96, 0, 1808, 1500)
    snapshot = cg.snapshot_camera(camera)
    camera.calls.clear()
    cg.apply_geometry(camera, cg.resolve(CameraGeometry(), snapshot), snapshot)
    assert camera.calls == [] and camera.roi == (96, 0, 1808, 1500)


def test_pixel_size_scales_with_binning_without_touching_the_camera():
    camera = FakeCamera(binning=2)
    store = SimpleNamespace(get_pixel_size_factor=lambda: 0.05)
    assert cg.pixel_size_for(store, camera, 2) == pytest.approx(0.376)
    assert cg.pixel_size_for(store, camera, 1) == pytest.approx(0.188)
    assert cg.pixel_size_for(store, camera, 4) == pytest.approx(0.752)
    assert camera.calls == []
    assert cg.pixel_size_for(SimpleNamespace(get_pixel_size_factor=lambda: None), camera, 2) is None


def test_validate_rows_names_the_row_the_camera_cannot_deliver():
    from control.core.record_zstack_controller import ChannelPlan
    from control.models.acquisition_config import AcquisitionChannel, CameraSettings, IlluminationSettings

    def row(name, geometry, active=True):
        channel = AcquisitionChannel(
            name=name,
            camera_settings=CameraSettings(exposure_time_ms=10.0, gain_mode=0.0),
            illumination_settings=IlluminationSettings(intensity=10.0),
        )
        return ChannelPlan(channel=channel, zf=active, geometry=geometry)

    camera = FakeCamera()
    assert (
        cg.validate_rows([row("a", CameraGeometry()), row("b", CameraGeometry(2, 1000, 500, "MONO8"))], camera) is None
    )
    assert "b: the camera does not offer 3×3 binning" in cg.validate_rows([row("b", CameraGeometry(binning=3))], camera)
    assert "does not offer pixel format MONO12" in cg.validate_rows(
        [row("b", CameraGeometry(pixel_format="MONO12"))], camera
    )
    too_big = cg.validate_rows([row("b", CameraGeometry(binning=2, crop_w=2008, crop_h=500))], camera)
    assert "larger than the sensor at 2×2 binning (2000×1500)" in too_big
    # A row that is not used is not checked, and no camera means nothing to check against.
    assert cg.validate_rows([row("b", CameraGeometry(binning=3), active=False)], camera) is None
    assert cg.validate_rows([row("b", CameraGeometry(binning=3))], None) is None
