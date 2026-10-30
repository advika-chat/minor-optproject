import copy
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import regimes
from src.utils import load_config

BASE = load_config()


def small_cfg():
    c = copy.deepcopy(BASE)
    c["regimes"].update({"vol_window": 10, "vol_min_history": 60, "hmm_min_history": 150,
                         "hmm_refit_every": 40, "hmm_restarts": 2, "hmm_max_iter": 60})
    return c


@pytest.fixture(scope="module")
def series():
    rng = np.random.default_rng(BASE["seed"])
    blocks = [(0.004, 180), (0.025, 80), (0.004, 150), (0.02, 70), (0.005, 120)]
    r = np.concatenate([rng.normal(0.0004, s, n) for s, n in blocks])
    return pd.Series(r, index=pd.bdate_range("2018-01-01", periods=len(r)))


METHODS = ["vol_threshold", "hmm2", "hmm3"]


@pytest.mark.parametrize("method", METHODS)
def test_no_look_ahead_appending_future_data_changes_nothing(series, method):
    c = small_cfg()
    cut = 450
    short = regimes.regime_series(series.iloc[:cut], method, c)
    full = regimes.regime_series(series, method, c)
    a, b = short, full.loc[short.index]
    assert (a["regime"].to_numpy() == b["regime"].to_numpy()).all()
    num = a.select_dtypes("number").columns
    np.testing.assert_allclose(a[num].to_numpy(float), b[num].to_numpy(float), atol=1e-9, equal_nan=True)


def test_future_shock_does_not_change_past_labels(series):
    c = small_cfg()
    shocked = series.copy()
    shocked.iloc[500:] *= 25.0
    for method in METHODS:
        a = regimes.regime_series(series, method, c)
        b = regimes.regime_series(shocked, method, c)
        assert (a["regime"].iloc[:500].to_numpy() == b["regime"].iloc[:500].to_numpy()).all()


def test_vol_thresholds_use_only_strictly_past_volatility(series):
    c = small_cfg()
    out = regimes.regime_series(series, "vol_threshold", c)
    rc, td = c["regimes"], c["trading_days"]
    vol = series.rolling(rc["vol_window"]).std() * np.sqrt(td)
    lo_q, hi_q = rc["vol_quantiles"]
    for pos in [100, 250, 400, 599]:
        past = vol.iloc[:pos].dropna().to_numpy()            # strictly before position pos
        assert out["lower"].iloc[pos] == pytest.approx(np.quantile(past, lo_q))
        assert out["upper"].iloc[pos] == pytest.approx(np.quantile(past, hi_q))
    n_unlabelled = (out["regime"] == regimes.UNLABELLED).sum()
    assert n_unlabelled >= rc["vol_min_history"]              # minimum history enforced
    first_labelled = out.index[out["regime"] != regimes.UNLABELLED][0]
    assert vol.shift(1).loc[:first_labelled].notna().sum() >= rc["vol_min_history"]


def test_hmm_states_ordered_by_variance_across_refits(series):
    rc = small_cfg()["regimes"]
    x = series.to_numpy() * 100
    for end in [300, 450, 600]:
        for k in (2, 3):
            fit = regimes.fit_hmm(x[:end], k, 5, 2, rc["hmm_max_iter"], rc["hmm_tol"])
            assert np.all(np.diff(fit["vars"]) > 0)
    out = regimes.regime_series(series, "hmm2", small_cfg())
    lab = out[out["regime"] != regimes.UNLABELLED]
    assert lab.loc[lab["regime"] == "calm"].index.size > 0 and lab.loc[lab["regime"] == "crisis"].index.size > 0
    absr = series.abs().loc[lab.index]
    assert absr[lab["regime"] == "calm"].mean() < absr[lab["regime"] == "crisis"].mean()


def test_hmm_probabilities_are_filtered_not_smoothed(series):
    rc = small_cfg()["regimes"]
    x = series.to_numpy()[:400] * 100
    fit = regimes.fit_hmm(x, 2, 3, 2, rc["hmm_max_iter"], rc["hmm_tol"])
    mine = regimes.filtered_probs(x, fit)
    for t in [150, 250, 399]:
        # at the last point of a prefix, smoothed and filtered posteriors coincide
        ref = fit["model"].predict_proba(x[: t + 1].reshape(-1, 1))[-1][fit["order"]]
        np.testing.assert_allclose(mine[t], ref, atol=1e-8)
    # but the smoothed posterior at an interior point differs from the filtered one
    smooth = fit["model"].predict_proba(x.reshape(-1, 1))[:, fit["order"]]
    assert np.abs(smooth[:-1] - mine[:-1]).max() > 1e-3
    np.testing.assert_allclose(mine.sum(axis=1), 1.0)


@pytest.mark.parametrize("method", METHODS)
def test_reproducible_with_seed(series, method):
    c = small_cfg()
    a = regimes.regime_series(series, method, c)
    b = regimes.regime_series(series, method, c)
    pd.testing.assert_frame_equal(a, b)


def test_hmm_reports_convergence_columns(series):
    out = regimes.regime_series(series, "hmm2", small_cfg())
    lab = out[out["regime"] != regimes.UNLABELLED]
    assert lab["fit_converged"].isin([0.0, 1.0]).all()
    assert (lab["restarts_failed"] >= 0).all() and lab["refit_date"].notna().all()
    assert (lab["refit_date"] < lab.index).all()              # model fitted strictly before the label date


def test_module_uses_no_smoothing_or_viterbi_calls():
    src = Path(regimes.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    for banned in [".predict(", ".predict_proba(", ".decode(", "_forward", "_backward"]:
        assert banned not in code


def test_unknown_method_raises(series):
    with pytest.raises(ValueError):
        regimes.regime_series(series, "nope", small_cfg())
