"""The channel configurator must offer every filter wheel the machine declares, including the
emission wheels built into a confocal unit (confocal_config.yaml), not only filter_wheels.yaml."""

import tests.control.gui_test_stubs  # noqa: F401 - ensures GUI modules import cleanly
import control._def
import control.widgets
from control.core.config import ConfigRepository
from qtpy.QtWidgets import QComboBox

CONFOCAL_YAML = """\
version: 1
model: dragonfly
filter_wheels:
  - name: "Dragonfly Emission"
    id: 1
    type: emission
    positions:
      1: "445/45"
      2: "525/50"
      3: "600/50"
"""

STANDALONE_YAML = """\
version: 1.0
filter_wheels:
  - name: "Body Emission"
    id: 1
    type: emission
    positions:
      1: "Empty"
      2: "LP 500"
"""


def _repo(tmp_path, confocal=None, standalone=None):
    machine_configs = tmp_path / "machine_configs"
    machine_configs.mkdir()
    if confocal:
        (machine_configs / "confocal_config.yaml").write_text(confocal)
    if standalone:
        (machine_configs / "filter_wheels.yaml").write_text(standalone)
    return ConfigRepository(base_path=tmp_path)


def _items(combo):
    return [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())]


def test_wheel_names_include_confocal_wheels(tmp_path):
    repo = _repo(tmp_path, confocal=CONFOCAL_YAML, standalone=STANDALONE_YAML)

    assert repo.get_filter_wheel_names() == ["Body Emission", "Dragonfly Emission"]


def test_positions_come_from_a_confocal_wheel_when_it_is_the_only_one(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "USE_EMISSION_FILTER_WHEEL", False)
    repo = _repo(tmp_path, confocal=CONFOCAL_YAML)
    combo = QComboBox()

    control.widgets._populate_filter_positions_for_combo(combo, None, repo, current_position=2)

    assert combo.isEnabled()
    assert _items(combo) == [("1: 445/45", 1), ("2: 525/50", 2), ("3: 600/50", 3)]
    assert combo.currentData() == 2


def test_an_explicitly_named_confocal_wheel_is_resolved(qtbot, tmp_path):
    repo = _repo(tmp_path, confocal=CONFOCAL_YAML, standalone=STANDALONE_YAML)
    combo = QComboBox()

    control.widgets._populate_filter_positions_for_combo(combo, "Dragonfly Emission", repo)

    assert [data for _, data in _items(combo)] == [1, 2, 3]


def test_no_wheels_anywhere_shows_not_applicable(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(control._def, "USE_EMISSION_FILTER_WHEEL", False)
    repo = _repo(tmp_path)
    combo = QComboBox()

    control.widgets._populate_filter_positions_for_combo(combo, None, repo)

    assert _items(combo) == [("N/A", None)]
    assert not combo.isEnabled()
