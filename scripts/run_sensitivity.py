"""Sensitivity analysis, KKT and duality for the capped long-only mean-variance portfolios.

Run from the repo root:
  python -m scripts.run_sensitivity --time-draws    # time the mu-draw loop with the configured n_draws and print a suggested count
  python -m scripts.run_sensitivity [--log]         # full analysis
  python -m scripts.run_sensitivity --check-flagged-periods [--log]   # unimodality check in windows where rf is above the min-variance return
Requires data/processed/returns_simple.parquet. Engine: src/solver_own.py; cvxpy and src/markowitz.py are used only to cross-check.
New files carry the suffix set in config (sensitivity.output_suffix). The script exits with status 1 if any check fails.
No out-of-sample performance is computed here (that belongs to the backtest task).
"""
import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.run_solver_own import cvx_solve  # noqa: E402
from src import duality as du  # noqa: E402
from src import markowitz  # noqa: E402
from src import robust  # noqa: E402
from src import sensitivity as sn  # noqa: E402
from src import solver_own as so  # noqa: E402
from src.utils import get_logger, load_config, set_seed  # noqa: E402

log = get_logger("sensitivity")


def update_notes_blocks(path, blocks):
    """Replace the text between <!-- name:start --> and <!-- name:end --> markers; returns the names found."""
    p = Path(path)
    if not p.exists():
        return []
    text, found = p.read_text(encoding="utf-8"), []
    for name, body in blocks.items():
        pat = re.compile(rf"(<!-- {name}:start -->\n).*?(\n<!-- {name}:end -->)", re.S)
        if pat.search(text):
            text = pat.sub(lambda m: m.group(1) + body + m.group(2), text)
            found.append(name)
    p.write_text(text, encoding="utf-8")
    return found


def timing_run(cfg, rets):
    sens, opts, td = cfg["sensitivity"], cfg["solver_own"], cfg["trading_days"]
    mu, cov = sn.annualised_moments(rets, td)
    z = sn.make_draws(sens["seed"], sens["n_draws"], len(mu))
    t0 = time.time()
    sn.mu_sensitivity(cov, mu, cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"], opts, sens, len(rets) / td, z, sens["k_multipliers"])
    el = time.time() - t0
    print(f"mu-draw loop: {sens['n_draws']} draws x {len(sens['k_multipliers'])} k values in {el:.1f} s = {el / sens['n_draws']:.2f} s per draw")
    return el


def run_all(cfg, rets):
    sens, opts, td = cfg["sensitivity"], cfg["solver_own"], cfg["trading_days"]
    cap, rf, thr = cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"], sens["position_threshold"]
    tickers = list(rets.columns)
    mu, cov = sn.annualised_moments(rets, td)
    n, years = len(mu), len(rets) / td
    se = sn.standard_errors(cov, years)
    lines, failures, tables, figdata, timing = [], [], {}, {}, {}
    T = lambda name, t0: timing.__setitem__(name, round(time.time() - t0, 1))        # noqa: E731

    def check(ok, msg):
        if not ok:
            failures.append(msg)
        return ok

    # ---- base solutions and which constraints bind ------------------------------------------------------------------------
    t0 = time.time()
    mv = so.min_variance(cov, cap, opts)
    ms = sn.solve_max_sharpe(cov, mu, cap, rf, opts)
    base_mv, base_ms = sn.metrics(mv.weights, mu, cov, rf, thr), sn.metrics(ms.weights, mu, cov, rf, thr)
    lines += [f"- Base: {n} assets, {len(rets)} days ({years:.2f} years), cap {cap}, rf {rf} (assumed), annualised x{td}. Mean standard error sigma_i / sqrt(years): median {np.median(se):.3f}.",
              f"- Base min-variance: vol {base_mv['vol']:.4f}, return {base_mv['ret']:.4f}, Sharpe {base_mv['sharpe']:.3f}, {base_mv['n_positions']} positions, effective N {base_mv['effective_n']:.1f}. "
              f"Base max-Sharpe: Sharpe {base_ms['sharpe']:.4f}, return {base_ms['ret']:.4f}, vol {base_ms['vol']:.4f}, {base_ms['n_positions']} positions, effective N {base_ms['effective_n']:.1f}."]
    bind_rows = []
    for label, res in (("min_variance", mv), ("max_sharpe", ms.qp)):
        kk = so.kkt_residuals(cov, mu, cap, res)
        check(max(kk.values()) <= opts["kkt_residual_tol"], f"KKT residuals above tolerance at the base {label}")
        at_cap = [tickers[i] for i in res.active_upper]
        lines.append(f"- Binding at the base {label}: budget (nu_b {res.nu_budget:.4g})" + (f", return target r = {res.target:.4f} (nu_r {res.nu_return:.4g})" if res.target is not None else "")
                     + f"; {len(res.active_upper)} assets at the cap ({', '.join(at_cap)}); {len(res.active_lower)} at zero; {n - len(res.active_upper) - len(res.active_lower)} free. KKT max residual {max(kk.values()):.1e}.")
        bind_rows.append({"kind": "binding_base", "solution": label, "cap": cap, "n_at_cap": len(res.active_upper), "n_at_zero": len(res.active_lower),
                          "assets_at_cap": ";".join(at_cap), "nu_budget": res.nu_budget, "nu_return": res.nu_return, "total_cap_multiplier": float(res.lam_upper.sum())})
    T("base", t0)

    # ---- step 1: mu sensitivity ------------------------------------------------------------------------------------------
    t0 = time.time()
    z = sn.make_draws(sens["seed"], sens["n_draws"], n)
    ks = sens["k_multipliers"]
    mu_df, kept = sn.mu_sensitivity(cov, mu, cap, rf, opts, sens, years, z, ks, keep_first=sens["crosscheck_draws"])
    tables["mu_sensitivity"] = mu_df
    summ = mu_df.groupby("k").agg(l1_median=("l1_change", "median"), l1_mean=("l1_change", "mean"), l1_p95=("l1_change", lambda s: s.quantile(0.95)),
                                  dropped_mean=("share_base_positions_dropped", "mean"), poschange_mean=("positions_change", "mean"),
                                  poschange_sd=("positions_change", "std"), sharpe_base_mean=("sharpe_base_of_perturbed", "mean"),
                                  loss_base_truth_mean=("loss_base_truth", "mean"), loss_base_truth_p95=("loss_base_truth", lambda s: s.quantile(0.95)),
                                  loss_draw_truth_mean=("loss_draw_truth", "mean"), nonpositive=("nonpositive_sharpe", "sum"),
                                  minvar_max_l1=("min_variance_l1_change", "max")).reset_index()
    boot = sn.bootstrap_summary(mu_df, sens["bootstrap_resamples"], sens["bootstrap_seed"])
    summ = summ.merge(boot.drop(columns=[c for c in boot.columns if c in summ.columns and c != "k"]), on="k")
    tables["mu_sensitivity_summary"] = summ
    ci = lambda r, s: f"{r[s]:.3f} (bootstrap SE {r[s + '_boot_se']:.3f}, 95% interval [{r[s + '_boot_ci_lo']:.3f}, {r[s + '_boot_ci_hi']:.3f}])"      # noqa: E731
    lines.append(f"- mu sensitivity: {sens['n_draws']} seeded draws (seed {sens['seed']}) of mu_tilde ~ N(mu_hat, k^2 Sigma / years) for k in {ks}, common random numbers across k; "
                 f"standard errors and 95% intervals from a seeded bootstrap over the draws ({sens['bootstrap_resamples']} resamples, seed {sens['bootstrap_seed']}). "
                 "Max-Sharpe, relative to the base solution:")
    for _, r in summ.iterrows():
        lines.append(f"    k {r['k']}: L1 weight change median {ci(r, 'l1_median')} (mean {r.l1_mean:.3f}, 95th percentile {r.l1_p95:.3f}); share of base positions dropped {ci(r, 'dropped_mean')}; "
                     f"change in number of positions mean {r.poschange_mean:+.2f} (sd {r.poschange_sd:.2f}); Sharpe of the perturbed-optimal weights under the base mu, mean {ci(r, 'sharpe_base_mean')}, median {r.sharpe_base_median:.3f} "
                     f"(base optimum {base_ms['sharpe']:.3f}); estimation-error loss, truth = base mu, mean {ci(r, 'loss_base_truth_mean')}, median {r.loss_base_truth_median:.3f} (95th pct {r.loss_base_truth_p95:.3f}); "
                     f"truth = the draw, mean {ci(r, 'loss_draw_truth_mean')}, median {r.loss_draw_truth_median:.3f}; draws with non-positive Sharpe {int(r.nonpositive)}")
    lines.append("    The estimation-error loss is reported two ways because its wording is ambiguous: 'truth = base mu' is Sharpe_mu(w(mu)) - Sharpe_mu(w(mu_tilde)); 'truth = the draw' is Sharpe_mu_tilde(w(mu_tilde)) - Sharpe_mu_tilde(w(mu)).")
    mv_inv = float(mu_df["min_variance_l1_change"].max())
    lines.append(f"- Minimum-variance weights under mu perturbation: max L1 change over all draws and k = {mv_inv:.1e} (minimum variance does not depend on mu).")
    check(mv_inv <= sens["min_variance_invariance_tol"], "min-variance weights changed under mu perturbation")
    # zero perturbation: k = 0 must reproduce the base solution
    zero_df, _ = sn.mu_sensitivity(cov, mu, cap, rf, opts, sens, years, z[:2], [0.0])
    zmax = float(zero_df["l1_change"].max())
    lines.append(f"- Zero perturbation (k = 0, 2 draws): max L1 weight change {zmax:.1e}.")
    check(zmax <= sens["zero_perturbation_tol"], "zero mu perturbation changed the weights")
    # cvxpy cross-check on the first draws
    cw, cs, n_cx, n_skip = 0.0, 0.0, 0, 0
    cx_rows = []
    for (k, d), (mu_t, res) in kept.items():
        wc = cvx_solve(cov, mu_t, cap, res.target_return, opts["cvxpy_solver_tol"])
        dw = float(np.max(np.abs(res.weights - wc)))
        try:
            w_ref = markowitz.max_sharpe(mu_t, cov, rf, cap)
            s_ref = (mu_t @ w_ref - rf) / np.sqrt(w_ref @ cov @ w_ref)
            ds = abs(res.sharpe - s_ref)
        except ValueError:
            ds, n_skip = float("nan"), n_skip + 1
        cx_rows.append({"k": k, "draw": d, "max_abs_weight_diff_vs_cvxpy_qp": dw, "abs_sharpe_diff_vs_cvxpy_max_sharpe": ds})
        cw = max(cw, dw)
        cs = max(cs, ds) if np.isfinite(ds) else cs
        n_cx += 1
    tables["mu_crosscheck"] = pd.DataFrame(cx_rows)
    lines.append(f"- cvxpy cross-check of {n_cx} perturbed max-Sharpe solutions ({sens['crosscheck_draws']} draws per k): max |weight difference| of the own QP at the optimal target against a cvxpy equality-target solve {cw:.2e}; "
                 f"max |Sharpe difference| against the cvxpy max-Sharpe reformulation (default tolerance) {cs:.2e}; skipped because cvxpy's reformulation is infeasible: {n_skip}.")
    check(cw <= sens["crosscheck_weight_tol"], f"own QP differs from cvxpy by {cw:.2e} on perturbed draws")
    check(cs <= sens["crosscheck_sharpe_tol"], f"own max-Sharpe differs from cvxpy by {cs:.2e} on perturbed draws")
    # one asset at a time
    oa = sn.one_asset_response(cov, mu, cap, rf, opts, years, thr)
    oa.insert(1, "asset", [tickers[i] for i in oa["asset_index"]])
    tables["mu_one_asset"] = oa
    top = oa.sort_values("l1_response", ascending=False).head(sens["one_asset_top"])
    lines.append(f"- One asset at a time (mu_i + one standard error, max-Sharpe): L1 weight response median {oa['l1_response'].median():.3f}; the {sens['one_asset_top']} most sensitive assets by L1 response: "
                 + "; ".join(f"{r.asset} (L1 {r.l1_response:.3f}, own weight {r.base_weight:.3f} -> {r.new_weight:.3f}, finite difference d w_i / d mu_i {r.fd_dweight_i_dmu_i:.2f})" for r in top.itertuples()) + ".")
    figdata["mu_df"] = mu_df
    T("mu", t0)

    # ---- step 2: Sigma sensitivity ---------------------------------------------------------------------------------------------
    t0 = time.time()
    sg = sn.sigma_sensitivity(cov, mu, cap, rf, opts, sens)
    tables["sigma_sensitivity"] = sg
    check(bool(sg["psd"].all()), "a perturbed covariance matrix is not positive semi-definite")
    for tgt in sn.TARGETS:
        check(np.allclose(sn.shrink(cov, 0.0, tgt), cov) and np.allclose(sn.shrink(cov, 1.0, tgt), sn.TARGETS[tgt](cov)), f"shrinkage endpoints wrong for target {tgt}")
    lw = robust.ledoit_wolf_shrinkage(rets)
    lines.append(f"- Sigma sensitivity: {len(sg)} perturbed covariance matrices (shrinkage toward the scaled identity, the target of the Ledoit-Wolf estimator in src/robust.py, and toward a constant-correlation matrix, which is new here; "
                 f"volatility scale; correlation stress). All positive semi-definite: {bool(sg['psd'].all())} (smallest eigenvalue / largest {sg['min_eig_ratio'].min():.1e}). Ledoit-Wolf's own estimated shrinkage on the daily returns: {lw:.4f}.")
    for fam in sg["family"].unique():
        d = sg[sg["family"] == fam]
        a, b = d.iloc[0], d.iloc[-1]
        lines.append(f"    {fam} ({d['parameter'].iloc[0]} -> {d['parameter'].iloc[-1]}): condition number {a.condition_number:.3g} -> {b.condition_number:.3g}; max-Sharpe positions {int(a.maxsharpe_positions)} -> {int(b.maxsharpe_positions)}, "
                     f"effective N {a.maxsharpe_effective_n:.1f} -> {b.maxsharpe_effective_n:.1f}, Sharpe {a.maxsharpe_sharpe:.3f} -> {b.maxsharpe_sharpe:.3f}, L1 vs base {a.maxsharpe_l1_vs_base:.3f} -> {b.maxsharpe_l1_vs_base:.3f}; "
                     f"min-variance vol {a.minvar_vol:.4f} -> {b.minvar_vol:.4f}, positions {int(a.minvar_positions)} -> {int(b.minvar_positions)}, L1 vs base {a.minvar_l1_vs_base:.3f} -> {b.minvar_l1_vs_base:.3f}")
    T("sigma", t0)

    # ---- step 3: time periods ----------------------------------------------------------------------------------------------------
    t0 = time.time()
    pdf, pw, store = sn.period_analysis(rets, cap, rf, opts, sens, td)
    tables["period_sensitivity"], tables["period_weights"] = pdf, pw
    per = pdf[pdf["kind"] == "period"]
    lines.append(f"- Time periods ({len(per)} windows: sub-periods and calendar years; mu and Sigma re-estimated in each, same cap and rf). No out-of-sample performance is computed here; that belongs to the backtest task.")
    for r in per.itertuples():
        lines.append(f"    {r.period} ({r.days} days): mean mu {r.mean_mu:.3f}, median vol {r.median_vol:.3f}, mean correlation {r.mean_pairwise_corr:.2f}; min-variance vol {r.minvar_vol:.4f}, {int(r.minvar_positions)} positions; "
                     f"max-Sharpe Sharpe {r.maxsharpe_sharpe:.3f}, {int(r.maxsharpe_positions)} positions" + ("; rf above the min-variance return (golden-section unimodality assumption may fail)" if r.rf_above_min_variance_return else "")
                     + ("; non-positive Sharpe" if r.nonpositive_sharpe else ""))
    cy = pdf[pdf["kind"] == "consecutive_years"]
    lines.append(f"- Weight stability, L1 distance between consecutive calendar years: max-Sharpe mean {cy['maxsharpe_l1_distance'].mean():.3f} (min {cy['maxsharpe_l1_distance'].min():.3f}, max {cy['maxsharpe_l1_distance'].max():.3f}); "
                 f"min-variance mean {cy['minvar_l1_distance'].mean():.3f} (min {cy['minvar_l1_distance'].min():.3f}, max {cy['minvar_l1_distance'].max():.3f}). Maximum possible L1 distance is 2.")
    figdata["store"] = store
    T("periods", t0)

    # ---- step 4: cap and rf sweeps, shadow prices -----------------------------------------------------------------------------------
    t0 = time.time()
    cs_df, frontiers = sn.cap_sweep(cov, mu, rf, opts, sens, tickers)
    tables["cap_sweep"] = cs_df
    figdata["frontiers"] = frontiers
    var = cs_df["minvar_variance"].to_numpy()
    check(bool(np.all(np.diff(var) <= sens["monotone_tol"])), "optimal minimum variance increases with the cap")
    for col in ("minvar_cap_shadow_price", "maxsharpe_target_cap_shadow_price"):
        check(bool((cs_df[col] >= -opts["dual_tol"]).all()), f"negative cap shadow price in {col}")
    sp_rows, n_fd_ok, n_fd_skip, fd_fail = [], 0, 0, 0
    for r in cs_df.itertuples():
        for label, pre in (("min_variance", "minvar"), ("max_sharpe_target", "maxsharpe_target")):
            pred = -getattr(r, f"{pre}_cap_shadow_price")
            fd = getattr(r, f"{pre}_fd_dV_dcap")
            same, uniq = getattr(r, f"{pre}_fd_active_set_unchanged"), getattr(r, f"{pre}_multipliers_unique")
            if not same or not uniq or r.single_feasible_point:
                note, ok = "skipped: " + ("single feasible point" if r.single_feasible_point else "active set changed under the perturbation" if not same else "multipliers not unique"), True
                n_fd_skip += 1
            else:
                ok = abs(fd - pred) <= opts["fd_rel_tol"] * max(abs(fd), abs(pred)) + opts["fd_abs_floor"]
                note = "agree" if ok else "DISAGREE"
                n_fd_ok += ok
                fd_fail += not ok
            sp_rows.append({"kind": "shadow_price", "solution": label, "cap": r.cap, "shadow_price_sum_beta": -pred, "fd_dV_dcap": fd, "predicted_dV_dcap": pred,
                            "max_asset_multiplier": getattr(r, f"{pre}_max_asset_multiplier"), "n_at_cap": getattr(r, f"{pre}_n_at_cap"), "note": note})
    tables["shadow_prices"] = pd.DataFrame(sp_rows + bind_rows)
    check(fd_fail == 0, f"{fd_fail} cap finite differences disagree with the multipliers")
    lines.append(f"- Cap sweep ({len(cs_df)} caps from {cs_df['cap'].min()} (= 1/N) to {cs_df['cap'].max()}): min-variance volatility {cs_df['minvar_vol'].iloc[0]:.4f} -> {cs_df['minvar_vol'].iloc[-1]:.4f}, max-Sharpe Sharpe "
                 f"{cs_df['maxsharpe_sharpe'].iloc[0]:.3f} -> {cs_df['maxsharpe_sharpe'].iloc[-1]:.3f}; optimal variance non-increasing in the cap: {bool(np.all(np.diff(var) <= sens['monotone_tol']))}; "
                 f"cap shadow price (sum of the upper-bound multipliers, min-variance) from {cs_df['minvar_cap_shadow_price'].iloc[0]:.4f} at cap {cs_df['cap'].iloc[0]} to {cs_df['minvar_cap_shadow_price'].iloc[-1]:.4f} at cap {cs_df['cap'].iloc[-1]}, "
                 f"all non-negative: {bool((cs_df['minvar_cap_shadow_price'] >= -opts['dual_tol']).all())}.")
    lines.append(f"    Identity 'finite-difference change in optimal variance from loosening the cap = -(sum of the cap multipliers)': {n_fd_ok} checks agree within {opts['fd_rel_tol']:g} (plus {opts['fd_abs_floor']:g} absolute), {n_fd_skip} skipped "
                 f"(single feasible point at cap = 1/N, a changed active set or non-unique multipliers), {fd_fail} disagree.")
    for r in cs_df.itertuples():
        lines.append(f"    cap {r.cap:g}: min-variance vol {r.minvar_vol:.4f} ({r.minvar_positions} positions, {r.minvar_n_at_cap} at cap, shadow price {r.minvar_cap_shadow_price:.4f}); max-Sharpe {r.maxsharpe_sharpe:.3f} "
                     f"({r.maxsharpe_positions} positions, {r.maxsharpe_target_n_at_cap} at cap, shadow price {r.maxsharpe_target_cap_shadow_price:.4f})")
    rf_df, rf_w = sn.rf_sweep(cov, mu, cap, opts, sens, rf)
    tables["rf_sweep"] = rf_df
    lines.append("- rf sweep (min-variance does not depend on rf): " + "; ".join(
        f"rf {r.rf:g}: max-Sharpe Sharpe {r.maxsharpe_sharpe:.3f}, return {r.maxsharpe_ret:.4f}, {r.maxsharpe_n_positions} positions, L1 vs base rf {r.l1_vs_base_rf:.3f}" for r in rf_df.itertuples()) + ".")
    T("cap_rf", t0)

    # ---- step 5: scenario payoff table ----------------------------------------------------------------------------------------------
    t0 = time.time()
    pf = sn.base_portfolios(cov, mu, cap, rf, opts, sens["frontier_fraction"])
    for sc in sens["scenarios"]:
        _, cov_s, _ = sn.scenario_parameters(mu, cov, se, sc, rf)
        check(sn.is_psd(cov_s, sens["psd_tol"]), f"scenario covariance not PSD: {sc['name']}")
    long, sharpe, regret = sn.payoff_tables(pf, mu, cov, se, rf, sens["scenarios"])
    tables["scenario_payoffs_long"] = long
    tables["scenario_sharpe_matrix"] = sharpe.reset_index()
    tables["scenario_regret_matrix"] = regret.reset_index()
    check(bool((regret.min(axis=0).abs() <= 1e-12).all()) and bool((regret >= -1e-12).all().all()), "regret matrix has no zero per scenario or has negative entries")
    figdata["sharpe_matrix"] = sharpe
    lines.append("- Scenario payoffs (weights computed once on the base estimates; scenario parameters: bear / bull = mu -/+ one standard error, high volatility = vol x1.5, high correlation = c 0.5, rf 8%). Sharpe matrix (rows portfolios, columns scenarios):\n"
                 + "\n".join("    " + ln for ln in sharpe.round(3).to_string().splitlines()))
    lines.append("  Regret matrix (best Sharpe in the scenario among the four portfolios minus the portfolio's Sharpe):\n" + "\n".join("    " + ln for ln in regret.round(3).to_string().splitlines())
                 + "\n  No decision rule is chosen here; these are inputs for the game-theory task.")
    T("scenarios", t0)

    # ---- step 6: duality ----------------------------------------------------------------------------------------------------------------
    t0 = time.time()
    dop = sens["duality"]
    rng = np.random.default_rng(dop["seed"])
    r_mv = float(mu @ mv.weights)
    r_max = so.feasible_return_range(mu, cap)[1]
    sols = [("min_variance", mv), ("max_sharpe", ms.qp)]
    for f in opts["kkt_target_fractions"]:
        sols.append((f"target_{int(round(100 * f))}pct", so.solve_qp(cov, mu, cap, opts, target=float(r_mv + f * (r_max - r_mv)))))
    drows = []
    for label, res in sols:
        c = du.check_duality(cov, mu, cap, res, dop, rng)
        drows.append({"solution": label, "target_return": res.target, **c})
        check(c["strong_gap"] <= dop["strong_gap_tol"], f"strong duality gap {c['strong_gap']:.2e} at {label}")
        check(c["weak_duality_violations"] == 0, f"weak duality violated at {label}")
        check(c["complementary_slackness"] <= dop["complementary_tol"], f"complementary slackness residual at {label}")
    ex = du.worked_example(cfg)
    c = du.check_duality(ex["sigma"], ex["mu"], ex["cap"], ex["res"], dop, rng)
    drows.append({"solution": "worked_example_3_assets", "target_return": ex["target"], **c})
    check(c["strong_gap"] <= dop["strong_gap_tol"] and c["weak_duality_violations"] == 0, "duality check failed on the worked example")
    tables["duality_checks"] = pd.DataFrame(drows)
    dd = tables["duality_checks"]
    lines.append(f"- Duality (g = lam + gam r - cap sum(beta) - (1/4) q' Sigma^-1 q, q = lam 1 + gam mu + alpha - beta; see docs/kkt_duality_notes.md), {len(sols)} real-data solutions and a 3-asset example: "
                 f"strong duality gap at the solver's multipliers max {dd['strong_gap'].max():.1e} (tolerance {dop['strong_gap_tol']:g}); weak duality on {dop['random_multipliers']} random dual-feasible multiplier vectors per solution: "
                 f"violations {int(dd['weak_duality_violations'].sum())}, largest (dual - primal) {dd['weak_duality_max_excess'].max():.2e} (must be <= 0); complementary slackness residual max {dd['complementary_slackness'].max():.1e}; "
                 f"distance between the Lagrangian minimiser and the primal solution max {dd['lagrangian_minimiser_distance'].max():.1e}.")
    figdata["example"] = ex
    figdata["duality_df"] = dd
    T("duality", t0)

    lines.append("- Section timings (s): " + ", ".join(f"{k} {v}" for k, v in timing.items()) + f"; draws used: {sens['n_draws']}.")
    return {"tables": tables, "lines": lines, "failures": failures, "figdata": figdata, "timing": timing, "tickers": tickers, "cap": cap}


def make_figures(res, cfg, figs_dir, suffix):
    sens = cfg["sensitivity"]
    fd = res["figdata"]
    # mu perturbation
    df = fd["mu_df"]
    fig, ax = plt.subplots(1, 2, figsize=(12, 4.5))
    ks = sorted(df["k"].unique())
    ax[0].boxplot([df[df["k"] == k]["l1_change"] for k in ks], tick_labels=[str(k) for k in ks])
    ax[0].set_xlabel("k (perturbation multiplier)")
    ax[0].set_ylabel("L1 change of max-Sharpe weights vs base")
    ax[0].set_title("Weight instability under estimation error")
    w = 0.35
    x = np.arange(len(ks))
    ax[1].bar(x - w / 2, [df[df["k"] == k]["loss_base_truth"].mean() for k in ks], w, label="truth = base mu")
    ax[1].bar(x + w / 2, [df[df["k"] == k]["loss_draw_truth"].mean() for k in ks], w, label="truth = the draw")
    ax[1].set_xticks(x)
    ax[1].set_xticklabels([str(k) for k in ks])
    ax[1].set_xlabel("k")
    ax[1].set_ylabel("mean Sharpe lost")
    ax[1].set_title("Estimation-error loss")
    ax[1].legend()
    fig.suptitle(f"Sensitivity to mu ({sens['n_draws']} seeded draws), in-sample estimates", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs_dir / f"mu_perturbation{suffix}.png", dpi=150)
    plt.close(fig)
    # Sigma sweeps
    sg = res["tables"]["sigma_sensitivity"]
    fig, axes = plt.subplots(2, 3, figsize=(14, 7))
    groups = [("shrinkage", ["shrink_identity", "shrink_constant_correlation"]), ("volatility scale", ["vol_scale"]), ("correlation stress c", ["corr_stress"])]
    for j, (title, fams) in enumerate(groups):
        for fam in fams:
            d = sg[sg["family"] == fam]
            axes[0, j].plot(d["parameter"], d["maxsharpe_positions"], marker="o", label=f"max-Sharpe {fam}")
            axes[0, j].plot(d["parameter"], d["minvar_positions"], marker="s", ls="--", label=f"min-variance {fam}")
            axes[1, j].plot(d["parameter"], d["condition_number"], marker="o", label=fam)
        axes[0, j].set_title(title)
        axes[0, j].set_ylabel("number of positions")
        axes[1, j].set_yscale("log")
        axes[1, j].set_ylabel("condition number of Sigma")
        axes[1, j].set_xlabel(title)
        axes[0, j].legend(fontsize=6)
        axes[1, j].legend(fontsize=7)
    fig.suptitle("Sensitivity to Sigma: positions and conditioning", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs_dir / f"sigma_sweeps{suffix}.png", dpi=150)
    plt.close(fig)
    # weights heatmap across years
    store, tickers = fd["store"], res["tickers"]
    years = [str(y) for y in sens["calendar_years"] if (str(y), "max_sharpe") in store]
    fig, axes = plt.subplots(1, 2, figsize=(15, 8))
    for ax, kind, title in ((axes[0], "max_sharpe", "max-Sharpe"), (axes[1], "min_variance", "min-variance")):
        mat = np.array([store[(y, kind)] for y in years]).T
        held = np.where(mat.max(axis=1) > sens["position_threshold"])[0]
        im = ax.imshow(mat[held], aspect="auto", cmap="viridis", vmin=0, vmax=res["cap"])
        ax.set_xticks(range(len(years)))
        ax.set_xticklabels(years, rotation=60)
        ax.set_yticks(range(len(held)))
        ax.set_yticklabels([tickers[i] for i in held], fontsize=6)
        ax.set_title(f"{title} weights by calendar year (assets ever held)")
        fig.colorbar(im, ax=ax, shrink=0.6)
    fig.suptitle("Weights re-estimated on each calendar year (in-sample, no out-of-sample performance)", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs_dir / f"weights_heatmap_years{suffix}.png", dpi=150)
    plt.close(fig)
    # cap shadow price and frontier
    cs = res["tables"]["cap_sweep"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for cap, (vol, ret) in fd["frontiers"].items():
        axes[0].plot(vol, ret, label=f"cap {cap:g}")
    axes[0].set_xlabel("volatility")
    axes[0].set_ylabel("return")
    axes[0].set_title("Frontier as the cap loosens")
    axes[0].legend()
    axes[1].plot(cs["cap"], cs["minvar_cap_shadow_price"], marker="o", label="min-variance")
    axes[1].plot(cs["cap"], cs["maxsharpe_target_cap_shadow_price"], marker="s", label="at the max-Sharpe target return")
    axes[1].axvline(res["cap"], color="grey", ls="--", lw=0.8)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("cap")
    axes[1].set_ylabel("cap shadow price: sum of upper-bound multipliers = -dV/dcap")
    axes[1].set_title("Cap shadow price")
    axes[1].legend()
    fig.suptitle("Cap sweep (dashed line: base cap)", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs_dir / f"cap_shadow_price{suffix}.png", dpi=150)
    plt.close(fig)
    # scenario Sharpe heatmap
    sh = fd["sharpe_matrix"]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    im = ax.imshow(sh.values, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(sh.shape[1]))
    ax.set_xticklabels(sh.columns, rotation=30)
    ax.set_yticks(range(sh.shape[0]))
    ax.set_yticklabels(sh.index)
    for i in range(sh.shape[0]):
        for j in range(sh.shape[1]):
            ax.text(j, i, f"{sh.values[i, j]:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax)
    ax.set_title("Sharpe ratio by portfolio and scenario (weights from the base estimates)")
    fig.tight_layout()
    fig.savefig(figs_dir / f"scenario_sharpe_heatmap{suffix}.png", dpi=150)
    plt.close(fig)


def flagged_period_check(cfg, rets):
    """Check the Sharpe-unimodality assumption (golden-section search) in every window where rf is above the min-variance return."""
    sens, opts, td = cfg["sensitivity"], cfg["solver_own"], cfg["trading_days"]
    cap, rf = cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"]
    tables_dir = Path(cfg["paths"]["tables_dir"])
    pdf = pd.read_csv(tables_dir / f"period_sensitivity{sens['output_suffix']}.csv")
    flagged = pdf[(pdf["kind"] == "period") & (pdf["rf_above_min_variance_return"] == True)]["period"].tolist()  # noqa: E712
    wins = sn.period_windows(sens)
    lines, failures = [f"- Unimodality check in the {len(flagged)} windows where rf is above the min-variance return ({', '.join(flagged) or 'none'}), "
                       f"on a {opts['unimodality_grid_points']}-point grid of target returns, against the golden-section result:"], []
    for name in flagged:
        a, b = wins[name]
        win = rets.loc[a:b]
        mu, cov = sn.annualised_moments(win, td)
        grid_r, grid_s = so.sharpe_profile(cov, mu, cap, rf, opts)
        vv = so.count_valleys(grid_s, opts["unimodality_noise_tol"])
        res = so.max_sharpe(cov, mu, cap, rf, opts)
        k = int(np.argmax(grid_s))
        same = abs(res.target_return - grid_r[k]) <= grid_r[1] - grid_r[0] + 1e-12 and res.sharpe >= grid_s[k] - 1e-12
        lines.append(f"    {name}: {vv['n_peaks']} interior peak(s), {vv['n_valleys']} valley(s) -> unimodal: {vv['n_valleys'] == 0}; golden-section Sharpe {res.sharpe:.6f} at return {res.target_return:.4f}, "
                     f"grid maximum {grid_s[k]:.6f} at {grid_r[k]:.4f}; agree: {same}; maximum at a boundary of the search interval: {res.at_boundary}; Sharpe at the minimum-variance return {grid_s[0]:.3f}")
        if vv["n_valleys"] != 0 or not same:
            failures.append(f"unimodality assumption fails or golden-section disagrees with the grid in window {name}")
    return lines, failures


def main(args, cfg=None):
    cfg = load_config() if cfg is None else cfg
    set_seed(cfg["sensitivity"]["seed"])
    suffix = cfg["sensitivity"]["output_suffix"]
    rets = pd.read_parquet(Path(cfg["data"]["processed_dir"]) / "returns_simple.parquet")
    if args.time_draws:
        timing_run(cfg, rets)
        return 0
    if args.check_flagged_periods:
        lines, failures = flagged_period_check(cfg, rets)
        if failures:
            lines.append("- CHECKS FAILED: " + "; ".join(failures))
        print("\n".join(lines))
        if args.log:
            with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
                f.write(f"\n## {datetime.now():%Y-%m-%d} - Sensitivity: unimodality check in flagged windows (scripts/run_sensitivity.py --check-flagged-periods)\n" + "\n".join(lines) + "\n")
        if failures:
            print("FAILED: " + "; ".join(failures), file=sys.stderr)
            return 1
        return 0
    t0 = time.time()
    res = run_all(cfg, rets)
    tables_dir, figs_dir = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    for name, df in res["tables"].items():
        df.to_csv(tables_dir / f"{name}{suffix}.csv", index=False)
    if res["figdata"]:
        make_figures(res, cfg, figs_dir, suffix)
        ex, dd = res["figdata"]["example"], res["figdata"]["duality_df"]
        ver = "\n".join(f"- {r.solution}: primal {r.primal:.8f}, dual {r.dual_at_solver_multipliers:.8f}, gap {r.strong_gap:.1e}; weak duality: largest (dual - primal) over {cfg['sensitivity']['duality']['random_multipliers']} random "
                        f"dual-feasible vectors {r.weak_duality_max_excess:.2e} ({int(r.weak_duality_violations)} violations); complementary slackness {r.complementary_slackness:.1e}" for r in dd.itertuples())
        found = update_notes_blocks(cfg["sensitivity"]["notes_path"], {"worked-example": du.worked_example_markdown(ex), "verification": ver})
        res["lines"].append(f"- Notes blocks regenerated in {cfg['sensitivity']['notes_path']}: {found}.")
    lines, failures = res["lines"], res["failures"]
    lines.append(f"- Total run time {time.time() - t0:.0f} s." + (" ALL CHECKS PASSED." if not failures else ""))
    if failures:
        lines.append("- CHECKS FAILED: " + "; ".join(failures))
    print("\n".join(lines))
    if args.log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Sensitivity analysis, KKT and duality (scripts/run_sensitivity.py)\n" + "\n".join(lines) + "\n")
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--time-draws", action="store_true")
    ap.add_argument("--check-flagged-periods", action="store_true")
    sys.exit(main(ap.parse_args()))
