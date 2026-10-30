"""Tests for src/montecarlo.py and scripts/run_montecarlo.py on small synthetic cases."""
import copy

import numpy as np
import pandas as pd
import pytest

from scripts import run_montecarlo as rm
from src import montecarlo as mc
from src import solver_own as so
from src import wdro
from src.utils import load_config


@pytest.fixture(scope="module")
def rets():
    rng = np.random.default_rng(3)
    n = 6
    a = rng.standard_normal((n, n)) * 0.004
    cov_d = a @ a.T + np.eye(n) * 1e-4
    mean_d = np.linspace(0.10, 0.28, n) / 252
    return pd.DataFrame(rng.multivariate_normal(mean_d, cov_d, size=3000), columns=[f"S{i}" for i in range(n)])


@pytest.fixture()
def cfg(tmp_path):
    c = copy.deepcopy(load_config())
    c["portfolio"]["weight_cap"] = 0.5
    m = c["montecarlo"]
    m.update(n_runs=12, T_list=[30, 60, 90], T_primary=60, T_short=30, T_long=90, grid_stride=4, n_jobs=1,
             checkpoint_dir=str(tmp_path / "ckpt"), notes_path=str(tmp_path / "notes.md"))
    m["sensitivity"] = {"n_runs_sens": 6, "scenarios": [
        {"name": "gaussian", "mu_shrink_s": 0.5, "dist": "gaussian", "T_list": [60], "extra_eps": []},
        {"name": "ext_grid", "mu_shrink_s": 0.5, "dist": "t", "T_list": [30, 60, 90], "extra_eps": [1.0, 10.0, 100.0]}]}
    m["bootstrap"]["n_resamples"] = 400
    m["pilot"] = {"runs": 2, "T": 90}
    m["weights_match_tol"] = 1.0e-5       # small synthetic case: the oracle's golden-section search is accurate to about 1e-6 here
    m["checks"].update(moment_n_days=400000, n_eps0_runs=3, moment_z_tol=6.0)
    c["paths"] = {**c["paths"], "tables_dir": str(tmp_path / "tables"), "figures_dir": str(tmp_path / "figs"),
                  "results_log": str(tmp_path / "log.md")}
    return c


@pytest.fixture()
def ctx(cfg, rets):
    return mc.make_context(rets, cfg, 0.5)


def test_simulator_moments_match_truth(cfg, ctx):
    for dist in ("t", "gaussian"):
        zm, zc = mc.moment_check(ctx.truth, dist, cfg, np.random.SeedSequence(1))
        assert max(zm, zc) < cfg["montecarlo"]["checks"]["moment_z_tol"], (dist, zm, zc)


def test_exact_moment_sample_has_exact_moments(cfg, ctx):
    r = mc.exact_moment_sample(ctx.truth, 50, cfg, np.random.SeedSequence(2))
    assert np.allclose(r.mean(axis=0) * 252, ctx.truth.mu, atol=1e-12)
    assert np.allclose(np.cov(r, rowvar=False, bias=True) * 252, ctx.truth.cov, atol=1e-12)


def test_true_sharpe_matches_hand_calculation():
    truth = mc.Truth(mu=np.array([0.20, 0.10]), cov=np.array([[0.04, 0.0], [0.0, 0.01]]), names=["a", "b"])
    w = np.array([0.5, 0.5])
    expected = (0.15 - 0.065) / np.sqrt(0.25 * 0.04 + 0.25 * 0.01)
    assert mc.true_sharpe(w, truth, 0.065) == pytest.approx(expected, abs=1e-14)


def test_lambda_calibration_oracle_mean_variance_equals_oracle_max_sharpe(cfg, ctx):
    w = mc.oracle_mv_weights(ctx, cfg)
    assert np.abs(w - ctx.w_orc).max() < cfg["montecarlo"]["weights_match_tol"]
    assert ctx.theta == pytest.approx(np.sqrt(252) / ctx.s_orc)


def test_eps0_equals_plugin_mean_variance_and_theta0_equals_wdro(cfg, ctx):
    cap = cfg["portfolio"]["weight_cap"]
    drmv = mc._drmv(6, cap, wdro._dual_p(cfg), cfg["montecarlo"]["solver_tol"])
    r = mc.simulate(ctx.truth, 60, "t", cfg, np.random.SeedSequence(5))
    mu_hat, cov_hat = r.mean(axis=0) * 252, np.cov(r, rowvar=False) * 252
    w0 = drmv.solve(r, 0.0, ctx.theta)
    qp = so.solve_qp(cov_hat, mu_hat, cap, cfg["solver_own"], target=float(mu_hat @ w0))
    assert np.abs(w0 - qp.weights).max() < cfg["montecarlo"]["checks"]["eps0_weight_tol"]
    for e in (0.0, 0.01):
        a = drmv.solve(r, e, 0.0)
        b = wdro.wdro_weights(pd.DataFrame(r), e, "meanvar", cfg)
        assert np.abs(a - b).max() < cfg["montecarlo"]["checks"]["wdro_weight_tol"]


def test_known_answer_plugin_with_true_parameters_is_oracle(cfg, ctx):
    r = mc.exact_moment_sample(ctx.truth, 60, cfg, np.random.SeedSequence(7))
    res = mc.plugin_maxsharpe(r.mean(axis=0) * 252, np.cov(r, rowvar=False, bias=True) * 252, cfg)
    assert np.abs(res.weights - ctx.w_orc).max() < cfg["montecarlo"]["checks"]["known_answer_tol"]


def test_run_one_oracle_bound_methods_and_same_data(cfg, ctx):
    scen = mc.scenarios(cfg)[0]
    out = mc.run_one(ctx, cfg, scen, 60, 0)
    names = [r["method"] for r in out["rows"]]
    assert set(rm.MAIN_METHODS) <= set(names) and sum(n.startswith("dro_fixed") for n in names) == len(ctx.grid)
    assert out["meta"]["max_oracle_excess"] <= cfg["montecarlo"]["checks"]["oracle_tol"]
    again = mc.run_one(ctx, cfg, scen, 60, 0)
    assert again["meta"]["data_hash"] == out["meta"]["data_hash"]
    assert [r["true_sharpe"] for r in again["rows"]] == [r["true_sharpe"] for r in out["rows"]]
    other = mc.run_one(ctx, cfg, scen, 60, 1)
    assert other["meta"]["data_hash"] != out["meta"]["data_hash"]


def test_paired_bootstrap_is_reproducible_under_seed(cfg):
    d = np.random.default_rng(0).normal(0.01, 0.05, 80)
    a, _ = mc.paired_stats(d, cfg, 5)
    b, _ = mc.paired_stats(d, cfg, 5)
    c, _ = mc.paired_stats(d, cfg, 6)
    assert a == b and a["ci_lo"] != c["ci_lo"]
    assert a["ci_lo"] < a["mean"] < a["ci_hi"]


def test_difference_in_differences_known_effect(cfg):
    rng = np.random.default_rng(1)
    did, s1, s2 = mc.diff_in_diff(rng.normal(1.0, 0.1, 100), rng.normal(0.0, 0.1, 100), cfg)
    assert did["ci_lo"] > 0 and did["did"] == pytest.approx(s1["mean"] - s2["mean"])
    did0, *_ = mc.diff_in_diff(rng.normal(0.0, 1.0, 100), rng.normal(0.0, 1.0, 100), cfg)
    assert did0["ci_lo"] < 0 < did0["ci_hi"]


def test_checkpoint_resume_equals_uninterrupted(cfg, rets, tmp_path):
    ctxs = rm.build_contexts(cfg, rets)
    tasks = rm.plan_tasks(cfg, ctxs)
    rm.execute(cfg, ctxs, tasks[: len(tasks) // 2], 1)                      # interrupted job
    first = {p.name: p.stat().st_mtime_ns for p in __import__("pathlib").Path(cfg["montecarlo"]["checkpoint_dir"]).glob("*.json")}
    assert 0 < len(first) < len(tasks)
    n_new = rm.execute(cfg, ctxs, tasks, 1)                                  # resume
    assert n_new == len(tasks) - len(first)
    after = {p.name: p.stat().st_mtime_ns for p in __import__("pathlib").Path(cfg["montecarlo"]["checkpoint_dir"]).glob("*.json")}
    assert all(after[k] == v for k, v in first.items())                      # finished runs were not recomputed
    runs_resumed, _ = rm.collect(cfg, ctxs)
    cfg2 = copy.deepcopy(cfg)
    cfg2["montecarlo"]["checkpoint_dir"] = str(tmp_path / "ckpt2")
    rm.execute(cfg2, ctxs, tasks, 1)                                         # uninterrupted job
    runs_full, _ = rm.collect(cfg2, ctxs)
    num = ["true_sharpe", "true_return", "true_vol", "l1_oracle", "regret"]
    pd.testing.assert_frame_equal(runs_resumed[num], runs_full[num])


def test_run_all_passes_all_checks_on_small_case(cfg, rets):
    res = rm.run_all(cfg, rets, n_jobs=1, check_pilot=False)
    assert res["failures"] == [], res["failures"]
    assert set(res["tables"]) == {"mc_summary_by_T", "mc_h1", "mc_ablation", "mc_h2", "mc_eps_curve", "mc_eps_chosen", "mc_sensitivity"}
    assert res["verdicts"]["h1"] in ("SUPPORTED", "FALSIFIED")
    assert res["verdicts"]["h2"] in ("SUPPORTED", "NOT SUPPORTED")


def test_script_exits_nonzero_when_a_check_fails(cfg, rets, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    real = rm.sanity_checks
    monkeypatch.setattr(rm, "sanity_checks", lambda *a, **k: real(*a, **k) + [("forced failure", False, "test")])
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: rets)
    args = type("A", (), {"log": False})()
    assert rm.main(args, cfg=cfg, n_jobs=1) == 1
    assert "forced failure" in capsys.readouterr().err


def test_script_exits_zero_and_writes_outputs_when_checks_pass(cfg, rets, monkeypatch, tmp_path):
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: rets)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    args = type("A", (), {"log": True})()
    assert rm.main(args, cfg=cfg, n_jobs=1) == 0
    tdir = tmp_path / "tables"
    for name in ("mc_runs", "mc_h1", "mc_h2", "mc_ablation", "mc_summary_by_T", "mc_eps_curve", "mc_eps_chosen", "mc_sensitivity"):
        assert (tdir / f"{name}_mc.csv").exists(), name
    assert (tmp_path / "figs" / "mc_h2_delta_by_T_mc.png").exists()
    text = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert "H1 verdict" in text and "H2 verdict" in text
    notes = (tmp_path / "notes.md").read_text(encoding="utf-8")
    assert "H1 (T = 60)" in notes
    low = notes.lower()
    banned = ("cl" + "aude", "assis" + "tant", "cv" + "xpy")
    assert not any(w in low for w in banned)
