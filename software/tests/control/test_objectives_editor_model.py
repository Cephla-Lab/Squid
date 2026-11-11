import pytest

import control.objectives_config as oc
from control.objectives_config import ChangerKind, EditorRow, ObjectivesConfigError

CATALOG = {
    "2x": {"magnification": 2.0, "NA": 0.1, "tube_lens_f_mm": 180.0},
    "4x": {"magnification": 4.0, "NA": 0.13, "tube_lens_f_mm": 180.0},
    "10x": {"magnification": 10.0, "NA": 0.3, "tube_lens_f_mm": 180.0},
    "20x": {"magnification": 20.0, "NA": 0.8, "tube_lens_f_mm": 180.0},
    "60x": {"magnification": 60.0, "NA": 1.2, "tube_lens_f_mm": 180.0},
}
SHIPPED_XERYON = dict(xeryon_pos_1=["10x", "20x", "25x", "60x"], xeryon_pos_2=["2x", "4x"])


def test_seed_turret_from_the_ini_map_in_slot_order():
    rows = oc.seed_rows(
        ChangerKind.NIMOTION_TURRET,
        catalog=CATALOG,
        turret_positions={"20x": 3, "4x": 1, "10x": 2},
        xeryon_pos_1=[],
        xeryon_pos_2=[],
    )
    assert [(r.name, r.slot, r.magnification) for r in rows] == [("4x", 1, 4.0), ("10x", 2, 10.0), ("20x", 3, 20.0)]


def test_seed_shipped_xeryon_lists_every_candidate_with_conflicts():
    rows = oc.seed_rows(ChangerKind.XERYON, catalog=CATALOG, turret_positions={}, **SHIPPED_XERYON)
    assert [(r.name, r.slot) for r in rows] == [("10x", 1), ("20x", 1), ("25x", 1), ("60x", 1), ("2x", 2), ("4x", 2)]
    assert oc.slot_conflicts(rows) == {0, 1, 2, 3, 4, 5}


def test_seed_name_missing_from_catalog_has_blank_optics():
    rows = oc.seed_rows(ChangerKind.XERYON, catalog=CATALOG, turret_positions={}, **SHIPPED_XERYON)
    row_25x = next(r for r in rows if r.name == "25x")
    assert (row_25x.magnification, row_25x.na, row_25x.tube_lens_f_mm) == (None, None, None)


def test_seed_without_changer_lists_the_catalog():
    rows = oc.seed_rows(ChangerKind.NONE, catalog=CATALOG, turret_positions={}, xeryon_pos_1=[], xeryon_pos_2=[])
    assert [r.name for r in rows] == list(CATALOG) and all(r.slot is None for r in rows)


def test_seeded_xeryon_cannot_be_saved_until_one_per_position():
    rows = oc.seed_rows(ChangerKind.XERYON, catalog=CATALOG, turret_positions={}, **SHIPPED_XERYON)
    with pytest.raises(ObjectivesConfigError):
        config = oc.rows_to_config(ChangerKind.XERYON, rows)
        oc.validate_objectives_config(config, use_xeryon=True, use_turret=False)
    kept = [r for r in rows if r.name in ("20x", "4x")]
    config = oc.rows_to_config(ChangerKind.XERYON, kept)
    oc.validate_objectives_config(config, use_xeryon=True, use_turret=False)
    assert oc.to_xeryon_lists(config) == (["20x"], ["4x"])


def test_blank_optics_block_the_save():
    rows = [EditorRow("25x", None, None, None, 1)]
    with pytest.raises(ObjectivesConfigError) as err:
        oc.rows_to_config(ChangerKind.XERYON, rows)
    assert "magnification" in err.value.field


def test_config_round_trips_through_rows():
    rows = [EditorRow("4x", 4.0, 0.13, 180.0, 1, "PLN4X", "SN4"), EditorRow("20x", 20.0, 0.8, 180.0, 2)]
    config = oc.rows_to_config(ChangerKind.NIMOTION_TURRET, rows)
    assert oc.config_to_rows(config) == rows


@pytest.mark.parametrize("magnification, expected", [(25.0, "20x"), (5.0, "4x"), (None, "4x")])
def test_nearest_by_magnification(magnification, expected):
    rows = [
        EditorRow("4x", 4.0, 0.13, 180.0, 1),
        EditorRow("20x", 20.0, 0.8, 180.0, 2),
        EditorRow("new", None, None, None, 3),
    ]
    assert oc.nearest_by_magnification(magnification, rows) == expected


def test_seed_accepts_the_shipped_ini_string_lists():
    as_strings = oc.seed_rows(
        ChangerKind.XERYON,
        catalog=CATALOG,
        turret_positions={},
        xeryon_pos_1="['10x', '20x', '25x', '60x']",
        xeryon_pos_2="['2x', '4x']",
    )
    as_lists = oc.seed_rows(ChangerKind.XERYON, catalog=CATALOG, turret_positions={}, **SHIPPED_XERYON)
    assert as_strings == as_lists


def test_seed_accepts_a_string_turret_map():
    rows = oc.seed_rows(
        ChangerKind.NIMOTION_TURRET,
        catalog=CATALOG,
        turret_positions="{'4x': 1, '10x': 2}",
        xeryon_pos_1=[],
        xeryon_pos_2=[],
    )
    assert [(r.name, r.slot) for r in rows] == [("4x", 1), ("10x", 2)]


def test_seed_ignores_an_unparsable_ini_value():
    rows = oc.seed_rows(
        ChangerKind.XERYON, catalog=CATALOG, turret_positions={}, xeryon_pos_1="not a list", xeryon_pos_2=["4x"]
    )
    assert [(r.name, r.slot) for r in rows] == [("4x", 2)]
