"""Sensitivity analysis of the capped long-only mean-variance portfolios (engine: src/solver_own.py).

Conventions. ``mu`` and ``cov`` are ANNUALISED (daily mean and covariance times trading_days); ``years`` is the
sample length in years (rows / trading_days). The standard error of an annualised mean is sigma_i / sqrt(years).

* mu perturbation: mu_tilde = mu + k * chol(cov) z / sqrt(years) with z ~ N(0, I), i.e. mu_tilde ~ N(mu, k^2 cov / years).
  The same z is used for every k (common random numbers).
* Sigma perturbations: shrinkage (1 - delta) cov + delta F toward a target F (scaled identity tr(cov)/N * I, which is the
  target of the Ledoit-Wolf estimator in src/robust.py, or a constant-correlation matrix, defined here), volatility scaling
  s^2 cov (correlations unchanged), and correlation stress D ((1 - c) R + c J) D with J the all-ones matrix.
"""
import numpy as np
import pandas as pd

from src import solver_own as so


def annualised_moments(returns, td):
    return returns.mean().to_numpy() * td, returns.cov().to_numpy() * td


def standard_errors(cov, years):
    return np.sqrt(np.diag(cov) / years)


def make_draws(seed, n_draws, n):
    return np.random.default_rng(seed).standard_normal((n_draws, n))


def perturb_mu(mu, chol, years, k, z):
    return mu + k * (chol @ z) / np.sqrt(years)


def l1(a, b):
    return float(np.sum(np.abs(np.asarray(a) - np.asarray(b))))


def metrics(w, mu, cov, rf, thr):
    ret, var = float(mu @ w), float(w @ cov @ w)
    vol = float(np.sqrt(var))
    return {"ret": ret, "vol": vol, "sharpe": (ret - rf) / vol, "n_positions": int((w > thr).sum()), "effective_n": float(1.0 / np.sum(w ** 2))}


# ---- covariance perturbations -----------------------------------------------------------------------------------

def identity_target(cov):
    n = len(cov)
    return np.trace(cov) / n * np.eye(n)


def constant_correlation_target(cov):
    """Keeps every variance and sets all pairwise correlations to their average."""
    d = np.sqrt(np.diag(cov))
    corr = cov / np.outer(d, d)
    n = len(cov)
    rbar = (corr.sum() - n) / (n * (n - 1))
    f = rbar * np.outer(d, d)
    np.fill_diagonal(f, np.diag(cov))
    return f


TARGETS = {"identity": identity_target, "constant_correlation": constant_correlation_target}


def shrink(cov, delta, target):
    return (1.0 - delta) * cov + delta * TARGETS[target](cov)


def scale_vol(cov, s):
    return s ** 2 * cov


def stress_corr(cov, c):
    d = np.sqrt(np.diag(cov))
    corr = cov / np.outer(d, d)
    return np.outer(d, d) * ((1.0 - c) * corr + c * np.ones_like(corr))


def scenario_cov(cov, vol_scale, corr_c):
    return scale_vol(stress_corr(cov, corr_c), vol_scale)


def min_eig_ratio(cov):
    """Smallest eigenvalue divided by the largest; PSD means this is >= -psd_tol."""
    ev = np.linalg.eigvalsh((cov + cov.T) / 2)
    return float(ev[0] / ev[-1])


def is_psd(cov, psd_tol):
    return min_eig_ratio(cov) >= -psd_tol


# ---- mu sensitivity ---------------------------------------------------------------------------------------------

def solve_max_sharpe(cov, mu, cap, rf, opts):
    ms = so.max_sharpe(cov, mu, cap, rf, opts)
    if ms.status != so.OPTIMAL:
        raise RuntimeError(f"max-Sharpe failed: {ms.status}")
    return ms


def mu_sensitivity(cov, mu, cap, rf, opts, sens, years, z, ks, keep_first=0):
    """Per-draw metrics for min-variance and max-Sharpe under mu perturbations (rows: one per k and draw).

    L1 change and positions are relative to the base solution (computed on mu). ``sharpe_base_of_perturbed`` is the Sharpe, under the
    BASE mu, of the weights that are optimal for mu_tilde. Two estimation-error losses are reported: ``loss_base_truth``
    = Sharpe_mu(w(mu)) - Sharpe_mu(w(mu_tilde)) (truth is the base mu, weights come from the draw) and ``loss_draw_truth``
    = Sharpe_mu_tilde(w(mu_tilde)) - Sharpe_mu_tilde(w(mu)) (truth is the draw, weights are the base-optimal ones);
    both are >= 0 up to solver accuracy. Returns (DataFrame, kept) where kept[(k, draw)] = (mu_tilde, SharpeResult)
    for the first ``keep_first`` draws."""
    thr = sens["position_threshold"]
    chol = np.linalg.cholesky(cov)
    base_ms = solve_max_sharpe(cov, mu, cap, rf, opts)
    w0 = base_ms.weights
    mv0 = so.min_variance(cov, cap, opts).weights
    s_base = metrics(w0, mu, cov, rf, thr)["sharpe"]
    pos0 = w0 > thr
    rows, kept = [], {}
    for k in ks:
        for d, zd in enumerate(z):
            mu_t = perturb_mu(mu, chol, years, k, zd)
            ms = solve_max_sharpe(cov, mu_t, cap, rf, opts)
            w = ms.weights
            if d < keep_first:
                kept[(k, d)] = (mu_t, ms)
            mv = so.solve_qp(cov, mu_t, cap, opts, target=None).weights          # minimum variance does not use mu
            m_base = metrics(w, mu, cov, rf, thr)
            s_t_of_w0 = metrics(w0, mu_t, cov, rf, thr)["sharpe"]
            rows.append({"k": k, "draw": d, "l1_change": l1(w, w0), "share_base_positions_dropped": float(np.sum(pos0 & ~(w > thr)) / max(pos0.sum(), 1)),
                         "positions_change": int((w > thr).sum() - pos0.sum()), "sharpe_base_of_perturbed": m_base["sharpe"],
                         "loss_base_truth": s_base - m_base["sharpe"], "sharpe_draw_optimum": ms.sharpe,
                         "loss_draw_truth": ms.sharpe - s_t_of_w0, "nonpositive_sharpe": ms.nonpositive_sharpe,
                         "min_variance_l1_change": l1(mv, mv0)})
    return pd.DataFrame(rows), kept


def one_asset_response(cov, mu, cap, rf, opts, years, thr):
    """Perturb each mu_i by +1 standard error one at a time; response of weight i and L1 response (divided by the step = finite difference)."""
    w0 = solve_max_sharpe(cov, mu, cap, rf, opts).weights
    se = standard_errors(cov, years)
    rows = []
    for i in range(len(mu)):
        mu_i = mu.copy()
        mu_i[i] += se[i]
        w = solve_max_sharpe(cov, mu_i, cap, rf, opts).weights
        rows.append({"asset_index": i, "step_se": float(se[i]), "delta_weight_i": float(w[i] - w0[i]), "l1_response": l1(w, w0),
                     "fd_dweight_i_dmu_i": float((w[i] - w0[i]) / se[i]), "fd_l1_dmu_i": float(l1(w, w0) / se[i]),
                     "base_weight": float(w0[i]), "new_weight": float(w[i])})
    return pd.DataFrame(rows)


# ---- Sigma sensitivity ------------------------------------------------------------------------------------------

def sigma_row(family, param, cov_p, mu, cap, rf, opts, base, thr, psd_tol):
    ms = so.max_sharpe(cov_p, mu, cap, rf, opts)
    mv = so.min_variance(cov_p, cap, opts)
    if mv.status != so.OPTIMAL or ms.status != so.OPTIMAL:
        raise RuntimeError(f"solver failed for {family} {param}")
    mm, ms_m = metrics(mv.weights, mu, cov_p, rf, thr), metrics(ms.weights, mu, cov_p, rf, thr)
    return {"family": family, "parameter": param, "condition_number": float(np.linalg.cond(cov_p)), "min_eig_ratio": min_eig_ratio(cov_p),
            "psd": is_psd(cov_p, psd_tol), "minvar_vol": mm["vol"], "minvar_positions": mm["n_positions"], "minvar_effective_n": mm["effective_n"],
            "minvar_l1_vs_base": l1(mv.weights, base["mv"]), "maxsharpe_sharpe": ms.sharpe, "maxsharpe_return": ms_m["ret"], "maxsharpe_vol": ms_m["vol"],
            "maxsharpe_positions": ms_m["n_positions"], "maxsharpe_effective_n": ms_m["effective_n"], "maxsharpe_l1_vs_base": l1(ms.weights, base["ms"])}


def sigma_sensitivity(cov, mu, cap, rf, opts, sens):
    thr, psd_tol = sens["position_threshold"], sens["psd_tol"]
    base = {"mv": so.min_variance(cov, cap, opts).weights, "ms": solve_max_sharpe(cov, mu, cap, rf, opts).weights}
    rows = []
    for tgt in TARGETS:
        for delta in sens["shrinkage_grid"]:
            rows.append(sigma_row(f"shrink_{tgt}", delta, shrink(cov, delta, tgt), mu, cap, rf, opts, base, thr, psd_tol))
    for s in sens["vol_scale_grid"]:
        rows.append(sigma_row("vol_scale", s, scale_vol(cov, s), mu, cap, rf, opts, base, thr, psd_tol))
    for c in sens["corr_stress_grid"]:
        rows.append(sigma_row("corr_stress", c, stress_corr(cov, c), mu, cap, rf, opts, base, thr, psd_tol))
    return pd.DataFrame(rows)


# ---- scenarios --------------------------------------------------------------------------------------------------

def scenario_parameters(mu, cov, se, sc, rf_base):
    return mu + sc["mu_shift_se"] * se, scenario_cov(cov, sc["vol_scale"], sc["corr_c"]), (rf_base if sc["rf"] is None else sc["rf"])


def base_portfolios(cov, mu, cap, rf, opts, frontier_fraction):
    mv = so.min_variance(cov, cap, opts)
    ms = solve_max_sharpe(cov, mu, cap, rf, opts)
    r_mv = float(mu @ mv.weights)
    r_max = so.feasible_return_range(mu, cap)[1]
    fr = so.solve_qp(cov, mu, cap, opts, target=r_mv + frontier_fraction * (r_max - r_mv))
    if not (mv.ok and fr.ok):
        raise RuntimeError("base portfolio solve failed")
    n = len(mu)
    return {"min_variance": mv.weights, "max_sharpe": ms.weights, "equal_weight": np.full(n, 1.0 / n), "frontier_50pct": fr.weights}


def payoff_tables(portfolios, mu, cov, se, rf_base, scenarios):
    rows = []
    for sc in scenarios:
        mu_s, cov_s, rf_s = scenario_parameters(mu, cov, se, sc, rf_base)
        for name, w in portfolios.items():
            ret, vol = float(mu_s @ w), float(np.sqrt(w @ cov_s @ w))
            rows.append({"portfolio": name, "scenario": sc["name"], "return": ret, "volatility": vol, "sharpe": (ret - rf_s) / vol, "rf": rf_s})
    long = pd.DataFrame(rows)
    sharpe = long.pivot(index="portfolio", columns="scenario", values="sharpe")[[s["name"] for s in scenarios]].loc[list(portfolios)]
    return long, sharpe, regret_matrix(sharpe)


def regret_matrix(sharpe):
    """Best Sharpe among the portfolios in each scenario minus each portfolio's Sharpe (zero for the best, otherwise positive)."""
    return sharpe.max(axis=0) - sharpe


# ---- cap and rf sweeps ------------------------------------------------------------------------------------------

def cap_fd(cov, mu, cap, res, opts):
    """Central finite difference of the optimal variance in the common cap versus -sum(lam_upper)."""
    d = opts["fd_relative_step"] * cap
    lo = so.solve_qp(cov, mu, cap - d, opts, target=res.target)
    hi = so.solve_qp(cov, mu, cap + d, opts, target=res.target)
    same = lo.ok and hi.ok and (lo.active_lower, lo.active_upper) == (res.active_lower, res.active_upper) == (hi.active_lower, hi.active_upper)
    fd = (hi.objective - lo.objective) / (2 * d) if lo.ok and hi.ok else float("nan")
    return fd, bool(same)


def cap_sweep(cov, mu, rf, opts, sens, tickers):
    thr = sens["position_threshold"]
    rows, frontiers = [], {}
    for cap in sens["cap_grid"]:
        mv = so.min_variance(cov, cap, opts)
        ms = so.max_sharpe(cov, mu, cap, rf, opts)
        if mv.status != so.OPTIMAL or ms.status != so.OPTIMAL:
            raise RuntimeError(f"solver failed at cap {cap}")
        mm, mmm = metrics(mv.weights, mu, cov, rf, thr), metrics(ms.weights, mu, cov, rf, thr)
        r_min, r_max = so.feasible_return_range(mu, cap)[:2]
        row = {"cap": cap, "target_range_low": r_min, "target_range_high": r_max, "minvar_vol": mm["vol"], "minvar_variance": float(mv.objective), "minvar_positions": mm["n_positions"],
               "minvar_effective_n": mm["effective_n"], "maxsharpe_sharpe": ms.sharpe, "maxsharpe_return": mmm["ret"], "maxsharpe_vol": mmm["vol"],
               "maxsharpe_positions": mmm["n_positions"], "single_feasible_point": bool(cap * len(mu) - 1.0 <= opts["feasibility_tol"])}
        for label, res in (("minvar", mv), ("maxsharpe_target", ms.qp)):
            fd, same = cap_fd(cov, mu, cap, res, opts)
            row[f"{label}_cap_shadow_price"] = float(res.lam_upper.sum())
            row[f"{label}_max_asset_multiplier"] = float(res.lam_upper.max())
            row[f"{label}_n_at_cap"] = len(res.active_upper)
            row[f"{label}_fd_dV_dcap"] = fd
            row[f"{label}_fd_active_set_unchanged"] = same
            row[f"{label}_multipliers_unique"] = bool(res.multipliers_unique)
        rows.append(row)
        if cap in sens["frontier_caps_plot"]:
            fr = so.frontier(cov, mu, cap, opts)
            frontiers[cap] = (np.array([np.sqrt(f.objective) for f in fr]), np.array([float(mu @ f.weights) for f in fr]))
    return pd.DataFrame(rows), frontiers


def rf_sweep(cov, mu, cap, opts, sens, rf_base):
    thr = sens["position_threshold"]
    base = None
    rows = []
    for rf in sens["rf_grid"]:
        ms = solve_max_sharpe(cov, mu, cap, rf, opts)
        m = metrics(ms.weights, mu, cov, rf, thr)
        if abs(rf - rf_base) < 1e-15:
            base = ms.weights
        rows.append({"rf": rf, **{f"maxsharpe_{k}": v for k, v in m.items()}, "target_return": ms.target_return, "weights": ms.weights})
    out = pd.DataFrame(rows)
    out["l1_vs_base_rf"] = [l1(w, base) if base is not None else float("nan") for w in out["weights"]]
    return out.drop(columns="weights"), list(pd.DataFrame(rows)["weights"])


# ---- sub-periods ------------------------------------------------------------------------------------------------

def period_windows(sens):
    wins = {name: tuple(bounds) for name, bounds in sens["periods"].items()}
    for y in sens["calendar_years"]:
        wins[str(y)] = (f"{y}-01-01", f"{y}-12-31")
    return wins


def period_analysis(returns, cap, rf, opts, sens, td):
    thr = sens["position_threshold"]
    prm, wts = [], []
    store = {}
    for name, (a, b) in period_windows(sens).items():
        win = returns.loc[a:b]
        if len(win) < returns.shape[1] + 2:
            continue
        mu, cov = annualised_moments(win, td)
        mv = so.min_variance(cov, cap, opts)
        ms = so.max_sharpe(cov, mu, cap, rf, opts)
        if mv.status != so.OPTIMAL or ms.status != so.OPTIMAL:
            raise RuntimeError(f"solver failed for period {name}")
        mm, mmm = metrics(mv.weights, mu, cov, rf, thr), metrics(ms.weights, mu, cov, rf, thr)
        d = np.sqrt(np.diag(cov))
        corr = cov / np.outer(d, d)
        n = len(mu)
        prm.append({"kind": "period", "period": name, "start": str(win.index[0].date()), "end": str(win.index[-1].date()), "days": len(win),
                    "mean_mu": float(mu.mean()), "min_mu": float(mu.min()), "max_mu": float(mu.max()), "median_vol": float(np.median(d)),
                    "mean_pairwise_corr": float((corr.sum() - n) / (n * (n - 1))), "condition_number": float(np.linalg.cond(cov)),
                    "minvar_vol": mm["vol"], "minvar_return": mm["ret"], "minvar_positions": mm["n_positions"], "minvar_effective_n": mm["effective_n"],
                    "maxsharpe_sharpe": ms.sharpe, "maxsharpe_return": mmm["ret"], "maxsharpe_vol": mmm["vol"], "maxsharpe_positions": mmm["n_positions"],
                    "maxsharpe_effective_n": mmm["effective_n"], "rf_above_min_variance_return": ms.rf_above_min_variance_return,
                    "nonpositive_sharpe": ms.nonpositive_sharpe, "maxsharpe_at_boundary": ms.at_boundary})
        for kind, w in (("min_variance", mv.weights), ("max_sharpe", ms.weights)):
            store[(name, kind)] = w
            for t, wi in zip(returns.columns, w):
                wts.append({"period": name, "portfolio": kind, "asset": t, "weight": float(wi)})
    years = [str(y) for y in sens["calendar_years"] if (str(y), "max_sharpe") in store]
    for a, b in zip(years[:-1], years[1:]):
        prm.append({"kind": "consecutive_years", "period": f"{a}->{b}", "minvar_l1_distance": l1(store[(a, "min_variance")], store[(b, "min_variance")]),
                    "maxsharpe_l1_distance": l1(store[(a, "max_sharpe")], store[(b, "max_sharpe")])})
    out = pd.DataFrame(prm)
    out["days"] = out["days"].astype("Int64")                    # integer day counts (the consecutive-year rows have none)
    return out, pd.DataFrame(wts), store


# ---- bootstrap over the mu draws ---------------------------------------------------------------------------------

BOOT_STATS = {                      # name: (per-draw column, statistic)
    "l1_median": ("l1_change", np.median),
    "dropped_mean": ("share_base_positions_dropped", np.mean),
    "sharpe_base_mean": ("sharpe_base_of_perturbed", np.mean),
    "sharpe_base_median": ("sharpe_base_of_perturbed", np.median),
    "loss_base_truth_mean": ("loss_base_truth", np.mean),
    "loss_base_truth_median": ("loss_base_truth", np.median),
    "loss_draw_truth_mean": ("loss_draw_truth", np.mean),
    "loss_draw_truth_median": ("loss_draw_truth", np.median),
}


def bootstrap_summary(mu_df, resamples, seed):
    """Seeded bootstrap over the draws (resampling draws with replacement, ``resamples`` times, the same resamples for every
    statistic within a k). For each k and each statistic in BOOT_STATS returns the point estimate, the bootstrap standard
    error (``_boot_se``) and the 95% percentile interval (``_boot_ci_lo``, ``_boot_ci_hi``). Draws are treated as independent,
    which they are by construction (independent normal vectors); the statistics are over draws, not over time."""
    rng = np.random.default_rng(seed)
    rows = []
    for k in sorted(mu_df["k"].unique()):
        d = mu_df[mu_df["k"] == k].sort_values("draw")
        idx = rng.integers(0, len(d), size=(resamples, len(d)))
        row = {"k": k}
        for name, (col, fn) in BOOT_STATS.items():
            v = d[col].to_numpy(float)
            boot = fn(v[idx], axis=1)
            row[name] = float(fn(v))
            row[f"{name}_boot_se"] = float(np.std(boot, ddof=1))
            row[f"{name}_boot_ci_lo"] = float(np.percentile(boot, 2.5))
            row[f"{name}_boot_ci_hi"] = float(np.percentile(boot, 97.5))
        rows.append(row)
    return pd.DataFrame(rows)
