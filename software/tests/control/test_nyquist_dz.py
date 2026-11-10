"""The objective-dependent default z step (control._def.nyquist_dz_um / default_dz_um) and the
widget mixin that re-seeds the z-step boxes when the objective changes."""

from types import SimpleNamespace

import pytest

import control._def
from control.widgets import _NyquistDefaultDzMixin


@pytest.mark.parametrize(
    "na, expected_um",
    [
        (0.13, 3.213),  # 4x
        (0.3, 1.392),  # 10x
        (0.8, 0.522),  # 20x
        (1.2, 0.348),  # 60x oil
    ],
)
def test_nyquist_dz_um(na, expected_um):
    assert control._def.nyquist_dz_um(na) == pytest.approx(expected_um)


def test_nyquist_dz_is_the_documented_formula():
    # axial resolution ~ 3 * 0.61 * lambda / NA, sampled 2.3 times
    assert control._def.nyquist_dz_um(0.5) == pytest.approx(round(3.0 * 0.61 * 0.525 / 0.5 / 2.3, 3))
    assert control._def.nyquist_dz_um("0.5") == control._def.nyquist_dz_um(0.5)  # objectives.csv may give text


def test_nyquist_dz_gets_finer_with_higher_na():
    steps = [control._def.nyquist_dz_um(na) for na in (0.1, 0.3, 0.8, 1.2)]
    assert steps == sorted(steps, reverse=True)


@pytest.mark.parametrize("bad_na", [None, "", "n/a", 0, -0.5])
def test_nyquist_dz_rejects_an_unusable_na(bad_na):
    assert control._def.nyquist_dz_um(bad_na) is None


def test_default_dz_follows_the_objective_and_falls_back_to_the_configured_step(monkeypatch):
    monkeypatch.setattr(
        control._def,
        "OBJECTIVES",
        {"10x": {"NA": 0.3}, "20x": {"NA": 0.8}, "no NA": {"magnification": 5}, "bad NA": {"NA": "?"}},
    )
    monkeypatch.setattr(control._def, "DEFAULT_OBJECTIVE", "20x")

    assert control._def.default_dz_um("10x") == pytest.approx(1.392)
    assert control._def.default_dz_um() == pytest.approx(0.522)  # DEFAULT_OBJECTIVE
    for name in ("no NA", "bad NA", "not in the table"):
        assert control._def.default_dz_um(name) == control._def.Acquisition.DZ


class _Spin:
    def __init__(self, value, minimum=0.0):
        self._value = value
        self._minimum = minimum

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def minimum(self):
        return self._minimum


class _Host(_NyquistDefaultDzMixin):
    def __init__(self, objective, dz, z_range=None):
        self.objectiveStore = SimpleNamespace(current_objective=objective) if objective else None
        self.entry_deltaZ = _Spin(dz)
        if z_range is not None:
            self.entry_zRange = _Spin(z_range, minimum=0.1)


@pytest.fixture
def objectives(monkeypatch):
    monkeypatch.setattr(control._def, "OBJECTIVES", {"10x": {"NA": 0.3}, "20x": {"NA": 0.8}})
    monkeypatch.setattr(control._def, "DEFAULT_OBJECTIVE", "10x")


def test_apply_default_dz_overwrites_a_hand_entered_step(objectives):
    host = _Host("20x", dz=5.0)
    host.apply_default_dz()
    assert host.entry_deltaZ.value() == pytest.approx(0.522)


def test_apply_default_dz_keeps_the_number_of_steps_the_relative_range_covered(objectives):
    host = _Host("20x", dz=1.392, z_range=13.92)  # 10 steps of range at the 10x step
    host.apply_default_dz()
    assert host.entry_deltaZ.value() == pytest.approx(0.522)
    assert host.entry_zRange.value() == pytest.approx(5.22)


def test_apply_default_dz_with_a_zero_step_falls_back_to_four_steps_of_range(objectives):
    host = _Host("20x", dz=0.0, z_range=50.0)
    host.apply_default_dz()
    assert host.entry_zRange.value() == pytest.approx(round(0.522 * 4, 3))


def test_default_dz_without_an_objective_store_uses_the_default_objective(objectives):
    assert _Host(None, dz=1.0).default_dz_um() == pytest.approx(1.392)


def test_step_entry_can_be_named_differently(objectives):
    class _AutofocusLike(_NyquistDefaultDzMixin):
        _DZ_ENTRY_ATTR = "entry_delta"

        def __init__(self):
            self.objectiveStore = SimpleNamespace(current_objective="20x")
            self.entry_delta = _Spin(1.524)

    host = _AutofocusLike()
    host.apply_default_dz()
    assert host.entry_delta.value() == pytest.approx(0.522)
