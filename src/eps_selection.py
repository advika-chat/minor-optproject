"""Per-regime choice of the Wasserstein radius by walk-forward validation.

Procedure (all steps use only information available before the date concerned):

1. Validation blocks. With window W = ``backtest.rolling_window_days`` and block length
   B = ``eps_selection.block_length``, block k starts at position o_k = W + k*B and covers
   positions o_k .. o_k + B - 1. For every eps in the grid (default ``wdro.eps_grid``, or a grid
   passed by the caller), the weights of block k are ``wdro_weights`` fitted on the W returns
   just before o_k; they are then held (buy-and-hold) over the block. The block's daily
   returns are net of transaction cost: ``costs.transaction_cost_bps`` times the L1 turnover from
   the previous block's drifted weights (from cash for the first block), charged on the
   block's first day.
2. Regime of a block: the label of the day before the block starts, i.e. the regime in effect
   when the weights were chosen. Labels come from ``src.regimes`` and are causal.
3. Refits. At refit position q = W + B + m * ``refit_every`` the eps choice is recomputed from
   the blocks that finished before q (o_k + B <= q). For each regime with at least
   ``min_blocks`` such blocks, the daily net returns of its blocks are pooled and, for every
   eps, the annualised Sharpe ratio (excess of ``risk_free_rate``) is computed. Two rules:

   * ``"best"``: the eps with the highest Sharpe. Sharpe values within ``tie_tolerance`` of the
     best are ties, broken by the lower realised CVaR (``risk.cvar_alpha``) of the pooled net
     returns; CVaR values equal up to float noise (1e-9) are ties again and the smallest eps
     is kept (this matters on the 1/N plateau, where every eps gives the same portfolio).
   * ``"one_se"``: the smallest eps whose Sharpe is at least (Sharpe of the best eps) minus the
     standard error of the best eps's Sharpe.

   Standard error: leave-one-block-out jackknife. The pooled Sharpe is recomputed with each
   validation block removed in turn, and SE = sqrt((n-1)/n * sum_b (S_(-b) - mean S_(-b))^2) with n the
   number of blocks. It treats whole blocks as the resampling units, so dependence between days
   inside a block is respected, but it ignores dependence BETWEEN blocks. Consecutive blocks
   may be autocorrelated (volatility clustering, regime persistence, overlapping 252-day
   training windows), so this SE is probably too small. It is also the SE of one eps's Sharpe, not of
   the difference between two eps values, which would be smaller because the same blocks
   enter both.

   A regime with too few blocks falls back to the eps chosen on all blocks pooled, with a logged
   warning; if even the pooled set has too few blocks, the smallest grid eps is used (source
   "default"). The selected eps always lies in the grid.
4. ``eps_for_date`` uses the latest refit at or before the date and the regime of the previous
   trading day.

No random or shuffled splits are used. The blocks are evaluated on realised data, so each
block's information is strictly older than the refit that uses it. Selection noise is large
with few blocks per regime; the result is a point estimate, not a tested improvement.
"""
import numpy as np
import pandas as pd

from src.regimes import UNLABELLED, regime_series
from src.robust import portfolio_cvar
from src.utils import get_logger
from src.wdro import eps_grid, wdro_weights

log = get_logger(__name__)
POOLED = "pooled"
RULES = ("best", "one_se")
_FLOAT_TIE = 1e-9          # CVaR values closer than this are treated as equal (float-noise guard, not a tuning parameter)


def _check_inputs(returns, benchmark):
    if returns is None or benchmark is None:
        raise ValueError("pass returns (asset daily returns) and benchmark (daily benchmark "
                         "returns), both loaded from data/processed by the caller")
    if not returns.index.equals(benchmark.index):
        raise ValueError("returns and benchmark must share the same index")


def block_origins(n, config):
    w, b = config["backtest"]["rolling_window_days"], config["eps_selection"]["block_length"]
    return np.arange(w, n - b + 1, b)


def refit_positions(n, config):
    w, b = config["backtest"]["rolling_window_days"], config["eps_selection"]["block_length"]
    return list(range(w + b, n, config["eps_selection"]["refit_every"]))


def extension_grid(config_section):
    """Log-spaced grid from a config section with eps_min, eps_max, eps_points."""
    return np.logspace(np.log10(config_section["eps_min"]), np.log10(config_section["eps_max"]),
                       config_section["eps_points"])


def extension_candidates(config, n_max=12):
    """Geometric continuation of the wdro grid above its maximum, at the same log step."""
    g = eps_grid(config)
    return g[-1] * (g[-1] / g[-2]) ** np.arange(1, n_max + 1)


def scan_plateau(returns, config, tol, n_max=12):
    """Find where the DR solution becomes 1/N, from weights only (no performance is used).

    For candidate eps in [grid maximum] + the geometric continuation above it, fit
    ``wdro_weights`` (kind ``eps_selection.kind``) on the W-day window before every validation
    block origin and record the effective number of positions 1 / sum(w^2). Stops at the
    first candidate for which every window has effective N >= (1 - tol) * N. Returns a DataFrame
    with one row per candidate: eps, min / median effective-N ratio, windows below the threshold."""
    es = config["eps_selection"]
    w_len = config["backtest"]["rolling_window_days"]
    n_assets = returns.shape[1]
    origins = block_origins(len(returns), config)
    candidates = np.concatenate([[eps_grid(config)[-1]], extension_candidates(config, n_max)])
    rows = []
    for eps in candidates:
        ratios = np.array([1.0 / np.sum(wdro_weights(returns.iloc[o - w_len:o], float(eps), es["kind"], config) ** 2)
                           for o in origins]) / n_assets
        rows.append({"eps": float(eps), "min_ratio": ratios.min(), "median_ratio": float(np.median(ratios)),
                     "windows": len(ratios), "windows_below": int((ratios < 1 - tol).sum())})
        if ratios.min() >= 1 - tol:
            return pd.DataFrame(rows)
    raise RuntimeError("1/N plateau not reached within the candidate range")


def compute_blocks(returns, config, grid=None):
    """Net daily returns of every (eps, block): array of shape (n_eps, n_blocks, B)."""
    es = config["eps_selection"]
    w_len, b_len = config["backtest"]["rolling_window_days"], es["block_length"]
    cost = config["costs"]["transaction_cost_bps"] / 1e4
    grid = eps_grid(config) if grid is None else grid
    r = returns.to_numpy()
    origins = block_origins(len(r), config)
    net = np.empty((len(grid), len(origins), b_len))
    for ei, eps in enumerate(grid):
        w_prev = np.zeros(r.shape[1])
        for bi, o in enumerate(origins):
            w = wdro_weights(returns.iloc[o - w_len:o], float(eps), es["kind"], config)
            growth = np.cumprod(1.0 + r[o:o + b_len], axis=0)
            value = growth @ w
            daily = value / np.concatenate([[1.0], value[:-1]]) - 1.0
            daily[0] -= cost * np.abs(w - w_prev).sum()
            net[ei, bi] = daily
            w_prev = w * growth[-1] / value[-1]
    return net


def _jackknife_se(x, rf_d, td):
    """Leave-one-block-out jackknife SE of the pooled annualised Sharpe; x has shape (n_blocks, B)."""
    n, b = x.shape
    if n < 2:
        return np.nan
    s_b, q_b = x.sum(axis=1), (x ** 2).sum(axis=1)
    total, m_all = n * b, None
    m = (s_b.sum() - s_b) / (total - b)
    var = ((q_b.sum() - q_b) - (total - b) * m ** 2) / (total - b - 1)
    sharpe = (m - rf_d) / np.sqrt(var) * np.sqrt(td)
    return float(np.sqrt((n - 1) / n * np.sum((sharpe - sharpe.mean()) ** 2)))


def _criteria(net, idx, config):
    """Net annualised Sharpe (excess of rf), daily CVaR and jackknife SE of the Sharpe, per eps,
    over the pooled blocks ``idx``."""
    td, rf = config["trading_days"], config["risk_free_rate"]
    alpha = config["risk"]["cvar_alpha"]
    sharpe, cvar, se = [], [], []
    for ei in range(net.shape[0]):
        x = net[ei, idx]
        pooled = x.ravel()
        sd = pooled.std(ddof=1)
        sharpe.append((pooled.mean() - rf / td) / sd * np.sqrt(td) if sd > 0 else -np.inf)
        cvar.append(portfolio_cvar(np.array([1.0]), pooled.reshape(-1, 1), alpha))
        se.append(_jackknife_se(x, rf / td, td) if sd > 0 else np.nan)
    return np.array(sharpe), np.array(cvar), np.array(se)


def _argbest(sharpe, cvar, tol):
    """Highest Sharpe; Sharpe ties (within tol) go to the lowest CVaR; CVaR values equal up to float noise go to the smallest eps."""
    best = np.max(sharpe)
    tied = np.where(sharpe >= best - tol)[0]
    cand = tied[cvar[tied] <= np.min(cvar[tied]) + _FLOAT_TIE]
    return int(cand[0])


def _choose(sharpe, cvar, se, tol, rule):
    best = _argbest(sharpe, cvar, tol)
    if rule == "best":
        return best, best
    if rule == "one_se":
        if not np.isfinite(se[best]):
            return best, best
        ok = np.where(sharpe >= sharpe[best] - se[best])[0]
        return int(ok[0]), best                           # smallest eps within one SE of the best
    raise ValueError(f"rule must be one of {RULES}, got {rule!r}")


def select_eps_by_regime(net, block_regimes, config, rule="best"):
    """Pick the eps index per regime from blocks ``net`` (n_eps, n_blocks, B) whose regime
    labels are ``block_regimes``. Returns {regime: dict(eps_index, best_index, source, n_blocks,
    sharpe, cvar, se_best)}, with a POOLED entry for all blocks. ``rule`` is "best" or "one_se"
    (see the module docstring). Regimes with fewer than ``min_blocks`` blocks fall back to
    the pooled choice (warning logged)."""
    es = config["eps_selection"]
    labels = np.asarray(block_regimes, dtype=object)
    n_blocks = net.shape[1]
    out = {}

    def pick(idx, source):
        sh, cv, se = _criteria(net, idx, config)
        k, best = _choose(sh, cv, se, es["tie_tolerance"], rule)
        return dict(eps_index=k, best_index=best, source=source, n_blocks=len(idx),
                    sharpe=sh[k], cvar=cv[k], se_best=se[best])

    if n_blocks >= es["min_blocks"]:
        out[POOLED] = pick(np.arange(n_blocks), "pooled")
    else:
        out[POOLED] = dict(eps_index=0, best_index=0, source="default", n_blocks=n_blocks,
                           sharpe=np.nan, cvar=np.nan, se_best=np.nan)
    for reg in sorted(set(labels) - {UNLABELLED}):
        idx = np.where(labels == reg)[0]
        if len(idx) >= es["min_blocks"]:
            out[reg] = pick(idx, "regime")
        else:
            log.warning("regime %r has %d validation blocks (< %d): using the pooled eps",
                        reg, len(idx), es["min_blocks"])
            out[reg] = dict(out[POOLED], source="pooled_fallback" if n_blocks >= es["min_blocks"] else "default",
                            n_blocks=len(idx))
    return out


def eps_schedule(returns, benchmark, labels, config, net=None, positions=None, grid=None, rule="best"):
    """Table of the selected eps per regime at every refit date.

    ``labels`` is a Series of regime labels aligned with ``returns`` (from ``regime_series``).
    ``net`` can be passed to reuse ``compute_blocks`` output (its first axis must match ``grid``).
    ``positions`` overrides the refit positions (default ``refit_positions``); a position equal to
    len(returns) is allowed and gives a NaT refit_date. ``grid`` defaults to ``wdro.eps_grid``.
    Columns: refit_date, refit_position, regime, rule, eps, eps_index, best_eps, source, n_blocks,
    n_blocks_pooled, sharpe_net, cvar_daily, se_best."""
    _check_inputs(returns, benchmark)
    es = config["eps_selection"]
    grid = eps_grid(config) if grid is None else grid
    w_len, b_len = config["backtest"]["rolling_window_days"], es["block_length"]
    if net is None:
        net = compute_blocks(returns, config, grid)
    if net.shape[0] != len(grid):
        raise ValueError("net and grid have different numbers of eps values")
    origins = block_origins(len(returns), config)
    at_origin = np.array([labels.iloc[o - 1] for o in origins], dtype=object)
    regimes_seen = sorted(set(labels) - {UNLABELLED})
    rows = []
    for q in (refit_positions(len(returns), config) if positions is None else positions):
        k = max((q - w_len) // b_len, 0)               # blocks finished before q
        sel = select_eps_by_regime(net[:, :k], at_origin[:k], config, rule)
        for reg in [POOLED] + regimes_seen:
            s = sel.get(reg)
            if s is None:                               # regime not yet seen in any finished block
                s = dict(sel[POOLED], source="pooled_fallback" if sel[POOLED]["source"] == "pooled" else "default",
                         n_blocks=0)
            rows.append({"refit_date": returns.index[q] if q < len(returns) else pd.NaT, "refit_position": q,
                         "regime": reg, "rule": rule, "eps": float(grid[s["eps_index"]]), "eps_index": s["eps_index"],
                         "best_eps": float(grid[s["best_index"]]), "source": s["source"], "n_blocks": s["n_blocks"],
                         "n_blocks_pooled": k, "sharpe_net": s["sharpe"], "cvar_daily": s["cvar"],
                         "se_best": s["se_best"]})
    return pd.DataFrame(rows)


def cv_curves(returns, labels, config, net, grid=None):
    """Criterion versus eps per regime using all finished blocks: DataFrame with columns
    regime, eps, sharpe_net, cvar_daily, se_sharpe (jackknife), n_blocks."""
    grid = eps_grid(config) if grid is None else grid
    origins = block_origins(len(returns), config)
    at_origin = np.array([labels.iloc[o - 1] for o in origins], dtype=object)
    rows = []
    for reg in [POOLED] + sorted(set(at_origin) - {UNLABELLED}):
        idx = np.arange(net.shape[1]) if reg == POOLED else np.where(at_origin == reg)[0]
        if len(idx) == 0:
            continue
        sh, cv, se = _criteria(net, idx, config)
        rows += [{"regime": reg, "eps": float(e), "sharpe_net": s, "cvar_daily": c, "se_sharpe": u, "n_blocks": len(idx)}
                 for e, s, c, u in zip(grid, sh, cv, se)]
    return pd.DataFrame(rows)


def eps_for_date_info(date, method, config, *, returns=None, benchmark=None, grid=None, rule="best"):
    """eps for ``date`` using only data strictly before it, with details.

    ``returns`` (asset daily returns) and ``benchmark`` (benchmark daily returns, same index) are
    passed by the caller, which loads them from data/processed; rows on or after ``date`` are
    ignored. ``grid`` (default ``wdro.eps_grid``) and ``rule`` ("best" or "one_se") select the
    eps grid and the selection rule. Returns {"eps", "source", "regime", "refit_position",
    "sharpe_net", "cvar_daily"} (the last two are the criterion values behind the choice, NaN for
    the default)."""
    _check_inputs(returns, benchmark)
    grid = eps_grid(config) if grid is None else grid
    p = int(returns.index.searchsorted(pd.Timestamp(date), side="left"))   # rows strictly before date
    qs = [q for q in refit_positions(len(returns), config) if q <= p]
    if not qs:
        return {"eps": float(grid[0]), "source": "default", "regime": UNLABELLED, "refit_position": None,
                "sharpe_net": np.nan, "cvar_daily": np.nan}
    q = qs[-1]
    r_q, b_q = returns.iloc[:q], benchmark.iloc[:q]
    net = compute_blocks(r_q, config, grid)
    lab_q = regime_series(b_q, method, config)["regime"]
    lab_p = regime_series(benchmark.iloc[:p], method, config)["regime"] if p > q else lab_q
    now = lab_p.iloc[p - 1]
    last = eps_schedule(r_q, b_q, lab_q, config, net=net, positions=[q], grid=grid, rule=rule)
    if now == UNLABELLED:
        row = last[last["regime"] == POOLED].iloc[0]
    else:
        match = last[last["regime"] == now]
        row = match.iloc[0] if len(match) else last[last["regime"] == POOLED].iloc[0]
    return {"eps": float(row["eps"]), "source": row["source"], "regime": now, "refit_position": q,
            "sharpe_net": float(row["sharpe_net"]), "cvar_daily": float(row["cvar_daily"])}


def eps_for_date(date, method, config, *, returns=None, benchmark=None, grid=None, rule="best"):
    """The eps to use on ``date`` (see ``eps_for_date_info``)."""
    return eps_for_date_info(date, method, config, returns=returns, benchmark=benchmark,
                             grid=grid, rule=rule)["eps"]
