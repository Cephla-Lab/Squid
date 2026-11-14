"""Hardware-sequenced Z FIRST: one single-channel burst per channel (Hongquan, 2026-11-01).

A bare MultiPointWorker (no __init__) whose burst runner is replaced by a recorder, so the loop
around the bursts is tested on its own: which bursts run, in what order, what the controller held
and what the camera and the emission filter were set to when each one ran.
"""

from unittest.mock import MagicMock

import squid.abc
from control.core.multi_point_worker import MultiPointWorker
from control.core.sequenced_acquisition import ChannelPlan, burst_plans, piezo_um_to_dac
from control.piezo import PiezoStage

NZ = 3
DZ_UM = 1.5
PIEZO_RANGE_UM = 300
START_UM = 20.0


class _FakeStage:
    def get_pos(self):
        return squid.abc.Pos(x_mm=10.0, y_mm=20.0, z_mm=1.0, theta_rad=None)


def _channel(name, exposure_ms, filter_position=None):
    ch = MagicMock()
    ch.name = name
    ch.exposure_time = exposure_ms
    ch.analog_gain = 10.0
    ch.filter_position = filter_position
    ch.z_offset_um = 0.0
    return ch


def _worker(channels, z_stack_order, *, failing=(), laser_af_per_stack=False):
    """failing: burst keys (channel name, attempt number) whose run fails with an injected reason."""
    w = MultiPointWorker.__new__(MultiPointWorker)
    w.selected_configurations = channels
    w.z_stack_order = z_stack_order
    w.NZ = NZ
    w.deltaZ = DZ_UM / 1000
    w.Nt = 1
    w.time_point = 0
    w.do_reflection_af = laser_af_per_stack
    w.do_autofocus = False
    w.laser_af_per_stack = laser_af_per_stack
    w.piezo = PiezoStage(MagicMock(), {"OBJECTIVE_PIEZO_RANGE_UM": PIEZO_RANGE_UM})
    w.piezo.move_to(START_UM)
    w.z_piezo_um = START_UM
    w.stage = _FakeStage()
    w.camera = MagicMock()
    w.liveController = MagicMock()
    w.microcontroller = MagicMock()
    w.wait_till_operation_is_completed = MagicMock()
    w._log = MagicMock()
    w.callbacks = MagicMock()
    w._last_time_point_z_pos = {}
    w.af_fov_count = 0
    w._applied_config = None
    w._burst_configs = None
    w._sequenced_program = None
    plans = [
        ChannelPlan(name=ch.name, exposure_ms=ch.exposure_time, source_code=11 + i, dac_percent=50.0, z_offset_um=0.0)
        for i, ch in enumerate(channels)
    ]
    w._burst_channel_plans = plans
    w._program_kwargs = dict(
        n_layers=NZ,
        dz_um=DZ_UM,
        piezo_range_um=PIEZO_RANGE_UM,
        piezo_flip=False,
        z_settle_ms=20,
        intensity_factor=1.0,
        wait_timeout_s=5.0,
        illum_lead_us=0,
    )
    w._sequenced_bursts = burst_plans(z_stack_order, len(plans))
    w.camera.get_strobe_time.return_value = 0.0
    w._burst_dispatcher = MagicMock()
    w.update_coordinates_dataframe = MagicMock()
    w.abort_requested_fn = lambda: False
    w.handle_acquisition_abort = MagicMock()
    w._ask_user_to_intervene = MagicMock()
    w._re_anchor_before_stack = MagicMock(return_value=True)
    w.runs = []  # one record per _run_sequenced_burst call
    attempts = {}

    def run(captures, stack_start, channel):
        key = captures[0].configuration.name
        attempts[key] = attempts.get(key, 0) + 1
        program = w.microcontroller.seq_upload.call_args[0][0]  # what the controller holds for this burst
        w.runs.append(
            dict(
                captures=[(c.z_index, c.configuration.name, c.configuration_idx, c.z_piezo_um) for c in captures],
                stack_start=stack_start,
                channel=channel,
                uploads=w.microcontroller.seq_upload.call_count,
                program_channels=len(program.channels),
                strobe_us=program.cameras[0].strobe_delay_us,
                exposure_ms=w.camera.set_exposure_time.call_args[0][0],
                filter_for=w.liveController.apply_emission_filter.call_args[0][0].name,
            )
        )
        if (key, attempts[key]) in failing:
            return None, "injected failure"
        return [(MagicMock(), c) for c in captures], None

    w._run_sequenced_burst = run
    return w


A = _channel("A", exposure_ms=5, filter_position=2)
B = _channel("B", exposure_ms=20, filter_position=3)
EXPECTED_STACK_START = piezo_um_to_dac(START_UM, PIEZO_RANGE_UM, False)


def test_z_first_runs_one_single_channel_burst_per_channel_in_channel_order():
    w = _worker([A, B], "Z FIRST")

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)

    assert [r["captures"] for r in w.runs] == [
        [(z, "A", 0, START_UM + z * DZ_UM) for z in range(NZ)],
        [(z, "B", 1, START_UM + z * DZ_UM) for z in range(NZ)],
    ]
    # the controller returned the piezo after the first burst, so the second starts where the first did
    assert [r["stack_start"] for r in w.runs] == [EXPECTED_STACK_START] * 2
    assert w.z_piezo_um == START_UM


def test_z_first_runs_each_channel_of_the_one_uploaded_program_with_its_own_settings():
    w = _worker([A, B], "Z FIRST")

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)

    assert [r["channel"] for r in w.runs] == [0, 1]  # SEQ_RUN's channel selector, no program per burst
    # One program, uploaded once (by the first burst here; the preparation is not run in this fixture) and
    # not again: the strobe delay is the same for both channels, so the second burst's program equals it.
    assert w.microcontroller.seq_upload.call_count == 1
    assert [r["exposure_ms"] for r in w.runs] == [5, 20]
    assert [r["filter_for"] for r in w.runs] == ["A", "B"]


def test_a_single_channel_acquisition_runs_the_whole_program_in_either_order():
    for order in ("Z FIRST", "CHANNEL FIRST"):
        w = _worker([A], order)
        w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)
        assert [r["channel"] for r in w.runs] == [None]


def test_z_first_hands_each_burst_to_the_dispatcher_with_its_place_in_the_acquisition():
    w = _worker([A, B], "Z FIRST")

    w._acquire_sequenced_stack("A1", "/tmp", 1, af_succeeded=True)

    first_indices = [call.args[0][1] for call in w._burst_dispatcher.submit.call_args_list]
    assert first_indices == [1 * NZ * 2 + 0 * NZ, 1 * NZ * 2 + 1 * NZ]


def test_z_first_writes_the_per_z_bookkeeping_once_not_per_channel():
    w = _worker([A, B], "Z FIRST")

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)

    assert [call.args[1] for call in w.update_coordinates_dataframe.call_args_list] == list(range(NZ))
    assert w.af_fov_count == NZ


def test_channel_first_is_one_burst_of_every_channel():
    w = _worker([A, B], "CHANNEL FIRST")

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)
    w._acquire_sequenced_stack("A1", "/tmp", 1, af_succeeded=True)

    assert [r["captures"] for r in w.runs] == [
        [(z, name, idx, START_UM + z * DZ_UM) for z in range(NZ) for idx, name in enumerate("AB")]
    ] * 2
    assert [r["channel"] for r in w.runs] == [None, None]
    assert w.microcontroller.seq_upload.call_count == 1  # once, by the first burst; the second FOV re-uses it
    assert [r["exposure_ms"] for r in w.runs] == [20, 20]  # the longest, set once
    assert w.camera.set_exposure_time.call_count == 1


def test_a_failed_burst_is_retried_alone():
    w = _worker([A, B], "Z FIRST", failing={("B", 1)})

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)

    assert [r["captures"][0][1] for r in w.runs] == ["A", "B", "B"]
    assert w._burst_dispatcher.submit.call_count == 2
    assert not w._ask_user_to_intervene.called


def test_a_burst_that_fails_three_times_asks_the_user_and_stops():
    w = _worker([A, B], "Z FIRST", failing={("A", 1), ("A", 2), ("A", 3)})

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)

    assert [r["captures"][0][1] for r in w.runs] == ["A", "A", "A"]  # B never ran
    assert w._ask_user_to_intervene.called
    assert not w._burst_dispatcher.submit.called


def test_a_failed_burst_puts_the_piezo_back_at_the_stack_start():
    """The engine's fail() leaves the piezo at the last plane it commanded (only Done and a cancel return to
    the start) while the worker's cache says the stack start; every failed attempt moves it back there, so
    the retry and the next acquisition start where the cache says."""
    w = _worker([A, B], "Z FIRST", failing={("A", 1), ("A", 2), ("A", 3)})
    moves_before = w.piezo._mc.set_piezo_um.call_count  # the fixture's own move to START_UM

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)

    moves = w.piezo._mc.set_piezo_um.call_args_list[moves_before:]
    assert [m.args[0] for m in moves] == [START_UM] * 3
    assert w.piezo.position == START_UM


def test_af_per_stack_re_anchors_before_every_burst_but_the_first():
    w = _worker([A, B], "Z FIRST", laser_af_per_stack=True)

    def re_anchor(config, region_id, fov, af_succeeded):
        w.piezo.move_to(25.0)  # the laser AF controller moves the piezo directly ...
        w.z_piezo_um = 25.0  # ... and _laser_af_anchor re-reads the cache
        return True

    w._re_anchor_before_stack.side_effect = re_anchor

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)

    assert [call.args[0].name for call in w._re_anchor_before_stack.call_args_list] == ["B"]
    assert [r["stack_start"] for r in w.runs] == [
        EXPECTED_STACK_START,
        piezo_um_to_dac(25.0, PIEZO_RANGE_UM, False),
    ]
    assert w.runs[1]["captures"][0][3] == 25.0


def test_an_abort_between_bursts_stops_before_the_next_run():
    w = _worker([A, B], "Z FIRST")
    w.abort_requested_fn = lambda: len(w.runs) >= 1

    w._acquire_sequenced_stack("A1", "/tmp", 0, af_succeeded=True)

    assert [r["captures"][0][1] for r in w.runs] == ["A"]
    assert w.handle_acquisition_abort.called


def test_each_burst_program_carries_the_strobe_delay_for_its_own_exposure():
    """A camera's trigger latency can depend on the exposure (the ToupCam's does, in global reset), and the
    program's strobe delay is read from the camera. So it is read after the camera is set up for the burst,
    once per burst, not once per acquisition before any exposure was set."""
    w = _worker([A, B], "Z FIRST")
    w.camera.set_exposure_time.side_effect = lambda exposure_ms: setattr(w.camera, "exposure_ms", exposure_ms)
    w.camera.get_strobe_time.side_effect = lambda: 0.1 * w.camera.exposure_ms  # 0.5 ms for A, 2 ms for B

    w._acquire_sequenced_stack("A1", "/tmp", fov=0, af_succeeded=True)

    assert [(r["exposure_ms"], r["strobe_us"]) for r in w.runs] == [(5, 500), (20, 2000)]
    assert w.microcontroller.seq_upload.call_count == 2
