#!/usr/bin/env python3
"""Coverslip height maps from af_map_flow.py output.

    python3 tools/coverslip_flow/af_map_analyze.py ~/flowtest/map/m1

Reads <base>_samples.csv and <base>_meta.json. For each map pass (before1, before2, after)
reports the bright-spot height range; before2 - before1 (map-to-map noise); after - before
(the change 30 s after the pull); and fits a paraboloid z = a + b x + c y + k (x^2 + y^2) to
each map, giving the bowl centre, the height at the centre and the depth at --radius mm.
Saves <base>_maps.png, <base>_centre.png and <base>_map_summary.json.

Heights are the bright laser AF reflection in um from the AF reference; lower = further into
the channel (the direction a pull moves it).
"""

import argparse
import csv
import json

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def fval(v):
    return float(v) if v not in ("", None) else np.nan


def fit_bowl(X, Y, z, radius):
    ok = np.isfinite(z)
    G = np.c_[np.ones_like(X), X, Y, X**2 + Y**2]
    c, *_ = np.linalg.lstsq(G[ok], z[ok], rcond=None)
    k = c[3]
    x0, y0 = -c[1] / (2 * k), -c[2] / (2 * k)
    return {
        "centre_x_mm": round(float(x0), 2),
        "centre_y_mm": round(float(y0), 2),
        "z_at_centre_um": round(float(c[0] - k * (x0**2 + y0**2)), 1),
        "k_um_per_mm2": round(float(k), 3),
        "depth_at_radius_um": round(float(k * radius**2), 1),
        "rms_residual_um": round(float((z[ok] - G[ok] @ c).std()), 2),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base", help="output base passed to af_map_flow.py --out")
    p.add_argument("--radius", type=float, default=7.0, help="mm from the bowl centre for the depth figure")
    p.add_argument("--column", default="bright_spot_um", help="bright_spot_um (default) or gui_spot_um")
    a = p.parse_args()
    base = a.base
    rows = list(csv.DictReader(open(base + "_samples.csv")))
    meta = json.load(open(base + "_meta.json"))
    ev = {n: t for t, n, _ in meta["events_s"]}

    def pass_map(name):
        return {
            int(r["pos_index"]): (fval(r["x_mm"]), fval(r["y_mm"]), fval(r[a.column]), fval(r["t_s"]))
            for r in rows
            if r["phase"] == name
        }

    passes = [p_ for p_ in ("before1", "before2", "after") if any(r["phase"] == p_ for r in rows)]
    if not passes:
        raise SystemExit("no map passes found")
    M = {p_: pass_map(p_) for p_ in passes}
    idx = sorted(M[passes[0]])
    X = np.array([M[passes[0]][i][0] for i in idx])
    Y = np.array([M[passes[0]][i][1] for i in idx])
    Z = {p_: np.array([M[p_].get(i, (0, 0, np.nan, 0))[2] for i in idx]) for p_ in passes}
    T = {p_: np.array([M[p_].get(i, (0, 0, np.nan, 0))[3] for i in idx]) for p_ in passes}
    revis = {p_: [fval(r[a.column]) for r in rows if r["phase"] == p_ + "_center"] for p_ in passes}

    summary = {}
    for p_ in passes:
        z = Z[p_]
        summary[p_] = dict(
            n=int(np.isfinite(z).sum()),
            failed=int(np.isnan(z).sum()),
            min=round(float(np.nanmin(z)), 1),
            max=round(float(np.nanmax(z)), 1),
            median=round(float(np.nanmedian(z)), 1),
            duration_s=round(float(np.nanmax(T[p_]) - np.nanmin(T[p_])), 1),
            centre_revisits=[round(v, 1) for v in revis[p_] if np.isfinite(v)],
            bowl=fit_bowl(X, Y, z, a.radius),
        )
    pairs = []
    if "before2" in Z:
        pairs.append(("before2", "before1", "noise: before2 - before1"))
    if "after" in Z:
        pairs.append(("after", "before2" if "before2" in Z else "before1", "after - before"))
    for x_, y_, lab in pairs:
        d = Z[x_] - Z[y_]
        summary[lab] = dict(
            median=round(float(np.nanmedian(d)), 2),
            sd=round(float(np.nanstd(d)), 2),
            p5=round(float(np.nanpercentile(d, 5)), 2),
            p95=round(float(np.nanpercentile(d, 95)), 2),
            min=round(float(np.nanmin(d)), 2),
            max=round(float(np.nanmax(d)), 2),
            bowl=fit_bowl(X, Y, d, a.radius),
        )
    print(json.dumps(summary, indent=1))
    json.dump(summary, open(base + "_map_summary.json", "w"), indent=1)

    ncol = len(passes) + len(pairs)
    fig, ax = plt.subplots(1, ncol, figsize=(4.6 * ncol, 4.4))
    ax = np.atleast_1d(ax)
    lo, hi = np.nanpercentile(np.concatenate([Z[p_] for p_ in passes]), [1, 99])
    for k, p_ in enumerate(passes):
        sc = ax[k].scatter(X, Y, c=Z[p_], s=60, marker="s", cmap="viridis", vmin=lo, vmax=hi)
        ax[k].set_title(f"{p_} (um)")
        fig.colorbar(sc, ax=ax[k], shrink=0.8)
    for j, (x_, y_, lab) in enumerate(pairs):
        d = Z[x_] - Z[y_]
        m = max(abs(np.nanpercentile(d, 2)), abs(np.nanpercentile(d, 98)), 1)
        sc = ax[len(passes) + j].scatter(X, Y, c=d, s=60, marker="s", cmap="RdBu_r", vmin=-m, vmax=m)
        ax[len(passes) + j].set_title(lab + " (um)")
        fig.colorbar(sc, ax=ax[len(passes) + j], shrink=0.8)
    for a_ in ax:
        a_.set_aspect("equal")
        a_.invert_yaxis()
        a_.set_xlabel("x (mm)")
    ax[0].set_ylabel("y (mm)")
    fig.tight_layout()
    fig.savefig(base + "_maps.png", dpi=80)

    cen = [(fval(r["t_s"]), fval(r[a.column])) for r in rows if r["pos_index"] == "-1"]
    fig, a1 = plt.subplots(figsize=(13, 3.8))
    a1.plot([c[0] for c in cen], [c[1] for c in cen], ".", ms=2, color="#2a78d6")
    for p_ in passes:
        if p_ + "_start" in ev:
            a1.axvspan(ev[p_ + "_start"], ev[p_ + "_end"], color="#eda100", alpha=0.15)
            a1.text(ev[p_ + "_start"], np.nanmax([c[1] for c in cen]), p_, fontsize=8, va="top")
    for k in ("pull_start", "pull_end", "pump_idle"):
        if k in ev:
            a1.axvline(ev[k], color="k", ls=":", lw=0.8)
    a1.set_xlabel("t (s)")
    a1.set_ylabel("centre (um)")
    a1.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(base + "_centre.png", dpi=80)
    print("Saved %s_maps.png, %s_centre.png, %s_map_summary.json" % (base, base, base))


if __name__ == "__main__":
    main()
