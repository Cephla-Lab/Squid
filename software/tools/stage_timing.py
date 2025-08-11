import logging

from control.microscope import Microscope
import squid.abc
import squid.logging
import time

log = squid.logging.get_logger("stage timing")


def get_move_fn(scope: Microscope, stage: squid.abc.AbstractStage, axis: str, relative):
    match axis.lower():
        case "z":
            return stage.move_z if relative else scope.move_z_to
        case "y":
            return stage.move_y if relative else scope.move_y_to
        case "x":
            return stage.move_x if relative else scope.move_x_to
        case _:
            raise ValueError(f"Unknown axis {axis}")


def home(scope: Microscope):
    scope.stage.home(x=False, y=False, z=True, theta=False)
    scope.stage.move_x(20)
    scope.stage.home(x=False, y=True, z=False, theta=False)
    scope.stage.home(x=True, y=False, z=False, theta=False)


def main(args):
    if args.verbose:
        squid.logging.set_stdout_log_level(logging.DEBUG)

    scope: Microscope = Microscope.build_from_global_config(False)

    if args.home:
        home(scope)

    axis_move_fn = get_move_fn(scope, scope.stage, args.axis, args.relative)
    axis_move_fn(args.start)

    t0 = time.time()
    total_moves = args.count
    log.info(f"Argument summary:\n {args}")

    def report(moves_so_far):
        elapsed = time.time() - t0
        log.info(
            f"\nAfter {moves_so_far}/{total_moves} moves of {args.axis} axis starting at {args.start} [mm] and step of {args.step} [mm]:\n  total time={elapsed:.3f} [s]\n  time per move={elapsed / moves_so_far:.3f} [s]\n  current position= {scope.stage.get_pos()} [mm, mm, mm]"
        )

    step = args.step
    start_pos = args.start
    for i in range(total_moves):
        this_move_num = i + 1
        move_pos = step if args.relative else start_pos + step * this_move_num
        axis_move_fn(move_pos)
        if this_move_num % args.report_interval == 0:
            report(this_move_num)
    report(total_moves)


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Run a stage z move timing test (make sure z axis is clear!)")

    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--report_interval", type=int, default=10, help="Report every this many moves.")
    ap.add_argument("--count", type=int, default=25, help="The number of moves to execute.")
    ap.add_argument("--axis", type=str, choices=["x", "y", "z"], default="z", help="The axis to do a timing test with.")
    ap.add_argument("--start", type=float, default=0.1, help="The starting position to use in mm.")
    ap.add_argument(
        "--step", type=float, default=0.001, help="The step size to use in mm.  This should be small!  EG 0.001"
    )
    ap.add_argument("--no_home", dest="home", action="store_false", help="Do not home zxy before running.")
    ap.add_argument("--relative", action="store_true", help="Use relative moves instead of absolute.")

    sys.exit(main(ap.parse_args()))
