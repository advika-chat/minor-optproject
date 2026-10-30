"""Calm / stress regimes and a continuous stress level.

Stress level s_t (primary): the FILTERED probability, from the forward algorithm, that the
2-state Gaussian HMM of ``src.regimes`` is in its higher-variance state, using data up to and
including day t only (no smoothing, no future data). States are ordered by variance, so the stress
state is always the higher-variance one. The HMM is the existing "hmm2" pipeline (expanding window,
refit every ``hmm_refit_every`` days, several random restarts); its settings are read from the
``regimes_stress`` config section, which must equal the settings of the ``regimes`` section. s_t is
NaN until ``hmm_min_history`` returns are available.

Cross-check ``vol_percentile``: the rank of the current realised volatility (annualised, rolling
``vol_percentile_window`` days including day t) within the volatilities observed strictly before t,
available once ``vol_percentile_min_history`` past values exist.

Hard labels: ``hmm2_stress`` is "stress" if s_t > ``stress_threshold`` (strictly), else "calm";
``vol_two`` merges the volatile and crisis labels of the existing ``vol_threshold`` method into
"stress". Dates without enough history are "unlabelled". The label of date t uses data up to and
including t; as in ``src.regimes`` it can drive the portfolio held from t+1.
"""
import copy
from bisect import bisect_right, insort

import numpy as np
import pandas as pd

from src.regimes import UNLABELLED, regime_series, _as_series

CALM, STRESS = "calm", "stress"
_HMM_KEYS = ["hmm_min_history", "hmm_refit_every", "hmm_restarts", "hmm_max_iter", "hmm_tol"]


def hmm_config(config):
    """Copy of ``config`` whose ``regimes`` HMM settings and seed come from ``regimes_stress``."""
    rs = config["regimes_stress"]
    if rs["hmm_states"] != 2:
        raise ValueError("regimes_stress.hmm_states must be 2 (the stress state is the higher-variance state of 2)")
    c = copy.deepcopy(config)
    c["regimes"].update({k: rs[k] for k in _HMM_KEYS})
    c["seed"] = rs["seed"]
    return c


def stress_level(returns_or_benchmark, config):
    """Filtered probability of the higher-variance HMM state, as a Series named ``s_t``."""
    out = regime_series(returns_or_benchmark, "hmm2", hmm_config(config))
    return out["p_crisis"].rename("s_t")        # "crisis" is the higher-variance state of the 2-state labelling


def vol_percentile(returns_or_benchmark, config):
    """Expanding-window, past-only percentile rank of current realised volatility, in [0, 1]."""
    rs, td = config["regimes_stress"], config["trading_days"]
    r = _as_series(returns_or_benchmark)
    vol = (r.rolling(rs["vol_percentile_window"]).std() * np.sqrt(td)).to_numpy()
    out = np.full(len(vol), np.nan)
    past = []
    for t, v in enumerate(vol):
        if not np.isfinite(v):
            continue
        if len(past) >= rs["vol_percentile_min_history"]:
            out[t] = bisect_right(past, v) / len(past)        # share of strictly earlier values <= v
        insort(past, v)
    return pd.Series(out, index=r.index, name="vol_percentile")


def hard_labels(s, threshold):
    """"stress" where s > threshold, "calm" where s <= threshold, "unlabelled" where s is NaN."""
    lab = pd.Series(UNLABELLED, index=s.index, dtype=object)
    lab[s > threshold] = STRESS
    lab[s <= threshold] = CALM
    return lab


def labels_hmm2_stress(returns_or_benchmark, config):
    return hard_labels(stress_level(returns_or_benchmark, config), config["regimes_stress"]["stress_threshold"])


def labels_vol_two(returns_or_benchmark, config):
    """Two-bucket volatility labels: calm stays calm, volatile and crisis become stress."""
    lab = regime_series(returns_or_benchmark, "vol_threshold", config)["regime"]
    return lab.map({"calm": CALM, "volatile": STRESS, "crisis": STRESS, UNLABELLED: UNLABELLED})


LABEL_FUNCTIONS = {"hmm2_stress": labels_hmm2_stress, "vol_two": labels_vol_two}


def soft_eps(s, eps_calm, eps_stress):
    """eps_t = eps_calm + s_t * (eps_stress - eps_calm), elementwise (NaN where any input is NaN)."""
    return eps_calm + s * (eps_stress - eps_calm)
