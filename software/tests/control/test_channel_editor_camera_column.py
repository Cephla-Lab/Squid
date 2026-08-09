from control.models.camera_registry import CameraDefinition, CameraRegistryConfig
from control.widgets import camera_display_name, camera_id_from_display

REGISTRY = CameraRegistryConfig(
    cameras=[
        CameraDefinition(name="Main Camera", id=1, serial_number="SN1", type="Toupcam"),
        CameraDefinition(name="Side Camera", id=2, serial_number="SN2", type="Toupcam", hardware_trigger=False),
    ]
)


def test_display_name_for_known_id():
    assert camera_display_name(REGISTRY, 1) == "Main Camera"
    assert camera_display_name(REGISTRY, 2) == "Side Camera"


def test_display_name_for_none_and_unknown():
    assert camera_display_name(REGISTRY, None) == "(None)"
    assert camera_display_name(REGISTRY, 99) == "(None)"
    assert camera_display_name(None, 1) == "(None)"


def test_id_from_display_round_trip():
    assert camera_id_from_display(REGISTRY, "Side Camera") == 2
    assert camera_id_from_display(REGISTRY, "(None)") is None
    assert camera_id_from_display(REGISTRY, "Nonexistent") is None
    assert camera_id_from_display(None, "Main Camera") is None
