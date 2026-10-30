# KKT conditions and duality for the capped mean-variance problem

These notes derive, step by step, the optimality conditions and the dual problem for the portfolio problem used throughout the project, and explain what every multiplier means. Basic calculus and linear algebra are assumed; no optimisation background is. The code that checks every statement numerically is in `src/duality.py` and `src/solver_own.py`, and the verification numbers below are produced by `scripts/run_sensitivity.py`.

## 1. The problem

There are N assets with expected returns collected in a vector `mu` and covariance matrix `Sigma` (symmetric, positive definite, so every portfolio has strictly positive variance). A portfolio is a vector of weights `w`. For a target return `r` we solve

    P(r):  minimise    V(w) = w' Sigma w
           subject to  1'w = 1                 (fully invested)
                       mu'w = r                (target return)
                       0 <= w_i <= cap         (long only, capped)

For the minimum-variance portfolio the return constraint is dropped. Write V*(r) for the optimal value. The maximum-Sharpe portfolio is the solution of P(r*) for the return r* that maximises (r - rf) / sqrt(V*(r)).

## 2. Why a Lagrangian

A constrained problem is hard because the constraints stop us from simply setting a gradient to zero. The Lagrangian trick moves each constraint into the objective with a price (a multiplier): a constraint that is violated is made expensive, one that is satisfied costs nothing or gives a credit. If the prices are right, minimising the combined function without constraints returns the constrained optimum. The prices themselves turn out to be the sensitivities of the optimal value to the constraints, which is why they matter beyond the algebra.

## 3. Sign conventions

Every textbook chooses its own signs, so we fix ours. We write inequalities in the form `h(w) <= 0` and give them a multiplier that is required to be non-negative; we add `multiplier * h(w)`. Equalities `a(w) = b` get a free multiplier and we add `-multiplier * (a(w) - b)`. We also use `V = w' Sigma w` (not one half of it); with one half every multiplier below would be halved.

| Constraint | Written as | Multiplier | Sign |
|---|---|---|---|
| fully invested | `1'w = 1` | `lam` | free |
| target return | `mu'w = r` | `gam` | free |
| no short sales | `-w_i <= 0` | `alpha_i` | `>= 0` |
| cap | `w_i - cap <= 0` | `beta_i` | `>= 0` |

## 4. The Lagrangian

    L(w; lam, gam, alpha, beta) = w'Sigma w - lam (1'w - 1) - gam (mu'w - r) - alpha'w + beta'(w - cap 1)

Collecting the terms that multiply `w` makes the structure visible. Define the vector

    q = lam 1 + gam mu + alpha - beta.

Then

    L = w'Sigma w - q'w + lam + gam r - cap (1'beta).

A feasible portfolio has `1'w = 1`, `mu'w = r`, `w >= 0` and `w <= cap`, so for it the three constraint terms are `0`, `-alpha'w <= 0` (because `alpha >= 0` and `w >= 0`) and `beta'(w - cap 1) <= 0` (because `beta >= 0` and `w <= cap`). Hence for every feasible `w` and every `alpha, beta >= 0`:

    L(w; ...) <= w'Sigma w.                                                      (*)

## 5. The KKT conditions

A feasible `w*` is optimal for this convex problem exactly when there are multipliers for which all four groups hold:

1. **Stationarity** (the gradient of `L` with respect to `w` is zero): `2 Sigma w = q`, that is `2 Sigma w = lam 1 + gam mu + alpha - beta`.
2. **Primal feasibility**: `1'w = 1`, `mu'w = r`, `0 <= w <= cap`.
3. **Dual feasibility**: `alpha >= 0`, `beta >= 0`.
4. **Complementary slackness**: `alpha_i w_i = 0` and `beta_i (cap - w_i) = 0` for every `i`. A constraint that is not active (the weight is strictly between its bounds) must have multiplier zero: its price is zero because it does not bind.

Because the problem is convex (`Sigma` is positive definite and the constraints are linear), these conditions are sufficient, and since the constraints are linear they are also necessary. The repository checks them on every solution: stationarity, feasibility, multiplier signs and complementary slackness residuals are all at the level of `1e-15` to `1e-8` (see `outputs/tables/kkt_report_own.csv`).

## 6. What the multipliers mean

Let `V*(b)` be the optimal value when a constraint's right-hand side is moved to `b`. If the set of binding constraints does not change under a small move (the usual situation), the envelope theorem says the derivative of the optimal value equals the derivative of the Lagrangian with respect to that right-hand side, with `w` held at the optimum. Applying that to the Lagrangian above gives:

* **Budget, `lam`**: `dV*/d(budget) = lam`. The extra variance per unit of additional capital invested while the target return stays fixed. It can be negative: with a fixed return target, more capital means each unit has to earn less, which can lower risk.
* **Return target, `gam`**: `dV*/dr = gam`. This is the slope of the minimum-variance curve against return, the marginal variance cost of asking for more return. In volatility terms `d sigma/dr = gam / (2 sigma)`. At the maximum-Sharpe portfolio the first-order condition for the Sharpe ratio gives the identity `gam = 2 V / (r - rf)`, which the code verifies.
* **Cap, `beta_i`**: the constraint is `w_i <= cap`, so raising the cap on asset `i` changes the optimal variance by `-beta_i`. `beta_i` is the variance saved per unit of loosening that asset's cap, and it is positive only for assets sitting at the cap. When all assets share one cap that moves together, `dV*/d cap = -sum_i beta_i`, which we call the cap shadow price.
* **No-short rule, `alpha_i`**: allowing a short position of size `s` in asset `i` (that is, `w_i >= -s`) changes the optimal variance by `-alpha_i` per unit of `s`. `alpha_i` is the variance saved per unit of short selling allowed; it is positive only for assets held at zero.

These statements are checked numerically in this repository by re-solving with a slightly different right-hand side and comparing finite differences with the multipliers.

## 7. The dual function

The dual function is the best value the Lagrangian can reach when we minimise it over `w` with the multipliers held fixed:

    g(lam, gam, alpha, beta) = min over w of L(w; lam, gam, alpha, beta).

With `L = w'Sigma w - q'w + lam + gam r - cap (1'beta)`, the terms involving `w` form a convex quadratic. Setting its gradient to zero, `2 Sigma w - q = 0`, gives `w = (1/2) Sigma^{-1} q`. Substituting back:

    w'Sigma w = (1/4) q'Sigma^{-1} q,      -q'w = -(1/2) q'Sigma^{-1} q,

so

    g(lam, gam, alpha, beta) = lam + gam r - cap (1'beta) - (1/4) q' Sigma^{-1} q,      q = lam 1 + gam mu + alpha - beta.

This requires `Sigma` to be invertible; the code uses `Sigma` positive definite. For the minimum-variance problem the terms with `gam` and `r` are simply absent.

The **dual problem** is to maximise `g` over `lam, gam` (free) and `alpha, beta >= 0`.

## 8. Weak and strong duality

**Weak duality.** For any feasible `w` and any `alpha, beta >= 0`, inequality (*) gives `g(...) <= L(w; ...) <= w'Sigma w`. Taking the best feasible `w`: `g(...) <= V*`. Every dual-feasible vector therefore gives a lower bound on the optimal variance. The code draws 1,000 random dual-feasible multiplier vectors (half of them random perturbations of the optimal multipliers, so some are close to the optimum) and confirms that `g` never exceeds the primal optimum.

**Strong duality.** For a convex quadratic problem with positive definite `Sigma`, linear constraints and a non-empty feasible set, the largest lower bound equals the optimum: `max g = V*`. The maximiser is exactly the set of KKT multipliers. Numerically: evaluating `g` at the multipliers returned by the solver gives the primal optimum to machine precision, and the minimiser of the Lagrangian, `(1/2) Sigma^{-1} q`, equals the primal solution `w*` (this is stationarity). At the optimum every constraint term in `L` vanishes by complementary slackness, so `L(w*; multipliers) = V*`.

## 9. A worked example with three assets

The numbers below are generated by the code from the configuration (`sensitivity.duality.worked_example` in `config.yaml`), and a test checks that this block equals what the code produces.

<!-- worked-example:start -->
- Inputs: mu = [0.060000, 0.100000, 0.140000], cap = 0.420000, target return r = 0.110000, Sigma rows [0.040000, 0.012000, 0.006000]; [0.012000, 0.090000, 0.018000]; [0.006000, 0.018000, 0.160000].
- Optimal weights w = [0.170000, 0.410000, 0.420000] (sum 1.000000, return 0.110000).
- Optimal variance V = w'Sigma w = 0.053238.
- Multipliers: lam (budget) = -0.068300, gam (return target) = 1.613000, alpha (lower bounds) = [0.000000, 0.000000, 0.000000], beta (cap) = [0.000000, 0.000000, 0.006320].
- Stationarity: 2 Sigma w = [0.028480, 0.093000, 0.151200] and q = lam 1 + gam mu + alpha - beta = [0.028480, 0.093000, 0.151200].
- Dual function at these multipliers: g = 0.053238; primal optimum 0.053238; gap 1.4e-17.
- Lagrangian at (w, multipliers) = 0.053238, equal to V because every constraint term vanishes at the optimum.
- Complementary slackness: alpha * w = [0.000000, 0.000000, 0.000000], beta * (cap - w) = [0.000000, 0.000000, 0.000000].
<!-- worked-example:end -->

Reading the example: the cap binds on the highest-return asset (`beta_3 > 0`) and nothing else binds (every other bound multiplier is zero, so complementary slackness holds trivially). `beta_3` says how much the optimal variance would fall if that asset's cap were raised by one unit. `gam` is the slope of variance against return. The dual value equals the primal value, and the Lagrangian evaluated at the optimum equals the optimal variance.

## 10. Verification on the real data

These lines are generated by `scripts/run_sensitivity.py` (table `outputs/tables/duality_checks_sens.csv`). They cover the minimum-variance portfolio, the maximum-Sharpe portfolio and three target returns on the frontier of the 40-stock universe, plus the three-asset example.

<!-- verification:start -->
- min_variance: primal 0.01670509, dual 0.01670509, gap 0.0e+00; weak duality: largest (dual - primal) over 1000 random dual-feasible vectors -1.64e-01 (0 violations); complementary slackness 0.0e+00
- max_sharpe: primal 0.02953418, dual 0.02953418, gap 3.5e-18; weak duality: largest (dual - primal) over 1000 random dual-feasible vectors -2.47e-01 (0 violations); complementary slackness 0.0e+00
- target_25pct: primal 0.01803035, dual 0.01803035, gap 3.5e-18; weak duality: largest (dual - primal) over 1000 random dual-feasible vectors -2.06e-01 (0 violations); complementary slackness 0.0e+00
- target_50pct: primal 0.02114394, dual 0.02114394, gap 3.5e-18; weak duality: largest (dual - primal) over 1000 random dual-feasible vectors -1.81e-01 (0 violations); complementary slackness 0.0e+00
- target_75pct: primal 0.02647917, dual 0.02647917, gap 1.0e-17; weak duality: largest (dual - primal) over 1000 random dual-feasible vectors -1.96e-01 (0 violations); complementary slackness 0.0e+00
- worked_example_3_assets: primal 0.05323780, dual 0.05323780, gap 1.4e-17; weak duality: largest (dual - primal) over 1000 random dual-feasible vectors -1.95e-03 (0 violations); complementary slackness 0.0e+00
<!-- verification:end -->

## 11. Common pitfalls

* Different books use different signs and factors. If you see `L = f + lam (a(w) - b)` the multiplier `lam` has the opposite sign to ours; if the objective is written `(1/2) w'Sigma w` every multiplier is half of ours.
* The dual function needs `Sigma` to be invertible. If `Sigma` is only positive semi-definite the dual is `-infinity` unless `q` lies in the range of `Sigma`.
* When several constraints bind at once with the same slack (degeneracy), the multipliers need not be unique; the sensitivities then depend on the direction of the change. The code flags such cases (for example when the cap equals `1/N` and the feasible set is a single point).
* A multiplier is a local sensitivity. It is exact only while the set of binding constraints stays the same.
