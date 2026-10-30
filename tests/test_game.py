import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import game as g
from src import robust, wdro
from src.utils import load_config

BASE = load_config()
GC, OPTS = BASE["game"], BASE["solver_own"]
TD = BASE["trading_days"]
LAM = BASE["robust"]["risk_aversion"]
CAP = 0.4


def random_matrices(count, seed=GC["wasserstein_seed"]):
    rng = np.random.default_rng(seed)
    return [rng.normal(size=(int(rng.integers(2, 8)), int(rng.integers(2, 8)))) for _ in range(count)]


# ---- Part A: matrix games -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("idx", range(25))
def test_lp_values_equal_for_both_players_and_strategies_are_best_responses(idx):
    a = random_matrices(25)[idx]
    s = g.solve_matrix_game(a)
    assert s["gap"] <= GC["lp_value_tol"]
    assert s["x"].sum() == pytest.approx(1.0) and s["y"].sum() == pytest.approx(1.0) and s["x"].min() >= 0 and s["y"].min() >= 0
    r = g.saddle_residuals(a, s["x"], s["y"])
    assert r["row_gap"] <= GC["best_response_tol"] and r["col_gap"] <= GC["best_response_tol"]
    assert r["payoff"] == pytest.approx(s["value_row"], abs=1e-8)
    lo, hi = g.pure_bounds(a)
    assert lo - 1e-9 <= s["value_row"] <= hi + 1e-9                       # pure maximin <= value <= pure minimax


def test_dual_variables_of_the_investor_lp_give_natures_strategy_when_the_solution_is_unique():
    s = g.solve_matrix_game(g.TWO_BY_TWO)
    np.testing.assert_allclose(s["x"], [0.6, 0.4], atol=1e-9)
    np.testing.assert_allclose(s["y"], [0.5, 0.5], atol=1e-9)
    np.testing.assert_allclose(s["dual_y"], s["y"], atol=1e-9)
    assert s["value_row"] == pytest.approx(1.0)


def test_teaching_example_maximin_picks_b_and_minimax_regret_picks_c():
    ex = g.teaching_example()
    assert ex["wald"] == "B" and ex["savage"] == "C"
    assert ex["security"] == 5 and ex["max_regret"] == 2
    np.testing.assert_array_equal(ex["regret"], [[0, 4], [4, 0], [2, 1]])


def test_regret_matrix_has_a_zero_in_every_column_and_no_negative_entries():
    mats = random_matrices(20) + [g.TEACHING_PAYOFF]
    saved = pd.read_csv(BASE["game"]["six_scenario_table"], index_col=0)
    mats.append(saved.to_numpy())
    for a in mats:
        r = g.regret_matrix(a)
        assert (r >= -1e-15).all() and (r.min(axis=0) == 0).all()


def test_saved_six_scenario_matrix_is_degenerate_max_sharpe_has_zero_regret_everywhere():
    saved = pd.read_csv(BASE["game"]["six_scenario_table"], index_col=0)
    reg = pd.DataFrame(g.regret_matrix(saved.to_numpy()), index=saved.index, columns=saved.columns)
    assert (reg.loc["max_sharpe"] == 0).all()
    assert g.wald(saved.to_numpy())[0] == g.savage(saved.to_numpy())[0] == list(saved.index).index("max_sharpe")


def test_mixed_minimax_regret_is_no_worse_than_the_pure_rule_and_both_players_agree():
    for a in random_matrices(15):
        mr = g.minimax_regret_mixed(a)
        assert mr["gap"] <= GC["lp_value_tol"]
        assert mr["value"] <= g.savage(a)[1] + 1e-9
        assert mr["value"] >= -1e-9                                        # regrets are non-negative
        assert (mr["x"] @ mr["regret"]).max() == pytest.approx(mr["value"], abs=1e-8)


def test_era_payoff_matrix_shape_and_values_on_a_small_market():
    rng = np.random.default_rng(2)
    n, t = 6, 900
    r = pd.DataFrame(rng.normal(0.0004, 0.01, size=(t, n)), index=pd.bdate_range("2015-01-01", periods=t))
    w = {"equal_weight": np.full(n, 1 / n), "tilt": np.array([0.4, 0.3, 0.1, 0.1, 0.05, 0.05])}
    eras = {"e1": ("2015-01-01", "2016-06-30"), "e2": ("2016-07-01", "2018-12-31")}
    m = g.era_payoff_matrix(w, r, eras, BASE)
    assert m.shape == (2, 2) and list(m.columns) == ["e1", "e2"]
    win = r.loc[eras["e1"][0]:eras["e1"][1]]
    mu, cov = win.mean().to_numpy() * TD, win.cov().to_numpy() * TD
    assert m.loc["tilt", "e1"] == pytest.approx(g.sharpe_under(w["tilt"], mu, cov, BASE["risk_free_rate"]))


# ---- Part B: ellipsoid game ----------------------------------------------------------------------------------------

def small_market(n=6, t=500, seed=4):
    rng = np.random.default_rng(seed)
    b = rng.normal(size=(n, 3))
    cov_d = (b @ b.T / 3 + 0.4 * np.eye(n)) * 1e-4
    r = pd.DataFrame(rng.multivariate_normal(rng.uniform(0.0002, 0.0010, n), cov_d, size=t), columns=[f"A{i}" for i in range(n)])
    return r, r.mean().to_numpy() * TD, r.cov().to_numpy() * TD, t / TD


R6, MU6, COV6, YEARS6 = small_market()


def small_cfg():
    c = copy.deepcopy(BASE)
    c["portfolio"]["weight_cap"] = CAP
    return c


def test_kappa_zero_reproduces_the_plug_in_mean_variance_solution():
    w, _ = g.saddle_own(COV6, MU6, CAP, LAM, 0.0, YEARS6, OPTS, GC["golden_tol"])
    w_robust = robust.worst_case_ellipsoid(R6, small_cfg(), kappa=0.0)
    np.testing.assert_allclose(w, w_robust, atol=GC["ellipsoid_weight_tol"])
    v_plug = g.payoff(w, MU6, COV6, LAM)
    assert g.worst_case_payoff(w, MU6, COV6, LAM, 0.0, YEARS6) == pytest.approx(v_plug)
    np.testing.assert_allclose(g.nature_best_response(w, MU6, COV6, 0.0, YEARS6), MU6)
    value_mm, mu_c = g.minmax_cvxpy(COV6, MU6, CAP, LAM, 0.0, YEARS6)
    assert abs(value_mm - v_plug) <= GC["ellipsoid_value_tol"]


@pytest.mark.parametrize("kappa", [0.5, 2.0])
def test_both_orders_of_optimisation_agree_and_saddle_residuals_are_small(kappa):
    w, _ = g.saddle_own(COV6, MU6, CAP, LAM, kappa, YEARS6, OPTS, GC["golden_tol"])
    maxmin = g.worst_case_payoff(w, MU6, COV6, LAM, kappa, YEARS6)
    minmax, mu_c = g.minmax_cvxpy(COV6, MU6, CAP, LAM, kappa, YEARS6)
    assert abs(maxmin - minmax) <= GC["ellipsoid_value_tol"]
    # a min-max is never below a max-min; any shortfall is the reference solver's tolerance
    assert minmax >= maxmin - GC["ellipsoid_value_tol"]
    rng = np.random.default_rng(1)
    v = g.verify_saddle(COV6, MU6, CAP, LAM, kappa, YEARS6, w, OPTS, rng, 300, GC["golden_tol"])
    assert abs(v["maha_sq"] - kappa ** 2) <= GC["ellipsoid_boundary_tol"] * max(kappa ** 2, 1)
    assert v["investor_gap"] <= GC["ellipsoid_residual_tol"] and v["investor_gap"] >= -1e-9
    assert v["nature_gap"] >= -1e-12 and v["nature_local_gap"] >= -1e-10
    assert v["closed_form_value"] == pytest.approx(v["value_at_saddle"], abs=1e-12)
    np.testing.assert_allclose(v["mu_star"], mu_c, atol=1e-6)
    w_robust = robust.worst_case_ellipsoid(R6, small_cfg(), kappa=kappa)
    np.testing.assert_allclose(w, w_robust, atol=GC["ellipsoid_weight_tol"])
    # the own engine is at least as good as the SOCP reference, which runs at the default solver tolerance
    assert maxmin >= g.worst_case_payoff(w_robust, MU6, COV6, LAM, kappa, YEARS6) - 1e-9


def test_price_of_robustness_is_non_negative_and_nature_moves_mu_toward_lower_returns_of_held_assets():
    w0, _ = g.saddle_own(COV6, MU6, CAP, LAM, 0.0, YEARS6, OPTS, GC["golden_tol"])
    plug = g.payoff(w0, MU6, COV6, LAM)
    w1, _ = g.saddle_own(COV6, MU6, CAP, LAM, 1.0, YEARS6, OPTS, GC["golden_tol"])
    assert plug - g.worst_case_payoff(w1, MU6, COV6, LAM, 1.0, YEARS6) >= 0
    mu_star = g.nature_best_response(w1, MU6, COV6, 1.0, YEARS6)
    assert MU6 @ w1 - mu_star @ w1 == pytest.approx(np.sqrt(w1 @ COV6 @ w1 / YEARS6))     # Nature lowers the portfolio's mean by kappa * sigma_mu


# ---- Part C: Wasserstein ball -------------------------------------------------------------------------------------------

def wcfg(norm, confidence=0.95):
    c = copy.deepcopy(BASE)
    c["wdro"]["ground_norm"] = norm
    c["risk"]["cvar_alpha"] = confidence
    return c


def returns_sample(t, n=5, seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.01, size=(t, n)) * rng.uniform(0.6, 1.4, n)


@pytest.mark.parametrize("norm", [1, 2, "inf"])
@pytest.mark.parametrize("t", [60, 57])                                      # alpha * T integer (3) and non-integer (2.85)
def test_lower_bound_never_exceeds_upper_bound_and_constructed_cost_is_within_the_budget(norm, t):
    cfg = wcfg(norm)
    X = returns_sample(t)
    rng = np.random.default_rng(3)
    w = rng.dirichlet(np.ones(5))
    df = pd.DataFrame(X)
    for eps in (0.0, 0.001, 0.01):
        up, _ = g.worst_case_cvar_dual_lp(w, X, eps, cfg)
        closed = wdro.worst_case_cvar_bound(w, df, eps, cfg)
        con = g.construct_nature(w, X, eps, cfg)
        assert up == pytest.approx(closed, abs=1e-10)                       # the dual LP equals the closed-form reduction of src/wdro.py
        assert con["cvar"] <= up + 1e-12                                    # lower bound <= upper bound
        assert con["cost"] <= eps * (1 + GC["wasserstein_budget_tol"]) + 1e-15
        alpha = 1 - cfg["risk"]["cvar_alpha"]
        dual = g.dual_norm_direction(w, norm)[1]
        assert up == pytest.approx(con["empirical_cvar"] + eps * dual / alpha, abs=1e-10)       # empirical CVaR + eps ||w||_dual / alpha
        assert con["cvar"] == pytest.approx(up, abs=1e-10)                  # and the constructed distribution attains it
        for _ in range(20):
            cv, cost = g.random_nature(w, X, eps, cfg, rng)
            assert cv <= up + 1e-12 and cost <= eps * (1 + 1e-9) + 1e-15


@pytest.mark.parametrize("norm", [1, 2, "inf"])
def test_dual_norm_direction_attains_the_dual_norm_with_a_unit_vector(norm):
    w = np.array([0.1, 0.4, 0.2, 0.3])
    u, dual = g.dual_norm_direction(w, norm)
    assert np.linalg.norm(u, ord=g._norm_ord(norm)) == pytest.approx(1.0)
    assert w @ u == pytest.approx(dual)
    assert dual == pytest.approx(wdro._norm_val(w, wdro._DUAL[norm]))


def test_cvar_discrete_matches_the_empirical_cvar_of_src_robust():
    X = returns_sample(100)
    w = np.full(5, 0.2)
    for conf in (0.9, 0.95, 0.975):
        alpha = 1 - conf
        assert g.cvar_discrete(-(X @ w), np.full(100, 0.01), alpha) == pytest.approx(robust.portfolio_cvar(w, X, conf), abs=1e-12)


# ---- script exit status and notes -------------------------------------------------------------------------------------

def test_main_exits_nonzero_when_a_check_fails_and_zero_otherwise(monkeypatch, tmp_path, capsys):
    from scripts import run_game as script
    cfg = copy.deepcopy(BASE)
    cfg["paths"].update({"tables_dir": str(tmp_path / "t"), "figures_dir": str(tmp_path / "f"), "results_log": str(tmp_path / "log.md")})
    cfg["game"]["notes_path"] = str(tmp_path / "notes.md")
    cfg["data"]["processed_dir"] = str(tmp_path)
    pd.DataFrame(np.random.default_rng(0).normal(size=(300, 4)) * 0.01, index=pd.bdate_range("2020-01-01", periods=300)).to_parquet(tmp_path / "returns_simple.parquet")
    forced = {"tables": {}, "lines": [], "failures": ["forced failure"], "figdata": {}}
    monkeypatch.setattr(script, "run_all", lambda c, r: forced)
    args = type("A", (), {"log": False})()
    assert script.main(args, cfg) == 1
    assert "forced failure" in capsys.readouterr().err
    forced["failures"] = []
    assert script.main(args, cfg) == 0


def test_notes_generated_blocks_match_the_code():
    notes = Path(GC["notes_path"]).read_text(encoding="utf-8")
    assert "<!-- teaching-example:start -->\n" + g.teaching_markdown() + "\n<!-- teaching-example:end -->" in notes
    assert "<!-- lp-example:start -->\n" + g.lp_example_markdown() + "\n<!-- lp-example:end -->" in notes
    assert "(generated)" not in notes
    for name in ("cvxpy", "scipy", "linprog"):                          # the notes name no software
        assert name not in notes.lower()
