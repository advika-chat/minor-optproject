"""Game theory: investor versus Nature.

Part A, finite zero-sum matrix games (rows = investor's portfolios, columns = Nature's scenarios, entries = the investor's payoff):
Wald maximin, Savage minimax regret, and the mixed-strategy solution of both players by linear programming.

Part B, a convex-concave game. The investor chooses w (capped long-only simplex), Nature chooses mu in the ellipsoid
{mu : (mu - mu_hat)' Sigma_mu^-1 (mu - mu_hat) <= kappa^2} with Sigma_mu = Sigma / T (T = years of data). Payoff
f(w, mu) = mu'w - (lam / 2) w'Sigma w, linear in mu and concave in w. The inner minimum over mu has the closed form
mu_hat'w - kappa sqrt(w'Sigma_mu w), attained at mu(w) = mu_hat - kappa Sigma_mu w / sqrt(w'Sigma_mu w).

Part C, Nature's strategy set is the Wasserstein ball of the DR-CVaR formulation in src/wdro.py (empirical distribution of T return
vectors, transport cost ||xi - xi'|| in the ground norm wdro.ground_norm, CVaR level risk.cvar_alpha, objective weight wdro.mean_weight).
"""
import numpy as np
import cvxpy as cp
from scipy import sparse
from scipy.optimize import linprog

from src import solver_own as so
from src import wdro


# ============================== Part A: matrix games ==================================================================

def regret_matrix(payoff):
    """Best payoff in each scenario (column) minus each portfolio's payoff: zero for the best, otherwise positive."""
    a = np.asarray(payoff, float)
    return a.max(axis=0, keepdims=True) - a


def wald(payoff):
    """Pure maximin: the row with the best worst-case payoff. Returns (row index, its security level)."""
    sec = np.asarray(payoff, float).min(axis=1)
    i = int(np.argmax(sec))
    return i, float(sec[i])


def savage(payoff):
    """Pure minimax regret: the row with the smallest maximum regret. Returns (row index, its maximum regret)."""
    worst = regret_matrix(payoff).max(axis=1)
    i = int(np.argmin(worst))
    return i, float(worst[i])


def solve_matrix_game(a):
    """Solve the zero-sum game with payoff matrix ``a`` (row player maximises) by two linear programs.

    Row player:    maximise v  subject to  sum_i x_i a_ij >= v for every column j,  sum x = 1,  x >= 0.
    Column player: minimise u  subject to  sum_j a_ij y_j <= u for every row i,     sum y = 1,  y >= 0.
    The second LP is the dual of the first, so the two optimal values are equal (von Neumann's minimax theorem). ``dual_y`` is the
    column player's strategy read from the dual variables (multipliers) of the first LP; it equals ``y`` when the solution is unique."""
    a = np.asarray(a, float)
    m, n = a.shape
    res_r = linprog(np.r_[np.zeros(m), -1.0], A_ub=np.hstack([-a.T, np.ones((n, 1))]), b_ub=np.zeros(n),
                    A_eq=np.r_[np.ones(m), 0.0][None, :], b_eq=[1.0], bounds=[(0, None)] * m + [(None, None)], method="highs")
    res_c = linprog(np.r_[np.zeros(n), 1.0], A_ub=np.hstack([a, -np.ones((m, 1))]), b_ub=np.zeros(m),
                    A_eq=np.r_[np.ones(n), 0.0][None, :], b_eq=[1.0], bounds=[(0, None)] * n + [(None, None)], method="highs")
    if res_r.status != 0 or res_c.status != 0:
        raise RuntimeError(f"matrix-game LP failed: {res_r.message} / {res_c.message}")
    x, y = np.clip(res_r.x[:m], 0, None), np.clip(res_c.x[:n], 0, None)
    return {"value_row": float(res_r.x[m]), "value_col": float(res_c.x[n]), "gap": abs(float(res_r.x[m]) - float(res_c.x[n])),
            "x": x / x.sum(), "y": y / y.sum(), "dual_y": -np.asarray(res_r.ineqlin.marginals, float)}


def saddle_residuals(a, x, y):
    """Best-response residuals of a pair of mixed strategies. row_gap = max_i (Ay)_i - x'Ay (zero iff x is a best response to y);
    col_gap = x'Ay - min_j (x'A)_j (zero iff y is a best response to x)."""
    a = np.asarray(a, float)
    val = float(x @ a @ y)
    return {"payoff": val, "row_gap": float((a @ y).max() - val), "col_gap": float(val - (x @ a).min()),
            "guarantee_row": float((x @ a).min()), "guarantee_col": float((a @ y).max())}


def pure_bounds(a):
    """(pure maximin, pure minimax): the value of the game lies between them."""
    a = np.asarray(a, float)
    return float(a.min(axis=1).max()), float(a.max(axis=0).min())


def minimax_regret_mixed(payoff):
    """Mixed minimax regret: the investor minimises the largest expected regret, Nature maximises. Equals the matrix game on -regret."""
    reg = regret_matrix(payoff)
    g = solve_matrix_game(-reg)
    return {"value": -g["value_row"], "value_nature": -g["value_col"], "gap": g["gap"], "x": g["x"], "y": g["y"], "regret": reg}


TEACHING_PAYOFF = np.array([[10.0, 1.0], [6.0, 5.0], [8.0, 4.0]])       # rows A, B, C; columns scenario 1, 2
TEACHING_ROWS, TEACHING_COLS = ["A", "B", "C"], ["scenario 1", "scenario 2"]
TWO_BY_TWO = np.array([[3.0, -1.0], [-2.0, 4.0]])


def teaching_example():
    p = TEACHING_PAYOFF
    iw, sw = wald(p)
    isv, ms = savage(p)
    return {"payoff": p, "regret": regret_matrix(p), "wald": TEACHING_ROWS[iw], "security": sw, "savage": TEACHING_ROWS[isv], "max_regret": ms,
            "min_payoff": p.min(axis=1), "max_regret_by_row": regret_matrix(p).max(axis=1)}


def teaching_markdown(ex=None):
    ex = ex or teaching_example()
    f = lambda x: f"{x:.0f}"                                                                          # noqa: E731
    rows = ["| | scenario 1 | scenario 2 | worst payoff | regret in 1 | regret in 2 | maximum regret |", "|---|---|---|---|---|---|---|"]
    for i, name in enumerate(TEACHING_ROWS):
        rows.append(f"| {name} | {f(ex['payoff'][i, 0])} | {f(ex['payoff'][i, 1])} | {f(ex['min_payoff'][i])} | {f(ex['regret'][i, 0])} | {f(ex['regret'][i, 1])} | {f(ex['max_regret_by_row'][i])} |")
    rows += ["", f"Wald maximin chooses **{ex['wald']}** (best worst-case payoff {f(ex['security'])}). Savage minimax regret chooses **{ex['savage']}** (smallest maximum regret {f(ex['max_regret'])}). "
                 "The two rules disagree."]
    return "\n".join(rows)


def lp_example_markdown():
    a = TWO_BY_TWO
    g = solve_matrix_game(a)
    r = saddle_residuals(a, g["x"], g["y"])
    fmt = lambda v: "[" + ", ".join(f"{t:.6f}" for t in v) + "]"                                       # noqa: E731
    return "\n".join([
        f"- Payoff matrix (rows = investor, columns = Nature): {a.tolist()}.",
        f"- Investor LP value {g['value_row']:.6f}; Nature LP value {g['value_col']:.6f}; gap {g['gap']:.1e}.",
        f"- Investor's maximin mixed strategy x = {fmt(g['x'])}; Nature's minimax mixed strategy y = {fmt(g['y'])}; Nature's strategy read from the investor LP's dual variables = {fmt(g['dual_y'])}.",
        f"- Best-response residuals: row_gap {r['row_gap']:.1e}, col_gap {r['col_gap']:.1e}; guarantee of x {r['guarantee_row']:.6f}, guarantee of y {r['guarantee_col']:.6f}."])


# ============================== Part B: ellipsoid saddle point ========================================================

def payoff(w, mu, cov, lam):
    return float(mu @ w - 0.5 * lam * w @ cov @ w)


def worst_case_payoff(w, mu_hat, cov, lam, kappa, years):
    """min over the ellipsoid of f(w, mu) = mu_hat'w - kappa sqrt(w'Sigma_mu w) - (lam/2) w'Sigma w."""
    return float(mu_hat @ w - kappa * np.sqrt(w @ cov @ w / years) - 0.5 * lam * w @ cov @ w)


def nature_best_response(w, mu_hat, cov, kappa, years):
    sig_mu = cov / years
    s = np.sqrt(w @ sig_mu @ w)
    return mu_hat.copy() if (kappa == 0 or s == 0) else mu_hat - kappa * (sig_mu @ w) / s


def mahalanobis_sq(mu, mu_hat, cov, years):
    d = mu - mu_hat
    return float(years * d @ np.linalg.solve(cov, d))


def mean_variance_best_response(cov, mu, cap, lam, opts, tol):
    """argmax over the capped simplex of mu'w - (lam/2) w'Sigma w with the own QP engine: the maximiser lies on the frontier, so it
    solves P(r) for the r that maximises the concave function r - (lam/2) V*(r) (golden-section search on r). Returns (weights, value)."""
    r_min, r_max, _, _ = so.feasible_return_range(mu, cap)
    gr = (np.sqrt(5.0) - 1.0) / 2.0

    def h(r):
        res = so.solve_qp(cov, mu, cap, opts, target=float(r))
        return r - 0.5 * lam * res.objective, res

    a, b = r_min, r_max
    best = None
    if b - a > tol:
        c, d = b - gr * (b - a), a + gr * (b - a)
        fc, fd = h(c)[0], h(d)[0]
        while b - a > tol:
            if fc > fd:
                b, d, fd = d, c, fc
                c = b - gr * (b - a)
                fc = h(c)[0]
            else:
                a, c, fc = c, d, fd
                d = a + gr * (b - a)
                fd = h(d)[0]
    cands = [h(r) for r in (0.5 * (a + b), r_min, r_max)]
    (val, res) = max(cands, key=lambda t: t[0])
    w = res.weights
    return w, payoff(w, mu, cov, lam)


def saddle_own(cov, mu_hat, cap, lam, kappa, years, opts, tol):
    """Max-min solution w* of the ellipsoid game with the own QP engine.

    kappa sqrt(a) = min_{t>0} (kappa/2)(a/t + t) turns the max-min problem into max over (w, t) of
    mu_hat'w - (1/2)(lam + kappa/(t T)) w'Sigma w - kappa t / 2, jointly concave in (w, t). For fixed t the inner problem in w is a mean-variance QP with
    risk aversion lam_t = lam + kappa/(t T); the partial maximum G(t) is concave, so a golden-section search over t finds the optimum,
    and t* = sqrt(w*'Sigma w* / T). kappa = 0 is the plug-in mean-variance problem. Returns (weights, number of inner solves)."""
    if kappa == 0:
        w, _ = mean_variance_best_response(cov, mu_hat, cap, lam, opts, tol)
        return w, 1
    vmin = so.min_variance(cov, cap, opts).objective
    t_lo = np.sqrt(vmin / years) * (1 - 1e-6)
    t_hi = np.sqrt(cap * np.linalg.eigvalsh(cov)[-1] / years)
    count = [0]

    def G(t):
        count[0] += 1
        w, val = mean_variance_best_response(cov, mu_hat, cap, lam + kappa / (t * years), opts, tol)
        return payoff(w, mu_hat, cov, lam + kappa / (t * years)) - kappa * t / 2.0, w

    gr = (np.sqrt(5.0) - 1.0) / 2.0
    a, b = t_lo, t_hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    (fc, wc), (fd, wd) = G(c), G(d)
    while (b - a) > 1e-11 * b:
        if fc > fd:
            b, d, fd, wd = d, c, fc, wc
            c = b - gr * (b - a)
            fc, wc = G(c)
        else:
            a, c, fc, wc = c, d, fd, wd
            d = a + gr * (b - a)
            fd, wd = G(d)
    w = G(0.5 * (a + b))[1]
    return w, count[0]


def minmax_cvxpy(cov, mu_hat, cap, lam, kappa, years):
    """min over mu in the ellipsoid of max over w of f(w, mu), verification role only. By QP duality the inner maximum equals
    min over (nu, alpha >= 0, beta >= 0) of nu + cap 1'beta + (1/(2 lam)) q'Sigma^-1 q with q = mu - nu 1 + alpha - beta, so the whole
    min-max is one jointly convex program. Returns (value, mu_star)."""
    n = len(mu_hat)
    chol = np.linalg.cholesky(cov)
    linv = np.linalg.inv(chol)
    mu, nu, al, be = cp.Variable(n), cp.Variable(), cp.Variable(n, nonneg=True), cp.Variable(n, nonneg=True)
    q = mu - nu * np.ones(n) + al - be
    obj = nu + cap * cp.sum(be) + cp.sum_squares(linv @ q) / (2.0 * lam)
    cons = [cp.sum_squares(np.sqrt(years) * (linv @ (mu - mu_hat))) <= kappa ** 2]
    prob = cp.Problem(cp.Minimize(obj), cons)
    prob.solve(solver=cp.CLARABEL, tol_gap_abs=1e-12, tol_gap_rel=1e-12, tol_feas=1e-12, max_iter=500)
    if prob.status not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"min-max program failed: {prob.status}")
    return float(prob.value), np.asarray(mu.value, float)


def ellipsoid_points(mu_hat, cov, kappa, years, rng, n_points):
    """Random points on the boundary of the ellipsoid (uniform directions)."""
    n = len(mu_hat)
    lmu = np.linalg.cholesky(cov / years)
    z = rng.standard_normal((n_points, n))
    z /= np.linalg.norm(z, axis=1, keepdims=True)
    return mu_hat + kappa * z @ lmu.T


def verify_saddle(cov, mu_hat, cap, lam, kappa, years, w_star, opts, rng, n_points, tol):
    """Saddle residuals at (w*, mu*): mu* from the closed form; w* a best response to mu* (own QP engine); mu* a best response to w*."""
    mu_star = nature_best_response(w_star, mu_hat, cov, kappa, years)
    value = payoff(w_star, mu_star, cov, lam)
    w_br, v_br = mean_variance_best_response(cov, mu_star, cap, lam, opts, tol)
    pts = ellipsoid_points(mu_hat, cov, kappa, years, rng, n_points) if kappa > 0 else mu_hat[None, :]
    vals = np.array([payoff(w_star, m, cov, lam) for m in pts])
    local_gap = float("inf")
    if kappa > 0:                                           # small perturbations of mu*, projected back to the ellipsoid boundary
        lmu = np.linalg.cholesky(cov / years)
        for eta in (1e-4, 1e-3, 1e-2, 1e-1):
            for _ in range(50):
                m = mu_star + eta * kappa * (lmu @ rng.standard_normal(len(mu_hat)))
                m = mu_hat + (m - mu_hat) * kappa / np.sqrt(mahalanobis_sq(m, mu_hat, cov, years))
                local_gap = min(local_gap, payoff(w_star, m, cov, lam) - value)
    else:
        local_gap = 0.0
    return {"nature_local_gap": float(local_gap), "mu_star": mu_star, "value_at_saddle": value, "closed_form_value": worst_case_payoff(w_star, mu_hat, cov, lam, kappa, years),
            "maha_sq": mahalanobis_sq(mu_star, mu_hat, cov, years),
            "investor_gap": float(v_br - value), "investor_weight_diff": float(np.max(np.abs(w_br - w_star))),
            "nature_gap": float(vals.min() - value), "nature_random_min": float(vals.min())}


# ============================== Part C: Wasserstein ball ===============================================================

def cvar_discrete(losses, probs, alpha):
    """CVaR with tail probability alpha of a discrete distribution: the mean of the worst alpha of the probability mass."""
    order = np.argsort(-np.asarray(losses, float))
    l, p = np.asarray(losses, float)[order], np.asarray(probs, float)[order]
    cum = np.cumsum(p)
    take = np.clip(alpha - (cum - p), 0.0, p)               # mass of each atom inside the worst alpha of the distribution
    return float(np.sum(take * l) / alpha)


def _norm_ord(q):
    return np.inf if q == "inf" else q


def dual_norm_direction(w, q):
    """Unit vector u in the ground norm q maximising w'u, and the dual norm of w (the maximum)."""
    w = np.asarray(w, float)
    if q == 1:
        j = int(np.argmax(np.abs(w)))
        u = np.zeros_like(w)
        u[j] = np.sign(w[j]) or 1.0
        return u, float(np.abs(w[j]))
    if q == 2:
        nrm = float(np.linalg.norm(w))
        return w / nrm, nrm
    if q == "inf":
        u = np.sign(w)
        u[u == 0] = 1.0
        return u, float(np.abs(w).sum())
    raise ValueError(f"unsupported ground norm {q!r}")


def worst_case_cvar_dual_lp(w, rets, eps, cfg):
    """The paper's dual LP at a fixed portfolio w (support R^m, the formulation of src/wdro.py):
    minimise lam*eps + (1/T) sum_i s_i  subject to  s_i >= b_k tau + a_k w'xi_i (k = 1, 2),  lam >= |a_k| ||w||_dual.
    An upper bound on sup of [m E_Q(-w'xi) + CVaR_Q] over the ball, because tau sits outside the supremum. Returns (value, tau)."""
    a_tail, m, (a1, b1, a2, b2) = wdro._cvar_coeffs(cfg)
    p = wdro._dual_p(cfg)
    x = np.asarray(rets, float) @ np.asarray(w, float)
    t = len(x)
    lam_min = max(abs(a1), abs(a2)) * wdro._norm_val(w, p)
    rows = np.arange(2 * t)
    idx = np.r_[np.arange(t), np.arange(t)]
    coef_tau = np.r_[np.full(t, b1), np.full(t, b2)]
    a_ub = sparse.csr_matrix((np.r_[coef_tau, np.full(2 * t, -1.0)], (np.r_[rows, rows], np.r_[np.zeros(2 * t, int), 2 + idx])), shape=(2 * t, t + 2))
    b_ub = -np.r_[a1 * x, a2 * x]
    res = linprog(np.r_[0.0, eps, np.full(t, 1.0 / t)], A_ub=a_ub, b_ub=b_ub, bounds=[(None, None), (lam_min, None)] + [(None, None)] * t, method="highs")
    if res.status != 0:
        raise RuntimeError(f"dual LP failed: {res.message}")
    return float(res.fun), float(res.x[0])


def construct_nature(w, rets, eps, cfg):
    """Explicit worst-case distribution inside the ball.

    Loss L = -w'xi. Let u maximise w'u over the unit ball of the ground norm q (u = e_j* for q = 1, with j* the largest weight), so that
    moving a point by d = -delta u raises its loss by delta ||w||_dual. Take the worst alpha of the probability mass of the empirical
    distribution (the CVaR tail: the floor(alpha T) worst points and the needed fraction of the next one, whose mass is split) and move
    exactly that mass by delta = eps / alpha. The transport cost is alpha * delta = eps (the whole budget). The moved mass stays the
    worst alpha of the new distribution, so CVaR rises by delta ||w||_dual = eps ||w||_dual / alpha."""
    alpha, _, _ = wdro._cvar_coeffs(cfg)
    q = cfg["wdro"]["ground_norm"]
    xi = np.asarray(rets, float)
    t = len(xi)
    loss = -(xi @ np.asarray(w, float))
    u, dual = dual_norm_direction(w, q)
    delta = eps / alpha
    order = np.argsort(-loss)
    moved_mass = np.zeros(t)
    remaining = alpha
    for i in order:
        take = min(1.0 / t, remaining)
        moved_mass[i] = take
        remaining -= take
        if remaining <= 1e-15:
            break
    stay = np.full(t, 1.0 / t) - moved_mass
    loss_moved = loss + delta * dual
    atoms_loss = np.r_[loss, loss_moved]
    atoms_prob = np.r_[stay, moved_mass]
    cost = float(np.sum(moved_mass) * delta * np.linalg.norm(u, ord=_norm_ord(q)))
    return {"cvar": cvar_discrete(atoms_loss, atoms_prob, alpha), "cost": cost, "delta": delta, "moved_mass": float(moved_mass.sum()), "dual_norm": dual,
            "empirical_cvar": cvar_discrete(loss, np.full(t, 1.0 / t), alpha)}


def random_nature(w, rets, eps, cfg, rng):
    """A random feasible Nature strategy: every atom moves a random share of its mass by a random direction, scaled to use a random
    fraction (0.5 to 1) of the budget. Returns (cvar, cost)."""
    alpha, _, _ = wdro._cvar_coeffs(cfg)
    q = cfg["wdro"]["ground_norm"]
    xi = np.asarray(rets, float)
    t, n = xi.shape
    p = rng.uniform(0, 1, t) * (rng.uniform(0, 1, t) < rng.uniform(0.05, 0.6))
    d = rng.standard_normal((t, n))
    d /= np.linalg.norm(d, ord=_norm_ord(q), axis=1, keepdims=True)
    raw = float(np.sum(p) / t)
    if eps == 0 or raw == 0:
        scale = 0.0
    else:
        scale = rng.uniform(0.5, 1.0) * eps / raw
    loss = -(xi @ np.asarray(w, float))
    loss_new = -((xi + scale * d) @ np.asarray(w, float))
    atoms_loss = np.r_[loss, loss_new]
    atoms_prob = np.r_[(1 - p) / t, p / t]
    cost = float(np.sum(p / t * np.linalg.norm(scale * d, ord=_norm_ord(q), axis=1)))
    return cvar_discrete(atoms_loss, atoms_prob, alpha), cost


# ============================== era payoff matrix ======================================================================

def full_sample_portfolios(rets, cfg, opts, names, dr_eps):
    """Weights computed once on full-sample estimates."""
    td, cap, rf = cfg["trading_days"], cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"]
    mu = rets.mean().to_numpy() * td
    cov = rets.cov().to_numpy() * td
    out = {}
    for name in names:
        if name == "min_variance":
            out[name] = so.min_variance(cov, cap, opts).weights
        elif name == "max_sharpe":
            out[name] = so.max_sharpe(cov, mu, cap, rf, opts).weights
        elif name == "equal_weight":
            out[name] = np.full(len(mu), 1.0 / len(mu))
        elif name.startswith("dr_meanvar_"):
            eps = float(name.split("_")[-1])
            if eps not in dr_eps:
                raise ValueError(f"{name}: epsilon not listed in game.dr_meanvar_eps")
            out[name] = wdro.wdro_weights(rets, eps, "meanvar", cfg)
        else:
            raise ValueError(f"unknown portfolio {name}")
    return out


def sharpe_under(w, mu, cov, rf):
    return float((mu @ w - rf) / np.sqrt(w @ cov @ w))


def era_payoff_matrix(weights, rets, eras, cfg):
    """Sharpe of every portfolio under each era's re-estimated parameters (in-sample for each era: the full-sample weights saw that era)."""
    import pandas as pd
    td, rf = cfg["trading_days"], cfg["risk_free_rate"]
    cols = {}
    for name, (a, b) in eras.items():
        win = rets.loc[a:b]
        mu, cov = win.mean().to_numpy() * td, win.cov().to_numpy() * td
        cols[name] = [sharpe_under(w, mu, cov, rf) for w in weights.values()]
    return pd.DataFrame(cols, index=list(weights))
