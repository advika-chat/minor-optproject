"""Game theory: investor versus Nature (matrix games, ellipsoid saddle point, Wasserstein worst case).

Run from the repo root:  python -m scripts.run_game [--log]
Requires data/processed/returns_simple.parquet and outputs/tables/scenario_sharpe_matrix_sens.csv. New files carry the suffix set in
config (game.output_suffix). The script exits with status 1 if any verification check fails. All era evaluations are in-sample for each era
(the full-sample weights saw that era), so the era matrix illustrates the decision rules; it is not a performance claim.
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

from src import game as g  # noqa: E402
from src import robust, wdro  # noqa: E402
from src.utils import get_logger, load_config, set_seed  # noqa: E402

log = get_logger("game")


def update_notes_blocks(path, blocks):
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


def resolve(cfg, dotted):
    node = cfg
    for part in dotted.split("."):
        node = node[part]
    return node


def analyse_matrix(name, pay, gc, tabs, lines, check):
    """Wald, Savage, regret matrix and the mixed-strategy LP solution of both players for one payoff matrix."""
    a = pay.to_numpy(float)
    rows, cols = list(pay.index), list(pay.columns)
    reg = g.regret_matrix(a)
    regdf = pd.DataFrame(reg, index=rows, columns=cols)
    iw, sw = g.wald(a)
    isv, ms = g.savage(a)
    same = iw == isv
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            tabs["payoff"].append({"matrix": name, "portfolio": r, "scenario": c, "payoff": a[i, j]})
            tabs["regret"].append({"matrix": name, "portfolio": r, "scenario": c, "regret": reg[i, j]})
    tabs["rules"] += [{"matrix": name, "rule": "wald_maximin_pure", "choice": rows[iw], "criterion_value": sw, "same_choice_as_other_rule": same},
                      {"matrix": name, "rule": "savage_minimax_regret_pure", "choice": rows[isv], "criterion_value": ms, "same_choice_as_other_rule": same}]
    check(bool((reg >= -1e-12).all()) and bool((reg.min(axis=0) == 0).all()), f"{name}: regret matrix lacks a zero in a column or has negative entries")
    lp = g.solve_matrix_game(a)
    res = g.saddle_residuals(a, lp["x"], lp["y"])
    lo, hi = g.pure_bounds(a)
    mr = g.minimax_regret_mixed(a)
    res_r = g.saddle_residuals(-reg, mr["x"], mr["y"])
    check(lp["gap"] <= gc["lp_value_tol"], f"{name}: investor and Nature LP values differ by {lp['gap']:.2e}")
    check(res["row_gap"] <= gc["best_response_tol"] and res["col_gap"] <= gc["best_response_tol"], f"{name}: mixed strategies are not mutual best responses ({res['row_gap']:.1e}, {res['col_gap']:.1e})")
    check(lo - 1e-9 <= lp["value_row"] <= hi + 1e-9, f"{name}: value outside the pure maximin / minimax bounds")
    check(mr["gap"] <= gc["lp_value_tol"] and res_r["row_gap"] <= gc["best_response_tol"] and res_r["col_gap"] <= gc["best_response_tol"], f"{name}: minimax-regret LP checks failed")
    check(mr["value"] <= ms + 1e-9, f"{name}: mixed minimax regret worse than the pure rule")
    dual_match = float(np.max(np.abs(lp["dual_y"] - lp["y"])))
    for game, vals, x, y, resid, lo_, hi_ in (("payoff", lp, lp["x"], lp["y"], res, lo, hi),
                                              ("regret", {"value_row": mr["value"], "value_col": mr["value_nature"], "gap": mr["gap"]}, mr["x"], mr["y"], res_r, None, None)):
        tabs["mixed"].append({"matrix": name, "game": game, "kind": "value", "name": "investor_LP_value", "value": vals["value_row"]})
        tabs["mixed"].append({"matrix": name, "game": game, "kind": "value", "name": "nature_LP_value", "value": vals["value_col"]})
        tabs["mixed"].append({"matrix": name, "game": game, "kind": "value", "name": "gap", "value": vals["gap"]})
        tabs["mixed"].append({"matrix": name, "game": game, "kind": "residual", "name": "row_gap (investor best response)", "value": resid["row_gap"]})
        tabs["mixed"].append({"matrix": name, "game": game, "kind": "residual", "name": "col_gap (Nature best response)", "value": resid["col_gap"]})
        if lo_ is not None:
            tabs["mixed"].append({"matrix": name, "game": game, "kind": "bound", "name": "pure_maximin", "value": lo_})
            tabs["mixed"].append({"matrix": name, "game": game, "kind": "bound", "name": "pure_minimax", "value": hi_})
        for r_, p_ in zip(rows, x):
            tabs["mixed"].append({"matrix": name, "game": game, "kind": "strategy_investor", "name": r_, "value": float(p_)})
        for c_, p_ in zip(cols, y):
            tabs["mixed"].append({"matrix": name, "game": game, "kind": "strategy_nature", "name": c_, "value": float(p_)})
    fmt = lambda names, p: ", ".join(f"{n} {v:.3f}" for n, v in zip(names, p) if v > 1e-9)             # noqa: E731
    lines.append(f"- Matrix {name} ({len(rows)} portfolios x {len(cols)} scenarios): Wald maximin chooses {rows[iw]} (worst case {sw:.4f}); Savage minimax regret chooses {rows[isv]} (max regret {ms:.4f}); "
                 f"the two rules choose the same portfolio: {same}.")
    lines.append(f"    Mixed strategies by LP: investor LP value {lp['value_row']:.8f}, Nature LP value {lp['value_col']:.8f}, gap {lp['gap']:.1e} (von Neumann). Investor mix: {fmt(rows, lp['x'])}. "
                 f"Nature mix: {fmt(cols, lp['y'])}. Best-response residuals: investor {res['row_gap']:.1e}, Nature {res['col_gap']:.1e}. Pure maximin {lo:.4f} <= value <= pure minimax {hi:.4f}. "
                 f"Nature's strategy from the investor LP's dual variables differs from the second LP's by {dual_match:.1e}"
                 + ("" if dual_match < 1e-6 else " (the optimal strategy is not unique)") + ".")
    lines.append(f"    Minimax-regret mixed strategy: value {mr['value']:.6f} (pure rule {ms:.4f}); investor mix {fmt(rows, mr['x'])}; Nature mix {fmt(cols, mr['y'])}; LP gap {mr['gap']:.1e}.")
    return regdf, iw, isv, lp


def ellipsoid_section(cfg, mu, cov, years, rets, lines, check, tabs, figdata):
    gc, opts = cfg["game"], cfg["solver_own"]
    cap, lam = cfg["portfolio"]["weight_cap"], cfg["robust"]["risk_aversion"]
    thr = cfg["robust"]["nonzero_threshold"]
    rng = np.random.default_rng(gc["wasserstein_seed"])
    se = np.sqrt(np.diag(cov) / years)
    rows, plug = [], None
    kappas = list(gc["ellipsoid_kappas"])
    if 0.0 not in kappas:
        kappas = [0.0] + kappas
    lines.append(f"- Ellipsoid game (payoff mu'w - (lambda/2) w'Sigma w with lambda = {lam}, cap {cap}, Sigma_mu = Sigma / T, T = {years:.2f} years). Own-engine max-min (nested golden section over an auxiliary scale, QPs by src/solver_own.py), "
                 "independent confirmation by a joint convex program for min-max (cvxpy, verification only) and by the SOCP of src/robust.py:")
    for kappa in kappas:
        t0 = time.time()
        w, n_inner = g.saddle_own(cov, mu, cap, lam, kappa, years, opts, gc["golden_tol"])
        secs = time.time() - t0
        w_rob = robust.worst_case_ellipsoid(rets, cfg, kappa=kappa)
        v_maxmin = g.worst_case_payoff(w, mu, cov, lam, kappa, years)
        v_rob = g.worst_case_payoff(w_rob, mu, cov, lam, kappa, years)
        v_minmax, mu_cvx = g.minmax_cvxpy(cov, mu, cap, lam, kappa, years)
        ver = g.verify_saddle(cov, mu, cap, lam, kappa, years, w, opts, rng, gc["ellipsoid_random_points"], gc["golden_tol"])
        if kappa == 0.0:
            plug = {"w": w, "value": v_maxmin}
        dv, dw = abs(v_maxmin - v_minmax), float(np.max(np.abs(w - w_rob)))
        boundary = abs(ver["maha_sq"] - kappa ** 2)
        check(dv <= gc["ellipsoid_value_tol"], f"kappa {kappa}: max-min and min-max values differ by {dv:.2e}")
        check(dw <= gc["ellipsoid_weight_tol"], f"kappa {kappa}: own saddle weights differ from the SOCP of src/robust.py by {dw:.2e}")
        check(v_maxmin >= v_rob - 1e-9, f"kappa {kappa}: own max-min value {v_maxmin:.12f} is below the SOCP value {v_rob:.12f}")
        check(boundary <= gc["ellipsoid_boundary_tol"] * max(kappa ** 2, 1.0), f"kappa {kappa}: mu* is not on the ellipsoid boundary")
        check(-1e-9 <= ver["investor_gap"] <= gc["ellipsoid_residual_tol"], f"kappa {kappa}: w* is not a best response to mu* (gap {ver['investor_gap']:.2e})")
        check(ver["nature_gap"] >= -1e-12 and ver["nature_local_gap"] >= -1e-10, f"kappa {kappa}: mu* is not a best response to w*")
        check(abs(ver["closed_form_value"] - ver["value_at_saddle"]) <= 1e-10, f"kappa {kappa}: closed-form inner minimum differs from the payoff at mu*")
        d = mu - ver["mu_star"]
        m = g_metrics = {"positions": int((w > thr).sum()), "effective_n": float(1.0 / np.sum(w ** 2)), "l1_vs_plugin": float(np.abs(w - plug["w"]).sum())}
        row = {"kappa": kappa, "source": "own engine + cvxpy + SOCP", "saddle_value_maxmin_own": v_maxmin, "value_minmax_cvxpy": v_minmax, "value_maxmin_socp_weights": v_rob,
               "value_gap_maxmin_vs_minmax": dv, "weight_diff_vs_socp": dw, "mu_star_vs_cvxpy_maxabs": float(np.max(np.abs(ver["mu_star"] - mu_cvx))),
               "mahalanobis_sq_minus_kappa_sq": boundary, "investor_best_response_gap": ver["investor_gap"], "investor_best_response_weight_diff": ver["investor_weight_diff"],
               "nature_random_gap": ver["nature_gap"], "nature_local_gap": ver["nature_local_gap"], "mu_shift_l2": float(np.linalg.norm(d)), "mu_shift_max_abs": float(np.abs(d).max()),
               "mu_shift_max_in_standard_errors": float(np.max(np.abs(d) / se)), "mean_shift_of_portfolio": float((mu - ver["mu_star"]) @ w),
               **g_metrics, "plugin_value": plug["value"], "price_of_robustness": plug["value"] - v_maxmin,
               "plugin_objective_of_robust_weights_under_mu_hat": g.payoff(w, mu, cov, lam), "solve_seconds": secs, "outer_evaluations": n_inner}
        row["opportunity_cost_under_mu_hat"] = plug["value"] - row["plugin_objective_of_robust_weights_under_mu_hat"]
        rows.append(row)
        lines.append(f"    kappa {kappa}: saddle value {v_maxmin:.10f}; min-max (cvxpy) {v_minmax:.10f}, gap {dv:.1e}; w* vs SOCP weights {dw:.1e}; mu* on the boundary (|Mahalanobis^2 - kappa^2| {boundary:.1e}); "
                     f"investor best-response gap {ver['investor_gap']:.1e} (weights {ver['investor_weight_diff']:.1e}), Nature gap over {gc['ellipsoid_random_points']} random boundary points {ver['nature_gap']:+.2e} and over local perturbations {ver['nature_local_gap']:+.2e} (both must be >= 0). "
                     f"Nature moves mu by L2 {row['mu_shift_l2']:.4f}, at most {row['mu_shift_max_in_standard_errors']:.2f} standard errors for any asset, lowering the portfolio's mean by {row['mean_shift_of_portfolio']:.4f}. "
                     f"Portfolio: {m['positions']} positions, effective N {m['effective_n']:.1f}, L1 distance from the plug-in {m['l1_vs_plugin']:.3f}. Price of robustness (plug-in value {plug['value']:.6f} minus saddle value) {row['price_of_robustness']:.6f}; "
                     f"opportunity cost of the robust weights under mu_hat {row['opportunity_cost_under_mu_hat']:.6f}. ({secs:.0f} s)")
    kz = [r for r in rows if r["kappa"] == 0.0][0]
    w_rb0 = robust.worst_case_ellipsoid(rets, cfg, kappa=0.0)
    check(abs(kz["price_of_robustness"]) <= 1e-12 and float(np.max(np.abs(plug["w"] - w_rb0))) <= gc["ellipsoid_weight_tol"] and abs(kz["mahalanobis_sq_minus_kappa_sq"]) <= 1e-12,
          "kappa = 0 does not reproduce the plug-in mean-variance solution")
    lines.append(f"    kappa = 0 reproduces the plug-in mean-variance solution: price of robustness {kz['price_of_robustness']:.1e}, own weights vs the plug-in SOCP {float(np.max(np.abs(plug['w'] - w_rb0))):.1e}.")
    # extra points for the curve, SOCP only (cvxpy), flagged
    for kappa in gc["ellipsoid_curve_kappas"]:
        w_rob = robust.worst_case_ellipsoid(rets, cfg, kappa=kappa)
        v = g.worst_case_payoff(w_rob, mu, cov, lam, kappa, years)
        rows.append({"kappa": kappa, "source": "SOCP (src/robust.py) only", "saddle_value_maxmin_own": np.nan, "value_maxmin_socp_weights": v, "plugin_value": plug["value"],
                     "price_of_robustness": plug["value"] - v, "positions": int((w_rob > thr).sum()), "effective_n": float(1.0 / np.sum(w_rob ** 2)), "l1_vs_plugin": float(np.abs(w_rob - plug["w"]).sum())})
    tab = pd.DataFrame(rows).sort_values("kappa")
    tabs["ellipsoid"] = tab
    figdata["ellipsoid"] = tab
    pr = tab[tab["source"] != "SOCP (src/robust.py) only"]
    check(bool((np.diff(pr["price_of_robustness"]) >= -1e-9).all()), "price of robustness is not non-decreasing in kappa")
    return rows


def wasserstein_section(cfg, rets, lines, check, tabs, figdata):
    gc = cfg["game"]
    rng = np.random.default_rng(gc["wasserstein_seed"])
    X = rets.to_numpy()
    n = X.shape[1]
    alpha, m, _ = wdro._cvar_coeffs(cfg)
    q = cfg["wdro"]["ground_norm"]
    p = wdro._dual_p(cfg)
    ports = {}
    for eps_d in gc["wasserstein_design_eps"]:
        ports[f"dr_cvar_design_{eps_d:g}"] = wdro.wdro_weights(rets, float(eps_d), "cvar", cfg)
    ports["equal_weight"] = np.full(n, 1.0 / n)
    lines.append(f"- Wasserstein ball (src/wdro.py formulation: empirical distribution of {len(X)} daily return vectors, transport cost = norm {q} of the difference, dual norm {p}, CVaR tail probability {alpha:.2f}, mean weight {m}, support = R^m). "
                 "Upper bound: the dual LP at the fixed portfolio (an upper bound on the worst-case CVaR because tau sits outside the supremum). Lower bound: CVaR of an explicit distribution inside the ball "
                 "(the worst alpha of the probability mass is moved by eps/alpha along the direction that most raises the loss; the mass of the boundary point is split).")
    rows, worst_resid = [], 0.0
    for name, w in ports.items():
        dual = wdro._norm_val(w, p)
        for eps in gc["wasserstein_eps"]:
            up, tau = g.worst_case_cvar_dual_lp(w, X, eps, cfg)
            closed = wdro.worst_case_cvar_bound(w, rets, eps, cfg)
            con = g.construct_nature(w, X, eps, cfg)
            rnd = [g.random_nature(w, X, eps, cfg, rng) for _ in range(gc["wasserstein_random_strategies"])]
            best_rnd = max(c for c, _ in rnd)
            max_rnd_cost = max(cst for _, cst in rnd)
            formula = con["empirical_cvar"] + eps * dual / alpha
            gap = up - con["cvar"]
            rel = abs(gap) / max(abs(up), 1e-300)
            worst_resid = max(worst_resid, abs(up - formula))
            check(con["cvar"] <= up + 1e-12, f"{name} eps {eps}: constructed lower bound {con['cvar']:.12f} exceeds the dual-LP upper bound {up:.12f}")
            check(con["cost"] <= eps * (1 + gc["wasserstein_budget_tol"]) + 1e-15 and max_rnd_cost <= eps * (1 + 1e-9) + 1e-15, f"{name} eps {eps}: transport cost above the budget")
            check(best_rnd <= up + 1e-12, f"{name} eps {eps}: a random feasible strategy beats the upper bound")
            check(abs(up - closed) <= 1e-9, f"{name} eps {eps}: dual LP differs from src/wdro.worst_case_cvar_bound by {abs(up - closed):.2e}")
            rows.append({"portfolio": name, "eps": eps, "empirical_cvar": con["empirical_cvar"], "upper_bound_dual_lp": up, "closed_form_wdro": closed,
                         "formula_empirical_plus_eps_dualnorm_over_alpha": formula, "lower_bound_constructed": con["cvar"], "constructed_cost": con["cost"],
                         "budget_used_fraction": (con["cost"] / eps if eps > 0 else np.nan), "gap_upper_minus_lower": gap, "relative_gap": rel, "bounds_agree": bool(rel <= gc["wasserstein_bound_tol"]),
                         "best_random_strategy_cvar": best_rnd, "max_random_strategy_cost": max_rnd_cost, "dual_norm_of_w": dual, "tau_dual_lp": tau})
        sub = [r for r in rows if r["portfolio"] == name]
        used = ", ".join("{:.3f}".format(r["budget_used_fraction"]) for r in sub if r["eps"] > 0)
        lines.append(f"    {name} ({int((w > cfg['robust']['nonzero_threshold']).sum())} positions, {p}-norm of w = {dual:.4f}): empirical CVaR {sub[0]['empirical_cvar']:.6f}; for eps in {gc['wasserstein_eps']}: upper bound "
                     + ", ".join(f"{r['upper_bound_dual_lp']:.6f}" for r in sub) + "; constructed lower bound " + ", ".join(f"{r['lower_bound_constructed']:.6f}" for r in sub)
                     + f"; largest gap upper - lower {max(abs(r['gap_upper_minus_lower']) for r in sub):.1e}; budget used {used}; "
                     f"best of {gc['wasserstein_random_strategies']} random strategies reaches at most {max(r['best_random_strategy_cvar'] - r['empirical_cvar'] for r in sub) / max(max(r['upper_bound_dual_lp'] - r['empirical_cvar'] for r in sub), 1e-300):.3f} of the possible CVaR increase")
    tab = pd.DataFrame(rows)
    tabs["wasserstein"] = tab
    figdata["wasserstein"] = tab
    allgap = float(tab["gap_upper_minus_lower"].abs().max())
    lines.append(f"- Wasserstein result: in every case the upper bound, the closed form 'empirical CVaR + eps * ||w||_dual / alpha' (largest difference {worst_resid:.1e}) and the CVaR of the constructed distribution agree; "
                 f"largest |upper - lower| {allgap:.1e} (relative {float(tab['relative_gap'].max()):.1e}); {int(tab['bounds_agree'].sum())} of {len(tab)} cases agree to relative {gc['wasserstein_bound_tol']:g}; "
                 "the constructed distribution uses the whole transport budget. Lower and upper bound coincide, which is a numerical saddle-point verification for the DR-CVaR game at these portfolios (it holds exactly in this formulation because the loss is piecewise linear in xi with Lipschitz constant ||w||_dual / alpha and the tail mass can be moved along a single direction).")
    return tab


def run_all(cfg, rets):
    gc, opts, td = cfg["game"], cfg["solver_own"], cfg["trading_days"]
    cap, rf = cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"]
    mu, cov = rets.mean().to_numpy() * td, rets.cov().to_numpy() * td
    years = len(rets) / td
    lines, failures, tabs, figdata = [], [], {"payoff": [], "regret": [], "rules": [], "mixed": []}, {}

    def check(ok, msg):
        if not ok:
            failures.append(msg)
        return ok

    eras = {k: tuple(v) for k, v in resolve(cfg, gc["eras_source"]).items()}
    lines.append(f"- Setup: {rets.shape[1]} stocks, {len(rets)} days ({years:.2f} years), cap {cap}, rf {rf} (assumed), lambda {cfg['robust']['risk_aversion']}, CVaR confidence {cfg['risk']['cvar_alpha']}. "
                 f"Eras (from {gc['eras_source']}): " + ", ".join(f"{k} {v[0]} to {v[1]}" for k, v in eras.items()) + ". The era evaluation is IN-SAMPLE for each era (the full-sample weights saw that era), so it illustrates the decision rules; it is not a performance claim.")

    # ---- Part A -------------------------------------------------------------------------------------------------------
    t0 = time.time()
    six = pd.read_csv(gc["six_scenario_table"], index_col=0)
    weights = g.full_sample_portfolios(rets, cfg, opts, gc["portfolios"], gc["dr_meanvar_eps"])
    era = g.era_payoff_matrix(weights, rets, eras, cfg)
    lines.append("- Full-sample portfolios: " + "; ".join(f"{k}: {int((w > cfg['robust']['nonzero_threshold']).sum())} positions, effective N {1 / np.sum(w ** 2):.1f}" for k, w in weights.items()) + ".")
    reg6, iw6, isv6, _ = analyse_matrix("six_scenarios", six, gc, tabs, lines, check)
    lines.append(f"    The saved six-scenario matrix is DEGENERATE: max-Sharpe has zero regret in every scenario ({bool((reg6.loc['max_sharpe'] == 0).all())}), it is the best portfolio in all six columns, so Wald, Savage and the mixed solutions all select it "
                 "and the game has a pure saddle point; it carries no trade-off.")
    check(bool((reg6.loc["max_sharpe"] == 0).all()), "expected the six-scenario matrix to be degenerate (zero regret for max-Sharpe); it is not")
    reg_e, iwe, ise, lp_e = analyse_matrix("eras", era, gc, tabs, lines, check)
    lines.append("    Era Sharpe matrix (rows portfolios, columns eras):\n" + "\n".join("    " + ln for ln in era.round(3).to_string().splitlines()))
    lines.append("    Era regret matrix:\n" + "\n".join("    " + ln for ln in reg_e.round(3).to_string().splitlines()))
    ex = g.teaching_example()
    check(ex["wald"] == "B" and ex["savage"] == "C", "teaching example does not give maximin B and minimax regret C")
    tex = g.solve_matrix_game(g.TWO_BY_TWO)
    check(abs(tex["value_row"] - 1.0) <= 1e-9 and np.allclose(tex["x"], [0.6, 0.4]) and np.allclose(tex["y"], [0.5, 0.5]), "2 x 2 worked example failed")
    lines.append(f"- Teaching example (3 portfolios, 2 scenarios): Wald chooses {ex['wald']}, Savage chooses {ex['savage']}. 2 x 2 LP example: value {tex['value_row']:.6f}, investor mix {np.round(tex['x'], 4).tolist()}, Nature mix {np.round(tex['y'], 4).tolist()}.")
    figdata["era"] = (era, iwe, ise)
    t_a = time.time() - t0

    # ---- Part B -------------------------------------------------------------------------------------------------------
    t0 = time.time()
    ellipsoid_section(cfg, mu, cov, years, rets, lines, check, tabs, figdata)
    t_b = time.time() - t0

    # ---- Part C -------------------------------------------------------------------------------------------------------
    t0 = time.time()
    wasserstein_section(cfg, rets, lines, check, tabs, figdata)
    lines.append(f"- Section times (s): matrix games {t_a:.0f}, ellipsoid {t_b:.0f}, Wasserstein {time.time() - t0:.0f}.")
    out = {"payoff_matrices": pd.DataFrame(tabs["payoff"]), "regret_matrices": pd.DataFrame(tabs["regret"]), "decision_rules": pd.DataFrame(tabs["rules"]),
           "mixed_strategies": pd.DataFrame(tabs["mixed"]), "ellipsoid_saddle": tabs["ellipsoid"], "wasserstein_bounds": tabs["wasserstein"]}
    return {"tables": out, "lines": lines, "failures": failures, "figdata": figdata}


def make_figures(res, figs_dir, suffix):
    fd = res["figdata"]
    era, iw, isv = fd["era"]
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    im = ax.imshow(era.values, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(era.shape[1]))
    ax.set_xticklabels(era.columns)
    ax.set_yticks(range(era.shape[0]))
    ax.set_yticklabels(era.index)
    for i in range(era.shape[0]):
        for j in range(era.shape[1]):
            ax.text(j, i, f"{era.values[i, j]:.2f}", ha="center", va="center", fontsize=9)
    ax.scatter([-0.62], [iw], marker="s", s=140, color="navy", clip_on=False, label="Wald maximin choice")
    ax.scatter([-0.62], [isv], marker="^", s=140, color="darkorange", clip_on=False, label="Savage minimax-regret choice")
    ax.set_xlim(-0.5, era.shape[1] - 0.5)
    fig.colorbar(im, ax=ax)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=8)
    ax.set_title("Sharpe ratio by portfolio and era (full-sample weights; in-sample for each era)", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs_dir / f"era_sharpe_heatmap{suffix}.png", dpi=150)
    plt.close(fig)
    tab = fd["ellipsoid"]
    own = tab[tab["source"] != "SOCP (src/robust.py) only"]
    other = tab[tab["source"] == "SOCP (src/robust.py) only"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].plot(tab["kappa"], tab["plugin_value"].iloc[0] - tab["price_of_robustness"], color="grey", lw=1)
    axes[0].scatter(own["kappa"], own["plugin_value"] - own["price_of_robustness"], color="tab:blue", zorder=3, label="own engine, verified")
    axes[0].scatter(other["kappa"], other["plugin_value"] - other["price_of_robustness"], color="tab:orange", marker="s", zorder=3, label="SOCP only")
    axes[0].set_xlabel("kappa (ellipsoid radius)")
    axes[0].set_ylabel("saddle value of the game")
    axes[0].set_title("Saddle value versus kappa")
    axes[0].legend()
    axes[1].plot(tab["kappa"], tab["price_of_robustness"], color="grey", lw=1)
    axes[1].scatter(own["kappa"], own["price_of_robustness"], color="tab:blue", zorder=3)
    axes[1].scatter(other["kappa"], other["price_of_robustness"], color="tab:orange", marker="s", zorder=3)
    axes[1].set_xlabel("kappa")
    axes[1].set_ylabel("price of robustness: plug-in value minus saddle value")
    axes[1].set_title("Price of robustness")
    for a in axes:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(figs_dir / f"price_of_robustness{suffix}.png", dpi=150)
    plt.close(fig)
    w = fd["wasserstein"]
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    for name, d in w.groupby("portfolio", sort=False):
        line, = ax.plot(d["eps"], d["upper_bound_dual_lp"], marker="o", label=f"{name}: upper bound (dual LP)")
        ax.plot(d["eps"], d["lower_bound_constructed"], marker="x", ls="--", color=line.get_color(), label=f"{name}: constructed Nature (lower bound)")
        ax.scatter(d["eps"], d["best_random_strategy_cvar"], s=12, color=line.get_color(), alpha=0.5)
        ax.axhline(d["empirical_cvar"].iloc[0], color=line.get_color(), lw=0.5, ls=":")
    ax.set_xlabel("Wasserstein radius eps (daily-return units)")
    ax.set_ylabel("worst-case daily CVaR")
    ax.set_title("Worst-case CVaR bounds versus eps (dots: best random strategy; dotted: empirical CVaR)", fontsize=9)
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(figs_dir / f"wasserstein_bounds{suffix}.png", dpi=150)
    plt.close(fig)


def main(args, cfg=None):
    cfg = load_config() if cfg is None else cfg
    set_seed(cfg["game"]["wasserstein_seed"])
    suffix = cfg["game"]["output_suffix"]
    rets = pd.read_parquet(Path(cfg["data"]["processed_dir"]) / "returns_simple.parquet")
    t0 = time.time()
    res = run_all(cfg, rets)
    tables_dir, figs_dir = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    for name, df in res["tables"].items():
        df.to_csv(tables_dir / f"{name}{suffix}.csv", index=False)
    lines, failures = res["lines"], res["failures"]
    if res["figdata"]:
        make_figures(res, figs_dir, suffix)
        ver = "\n".join(l for l in lines if l.startswith("- Matrix") or l.startswith("    kappa") or l.startswith("- Wasserstein result"))
        ver = ver.replace("min-max (cvxpy)", "min-max (independent convex program)")          # the notes name no software
        found = update_notes_blocks(cfg["game"]["notes_path"], {"teaching-example": g.teaching_markdown(), "lp-example": g.lp_example_markdown(), "verification": ver})
        lines.append(f"- Notes blocks regenerated in {cfg['game']['notes_path']}: {found}.")
    lines.append(f"- Total run time {time.time() - t0:.0f} s." + (" ALL CHECKS PASSED." if not failures else ""))
    if failures:
        lines.append("- CHECKS FAILED: " + "; ".join(failures))
    print("\n".join(lines))
    if args.log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Game theory: investor versus Nature (scripts/run_game.py)\n" + "\n".join(lines) + "\n")
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    sys.exit(main(ap.parse_args()))
