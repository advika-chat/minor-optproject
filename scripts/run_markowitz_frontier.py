"""In-sample Markowitz frontier illustration.

Run from the repo root:  python -m scripts.run_markowitz_frontier
"""
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import clean_prices, download_prices, load_tickers, to_returns
from src.markowitz import annualise, efficient_frontier, max_sharpe, min_variance, portfolio_stats
from src.plotting import plot_frontier
from src.utils import get_logger, load_config, set_seed

log = get_logger("markowitz_frontier")


def main():
    cfg = load_config()
    set_seed(cfg["seed"])
    d, cap, rf = cfg["data"], cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"]
    td = cfg["trading_days"]

    tickers = load_tickers(d["tickers_file"])
    prices = download_prices(tickers + [d["index_ticker"]], d["start_date"],
                             d["end_date"], d["raw_dir"])
    missing = [t for t in tickers + [d["index_ticker"]] if t not in prices.columns]
    prices, dropped = clean_prices(prices, d["min_history_fraction"])
    if d["index_ticker"] in dropped:
        raise RuntimeError("index series has insufficient data")
    log.info("tickers not returned by download: %s", missing)
    log.info("tickers dropped for short history: %s", dropped)

    rets = to_returns(prices)
    idx = rets.pop(d["index_ticker"])
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
    i_ret, i_vol = idx.mean() * td, idx.std() * np.sqrt(td)
    stats["NIFTY 50 index"] = {"return": i_ret, "vol": i_vol, "sharpe": (i_ret - rf) / i_vol}

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
                  f"{rets.index[-1].date()}, cap {cap:.0%}, rf {rf:.1%} assumed)")

    lines = [
        "",
        "## Markowitz in-sample frontier (scripts/run_markowitz_frontier.py)",
        f"- IN-SAMPLE only. Sample {rets.index[0].date()} to {rets.index[-1].date()}, "
        f"{len(rets)} daily obs, {n} assets, annualised x{td}.",
        f"- Weight cap {cap}, rf {rf} (ASSUMPTION, unconfirmed). Survivorship bias: current constituents only.",
        f"- Tickers dropped (short history): {dropped or 'none'}; not returned by download: {missing or 'none'}.",
    ]
    for k, v in stats.items():
        lines.append(f"- {k}: return {v['return']:.4f}, vol {v['vol']:.4f}, Sharpe {v['sharpe']:.3f}")
    lines.append("- Max-Sharpe nonzero weights: " +
                 ", ".join(f"{t} {w:.3f}" for t, w in top[top > 1e-6].items()))
    with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(table.round(4))


if __name__ == "__main__":
    main()
