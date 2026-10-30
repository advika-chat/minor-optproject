import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import run_sensitivity as script
from scripts.run_solver_own import random_problem
from src import duality as du
from src import sensitivity as sn
from src import solver_own as so
from src.utils import load_config

BASE = load_config()
OPTS, SENS = BASE["solver_own"], BASE["sensitivity"]
TD = BASE["trading_days"]
RF = BASE["risk_free_rate"]
CAP = 0.3


def synthetic_returns(n=10, t=500, seed=3):
    rng = np.random.default_rng(seed)
    b = rng.normal(size=(n, 4))
    cov_d = (b @ b.T / 4 + 0.3 * np.eye(n)) * 1e-4
    mu_d = rng.uniform(0.0002, 0.0011, n)
    r = rng.multivariate_normal(mu_d, cov_d, size=t)
    return pd.DataFrame(r, index=pd.bdate_range("2015-01-01", periods=t), columns=[f"A{i}" for i in range(n)])


RETS = synthetic_returns()
MU, COV = sn.annualised_moments(RETS, TD)
YEARS = len(RETS) / TD


def small_cfg(tmp_path=None):
    c = copy.deepcopy(BASE)
    c["portfolio"]["weight_cap"] = CAP
    s = c["sensitivity"]
    s.update({"n_draws": 2, "crosscheck_draws": 2, "cap_grid": [0.1, 0.2, 0.3, 0.5, 1.0], "frontier_caps_plot": [0.2, 1.0],
              "rf_grid": [0.04, 0.065, 0.08], "calendar_years": [2015, 2016, 2017],
              "periods": {"first": ["2015-01-01", "2015-12-31"], "rest": ["2016-01-01", "2017-12-31"]},
              "shrinkage_grid": [0.0, 0.5, 1.0], "vol_scale_grid": [0.8, 1.0, 1.5], "corr_stress_grid": [0.0, 0.5]})
    s["duality"]["random_multipliers"] = 100
    if tmp_path is not None:
        c["paths"].update({"tables_dir": str(tmp_path / "tables"), "figures_dir": str(tmp_path / "figures"), "results_log": str(tmp_path / "log.md")})
        s["notes_path"] = str(tmp_path / "notes.md")
    return c


def test_zero_perturbation_gives_zero_change():
    chol = np.linalg.cholesky(COV)
    z = sn.make_draws(1, 3, len(MU))
    for zd in z:
        np.testing.assert_array_equal(sn.perturb_mu(MU, chol, YEARS, 0.0, zd), MU)
    df, _ = sn.mu_sensitivity(COV, MU, CAP, RF, OPTS, SENS, YEARS, z[:2], [0.0])
    assert df["l1_change"].max() <= SENS["zero_perturbation_tol"]
    assert df["loss_base_truth"].abs().max() <= 1e-10 and df["loss_draw_truth"].abs().max() <= 1e-10
    # zero covariance perturbation leaves Sigma unchanged
    np.testing.assert_allclose(sn.shrink(COV, 0.0, "identity"), COV)
    np.testing.assert_allclose(sn.scale_vol(COV, 1.0), COV)
    np.testing.assert_allclose(sn.stress_corr(COV, 0.0), COV, atol=1e-15)
    # base scenario: no shift, unit vol, no stress
    base = [s for s in SENS["scenarios"] if s["name"] == "base"][0]
    mu_s, cov_s, rf_s = sn.scenario_parameters(MU, COV, sn.standard_errors(COV, YEARS), base, RF)
    np.testing.assert_allclose(mu_s, MU)
    np.testing.assert_allclose(cov_s, COV, atol=1e-15)
    assert rf_s == RF


def test_seed_reproducibility():
    a, b, c = sn.make_draws(7, 5, 4), sn.make_draws(7, 5, 4), sn.make_draws(8, 5, 4)
    np.testing.assert_array_equal(a, b)
    assert np.abs(a - c).max() > 0
    z = sn.make_draws(SENS["seed"], 2, len(MU))
    d1, _ = sn.mu_sensitivity(COV, MU, CAP, RF, OPTS, SENS, YEARS, z, [0.5])
    d2, _ = sn.mu_sensitivity(COV, MU, CAP, RF, OPTS, SENS, YEARS, z, [0.5])
    pd.testing.assert_frame_equal(d1, d2)


def test_min_variance_weights_unchanged_under_mu_perturbation_and_losses_nonnegative():
    z = sn.make_draws(SENS["seed"], 4, len(MU))
    df, _ = sn.mu_sensitivity(COV, MU, CAP, RF, OPTS, SENS, YEARS, z, SENS["k_multipliers"])
    assert df["min_variance_l1_change"].max() <= SENS["min_variance_invariance_tol"]
    assert df["l1_change"].max() > 1e-3                        # but the max-Sharpe weights do move
    assert (df["loss_base_truth"] >= -1e-8).all() and (df["loss_draw_truth"] >= -1e-8).all()


def test_every_perturbed_or_scenario_sigma_is_psd():
    rng = np.random.default_rng(0)
    covs = [COV]
    for _ in range(4):
        n = int(rng.integers(3, 25))
        b = rng.normal(size=(n, max(2, n // 3)))
        covs.append(b @ b.T / b.shape[1] + 10 ** rng.uniform(-8, -1) * np.eye(n))      # includes nearly singular matrices
    for cov in covs:
        for tgt in sn.TARGETS:
            for delta in SENS["shrinkage_grid"]:
                assert sn.is_psd(sn.shrink(cov, delta, tgt), SENS["psd_tol"])
        for s in SENS["vol_scale_grid"]:
            assert sn.is_psd(sn.scale_vol(cov, s), SENS["psd_tol"])
        for c in SENS["corr_stress_grid"] + [1.0]:
            assert sn.is_psd(sn.stress_corr(cov, c), SENS["psd_tol"])
        for sc in SENS["scenarios"]:
            assert sn.is_psd(sn.scenario_cov(cov, sc["vol_scale"], sc["corr_c"]), SENS["psd_tol"])
    # stress keeps unit correlations on the diagonal and variances unchanged
    np.testing.assert_allclose(np.diag(sn.stress_corr(COV, 0.5)), np.diag(COV))


def test_shrinkage_zero_is_sample_covariance_and_one_is_the_target():
    for tgt, fn in sn.TARGETS.items():
        np.testing.assert_allclose(sn.shrink(COV, 0.0, tgt), COV)
        np.testing.assert_allclose(sn.shrink(COV, 1.0, tgt), fn(COV))
    np.testing.assert_allclose(sn.identity_target(COV), np.trace(COV) / len(COV) * np.eye(len(COV)))
    cc = sn.constant_correlation_target(COV)
    d = np.sqrt(np.diag(cc))
    corr = cc / np.outer(d, d)
    off = corr[~np.eye(len(corr), dtype=bool)]
    assert np.ptp(off) < 1e-12 and np.allclose(np.diag(cc), np.diag(COV))        # one common correlation, variances kept


def test_cap_sweep_variance_non_increasing_and_shadow_price_non_negative():
    s = dict(SENS, cap_grid=[1.0 / len(MU), 0.15, 0.2, 0.3, 0.5, 1.0], frontier_caps_plot=[0.2])
    cs, fr = sn.cap_sweep(COV, MU, RF, OPTS, s, list(RETS.columns))
    assert np.all(np.diff(cs["minvar_variance"]) <= SENS["monotone_tol"])
    assert (cs["minvar_cap_shadow_price"] >= -OPTS["dual_tol"]).all() and (cs["maxsharpe_target_cap_shadow_price"] >= -OPTS["dual_tol"]).all()
    # shadow price equals -dV/dcap wherever the active set is stable and the multipliers are unique
    ok = cs[(~cs["single_feasible_point"]) & cs["minvar_fd_active_set_unchanged"] & cs["minvar_multipliers_unique"]]
    assert len(ok) >= 3
    for r in ok.itertuples():
        assert abs(r.minvar_fd_dV_dcap + r.minvar_cap_shadow_price) <= OPTS["fd_rel_tol"] * abs(r.minvar_cap_shadow_price) + OPTS["fd_abs_floor"]
    assert 0.2 in fr


@pytest.mark.parametrize("seed", range(6))
def test_dual_identity_and_weak_duality_on_random_problems(seed):
    rng = np.random.default_rng(seed)
    sigma, mu, cap, target = random_problem(rng, OPTS)
    if abs(cap - 1.0 / len(mu)) < 1e-9:
        cap = 2.0 / len(mu)
        r_min, r_max, _, _ = so.feasible_return_range(mu, cap)
        target = float(0.5 * (r_min + r_max)) if target is not None else None
    res = so.solve_qp(sigma, mu, cap, OPTS, target=target)
    assert res.ok
    c = du.check_duality(sigma, mu, cap, res, dict(SENS["duality"], random_multipliers=300), rng)
    assert c["strong_gap"] <= SENS["duality"]["strong_gap_tol"]
    assert c["weak_duality_violations"] == 0 and c["weak_duality_max_excess"] <= SENS["duality"]["weak_duality_tol"]
    assert c["complementary_slackness"] <= SENS["duality"]["complementary_tol"]
    assert c["lagrangian_minimiser_distance"] <= 1e-6


def test_weak_duality_is_strict_for_non_optimal_multipliers_and_dual_function_matches_lagrangian_minimum():
    ex = du.worked_example(BASE)
    sigma, mu, cap, r = ex["sigma"], ex["mu"], ex["cap"], ex["target"]
    assert ex["res"].ok and ex["beta"][2] > 0                       # the cap binds in the example
    rng = np.random.default_rng(1)
    for _ in range(50):
        lam, gam, alpha, beta = du.random_multipliers(rng, 3, 1.0)
        g = du.dual_function(sigma, mu, cap, r, lam, gam, alpha, beta)
        q = lam + gam * mu + alpha - beta
        w_l = 0.5 * np.linalg.solve(sigma, q)                       # the Lagrangian's minimiser
        assert g == pytest.approx(du.lagrangian(w_l, sigma, mu, cap, r, lam, gam, alpha, beta), abs=1e-12)
        assert g < ex["primal"]
    assert ex["dual"] == pytest.approx(ex["primal"], abs=1e-12)


def test_regret_matrix_zero_in_every_scenario_and_nonnegative():
    pf = sn.base_portfolios(COV, MU, CAP, RF, OPTS, SENS["frontier_fraction"])
    long, sharpe, regret = sn.payoff_tables(pf, MU, COV, sn.standard_errors(COV, YEARS), RF, SENS["scenarios"])
    assert regret.shape == (4, len(SENS["scenarios"])) and set(long["portfolio"]) == set(pf)
    assert (regret >= -1e-12).all().all()
    assert (regret.min(axis=0).abs() <= 1e-12).all()                 # someone is best in every scenario
    for name, w in pf.items():
        assert w.sum() == pytest.approx(1.0, abs=1e-9) and w.min() >= -1e-12 and w.max() <= CAP + 1e-9, name
    # a dominated portfolio has positive regret everywhere in this simple case
    s2 = pd.DataFrame({"s1": [1.0, 0.5], "s2": [0.8, 0.8]}, index=["a", "b"])
    r2 = sn.regret_matrix(s2)
    assert r2.loc["a", "s1"] == 0 and r2.loc["b", "s1"] == 0.5 and r2.loc["a", "s2"] == 0 == r2.loc["b", "s2"]


def test_pipeline_runs_end_to_end_without_failed_checks_on_a_small_market(tmp_path):
    cfg = small_cfg(tmp_path)
    cfg["sensitivity"]["duality"]["random_multipliers"] = 50
    res = script.run_all(cfg, RETS)
    assert res["failures"] == [], res["failures"]
    for name in ["mu_sensitivity", "mu_one_asset", "sigma_sensitivity", "period_sensitivity", "period_weights", "cap_sweep", "shadow_prices", "rf_sweep",
                 "scenario_payoffs_long", "scenario_sharpe_matrix", "scenario_regret_matrix", "duality_checks"]:
        assert name in res["tables"] and len(res["tables"][name]) > 0, name


def test_main_exits_nonzero_when_a_check_fails_and_zero_otherwise(monkeypatch, tmp_path, capsys):
    cfg = small_cfg(tmp_path)
    cfg["data"]["processed_dir"] = str(tmp_path)
    RETS.to_parquet(tmp_path / "returns_simple.parquet")
    forced = {"tables": {}, "lines": [], "failures": ["forced failure"], "figdata": {}, "timing": {}, "tickers": [], "cap": CAP}
    monkeypatch.setattr(script, "run_all", lambda c, r: forced)
    args = type("A", (), {"log": False, "time_draws": False, "check_flagged_periods": False})()
    assert script.main(args, cfg) == 1
    assert "forced failure" in capsys.readouterr().err
    forced["failures"] = []
    assert script.main(args, cfg) == 0


def test_notes_worked_example_block_matches_the_code():
    notes = Path(SENS["notes_path"]).read_text(encoding="utf-8")
    block = du.worked_example_markdown(du.worked_example(BASE))
    assert "<!-- worked-example:start -->\n" + block + "\n<!-- worked-example:end -->" in notes
    assert "(generated)" not in notes                                   # both generated blocks have been filled in


# ---- bootstrap over the mu draws and integer day counts ---------------------------------------------------------

def synthetic_draw_frame(n=400, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for k in (0.5, 1.0):
        for d in range(n):
            rows.append({"k": k, "draw": d, "l1_change": abs(rng.normal(k, 0.3)), "share_base_positions_dropped": rng.uniform(0, 0.5),
                         "sharpe_base_of_perturbed": rng.normal(1.0 - 0.1 * k, 0.1), "loss_base_truth": abs(rng.normal(0.1 * k, 0.05)),
                         "loss_draw_truth": abs(rng.normal(0.12 * k, 0.05))})
    return pd.DataFrame(rows)


def test_bootstrap_is_seed_reproducible_and_has_the_expected_columns():
    df = synthetic_draw_frame()
    a = sn.bootstrap_summary(df, 200, 5)
    b = sn.bootstrap_summary(df, 200, 5)
    c = sn.bootstrap_summary(df, 200, 6)
    pd.testing.assert_frame_equal(a, b)
    assert not np.allclose(a["l1_median_boot_se"], c["l1_median_boot_se"])
    for name in sn.BOOT_STATS:
        for suffix in ("", "_boot_se", "_boot_ci_lo", "_boot_ci_hi"):
            assert name + suffix in a.columns
    assert list(a["k"]) == [0.5, 1.0]


def test_bootstrap_standard_error_and_interval_are_sensible():
    df = synthetic_draw_frame(n=400)
    b = sn.bootstrap_summary(df, 1000, 1)
    for name in sn.BOOT_STATS:
        assert (b[f"{name}_boot_se"] > 0).all()
        assert ((b[f"{name}_boot_ci_lo"] <= b[name]) & (b[name] <= b[f"{name}_boot_ci_hi"])).all()
    # the bootstrap SE of a mean should be close to sd / sqrt(n)
    d = df[df["k"] == 1.0]
    expected = d["sharpe_base_of_perturbed"].std(ddof=1) / np.sqrt(len(d))
    got = float(b.loc[b["k"] == 1.0, "sharpe_base_mean_boot_se"].iloc[0])
    assert got == pytest.approx(expected, rel=0.2)


def test_period_day_counts_are_integers():
    cfg = small_cfg()
    pdf, _, _ = sn.period_analysis(RETS, CAP, RF, OPTS, cfg["sensitivity"], TD)
    assert str(pdf["days"].dtype) == "Int64"
    per = pdf[pdf["kind"] == "period"]
    assert per["days"].notna().all() and (per["days"] > 100).all()
    assert pdf[pdf["kind"] == "consecutive_years"]["days"].isna().all()
