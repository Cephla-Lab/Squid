import logging

from control.microscope import Microscope
import squid.logging
import time

log = squid.logging.get_logger("stage timing")


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    scope = Microscope(is_simulation=False)
    scope.stage.home(x=False, y=False, z=True, theta=False)

    z_pos_um = 1000
    scope.move_z_to(z_pos_um / 1000)

    t0 = time.time()
    N = args.count

    def report(moves_so_far):
        elapsed = time.time() - t0
        log.info(
            f"After {moves_so_far}/{N} moves:\n  total time={elapsed:.3f} [s]\n  time per move={elapsed / moves_so_far:.3f} [s]"
        )

    step_direction = 1 if not args.down else -1
    for i in range(N):
        this_move_num = i + 1
        scope.move_z_to((z_pos_um + step_direction * i) / 1000)
        if this_move_num % args.report_interval == 0:
            report(this_move_num)
    report(N)


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Run a stage z move timing test (make sure z axis is clear!)")

    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--report_interval", type=int, default=10, help="Report every this many moves.")
    ap.add_argument("--count", type=int, default=25, help="The number of moves to execute.")
    ap.add_argument("--down", action="store_true", help="Instead of making +z steps, make -z steps.")

    sys.exit(main(ap.parse_args()))
