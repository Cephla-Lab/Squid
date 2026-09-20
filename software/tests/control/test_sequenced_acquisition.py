"""Pure logic of hardware-sequenced acquisition: what the per-frame host loop used to do, resolved
up front into one MCU program; when a burst may run at all; and when a finished burst may be saved.
"""

import pytest

from control.core.sequenced_acquisition import (
    MAX_ATTEMPTS_PER_FOV,
    PIEZO_DAC_ID,
    BurstOutcome,
    ChannelPlan,
    burst_failure_reason,
    build_program,
    ineligibility_reason,
    intensity_percent_to_dac,
    outcome_after_failed_burst,
    piezo_step_lsb,
    piezo_um_to_dac,
)
from control.sequencer_program import (
    Order,
    SeqCameraSpec,
    SeqError,
    SeqState,
    SequencerStatus,
    StackAxisType,
    TriggerMode,
)

CAMERA = SeqCameraSpec(
    trigger_mode=TriggerMode.LEVEL,
    ready_line=0,
    ready_active_high=True,
    readout_overlap_safe=True,
    strobe_delay_us=300,
    readout_time_us=25_000,
)


def plan(name="Fluorescence 488 nm Ex", exposure_ms=20.0, source_code=12, dac_percent=50.0, z_offset_um=0.0):
    return ChannelPlan(
        name=name, exposure_ms=exposure_ms, source_code=source_code, dac_percent=dac_percent, z_offset_um=z_offset_um
    )


# --- the arithmetic must equal what the per-frame commands do today -------------------------------


def test_intensity_matches_set_illumination_then_the_firmware_factor():
    # host: int(percent/100*65535); firmware: uint16(u16 * float(factor_byte)/100)
    assert intensity_percent_to_dac(100.0, 1.0) == 65535
    assert intensity_percent_to_dac(0.0, 0.6) == 0
    assert intensity_percent_to_dac(50.0, 1.0) == 32767
    assert intensity_percent_to_dac(100.0, 0.6) == int(65535 * 0.6)  # 39321
    assert intensity_percent_to_dac(50.0, 0.6) == 19660  # 32767 * 0.6 = 19660.2 -> truncated


def test_intensity_factor_is_clamped_and_quantized_like_the_host_command():
    assert intensity_percent_to_dac(100.0, 5.0) == 65535  # > 1 clamps to 1
    assert intensity_percent_to_dac(100.0, 0.604) == intensity_percent_to_dac(100.0, 0.6)  # sent as a whole percent
    assert intensity_percent_to_dac(100.0, -1.0) == intensity_percent_to_dac(100.0, 0.01)


def test_piezo_conversion_matches_set_piezo_um():
    assert piezo_um_to_dac(0.0, 300, flip=False) == 0
    assert piezo_um_to_dac(300.0, 300, flip=False) == 65535
    assert piezo_um_to_dac(150.0, 300, flip=False) == int(65535 * 0.5)
    assert piezo_um_to_dac(20.0, 300, flip=True) == 65535 - int(65535 * (20.0 / 300))


def test_piezo_step_is_signed_by_the_flip():
    assert piezo_step_lsb(1.0, 300, flip=False) == round(65535 / 300)  # 218
    assert piezo_step_lsb(1.0, 300, flip=True) == -218
    assert piezo_step_lsb(-1.5, 300, flip=False) == -round(65535 * 1.5 / 300)


# --- program building ---------------------------------------------------------------------------


def test_build_program_resolves_each_channel_to_ttl_port_dac_and_intensity():
    channels = [
        plan(source_code=11, dac_percent=100.0, exposure_ms=10.0),
        plan(source_code=14, dac_percent=50.0, exposure_ms=25.5),  # D3 is source 14, NOT 13
        plan(source_code=13, dac_percent=25.0, exposure_ms=5.0),  # D4 is source 13
    ]
    program = build_program(
        channels,
        n_layers=10,
        dz_um=1.5,
        piezo_range_um=300,
        piezo_flip=False,
        z_settle_ms=20,
        intensity_factor=0.6,
        camera=CAMERA,
        wait_timeout_s=5.0,
    )
    assert program.loop.stack_axis_type == StackAxisType.PIEZO
    assert program.loop.stack_axis_id == PIEZO_DAC_ID == 7
    assert program.loop.order == Order.CHANNELS_INNER  # the worker's loop nesting: z outer, channel inner
    assert program.loop.n_layers == 10
    assert program.loop.dz == round(65535 * 1.5 / 300)
    assert program.loop.z_settle_us == 20_000
    assert program.loop.return_to_start is True
    assert program.wait_timeout_us == 5_000_000
    assert program.cameras == (CAMERA,)

    d1, d3, d4 = program.channels
    assert (d1.illum_ttl_mask, d1.intensity_dac) == (0b00001, 0)
    assert (d3.illum_ttl_mask, d3.intensity_dac) == (0b00100, 2)
    assert (d4.illum_ttl_mask, d4.intensity_dac) == (0b01000, 3)
    assert d1.exposure_us == 10_000 and d3.exposure_us == 25_500
    assert d3.intensity == intensity_percent_to_dac(50.0, 0.6)
    assert all(c.camera_mask == 1 for c in program.channels)
    program.validate()  # and it is a program the MCU will accept


def test_build_program_carries_per_channel_z_offsets_in_piezo_lsb():
    program = build_program(
        [plan(), plan(name="Fluorescence 638 nm Ex", source_code=13, z_offset_um=2.0)],
        n_layers=3,
        dz_um=1.0,
        piezo_range_um=300,
        piezo_flip=True,
        z_settle_ms=20,
        intensity_factor=1.0,
        camera=CAMERA,
        wait_timeout_s=5.0,
    )
    assert program.loop.dz == -218
    assert program.channels[0].z_offset == 0
    assert program.channels[1].z_offset == piezo_step_lsb(2.0, 300, flip=True)


def test_build_program_rejects_a_source_the_mcu_cannot_strobe():
    with pytest.raises(ValueError, match="source code 0"):
        build_program(
            [plan(name="BF LED matrix full", source_code=0)],
            n_layers=1,
            dz_um=0,
            piezo_range_um=300,
            piezo_flip=False,
            z_settle_ms=20,
            intensity_factor=1.0,
            camera=CAMERA,
            wait_timeout_s=5.0,
        )


# --- eligibility: decided once per acquisition, and it always says WHY ---------------------------


def eligible_kwargs(**overrides):
    kwargs = dict(
        firmware_supports_sequencer=True,
        trigger_is_hardware=True,
        use_piezo=True,
        global_reset_active=True,
        intensity_is_mcu_dac=True,
        channels=[plan(source_code=11), plan(source_code=12)],
        camera_gains=[10.0, 10.0],
        burst_bytes=400_000_000,
        byte_budget=2_000_000_000,
    )
    kwargs.update(overrides)
    return kwargs


def test_a_plain_fluorescence_piezo_stack_is_eligible():
    assert ineligibility_reason(**eligible_kwargs()) is None


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        (dict(trigger_is_hardware=False), "hardware trigger"),
        (dict(use_piezo=False), "piezo"),
        (dict(global_reset_active=False), "global reset"),
        (dict(intensity_is_mcu_dac=False), "light source"),
        (dict(channels=[plan(name="BF LED matrix full", source_code=0)], camera_gains=[10.0]), "BF LED matrix full"),
        (dict(channels=[plan(name="Fluorescence 488 nm Ex RGB")], camera_gains=[10.0]), "RGB"),
        (dict(camera_gains=[10.0, 12.0]), "gain"),
        (dict(channels=[plan(), plan(name="Fluorescence 638 nm Ex", source_code=13, z_offset_um=1.5)]), "z offset"),
        (dict(burst_bytes=3_000_000_000), "memory"),
        (dict(channels=[], camera_gains=[]), "no channels"),
    ],
)
def test_each_ineligibility_names_its_reason(overrides, fragment):
    reason = ineligibility_reason(**eligible_kwargs(**overrides))
    assert reason is not None and fragment in reason


def test_no_byte_budget_means_no_memory_limit():
    assert ineligibility_reason(**eligible_kwargs(burst_bytes=10**12, byte_budget=None)) is None


def test_old_firmware_is_not_an_ineligibility_but_an_error():
    # Old firmware answers the sequencer opcodes with SUCCESS and does nothing. Quietly falling
    # back would hide that the user asked for a feature the controller cannot deliver.
    with pytest.raises(RuntimeError, match="firmware"):
        ineligibility_reason(**eligible_kwargs(firmware_supports_sequencer=False))


# --- a finished burst is saved only if it is provably complete and in order ----------------------


def status(state=SeqState.DONE, error=SeqError.NONE, detail=0, frames_fired=20):
    return SequencerStatus(state=state, error=error, detail=detail, frames_fired=frames_fired)


def test_a_complete_in_order_burst_passes():
    assert burst_failure_reason(status(), expected=20, received=20, first_gap=None) is None


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        (dict(status=None, expected=20, received=20, first_gap=None), "no sequencer status"),
        (
            dict(
                status=status(state=SeqState.FAILED, error=SeqError.WAIT_TIMEOUT),
                expected=20,
                received=3,
                first_gap=None,
            ),
            "WAIT_TIMEOUT",
        ),
        # TURN_OFF_ALL_PORTS / watchdog: the command may COMPLETE, only the status says it failed
        (
            dict(
                status=status(state=SeqState.FAILED, error=SeqError.HOST_ABORT),
                expected=20,
                received=20,
                first_gap=None,
            ),
            "HOST_ABORT",
        ),
        (
            dict(status=status(error=SeqError.CANCELED, frames_fired=7), expected=20, received=7, first_gap=None),
            "CANCELED",
        ),
        (dict(status=status(frames_fired=19), expected=20, received=19, first_gap=None), "fired 19"),
        (dict(status=status(), expected=20, received=19, first_gap=None), "received 19"),
        (dict(status=status(), expected=20, received=20, first_gap=(104, 105)), "frame id"),
    ],
)
def test_each_burst_failure_names_its_reason(kwargs, fragment):
    reason = burst_failure_reason(**kwargs)
    assert reason is not None and fragment in reason


# --- O1 (Hongquan, 2026-09-20): retry the FOV once, sequenced ------------------------------------


def test_a_failed_burst_is_retried_once_then_the_acquisition_aborts():
    assert MAX_ATTEMPTS_PER_FOV == 2
    assert outcome_after_failed_burst(attempt=1) == BurstOutcome.RETRY
    assert outcome_after_failed_burst(attempt=2) == BurstOutcome.ABORT
