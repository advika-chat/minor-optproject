"""Verify and report the own active-set QP solver (src/solver_own.py).

Run from the repo root:  python -m scripts.run_solver_own [--log]
Requires data/processed/returns_simple.parquet (scripts/run_data.py). cvxpy and src/markowitz.py are used only
as references. New files carry the suffix set in config (solver_own.output_suffix). The script exits with
status 1 if any verification check fails.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import cvxpy as cp  # noqa: E402

from src import markowitz  # noqa: E402
from src import solver_own as so  # noqa: E402
from src.utils import get_logger, load_config, set_seed  # noqa: E402

log = get_logger("solver_own")

INTERPRETATION = {
    "budget": "nu_b = dV/d(budget): variance added per unit of extra capital invested (V = w' Sigma w)",
    "return": "nu_r = dV/dr: slope of the minimum variance against the target return (the marginal variance cost of return)",
    "upper": "lam_upper_i = -dV/d(cap_i): variance saved per unit of loosening the cap on this asset",
    "lower": "lam_lower_i = dV/d(lower bound_i): variance added per unit of forcing a minimum holding in this asset (cost of the no-short rule)",
}


def cvx_solve(sigma, mu, cap, target, tol):
    """Reference solution of P(r) with cvxpy/Clarabel at the configured tolerance."""
    n = len(mu)
    w = cp.Variable(n)
    cons = [cp.sum(w) == 1, w >= 0, w <= cap] + ([mu @ w == target] if target is not None else [])
    prob = cp.Problem(cp.Minimize(cp.quad_form(w, cp.psd_wrap(sigma))), cons)
    prob.solve(solver=cp.CLARABEL, tol_gap_abs=tol, tol_gap_rel=tol, tol_feas=tol, max_iter=500)
    if prob.status not in ("optimal", "optimal_inaccurate"):
        return None
    wv = np.clip(w.value, 0.0, cap)
    return wv


def random_problem(rng, opts):
    n = int(rng.integers(opts["random_n_min"], opts["random_n_max"] + 1))
    k = max(2, n // 2)
    b = rng.normal(size=(n, k))
    lo, hi = opts["random_ridge_range"]
    sigma = b @ b.T / k + 10 ** rng.uniform(np.log10(lo), np.log10(hi)) * np.eye(n)
    mu = rng.uniform(0.02, 0.2, n)
    cap = 1.0 / n if rng.uniform() < 0.1 else 1.0 / n + rng.uniform() * (1 - 1.0 / n)
    target = None
    if rng.uniform() < 0.7:
        r_min, r_max, _, _ = so.feasible_return_range(mu, cap)
        u = rng.choice([0.0, 1.0, rng.uniform()], p=[0.05, 0.05, 0.9])
        target = float(r_min + u * (r_max - r_min))
    return sigma, mu, cap, target


def compare(res, wc, sigma):
    own_obj = float(res.weights @ sigma @ res.weights)
    cvx_obj = float(wc @ sigma @ wc)
    return float(np.max(np.abs(res.weights - wc))), abs(own_obj - cvx_obj) / max(abs(cvx_obj), 1e-300)


def stats(w, mu, cov, rf):
    r, v = float(mu @ w), float(np.sqrt(w @ cov @ w))
    return {"ret": r, "vol": v, "sharpe": (r - rf) / v}


def kkt_rows(label, sigma, mu, cap, res, tickers, opts):
    kk = so.kkt_residuals(sigma, mu, cap, res)
    ok = all(v <= opts["kkt_residual_tol"] for v in kk.values())
    rows = [{"kind": "summary", "solution": label, **kk, "status": res.status, "iterations": res.iterations,
             "active_lower": len(res.active_lower), "active_upper": len(res.active_upper),
             "free": len(res.weights) - len(res.active_lower) - len(res.active_upper), "kkt_ok": ok,
             "multipliers_unique": res.multipliers_unique, "value": float(res.weights @ sigma @ res.weights)}]
    rows.append({"kind": "constraint", "solution": label, "constraint": "budget", "value": res.nu_budget, "interpretation": INTERPRETATION["budget"]})
    if res.nu_return is not None:
        rows.append({"kind": "constraint", "solution": label, "constraint": "return_target", "value": res.nu_return,
                     "interpretation": INTERPRETATION["return"]})
    for i in res.active_upper:
        rows.append({"kind": "constraint", "solution": label, "constraint": "upper_bound (cap)", "asset": tickers[i],
                     "value": float(res.lam_upper[i]), "slack": float(cap - res.weights[i]), "interpretation": INTERPRETATION["upper"]})
    for i in res.active_lower:
        rows.append({"kind": "constraint", "solution": label, "constraint": "lower_bound (zero)", "asset": tickers[i],
                     "value": float(res.lam_lower[i]), "slack": float(res.weights[i]), "interpretation": INTERPRETATION["lower"]})
    return rows, ok, kk


def fd_sensitivities(label, sigma, mu, cap, res, rf, opts, is_max_sharpe=False):
    """Finite-difference sensitivities of the optimal variance against the multipliers' predictions."""
    d = opts["fd_relative_step"]
    rows, failures = [], []
    r_min, r_max, _, _ = so.feasible_return_range(mu, cap)
    base_sets = (res.active_lower, res.active_upper)

    def check(name, solve, step, predicted):
        lo, hi = solve(-step), solve(+step)
        if not (lo.ok and hi.ok):
            rows.append({"kind": "sensitivity", "solution": label, "constraint": name, "note": "perturbed solve failed"})
            failures.append(f"{label}: perturbed solve failed for {name}")
            return
        same = (lo.active_lower, lo.active_upper) == base_sets == (hi.active_lower, hi.active_upper)
        fd = (hi.objective - lo.objective) / (2 * step)
        rel = abs(fd - predicted) / max(abs(predicted), abs(fd), opts["fd_abs_floor"])
        ok = (not same) or abs(fd - predicted) <= opts["fd_rel_tol"] * max(abs(predicted), abs(fd)) + opts["fd_abs_floor"]
        rows.append({"kind": "sensitivity", "solution": label, "constraint": name, "value": predicted, "fd_value": fd, "rel_error": rel,
                     "note": "agree" if (same and ok) else ("skipped: active set changed under the perturbation" if not same else "DISAGREE")})
        if not ok:
            failures.append(f"{label}: finite difference for {name} ({fd:.6g}) disagrees with the multiplier prediction ({predicted:.6g})")

    check("d(variance)/d(cap) = -sum(lam_upper)", lambda s: so.solve_qp(sigma, mu, cap + s, opts, target=res.target), d * cap, -float(res.lam_upper.sum()))
    check("d(variance)/d(budget) = nu_b", lambda s: so.solve_qp(sigma, mu, cap, opts, target=res.target, budget=1.0 + s), d, res.nu_budget)
    if res.target is not None:
        step = d * (r_max - r_min)
        check("d(variance)/d(target return) = nu_r", lambda s: so.solve_qp(sigma, mu, cap, opts, target=res.target + s), step, res.nu_return)
    if is_max_sharpe:
        pred = 2.0 * res.objective / (res.target - rf)
        rel = abs(res.nu_return - pred) / abs(pred)
        rows.append({"kind": "sensitivity", "solution": label, "constraint": "tangency: nu_r = 2 V / (r - rf)", "value": res.nu_return,
                     "fd_value": pred, "rel_error": rel, "note": "agree" if rel <= opts["tangency_rel_tol"] else "DISAGREE"})
        if rel > opts["tangency_rel_tol"]:
            failures.append(f"{label}: tangency identity violated (relative error {rel:.2e})")
    return rows, failures


def run_verifications(cfg, mu, cov, tickers):
    opts, rf, cap = cfg["solver_own"], cfg["risk_free_rate"], cfg["portfolio"]["weight_cap"]
    tol_cvx = opts["cvxpy_solver_tol"]
    n = len(mu)
    lines, failures, kkt_rows_all, vs_rows = [], [], [], []

    # ---- real data: min-variance and max-Sharpe against src/markowitz.py and the logged numbers -------------------
    mv = so.min_variance(cov, cap, opts)
    ms = so.max_sharpe(cov, mu, cap, rf, opts)
    if not (mv.ok and ms.status == so.OPTIMAL):
        failures.append("own solver failed on the real data")
        return {"kkt": pd.DataFrame(), "vs": pd.DataFrame(), "frontier": pd.DataFrame(), "lines": lines, "failures": failures, "fig": None}
    w_mv_ref, w_ms_ref = markowitz.min_variance(cov, cap), markowitz.max_sharpe(mu, cov, rf, cap)
    lines.append(f"- Real data: {n} assets, cap {cap}, rf {rf} (assumed), annualised x{cfg['trading_days']}. Own solver: min-variance {mv.iterations} iterations "
                 f"({len(mv.active_lower)} at zero, {len(mv.active_upper)} at the cap); max-Sharpe by golden-section search with {ms.evaluations} QP solves, "
                 f"bracket width {ms.bracket_width:.1e}.")
    wc_mv = cvx_solve(cov, mu, cap, None, tol_cvx)
    dw_t, do_t = compare(mv, wc_mv, cov)
    lines.append(f"- min_variance against cvxpy at tolerance {tol_cvx:g}: max |weight difference| {dw_t:.2e}, relative objective difference {do_t:.1e}. "
                 f"Variance of the own solution minus variance of the src/markowitz.py solution (cvxpy default tolerance): {float(mv.objective - w_mv_ref @ cov @ w_mv_ref):+.2e} "
                 "(not positive: the own solution is the better of the two).")
    for name, w_own, w_ref, ref_key in [("min_variance", mv.weights, w_mv_ref, "min_variance"), ("max_sharpe", ms.weights, w_ms_ref, "max_sharpe")]:
        so_s, ref_s = stats(w_own, mu, cov, rf), stats(w_ref, mu, cov, rf)
        dw = float(np.max(np.abs(w_own - w_ref)))
        lines.append(f"- {name}: own return {so_s['ret']:.6f}, vol {so_s['vol']:.6f}, Sharpe {so_s['sharpe']:.6f}; src/markowitz.py return {ref_s['ret']:.6f}, "
                     f"vol {ref_s['vol']:.6f}, Sharpe {ref_s['sharpe']:.6f}; max |weight difference| {dw:.2e}")
        ref = opts["regression_reference"][ref_key]
        ok_w = dw <= opts["regression_weight_tol"]
        ok_ref = (abs(so_s["ret"] - ref["ret"]) <= opts["regression_tol_return_vol"] and abs(so_s["vol"] - ref["vol"]) <= opts["regression_tol_return_vol"]
                  and abs(so_s["sharpe"] - ref["sharpe"]) <= opts["regression_tol_sharpe"])
        lines.append(f"    previously logged: return {ref['ret']}, vol {ref['vol']}, Sharpe {ref['sharpe']} -> own agrees: {ok_ref}; weights within {opts['regression_weight_tol']:g} of src/markowitz.py: {ok_w}")
        vs_rows.append({"kind": "regression_markowitz", "id": name, "n": n, "cap": cap, "status": "optimal", "max_abs_weight_diff": dw,
                        "obj_rel_diff": abs(so_s["vol"] ** 2 - ref_s["vol"] ** 2) / ref_s["vol"] ** 2, "note": f"matches logged numbers: {ok_ref}"})
        if not ok_w:
            failures.append(f"{name}: weights differ from src/markowitz.py by {dw:.2e}")
        if not ok_ref:
            failures.append(f"{name}: own numbers differ from the previously logged in-sample numbers")

    # ---- unimodality of the Sharpe ratio along the frontier, golden section versus fine grid ------------------------
    grid_r, grid_s = so.sharpe_profile(cov, mu, cap, rf, opts)
    vv = so.count_valleys(grid_s, opts["unimodality_noise_tol"])
    k = int(np.argmax(grid_s))
    spacing = grid_r[1] - grid_r[0]
    same_peak = abs(ms.target_return - grid_r[k]) <= spacing and ms.sharpe >= grid_s[k] - 1e-12
    lines.append(f"- Sharpe unimodality (assumption behind golden-section search), checked on a {len(grid_r)}-point grid of target returns from "
                 f"{grid_r[0]:.4f} to {grid_r[-1]:.4f}: {vv['n_peaks']} interior peak(s), {vv['n_valleys']} valley(s) -> unimodal: {vv['n_valleys'] == 0}. "
                 f"rf {rf} {'is above' if ms.rf_above_min_variance_return else 'is below'} the min-variance return {ms.r_min_variance:.4f}"
                 + ("; THE ASSUMPTION MAY FAIL in this case" if ms.rf_above_min_variance_return else "") + ".")
    lines.append(f"    golden-section maximum: return {ms.target_return:.8f}, Sharpe {ms.sharpe:.10f}; grid maximum: return {grid_r[k]:.8f}, Sharpe {grid_s[k]:.10f} "
                 f"(grid spacing {spacing:.1e}); golden >= grid maximum and within one grid step: {same_peak}; maximum at a boundary of the search interval: {ms.at_boundary}")
    if vv["n_valleys"] != 0:
        failures.append("Sharpe ratio is not unimodal along the frontier on the grid")
    if not same_peak:
        failures.append("golden-section maximum disagrees with the grid maximum")

    # ---- frontier against cvxpy -------------------------------------------------------------------------------------
    r_grid = np.linspace(ms.r_min_variance, ms.r_max, opts["frontier_points"])
    fr_rows, own_pts, cvx_pts, fr_fail = [], [], [], 0
    for r in r_grid:
        res = so.solve_qp(cov, mu, cap, opts, target=float(r))
        wc = cvx_solve(cov, mu, cap, float(r), tol_cvx)
        dw, do = compare(res, wc, cov)
        vol_own, vol_cvx = float(np.sqrt(res.weights @ cov @ res.weights)), float(np.sqrt(wc @ cov @ wc))
        fr_rows.append({"target_return": float(r), "return_own": float(mu @ res.weights), "vol_own": vol_own, "vol_cvxpy": vol_cvx,
                        "max_abs_weight_diff": dw, "obj_rel_diff": do, "iterations": res.iterations, "status": res.status,
                        "active_lower": len(res.active_lower), "active_upper": len(res.active_upper)})
        own_pts.append((vol_own, float(mu @ res.weights)))
        cvx_pts.append((vol_cvx, float(mu @ wc)))
        if dw > opts["agree_weight_tol"] or do > opts["agree_objective_rel_tol"] or not res.ok:
            fr_fail += 1
    fr = pd.DataFrame(fr_rows)
    lines.append(f"- Frontier ({len(fr)} points, targets {r_grid[0]:.4f} to {r_grid[-1]:.4f}) against cvxpy: max |weight difference| {fr['max_abs_weight_diff'].max():.2e}, "
                 f"max relative objective difference {fr['obj_rel_diff'].max():.2e}, own iterations {fr['iterations'].min()} to {fr['iterations'].max()}; "
                 f"points beyond tolerance ({opts['agree_weight_tol']:g} weights, {opts['agree_objective_rel_tol']:g} objective): {fr_fail}")
    if fr_fail:
        failures.append(f"{fr_fail} frontier points disagree with cvxpy beyond tolerance")
    for name, w_own, qp in [("min_variance", mv.weights, mv), ("max_sharpe", ms.weights, ms.qp)]:
        vs_rows.append({"kind": "real", "id": name, "n": n, "cap": cap,
                        "target": qp.target, "status": qp.status, "iterations": qp.iterations,
                        "max_abs_weight_diff": float(np.max(np.abs(w_own - (w_mv_ref if name == "min_variance" else w_ms_ref)))), "note": "vs src/markowitz.py (cvxpy)"})
    for _, row in fr.iterrows():
        vs_rows.append({"kind": "real_frontier", "id": f"target {row['target_return']:.5f}", "n": n, "cap": cap, "target": row["target_return"],
                        "status": row["status"], "iterations": row["iterations"], "max_abs_weight_diff": row["max_abs_weight_diff"], "obj_rel_diff": row["obj_rel_diff"]})

    # ---- KKT report and multiplier sensitivities -----------------------------------------------------------------------
    sols = [("min_variance", mv), ("max_sharpe", ms.qp)]
    for f in opts["kkt_target_fractions"]:
        r = ms.r_min_variance + f * (ms.r_max - ms.r_min_variance)
        sols.append((f"target_{int(round(100 * f))}pct", so.solve_qp(cov, mu, cap, opts, target=float(r))))
    sens_rows = []
    lines.append(f"- KKT report (tolerance {opts['kkt_residual_tol']:g}); multipliers use the convention in the src/solver_own.py docstring (variance V = w' Sigma w):")
    for label, res in sols:
        rows, ok, kk = kkt_rows(label, cov, mu, cap, res, tickers, opts)
        kkt_rows_all += rows
        lines.append(f"    {label}: stationarity {kk['stationarity']:.1e}, primal feasibility {kk['primal_feasibility']:.1e}, dual feasibility {kk['dual_feasibility']:.1e}, "
                     f"complementary slackness {kk['complementary_slackness']:.1e}; {len(res.active_upper)} at the cap, {len(res.active_lower)} at zero, "
                     f"{len(res.weights) - len(res.active_upper) - len(res.active_lower)} free; nu_budget {res.nu_budget:.6g}"
                     + (f", nu_return {res.nu_return:.6g}" if res.nu_return is not None else "")
                     + (f"; cap multipliers {float(res.lam_upper[list(res.active_upper)].min()):.3g} to {float(res.lam_upper[list(res.active_upper)].max()):.3g}" if res.active_upper else ""))
        if not ok:
            failures.append(f"KKT residuals above tolerance at {label}")
        srows, sfail = fd_sensitivities(label, cov, mu, cap, res, rf, opts, is_max_sharpe=(label == "max_sharpe"))
        sens_rows += srows
        failures += sfail
    kkt_rows_all += sens_rows
    n_agree = sum(1 for r in sens_rows if r.get("note") == "agree")
    n_skip = sum(1 for r in sens_rows if str(r.get("note", "")).startswith("skipped"))
    lines.append(f"- Multiplier sensitivities (cap, budget, target return; step {opts['fd_relative_step']:g} relative, central differences; tangency identity at max-Sharpe): "
                 f"{n_agree} agree within {opts['fd_rel_tol']:g}, {n_skip} skipped because the active set changed, {len(sens_rows) - n_agree - n_skip} disagree. "
                 f"Largest relative error among agreeing checks {max([r['rel_error'] for r in sens_rows if r.get('note') == 'agree'] or [float('nan')]):.1e}.")

    # ---- random PSD problems against cvxpy -------------------------------------------------------------------------------
    rng = np.random.default_rng(opts["seed"])
    rand_rows = []
    infeas_expected = infeas_ok = 0
    for i in range(opts["random_problems"]):
        sigma, mu_r, cap_r, target = random_problem(rng, opts)
        res = so.solve_qp(sigma, mu_r, cap_r, opts, target=target)
        row = {"kind": "random", "id": i, "n": len(mu_r), "cap": cap_r, "target": target, "status": res.status, "iterations": res.iterations,
               "bland_used": res.bland_used}
        if res.ok:
            wc = cvx_solve(sigma, mu_r, cap_r, target, tol_cvx)
            dw, do = compare(res, wc, sigma)
            kk = so.kkt_residuals(sigma, mu_r, cap_r, res)
            row.update({"max_abs_weight_diff": dw, "obj_rel_diff": do, "kkt_max": max(kk.values()),
                        "own_minus_cvxpy_objective": float(res.objective - wc @ sigma @ wc)})
        rand_rows.append(row)
        if i % 10 == 0:                                   # targets just outside the achievable range must be reported infeasible
            r_min, r_max, _, _ = so.feasible_return_range(mu_r, cap_r)
            for t_out in (r_max + 1e-3, r_min - 1e-3):
                infeas_expected += 1
                infeas_ok += so.solve_qp(sigma, mu_r, cap_r, opts, target=t_out).status == so.INFEASIBLE_TARGET
    rd = pd.DataFrame(rand_rows)
    vs_rows += rand_rows
    bad = rd[(rd["status"] != so.OPTIMAL) | (rd["max_abs_weight_diff"] > opts["agree_weight_tol"]) | (rd["obj_rel_diff"] > opts["agree_objective_rel_tol"])
             | (rd["kkt_max"] > opts["kkt_residual_tol"])]
    lines.append(f"- {len(rd)} random PSD problems (seed {opts['seed']}, N {opts['random_n_min']} to {opts['random_n_max']}, caps from 1/N to 1, "
                 f"{int(rd['target'].notna().sum())} with a target return across the feasible range, the rest minimum variance) against cvxpy at tolerance {tol_cvx:g}: "
                 f"max |weight difference| {rd['max_abs_weight_diff'].max():.2e}, max relative objective difference {rd['obj_rel_diff'].max():.2e}, "
                 f"own objective above the cvxpy objective in {int((rd['own_minus_cvxpy_objective'] > 1e-12).sum())} problems (max excess {rd['own_minus_cvxpy_objective'].max():+.1e}), "
                 f"below it in {int((rd['own_minus_cvxpy_objective'] < -1e-12).sum())}; max KKT residual {rd['kkt_max'].max():.1e}, max iterations {int(rd['iterations'].max())}, Bland rule used in {int(rd['bland_used'].sum())}, "
                 f"problems beyond tolerance or not optimal: {len(bad)}; targets just outside the feasible range reported infeasible: {infeas_ok} of {infeas_expected}.")
    if len(bad):
        failures.append(f"{len(bad)} random problems disagree with cvxpy or fail the KKT check")
    if infeas_ok != infeas_expected:
        failures.append("an infeasible target was not reported as infeasible")

    # ---- edge cases ---------------------------------------------------------------------------------------------------------
    edge = []

    def edge_row(name, ok, note, **kw):
        edge.append({"kind": "edge", "id": name, "status": "ok" if ok else "FAILED", "note": note, **kw})
        if not ok:
            failures.append(f"edge case failed: {name}: {note}")

    rng_e = np.random.default_rng(opts["seed"] + 1)
    s12 = rng_e.normal(size=(12, 6))
    sig12, mu12 = s12 @ s12.T / 6 + 1e-2 * np.eye(12), rng_e.uniform(0.02, 0.2, 12)
    res = so.solve_qp(sig12, mu12, 1 / 12, opts)
    r_eq = float(mu12.mean())
    res_t = so.solve_qp(sig12, mu12, 1 / 12, opts, target=r_eq)
    res_bad = so.solve_qp(sig12, mu12, 1 / 12, opts, target=r_eq + 1e-3)
    ok = res.ok and np.allclose(res.weights, 1 / 12, atol=1e-12) and res_t.ok and np.allclose(res_t.weights, 1 / 12, atol=1e-12) and res_bad.status == so.INFEASIBLE_TARGET
    edge_row("cap = 1/N (single feasible point)", ok, f"weights equal 1/N to {np.abs(res.weights - 1 / 12).max():.1e}; target at the equal-weight return solved ({res_t.status}), any other target {res_bad.status}; multipliers unique: {res.multipliers_unique}")
    r_min, r_max, _, _ = so.feasible_return_range(mu, cap)
    hi = so.solve_qp(cov, mu, cap, opts, target=r_max + 1e-3)
    lo = so.solve_qp(cov, mu, cap, opts, target=r_min - 1e-3)
    capbad = so.solve_qp(cov, mu, 0.9 / n, opts)
    edge_row("infeasible target / cap (real data)", hi.status == lo.status == so.INFEASIBLE_TARGET and capbad.status == so.INFEASIBLE_CAP and hi.weights is None,
             f"range [{r_min:.4f}, {r_max:.4f}]: above -> {hi.status}, below -> {lo.status}; cap 0.9/N -> {capbad.status}")
    b3 = rng_e.normal(size=(20, 3))
    sig_ns, mu_ns = b3 @ b3.T / 3 + 1e-10 * np.eye(20), rng_e.uniform(0.02, 0.2, 20)
    cap_ns = 0.2
    r_ns = float(np.mean(so.feasible_return_range(mu_ns, cap_ns)[:2]))
    res = so.solve_qp(sig_ns, mu_ns, cap_ns, opts, target=r_ns)
    wc = cvx_solve(sig_ns, mu_ns, cap_ns, r_ns, tol_cvx)
    dw, do = compare(res, wc, sig_ns)
    kk = so.kkt_residuals(sig_ns, mu_ns, cap_ns, res)
    own_o, cvx_o = float(res.weights @ sig_ns @ res.weights), float(wc @ sig_ns @ wc)
    scale = float(np.mean(np.diag(sig_ns)))
    abs_ok = abs(own_o - cvx_o) <= opts["near_singular_scaled_abs_tol"] * scale and own_o <= cvx_o + opts["near_singular_scaled_abs_tol"] * scale
    edge_row("near-singular Sigma (rank 3 plus 1e-10 ridge, N = 20)", res.ok and abs_ok and max(kk.values()) <= opts["kkt_residual_tol"],
             f"status {res.status} in {res.iterations} iterations; optimal variance own {own_o:.4e} versus cvxpy {cvx_o:.4e} (absolute difference {abs(own_o - cvx_o):.1e}, "
             f"bound {opts['near_singular_scaled_abs_tol'] * scale:.1e}; the relative difference {do:.1e} is not meaningful at this scale, below the reference solver's resolution); "
             f"own objective not above cvxpy's: {own_o <= cvx_o + opts['near_singular_scaled_abs_tol'] * scale}; max |weight difference| {dw:.1e} (the problem is nearly flat); "
             f"max KKT residual {max(kk.values()):.1e}",
             max_abs_weight_diff=dw, obj_rel_diff=do, kkt_max=max(kk.values()), iterations=res.iterations)
    r_mid = ms.r_min_variance + 0.5 * (ms.r_max - ms.r_min_variance)
    base = so.solve_qp(cov, mu, cap, opts, target=float(r_mid))
    free_idx = [i for i in range(n) if 1e-6 < base.weights[i] < cap - 1e-6]
    j = max(free_idx, key=lambda i: base.weights[i]) if free_idx else int(np.argmax(base.weights))
    cov_d = np.pad(cov, ((0, 1), (0, 1)))
    cov_d[-1, :-1], cov_d[:-1, -1], cov_d[-1, -1] = cov[j], cov[j], cov[j, j]
    mu_d = np.append(mu, mu[j])
    cap_d, r_d = cap, float(r_mid)
    res = so.solve_qp(cov_d, mu_d, cap_d, opts, target=r_d)
    wc = cvx_solve(cov_d, mu_d, cap_d, r_d, tol_cvx)
    dw, do = compare(res, wc, cov_d)
    kk = so.kkt_residuals(cov_d, mu_d, cap_d, res)
    edge_row(f"asset duplicated: {tickers[j]} (identical mu, variance and covariances; Sigma exactly singular, N = 41)",
             res.ok and do <= opts["edge_objective_rel_tol"] and max(kk.values()) <= opts["kkt_residual_tol"]
             and abs((res.weights[j] + res.weights[-1]) - (wc[j] + wc[-1])) <= opts["agree_weight_tol"],
             f"status {res.status}, relative objective difference to cvxpy {do:.1e}; combined weight of the pair: own {res.weights[j] + res.weights[-1]:.6f}, cvxpy {wc[j] + wc[-1]:.6f} "
             f"(split between the two: own {res.weights[j]:.4f}/{res.weights[-1]:.4f}, cvxpy {wc[j]:.4f}/{wc[-1]:.4f}; the split between identical assets is not unique); "
             f"max KKT residual {max(kk.values()):.1e}; multipliers unique: {res.multipliers_unique}",
             obj_rel_diff=do, kkt_max=max(kk.values()), iterations=res.iterations)
    sig2 = np.array([[0.04, 0.006], [0.006, 0.09]])
    mu2 = np.array([0.08, 0.15])
    bad2 = 0.0
    for cap2, t2 in [(1.0, None), (1.0, 0.11), (0.7, 0.12), (0.7, None)]:
        r2 = so.solve_qp(sig2, mu2, cap2, opts, target=t2)
        wc2 = cvx_solve(sig2, mu2, cap2, t2, tol_cvx)
        bad2 = max(bad2, compare(r2, wc2, sig2)[0] if r2.ok else 1.0)
    edge_row("N = 2", bad2 <= opts["agree_weight_tol"], f"four cases (caps 1 and 0.7, with and without a target) agree with cvxpy to {bad2:.1e}")
    vs_rows += edge
    for e in edge:
        lines.append(f"- edge case {e['id']}: {e['status']}; {e['note']}")

    # ---- tables and figure data ---------------------------------------------------------------------------------------------
    cvx_mv = (float(np.sqrt(w_mv_ref @ cov @ w_mv_ref)), float(mu @ w_mv_ref))
    cvx_ms = (float(np.sqrt(w_ms_ref @ cov @ w_ms_ref)), float(mu @ w_ms_ref))
    fig = {"own": own_pts, "cvx": cvx_pts, "own_mv": (stats(mv.weights, mu, cov, rf)["vol"], stats(mv.weights, mu, cov, rf)["ret"]),
           "own_ms": (stats(ms.weights, mu, cov, rf)["vol"], stats(ms.weights, mu, cov, rf)["ret"]), "cvx_mv": cvx_mv, "cvx_ms": cvx_ms}
    return {"kkt": pd.DataFrame(kkt_rows_all), "vs": pd.DataFrame(vs_rows), "frontier": fr, "lines": lines, "failures": failures, "fig": fig}


def main(args, cfg=None):
    cfg = load_config() if cfg is None else cfg
    set_seed(cfg["solver_own"]["seed"])
    suffix = cfg["solver_own"]["output_suffix"]
    rets = pd.read_parquet(Path(cfg["data"]["processed_dir"]) / "returns_simple.parquet")
    td = cfg["trading_days"]
    mu = rets.mean().to_numpy() * td
    cov = rets.cov().to_numpy() * td
    res = run_verifications(cfg, mu, cov, list(rets.columns))
    tables, figs = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    res["kkt"].to_csv(tables / f"kkt_report{suffix}.csv", index=False)
    res["vs"].to_csv(tables / f"solver_vs_cvxpy{suffix}.csv", index=False)
    res["frontier"].to_csv(tables / f"frontier{suffix}.csv", index=False)
    fg = res["fig"]
    if fg is not None:
        fig, ax = plt.subplots(figsize=(8.5, 6))
        cv = np.array(fg["cvx"])
        ow = np.array(fg["own"])
        ax.plot(cv[:, 0], cv[:, 1], color="tab:blue", lw=5, alpha=0.35, label="cvxpy frontier (equality target)")
        ax.plot(ow[:, 0], ow[:, 1], "o", color="black", ms=3, label="own active-set solver")
        ax.scatter(*fg["cvx_mv"], s=160, facecolors="none", edgecolors="tab:green", linewidths=2, label="min-variance (cvxpy)")
        ax.scatter(*fg["own_mv"], marker="*", s=130, color="tab:green", label="min-variance (own)")
        ax.scatter(*fg["cvx_ms"], s=160, facecolors="none", edgecolors="tab:red", linewidths=2, label="max-Sharpe (cvxpy)")
        ax.scatter(*fg["own_ms"], marker="D", s=60, color="tab:red", label="max-Sharpe (own, golden section)")
        ax.set_xlabel("Annualised volatility")
        ax.set_ylabel("Annualised return")
        ax.set_title("Capped long-only frontier: own solver versus cvxpy (in-sample, 40 stocks)")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(figs / f"frontier_own_vs_cvxpy.png", dpi=150)
        plt.close(fig)
    lines, failures = res["lines"], res["failures"]
    if failures:
        lines.append("- VERIFICATION FAILED: " + "; ".join(failures))
    else:
        lines.append("- All verification checks passed.")
    print("\n".join(lines))
    if args.log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Own active-set QP solver, golden-section max-Sharpe and KKT report (scripts/run_solver_own.py)\n"
                    + "\n".join(lines) + "\n")
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    sys.exit(main(ap.parse_args()))
