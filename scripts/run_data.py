"""Download, clean and summarise NIFTY 50 price data.

Run from the repo root:
  python -m scripts.run_data --rebuild-tickers   # rebuild ticker CSV from the NSE file, print diff
  python -m scripts.run_data                     # pipeline; only new/failed symbols are downloaded
  python -m scripts.run_data --refresh           # full re-download
  python -m scripts.run_data --inspect TRENT.NS 2026-01-01   # raw vs adjusted prices around a date
  python -m scripts.run_data --log               # also append the run summary to docs/results_log.md
"""
import argparse
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from src.data import (align_and_clean, apply_exclusions, compute_returns, download_snapshot, flag_jumps,  # noqa: E402
                      inspect_jump, load_latest_snapshot, load_tickers,
                      nse_constituents_to_table, summary_stats, ticker_diff,
                      update_snapshot, write_ticker_csv)
from src.utils import get_logger, load_config, set_seed  # noqa: E402

log = get_logger("run_data")

COVID_NOTE = ("Note: the rolling-volatility peak appears after the shaded COVID window "
              "because of the rolling-window smoothing.")


def plot_overview(norm_prices, norm_bench, roll_vol, shaded, path, vol_window):
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 8.6), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1]})
    a1.plot(norm_prices.index, norm_prices.values, color="grey", alpha=0.25, lw=0.7)
    a1.plot(norm_bench.index, norm_bench.values, color="black", lw=2,
            label="NIFTY 50 price index (excludes dividends)")
    a1.set_yscale("log")
    a1.set_ylabel("Normalised price (start = 100, log scale)")
    a1.set_title("NIFTY 50 constituents: normalised adjusted prices")
    a2.plot(roll_vol.index, roll_vol.values, color="tab:red", lw=1.5)
    a2.set_ylabel(f"{vol_window}-day rolling index vol (annualised)")
    a2.set_xlabel("Date")
    for (label, (s, e)), c in zip(shaded.items(), ["tab:orange", "tab:blue", "tab:green"]):
        for ax in (a1, a2):
            ax.axvspan(pd.Timestamp(s), pd.Timestamp(e), color=c, alpha=0.2,
                       label=label if ax is a1 else None)
    a1.legend(loc="upper left", fontsize=8)
    a1.grid(alpha=0.3)
    a2.grid(alpha=0.3)
    fig.text(0.5, 0.005, COVID_NOTE, ha="center", va="bottom", fontsize=8, style="italic")
    fig.tight_layout(rect=(0, 0.025, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def rebuild_tickers(cfg):
    d = cfg["data"]
    src = Path(d["nse_constituents_file"])
    if not src.exists():
        raise SystemExit(f"Official NSE file not found. Place it at: {src}")
    table = nse_constituents_to_table(src)
    out = Path(d["tickers_file"])
    old = load_tickers(out) if out.exists() else []
    diff = ticker_diff(old, table["ticker"])
    stamp = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d")
    write_ticker_csv(table, out, [
        f"Source: official NSE NIFTY 50 constituents file ({src.name}), unmodified, "
        f"{len(table)} rows; '.NS' appended to each Symbol.",
        f"Retrieval date: {stamp} (file modification date of the copy in {src.parent}).",
        "Rebuild with: python -m scripts.run_data --rebuild-tickers",
    ])
    tables = Path(cfg["paths"]["tables_dir"])
    tables.mkdir(parents=True, exist_ok=True)
    diff.to_csv(tables / "data_ticker_diff.csv", index=False)
    for status in ["added", "removed", "both"]:
        names = diff.loc[diff["status"] == status, "ticker"].tolist()
        print(f"{status} ({len(names)}): {', '.join(names) if names else '-'}")
    return table


def inspect(cfg, pairs):
    proc = Path(cfg["data"]["processed_dir"])
    rows = []
    for sym, date in pairs:
        win, actions = inspect_jump(sym, date)
        print(f"\n=== {sym} around {date} ===")
        print(win.round(4).to_string())
        print("dividend/split entries in +/-20d window:",
              "none" if actions.empty else "\n" + actions.to_string())
        if (proc / "returns_simple.parquet").exists():
            rs = pd.read_parquet(proc / "returns_simple.parquet")
            b = pd.read_parquet(proc / "benchmark.parquet")
            dt = pd.Timestamp(date)
            if dt in rs.index:
                print(f"same-day cross-section: median stock return {rs.loc[dt].median():.4f}, "
                      f"min {rs.loc[dt].min():.4f} ({rs.loc[dt].idxmin()}), "
                      f"max {rs.loc[dt].max():.4f}; index return {b.loc[dt, 'simple']:.4f}")
        w = win.copy()
        w.insert(0, "date", w.index)
        w.insert(0, "ticker", sym)
        w["actions_in_window"] = "none" if actions.empty else actions.to_json()
        rows.append(w)
    pd.concat(rows).to_csv(Path(cfg["paths"]["tables_dir"]) / "data_jump_inspection.csv",
                           index=False)


def main(args):
    cfg = load_config()
    set_seed(cfg["seed"])
    d, td = cfg["data"], cfg["trading_days"]
    bench_sym = d["index_ticker"]

    if args.rebuild_tickers:
        rebuild_tickers(cfg)
    if args.inspect:
        inspect(cfg, args.inspect)
        return

    tickers = load_tickers(d["tickers_file"])
    symbols = tickers + [bench_sym]
    log.info("%d tickers from %s", len(tickers), d["tickers_file"])

    snap = None if args.refresh else load_latest_snapshot(d["raw_dir"])
    if snap is None:
        prices, new_failed, path = download_snapshot(tickers, bench_sym, d["start_date"],
                                                     d["end_date"], d["raw_dir"])
    else:
        prices, new_failed, path = update_snapshot(symbols, snap[0], snap[2], d["start_date"],
                                                   d["end_date"], d["raw_dir"])
    log.info("snapshot: %s", path.name)

    failed = {s: new_failed.get(s, "not in snapshot") for s in symbols if s not in prices.columns}
    for s, why in failed.items():
        log.warning("DOWNLOAD FAILED: %s (%s)", s, why)
    if bench_sym in failed:
        raise RuntimeError(f"benchmark {bench_sym} failed to download: {failed[bench_sym]}")
    prices = prices[[s for s in symbols if s in prices.columns]]

    excl_cfg = d.get("excluded_tickers", [])
    for e in excl_cfg:
        if e["ticker"] not in tickers:
            log.warning("excluded_tickers entry %s is not in the ticker list", e["ticker"])
    prices, excluded = apply_exclusions(prices, excl_cfg)
    for t_, why in excluded.items():
        log.warning("EXCLUDED %s: %s", t_, why)

    stocks, bench, dropped, info = align_and_clean(
        prices, bench_sym, d["cleaning"]["ffill_limit_days"])
    for t, why in dropped.items():
        log.warning("DROPPED %s: %s", t, why)
    all_dropped = {**excluded, **{t: f"download failed: {w}" for t, w in failed.items()}, **dropped}

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
    pd.DataFrame({"ticker": list(all_dropped), "reason": list(all_dropped.values())}).to_csv(
        tables / "data_dropped_tickers.csv", index=False)

    w = d["plots"]["rolling_vol_window"]
    roll_vol = simple[bench_sym].rolling(w).std().dropna() * (td ** 0.5)
    plot_overview(stocks / stocks.iloc[0] * 100, bench / bench.iloc[0] * 100, roll_vol,
                  d["plots"]["shaded_periods"], figs / "data_prices_and_vol.png", w)

    b = stats.loc[bench_sym]
    st = stats.drop(bench_sym)
    summary = [
        f"- Source of universe: {d['tickers_file']} (built from the official NSE file). "
        f"Snapshot: {path.name}. Window {d['start_date']} to {d['end_date']} (config); "
        f"adjusted closes (auto_adjust=True).",
        f"- Tickers requested: {len(tickers)}; downloaded: {len(tickers) - len([t for t in failed if t != bench_sym])}; "
        f"failed: {failed if failed else 'none'}.",
        f"- Excluded by config (data-quality, prices not edited): {len(excluded)}: "
        + (", ".join(excluded) if excluded else "none"),
        f"- Final universe size: {stocks.shape[1]} stocks (+ benchmark). Dropped/excluded {len(all_dropped)}: "
        + ("; ".join(f"{t} ({r})" for t, r in all_dropped.items()) if all_dropped else "none"),
        f"- Calendar: {info['calendar_days']} benchmark trading days "
        f"({stocks.index[0].date()} to {stocks.index[-1].date()}); {len(simple)} return rows. "
        f"Forward-filled cells (gap <= {d['cleaning']['ffill_limit_days']}d): {info['ffilled_cells']}. "
        f"Stock dates not on benchmark calendar (discarded): {info['stock_dates_not_on_benchmark_calendar']}.",
        f"- Daily moves with |return| > {d['cleaning']['jump_threshold']:.0%} flagged (not removed): "
        f"{len(jumps)} (outputs/tables/data_flagged_jumps.csv).",
        f"- NIFTY 50 price index (excludes dividends), daily simple returns: ann. mean {b['ann_mean']:.4f}, "
        f"ann. vol {b['ann_vol']:.4f}, skew {b['skew']:.3f}, excess kurtosis {b['excess_kurtosis']:.2f}.",
        f"- Single stocks: median ann. vol {st['ann_vol'].median():.4f} vs index {b['ann_vol']:.4f} "
        f"(ratio {st['ann_vol'].median() / b['ann_vol']:.2f}); median excess kurtosis "
        f"{st['excess_kurtosis'].median():.2f}.",
    ]
    print("\n".join(summary))
    print(jumps.to_string(index=False))
    if args.log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Data pipeline re-run on official NSE universe "
                    "(scripts/run_data.py)\n" + "\n".join(summary) + "\n")
        log.info("appended to %s", cfg["paths"]["results_log"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--rebuild-tickers", action="store_true")
    ap.add_argument("--inspect", nargs=2, action="append", metavar=("TICKER", "DATE"))
    ap.add_argument("--log", action="store_true")
    main(ap.parse_args())
