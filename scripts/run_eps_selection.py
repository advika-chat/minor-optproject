"""Per-regime selection of the Wasserstein radius on the real data (extended grid, two rules).

Run from the repo root:
  python -m scripts.run_eps_selection --find-grid [--log]   # where does the DR solution reach 1/N? (weights only)
  python -m scripts.run_eps_selection [--log]               # selection on the extended grid, rules best and one_se

Requires data/processed/{returns_simple,benchmark}.parquet (scripts/run_data.py) and, for the HMM
labels, outputs/tables/regimes_labels.csv (scripts/run_regimes.py). The labels are causal and
deterministic; the volatility-threshold labels in the file are recomputed and compared as a check,
the HMM labels are taken from the file (recomputing them takes about 13 minutes). If the file is
missing, all labels are recomputed.

Outputs of the first (original-grid) run are kept; this script writes files with the suffix set in
config (eps_selection_ext.output_suffix). The script exits with status 1 if any consistency check fails.
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.eps_selection import (POOLED, _argbest, compute_blocks, cv_curves, eps_for_date_info,  # noqa: E402
                               eps_schedule, extension_grid, scan_plateau)
from src.regimes import UNLABELLED, regime_series  # noqa: E402
from src.utils import get_logger, load_config, set_seed  # noqa: E402
from src.wdro import eps_grid  # noqa: E402

log = get_logger("eps_selection")
COLOURS = {"calm": "tab:green", "volatile": "goldenrod", "crisis": "tab:red", POOLED: "black"}


def get_labels(methods, bench, cfg, labels_path):
    out = {}
    if labels_path.exists():
        saved = pd.read_csv(labels_path, index_col=0, parse_dates=True).reindex(bench.index)
        fresh = regime_series(bench, "vol_threshold", cfg)["regime"]
        if not (saved["vol_threshold"].fillna("?") == fresh).all():
            raise RuntimeError("saved vol_threshold labels differ from a fresh computation; re-run scripts/run_regimes.py")
        log.info("saved regime labels verified against a fresh vol_threshold run")
        for m in methods:
            out[m] = fresh if m == "vol_threshold" else saved[m].fillna(UNLABELLED)
    else:
        for m in methods:
            out[m] = regime_series(bench, m, cfg)["regime"]
    return out


def find_grid(cfg, rets, write_log):
    sec = cfg["eps_selection_ext"]
    tol = sec["plateau_tolerance"]
    t0 = time.time()
    scan = scan_plateau(rets, cfg, tol)
    tables = Path(cfg["paths"]["tables_dir"])
    scan.to_csv(tables / "eps_grid_scan.csv", index=False)
    old = eps_grid(cfg)
    first = scan.iloc[-1]
    n_new = int((scan["eps"] > old[-1] * (1 + 1e-9)).sum())
    lines = [
        f"- Plateau scan (weights only, no performance used): kind {cfg['eps_selection']['kind']}, ground norm {cfg['wdro']['ground_norm']}, "
        f"{int(first['windows'])} training windows (one per validation block). Condition: effective number of positions 1/sum(w^2) >= "
        f"{1 - tol:.2f} N in every window. Old grid maximum {old[-1]:.3g}.",
    ] + [f"    eps {r.eps:.4g}: min ratio {r.min_ratio:.4f}, median ratio {r.median_ratio:.4f}, windows below {r.windows_below}/{r.windows}"
         for r in scan.itertuples()] + [
        f"- First candidate meeting the condition in every window: {first['eps']:.6g}; extension values above the old grid maximum: {n_new} "
        f"(scan took {time.time() - t0:.0f} s)."]
    print("\n".join(lines))
    if write_log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Eps grid extension scan (scripts/run_eps_selection.py --find-grid)\n"
                    + "\n".join(lines) + "\n")


def plateau_info(cur_reg):
    """Classify the all-blocks curve of one regime. Returns (best_idx, plateau_start_idx, kind)."""
    sh, cv = cur_reg["sharpe_net"].to_numpy(), cur_reg["cvar_daily"].to_numpy()
    best = _argbest(sh, cv, 1e-6)
    last = len(sh) - 1
    start = last
    while start > 0 and abs(sh[start - 1] - sh[last]) < 1e-9 and abs(cv[start - 1] - cv[last]) < 1e-9:
        start -= 1
    if best >= start and start < last:
        kind = "plateau (1/N: same portfolio for every larger eps)"
    elif best >= start:
        kind = "edge (best at the largest grid value, no plateau reached)"
    else:
        kind = "interior (best below the plateau, lower value at larger eps)"
    return best, start, kind


def identical_counts(sched):
    """Refits where the choice is identical across regimes: (a) among refits with >= 2 regime-sourced
    choices; (b) among all refits, over all non-pooled regime rows including pooled fallbacks."""
    reg = sched[sched["regime"] != POOLED]
    own = reg[reg["source"] == "regime"].groupby("refit_position")["eps"].agg(["nunique", "size"])
    multi = own[own["size"] >= 2]
    allrows = reg.groupby("refit_position")["eps"].nunique()
    return {"refits": int(reg["refit_position"].nunique()), "multi": int(len(multi)),
            "multi_identical": int((multi["nunique"] == 1).sum()),
            "all_rows_identical": int((allrows == 1).sum())}


def main(args):
    cfg = load_config()
    set_seed(cfg["seed"])
    es, sec = cfg["eps_selection"], cfg["eps_selection_ext"]
    proc = Path(cfg["data"]["processed_dir"])
    rets = pd.read_parquet(proc / "returns_simple.parquet")
    bench = pd.read_parquet(proc / "benchmark.parquet")["simple"]
    if not rets.index.equals(bench.index):
        raise RuntimeError("returns and benchmark are not aligned")
    if args.find_grid:
        find_grid(cfg, rets, args.log)
        return 0

    grid, old_grid, suffix, rules = extension_grid(sec), eps_grid(cfg), sec["output_suffix"], sec["rules"]
    failures = []
    # the new grid must contain every old grid value
    old_idx = [int(np.argmin(np.abs(grid - g))) for g in old_grid]
    if not np.allclose(grid[old_idx], old_grid, rtol=1e-9):
        print("FAILED: extended grid is not a superset of the original grid", file=sys.stderr)
        return 1
    tables, figs = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    n_blocks = len(range(cfg["backtest"]["rolling_window_days"], len(rets) - es["block_length"] + 1, es["block_length"]))
    log.info("computing %d validation blocks x %d eps (old grid has %d) ...", n_blocks, len(grid), len(old_grid))
    net = compute_blocks(rets, cfg, grid)
    log.info("blocks done in %.0f s, shape %s", time.time() - t0, net.shape)
    labels = get_labels(es["methods"], bench, cfg, tables / "regimes_labels.csv")

    lines = [
        f"- Extended-grid run (the original-grid run above is kept, not overwritten). Walk-forward blocks of {es['block_length']} days after a "
        f"{cfg['backtest']['rolling_window_days']}-day window; kind {es['kind']}; ground norm {cfg['wdro']['ground_norm']}; cost "
        f"{cfg['costs']['transaction_cost_bps']} bps; criterion net annualised Sharpe (rf {cfg['risk_free_rate']} assumed); min {es['min_blocks']} "
        f"blocks per regime; refit every {es['refit_every']} days; {net.shape[1]} blocks in total.",
        f"- Grid: {len(grid)} log-spaced values {grid[0]:.3g} to {grid[-1]:.3g}; the original {len(old_grid)} values are included "
        f"({len(grid) - len(old_grid)} added above {old_grid[-1]:.3g}). Rules: best = highest Sharpe (ties: lower CVaR, then smaller eps); "
        f"one_se = smallest eps with Sharpe >= best Sharpe minus the best eps's leave-one-block-out jackknife SE (treats blocks as "
        f"exchangeable; autocorrelation between blocks is ignored, so the SE is probably too small).",
        "- No significance test was run, and nothing here shows that regime-dependent eps beats a fixed eps.",
    ]
    scheds, curves, summary_rows = {}, {}, []
    for m in es["methods"]:
        parts = [eps_schedule(rets, bench, labels[m], cfg, net=net, grid=grid, rule=r) for r in rules]
        scheds[m] = parts
        pd.concat(parts, ignore_index=True).to_csv(tables / f"eps_selection_table_{m}{suffix}.csv", index=False)
        cur = cv_curves(rets, labels[m], cfg, net, grid)
        curves[m] = cur
        cur.to_csv(tables / f"eps_cv_curves_{m}{suffix}.csv", index=False)
        lines.append(f"- {m}:")
        for r, sched in zip(rules, parts):
            final = sched[sched["refit_position"] == sched["refit_position"].max()]
            ic = identical_counts(sched)
            src = sched[sched["regime"] != POOLED]["source"].value_counts().to_dict()
            for _, row in final.iterrows():
                summary_rows.append({"method": m, "rule": r, "regime": row["regime"], "final_eps": row["eps"], "best_eps": row["best_eps"],
                                     "source": row["source"], "n_blocks": row["n_blocks"], "sharpe_net": row["sharpe_net"],
                                     "se_best": row["se_best"]})
            lines.append(f"    rule {r}: final refit eps by regime: " + ", ".join(
                f"{x['regime']} {x['eps']:.3g} ({x['source']}, {int(x['n_blocks'])} blocks)" for _, x in final.iterrows()))
            lines.append(f"      sources (regime rows) {src}; refits {ic['refits']}; refits with >=2 regime-sourced choices {ic['multi']}, "
                         f"of which identical across those regimes {ic['multi_identical']}; refits where ALL regime rows (incl. pooled fallbacks) "
                         f"share one eps {ic['all_rows_identical']}")
        for reg, d in cur.groupby("regime"):
            best, start, kind = plateau_info(d.reset_index(drop=True))
            e = d["eps"].to_numpy()
            lines.append(f"    all-blocks curve, {reg} ({int(d['n_blocks'].iloc[0])} blocks): best eps {e[best]:.3g}, plateau starts at {e[start]:.3g} "
                         f"-> {kind}; Sharpe at best {d['sharpe_net'].iloc[best]:.3f} (jackknife SE {d['se_sharpe'].iloc[best]:.3f})")
            summary_rows.append({"method": m, "rule": "all_blocks_curve", "regime": reg, "final_eps": e[best], "best_eps": e[best],
                                 "source": kind, "n_blocks": d["n_blocks"].iloc[0], "sharpe_net": d["sharpe_net"].iloc[best],
                                 "se_best": d["se_sharpe"].iloc[best]})
    pd.DataFrame(summary_rows).to_csv(tables / f"eps_selection_summary{suffix}.csv", index=False)

    # check 1: the superset run reproduces the original-grid selections (best rule)
    for m in es["methods"]:
        old_path = tables / f"eps_selection_table_{m}.csv"
        if not old_path.exists():
            lines.append(f"- Reproduction check {m}: original table not found, skipped")
            continue
        old = pd.read_csv(old_path, parse_dates=["refit_date"])
        re = eps_schedule(rets, bench, labels[m], cfg, net=net[old_idx], grid=old_grid, rule="best")
        mrg = old.merge(re, on=["refit_position", "regime"], suffixes=("_old", "_new"))
        tol = es["tie_tolerance"]
        eps_same = np.isclose(mrg["eps_old"], mrg["eps_new"], rtol=1e-9)
        sharpe_close = np.isclose(mrg["sharpe_net_old"], mrg["sharpe_net_new"], rtol=1e-7, equal_nan=True)
        # a different eps is accepted only as a float-level tie: criterion within the tie tolerance and the smaller eps kept
        tie_only = (~eps_same) & ((mrg["sharpe_net_old"] - mrg["sharpe_net_new"]).abs() <= tol) & (mrg["eps_new"] < mrg["eps_old"])
        same = bool((len(mrg) == len(old) == len(re)) and (eps_same & sharpe_close | tie_only).all())
        lines.append(f"- Reproduction check {m}: restricting the extended run to the original grid reproduces the saved original selections "
                     f"({len(mrg)} rows): {same}; rows where the eps differs only through a float-level tie (|dSharpe| <= {tol}, "
                     f"the new rule keeps the smaller eps): {int(tie_only.sum())}"
                     + (f", max |dSharpe| {float((mrg['sharpe_net_old'] - mrg['sharpe_net_new']).abs()[tie_only].max()):.1e}" if tie_only.any() else ""))
        if not same:
            failures.append(f"original-grid selections for {m} are not reproduced")

    # check 2: eps_for_date recomputed from data strictly before the date equals the schedule (every method and rule)
    for m in es["methods"]:
        for r, sched in zip(rules, scheds[m]):
            own = sched.loc[(sched["source"] == "regime") & (sched["regime"] != POOLED), "refit_position"]
            if own.empty:
                lines.append(f"- eps_for_date check {m}/{r}: no regime-sourced refit, skipped")
                continue
            d_check = rets.index[int(own.min()) + 5]
            info = eps_for_date_info(d_check, m, cfg, returns=rets, benchmark=bench, grid=grid, rule=r)
            p = int(rets.index.searchsorted(d_check))
            now = labels[m].iloc[p - 1]
            row = sched[(sched["refit_position"] == info["refit_position"]) &
                        (sched["regime"] == (now if now != UNLABELLED else POOLED))].iloc[0]
            ok = bool(info["eps"] == row["eps"] and np.isclose(info["sharpe_net"], row["sharpe_net"], equal_nan=True))
            lines.append(f"- eps_for_date check {m}/{r}: date {d_check.date()}, regime {info['regime']}, eps {info['eps']:.3g} "
                         f"({info['source']}); matches the full-sample schedule: {ok}")
            if not ok:
                failures.append(f"eps_for_date disagrees with the schedule for {m}/{r}")

    # figures
    meths = es["methods"]
    fig, axes = plt.subplots(2 * len(meths), len(rules), figsize=(7 * len(rules), 3.3 * len(meths)), sharex=True,
                             gridspec_kw={"height_ratios": [3, 0.7] * len(meths)}, squeeze=False)
    for i, m in enumerate(meths):
        for j, (r, s) in enumerate(zip(rules, scheds[m])):
            ax, strip = axes[2 * i, j], axes[2 * i + 1, j]
            for reg in [POOLED] + sorted(set(s["regime"]) - {POOLED}):
                d = s[s["regime"] == reg]
                ax.step(d["refit_date"], d["eps"], where="post", color=COLOURS.get(reg, "grey"),
                        ls="--" if reg == POOLED else "-", label=reg, alpha=0.9)
            ax.set_yscale("log")
            ax.set_ylabel(f"{m}\nselected eps")
            ax.set_title(f"rule: {r}", fontsize=9)
            ax.legend(fontsize=7, ncol=4, loc="upper left")
            ax.grid(alpha=0.3)
            for reg, colour in COLOURS.items():
                strip.fill_between(labels[m].index, 0, 1, where=(labels[m] == reg).to_numpy(), color=colour, step="mid", linewidth=0)
            strip.set_yticks([])
    fig.suptitle("Selected Wasserstein radius per regime at each refit, walk-forward, extended grid (strip: regime label of the day)", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs / f"eps_selection_over_time{suffix}.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(2, len(meths), figsize=(5 * len(meths), 7), sharex=True, squeeze=False)
    for j, m in enumerate(meths):
        cur = curves[m]
        for reg, d in cur.groupby("regime"):
            c = COLOURS.get(reg, "grey")
            axes[0, j].plot(d["eps"], d["sharpe_net"], marker="o", ms=3, color=c, ls="--" if reg == POOLED else "-",
                            label=f"{reg} ({int(d['n_blocks'].iloc[0])} blocks)")
            axes[0, j].fill_between(d["eps"], d["sharpe_net"] - d["se_sharpe"], d["sharpe_net"] + d["se_sharpe"], color=c, alpha=0.12)
            axes[1, j].plot(d["eps"], d["cvar_daily"], marker="o", ms=3, color=c, ls="--" if reg == POOLED else "-")
        axes[0, j].set_title(m)
        axes[0, j].set_ylabel("net Sharpe (band: +/- jackknife SE)")
        axes[1, j].set_ylabel("daily CVaR")
        axes[1, j].set_xscale("log")
        axes[1, j].set_xlabel("eps (daily-return units)")
        axes[0, j].legend(fontsize=7)
        for k in range(2):
            axes[k, j].grid(alpha=0.3)
    fig.suptitle("Walk-forward validation curves, all finished blocks, extended grid (out-of-sample block returns, not a backtest of the final rule)", fontsize=9)
    fig.tight_layout()
    fig.savefig(figs / f"eps_cv_curves{suffix}.png", dpi=150)
    plt.close(fig)

    if failures:
        lines.append("- CONSISTENCY CHECKS FAILED: " + "; ".join(failures))
    print("\n".join(lines))
    if args.log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Per-regime eps selection, extended grid and one-SE rule (scripts/run_eps_selection.py)\n"
                    + "\n".join(lines) + "\n")
        log.info("appended to %s", cfg["paths"]["results_log"])
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--find-grid", action="store_true")
    sys.exit(main(ap.parse_args()))
