"""Robust and shrinkage portfolio constructors.

Every method has the signature ``fn(returns_window, config) -> np.ndarray``:
``returns_window`` is a DataFrame of daily simple returns (rows = days, columns =
assets) and ``config`` is the dict from ``load_config()``. Weights are long-only,
fully invested and capped at ``config["portfolio"]["weight_cap"]``. Means and
covariances are annualised with ``config["trading_days"]``.

Mean uncertainty is expressed in annual units. The sample mean of T daily returns
has covariance Sigma_daily / T, so the annualised mean has covariance
Sigma_ann / T_years with T_years = T / trading_days. Both uncertainty sets below
use T_years for that reason.
"""
import numpy as np
import cvxpy as cp
from scipy.stats import chi2
from sklearn.covariance import LedoitWolf

from src.markowitz import _check_cap, _solve, annualise, max_sharpe, min_variance
from src.utils import get_logger

log = get_logger(__name__)


def _setup(returns_window, config):
    cap = config["portfolio"]["weight_cap"]
    td = config["trading_days"]
    mu, cov = annualise(returns_window, td)
    t_years = len(returns_window) / td
    return cap, config["risk_free_rate"], mu, cov, t_years


def ledoit_wolf_shrinkage(returns_window):
    """Ledoit-Wolf shrinkage coefficient in [0, 1] for the daily returns."""
    return float(LedoitWolf().fit(returns_window.to_numpy()).shrinkage_)


def ledoit_wolf_min_variance(returns_window, config):
    """Minimum-variance weights on the Ledoit-Wolf shrunk covariance matrix.
    Use ``ledoit_wolf_shrinkage`` to read the shrinkage coefficient."""
    cap = config["portfolio"]["weight_cap"]
    cov = LedoitWolf().fit(returns_window.to_numpy()).covariance_ * config["trading_days"]
    return min_variance(cov, cap)


def michaud_resampled(returns_window, config):
    """Michaud resampled efficiency.

    Draw ``robust.n_draws`` samples of T daily returns from N(mu_hat, Sigma_hat)
    (sample moments of the window), re-estimate mean and covariance on each sample,
    solve max-Sharpe with ``src.markowitz.max_sharpe`` and average the weights.
    Draws where no capped portfolio has positive excess return are infeasible; they
    are skipped and counted in a warning. If every draw fails a ValueError is raised.
    The generator is seeded from ``config["seed"]``, so results are reproducible."""
    cap = config["portfolio"]["weight_cap"]
    rf, td = config["risk_free_rate"], config["trading_days"]
    r = returns_window.to_numpy()
    t, n = r.shape
    _check_cap(n, cap)
    mu_d, cov_d = r.mean(axis=0), np.cov(r, rowvar=False)
    rng = np.random.default_rng(config["seed"])
    n_draws = config["robust"]["n_draws"]
    acc, failed = np.zeros(n), 0
    for _ in range(n_draws):
        sim = rng.multivariate_normal(mu_d, cov_d, size=t)
        try:
            acc += max_sharpe(sim.mean(axis=0) * td, np.cov(sim, rowvar=False) * td, rf, cap)
        except ValueError:
            failed += 1
    ok = n_draws - failed
    if ok == 0:
        raise ValueError("Michaud: max-Sharpe infeasible in every draw")
    if failed:
        log.warning("Michaud: %d of %d draws infeasible and skipped", failed, n_draws)
    return acc / ok


def worst_case_box(returns_window, config, z=None):
    """Worst-case mean-variance with mu in a box around the sample mean.

    Uncertainty set: mu_i in [mu_hat_i - d_i, mu_hat_i + d_i], with
    d_i = z * sigma_i / sqrt(T_years) (sigma_i annualised volatility, z from
    ``robust.box_z`` unless given).

    Reduction. For a long-only portfolio (w >= 0) the return mu'w is increasing in every
    mu_i, so its minimum over the box is attained at the lower endpoint mu_lo = mu_hat - d:
    min over the box of mu'w = (mu_hat - d)'w. The variance w' Sigma w does not depend on mu.
    The robust problem is therefore

        maximise  (mu_hat - d)'w - lambda * w' Sigma w   over  1'w = 1, 0 <= w <= cap,

    with lambda = ``robust.risk_aversion`` (annualised units). The objective is concave and the
    feasible set is a non-empty compact convex set (checked against the cap), so a solution
    always exists; unlike a Sharpe-ratio objective this never needs a positive excess return.
    z = 0 gives plain mean-variance, whose solution lies on the Markowitz efficient frontier.

    Scale note: this objective uses lambda * variance, whereas ``worst_case_ellipsoid`` uses
    (lambda / 2) * variance, so the same ``risk_aversion`` value means twice the effective risk
    aversion here."""
    cap, rf, mu, cov, t_years = _setup(returns_window, config)
    n = len(mu)
    _check_cap(n, cap)
    z = config["robust"]["box_z"] if z is None else z
    delta = z * np.sqrt(np.diag(cov)) / np.sqrt(t_years)
    lam = config["robust"]["risk_aversion"]
    chol = np.linalg.cholesky(cov + 1e-12 * np.eye(n))
    w = cp.Variable(n)
    objective = (mu - delta) @ w - lam * cp.sum_squares(chol.T @ w)
    prob = cp.Problem(cp.Maximize(objective), [cp.sum(w) == 1, w >= 0, w <= cap])
    _solve(prob)
    return np.clip(w.value, 0.0, cap)


def worst_case_ellipsoid(returns_window, config, kappa=None):
    """Worst-case mean-variance with mu in an ellipsoid around the sample mean.

    Uncertainty set: {mu : (mu - mu_hat)' Sigma^-1 (mu - mu_hat) <= kappa^2 / T_years},
    kappa = sqrt(chi2.ppf(``robust.ellipsoid_confidence``, N)) unless given.

    Derivation. Write Sigma = L L' and mu = mu_hat + L u with ||u|| <= kappa/sqrt(T_years).
    Then mu'w = mu_hat'w + u' L'w >= mu_hat'w - ||u|| ||L'w|| by Cauchy-Schwarz, with
    equality at u = -(kappa/sqrt(T_years)) L'w / ||L'w||. Hence

        min over the set of mu'w = mu_hat'w - kappa * ||Sigma^(1/2) w|| / sqrt(T_years),

    a second-order-cone term (||L'w|| = sqrt(w' Sigma w)). The problem solved is

        maximise  mu_hat'w - kappa ||L'w|| / sqrt(T_years) - (lambda / 2) w' Sigma w

    with lambda = ``robust.risk_aversion``, over the capped simplex.

    Why not max-Sharpe here: the worst-case Sharpe ratio is
    (mu_hat'w - rf) / sqrt(w' Sigma w) - kappa / sqrt(T_years). The shift does not
    depend on w, so the maximiser is the nominal max-Sharpe portfolio for every kappa and the
    ellipsoid would have no effect. The mean-variance form keeps the uncertainty
    meaningful. kappa = 0 gives plain mean-variance, whose solution lies on the
    Markowitz efficient frontier."""
    cap, rf, mu, cov, t_years = _setup(returns_window, config)
    n = len(mu)
    _check_cap(n, cap)
    if kappa is None:
        kappa = float(np.sqrt(chi2.ppf(config["robust"]["ellipsoid_confidence"], n)))
    lam = config["robust"]["risk_aversion"]
    chol = np.linalg.cholesky(cov + 1e-12 * np.eye(n))
    w = cp.Variable(n)
    risk = cp.norm(chol.T @ w, 2)
    objective = mu @ w - kappa / np.sqrt(t_years) * risk - 0.5 * lam * cp.square(risk)
    prob = cp.Problem(cp.Maximize(objective), [cp.sum(w) == 1, w >= 0, w <= cap])
    _solve(prob)
    return np.clip(w.value, 0.0, cap)


def cvar_min(returns_window, config, min_return=None):
    """Minimum CVaR on historical scenarios (Rockafellar-Uryasev linear program).

        minimise   zeta + sum_t u_t / ((1 - alpha) T)
        subject to u_t >= -r_t'w - zeta,  u_t >= 0,  1'w = 1,  0 <= w <= cap

    alpha = ``risk.cvar_alpha``. Optional constraint mu_hat'w >= min_return on the
    annualised mean (argument, else ``robust.cvar_min_return``; None = no constraint)."""
    cap = config["portfolio"]["weight_cap"]
    alpha = config["risk"]["cvar_alpha"]
    r = returns_window.to_numpy()
    t, n = r.shape
    _check_cap(n, cap)
    min_return = config["robust"]["cvar_min_return"] if min_return is None else min_return
    w, zeta, u = cp.Variable(n), cp.Variable(), cp.Variable(t, nonneg=True)
    cons = [u >= -r @ w - zeta, cp.sum(w) == 1, w >= 0, w <= cap]
    if min_return is not None:
        cons.append(r.mean(axis=0) * config["trading_days"] @ w >= min_return)
    prob = cp.Problem(cp.Minimize(zeta + cp.sum(u) / ((1 - alpha) * t)), cons)
    _solve(prob)
    return np.clip(w.value, 0.0, cap)


def equal_weight(returns_window, config):
    """1/N weights. The cap is respected only if 1/N <= cap; otherwise a ValueError
    is raised (this method cannot satisfy the cap by construction)."""
    n = returns_window.shape[1]
    cap = config["portfolio"]["weight_cap"]
    if 1.0 / n > cap + 1e-12:
        raise ValueError(f"1/N = {1.0 / n:.4f} exceeds the weight cap {cap} for N = {n}")
    return np.full(n, 1.0 / n)


def portfolio_cvar(w, returns_window, alpha):
    """Empirical CVaR of daily portfolio losses (loss = -return) at level alpha,
    computed as min over zeta in the sample losses of zeta + mean((L - zeta)+) / (1 - alpha).
    Independent of the LP above."""
    losses = -np.asarray(returns_window) @ np.asarray(w)
    zetas = losses[:, None]
    vals = zetas[:, 0] + np.maximum(losses[None, :] - zetas, 0.0).mean(axis=1) / (1 - alpha)
    return float(vals.min())


METHODS = {
    "ledoit_wolf_min_variance": ledoit_wolf_min_variance,
    "michaud_resampled": michaud_resampled,
    "worst_case_box": worst_case_box,
    "worst_case_ellipsoid": worst_case_ellipsoid,
    "cvar_min": cvar_min,
    "equal_weight": equal_weight,
}
