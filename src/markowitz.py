"""Mean-variance (Markowitz) portfolios with cvxpy.

All portfolios are long-only, fully invested (sum w = 1) and capped (w_i <= cap).
Inputs mu, cov are ANNUALISED; use `annualise` to build them from daily returns.

Max-Sharpe convex reformulation
-------------------------------
Maximising (mu'w - rf) / sqrt(w' S w) is not convex in w. Substitute
w = y / k with k = 1'y > 0 (Charnes-Cooper style) and normalise the excess
return of y to 1:

    minimise    y' S y
    subject to  (mu - rf)' y = 1
                1'y = k,   k >= 0
                0 <= y <= cap * k

Dividing the constraints by k recovers w = y/k with sum w = 1 and 0 <= w <= cap.
Because (mu - rf)'y = 1, the Sharpe ratio of w is 1 / sqrt(y' S y), so
minimising y' S y maximises the Sharpe ratio. The problem is a convex QP.
It needs at least one feasible portfolio with mu'w > rf; otherwise it is
infeasible and a ValueError is raised.
"""
import cvxpy as cp
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def annualise(daily_returns, trading_days=TRADING_DAYS):
    """Annualised mean vector and covariance matrix from daily simple returns."""
    mu = daily_returns.mean().to_numpy() * trading_days
    cov = daily_returns.cov().to_numpy() * trading_days
    return mu, cov


def portfolio_stats(w, mu, cov, rf=0.0):
    ret = float(mu @ w)
    vol = float(np.sqrt(w @ cov @ w))
    return {"return": ret, "vol": vol, "sharpe": (ret - rf) / vol}


def _solve(prob):
    prob.solve(solver=cp.CLARABEL)
    if prob.status not in ("optimal", "optimal_inaccurate"):
        raise ValueError(f"cvxpy status: {prob.status}")


def _check_cap(n, cap):
    if cap * n < 1 - 1e-12:
        raise ValueError(f"cap {cap} infeasible for {n} assets (need cap*n >= 1)")


def min_variance(cov, cap):
    n = cov.shape[0]
    _check_cap(n, cap)
    w = cp.Variable(n)
    prob = cp.Problem(
        cp.Minimize(cp.quad_form(w, cp.psd_wrap(cov))),
        [cp.sum(w) == 1, w >= 0, w <= cap],
    )
    _solve(prob)
    return np.clip(w.value, 0.0, cap)


def max_sharpe(mu, cov, rf, cap):
    n = cov.shape[0]
    _check_cap(n, cap)
    y = cp.Variable(n)
    k = cp.Variable(nonneg=True)
    prob = cp.Problem(
        cp.Minimize(cp.quad_form(y, cp.psd_wrap(cov))),
        [(mu - rf) @ y == 1, cp.sum(y) == k, y >= 0, y <= cap * k],
    )
    _solve(prob)
    w = y.value / k.value
    return np.clip(w, 0.0, cap)


def efficient_frontier(mu, cov, cap, n_points=50):
    """Minimum-variance portfolios for target returns from the global
    min-variance return up to the highest return achievable under the cap.

    Returns (frontier DataFrame with columns return, vol; weights array
    of shape (n_points, n_assets))."""
    n = cov.shape[0]
    _check_cap(n, cap)

    w_mv = min_variance(cov, cap)
    r_lo = float(mu @ w_mv)

    w = cp.Variable(n)
    cons = [cp.sum(w) == 1, w >= 0, w <= cap]
    max_prob = cp.Problem(cp.Maximize(mu @ w), cons)
    _solve(max_prob)
    r_hi = float(mu @ w.value)

    targets = np.linspace(r_lo, r_hi, n_points)
    rows, weights = [], []
    for t in targets:
        wv = cp.Variable(n)
        prob = cp.Problem(
            cp.Minimize(cp.quad_form(wv, cp.psd_wrap(cov))),
            [cp.sum(wv) == 1, wv >= 0, wv <= cap, mu @ wv >= t],
        )
        _solve(prob)
        wt = np.clip(wv.value, 0.0, cap)
        weights.append(wt)
        rows.append({"return": float(mu @ wt), "vol": float(np.sqrt(wt @ cov @ wt))})
    return pd.DataFrame(rows), np.array(weights)
