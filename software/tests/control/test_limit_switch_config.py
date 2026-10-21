from configparser import ConfigParser
from pathlib import Path

from control._def import LIMIT_SWITCHES, legacy_limit_switch_warning, limit_switch_config_byte


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
    config_dir = Path(__file__).parent.parent.parent / "configurations"
    files = sorted(config_dir.glob("*.ini"))
    assert files
    for path in files:
        cfp = ConfigParser()
        cfp.read(path)
        assert not cfp.has_section("LIMIT_SWITCH_POLARITY"), path.name
