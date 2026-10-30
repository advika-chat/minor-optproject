"""Independent validation (audit) of the project's results.  Run from the repo root:

    python -m scripts.run_qa [--tests] [--clean] [--hmm] [--eps]

Plan and tolerances: docs/qa_plan_qa.md (written before this script was run). Recomputations use pandas, numpy and scipy only; the repository's
tables are read as plain CSV and compared. The only imports from src/ are in the optional --hmm and --eps items and in C1 (the project's own solver
is the object being compared against); they are labelled where used. Nothing existing is changed. Prints one PASS / FAIL / INFO line per item and
exits 1 if any FAIL.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import optimize, stats

# ----------------------------------------------------------------------------------------------- tolerances (see docs/qa_plan_qa.md)
TOL_EXACT = 1e-8
TOL_DATA_COUNT = 0
TOL_SLSQP_OBJ = 1e-6
TOL_SLSQP_W = 2e-3
TOL_KKT_STAT = 1e-6
TOL_KKT_COMP = 1e-8
TOL_MULT = 5e-4
TOL_BOOT_MC = 2e-4
TOL_BOOT_BT_SHARPE = 0.02
TOL_BOOT_BT_CVAR = 3e-4
TOL_BOOT_BT_DD = 0.01
TOL_ORACLE = 1e-9
TOL_REPRO_W = 1e-5
TOL_MOMENT_Z = 5.0
TOL_SIM_PLUGIN = 1e-4
TOL_ORACLE_RECOMP = 1e-6
TOL_DF = 0.1
TOL_IDENT_SERIES = 5e-5
TOL_PLATEAU_SERIES = 1e-3
TOL_RECON_SHARPE = 2e-3
SEED_MC_BOOT = 424242        # config: 777
SEED_BT_BOOT = 91827         # config: 20261031
SEED_PICK = 31

APPROVED_HYPOTHESES = [
    'H1. Out of sample, Wasserstein DRO (epsilon chosen by cross-validation) outperforms plug-in Markowitz max-Sharpe. Primary test: Monte Carlo true out-of-sample Sharpe at training length T=252; the 95% interval of the paired difference (DRO minus plug-in max-Sharpe) must exclude 0 on the positive side. Secondary: lower CVaR95 and maximum drawdown in the backtest. Falsified if the Monte Carlo interval includes 0 or lies below 0.',
    'H2. The improvement of DRO over plug-in Markowitz is larger when the training window is short, because estimation error is larger. Test: Monte Carlo difference-in-differences between T=126 and T=504; supported if its 95% interval lies above 0.',
    'Fixed in advance: epsilon is the best out-of-fold value (argmax; ties to the smaller epsilon); the primary DRO is mean-variance; every DRO result is shown next to its epsilon=0 version and next to 1/N.',
    'Exploratory (reported, not tested): regime-aware epsilon with two regimes (calm / stress) versus the best static epsilon on CVaR95 in stress windows (Feb to Apr 2020; 2022) and full-sample Sharpe.',
    'Always reported, not tested: price-of-robustness curve, comparison with 1/N, game-theoretic results.',
]   # the wording approved in the task that created docs/hypotheses.md

ROOT = Path.cwd()
TABLES = ROOT / "outputs" / "tables"
CFG = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
RF, TD, CAP = CFG["risk_free_rate"], CFG["trading_days"], CFG["portfolio"]["weight_cap"]
BANNED = ["cl" + "aude", "anthr" + "opic", "ai " + "assistant", "co-" + "authored", "generated " + "with", r"\bl" + "lm" + r"\b"]

RESULTS, NUMBERS, TABLE_OUT = [], [], {}


def chk(item, desc, ok, detail=""):
    RESULTS.append({"item": item, "status": "PASS" if ok else "FAIL", "description": desc, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'} {item} {desc} | {detail}", flush=True)
    return bool(ok)


def info(item, desc, detail=""):
    RESULTS.append({"item": item, "status": "INFO", "description": desc, "detail": detail})
    print(f"INFO {item} {desc} | {detail}", flush=True)


def num(section, name, value, unit, source, repro):
    NUMBERS.append({"section": section, "number": name, "value": value, "unit": unit, "source": source, "reproduced": repro})


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT, encoding="utf-8", errors="replace").stdout


def rd(name):
    return pd.read_csv(TABLES / f"{name}.csv")


def load():
    base = ROOT / "data" / "processed"
    return (pd.read_parquet(base / "returns_simple.parquet"), pd.read_parquet(base / "prices.parquet"),
            pd.read_parquet(base / "benchmark.parquet"))


# ------------------------------------------------------------------------------------------------ metrics (own implementations)
def cvar_ru(r, alpha=0.95):
    """CVaR of daily losses by the Rockafellar-Uryasev minimisation min_z z + mean((loss - z)+) / (1 - alpha), at the data points."""
    loss = -np.asarray(r, float)
    srt = np.sort(loss)
    n = len(srt)
    c = np.concatenate([[0.0], np.cumsum(srt)])            # c[i] = sum of the i smallest
    # for z = srt[i]: sum over j > i of (srt[j] - z) = (total - c[i+1]) - (n - i - 1) * z
    i = np.arange(n)
    tail = (c[n] - c[i + 1]) - (n - i - 1) * srt
    return float(np.min(srt + tail / n / (1.0 - alpha)))


def mdd(r):
    w = np.cumprod(1.0 + np.asarray(r, float))
    peak = np.maximum.accumulate(np.concatenate([[1.0], w]))[1:]
    return float((w / peak - 1.0).min())


def mdd_dates(r, idx):
    w = np.cumprod(1.0 + np.asarray(r, float))
    peak_val = np.maximum.accumulate(np.concatenate([[1.0], w]))[1:]
    dd = w / peak_val - 1.0
    t = int(dd.argmin())
    peak_pos = int(np.flatnonzero(w[: t + 1] == peak_val[t])[-1]) if peak_val[t] > 1.0 else -1
    return (idx[peak_pos] if peak_pos >= 0 else "start"), idx[t]


def metrics(r):
    r = np.asarray(r, float)
    n = len(r)
    sd = r.std(ddof=1)
    cagr = float(np.prod(1.0 + r) ** (TD / n) - 1.0)
    m = mdd(r)
    return {"ann_return": r.mean() * TD, "cagr": cagr, "ann_vol": sd * np.sqrt(TD), "sharpe": (r.mean() - RF / TD) / sd * np.sqrt(TD),
            "max_drawdown": m, "cvar95_daily": cvar_ru(r), "calmar": cagr / abs(m) if m < 0 else np.nan}


def clamp_start(w, cap=CAP):
    w = np.clip(w, 0.0, cap)
    return w / w.sum()


def maxsharpe_cc(mu, cov, cap=CAP, rf=RF):
    """Max-Sharpe over the capped simplex via the Charnes-Cooper QP: min y'Sigma y s.t. (mu - rf)'y = 1, 1'y = k, 0 <= y <= cap k; w = y / k."""
    n = len(mu)
    ex = mu - rf
    best = clamp_start(np.full(n, 1.0 / n))
    s0 = ex @ best / np.sqrt(best @ cov @ best)
    k0 = 1.0 / (ex @ best) if ex @ best > 0 else 1.0
    z0 = np.concatenate([best * k0, [k0]])
    cons = [{"type": "eq", "fun": lambda z: ex @ z[:n] - 1.0, "jac": lambda z: np.concatenate([ex, [0.0]])},
            {"type": "eq", "fun": lambda z: z[:n].sum() - z[n], "jac": lambda z: np.concatenate([np.ones(n), [-1.0]])},
            {"type": "ineq", "fun": lambda z: cap * z[n] - z[:n], "jac": lambda z: np.hstack([-np.eye(n), cap * np.ones((n, 1))])}]
    res = optimize.minimize(lambda z: z[:n] @ cov @ z[:n], z0, jac=lambda z: np.concatenate([2 * cov @ z[:n], [0.0]]), constraints=cons,
                            bounds=[(0, None)] * n + [(1e-9, None)], method="SLSQP", options={"maxiter": 2000, "ftol": 1e-16})
    w = np.clip(res.x[:n], 0, None) / res.x[n]
    w = clamp_start(w, cap)
    s = ex @ w / np.sqrt(w @ cov @ w)
    return (w, s) if s >= s0 - 1e-12 else (best, s0)


def minvar_slsqp(cov, cap=CAP, x0=None):
    n = len(cov)
    x0 = np.full(n, 1.0 / n) if x0 is None else x0
    res = optimize.minimize(lambda w: w @ cov @ w, x0, jac=lambda w: 2 * cov @ w, bounds=[(0, cap)] * n,
                            constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0, "jac": lambda w: np.ones(n)}],
                            method="SLSQP", options={"maxiter": 2000, "ftol": 1e-16})
    return res.x, res.fun


def drift_engine(rets_np, positions, targets, cost=0.001):
    """Own rebalancing engine: gross daily return from drifting weights, cost = cost * sum|target - drifted| on the rebalance day. Targets: dict
    position -> weights or None (carry forward)."""
    n_days, n = rets_np.shape
    first = positions[0]
    net = np.empty(n_days - first)
    drift = np.zeros(n)
    prev = None
    turns = []
    for i, p in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else n_days
        w = targets[p]
        if w is None:
            w = prev if prev is not None else np.full(n, 1.0 / n)
        turn = np.abs(w - drift).sum()
        g = np.cumprod(1.0 + rets_np[p:end], axis=0)
        v = g @ w
        d = v / np.concatenate([[1.0], v[:-1]]) - 1.0
        d[0] -= cost * turn
        net[p - first:end - first] = d
        drift = w * g[-1] / v[-1]
        prev = w
        turns.append(turn)
    return net, np.array(turns)


# =============================================================================================================== A
def section_a(args):
    print("--- A: tests, environment, repository", flush=True)
    files = [f for f in git("ls-files").splitlines() if f]
    head, origin = git("rev-parse", "HEAD").strip(), git("rev-parse", "origin/main").strip()
    chk("A3.1", "HEAD equals origin/main", head == origin, f"HEAD {head[:7]}, origin/main {origin[:7]}")
    tool = "cl" + "aude"
    bad = [f for f in files if re.search(r"(^|/)outputs/cache|(^|/)" + tool.upper() + r"\.md$|(^|/)\." + tool + "/", f, re.I)]
    chk("A3.2", "no cache directory, local-instruction file or ." + tool + " entry is tracked", not bad, f"{len(files)} tracked files; offending: {bad}")
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    chk("A3.3", ".gitignore lists the cache directories", any("outputs/cache" in g for g in gi) and any("cache_bt" in g or "cache*" in g for g in gi), f".gitignore: {gi}")
    status = [s for s in git("status", "--porcelain").splitlines() if s]
    new_ok = [s for s in status if s[3:].startswith(("docs/qa_", "docs/report_numbers_qa", "scripts/run_qa", "outputs/tables/qa_", "docs/results_log.md", ".gitignore"))]
    other = [s for s in status if s not in new_ok]
    chk("A3.4", "working tree: only this audit's own files differ from HEAD", not other, f"{len(status)} entries; unexpected: {other}")
    # A4 scan
    pats = [re.compile(p, re.I) for p in BANNED]
    texts = files + [f for f in git("ls-files", "--others", "--exclude-standard").splitlines() if f]
    hits = []
    for f in texts:
        if f.endswith((".parquet", ".png", ".npy", ".pdf")) or f.startswith("scripts/run_qa") or f.startswith("docs/qa_"):
            continue
        try:
            for i, line in enumerate((ROOT / f).read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                for p in pats:
                    if p.search(line):
                        hits.append((f, i, p.pattern, line.strip()[:100]))
        except OSError:
            pass
    msgs = git("log", "--format=%H%x1f%B%x1e").split("\x1e")
    mh = [(m.split("\x1f")[0][:7], p.pattern) for m in msgs for p in pats if p.search(m)]
    chk("A4.1", "scan of tracked + new files and commit messages for the six forbidden terms", not hits and not mh,
        f"{len(texts)} files scanned; file hits: {hits[:8]}; commit-message hits: {mh}")
    TABLE_OUT["qa_safety_scan"] = pd.DataFrame(hits, columns=["file", "line", "pattern", "text"])
    # A5 timeline
    rows = []
    for path in ["config.yaml", "docs/hypotheses.md", "docs/backtest_spec.md", "docs/montecarlo_notes.md", "docs/backtest_notes.md",
                 "outputs/tables/mc_runs_mc.csv", "outputs/tables/mc_h1_mc.csv", "outputs/tables/mc_h2_mc.csv", "outputs/tables/backtest_summary_bt.csv",
                 "outputs/tables/daily_returns_bt.csv", "outputs/tables/backtest_bootstrap_bt.csv", "src/montecarlo.py", "src/backtest.py"]:
        out = git("log", "--diff-filter=A", "--follow", "--format=%h|%ad", "--date=iso-strict", "--", path).strip().splitlines()
        h, d = (out[-1].split("|") + [""])[:2] if out else ("", "")
        rows.append({"path": path, "first_commit": h, "first_commit_time": d})
    for sec in ("montecarlo:", "backtest:"):
        out = git("log", "-S", sec + "\n", "--format=%h|%ad", "--date=iso-strict", "--", "config.yaml").strip().splitlines()
    # config sections: first commit whose config.yaml contains the section keys that carry the pre-specification
    for label, needle in (("config montecarlo section", "student_df"), ("config backtest pre-specification", "start_position_min")):
        out = git("log", "-S" + needle, "--format=%h|%ad", "--date=iso-strict", "--", "config.yaml").strip().splitlines()
        h, d = (out[-1].split("|") + [""])[:2] if out else ("", "")
        rows.append({"path": label, "first_commit": h, "first_commit_time": d})
    tl = pd.DataFrame(rows)
    TABLE_OUT["qa_preregistration_timeline"] = tl
    t = tl.set_index("path")
    mc_same = t.loc["docs/hypotheses.md", "first_commit"] == t.loc["outputs/tables/mc_runs_mc.csv", "first_commit"]
    bt_same = t.loc["docs/backtest_spec.md", "first_commit"] == t.loc["outputs/tables/backtest_summary_bt.csv", "first_commit"]
    info("A5.1", "hypotheses/spec and result files first appear in the same commit?", f"MC: hypotheses.md {t.loc['docs/hypotheses.md', 'first_commit']} vs "
         f"mc_runs {t.loc['outputs/tables/mc_runs_mc.csv', 'first_commit']} -> same={mc_same}; backtest: backtest_spec.md {t.loc['docs/backtest_spec.md', 'first_commit']} vs "
         f"summary {t.loc['outputs/tables/backtest_summary_bt.csv', 'first_commit']} -> same={bt_same}")
    # local file times (supporting evidence only)
    ft = {p: pd.Timestamp(Path(p).stat().st_mtime, unit="s").strftime("%Y-%m-%d %H:%M:%S") for p in
          ["docs/hypotheses.md", "docs/backtest_spec.md", "outputs/tables/mc_runs_mc.csv", "outputs/tables/daily_returns_bt.csv"] if Path(p).exists()}
    info("A5.2", "local file modification times (supporting only; a file edited after its first save shows the last edit)", str(ft))
    # config tolerance history
    log = git("log", "-p", "--format=@@%h|%ad", "--date=iso-strict", "--", "config.yaml").splitlines()
    cur, ch = "", []
    for line in log:
        if line.startswith("@@"):
            cur = line[2:]
        elif line[:1] in "+-" and not line.startswith(("+++", "---")) and re.search(r"tol|turnover_max|max\b|eps_near|min_blocks", line, re.I):
            ch.append((cur, line.strip()[:140]))
    TABLE_OUT["qa_config_tolerance_history"] = pd.DataFrame(ch, columns=["commit_time", "diff_line"])
    info("A5.3", "config.yaml diff lines touching tolerance-like keys across all commits", f"{len(ch)} lines; see outputs/tables/qa_config_tolerance_history.csv")
    if args.tests:
        t0 = time.time()
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], capture_output=True, text=True, cwd=ROOT)
        tail = [x for x in p.stdout.strip().splitlines() if x.strip()][-1] if p.stdout.strip() else p.stderr[-200:]
        m = re.search(r"(\d+) passed", tail)
        f = re.search(r"(\d+) failed", tail)
        chk("A1.1", "full test suite passes", p.returncode == 0, f"pytest exit {p.returncode}; summary: {tail}; wall {time.time() - t0:.0f} s")
        num("A", "tests passed", int(m.group(1)) if m else None, "count", "pytest", "yes")
    if args.clean:
        clean_checkout(files)


def clean_checkout(files):
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="nifty_clean_"))
    for f in files:
        dst = tmp / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((ROOT / f).read_bytes())
    py = [sys.executable, "-m", "venv", str(tmp / "_venv")]
    r1 = subprocess.run(py, capture_output=True, text=True)
    vpy = str(tmp / "_venv" / "Scripts" / "python.exe") if (tmp / "_venv" / "Scripts").exists() else str(tmp / "_venv" / "bin" / "python")
    r2 = subprocess.run([vpy, "-m", "pip", "install", "-q", "-r", "requirements.txt"], capture_output=True, text=True, cwd=tmp)
    chk("A2.1", "fresh virtual environment: requirements.txt installs", r1.returncode == 0 and r2.returncode == 0, f"venv exit {r1.returncode}, pip exit {r2.returncode}; {r2.stderr[-300:]}")
    subset = ["tests/test_data.py", "tests/test_markowitz.py", "tests/test_solver_own.py", "tests/test_robust.py", "tests/test_wdro.py"]
    r3 = subprocess.run([vpy, "-m", "pytest", "-q", "-p", "no:cacheprovider", *subset], capture_output=True, text=True, cwd=tmp)
    tail = [x for x in r3.stdout.strip().splitlines() if x.strip()][-1:] or [r3.stderr[-300:]]
    chk("A2.2", "clean checkout: data, markowitz, solver_own, robust, wdro tests pass", r3.returncode == 0,
        f"pytest exit {r3.returncode}; {tail[0]}; temp dir {tmp}; wall {time.time() - t0:.0f} s")
    imp = subprocess.run([vpy, "-c", "import src.montecarlo, src.backtest, scripts.run_montecarlo, scripts.run_backtest"], capture_output=True, text=True, cwd=tmp)
    chk("A2.3", "clean checkout: final-state modules import (montecarlo, backtest and their scripts)", imp.returncode == 0, imp.stderr[-300:] or "ok")


# =============================================================================================================== B
def section_b():
    print("--- B: data", flush=True)
    rets, prices, bench = load()
    tick = pd.read_csv(ROOT / "data" / "nifty50_tickers.csv", comment="#")
    dropped = rd("data_dropped_tickers")
    chk("B1.1", "40 stocks", rets.shape[1] == 40, f"{rets.shape[1]} columns")
    chk("B1.2", "2911 calendar days (prices) and 2910 return rows (returns and benchmark)", len(prices) == 2911 and len(rets) == 2910 and len(bench) == 2910, f"prices {len(prices)}, returns {len(rets)}, benchmark {len(bench)}")
    chk("B1.3", "date range 2015-01-02 to 2026-10-29 (prices), returns from 2015-01-05",
        str(prices.index[0].date()) == "2015-01-02" and str(prices.index[-1].date()) == "2026-10-29" and str(rets.index[0].date()) == "2015-01-05" and str(rets.index[-1].date()) == "2026-10-29",
        f"prices {prices.index[0].date()}..{prices.index[-1].date()}, returns {rets.index[0].date()}..{rets.index[-1].date()}")
    chk("B1.4", "no NaN in prices or returns", prices.isna().sum().sum() == 0 and rets.isna().sum().sum() == 0, f"NaN prices {prices.isna().sum().sum()}, returns {rets.isna().sum().sum()}")
    chk("B1.5", "returns index equals benchmark return index and prices index equals benchmark index", rets.index.equals(bench.index) and prices.index[1:].equals(rets.index),
        f"returns and benchmark share one index of {len(rets)} rows; prices index minus its first day equals it")
    # forward-filled cells from the raw snapshot (assumption: cells missing in the raw snapshot on benchmark days between a stock's first and last prices)
    try:
        raw_files = sorted((ROOT / "data" / "raw").glob("prices_raw_*.parquet"))
        raw = pd.read_parquet(raw_files[-1])
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw.xs(raw.columns.levels[0][0], axis=1)
        all50 = [t for t in tick.iloc[:, 0] if t in raw.columns]
        rr = raw[all50].reindex(prices.index)
        first = rr.apply(lambda s_: s_.first_valid_index())
        per = {c: int(rr[c].loc[first[c]:].isna().sum()) for c in all50}
        where = {c: [str(d.date()) for d in rr[c].loc[first[c]:][rr[c].loc[first[c]:].isna()].index] for c in all50 if per[c]}
        n50, n40 = sum(per.values()), sum(v for c, v in per.items() if c in prices.columns)
        chk("B1.6", "forward-filled cells: 7 over the 50 requested tickers (the logged figure); the 40 final stocks contain fewer", n50 == 7 and n40 == 6,
            f"{n50} over {len(all50)} tickers in {raw_files[-1].name}, {n40} within the 40 final stocks; cells: {where}")
    except Exception as exc:                                             # raw data are not tracked: report, do not hide
        info("B1.6", "forward-filled cells could not be recomputed", f"{type(exc).__name__}: {str(exc)[:120]}")
    big = rets.abs().stack()
    big = big[big > 0.20]
    found = sorted((str(d.date()), t, round(float(rets.loc[d, t]), 4)) for (d, t) in big.index)
    flag = rd("data_flagged_jumps")
    dcol = [c for c in flag.columns if "date" in c.lower()][0]
    tcol = [c for c in flag.columns if "ticker" in c.lower()][0]
    logged = sorted((str(pd.Timestamp(a).date()), b) for a, b in zip(flag[dcol], flag[tcol]))
    chk("B1.7", "6 daily moves beyond +-20%, equal to the flagged table", len(found) == 6 and [(a, b) for a, b, _ in found] == logged, f"found {found}")
    ex = ["TMPV.NS", "ADANIENT.NS", "TRENT.NS"]
    short = [t for t in dropped.iloc[:, 0] if t not in ex]
    chk("B1.8", "3 excluded and 7 short-history tickers absent from the processed data and the table lists exactly 10",
        not set(ex + short) & set(rets.columns) and len(dropped) == 10 and len(short) == 7 and set(tick.iloc[:, 0]) - set(rets.columns) == set(dropped.iloc[:, 0]),
        f"absent: {sorted(set(tick.iloc[:, 0]) - set(rets.columns))}")
    num("B", "number of stocks", 40, "count", "data/processed/returns_simple.parquet", "yes")
    num("B", "trading days (benchmark calendar)", 2911, "days", "data/processed/benchmark.parquet", "yes")
    num("B", "return rows", 2910, "days", "data/processed/returns_simple.parquet", "yes")
    num("B", "daily moves beyond +-20%", 6, "count", "outputs/tables/data_flagged_jumps.csv", "yes")
    num("B", "excluded tickers", 3, "count", "config.yaml data.excluded_tickers", "yes")
    num("B", "short-history tickers dropped", 7, "count", "outputs/tables/data_dropped_tickers.csv", "yes")
    # B2
    events = [("SBIN.NS", "2017-10-25", ["AXISBANK.NS", "ICICIBANK.NS", "KOTAKBANK.NS", "HDFCBANK.NS"]), ("ADANIPORTS.NS", "2024-06-04", ["LT.NS", "COALINDIA.NS", "NTPC.NS", "POWERGRID.NS"])]
    rows = []
    for tk, d, peers in events:
        d = pd.Timestamp(d)
        i = prices.index.get_loc(d)
        sl = prices[tk].iloc[i - 3:i + 4]
        for dt, px in sl.items():
            rows.append({"ticker": tk, "event": str(d.date()), "date": str(dt.date()), "close": px, "ret": float(rets.loc[dt, tk]) if dt in rets.index else np.nan,
                         "index_ret": float(bench.loc[dt, "simple"]) if dt in bench.index else np.nan, "median_stock_ret": float(rets.loc[dt].median()) if dt in rets.index else np.nan})
        move = float(rets.loc[d, tk])
        nxt = float(rets.iloc[rets.index.get_loc(d) + 1][tk])
        ratio = float(prices[tk].iloc[i] / prices[tk].iloc[i - 1])
        k = 1 / ratio if ratio < 1 else ratio
        split_like = min(abs(k - kk) / kk for kk in (2, 3, 4, 5, 10)) < 0.03
        lvl = float(prices[tk].iloc[i + 20] / prices[tk].iloc[i - 1])
        peers_ret = {p: round(float(rets.loc[d, p]), 4) for p in peers}
        n_big = int((rets.loc[d].abs() > 0.05).sum())
        raw_ohlc = ""
        info("B2." + tk[:4], f"{tk} {d.date()}", f"move {move:+.4f}; index {float(bench.loc[d, 'simple']):+.4f}; cross-section median {float(rets.loc[d].median()):+.4f}; peers {peers_ret}; "
             f"stocks beyond +-5% that day: {n_big}; next-day return {nxt:+.4f}; price ratio {ratio:.4f} (split-like: {split_like}); price 20 days later / prior close {lvl:.4f}")
        num("B", f"{tk} return on {d.date()}", move, "daily simple return", "data/processed/returns_simple.parquet", "yes")
    TABLE_OUT["qa_event_context"] = pd.DataFrame(rows)
    # B3
    ew = rets.mean(axis=1)
    ix = bench["simple"].loc[rets.index]
    res = {}
    for nm, s in (("index", ix), ("equal_weight", ew)):
        for dd in (1, 0):
            res[(nm, dd)] = {"return": s.mean() * TD, "vol": s.std(ddof=dd) * np.sqrt(TD)}
            res[(nm, dd)]["sharpe"] = (res[(nm, dd)]["return"] - RF) / res[(nm, dd)]["vol"]
    ins = pd.read_csv(TABLES / "markowitz_insample_summary.csv", index_col=0)
    for dd in (1, 0):
        d_ew = max(abs(res[("equal_weight", dd)][k] - ins.loc["1/N", k]) for k in ("return", "vol", "sharpe"))
        info(f"B3.conv{dd}", f"equal-weight statistics with ddof={dd} against markowitz_insample_summary.csv", f"max abs diff {d_ew:.2e}")
    dd = 1 if max(abs(res[("equal_weight", 1)][k] - ins.loc["1/N", k]) for k in ("return", "vol", "sharpe")) <= max(abs(res[("equal_weight", 0)][k] - ins.loc["1/N", k]) for k in ("return", "vol", "sharpe")) else 0
    chk("B3.1", "equal-weight return, vol, Sharpe equal the in-sample table", max(abs(res[("equal_weight", dd)][k] - ins.loc["1/N", k]) for k in ("return", "vol", "sharpe")) <= TOL_EXACT,
        f"ddof={dd}: {res[('equal_weight', dd)]}")
    logged_ew = {"return": 0.1777, "vol": 0.1617, "sharpe": 0.697}
    chk("B3.2", "equal-weight values equal the results_log entry to the digits quoted", all(abs(res[("equal_weight", dd)][k] - v) < 0.5 * 10 ** -(3 if k == "sharpe" else 4) + 1e-12 for k, v in logged_ew.items()), f"log {logged_ew}")
    logged_ix = {"return": 0.1002, "vol": 0.1615, "sharpe": 0.218}
    chk("B3.3", "index return, vol, Sharpe equal the results_log entry to the digits quoted", all(abs(res[("index", dd)][k] - v) < 0.5 * 10 ** -(3 if k == "sharpe" else 4) + 1e-12 for k, v in logged_ix.items()), f"{res[('index', dd)]}")
    sk, ku = float(stats.skew(ix, bias=True)), float(stats.kurtosis(ix, bias=True))
    sk1, ku1 = float(ix.skew()), float(ix.kurt())
    chk("B3.4", "index skew -0.995 and excess kurtosis 16.98 (results_log)", (abs(sk - -0.995) < 5e-4 and abs(ku - 16.98) < 5e-3) or (abs(sk1 - -0.995) < 5e-4 and abs(ku1 - 16.98) < 5e-3),
        f"population (moment) estimators {sk:.4f}/{ku:.4f}; sample-adjusted {sk1:.4f}/{ku1:.4f}")
    vols = rets.std(ddof=1) * np.sqrt(TD)
    kurt = rets.apply(lambda s: stats.kurtosis(s, bias=True))
    kurt1 = rets.kurt()
    chk("B3.5", "single-stock median vol 0.2841, range 0.2232-0.4240, ratio to index 1.76, median excess kurtosis 7.15 (results_log)",
        abs(vols.median() - 0.2841) < 5e-5 and abs(vols.min() - 0.2232) < 5e-5 and abs(vols.max() - 0.4240) < 5e-5 and abs(vols.median() / ix.std(ddof=1) / np.sqrt(TD) - 1.76) < 5e-3
        and (abs(kurt.median() - 7.15) < 5e-3 or abs(kurt1.median() - 7.15) < 5e-3),
        f"median vol {vols.median():.4f}, range {vols.min():.4f}-{vols.max():.4f}, ratio {vols.median() / (ix.std(ddof=1) * np.sqrt(TD)):.3f}, median kurtosis {kurt.median():.3f} (moment) / {kurt1.median():.3f} (adjusted)")
    for k, v in res[("index", dd)].items():
        num("B", f"NIFTY 50 price index {k}", float(v), "annualised" if k != "sharpe" else "ratio", "data/processed/benchmark.parquet", "yes")
    num("B", "NIFTY 50 price index skew", sk, "ratio", "data/processed/benchmark.parquet", "yes")
    num("B", "NIFTY 50 price index excess kurtosis", ku, "ratio", "data/processed/benchmark.parquet", "yes")
    for k, v in res[("equal_weight", dd)].items():
        num("B", f"1/N in-sample {k}", float(v), "annualised" if k != "sharpe" else "ratio", "outputs/tables/markowitz_insample_summary.csv", "yes")
    num("B", "median single-stock volatility", float(vols.median()), "annualised", "data/processed/returns_simple.parquet", "yes")


# =============================================================================================================== C
def section_c():
    print("--- C: Markowitz and solver", flush=True)
    rets = load()[0]
    mu, cov = rets.mean().to_numpy() * TD, rets.cov().to_numpy() * TD
    names = list(rets.columns)
    n = len(mu)
    rng = np.random.default_rng(SEED_PICK)
    ins = pd.read_csv(TABLES / "markowitz_insample_summary.csv", index_col=0)
    wf = pd.read_csv(TABLES / "markowitz_max_sharpe_weights.csv").set_index("Ticker")["weight"].reindex(names).fillna(0.0).to_numpy()
    # min variance
    sols = []
    for _ in range(8):
        x0 = clamp_start(rng.dirichlet(np.ones(n)))
        sols.append(minvar_slsqp(cov, CAP, x0))
    w_mv, v_mv = min(sols, key=lambda t: t[1])
    spread = max(s[1] for s in sols) - v_mv
    ret, vol = mu @ w_mv, np.sqrt(w_mv @ cov @ w_mv)
    from src import solver_own as so_mod                  # the object under comparison only
    so_mv = so_mod.min_variance(cov, CAP, CFG["solver_own"])
    chk("C1.1", "min-variance by SLSQP (8 random starts) matches the table (return, vol, Sharpe)", max(abs(ret - ins.loc["Min-variance", "return"]), abs(vol - ins.loc["Min-variance", "vol"]),
        abs((ret - RF) / vol - ins.loc["Min-variance", "sharpe"])) <= TOL_SLSQP_OBJ * 10, f"SLSQP {ret:.6f}/{vol:.6f}/{(ret - RF) / vol:.6f}; table {ins.loc['Min-variance'].tolist()}; variance spread across starts {spread:.1e}; own solver {mu @ np.asarray(so_mv.weights):.6f}/{np.sqrt(np.asarray(so_mv.weights) @ cov @ np.asarray(so_mv.weights)):.8f}")
    chk("C1.2", "min-variance logged to the digits quoted (13.2% / 12.9% / 0.520)", round(ret, 3) == 0.132 and round(vol, 3) == 0.129 and round((ret - RF) / vol, 3) == 0.520,
        f"{ret:.4f}/{vol:.4f}/{(ret - RF) / vol:.4f}")
    # max Sharpe: direct Sharpe objective from random starts, plus the convex reformulation
    ex = mu - RF
    best = None
    for _ in range(10):
        x0 = clamp_start(rng.dirichlet(np.ones(n)))
        r = optimize.minimize(lambda w: -(ex @ w) / np.sqrt(w @ cov @ w), x0, bounds=[(0, CAP)] * n,
                              constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}], method="SLSQP", options={"maxiter": 2000, "ftol": 1e-16})
        if best is None or r.fun < best.fun:
            best = r
    w_ms_direct = best.x
    w_ms, s_ms = maxsharpe_cc(mu, cov)
    s_direct = -best.fun
    chk("C1.3", "max-Sharpe: direct SLSQP (10 random starts) and the convex reformulation agree on the Sharpe ratio", abs(s_direct - s_ms) <= TOL_SLSQP_OBJ * 10, f"direct {s_direct:.8f}, convex {s_ms:.8f}")
    ret2, vol2 = mu @ w_ms, np.sqrt(w_ms @ cov @ w_ms)
    chk("C1.4", "max-Sharpe matches the table (return, vol, Sharpe)", max(abs(ret2 - ins.loc["Max-Sharpe", "return"]), abs(vol2 - ins.loc["Max-Sharpe", "vol"]), abs(s_ms - ins.loc["Max-Sharpe", "sharpe"])) <= TOL_SLSQP_OBJ * 10,
        f"SLSQP {ret2:.6f}/{vol2:.6f}/{s_ms:.6f}; table {ins.loc['Max-Sharpe'].tolist()}")
    chk("C1.5", "max-Sharpe logged to the digits quoted (23.8% / 17.2% / 1.005)", round(ret2, 3) == 0.238 and round(vol2, 3) == 0.172 and round(s_ms, 3) == 1.005, f"{ret2:.4f}/{vol2:.4f}/{s_ms:.4f}")
    chk("C1.6", "max-Sharpe weights: SLSQP versus stored weight file", float(np.abs(w_ms - wf).max()) <= TOL_SLSQP_W, f"max abs weight diff {np.abs(w_ms - wf).max():.2e}")
    chk("C1.7", "max-Sharpe stored weights are feasible and attain the table Sharpe", abs(wf.sum() - 1) < 1e-8 and wf.min() >= -1e-9 and wf.max() <= CAP + 1e-9 and abs((mu @ wf - RF) / np.sqrt(wf @ cov @ wf) - ins.loc["Max-Sharpe", "sharpe"]) < 1e-6,
        f"sum {wf.sum():.10f}, min {wf.min():.2e}, max {wf.max():.10f}, Sharpe {(mu @ wf - RF) / np.sqrt(wf @ cov @ wf):.8f}")
    # project solver for comparison
    try:
        from src import solver_own as so           # object under comparison, not used for any recomputation above
        o = so.max_sharpe(cov, mu, CAP, RF, CFG["solver_own"])
        om = so.min_variance(cov, CAP, CFG["solver_own"])
        chk("C1.8", "own solver: max-Sharpe and min-variance weights match the independent SLSQP solutions", np.abs(np.asarray(o.weights) - w_ms).max() <= TOL_SLSQP_W and np.abs(np.asarray(om.weights) - w_mv).max() <= TOL_SLSQP_W,
            f"max-Sharpe weights diff {np.abs(np.asarray(o.weights) - w_ms).max():.2e}, Sharpe own {o.sharpe:.8f}; min-var weights diff {np.abs(np.asarray(om.weights) - w_mv).max():.2e}")
        w_own = np.asarray(o.weights, float)
    except Exception as exc:
        info("C1.8", "own solver comparison could not run", str(exc)[:150])
        w_own = wf
    num("C", "min-variance annualised return", float(ret), "annualised", "outputs/tables/markowitz_insample_summary.csv", "yes")
    num("C", "min-variance annualised volatility", float(vol), "annualised", "outputs/tables/markowitz_insample_summary.csv", "yes")
    num("C", "min-variance Sharpe", float((ret - RF) / vol), "ratio", "outputs/tables/markowitz_insample_summary.csv", "yes")
    num("C", "max-Sharpe annualised return", float(ret2), "annualised", "outputs/tables/markowitz_insample_summary.csv", "yes")
    num("C", "max-Sharpe annualised volatility", float(vol2), "annualised", "outputs/tables/markowitz_insample_summary.csv", "yes")
    num("C", "max-Sharpe Sharpe", float(s_ms), "ratio", "outputs/tables/markowitz_insample_summary.csv", "yes")
    # C2 KKT from weights (convention of docs/kkt_duality_notes.md: V = w' Sigma w; L = V - lam(1'w-1) - gam(mu'w-r) - alpha'w + beta'(w-cap))
    kk = rd("kkt_report_own")
    rows = []
    for label, w, thr in (("stored_weights", wf, 1e-5), ("own_solver_weights", w_own, 1e-7)):
        r_t = float(mu @ w)
        g = 2 * cov @ w
        free = (w > thr) & (w < CAP - thr)
        A = np.column_stack([np.ones(free.sum()), mu[free]])
        sol, *_ = np.linalg.lstsq(A, g[free], rcond=None)
        lam, gam = float(sol[0]), float(sol[1])
        resid = g - lam - gam * mu
        stat = float(np.abs(resid[free]).max())
        lo, up = w <= thr, w >= CAP - thr
        alpha = np.where(lo, resid, 0.0)
        beta = np.where(up, -resid, 0.0)
        comp = float(max(np.abs(alpha * w).max(), np.abs(beta * (CAP - w)).max()))
        sign_ok = bool(alpha.min() >= -1e-9 and beta.min() >= -1e-9)
        V = float(w @ cov @ w)
        ident = abs(gam - 2 * V / (r_t - RF))
        rows.append({"weights": label, "n_free": int(free.sum()), "n_at_cap": int(up.sum()), "n_at_zero": int(lo.sum()), "lam_budget": lam, "gam_return": gam,
                     "sum_beta": float(beta.sum()), "stationarity": stat, "comp_slack": comp, "multipliers_signs_ok": sign_ok, "gam_identity_err": ident})
        chk(f"C2.{label}", f"KKT of the {label}: stationarity <= {TOL_KKT_STAT:g}, complementary slackness <= {TOL_KKT_COMP:g}, multiplier signs, gamma = 2V/(r - rf)",
            stat <= TOL_KKT_STAT and comp <= TOL_KKT_COMP and sign_ok and ident < 1e-5, f"free {int(free.sum())}, cap {int(up.sum())}, zero {int(lo.sum())}; stationarity {stat:.2e}; comp {comp:.2e}; signs ok {sign_ok}; gamma identity err {ident:.1e}")
    TABLE_OUT["qa_kkt_recompute"] = pd.DataFrame(rows)
    b = float(kk[(kk.solution == "max_sharpe") & (kk.constraint == "budget")]["value"].iloc[0])
    gam_t = float(kk[(kk.solution == "max_sharpe") & (kk.constraint == "return_target")]["value"].iloc[0])
    lam_o = rows[1]["lam_budget"]
    chk("C2.mult", "budget multiplier at max-Sharpe is about -0.0179 (task text) and equals the logged kkt_report_own value", abs(lam_o - -0.0179) <= TOL_MULT and abs(lam_o - b) <= 1e-6 and abs(rows[1]["gam_return"] - gam_t) <= 1e-6,
        f"recomputed lam {lam_o:.6f} (stored-weights solution {rows[0]['lam_budget']:.6f}); table {b:.6f}; gam {rows[1]['gam_return']:.6f} vs {gam_t:.6f}. Convention: dV*/d(budget), V = w'Sigma w (not 1/2), L = V - lam(1'w - 1) - ...")
    num("C", "budget multiplier at max-Sharpe (lam, dV*/d budget, V = w'Sigma w)", lam_o, "variance per unit of capital", "outputs/tables/kkt_report_own.csv (solution max_sharpe, constraint budget)", "yes")
    num("C", "return-target multiplier at max-Sharpe (gam = dV*/dr)", rows[1]["gam_return"], "variance per unit of return", "outputs/tables/kkt_report_own.csv", "yes")
    num("C", "cap shadow price at max-Sharpe (sum of beta = -dV*/dcap)", rows[1]["sum_beta"], "variance per unit of cap", "outputs/tables/kkt_report_own.csv", "yes (computed from the weights)")


# =============================================================================================================== D
def boot_mean(d, rng, b=10000):
    idx = rng.integers(0, len(d), size=(b, len(d)))
    return d[idx].mean(axis=1)


def section_d():
    print("--- D: Monte Carlo", flush=True)
    runs = pd.read_csv(TABLES / "mc_runs_mc.csv")
    rng = np.random.default_rng(SEED_MC_BOOT)
    piv = runs[runs.scenario == "primary"].pivot_table(index=["T", "run"], columns="method", values="true_sharpe")
    rows = []
    delta, boots = {}, {}
    for T in (126, 252, 504):
        d = (piv.loc[T, "dro_cv"] - piv.loc[T, "plugin_maxsharpe"]).to_numpy()
        bm = boot_mean(d, rng)
        delta[T], boots[T] = d.mean(), bm
        rows.append({"quantity": f"Delta(T={T})", "estimate": d.mean(), "ci_lo": np.quantile(bm, 0.025), "ci_hi": np.quantile(bm, 0.975), "n": len(d)})
    did = delta[126] - delta[504]
    bdid = boots[126] - boots[504]
    rows.append({"quantity": "DiD", "estimate": did, "ci_lo": np.quantile(bdid, 0.025), "ci_hi": np.quantile(bdid, 0.975), "n": 500})
    own = pd.DataFrame(rows)
    h1, h2 = rd("mc_h1_mc"), rd("mc_h2_mc")
    for _, r in own.iterrows():
        if r.quantity.startswith("Delta"):
            T = int(re.search(r"\d+", r.quantity).group())
            t = h1[h1["T"] == T].iloc[0]
            ok = abs(r.estimate - t.mean_diff) <= TOL_EXACT and abs(r.ci_lo - t.ci_lo) <= TOL_BOOT_MC and abs(r.ci_hi - t.ci_hi) <= TOL_BOOT_MC and ((r.ci_lo > 0) == (t.ci_lo > 0)) and ((r.ci_lo < 0 < r.ci_hi) == (t.ci_lo < 0 < t.ci_hi))
            chk(f"D1.{r.quantity}", f"{r.quantity} (DRO CV minus plug-in max-Sharpe, true Sharpe) and its paired bootstrap interval", ok,
                f"own {r.estimate:+.5f} [{r.ci_lo:+.5f}, {r.ci_hi:+.5f}]; table {t.mean_diff:+.5f} [{t.ci_lo:+.5f}, {t.ci_hi:+.5f}]")
        else:
            t = h2[h2.quantity.str.startswith("DiD")].iloc[0]
            ok = abs(r.estimate - t.estimate) <= TOL_EXACT and abs(r.ci_lo - t.ci_lo) <= TOL_BOOT_MC * 2 and abs(r.ci_hi - t.ci_hi) <= TOL_BOOT_MC * 2 and (r.ci_hi < 0) == (t.ci_hi < 0)
            chk("D1.DiD", "H2 difference in differences Delta(126) - Delta(504) and interval", ok,
                f"own {r.estimate:+.5f} [{r.ci_lo:+.5f}, {r.ci_hi:+.5f}]; table {t.estimate:+.5f} [{t.ci_lo:+.5f}, {t.ci_hi:+.5f}] (endpoint tolerance doubled: two bootstrapped arms)")
    t252 = own[own.quantity == "Delta(T=252)"].iloc[0]
    chk("D1.log", "logged H1 -0.0049 [-0.0073, -0.0025], Delta(126) -0.0079, Delta(252) -0.0049, Delta(504) +0.0001, DiD -0.0080 [-0.0117, -0.0043]: point estimates equal when rounded to the digits quoted; interval endpoints within the bootstrap tolerance",
        round(t252.estimate, 4) == -0.0049 and round(delta[126], 4) == -0.0079 and round(delta[504], 4) == 0.0001 and round(did, 4) == -0.0080
        and abs(t252.ci_lo - -0.0073) < TOL_BOOT_MC and abs(t252.ci_hi - -0.0025) < TOL_BOOT_MC and abs(own.iloc[3].ci_lo - -0.0117) < 2 * TOL_BOOT_MC and abs(own.iloc[3].ci_hi - -0.0043) < 2 * TOL_BOOT_MC,
        f"own Delta(126) {delta[126]:+.5f}, Delta(252) {delta[252]:+.5f}, Delta(504) {delta[504]:+.5f}, DiD {did:+.5f}, DiD interval [{own.iloc[3].ci_lo:+.5f}, {own.iloc[3].ci_hi:+.5f}] (a first version of this item compared interval endpoints to four decimals, which a different bootstrap seed cannot meet; corrected to the plan's tolerance)")
    h1_verdict = "SUPPORTED" if t252.ci_lo > 0 else "FALSIFIED" if t252.ci_hi < 0 else "NOT SUPPORTED"
    h2_verdict = "SUPPORTED" if own.iloc[3].ci_lo > 0 else "NOT SUPPORTED"
    chk("D1.verdict", "independent verdicts: H1 falsified (interval entirely below zero at T = 252), H2 not supported", h1_verdict == "FALSIFIED" and h2_verdict == "NOT SUPPORTED", f"H1 {h1_verdict}, H2 {h2_verdict}")
    # ablation
    pairs = {"DRO (CV epsilon) minus DRO epsilon=0": ("dro_cv", "dro_eps0"), "DRO (CV epsilon) minus 1/N": ("dro_cv", "equal_weight"),
             "DRO epsilon=0 minus plug-in max-Sharpe": ("dro_eps0", "plugin_maxsharpe")}
    ab = rd("mc_ablation_mc")
    bad = []
    out = []
    for _, t in ab.iterrows():
        a, b = pairs[t.comparison]
        d = (piv.loc[int(t["T"]), a] - piv.loc[int(t["T"]), b]).to_numpy()
        bm = boot_mean(d, rng)
        lo, hi = np.quantile(bm, [0.025, 0.975])
        out.append({"T": t["T"], "comparison": t.comparison, "own_mean": d.mean(), "table_mean": t.mean_diff, "own_lo": lo, "own_hi": hi, "table_lo": t.ci_lo, "table_hi": t.ci_hi})
        if abs(d.mean() - t.mean_diff) > TOL_EXACT or abs(lo - t.ci_lo) > TOL_BOOT_MC or abs(hi - t.ci_hi) > TOL_BOOT_MC or (lo > 0) != (t.ci_lo > 0) or (hi < 0) != (t.ci_hi < 0):
            bad.append((int(t["T"]), t.comparison))
    TABLE_OUT["qa_mc_ablation_recompute"] = pd.DataFrame(out)
    chk("D1.ablation", "ablation table (9 rows): point estimates, intervals and their sign", not bad, f"mismatches: {bad}")
    TABLE_OUT["qa_mc_h1_h2_recompute"] = own
    num("D", "H1 mean difference in true Sharpe at T = 252 (DRO CV minus plug-in)", float(delta[252]), "Sharpe units", "outputs/tables/mc_h1_mc.csv", "yes")
    num("D", "H1 interval lower", float(t252.ci_lo), "Sharpe units", "outputs/tables/mc_h1_mc.csv", "yes (new seed, within tolerance)")
    num("D", "H1 interval upper", float(t252.ci_hi), "Sharpe units", "outputs/tables/mc_h1_mc.csv", "yes (new seed, within tolerance)")
    num("D", "Delta(126)", float(delta[126]), "Sharpe units", "outputs/tables/mc_h2_mc.csv", "yes")
    num("D", "Delta(504)", float(delta[504]), "Sharpe units", "outputs/tables/mc_h2_mc.csv", "yes")
    num("D", "H2 difference in differences", float(did), "Sharpe units", "outputs/tables/mc_h2_mc.csv", "yes")
    num("D", "H2 interval lower", float(own.iloc[3].ci_lo), "Sharpe units", "outputs/tables/mc_h2_mc.csv", "yes (new seed, within tolerance)")
    num("D", "H2 interval upper", float(own.iloc[3].ci_hi), "Sharpe units", "outputs/tables/mc_h2_mc.csv", "yes (new seed, within tolerance)")
    for _, t in ab.iterrows():
        num("D", f"ablation T={int(t['T'])}: {t.comparison}", float(t.mean_diff), "Sharpe units", "outputs/tables/mc_ablation_mc.csv", "yes")
    # D3 oracle bound in every stored run
    g = runs.groupby(["scenario", "T", "run"])
    orc = runs[runs.method == "oracle"].set_index(["scenario", "T", "run"])["true_sharpe"]
    mx = runs[runs.method != "oracle"].groupby(["scenario", "T", "run"])["true_sharpe"].max()
    excess = (mx - orc.reindex(mx.index)).max()
    chk("D3.1", "oracle true Sharpe is at least that of every method in every stored run", excess <= TOL_ORACLE and g.ngroups == 2700, f"{g.ngroups} runs checked; largest (method - oracle) = {excess:.2e}; groups: {runs.groupby(['scenario', 'T']).run.nunique().to_dict()}")
    chk("D3.2", "oracle true Sharpe is the same in every run of a scenario", (runs[runs.method == "oracle"].groupby("scenario").true_sharpe.agg(lambda s: s.max() - s.min()) < 1e-12).all(), str(runs[runs.method == "oracle"].groupby("scenario").true_sharpe.first().to_dict()))
    # fresh re-simulation of 20 runs
    rets = load()[0]
    td = TD
    mc = CFG["montecarlo"]
    scen_idx = {"primary": (0, 0.5, "t"), "s1.0": (1, 1.0, "t"), "s0.0": (2, 0.0, "t"), "gaussian": (3, 0.5, "gaussian"), "ext_grid": (4, 0.5, "t")}
    mu_full = rets.mean().to_numpy() * td
    covt = rets.cov().to_numpy() * td
    pick = runs.drop_duplicates(["scenario", "T", "run"])[["scenario", "T", "run"]].sample(20, random_state=SEED_PICK)
    worst = {"plugin": 0.0, "oracle": 0.0, "equal": 0.0, "minvar": 0.0}
    cache = {}
    for _, p in pick.iterrows():
        si, s, dist = scen_idx[p.scenario]
        if si not in cache:
            mu_t = mu_full.mean() + s * (mu_full - mu_full.mean())
            w_o, s_o = maxsharpe_cc(mu_t, covt)
            cache[si] = (mu_t, w_o, s_o)
        mu_t, w_o, s_o = cache[si]
        ti = mc["T_list"].index(int(p["T"]))
        rg = np.random.default_rng(np.random.SeedSequence(mc["base_seed"], spawn_key=(si, ti, int(p.run))))
        chol = np.linalg.cholesky(covt / td)
        z = rg.standard_normal((int(p["T"]), len(mu_t))) @ chol.T
        if dist == "t":
            z = z * np.sqrt((mc["student_df"] - 2.0) / rg.chisquare(mc["student_df"], size=int(p["T"])))[:, None]
        r = z + mu_t / td
        w_p, _ = maxsharpe_cc(r.mean(axis=0) * td, np.cov(r, rowvar=False) * td)
        w_m, _ = minvar_slsqp(np.cov(r, rowvar=False) * td)

        def ts(w):
            return (mu_t @ w - RF) / np.sqrt(w @ covt @ w)
        sub = runs[(runs.scenario == p.scenario) & (runs["T"] == p["T"]) & (runs.run == p.run)].set_index("method")["true_sharpe"]
        worst["plugin"] = max(worst["plugin"], abs(ts(w_p) - sub["plugin_maxsharpe"]))
        worst["minvar"] = max(worst["minvar"], abs(ts(w_m) - sub["plugin_minvar"]))
        worst["oracle"] = max(worst["oracle"], abs(s_o - sub["oracle"]))
        worst["equal"] = max(worst["equal"], abs(ts(np.full(len(mu_t), 1 / len(mu_t))) - sub["equal_weight"]))
    chk("D3.3", "true Sharpe of plug-in max-Sharpe, plug-in min-variance, oracle and 1/N recomputed from re-simulated samples (20 random runs, stored child seeds)",
        worst["plugin"] <= TOL_SIM_PLUGIN and worst["minvar"] <= TOL_SIM_PLUGIN and worst["oracle"] <= TOL_ORACLE_RECOMP and worst["equal"] <= 1e-10, str({k: f"{v:.2e}" for k, v in worst.items()}))
    # D4 moments and degrees of freedom of a fresh draw
    mu_t = cache.get(0, (mu_full.mean() + 0.5 * (mu_full - mu_full.mean()),))[0]
    rg = np.random.default_rng(np.random.SeedSequence(SEED_MC_BOOT))
    n_b, per = 20, 100000
    chol = np.linalg.cholesky(covt / td)
    means, covs, d2 = [], [], []
    inv = np.linalg.inv(covt / td)
    for _ in range(n_b):
        z = rg.standard_normal((per, len(mu_t))) @ chol.T
        z = z * np.sqrt((mc["student_df"] - 2.0) / rg.chisquare(mc["student_df"], size=per))[:, None]
        x = z + mu_t / td
        means.append(x.mean(0))
        covs.append(np.cov(x, rowvar=False))
        if len(d2) < 3:
            c = x - mu_t / td
            d2.append(np.einsum("ij,jk,ik->i", c, inv, c))
    means, covs = np.array(means), np.array(covs)
    zmu = np.abs((means.mean(0) - mu_t / td) / (means.std(0, ddof=1) / np.sqrt(n_b))).max()
    zcov = np.abs((covs.mean(0) - covt / td) / (covs.std(0, ddof=1) / np.sqrt(n_b))).max()
    chk("D4.1", f"fresh Student-t(5) draw ({n_b * per:,} days): mean and covariance equal the truth within {TOL_MOMENT_Z:g} batch-means standard errors", max(zmu, zcov) < TOL_MOMENT_Z, f"max |z| mean {zmu:.2f}, covariance {zcov:.2f}")
    n = len(mu_t)
    f_stat = np.concatenate(d2) * mc["student_df"] / ((mc["student_df"] - 2.0) * n)
    ks = stats.kstest(f_stat, stats.f(n, mc["student_df"]).cdf)
    dfd = stats.f.fit(f_stat, f0=n, floc=0, fscale=1)[1]
    chk("D4.2", "Mahalanobis distance of the fresh draw follows F(40, 5): estimated degrees of freedom within 0.1 of 5, KS p-value above 0.001", abs(dfd - mc["student_df"]) <= TOL_DF and ks.pvalue > 1e-3,
        f"estimated df {dfd:.3f}; KS statistic {ks.statistic:.4f}, p {ks.pvalue:.3f} (n = {len(f_stat):,})")
    # D2 wording
    docs = {f: (ROOT / "docs" / f).read_text(encoding="utf-8") for f in ("hypotheses.md", "montecarlo_notes.md", "results_log.md")}
    hyp = docs["hypotheses.md"]
    got = [l.strip() for l in hyp.splitlines() if l.strip()]
    chk("D2.1", "docs/hypotheses.md equals the approved wording line for line (5 lines)", got == APPROVED_HYPOTHESES, f"{len(got)} lines; differing lines: {[i + 1 for i, (x, y) in enumerate(zip(got, APPROVED_HYPOTHESES)) if x != y]}")
    for f in ("montecarlo_notes.md", "results_log.md"):
        txt = docs[f]
        lines = txt.splitlines()
        h1l = [(i + 1, l.strip()[:140]) for i, l in enumerate(lines) if re.search(r"H1 verdict|H1 (is|was) (falsif|supported)|H1:.*(falsif|supported)", l, re.I)]
        h2l = [(i + 1, l.strip()[:140]) for i, l in enumerate(lines) if re.search(r"H2 verdict|H2 (is|was) not supported|H2:.*supported", l, re.I)]
        contra = [x for x in h1l if re.search(r"\bSUPPORTED\b", x[1]) and not re.search(r"NOT SUPPORTED|FALSIFIED|not supported|falsified", x[1])]
        chk(f"D2.{f}", f"{f}: H1/H2 verdict lines present and none contradicts the numbers", bool(h1l) and bool(h2l) and not contra, f"H1 lines {h1l[:4]}; H2 lines {h2l[:4]}; contradictions {contra}")


# =============================================================================================================== E
def circ_boot_stats(R, rng, b, block, chunk=250):
    T, M = R.shape
    nb = int(np.ceil(T / block))
    S, C, D = (np.empty((b, M)) for _ in range(3))
    k = int(np.floor(0.05 * T + 1e-12))
    frac = 0.05 * T - k
    for i in range(0, b, chunk):
        c = min(chunk, b - i)
        starts = rng.integers(0, T, size=(c, nb))
        idx = ((starts[:, :, None] + np.arange(block)) % T).reshape(c, -1)[:, :T]
        X = R[idx]
        S[i:i + c] = (X.mean(1) - RF / TD) / X.std(1, ddof=1) * np.sqrt(TD)
        part = -np.partition(-(-X), T - k - 1, axis=1)[:, -(k + 1):, :] if False else None
        loss = -X
        top = -np.sort(-loss, axis=1)[:, :k + 1, :]
        C[i:i + c] = (top[:, :k, :].sum(1) + frac * top[:, k, :]) / (0.05 * T)
        w = np.cumprod(1.0 + X, axis=1)
        pk = np.maximum(np.maximum.accumulate(w, axis=1), 1.0)
        D[i:i + c] = (w / pk - 1.0).min(1)
    return S, C, D


def ckpt(p, group):
    d = json.load(open(ROOT / "outputs" / "cache_bt" / "W504" / f"p{p:05d}_{group}.json"))
    return d


def section_e(args):
    print("--- E: backtest", flush=True)
    rets, prices, bench = load()
    dr = pd.read_csv(TABLES / "daily_returns_bt.csv", index_col=0, parse_dates=True)
    summ = rd("backtest_summary_bt").set_index("method")
    # E1
    diffs, rows = [], []
    for m in summ.index:
        if m not in dr.columns:
            rows.append({"method": m, "note": "no daily series"})
            continue
        mm = metrics(dr[m].to_numpy())
        for k, v in mm.items():
            d = abs(v - summ.loc[m, k])
            rows.append({"method": m, "metric": k, "own": v, "table": summ.loc[m, k], "abs_diff": d})
    e1 = pd.DataFrame(rows)
    TABLE_OUT["qa_metrics_recompute"] = e1
    bad = e1[e1.abs_diff > TOL_EXACT] if "abs_diff" in e1 else e1
    chk("E1.1", f"annualised return (arithmetic mean x 252 - the table's 'ann_return'; CAGR is a separate column), CAGR, vol, Sharpe, max drawdown, CVaR95, Calmar of every method recomputed from daily returns (> {TOL_EXACT:g} reported)",
        bad.empty, f"{e1.method.nunique()} methods x 7 metrics; largest diff {e1.abs_diff.max():.2e}; breaches: {bad[['method', 'metric', 'abs_diff']].head(6).values.tolist()}")
    missing = [m for m in summ.index if m not in dr.columns]
    info("E1.2", "table methods with no daily series", str(missing))
    chk("E1.3", "all methods share the same dates and no NaN", not dr.isna().any().any() and len(dr) == 2405 and str(dr.index[0].date()) == "2017-01-27", f"{len(dr)} days, {dr.index[0].date()} to {dr.index[-1].date()}, columns {dr.shape[1]}")
    # positions
    cb = rd("chosen_epsilon_over_time_bt")
    pos = cb["position"].astype(int).tolist()
    chk("E3.1", "first rebalance 2017-01-27 at row 505, 115 rebalances every 21 rows, evaluation starts that day", pos[0] == 505 and rets.index[505] == pd.Timestamp("2017-01-27") and len(pos) == 115 and set(np.diff(pos)) == {21}
        and dr.index[0] == rets.index[505], f"first {pos[0]} ({rets.index[pos[0]].date()}), n {len(pos)}, steps {sorted(set(np.diff(pos)))}")
    rn = rets.to_numpy()
    # E2 reconstruction
    wts = pd.read_csv(TABLES / "weights_drmsd_cv_bt.csv", index_col=0, parse_dates=True)
    basic = {p: ckpt(p, "basic") for p in pos}
    drc = {p: ckpt(p, "drcvar") for p in pos}
    drm = {p: ckpt(p, "drmsd") for p in pos}
    tg = {}
    tg["equal_weight"] = {p: np.full(rn.shape[1], 1 / rn.shape[1]) for p in pos}
    tg["drmsd_cv"] = {p: wts.loc[rets.index[p]].to_numpy(float) for p in pos}
    for name in ("plugin_maxsharpe", "plugin_minvar", "lw_maxsharpe", "michaud", "wc_box", "wc_ellipsoid", "cvar_plugin"):
        tg[name] = {p: (np.array(basic[p][name], float) if basic[p].get(name) is not None else None) for p in pos}
    idx_of = cb.set_index("position")
    for name, grp, col in (("drcvar_cv", drc, "drcvar_cv_idx"), ("drcvar_regime", drc, "drcvar_regime_idx"), ("drmsd_regime", drm, "drmsd_regime_idx"), ("drmsd_cv_ck", drm, "drmsd_cv_idx")):
        tg[name] = {p: np.array(grp[p][f"fix_{int(idx_of.loc[p, col]):02d}"], float) for p in pos}
    chk("E2.0", "stored DR-MSD CV weights file equals the checkpoint weights at the chosen epsilon index", max(np.abs(tg["drmsd_cv"][p] - tg["drmsd_cv_ck"][p]).max() for p in pos) <= 1e-9,
        f"max diff {max(np.abs(tg['drmsd_cv'][p] - tg['drmsd_cv_ck'][p]).max() for p in pos):.2e}")
    rec_rows = []
    for name, targets in tg.items():
        if name == "drmsd_cv_ck":
            continue
        net, turns = drift_engine(rn, pos, targets)
        col = dr[name].to_numpy()
        dmax = float(np.abs(net - col).max())
        avg_turn = float(turns[1:].mean())
        tot_cost = float(0.001 * turns.sum())
        rec_rows.append({"method": name, "max_abs_daily_diff": dmax, "turnover_own": avg_turn, "turnover_table": summ.loc[name, "avg_turnover"], "cost_own": tot_cost, "cost_table": summ.loc[name, "total_cost"]})
    rec = pd.DataFrame(rec_rows)
    TABLE_OUT["qa_net_reconstruction"] = rec
    chk("E2.1", "net daily returns rebuilt from stored weights (drifting weights, 10 bps x turnover on the rebalance day) equal the stored series",
        (rec.max_abs_daily_diff <= TOL_EXACT).all(), f"{len(rec)} methods; largest daily diff {rec.max_abs_daily_diff.max():.2e}; per method {rec.set_index('method').max_abs_daily_diff.round(14).to_dict()}")
    chk("E2.2", "average turnover (first rebalance excluded) and total cost equal the summary table", ((rec.turnover_own - rec.turnover_table).abs() <= TOL_EXACT).all() and ((rec.cost_own - rec.cost_table).abs() <= TOL_EXACT).all(),
        f"largest turnover diff {(rec.turnover_own - rec.turnover_table).abs().max():.2e}, cost diff {(rec.cost_own - rec.cost_table).abs().max():.2e}")
    # gross + cost identity for 3 named methods
    ident = []
    for name in ("plugin_maxsharpe", "equal_weight", "drmsd_cv"):
        net, turns = drift_engine(rn, pos, tg[name], cost=0.0)
        net_c, _ = drift_engine(rn, pos, tg[name], cost=0.001)
        ident.append(float(np.abs((net - net_c) - np.concatenate([[0]])[:0].sum() - 0).max()))
        cost_series = np.zeros(len(net))
        for i, p in enumerate(pos):
            cost_series[p - pos[0]] = 0.001 * turns[i]
        ident[-1] = float(np.abs(net_c - (net - cost_series)).max())
    chk("E2.3", "net = gross - cost on the rebalance day for plug-in max-Sharpe, 1/N and DR-MSD (CV)", max(ident) <= 1e-12, f"max |net - (gross - cost)| = {max(ident):.2e}")
    # independent plug-in weights from the window (E3) and rebuild of the plug-in path
    rng = np.random.default_rng(SEED_PICK + 1)
    stored_dates = set(np.random.default_rng(11).choice(len(pos), 5, replace=False).tolist())
    cand = [i for i in range(len(pos)) if i not in stored_dates]
    picks = sorted(rng.choice(cand, 10, replace=False).tolist())
    wr, lk = [], []
    for i in picks:
        p = pos[i]
        win = rn[p - 504:p]
        w_ok, _ = maxsharpe_cc(win.mean(0) * TD, np.cov(win, rowvar=False) * TD)
        win_leak = rn[p - 503:p + 1]
        w_lk, _ = maxsharpe_cc(win_leak.mean(0) * TD, np.cov(win_leak, rowvar=False) * TD)
        st = tg["plugin_maxsharpe"][p]
        wr.append(float(np.abs(w_ok - st).max()))
        lk.append(float(np.abs(w_lk - st).max()))
    chk("E3.2", "plug-in max-Sharpe weights at 10 random rebalance dates (not the script's 5) recomputed from strictly earlier data equal the stored weights",
        max(wr) <= TOL_SLSQP_W, f"dates {[str(rets.index[pos[i]].date()) for i in picks]}; max weight diff {max(wr):.2e} (project's own tolerance {TOL_REPRO_W:g}; different solver here)")
    chk("E3.3", "a window that includes the evaluation day would NOT reproduce the stored weights (the test can detect look-ahead)", min(lk) > max(wr) and max(lk) > 10 * max(wr), f"leaked-window diffs {np.round(lk, 4).tolist()} vs strict-window diffs {np.round(wr, 5).tolist()}")
    # rebuilt plug-in path from independent weights
    tg_ind = {}
    for p in pos:
        win = rn[p - 504:p]
        tg_ind[p], _ = maxsharpe_cc(win.mean(0) * TD, np.cov(win, rowvar=False) * TD)
    net_i, _ = drift_engine(rn, pos, tg_ind)
    m_i, m_s = metrics(net_i), metrics(dr["plugin_maxsharpe"].to_numpy())
    chk("E2.4", "plug-in max-Sharpe path rebuilt from independently solved weights: Sharpe equals the stored path", abs(m_i["sharpe"] - m_s["sharpe"]) <= TOL_RECON_SHARPE,
        f"own Sharpe {m_i['sharpe']:.5f}, stored {m_s['sharpe']:.5f}; max daily return diff {np.abs(net_i - dr['plugin_maxsharpe'].to_numpy()).max():.2e}")
    # HMM labels: structure
    sd = pd.read_csv(TABLES / "stress_level_daily_2reg.csv", index_col=0, parse_dates=True)
    first_s = sd["s_t"].first_valid_index()
    chk("E3.4", "stress probability s_t is first defined on 2017-01-25 and the first rebalance uses the previous day's label", str(first_s.date()) == "2017-01-25" and cb.loc[0, "date"] == "2017-01-27" and abs(cb.loc[0, "s_t"] - sd.loc[rets.index[504], "s_t"]) < 1e-12,
        f"first s_t {first_s.date()}; table s_t at first rebalance {cb.loc[0, 's_t']:.6f}, stress file at row 504 ({rets.index[504].date()}) {sd.loc[rets.index[504], 's_t']:.6f}")
    lab = sd["hmm2_stress"].reindex(dr.index)
    # E5 identities
    d0 = float(np.abs(dr["drmsd_eps0"] - dr["plugin_maxsharpe"]).max())
    floor_n = int(sum(1 for p in pos if drm[p]["S_hat"] < CFG["backtest"]["theta_floor_sharpe"]))
    chk("E5.1", "DR-MSD at epsilon = 0 equals plug-in max-Sharpe (daily series, largest difference)", d0 <= TOL_IDENT_SERIES, f"max |diff| = {d0:.2e}; windows where S_hat < floor (theta capped): {floor_n} of {len(pos)}; metrics Sharpe {summ.loc['drmsd_eps0', 'sharpe']:.6f} vs {summ.loc['plugin_maxsharpe', 'sharpe']:.6f}")
    plateau = [c for c in dr.columns if c.startswith("drcvar_fix_")][-1]
    dp = float(np.abs(dr[plateau] - dr["equal_weight"]).max())
    chk("E5.2", f"DR-CVaR at the plateau epsilon ({plateau}) equals 1/N (largest daily difference)", dp <= TOL_PLATEAU_SERIES, f"max |diff| = {dp:.2e}; correlation {np.corrcoef(dr[plateau], dr['equal_weight'])[0, 1]:.8f}")
    cv_share = float((cb["drcvar_cv_idx"] == 13).mean())
    d_cv = float(np.abs(dr["drcvar_cv"] - dr["equal_weight"]).max())
    pk1, tr1 = mdd_dates(dr["drcvar_cv"].to_numpy(), dr.index)
    pk2, tr2 = mdd_dates(dr["equal_weight"].to_numpy(), dr.index)
    m1, m2 = mdd(dr["drcvar_cv"].to_numpy()), mdd(dr["equal_weight"].to_numpy())
    seg = slice(dr.index.get_loc(pk2), dr.index.get_loc(tr2) + 1) if hasattr(pk2, "date") else slice(0, dr.index.get_loc(tr2) + 1)
    d_seg = float(np.abs(dr["drcvar_cv"].to_numpy()[seg] - dr["equal_weight"].to_numpy()[seg]).max())
    idx_by_date = cb.set_index("date")["drcvar_cv_idx"]
    eff = float(summ.loc["drcvar_cv", "avg_effective_n"])
    differ_days = int((np.abs(dr["drcvar_cv"] - dr["equal_weight"]) > 1e-9).sum())
    n_eq = 1.0 / rn.shape[1]
    exact_cvar = int(sum(np.abs(tg["drcvar_cv"][p_] - n_eq).max() < 1e-9 for p_ in pos))
    exact_msd = int(sum(np.abs(tg["drmsd_cv"][p_] - n_eq).max() < 1e-6 for p_ in pos))
    effn12 = float(np.mean([1 / np.sum(np.array(drc[p_]["fix_12"]) ** 2) for p_ in pos]))
    info("E5.3b", "how often the cross-validated DR portfolios are exactly (or nearly) 1/N", f"DR-CVaR (CV) weights equal 1/N to 1e-9 at {exact_cvar} of {len(pos)} rebalances; DR-MSD (CV) within 1e-6 of 1/N at {exact_msd}; mean effective N of DR-CVaR at epsilon index 12 (0.1): {effn12:.2f}")
    info("E5.3", "DR-CVaR (CV epsilon) maximum drawdown equals 1/N's: same episode or copy error?", f"max drawdown {m1:.6f} vs {m2:.6f} (diff {abs(m1 - m2):.2e}); peak/trough dates DR-CVaR {pk1.date() if hasattr(pk1, 'date') else pk1}/{tr1.date()}, 1/N {pk2.date() if hasattr(pk2, 'date') else pk2}/{tr2.date()}; "
         f"largest daily difference between the two series inside the peak-to-trough interval {d_seg:.2e}; over the whole sample {d_cv:.2e} ({differ_days} of {len(dr)} days differ by more than 1e-9); "
         f"epsilon indices used {sorted(cb['drcvar_cv_idx'].unique().tolist())}, counts {cb['drcvar_cv_idx'].value_counts().to_dict()}; average effective N of DR-CVaR (CV) {eff:.2f}; epsilon index on the rebalances inside the interval: {sorted(set(idx_by_date.loc[str(pk2.date()) if hasattr(pk2, 'date') else dr.index[0]:str(tr2.date())].tolist()))}")
    chk("E5.4", "DR-CVaR (CV) and 1/N have different daily series (not a copy) and the same worst episode", d_cv > 1e-6 and tr1 == tr2, f"max daily diff {d_cv:.2e}; trough {tr1.date()} vs {tr2.date()}")
    # E4 bootstrap
    cols = ["drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime", "plugin_maxsharpe", "equal_weight", "nifty_price_index"]
    R = dr[cols].to_numpy()
    S, C, D = circ_boot_stats(R, np.random.default_rng(SEED_BT_BOOT), 5000, 21)
    stat = {"sharpe": S, "cvar95_daily": C, "max_drawdown": D}
    point = {"sharpe": [metrics(R[:, j])["sharpe"] for j in range(len(cols))], "cvar95_daily": [metrics(R[:, j])["cvar95_daily"] for j in range(len(cols))], "max_drawdown": [metrics(R[:, j])["max_drawdown"] for j in range(len(cols))]}
    tolmap = {"sharpe": TOL_BOOT_BT_SHARPE, "cvar95_daily": TOL_BOOT_BT_CVAR, "max_drawdown": TOL_BOOT_BT_DD}
    bt = rd("backtest_bootstrap_bt")
    brow = []
    for _, t in bt.iterrows():
        j = cols.index(t.method)
        if t.versus == "(level)":
            est, draws = point[t.metric][j], stat[t.metric][:, j]
        else:
            k = cols.index(t.versus)
            est, draws = point[t.metric][j] - point[t.metric][k], stat[t.metric][:, j] - stat[t.metric][:, k]
        lo, hi = np.quantile(draws, [0.025, 0.975])
        ex = "yes" if (lo > 0 or hi < 0) else "no"
        brow.append({"method": t.method, "versus": t.versus, "metric": t.metric, "own_est": est, "table_est": t.estimate, "own_lo": lo, "own_hi": hi, "table_lo": t.ci_lo, "table_hi": t.ci_hi,
                     "own_excludes_zero": ex, "table_excludes_zero": t.excludes_zero if isinstance(t.excludes_zero, str) else "",
                     "est_ok": abs(est - t.estimate) <= TOL_EXACT, "endpoint_ok": abs(lo - t.ci_lo) <= tolmap[t.metric] and abs(hi - t.ci_hi) <= tolmap[t.metric],
                     "sign_zero_same": (t.versus == "(level)") or (ex == t.excludes_zero), "borderline": min(abs(lo), abs(hi)) < tolmap[t.metric] and t.versus != "(level)"})
    be = pd.DataFrame(brow)
    TABLE_OUT["qa_bootstrap_compare"] = be
    chk("E4.1", "own circular block bootstrap (block 21, 5000 resamples, new seed): point estimates equal the table", be.est_ok.all(), f"{len(be)} rows; largest point diff {(be.own_est - be.table_est).abs().max():.2e}")
    chk("E4.2", "interval endpoints within the Monte Carlo tolerances of the table", be.endpoint_ok.all(), f"{(~be.endpoint_ok).sum()} of {len(be)} outside; largest endpoint diffs: Sharpe {max((be[be.metric == 'sharpe'].own_lo - be[be.metric == 'sharpe'].table_lo).abs().max(), (be[be.metric == 'sharpe'].own_hi - be[be.metric == 'sharpe'].table_hi).abs().max()):.4f}, "
        f"CVaR {max((be[be.metric == 'cvar95_daily'].own_lo - be[be.metric == 'cvar95_daily'].table_lo).abs().max(), (be[be.metric == 'cvar95_daily'].own_hi - be[be.metric == 'cvar95_daily'].table_hi).abs().max()):.5f}, "
        f"drawdown {max((be[be.metric == 'max_drawdown'].own_lo - be[be.metric == 'max_drawdown'].table_lo).abs().max(), (be[be.metric == 'max_drawdown'].own_hi - be[be.metric == 'max_drawdown'].table_hi).abs().max()):.4f}")
    flips = be[~be.sign_zero_same]
    chk("E4.3", "no interval differs from the table in whether it contains zero", flips.empty, f"differences: {flips[['method', 'versus', 'metric', 'own_lo', 'own_hi', 'table_lo', 'table_hi']].values.tolist()}; borderline intervals (an endpoint within one tolerance of zero): {be[be.borderline][['method', 'versus', 'metric']].values.tolist()}")
    dd = be[be.versus != "(level)"]
    cv = dd[(dd.metric == "cvar95_daily") & dd.method.isin(["drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime"])]
    cl_ok = bool((cv.own_hi < 0).all())
    sh_all = dd[dd.metric == "sharpe"]
    sh = sh_all[~sh_all.method.eq("nifty_price_index")]
    sh_ok = bool(((sh.own_lo < 0) & (sh.own_hi > 0)).all())
    chk("E4.5", "claim (backtest_notes): every Sharpe-difference interval between a primary DR portfolio and plug-in max-Sharpe or 1/N includes zero (own bootstrap)", sh_ok,
        f"{len(sh)} DR/regime-aware comparisons; excluding zero (own): {sh[sh.own_excludes_zero == 'yes'][['method', 'versus']].values.tolist()}; table: {sh[sh.table_excludes_zero == 'yes'][['method', 'versus']].values.tolist()}")
    info("E4.5b", "Sharpe differences that exclude zero in the table: the NIFTY price index against plug-in and 1/N (a first version of E4.5 included these rows and failed; the notes' claim concerns the DR portfolios)",
         f"own: {sh_all[sh_all.own_excludes_zero == 'yes'][['method', 'versus', 'own_est', 'own_lo', 'own_hi']].round(3).values.tolist()}")
    dd_dd = dd[(dd.metric == "max_drawdown") & dd.method.isin(["drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime"])]
    chk("E4.7", "claim: no maximum-drawdown difference between a DR portfolio and plug-in / 1/N has an interval excluding zero (own bootstrap)", bool(((dd_dd.own_lo < 0) & (dd_dd.own_hi > 0)).all()),
        f"{len(dd_dd)} comparisons; excluding zero (own): {dd_dd[dd_dd.own_excludes_zero == 'yes'][['method', 'versus']].values.tolist()}; table: {dd_dd[dd_dd.table_excludes_zero == 'yes'][['method', 'versus']].values.tolist()}")
    # diagnostic for E4.2: a 50,000-resample reference shows whether endpoint differences are Monte Carlo noise
    S2, C2_, D2_ = circ_boot_stats(R, np.random.default_rng(SEED_BT_BOOT + 1), 50000, 21)
    ref = {"sharpe": S2, "cvar95_daily": C2_, "max_drawdown": D2_}
    dev_own, dev_tab = [], []
    for _, t in bt.iterrows():
        j = cols.index(t.method)
        if t.versus == "(level)":
            dr_ = ref[t.metric][:, j]
        else:
            k = cols.index(t.versus)
            dr_ = ref[t.metric][:, j] - ref[t.metric][:, k]
        lo, hi = np.quantile(dr_, [0.025, 0.975])
        r_ = be[(be.method == t.method) & (be.versus == t.versus) & (be.metric == t.metric)].iloc[0]
        dev_own += [(t.metric, abs(r_.own_lo - lo)), (t.metric, abs(r_.own_hi - hi))]
        dev_tab += [(t.metric, abs(t.ci_lo - lo)), (t.metric, abs(t.ci_hi - hi))]
    do, dt_ = pd.DataFrame(dev_own, columns=["m", "d"]), pd.DataFrame(dev_tab, columns=["m", "d"])
    info("E4.8", "diagnostic: endpoint deviation from a 50,000-resample reference (new seed): mean / max for my 5,000-resample run and for the stored table, by metric",
         "; ".join(f"{m}: mine {do[do.m == m].d.mean():.4f}/{do[do.m == m].d.max():.4f}, table {dt_[dt_.m == m].d.mean():.4f}/{dt_[dt_.m == m].d.max():.4f}" for m in ("sharpe", "cvar95_daily", "max_drawdown")))
    # h1_secondary
    h1s = rd("h1_secondary_bt")
    mism = []
    for _, t in h1s.iterrows():
        m = be[(be.method == t.method) & (be.versus == t.versus) & (be.metric == t.metric)]
        if m.empty:
            mism.append((t.method, t.versus, t.metric, "not in bootstrap table"))
        else:
            m = m.iloc[0]
            if abs(m.table_lo - t.ci_lo) > 1e-12 or abs(m.table_hi - t.ci_hi) > 1e-12 or m.own_excludes_zero != t.excludes_zero:
                mism.append((t.method, t.versus, t.metric, f"own [{m.own_lo:.5f},{m.own_hi:.5f}] stored [{t.ci_lo:.5f},{t.ci_hi:.5f}]"))
    chk("E4.6", "h1_secondary_bt.csv agrees with the bootstrap table and with the own bootstrap on whether zero is excluded", not mism, f"{len(h1s)} rows; disagreements {mism}")
    for _, t in h1s.iterrows():
        m = be[(be.method == t.method) & (be.versus == t.versus) & (be.metric == t.metric)]
        if len(m):
            m = m.iloc[0]
            num("E", f"{t.method} minus {t.versus}, {t.metric}: estimate", float(t.estimate), "daily loss units" if "cvar" in t.metric else "fraction" if "draw" in t.metric else "Sharpe units", "outputs/tables/h1_secondary_bt.csv", "yes")
            num("E", f"{t.method} minus {t.versus}, {t.metric}: 95% interval", f"[{t.ci_lo:.5f}, {t.ci_hi:.5f}]", "same", "outputs/tables/h1_secondary_bt.csv", f"yes (own [{m.own_lo:.5f}, {m.own_hi:.5f}])")
    # E6 stress windows
    sw = rd("stress_windows_bt")
    s_rows, s_bad = [], []
    windows = {k: tuple(map(pd.Timestamp, v)) for k, v in CFG["backtest"]["stress_windows"].items()}
    for wn, (a, b) in windows.items():
        sub = dr.loc[a:b]
        for m in sw[sw.window == wn].method.unique():
            r = sub[m].to_numpy()
            own = {"cumulative_return": float(np.prod(1 + r) - 1), "max_drawdown": mdd(r), "cvar95_daily": cvar_ru(r)}
            t = sw[(sw.window == wn) & (sw.method == m)].iloc[0]
            for k, v in own.items():
                d = abs(v - t[k])
                s_rows.append({"window": wn, "method": m, "metric": k, "own": v, "table": t[k], "abs_diff": d})
                if d > TOL_EXACT:
                    s_bad.append((wn, m, k, d))
    TABLE_OUT["qa_stress_recompute"] = pd.DataFrame(s_rows)
    chk("E6.1", "stress-window cumulative return, max drawdown, CVaR95 of every method recomputed", not s_bad, f"{len(s_rows)} values; breaches {s_bad[:5]}")
    pset = set(pos)
    cnt = {}
    for wn, (a, b) in windows.items():
        sub = dr.loc[a:b]
        cnt[wn] = {"days": len(sub), "rebalances": int(sum(1 for p in pos if a <= rets.index[p] <= b)), "hmm_stress_days": int((lab.loc[a:b] == "stress").sum())}
    info("E6.2", "rebalance dates and HMM stress days inside each stress window", str(cnt))
    chk("E6.3", "n_days in the stress table equals the number of evaluation days in the window", all(int(sw[sw.window == wn].n_days.iloc[0]) == cnt[wn]["days"] for wn in windows), str({wn: (int(sw[sw.window == wn].n_days.iloc[0]), cnt[wn]["days"]) for wn in windows}))
    for wn in windows:
        for m in ("equal_weight", "plugin_maxsharpe", "drmsd_cv", "drcvar_cv"):
            t = sw[(sw.window == wn) & (sw.method == m)].iloc[0]
            num("E", f"{wn} {m} cumulative return", float(t.cumulative_return), "fraction", "outputs/tables/stress_windows_bt.csv", "yes")
            num("E", f"{wn} {m} CVaR95", float(t.cvar95_daily), "daily loss", "outputs/tables/stress_windows_bt.csv", "yes")
    # E7 multiplicity
    tabs = {"backtest_bootstrap_bt": bt, "h1_secondary_bt": h1s}
    sens = rd("sensitivities_bt")
    cnts = []
    for nm, t in tabs.items():
        d = t[t.versus != "(level)"] if "versus" in t else t
        cnts.append({"table": nm, "comparisons": len(d), "exclude_zero": int((d.excludes_zero == "yes").sum()), "status": "H1 secondary (primary comparison)" if nm == "h1_secondary_bt" else "descriptive (not pre-specified as tests)"})
    for suf, a, b in (("vs_plugin", "ci_lo_vs_plugin", "ci_hi_vs_plugin"), ("vs_equal_weight", "ci_lo_vs_equal_weight", "ci_hi_vs_equal_weight")):
        d = sens[(sens[b] != 0) | (sens[a] != 0)]
        cnts.append({"table": f"sensitivities_bt ({suf})", "comparisons": len(d), "exclude_zero": int(((d[a] > 0) | (d[b] < 0)).sum()), "status": "sensitivity (exploratory)"})
    mult = pd.DataFrame(cnts)
    TABLE_OUT["qa_multiplicity"] = mult
    info("E7.1", "comparisons reported in the backtest tables and how many have an interval excluding zero", mult.to_string(index=False).replace("\n", " ; "))
    spec = (ROOT / "docs" / "backtest_spec.md").read_text(encoding="utf-8")
    chk("E7.2", "spec states no multiple-testing correction and lists primary versus exploratory", "No multiple-testing correction" in spec and "exploratory" in spec.lower(), "backtest_spec.md 'What counts as evidence'")
    # E8 evaluation start and lag
    prim = ["plugin_maxsharpe", "equal_weight", "drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime"]
    out = {}
    for k in (0, 1, 2):
        sub = dr.iloc[pos[k] - pos[0]:]
        out[f"start+{k}_rebalances"] = {m: metrics(sub[m].to_numpy())["sharpe"] for m in prim}
    pos_l = [p + 1 for p in pos]
    lagged = {}
    for m in prim:
        t_l = {p + 1: tg[m][p] for p in pos}
        net_l, _ = drift_engine(rn, pos_l, t_l)
        lagged[m] = metrics(net_l)["sharpe"]
    out["weights lagged one day"] = lagged
    base_rank = pd.Series(out["start+0_rebalances"]).rank(ascending=False).astype(int)
    rk = pd.DataFrame({k: pd.Series(v) for k, v in out.items()})
    rank_tab = rk.rank(ascending=False).astype(int)
    TABLE_OUT["qa_eval_sensitivity"] = pd.concat([rk.round(5), rank_tab.add_suffix("_rank")], axis=1).reset_index().rename(columns={"index": "method"})
    changed = [c for c in rank_tab.columns if not (rank_tab[c] == base_rank).all()]
    info("E8.1", "Sharpe of the primary methods: baseline, evaluation start shifted by one and two rebalances, weights lagged one day", rk.round(4).to_string().replace("\n", " ; ") + f" || rank order changes in: {changed}")
    for k in (0, 1, 2):
        pass
    # E9 epsilon positions
    ep = []
    for col in ("drcvar_cv_idx", "drmsd_cv_idx", "drcvar_regime_idx", "drmsd_regime_idx", "drcvar_1se_idx", "drmsd_1se_idx", "drmsd_extcv_idx", "drmsd_cvarblocks_idx"):
        v = cb[col]
        top = 17 if "ext" in col else 13
        ep.append({"series": col, "share_at_grid_max": float((v == top).mean()), "share_at_grid_min": float((v == 0).mean()), "distinct_values": int(v.nunique()), "median_index": float(v.median())})
    ept = pd.DataFrame(ep)
    TABLE_OUT["qa_eps_positions"] = ept
    info("E9.1", "epsilon index positions chosen at the 115 rebalances (index 13 = grid maximum 0.2154; extended-grid series use index 17 = 10)", ept.round(3).to_string(index=False).replace("\n", " ; "))
    for _, t in ept.iterrows():
        num("E", f"share of rebalances at the grid maximum: {t.series}", t.share_at_grid_max, "share", "outputs/tables/chosen_epsilon_over_time_bt.csv", "yes")
    if args.hmm:
        e3_hmm(rets, bench, sd, pos)
    if args.eps:
        e9_eps(rets, bench, sd, cb, pos)
    # headline numbers
    for m in ("equal_weight", "plugin_maxsharpe", "plugin_minvar", "drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime", "nifty_price_index"):
        for k in ("sharpe", "ann_return", "cagr", "ann_vol", "max_drawdown", "cvar95_daily", "calmar", "avg_turnover", "avg_effective_n", "total_cost"):
            ok = "yes" if k in ("sharpe", "ann_return", "cagr", "ann_vol", "max_drawdown", "cvar95_daily", "calmar") else ("yes" if k in ("avg_turnover", "total_cost") and m in rec.method.values else "no")
            num("E", f"{m} {k}", float(summ.loc[m, k]), {"sharpe": "ratio", "ann_return": "annualised (mean x 252)", "cagr": "annualised (geometric)", "ann_vol": "annualised", "max_drawdown": "fraction", "cvar95_daily": "daily loss", "calmar": "ratio",
                                                       "avg_turnover": "sum |dw| per rebalance", "avg_effective_n": "1/sum w^2", "total_cost": "fraction of wealth (sum of per-rebalance costs)"}[k], "outputs/tables/backtest_summary_bt.csv", ok)


def e3_hmm(rets, bench, sd, pos):
    """Uses the project's HMM code: checks the stored probabilities against a recomputation from truncated history (not an independent HMM)."""
    from src.stress import stress_level
    b = bench["simple"].loc[rets.index]
    res = []
    for p in (1000, 2000):
        s_tr = stress_level(b.iloc[:p], CFG)
        a, c = s_tr.dropna(), sd["s_t"].iloc[:p].dropna()
        d = float(np.abs(a.to_numpy() - c.reindex(a.index).to_numpy()).max())
        res.append((p, str(b.index[p - 1].date()), d))
    chk("E3.5", "HMM stress probabilities are filtered: recomputation from history truncated at two positions equals the stored values on the whole truncated range (project HMM code, fixed seed)", all(r[2] <= 1e-6 for r in res), f"(position, last date, max |diff|) {res}")


def e9_eps(rets, bench, sd, cb, pos):
    """Uses the project's eps_for_date_info on history strictly before five random rebalance dates (different from the script's)."""
    from src.eps_selection import eps_for_date_info, extension_grid
    grid = extension_grid(CFG["eps_selection_ext"])
    b = bench["simple"].loc[rets.index]
    lab = sd["hmm2_stress"]
    rng = np.random.default_rng(SEED_PICK + 2)
    picks = sorted(rng.choice(len(pos), 5, replace=False).tolist())
    res = []
    for i in picks:
        p = pos[i]
        tr, tb = rets.iloc[:p], b.iloc[:p]
        lab_p = lab.reindex(tr.index).fillna("unlabelled")
        t0 = time.time()
        info_ = eps_for_date_info(rets.index[p], "hmm2_stress", CFG, returns=tr, benchmark=tb, grid=grid, rule="best", label_fn=lambda bb, c: lab_p.reindex(bb.index))
        res.append((str(rets.index[p].date()), info_["eps"], float(cb.loc[i, "drcvar_regime_eps"]), round(time.time() - t0)))
    ok = all(abs(a - b_) <= 1e-12 + 1e-9 * abs(b_) for _, a, b_, _ in res)
    chk("E9.2", "DR-CVaR regime-aware epsilon on 5 random dates recomputed from history strictly before the date equals the stored schedule (project function)", ok, f"(date, recomputed, stored, seconds) {res}")


# =============================================================================================================== F / G
def section_f():
    print("--- F: documents and figures", flush=True)
    pool = []
    for f in TABLES.glob("*.csv"):
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        for c in df.select_dtypes("number").columns:
            pool.append(df[c].dropna().to_numpy(float))
    pool = np.unique(np.round(np.concatenate(pool), 10))
    cfg_nums = {float(x) for x in re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", (ROOT / "config.yaml").read_text(encoding="utf-8"))}

    def found(tok):
        v = float(tok)
        d = len(tok.split(".")[1].split("e")[0]) if "." in tok else 0
        cands = [v]
        for scale in (1.0, 100.0):
            x = v / scale
            lo, hi = x - 0.5 * 10 ** -(d + (2 if scale == 100.0 else 0)), x + 0.5 * 10 ** -(d + (2 if scale == 100.0 else 0))
            if scale == 100.0:
                lo, hi = (v - 0.5 * 10 ** -d) / 100.0, (v + 0.5 * 10 ** -d) / 100.0
            i = np.searchsorted(pool, lo - 1e-12)
            if i < len(pool) and pool[i] <= hi + 1e-12:
                return True
            j = np.searchsorted(pool, -hi - 1e-12)
            if j < len(pool) and pool[j] <= -lo + 1e-12:
                return True
        return any(abs(v - c) < 1e-9 for c in cfg_nums)
    rows = []
    for f in ("results_log.md", "limitations.md", "montecarlo_notes.md", "backtest_notes.md", "kkt_duality_notes.md", "game_theory_notes.md"):
        for i, line in enumerate((ROOT / "docs" / f).read_text(encoding="utf-8").splitlines(), 1):
            clean = re.sub(r"\d{4}-\d{2}-\d{2}", " ", line)
            for tok in re.findall(r"(?<![\w.])-?\d+\.\d{3,}(?![\w])", clean):
                rows.append({"file": f, "line": i, "token": tok, "traceable_to_a_table_or_config": found(tok), "text": line.strip()[:110]})
    # README
    rd_text = (ROOT / "README.md").read_text(encoding="utf-8")
    df = pd.DataFrame(rows)
    TABLE_OUT["qa_doc_numbers"] = df
    miss = df[~df.traceable_to_a_table_or_config]
    info("F1.1", "numbers with at least three decimals quoted in the six documents that match no value in any output table or config (rounded to the quoted digits, or x100 for percentages)",
         f"{len(df)} tokens scanned; {len(miss)} not traceable (list: outputs/tables/qa_doc_numbers.csv); by file {miss.groupby('file').size().to_dict()}")
    stale = "scaffold" in rd_text.lower()
    chk("F1.2", "README.md describes the current state of the project", not stale, "README.md still says the project is a scaffold with no analysis code" if stale else "ok")
    # supersession notes where results were rerun
    log = (ROOT / "docs" / "results_log.md").read_text(encoding="utf-8")
    sup = [l for l in log.splitlines() if "SUPERSEDED" in l or "supersed" in l.lower()]
    chk("F1.3", "results_log carries supersession notes (data pipeline, Markowitz, Monte Carlo reruns, backtest reruns)", sum(1 for s in sup if re.search(r"Markowitz|Data pipeline|montecarlo|Monte Carlo|MC|backtest|run", s, re.I)) >= 4, f"{len(sup)} lines mention supersession; first: {[s[:90] for s in sup[:6]]}")
    # F2 figures
    rows = []
    for p in sorted((ROOT / "outputs" / "figures").glob("*.png")):
        try:
            import matplotlib.image as mpimg
            im = mpimg.imread(p)
            ok, shape = True, im.shape
        except Exception as exc:
            ok, shape = False, str(exc)
        rows.append({"figure": p.name, "opens": ok, "shape": str(shape), "bytes": p.stat().st_size})
    figs = pd.DataFrame(rows)
    srcs = {}
    for s in (ROOT / "scripts").glob("*.py"):
        txt = s.read_text(encoding="utf-8")
        for fn in re.findall(r"([\w\-]+\.png)", txt):
            srcs.setdefault(fn, []).append(s.name)
    figs["scripts_mentioning"] = figs.figure.map(lambda n: ",".join(sorted(set(srcs.get(n, [])))))
    # static check: labels / legend / title / index wording in scripts that save figures
    flags = []
    for _, r in figs.iterrows():
        scr = [ROOT / "scripts" / s for s in r.scripts_mentioning.split(",") if s]
        src_txt = "\n".join(s.read_text(encoding="utf-8") for s in scr)
        pre = src_txt
        flags.append({"has_ylabel": "ylabel" in pre or "set_ylabel" in pre, "has_xlabel": "xlabel" in pre, "has_legend": "legend" in pre, "has_title": "title" in pre,
                      "index_wording_in_script": "excludes dividends" in pre})
    figs = pd.concat([figs, pd.DataFrame(flags)], axis=1)
    TABLE_OUT["qa_figures"] = figs
    chk("F2.1", "every figure file opens", figs.opens.all(), f"{len(figs)} figures; failing {figs[~figs.opens].figure.tolist()}")
    info("F2.2", "figures whose generating script has no xlabel / ylabel / legend / title call (static scan; each image still has to be inspected)", f"no xlabel: {figs[~figs.has_xlabel].figure.tolist()}; no legend: {figs[~figs.has_legend].figure.tolist()}; no title: {figs[~figs.has_title].figure.tolist()}; no script found: {figs[figs.scripts_mentioning == ''].figure.tolist()}")
    # F3 limitations coverage
    lim = (ROOT / "docs" / "limitations.md").read_text(encoding="utf-8").lower()
    topics = {"survivorship bias": ["survivorship"], "price index": ["price index"], "risk-free assumption": ["risk-free", "risk free"], "cost model": ["transaction cost", "10 bps", "cost model"],
              "inner-window 252 vs 504 mismatch": ["252", "504", "inner"], "few crisis episodes": ["crisis episodes", "few crises", "two crisis", "few episodes"], "HMM barely flags 2022": ["2022"],
              "theta calibrated on truth in MC / estimated in backtest": ["theta"], "truncated epsilon grid for DR-MSD": ["grid"], "bootstrap blocks exchangeable": ["exchangeable"],
              "post-hoc tolerance changes": ["tolerance", "changed"], "excluded stocks": ["adanient", "trent", "tmpv"], "unexamined data moves": ["sbin", "adaniports", "unexamined", "not examined"]}
    cover = {}
    for k, kws in topics.items():
        cover[k] = all(w in lim for w in kws) if k in ("inner-window 252 vs 504 mismatch", "excluded stocks") else any(w in lim for w in kws)
    missing = [k for k, v in cover.items() if not v]
    chk("F3.1", "limitations.md mentions every required topic (keyword scan; judged by hand in the report)", not missing, f"missing keywords for: {missing}")
    TABLE_OUT["qa_limitations_coverage"] = pd.DataFrame([{"topic": k, "keywords_present": v} for k, v in cover.items()])


def section_f_claims():
    print("--- F1b: specific claims in backtest_notes.md against the tables", flush=True)
    txt = (ROOT / "docs" / "backtest_notes.md").read_text(encoding="utf-8")
    summ = rd("backtest_summary_bt").set_index("method")
    cb = rd("chosen_epsilon_over_time_bt")
    bt = rd("backtest_bootstrap_bt")
    sw = rd("stress_windows_bt")
    sens = rd("sensitivities_bt").set_index("method")

    def cr(w, m, k):
        return float(sw[(sw.window == w) & (sw.method == m)][k].iloc[0])

    def est(m, v, k):
        return float(bt[(bt.method == m) & (bt.versus == v) & (bt.metric == k)].estimate.iloc[0])
    drs = ["drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime"]
    vp = [-est(m, "plugin_maxsharpe", "cvar95_daily") * 100 for m in drs]
    ve = [-est(m, "equal_weight", "cvar95_daily") * 100 for m in drs]
    n13, n11, n12 = int((cb.drmsd_cv_idx == 13).sum()), int((cb.drmsd_cv_idx == 11).sum()), int((cb.drcvar_cv_idx == 12).sum())
    sh = lambda m: summ.loc[m, "sharpe"]      # noqa: E731
    claims = [
        (f"1/N has a Sharpe ratio of {sh('equal_weight'):.3f}", "headline Sharpe 1/N"),
        (f"plug-in max-Sharpe {sh('plugin_maxsharpe'):.3f}", "headline Sharpe plug-in"),
        (f"DR-MSD with cross-validated epsilon {sh('drmsd_cv'):.3f}", "headline Sharpe DR-MSD"),
        (f"DR-CVaR with cross-validated epsilon {sh('drcvar_cv'):.3f}", "headline Sharpe DR-CVaR"),
        (f"regime-aware DR-CVaR {sh('drcvar_regime'):.3f}", "headline Sharpe regime-aware DR-CVaR"),
        (f"has {sh('nifty_price_index'):.3f}", "headline Sharpe index"),
        (f"picks the largest value (0.2154) at {n13} of 115 rebalances and the value two steps below it at {n11}", "DR-MSD epsilon positions"),
        (f"picks 0.1 at {n12} of 115", "DR-CVaR epsilon position"),
        (f"about {summ.loc['drcvar_cv', 'avg_effective_n']:.0f} effective positions", "DR-CVaR effective positions"),
        (f"(by {min(vp):.2f} to {max(vp):.2f} percentage points)", "CVaR95 gap to plug-in"),
        (f"(by {min(ve):.2f} to {max(ve):.2f} points)", "CVaR95 gap to 1/N"),
        (f"DR-MSD minus plug-in: {est('drmsd_cv', 'plugin_maxsharpe', 'sharpe'):+.3f}", "Sharpe difference DR-MSD minus plug-in"),
        (f"regime-aware DR-CVaR minus plug-in: {est('drcvar_regime', 'plugin_maxsharpe', 'sharpe'):+.3f}", "Sharpe difference regime-aware DR-CVaR minus plug-in"),
        (f"plug-in {cr('covid_2020', 'plugin_maxsharpe', 'cumulative_return') * 100:+.1f}%", "2020 plug-in"),
        (f"regime-aware DR-CVaR {cr('covid_2020', 'drcvar_regime', 'cumulative_return') * 100:+.1f}%", "2020 regime-aware DR-CVaR"),
        (f"DR-MSD {cr('covid_2020', 'drmsd_cv', 'cumulative_return') * 100:+.1f}%", "2020 DR-MSD"),
        (f"DR-CVaR {cr('covid_2020', 'drcvar_cv', 'cumulative_return') * 100:+.1f}%", "2020 DR-CVaR"),
        (f"1/N {cr('covid_2020', 'equal_weight', 'cumulative_return') * 100:+.1f}%", "2020 1/N"),
        (f"index {cr('covid_2020', 'nifty_price_index', 'cumulative_return') * 100:+.1f}%", "2020 index"),
        (f"plug-in {cr('year_2022', 'plugin_maxsharpe', 'cumulative_return') * 100:+.1f}%", "2022 plug-in"),
        (f"1/N {cr('year_2022', 'equal_weight', 'cumulative_return') * 100:+.1f}%", "2022 1/N"),
        (f"DR-CVaR {cr('year_2022', 'drcvar_cv', 'cumulative_return') * 100:+.1f}%", "2022 DR-CVaR"),
        (f"DR-MSD {cr('year_2022', 'drmsd_cv', 'cumulative_return') * 100:+.1f}%", "2022 DR-MSD"),
        (f"index {cr('year_2022', 'nifty_price_index', 'cumulative_return') * 100:+.1f}%", "2022 index"),
        (f"({cr('covid_2020', 'drcvar_regime', 'cvar95_daily') * 100:.1f}% against {cr('covid_2020', 'drcvar_cv', 'cvar95_daily') * 100:.1f}%)", "2020 CVaR95 regime-aware vs static"),
        (f"({sh('drcvar_regime'):.3f} against {sh('drcvar_cv'):.3f})", "Sharpe regime-aware DR-CVaR vs static"),
        (f"({sh('drmsd_regime'):.3f} against {sh('drmsd_cv'):.3f})", "Sharpe regime-aware DR-MSD vs static"),
        (f"Regime labels come from only {int((cb.label_hmm2 == 'stress').sum())} stress rebalances", "stress rebalances"),
        (f"(plug-in {sens.loc['w252_plugin_maxsharpe', 'sharpe']:.3f})", "window-252 plug-in Sharpe"),
        (f"reaches {sens.loc['drmsd_extcv', 'sharpe']:.3f}", "extended-grid DR-MSD Sharpe"),
        (f"(Sharpe {summ.loc['drcvar_eps0', 'sharpe']:.3f}", "DR-CVaR epsilon 0 Sharpe"),
        (f"Sharpe of {sh('drmsd_eps0'):.3f} is the same number", "DR-MSD epsilon 0 Sharpe"),
    ]
    bad = [(label, needle) for needle, label in claims if needle not in txt]
    chk("F1.4", "specific numeric claims in backtest_notes 'Plain-language reading', rebuilt from the tables, appear verbatim in the notes", not bad, f"{len(claims)} claims; not found: {bad}")
    same = abs(sh("drmsd_eps0") - sh("plugin_maxsharpe"))
    info("F1.5", "wording check: notes say DR-MSD at epsilon 0 'is exactly plug-in max-Sharpe ... the same number'", f"Sharpe {sh('drmsd_eps0'):.6f} vs {sh('plugin_maxsharpe'):.6f} (diff {same:.1e}); daily series differ by up to 3.9e-5 (different solvers): equal to three decimals, not exactly equal")


def section_g():
    print("--- G: game-theory numbers that can be reproduced cheaply", flush=True)
    rets = load()[0]
    mu, cov = rets.mean().to_numpy() * TD, rets.cov().to_numpy() * TD
    names = list(rets.columns)
    n = len(mu)
    wf = pd.read_csv(TABLES / "markowitz_max_sharpe_weights.csv").set_index("Ticker")["weight"].reindex(names).fillna(0.0).to_numpy()
    w_mv, _ = minvar_slsqp(cov)
    port = {"min_variance": w_mv, "max_sharpe": wf, "equal_weight": np.full(n, 1 / n)}
    eras = {"2015-2019": ("2015-01-01", "2019-12-31"), "2020-2022": ("2020-01-01", "2022-12-31"), "2023-end": ("2023-01-01", "2026-12-31")}
    logged = {"min_variance": [0.492, 0.836, 0.203], "max_sharpe": [1.035, 1.097, 0.935], "equal_weight": [0.550, 0.892, 0.719]}
    worst, rows = 0.0, []
    for k, w in port.items():
        r = rets @ w
        for (e, (a, b)), lv in zip(eras.items(), logged[k]):
            x = r.loc[a:b]
            sh = (x.mean() * TD - RF) / (x.std(ddof=1) * np.sqrt(TD))
            rows.append({"portfolio": k, "era": e, "own": sh, "logged": lv})
            worst = max(worst, abs(round(sh, 3) - lv))
            num("G", f"era Sharpe {k} {e}", float(sh), "ratio (in-sample for the era)", "docs/results_log.md (game entry); outputs/tables/payoff_matrices_game.csv", "yes")
    TABLE_OUT["qa_game_era_sharpe"] = pd.DataFrame(rows)
    chk("G.era", "era Sharpe matrix (min-variance, max-Sharpe, 1/N x three eras) recomputed from the weights and daily returns equals the logged matrix to three decimals", worst < 1e-9, f"largest rounded difference {worst:.4f}")
    x = rets @ port["equal_weight"]
    cv = cvar_ru(x.to_numpy())
    exp = {0.0: 0.023608, 0.0005: 0.023858, 0.001: 0.024108, 0.002: 0.024608, 0.005: 0.026108, 0.01: 0.028608}
    bad = {e: round(cv + e * 0.025 / 0.05, 6) for e, v in exp.items() if abs(round(cv + e * 0.025 / 0.05, 6) - v) > 1.5e-6}
    chk("G.wass", "worst-case CVaR of 1/N over the Wasserstein ball = empirical CVaR + eps ||w||_inf / 0.05 reproduces the logged upper bounds (six radii)", not bad, f"empirical CVaR {cv:.6f}; mismatches {bad}")
    num("G", "empirical daily CVaR95 of 1/N (in-sample)", float(cv), "daily loss", "docs/results_log.md (game entry)", "yes")
    t_years = len(rets) / TD
    lam = 5.0
    got = {}
    for kappa, v in ((0.0, 0.1641944258), (0.5, 0.1386045776), (1.0, 0.1135947339), (2.0, 0.0648439690)):
        best = -np.inf
        for w0 in (port["equal_weight"], wf):
            r = optimize.minimize(lambda w: -(mu @ w - kappa * np.sqrt(w @ cov @ w / t_years) - lam / 2 * w @ cov @ w), w0, bounds=[(0, CAP)] * n,
                                  constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}], method="SLSQP", options={"maxiter": 3000, "ftol": 1e-16})
            best = max(best, -r.fun)
        got[kappa] = (best, v)
        num("G", f"ellipsoid game saddle value, kappa {kappa}", float(best), "payoff units", "docs/results_log.md (game entry); outputs/tables/ellipsoid_saddle_game.csv", "yes")
    chk("G.ellipsoid", "ellipsoid-game saddle values for kappa 0, 0.5, 1, 2 recomputed by SLSQP equal the logged values to 1e-6", all(abs(a - b) < 1e-6 for a, b in got.values()), str({k: (round(a, 10), b) for k, (a, b) in got.items()}))


def write_numbers_md():
    df = pd.DataFrame(NUMBERS, columns=["section", "number", "value", "unit", "source", "reproduced"])
    TABLE_OUT["qa_numbers"] = df
    lines = ["# Numbers the report may cite", "",
             "Single list generated by `scripts/run_qa.py` (rerun it to refresh). Each number has its value, unit, source file and column or cell, and whether this audit reproduced it independently. "
             "`yes` = recomputed by independent code within the tolerances in `docs/qa_plan_qa.md`; `no` = taken from the source table and not recomputed here (these are marked in bold). "
             "Intervals marked 'new seed' are bootstrap intervals recomputed with a different seed; they agree within Monte Carlo error, not to the last digit.", ""]
    for sec, name in (("B", "Data facts"), ("C", "In-sample Markowitz and KKT multipliers"), ("D", "Monte Carlo (A8)"), ("E", "Backtest (A9)"), ("A", "Test suite")):
        d = df[df.section == sec]
        if d.empty:
            continue
        lines += [f"## {name}", "", "| Number | Value | Unit | Source | Reproduced |", "|---|---|---|---|---|"]
        for _, r in d.iterrows():
            v = r.value if isinstance(r.value, str) else (f"{r.value:.6g}" if isinstance(r.value, (float, np.floating)) else str(r.value))
            rep = r.reproduced if str(r.reproduced).startswith("yes") else f"**{r.reproduced}**"
            lines.append(f"| {r.number} | {v} | {r.unit} | `{r.source}` | {rep} |")
        lines.append("")
    extra = (ROOT / "docs" / "report_numbers_extra_qa.txt")
    if extra.exists():
        lines += extra.read_text(encoding="utf-8").splitlines()
    (ROOT / "docs" / "report_numbers_qa.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tests", action="store_true")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--hmm", action="store_true")
    ap.add_argument("--eps", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    t0 = time.time()
    steps = [("A", lambda: section_a(args)), ("B", section_b), ("C", section_c), ("D", section_d), ("E", lambda: section_e(args)), ("F", section_f), ("F1b", section_f_claims), ("G", section_g)]
    for name, fn in steps:
        if args.only and name not in args.only.split(","):
            continue
        try:
            fn()
        except Exception as exc:                       # an audit item that crashes is a failure, reported with its error
            import traceback
            traceback.print_exc()
            chk(f"{name}.crash", f"section {name} ran to completion", False, f"{type(exc).__name__}: {exc}")
    write_numbers_md()
    res = pd.DataFrame(RESULTS)
    TABLE_OUT["qa_items"] = res
    TABLES.mkdir(parents=True, exist_ok=True)
    if not args.only:
        for k, v in TABLE_OUT.items():
            v.to_csv(TABLES / f"{k}.csv", index=False)
    n_pass, n_fail, n_info = (res.status == "PASS").sum(), (res.status == "FAIL").sum(), (res.status == "INFO").sum()
    print(f"\nPASS {n_pass}  FAIL {n_fail}  INFO {n_info}  ({time.time() - t0:.0f} s)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
