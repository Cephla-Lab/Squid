"""Reconcile imported laser AF configs against *this* build's schema.

Run from the ``software`` directory::

    python tools/reconcile_laser_af.py
    python tools/reconcile_laser_af.py --drop-reference

``LaserAFConfig`` sets ``extra = "forbid"``, so a single key this build does not
declare invalidates the whole config -- and the failure is silent: the
repository logs a warning, returns None, and laser AF is simply disabled, with
Set Reference / Measure Displacement greyed out. Configs copied from an older
bundle are the usual way to get there. This drops any key the model does not
declare, re-validates, and rewrites the file only when something changed.

``--drop-reference`` also removes the stored reference. The absolute x of the
AF spot depends on the spot detector that measured it, so a reference is only
trustworthy when the old and new bundles share their laser AF code.

Exits 0 if every config validates, 1 otherwise.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# control._def logs a wall of INFO at import time; only our own output matters.
logging.disable(logging.INFO)

# Running as `python tools/reconcile_laser_af.py` puts tools/ on sys.path, not
# the CWD, so `import control...` would fail without this.
sys.path.insert(0, os.getcwd())

import yaml  # noqa: E402

REFERENCE_KEYS = ("x_reference", "reference_image", "reference_image_shape", "reference_image_dtype")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drop-reference", action="store_true", help="remove the stored laser AF reference")
    args = parser.parse_args()

    if not Path("main_hcs.py").exists():
        print("run this from the bundle's software\\ directory")
        return 1

    from control.models.laser_af_config import LaserAFConfig

    allowed = set(LaserAFConfig.model_fields)
    paths = sorted(Path("user_profiles").glob("*/laser_af_configs/*.yaml"))
    if not paths:
        print("  no laser AF configs found -- nothing to reconcile")
        return 0

    failures = 0
    for path in paths:
        with open(path) as handle:
            cfg = yaml.safe_load(handle) or {}

        unknown = sorted(k for k in cfg if k not in allowed)
        for key in unknown:
            del cfg[key]

        dropped_ref = []
        if args.drop_reference:
            dropped_ref = [k for k in REFERENCE_KEYS if k in cfg]
            for key in dropped_ref:
                del cfg[key]
            if dropped_ref:
                cfg["has_reference"] = False

        try:
            parsed = LaserAFConfig(**cfg)
        except Exception as exc:  # pydantic ValidationError, or anything else
            print(f"  {path}: INVALID after reconciling -- {exc}")
            failures += 1
            continue

        if unknown or dropped_ref:
            with open(path, "w") as handle:
                yaml.safe_dump(cfg, handle, default_flow_style=False, sort_keys=False)

        notes = []
        if unknown:
            notes.append(f"dropped {len(unknown)} key(s) this build does not know: {', '.join(unknown)}")
        if dropped_ref:
            notes.append("dropped the reference")
        notes.append("has reference" if parsed.has_reference else "needs Set Reference on the scope")
        print(f"  {path}: {'; '.join(notes)}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
