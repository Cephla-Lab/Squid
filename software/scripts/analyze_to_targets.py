#!/usr/bin/env python3
"""Turn a finished survey acquisition into a new set of stage coordinates.

Reads a completed acquisition folder, runs an image analyzer over every field of
view, converts what it finds into absolute stage coordinates, and writes a new
flexible-type ``acquisition.yaml`` + ``coordinates.csv`` that can be run
directly (Flexible Multipoint, the Workflow Runner, or the TCP server).

No Qt and no hardware -- it can be run offline against an acquisition copied off
the microscope PC.

Typical use, as the middle step of a Workflow Runner workflow:

    Acquisition(survey_4x.yaml) -> Script(this) -> Acquisition(targets/acquisition.yaml)

When run from the Workflow Runner, ``--input`` may be omitted: the path of the
acquisition that just finished arrives in ``SQUID_LAST_ACQUISITION_PATH``.

Examples:
    python scripts/analyze_to_targets.py --input /data/survey --analyzer ring_grid \
        --template configs/highmag_20x.yaml --out /data/targets --overlay

    python scripts/analyze_to_targets.py --list-analyzers
"""

import argparse
import json
import os
import sys

# Allow running as `python scripts/analyze_to_targets.py` from the software dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from control.core.smart_acquisition import analyzers, emit, pipeline  # noqa: E402

ENV_ACQUISITION_PATH = "SQUID_LAST_ACQUISITION_PATH"
ENV_CYCLE = "SQUID_CYCLE"


def _parse_params(values):
    """Parse repeated --param key=value into a dict, coercing JSON-ish values."""
    params = {}
    for item in values or []:
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"--param expects key=value, got {item!r}")
        key, _, raw = item.partition("=")
        try:
            params[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            params[key.strip()] = raw
    return params


def build_parser():
    p = argparse.ArgumentParser(
        description="Analyse a survey acquisition and emit stage coordinates for a follow-up run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", "-i", help=f"Survey acquisition folder (default: ${ENV_ACQUISITION_PATH})")
    p.add_argument("--analyzer", "-a", default="ring_grid", help="Analyzer name (default: ring_grid)")
    p.add_argument("--template", "-t", help="High-magnification acquisition.yaml supplying channels/z/AF")
    p.add_argument("--out", "-o", help="Output folder for acquisition.yaml + coordinates.csv")
    p.add_argument("--param", "-p", action="append", metavar="KEY=VALUE", help="Analyzer parameter; repeatable")
    p.add_argument("--channel", help="Channel to analyse (default: the acquisition's first)")
    p.add_argument("--timepoint", type=int, help="Timepoint to analyse (default: first)")
    p.add_argument("--z-level", type=int, help="Z level to analyse (default: middle of the stack)")
    p.add_argument("--carry-z", action="store_true", help="Propagate survey z instead of leaving it to autofocus")
    p.add_argument(
        "--sweep-z",
        action="store_true",
        help="Analyse every plane of each z-stack and keep the best-focus one (implies --carry-z)",
    )
    p.add_argument(
        "--no-interpolate-z",
        action="store_true",
        help="With --sweep-z, snap to the winning plane instead of interpolating between planes",
    )
    p.add_argument("--merge-um", type=float, help="Merge targets closer than this (default: 0.3 x grid pitch)")
    p.add_argument("--min-score", type=float, default=0.0, help="Drop targets scoring below this")
    p.add_argument("--max-targets", type=int, help="Keep only the N highest-scoring targets")
    p.add_argument("--prefix", default="smart", help="Region-name prefix for emitted targets (default: smart)")
    p.add_argument(
        "--xy-offset-mm",
        nargs=2,
        type=float,
        metavar=("DX", "DY"),
        help="Shift every target by this XY offset in mm (target_high = target_low + offset)",
    )
    p.add_argument(
        "--z-offset-mm",
        type=float,
        help="Shift carried z by this offset in mm (parfocal difference; needs --carry-z or --sweep-z)",
    )
    p.add_argument("--overlay", action="store_true", help="Also write annotated per-FOV PNGs for inspection")
    p.add_argument("--allow-incomplete", action="store_true", help="Analyse without a .done sentinel (unsafe)")
    p.add_argument("--list-analyzers", action="store_true", help="List analyzers and their parameters, then exit")
    p.add_argument("--dry-run", action="store_true", help="Analyse and report, but write nothing")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.list_analyzers:
        for name in analyzers.available():
            print(f"{name}:")
            for key, value in sorted(analyzers.defaults_for(name).items()):
                print(f"    {key} = {value!r}")
        return 0

    acq_dir = args.input or os.environ.get(ENV_ACQUISITION_PATH)
    if not acq_dir:
        print(
            f"error: no acquisition folder. Pass --input, or run from the Workflow Runner "
            f"which sets ${ENV_ACQUISITION_PATH}.",
            file=sys.stderr,
        )
        return 2
    if not os.path.isdir(acq_dir):
        print(f"error: not a directory: {acq_dir}", file=sys.stderr)
        return 2

    cycle = os.environ.get(ENV_CYCLE)
    if cycle:
        print(f"Workflow cycle {cycle}")
    print(f"Analysing {acq_dir} with '{args.analyzer}'")

    try:
        params = _parse_params(args.param)
        result = pipeline.run_analysis(
            acq_dir,
            args.analyzer,
            params=params,
            channel=args.channel,
            time_point=args.timepoint,
            z_level=args.z_level,
            carry_z=args.carry_z,
            sweep_z=args.sweep_z,
            interpolate_z=not args.no_interpolate_z,
            merge_tolerance_um=args.merge_um,
            min_score=args.min_score,
            max_targets=args.max_targets,
            require_complete=not args.allow_incomplete,
        )
    except Exception as e:
        print(f"error: analysis failed: {e}", file=sys.stderr)
        if args.verbose:
            raise
        return 1

    print(f"  {len(result.fovs)} FOV(s) -> {len(result.targets)} target(s) " f"({result.measured_count} measured)")
    for message in result.errors:
        print(f"  warning: {message}", file=sys.stderr)
    if not result.targets:
        print("error: no targets found; nothing to write.", file=sys.stderr)
        return 1

    if args.verbose:
        for t in result.targets:
            flag = "" if t.measured else "  (inferred)"
            z = f"  z={t.z_mm:.4f}" if t.z_mm is not None else ""
            best = t.metrics.get("best_z_level")
            focus = ""
            if best is not None:
                focus = f"  best_z_level={best}"
                if t.metrics.get("z_interpolated"):
                    focus += " (interp)"
            print(f"    {t.label:<28} x={t.x_mm:9.4f}  y={t.y_mm:9.4f}{z}  score={t.score:.4g}{focus}{flag}")

    if args.overlay:
        out_base = args.out or os.path.join(acq_dir, "smart_analysis")
        overlay_dir = os.path.join(out_base, "overlays")
        written = pipeline.save_overlays(
            acq_dir, result, overlay_dir, channel=args.channel, require_complete=not args.allow_incomplete
        )
        print(f"  wrote {len(written)} overlay(s) to {overlay_dir}")

    if args.dry_run:
        print("dry run: no acquisition written.")
        return 0

    if not args.out or not args.template:
        print("error: --out and --template are required unless --dry-run is given.", file=sys.stderr)
        return 2

    try:
        summary = emit.write_targets(
            result.targets,
            args.template,
            args.out,
            prefix=args.prefix,
            xy_offset_mm=args.xy_offset_mm,
            z_offset_mm=args.z_offset_mm,
            source_acquisition=acq_dir,
        )
    except Exception as e:
        print(f"error: could not write targets: {e}", file=sys.stderr)
        if args.verbose:
            raise
        return 1

    print(f"  wrote {summary['written']} position(s) to {summary['acquisition_yaml']} ({summary['widget_type']})")
    if summary["xy_offset_mm"] is not None or summary["z_offset_mm"] is not None:
        print(f"  applied offsets xy={summary['xy_offset_mm']} mm  z={summary['z_offset_mm']} mm")
    print(f"  and {summary['coordinates_csv']}")
    if summary["dropped_out_of_limits"]:
        print(f"  warning: dropped {summary['dropped_out_of_limits']} target(s) outside the stage limits")
    if summary["inferred"]:
        print(f"  note: {summary['inferred']} target(s) were inferred rather than directly detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
