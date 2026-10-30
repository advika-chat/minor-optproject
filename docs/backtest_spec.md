# Rolling-window backtest: pre-specification

Written and committed to `config.yaml` (section `backtest`) before the backtest was run. Real data: the 40 surviving NIFTY 50 stocks, daily simple returns, 2015-01-05 to 2026-10-29. Hypotheses: `docs/hypotheses.md` (H1 secondary metrics, exploratory regime-aware comparison). The earlier simulation study (`docs/montecarlo_notes.md`) found H1 falsified and H2 not supported on a simulated truth; the backtest is reported exactly as it comes out, whatever the outcome.

## Protocol

* Training window: 504 trading days (main), 252 (sensitivity). Weights chosen at day t use data up to t-1 only.
* Rebalance every 21 trading days. Between rebalances weights drift with returns. Turnover at a rebalance is sum |w_target - w_drifted_before_trade|; the first rebalance starts from cash (turnover 1). Cost = 10 bps x turnover, deducted from the return of the rebalance day.
* Long-only, fully invested, 10% cap. Risk-free rate 6.5% a year (ASSUMPTION): Sharpe = annualised mean excess return over rf/252 divided by annualised standard deviation, 252 days. CVaR level 0.95.
* Common evaluation start: the first rebalance position at which every primary method is available (504-day window, labelled stress level from the HMM, which needs 504 returns). The same start and the same dates are used for every method in every table.
* Benchmarks: 1/N (rebalanced on the same dates, same costs) and the NIFTY 50 PRICE index, labelled "NIFTY 50 price index (excludes dividends)", no costs.

## Methods (all net of costs, same dates)

1 plug-in max-Sharpe; 2 plug-in minimum variance; 3 1/N; 4 NIFTY 50 price index; 5 Ledoit-Wolf covariance with plug-in mean, max-Sharpe; 6 Michaud resampling; 7 worst-case box; 8 worst-case ellipsoid; 9 plug-in CVaR minimisation (linear program); 10 Wasserstein DR-CVaR (existing linear program), epsilon by cross-validation; 11 DR mean-standard-deviation (DR-MSD), epsilon by cross-validation; 12 DR-MSD and DR-CVaR at epsilon = 0 (ablation); 13 both at every fixed epsilon of the grid (price-of-robustness curve; the best fixed epsilon in hindsight is a diagnostic labelled as such); 14 regime-aware versions (epsilon per calm/stress regime); 15 exploratory soft version eps_t = eps_calm + s_t (eps_stress - eps_calm).

* **DR-MSD** (primary DRO for H1): minimise sqrt(w' Sigma_n w) + eps ||w||_p - theta (mu_n'w - rf/252) over the capped simplex (daily units, Sigma_n the 1/T covariance of the window, p dual to the ground norm). There is no truth in a backtest, so theta is estimated inside every window: theta_hat = sqrt(252) / max(S_hat, S_floor), S_hat the in-sample annualised max-Sharpe of that window (plug-in, capped) and S_floor = 0.25. This was fixed before any backtest result was seen.
* **DR-CVaR** (secondary DRO): the existing linear program (`src/wdro.py`, kind cvar).
* **Epsilon grid**: the 14-point extended grid `eps_selection_ext`, unchanged. For DR-MSD the 1/N plateau lies above this grid (simulation study), and the primary run does not extend it. One disclosed sensitivity run appends the simulation values 0.5, 1, 2, 5, 10.
* **Epsilon rule**: primary = best out-of-fold value (highest net Sharpe, ties to the smaller epsilon) from the existing walk-forward selection (`eps_selection`: 21-day validation blocks, refit every 63 days, per regime with pooled fallback). One-SE rule: sensitivity only. Regime labels: `hmm2_stress` (two regimes, filtered stress probability > 0.5) primary; `vol_two` secondary. The primary regime method is not changed after seeing results.
* **How epsilon is validated.** `eps_for_date` as built validates epsilon with DR-CVaR portfolios (`eps_selection.kind = cvar`) on 252-day training windows inside the validation blocks, whatever the objective being tuned, and the main window here is 504. This is kept for DR-CVaR. For DR-MSD the same selection rule is applied to validation blocks built with DR-MSD itself (same blocks, cadence, costs and rule; theta_hat estimated in each 252-day inner window), because choosing the radius of one objective by another objective's performance is a mismatch. As a sensitivity, DR-MSD is also run with the epsilon chosen by the DR-CVaR blocks (the literal reading). The inner-window mismatch (252 inside, 504 outside) is disclosed, not changed.
* **Worst-case box**: the mean-variance form adopted in `robust.py` is always feasible; if a solve fails on a date, the previous weights are carried forward and such dates are counted.
* Michaud: 500 draws (`robust.n_draws`); reduced only if the pilot says the budget is exceeded, and disclosed.

## Metrics and tests

* Per method: annualised net return (arithmetic mean x 252, and CAGR), volatility, Sharpe, maximum drawdown, CVaR95 of daily net returns (daily loss units), average turnover per rebalance (first rebalance excluded), Calmar (CAGR / |max drawdown|), average effective number of positions (1 / sum w^2), total transaction cost.
* Circular block bootstrap (blocks of 21 days, 5000 resamples, seed in config) on the paired daily net-return series: 95% percentile intervals for the Sharpe ratio, and for differences against plug-in max-Sharpe and against 1/N in Sharpe, CVaR95 and maximum drawdown, for primary DR-MSD, primary DR-CVaR and the primary regime-aware versions.
* Sub-periods, fixed by date regardless of regime labels: full sample, 1 Feb to 30 Apr 2020, calendar 2022 (cumulative return, maximum drawdown, CVaR95 per method).
* Sensitivities, each its own table: window 252; one-SE rule; vol_two labels; DR-MSD with epsilon from DR-CVaR blocks; extended grid for DR-MSD.

## What counts as evidence

* **H1 secondary metrics** (CVaR95 and maximum drawdown of the primary DR-MSD versus plug-in max-Sharpe) are reported with point estimates and block-bootstrap intervals. H1 itself was tested in the simulation study; the backtest is a single historical path and is descriptive evidence only.
* An interval that excludes zero is reported as such; an interval that includes zero is not a finding. No multiple-testing correction is applied, and the many comparisons are listed in the tables, not selected from.
* **No regime-aware claim is confirmatory.** The regime-aware versus static comparison (stress-window CVaR95 and full-sample Sharpe, against the cross-validated static epsilon and against the hindsight best static epsilon) is exploratory. Hindsight quantities use future information and are upper reference points, not strategies.
* The bootstrap treats blocks as exchangeable; 2020 and 2022 are few crisis episodes.
