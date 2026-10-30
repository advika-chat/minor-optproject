import copy
import logging

import numpy as np
import pandas as pd
import pytest

from src import eps_selection as es
from src.regimes import regime_series
from src.utils import load_config
from src.wdro import eps_grid

BASE = load_config()


def small_cfg():
    c = copy.deepcopy(BASE)
    c["backtest"]["rolling_window_days"] = 60
    c["portfolio"]["weight_cap"] = 0.4
    c["eps_selection"].update({"block_length": 10, "refit_every": 20, "min_blocks": 3})
    c["wdro"].update({"eps_min": 1e-4, "eps_max": 1e-1, "eps_points": 3})
    c["regimes"].update({"vol_window": 5, "vol_min_history": 30})
    return c


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(BASE["seed"])
    n, k = 300, 6
    vol = np.where((np.arange(n) // 60) % 2 == 0, 0.006, 0.02)[:, None]     # alternating calm / stressed
    r = pd.DataFrame(rng.normal(0.0004, 1.0, size=(n, k)) * vol * rng.uniform(0.8, 1.2, k),
                     index=pd.bdate_range("2020-01-01", periods=n))
    return r, r.mean(axis=1)


def test_selected_eps_always_in_grid(data):
    r, b = data
    c = small_cfg()
    labels = regime_series(b, "vol_threshold", c)["regime"]
    sched = es.eps_schedule(r, b, labels, c)
    assert len(sched) > 0
    assert np.isin(sched["eps"], eps_grid(c)).all()


def test_walk_forward_uses_only_finished_blocks(data):
    r, b = data
    c = small_cfg()
    labels = regime_series(b, "vol_threshold", c)["regime"]
    sched = es.eps_schedule(r, b, labels, c)
    w, bl = c["backtest"]["rolling_window_days"], c["eps_selection"]["block_length"]
    for _, row in sched.iterrows():
        q, k = row["refit_position"], row["n_blocks_pooled"]
        assert w + k * bl <= q < w + (k + 1) * bl + c["eps_selection"]["refit_every"]
        assert row["refit_date"] == r.index[q]
    assert sched["refit_position"].is_monotonic_increasing


def test_leakage_changing_later_returns_does_not_change_eps(data):
    r, b = data
    c = small_cfg()
    date = r.index[220]
    p = int(r.index.searchsorted(date))
    base = es.eps_for_date_info(date, "vol_threshold", c, returns=r, benchmark=b)
    r2, b2 = r.copy(), b.copy()
    rng = np.random.default_rng(1)
    r2.iloc[p:] = rng.normal(0.0, 0.2, size=r2.iloc[p:].shape)      # rewrite date t and everything after
    b2 = r2.mean(axis=1)
    again = es.eps_for_date_info(date, "vol_threshold", c, returns=r2, benchmark=b2)
    assert base["source"] != "default" and np.isfinite(base["sharpe_net"])   # a real selection, not the default
    assert again == base            # includes the continuous criterion values, which any leak would change


def test_eps_for_date_matches_full_schedule(data):
    r, b = data
    c = small_cfg()
    labels = regime_series(b, "vol_threshold", c)["regime"]
    sched = es.eps_schedule(r, b, labels, c)
    date = r.index[250]
    info = es.eps_for_date_info(date, "vol_threshold", c, returns=r, benchmark=b)
    p = 250
    row = sched[(sched["refit_position"] == info["refit_position"]) &
                (sched["regime"] == (labels.iloc[p - 1] if labels.iloc[p - 1] != "unlabelled" else es.POOLED))]
    assert len(row) == 1
    assert info["eps"] == row["eps"].iloc[0]
    # the continuous criterion values must also match: they reveal a stale or different refit
    assert info["sharpe_net"] == pytest.approx(row["sharpe_net"].iloc[0], nan_ok=True)
    assert info["cvar_daily"] == pytest.approx(row["cvar_daily"].iloc[0], nan_ok=True)
    # and it must use exactly the blocks finished before its refit
    qrow = sched[sched["refit_position"] == info["refit_position"]]
    assert (qrow["n_blocks_pooled"] == (info["refit_position"] - c["backtest"]["rolling_window_days"])
            // c["eps_selection"]["block_length"]).all()


def test_before_first_refit_returns_smallest_grid_eps(data):
    r, b = data
    c = small_cfg()
    assert es.eps_for_date(r.index[40], "vol_threshold", c, returns=r, benchmark=b) == eps_grid(c)[0]


def test_requires_data_arguments():
    with pytest.raises(ValueError):
        es.eps_for_date("2020-06-01", "vol_threshold", small_cfg())


def synthetic_net(means_a, means_b, n_a=6, n_b=6, length=50, seed=0):
    rng = np.random.default_rng(seed)
    n_eps = len(means_a)
    net = np.empty((n_eps, n_a + n_b, length))
    for e in range(n_eps):
        for k in range(n_a + n_b):
            net[e, k] = rng.normal(means_a[e] if k < n_a else means_b[e], 0.01, length)
    return net, ["A"] * n_a + ["B"] * n_b


def test_best_eps_chosen_separately_per_regime():
    c = small_cfg()
    net, regs = synthetic_net([0.0, 0.0, 0.003], [0.003, 0.0, 0.0])
    sel = es.select_eps_by_regime(net, regs, c)
    assert sel["A"]["eps_index"] == 2 and sel["B"]["eps_index"] == 0
    assert sel["A"]["source"] == sel["B"]["source"] == "regime"


def test_fallback_to_pooled_with_warning_when_regime_has_too_few_blocks(caplog):
    c = small_cfg()
    net, regs = synthetic_net([0.0, 0.0, 0.003], [0.0, 0.0, 0.003], n_a=8, n_b=2)
    with caplog.at_level(logging.WARNING):
        sel = es.select_eps_by_regime(net, regs, c)
    assert sel["B"]["source"] == "pooled_fallback"
    assert sel["B"]["eps_index"] == sel[es.POOLED]["eps_index"]
    assert sel["B"]["n_blocks"] == 2
    assert any("pooled eps" in rec.message for rec in caplog.records)


def test_default_when_even_pooled_blocks_are_too_few():
    c = small_cfg()
    net, regs = synthetic_net([0.0, 0.0, 0.003], [0.0, 0.0, 0.003], n_a=1, n_b=1)
    sel = es.select_eps_by_regime(net, regs, c)
    assert sel[es.POOLED]["source"] == "default" and sel[es.POOLED]["eps_index"] == 0


def test_cvar_breaks_sharpe_ties():
    c = small_cfg()
    c["eps_selection"]["tie_tolerance"] = 1e9                   # everything ties on Sharpe
    rng = np.random.default_rng(2)
    net = np.empty((2, 6, 60))
    net[0] = rng.normal(0.001, 0.02, (6, 60))                   # fat
    net[1] = rng.normal(0.001, 0.005, (6, 60))                  # thin: lower CVaR
    sel = es.select_eps_by_regime(net, ["A"] * 6, c)
    assert sel["A"]["eps_index"] == 1


# ---- extended grid, one-standard-error rule, jackknife SE, plateau handling ----

def test_extension_grid_is_superset_of_original_grid():
    from src.wdro import eps_grid
    old = eps_grid(BASE)
    new = es.extension_grid(BASE["eps_selection_ext"])
    assert len(new) == len(old) + 1
    np.testing.assert_allclose(new[:len(old)], old, rtol=1e-12)
    np.testing.assert_allclose(new[-1] / new[-2], old[-1] / old[-2], rtol=1e-9)    # same log step


def test_jackknife_se_matches_brute_force_leave_one_out():
    rng = np.random.default_rng(4)
    x = rng.normal(0.001, 0.01, size=(9, 21))
    rf_d, td = BASE["risk_free_rate"] / BASE["trading_days"], BASE["trading_days"]
    sharpe = lambda a: (a.mean() - rf_d) / a.std(ddof=1) * np.sqrt(td)      # noqa: E731
    loo = np.array([sharpe(np.delete(x, b, axis=0).ravel()) for b in range(len(x))])
    n = len(x)
    expected = np.sqrt((n - 1) / n * np.sum((loo - loo.mean()) ** 2))
    assert es._jackknife_se(x, rf_d, td) == pytest.approx(expected, rel=1e-10)
    assert np.isnan(es._jackknife_se(x[:1], rf_d, td))


def test_one_se_picks_smallest_eps_within_one_se_of_best():
    c = small_cfg()
    rng = np.random.default_rng(8)
    n_eps, n_blocks, length = 6, 14, 21
    drift = np.array([0.0000, 0.0003, 0.0006, 0.0009, 0.0011, 0.0012])          # Sharpe rises with eps
    net = np.stack([rng.normal(d, 0.01, size=(n_blocks, length)) for d in drift])
    regs = ["A"] * n_blocks
    best = es.select_eps_by_regime(net, regs, c, rule="best")["A"]
    one = es.select_eps_by_regime(net, regs, c, rule="one_se")["A"]
    sh, cv, se = es._criteria(net, np.arange(n_blocks), c)
    k_best = es._argbest(sh, cv, c["eps_selection"]["tie_tolerance"])
    expected = int(np.where(sh >= sh[k_best] - se[k_best])[0][0])
    assert best["eps_index"] == k_best and one["eps_index"] == expected
    assert one["eps_index"] <= best["eps_index"] and one["best_index"] == k_best
    assert one["se_best"] == pytest.approx(se[k_best])


def test_one_se_equals_best_when_se_unavailable():
    c = small_cfg()
    c["eps_selection"]["min_blocks"] = 1
    net, _ = synthetic_net([0.0, 0.0, 0.003], [0.0, 0.0, 0.003], n_a=1, n_b=0)
    sel = es.select_eps_by_regime(net, ["A"], c, rule="one_se")["A"]
    assert sel["eps_index"] == sel["best_index"]


def test_unknown_rule_raises():
    net, regs = synthetic_net([0.0, 0.0, 0.003], [0.0, 0.0, 0.003])
    with pytest.raises(ValueError, match="rule"):
        es.select_eps_by_regime(net, regs, small_cfg(), rule="median")


def test_identical_plateau_columns_resolve_to_smallest_eps_in_best_rule():
    c = small_cfg()
    rng = np.random.default_rng(3)
    base = rng.normal(0.001, 0.01, size=(8, 21))
    net = np.stack([rng.normal(0.0, 0.01, size=(8, 21)), base, base * (1 + 1e-13), base * (1 - 1e-13)])  # 3 identical
    sel = es.select_eps_by_regime(net, ["A"] * 8, c, rule="best")["A"]
    assert sel["eps_index"] == 1                                    # smallest eps of the plateau, not a noise-picked later one


def test_scan_plateau_stops_at_first_candidate_with_one_over_n_everywhere(data):
    r, _ = data
    c = small_cfg()
    c["wdro"].update({"eps_min": 1e-4, "eps_max": 1e-2, "eps_points": 3})
    scan = es.scan_plateau(r, c, tol=0.01, n_max=12)
    assert scan["eps"].iloc[0] == pytest.approx(1e-2)               # starts at the old grid maximum
    assert (scan["min_ratio"].iloc[:-1] < 0.99).all() and scan["min_ratio"].iloc[-1] >= 0.99
    assert scan["windows_below"].iloc[-1] == 0


def test_schedule_has_rule_columns_and_rejects_grid_mismatch(data):
    r, b = data
    c = small_cfg()
    labels = regime_series(b, "vol_threshold", c)["regime"]
    grid = es.eps_grid(c)
    net = es.compute_blocks(r, c, grid)
    sched = es.eps_schedule(r, b, labels, c, net=net, grid=grid, rule="one_se")
    assert set(["rule", "best_eps", "se_best"]).issubset(sched.columns) and (sched["rule"] == "one_se").all()
    assert (sched["eps"] <= sched["best_eps"] + 1e-15).all()          # one-SE never picks a larger eps than the best
    with pytest.raises(ValueError):
        es.eps_schedule(r, b, labels, c, net=net, grid=grid[:2])


@pytest.mark.parametrize("rule", ["best", "one_se"])
def test_eps_for_date_matches_schedule_for_both_rules_on_extended_grid(data, rule):
    r, b = data
    c = small_cfg()
    grid = np.append(es.eps_grid(c), es.eps_grid(c)[-1] * 2.0)         # an extra, larger eps
    labels = regime_series(b, "vol_threshold", c)["regime"]
    sched = es.eps_schedule(r, b, labels, c, grid=grid, rule=rule)
    date = r.index[250]
    info = es.eps_for_date_info(date, "vol_threshold", c, returns=r, benchmark=b, grid=grid, rule=rule)
    now = labels.iloc[249]
    row = sched[(sched["refit_position"] == info["refit_position"]) &
                (sched["regime"] == (now if now != "unlabelled" else es.POOLED))].iloc[0]
    assert info["eps"] == row["eps"] and np.isin(info["eps"], grid)
    assert info["sharpe_net"] == pytest.approx(row["sharpe_net"], nan_ok=True)


def test_script_exits_nonzero_when_grid_is_not_a_superset(monkeypatch, capsys):
    from scripts import run_eps_selection as script
    monkeypatch.setattr(script, "extension_grid", lambda sec: np.logspace(-3, -2, 5))
    args = type("A", (), {"find_grid": False, "log": False})()
    assert script.main(args) == 1
    assert "not a superset" in capsys.readouterr().err
