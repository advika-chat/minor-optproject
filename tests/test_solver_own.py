import copy
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import run_solver_own as script
from src import markowitz
from src import solver_own as so
from src.utils import load_config

BASE = load_config()
OPTS = BASE["solver_own"]
TOL = OPTS["kkt_residual_tol"]


def problems(count, seed=OPTS["seed"], with_targets=True):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(count):
        sigma, mu, cap, target = script.random_problem(rng, OPTS)
        out.append((sigma, mu, cap, target if with_targets else None))
    return out


PROBS = problems(30)


@pytest.mark.parametrize("idx", range(len(PROBS)))
def test_kkt_residuals_below_tolerance_on_random_problems(idx):
    sigma, mu, cap, target = PROBS[idx]
    res = so.solve_qp(sigma, mu, cap, OPTS, target=target)
    assert res.ok, res.status
    kk = so.kkt_residuals(sigma, mu, cap, res)
    assert max(kk.values()) <= TOL, kk


def test_multiplier_signs_and_complementary_support():
    for sigma, mu, cap, target in PROBS:
        res = so.solve_qp(sigma, mu, cap, OPTS, target=target)
        assert res.lam_lower.min() >= -OPTS["dual_tol"] and res.lam_upper.min() >= -OPTS["dual_tol"]
        w = res.weights
        # a multiplier can be nonzero only where the constraint is active
        assert np.all(res.lam_lower[w > 1e-9] == 0) and np.all(res.lam_upper[w < res.lam_upper.size * 0 + (cap - 1e-9)] == 0)
        assert set(res.active_lower).isdisjoint(res.active_upper) or cap == 0


def test_multipliers_equal_finite_difference_sensitivities():
    d = OPTS["fd_relative_step"]
    compared = {"cap": 0, "budget": 0, "return": 0}
    for sigma, mu, cap, target in PROBS:
        if abs(cap - 1.0 / len(mu)) < 1e-9:
            continue                                               # single feasible point: multipliers are not unique
        res = so.solve_qp(sigma, mu, cap, OPTS, target=target)
        sets = (res.active_lower, res.active_upper)

        def fd(solve, step):
            lo, hi = solve(-step), solve(step)
            same = lo.ok and hi.ok and (lo.active_lower, lo.active_upper) == sets == (hi.active_lower, hi.active_upper)
            return ((hi.objective - lo.objective) / (2 * step) if same else None)

        cases = [("cap", lambda s: so.solve_qp(sigma, mu, cap + s, OPTS, target=target), d * cap, -res.lam_upper.sum()),
                 ("budget", lambda s: so.solve_qp(sigma, mu, cap, OPTS, target=target, budget=1.0 + s), d, res.nu_budget)]
        if target is not None:
            r_min, r_max, _, _ = so.feasible_return_range(mu, cap)
            if r_max - r_min > 1e-6:
                cases.append(("return", lambda s: so.solve_qp(sigma, mu, cap, OPTS, target=target + s), d * (r_max - r_min), res.nu_return))
        for name, solve, step, pred in cases:
            val = fd(solve, step)
            if val is None:
                continue
            compared[name] += 1
            assert abs(val - pred) <= OPTS["fd_rel_tol"] * max(abs(pred), abs(val)) + OPTS["fd_abs_floor"], (name, val, pred)
    assert all(v >= 5 for v in compared.values()), compared


def test_agreement_with_cvxpy_on_random_problems():
    tol = OPTS["cvxpy_solver_tol"]
    for sigma, mu, cap, target in PROBS:
        res = so.solve_qp(sigma, mu, cap, OPTS, target=target)
        wc = script.cvx_solve(sigma, mu, cap, target, tol)
        dw, do = script.compare(res, wc, sigma)
        assert dw <= OPTS["agree_weight_tol"] and do <= OPTS["agree_objective_rel_tol"], (dw, do)
        # the own solution is never worse than the reference
        assert res.objective <= wc @ sigma @ wc + 1e-12


def test_degenerate_problem_terminates_and_cycling_guard_gives_same_answer():
    n = 10
    sigma = 0.05 * np.eye(n)
    mu = np.array([0.1] * 5 + [0.2] * 5)                       # many ties: highly degenerate working sets
    cap, target = 0.3, 0.14
    normal = so.solve_qp(sigma, mu, cap, OPTS, target=target)
    forced = so.solve_qp(sigma, mu, cap, dict(OPTS, cycle_guard_zero_steps=0), target=target)       # Bland's rule from the start
    assert normal.ok and forced.ok and forced.bland_used
    assert normal.iterations < OPTS["max_iterations"] and forced.iterations < OPTS["max_iterations"]
    np.testing.assert_allclose(normal.weights, forced.weights, atol=1e-9)
    assert max(so.kkt_residuals(sigma, mu, cap, forced).values()) <= TOL
    # ties in mu and variance for every asset, target at the common return
    flat = so.solve_qp(sigma, np.full(n, 0.1), cap, OPTS, target=0.1)
    assert flat.ok and np.allclose(flat.weights, 1.0 / n, atol=1e-9)


def test_golden_section_matches_fine_grid_maximum():
    rf = BASE["risk_free_rate"]
    for sigma, mu, cap, _ in PROBS[:6]:
        if cap * len(mu) < 1.05:
            continue
        ms = so.max_sharpe(sigma, mu, cap, rf, OPTS)
        assert ms.status == so.OPTIMAL
        grid_r, grid_s = so.sharpe_profile(sigma, mu, cap, rf, OPTS, n_points=400)
        assert so.count_valleys(grid_s, OPTS["unimodality_noise_tol"])["n_valleys"] == 0
        k = int(np.argmax(grid_s))
        assert ms.sharpe >= grid_s[k] - 1e-12
        assert abs(ms.target_return - grid_r[k]) <= grid_r[1] - grid_r[0] + 1e-12
        assert ms.rf_above_min_variance_return == (rf >= ms.r_min_variance)


def test_golden_section_agrees_with_cvxpy_max_sharpe_regression():
    rng = np.random.default_rng(5)
    n = 15
    b = rng.normal(size=(n, 8))
    sigma, mu = b @ b.T / 8 + 0.01 * np.eye(n), rng.uniform(0.02, 0.25, n)
    cap, rf = 0.2, 0.01
    ms = so.max_sharpe(sigma, mu, cap, rf, OPTS)
    w_ref = markowitz.max_sharpe(mu, sigma, rf, cap)
    mv = so.min_variance(sigma, cap, OPTS)
    w_mv_ref = markowitz.min_variance(sigma, cap)
    assert np.abs(ms.weights - w_ref).max() <= OPTS["regression_weight_tol"]
    assert np.abs(mv.weights - w_mv_ref).max() <= OPTS["regression_weight_tol"]
    assert ms.sharpe == pytest.approx((mu @ w_ref - rf) / np.sqrt(w_ref @ sigma @ w_ref), abs=1e-6)


def test_infeasible_target_and_cap_statuses():
    sigma, mu, cap, _ = PROBS[3]
    r_min, r_max, _, _ = so.feasible_return_range(mu, cap)
    hi = so.solve_qp(sigma, mu, cap, OPTS, target=r_max + 1e-3)
    lo = so.solve_qp(sigma, mu, cap, OPTS, target=r_min - 1e-3)
    assert hi.status == lo.status == so.INFEASIBLE_TARGET and hi.weights is None and not hi.ok
    assert so.solve_qp(sigma, mu, 0.9 / len(mu), OPTS).status == so.INFEASIBLE_CAP
    # the end points themselves are feasible
    assert so.solve_qp(sigma, mu, cap, OPTS, target=r_max).ok and so.solve_qp(sigma, mu, cap, OPTS, target=r_min).ok


def test_achievable_return_range_is_exact_greedy_fill():
    mu = np.array([0.05, 0.20, 0.10, 0.15])
    r_min, r_max, w_min, w_max = so.feasible_return_range(mu, 0.4)
    np.testing.assert_allclose(w_max, [0.0, 0.4, 0.2, 0.4])      # 0.20 and 0.15 to the cap, remainder to 0.10
    np.testing.assert_allclose(w_min, [0.4, 0.0, 0.4, 0.2])
    assert r_max == pytest.approx(0.2 * 0.4 + 0.1 * 0.2 + 0.15 * 0.4)


def test_cap_equal_to_one_over_n_gives_equal_weights():
    sigma, mu, _, _ = PROBS[0]
    n = len(mu)
    res = so.solve_qp(sigma, mu, 1.0 / n, OPTS)
    assert res.ok and np.allclose(res.weights, 1.0 / n, atol=1e-12)
    assert res.objective == pytest.approx(np.full(n, 1.0 / n) @ sigma @ np.full(n, 1.0 / n))
    assert not res.multipliers_unique                       # the feasible set is one point
    assert so.solve_qp(sigma, mu, 1.0 / n, OPTS, target=float(mu.mean())).ok
    assert so.solve_qp(sigma, mu, 1.0 / n, OPTS, target=float(mu.mean()) + 1e-3).status == so.INFEASIBLE_TARGET


def test_edge_cases_near_singular_duplicated_asset_and_two_assets_agree_with_cvxpy():
    rng = np.random.default_rng(2)
    b = rng.normal(size=(20, 3))
    sigma, mu = b @ b.T / 3 + 1e-10 * np.eye(20), rng.uniform(0.02, 0.2, 20)
    target = float(np.mean(so.feasible_return_range(mu, 0.2)[:2]))
    res = so.solve_qp(sigma, mu, 0.2, OPTS, target=target)
    wc = script.cvx_solve(sigma, mu, 0.2, target, OPTS["cvxpy_solver_tol"])
    scale = float(np.mean(np.diag(sigma)))
    tol_abs = OPTS["near_singular_scaled_abs_tol"] * scale
    own_o, cvx_o = res.objective, float(wc @ sigma @ wc)
    assert res.ok and abs(own_o - cvx_o) <= tol_abs and own_o <= cvx_o + tol_abs     # optimal variance ~1e-12: relative comparison is meaningless
    assert max(so.kkt_residuals(sigma, mu, 0.2, res).values()) <= TOL
    # duplicated asset: identical mu, variance and covariances -> exactly singular Sigma; duplicate an asset that holds weight
    s0, m0, _, _ = PROBS[1]
    n = len(m0)
    cap0 = max(0.4, 2.0 / n)
    t0 = float(np.mean(so.feasible_return_range(m0, cap0)[:2]))
    base = so.solve_qp(s0, m0, cap0, OPTS, target=t0)
    free = [i for i in range(n) if 1e-6 < base.weights[i] < cap0 - 1e-6]
    j = max(free, key=lambda i: base.weights[i]) if free else int(np.argmax(base.weights))
    sd = np.pad(s0, ((0, 1), (0, 1)))
    sd[-1, :-1], sd[:-1, -1], sd[-1, -1] = s0[j], s0[j], s0[j, j]
    md = np.append(m0, m0[j])
    res = so.solve_qp(sd, md, cap0, OPTS, target=t0)
    wc = script.cvx_solve(sd, md, cap0, t0, OPTS["cvxpy_solver_tol"])
    assert res.ok and script.compare(res, wc, sd)[1] <= OPTS["edge_objective_rel_tol"]
    assert res.weights[j] + res.weights[-1] > 1e-3                                    # the duplicated pair actually carries weight
    assert res.weights[j] + res.weights[-1] == pytest.approx(wc[j] + wc[-1], abs=1e-4)
    assert max(so.kkt_residuals(sd, md, cap0, res).values()) <= TOL
    # N = 2
    s2, m2 = np.array([[0.04, 0.006], [0.006, 0.09]]), np.array([0.08, 0.15])
    for cap2, t2 in [(1.0, None), (1.0, 0.11), (0.7, 0.12), (0.7, None)]:
        r2 = so.solve_qp(s2, m2, cap2, OPTS, target=t2)
        assert r2.ok and script.compare(r2, script.cvx_solve(s2, m2, cap2, t2, OPTS["cvxpy_solver_tol"]), s2)[0] <= OPTS["agree_weight_tol"]


def test_solver_module_uses_numpy_only():
    src = Path(so.__file__).read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:import|from)\s+([\w\.]+)", src, flags=re.M)
    assert set(imports) <= {"numpy", "dataclasses"}, imports
    assert "scipy" not in src.replace("scipy.optimize", "") or "import scipy" not in src


# ---- script exit status ----------------------------------------------------------------------------------

def tmp_cfg(tmp_path):
    c = copy.deepcopy(BASE)
    c["paths"].update({"tables_dir": str(tmp_path / "tables"), "figures_dir": str(tmp_path / "figures"),
                       "results_log": str(tmp_path / "log.md")})
    return c


def test_main_exits_nonzero_when_a_verification_check_fails(monkeypatch, tmp_path, capsys):
    forced = {"kkt": pd.DataFrame(), "vs": pd.DataFrame(), "frontier": pd.DataFrame(), "lines": [], "failures": ["forced failure"], "fig": None}
    monkeypatch.setattr(script, "run_verifications", lambda cfg, mu, cov, tickers: forced)
    assert script.main(type("A", (), {"log": False})(), tmp_cfg(tmp_path)) == 1
    assert "forced failure" in capsys.readouterr().err


def test_run_verifications_reports_a_real_failed_check_on_synthetic_data(tmp_path):
    # small synthetic problem: the comparison with the logged real-data numbers must fail, and everything else should pass
    rng = np.random.default_rng(1)
    n = 12
    b = rng.normal(size=(n, 6))
    cov, mu = b @ b.T / 6 + 0.02 * np.eye(n), rng.uniform(0.05, 0.25, n)
    cfg = tmp_cfg(tmp_path)
    cfg["portfolio"]["weight_cap"] = 0.25
    cfg["solver_own"].update({"random_problems": 6, "unimodality_grid_points": 60, "frontier_points": 6})
    res = script.run_verifications(cfg, mu, cov, [f"A{i}" for i in range(n)])
    assert any("previously logged" in f for f in res["failures"])
    others = [f for f in res["failures"] if "previously logged" not in f]
    assert others == [], others
    assert set(res["kkt"]["kind"]) >= {"summary", "constraint", "sensitivity"}
