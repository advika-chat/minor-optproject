"""Download, clean and summarise NIFTY 50 price data.

Run from the repo root:  python -m scripts.run_data [--refresh]
By default the newest raw snapshot in data/raw is reused; --refresh downloads again.
"""
import argparse
from pathlib import Path

import pandas as pd

from src.data import (align_and_clean, compute_returns, download_snapshot, flag_jumps,
                      load_latest_snapshot, load_tickers, summary_stats)
from src.plotting import plot_prices_and_vol
from src.utils import get_logger, load_config, set_seed

log = get_logger("run_data")


def main(refresh=False):
    cfg = load_config()
    set_seed(cfg["seed"])
    d, td = cfg["data"], cfg["trading_days"]
    bench_sym = d["index_ticker"]
    tickers = load_tickers(d["tickers_file"])
    log.info("%d tickers in %s (UNVERIFIED against NSE)", len(tickers), d["tickers_file"])

    snap = None if refresh else load_latest_snapshot(d["raw_dir"])
    if snap is None:
        prices, failed, path = download_snapshot(tickers, bench_sym, d["start_date"],
                                                 d["end_date"], d["raw_dir"])
    else:
        prices, failed, path = snap
        log.info("reusing existing snapshot (use --refresh to re-download)")
    log.info("snapshot: %s", path.name)

    for s, why in failed.items():
        log.warning("DOWNLOAD FAILED: %s (%s)", s, why)
    if bench_sym in failed:
        raise RuntimeError(f"benchmark {bench_sym} failed to download: {failed[bench_sym]}")

    stocks, bench, dropped, info = align_and_clean(
        prices, bench_sym, d["cleaning"]["ffill_limit_days"])
    for t, why in dropped.items():
        log.warning("DROPPED %s: %s", t, why)

    prices_all = stocks.assign(**{bench_sym: bench})
    simple, logret = compute_returns(prices_all)
    jumps = flag_jumps(simple, d["cleaning"]["jump_threshold"])
    stats = summary_stats(simple, td)

    proc = Path(d["processed_dir"])
    proc.mkdir(parents=True, exist_ok=True)
    stocks.to_parquet(proc / "prices.parquet")
    simple[stocks.columns].to_parquet(proc / "returns_simple.parquet")
    logret[stocks.columns].to_parquet(proc / "returns_log.parquet")
    pd.DataFrame({"price": bench, "simple": simple[bench_sym],
                  "log": logret[bench_sym]}).dropna().to_parquet(proc / "benchmark.parquet")

    tables, figs = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    stats.to_csv(tables / "data_summary_stats.csv")
    jumps.to_csv(tables / "data_flagged_jumps.csv", index=False)
    pd.DataFrame({"ticker": list(dropped), "reason": list(dropped.values())}).to_csv(
        tables / "data_dropped_tickers.csv", index=False)

    w = d["plots"]["rolling_vol_window"]
    roll_vol = simple[bench_sym].rolling(w).std().dropna() * (td ** 0.5)
    norm = stocks / stocks.iloc[0] * 100
    plot_prices_and_vol(norm, bench / bench.iloc[0] * 100, roll_vol,
                        d["plots"]["shaded_periods"], figs / "data_prices_and_vol.png", w)

    b = stats.loc[bench_sym]
    lines = [
        "",
        "## Data pipeline (scripts/run_data.py)",
        f"- Snapshot: {path.name}. Window {d['start_date']} to {d['end_date']} (config); "
        f"adjusted closes (auto_adjust=True); benchmark {bench_sym} is a PRICE index (no dividends).",
        f"- Tickers requested: {len(tickers)} (list from memory, UNVERIFIED vs NSE). "
        f"Failed to download: {failed if failed else 'none'}.",
        f"- Kept {stocks.shape[1]} stocks; dropped {len(dropped)}: "
        + ("; ".join(f"{t} ({r})" for t, r in dropped.items()) if dropped else "none"),
        f"- Calendar: {info['calendar_days']} benchmark trading days "
        f"({stocks.index[0].date()} to {stocks.index[-1].date()}); {len(simple)} return rows. "
        f"Forward-filled cells (gap <= {d['cleaning']['ffill_limit_days']}d): {info['ffilled_cells']}. "
        f"Stock dates not on benchmark calendar (discarded): {info['stock_dates_not_on_benchmark_calendar']}.",
        f"- Daily moves with |return| > {d['cleaning']['jump_threshold']:.0%} flagged (not removed): "
        f"{len(jumps)} (see outputs/tables/data_flagged_jumps.csv).",
        f"- {bench_sym}: ann. mean {b['ann_mean']:.4f}, ann. vol {b['ann_vol']:.4f}, "
        f"skew {b['skew']:.3f}, excess kurtosis {b['excess_kurtosis']:.2f}.",
        f"- Stocks, median ann. vol {stats.drop(bench_sym)['ann_vol'].median():.4f}; "
        f"median excess kurtosis {stats.drop(bench_sym)['excess_kurtosis'].median():.2f}.",
    ]
    with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(stats.round(4).to_string())
    if len(jumps):
        print("\nFlagged jumps:\n", jumps.to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    main(ap.parse_args().refresh)
