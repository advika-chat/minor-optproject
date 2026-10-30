from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data import (align_and_clean, compute_returns, flag_jumps, load_tickers,
                      summary_stats)
from src.utils import load_config

CFG = load_config()
BENCH = "^NSEI"
LIMIT = CFG["data"]["cleaning"]["ffill_limit_days"]


@pytest.fixture
def raw():
    idx = pd.bdate_range("2020-01-01", periods=40)
    rng = np.random.default_rng(CFG["seed"])
    base = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(40, 5)), axis=0))
    df = pd.DataFrame(base, index=idx, columns=[BENCH, "FULL", "LATE", "GAP2", "GAP5"])
    df.loc[idx[:5], "LATE"] = np.nan          # listed after window start
    df.loc[idx[10:12], "GAP2"] = np.nan       # 2-day gap: should be filled
    df.loc[idx[20:25], "GAP5"] = np.nan       # 5-day gap: should be dropped
    return df


def test_align_and_clean_keeps_and_drops(raw):
    stocks, bench, dropped, info = align_and_clean(raw, BENCH, LIMIT)
    assert set(stocks.columns) == {"FULL", "GAP2"}
    assert set(dropped) == {"LATE", "GAP5"}
    assert "window start" in dropped["LATE"]
    assert "remain after forward-fill" in dropped["GAP5"]
    assert info["ffilled_cells"] >= 2 + LIMIT


def test_cleaned_has_no_nans_and_monotonic_index(raw):
    stocks, bench, _, _ = align_and_clean(raw, BENCH, LIMIT)
    assert not stocks.isna().any().any()
    assert stocks.index.is_monotonic_increasing and stocks.index.is_unique
    assert stocks.index.equals(bench.index)


def test_returns_shapes_and_relationship(raw):
    stocks, _, _, _ = align_and_clean(raw, BENCH, LIMIT)
    simple, logret = compute_returns(stocks)
    assert simple.shape == logret.shape == (len(stocks) - 1, stocks.shape[1])
    assert not simple.isna().any().any() and not logret.isna().any().any()
    np.testing.assert_allclose(logret.values, np.log1p(simple.values), atol=1e-12)


def test_flag_jumps():
    idx = pd.bdate_range("2021-01-01", periods=4)
    r = pd.DataFrame({"A": [0.01, 0.25, -0.30, 0.0], "B": [0.0, 0.19, 0.0, 0.0]}, index=idx)
    f = flag_jumps(r, 0.20)
    assert list(f["ticker"]) == ["A", "A"]
    assert len(f) == 2


def test_summary_stats_columns(raw):
    stocks, _, _, _ = align_and_clean(raw, BENCH, LIMIT)
    simple, _ = compute_returns(stocks)
    s = summary_stats(simple, 252)
    assert list(s.columns) == ["ann_mean", "ann_vol", "skew", "excess_kurtosis"]
    assert s.shape[0] == stocks.shape[1]


def test_ticker_file_loads_and_is_unique():
    t = load_tickers(CFG["data"]["tickers_file"])
    assert len(t) == len(set(t)) and all(x.endswith(".NS") for x in t)


# --- checks on the saved processed files (require scripts/run_data.py to have run) ---

PROC = Path(CFG["data"]["processed_dir"])


@pytest.fixture(scope="module")
def processed():
    files = ["prices", "returns_simple", "returns_log", "benchmark"]
    missing = [f for f in files if not (PROC / f"{f}.parquet").exists()]
    if missing:
        pytest.skip(f"processed files missing, run scripts/run_data.py: {missing}")
    return {f: pd.read_parquet(PROC / f"{f}.parquet") for f in files}


def test_processed_no_nans_and_monotonic(processed):
    for name, df in processed.items():
        assert not df.isna().any().any(), name
        assert df.index.is_monotonic_increasing and df.index.is_unique, name


def test_processed_shapes_consistent(processed):
    p, rs, rl, b = (processed[k] for k in ["prices", "returns_simple", "returns_log", "benchmark"])
    assert rs.shape == rl.shape == (p.shape[0] - 1, p.shape[1])
    assert list(rs.columns) == list(rl.columns) == list(p.columns)
    assert rs.index.equals(rl.index) and rs.index.equals(p.index[1:])
    assert b.index.equals(rs.index)  # benchmark file is aligned with the return rows
    assert (p > 0).all().all()


def test_apply_exclusions_drops_columns_without_editing_prices(raw):
    from src.data import apply_exclusions
    excl = [{"ticker": "FULL", "reason": "r1"}, {"ticker": "NOT_THERE", "reason": "r2"}]
    out, present = apply_exclusions(raw, excl)
    assert "FULL" not in out.columns and present == {"FULL": "r1"}
    pd.testing.assert_frame_equal(out, raw.drop(columns=["FULL"]))


def test_config_exclusions_are_well_formed():
    excl = CFG["data"]["excluded_tickers"]
    tickers = load_tickers(CFG["data"]["tickers_file"])
    assert {e["ticker"] for e in excl} == {"ADANIENT.NS", "TRENT.NS", "TMPV.NS"}
    for e in excl:
        assert e["ticker"] in tickers and e["reason"].startswith("data-quality exclusion")


def test_processed_excludes_configured_tickers(processed):
    excluded = {e["ticker"] for e in CFG["data"]["excluded_tickers"]}
    assert excluded.isdisjoint(processed["prices"].columns)
