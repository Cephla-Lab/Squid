import logging
import time
from itertools import cycle
import threading

import control.microcontroller
import squid.logging
import squid.stage.cephla
import squid.config

log = squid.logging.get_logger("mst")


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    log.info("Creating microcontroller...")
    micro = control.microcontroller.Microcontroller(
        serial_device=control.microcontroller.get_microcontroller_serial_device(simulated=False)
    )

    stage_positions = cycle([(10, 10), (10.001, 10), (10.001, 10.001), (10, 10.001)])
    stage = None
    if args.stage:
        stage = squid.stage.cephla.CephlaStage(micro, stage_config=squid.config.get_stage_config())
        # stage.home(x=False, y=False, z=True, theta=False)
        log.info("Moving z to 0.1")
        stage.move_z(0.1)
        log.info("Moving x to 20")
        stage.move_x(20)
        log.info("Homing y")
        stage.home(x=False, y=True, z=False, theta=False)
        log.info("Homing x")
        stage.home(x=True, y=False, z=False, theta=False)
    end_time = time.time() + args.runtime
    start_time = time.time()
    keep_running = threading.Event()

    def run_test():
        loop_count = 0
        last_loop_end = time.time()

        def wait_if_needed():
            if not args.no_wait:
                log.debug("Waiting...")
                micro.wait_till_operation_is_completed()

        blocking_moves = not args.no_wait

        while time.time() < end_time and keep_running.is_set():
            next_pos = next(stage_positions)
            # The stage moves are split so they bracket other micro commands.
            if stage:
                log.debug(f"Moving x to {next_pos[0]} [mm]")
                stage.move_x_to(next_pos[0], blocking=blocking_moves)
            if args.laser_af:
                log.debug("Turning af laser on then off.")
                micro.turn_on_AF_laser()
                wait_if_needed()
                micro.turn_off_AF_laser()
                wait_if_needed()
            if stage:
                log.debug(f"Moving y to {next_pos[1]} [mm]")
                stage.move_y_to(next_pos[1], blocking=blocking_moves)
            if not args.no_loop_sleep:
                time.sleep(0.0001)

            loop_count += 1
            if loop_count % args.report_interval == 0:
                log.info(
                    f"Loop count {loop_count}, last loop time [s]: {time.time() - last_loop_end}, avg time per loop [s]: {(time.time() - start_time) / loop_count}"
                )
            last_loop_end = time.time()

    def hang_out():
        while time.time() < end_time:
            time.sleep(0.001)

    keep_running.set()

    if args.on_thread:
        test_thread = threading.Thread(target=run_test)
        test_thread.start()
        try:
            hang_out()
        except KeyboardInterrupt:
            keep_running.clear()

        test_thread.join()
    else:
        run_test()


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="A stress test to try to trigger microcontroller errors.")

    ap.add_argument("--runtime", type=float, help="The time to run the test for, in [s]", default=60)
    ap.add_argument("--report_interval", type=int, help="How often to report (in loop counts)", default=100)
    ap.add_argument("--laser_af", action="store_true", help="Toggle the laser af on/off as part of the test.")
    ap.add_argument(
        "--stage", action="store_true", help="Create a motion stage, home it, then use it in the stress test."
    )
    ap.add_argument("--no_loop_sleep", action="store_true", help="Do not sleep to yield the test thread.")
    ap.add_argument(
        "--on_thread", action="store_true", help="Run the test on a daemon thread while the main thread spins"
    )
    ap.add_argument("--verbose", action="store_true", help="Turn on debug logging.")
    ap.add_argument("--no_wait", action="store_true", help="Turn off waiting for micro to complete commands.")
    args = ap.parse_args()

    sys.exit(main(args))
