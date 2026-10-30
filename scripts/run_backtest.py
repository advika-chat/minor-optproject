"""Rolling-window backtest on the real NIFTY 50 data (docs/backtest_spec.md).

Run from the repository root:  python -m scripts.run_backtest [--log]
Parameters: config.yaml section ``backtest``. Weights are checkpointed per (window, rebalance position, group) in backtest.checkpoint_dir, so an
interrupted run resumes. New files carry the suffix backtest.output_suffix. Exits with status 1 if any check fails.
"""
import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from joblib import Parallel, delayed  # noqa: E402

from src import backtest as bt  # noqa: E402
from src import solver_own as so  # noqa: E402
from src.eps_selection import POOLED, block_origins, compute_blocks, eps_for_date_info, extension_grid  # noqa: E402
from src.regimes import UNLABELLED  # noqa: E402
from src.robust import portfolio_cvar  # noqa: E402
from src.stress import CALM, STRESS, labels_hmm2_stress, labels_vol_two, soft_eps, stress_level  # noqa: E402

KINDS = ("cvar", "msd")
PREFIX = {"cvar": "drcvar", "msd": "drmsd"}
BASIC = ["plugin_maxsharpe", "plugin_minvar", "lw_maxsharpe", "michaud", "wc_box", "wc_ellipsoid", "cvar_plugin"]
LABELS = {"plugin_maxsharpe": "plug-in max-Sharpe", "plugin_minvar": "plug-in min-variance", "equal_weight": "1/N",
          "nifty_price_index": "NIFTY 50 price index (excludes dividends)", "lw_maxsharpe": "Ledoit-Wolf max-Sharpe",
          "michaud": "Michaud resampling", "wc_box": "worst-case box", "wc_ellipsoid": "worst-case ellipsoid",
          "cvar_plugin": "plug-in CVaR (LP)", "drcvar_cv": "DR-CVaR (CV epsilon)", "drmsd_cv": "DR-MSD (CV epsilon)",
          "drcvar_regime": "DR-CVaR regime-aware", "drmsd_regime": "DR-MSD regime-aware"}


# ---------------------------------------------------------------------------------------------- helpers
def update_notes_blocks(path, blocks):
    p = Path(path)
    if not p.exists():
        return []
    text, found = p.read_text(encoding="utf-8"), []
    for name, body in blocks.items():
        pat = re.compile(rf"(<!-- {name}:start -->\n).*?(\n<!-- {name}:end -->)", re.S)
        if pat.search(text):
            text = pat.sub(lambda m: m.group(1) + body + m.group(2), text)
            found.append(name)
    p.write_text(text, encoding="utf-8")
    return found


def md_table(df, digits=4):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            cells.append(f"{v:.{digits}f}" if isinstance(v, (float, np.floating)) and np.isfinite(v) else ("" if isinstance(v, float) else str(v)))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def name_of(kind, what):
    return f"{PREFIX[kind]}_{what}"


# ---------------------------------------------------------------------------------- labels and epsilon
def get_labels(cfg, bench, cache_dir):
    f = Path(cache_dir) / "labels.parquet"
    if f.exists():
        d = pd.read_parquet(f)
        if d.index.equals(bench.index):
            return d["hmm2_stress"], d["vol_two"], d["s_t"]
    s = stress_level(bench, cfg)
    d = pd.DataFrame({"hmm2_stress": labels_hmm2_stress(bench, cfg), "vol_two": labels_vol_two(bench, cfg), "s_t": s})
    f.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(f)
    return d["hmm2_stress"], d["vol_two"], d["s_t"]


def cache_net(cache_dir, name, fn):
    f = Path(cache_dir) / f"{name}.npy"
    if f.exists():
        return np.load(f)
    x = fn()
    f.parent.mkdir(parents=True, exist_ok=True)
    np.save(f, x)
    return x


def build_nets(cfg, rets, grid, extra, n_jobs):
    cache = cfg["backtest"]["checkpoint_dir"]
    if n_jobs == 1:
        net_c = cache_net(cache, "net_cvar", lambda: compute_blocks(rets, cfg, grid))
    else:
        net_c = cache_net(cache, "net_cvar", lambda: np.concatenate(
            Parallel(n_jobs=n_jobs)(delayed(compute_blocks)(rets, cfg, grid[i:i + 1]) for i in range(len(grid))), axis=0))
    net_m = cache_net(cache, "net_msd", lambda: bt.compute_blocks_msd(rets, cfg, grid, n_jobs))
    net_x = cache_net(cache, "net_msd_extra", lambda: bt.compute_blocks_msd(rets, cfg, np.asarray(extra), n_jobs)) if len(extra) else None
    return net_c, net_m, net_x


def build_schedules(cfg, rets, bench, lab_h, lab_v, grid, grid_ext, net_c, net_m, net_x):
    S = {}
    for rule in ("best", "one_se"):
        S[("cvar", rule, "hmm")] = bt.build_schedule(rets, bench, lab_h, cfg, net_c, grid, rule)
        S[("msd", rule, "hmm")] = bt.build_schedule(rets, bench, lab_h, cfg, net_m, grid, rule)
    S[("cvar", "best", "vol")] = bt.build_schedule(rets, bench, lab_v, cfg, net_c, grid, "best")
    S[("msd", "best", "vol")] = bt.build_schedule(rets, bench, lab_v, cfg, net_m, grid, "best")
    if net_x is not None:
        S[("msd_ext", "best", "hmm")] = bt.build_schedule(rets, bench, lab_h, cfg, np.concatenate([net_m, net_x], axis=0), grid_ext, "best")
    return S


def eps_table(cfg, S, positions, lab_h, lab_v, s_t, grid, grid_ext):
    """Per rebalance position: epsilon index / value for every rule (kind cvar and msd), regime labels, s_t and the soft epsilons."""
    rows = []
    for p in positions:
        lh, lv, st = lab_h.iloc[p - 1], lab_v.iloc[p - 1], float(s_t.iloc[p - 1])
        row = {"position": p, "label_hmm2": lh, "label_vol": lv, "s_t": st}
        for kind in KINDS:
            pre = PREFIX[kind]
            cv = bt.lookup_eps(S[(kind, "best", "hmm")], p, POOLED, grid)
            se = bt.lookup_eps(S[(kind, "one_se", "hmm")], p, POOLED, grid)
            rg = bt.lookup_eps(S[(kind, "best", "hmm")], p, lh, grid)
            rv = bt.lookup_eps(S[(kind, "best", "vol")], p, lv, grid)
            ec = bt.lookup_eps(S[(kind, "best", "hmm")], p, CALM, grid)["eps"]
            es = bt.lookup_eps(S[(kind, "best", "hmm")], p, STRESS, grid)["eps"]
            row.update({f"{pre}_cv_idx": cv["eps_index"], f"{pre}_cv_eps": cv["eps"], f"{pre}_1se_idx": se["eps_index"], f"{pre}_1se_eps": se["eps"],
                        f"{pre}_regime_idx": rg["eps_index"], f"{pre}_regime_eps": rg["eps"], f"{pre}_regime_src": rg["source"],
                        f"{pre}_regimevol_idx": rv["eps_index"], f"{pre}_regimevol_eps": rv["eps"],
                        f"{pre}_eps_calm": ec, f"{pre}_eps_stress": es,
                        f"{pre}_soft_eps": float(soft_eps(st, ec, es))})
        row["drmsd_cvarblocks_idx"] = row["drcvar_cv_idx"]
        if ("msd_ext", "best", "hmm") in S:
            ex = bt.lookup_eps(S[("msd_ext", "best", "hmm")], p, POOLED, grid_ext)
            row["drmsd_extcv_idx"], row["drmsd_extcv_eps"] = ex["eps_index"], ex["eps"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("position")


# ---------------------------------------------------------------------------------------- computation
def tasks_for(cfg, positions, windows, ept, grid, extra):
    tasks = []
    for W in windows:
        for p in positions:
            tasks.append((W, p, "basic", None))
            tasks.append((W, p, "drcvar", {"soft": float(ept.loc[p, "drcvar_soft_eps"])}))
            ex = {"soft": float(ept.loc[p, "drmsd_soft_eps"])}
            if W == cfg["backtest"]["window_days"]:
                ex.update({f"ext_{i:02d}": float(e) for i, e in enumerate(extra)})
            tasks.append((W, p, "drmsd", ex))
    return tasks


def execute(cfg, rets, tasks, grid, n_jobs):
    pending = [t for t in tasks if not bt.ckpt_file(cfg, t[0], t[1], t[2]).exists()]
    gen = (delayed(bt.worker)(rets, cfg, W, p, g, grid, ex) for W, p, g, ex in pending)
    if n_jobs == 1:
        for g in gen:
            g[0](*g[1], **g[2])
    else:
        done = 0
        for _ in Parallel(n_jobs=n_jobs, backend="loky", return_as="generator_unordered")(gen):
            done += 1
            if done % 50 == 0:
                bt.plog(cfg, f"weights: {done}/{len(pending)} tasks done")
    return len(pending)


def load_weights(cfg, window, positions):
    return {p: {g: bt.read_ckpt(bt.ckpt_file(cfg, window, p, g)) for g in ("basic", "drcvar", "drmsd")} for p in positions}


def method_targets(cfg, G, ept, positions, n_assets, only=None):
    """{method: {position: weights or None}} for one window. ``only`` restricts the method names."""
    nm = n_assets
    T = {}

    def put(name, fn):
        if only is None or name in only:
            T[name] = {p: fn(p) for p in positions}

    def val(x):
        return np.asarray(x, float) if isinstance(x, np.ndarray) else None

    put("equal_weight", lambda p: np.full(nm, 1.0 / nm))
    for b in BASIC:
        put(b, lambda p, b=b: val(G[p]["basic"].get(b)))
    for kind in KINDS:
        pre, g = PREFIX[kind], PREFIX[kind][2:]
        gk = lambda p, k=kind: G[p]["drcvar" if k == "cvar" else "drmsd"]
        wname = lambda i, ext=False: (f"fix_{i:02d}" if i < len(cfg_grid(cfg)) else f"ext_{i - len(cfg_grid(cfg)):02d}")
        put(f"{pre}_cv", lambda p, gk=gk, pre=pre: gk(p)[wname(int(ept.loc[p, f"{pre}_cv_idx"]))])
        put(f"{pre}_1se", lambda p, gk=gk, pre=pre: gk(p)[wname(int(ept.loc[p, f"{pre}_1se_idx"]))])
        put(f"{pre}_regime", lambda p, gk=gk, pre=pre: gk(p)[wname(int(ept.loc[p, f"{pre}_regime_idx"]))])
        put(f"{pre}_regimevol", lambda p, gk=gk, pre=pre: gk(p)[wname(int(ept.loc[p, f"{pre}_regimevol_idx"]))])
        put(f"{pre}_soft", lambda p, gk=gk: gk(p)["soft"])
        put(f"{pre}_eps0", lambda p, gk=gk: gk(p)["eps0"])
        for i in range(len(cfg_grid(cfg))):
            put(f"{pre}_fix_{i:02d}", lambda p, gk=gk, i=i: gk(p)[f"fix_{i:02d}"])
    if "drmsd_extcv_idx" in ept.columns:
        put("drmsd_extcv", lambda p: G[p]["drmsd"][f"fix_{int(ept.loc[p, 'drmsd_extcv_idx']):02d}"] if ept.loc[p, "drmsd_extcv_idx"] < len(cfg_grid(cfg))
            else G[p]["drmsd"][f"ext_{int(ept.loc[p, 'drmsd_extcv_idx']) - len(cfg_grid(cfg)):02d}"])
        for i in range(len(cfg["backtest"]["ext_grid_extra_eps"])):
            put(f"drmsd_extfix_{i:02d}", lambda p, i=i: G[p]["drmsd"][f"ext_{i:02d}"])
    put("drmsd_cvarblocks", lambda p: G[p]["drmsd"][f"fix_{int(ept.loc[p, 'drmsd_cvarblocks_idx']):02d}"])
    return T


def cfg_grid(cfg):
    return extension_grid(cfg["eps_selection_ext"])[::1]


def run_paths(cfg, rets, bench, positions, targets):
    """Simulate every method. Returns returns DataFrame, turnover/cost/effN DataFrames (per rebalance), weights, carried counts."""
    cost_rate = cfg["costs"]["transaction_cost_bps"] / 1e4
    r = rets.to_numpy()
    first = positions[0]
    idx = rets.index[first:]
    ret, turn, cost, effn, W, carried = {}, {}, {}, {}, {}, {}
    for name, tg in targets.items():
        net, t, c, tw, car = bt.simulate(tg, positions, r, cost_rate)
        ret[name], turn[name], cost[name] = net, t, c
        effn[name] = 1.0 / (tw ** 2).sum(axis=1)
        W[name], carried[name] = tw, int(car.sum())
    rd = rets.index[positions]
    return (pd.DataFrame(ret, index=idx), pd.DataFrame(turn, index=rd), pd.DataFrame(cost, index=rd), pd.DataFrame(effn, index=rd), W, carried)


# ------------------------------------------------------------------------------------------- tables
def summary_table(cfg, ret, turn, cost, effn, carried, names, group_of):
    rows = []
    for m in names:
        d = bt.perf_metrics(ret[m].to_numpy(), cfg)
        d.update({"method": m, "label": LABELS.get(m, m), "group": group_of(m)})
        if m in turn:
            d.update({"avg_turnover": float(turn[m].iloc[1:].mean()), "avg_effective_n": float(effn[m].mean()),
                      "total_cost": float(cost[m].sum()), "carried_forward_dates": carried.get(m, 0)})
        else:
            d.update({"avg_turnover": np.nan, "avg_effective_n": np.nan, "total_cost": 0.0, "carried_forward_dates": 0})
        rows.append(d)
    cols = ["method", "label", "group", "ann_return", "cagr", "ann_vol", "sharpe", "max_drawdown", "cvar95_daily", "calmar", "avg_turnover",
            "avg_effective_n", "total_cost", "carried_forward_dates"]
    return pd.DataFrame(rows)[cols]


def group_of(m):
    if m in ("equal_weight", "nifty_price_index"):
        return "benchmark"
    if m in BASIC:
        return "comparator"
    if m in ("drcvar_cv", "drmsd_cv", "drcvar_regime", "drmsd_regime"):
        return "primary"
    if "_fix_" in m or "_extfix_" in m:
        return "fixed_eps"
    return "ablation/sensitivity/exploratory"


def stress_table(cfg, ret, names):
    rows = []
    for wname, (d0, d1) in cfg["backtest"]["stress_windows"].items():
        sub = ret.loc[d0:d1]
        for m in names:
            x = sub[m].to_numpy()
            rows.append({"window": wname, "start": d0, "end": d1, "n_days": len(x), "method": m, "cumulative_return": float(np.prod(1 + x) - 1),
                         "max_drawdown": bt.max_drawdown(x), "cvar95_daily": bt.cvar_exact(x, cfg["risk"]["cvar_alpha"])})
    return pd.DataFrame(rows)


def bootstrap_table(cfg, ret, methods, baselines):
    cols = list(dict.fromkeys(methods + baselines))
    R = ret[cols].to_numpy()
    sh, cv, dd = bt.block_bootstrap(R, cfg)
    ix = {c: i for i, c in enumerate(cols)}
    pt = {c: bt.perf_metrics(ret[c].to_numpy(), cfg) for c in cols}
    rows = []
    for m in methods:
        lo, hi = bt.interval(sh[:, ix[m]], cfg)
        rows.append({"method": m, "versus": "(level)", "metric": "sharpe", "estimate": pt[m]["sharpe"], "ci_lo": lo, "ci_hi": hi, "excludes_zero": ""})
        for b in baselines:
            if b == m:
                continue
            for metric, arr, key in (("sharpe", sh, "sharpe"), ("cvar95_daily", cv, "cvar95_daily"), ("max_drawdown", dd, "max_drawdown")):
                d = arr[:, ix[m]] - arr[:, ix[b]]
                lo, hi = bt.interval(d, cfg)
                rows.append({"method": m, "versus": b, "metric": metric, "estimate": pt[m][key] - pt[b][key], "ci_lo": lo, "ci_hi": hi,
                             "excludes_zero": "yes" if lo > 0 or hi < 0 else "no"})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------- checks
def _lookahead_one(cfg, rets, bench, p, lab_h, recompute_labels, grid):
    """Recompute with every row from day p on replaced by zeros (so nothing at or after t can matter), keeping row p so that a refit falling exactly
    at p is visible: plug-in weights, DR-MSD at a fixed epsilon, and the regime-aware epsilon for both kinds (DR-CVaR via ``eps_for_date_info``)."""
    tr, tb = rets.iloc[:p + 1].copy(), bench.iloc[:p + 1].copy()
    tr.iloc[p:], tb.iloc[p:] = 0.0, 0.0
    win = tr.iloc[p - cfg["backtest"]["window_days"]:p]
    w_plug, _ = bt.plugin_maxsharpe(win, cfg)
    theta, _ = bt.theta_hat(win, cfg)
    w_msd = bt.msd_weights(win, [grid[6]], theta, cfg)[0]
    if recompute_labels:
        lab_t = labels_hmm2_stress(bench.iloc[:p], cfg)
        same_labels = bool((lab_t == lab_h.iloc[:p]).all())
    else:
        lab_t, same_labels = lab_h.iloc[:p], None
    lab_pad = lab_t.reindex(tr.index, fill_value=UNLABELLED)
    fn = lambda b, c: lab_pad.reindex(b.index)
    info = eps_for_date_info(rets.index[p], "hmm2_stress", cfg, returns=tr, benchmark=tb, grid=grid, rule="best", label_fn=fn)
    net_m = bt.compute_blocks_msd(tr, cfg, grid, 1)
    sched = bt.build_schedule(tr, tb, lab_pad, cfg, net_m, grid, "best")
    msd = bt.lookup_eps(sched, p, lab_pad.iloc[p - 1], grid)
    return {"p": p, "w_plug": w_plug, "w_msd": w_msd, "eps_cvar": info["eps"], "eps_msd": msd["eps"], "same_labels": same_labels}


def sanity_checks(cfg, rets, bench, positions, G, ept, targets, ret, turn, cost, effn, lab_h, grid, n_jobs, label_recompute_ok=True):
    ck, bc = cfg["backtest"]["checks"], cfg["backtest"]
    cap, td = cfg["portfolio"]["weight_cap"], cfg["trading_days"]
    out = []

    def add(name, ok, detail):
        out.append((name, bool(ok), detail))

    # weights sum to 1 and respect the cap, every date, every method (carried-forward dates keep the previous weights)
    worst_sum, worst_cap, nbad_nan = 0.0, 0.0, 0
    for name, tg in targets.items():
        for p, w in tg.items():
            if w is None:
                continue
            if not np.all(np.isfinite(w)):
                nbad_nan += 1
                continue
            worst_sum = max(worst_sum, abs(w.sum() - 1.0))
            worst_cap = max(worst_cap, float(w.max() - cap), float(-w.min()))
    add("weights sum to 1 and respect the cap on every date for every method", worst_sum <= ck["weight_sum_tol"] and worst_cap <= ck["cap_tol"] and nbad_nan == 0,
        f"max |sum-1| {worst_sum:.2e}, max cap/sign violation {worst_cap:.2e}, non-finite vectors {nbad_nan}, methods {len(targets)}")

    # no look-ahead
    rng = np.random.default_rng(ck["lookahead_seed"])
    pick = sorted(rng.choice(positions, size=min(ck["lookahead_dates"], len(positions)), replace=False).tolist())
    n_lab = ck["lookahead_labels_recompute_dates"] if label_recompute_ok else 0
    res = Parallel(n_jobs=min(n_jobs, len(pick)))(delayed(_lookahead_one)(cfg, rets, bench, p, lab_h, i < n_lab, grid) for i, p in enumerate(pick))
    worst_w, eps_ok, lab_ok = 0.0, True, True
    for r in res:
        p = r["p"]
        worst_w = max(worst_w, float(np.abs(r["w_plug"] - G[p]["basic"]["plugin_maxsharpe"]).max()), float(np.abs(r["w_msd"] - G[p]["drmsd"]["fix_06"]).max()))
        eps_ok &= abs(r["eps_cvar"] - ept.loc[p, "drcvar_regime_eps"]) < 1e-15 and abs(r["eps_msd"] - ept.loc[p, "drmsd_regime_eps"]) < 1e-15
        if r["same_labels"] is not None:
            lab_ok &= r["same_labels"]
    add("no look-ahead: weights and regime-aware epsilon recomputed with all data from day t on replaced by zeros equal the stored ones",
        worst_w <= ck["lookahead_weight_tol"] and eps_ok and lab_ok,
        f"{len(pick)} dates (rows {pick}); max |dw| {worst_w:.2e} (plug-in max-Sharpe and DR-MSD at a fixed epsilon); epsilon equal for DR-CVaR "
        f"(via eps_for_date_info) and DR-MSD: {eps_ok}; HMM labels recomputed from truncated data identical on {n_lab} date(s): {lab_ok}")

    # cost accounting and turnover reconciliation
    r_np = rets.to_numpy()
    worst_c, worst_t = 0.0, 0.0
    for name in ("plugin_maxsharpe", "drmsd_cv", "drcvar_regime", "equal_weight"):
        if name not in targets:
            continue
        gross, _, _, tw, _ = bt.simulate(targets[name], positions, r_np, 0.0)
        net = ret[name].to_numpy()
        diff = gross - net
        expected = np.zeros_like(diff)
        for i, p in enumerate(positions):
            expected[p - positions[0]] = cost[name].iloc[i]
        worst_c = max(worst_c, float(np.abs(diff - expected).max()))
        wd = np.zeros(r_np.shape[1])
        for i, p in enumerate(positions):
            t_ind = float(np.abs(tw[i] - wd).sum())
            worst_t = max(worst_t, abs(t_ind - turn[name].iloc[i]), abs(cost[name].iloc[i] - cfg["costs"]["transaction_cost_bps"] / 1e4 * t_ind))
            end = positions[i + 1] if i + 1 < len(positions) else len(r_np)
            g = np.prod(1.0 + r_np[p:end], axis=0)
            wd = tw[i] * g / (tw[i] @ g)
    add("cost accounting: net = gross - cost x turnover on rebalance days; turnover reconciles with weight changes",
        worst_c <= ck["cost_tol"] and worst_t <= ck["turnover_tol"], f"max net/gross/cost mismatch {worst_c:.2e}, max turnover/cost mismatch {worst_t:.2e}")

    # epsilon = 0 consistency
    n_chk = min(5, len(positions))
    cv_gap, qp_gap = 0.0, 0.0
    for p in positions[:: max(len(positions) // n_chk, 1)][:n_chk]:
        win = rets.iloc[p - bc["window_days"]:p]
        cv_gap = max(cv_gap, abs(portfolio_cvar(G[p]["drcvar"]["eps0"], win, cfg["risk"]["cvar_alpha"]) - portfolio_cvar(G[p]["basic"]["cvar_plugin"], win, cfg["risk"]["cvar_alpha"])))
        mu, cov = bt.window_stats(win, cfg)
        w0 = G[p]["drmsd"]["eps0"]
        qp = so.solve_qp(cov, mu, cap, cfg["solver_own"], target=float(mu @ w0))
        qp_gap = max(qp_gap, float(np.abs(w0 - qp.weights).max()))
    add("epsilon = 0: DR-CVaR attains the plug-in CVaR LP's in-window CVaR; DR-MSD equals its QP form (own active-set solver at the same return)",
        cv_gap <= ck["eps0_cvar_value_tol"] and qp_gap <= ck["eps0_msd_weight_tol"],
        f"{n_chk} dates; max |CVaR difference| {cv_gap:.2e} (daily loss units; LP minimisers need not be unique, so values are compared); max DR-MSD |dw| {qp_gap:.2e}. "
        f"Relation: DR-CVaR at eps=0 (mean weight 0) is the empirical CVaR minimiser; DR-MSD at eps=0 minimises sqrt(w'Sigma_n w) - theta(mu_n'w - rf/252), whose solution is the minimum-variance portfolio at its own return")
    ratios = np.array([1.0 / (G[p]["drcvar"][f"fix_{len(grid) - 1:02d}"] ** 2).sum() / rets.shape[1] for p in positions])
    ratios_msd = np.array([1.0 / (G[p]["drmsd"][f"fix_{len(grid) - 1:02d}"] ** 2).sum() / rets.shape[1] for p in positions])
    tol = cfg["eps_selection_ext"]["plateau_tolerance"]
    add("DR-CVaR at the plateau epsilon (largest grid value) is 1/N up to the plateau tolerance", ratios.min() >= 1 - tol,
        f"smallest effective-N ratio over {len(positions)} windows {ratios.min():.4f} (tolerance {1 - tol:.2f}); for information, DR-MSD at that epsilon: smallest ratio {ratios_msd.min():.4f}, mean {ratios_msd.mean():.4f}")
    t1n = float(turn["equal_weight"].iloc[1:].mean())
    add("1/N turnover is small and positive (drift only)", 0 < t1n <= ck["one_over_n_turnover_max"], f"mean turnover per rebalance (first excluded) {t1n:.4f}")

    # reproducibility of three stored rebalance dates
    rep_pos = positions[:: max(len(positions) // ck["repro_dates"], 1)][:ck["repro_dates"]]
    worst_r = 0.0
    for p in rep_pos:
        win = rets.iloc[p - bc["window_days"]:p]
        again = bt.basic_group(win, cfg)
        worst_r = max(worst_r, float(np.abs(again["plugin_maxsharpe"] - G[p]["basic"]["plugin_maxsharpe"]).max()),
                      float(np.abs(again["wc_ellipsoid"] - G[p]["basic"]["wc_ellipsoid"]).max()))
        theta, _ = bt.theta_hat(win, cfg)
        w = bt.msd_weights(win, [grid[3], grid[9]], theta, cfg)
        worst_r = max(worst_r, float(np.abs(w[0] - G[p]["drmsd"]["fix_03"]).max()), float(np.abs(w[1] - G[p]["drmsd"]["fix_09"]).max()))
        wc = bt.drcvar_weights_list(win, [grid[5]], cfg)[0]
        worst_r = max(worst_r, float(np.abs(wc - G[p]["drcvar"]["fix_05"]).max()))
    add("reproducibility: recomputing stored rebalance dates gives the same weights", worst_r <= ck["repro_tol"], f"{len(rep_pos)} dates (rows {rep_pos}); max |dw| {worst_r:.2e}")

    # paired series
    idx_ok = all(ret[c].index.equals(ret.index) for c in ret.columns) and not ret.isna().any().any()
    pos_ok = all(list(turn[c].index) == list(turn.index) for c in turn.columns)
    add("paired series: same dates and no missing values for all methods", idx_ok and pos_ok, f"{ret.shape[1]} methods x {ret.shape[0]} days; {len(positions)} rebalances")
    return out


# ---------------------------------------------------------------------------------------------- driver
def project(cfg, rets, n_jobs, positions, pilot_secs, n_blocks):
    """Projected wall minutes: weight computation for two windows plus the validation blocks, from per-date seconds and a block timing."""
    grid = extension_grid(cfg["eps_selection_ext"])
    sub = rets.iloc[:cfg["backtest"]["rolling_window_days"] + 5 * cfg["eps_selection"]["block_length"] + 1]
    t0 = time.perf_counter()
    compute_blocks(sub, cfg, grid[:1])
    nb = len(block_origins(len(sub), cfg))
    t_c = (time.perf_counter() - t0) / nb
    t0 = time.perf_counter()
    bt.compute_blocks_msd(sub, cfg, grid[:1], 1)
    t_m = (time.perf_counter() - t0) / nb
    blocks_cpu = n_blocks * (len(grid) * t_c + (len(grid) + len(cfg["backtest"]["ext_grid_extra_eps"])) * t_m * 0.5 + t_m)
    weights_cpu = pilot_secs * len(positions) * 2
    return (blocks_cpu + weights_cpu) / n_jobs / 60.0, t_c, t_m


def run_all(cfg, rets, bench, labels=None, n_jobs=None, pilot=True, label_recompute_ok=True):
    bc = cfg["backtest"]
    n_jobs = n_jobs or bc["n_jobs"]
    lines, failures, timing, cuts = [], [], {}, []
    t_start = time.perf_counter()
    grid = extension_grid(cfg["eps_selection_ext"])
    extra = list(bc["ext_grid_extra_eps"])
    grid_ext = np.concatenate([grid, extra])
    lab_h, lab_v, s_t = labels if labels is not None else get_labels(cfg, bench, bc["checkpoint_dir"])
    first = bt.eval_start(cfg, lab_h)
    positions = bt.rebalance_positions(len(rets), first, bc["rebalance_every_days"])
    lines.append(f"- Common evaluation start: row {first} = {rets.index[first].date()} (first rebalance at which the {bc['window_days']}-day window and the HMM stress label exist); "
                 f"{len(positions)} rebalances every {bc['rebalance_every_days']} days, {len(rets) - first} evaluation days to {rets.index[-1].date()}; universe {rets.shape[1]} stocks.")
    bt.plog(cfg, f"start: first={first}, {len(positions)} rebalances, n_jobs={n_jobs}")

    # pilot: first dates, all groups, window 504, with soft epsilons set to a mid-grid value (timing only)
    if pilot:
        pil_pos = positions[:bc["pilot_dates"]]
        cfg_p = {**cfg, "backtest": {**bc, "checkpoint_dir": str(Path(bc["checkpoint_dir"]) / "pilot")}}   # pilot weights are never reused
        secs = []
        for p in pil_pos:
            ts = time.perf_counter()
            for g, ex in (("basic", None), ("drcvar", {"soft": float(grid[6])}), ("drmsd", {"soft": float(grid[6]), **{f"ext_{i:02d}": float(e) for i, e in enumerate(extra)}})):
                bt.worker(rets, cfg_p, bc["window_days"], p, g, grid, ex)
            secs.append(time.perf_counter() - ts)
            bt.plog(cfg, f"pilot position {p}: {secs[-1]:.1f} s")
        n_blocks = len(range(bc["rolling_window_days"], len(rets) - cfg["eps_selection"]["block_length"] + 1, cfg["eps_selection"]["block_length"]))
        est, t_c, t_m = project(cfg, rets, n_jobs, positions, float(np.mean(secs)), n_blocks)
        timing.update(pilot_seconds_per_date=[round(s, 1) for s in secs], projected_minutes=est)
        lines.append(f"- Pilot ({len(pil_pos)} dates, all methods, window {bc['window_days']}): {', '.join(f'{s:.1f} s' for s in secs)} per date (serial); "
                     f"projected total {est:.1f} min on {n_jobs} workers (budget {bc['time_budget_minutes']} min).")
        if est > bc["time_budget_minutes"]:
            failures.append(f"pilot projects {est:.0f} min > budget {bc['time_budget_minutes']}: apply the cuts listed in the spec and re-run")
            return {"lines": lines, "failures": failures, "timing": timing}

    # epsilon selection (validation blocks and schedules)
    t0 = time.perf_counter()
    net_c, net_m, net_x = build_nets(cfg, rets, grid, extra, n_jobs)
    S = build_schedules(cfg, rets, bench, lab_h, lab_v, grid, grid_ext, net_c, net_m, net_x)
    ept = eps_table(cfg, S, positions, lab_h, lab_v, s_t, grid, grid_ext)
    timing["eps_selection_s"] = time.perf_counter() - t0
    bt.plog(cfg, f"epsilon schedules built in {timing['eps_selection_s']:.0f} s")

    windows = [bc["window_days"], bc["sens_window_days"]]
    tasks = tasks_for(cfg, positions, windows, ept, grid, extra)
    t0 = time.perf_counter()
    n_new = execute(cfg, rets, tasks, grid, n_jobs)
    timing["weights_s"] = time.perf_counter() - t0
    bt.plog(cfg, f"weights done: {n_new} tasks computed in {timing['weights_s']:.0f} s")

    G4, G2 = load_weights(cfg, windows[0], positions), load_weights(cfg, windows[1], positions)
    n_assets = rets.shape[1]
    tg4 = method_targets(cfg, G4, ept, positions, n_assets)
    tg2 = method_targets(cfg, G2, ept, positions, n_assets, only=set(bc["window_sens_methods"]) - {"equal_weight"})
    tg2 = {f"w{windows[1]}_{k}": v for k, v in tg2.items() if k in bc["window_sens_methods"]}
    targets = {**tg4, **tg2}
    ret, turn, cost, effn, W, carried = run_paths(cfg, rets, bench, positions, targets)
    ret["nifty_price_index"] = bench.to_numpy()[first:]
    methods_main = [m for m in ret.columns if not m.startswith(f"w{windows[1]}_")]
    lines.append(f"- Carried-forward dates (failed solves): {({k: v for k, v in carried.items() if v}) or 'none'}.")

    # tables
    tabs = {}
    summary_names = [m for m in methods_main if "_fix_" not in m and "_extfix_" not in m] + [m for m in ret.columns if m.startswith(f"w{windows[1]}_")]
    tabs["backtest_summary"] = summary_table(cfg, ret, turn, cost, effn, carried, summary_names, group_of)
    primary_dr = ["drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime"]
    boot_methods = primary_dr
    baselines = ["plugin_maxsharpe", "equal_weight"]
    tabs["backtest_bootstrap"] = bootstrap_table(cfg, ret, boot_methods + ["plugin_maxsharpe", "equal_weight", "nifty_price_index"], baselines)
    b = tabs["backtest_bootstrap"]
    tabs["h1_secondary"] = b[(b.method.isin(["drmsd_cv", "drcvar_cv"])) & (b.versus == "plugin_maxsharpe") & (b.metric.isin(["cvar95_daily", "max_drawdown"]))].reset_index(drop=True)
    st_names = [m for m in summary_names if not m.startswith(f"w{windows[1]}_")]
    tabs["stress_windows"] = stress_table(cfg, ret, st_names)

    por = []
    fixed_hind = {}
    for kind in KINDS:
        pre = PREFIX[kind]
        n_fix = len(grid)
        names = [f"{pre}_fix_{i:02d}" for i in range(n_fix)] + (["drmsd_extfix_%02d" % i for i in range(len(extra))] if kind == "msd" else [])
        epss = list(grid) + (extra if kind == "msd" else [])
        for nm_, e in zip(names, epss):
            d = bt.perf_metrics(ret[nm_].to_numpy(), cfg)
            por.append({"kind": kind, "method": nm_, "eps": float(e), "in_primary_grid": nm_.count("_fix_") == 1, "sharpe": d["sharpe"], "max_drawdown": d["max_drawdown"],
                        "cvar95_daily": d["cvar95_daily"], "avg_turnover": float(turn[nm_].iloc[1:].mean()), "avg_effective_n": float(effn[nm_].mean())})
        sub = [r for r in por if r["kind"] == kind and r["in_primary_grid"]]
        fixed_hind[kind] = max(sub, key=lambda r: (r["sharpe"], -r["eps"]))["method"]
    tabs["price_of_robustness"] = pd.DataFrame(por)
    tabs["price_of_robustness"]["hindsight_best_sharpe_in_primary_grid"] = tabs["price_of_robustness"].method.isin(fixed_hind.values())

    rx = []
    for kind in KINDS:
        pre = PREFIX[kind]
        for label, m in (("regime-aware (hmm2_stress)", f"{pre}_regime"), ("regime-aware (vol_two)", f"{pre}_regimevol"), ("soft eps_t (exploratory)", f"{pre}_soft"),
                         ("static, cross-validated epsilon", f"{pre}_cv"), ("static, hindsight best Sharpe epsilon (uses future data)", fixed_hind[kind])):
            row = {"kind": kind, "version": label, "method": m, "sharpe": bt.perf_metrics(ret[m].to_numpy(), cfg)["sharpe"]}
            for wname, (d0, d1) in bc["stress_windows"].items():
                x = ret.loc[d0:d1, m].to_numpy()
                row[f"cvar95_{wname}"] = bt.cvar_exact(x, cfg["risk"]["cvar_alpha"])
                row[f"cum_return_{wname}"] = float(np.prod(1 + x) - 1)
            rx.append(row)
    tabs["regime_exploratory"] = pd.DataFrame(rx)

    # sensitivities: each its own labelled block, with paired Sharpe differences against plug-in max-Sharpe and 1/N
    sens_sets = {"window_252": [f"w{windows[1]}_{m}" for m in bc["window_sens_methods"]],
                 "one_se_rule": ["drcvar_1se", "drmsd_1se"], "vol_two_labels": ["drcvar_regimevol", "drmsd_regimevol"],
                 "drmsd_eps_from_cvar_blocks": ["drmsd_cvarblocks"], "drmsd_extended_grid": ["drmsd_extcv"] if "drmsd_extcv" in ret.columns else []}
    cols = sorted({c for v in sens_sets.values() for c in v} | {"plugin_maxsharpe", "equal_weight", "drmsd_cv", "drcvar_cv", f"w{windows[1]}_plugin_maxsharpe"})
    R = ret[cols].to_numpy()
    sh, cv, dd = bt.block_bootstrap(R, cfg)
    ix = {c: i for i, c in enumerate(cols)}
    rows = []
    for sname, members in sens_sets.items():
        for m in members:
            d = bt.perf_metrics(ret[m].to_numpy(), cfg)
            base_p = f"w{windows[1]}_plugin_maxsharpe" if sname == "window_252" else "plugin_maxsharpe"
            row = {"sensitivity": sname, "method": m, "sharpe": d["sharpe"], "max_drawdown": d["max_drawdown"], "cvar95_daily": d["cvar95_daily"],
                   "avg_turnover": float(turn[m].iloc[1:].mean()), "avg_effective_n": float(effn[m].mean())}
            for tag, base in (("plugin", base_p), ("equal_weight", "equal_weight")):
                dd_ = sh[:, ix[m]] - sh[:, ix[base]]
                lo, hi = bt.interval(dd_, cfg)
                row.update({f"sharpe_diff_vs_{tag}": d["sharpe"] - bt.perf_metrics(ret[base].to_numpy(), cfg)["sharpe"], f"ci_lo_vs_{tag}": lo, f"ci_hi_vs_{tag}": hi})
            rows.append(row)
    tabs["sensitivities"] = pd.DataFrame(rows)

    tabs["daily_returns"] = ret.reset_index().rename(columns={"index": "date"})
    eo = ept.copy()
    eo.insert(0, "date", rets.index[eo.index])
    tabs["chosen_epsilon_over_time"] = eo.reset_index()
    wmsd = pd.DataFrame(W["drmsd_cv"], index=rets.index[positions], columns=rets.columns)
    tabs["weights_drmsd_cv"] = wmsd.reset_index().rename(columns={"index": "date"})

    checks = sanity_checks(cfg, rets, bench, positions, G4, ept, targets, ret, turn, cost, effn, lab_h, grid, n_jobs, label_recompute_ok)
    for name, ok, detail in checks:
        lines.append(f"- Check {'PASS' if ok else 'FAIL'}: {name}: {detail}")
        if not ok:
            failures.append(name)
    timing["total_s"] = time.perf_counter() - t_start
    return {"tables": tabs, "lines": lines, "failures": failures, "timing": timing, "ret": ret, "turn": turn, "effn": effn, "ept": ept, "positions": positions,
            "cost": cost, "carried": carried, "first": first, "fixed_hind": fixed_hind, "grid": grid, "labels": (lab_h, lab_v, s_t), "W": W}


# --------------------------------------------------------------------------------------------- figures
def make_figures(cfg, res, rets, figs_dir, suffix):
    figs_dir.mkdir(parents=True, exist_ok=True)
    ret, tabs = res["ret"], res["tables"]
    prim = ["plugin_maxsharpe", "drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime", "equal_weight", "nifty_price_index"]
    style = {"nifty_price_index": dict(color="k", ls="--")}
    fig, ax = plt.subplots(figsize=(9, 5))
    for m in prim:
        ax.plot(ret.index, (1 + ret[m]).cumprod(), label=bt_label(m), **style.get(m, {}))
    ax.set_yscale("log")
    ax.set_ylabel("net wealth (start = 1, log scale)")
    ax.set_xlabel("date")
    ax.set_title("Net wealth after 10 bps costs (index: price only, no costs, excludes dividends)")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(figs_dir / f"wealth{suffix}.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for m in prim:
        w = (1 + ret[m]).cumprod()
        ax.plot(ret.index, w / w.cummax() - 1, label=bt_label(m), **style.get(m, {}))
    ax.set_ylabel("drawdown from running peak (fraction)")
    ax.set_xlabel("date")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(figs_dir / f"drawdown{suffix}.png", dpi=150)
    plt.close(fig)

    por = tabs["price_of_robustness"]
    fig, axs = plt.subplots(2, 2, figsize=(10, 7), sharex=True)
    s = tabs["backtest_summary"].set_index("method")
    for kind, col in (("cvar", "tab:blue"), ("msd", "tab:orange")):
        d = por[por.kind == kind]
        for ax, key, lab in zip(axs.ravel(), ["sharpe", "max_drawdown", "cvar95_daily", "avg_effective_n"],
                                ["net Sharpe ratio (annualised)", "maximum drawdown (fraction)", "CVaR95 of daily net returns (loss fraction)", "average effective number of positions"]):
            ax.plot(d.eps, d[key], "o-", ms=3, color=col, label="DR-" + ("CVaR" if kind == "cvar" else "MSD"))
            ax.set_ylabel(lab)
            ax.set_xscale("log")
    for ax, key in zip(axs.ravel(), ["sharpe", "max_drawdown", "cvar95_daily", "avg_effective_n"]):
        ax.axhline(s.loc["plugin_maxsharpe", key], color="tab:red", ls="--", label="plug-in max-Sharpe")
        ax.axhline(s.loc["equal_weight", key], color="tab:green", ls=":", label="1/N")
    for ax in axs[1]:
        ax.set_xlabel("epsilon (daily-return units)")
    axs[0, 0].legend(fontsize=7)
    fig.suptitle("Price of robustness: fixed-epsilon portfolios, 2017-2026 (hindsight curve; DR-MSD extra points above 0.2154 are the extended grid)")
    fig.tight_layout()
    fig.savefig(figs_dir / f"price_of_robustness{suffix}.png", dpi=150)
    plt.close(fig)

    st = tabs["stress_windows"]
    names = [m for m in prim if m in st.method.values]
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5), sharey=False)
    for ax, key, lab in zip(axs, ["cumulative_return", "cvar95_daily"], ["cumulative net return in the window (fraction)", "CVaR95 of daily net returns (loss fraction)"]):
        wins = list(cfg["backtest"]["stress_windows"])
        x = np.arange(len(names))
        for j, wn in enumerate(wins):
            v = [st[(st.window == wn) & (st.method == m)][key].iloc[0] for m in names]
            ax.bar(x + (j - 0.5) * 0.4, v, 0.4, label=wn)
        ax.set_xticks(x, [bt_label(m) for m in names], rotation=60, ha="right", fontsize=7)
        ax.set_ylabel(lab)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(figs_dir / f"stress_windows{suffix}.png", dpi=150)
    plt.close(fig)

    ept = res["ept"]
    dates = rets.index[ept.index]
    fig, axs = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for ax, pre, title in zip(axs, ["drcvar", "drmsd"], ["DR-CVaR", "DR-MSD"]):
        ax.step(dates, ept[f"{pre}_cv_eps"], where="post", label="static (pooled) CV epsilon", color="k")
        ax.step(dates, ept[f"{pre}_eps_calm"], where="post", label="calm regime", color="tab:blue")
        ax.step(dates, ept[f"{pre}_eps_stress"], where="post", label="stress regime", color="tab:red")
        ax.set_yscale("log")
        ax.set_ylabel(f"{title} chosen epsilon (daily-return units)")
        ax.legend(fontsize=7)
    s_t = res["labels"][2].reindex(dates)
    ax2 = axs[0].twinx()
    ax2.fill_between(dates, 0, (ept["label_hmm2"] == STRESS).astype(float).to_numpy(), color="grey", alpha=0.2, step="post")
    ax2.set_ylim(0, 1)
    ax2.set_yticks([])
    axs[1].set_xlabel("rebalance date (grey: HMM stress label)")
    fig.tight_layout()
    fig.savefig(figs_dir / f"chosen_epsilon{suffix}.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for m in ["plugin_maxsharpe", "drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime"]:
        ax.plot(res["effn"].index, res["effn"][m], label=bt_label(m))
    ax.axhline(rets.shape[1], color="k", ls=":", label="1/N")
    ax.set_ylabel("effective number of positions (1 / sum w^2)")
    ax.set_xlabel("rebalance date")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(figs_dir / f"effective_n{suffix}.png", dpi=150)
    plt.close(fig)


def bt_label(m):
    return LABELS.get(m, m)


# ----------------------------------------------------------------------------------------------- notes
NOTES_TEMPLATE = """# Rolling-window backtest on the NIFTY 50 data

Specification (written before the run): `docs/backtest_spec.md`. All parameters: `config.yaml`, section `backtest`.

## What was done

Every 21 trading days, from the common evaluation start, each method builds long-only, fully invested portfolios with a 10% cap from the previous 504 trading days
(252 in a sensitivity run). The weights are held, drifting with returns, until the next rebalance. Trading costs are 10 basis points of turnover, charged on the
rebalance day, so every series below is net of costs. The NIFTY 50 price index is shown without costs and without dividends. The risk-free rate is 6.5% a year
(an assumption); the Sharpe ratio is the annualised mean excess return over rf/252 divided by the annualised standard deviation of daily net returns.

## Methods

Plug-in max-Sharpe and minimum variance, 1/N, Ledoit-Wolf max-Sharpe, Michaud resampling, worst-case box and ellipsoid, plug-in CVaR (linear program), and two Wasserstein
distributionally robust portfolios: DR-CVaR (the existing linear program) and DR-MSD, which minimises sqrt(w' V w) + eps ||w||_p - theta (m'w - rf/252), where V and m are the
covariance (1/T) and mean of the daily returns in the window. theta is estimated in every window as sqrt(252) / max(S, 0.25), with S the window's in-sample max-Sharpe ratio.
The radius eps comes from the existing walk-forward selection (21-day validation blocks, refit every 63 days, pooled across regimes or per regime of a two-state hidden Markov
model of NIFTY volatility). The regime-aware versions use the epsilon of the regime in effect on the previous day; the soft version blends the two regimes' epsilons with the
filtered stress probability. Epsilon values are in daily-return units.

## Reading the tables (suffix `_bt` in `outputs/tables`)

* `backtest_summary`: annualised return (mean x 252, and compound rate), volatility, Sharpe, maximum drawdown, CVaR95 of daily net returns (a loss, as a positive number), Calmar ratio,
  average turnover per rebalance (the first purchase from cash is excluded), average effective number of positions, total cost.
* `backtest_bootstrap`: 95% intervals from a circular block bootstrap (21-day blocks) of the paired daily series. "versus" rows are differences (method minus comparator); for
  maximum drawdown a positive difference means a shallower drawdown; for CVaR95 a negative difference means a smaller loss.
* `h1_secondary`: the CVaR95 and maximum-drawdown rows of the bootstrap table for the primary DR portfolios against plug-in max-Sharpe.
* `stress_windows`: cumulative return, maximum drawdown and CVaR95 inside 1 Feb to 30 Apr 2020 and calendar 2022, fixed by date.
* `price_of_robustness`: one row per fixed epsilon (a hindsight view: the best fixed epsilon uses information that was not available at the time).
* `regime_exploratory`: regime-aware portfolios against the cross-validated static epsilon and the hindsight best static epsilon; exploratory, never a confirmed result.
* `sensitivities`: window 252, one-SE rule, the other regime labels, DR-MSD with epsilon from the DR-CVaR validation blocks, and DR-MSD with the extended grid.

## Results

<!-- results:start -->
(filled in by scripts/run_backtest.py)
<!-- results:end -->
"""

LIM_MARK = "<!-- backtest-limitations -->"
LIM_TEXT = f"""
{LIM_MARK}
## Rolling-window backtest (scripts/run_backtest.py)

* **Survivorship.** The universe is the 40 stocks that are in the current index and have full history; stocks that left the index, or were excluded for unadjusted corporate actions, are absent, which flatters every method.
* **Price index.** The benchmark is the NIFTY 50 price index; it excludes dividends, so its return is understated against the net-of-cost total-return portfolios (whose stock returns are also price returns, so no method receives dividends either).
* **Risk-free rate.** 6.5% a year is an assumption applied to the whole period.
* **Cost model.** A flat 10 bps of turnover, no market impact or spread dependence, no taxes; trading happens at the prior close.
* **Inner-window mismatch.** The walk-forward epsilon selection validates portfolios fitted on 252-day windows (`backtest.rolling_window_days`), while the backtest windows are 504 days; the selected epsilon is therefore tuned for a different estimation window than the one it is applied to (the 252-day sensitivity matches).
* **Validation objective.** `eps_for_date` validates with DR-CVaR portfolios; for DR-MSD the same rule is applied to DR-MSD validation blocks, and the literal reading (epsilon from the DR-CVaR blocks) is a sensitivity.
* **Few crisis episodes.** The sample holds one crash (February to April 2020) and one slow decline (2022); regime-specific conclusions rest on those few episodes.
* **HMM barely flags 2022.** The two-state hidden Markov model labels about 22 of 248 days of 2022 as stress, so the regime-aware portfolios are in their stress setting for little of that year.
* **theta_hat.** The mean weight of DR-MSD is estimated from the in-sample max-Sharpe ratio of each window, which is itself noisy (floored at 0.25); there is no truth to calibrate against.
* **Truncated epsilon grid.** For DR-MSD the 1/N portfolio lies above the largest grid value (0.2154), so the primary DR-MSD cannot reach 1/N; an extended-grid sensitivity is reported.
* **Bootstrap.** The circular block bootstrap treats 21-day blocks as exchangeable and cannot represent dependence between blocks or regime persistence beyond a block; the intervals are approximate, and many comparisons are shown without multiplicity correction.
* **One path.** A single historical path is a single draw; the backtest is descriptive evidence and does not by itself confirm or refute a hypothesis.
"""


def results_markdown(res, cfg):
    t = res["tables"]
    s = t["backtest_summary"]
    head = s[s.method.isin(["plugin_maxsharpe", "drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime", "equal_weight", "nifty_price_index"])][
        ["label", "sharpe", "max_drawdown", "cvar95_daily", "avg_turnover", "ann_return", "ann_vol"]]
    b = t["backtest_bootstrap"]
    d = b[(b.metric == "sharpe") & (b.versus != "(level)") & (b.method.isin(["drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime"]))]
    parts = [f"Common evaluation start: {res['ret'].index[0].date()} (rebalance row {res['first']}); evaluation to {res['ret'].index[-1].date()}; {len(res['positions'])} rebalances.", "",
             "Headline (net of costs):", "", md_table(head), "", "Paired Sharpe differences (block bootstrap 95% intervals):", "", md_table(d), "",
             "H1 secondary metrics:", "", md_table(t["h1_secondary"]), "", "Stress windows:", "", md_table(t["stress_windows"][t["stress_windows"].method.isin(
                 ["plugin_maxsharpe", "drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime", "equal_weight", "nifty_price_index"])]), "",
             "Regime-aware comparison (exploratory):", "", md_table(t["regime_exploratory"]), "", "Sensitivities:", "", md_table(t["sensitivities"])]
    return "\n".join(parts)


def main(args, cfg=None, n_jobs=None, rets=None, bench=None, labels=None, pilot=True, label_recompute_ok=True):
    from src.utils import load_config
    cfg = load_config() if cfg is None else cfg
    bc = cfg["backtest"]
    suffix = bc["output_suffix"]
    if rets is None:
        rets, bench = bt.load_data(cfg)
    t0 = time.time()
    res = run_all(cfg, rets, bench, labels=labels, n_jobs=n_jobs, pilot=pilot, label_recompute_ok=label_recompute_ok)
    lines, failures = res["lines"], res["failures"]
    tables_dir, figs_dir = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    if res.get("tables"):
        tables_dir.mkdir(parents=True, exist_ok=True)
        for name, df in res["tables"].items():
            df.to_csv(tables_dir / f"{name}{suffix}.csv", index=False)
        make_figures(cfg, res, rets, figs_dir, suffix)
        notes = Path(bc["notes_path"])
        if not notes.exists():
            notes.write_text(NOTES_TEMPLATE, encoding="utf-8")
        found = update_notes_blocks(notes, {"results": results_markdown(res, cfg)})
        lim = Path("docs/limitations.md")
        if lim.exists() and LIM_MARK not in lim.read_text(encoding="utf-8"):
            with open(lim, "a", encoding="utf-8") as f:
                f.write(LIM_TEXT)
        lines.append(f"- Tables and figures written with suffix {suffix}; notes blocks regenerated: {found}.")
        tm = res["timing"]
        lines.append(f"- Wall time: epsilon selection {tm['eps_selection_s']:.0f} s, weights {tm['weights_s']:.0f} s, total {tm['total_s'] / 60:.1f} min.")
    lines.append(f"- Total run time {time.time() - t0:.0f} s." + (" ALL CHECKS PASSED." if not failures else ""))
    if failures:
        lines.append("- CHECKS FAILED: " + "; ".join(failures))
    print("\n".join(lines))
    if args.log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Rolling-window backtest (scripts/run_backtest.py)\n" + "\n".join(lines) + "\n")
            for name in ("backtest_summary", "backtest_bootstrap", "h1_secondary", "regime_exploratory"):
                if name in res.get("tables", {}):
                    f.write(f"\n{name}:\n\n" + md_table(res["tables"][name]) + "\n")
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    sys.exit(main(ap.parse_args()))
