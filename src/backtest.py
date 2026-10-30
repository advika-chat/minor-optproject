"""Rolling-window backtest on real data (docs/backtest_spec.md; parameters in config.yaml section ``backtest``).

Timing. Rebalance positions p = first, first + 21, ... (row numbers of the daily return table). The weights traded at the start of day p use the
``window`` rows p-window .. p-1 only. Between rebalances the weights drift with returns. Turnover at a rebalance is sum |w_target - w_drifted|
(from cash at the first one), the cost ``costs.transaction_cost_bps`` x turnover is deducted from the return of the rebalance day, as in
``src.eps_selection.compute_blocks``.

DR-MSD (primary DRO): sqrt(w' Sigma_n w) + eps ||w||_p - theta (mu_n'w - rf/252), solved by ``src.montecarlo.DRMeanVar``; theta_hat is estimated in every
window as sqrt(252) / max(S_hat, S_floor), S_hat the window's plug-in max-Sharpe (own active-set solver).

Epsilon. The walk-forward selection of ``src.eps_selection`` (``eps_schedule`` with precomputed validation blocks) is used. For DR-CVaR the blocks are
the ones ``compute_blocks`` builds; for DR-MSD ``compute_blocks_msd`` builds the same blocks (same origins, drift, cost) with DR-MSD portfolios.
``lookup_eps`` reads the schedule as ``eps_for_date_info`` does; the script checks the two agree on truncated data.
"""
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from src import montecarlo as mc
from src import robust
from src import solver_own as so
from src.eps_selection import POOLED, block_origins, eps_schedule, extension_grid, refit_positions
from src.markowitz import annualise
from src.regimes import UNLABELLED
from src.wdro import _dual_p, wdro_weights

GRID_GROUPS = ("drcvar", "drmsd")


# ------------------------------------------------------------------------------------------------ data
def load_data(cfg):
    base = Path(cfg["data"]["processed_dir"])
    rets = pd.read_parquet(base / "returns_simple.parquet")
    bench = pd.read_parquet(base / "benchmark.parquet")["simple"]
    if not rets.index.equals(bench.index):
        raise ValueError("returns and benchmark have different indexes")
    return rets, bench


def plog(cfg, msg):
    path = Path(cfg["backtest"]["progress_log"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def clean(w, cap):
    w = np.clip(np.asarray(w, float), 0.0, cap)
    return w / w.sum()


def rebalance_positions(n, first, step):
    return list(range(first, n, step))


def eval_start(cfg, labels):
    """First rebalance position: at least start_position_min and with a labelled day before it."""
    p = cfg["backtest"]["start_position_min"]
    while labels.iloc[p - 1] == UNLABELLED:
        p += 1
    return p


# ----------------------------------------------------------------------------------------- one window
def window_stats(win, cfg):
    return annualise(win, cfg["trading_days"])


def plugin_maxsharpe(win, cfg):
    mu, cov = window_stats(win, cfg)
    res = mc.plugin_maxsharpe(mu, cov, cfg)
    return clean(res.weights, cfg["portfolio"]["weight_cap"]), res


def theta_hat(win, cfg):
    """theta_hat = sqrt(trading_days) / max(S_hat, S_floor) and S_hat (plug-in max-Sharpe of the window)."""
    _, res = plugin_maxsharpe(win, cfg)
    s_hat = float(res.sharpe) if res.status == so.OPTIMAL and np.isfinite(res.sharpe) else float("nan")
    floor = cfg["backtest"]["theta_floor_sharpe"]
    s_eff = max(s_hat, floor) if np.isfinite(s_hat) else floor
    return float(np.sqrt(cfg["trading_days"]) / s_eff), s_hat


def msd_solver(cfg, n):
    return mc._drmv(n, cfg["portfolio"]["weight_cap"], _dual_p(cfg), cfg["backtest"]["solver_tol"])


def msd_weights(win, eps_list, theta, cfg):
    r = win.to_numpy()
    d = msd_solver(cfg, r.shape[1])
    return np.array([d.solve(r, float(e), theta) for e in eps_list])


def drcvar_weights_list(win, eps_list, cfg):
    cap = cfg["portfolio"]["weight_cap"]
    return np.array([clean(wdro_weights(win, float(e), "cvar", cfg), cap) for e in eps_list])


def basic_group(win, cfg):
    """Plug-in and robust comparators for one window. Values are weight vectors, or None where a solve failed."""
    cap, td = cfg["portfolio"]["weight_cap"], cfg["trading_days"]
    mu, cov = window_stats(win, cfg)
    out = {}
    w, res = plugin_maxsharpe(win, cfg)
    out["plugin_maxsharpe"] = w
    out["S_hat"] = float(res.sharpe)
    out["plugin_status"] = res.status
    out["plugin_minvar"] = clean(so.min_variance(cov, cap, cfg["solver_own"]).weights, cap)
    lw = LedoitWolf().fit(win.to_numpy())
    out["lw_maxsharpe"] = clean(mc.plugin_maxsharpe(mu, lw.covariance_ * td, cfg).weights, cap)
    c2 = dict(cfg)
    c2["robust"] = dict(cfg["robust"], n_draws=cfg["backtest"]["michaud_draws"])
    for name, fn in (("michaud", lambda: robust.michaud_resampled(win, c2)),
                     ("wc_box", lambda: robust.worst_case_box(win, cfg)),
                     ("wc_ellipsoid", lambda: robust.worst_case_ellipsoid(win, cfg)),
                     ("cvar_plugin", lambda: robust.cvar_min(win, cfg))):
        try:
            out[name] = clean(fn(), cap)
        except Exception as exc:        # a failed solve is carried forward by the engine and counted
            out[name] = None
            out[name + "_error"] = str(exc)[:200]
    return out


def grid_group(kind, win, grid, extra, cfg):
    """Weights at every grid epsilon, at epsilon = 0 and at the extra epsilons {name: value} for DR-CVaR (kind cvar) or DR-MSD (kind msd)."""
    names = [f"fix_{k:02d}" for k in range(len(grid))] + ["eps0"] + list(extra)
    values = list(grid) + [0.0] + [extra[k] for k in extra]
    if kind == "cvar":
        w = drcvar_weights_list(win, values, cfg)
        return {n: v for n, v in zip(names, w)}
    theta, s_hat = theta_hat(win, cfg)
    w = msd_weights(win, values, theta, cfg)
    out = {n: v for n, v in zip(names, w)}
    out["theta"], out["S_hat"] = theta, s_hat
    return out


# ------------------------------------------------------------------------------------ checkpoints
def ckpt_file(cfg, window, p, group):
    return Path(cfg["backtest"]["checkpoint_dir"]) / f"W{window}" / f"p{p:05d}_{group}.json"


def _ser(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    return v


def write_ckpt(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({k: _ser(v) for k, v in obj.items()}))
    os.replace(tmp, path)


def read_ckpt(path):
    d = json.loads(Path(path).read_text())
    return {k: (np.array(v) if isinstance(v, list) else v) for k, v in d.items()}


def worker(rets, cfg, window, p, group, grid, extra):
    """Compute one (window, rebalance position, group) and write its checkpoint; skipped when it exists."""
    path = ckpt_file(cfg, window, p, group)
    if path.exists():
        return str(path), 0.0
    t0 = time.perf_counter()
    win = rets.iloc[p - window:p]
    out = basic_group(win, cfg) if group == "basic" else grid_group({"drcvar": "cvar", "drmsd": "msd"}[group], win, grid, extra, cfg)
    out["seconds"] = time.perf_counter() - t0
    write_ckpt(path, out)
    return str(path), out["seconds"]


# ---------------------------------------------------------------------- epsilon-selection blocks (DR-MSD)
def compute_blocks_msd(returns, cfg, grid, n_jobs=1):
    """Same blocks as ``eps_selection.compute_blocks`` (origins, buy-and-hold drift, cost on the first day of a block) with DR-MSD portfolios
    fitted on the ``backtest.rolling_window_days`` days before each origin; theta_hat is estimated in each of those windows."""
    from joblib import Parallel, delayed
    es = cfg["eps_selection"]
    w_len, b_len = cfg["backtest"]["rolling_window_days"], es["block_length"]
    cost = cfg["costs"]["transaction_cost_bps"] / 1e4
    r = returns.to_numpy()
    origins = block_origins(len(r), cfg)

    def fit(o):
        win = returns.iloc[o - w_len:o]
        theta, _ = theta_hat(win, cfg)
        return msd_weights(win, grid, theta, cfg)

    if n_jobs == 1:
        ws = [fit(o) for o in origins]
    else:
        ws = Parallel(n_jobs=n_jobs)(delayed(fit)(o) for o in origins)
    net = np.empty((len(grid), len(origins), b_len))
    for ei in range(len(grid)):
        w_prev = np.zeros(r.shape[1])
        for bi, o in enumerate(origins):
            w = ws[bi][ei]
            growth = np.cumprod(1.0 + r[o:o + b_len], axis=0)
            value = growth @ w
            daily = value / np.concatenate([[1.0], value[:-1]]) - 1.0
            daily[0] -= cost * np.abs(w - w_prev).sum()
            net[ei, bi] = daily
            w_prev = w * growth[-1] / value[-1]
    return net


def lookup_eps(sched, p, label_prev, grid):
    """eps for rebalance position p: the latest refit at or before p; the regime of the day before p, else the pooled row."""
    qs = sched.loc[sched.refit_position <= p, "refit_position"]
    if qs.empty:
        return {"eps": float(grid[0]), "eps_index": 0, "source": "default", "regime": UNLABELLED, "refit_position": -1}
    last = sched[sched.refit_position == qs.max()]
    row = last[last.regime == POOLED].iloc[0]
    if label_prev != UNLABELLED:
        m = last[last.regime == label_prev]
        if len(m):
            row = m.iloc[0]
    return {"eps": float(row["eps"]), "eps_index": int(row["eps_index"]), "source": row["source"], "regime": label_prev,
            "refit_position": int(row["refit_position"])}


def build_schedule(rets, bench, labels, cfg, net, grid, rule, n=None):
    pos = refit_positions(len(rets) if n is None else n, cfg)
    return eps_schedule(rets, bench, labels, cfg, net=net, positions=pos, grid=grid, rule=rule)


# --------------------------------------------------------------------------------------------- engine
def simulate(targets, positions, rets_np, cost_rate):
    """Run the rebalancing path. ``targets``: {position: weight vector or None (carry forward the previous target)}.
    Returns daily net returns from positions[0] to the end, and per-rebalance arrays: turnover, cost, target weights, carried flag."""
    n_days, n_assets = rets_np.shape
    first = positions[0]
    net = np.empty(n_days - first)
    w_drift = np.zeros(n_assets)
    prev_target = None
    turns, costs, tw, carried = [], [], [], []
    for i, p in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else n_days
        w = targets[p]
        carry = w is None
        if carry:
            w = prev_target if prev_target is not None else np.full(n_assets, 1.0 / n_assets)
        turn = float(np.abs(w - w_drift).sum())
        growth = np.cumprod(1.0 + rets_np[p:end], axis=0)
        value = growth @ w
        daily = value / np.concatenate([[1.0], value[:-1]]) - 1.0
        daily[0] -= cost_rate * turn
        net[p - first:end - first] = daily
        w_drift = w * growth[-1] / value[-1]
        prev_target = w
        turns.append(turn)
        costs.append(cost_rate * turn)
        tw.append(w)
        carried.append(carry)
    return net, np.array(turns), np.array(costs), np.array(tw), np.array(carried)


# --------------------------------------------------------------------------------------------- metrics
def cvar_exact(r, alpha):
    """Empirical CVaR of daily losses at level alpha (equals ``robust.portfolio_cvar`` for a single return series)."""
    loss = np.sort(-np.asarray(r, float))[::-1]
    m = (1.0 - alpha) * len(loss)
    k = int(np.floor(m + 1e-12))
    frac = m - k
    return float((loss[:k].sum() + (frac * loss[k] if k < len(loss) else 0.0)) / m)


def max_drawdown(r):
    w = np.cumprod(1.0 + np.asarray(r, float))
    peak = np.maximum.accumulate(np.concatenate([[1.0], w]))[1:]
    return float((w / peak - 1.0).min())


def perf_metrics(r, cfg):
    td, rf, alpha = cfg["trading_days"], cfg["risk_free_rate"], cfg["risk"]["cvar_alpha"]
    r = np.asarray(r, float)
    sd = r.std(ddof=1)
    cagr = float(np.prod(1.0 + r) ** (td / len(r)) - 1.0)
    mdd = max_drawdown(r)
    return {"ann_return": float(r.mean() * td), "cagr": cagr, "ann_vol": float(sd * np.sqrt(td)),
            "sharpe": float((r.mean() - rf / td) / sd * np.sqrt(td)), "max_drawdown": mdd,
            "cvar95_daily": cvar_exact(r, alpha), "calmar": float(cagr / abs(mdd)) if mdd < 0 else float("nan")}


def _stat_arrays(R, cfg):
    """Sharpe, CVaR95 and maximum drawdown along axis 1 of R with shape (B, T, M)."""
    td, rf, alpha = cfg["trading_days"], cfg["risk_free_rate"], cfg["risk"]["cvar_alpha"]
    t = R.shape[1]
    sharpe = (R.mean(axis=1) - rf / td) / R.std(axis=1, ddof=1) * np.sqrt(td)
    loss = np.sort(-R, axis=1)[:, ::-1, :]
    m = (1.0 - alpha) * t
    k = int(np.floor(m + 1e-12))
    cvar = (loss[:, :k, :].sum(axis=1) + (m - k) * (loss[:, k, :] if k < t else 0.0)) / m
    wealth = np.cumprod(1.0 + R, axis=1)
    peak = np.maximum(np.maximum.accumulate(wealth, axis=1), 1.0)
    mdd = (wealth / peak - 1.0).min(axis=1)
    return sharpe, cvar, mdd


def block_bootstrap(R, cfg, chunk=100):
    """Circular block bootstrap of the paired daily series R (T, M). Returns arrays (B, M) of Sharpe, CVaR95 and maximum drawdown."""
    b = cfg["backtest"]["bootstrap"]
    rng = np.random.default_rng(b["seed"])
    t, m = R.shape
    length = b["block_length"]
    n_blocks = int(np.ceil(t / length))
    out = [np.empty((b["n_resamples"], m)) for _ in range(3)]
    for i in range(0, b["n_resamples"], chunk):
        k = min(chunk, b["n_resamples"] - i)
        starts = rng.integers(0, t, size=(k, n_blocks))
        idx = ((starts[:, :, None] + np.arange(length)) % t).reshape(k, -1)[:, :t]
        for o, a in zip(out, _stat_arrays(R[idx], cfg)):
            o[i:i + k] = a
    return out


def interval(x, cfg):
    a = (1.0 - cfg["backtest"]["bootstrap"]["ci_level"]) / 2.0
    return float(np.quantile(x, a)), float(np.quantile(x, 1.0 - a))
