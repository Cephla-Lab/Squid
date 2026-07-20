import pytest

import control._def
from squid_service.wells import index_to_row, parse_well_names, row_to_index, well_center_mm

SETTINGS = {"a1_x_mm": 14.3, "a1_y_mm": 11.36, "well_spacing_mm": 9.0, "rows": 8, "cols": 12}


def test_row_index_roundtrip():
    for name, idx in (("A", 0), ("H", 7), ("Z", 25), ("AA", 26), ("AF", 31)):
        assert row_to_index(name) == idx
        assert index_to_row(idx) == name


def test_parse_single_and_list():
    assert parse_well_names("A1") == ["A1"]
    assert parse_well_names("a1, b12") == ["A1", "B12"]


def test_parse_range_expands_rectangle():
    assert parse_well_names("A1:B3") == ["A1", "A2", "A3", "B1", "B2", "B3"]


def test_parse_mixed_range_and_list():
    assert parse_well_names("A1:A2,C5") == ["A1", "A2", "C5"]


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_well_names("1A")
    with pytest.raises(ValueError):
        parse_well_names("")


def test_well_center_includes_wellplate_offset(monkeypatch):
    monkeypatch.setattr(control._def, "WELLPLATE_OFFSET_X_mm", 2.0, raising=False)
    monkeypatch.setattr(control._def, "WELLPLATE_OFFSET_Y_mm", -1.0, raising=False)
    x, y = well_center_mm("B3", SETTINGS)
    assert x == pytest.approx(14.3 + 2 * 9.0 + 2.0)
    assert y == pytest.approx(11.36 + 1 * 9.0 - 1.0)


def test_well_center_rejects_out_of_plate():
    with pytest.raises(ValueError):
        well_center_mm("I1", SETTINGS)  # row 9 on an 8-row plate
    with pytest.raises(ValueError):
        well_center_mm("A13", SETTINGS)
