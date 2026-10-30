"""In-sample Markowitz frontier illustration, from data/processed only.

Run from the repo root:  python -m scripts.run_markowitz_frontier [--log]
Requires scripts/run_data.py to have produced data/processed/*.parquet.
"""
import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.markowitz import annualise, efficient_frontier, max_sharpe, min_variance, portfolio_stats
from src.plotting import plot_frontier
from src.utils import get_logger, load_config, set_seed

log = get_logger("markowitz_frontier")

INDEX_LABEL = "NIFTY 50 price index (excludes dividends)"


def main(write_log=False):
    cfg = load_config()
    set_seed(cfg["seed"])
    d, cap, rf = cfg["data"], cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"]
    td = cfg["trading_days"]
    proc = Path(d["processed_dir"])

    rets = pd.read_parquet(proc / "returns_simple.parquet")
    bench = pd.read_parquet(proc / "benchmark.parquet")
    if not rets.index.equals(bench.index):
        raise RuntimeError("returns and benchmark are not aligned; re-run scripts/run_data.py")
    mu, cov = annualise(rets, td)
    n = len(mu)
    log.info("%d assets, %d daily obs, %s to %s", n, len(rets),
             rets.index[0].date(), rets.index[-1].date())

    w_mv = min_variance(cov, cap)
    w_ms = max_sharpe(mu, cov, rf, cap)
    w_ew = np.ones(n) / n
    fr, _ = efficient_frontier(mu, cov, cap, cfg["markowitz"]["frontier_points"])

    stats = {
        "Min-variance": portfolio_stats(w_mv, mu, cov, rf),
        "Max-Sharpe": portfolio_stats(w_ms, mu, cov, rf),
        "1/N": portfolio_stats(w_ew, mu, cov, rf),
    }
    i_ret, i_vol = bench["simple"].mean() * td, bench["simple"].std() * np.sqrt(td)
    stats[INDEX_LABEL] = {"return": i_ret, "vol": i_vol, "sharpe": (i_ret - rf) / i_vol}

    table = pd.DataFrame(stats).T
    tables_dir, figs_dir = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(tables_dir / "markowitz_insample_summary.csv")
    top = pd.Series(w_ms, index=rets.columns).sort_values(ascending=False)
    top[top > 1e-6].to_csv(tables_dir / "markowitz_max_sharpe_weights.csv", header=["weight"])

    stocks = pd.DataFrame({"return": mu, "vol": np.sqrt(np.diag(cov))}, index=rets.columns)
    points = {k: (v["vol"], v["return"]) for k, v in stats.items()}
    plot_frontier(fr, stocks, points, figs_dir / "markowitz_frontier_insample.png",
                  f"Markowitz frontier, in-sample ({rets.index[0].date()} to "
                  f"{rets.index[-1].date()}, {n} stocks, cap {cap:.0%}, rf {rf:.1%} assumed)")

    lines = [
        f"- IN-SAMPLE only, from data/processed. Sample {rets.index[0].date()} to {rets.index[-1].date()}, "
        f"{len(rets)} daily obs, {n} assets, annualised x{td}.",
        f"- Weight cap {cap}, rf {rf} (ASSUMPTION, unconfirmed). Survivorship bias: current constituents only.",
        "- Headline comparison is 1/N. The index is a PRICE index without dividends; shown for reference only.",
    ]
    for k, v in stats.items():
        lines.append(f"- {k}: return {v['return']:.4f}, vol {v['vol']:.4f}, Sharpe {v['sharpe']:.3f}")
    nz = top[top > 1e-6]
    lines.append(f"- Max-Sharpe: {len(nz)} nonzero weights, {int((nz > cap - 1e-6).sum())} at the cap: "
                 + ", ".join(f"{t} {w:.3f}" for t, w in nz.items()))
    print("\n".join(lines))
    if write_log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Markowitz in-sample frontier, final data "
                    "(scripts/run_markowitz_frontier.py)\n" + "\n".join(lines) + "\n")
        log.info("appended to %s", cfg["paths"]["results_log"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    main(ap.parse_args().log)
