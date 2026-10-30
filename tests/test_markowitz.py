import numpy as np
import pytest
from scipy.optimize import minimize

from src.markowitz import efficient_frontier, max_sharpe, min_variance, portfolio_stats
from src.utils import load_config

CFG = load_config()
CAP = CFG["portfolio"]["weight_cap"]
RF = CFG["risk_free_rate"]


@pytest.fixture(scope="module")
def case():
    rng = np.random.default_rng(CFG["seed"])
    n = 15
    a = rng.normal(size=(n, n))
    cov = (a @ a.T / n + 0.05 * np.eye(n)) * 0.05
    mu = rng.uniform(0.04, 0.22, size=n)
    return mu, cov


def _valid(w):
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert w.min() >= -1e-9
    assert w.max() <= CAP + 1e-9


def test_min_variance_constraints(case):
    _, cov = case
    _valid(min_variance(cov, CAP))


def test_max_sharpe_constraints(case):
    mu, cov = case
    _valid(max_sharpe(mu, cov, RF, CAP))


def test_frontier_monotone_and_valid(case):
    mu, cov = case
    fr, ws = efficient_frontier(mu, cov, CAP, n_points=20)
    for w in ws:
        _valid(w)
    assert (np.diff(fr["return"]) >= -1e-9).all()
    assert (np.diff(fr["vol"]) >= -1e-7).all()


def test_max_sharpe_beats_min_variance_and_equal_weight(case):
    mu, cov = case
    n = len(mu)
    s_ms = portfolio_stats(max_sharpe(mu, cov, RF, CAP), mu, cov, RF)["sharpe"]
    s_mv = portfolio_stats(min_variance(cov, CAP), mu, cov, RF)["sharpe"]
    s_ew = portfolio_stats(np.ones(n) / n, mu, cov, RF)["sharpe"]
    assert s_ms >= s_mv - 1e-9
    assert s_ms >= s_ew - 1e-9


def _slsqp(fun, n, x0):
    res = minimize(
        fun, x0, method="SLSQP", bounds=[(0, CAP)] * n,
        constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    assert res.success
    return res.x


def test_min_variance_matches_slsqp(case):
    _, cov = case
    n = len(cov)
    w_cvx = min_variance(cov, CAP)
    w_sp = _slsqp(lambda w: w @ cov @ w, n, np.ones(n) / n)
    assert w_cvx @ cov @ w_cvx == pytest.approx(w_sp @ cov @ w_sp, rel=1e-5)
    np.testing.assert_allclose(w_cvx, w_sp, atol=1e-3)


def test_max_sharpe_matches_slsqp(case):
    mu, cov = case
    n = len(mu)
    w_cvx = max_sharpe(mu, cov, RF, CAP)
    w_sp = _slsqp(lambda w: -(mu @ w - RF) / np.sqrt(w @ cov @ w), n, np.ones(n) / n)
    s_cvx = portfolio_stats(w_cvx, mu, cov, RF)["sharpe"]
    s_sp = portfolio_stats(w_sp, mu, cov, RF)["sharpe"]
    assert s_cvx == pytest.approx(s_sp, rel=1e-5)
