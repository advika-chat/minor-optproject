import copy

import numpy as np
import pandas as pd
import pytest

from src import wdro
from src.markowitz import min_variance
from src.robust import cvar_min, portfolio_cvar
from src.utils import load_config

BASE = load_config()


def cfg(cap=None, **wdro_overrides):
    c = copy.deepcopy(BASE)
    if cap is not None:
        c["portfolio"]["weight_cap"] = cap
    c["wdro"].update(wdro_overrides)
    return c


@pytest.fixture(scope="module")
def synthetic():
    rng = np.random.default_rng(BASE["seed"])
    t, n = 120, 8
    vols = np.linspace(0.008, 0.02, n)
    mu = np.linspace(0.0002, 0.0012, n)
    return pd.DataFrame(rng.normal(mu, vols, size=(t, n)))


def check_weights(w, cap):
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert w.min() >= -1e-9 and w.max() <= cap + 1e-9


@pytest.mark.parametrize("kind", ["cvar", "meanvar"])
@pytest.mark.parametrize("norm", [1, 2, "inf"])
def test_valid_weights_for_all_norms(synthetic, kind, norm):
    c = cfg(cap=0.4, ground_norm=norm)
    for eps in [0.0, 1e-3, 1e-1]:
        check_weights(wdro.wdro_weights(synthetic, eps, kind, c), 0.4)


def test_eps_zero_recovers_empirical_cvar_minimisation(synthetic):
    c = cfg(cap=0.4)
    w = wdro.wdro_weights(synthetic, 0.0, "cvar", c)
    w_ref = cvar_min(synthetic, c)
    alpha = c["risk"]["cvar_alpha"]
    assert portfolio_cvar(w, synthetic, alpha) == pytest.approx(
        portfolio_cvar(w_ref, synthetic, alpha), abs=1e-8)


def test_eps_zero_recovers_empirical_min_variance(synthetic):
    c = cfg(cap=0.4)
    w = wdro.wdro_weights(synthetic, 0.0, "meanvar", c)
    cov = np.cov(synthetic.to_numpy(), rowvar=False, bias=True)
    w_ref = min_variance(cov, 0.4)
    assert w @ cov @ w == pytest.approx(w_ref @ cov @ w_ref, rel=1e-6)
    np.testing.assert_allclose(w, w_ref, atol=1e-4)


@pytest.mark.parametrize("kind", ["cvar", "meanvar"])
def test_effective_positions_non_decreasing_and_move_to_equal_weight(synthetic, kind):
    c = cfg(cap=0.4)
    grid = np.logspace(-5, -1, 9)
    eff = []
    for eps in grid:
        w = wdro.wdro_weights(synthetic, eps, kind, c)
        eff.append(1.0 / np.sum(w ** 2))
    assert np.all(np.diff(eff) >= -1e-6), eff
    n = synthetic.shape[1]
    dist = lambda w: np.abs(w - 1.0 / n).max()  # noqa: E731
    w_small = wdro.wdro_weights(synthetic, grid[0], kind, c)
    w_huge = wdro.wdro_weights(synthetic, 10.0, kind, c)
    assert dist(w_huge) < dist(w_small)
    np.testing.assert_allclose(w_huge, 1.0 / n, atol=1e-3)


def test_worst_case_bound_not_below_empirical_cvar(synthetic):
    c = cfg(cap=0.4)
    sweep = wdro.sweep_eps(synthetic, np.logspace(-5, -1, 7), "cvar", c)
    assert (sweep["objective_value"] >= sweep["empirical_cvar"] - 1e-9).all()
    assert (sweep["worst_case_cvar_bound"] >= sweep["empirical_cvar"] - 1e-9).all()
    # the optimiser's value equals the closed-form bound of its own portfolio
    np.testing.assert_allclose(sweep["objective_value"], sweep["worst_case_cvar_bound"], atol=1e-7)


def test_reduction_identity_empirical_loss_plus_norm_penalty(synthetic):
    c = cfg(cap=0.4)
    eps = 2e-3
    sol = wdro.wdro_solve(synthetic, eps, "cvar", c)
    w, tau = sol["weights"], sol["tau"]
    a, m, (a1, b1, a2, b2) = wdro._cvar_coeffs(c)
    s = synthetic.to_numpy() @ w
    emp = np.mean(np.maximum(a1 * s + b1 * tau, a2 * s + b2 * tau))
    pen = eps * (m + 1 / a) * wdro._norm_val(w, wdro._dual_p(c))
    assert sol["value"] == pytest.approx(emp + pen, abs=1e-7)


# ---- tiny problem: bound vs brute-force search over adversarial distributions ----

def _cvar_discrete(losses, probs, a):
    vals = [tau + np.sum(probs * np.maximum(losses - tau, 0.0)) / a for tau in losses]
    return min(vals)


@pytest.mark.parametrize("norm", [1, 2])
def test_bound_against_brute_force_adversary_n3_t5(norm):
    rng = np.random.default_rng(11)
    r = pd.DataFrame(rng.normal([0.001, 0.0, -0.001], [0.02, 0.01, 0.015], size=(5, 3)))
    c = cfg(cap=0.8, ground_norm=norm)
    eps = 0.01
    sol = wdro.wdro_solve(r, eps, "cvar", c)
    w, tau, bound = sol["weights"], sol["tau"], sol["value"]
    a, m, (a1, b1, a2, b2) = wdro._cvar_coeffs(c)
    xi = r.to_numpy()
    t, n = xi.shape

    def ell(x):
        s = x @ w
        return np.maximum(a1 * s + b1 * tau, a2 * s + b2 * tau)

    def unit(v):
        return v / np.linalg.norm(v, ord=norm)

    best_ell, best_cvar = -np.inf, -np.inf

    def evaluate(moves):
        """moves: list of (scenario i, split probability p, displacement d)."""
        nonlocal best_ell, best_cvar
        cost = sum(p / t * np.linalg.norm(d, ord=norm) for _, p, d in moves)
        assert cost <= eps * (1 + 1e-9)
        pts, probs = [xi[i] for i in range(t)], [1.0 / t] * t
        probs = np.array(probs)
        extra_pts, extra_probs = [], []
        for i, p, d in moves:
            probs[i] -= p / t
            extra_pts.append(xi[i] + d)
            extra_probs.append(p / t)
        pts = np.vstack([np.array(pts)] + [np.array(extra_pts)]) if extra_pts else np.array(pts)
        probs = np.concatenate([probs, extra_probs]) if extra_pts else probs
        e = float(np.sum(probs * ell(pts)))
        cv = _cvar_discrete(-(pts @ w), probs, a)
        best_ell, best_cvar = max(best_ell, e), max(best_cvar, cv)

    # structured adversary: push a small fraction of one scenario far along the steepest direction
    steepest = [unit(-np.sign(w) * (w == w.max())) if norm == 1 else unit(-w)]
    for i in range(t):
        for p in [1e-9, 1e-6, 1e-3, 0.1, 1.0]:
            for u in steepest + [unit(-np.eye(n)[j]) for j in range(n)]:
                evaluate([(i, p, u * (eps * t / p))])
    # random adversaries: random subsets of scenarios, random splits and directions
    for _ in range(3000):
        k = rng.integers(1, t + 1)
        idx = rng.choice(t, size=k, replace=False)
        shares = rng.dirichlet(np.ones(k))
        moves = []
        for i, share in zip(idx, shares):
            p = 10 ** rng.uniform(-8, 0)
            d = unit(rng.normal(size=n)) * (eps * share * t / p)
            moves.append((int(i), p, d))
        evaluate(moves)

    assert best_ell <= bound + 1e-9              # no adversary beats the bound
    assert best_cvar <= bound + 1e-9             # in particular the true worst-case CVaR is below it
    assert best_ell >= bound - 1e-6              # and the structured adversary nearly attains it
