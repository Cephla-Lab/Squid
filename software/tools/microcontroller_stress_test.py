import time

import control.microcontroller
import squid.logging

log = squid.logging.get_logger("mst")

def main(args):
    log.info("Creating microcontroller...")
    micro = control.microcontroller.Microcontroller(serial_device=control.microcontroller.get_microcontroller_serial_device(simulated=False))

    end_time = time.time() + args.runtime

    loop_count = 0
    while time.time() < end_time:
        loop_count += 1
        if loop_count % args.report_interval == 0:
            log.info(f"Loop count {loop_count}")
        if args.laser_af:
            micro.turn_on_AF_laser()
            micro.wait_till_operation_is_completed()
            micro.turn_off_AF_laser()
            micro.wait_till_operation_is_completed()
        time.sleep(0)

if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="A stress test to try to trigger microcontroller errors.")

    ap.add_argument("--runtime", type=float, help="The time to run the test for, in [s]", default=60)
    ap.add_argument("--report_interval", type=int, help="How often to report (in loop counts)", default=100)
    ap.add_argument("--laser_af", action="store_true", help="Toggle the laser af on/off as part of the test.")

    args = ap.parse_args()

    sys.exit(main(args))
