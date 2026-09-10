"""The worker must send camera triggers through LiveController.send_camera_trigger()
so the strobe window is recorded for set_microscope_mode()'s pre-1.5-firmware wait."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from control import utils
from control._def import TriggerMode
from control.core.multi_point_worker import MultiPointWorker


class _FakeCamera:
    def get_exposure_time(self):
        return 2.0

    def get_total_frame_time(self):
        return 3.0

    def get_ready_for_trigger(self):
        return True


def test_acquire_camera_image_triggers_via_live_controller():
    w = MultiPointWorker.__new__(MultiPointWorker)
    w._log = MagicMock()
    w._timing = utils.TimingManager("test timing")
    w._ready_for_next_trigger = threading.Event()
    w._ready_for_next_trigger.set()
    w._backpressure = SimpleNamespace(should_throttle=lambda: False)
    w.liveController = SimpleNamespace(trigger_mode=TriggerMode.HARDWARE, send_camera_trigger=MagicMock())
    w.stage = SimpleNamespace(get_pos=lambda: "pos")
    w.use_piezo = False
    w.z_piezo_um = None
    w.time_point = 0
    w._current_capture_info = None
    w._select_config = lambda config: None
    w.camera = _FakeCamera()

    w.acquire_camera_image(
        SimpleNamespace(name="ch", id=0), file_ID="f", current_path="/tmp", k=0, region_id=0, fov=0, config_idx=0
    )

    w.liveController.send_camera_trigger.assert_called_once_with(2.0)
