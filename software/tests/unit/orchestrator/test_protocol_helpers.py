"""Unit tests for orchestrator protocol helper utilities."""

from unittest.mock import MagicMock

from squid.backend.controllers.orchestrator import protocol_helpers
from squid.core.events import LoadScanCoordinatesCommand


def test_load_fov_set_preserves_optional_z_column(tmp_path):
    """CSV z-values should be propagated into coordinates and region centers."""
    csv_path = tmp_path / "fovs.csv"
    csv_path.write_text(
        "region,x (mm),y (mm),z (mm)\n"
        "A,1.0,2.0,3.0\n"
        "A,4.0,5.0,6.0\n"
    )

    scan_coordinates = MagicMock()
    event_bus = MagicMock()

    protocol_helpers.load_fov_set(str(csv_path), scan_coordinates, event_bus)

    call_kwargs = scan_coordinates.load_coordinates.call_args.kwargs
    assert call_kwargs["region_fov_coordinates"]["A"] == (
        (1.0, 2.0, 3.0),
        (4.0, 5.0, 6.0),
    )
    assert call_kwargs["region_centers"]["A"] == (2.5, 3.5, 4.5)

    event = event_bus.publish.call_args[0][0]
    assert isinstance(event, LoadScanCoordinatesCommand)
    assert event.region_fov_coordinates["A"][0] == (1.0, 2.0, 3.0)
