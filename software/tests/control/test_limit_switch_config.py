from configparser import ConfigParser
from pathlib import Path

import pytest

import control._def
from control._def import (
    LIMIT_SWITCHES,
    legacy_limit_switch_warning,
    limit_switch_config_byte,
    limit_switch_config_problems,
)

CONFIG_DIR = Path(__file__).parent.parent.parent / "configurations"


def _exec_like_the_txt_loader(source):
    """Run a legacy .txt configuration the way _def.py loads one: exec'd in _def's own namespace (a copy here)."""
    namespace = dict(vars(control._def))
    exec(source, namespace)
    return namespace


def test_default_config_byte_is_stock_for_every_axis():
    assert [limit_switch_config_byte(a) for a in "xyz"] == [0x03, 0x03, 0x03]


def test_stowers_z_both_active_high(monkeypatch):
    monkeypatch.setattr(LIMIT_SWITCHES, "Z_LEFT_ACTIVE_HIGH", True)
    monkeypatch.setattr(LIMIT_SWITCHES, "Z_RIGHT_ACTIVE_HIGH", True)
    assert limit_switch_config_byte("z") == 0x0F
    assert limit_switch_config_byte("x") == 0x03


def test_disabled_left_input_clears_bit_0(monkeypatch):
    monkeypatch.setattr(LIMIT_SWITCHES, "Y_LEFT_ENABLED", False)
    assert limit_switch_config_byte("y") == 0x02


def test_legacy_section_is_reported():
    cfp = ConfigParser()
    cfp.read_string("[GENERAL]\n[LIMIT_SWITCH_POLARITY]\nx_home = 1\n")
    msg = legacy_limit_switch_warning(cfp)
    assert "LIMIT_SWITCH_POLARITY" in msg and "LIMIT_SWITCHES" in msg


def test_no_legacy_section_no_warning():
    cfp = ConfigParser()
    cfp.read_string("[GENERAL]\n")
    assert legacy_limit_switch_warning(cfp) is None


def test_shipped_configurations_drop_the_legacy_section():
    files = sorted(CONFIG_DIR.glob("*.ini"))
    assert files
    for path in files:
        cfp = ConfigParser()
        cfp.read(path)
        assert not cfp.has_section("LIMIT_SWITCH_POLARITY"), path.name


def test_shipped_txt_configurations_load():
    files = sorted(CONFIG_DIR.glob("*.txt"))
    assert files
    for path in files:
        _exec_like_the_txt_loader(path.read_text())  # NameError here = a .txt machine cannot start


def test_shipped_txt_configurations_drop_the_retired_polarity_lines():
    for path in sorted(CONFIG_DIR.glob("*.txt")):
        assert "HOME_SWITCH_POLARITY" not in path.read_text(), path.name


def test_field_txt_configurations_with_the_retired_polarity_lines_still_load():
    # Field machines keep their own .txt; the retired constants stay so these lines still exec (read by nothing).
    _exec_like_the_txt_loader(
        "X_HOME_SWITCH_POLARITY = LIMIT_SWITCH_POLARITY.ACTIVE_HIGH\n"
        "Y_HOME_SWITCH_POLARITY = LIMIT_SWITCH_POLARITY.ACTIVE_LOW\n"
        "Z_HOME_SWITCH_POLARITY = LIMIT_SWITCH_POLARITY.DISABLED\n"
    )


def test_ini_section_reaches_the_config_byte(load_limit_switches):
    # Through the loader's class-section path, inline comment included.
    load_limit_switches("[LIMIT_SWITCHES]\nz_left_active_high = True\nz_right_active_high = true  # photoelectric\n")
    assert limit_switch_config_byte("z") == 0x0F
    assert limit_switch_config_byte("x") == 0x03
    assert limit_switch_config_problems() == []


def test_the_default_config_has_no_problems():
    assert limit_switch_config_problems() == []


@pytest.mark.parametrize("value", ["True", "true", "False", "false", "False  # no switch fitted"])
def test_real_booleans_are_accepted(load_limit_switches, value):
    load_limit_switches(f"[LIMIT_SWITCHES]\ny_right_enabled = {value}\n")
    assert limit_switch_config_problems() == []


@pytest.mark.parametrize("value", ["no", "off", "Flase", "yes", "TRUE", "0", "1", "None", ""])
def test_a_value_that_is_not_a_real_boolean_is_a_problem(load_limit_switches, value):
    # Each of these is used by truthiness otherwise: "no" / "off" / "Flase" would silently mean True.
    load_limit_switches(f"[LIMIT_SWITCHES]\nz_left_active_high = {value}\n")
    problems = limit_switch_config_problems()
    assert len(problems) == 1 and "z_left_active_high" in problems[0], problems


def test_an_unknown_key_is_a_problem(load_limit_switches):
    load_limit_switches("[LIMIT_SWITCHES]\nz_left_activ_high = True\n")
    problems = limit_switch_config_problems()
    assert len(problems) == 1 and "z_left_activ_high" in problems[0], problems


def test_every_problem_is_reported(load_limit_switches):
    load_limit_switches("[LIMIT_SWITCHES]\nx_rigth_enabled = True\ny_left_enabled = off\n")
    problems = limit_switch_config_problems()
    assert len(problems) == 2
    assert any("x_rigth_enabled" in p for p in problems) and any("y_left_enabled" in p for p in problems)
