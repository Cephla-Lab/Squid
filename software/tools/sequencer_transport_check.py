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

Flash firmware 1.7 first, from firmware/controller on the sequencer branch:
    pio run -e teensy41_newctrl -t upload      (new controller: trigger pin 19, ready pin 18)
    pio run -e teensy41 -t upload              (previous controllers)
"""

import argparse
import logging
import sys
import time
from typing import Callable, List, Tuple

import control.microcontroller as microcontroller
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


def make_program(stack_dac: int, n_layers: int) -> SequencerProgram:
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
                ready_line=NONE_ID,  # timing model: no camera is attached
                ready_active_high=True,
                readout_overlap_safe=True,
                strobe_delay_us=300,
                readout_time_us=25_000,
            )
        ],
        wait_timeout_us=2_000_000,
    )


class Checker:
    def __init__(self, mcu: microcontroller.Microcontroller, stack_dac: int):
        self.mcu = mcu
        self.stack_dac = stack_dac
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
        self.check("re-upload the short program", self.upload(3))
        self.check("the controller recovers after the aborts", self.run_again_new_start)
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
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    serial_device = microcontroller.get_microcontroller_serial_device(sn=args.sn, simulated=args.simulated)
    mcu = microcontroller.Microcontroller(serial_device, reset_and_initialize=False)
    try:
        print(
            f"Sequencer transport check ({'SIMULATED' if args.simulated else 'real controller'}), stack DAC {args.stack_dac}"
        )
        ok = Checker(mcu, args.stack_dac).run_all()
    finally:
        mcu.close()
    print("\nRESULT: " + ("all checks passed" if ok else "FAILURES - see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
