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
