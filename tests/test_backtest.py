"""Tests for src/backtest.py and scripts/run_backtest.py on small synthetic data."""
import copy

import numpy as np
import pandas as pd
import pytest

from scripts import run_backtest as rb
from src import backtest as bt
from src import solver_own as so
from src.eps_selection import eps_for_date_info, extension_grid
from src.regimes import UNLABELLED
from src.robust import portfolio_cvar
from src.utils import load_config

N_DAYS, N_ASSETS = 260, 8


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(5)
    a = rng.standard_normal((N_ASSETS, N_ASSETS)) * 0.004
    cov = a @ a.T + np.eye(N_ASSETS) * 1e-4
    mean = np.linspace(0.08, 0.30, N_ASSETS) / 252
    idx = pd.bdate_range("2015-01-05", periods=N_DAYS)
    rets = pd.DataFrame(rng.multivariate_normal(mean, cov, size=N_DAYS), index=idx, columns=[f"S{i}" for i in range(N_ASSETS)])
    bench = pd.Series(rng.normal(0.0004, 0.01, N_DAYS), index=idx)
    stress = np.zeros(N_DAYS, bool)
    stress[130:160] = True
    stress[200:215] = True
    lab_h = pd.Series(np.where(stress, "stress", "calm"), index=idx, dtype=object)
    lab_h.iloc[:55] = UNLABELLED
    lab_v = lab_h.copy()
    lab_v.iloc[:60] = UNLABELLED
    s_t = pd.Series(np.clip(stress * 0.7 + 0.15 + rng.normal(0, 0.05, N_DAYS), 0, 1), index=idx)
    s_t.iloc[:55] = np.nan
    return rets, bench, (lab_h, lab_v, s_t)


@pytest.fixture()
def cfg(tmp_path, data):
    c = copy.deepcopy(load_config())
    c["portfolio"]["weight_cap"] = 0.5
    c["eps_selection"].update(block_length=5, refit_every=10, min_blocks=3)
    c["backtest"].update(rolling_window_days=40, window_days=60, sens_window_days=40, rebalance_every_days=10, start_position_min=60, n_jobs=1,
                         pilot_dates=1, michaud_draws=5, checkpoint_dir=str(tmp_path / "ckpt"), progress_log=str(tmp_path / "ckpt" / "p.log"),
                         notes_path=str(tmp_path / "notes.md"),
                         stress_windows={"w_a": ["2015-06-15", "2015-08-14"], "w_b": ["2015-09-01", "2015-12-31"]})
    c["backtest"]["bootstrap"].update(n_resamples=60, block_length=5)
    c["backtest"]["checks"].update(lookahead_dates=2, lookahead_labels_recompute_dates=0, repro_dates=2, one_over_n_turnover_max=0.2)
    c["eps_selection_ext"]["plateau_tolerance"] = 0.2
    c["paths"] = {**c["paths"], "tables_dir": str(tmp_path / "tables"), "figures_dir": str(tmp_path / "figs"), "results_log": str(tmp_path / "log.md")}
    return c


def test_cvar_exact_matches_independent_formula():
    rng = np.random.default_rng(1)
    for n in (250, 300, 123):
        r = rng.normal(0.0005, 0.01, n)
        assert bt.cvar_exact(r, 0.95) == pytest.approx(portfolio_cvar(np.array([1.0]), r.reshape(-1, 1), 0.95), abs=1e-12)


def test_max_drawdown_and_sharpe_hand_calculation(cfg):
    r = np.array([0.10, -0.20, 0.05, 0.10])
    w = np.cumprod(1 + r)
    assert bt.max_drawdown(r) == pytest.approx(0.88 / 1.10 - 1.0)
    m = bt.perf_metrics(r, cfg)
    expected = (r.mean() - cfg["risk_free_rate"] / 252) / r.std(ddof=1) * np.sqrt(252)
    assert m["sharpe"] == pytest.approx(expected) and m["cagr"] == pytest.approx(w[-1] ** (252 / 4) - 1)


def test_bootstrap_statistics_match_point_estimates_and_are_reproducible(cfg):
    rng = np.random.default_rng(2)
    R = rng.normal(0.0004, 0.01, (150, 3))
    sh, cv, dd = bt._stat_arrays(R[None], cfg)
    for j in range(3):
        m = bt.perf_metrics(R[:, j], cfg)
        assert sh[0, j] == pytest.approx(m["sharpe"]) and cv[0, j] == pytest.approx(m["cvar95_daily"]) and dd[0, j] == pytest.approx(m["max_drawdown"])
    a, b = bt.block_bootstrap(R, cfg), bt.block_bootstrap(R, cfg)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    cfg2 = copy.deepcopy(cfg)
    cfg2["backtest"]["bootstrap"]["seed"] += 1
    assert not np.array_equal(a[0], bt.block_bootstrap(R, cfg2)[0])


def test_simulate_costs_turnover_and_carry_forward(cfg):
    rng = np.random.default_rng(3)
    r = rng.normal(0.0005, 0.01, (40, 3))
    w1, w2 = np.array([0.5, 0.3, 0.2]), np.array([0.2, 0.3, 0.5])
    net, turn, cost, tw, car = bt.simulate({10: w1, 20: w2, 30: None}, [10, 20, 30], r, 0.001)
    assert turn[0] == pytest.approx(1.0) and cost[0] == pytest.approx(0.001)
    g = np.prod(1 + r[10:20], axis=0)
    drift = w1 * g / (w1 @ g)
    assert turn[1] == pytest.approx(np.abs(w2 - drift).sum())
    assert car.tolist() == [False, False, True] and np.allclose(tw[2], w2)
    gross, *_ = bt.simulate({10: w1, 20: w2, 30: None}, [10, 20, 30], r, 0.0)
    assert (gross - net)[0] == pytest.approx(cost[0]) and (gross - net)[1] == pytest.approx(0.0)


def test_theta_hat_uses_the_floor_when_sharpe_is_low(cfg, data):
    rets = data[0]
    bad = rets.iloc[:60] - 0.01                         # strongly negative mean: in-sample Sharpe below the floor
    theta, _ = bt.theta_hat(bad, cfg)
    assert theta == pytest.approx(np.sqrt(252) / cfg["backtest"]["theta_floor_sharpe"])
    good, s_hat = bt.theta_hat(rets.iloc[:60], cfg)
    assert good == pytest.approx(np.sqrt(252) / max(s_hat, cfg["backtest"]["theta_floor_sharpe"]))


def test_msd_at_eps_zero_equals_its_qp_form(cfg, data):
    win = data[0].iloc[:60]
    theta, _ = bt.theta_hat(win, cfg)
    w0 = bt.msd_weights(win, [0.0], theta, cfg)[0]
    mu, cov = bt.window_stats(win, cfg)
    qp = so.solve_qp(cov, mu, cfg["portfolio"]["weight_cap"], cfg["solver_own"], target=float(mu @ w0))
    assert np.abs(w0 - qp.weights).max() < cfg["backtest"]["checks"]["eps0_msd_weight_tol"]


def test_lookup_eps_matches_eps_for_date_info(cfg, data):
    rets, bench, (lab_h, _, _) = data
    grid = extension_grid(cfg["eps_selection_ext"])
    net = rb.compute_blocks(rets, cfg, grid)
    sched = bt.build_schedule(rets, bench, lab_h, cfg, net, grid, "best")
    fn = lambda b, c: lab_h.reindex(b.index)
    for p in (95, 110, 140, 170, 205):
        info = eps_for_date_info(rets.index[p], "hmm2_stress", cfg, returns=rets.iloc[:p + 1], benchmark=bench.iloc[:p + 1], grid=grid, rule="best", label_fn=fn)
        mine = bt.lookup_eps(sched, p, lab_h.iloc[p - 1], grid)
        assert mine["eps"] == info["eps"], p


def test_eval_start_waits_for_a_labelled_day(cfg, data):
    lab_h = data[2][0]
    assert bt.eval_start(cfg, lab_h) == 60 and lab_h.iloc[59] != UNLABELLED
    cfg2 = copy.deepcopy(cfg)
    cfg2["backtest"]["start_position_min"] = 40
    assert bt.eval_start(cfg2, lab_h) == 56


def test_run_all_passes_all_checks_and_resumes(cfg, data):
    rets, bench, labels = data
    res = rb.run_all(cfg, rets, bench, labels=labels, n_jobs=1, pilot=True, label_recompute_ok=False)
    assert res["failures"] == [], [l for l in res["lines"] if "FAIL" in l]
    assert res["ret"].index[0] == rets.index[res["first"]] and res["ret"].shape[0] == N_DAYS - res["first"]
    assert {"backtest_summary", "backtest_bootstrap", "stress_windows", "h1_secondary", "price_of_robustness", "regime_exploratory", "sensitivities",
            "daily_returns", "chosen_epsilon_over_time", "weights_drmsd_cv"} <= set(res["tables"])
    # a second invocation finds every checkpoint and recomputes nothing
    grid = extension_grid(cfg["eps_selection_ext"])
    again = rb.run_all(cfg, rets, bench, labels=labels, n_jobs=1, pilot=False, label_recompute_ok=False)
    assert again["failures"] == []
    assert np.allclose(again["ret"].to_numpy(), res["ret"].to_numpy(), atol=1e-12)
    assert grid[-1] == pytest.approx(cfg["backtest"]["checks"]["plateau_eps"])


def test_failed_solve_is_carried_forward_and_counted(cfg, data, monkeypatch):
    rets, bench, labels = data
    import src.robust as rob
    real = rob.worst_case_box
    calls = {"n": 0}

    def flaky(win, config, z=None):
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            raise ValueError("forced infeasible")
        return real(win, config, z)

    monkeypatch.setattr(rob, "worst_case_box", flaky)
    monkeypatch.setattr(bt.robust, "worst_case_box", flaky)
    res = rb.run_all(cfg, rets, bench, labels=labels, n_jobs=1, pilot=False, label_recompute_ok=False)
    assert res["carried"]["wc_box"] > 0 and res["failures"] == []


def test_main_writes_outputs_and_exits_nonzero_on_failed_check(cfg, data, monkeypatch, capsys, tmp_path):
    rets, bench, labels = data
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    args = type("A", (), {"log": True})()
    assert rb.main(args, cfg=cfg, n_jobs=1, rets=rets, bench=bench, labels=labels, pilot=False, label_recompute_ok=False) == 0
    for name in ("backtest_summary", "backtest_bootstrap", "stress_windows", "h1_secondary", "price_of_robustness", "regime_exploratory", "sensitivities",
                 "daily_returns", "chosen_epsilon_over_time"):
        assert (tmp_path / "tables" / f"{name}_bt.csv").exists(), name
    assert (tmp_path / "figs" / "wealth_bt.png").exists()
    notes = (tmp_path / "notes.md").read_text(encoding="utf-8")
    low = notes.lower()
    banned = ("cl" + "aude", "assis" + "tant", "cv" + "xpy")
    assert "Headline" in notes and not any(w in low for w in banned)
    real = rb.sanity_checks
    monkeypatch.setattr(rb, "sanity_checks", lambda *a, **k: real(*a, **k) + [("forced failure", False, "test")])
    assert rb.main(type("A", (), {"log": False})(), cfg=cfg, n_jobs=1, rets=rets, bench=bench, labels=labels, pilot=False, label_recompute_ok=False) == 1
    assert "forced failure" in capsys.readouterr().err


def test_stored_group_weights_are_the_right_objective(cfg, data):
    """Regression: the DR-CVaR group must hold DR-CVaR weights and the DR-MSD group DR-MSD weights (the checkpoint group name is not the solver kind)."""
    from src.wdro import wdro_weights
    rets = data[0]
    grid = extension_grid(cfg["eps_selection_ext"])
    W, p = cfg["backtest"]["window_days"], 80
    for group in ("drcvar", "drmsd"):
        bt.worker(rets, cfg, W, p, group, grid, {"soft": float(grid[4])})
    win = rets.iloc[p - W:p]
    cv = bt.read_ckpt(bt.ckpt_file(cfg, W, p, "drcvar"))
    ms = bt.read_ckpt(bt.ckpt_file(cfg, W, p, "drmsd"))
    assert np.abs(cv["fix_05"] - bt.clean(wdro_weights(win, float(grid[5]), "cvar", cfg), cfg["portfolio"]["weight_cap"])).max() < 1e-9
    assert np.abs(cv["soft"] - bt.clean(wdro_weights(win, float(grid[4]), "cvar", cfg), cfg["portfolio"]["weight_cap"])).max() < 1e-9
    theta, _ = bt.theta_hat(win, cfg)
    assert np.abs(ms["fix_05"] - bt.msd_weights(win, [grid[5]], theta, cfg)[0]).max() < 1e-9 and "theta" in ms
    assert np.abs(cv["fix_05"] - ms["fix_05"]).max() > 1e-4          # the two objectives give different portfolios
