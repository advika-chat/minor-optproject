"""In-sample illustration of the robust portfolio methods on the first rolling window.

Run from the repo root:  python -m scripts.run_robust_demo [--log]
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

from src.markowitz import annualise  # noqa: E402
from src.robust import METHODS, ledoit_wolf_shrinkage, portfolio_cvar  # noqa: E402
from src.utils import get_logger, load_config, set_seed  # noqa: E402

log = get_logger("robust_demo")


def main(write_log=False):
    cfg = load_config()
    set_seed(cfg["seed"])
    td, alpha = cfg["trading_days"], cfg["risk"]["cvar_alpha"]
    thr, cap = cfg["robust"]["nonzero_threshold"], cfg["portfolio"]["weight_cap"]
    win_len = cfg["backtest"]["rolling_window_days"]

    rets = pd.read_parquet(Path(cfg["data"]["processed_dir"]) / "returns_simple.parquet")
    window = rets.iloc[:win_len]
    n = window.shape[1]
    _, cov = annualise(window, td)
    log.info("first window: %s to %s, %d days, %d assets", window.index[0].date(),
             window.index[-1].date(), len(window), n)

    rows, weights, failures = [], {}, {}
    for name, fn in METHODS.items():
        try:
            w = fn(window, cfg)
        except ValueError as e:
            failures[name] = str(e)
            log.warning("%s failed: %s", name, e)
            rows.append({"method": name, "status": f"failed: {e}"})
            continue
        weights[name] = w
        rows.append({
            "method": name, "status": "ok",
            "n_nonzero": int((w > thr).sum()),
            "max_weight": float(w.max()),
            "effective_n": float(1.0 / np.sum(w ** 2)),
            "insample_vol_ann": float(np.sqrt(w @ cov @ w)),
            "insample_cvar_daily": portfolio_cvar(w, window, alpha),
        })
    table = pd.DataFrame(rows)
    tables, figs = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    table.to_csv(tables / "robust_insample_summary.csv", index=False)
    pd.DataFrame(weights, index=window.columns).to_csv(tables / "robust_insample_weights.csv")

    ok = table[table["status"] == "ok"].sort_values("effective_n")
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(ok["method"], ok["effective_n"], color="tab:blue")
    ax.axvline(1 / cap, color="firebrick", ls="--", label=f"minimum possible under cap ({1 / cap:.0f})")
    ax.axvline(n, color="grey", ls=":", label=f"1/N ({n})")
    ax.set_xlabel("Effective number of positions, 1 / sum(w^2)")
    ax.set_title(f"Weight concentration by method, first window "
                 f"({window.index[0].date()} to {window.index[-1].date()})")
    ax.text(0.5, -0.2, "IN-SAMPLE ILLUSTRATION - weights fitted and evaluated on the same window",
            transform=ax.transAxes, ha="center", fontsize=9, color="firebrick")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(figs / "robust_concentration_insample.png", dpi=150)
    plt.close(fig)

    shrink = ledoit_wolf_shrinkage(window)
    lines = [
        f"- IN-SAMPLE illustration, first rolling window {window.index[0].date()} to "
        f"{window.index[-1].date()} ({len(window)} days, {n} assets, cap {cap}, rf {cfg['risk_free_rate']} assumed).",
        f"- Parameters (config.yaml robust section): n_draws {cfg['robust']['n_draws']}, box_z {cfg['robust']['box_z']}, "
        f"ellipsoid confidence {cfg['robust']['ellipsoid_confidence']}, risk_aversion {cfg['robust']['risk_aversion']}; "
        f"CVaR alpha {alpha}.",
        f"- Ledoit-Wolf shrinkage coefficient: {shrink:.4f}.",
    ]
    for _, r in table.iterrows():
        if r["status"] == "ok":
            lines.append(f"- {r['method']}: {int(r['n_nonzero'])} positions, max weight {r['max_weight']:.3f}, "
                         f"effective N {r['effective_n']:.2f}, vol {r['insample_vol_ann']:.4f}, "
                         f"daily CVaR {r['insample_cvar_daily']:.4f}")
        else:
            lines.append(f"- {r['method']}: {r['status']}")
    print("\n".join(lines))
    if write_log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Robust methods demo (scripts/run_robust_demo.py)\n"
                    + "\n".join(lines) + "\n")
        log.info("appended to %s", cfg["paths"]["results_log"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    main(ap.parse_args().log)
