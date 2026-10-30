import copy
import logging

import numpy as np
import pandas as pd
import pytest

from src import eps_selection as es
from src import stress
from src.regimes import regime_series
from src.utils import load_config

BASE = load_config()


def small_cfg():
    c = copy.deepcopy(BASE)
    c["regimes_stress"].update({"hmm_min_history": 120, "hmm_refit_every": 30, "hmm_restarts": 2, "hmm_max_iter": 60,
                                "vol_percentile_window": 10, "vol_percentile_min_history": 60})
    c["regimes"].update({"hmm_min_history": 120, "hmm_refit_every": 30, "hmm_restarts": 2, "hmm_max_iter": 60,
                         "vol_window": 10, "vol_min_history": 60})
    c["backtest"]["rolling_window_days"] = 60
    c["portfolio"]["weight_cap"] = 0.4
    c["eps_selection"].update({"block_length": 10, "refit_every": 20, "min_blocks": 3})
    c["wdro"].update({"eps_min": 1e-4, "eps_max": 1e-1, "eps_points": 3})
    return c


def switching(segments, seed=BASE["seed"]):
    rng = np.random.default_rng(seed)
    r = np.concatenate([rng.normal(0.0003, sd, n) for sd, n in segments])
    return pd.Series(r, index=pd.bdate_range("2018-01-01", periods=len(r))), \
        np.concatenate([[sd] * n for sd, n in segments])


SEG = [(0.004, 200), (0.025, 100), (0.004, 150), (0.025, 100), (0.004, 100)]


@pytest.fixture(scope="module")
def series():
    return switching(SEG)


@pytest.fixture(scope="module")
def s_full(series):
    return stress.stress_level(series[0], small_cfg())


def test_config_hmm_settings_identical_to_regimes_section():
    rs, rg = BASE["regimes_stress"], BASE["regimes"]
    for k in ["hmm_min_history", "hmm_refit_every", "hmm_restarts", "hmm_max_iter", "hmm_tol"]:
        assert rs[k] == rg[k], k
    assert rs["seed"] == BASE["seed"] and rs["hmm_states"] == 2 and rs["stress_threshold"] == 0.5


def test_stress_level_in_unit_interval_and_nan_free_after_burn_in(s_full):
    burn = small_cfg()["regimes_stress"]["hmm_min_history"]
    assert s_full.iloc[:burn].isna().all()
    after = s_full.iloc[burn:]
    assert after.notna().all()
    assert ((after >= 0) & (after <= 1)).all()


def test_causality_truncated_run_equals_full_run_incl_refit_boundaries(series, s_full):
    c = small_cfg()
    rs = c["regimes_stress"]
    first, step = rs["hmm_min_history"] - 1, rs["hmm_refit_every"]
    # first labelled date, the refit rows themselves (where the model in use switches) and the rows just after them
    positions = [first + 1, first + step, first + step + 1, first + 2 * step, first + 2 * step + 1, 400]
    for p in positions:
        trunc = stress.stress_level(series[0].iloc[:p + 1], c)
        np.testing.assert_allclose(trunc.to_numpy(), s_full.iloc[:p + 1].to_numpy(), atol=1e-10, equal_nan=True,
                                   err_msg=f"truncated at row {p}")


def test_higher_variance_state_is_the_stress_state_in_either_segment_order():
    c = small_cfg()
    for segments in ([(0.004, 200), (0.025, 120), (0.004, 150), (0.025, 120), (0.004, 100)],
                     [(0.025, 220), (0.004, 150), (0.025, 120), (0.004, 150), (0.025, 80)]):
        r, sd = switching(segments)
        s = stress.stress_level(r, c)
        burn = c["regimes_stress"]["hmm_min_history"]
        high = (sd == 0.025) & (np.arange(len(sd)) >= burn + 30)      # skip a short adaptation period
        low = (sd == 0.004) & (np.arange(len(sd)) >= burn + 30)
        assert s[high].mean() > 0.7 and s[low].mean() < 0.3, (s[high].mean(), s[low].mean())
        assert (stress.hard_labels(s, 0.5)[high] == stress.STRESS).mean() > 0.7


def test_hard_label_is_strictly_above_threshold_and_unlabelled_for_nan():
    s = pd.Series([np.nan, 0.2, 0.5, 0.5000001, 0.9], index=pd.bdate_range("2020-01-01", periods=5))
    lab = stress.hard_labels(s, 0.5)
    assert list(lab) == ["unlabelled", "calm", "calm", "stress", "stress"]
    assert list(stress.hard_labels(s, 0.3)) == ["unlabelled", "calm", "stress", "stress", "stress"]


def test_hmm2_stress_labels_equal_threshold_on_stress_level(series, s_full):
    c = small_cfg()
    lab = stress.labels_hmm2_stress(series[0], c)
    expected = stress.hard_labels(s_full, c["regimes_stress"]["stress_threshold"])
    assert (lab == expected).all()
    assert ((lab == stress.STRESS) == (s_full > c["regimes_stress"]["stress_threshold"])).all()


def test_vol_two_merges_volatile_and_crisis_and_is_causal(series):
    c = small_cfg()
    r = series[0]
    two = stress.labels_vol_two(r, c)
    three = regime_series(r, "vol_threshold", c)["regime"]
    assert (two[three == "calm"] == "calm").all()
    assert (two[three.isin(["volatile", "crisis"])] == "stress").all()
    assert (two[three == "unlabelled"] == "unlabelled").all()
    for cut in (250, 400):
        short = stress.labels_vol_two(r.iloc[:cut], c)
        assert (short.to_numpy() == two.iloc[:cut].to_numpy()).all()
    shocked = r.copy()
    shocked.iloc[450:] *= 30
    assert (stress.labels_vol_two(shocked, c).iloc[:450].to_numpy() == two.iloc[:450].to_numpy()).all()


def test_vol_percentile_is_in_unit_interval_causal_and_past_only(series):
    c = small_cfg()
    r = series[0]
    pct = stress.vol_percentile(r, c)
    rs = c["regimes_stress"]
    assert pct.dropna().between(0, 1).all()
    vol = r.rolling(rs["vol_percentile_window"]).std() * np.sqrt(c["trading_days"])
    first = pct.first_valid_index()
    assert (vol.loc[:first].dropna().shape[0] - 1) >= rs["vol_percentile_min_history"]      # enough strictly earlier values
    for pos in (150, 300, 500):
        past = vol.iloc[:pos].dropna().to_numpy()
        assert pct.iloc[pos] == pytest.approx((past <= vol.iloc[pos]).mean())
        short = stress.vol_percentile(r.iloc[:pos + 1], c)
        assert short.iloc[-1] == pytest.approx(pct.iloc[pos])


# ---- eps selection with two regimes ----------------------------------------------------------------

@pytest.fixture(scope="module")
def market():
    rng = np.random.default_rng(BASE["seed"])
    n, k = 300, 6
    vol = np.where((np.arange(n) // 60) % 2 == 0, 0.006, 0.02)[:, None]
    r = pd.DataFrame(rng.normal(0.0004, 1.0, size=(n, k)) * vol * rng.uniform(0.8, 1.2, k),
                     index=pd.bdate_range("2020-01-01", periods=n))
    return r, r.mean(axis=1)


@pytest.mark.parametrize("method", ["hmm2_stress", "vol_two"])
@pytest.mark.parametrize("rule", ["best", "one_se"])
def test_eps_for_date_full_equals_truncated_incl_refit_boundary(market, method, rule):
    r, b = market
    c = small_cfg()
    fn = stress.LABEL_FUNCTIONS[method]
    grid = es.extension_grid({"eps_min": 1e-4, "eps_max": 2e-1, "eps_points": 4})
    labels = fn(b, c)
    sched = es.eps_schedule(r, b, labels, c, grid=grid, rule=rule)
    q = 150                                   # a refit row: (150 - (60 + 10)) is a multiple of refit_every
    assert q in set(sched["refit_position"])
    for p in (q - 1, q, q + 1, 250):
        info = es.eps_for_date_info(r.index[p], method, c, returns=r, benchmark=b, grid=grid, rule=rule, label_fn=fn)
        now = labels.iloc[p - 1]
        qq = max(x for x in set(sched["refit_position"]) if x <= p)
        assert info["refit_position"] == qq, (p, info["refit_position"], qq)          # no stale refit
        row = sched[(sched["refit_position"] == qq) & (sched["regime"] == (now if now != "unlabelled" else es.POOLED))].iloc[0]
        assert info["eps"] == row["eps"] and np.isin(info["eps"], grid)
        assert info["sharpe_net"] == pytest.approx(row["sharpe_net"], nan_ok=True)
        assert info["cvar_daily"] == pytest.approx(row["cvar_daily"], nan_ok=True)


def test_eps_for_date_does_not_use_returns_on_or_after_the_date(market):
    r, b = market
    c = small_cfg()
    fn = stress.LABEL_FUNCTIONS["hmm2_stress"]
    date = r.index[230]
    p = int(r.index.searchsorted(date))
    base = es.eps_for_date_info(date, "hmm2_stress", c, returns=r, benchmark=b, label_fn=fn)
    r2 = r.copy()
    r2.iloc[p:] = np.random.default_rng(2).normal(0.0, 0.2, size=r2.iloc[p:].shape)
    again = es.eps_for_date_info(date, "hmm2_stress", c, returns=r2, benchmark=r2.mean(axis=1), label_fn=fn)
    assert base["source"] != "default" and np.isfinite(base["sharpe_net"])
    assert again == base


def test_stress_regime_falls_back_to_pooled_below_the_minimum_block_count(caplog):
    c = small_cfg()
    rng = np.random.default_rng(0)
    net = np.stack([rng.normal(d, 0.01, size=(10, 30)) for d in (0.0, 0.001, 0.002)])
    regs = ["calm"] * 8 + ["stress"] * 2                       # 2 stress blocks < min_blocks (3)
    with caplog.at_level(logging.WARNING):
        sel = es.select_eps_by_regime(net, regs, c, rule="best")
    assert sel["stress"]["source"] == "pooled_fallback" and sel["stress"]["n_blocks"] == 2
    assert sel["stress"]["eps_index"] == sel[es.POOLED]["eps_index"]
    assert sel["calm"]["source"] == "regime"
    assert any("pooled eps" in rec.message for rec in caplog.records)


# ---- script exit status --------------------------------------------------------------------------------

def _args():
    return type("A", (), {"log": False})()


def test_script_exits_nonzero_when_grid_is_not_a_superset(monkeypatch, capsys):
    from scripts import run_stress as script
    monkeypatch.setattr(script, "extension_grid", lambda sec: np.logspace(-3, -2, 5))
    assert script.main(_args()) == 1
    assert "not a superset" in capsys.readouterr().err


def test_script_exits_nonzero_when_saved_probabilities_fail_verification(monkeypatch, capsys):
    from pathlib import Path
    from scripts import run_stress as script
    if not (Path(BASE["paths"]["tables_dir"]) / "regimes_labels.csv").exists():
        pytest.skip("saved regime labels not present")
    monkeypatch.setattr(script, "verify_saved_stress", lambda bench, saved, cfg: ["forced failure"])
    assert script.main(_args()) == 1
    assert "forced failure" in capsys.readouterr().err
