# Data limitations

## Survivorship bias

The universe is today's NIFTY 50 list (`data/nifty50_tickers.csv`), applied to a
window that starts in 2015. Stocks that were in the index in 2015 but were later
removed (after poor performance, mergers, delistings or demotion) are not in the
sample, while stocks that were added after strong performance are. Every stock is
therefore known, in hindsight, to have been successful enough to be a constituent
now.

Consequences:
- Historical returns are biased upward and drawdowns and tail risk are understated
  relative to what an investor choosing the index members in 2015 would have seen.
- Mean-variance and robust optimisers are especially exposed, because they
  overweight assets with high estimated returns, and those estimates are inflated
  by selection.
- The full-history filter adds to this: recently listed constituents are excluded (see `outputs/tables/data_dropped_tickers.csv` for the current list and listing dates), which favours older, established companies.
- Any in-sample or backtest figure on this data should be read as optimistic. It
  is not evidence of real out-of-sample performance.

A fix needs point-in-time constituent lists and prices for delisted stocks, which
yfinance does not provide.

## Other known issues

- **Ticker list source.** `data/nifty50_tickers.csv` is built from the official NSE constituents file (`data/raw/ind_nifty50list.csv`, retrieval date in the CSV header). Constituents change over time, so rebuild it with `python -m scripts.run_data --rebuild-tickers` whenever the NSE file is refreshed. NSE lists Tata Motors Passenger Vehicles Ltd. under symbol `TMPV`; `TATAMOTORS.NS` no longer returns data.
- **Benchmark is a price index.** `^NSEI` excludes dividends while the stocks use
  dividend-adjusted prices, so comparisons against the index favour the stocks.
- **Flagged jumps are not corrected.** Daily moves above 20% are flagged in
  `outputs/tables/data_flagged_jumps.csv` but left in the data. Some are real
  (COVID crash, Adani Feb 2023) and some may be data or corporate-action
  artefacts. They should be checked by hand.
- **Free data.** Yahoo Finance data is unaudited and can be revised between downloads.
  Raw snapshots are timestamped in `data/raw/` and not tracked by git.

## Price-data issues found in review (2026-10-30)

Each flagged move was inspected with `python -m scripts.run_data --inspect TICKER DATE`
(raw vs adjusted prices, dividend/split entries, same-day cross-section). Output:
`outputs/tables/data_jump_inspection.csv`. No price data has been edited or removed.

### ADANIENT.NS, 2015-06-03 (-38.7%): corporate action, adjustment unreliable
- Evidence in the data: Open 312.39 and High 312.39 against a prior close of 97.64, then
  Close 59.80; Adj/Close ratio is constant (0.9417) so no adjustment step sits at this
  date; yfinance reports no dividend or split in the window; the index fell 1.2% and the
  median stock 1.0% that day; the lower price persists afterwards.
- External evidence: Business Standard headlines found by web search ("Adani Enterprises
  shares fall on demerger", "Why the 80% drop in Adani Enterprises is justified", search
  summaries report 4 June 2015 as the record date and a fall of over 80% on 3 June). I could
  not open the articles (HTTP 403), so this rests on the search summaries.
- Reading: type (b), a demerger ex-date. The series shows -38.7%, not the roughly -80%
  reported, and has an impossible Open, so the adjustment around this date is inconsistent.
  Confidence the move is not an economic loss: fairly high. Confidence in any correction
  factor: low, it cannot be derived from this data alone.
- Related, not acted on: a business-standard headline on search ("Adani Enterprises trades
  ex-date for spin off", 2018-09-06) coincides with the +20.9% flagged on that date, so that
  move may also be a corporate-action artefact.

### TRENT.NS, 2026-01-01 (-33.0%): unadjusted bonus issue, adjustment mis-dated
- Evidence in the data: Adj/Close constant (0.9985), no actions reported by yfinance,
  index +0.06% and median stock +0.5% that day, normal volume, level persists. The ratio
  2864.93 / 4279.00 = 0.6695 is close to 2/3.
- External evidence: Business Today (2026-06-04, read in full) reports a 1:2 bonus issue,
  record date revised to 2026-06-04, shares settled at Rs 4,258.10 on 2026-06-03 and opened at
  Rs 2,833.45 on 2026-06-04 after adjustment, and that some apps showed a ~33% fall because
  they displayed unadjusted prices. In our series the 2026-06-03 close is already 2,834, so
  prices from 2026-01-01 onward are bonus-adjusted while prices before 2026-01-01 are not.
- Reading: type (b)/(c). The -33% is not a real move; the adjustment boundary in the source
  series sits at 2026-01-01 instead of 2026-06-04. Confidence: high.

### TMPV.NS, 2025-10-14 (-40.2%): demerger, history not adjusted
- Found while checking the replacement for TATAMOTORS.NS, not in the original review list.
- Evidence in the data: Open 400.00 vs prior close 660.75, no actions reported, Adj/Close
  constant (0.9918), index -0.3%, median stock -0.4%, volume 52M vs 33M the day before;
  history runs from 2015-01-02 (price 493), i.e. the pre-demerger Tata Motors series.
- External evidence (web search summaries from several outlets, not the primary filing):
  demerger effective 2025-10-01, record date 2025-10-14, 1:1 entitlement to shares in the
  commercial-vehicles company. The official NSE file only gives the name and symbol.
- Reading: type (b). The series spans the demerger without adjustment, so returns before
  and after 2025-10-14 describe different businesses. Confidence: high that the move is
  a corporate action; the correct factor is not verified (one blog cites a 68.85% cost split,
  unconfirmed).

### Decisions (2026-10-30): all three stocks excluded
- `ADANIENT.NS`, `TRENT.NS` and `TMPV.NS` are excluded from the universe through the
  config key `data.excluded_tickers` (each entry has a reason string). Price series are
  not edited or corrected; the stocks are simply removed before alignment.
- Reasons, by stock:
  - `TMPV.NS`: pre-demerger history is unadjusted (-40.2% on 2025-10-14) and no verified
    adjustment factor exists.
  - `ADANIENT.NS`: suspect corporate-action artefacts on 2015-06-03 (-38.7%, impossible Open,
    reported demerger ex-date) and possibly 2018-09-06 (+20.9%, search headline mentions a
    spin-off ex-date); no verified factor.
  - `TRENT.NS`: the source's 1:2 bonus adjustment is placed at 2026-01-01 instead of the
    reported 2026-06-04 ex-date (-33.0% on 2026-01-01). Hand-correcting the prices was
    rejected.
- They appear in `outputs/tables/data_dropped_tickers.csv` with the reason
  "data-quality exclusion: unadjusted corporate action". The evidence is kept in
  `outputs/tables/data_jump_inspection.csv`.
- Evidence limits: the ADANIENT and TMPV external evidence comes from web-search summaries
  of news articles (the ADANIENT articles returned HTTP 403 and could not be opened), not
  from exchange filings. The TRENT article was read in full.

## Consequences of the final universe

- **Final universe: 40 stocks.** Of the 50 NSE constituents, 3 were excluded for data quality
  and 7 lack a price on 2015-01-02 (BSE, ETERNAL, HDFCLIFE, INDIGO, JIOFIN, MAXHEALTH,
  SBILIFE); see `outputs/tables/data_dropped_tickers.csv`.
- **The 2023 Adani tail event is no longer in the sample.** `ADANIENT.NS` fell 28.2%, 26.7%
  (2023-02-01, 2023-02-02) and rebounded 20.0% (2023-02-08), roughly -60% from 2023-01-24 to
  2023-02-28 on the source prices, while the index fell about 4.5% over the same dates.
  Those 2023 returns are probably sound, but the stock is excluded because of its earlier
  artefacts, so the dataset has lost its clearest single-name crash. `ADANIPORTS.NS`
  (about -22% over the same dates) remains and carries part of the episode. Tail-risk
  estimates (CVaR, Wasserstein radius calibration, regime detection) will understate
  single-name stress relative to the full index. After the exclusions only 5
  stock-days remain below -20%, the largest being -27.9% (AXISBANK, 2020-03-23).
- **Tata Motors is absent from the sample.** NSE now lists it as `TMPV` only, and `TMPV.NS` is
  excluded; `TATAMOTORS.NS` returns no data. The old Tata Motors series cannot be used
  without a verified demerger adjustment. The universe therefore lacks one of the index's
  large automobile names across the whole window, which biases sector composition and
  correlations relative to the real index.
- **IndusInd Bank, Wipro and Hero MotoCorp are not in the current NSE list.** They were in
  the earlier list used for the first runs but are absent from the official file. I did not
  verify when or why they left the index. Their absence adds survivorship bias beyond the
  general effect described above, because they are exactly the names that are no longer
  deemed index-worthy. Over 2015-01-02 to 2026-10-29 on the raw adjusted prices (computed
  from the first raw snapshot, which still holds them):

  | Stock | Annualised mean | Annualised vol | Price return over window | Worst day |
  |---|---|---|---|---|
  | INDUSINDBK | 9.0% | 39.8% | +13.7% | -27.2% (2025-03-11) |
  | WIPRO | 9.8% | 25.8% | +110.9% | -9.2% (2024-07-22) |
  | HEROMOTOCO | 11.2% | 27.7% | +135.8% | -11.0% (2020-03-23) |

  The final universe has a median annualised mean of 17.0% and a median compounded price
  return of about +353%. All three leavers sit well below that median, so excluding them
  makes the sample look better than a 2015 investor holding today's list plus these names
  would have seen. IndusInd also carries the heaviest tail in the earlier sample, which is
  now gone. The earlier first-run figures also had a few other index changes (BSE, INDIGO,
  MAXHEALTH, TMPV added) that do not reach the final universe because of history length
  or exclusion.

## Regime detection: delay and flicker (2026-10-30)

Regime labels (`src/regimes.py`) use data up to and including each date, so the label of date t can only drive the portfolio held from t+1. Any use of the label for the return earned on date t is look-ahead. All three methods are causal, and the shift tests in `tests/test_regimes.py` check that appending future data does not change earlier labels. Causal labels still lag, and the labels are noisy.

Measured on one episode, the COVID crash (index peak 2020-01-14, first -10% from peak 2020-03-06, first -20% 2020-03-12, trough 2020-03-23, back within 10% of the peak 2020-07-21). Start and end of the continuous crisis spell that contains the trough:

| Method | Spell starts | Start vs peak | Start vs first -10% day | Spell ends | End vs trough |
|---|---|---|---|---|---|
| vol_threshold | 2020-02-03 | +14 trading days | 23 days before | 2020-07-22 | +81 trading days |
| hmm2 | 2020-02-24 | +28 | 9 days before | 2020-04-13 | +12 |
| hmm3 | 2020-03-06 | +37 | on the -10% day | 2020-04-13 | +12 |

Reading, with the limits of a single episode:
- The volatility-threshold method flags crisis early only because volatility was already above its past 67th percentile in early 2020 (the figure shows frequent crisis labels in 2018 and 2019 as well). It stays in crisis for about four months after the trough, until the index has recovered, which is consistent with an expanding-quantile threshold and a 21-day window that stays elevated; I did not test that explanation separately.
- The 3-state HMM flags crisis only once the index is already about 10% below its peak, and its crisis state is short-lived (mean spell 3.2 days, 125 crisis days in total), so it carries little information about sustained stress.
- Labels flicker: the volatility method has 201 spells over 2,638 labelled days, hmm3 has 268 over 2,406. On the 2,406 days labelled by all three methods, vol_threshold and hmm3 agree exactly on 56.5% of days. On the calm / not-calm split the agreement is 82.4% (vol_threshold vs hmm3), 62.1% (hmm2 vs hmm3) and 56.7% (vol_threshold vs hmm2). Regime-dependent decisions will therefore depend strongly on the method chosen.
- The first 252+21 days (volatility method) and 504 days (HMM) are unlabelled, so any regime-aware backtest has a shorter usable sample than the full data.
- HMM fits are on benchmark returns only, with parameters refitted every 21 days. All 115 refits of each HMM converged on the best restart in this run; that is a result for this data, not a guarantee.

## Eps grid extension and selection rules (2026-10-30)

The eps grid was extended after observing edge solutions. In the first walk-forward run (13 log-spaced values, 1e-5 to 0.1) the pooled and several per-regime choices sat at the upper end of the grid. The extension was decided within the walk-forward cross-validation and before any backtest, and it does not use realised performance: the end point was found from the effective number of positions 1/sum(w^2) of the DR-CVaR weights in each of the 126 training windows (`scripts.run_eps_selection --find-grid`, table `outputs/tables/eps_grid_scan.csv`). At 0.1, 4 of 126 windows were still below 99% of N (minimum ratio 0.975); at the next log step, 0.2154, all windows were exactly 1/N. One value was therefore added. The new grid is a superset of the old one, and no values beyond 0.2154 were added because they would give the identical portfolio.

The original-grid results are kept (`outputs/tables/eps_*` without suffix, and the earlier log entry). The extended results carry the suffix `_ext`. Restricting the extended run to the original grid reproduces the original selections; for hmm3, 4 of 168 rows differ in eps only through float-level ties (|Sharpe difference| at most 7.5e-8, below the 1e-6 tie tolerance), where the earlier tie-break picked an arbitrary one of numerically identical portfolios and the current one keeps the smallest eps.

Selection rules, both reported. "best": highest net Sharpe (ties: lower CVaR, then smaller eps). "one_se": the smallest eps whose Sharpe is within one standard error of the best. The standard error is a leave-one-block-out jackknife of the pooled Sharpe over the validation blocks. It treats blocks as exchangeable units and ignores dependence between blocks, which may be autocorrelated (volatility clustering, regime persistence, overlapping 252-day training windows), so it is probably too small. It is the SE of one eps's Sharpe, not of a difference between two eps values.

Limits of what this shows:
- The jackknife SE of the best eps's Sharpe is between 0.38 and 1.28 across the regimes and methods on the all-blocks curves, which is as large as or larger than the Sharpe differences across most of the grid. The validation blocks therefore cannot separate most eps values, and the one-SE rule, which prefers the smallest eps within the band, selects very small eps in most cases by construction.
- No significance test was run. The results do not show that a regime-dependent eps beats a fixed eps, or that any eps beats 1/N or the empirical (eps near 0) solution out of sample.
- Several regimes have few blocks (hmm3 crisis: 9, hmm2 crisis: 17), so their per-regime choices are noisy and often fall back to the pooled eps.

## Two regimes and a continuous stress level (2026-10-30)

Calm/stress regimes plus a continuous stress level s_t were adopted because the three-regime crisis buckets had too few validation blocks (hmm3 crisis 9, hmm2 crisis 17) and the per-regime eps results conflicted across methods. The change was decided before any backtest, and the stress threshold 0.5 was fixed in `config.yaml` (section `regimes_stress`) before running. Other thresholds are reported as a sensitivity only: stress is 18.3% of labelled days at 0.3, 15.4% at 0.5 and 12.2% at 0.7. Results are in the files with suffix `_2reg`; earlier outputs are unchanged.

What the results show and do not show:
- s_t is the filtered probability of the higher-variance state of the 2-state HMM. It is defined from 2017-01-25 (504-day burn-in), so any backtest using it loses the first two years of the sample.
- s_t and the volatility-percentile cross-check agree only moderately: Pearson 0.531, Spearman 0.455, and they differ by more than 0.5 on 25.7% of days. They measure different things.
- The two hard-label methods have very different base rates: hmm2_stress labels 15.4% of days as stress, vol_two 57.7% (its calm bucket is only the lowest-volatility third by construction, and stress is the union of the earlier volatile and crisis buckets). No hmm2_stress day is labelled calm by vol_two. The two are not the same regime concept.
- COVID: s_t first exceeds 0.5 on 2020-02-03, 14 trading days after the market peak of 2020-01-14, is 1.0 at the trough (2020-03-23) and stays above 0.5 until 2020-04-13, 12 trading days after the trough. 2022 (peak 2022-01-17, trough 2022-06-17, -16.5%): s_t exceeds 0.5 on only 22 of 248 days, while vol_two labels 201 of 248 days as stress. The HMM variance state flags sharp spikes much more readily than a slow decline; I did not test that explanation.
- The stress regime still has few blocks under hmm2_stress (17 at the final refit), and its eps choice fell back to the pooled eps at 11 of 42 refits (plus 2 early refits with no usable pooled choice); vol_two stress has 71 blocks and fell back at 5 of 42 (plus 2).
- The eps conclusions still conflict across the two methods. At the final refit under the best-value rule: calm 0.1 in both; stress 0.001 under hmm2_stress (17 blocks, jackknife SE of the best Sharpe 1.28) against 0.1 under vol_two (71 blocks, SE 0.52). The eps is identical across the two regimes at 0 of 29 refits for hmm2_stress and 10 of 35 for vol_two (best-value rule), and at 26 of 29 and 15 of 35 under the one-SE rule, which prefers the smallest eps by construction.
- The soft series eps_t = eps_calm + s_t (eps_stress - eps_calm) is exploratory. Its negative correlation with s_t (about -0.49 and -0.64 under the best-value rule) arises mostly because the selected stress eps is below the calm eps on 65% and 50% of days. No performance comparison was made.
- No significance test was run. Nothing here shows that regime-dependent eps beats a fixed eps, or that either regime definition identifies periods in which a different eps helps.

## Own QP solver: scope and assumptions (2026-10-30)

`src/solver_own.py` solves the long-only, capped, fully invested mean-variance problem with a primal active-set method (numpy only) and finds the max-Sharpe portfolio by golden-section search over the target return. Its scope and assumptions:

- **Feasible target range.** P(r) is defined for target returns between r_min and r_max, which are the smallest and largest returns achievable under the cap, computed exactly by filling assets up to the cap in order of expected return. On the real data (cap 0.10, 40 assets) the range is 0.1109 to 0.2583. A target outside it gets the status `infeasible_target`; a cap below 1/N gets `infeasible_cap`. The efficient frontier starts at the minimum-variance return (0.1323); targets between 0.1109 and 0.1323 are feasible but inefficient.
- **Sharpe unimodality is an assumption, not a theorem here.** Golden-section search is only guaranteed to find the maximum if the Sharpe ratio is unimodal in the target return between the minimum-variance return and r_max. I verified it numerically on the real data only (a 1000-point grid: one interior peak, no valleys; rf 0.065 is below the minimum-variance return 0.1323). It can fail when rf is above the minimum-variance return, which the solver flags. A different rf, cap or universe needs the check repeated (`scripts/run_solver_own.py` does it). Because the Sharpe ratio is flat at its maximum, the search pins the maximising return only to about the square root of machine precision; the max-Sharpe weights agree with `src/markowitz.py` to 2.8e-7 here.
- **Numerical limits.** If Sigma is nearly singular the optimal weights are poorly determined even though the objective is accurate (the near-singular check differs from cvxpy by 3.2e-2 in weights at an objective of 6e-12). If Sigma is exactly singular, for example a duplicated asset, the split of weight between identical assets is not unique and neither are the multipliers. If cap equals 1/N the feasible set is a single point and the multipliers are not unique. The active-set method takes a Newton step only when the projected gradient exceeds `projected_gradient_tol`, because the step itself is noisy when Sigma is ill-conditioned.
- **What the checks compare against.** cvxpy at its default tolerance is coarse in absolute terms (gap 1e-8), which matters when the optimal variance is about 1e-5 or smaller. The reference solves therefore use a tolerance of 1e-12 (`solver_own.cvxpy_solver_tol`); the agreement thresholds were fixed beforehand and not loosened. The existing `src/markowitz.py` min-variance weights differ from the exact optimum by up to 3.9e-5 for the same reason (its variance is 4.1e-10 above the own solution's); that module is unchanged. For the near-singular case the relative objective comparison was replaced by a covariance-scaled absolute one, a decision made after the relative check failed (the own solution was KKT-exact and lower).
- **Coverage.** Only the constraint set above is supported. Random checks use N from 3 to 40; speed was not benchmarked.

## Sensitivity analysis: scope and caveats (2026-10-30)

Everything in `scripts/run_sensitivity.py` is in-sample: parameters are estimated on the same 2015-2026 data that the portfolios are then evaluated on, and re-estimating on sub-periods or calendar years is not a backtest. No out-of-sample performance is computed there; that belongs to the backtest task.

- **mu draws.** The sampling distribution N(mu_hat, k^2 Sigma / years) treats daily returns as independent with a stable covariance, so "one standard error" is a stand-in under that model, not a measured uncertainty. 1,000 draws were used (an earlier run used 220, a number set by a run-time budget, not by statistical need; its first 220 draws are identical to the first 220 of the final run, and the final values lie within the earlier run's bootstrap 95% intervals). The Monte Carlo error of each headline statistic is given by a seeded bootstrap over the draws: standard errors of 0.003 to 0.009 for the median L1 weight change, 0.002 to 0.003 for the share of positions dropped and 0.002 or less for the Sharpe under the base mu. This quantifies draw-to-draw noise only, not the model assumptions above, and the summaries are descriptive, not significance tests. The estimation-error loss is reported in two ways (truth = the base mu, and truth = the draw) because the wording of the request admits both readings.
- **One-asset responses are secants, not derivatives.** A +1 standard-error bump to a single mu_i sends the most sensitive assets straight from weight 0 to the cap (0.100), so the response is limited by the cap and the "finite difference" is taken over a large step. The median L1 response of 0.19 is close to twice the cap, i.e. one asset enters at the cap and another leaves.
- **Cap shadow prices.** At cap = 1/N (0.025) the feasible set is a single point and the multipliers are not unique; the value printed for that cap (1.11 for min-variance) is one valid choice, not a unique sensitivity. The finite-difference identity was checked at 22 of 24 (cap, solution) pairs; the other 2 were skipped (that single-point case, and a changed active set or non-unique multipliers). Multipliers are local sensitivities that hold only while the set of binding constraints stays the same.
- **Sigma sweeps.** Full shrinkage to the scaled identity (delta = 1) removes all correlation, so the resulting minimum-variance volatility of 0.047 and max-Sharpe Sharpe of 2.73 are artefacts of an unrealistically diversified matrix, not findings. The Ledoit-Wolf shrinkage estimated on these returns is small (0.0135), and the sample covariance is well conditioned (condition number 53). `src/robust.py` only has the scaled-identity target (through its Ledoit-Wolf estimator); the constant-correlation target was defined in `src/sensitivity.py`.
- **Periods.** The windows for 2015, 2016 and 2026 have rf above the minimum-variance return, where the Sharpe-unimodality assumption of the golden-section search could fail; it was checked in each of them (unimodal, and the golden-section result matches a 1000-point grid). 2026 is a partial year (203 days, to 2026-10-29) with a negative mean return, and calendar years of 200 to 250 days for 40 assets give noisy estimates. Max-Sharpe weights change a lot between consecutive years (mean L1 distance 1.38 of a possible 2; min-variance 0.82).
- **Scenario matrices.** Max-Sharpe has zero regret in all six scenarios, so this scenario set contains no trade-off between the four portfolios. A plausible reason, which I did not test, is that the scenarios shift mu, volatility, correlation or rf uniformly and none of them reorders the assets' expected returns, which is what the mu draws do. The matrices are inputs for the game-theory task; no decision rule is chosen.

## Game theory: scope and caveats (2026-10-30)

- **Both payoff matrices are degenerate.** In the saved six-scenario matrix and in the new era matrix, max-Sharpe has the highest Sharpe ratio in every column, so its regret row is all zeros, Wald maximin and Savage minimax regret choose the same portfolio, and both mixed-strategy programs return the pure strategy "max-Sharpe against the column where it is worst" (a pure saddle point). Nature's mixed strategy in the regret game is not unique (every column gives regret zero); the one reported is an arbitrary optimal choice. The only Wald-versus-Savage disagreement in the project is the constructed A, B, C teaching example. A matrix with a real trade-off needs a different portfolio or scenario set.
- **The era evaluation is in-sample for each era.** All portfolios are computed once on the full sample, so each era's data was part of their estimation sample. The era matrix illustrates the decision rules; it is not a performance claim.
- **Ellipsoid game.** Sigma_mu = Sigma / T assumes independent returns, as in the sensitivity analysis. Nature's move is small relative to the weights' freedom because the cap binds: from kappa = 0 to 2 the portfolio changes by an L1 distance of 0.29 (13 to 16 positions, effective N about 11.4) and loses only 0.0034 of plug-in objective under mu_hat, while the saddle value falls by 0.099, so the price of robustness is mostly Nature's pessimism about the mean (a lower mean of 0.096 for the held portfolio), not a different portfolio. The own-engine solution is checked against an independent min-max program (agreement 4e-12 or better for kappa > 0; 4.8e-9 at kappa = 0, which is that reference solver's tolerance, since a min-max cannot lie below the max-min) and against the second-order-cone program in `src/robust.py`. That program runs at the default solver tolerance and is slightly less accurate: its weights differ from the own solution by up to 9.8e-6 and its value is lower by about 4e-10. I therefore relaxed the weight-comparison tolerance from 1e-5 to 1e-4 after seeing this and added the check that the own value is at least the reference value. The additional kappa points on the price-of-robustness curve (0.25, 0.75, 1.5, 3, 4) come from that second-order-cone program only and are flagged in the table.
- **Wasserstein bounds.** The equality of upper bound (dual LP), closed form and constructed distribution (agreement at most 3e-14) shows Nature's best response and the inner maximum for the given portfolios; it does not show that the DR-CVaR portfolio is a best response to Nature's constructed distribution, which a full saddle-point statement would also need, and that was not checked. The support is R^m with no lower bound on returns; the construction moves one asset's return by eps/alpha (at most 0.2 for eps = 0.01) and the lowest daily return in the sample is -0.279, so no moved return in the tested range would go below -1, but this was reasoned from those two numbers, not checked point by point. The DR-CVaR portfolios were computed on the full sample (2,910 days); radii are in daily-return units. Random Nature strategies reach at most about 0.5% of the possible CVaR increase, which shows only that random moves are weak lower bounds.

<!-- montecarlo-limitations -->
## Monte Carlo with known truth (scripts/run_montecarlo.py)

* The results depend on the assumed truth: the shrink factor s for the mean vector (primary 0.5; 1.0 and 0.0 are sensitivity runs) and the
  Student-t(5) return distribution. A different truth can reverse the ranking of methods.
* Simulated returns are independent over days. Real returns show volatility clustering and regime changes, which this study does not represent.
* The epsilon of each run is chosen by five-fold contiguous-block cross-validation, valid for independent returns. This differs from the walk-forward
  cross-validation used on the real data, so the two chosen-epsilon distributions are not directly comparable.
* The risk-aversion weight theta of the DR mean-variance objective is calibrated on the true parameters (so that the oracle mean-variance portfolio equals the oracle
  max-Sharpe portfolio). The plug-in max-Sharpe rival does not use the truth, so the comparison favours the DR objective in this one respect.
* The objective in `src/wdro.py` has no mean term, so the study adds one (equal to the original at theta = 0).
* Reproducibility holds to solver accuracy, not bit for bit: in a diagnostic re-run of 60 stored (T, run) pairs, 25 chose a neighbouring epsilon because the
  out-of-fold Sharpe values were tied to within 2.7e-6; the true Sharpe of the chosen portfolio changed by at most 7.3e-8 (the H1 effect is about 5e-3).
* With this objective the 1/N portfolio is reached only at epsilon of about 2 to 5, above the largest value of the pre-specified grid; in the primary study the chosen portfolio is
  therefore never exactly 1/N. The `ext_grid` scenario, added after a weights-only probe, extends the grid to show the plateau.

<!-- backtest-limitations -->
## Rolling-window backtest (scripts/run_backtest.py)

* **Survivorship.** The universe is the 40 stocks that are in the current index and have full history; stocks that left the index, or were excluded for unadjusted corporate actions, are absent, which flatters every method.
* **Price index.** The benchmark is the NIFTY 50 price index; it excludes dividends, so its return is understated against the net-of-cost total-return portfolios (whose stock returns are also price returns, so no method receives dividends either).
* **Risk-free rate.** 6.5% a year is an assumption applied to the whole period.
* **Cost model.** A flat 10 bps of turnover, no market impact or spread dependence, no taxes; trading happens at the prior close.
* **Inner-window mismatch.** The walk-forward epsilon selection validates portfolios fitted on 252-day windows (`backtest.rolling_window_days`), while the backtest windows are 504 days; the selected epsilon is therefore tuned for a different estimation window than the one it is applied to (the 252-day sensitivity matches).
* **Validation objective.** `eps_for_date` validates with DR-CVaR portfolios; for DR-MSD the same rule is applied to DR-MSD validation blocks, and the literal reading (epsilon from the DR-CVaR blocks) is a sensitivity.
* **Few crisis episodes.** The sample holds one crash (February to April 2020) and one slow decline (2022); regime-specific conclusions rest on those few episodes.
* **HMM barely flags 2022.** The two-state hidden Markov model labels about 22 of 248 days of 2022 as stress, so the regime-aware portfolios are in their stress setting for little of that year.
* **theta_hat.** The mean weight of DR-MSD is estimated from the in-sample max-Sharpe ratio of each window, which is itself noisy (floored at 0.25); there is no truth to calibrate against.
* **Truncated epsilon grid.** For DR-MSD the 1/N portfolio lies above the largest grid value (0.2154), so the primary DR-MSD cannot reach 1/N; an extended-grid sensitivity is reported.
* **Bootstrap.** The circular block bootstrap treats 21-day blocks as exchangeable and cannot represent dependence between blocks or regime persistence beyond a block; the intervals are approximate, and many comparisons are shown without multiplicity correction.
* **One path.** A single historical path is a single draw; the backtest is descriptive evidence and does not by itself confirm or refute a hypothesis.
* **Tolerance changes after the first backtest run.** The look-ahead and reproducibility weight tolerances were loosened from 1e-6 to 1e-5 and the 1/N turnover ceiling raised from 0.05 to 0.10 after three checks failed narrowly; the causes (interior-point solver noise of 2e-6 to 3e-6 in DR-MSD weights; a guessed ceiling below the correct 0.0518) were established before the change and are recorded in `config.yaml` and `docs/results_log.md`.
* **Epsilon at the grid edge.** The cross-validated DR-MSD epsilon equals the largest grid value at 82 of 115 rebalances, so the grid constrains the primary DR-MSD; the extended-grid sensitivity is the check.
