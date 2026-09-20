"""The simulated MCU -> simulated camera trigger link.

Nothing connected a simulated microcontroller's triggers to a simulated camera before the
hardware sequencer: SimulatedCamera made its own frames inside _send_trigger_imp.  These
tests cover the link the simulation Microscope builds, so a sequenced acquisition produces
real frames in `--simulation` and in pytest.
"""

import threading

import pytest

import control.sequencer_program as sp
import control.sequencer_sim as seq_sim
import squid.camera.utils
import squid.config
from control.microcontroller import CommandAborted, Microcontroller, SimSerial
from control.microscope import link_simulated_sequencer_to_camera
from squid.abc import CameraAcquisitionMode, CameraError

from tests.control.test_sequencer_program import minimal_program

TEST_SPEED_UP = 500.0
RUN_TIMEOUT_S = 20.0


@pytest.fixture(autouse=True)
def fast_timeline(monkeypatch):
    monkeypatch.setattr(seq_sim, "SPEED_UP_FACTOR", TEST_SPEED_UP)


class FrameRecorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.frames = []

    def __call__(self, camera_frame):
        with self.lock:
            self.frames.append(camera_frame)

    @property
    def frame_ids(self):
        with self.lock:
            return [f.frame_id for f in self.frames]

    @property
    def count(self):
        with self.lock:
            return len(self.frames)


@pytest.fixture
def rig():
    """A simulated microcontroller and camera, wired the way the simulation Microscope wires
    them, with the camera in HARDWARE_TRIGGER mode."""
    serial = SimSerial()
    mcu = Microcontroller(serial, reset_and_initialize=False)
    camera = squid.camera.utils.get_camera(
        squid.config.get_camera_config(),
        simulated=True,
        hw_trigger_fn=lambda illumination_time: True,
        hw_set_strobe_delay_ms_fn=lambda strobe_delay_ms: True,
    )
    link_simulated_sequencer_to_camera(serial, camera)
    camera.set_acquisition_mode(CameraAcquisitionMode.HARDWARE_TRIGGER)
    recorder = FrameRecorder()
    camera.add_frame_callback(recorder)
    yield mcu, camera, recorder
    mcu.close()
    camera.close()


def stack_program(n_channels=2, n_layers=3):
    loop = minimal_program().loop.replace(
        stack_axis_type=sp.StackAxisType.PIEZO, stack_axis_id=0, dz=100, n_layers=n_layers
    )
    return minimal_program(
        loop=loop,
        channels=tuple(
            sp.SeqChannelSpec(exposure_us=5000, camera_mask=1, illum_ttl_mask=1 << i) for i in range(n_channels)
        ),
        cameras=(sp.SeqCameraSpec(trigger_mode=sp.TriggerMode.LEVEL, strobe_delay_us=300, readout_time_us=10000),),
    )


class TestSequencedAcquisitionProducesFrames:
    def test_two_channels_times_three_layers_gives_six_frames(self, rig):
        mcu, _camera, recorder = rig

        mcu.seq_upload(stack_program(n_channels=2, n_layers=3))
        mcu.seq_run(0)
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert recorder.count == 6
        assert mcu.seq_status.frames_fired == 6

    def test_frame_ids_are_consecutive(self, rig):
        mcu, _camera, recorder = rig

        mcu.seq_upload(stack_program(n_channels=2, n_layers=3))
        mcu.seq_run(0)
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        ids = recorder.frame_ids
        assert ids == list(range(ids[0], ids[0] + 6))

    def test_frame_ids_stay_monotonic_across_runs(self, rig):
        mcu, _camera, recorder = rig

        mcu.seq_upload(stack_program(n_channels=1, n_layers=2))
        for _ in range(3):
            mcu.seq_run(0)
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        ids = recorder.frame_ids
        assert len(ids) == 6
        assert ids == sorted(ids)
        assert len(set(ids)) == 6

    def test_the_frames_carry_image_data(self, rig):
        mcu, camera, recorder = rig
        width, height = camera.get_resolution()

        mcu.seq_upload(stack_program(n_channels=1, n_layers=2))
        mcu.seq_run(0)
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        for frame in recorder.frames:
            assert frame.frame.shape[:2] == (height, width)


class TestCancelAndFailure:
    def test_cancel_mid_run_keeps_the_frames_already_fired(self, rig):
        mcu, _camera, recorder = rig
        program = stack_program(n_channels=2, n_layers=16)  # 32 frames

        mcu.seq_upload(program)
        mcu.seq_run(0)
        _wait_until(lambda: recorder.count >= 2, "the run to fire a couple of frames")
        mcu.seq_cancel()
        mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert mcu.seq_status.error == sp.SeqError.CANCELED
        assert 0 < mcu.seq_status.frames_fired < program.n_frames
        # The host keeps exactly the frames that were fired -- no more, no fewer.
        assert recorder.count == mcu.seq_status.frames_fired

    def test_a_stack_out_of_range_produces_no_frames_at_all(self, rig):
        mcu, _camera, recorder = rig

        mcu.seq_upload(stack_program(n_channels=2, n_layers=10))  # dz=100 -> 900 LSB of travel
        mcu.seq_run(65000)
        with pytest.raises(CommandAborted) as exc:
            mcu.wait_till_operation_is_completed(RUN_TIMEOUT_S)

        assert exc.value.seq_status.error == sp.SeqError.STACK_OUT_OF_RANGE
        assert recorder.count == 0


class TestEmitHardwareTriggeredFrame:
    def test_refuses_outside_hardware_trigger_mode(self, rig):
        _mcu, camera, _recorder = rig
        camera.set_acquisition_mode(CameraAcquisitionMode.SOFTWARE_TRIGGER)

        with pytest.raises(CameraError, match="HARDWARE_TRIGGER"):
            camera.emit_hardware_triggered_frame()

    def test_does_not_block_for_the_frame_time(self, rig):
        import time

        _mcu, camera, _recorder = rig
        camera.set_exposure_time(500)  # ms -- a real trigger would take at least this long

        start = time.time()
        camera.emit_hardware_triggered_frame()
        elapsed = time.time() - start

        # The caller is the MCU timeline thread, which already paces the run.
        assert elapsed < 0.4

    def test_each_call_advances_the_frame_id(self, rig):
        _mcu, camera, recorder = rig

        camera.emit_hardware_triggered_frame()
        camera.emit_hardware_triggered_frame()

        assert recorder.frame_ids[1] == recorder.frame_ids[0] + 1
        assert camera.get_frame_id() == recorder.frame_ids[1]


def _wait_until(predicate, what, timeout_s=RUN_TIMEOUT_S):
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError(f"timed out waiting for {what}")
