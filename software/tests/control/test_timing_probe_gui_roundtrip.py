"""The Simulate round trip must leave the widget able to start an acquisition.

After a timing probe the widget re-enters toggle_acquisition() to re-show the pacing
dialog with the measured number.  That path runs the same guard clauses an operator's
click does, so anything the probe disturbs -- notably the channel selection, which
channel_sequence stores as the QListWidget's selection -- surfaces as a spurious
"Please select at least one imaging channel" dialog.
"""

import pytest
from qtpy.QtWidgets import QMessageBox

import control._def
import control.gui_hcs
import control.microscope


@pytest.fixture
def confirm_exit_yes(monkeypatch):
    def confirm_exit(parent, title, text, *args, **kwargs):
        if title == "Confirm Exit":
            return QMessageBox.Yes
        raise RuntimeError(f"Unexpected QMessageBox: {title} - {text}")

    monkeypatch.setattr(QMessageBox, "question", confirm_exit)


def _select_first_channel(widget):
    items = [widget.list_configurations.item(i) for i in range(widget.list_configurations.count())]
    assert items, "no channels available to select"
    items[0].setSelected(True)
    return items[0].text()


@pytest.mark.parametrize("widget_attr", ["wellplateMultiPointWidget", "flexibleMultiPointWidget"])
def test_enable_disable_preserves_channel_selection(qtbot, confirm_exit_yes, widget_attr):
    """setEnabled_all() is what the probe brackets its run with; it must not clear selection."""
    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    widget = getattr(win, widget_attr)
    name = _select_first_channel(widget)
    assert widget.channel_sequence.ordered_selected_names() == [name]

    widget.setEnabled_all(False)
    widget.setEnabled_all(True)

    assert widget.list_configurations.selectedItems(), (
        "setEnabled_all() cleared the channel selection; the acquisition start guard "
        "would reject the run with 'Please select at least one imaging channel'"
    )
    assert widget.channel_sequence.ordered_selected_names() == [name]


def test_probe_result_only_reaches_the_widget_that_launched_it(qtbot, confirm_exit_yes, monkeypatch):
    """timing_probe_finished is a controller signal, so both multipoint widgets receive it.

    Regression: with no ownership guard the widget that did NOT start the probe also
    re-entered toggle_acquisition, hit its own empty channel list, and popped
    "Please select at least one imaging channel" on top of the real dialog.
    """
    from control.core.multi_point_controller import TimingProbeResult

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    owner = win.wellplateMultiPointWidget
    bystander = win.flexibleMultiPointWidget
    _select_first_channel(owner)

    reentered = []
    monkeypatch.setattr(owner, "toggle_acquisition", lambda pressed: reentered.append("owner"))
    monkeypatch.setattr(bystander, "toggle_acquisition", lambda pressed: reentered.append("bystander"))

    # The owner claims the probe, exactly as _start_timing_simulation() does.
    owner._timing_simulation_is_mine = True
    result = TimingProbeResult(ok=True, per_fov_s=7.0, n_fovs_probed=2)

    # Both widgets are connected to the signal, so both handlers run.
    owner._on_timing_probe_finished(result)
    bystander._on_timing_probe_finished(result)

    assert reentered == ["owner"], f"expected only the launching widget to react, got {reentered}"


@pytest.mark.parametrize("widget_attr", ["wellplateMultiPointWidget", "flexibleMultiPointWidget"])
def test_probe_finished_does_not_lose_the_channel_selection(qtbot, confirm_exit_yes, monkeypatch, widget_attr):
    """The full post-probe handler must leave the widget startable."""
    from control.core.multi_point_controller import TimingProbeResult

    scope = control.microscope.Microscope.build_from_global_config(True)
    win = control.gui_hcs.HighContentScreeningGui(microscope=scope, is_simulation=True)
    qtbot.add_widget(win)

    widget = getattr(win, widget_attr)
    name = _select_first_channel(widget)

    # Stop the re-entry into toggle_acquisition from actually starting anything; we only
    # care about the widget state the handler leaves behind.
    reentered = {}

    def fake_toggle(pressed):
        reentered["selected"] = [i.text() for i in widget.list_configurations.selectedItems()]

    monkeypatch.setattr(widget, "toggle_acquisition", fake_toggle)

    widget._timing_simulation_is_mine = True
    widget._on_timing_probe_finished(TimingProbeResult(ok=True, per_fov_s=7.0, n_fovs_probed=2))

    assert reentered.get("selected") == [name], (
        f"channel selection was {reentered.get('selected')} when the post-probe handler re-entered "
        "toggle_acquisition; the start guard would reject the run"
    )
    assert widget.channel_sequence.ordered_selected_names() == [name]
