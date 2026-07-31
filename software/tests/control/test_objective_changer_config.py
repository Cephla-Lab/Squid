"""Unit tests for mutual-exclusion of Xeryon and turret flags."""

import pytest

from control._def import _validate_objective_changer_flags, OBJECTIVE_TURRET_POSITIONS


def test_mutual_exclusion_raises_when_both_true():
    with pytest.raises(ValueError, match="mutually exclusive"):
        _validate_objective_changer_flags(use_xeryon=True, use_turret=True)


def test_mutual_exclusion_allows_xeryon_only():
    _validate_objective_changer_flags(use_xeryon=True, use_turret=False)


def test_mutual_exclusion_allows_turret_only():
    _validate_objective_changer_flags(use_xeryon=False, use_turret=True)


def test_mutual_exclusion_allows_neither():
    _validate_objective_changer_flags(use_xeryon=False, use_turret=False)


def test_objective_turret_positions_shape():
    assert len(OBJECTIVE_TURRET_POSITIONS) == 4
    assert sorted(OBJECTIVE_TURRET_POSITIONS.values()) == [1, 2, 3, 4]


@pytest.mark.parametrize(
    "xeryon,turret,asi",
    [(True, True, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_mutual_exclusion_any_two_of_three_raise(xeryon, turret, asi):
    with pytest.raises(ValueError, match="mutually exclusive"):
        _validate_objective_changer_flags(xeryon, turret, asi)


def test_mutual_exclusion_allows_asi_turret_only():
    _validate_objective_changer_flags(use_xeryon=False, use_turret=False, use_asi_turret=True)


def test_asi_turret_positions_shape():
    from control._def import ASI_OBJECTIVE_TURRET_POSITIONS, OBJECTIVES, DEFAULT_OBJECTIVE

    assert len(ASI_OBJECTIVE_TURRET_POSITIONS) == 6
    assert sorted(ASI_OBJECTIVE_TURRET_POSITIONS.values()) == [1, 2, 3, 4, 5, 6]
    # Keys must be real objective names so the GUI dropdown -> move_to_objective flow works,
    # and the default dict must cover the startup DEFAULT_OBJECTIVE fallback.
    assert set(ASI_OBJECTIVE_TURRET_POSITIONS) <= set(OBJECTIVES)
    assert "20x" in ASI_OBJECTIVE_TURRET_POSITIONS
