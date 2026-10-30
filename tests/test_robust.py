import copy

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import brute

from src import robust
from src.markowitz import efficient_frontier, annualise
from src.utils import load_config

BASE = load_config()
CAP = BASE["portfolio"]["weight_cap"]


def cfg(**overrides):
    c = copy.deepcopy(BASE)
    c["robust"]["n_draws"] = 20          # keep resampling fast in tests
    for k, v in overrides.items():
        c["robust"][k] = v
    return c


@pytest.fixture(scope="module")
def window():
    rng = np.random.default_rng(BASE["seed"])
    t, n = 250, 15
    a = rng.normal(size=(n, n))
    cov = (a @ a.T / n + 0.5 * np.eye(n)) * 1e-4
    mu = rng.uniform(0.0002, 0.0009, size=n)       # daily; some assets beat rf, some do not
    return pd.DataFrame(rng.multivariate_normal(mu, cov, size=t))


def check_weights(w, cap=CAP):
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert w.min() >= -1e-9
    assert w.max() <= cap + 1e-9


@pytest.mark.parametrize("name", list(robust.METHODS))
def test_all_methods_valid_weights(window, name):
    check_weights(robust.METHODS[name](window, cfg()))


def test_equal_weight_raises_when_cap_violated(window):
    c = cfg()
    c["portfolio"]["weight_cap"] = 0.05           # 1/15 > 0.05
    with pytest.raises(ValueError, match="cap"):
        robust.equal_weight(window, c)


def _slsqp_mean_variance(mu, cov, lam, cap):
    from scipy.optimize import minimize
    n = len(mu)
    res = minimize(lambda w: -(mu @ w - lam * w @ cov @ w), np.ones(n) / n, method="SLSQP",
                   bounds=[(0, cap)] * n, constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1}],
                   options={"ftol": 1e-14, "maxiter": 1000})
    assert res.success
    return res.x


def test_box_zero_uncertainty_is_markowitz_mean_variance(window):
    mu, cov = annualise(window, BASE["trading_days"])
    lam = BASE["robust"]["risk_aversion"]
    w = robust.worst_case_box(window, cfg(), z=0.0)
    check_weights(w)
    ref = _slsqp_mean_variance(mu, cov, lam, CAP)
    utility = lambda x: mu @ x - lam * x @ cov @ x                      # noqa: E731
    assert utility(w) == pytest.approx(utility(ref), abs=1e-8)
    np.testing.assert_allclose(w, ref, atol=1e-4)
    # the solution lies on the Markowitz efficient frontier
    fr, _ = efficient_frontier(mu, cov, CAP, n_points=400)
    r, vol = float(mu @ w), float(np.sqrt(w @ cov @ w))
    assert fr["return"].iloc[0] - 1e-6 <= r <= fr["return"].iloc[-1] + 1e-6
    assert vol == pytest.approx(np.interp(r, fr["return"], fr["vol"]), abs=2e-4)


def test_box_positive_z_changes_weights_and_uses_lower_endpoint(window):
    # a loose cap and a wide box: with a tight cap the solution is bang-bang and ranks barely move
    c = cfg()
    c["portfolio"]["weight_cap"] = 0.5
    mu, cov = annualise(window, BASE["trading_days"])
    lam = BASE["robust"]["risk_aversion"]
    z = 3.0
    w0 = robust.worst_case_box(window, c, z=0.0)
    w1 = robust.worst_case_box(window, c, z=z)
    check_weights(w0, 0.5)
    check_weights(w1, 0.5)
    assert np.abs(w1 - w0).max() > 1e-3
    t_years = len(window) / BASE["trading_days"]
    mu_lo = mu - z * np.sqrt(np.diag(cov)) / np.sqrt(t_years)
    np.testing.assert_allclose(w1, _slsqp_mean_variance(mu_lo, cov, lam, 0.5), atol=1e-4)


def test_box_is_always_feasible_even_when_every_excess_return_is_negative():
    rng = np.random.default_rng(9)
    t, n = 250, 15
    r = pd.DataFrame(rng.normal(-0.001, 0.012, size=(t, n)))             # strongly negative drift, far below rf
    w = robust.worst_case_box(r, cfg())
    check_weights(w)
    with pytest.raises(ValueError):                                      # a Sharpe objective would be infeasible here
        from src.markowitz import max_sharpe
        mu, cov = annualise(r, BASE["trading_days"])
        max_sharpe(mu, cov, BASE["risk_free_rate"], CAP)


def test_ellipsoid_zero_uncertainty_lies_on_markowitz_frontier(window):
    mu, cov = annualise(window, BASE["trading_days"])
    w = robust.worst_case_ellipsoid(window, cfg(), kappa=0.0)
    check_weights(w)
    fr, _ = efficient_frontier(mu, cov, CAP, n_points=400)
    r, vol = float(mu @ w), float(np.sqrt(w @ cov @ w))
    assert fr["return"].iloc[0] - 1e-6 <= r <= fr["return"].iloc[-1] + 1e-6
    assert vol == pytest.approx(np.interp(r, fr["return"], fr["vol"]), abs=2e-4)


def test_ellipsoid_positive_kappa_changes_weights(window):
    w0 = robust.worst_case_ellipsoid(window, cfg(), kappa=0.0)
    w1 = robust.worst_case_ellipsoid(window, cfg())
    assert np.abs(w1 - w0).max() > 1e-4


def test_ledoit_wolf_shrinkage_in_unit_interval(window):
    s = robust.ledoit_wolf_shrinkage(window)
    assert 0.0 <= s <= 1.0


def test_michaud_reproducible_with_seed_and_seed_matters(window):
    a = robust.michaud_resampled(window, cfg())
    b = robust.michaud_resampled(window, cfg())
    np.testing.assert_array_equal(a, b)
    c = cfg()
    c["seed"] = 7
    assert np.abs(robust.michaud_resampled(window, c) - a).max() > 1e-6


def test_cvar_min_matches_scipy_brute_force_on_tiny_problem():
    rng = np.random.default_rng(3)
    r = pd.DataFrame(rng.normal([0.001, 0.0005, 0.0], [0.02, 0.01, 0.005], size=(40, 3)))
    c = cfg()
    c["portfolio"]["weight_cap"] = 0.8
    alpha = c["risk"]["cvar_alpha"]
    w = robust.cvar_min(r, c)
    check_weights(w, cap=0.8)
    lp_val = robust.portfolio_cvar(w, r, alpha)

    def objective(x):
        w3 = np.array([x[0], x[1], 1.0 - x[0] - x[1]])
        if w3.min() < 0 or w3.max() > 0.8:
            return 1e3
        return robust.portfolio_cvar(w3, r, alpha)

    best = brute(objective, ranges=((0, 0.8), (0, 0.8)), Ns=161, finish=None, full_output=True)
    assert lp_val <= best[1] + 1e-9           # LP is at least as good as any grid point
    assert lp_val >= best[1] - 5e-4           # and the grid gets within its resolution


def test_cvar_min_return_constraint_binds():
    rng = np.random.default_rng(5)
    r = pd.DataFrame(rng.normal([0.002, 0.0005, 0.0], [0.02, 0.01, 0.005], size=(60, 3)))
    c = cfg()
    c["portfolio"]["weight_cap"] = 0.8
    mu = r.mean().to_numpy() * c["trading_days"]
    free = robust.cvar_min(r, c)
    target = float(mu @ free) + 0.05
    w = robust.cvar_min(r, c, min_return=target)
    assert float(mu @ w) >= target - 1e-6
