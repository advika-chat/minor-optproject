"""Market regime detection with no look-ahead.

``regime_series(returns_or_benchmark, method, config)`` returns a DataFrame indexed by
date. The label for date t uses data up to and including t only (closing return of day t).
In a backtest the label of date t can therefore drive the portfolio held from t+1; using it
for the return earned on day t itself would be look-ahead. Dates without enough history are
labelled "unlabelled".

Input: a Series of daily simple returns of the benchmark, or a DataFrame. A DataFrame with a
"simple" column (data/processed/benchmark.parquet) uses that column; any other DataFrame is
averaged across columns (an equal-weight market proxy).

Methods
-------
"vol_threshold": annualised rolling realised volatility of the last ``regimes.vol_window``
    returns (including day t). Regime thresholds at date t are the ``regimes.vol_quantiles``
    expanding quantiles of the volatilities observed strictly before t; calm if vol <= lower,
    crisis if vol > upper, volatile in between. Labelling starts once ``vol_min_history``
    past volatilities exist.
"hmm2", "hmm3": Gaussian HMM with 2 (calm, crisis) or 3 (calm, volatile, crisis) states on
    daily returns in percent. Model j is fitted on all returns up to refit date r_j
    (expanding window), r_j = hmm_min_history - 1 + j * hmm_refit_every (positions counted from
    the first return). It labels dates r_j + 1 .. r_{j+1}. Each fit uses ``hmm_restarts``
    random restarts and keeps the best log-likelihood. States are ordered by estimated
    variance, so label k means the same thing in every refit. The probabilities are FILTERED:
    P(S_t | y_0..y_t) from a forward pass that uses model j's parameters and observations up
    to t only. No predict(), Viterbi decoding or smoothed posteriors are used. Fit
    diagnostics (converged, log-likelihood, restarts converged / failed) are returned in
    columns; a refit whose restarts all fail keeps the previous model and sets fit_failed.
    Non-convergence is reported in ``fit_converged``, never hidden.

Detection delay: the volatility method reacts only after the rolling window fills with
turbulent returns, and filtered HMM probabilities need several observations to move away from
the previous state. Both lag true regime changes.
"""
import warnings

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from scipy.stats import norm

from src.utils import get_logger

log = get_logger(__name__)

UNLABELLED = "unlabelled"
STATE_LABELS = {2: ["calm", "crisis"], 3: ["calm", "volatile", "crisis"]}
HMM_METHODS = {"hmm2": 2, "hmm3": 3}


def _as_series(x):
    if isinstance(x, pd.DataFrame):
        x = x["simple"] if "simple" in x.columns else x.mean(axis=1)
    s = pd.Series(x).astype(float).dropna()
    if not s.index.is_monotonic_increasing:
        raise ValueError("returns must be sorted by date")
    return s


def _vol_threshold(r, config):
    rc, td = config["regimes"], config["trading_days"]
    lo_q, hi_q = rc["vol_quantiles"]
    vol = r.rolling(rc["vol_window"]).std() * np.sqrt(td)
    past = vol.shift(1)                                   # volatilities strictly before t
    lower = past.expanding(min_periods=rc["vol_min_history"]).quantile(lo_q)
    upper = past.expanding(min_periods=rc["vol_min_history"]).quantile(hi_q)
    ok = vol.notna() & lower.notna()
    regime = pd.Series(UNLABELLED, index=r.index, dtype=object)
    regime[ok & (vol <= lower)] = "calm"
    regime[ok & (vol > lower) & (vol <= upper)] = "volatile"
    regime[ok & (vol > upper)] = "crisis"
    return pd.DataFrame({"regime": regime, "vol": vol, "lower": lower, "upper": upper})


def fit_hmm(x_pct, n_states, seed, n_restarts, max_iter, tol):
    """Fit a Gaussian HMM with random restarts and return parameters ordered by variance.

    Keeps the restart with the best log-likelihood (converged or not) and records whether it
    converged. Raises RuntimeError if every restart fails."""
    best, n_conv, n_fail = None, 0, 0
    for k in range(n_restarts):
        model = GaussianHMM(n_components=n_states, covariance_type="full",
                            n_iter=max_iter, tol=tol, random_state=seed + k)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model.fit(x_pct.reshape(-1, 1))
                score = float(model.score(x_pct.reshape(-1, 1)))
        except Exception as e:  # noqa: BLE001 - reported through n_failed
            n_fail += 1
            log.warning("HMM restart failed: %s", e)
            continue
        conv = bool(model.monitor_.converged)
        n_conv += conv
        if best is None or score > best[0]:
            best = (score, model, conv)
    if best is None:
        raise RuntimeError(f"all {n_restarts} HMM restarts failed")
    score, model, conv = best
    var = np.asarray(model.covars_).reshape(n_states)
    order = np.argsort(var)
    return {
        "model": model, "order": order, "means": model.means_[order, 0], "vars": var[order],
        "transmat": model.transmat_[np.ix_(order, order)], "startprob": model.startprob_[order],
        "loglik": score, "converged": conv, "n_converged": n_conv, "n_failed": n_fail,
    }


def filtered_probs(x_pct, params):
    """Forward algorithm: row t is P(S_t | y_0..y_t) under the given (ordered) parameters."""
    k = len(params["means"])
    logb = norm.logpdf(x_pct[:, None], params["means"][None, :], np.sqrt(params["vars"])[None, :])
    out = np.empty((len(x_pct), k))
    prev = params["startprob"]
    for t in range(len(x_pct)):
        pred = prev if t == 0 else prev @ params["transmat"]
        unnorm = pred * np.exp(logb[t] - logb[t].max())
        prev = unnorm / unnorm.sum()
        out[t] = prev
    return out


def _hmm(r, n_states, config):
    rc = config["regimes"]
    labels = STATE_LABELS[n_states]
    x = r.to_numpy() * 100.0
    n = len(x)
    first, step = rc["hmm_min_history"] - 1, rc["hmm_refit_every"]
    cols = {"regime": np.array([UNLABELLED] * n, dtype=object),
            "fit_converged": np.full(n, np.nan), "fit_loglik": np.full(n, np.nan),
            "restarts_converged": np.full(n, np.nan), "restarts_failed": np.full(n, np.nan),
            "fit_failed": np.zeros(n, dtype=bool),
            "refit_date": np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")}
    probs = np.full((n, n_states), np.nan)
    refits = list(range(first, n - 1, step))
    params, failed_flag = None, False
    for j, idx in enumerate(refits):
        end = refits[j + 1] if j + 1 < len(refits) else n - 1
        try:
            params = fit_hmm(x[: idx + 1], n_states, config["seed"] + 1000 * j,
                             rc["hmm_restarts"], rc["hmm_max_iter"], rc["hmm_tol"])
            failed_flag = False
        except RuntimeError as e:
            log.warning("HMM refit at %s failed: %s", r.index[idx].date(), e)
            failed_flag = True
            if params is None:
                continue                                    # nothing to label with yet
        if not params["converged"] and not failed_flag:
            log.warning("HMM (%d states) refit at %s did not converge (best of %d restarts)",
                        n_states, r.index[idx].date(), rc["hmm_restarts"])
        seg = slice(idx + 1, end + 1)
        p = filtered_probs(x[: end + 1], params)[seg]
        probs[seg] = p
        cols["regime"][seg] = np.array(labels, dtype=object)[p.argmax(axis=1)]
        cols["fit_converged"][seg] = float(params["converged"]) if not failed_flag else 0.0
        cols["fit_loglik"][seg] = params["loglik"]
        cols["restarts_converged"][seg] = params["n_converged"]
        cols["restarts_failed"][seg] = rc["hmm_restarts"] if failed_flag else params["n_failed"]
        cols["fit_failed"][seg] = failed_flag
        cols["refit_date"][seg] = np.datetime64(r.index[idx])
    out = pd.DataFrame(cols, index=r.index)
    for i, lab in enumerate(labels):
        out[f"p_{lab}"] = probs[:, i]
    return out


def regime_series(returns_or_benchmark, method, config):
    """Regime labels (and, for the HMM, filtered state probabilities) by date.
    ``method`` is "vol_threshold", "hmm2" or "hmm3"; see the module docstring."""
    r = _as_series(returns_or_benchmark)
    if method == "vol_threshold":
        return _vol_threshold(r, config)
    if method in HMM_METHODS:
        return _hmm(r, HMM_METHODS[method], config)
    raise ValueError(f"unknown method {method!r}; use vol_threshold, hmm2 or hmm3")
