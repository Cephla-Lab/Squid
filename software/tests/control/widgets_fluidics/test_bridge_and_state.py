import threading

from control.widgets_fluidics.runner_bridge import RunnerEventBridge
from control.widgets_fluidics.state import load_ui_state, save_ui_state


def test_bridge_delivers_events_on_the_gui_thread(qtbot):
    bridge = RunnerEventBridge()
    received = []
    bridge.event_received.connect(lambda ev: received.append((ev, threading.current_thread())))

    worker = threading.Thread(target=lambda: bridge.listener("hello"))
    with qtbot.waitSignal(bridge.event_received, timeout=2000):
        worker.start()
    worker.join()
    assert received[0][0] == "hello"
    assert received[0][1] is threading.main_thread()


def test_ui_state_round_trips_and_merges(tmp_path):
    path = tmp_path / "state.json"
    assert load_ui_state(path=str(path)) == {}
    saved = save_ui_state(path=str(path), save_to="/data", run_name="liver")
    assert saved == {"save_to": "/data", "run_name": "liver"}
    save_ui_state(path=str(path), run_name="liver2")
    assert load_ui_state(path=str(path)) == {"save_to": "/data", "run_name": "liver2"}


def test_ui_state_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    assert load_ui_state(path=str(path)) == {}
    assert save_ui_state(path=str(path), a="b") == {"a": "b"}
