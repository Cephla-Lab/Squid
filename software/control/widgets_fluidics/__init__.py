"""Phase-2 fluidics GUI: the Fluidics display tab (Initialize, manual control, device status,
Log | Temperature | Reagents, Protocol editor) and the Fluidics Protocol record tab, over the Qt-free
engine in control/core/fluidics_protocol. Everything Qt lives here; the engine never imports this."""


def wire_fluidics(display_tab, protocol_widget) -> None:
    """Connect the two fluidics widgets to each other. The GUI calls this once and keeps
    only the connections that touch objects it owns (record tabs, napari, Slack)."""
    from qtpy.QtCore import QTimer

    protocol_widget.signal_reagent_rows.connect(display_tab.reagents_table.set_rows)
    display_tab.system_ready.connect(lambda: protocol_widget.set_fluidics_port(display_tab.fluidics_port))
    # Crash recovery is only actionable once the fluidics system exists, so the startup
    # offer waits for Initialize.
    display_tab.system_ready.connect(lambda: QTimer.singleShot(0, lambda: protocol_widget.offer_recovery(startup=True)))
    display_tab.run_line_provider = protocol_widget.run_line
