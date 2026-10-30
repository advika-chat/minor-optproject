"""Own solver for the long-only, capped, fully invested mean-variance problem (numpy only).

Problem P(r), with variance V(r) as its optimal value:

    minimise    w' Sigma w
    subject to  sum(w) = budget            (budget = 1 in the portfolio problem)
                mu' w = r                  (optional target return)
                0 <= w_i <= cap

Method: a primal active-set method for convex quadratic programs (see Nocedal and Wright,
"Numerical Optimization"). The working set holds the bound constraints currently treated as
equalities; the equality-constrained step over the free variables is found from the KKT linear
system

    [ 2 Sigma_FF   A_F' ] [ p ]   [ -g_F ]
    [ A_F           0   ] [ y ] = [   0  ]        g = 2 Sigma w, y = -nu

(no inverse of Sigma; a least-squares solve is used only if the KKT matrix is numerically
singular). The working set is stationary when the projected gradient (gradient minus A_F' nu on the free
variables) is below ``projected_gradient_tol``; this is scale-aware, whereas the Newton step itself is noisy
when Sigma is nearly singular. A step to a bound adds that bound to the working set; at a stationary point the
multipliers of the working-set bounds are computed and the most negative one is dropped. A
cycling guard switches the dropping rule to Bland's (smallest index) after repeated zero-length
steps, and the iteration count is capped.

Lagrangian convention (so the multipliers read as sensitivities of the optimal variance V):

    2 Sigma w = nu_b * 1 + nu_r * mu + lam_lower - lam_upper,   lam_lower, lam_upper >= 0
    lam_lower_i * w_i = 0,   lam_upper_i * (cap - w_i) = 0

    nu_b  = dV/d(budget)        nu_r  = dV/dr (slope of variance against target return)
    lam_upper_i = -dV/d(cap_i)  (variance saved by loosening the cap on asset i)
    lam_lower_i = +dV/d(lower bound of asset i)

Starting point (no external optimiser): the equal-weight vector w_eq is feasible when
budget/N <= cap. The maximum-return capped vector w_max (fill the highest-mu assets up to the cap)
and the minimum-return capped vector w_min (lowest-mu first) are computed analytically, and are
feasible, so r_min = mu'w_min and r_max = mu'w_max are the smallest and largest achievable
returns. For a target r between r_eq and r_max the start is the convex combination
w_eq + t (w_max - w_eq) with t = (r - r_eq) / (r_max - r_eq), and for r between r_min and r_eq the
analogous combination with w_min. This covers the whole feasible range [r_min, r_max]. Outside it
(by more than ``feasibility_tol``) the status is "infeasible_target"; if cap * N < budget the
status is "infeasible_cap".

Known limitations: if the covariance is exactly singular on the free variables the KKT matrix is
singular, the minimum-norm least-squares step is used and the optimal weights need not be unique
(multipliers_unique is then False). If cap * N equals the budget the feasible set is the single
point cap * 1 and the multipliers are not unique. Accuracy of weights degrades with the conditioning of Sigma.
"""
from dataclasses import dataclass

import numpy as np

OPTIMAL = "optimal"
INFEASIBLE_TARGET = "infeasible_target"
INFEASIBLE_CAP = "infeasible_cap"
MAX_ITERATIONS = "max_iterations"


@dataclass
class QPResult:
    status: str
    weights: object = None
    objective: float = float("nan")
    target: object = None
    budget: float = 1.0
    nu_budget: float = float("nan")
    nu_return: object = None
    lam_lower: object = None
    lam_upper: object = None
    active_lower: tuple = ()
    active_upper: tuple = ()
    iterations: int = 0
    multipliers_unique: bool = False
    bland_used: bool = False
    note: str = ""

    @property
    def ok(self):
        return self.status == OPTIMAL


@dataclass
class SharpeResult:
    status: str
    weights: object = None
    target_return: float = float("nan")
    volatility: float = float("nan")
    sharpe: float = float("nan")
    evaluations: int = 0
    bracket_width: float = float("nan")
    r_min_variance: float = float("nan")
    r_max: float = float("nan")
    rf_above_min_variance_return: bool = False
    nonpositive_sharpe: bool = False
    at_boundary: bool = False
    qp: object = None


def _fill(mu, cap, budget, descending):
    order = np.argsort(-mu if descending else mu, kind="stable")
    w = np.zeros(len(mu))
    remaining = budget
    for i in order:
        x = min(cap, remaining)
        w[i] = x
        remaining -= x
        if remaining <= 0:
            break
    return w


def feasible_return_range(mu, cap, budget=1.0):
    """(r_min, r_max, w_min, w_max): smallest and largest achievable return under the cap, computed
    analytically by filling assets up to the cap in order of mu (a fractional knapsack)."""
    mu = np.asarray(mu, float)
    w_max = _fill(mu, cap, budget, True)
    w_min = _fill(mu, cap, budget, False)
    return float(mu @ w_min), float(mu @ w_max), w_min, w_max


def _kkt_step(h, a_f, g_f):
    """Solve the equality-constrained step over the free variables from the KKT system."""
    nf, m = len(g_f), a_f.shape[0]
    if np.linalg.matrix_rank(a_f) >= nf:
        return np.zeros(nf)                 # empty null space of A_F: nothing can move (avoids noise from an ill-conditioned solve)
    k = np.zeros((nf + m, nf + m))
    k[:nf, :nf] = h
    k[:nf, nf:] = a_f.T
    k[nf:, :nf] = a_f
    rhs = np.concatenate([-g_f, np.zeros(m)])
    try:
        if np.linalg.cond(k) < 1e13:
            sol = np.linalg.solve(k, rhs)
        else:
            sol = np.linalg.lstsq(k, rhs, rcond=None)[0]
    except np.linalg.LinAlgError:
        sol = np.linalg.lstsq(k, rhs, rcond=None)[0]
    return sol[:nf]


def _multipliers(g, a, state):
    """Equality multipliers nu from the free-variable stationarity, bound multipliers from the rest."""
    free = state == 0
    if free.any():
        a_f = a[:, free]
        nu = np.linalg.lstsq(a_f.T, g[free], rcond=None)[0]
        unique = np.linalg.matrix_rank(a_f) == a.shape[0]
    else:
        nu, unique = np.zeros(a.shape[0]), False
    r = g - a.T @ nu
    lam_lo = np.where(state == -1, r, 0.0)
    lam_hi = np.where(state == 1, -r, 0.0)
    return nu, lam_lo, lam_hi, bool(unique)


def solve_qp(sigma, mu, cap, opts, target=None, budget=1.0):
    """Solve P(r) (``target`` given) or the minimum-variance problem (``target`` None). ``opts`` is the
    ``solver_own`` config section. Returns a QPResult."""
    sigma, mu = np.asarray(sigma, float), np.asarray(mu, float)
    n = len(mu)
    feas = opts["feasibility_tol"]
    if cap * n < budget - feas:
        return QPResult(INFEASIBLE_CAP, target=target, budget=budget, note="cap * N < budget")
    use_ret, redundant = target is not None, False
    if use_ret:
        r_min, r_max, w_min, w_max = feasible_return_range(mu, cap, budget)
        if target < r_min - feas or target > r_max + feas:
            return QPResult(INFEASIBLE_TARGET, target=target, budget=budget,
                            note=f"target outside the achievable range [{r_min:.6g}, {r_max:.6g}]")
        if np.ptp(mu) <= 1e-14 * max(1.0, np.abs(mu).max()):     # constant mu: the return row is parallel to the budget row
            use_ret, redundant = False, True
    w_eq = np.full(n, budget / n)
    w = w_eq.copy()
    if use_ret:
        r_eq = float(mu @ w_eq)
        if target >= r_eq:
            denom = r_max - r_eq
            t = 0.0 if denom <= 0 else min(1.0, (target - r_eq) / denom)
            w = w_eq + t * (w_max - w_eq)
        else:
            denom = r_eq - r_min
            t = 0.0 if denom <= 0 else min(1.0, (r_eq - target) / denom)
            w = w_eq + t * (w_min - w_eq)
    w = np.clip(w, 0.0, cap)
    a = np.vstack([np.ones(n), mu]) if use_ret else np.ones((1, n))

    g2 = 2.0 * sigma
    state = np.zeros(n, dtype=int)            # 0 free, -1 at the lower bound, +1 at the cap (working set)
    step_tol, dual_tol = opts["active_set_step_tol"], opts["dual_tol"]
    zero_run, bland, iters, status = 0, False, 0, MAX_ITERATIONS
    while iters < opts["max_iterations"]:
        iters += 1
        free = np.where(state == 0)[0]
        g = g2 @ w
        p = np.zeros(n)
        proj = 0.0
        if len(free):
            a_f = a[:, free]
            nu_f = np.linalg.lstsq(a_f.T, g[free], rcond=None)[0]
            proj = float(np.max(np.abs(g[free] - a_f.T @ nu_f)))        # projected gradient: zero exactly at a stationary point
            if proj > opts["projected_gradient_tol"]:
                p[free] = _kkt_step(g2[np.ix_(free, free)], a_f, g[free])
        if np.max(np.abs(p)) <= step_tol:
            nu, lam_lo, lam_hi, _ = _multipliers(g, a, state)
            viol = [(lam_lo[i], i) for i in np.where(state == -1)[0] if lam_lo[i] < -dual_tol]
            viol += [(lam_hi[i], i) for i in np.where(state == 1)[0] if lam_hi[i] < -dual_tol]
            if not viol:
                status = OPTIMAL
                break
            drop = min(viol, key=lambda v: v[1]) if bland else min(viol, key=lambda v: (v[0], v[1]))
            state[drop[1]] = 0
            continue
        alpha, block, kind = 1.0, -1, 0
        for i in free:
            if p[i] < -1e-14:
                a_i, k = w[i] / -p[i], -1
            elif p[i] > 1e-14:
                a_i, k = (cap - w[i]) / p[i], 1
            else:
                continue
            if a_i < alpha:
                alpha, block, kind = max(a_i, 0.0), i, k
        w = np.clip(w + alpha * p, 0.0, cap)
        if block >= 0:
            w[block] = 0.0 if kind == -1 else cap
            state[block] = kind
        zero_run = zero_run + 1 if alpha <= 1e-15 else 0
        if zero_run >= opts["cycle_guard_zero_steps"]:
            bland = True
    g = g2 @ w
    nu, lam_lo, lam_hi, unique = _multipliers(g, a, state)
    degenerate_set = cap * n - budget <= feas
    return QPResult(
        status=status, weights=w, objective=float(w @ sigma @ w), target=target, budget=budget,
        nu_budget=float(nu[0]), nu_return=(float(nu[1]) if use_ret else (0.0 if redundant else None)),
        lam_lower=lam_lo, lam_upper=lam_hi,
        active_lower=tuple(int(i) for i in np.where(state == -1)[0]),
        active_upper=tuple(int(i) for i in np.where(state == 1)[0]),
        iterations=iters, multipliers_unique=bool(unique and not degenerate_set), bland_used=bland,
        note="feasible set is a single point" if degenerate_set else "")


def min_variance(sigma, cap, opts, budget=1.0):
    """Minimum-variance portfolio (no return constraint)."""
    return solve_qp(sigma, np.zeros(len(sigma)), cap, opts, target=None, budget=budget)


def frontier(sigma, mu, cap, opts, r_grid=None, n_points=None):
    """Optimal portfolios along the capped frontier. Default grid: ``frontier_points`` values from the
    minimum-variance return to the maximum achievable return."""
    if r_grid is None:
        mv = min_variance(sigma, cap, opts)
        if not mv.ok:
            return [mv]
        r_hi = feasible_return_range(mu, cap)[1]
        r_grid = np.linspace(float(np.asarray(mu) @ mv.weights), r_hi, n_points or opts["frontier_points"])
    return [solve_qp(sigma, mu, cap, opts, target=float(r)) for r in r_grid]


def kkt_residuals(sigma, mu, cap, res):
    """Independent KKT residuals of a QPResult: stationarity, primal feasibility, dual feasibility
    (largest multiplier-sign violation) and complementary slackness."""
    w = res.weights
    g = 2.0 * np.asarray(sigma, float) @ w
    stat = g - res.nu_budget - (res.nu_return or 0.0) * np.asarray(mu, float) - res.lam_lower + res.lam_upper
    primal = max(abs(w.sum() - res.budget), float(np.max(-w)), float(np.max(w - cap)), 0.0)
    if res.target is not None:
        primal = max(primal, abs(float(np.asarray(mu) @ w) - res.target))
    dual = max(0.0, -float(min(res.lam_lower.min(), res.lam_upper.min())))
    comp = max(float(np.max(np.abs(res.lam_lower * w))), float(np.max(np.abs(res.lam_upper * (cap - w)))))
    return {"stationarity": float(np.max(np.abs(stat))), "primal_feasibility": float(primal),
            "dual_feasibility": dual, "complementary_slackness": comp}


def max_sharpe(sigma, mu, cap, rf, opts):
    """Maximise (r - rf) / sqrt(V(r)) over the achievable returns by golden-section search on r between
    the minimum-variance return and the maximum achievable return, solving P(r) for each r.

    Assumption: the Sharpe ratio is unimodal along that part of the frontier. Golden-section search
    is only guaranteed to find the maximum then; verify with ``sharpe_profile`` / ``count_valleys``.
    The assumption can fail when rf is above the minimum-variance return, which is flagged in the result.
    The search locates the maximiser only to about sqrt(machine epsilon) in r because the Sharpe ratio is
    flat there. The best of the search result and the two end points is returned."""
    sigma, mu = np.asarray(sigma, float), np.asarray(mu, float)
    mv = min_variance(sigma, cap, opts)
    if not mv.ok:
        return SharpeResult(status=mv.status)
    r_lo = float(mu @ mv.weights)
    r_hi = feasible_return_range(mu, cap)[1]
    evals = 0

    def f(r):
        nonlocal evals
        evals += 1
        res = solve_qp(sigma, mu, cap, opts, target=r)
        return (r - rf) / np.sqrt(res.objective), res

    a, b = r_lo, r_hi
    gr = (np.sqrt(5.0) - 1.0) / 2.0
    if b - a > opts["golden_section_tol"]:
        c, d = b - gr * (b - a), a + gr * (b - a)
        fc, fd = f(c)[0], f(d)[0]
        while b - a > opts["golden_section_tol"]:
            if fc > fd:
                b, d, fd = d, c, fc
                c = b - gr * (b - a)
                fc = f(c)[0]
            else:
                a, c, fc = c, d, fd
                d = a + gr * (b - a)
                fd = f(d)[0]
    candidates = [(f(r), r) for r in (0.5 * (a + b), r_lo, r_hi)]
    (s, res), r_best = max(candidates, key=lambda x: x[0][0])
    return SharpeResult(
        status=OPTIMAL, weights=res.weights, target_return=r_best, volatility=float(np.sqrt(res.objective)), sharpe=float(s),
        evaluations=evals, bracket_width=b - a, r_min_variance=r_lo, r_max=r_hi,
        rf_above_min_variance_return=bool(rf >= r_lo), nonpositive_sharpe=bool(s <= 0),
        at_boundary=bool(min(abs(r_best - r_lo), abs(r_best - r_hi)) <= 1e-8), qp=res)


def sharpe_profile(sigma, mu, cap, rf, opts, n_points=None):
    """Sharpe ratio on a fine grid of target returns between the minimum-variance return and r_max."""
    mv = min_variance(sigma, cap, opts)
    r_lo = float(np.asarray(mu) @ mv.weights)
    r_hi = feasible_return_range(mu, cap)[1]
    grid = np.linspace(r_lo, r_hi, n_points or opts["unimodality_grid_points"])
    s = np.array([(r - rf) / np.sqrt(solve_qp(sigma, mu, cap, opts, target=float(r)).objective) for r in grid])
    return grid, s


def count_valleys(s, noise_tol):
    """Direction changes of a profile, ignoring differences below noise_tol. A profile that rises then falls
    has n_valleys == 0 (unimodal for a maximisation); a fall followed by a rise is a valley."""
    d = np.diff(s)
    sign = np.where(np.abs(d) <= noise_tol, 0, np.sign(d))
    nz = sign[sign != 0]
    return {"n_peaks": int(np.sum((nz[:-1] > 0) & (nz[1:] < 0))), "n_valleys": int(np.sum((nz[:-1] < 0) & (nz[1:] > 0)))}
