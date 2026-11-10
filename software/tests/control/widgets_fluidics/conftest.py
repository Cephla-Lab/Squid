import pytest

import control._def
import control.microscope


@pytest.fixture
def qt_controller(qtbot):
    """A simulated microscope + QtMultiPointController, shared by the imaging-path tests."""
    control._def.MERGE_CHANNELS = False
    from tests.control.gui_test_stubs import get_test_qt_multi_point_controller

    scope = control.microscope.Microscope.build_from_global_config(True)
    controller = get_test_qt_multi_point_controller(scope)
    yield scope, controller
    controller.close()
    scope.close()


@pytest.fixture
def initialized_display_tab(qtbot, tmp_path, monkeypatch):
    """Build a FluidicsDisplayTab over a simulated system initialized from the given config text."""
    pytest.importorskip("fluidics")
    from control.fluidics_system import FluidicsService
    from control.widgets_fluidics.display_tab import FluidicsDisplayTab
    import control.widgets_fluidics.system_panel as system_panel_module
    from control.widgets_fluidics.system_panel import SystemPanel

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SystemPanel, "_INITIALIZE_KWARGS", {"instant": True})
    # a failed Initialize raises a modal, which would hang the offscreen run; fail the test instead
    monkeypatch.setattr(system_panel_module.QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    built = []

    def build(config_text, shown_at=None):
        config = tmp_path / "fluidics_config.yaml"
        config.write_text(config_text)
        service = FluidicsService(default_config_path=str(config), simulated=True)
        tab = FluidicsDisplayTab(service)
        qtbot.addWidget(tab)
        built.append((tab, service))
        if shown_at is not None:  # the application's order: on screen first, Initialize later
            tab.resize(*shown_at)
            tab.show()
            qtbot.waitExposed(tab)
        outcome = []
        tab.system_ready.connect(lambda: outcome.append(None))
        tab.system_panel.initialize_failed.connect(outcome.append)
        tab.system_panel.initialize_button.click()
        qtbot.waitUntil(lambda: bool(outcome), timeout=15000)
        assert outcome == [None], f"Initialize failed: {outcome[0]}"
        return tab

    yield build
    for tab, service in built:
        tab.shutdown()
        assert service.close() == []


# ---------------------------------------------------------------------------------------------------
# TEMPORARY CI PROBE (debug branch for PR #744 only; never merge).
# test_display_tab_initializes_and_builds_the_port fails on CI with "wrapped C/C++ object of type
# MplCanvas has been deleted". This records what destroys the canvas, and when, in SQUID_PROBE_LOG.
# ---------------------------------------------------------------------------------------------------
import faulthandler
import gc
import os
import threading
import time
import traceback

_PROBE_LOG = os.environ.get("SQUID_PROBE_LOG", "/tmp/squid_probe.log")
_PROBE_TARGET = "test_display_tab_initializes_and_builds_the_port"


def _probe(*parts):
    now = time.time()
    stamp = time.strftime("%H:%M:%S", time.gmtime(now)) + ".%03d" % (now % 1 * 1000)
    with open(_PROBE_LOG, "a") as f:
        f.write("[probe %s %s] %s\n" % (stamp, threading.current_thread().name, " ".join(str(p) for p in parts)))


def _probe_stacks(label):
    with open(_PROBE_LOG, "a") as f:
        f.write("[probe] ---- stacks at %s (current thread first) ----\n" % label)
        f.write("".join(traceback.format_stack()))
        f.flush()
        faulthandler.dump_traceback(file=f, all_threads=True)
        f.write("[probe] ---- end stacks ----\n")


@pytest.fixture(autouse=True)
def _probe_mplcanvas(request, monkeypatch):
    if request.node.name != _PROBE_TARGET:
        yield
        return
    try:
        from PyQt5 import sip
    except ImportError:
        import sip
    import matplotlib
    import pytestqt
    import pytestqt.plugin as qt_plugin
    from fluidics.qt import sensor_plots

    from control.fluidics_system import FluidicsService
    from control.widgets_fluidics.display_tab import FluidicsDisplayTab

    state = {"canvases": [], "destroyed": [], "gc": None}

    def describe(obj):
        try:
            if sip.isdeleted(obj):
                return "%s<DELETED>" % type(obj).__name__
            parent = obj.parent()
            return "%s@%#x pyowned=%s parent=%s" % (
                type(obj).__name__,
                int(sip.unwrapinstance(obj)),
                sip.ispyowned(obj),
                type(parent).__name__ if parent is not None else None,
            )
        except Exception as e:
            return "%s<describe failed: %r>" % (type(obj).__name__, e)

    def canvases():
        return "; ".join("#%d %s" % (i, describe(c)) for i, c in enumerate(state["canvases"])) or "none"

    def watch(obj, label):
        def gone():  # zero-argument, so PyQt passes nothing (the fluidics widgets connect the same way)
            state["destroyed"].append(label)
            _probe("DESTROYED:", label, "| in gc generation:", state["gc"])
            _probe_stacks("destroyed " + label)

        obj.destroyed.connect(gone)

    orig_canvas_init = sensor_plots.MplCanvas.__init__

    def probe_canvas_init(self, *args, **kwargs):
        orig_canvas_init(self, *args, **kwargs)
        index = len(state["canvases"])
        state["canvases"].append(self)
        watch(self, "canvas#%d" % index)
        _probe("canvas created #%d" % index, describe(self))

    monkeypatch.setattr(sensor_plots.MplCanvas, "__init__", probe_canvas_init)

    orig_draw = sensor_plots.MplCanvas.draw

    def probe_draw(self):
        _probe("draw begin", describe(self))
        try:
            return orig_draw(self)
        finally:
            _probe("draw end", describe(self))

    monkeypatch.setattr(sensor_plots.MplCanvas, "draw", probe_draw)

    orig_fanout = sensor_plots.TemperatureControlWidget._fanout

    def probe_fanout(self, temps):
        _probe(
            "_fanout begin; receiver:",
            describe(self),
            "| channel widgets:",
            [describe(cw) for cw in self.plot_widgets],
            "| canvases:",
            canvases(),
            "| destroyed so far:",
            state["destroyed"],
        )
        return orig_fanout(self, temps)

    monkeypatch.setattr(sensor_plots.TemperatureControlWidget, "_fanout", probe_fanout)

    orig_on_initialized = FluidicsDisplayTab._on_initialized

    def probe_on_initialized(self):
        orig_on_initialized(self)
        seen = set()
        for i, canvas in enumerate(state["canvases"]):
            node, depth = canvas.parent(), 1
            while node is not None:
                _probe("canvas#%d ancestor depth %d:" % (i, depth), describe(node))
                key = int(sip.unwrapinstance(node))
                if key not in seen:
                    seen.add(key)
                    watch(node, "ancestor(depth %d of canvas#%d) %s@%#x" % (depth, i, type(node).__name__, key))
                node, depth = node.parent(), depth + 1
        _probe("initialized; canvases:", canvases())

    monkeypatch.setattr(FluidicsDisplayTab, "_on_initialized", probe_on_initialized)

    def wrap(cls, name):
        orig = getattr(cls, name)
        label = "%s.%s" % (cls.__name__, name)

        def wrapped(self, *args, **kwargs):
            _probe(label, "begin; canvases:", canvases())
            try:
                return orig(self, *args, **kwargs)
            finally:
                _probe(label, "end; canvases:", canvases(), "| destroyed so far:", state["destroyed"])

        monkeypatch.setattr(cls, name, wrapped)

    wrap(FluidicsDisplayTab, "shutdown")
    wrap(FluidicsService, "close")

    orig_process_events = qt_plugin._process_events

    def probe_process_events():
        _probe("pytest-qt _process_events begin; canvases:", canvases(), "| destroyed so far:", state["destroyed"])
        try:
            return orig_process_events()
        finally:
            _probe("pytest-qt _process_events end; canvases:", canvases(), "| destroyed so far:", state["destroyed"])

    monkeypatch.setattr(qt_plugin, "_process_events", probe_process_events)

    def gc_callback(phase, info):
        state["gc"] = info["generation"] if phase == "start" else None
        if info["generation"] >= 1 or info.get("collected"):
            _probe("gc", phase, info)

    gc.callbacks.append(gc_callback)
    _probe(
        "==== probe start:",
        request.node.nodeid,
        "| matplotlib",
        matplotlib.__version__,
        "| pytest-qt",
        pytestqt.version,
        "| gc threshold",
        gc.get_threshold(),
        "| gc counts",
        gc.get_count(),
        "| threads",
        [t.name for t in threading.enumerate()],
    )
    try:
        yield
    finally:
        gc.callbacks.remove(gc_callback)
        _probe("==== probe fixture teardown; canvases:", canvases(), "| destroyed:", state["destroyed"])
