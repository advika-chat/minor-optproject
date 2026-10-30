"""Price download, cleaning and return construction."""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from src.utils import get_logger

log = get_logger(__name__)


# --- legacy helpers used by scripts/run_markowitz_frontier.py -----------------

def download_prices(tickers, start, end, raw_dir):
    """Download adjusted close prices via yfinance and cache to raw_dir (parquet).
    Reuses the cache if it exists. Raises if the download returns nothing."""
    path = Path(raw_dir) / "prices.parquet"
    if path.exists():
        log.info("using cached %s", path)
        return pd.read_parquet(path)
    df = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)["Close"]
    if df is None or df.empty:
        raise RuntimeError("yfinance returned no data")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def clean_prices(prices, min_history_fraction):
    """Drop columns with too much missing data, then drop rows with any NaN.
    Returns (clean prices, list of dropped tickers)."""
    frac = prices.notna().mean()
    dropped = frac[frac < min_history_fraction].index.tolist()
    kept = prices.drop(columns=dropped).dropna()
    return kept, dropped


def to_returns(prices):
    return prices.pct_change().dropna()


# --- data pipeline ------------------------------------------------------------

def load_tickers(path):
    """Read tickers from a CSV with '#' comment lines and a 'ticker' column."""
    df = pd.read_csv(path, comment="#")
    return df["ticker"].str.strip().tolist()


def download_snapshot(tickers, benchmark, start, end, raw_dir):
    """Download adjusted closes (auto_adjust=True) for tickers + benchmark and save a
    timestamped raw snapshot plus a JSON metadata file.

    Returns (prices, failed, snapshot_path). `failed` maps each requested symbol
    that returned no usable data to a reason; nothing is silently dropped."""
    symbols = list(tickers) + [benchmark]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = yf.download(symbols, start=start, end=end, auto_adjust=True,
                      progress=False, group_by="column")
    if raw is None or raw.empty:
        raise RuntimeError("yfinance returned no data at all")
    prices = raw["Close"]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame(symbols[0])
    failed = {}
    for s in symbols:
        if s not in prices.columns:
            failed[s] = "not returned by yfinance"
        elif prices[s].notna().sum() == 0:
            failed[s] = "returned only NaN"
    prices = prices.drop(columns=list(failed), errors="ignore")
    prices.index = pd.to_datetime(prices.index).tz_localize(None)

    out = Path(raw_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"prices_raw_{stamp}.parquet"
    prices.to_parquet(path)
    meta = {
        "downloaded_utc": stamp, "yfinance_version": yf.__version__,
        "start": start, "end": end, "auto_adjust": True,
        "requested": symbols, "failed": failed,
        "rows": int(len(prices)), "columns": int(prices.shape[1]),
    }
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return prices, failed, path


def load_latest_snapshot(raw_dir):
    """Return (prices, failed, path) for the newest saved snapshot, or None."""
    files = sorted(Path(raw_dir).glob("prices_raw_*.parquet"))
    if not files:
        return None
    path = files[-1]
    meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    return pd.read_parquet(path), meta["failed"], path


def align_and_clean(prices, benchmark, ffill_limit):
    """Align stocks on the benchmark's trading calendar and keep full-history stocks.

    Steps: reindex to the benchmark calendar, forward-fill gaps of at most
    `ffill_limit` consecutive days, then drop any stock that still has NaNs
    (late listing, long gap or early end of data). Returns
    (stocks, benchmark_series, dropped, info) where `dropped` maps ticker -> reason."""
    bench = prices[benchmark].dropna()
    calendar = bench.index
    stocks = prices.drop(columns=[benchmark]).reindex(calendar)
    n_off_calendar = int(prices.drop(columns=[benchmark]).index.difference(calendar).size)

    raw_nan = stocks.isna()
    filled = stocks.ffill(limit=ffill_limit)
    n_filled = int((raw_nan & filled.notna()).sum().sum())

    dropped = {}
    for t in filled.columns:
        col = filled[t]
        if col.notna().sum() == 0:
            dropped[t] = "no data in window"
        elif pd.isna(col.iloc[0]):
            first = col.first_valid_index()
            dropped[t] = f"no price at window start; first valid price {first.date()}"
        elif col.isna().any():
            bad = col[col.isna()]
            dropped[t] = (f"{len(bad)} missing days remain after forward-fill "
                          f"(limit {ffill_limit}); first at {bad.index[0].date()}")
    kept = filled.drop(columns=list(dropped))
    info = {"calendar_days": len(calendar), "ffilled_cells": n_filled,
            "stock_dates_not_on_benchmark_calendar": n_off_calendar}
    return kept, bench, dropped, info


def compute_returns(prices):
    """Simple and log daily returns (first row dropped)."""
    simple = prices.pct_change().iloc[1:]
    log_ret = np.log(prices).diff().iloc[1:]
    return simple, log_ret


def flag_jumps(simple_returns, threshold):
    """Long table (date, ticker, return) of moves with |return| > threshold.
    These are FLAGGED, not removed: they may be splits/data errors or real moves."""
    s = simple_returns.stack()
    s = s[s.abs() > threshold]
    out = s.rename("return").reset_index()
    out.columns = ["date", "ticker", "return"]
    return out


def summary_stats(simple_returns, trading_days):
    """Per-asset annualised mean and volatility, skewness and EXCESS kurtosis
    of daily simple returns."""
    return pd.DataFrame({
        "ann_mean": simple_returns.mean() * trading_days,
        "ann_vol": simple_returns.std() * np.sqrt(trading_days),
        "skew": simple_returns.skew(),
        "excess_kurtosis": simple_returns.kurt(),
    })


# --- official constituents, incremental download, jump inspection --------------

def nse_constituents_to_table(csv_path, suffix=".NS"):
    """Read NSE's ind_nifty50list.csv into a (ticker, company, industry, isin) table."""
    raw = pd.read_csv(csv_path)
    table = pd.DataFrame({
        "ticker": raw["Symbol"].str.strip() + suffix,
        "company": raw["Company Name"].str.strip(),
        "industry": raw["Industry"].str.strip(),
        "isin": raw["ISIN Code"].str.strip(),
    })
    if table["ticker"].duplicated().any():
        raise ValueError("duplicate symbols in NSE file")
    return table


def write_ticker_csv(table, path, header_lines):
    with open(path, "w", encoding="utf-8", newline="") as f:
        for line in header_lines:
            f.write(f"# {line}\n")
        table.to_csv(f, index=False, lineterminator="\n")


def ticker_diff(old, new):
    """Table (ticker, status) with status in {added, removed, both}."""
    old, new = set(old), set(new)
    rows = ([(t, "added") for t in sorted(new - old)]
            + [(t, "removed") for t in sorted(old - new)]
            + [(t, "both") for t in sorted(old & new)])
    return pd.DataFrame(rows, columns=["ticker", "status"])


def _download_close(symbols, start, end):
    """Download adjusted closes; return (prices, failed) with no silent drops."""
    raw = yf.download(list(symbols), start=start, end=end, auto_adjust=True,
                      progress=False, group_by="column")
    failed = {}
    if raw is None or raw.empty:
        return pd.DataFrame(), {s: "not returned by yfinance" for s in symbols}
    prices = raw["Close"]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame(list(symbols)[0])
    for s in symbols:
        if s not in prices.columns:
            failed[s] = "not returned by yfinance"
        elif prices[s].notna().sum() == 0:
            failed[s] = "returned only NaN"
    prices = prices.drop(columns=list(failed), errors="ignore")
    prices.index = pd.to_datetime(prices.index).tz_localize(None)
    return prices, failed


def update_snapshot(symbols, snapshot_prices, snapshot_path, start, end, raw_dir):
    """Download only symbols missing from the cached snapshot (new or previously
    failed), merge them in and save a new timestamped snapshot.
    Returns (prices, failed, path); `failed` covers only the symbols fetched now."""
    needed = [s for s in symbols if s not in snapshot_prices.columns]
    if not needed:
        return snapshot_prices, {}, snapshot_path
    log.info("downloading only new/failed symbols: %s", needed)
    new, failed = _download_close(needed, start, end)
    merged = snapshot_prices.join(new, how="outer") if not new.empty else snapshot_prices
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(raw_dir) / f"prices_raw_{stamp}.parquet"
    merged.to_parquet(path)
    meta = {
        "downloaded_utc": stamp, "yfinance_version": yf.__version__,
        "start": start, "end": end, "auto_adjust": True,
        "base_snapshot": snapshot_path.name, "requested": needed, "failed": failed,
        "rows": int(len(merged)), "columns": int(merged.shape[1]),
    }
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return merged, failed, path


def inspect_jump(symbol, date, n_around=2, pad_days=20):
    """Raw (unadjusted) vs adjusted prices around `date` plus yfinance dividend/split
    entries in the window. Returns (price table, actions table)."""
    d = pd.Timestamp(date)
    raw = yf.download(symbol, start=(d - pd.Timedelta(days=pad_days)).strftime("%Y-%m-%d"),
                      end=(d + pd.Timedelta(days=pad_days)).strftime("%Y-%m-%d"),
                      auto_adjust=False, progress=False)
    if raw is None or raw.empty:
        raise RuntimeError(f"no data for {symbol} around {date}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    pos = raw.index.searchsorted(d)
    win = raw.iloc[max(pos - n_around, 0): pos + n_around + 1][
        ["Open", "High", "Low", "Close", "Adj Close", "Volume"]].copy()
    win["adj_over_close"] = win["Adj Close"] / win["Close"]
    win["close_ret"] = raw["Close"].pct_change().reindex(win.index)
    win["adj_ret"] = raw["Adj Close"].pct_change().reindex(win.index)
    actions = yf.Ticker(symbol).actions
    if actions is not None and len(actions):
        actions.index = pd.to_datetime(actions.index).tz_localize(None)
        lo, hi = d - pd.Timedelta(days=pad_days), d + pd.Timedelta(days=pad_days)
        actions = actions[(actions.index >= lo) & (actions.index <= hi)]
    else:
        actions = pd.DataFrame()
    return win, actions


def apply_exclusions(prices, excluded):
    """Drop config-listed data-quality exclusions without touching any price.

    `excluded` is a list of {"ticker": ..., "reason": ...}. Returns
    (prices without those columns, {ticker: reason} for those that were present)."""
    reasons = {e["ticker"]: e["reason"] for e in excluded}
    present = {t: r for t, r in reasons.items() if t in prices.columns}
    return prices.drop(columns=list(present)), present
