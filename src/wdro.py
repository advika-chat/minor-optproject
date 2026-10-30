"""Wasserstein distributionally robust portfolios (CVaR and mean-variance).

Entry points: ``wdro_weights(returns_window, eps, kind, config)`` with kind in
{"cvar", "meanvar"}, ``wdro_solve`` (also returns the optimal value and tau),
``worst_case_cvar_bound`` and ``sweep_eps``. Weights are long-only, fully invested and
capped at ``config["portfolio"]["weight_cap"]``. Scenarios are the T rows of
``returns_window`` (daily simple returns) and the Wasserstein radius ``eps`` is in
daily-return units. ``wdro.ground_norm`` (q in {1, 2, "inf"}) is the norm on return
vectors used for the ground metric; the dual norm p (inf, 2, 1 respectively) appears in
the regularisers below.

======================================================================
1. DR mean-CVaR  (Mohajerin Esfahani & Kuhn, Math. Programming 171, 2018, Sec. 7.1)
======================================================================
Source facts, checked against the paper's text. Problem (26) minimises
E[-<x,xi>] + rho * CVaR_alpha(-<x,xi>) over the simplex, where alpha in (0, 1] is
the tail probability, CVaR_alpha(Z) = inf_tau tau + (1/alpha) E[max(Z - tau, 0)]. The
paper writes the objective as inf_tau E[max_k a_k <x,xi> + b_k tau] with K = 2,
a_1 = -1, a_2 = -1 - rho/alpha, b_1 = rho, b_2 = rho (1 - 1/alpha). Its distributionally
robust counterpart (taken as the paper's definition) is

    J_N(eps) = inf_{x, tau}  sup_{Q in B_eps(P_N)}  E_Q[ max_k a_k <x,xi> + b_k tau ],

with B_eps the type-1 Wasserstein ball around the empirical distribution P_N, and
(27) (from Corollary 5.1, support {C xi <= d}) is the finite program

    inf  lambda*eps + (1/N) sum_i s_i
    s.t. x in X,  b_k tau + a_k <x, xi_i> + <gamma_ik, d - C xi_i> <= s_i   for all i, k
         || C' gamma_ik - a_k x ||_* <= lambda,   gamma_ik >= 0   for all i, k.

What is implemented here. (a) Support Xi = R^m, i.e. C = 0 and d = 0, so the gamma
terms vanish and the dual-norm constraint becomes |a_k| ||x||_* <= lambda. (Corollary 5.1
is stated for polytopes; R^m is the degenerate case C = 0, d = 0, which I use. I did not
check the paper for an explicit discussion of that edge case; the brute-force test in
tests/test_wdro.py checks the resulting bound numerically.) Returns are therefore
treated as unbounded, and no support constraint such as xi >= -1 is imposed.
(b) Dividing the paper's objective by rho > 0 gives m E[-<x,xi>] + CVaR with
m = 1/rho. In the form used here a_1 = -m, b_1 = 1, a_2 = -(m + 1/alpha),
b_2 = 1 - 1/alpha. m = 0 is pure CVaR minimisation, the rho -> infinity limit, which is
not itself a case of the paper's rho in R+, but the derivation only uses that the loss is
a maximum of affine functions. m = ``wdro.mean_weight``. (c) alpha = 1 - ``risk.cvar_alpha``:
the project config stores the confidence level (0.95), the paper's alpha is the tail
probability (0.05).

Because |a_2| > |a_1|, lambda = (m + 1/alpha) ||x||_* at the optimum, so the program is
equivalent to  inf_{x,tau}  (1/N) sum_i max_k(a_k <x,xi_i> + b_k tau) + eps (m + 1/alpha) ||x||_*,
i.e. empirical mean-CVaR plus a norm penalty on the weights. This is a consequence of the
constraints above, not an additional claim from the paper. The solver nevertheless keeps the
paper's explicit lambda and s_i variables.

What the optimal value means. Because tau sits outside the supremum, the optimal value is
the paper's J_N(eps). For the chosen x it is an upper bound on sup_Q [m E_Q[-<x,xi>] +
CVaR_alpha^Q(-<x,xi>)], since CVaR is an infimum over tau (minimax inequality). I did not
see a statement in the paper that the two coincide here, so I do not claim equality.
The optional minimum-return constraint mean(xi)'w >= r_min uses the nominal (empirical) mean
and is not part of the paper's program.

======================================================================
2. DR mean-variance  (Blanchet, Chen & Zhou, Management Science 68(9), 2022)
======================================================================
Source facts, checked against the paper's text (arXiv 1802.04885 version). Problem (2):
min over phi in F of max over P in U_delta(P_n) of Var_P(phi'R), where
U_delta = {P : D_c(P, P_n) <= delta}, c(u,w) = ||u - w||_q^2 with q >= 1 (so D_c is the squared
order-2 Wasserstein distance) and F = {phi : 1'phi = 1, min_P E_P[phi'R] >= abar}. Proposition 1
gives min_P E_P[phi'R] = E_n[phi'R] - sqrt(delta) ||phi||_p with 1/p + 1/q = 1. Theorem 1:
the problem is equivalent to

    min over phi in F  ( sqrt(phi' Var_n(R) phi) + sqrt(delta) ||phi||_p )^2,

with the same optimal solutions and value. Var_n is the empirical covariance (1/T, not
1/(T-1)). R lives in R^d with no support restriction.

What is implemented. delta = eps^2 (eps is the order-2 Wasserstein radius), q =
``wdro.ground_norm``, p its conjugate. The solver minimises sqrt(w' Var_n w) + eps ||w||_p,
which has the same minimisers as the squared form; the reported objective value is the
square. The feasible set is long-only, capped and fully invested, plus, if
``wdro.min_return`` is set, E_n[w'R] - eps ||w||_p >= min_return/252 (the worst-case mean
constraint of F with abar = min_return/trading_days). Differences from the paper's
statement: the paper's F contains only the budget and worst-case-mean constraints. The
reduction is derived for each fixed phi, so I apply it on the smaller convex set
{0 <= w <= cap}; that restriction is mine, not something the paper states. Without
``min_return`` I read the unconstrained problem as abar below every portfolio's worst-case
mean, so the proof's only use of abar (alpha_opt = phi'E_n[R] >= abar) holds; this too is my
reading. The paper's objective has no mean term: it minimises worst-case variance subject to
a worst-case mean floor. It says nothing about CVaR, and its results on choosing delta
(Section 3) are not used here. At eps = 0 the problem is the empirical minimum-variance
portfolio.
"""
import cvxpy as cp
import numpy as np
import pandas as pd

from src.markowitz import _check_cap, _solve
from src.robust import portfolio_cvar

_DUAL = {1: "inf", 2: 2, "inf": 1}


def eps_grid(config):
    w = config["wdro"]
    return np.logspace(np.log10(w["eps_min"]), np.log10(w["eps_max"]), w["eps_points"])


def _dual_p(config):
    q = config["wdro"]["ground_norm"]
    if q not in _DUAL:
        raise ValueError(f"wdro.ground_norm must be 1, 2 or 'inf', got {q!r}")
    return _DUAL[q]


def _norm_expr(w, p):
    return cp.norm_inf(w) if p == "inf" else cp.norm(w, p)


def _norm_val(w, p):
    return float(np.linalg.norm(w, ord=np.inf if p == "inf" else p))


def _cvar_coeffs(config):
    a = 1.0 - config["risk"]["cvar_alpha"]            # tail probability
    m = config["wdro"]["mean_weight"]
    return a, m, (-m, 1.0, -(m + 1.0 / a), 1.0 - 1.0 / a)


def _solve_cvar(r, eps, config):
    cap, td = config["portfolio"]["weight_cap"], config["trading_days"]
    t, n = r.shape
    _check_cap(n, cap)
    p = _dual_p(config)
    _, _, (a1, b1, a2, b2) = _cvar_coeffs(config)
    w, tau, lam, s = cp.Variable(n), cp.Variable(), cp.Variable(nonneg=True), cp.Variable(t)
    nw = _norm_expr(w, p)
    cons = [cp.sum(w) == 1, w >= 0, w <= cap,
            s >= b1 * tau + a1 * (r @ w), s >= b2 * tau + a2 * (r @ w),
            lam >= abs(a1) * nw, lam >= abs(a2) * nw]
    if config["wdro"]["min_return"] is not None:
        cons.append(r.mean(axis=0) * td @ w >= config["wdro"]["min_return"])
    prob = cp.Problem(cp.Minimize(lam * eps + cp.sum(s) / t), cons)
    _solve(prob)
    return np.clip(w.value, 0.0, cap), float(tau.value), float(prob.value)


def _solve_meanvar(r, eps, config):
    cap, td = config["portfolio"]["weight_cap"], config["trading_days"]
    t, n = r.shape
    _check_cap(n, cap)
    p = _dual_p(config)
    cov = np.cov(r, rowvar=False, bias=True)          # Var_n, 1/T normalisation
    chol = np.linalg.cholesky(cov + 1e-12 * np.eye(n))
    w = cp.Variable(n)
    reg = _norm_expr(w, p)
    cons = [cp.sum(w) == 1, w >= 0, w <= cap]
    if config["wdro"]["min_return"] is not None:
        cons.append(r.mean(axis=0) @ w - eps * reg >= config["wdro"]["min_return"] / td)
    prob = cp.Problem(cp.Minimize(cp.norm(chol.T @ w, 2) + eps * reg), cons)
    _solve(prob)
    return np.clip(w.value, 0.0, cap), float("nan"), float(prob.value) ** 2


def wdro_solve(returns_window, eps, kind, config):
    """Solve and return {"weights", "tau", "value"}. For kind="cvar", value is the optimal
    worst-case mean-CVaR bound J_N(eps); for "meanvar" it is the worst-case variance
    (sqrt(w' Var_n w) + eps ||w||_p)^2 and tau is NaN."""
    if eps < 0:
        raise ValueError("eps must be non-negative")
    r = returns_window.to_numpy()
    if kind == "cvar":
        w, tau, val = _solve_cvar(r, eps, config)
    elif kind == "meanvar":
        w, tau, val = _solve_meanvar(r, eps, config)
    else:
        raise ValueError(f"kind must be 'cvar' or 'meanvar', got {kind!r}")
    return {"weights": w, "tau": tau, "value": val}


def wdro_weights(returns_window, eps, kind, config):
    return wdro_solve(returns_window, eps, kind, config)["weights"]


def worst_case_cvar_bound(w, returns_window, eps, config):
    """Worst-case mean-CVaR bound of a fixed portfolio w at radius eps (tau optimised):
    empirical m E[-w'xi] + CVaR_alpha plus eps (m + 1/alpha) ||w||_p. With
    ``wdro.mean_weight`` = 0 this is the DR-CVaR bound of Section 1 for that portfolio."""
    a, m, _ = _cvar_coeffs(config)
    r = returns_window.to_numpy()
    emp = -m * float(r.mean(axis=0) @ w) + portfolio_cvar(w, returns_window, 1.0 - a)
    return emp + eps * (m + 1.0 / a) * _norm_val(w, _dual_p(config))


def sweep_eps(returns_window, eps_values, kind, config):
    """For each eps: weights, nominal (empirical) annualised expected return, empirical CVaR
    (daily losses, level risk.cvar_alpha), the worst-case CVaR bound of the chosen portfolio,
    and the optimiser's attained objective value (the CVaR bound for kind="cvar", the
    worst-case variance for "meanvar"). Returned as a DataFrame with a "weights" column of arrays."""
    td, alpha = config["trading_days"], config["risk"]["cvar_alpha"]
    mu_ann = returns_window.mean().to_numpy() * td
    rows = []
    for eps in eps_values:
        sol = wdro_solve(returns_window, float(eps), kind, config)
        w = sol["weights"]
        rows.append({
            "eps": float(eps), "weights": w,
            "nominal_return": float(mu_ann @ w),
            "empirical_cvar": portfolio_cvar(w, returns_window, alpha),
            "worst_case_cvar_bound": worst_case_cvar_bound(w, returns_window, float(eps), config),
            "objective_value": sol["value"],
            "effective_n": float(1.0 / np.sum(w ** 2)),
        })
    return pd.DataFrame(rows)
