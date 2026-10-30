"""Lagrangian duality for the capped long-only mean-variance problem.

Primal problem P(r), with variance V = w' Sigma w:

    minimise    w' Sigma w
    subject to  1'w = 1                 (multiplier lam, free)
                mu'w = r                (multiplier gam, free; absent for minimum variance)
                -w <= 0                 (multiplier alpha >= 0)
                w - cap*1 <= 0          (multiplier beta >= 0)

Lagrangian (an inequality h(w) <= 0 gets +multiplier * h, an equality gets -multiplier * (lhs - rhs)):

    L(w; lam, gam, alpha, beta) = w'Sigma w - lam (1'w - 1) - gam (mu'w - r) - alpha'w + beta'(w - cap 1)

Stationarity:  2 Sigma w = q,   q = lam 1 + gam mu + alpha - beta.
Dual function (Sigma positive definite): minimising L over w gives w = Sigma^-1 q / 2 and

    g(lam, gam, alpha, beta) = lam + gam r - cap (1'beta) - (1/4) q' Sigma^-1 q.

These multipliers coincide with the ones in ``src.solver_own``: lam = nu_budget, gam = nu_return,
alpha = lam_lower, beta = lam_upper. For minimum variance (no target) gam is absent and q = lam 1 + alpha - beta.
"""
import numpy as np

from src import solver_own as so


def multipliers_of(res):
    """(lam, gam, alpha, beta) of a solver_own result; gam = 0 when there is no return target."""
    return float(res.nu_budget), float(res.nu_return or 0.0), np.asarray(res.lam_lower, float), np.asarray(res.lam_upper, float)


def _q(mu, target, lam, gam, alpha, beta):
    n = len(alpha)
    q = lam * np.ones(n) + alpha - beta
    if target is not None:
        q = q + gam * np.asarray(mu, float)
    return q


def lagrangian(w, sigma, mu, cap, target, lam, gam, alpha, beta):
    w = np.asarray(w, float)
    val = w @ sigma @ w - lam * (w.sum() - 1.0) - alpha @ w + beta @ (w - cap)
    if target is not None:
        val -= gam * (np.asarray(mu, float) @ w - target)
    return float(val)


def dual_function(sigma, mu, cap, target, lam, gam, alpha, beta):
    """g(lam, gam, alpha, beta) = lam + gam r - cap * sum(beta) - (1/4) q' Sigma^-1 q."""
    q = _q(mu, target, lam, gam, alpha, beta)
    val = lam - cap * float(np.sum(beta)) - 0.25 * float(q @ np.linalg.solve(sigma, q))
    if target is not None:
        val += gam * target
    return float(val)


def random_multipliers(rng, n, scale, near=None):
    """A random dual-feasible vector (alpha, beta >= 0; lam, gam free). If ``near`` = (lam, gam, alpha, beta) is
    given the vector is a small random perturbation of it (alpha, beta clipped at zero)."""
    if near is None:
        lam, gam = rng.normal(0, scale), rng.normal(0, scale)
        alpha, beta = np.abs(rng.normal(0, scale, n)), np.abs(rng.normal(0, scale, n))
    else:
        l0, g0, a0, b0 = near
        lam, gam = l0 + rng.normal(0, 0.05 * scale), g0 + rng.normal(0, 0.05 * scale)
        alpha = np.maximum(a0 + rng.normal(0, 0.05 * scale, n), 0.0)
        beta = np.maximum(b0 + rng.normal(0, 0.05 * scale, n), 0.0)
    return lam, gam, alpha, beta


def check_duality(sigma, mu, cap, res, dopts, rng):
    """Strong duality, weak duality and complementary slackness for one solved problem.

    Returns a dict: primal optimum, dual value at the solver's multipliers, their gap, the distance between the
    Lagrangian minimiser Sigma^-1 q / 2 and the primal solution, the largest complementary-slackness product,
    and the largest value of (dual - primal) over ``random_multipliers`` random dual-feasible vectors (weak
    duality requires it to be <= 0), together with how many exceeded the tolerance."""
    lam, gam, alpha, beta = multipliers_of(res)
    primal = float(res.weights @ sigma @ res.weights)
    dual = dual_function(sigma, mu, cap, res.target, lam, gam, alpha, beta)
    q = _q(mu, res.target, lam, gam, alpha, beta)
    w_l = 0.5 * np.linalg.solve(sigma, q)
    comp = max(float(np.max(np.abs(alpha * res.weights))), float(np.max(np.abs(beta * (cap - res.weights)))))
    scale = max(1.0, abs(lam), abs(gam), float(np.max(np.abs(q))))
    excess, n_violate = -np.inf, 0
    for i in range(dopts["random_multipliers"]):
        m = random_multipliers(rng, len(mu), scale, near=(lam, gam, alpha, beta) if i % 2 else None)
        e = dual_function(sigma, mu, cap, res.target, *m) - primal
        excess = max(excess, e)
        n_violate += e > dopts["weak_duality_tol"]
    return {"primal": primal, "dual_at_solver_multipliers": dual, "strong_gap": abs(primal - dual),
            "lagrangian_minimiser_distance": float(np.max(np.abs(w_l - res.weights))), "complementary_slackness": comp,
            "weak_duality_max_excess": float(excess), "weak_duality_violations": int(n_violate)}


def fmt(x):
    return f"{x:.6f}"


def worked_example(cfg):
    """Solve the 3-asset example from the config and collect every number used in docs/kkt_duality_notes.md."""
    ex = cfg["sensitivity"]["duality"]["worked_example"]
    sigma, mu, cap, r = np.array(ex["sigma"], float), np.array(ex["mu"], float), ex["cap"], ex["target"]
    res = so.solve_qp(sigma, mu, cap, cfg["solver_own"], target=r)
    lam, gam, alpha, beta = multipliers_of(res)
    q = _q(mu, r, lam, gam, alpha, beta)
    return {"sigma": sigma, "mu": mu, "cap": cap, "target": r, "res": res, "w": res.weights, "primal": float(res.weights @ sigma @ res.weights),
            "lam": lam, "gam": gam, "alpha": alpha, "beta": beta, "q": q, "two_sigma_w": 2 * sigma @ res.weights,
            "dual": dual_function(sigma, mu, cap, r, lam, gam, alpha, beta),
            "lagrangian_at_w": lagrangian(res.weights, sigma, mu, cap, r, lam, gam, alpha, beta)}


def worked_example_markdown(ex):
    """Markdown block reproduced in the notes (a test checks the notes contain exactly this text)."""
    v = lambda a: "[" + ", ".join(fmt(x) for x in a) + "]"            # noqa: E731
    rows = [
        f"- Inputs: mu = {v(ex['mu'])}, cap = {fmt(ex['cap'])}, target return r = {fmt(ex['target'])}, Sigma rows "
        + "; ".join(v(row) for row in ex["sigma"]) + ".",
        f"- Optimal weights w = {v(ex['w'])} (sum {fmt(ex['w'].sum())}, return {fmt(ex['mu'] @ ex['w'])}).",
        f"- Optimal variance V = w'Sigma w = {fmt(ex['primal'])}.",
        f"- Multipliers: lam (budget) = {fmt(ex['lam'])}, gam (return target) = {fmt(ex['gam'])}, alpha (lower bounds) = {v(ex['alpha'])}, beta (cap) = {v(ex['beta'])}.",
        f"- Stationarity: 2 Sigma w = {v(ex['two_sigma_w'])} and q = lam 1 + gam mu + alpha - beta = {v(ex['q'])}.",
        f"- Dual function at these multipliers: g = {fmt(ex['dual'])}; primal optimum {fmt(ex['primal'])}; gap {abs(ex['dual'] - ex['primal']):.1e}.",
        f"- Lagrangian at (w, multipliers) = {fmt(ex['lagrangian_at_w'])}, equal to V because every constraint term vanishes at the optimum.",
        f"- Complementary slackness: alpha * w = {v(ex['alpha'] * ex['w'])}, beta * (cap - w) = {v(ex['beta'] * (ex['cap'] - ex['w']))}.",
    ]
    return "\n".join(rows)
