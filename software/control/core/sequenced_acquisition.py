"""Hardware-sequenced acquisition — the pure host-side logic.

In a software-sequenced acquisition the host commands every z move, illumination switch and
camera trigger one by one. In a hardware-sequenced one the microcontroller runs the whole
multichannel z-stack at an XY position from ONE program: the host uploads the program once per
acquisition, sends SEQ_RUN once per FOV, and only receives frames.

So everything the per-frame host loop used to do has to be resolved UP FRONT, with exactly the
arithmetic the per-frame commands use today — otherwise a sequenced and a software-sequenced
acquisition of the same sample would differ. This module holds that arithmetic, the decision
whether an acquisition may be sequenced at all (and why not), and the check that decides whether
a finished burst may be saved. Nothing here touches hardware, Qt or the worker.

Design: AI-docs Squid/to-do/2026-09-20-firmware-v2-hardware-sequencer-v1-shim-design.md.
"""

import dataclasses
import enum
from typing import Optional, Sequence, Tuple

import numpy as np

from control.sequencer_program import (
    NONE_ID,
    Order,
    SeqCameraSpec,
    SeqChannelSpec,
    SeqError,
    SeqLoopSpec,
    SeqState,
    SequencerProgram,
    SequencerStatus,
    StackAxisType,
    TriggerMode,
)

# Illumination source code -> TTL port index. The port index is also the TTL mask bit and the
# DAC80508 channel that carries that port's intensity. NOT sequential: D3 is 14 and D4 is 13
# (firmware/controller/src/utils/illumination_mapping.h).
SOURCE_CODE_TO_PORT = {11: 0, 12: 1, 14: 2, 13: 3, 15: 4}

PIEZO_DAC_ID = 7  # DAC80508 channel wired to the objective piezo (Microcontroller.set_piezo_um)

# O1 (Hongquan, 2026-09-20): "Retry the FOV once, sequenced." - then, the same day: "after a
# failed retry, retry again. if it still failed, pause or fail the acquisition (depending on
# what's supported now) and ask the user to intervene". So: three attempts per FOV, and after the
# third the acquisition cannot decide on its own. Three failures on one FOV look systematic (USB,
# disk, camera, trigger cable); carrying on would produce more suspect data.
MAX_ATTEMPTS_PER_FOV = 3


class BurstOutcome(enum.Enum):
    RETRY = "retry"
    ASK_USER = "ask_user"  # stop and hand the decision to the user (pause when supported, else fail)


def outcome_after_failed_burst(attempt: int) -> BurstOutcome:
    """What to do after sequenced attempt number `attempt` (1-based) of a FOV failed."""
    return BurstOutcome.RETRY if attempt < MAX_ATTEMPTS_PER_FOV else BurstOutcome.ASK_USER


def intervention_message(*, region_id, fov, attempts: int, last_reason: str) -> str:
    """What the user is told when a FOV failed every attempt. Pausing an acquisition is not
    supported yet, so the run is stopped and has to be restarted once the cause is fixed."""
    return (
        f"Hardware-sequenced acquisition stopped at region {region_id}, FOV {fov}: the image burst failed "
        f"{attempts} times in a row. Last reason: {last_reason}. Nothing from the failed bursts was saved. "
        "Check the camera connection and its trigger / ready cables, the controller, and free disk space, "
        "then restart the acquisition."
    )


@dataclasses.dataclass(frozen=True)
class ChannelPlan:
    """One channel of a burst, already resolved by the caller through the SAME path the
    per-frame loop uses (intensity cap, calibration LUT, wavelength -> source code)."""

    name: str
    exposure_ms: float
    source_code: int  # what Microcontroller.set_illumination() would be given
    dac_percent: float  # 0-100, AFTER the calibration LUT
    z_offset_um: float = 0.0


def intensity_percent_to_dac(dac_percent: float, intensity_factor: float) -> int:
    """The DAC code that set_illumination(source, dac_percent) ends up writing.

    Host (Microcontroller.set_illumination) sends int(percent / 100 * 65535); the firmware
    multiplies by illumination_intensity_factor, which the host sent as a whole-percent byte
    (set_dac80508_scaling_factor_for_illumination) and the firmware holds as a float32.
    """
    u16 = int((dac_percent / 100) * 65535)
    if intensity_factor > 1:
        intensity_factor = 1
    if intensity_factor < 0:
        intensity_factor = 0.01
    factor_byte = min(int(round(intensity_factor, 2) * 100), 100)
    return int(np.float32(u16) * (np.float32(factor_byte) / np.float32(100)))


def piezo_um_to_dac(z_um: float, range_um: float, flip: bool) -> int:
    """Microcontroller.set_piezo_um(), as a pure function."""
    dac = int(65535 * (z_um / range_um))
    return 65535 - dac if flip else dac


def piezo_step_lsb(delta_um: float, range_um: float, flip: bool) -> int:
    """A relative piezo move in DAC LSB; the sign follows OBJECTIVE_PIEZO_FLIP_DIR."""
    step = round(65535 * delta_um / range_um)
    return -step if flip else step


def camera_record(*, use_ready_line: bool, strobe_delay_ms: float, readout_ms: float) -> SeqCameraSpec:
    """The camera record of a burst: LEVEL trigger, exposure = pulse width.

    Without the ready line the controller models readiness as exposure end + readout_ms. With it,
    the line is ACTIVE LOW: the controller's ready input is pulled up on the board (new controller:
    about 4.7 k to 3.3 V, measured), so an unplugged or broken cable reads HIGH. HIGH therefore has
    to mean NOT ready - the run then times out before its first frame instead of triggering without
    waiting for the camera. The camera drivers configure their ready outputs to match
    (CAMERA_TRIGGER_READY_OUTPUT).
    """
    return SeqCameraSpec(
        trigger_mode=TriggerMode.LEVEL,
        ready_line=0 if use_ready_line else NONE_ID,
        ready_active_high=False,
        readout_overlap_safe=True,  # global reset + strobed light: nothing is lit during readout
        strobe_delay_us=round(strobe_delay_ms * 1000),
        readout_time_us=round(readout_ms * 1000),
    )


def build_program(
    channels: Sequence[ChannelPlan],
    *,
    n_layers: int,
    dz_um: float,
    piezo_range_um: float,
    piezo_flip: bool,
    z_settle_ms: float,
    intensity_factor: float,
    camera: SeqCameraSpec,
    wait_timeout_s: float,
) -> SequencerProgram:
    """The MCU program for one piezo z-stack: z outer, channels inner — the worker's nesting."""
    channel_specs = []
    for channel in channels:
        if channel.source_code not in SOURCE_CODE_TO_PORT:
            raise ValueError(
                f"Channel '{channel.name}' uses illumination source code {channel.source_code}, which is not an "
                f"MCU TTL port ({sorted(SOURCE_CODE_TO_PORT)}); it cannot be strobed by the hardware sequencer."
            )
        port = SOURCE_CODE_TO_PORT[channel.source_code]
        channel_specs.append(
            SeqChannelSpec(
                exposure_us=round(channel.exposure_ms * 1000),
                camera_mask=1,
                illum_ttl_mask=1 << port,
                intensity_dac=port,
                intensity=intensity_percent_to_dac(channel.dac_percent, intensity_factor),
                z_offset=piezo_step_lsb(channel.z_offset_um, piezo_range_um, piezo_flip),
            )
        )
    program = SequencerProgram(
        loop=SeqLoopSpec(
            stack_axis_type=StackAxisType.PIEZO,
            stack_axis_id=PIEZO_DAC_ID,
            dz=piezo_step_lsb(dz_um, piezo_range_um, piezo_flip),
            n_layers=n_layers,
            order=Order.CHANNELS_INNER,
            z_settle_us=round(z_settle_ms * 1000),
            return_to_start=True,
        ),
        channels=channel_specs,
        cameras=[camera],
        wait_timeout_us=round(wait_timeout_s * 1e6),
    )
    program.validate()
    return program


def ineligibility_reason(
    *,
    firmware_supports_sequencer: bool,
    trigger_is_hardware: bool,
    use_piezo: bool,
    global_reset_active: bool,
    intensity_is_mcu_dac: bool,
    channels: Sequence[ChannelPlan],
    camera_gains: Sequence[float],
    burst_bytes: int,
    byte_budget: Optional[int],
    use_ready_line: bool,
    camera_drives_ready_line: bool,
) -> Optional[str]:
    """Why this acquisition cannot be hardware-sequenced, or None when it can.

    Decided once per acquisition. An ineligible acquisition runs software-sequenced exactly as
    it does today, and the reason is logged. Two things are NOT ineligibilities but errors, because
    falling back would hide that the setup cannot deliver what was asked for: old firmware (it
    answers the sequencer opcodes with success and does nothing), and gating on a camera-ready line
    the camera was never configured to drive.
    """
    if not firmware_supports_sequencer:
        raise RuntimeError(
            "Hardware-sequenced acquisition is enabled, but the controller firmware is older than 1.7 and "
            "would silently ignore the sequencer commands. Update the firmware or turn the setting off."
        )
    if use_ready_line and not camera_drives_ready_line:
        raise RuntimeError(
            "SEQUENCER_USE_CAMERA_READY_LINE is on, but CAMERA_TRIGGER_READY_OUTPUT is off: the camera's "
            "trigger-ready output is not configured (active low), so the controller would gate its triggers on a "
            "line nobody drives. Turn CAMERA_TRIGGER_READY_OUTPUT on, or the ready-line setting off."
        )
    if not trigger_is_hardware:
        return "the camera is not in hardware trigger mode"
    if not use_piezo:
        return "the z-stack does not use the piezo (stepper z-stacks are not sequenced yet)"
    if not global_reset_active:
        return (
            "LEVEL trigger + global reset is not active (hardware_trigger_mode = LEVEL and "
            "HARDWARE_TRIGGER_GLOBAL_RESET): per-channel exposure cannot change without a host command"
        )
    if not intensity_is_mcu_dac:
        return "the light source's intensity is not set through the controller's DACs"
    if not channels:
        return "no channels are selected"
    for channel in channels:
        if "RGB" in channel.name:
            return f"channel '{channel.name}' is an RGB composite"
        if channel.z_offset_um != 0:
            # The per-channel offset is anchored to the autofocus result per FOV today; until that is
            # modelled in the program, such acquisitions stay software-sequenced.
            return f"channel '{channel.name}' has a z offset ({channel.z_offset_um} um), which is not sequenced yet"
        if channel.source_code not in SOURCE_CODE_TO_PORT:
            return f"channel '{channel.name}' is not strobed from an MCU TTL port (source code {channel.source_code})"
    if len(set(camera_gains)) > 1:
        return f"the channels use different camera gain values ({sorted(set(camera_gains))}); gain is a camera register write"
    if byte_budget is not None and burst_bytes > byte_budget:
        return (
            f"one burst needs {burst_bytes / 1e6:.0f} MB of memory but the acquisition's image budget is "
            f"{byte_budget / 1e6:.0f} MB (frames are held until the burst is validated)"
        )
    return None


def burst_failure_reason(
    status: Optional[SequencerStatus], *, expected: int, received: int, first_gap: Optional[Tuple[int, int]]
) -> Optional[str]:
    """Why a finished burst must NOT be saved, or None when it is complete and in order.

    wait_till_operation_is_completed() returning is not enough: a TURN_OFF_ALL_PORTS shutdown
    lets the pending SEQ_RUN complete successfully while the status says Failed / HostAbort.
    A pairing mistake saves the right pixels under the wrong channel or z and nothing crashes,
    so every condition here is required before a single frame of the burst reaches a save job.
    """
    if status is None:
        return "no sequencer status was received from the controller"
    if status.state != SeqState.DONE or status.error != SeqError.NONE:
        error = status.error.name if isinstance(status.error, SeqError) else str(status.error)
        state = status.state.name if isinstance(status.state, SeqState) else str(status.state)
        return f"the sequence ended {state}/{error} (detail {status.detail}) after {status.frames_fired} frames"
    if status.frames_fired != expected:
        return f"the controller fired {status.frames_fired} of {expected} frames"
    if received != expected:
        return f"the camera delivered only {received} of {expected} frames (received {received})"
    if first_gap is not None:
        return f"a frame was dropped inside the burst (expected frame id {first_gap[0]}, got {first_gap[1]})"
    return None
