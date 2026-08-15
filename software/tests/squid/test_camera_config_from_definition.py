import squid.config
from control.models.camera_registry import CameraDefinition
from squid.config import CameraPixelFormat


def test_bare_definition_inherits_ini_defaults():
    base = squid.config.get_camera_config()
    defn = CameraDefinition(serial_number="SN-A")
    cfg = squid.config.camera_config_from_definition(defn)
    assert cfg.serial_number == "SN-A"
    assert cfg.camera_type == base.camera_type
    assert cfg.crop_width == base.crop_width
    assert cfg.default_pixel_format == base.default_pixel_format
    # The INI singleton must not be mutated
    assert squid.config.get_camera_config().serial_number == base.serial_number


def test_overrides_applied():
    defn = CameraDefinition(
        name="Side",
        id=2,
        serial_number="SN-B",
        type="Toupcam",
        hardware_trigger=False,
        rotate_image_angle=90.0,
        flip="Vertical",
        crop_width=1000,
        crop_height=800,
        default_pixel_format="RGB24",
        default_binning=[2, 2],
    )
    cfg = squid.config.camera_config_from_definition(defn)
    assert cfg.serial_number == "SN-B"
    assert cfg.camera_type == squid.config.CameraVariant.TOUPCAM
    assert cfg.rotate_image_angle == 90.0
    assert cfg.flip == squid.config.FlipVariant.VERTICAL
    assert cfg.crop_width == 1000 and cfg.crop_height == 800
    assert cfg.default_pixel_format == CameraPixelFormat.RGB24
    assert cfg.default_binning == (2, 2)


def test_device_index_mapped_to_config():
    defn = CameraDefinition(serial_number="SN-IDX", device_index=1)
    cfg = squid.config.camera_config_from_definition(defn)
    assert cfg.device_index == 1


def test_device_index_defaults_to_none_in_config():
    defn = CameraDefinition(serial_number="SN-NOIDX")
    cfg = squid.config.camera_config_from_definition(defn)
    assert cfg.device_index is None


def test_default_roi_mapped_to_config():
    defn = CameraDefinition(serial_number="SN-ROI", default_roi=[240, 240, 2720, 2720])
    cfg = squid.config.camera_config_from_definition(defn)
    assert cfg.default_roi == (240, 240, 2720, 2720)


def test_absent_default_roi_inherits_ini_default():
    base = squid.config.get_camera_config()
    defn = CameraDefinition(serial_number="SN-NOROI")
    cfg = squid.config.camera_config_from_definition(defn)
    assert cfg.default_roi == base.default_roi


def test_type_change_clears_ini_camera_model():
    base = squid.config.get_camera_config()
    other_type = "Hamamatsu" if base.camera_type != squid.config.CameraVariant.HAMAMATSU else "Toupcam"
    defn = CameraDefinition(serial_number="SN-C", type=other_type)
    cfg = squid.config.camera_config_from_definition(defn)
    assert cfg.camera_model is None
