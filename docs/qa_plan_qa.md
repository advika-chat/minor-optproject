# Independent validation (audit): plan and tolerances

Written before `scripts/run_qa.py` was run. This is an audit: no existing result, table, figure, config value or module is changed. Findings are reported with a severity and fixes are proposed, not applied. All recomputations use pandas, numpy and scipy only; no metric or solver code from `src/` is imported (data are read directly with `pd.read_parquet`; the repository's own tables are read as plain CSV for comparison).

## Severity scale

* BLOCKER: a number or claim that the report would quote is wrong.
* MAJOR: a result could change under a defensible alternative, or a safeguard is missing.
* MINOR: wording, cosmetic, stale text.

## Items

* A1 full test suite (count, failures, real exit code, wall time). A2 clean-checkout reproducibility from `git ls-files` in a temporary directory outside the repository, fresh virtual environment, `requirements.txt`, fast subset of tests. A3 repository state (clean, HEAD equals origin/main, no cache / local-instruction files tracked, ignore entries). A4 text scan of tracked files. A5 order of commits versus pre-specification and tolerance history.
* B1 data facts recomputed from `data/processed`; B2 the two large single-day moves; B3 index and equal-weight in-sample statistics.
* C1 independent SLSQP solutions of minimum variance and max-Sharpe; C2 KKT conditions of the logged solution.
* D1 independent paired bootstrap of the simulation study; D2 verdict wording; D3 oracle bound in every stored run and fresh true-Sharpe recomputation from the stored seeds; D4 moments and degrees of freedom of a large fresh draw.
* E1 metrics of every backtest method from the daily return series; E2 net-return reconstruction from stored weights; E3 look-ahead audit (ten dates) and filtered-probability audit; E4 independent circular block bootstrap; E5 identity relations; E6 stress windows; E7 multiplicity count; E8 sensitivity of the headline to the evaluation start and a one-day lag; E9 epsilon selection audit.
* F1 numbers in documents against tables; F2 figures; F3 limitations coverage. G single list of numbers for the report.

## Tolerances (also named constants at the top of `scripts/run_qa.py`)

| Constant | Value | Used for |
|---|---|---|
| `TOL_EXACT` | 1e-8 | metrics recomputed from the same daily series with the same formula (E1, E2 net identity, E5 identities, B3 vs table) |
| `TOL_DATA_COUNT` | 0 | counts of stocks, days, NaN, flagged moves |
| `TOL_LOG_ROUND` | half a unit in the last digit quoted | a logged value is accepted if it equals the recomputed value rounded to the digits quoted |
| `TOL_SLSQP_OBJ` | 1e-6 | objective / Sharpe of the SLSQP solution against the logged one (SLSQP is a different algorithm) |
| `TOL_SLSQP_W` | 2e-3 | weight difference, SLSQP versus logged (flat objective near the optimum) |
| `TOL_KKT_STAT` | 1e-6 | stationarity residual of the logged weights (these weights carry about 1e-10 solver error) |
| `TOL_KKT_COMP` | 1e-8 | complementary slackness |
| `TOL_MULT` | 5e-4 | budget multiplier against the value stated in the task text (-0.0179, four digits) |
| `TOL_BOOT_MC` | 2e-4 | simulation-study bootstrap interval endpoints (10,000 resamples, new seed) |
| `TOL_BOOT_BT_SHARPE` | 0.02 | backtest bootstrap endpoints, Sharpe differences (5,000 resamples, new seed) |
| `TOL_BOOT_BT_CVAR` | 3e-4 | backtest bootstrap endpoints, CVaR95 differences |
| `TOL_BOOT_BT_DD` | 0.01 | backtest bootstrap endpoints, drawdown differences |
| `TOL_ORACLE` | 1e-9 | oracle true Sharpe must be at least that of every method minus this |
| `TOL_REPRO_W` | 1e-5 | recomputed plug-in max-Sharpe weights against stored (the project's own look-ahead tolerance) |
| `TOL_MOMENT_Z` | 5 | standard errors, fresh Student-t draw moments |
| `TOL_SIM_PLUGIN` | 1e-4 | true Sharpe of the plug-in max-Sharpe and min-variance portfolios recomputed from a re-simulated sample (SLSQP versus the project's active-set solver; the objective is flat near the optimum) |
| `TOL_ORACLE_RECOMP` | 1e-6 | true Sharpe of the oracle recomputed by SLSQP |
| `TOL_DF` | 0.1 | estimated degrees of freedom of a fresh Student-t draw against 5 (also a Kolmogorov-Smirnov p-value above 0.001 for the Mahalanobis distance against its F(40, 5) law) |
| `TOL_IDENT_SERIES` | 5e-5 | largest daily return difference between two series that are equal in exact arithmetic but solved by different solvers (DR-MSD at epsilon 0 versus plug-in max-Sharpe) |
| `TOL_PLATEAU_SERIES` | 1e-3 | largest daily return difference between DR-CVaR at the plateau epsilon and 1/N (the plateau is defined up to 1% of the effective number of positions) |
| `TOL_RECON_SHARPE` | 2e-3 | Sharpe of a path rebuilt from independently recomputed plug-in weights (SLSQP) against the stored path |
| `TOL_DOC` | rounding to quoted digits | numbers quoted in documents |

For bootstrap items two further rules are applied regardless of the numeric tolerance: the point estimate must match to `TOL_EXACT`, and the interval must agree with the stored one in sign and in whether it contains zero. A disagreement is reported as a FAIL and discussed, not hidden. A bootstrap interval that sits within one endpoint tolerance of zero is flagged as borderline in the report.

## Method notes

* Different seeds: simulation study 424242 (config 777); backtest 91827 (config 20261031).
* Max-Sharpe by SLSQP: maximise (mu'w - rf)/sqrt(w'Sigma w) over the capped simplex from ten random starts; minimum variance likewise.
* Backtest windows follow the specification: weights at position p use the 504 daily returns ending at p-1; rebalances every 21 rows from position 505.
* "Sharpe ranking" in E8 is the order of the primary methods (plug-in max-Sharpe, 1/N, DR-MSD CV, DR-CVaR CV, regime-aware versions) by net Sharpe.
* Anything that cannot be reproduced independently is labelled "no" in `docs/report_numbers_qa.md`, not dropped.
