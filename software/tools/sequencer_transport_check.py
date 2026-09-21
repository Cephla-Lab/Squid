"""Controller-only check of the hardware sequencer's serial transport.

Needs ONLY a controller on USB — no camera, no scope, no light, no motors. It drives the real
firmware (>= 1.7) through every transport path and prints PASS / FAIL per check:

    upload + commit, run, run again with a new stack start, a corrupted upload (CRC), run without
    a committed program, a stack that leaves the DAC range, recovery after a failure, cancel
    mid-run, a command refused mid-run, TURN_OFF_ALL_PORTS mid-run, heartbeats during a run.

Why it exists: the firmware's transport callbacks (commands/sequence_commands.cpp) are Arduino-side
code. They are compiled in CI and mirrored by the Python simulator, but before this script ran
against a controller they had never been EXECUTED.

What it touches on the controller:
  - one DAC80508 channel, stepped as the "stack axis". YOU choose it with --stack-dac; there is no
    default, because channel 7 is the real objective piezo on a microscope (it WOULD move) and the
    script will not guess which output is safe on your bench;
  - camera trigger output 0 (pin 19 on the new controller, pin 29 on previous ones) pulses —
    harmless with no camera attached;
  - NO illumination port (the programs use TTL mask 0) and no laser intensity DAC, so the laser
    interlock state does not matter; no stepper moves.

USAGE (from software/, with the Squid GUI closed — only one process can hold the serial port):

    python -m tools.sequencer_transport_check --stack-dac 6
    python -m tools.sequencer_transport_check --stack-dac 6 --simulated     # dry run, no hardware
    python -m tools.sequencer_transport_check --stack-dac 6 --soak 200      # + 200 runs back to back
    python -m tools.sequencer_transport_check --stack-dac 6 --ready-line-unconnected
        # new controller, NOTHING on the camera-ready input (pin 18): measures the level the input
        # idles at, checks the gate in both polarities, and checks that an unplugged cable would
        # read NOT READY. Reads the pin only; it drives nothing.

Flash firmware 1.7 first, from firmware/controller on the sequencer branch:
    pio run -e teensy41_newctrl -t upload      (new controller: trigger pin 19, ready pin 18)
    pio run -e teensy41 -t upload              (previous controllers)
"""

import argparse
import logging
import sys
import time
from typing import Callable, List, Optional, Tuple

import control.microcontroller as microcontroller
from control.core.sequenced_acquisition import camera_record
import squid.logging
from control.microcontroller import CommandAborted
from control.sequencer_program import (
    MIN_FIRMWARE_VERSION,
    NONE_ID,
    Order,
    SeqCameraSpec,
    SeqChannelSpec,
    SeqError,
    SeqLoopSpec,
    SeqState,
    SequencerProgram,
    StackAxisType,
    TriggerMode,
    crc16_ccitt_false,
    split_words,
)

MID_RANGE = 32768
DZ_LSB = 218  # ~1 um on a 300 um piezo
EXPOSURES_US = (20_000, 50_000)
WAIT_TIMEOUT_US = 2_000_000
# What a real acquisition puts in the camera record - asked of the code that builds it, not restated.
ACQUISITION_READY_ACTIVE_HIGH = camera_record(
    use_ready_line=True, strobe_delay_ms=0.0, readout_ms=0.0
).ready_active_high


def make_program(
    stack_dac: int, n_layers: int, ready_line: int = NONE_ID, ready_active_high: bool = True
) -> SequencerProgram:
    return SequencerProgram(
        loop=SeqLoopSpec(
            stack_axis_type=StackAxisType.PIEZO,
            stack_axis_id=stack_dac,
            dz=DZ_LSB,
            n_layers=n_layers,
            order=Order.CHANNELS_INNER,
            z_settle_us=20_000,
            return_to_start=True,
        ),
        channels=[SeqChannelSpec(exposure_us=exposure, camera_mask=1, illum_ttl_mask=0) for exposure in EXPOSURES_US],
        cameras=[
            SeqCameraSpec(
                trigger_mode=TriggerMode.LEVEL,
                ready_line=ready_line,  # NONE_ID = timing model: no camera is attached
                ready_active_high=ready_active_high,
                readout_overlap_safe=True,
                strobe_delay_us=300,
                readout_time_us=25_000,
            )
        ],
        wait_timeout_us=WAIT_TIMEOUT_US,
    )


SOAK_LATE_MS = 100  # a run this much slower than the median counts as late
SOAK_STALL_MS = 1000  # a run this much slower than the median fails the soak on its own
SOAK_LATE_FRACTION = 0.001  # more late runs than this (and more than one) is a pattern, not a hiccup


def soak_verdict(durations_ms: List[float]) -> str:
    """'' when the spread of run times is healthy, else what is wrong with it.

    The times are HOST wall times: a run the controller finished on time is still reported late when
    the OS schedules the reader late or USB hiccups. Over a three-hour soak that happens now and then
    (bench 2026-09-20, Windows: one 597 ms run among 24,000 at a 385 ms median). One of those is not
    the controller stalling; a run that is a whole second late is, and so is a steady trickle of
    late runs.
    """
    ordered = sorted(durations_ms)
    median = ordered[len(ordered) // 2]
    if ordered[-1] > median + SOAK_STALL_MS:
        return f"slowest run took {ordered[-1]:.0f} ms against a median of {median:.0f} ms: something stalled"
    late = sum(1 for d in ordered if d > median + SOAK_LATE_MS)
    if late > max(1, int(len(ordered) * SOAK_LATE_FRACTION)):
        return (
            f"{late} of {len(ordered)} runs were more than {SOAK_LATE_MS} ms slower than the median ({median:.0f} ms)"
        )
    return ""


class CorruptionCounter(logging.Handler):
    """Counts the driver's "Bad checksum" warnings: bytes on the protocol port that are not a packet.

    The port carries a binary protocol and nothing else. The first run of this script on a real
    controller found a library printing text on it (FastLED's debug log, after FastLED.show());
    the checks only saw the consequences - a lost ack, a bogus status - so count the cause.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "Bad checksum" in record.getMessage():
            self.count += 1


class Checker:
    def __init__(
        self,
        mcu: microcontroller.Microcontroller,
        stack_dac: int,
        soak_runs: int,
        ready_line_unconnected: bool,
        corruption: CorruptionCounter,
    ):
        self.mcu = mcu
        self.stack_dac = stack_dac
        self.soak_runs = soak_runs
        self.ready_line_unconnected = ready_line_unconnected
        self.ready_idle_high: Optional[bool] = None  # measured by probe_ready_line
        self.corruption = corruption
        self.results: List[Tuple[str, bool, str]] = []

    # --- helpers ---------------------------------------------------------------------------

    def status(self):
        time.sleep(0.05)  # let one more 10 ms status packet arrive after the command completed
        return self.mcu.seq_status

    def expect_status(self, state: SeqState, error: SeqError, frames=None) -> str:
        status = self.status()
        problems = []
        if status is None:
            return "no sequencer status received"
        if status.state != state:
            problems.append(f"state {status.state!r}, expected {state!r}")
        if status.error != error:
            problems.append(f"error {status.error!r} (detail {status.detail}), expected {error!r}")
        if frames is not None and not frames(status.frames_fired):
            problems.append(f"frames_fired {status.frames_fired}")
        return "; ".join(problems)

    def run_and_wait(self, stack_start: int, timeout_s: float) -> float:
        started = time.time()
        self.mcu.seq_run(stack_start)
        self.mcu.wait_till_operation_is_completed(timeout_s)
        return time.time() - started

    def check(self, name: str, fn: Callable[[], str]) -> None:
        """fn returns '' on success or a description of what was wrong."""
        try:
            problem = fn()
        except Exception as e:  # a check must never stop the ones after it
            problem = f"unexpected {type(e).__name__}: {e}"
        ok = not problem
        self.results.append((name, ok, problem))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"\n         -> {problem}"))

    # --- the checks ------------------------------------------------------------------------

    def firmware_version(self) -> str:
        version = self.mcu.firmware_version
        if version < MIN_FIRMWARE_VERSION:
            return (
                f"controller reports firmware {version[0]}.{version[1]}; the sequencer needs "
                f">= {MIN_FIRMWARE_VERSION[0]}.{MIN_FIRMWARE_VERSION[1]}. Flash the sequencer branch first."
            )
        return ""

    def upload(self, n_layers: int) -> Callable[[], str]:
        def fn() -> str:
            self.mcu.seq_upload(make_program(self.stack_dac, n_layers))
            return ""

        return fn

    def normal_run(self) -> str:
        elapsed = self.run_and_wait(MID_RANGE, 10)
        problem = self.expect_status(SeqState.DONE, SeqError.NONE, lambda n: n == 6)
        # 3 layers x (20.3 + 50.3 ms exposure, each followed by ~25 ms readout / 20 ms settle)
        print(f"         3 layers x 2 channels took {elapsed * 1000:.0f} ms (expect roughly 350-500 ms)")
        if not problem and not 0.25 < elapsed < 1.5:
            problem = f"run took {elapsed:.2f} s, outside 0.25-1.5 s"
        return problem

    def run_again_new_start(self) -> str:
        self.run_and_wait(MID_RANGE + 1000, 10)
        return self.expect_status(SeqState.DONE, SeqError.NONE, lambda n: n == 6)

    def corrupted_upload(self) -> str:
        staged = make_program(self.stack_dac, 3).pack()
        for index, data in split_words(staged):
            cmd = bytearray(self.mcu.tx_buffer_length)
            cmd[1] = microcontroller.CMD_SET.SEQ_WRITE
            cmd[2] = index
            cmd[3:7] = data
            self.mcu.send_command(cmd)
            self.mcu.wait_till_operation_is_completed(2)
        bad_crc = crc16_ccitt_false(staged) ^ 0x5555
        cmd = bytearray(self.mcu.tx_buffer_length)
        cmd[1] = microcontroller.CMD_SET.SEQ_COMMIT
        cmd[2], cmd[3] = (len(staged) >> 8) & 0xFF, len(staged) & 0xFF
        cmd[4], cmd[5] = (bad_crc >> 8) & 0xFF, bad_crc & 0xFF
        self.mcu.send_command(cmd)
        try:
            self.mcu.wait_till_operation_is_completed(2)
        except CommandAborted:
            status = self.status()
            if status is None or status.error != SeqError.BAD_PROGRAM or status.detail != 3:
                return f"commit was refused, but the status says {status!r} (expected BAD_PROGRAM, detail 3 = CRC)"
            return ""
        return "a commit with a WRONG CRC was accepted"

    def run_without_commit(self) -> str:
        try:
            self.run_and_wait(MID_RANGE, 5)
        except CommandAborted:
            status = self.status()
            return "" if status is not None and status.error == SeqError.NOT_COMMITTED else f"status {status!r}"
        return "SEQ_RUN was accepted although the last commit failed"

    def stack_out_of_range(self) -> str:
        try:
            self.run_and_wait(65535, 5)  # dz > 0: the second layer would pass 65535
        except CommandAborted:
            return self.expect_status(SeqState.FAILED, SeqError.STACK_OUT_OF_RANGE, lambda n: n == 0)
        return "a stack leaving the DAC range was accepted"

    def refused_command_mid_run(self) -> str:
        self.mcu.seq_run(MID_RANGE)
        time.sleep(0.05)
        # Refused by the dispatcher's allow-table. If the firmware failed to refuse it, all it
        # does is write the same spare DAC the run is already stepping.
        self.mcu.analog_write_onboard_DAC(self.stack_dac, MID_RANGE)
        try:
            self.mcu.wait_till_operation_is_completed(10)
        except CommandAborted:
            return self.expect_status(SeqState.DONE, SeqError.NONE, lambda n: n == 6)
        return "a DAC write during a run was NOT refused"

    def heartbeats_mid_run(self) -> str:
        self.mcu.seq_run(MID_RANGE)
        for _ in range(5):
            time.sleep(0.05)
            self.mcu.send_heartbeat()
        self.mcu.wait_till_operation_is_completed(10)
        return self.expect_status(SeqState.DONE, SeqError.NONE, lambda n: n == 6)

    def cancel_mid_run(self) -> str:
        self.mcu.seq_run(MID_RANGE)
        time.sleep(0.5)
        self.mcu.seq_cancel()
        self.mcu.wait_till_operation_is_completed(10)
        return self.expect_status(SeqState.DONE, SeqError.CANCELED, lambda n: 0 < n < 120)

    def turn_off_all_ports_mid_run(self) -> str:
        self.mcu.seq_run(MID_RANGE)
        time.sleep(0.5)
        self.mcu.turn_off_all_ports()  # a safety command must itself SUCCEED...
        self.mcu.wait_till_operation_is_completed(10)
        # ...while the status says the run did not.
        return self.expect_status(SeqState.FAILED, SeqError.HOST_ABORT, lambda n: 0 < n < 120)

    def probe_ready_line(self) -> str:
        """With nothing connected the ready input sits at ONE level and never changes.

        Gated on the OTHER level, the line never reads ready: the run must fire nothing, give up
        after wait_timeout_us and say WAIT_TIMEOUT. Gated on the level it idles at, the line reads
        ready but never goes BUSY after the first trigger - a stuck line - so the run must fire
        exactly one frame and stop with READY_TIMEOUT naming camera 0 (the liveness check). A run
        that completes means the firmware predates that check, or something IS driving the input.
        """
        timeout_s = WAIT_TIMEOUT_US / 1e6
        stuck_at = []  # the polarity (active_high) under which the line read ready-and-stuck
        for active_high in (True, False):
            level = "HIGH" if active_high else "LOW"
            self.mcu.seq_upload(make_program(self.stack_dac, 3, ready_line=0, ready_active_high=active_high))
            started = time.time()
            try:
                self.run_and_wait(MID_RANGE, 10)
            except CommandAborted:
                elapsed = time.time() - started
                status = self.status()
                if status is not None and status.error == SeqError.READY_TIMEOUT:
                    problem = self.expect_status(SeqState.FAILED, SeqError.READY_TIMEOUT, lambda n: n == 1)
                    if not problem and status.detail != 0:
                        problem = f"READY_TIMEOUT names camera {status.detail}, expected camera 0"
                    if problem:
                        return f"gated on {level}: {problem}"
                    stuck_at.append(active_high)
                    continue
                problem = self.expect_status(SeqState.FAILED, SeqError.WAIT_TIMEOUT, lambda n: n == 0)
                if not problem and not timeout_s - 0.2 < elapsed < timeout_s + 0.5:
                    problem = f"gave up after {elapsed:.2f} s, expected about {timeout_s:.0f} s"
                if problem:
                    return f"gated on {level}: {problem}"
                continue
            return (
                f"gated on {level} the run COMPLETED: either this firmware has no ready-line liveness check "
                "(rebuild and flash the current branch), or something is driving the ready input - unplug it"
            )
        if len(stuck_at) == 2:
            return "READY_TIMEOUT gated on HIGH and gated on LOW: the ready input is not at a steady level"
        if not stuck_at:
            return "WAIT_TIMEOUT gated on HIGH and gated on LOW: the ready input is not at a steady level"
        self.ready_idle_high = stuck_at[0]
        idle = "HIGH" if self.ready_idle_high else "LOW"
        print(
            f"         the unconnected ready input idles {idle}; gated on {idle} the run fired one frame and stopped "
            f"with READY_TIMEOUT (stuck line), gated on the other level it fired nothing and gave up after {timeout_s:.0f} s"
        )
        return ""

    def unconnected_ready_line_fails_safe(self) -> str:
        if self.ready_idle_high is None:
            return "not determined: the ready-line probe above did not pass"
        if self.ready_idle_high == ACQUISITION_READY_ACTIVE_HIGH:
            return (
                "an unplugged or broken ready cable reads READY on this controller: the acquisition treats "
                f"{'HIGH' if ACQUISITION_READY_ACTIVE_HIGH else 'LOW'} as ready and the input idles there. A gated run "
                "would not time out; it would trigger without waiting for the camera. FIRST make sure the input "
                "really is unconnected: a camera output wired to it drives the line (an unconfigured Hamamatsu "
                "output sits LOW) and this check then measures the camera, not the board - unplug it and rerun"
            )
        return ""

    def soak(self) -> str:
        """Many runs back to back, as an acquisition sends them: one SEQ_RUN per FOV, no re-upload."""
        durations_ms = []
        for index in range(self.soak_runs):
            durations_ms.append(self.run_and_wait(MID_RANGE + (index % 50) * 100, 10) * 1000)
            problem = self.expect_status(SeqState.DONE, SeqError.NONE, lambda n: n == 6)
            if problem:
                return f"run {index + 1} of {self.soak_runs}: {problem}"
        durations_ms.sort()
        median = durations_ms[len(durations_ms) // 2]
        late = sum(1 for d in durations_ms if d > median + SOAK_LATE_MS)
        print(
            f"         {self.soak_runs} runs: min {durations_ms[0]:.0f} / median {median:.0f} / "
            f"max {durations_ms[-1]:.0f} ms, {late} more than {SOAK_LATE_MS} ms late "
            "(host wall time; status packets arrive every 10 ms)"
        )
        return soak_verdict(durations_ms)

    def no_corrupted_packets(self) -> str:
        if self.corruption.count:
            return (
                f"{self.corruption.count} corrupted packets: something other than the protocol wrote to the serial "
                "port (rerun with --verbose to see the bytes; text decodes as ASCII)"
            )
        return ""

    def run_all(self) -> bool:
        self.check("firmware is >= 1.7", self.firmware_version)
        if not self.results[-1][1]:
            return False
        self.check("upload + commit a 3-layer x 2-channel program", self.upload(3))
        self.check("run: Done, 6 frames fired, plausible duration", self.normal_run)
        self.check("run again with a new stack start, no re-upload", self.run_again_new_start)
        self.check("a commit with a wrong CRC is refused (BAD_PROGRAM, detail 3)", self.corrupted_upload)
        self.check("SEQ_RUN after a failed commit is refused (NOT_COMMITTED)", self.run_without_commit)
        self.check("re-upload after the failed commit", self.upload(3))
        self.check("a stack leaving the DAC range is refused before anything moves", self.stack_out_of_range)
        self.check("the controller recovers: the next run is Done", self.run_again_new_start)
        self.check("a command sent mid-run is refused; the run itself still completes", self.refused_command_mid_run)
        self.check("heartbeats during a run do not disturb it", self.heartbeats_mid_run)
        self.check("upload a long program (60 layers, ~7 s)", self.upload(60))
        self.check("cancel mid-run: Done / CANCELED, partial frame count", self.cancel_mid_run)
        self.check(
            "TURN_OFF_ALL_PORTS mid-run: command succeeds, status Failed / HOST_ABORT", self.turn_off_all_ports_mid_run
        )
        if self.ready_line_unconnected:
            self.check(
                "ready-line gate: WAIT_TIMEOUT on one level, READY_TIMEOUT (stuck line) on the other",
                self.probe_ready_line,
            )
            self.check(
                "an unconnected ready input reads NOT READY to the acquisition", self.unconnected_ready_line_fails_safe
            )
        self.check("re-upload the short program", self.upload(3))
        self.check("the controller recovers after the aborts", self.run_again_new_start)
        if self.soak_runs:
            self.check(f"soak: {self.soak_runs} runs back to back, every one Done with 6 frames", self.soak)
        self.check("nothing but protocol packets on the serial port during all of the above", self.no_corrupted_packets)
        return all(ok for _, ok, _ in self.results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--stack-dac",
        type=int,
        required=True,
        choices=range(8),
        help="DAC80508 channel stepped as the stack axis. 7 is the real objective piezo on a microscope.",
    )
    parser.add_argument("--simulated", action="store_true", help="dry run against the firmware simulator")
    parser.add_argument("--sn", default=None, help="controller serial number, when several are connected")
    parser.add_argument(
        "--soak",
        type=int,
        default=0,
        metavar="N",
        help="also run the short program N times back to back (~0.45 s each)",
    )
    parser.add_argument(
        "--ready-line-unconnected",
        action="store_true",
        help="new controller only, and ONLY with nothing connected to the camera-ready input (pin 18): "
        "measure the level it idles at, check the gate in both polarities, and check that the idle level "
        "means NOT READY to the acquisition",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.simulated and args.ready_line_unconnected:
        parser.error("--ready-line-unconnected needs a real controller: the simulator does not model ready lines")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    corruption = CorruptionCounter()
    squid.logging.get_logger().addHandler(corruption)  # before the port opens

    serial_device = microcontroller.get_microcontroller_serial_device(sn=args.sn, simulated=args.simulated)
    mcu = microcontroller.Microcontroller(serial_device, reset_and_initialize=False)
    try:
        print(
            f"Sequencer transport check ({'SIMULATED' if args.simulated else 'real controller'}), stack DAC {args.stack_dac}"
        )
        time.sleep(0.2)
        if corruption.count:  # the port can open in the middle of a packet; that is not the controller's doing
            print(f"  note: {corruption.count} corrupted packets while connecting - reported here, not counted below")
            corruption.count = 0
        ok = Checker(mcu, args.stack_dac, args.soak, args.ready_line_unconnected, corruption).run_all()
    finally:
        mcu.close()
    print("\nRESULT: " + ("all checks passed" if ok else "FAILURES - see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
