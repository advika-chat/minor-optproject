"""Monte Carlo with known ground truth: does Wasserstein DRO beat plug-in Markowitz out of sample?

The truth (mu_true, Sigma_true) is built from the real data and returns are simulated from it, so the
out-of-sample performance of any weight vector is exact: true Sharpe = (mu_true'w - rf) / sqrt(w'Sigma_true w).
No test sample is needed. All parameters are in config.yaml section ``montecarlo``.

Truth. Sigma_true = full-sample annualised covariance; mu_true = grand_mean + s*(mu_hat_full - grand_mean) with
grand_mean the cross-sectional average of mu_hat_full. Daily returns are i.i.d. multivariate Student-t (df =
montecarlo.student_df) or Gaussian, with daily mean mu_true/252 and daily covariance Sigma_true/252 exactly
(the t draw is z * sqrt((df-2)/chi2_df), which has covariance equal to that of z).

DR mean-variance used here. src/wdro.py solves  min sqrt(w' Var_n w) + eps ||w||_p  over the capped long-only
simplex (Var_n = 1/T covariance of daily returns, p dual to wdro.ground_norm, eps in daily-return units). That
objective has no risk-aversion weight and no mean term, and at eps = 0 it is the minimum-variance portfolio, so it
cannot be tuned to the max-Sharpe portfolio. Here the objective is

    sqrt(w' Var_n w) + eps ||w||_p - theta * (mean_n'w - rf/252),

which equals the src/wdro.py objective at theta = 0 (checked in the tests). The weight theta plays the role of the
risk-aversion parameter. It is set once, on the true parameters only: theta = sqrt(252)/S*, S* the oracle max-Sharpe
ratio. For every feasible w, (mu'w - rf)/sqrt(w'Sigma w) <= S*, so sigma(w) - (mu'w - rf)/S* >= 0 with equality at
the oracle; hence at eps = 0 and with the true parameters the objective is minimised exactly by the oracle max-Sharpe
portfolio. theta is the same in every run of a scenario.

Seeds. Each (scenario, T, run) uses numpy SeedSequence(base_seed, spawn_key=(scenario_index, T_index, run)), the
same stream as spawning a child of the base seed with that key, so runs are independent and reproducible in any order.
All methods in a run use the same simulated array, which is made read-only.
"""
import hashlib
import json
import os
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import cvxpy as cp
import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from src import solver_own as so
from src.eps_selection import _FLOAT_TIE, extension_grid
from src.wdro import _dual_p, _norm_expr


# --------------------------------------------------------------------------------------------- truth
@dataclass
class Truth:
    mu: np.ndarray        # annualised
    cov: np.ndarray       # annualised
    names: list


def build_truth(rets, cfg, s):
    """Truth from the real daily simple returns (DataFrame): annualised covariance and shrunk mean."""
    td = cfg["trading_days"]
    mu_full = rets.mean().to_numpy() * td
    grand = mu_full.mean()
    return Truth(mu=grand + s * (mu_full - grand), cov=rets.cov().to_numpy() * td, names=list(rets.columns))


def true_sharpe(w, truth, rf):
    w = np.asarray(w, float)
    return float((truth.mu @ w - rf) / np.sqrt(w @ truth.cov @ w))


def child_seed(cfg, scen_index, t_index, run):
    return np.random.SeedSequence(cfg["montecarlo"]["base_seed"], spawn_key=(scen_index, t_index, run))


def simulate(truth, T, dist, cfg, seed_seq):
    """T days of i.i.d. daily returns with mean mu/252 and covariance Sigma/252 (dist: "t" or "gaussian")."""
    td = cfg["trading_days"]
    rng = np.random.default_rng(seed_seq)
    n = len(truth.mu)
    chol = np.linalg.cholesky(truth.cov / td)
    z = rng.standard_normal((T, n)) @ chol.T
    if dist == "t":
        df = cfg["montecarlo"]["student_df"]
        z = z * np.sqrt((df - 2.0) / rng.chisquare(df, size=T))[:, None]
    elif dist != "gaussian":
        raise ValueError(f"dist must be 't' or 'gaussian', got {dist!r}")
    return z + truth.mu / td


def exact_moment_sample(truth, T, cfg, seed_seq):
    """T days whose sample mean is exactly mu/252 and whose 1/T covariance is exactly Sigma/252 (for known-answer
    checks: a method fed this sample sees the true parameters)."""
    td = cfg["trading_days"]
    n = len(truth.mu)
    z = np.random.default_rng(seed_seq).standard_normal((T, n))
    z -= z.mean(axis=0)
    z = z @ np.linalg.inv(np.linalg.cholesky(z.T @ z / T)).T
    return z @ np.linalg.cholesky(truth.cov / td).T + truth.mu / td


def moment_check(truth, dist, cfg, seed_seq, n_days=None, n_batches=40):
    """Large seeded simulation in batches. Returns the largest |z| of the mean and of the covariance elements,
    z = (estimate - truth) / batch-means standard error."""
    td = cfg["trading_days"]
    n_days = n_days or cfg["montecarlo"]["checks"]["moment_n_days"]
    per = n_days // n_batches
    kids = seed_seq.spawn(n_batches)
    means, covs = [], []
    for k in kids:
        x = simulate(truth, per, dist, cfg, k)
        means.append(x.mean(axis=0))
        covs.append(np.cov(x, rowvar=False))
    means, covs = np.array(means), np.array(covs)
    z_mu = (means.mean(0) - truth.mu / td) / (means.std(0, ddof=1) / np.sqrt(n_batches))
    z_cov = (covs.mean(0) - truth.cov / td) / (covs.std(0, ddof=1) / np.sqrt(n_batches))
    return float(np.abs(z_mu).max()), float(np.abs(z_cov).max())


# ------------------------------------------------------------------------------------ DR mean-variance
class DRMeanVar:
    """Parametrised cvxpy problem, so repeated solves reuse one compiled problem."""

    def __init__(self, n, cap, p, tol=1e-11):
        self.tol = tol
        self.lt = cp.Parameter((n, n))
        self.m = cp.Parameter(n)
        self.eps = cp.Parameter(nonneg=True)
        self.w = cp.Variable(n)
        obj = cp.norm(self.lt @ self.w, 2) + self.eps * _norm_expr(self.w, p) - self.m @ self.w
        self.prob = cp.Problem(cp.Minimize(obj), [cp.sum(self.w) == 1, self.w >= 0, self.w <= cap])
        self.cap = cap

    def solve(self, r, eps, theta):
        """Weights for daily returns r (rows = days), radius eps, mean weight theta."""
        n = r.shape[1]
        cov = np.cov(r, rowvar=False, bias=True)
        self.lt.value = np.linalg.cholesky(cov + 1e-12 * np.eye(n)).T
        self.m.value = theta * r.mean(axis=0)
        self.eps.value = float(eps)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")       # "solution may be inaccurate" at this tolerance; status is checked below
            self.prob.solve(solver=cp.CLARABEL, tol_gap_abs=self.tol, tol_gap_rel=self.tol, tol_feas=self.tol)
        if self.prob.status not in ("optimal", "optimal_inaccurate"):
            raise ValueError(f"cvxpy status: {self.prob.status}")
        return _clean(self.w.value, self.cap)


def _clean(w, cap):
    w = np.clip(np.asarray(w, float), 0.0, cap)
    return w / w.sum()


_CACHE = {}


def _drmv(n, cap, p, tol=1e-11):
    key = (n, cap, p, tol)
    if key not in _CACHE:
        _CACHE[key] = DRMeanVar(n, cap, p, tol)
    return _CACHE[key]


def plugin_maxsharpe(mu_hat, cov_hat, cfg):
    """Plug-in max-Sharpe with the own active-set solver (annualised inputs)."""
    return so.max_sharpe(cov_hat, mu_hat, cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"], cfg["solver_own"])


def calibrate_theta(truth, cfg):
    """Oracle max-Sharpe (own solver) and theta = sqrt(trading_days) / S*."""
    cap, rf, td = cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"], cfg["trading_days"]
    res = so.max_sharpe(truth.cov, truth.mu, cap, rf, cfg["solver_own"])
    if res.status != so.OPTIMAL or not res.sharpe > 0:
        raise ValueError(f"oracle max-Sharpe unusable: status {res.status}, Sharpe {res.sharpe}")
    return np.asarray(res.weights, float), float(res.sharpe), float(np.sqrt(td) / res.sharpe)


@dataclass
class Context:
    truth: Truth
    w_orc: np.ndarray
    s_orc: float
    theta: float
    grid: np.ndarray


def make_context(rets, cfg, s, extra_eps=()):
    """Truth, oracle, theta and the eps grid (eps_selection_ext grid thinned by grid_stride, plus extra_eps)."""
    truth = build_truth(rets, cfg, s)
    w, sh, theta = calibrate_theta(truth, cfg)
    grid = extension_grid(cfg["eps_selection_ext"])[::cfg["montecarlo"]["grid_stride"]]
    return Context(truth, w, sh, theta, np.concatenate([grid, np.asarray(extra_eps, float)]))


def scenarios(cfg):
    """Primary scenario (index 0) followed by the sensitivity scenarios, each with its T list and run count."""
    mc = cfg["montecarlo"]
    out = [{"name": "primary", "index": 0, "mu_shrink_s": mc["mu_shrink_s"], "dist": "t", "T_list": list(mc["T_list"]),
            "n_runs": mc["n_runs"], "extra_eps": []}]
    for i, sc in enumerate(mc["sensitivity"]["scenarios"], start=1):
        out.append({**sc, "index": i, "n_runs": mc["sensitivity"]["n_runs_sens"]})
    return out


def oracle_mv_weights(ctx, cfg):
    """DR mean-variance at eps = 0 on the TRUE parameters (the calibration check: must equal the oracle)."""
    td = cfg["trading_days"]
    n = len(ctx.truth.mu)
    cap = cfg["portfolio"]["weight_cap"]
    w = cp.Variable(n)
    chol = np.linalg.cholesky(ctx.truth.cov / td)
    prob = cp.Problem(cp.Minimize(cp.norm(chol.T @ w, 2) - ctx.theta * (ctx.truth.mu / td) @ w),
                      [cp.sum(w) == 1, w >= 0, w <= cap])
    tol = cfg["montecarlo"]["calibration_solver_tol"]
    prob.solve(solver=cp.CLARABEL, tol_gap_abs=tol, tol_gap_rel=tol, tol_feas=tol)
    return _clean(w.value, cap)


# ------------------------------------------------------------------------------------- one simulated run
def fit_grid(drmv, r, grid, theta):
    return np.array([drmv.solve(r, e, theta) for e in grid])


def cv_select(drmv, r, grid, theta, cfg):
    """Pooled out-of-fold excess Sharpe per eps with contiguous folds; argmax, ties to the smaller eps."""
    td, rf = cfg["trading_days"], cfg["risk_free_rate"]
    folds = np.array_split(np.arange(r.shape[0]), cfg["montecarlo"]["cv_folds"])
    oof = np.empty((r.shape[0], len(grid)))
    for f in folds:
        mask = np.ones(r.shape[0], bool)
        mask[f] = False
        oof[f] = r[f] @ fit_grid(drmv, r[mask], grid, theta).T
    sd = oof.std(axis=0, ddof=1)
    crit = np.where(sd > 0, (oof.mean(axis=0) - rf / td) / np.where(sd > 0, sd, 1.0) * np.sqrt(td), -np.inf)
    best = int(np.flatnonzero(crit >= crit.max() - _FLOAT_TIE)[0])
    return best, crit


def _metrics(w, ctx, cfg):
    rf, thr = cfg["risk_free_rate"], cfg["robust"]["nonzero_threshold"]
    t = ctx.truth
    ret, vol = float(t.mu @ w), float(np.sqrt(w @ t.cov @ w))
    sh = (ret - rf) / vol
    n = len(w)
    return {"true_sharpe": sh, "true_return": ret, "true_vol": vol, "n_pos": int((w > thr).sum()),
            "eff_n": float(1.0 / np.sum(w ** 2)), "l1_oracle": float(np.abs(w - ctx.w_orc).sum()),
            "regret": ctx.s_orc - sh, "maxdev_equal": float(np.abs(w - 1.0 / n).max())}


def data_hash(r):
    return hashlib.sha256(np.ascontiguousarray(r).tobytes()).hexdigest()[:16]


def run_one(ctx, cfg, scen, T, run):
    """All methods for one simulated training window. Returns {"meta": ..., "rows": [...]}"""
    mc = cfg["montecarlo"]
    td, rf, cap = cfg["trading_days"], cfg["risk_free_rate"], cfg["portfolio"]["weight_cap"]
    opts = cfg["solver_own"]
    t_index = mc["T_list"].index(T)
    r = simulate(ctx.truth, T, scen["dist"], cfg, child_seed(cfg, scen["index"], t_index, run))
    r.setflags(write=False)
    h0 = data_hash(r)
    n = r.shape[1]
    drmv = _drmv(n, cap, _dual_p(cfg), mc["solver_tol"])
    mu_hat, cov_hat = r.mean(axis=0) * td, np.cov(r, rowvar=False) * td

    ws = {}
    ms = plugin_maxsharpe(mu_hat, cov_hat, cfg)
    ws["plugin_maxsharpe"] = np.asarray(ms.weights, float)
    ws["plugin_minvar"] = np.asarray(so.min_variance(cov_hat, cap, opts).weights, float)
    lw = LedoitWolf().fit(r)
    ms_lw = so.max_sharpe(lw.covariance_ * td, mu_hat, cap, rf, opts)
    ws["lw_maxsharpe"] = np.asarray(ms_lw.weights, float)
    ws["equal_weight"] = np.full(n, 1.0 / n)
    ws["oracle"] = ctx.w_orc
    fixed = fit_grid(drmv, r, ctx.grid, ctx.theta)
    best, crit = cv_select(drmv, r, ctx.grid, ctx.theta, cfg)
    ws["dro_cv"] = fixed[best]
    ws["dro_eps0"] = drmv.solve(r, 0.0, ctx.theta)

    rows = []
    for name, w in ws.items():
        rows.append({"method": name, "eps": float(ctx.grid[best]) if name == "dro_cv" else (0.0 if name == "dro_eps0" else np.nan),
                     "eps_index": best if name == "dro_cv" else -1, **_metrics(w, ctx, cfg)})
    for k, e in enumerate(ctx.grid):
        rows.append({"method": f"dro_fixed_{k:02d}", "eps": float(e), "eps_index": k, **_metrics(fixed[k], ctx, cfg)})
    for row in rows:
        row.update({"scenario": scen["name"], "T": T, "run": run})
    assert data_hash(r) == h0 and not r.flags.writeable
    meta = {"scenario": scen["name"], "T": T, "run": run, "data_hash": h0, "eps_index": best,
            "eps_chosen": float(ctx.grid[best]), "plateau": bool(rows[[x["method"] for x in rows].index("dro_cv")]["maxdev_equal"]
                                                                  <= mc["checks"]["plateau_weight_tol"]),
            "plugin_status": ms.status, "plugin_nonpositive_sharpe": bool(ms.nonpositive_sharpe),
            "top_eps_maxdev_equal": float(np.abs(fixed[-1] - 1.0 / n).max()),
            "max_oracle_excess": float(max(m["true_sharpe"] for m in rows) - ctx.s_orc),
            "mu_hat": mu_hat.tolist(), "oof_sharpe": crit.tolist()}
    return {"meta": meta, "rows": rows}


# --------------------------------------------------------------------------------------- checkpointing
def ckpt_path(cfg, scen, T, run):
    return Path(cfg["montecarlo"]["checkpoint_dir"]) / f"{scen['name']}_T{T}_r{run:04d}.json"


def _write_atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)


def worker(ctx, cfg, scen, T, run):
    """Compute one run and write its checkpoint. Skips runs whose checkpoint exists (resumable)."""
    path = ckpt_path(cfg, scen, T, run)
    if path.exists():
        return str(path), 0.0
    t0 = time.perf_counter()
    out = run_one(ctx, cfg, scen, T, run)
    out["meta"]["seconds"] = time.perf_counter() - t0
    _write_atomic(path, out)
    return str(path), out["meta"]["seconds"]


def load_checkpoints(cfg, scen, T, runs):
    metas, rows = [], []
    for run in runs:
        d = json.loads(ckpt_path(cfg, scen, T, run).read_text())
        metas.append(d["meta"])
        rows.extend(d["rows"])
    return metas, rows


# ----------------------------------------------------------------------------------------- statistics
def bootstrap_means(d, n_resamples, rng, chunk=1000):
    """Bootstrap distribution of the mean of d (resampling runs)."""
    d = np.asarray(d, float)
    out = np.empty(n_resamples)
    for i in range(0, n_resamples, chunk):
        k = min(chunk, n_resamples - i)
        out[i:i + k] = d[rng.integers(0, len(d), size=(k, len(d)))].mean(axis=1)
    return out


def paired_stats(d, cfg, seed_offset=0):
    b = cfg["montecarlo"]["bootstrap"]
    rng = np.random.default_rng(b["seed"] + seed_offset)
    boot = bootstrap_means(d, b["n_resamples"], rng)
    a = (1.0 - b["ci_level"]) / 2.0
    d = np.asarray(d, float)
    return {"n": len(d), "mean": float(d.mean()), "median": float(np.median(d)), "boot_se": float(boot.std(ddof=1)),
            "ci_lo": float(np.quantile(boot, a)), "ci_hi": float(np.quantile(boot, 1 - a)),
            "share_positive": float((d > 0).mean())}, boot


def diff_in_diff(d_short, d_long, cfg, seed_offset=0):
    """H2: Delta(T_short) - Delta(T_long) with each Delta bootstrapped separately (runs are independent across T)."""
    s1, b1 = paired_stats(d_short, cfg, seed_offset)
    s2, b2 = paired_stats(d_long, cfg, seed_offset + 1)
    did = b1 - b2
    a = (1.0 - cfg["montecarlo"]["bootstrap"]["ci_level"]) / 2.0
    return {"did": s1["mean"] - s2["mean"], "boot_se": float(did.std(ddof=1)),
            "ci_lo": float(np.quantile(did, a)), "ci_hi": float(np.quantile(did, 1 - a))}, s1, s2


def wide(runs, scen, T, col="true_sharpe"):
    sub = runs[(runs.scenario == scen) & (runs["T"] == T)]
    return sub.pivot(index="run", columns="method", values=col)
