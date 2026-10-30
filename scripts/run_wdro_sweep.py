"""In-sample Wasserstein-radius sweep on the first rolling window.

Run from the repo root:  python -m scripts.run_wdro_sweep [--log]
Requires data/processed/returns_simple.parquet (written by scripts/run_data.py).
"""
import argparse
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.utils import get_logger, load_config, set_seed  # noqa: E402
from src.wdro import eps_grid, sweep_eps, worst_case_cvar_bound  # noqa: E402

log = get_logger("wdro_sweep")
NOTE = "IN-SAMPLE ILLUSTRATION - first rolling window, weights fitted and evaluated on the same data"


def main(write_log=False):
    cfg = load_config()
    set_seed(cfg["seed"])
    win_len, thr = cfg["backtest"]["rolling_window_days"], 1e-4
    top_k = cfg["wdro"]["top_positions_plot"]
    rets = pd.read_parquet(Path(cfg["data"]["processed_dir"]) / "returns_simple.parquet")
    window = rets.iloc[:win_len]
    grid = eps_grid(cfg)
    log.info("window %s to %s, %d assets, %d eps values in [%g, %g], ground norm %s",
             window.index[0].date(), window.index[-1].date(), window.shape[1], len(grid),
             grid[0], grid[-1], cfg["wdro"]["ground_norm"])

    frames, weight_rows = [], []
    for kind in ["cvar", "meanvar"]:
        sw = sweep_eps(window, grid, kind, cfg)
        # baseline: the empirical (eps = 0) portfolio of the same kind, bound evaluated at each eps
        base = sweep_eps(window, [0.0], kind, cfg)["weights"].iloc[0]
        sw["bound_gain_vs_eps0"] = [worst_case_cvar_bound(base, window, e, cfg) - b
                                    for e, b in zip(sw["eps"], sw["worst_case_cvar_bound"])]
        sw["return_lost_vs_eps0"] = float(window.mean().to_numpy() @ base) * cfg["trading_days"] - sw["nominal_return"]
        sw["n_nonzero"] = [int((w > thr).sum()) for w in sw["weights"]]
        sw.insert(0, "kind", kind)
        for e, w in zip(sw["eps"], sw["weights"]):
            weight_rows.append(pd.Series(w, index=window.columns, name=(kind, e)))
        frames.append(sw)
    table = pd.concat(frames, ignore_index=True)

    tables, figs = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    table.drop(columns="weights").to_csv(tables / "wdro_insample_sweep.csv", index=False)
    wide = pd.DataFrame(weight_rows)
    wide.index = pd.MultiIndex.from_tuples(wide.index, names=["kind", "eps"])
    wide.to_csv(tables / "wdro_insample_weights.csv")

    # Figure 1: weights vs eps, stacked area of the top positions
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    for ax, kind in zip(axes, ["cvar", "meanvar"]):
        wk = wide.loc[kind]
        order = wk.mean().sort_values(ascending=False).index[:top_k]
        stack = [wk[c].to_numpy() for c in order] + [wk.drop(columns=order).sum(axis=1).to_numpy()]
        ax.stackplot(wk.index.to_numpy(), stack, labels=[str(c) for c in order] + ["other"])
        ax.set_xscale("log")
        ax.set_xlabel("Wasserstein radius eps (daily-return units)")
        ax.set_title({"cvar": "DR-CVaR", "meanvar": "DR mean-variance"}[kind])
    axes[0].set_ylabel("Portfolio weight")
    axes[1].legend(fontsize=7, loc="center left", bbox_to_anchor=(1.0, 0.5),
                   title=f"top {top_k} (column index)")
    fig.suptitle("Weights versus eps, " + NOTE, fontsize=9, color="firebrick")
    fig.tight_layout()
    fig.savefig(figs / "wdro_weights_vs_eps_insample.png", dpi=150)
    plt.close(fig)

    # Figure 2: nominal return lost vs worst-case CVaR bound gained
    fig, ax = plt.subplots(figsize=(8, 5.5))
    for kind, colour in [("cvar", "tab:blue"), ("meanvar", "tab:orange")]:
        s = table[table["kind"] == kind]
        ax.plot(s["return_lost_vs_eps0"] * 100, s["bound_gain_vs_eps0"] * 100, "o-",
                color=colour, label={"cvar": "DR-CVaR", "meanvar": "DR mean-variance"}[kind])
        for _, row in s.iloc[::3].iterrows():
            ax.annotate(f"{row['eps']:.0e}", (row["return_lost_vs_eps0"] * 100,
                                              row["bound_gain_vs_eps0"] * 100),
                        fontsize=7, xytext=(4, 4), textcoords="offset points")
    ax.set_xlabel("Nominal expected return lost vs eps = 0 portfolio (annualised, % points)")
    ax.set_ylabel("Worst-case daily CVaR bound gained at the same eps (% points)")
    ax.set_title("Price of robustness (labels: eps)")
    ax.text(0.5, -0.17, NOTE, transform=ax.transAxes, ha="center", fontsize=8, color="firebrick")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(figs / "wdro_tradeoff_insample.png", dpi=150)
    plt.close(fig)

    lines = [
        f"- IN-SAMPLE illustration, first rolling window {window.index[0].date()} to {window.index[-1].date()} "
        f"({len(window)} days, {window.shape[1]} assets, cap {cfg['portfolio']['weight_cap']}).",
        f"- Ground norm {cfg['wdro']['ground_norm']} (dual norm in the penalty), CVaR confidence {cfg['risk']['cvar_alpha']} "
        f"(tail probability {1 - cfg['risk']['cvar_alpha']:.2f}), mean weight {cfg['wdro']['mean_weight']}, support = R^m (no return bounds). "
        f"eps grid: {len(grid)} log-spaced values from {grid[0]:.0e} to {grid[-1]:.0e}, daily-return units.",
    ]
    for kind in ["cvar", "meanvar"]:
        s = table[table["kind"] == kind]
        lo, hi = s.iloc[0], s.iloc[-1]
        lines.append(
            f"- {kind}: eps {lo['eps']:.0e} -> {hi['eps']:.0e}: effective N {lo['effective_n']:.2f} -> {hi['effective_n']:.2f}, "
            f"positions {int(lo['n_nonzero'])} -> {int(hi['n_nonzero'])}, nominal return {lo['nominal_return']:.4f} -> {hi['nominal_return']:.4f}, "
            f"empirical daily CVaR {lo['empirical_cvar']:.4f} -> {hi['empirical_cvar']:.4f}, "
            f"worst-case CVaR bound {lo['worst_case_cvar_bound']:.4f} -> {hi['worst_case_cvar_bound']:.4f}, "
            f"bound gained vs eps=0 portfolio at eps_max {hi['bound_gain_vs_eps0']:.4f}.")
    print("\n".join(lines))
    print(table.drop(columns="weights").round(5).to_string(index=False))
    if write_log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - WDRO eps sweep (scripts/run_wdro_sweep.py)\n"
                    + "\n".join(lines) + "\n")
        log.info("appended to %s", cfg["paths"]["results_log"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    main(ap.parse_args().log)
