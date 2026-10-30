# Rolling-window backtest on the NIFTY 50 data

Specification (written before the run): `docs/backtest_spec.md`. All parameters: `config.yaml`, section `backtest`.

## What was done

Every 21 trading days, from the common evaluation start, each method builds long-only, fully invested portfolios with a 10% cap from the previous 504 trading days
(252 in a sensitivity run). The weights are held, drifting with returns, until the next rebalance. Trading costs are 10 basis points of turnover, charged on the
rebalance day, so every series below is net of costs. The NIFTY 50 price index is shown without costs and without dividends. The risk-free rate is 6.5% a year
(an assumption); the Sharpe ratio is the annualised mean excess return over rf/252 divided by the annualised standard deviation of daily net returns.

## Methods

Plug-in max-Sharpe and minimum variance, 1/N, Ledoit-Wolf max-Sharpe, Michaud resampling, worst-case box and ellipsoid, plug-in CVaR (linear program), and two Wasserstein
distributionally robust portfolios: DR-CVaR (the existing linear program) and DR-MSD, which minimises sqrt(w' V w) + eps ||w||_p - theta (m'w - rf/252), where V and m are the
covariance (1/T) and mean of the daily returns in the window. theta is estimated in every window as sqrt(252) / max(S, 0.25), with S the window's in-sample max-Sharpe ratio.
The radius eps comes from the existing walk-forward selection (21-day validation blocks, refit every 63 days, pooled across regimes or per regime of a two-state hidden Markov
model of NIFTY volatility). The regime-aware versions use the epsilon of the regime in effect on the previous day; the soft version blends the two regimes' epsilons with the
filtered stress probability. Epsilon values are in daily-return units.

## Reading the tables (suffix `_bt` in `outputs/tables`)

* `backtest_summary`: annualised return (mean x 252, and compound rate), volatility, Sharpe, maximum drawdown, CVaR95 of daily net returns (a loss, as a positive number), Calmar ratio,
  average turnover per rebalance (the first purchase from cash is excluded), average effective number of positions, total cost.
* `backtest_bootstrap`: 95% intervals from a circular block bootstrap (21-day blocks) of the paired daily series. "versus" rows are differences (method minus comparator); for
  maximum drawdown a positive difference means a shallower drawdown; for CVaR95 a negative difference means a smaller loss.
* `h1_secondary`: the CVaR95 and maximum-drawdown rows of the bootstrap table for the primary DR portfolios against plug-in max-Sharpe.
* `stress_windows`: cumulative return, maximum drawdown and CVaR95 inside 1 Feb to 30 Apr 2020 and calendar 2022, fixed by date.
* `price_of_robustness`: one row per fixed epsilon (a hindsight view: the best fixed epsilon uses information that was not available at the time).
* `regime_exploratory`: regime-aware portfolios against the cross-validated static epsilon and the hindsight best static epsilon; exploratory, never a confirmed result.
* `sensitivities`: window 252, one-SE rule, the other regime labels, DR-MSD with epsilon from the DR-CVaR validation blocks, and DR-MSD with the extended grid.

## Results

<!-- results:start -->
Common evaluation start: 2017-01-27 (rebalance row 505); evaluation to 2026-10-29; 115 rebalances.

Headline (net of costs):

| label | sharpe | max_drawdown | cvar95_daily | avg_turnover | ann_return | ann_vol |
|---|---|---|---|---|---|---|
| 1/N | 0.7680 | -0.3628 | 0.0236 | 0.0518 | 0.1891 | 0.1615 |
| plug-in max-Sharpe | 0.7443 | -0.3114 | 0.0242 | 0.3206 | 0.1903 | 0.1683 |
| DR-CVaR (CV epsilon) | 0.6794 | -0.3628 | 0.0226 | 0.0764 | 0.1716 | 0.1570 |
| DR-CVaR regime-aware | 0.7890 | -0.2978 | 0.0215 | 0.2213 | 0.1833 | 0.1499 |
| DR-MSD (CV epsilon) | 0.7262 | -0.3369 | 0.0224 | 0.1772 | 0.1779 | 0.1555 |
| DR-MSD regime-aware | 0.6887 | -0.3369 | 0.0225 | 0.2432 | 0.1726 | 0.1563 |
| NIFTY 50 price index (excludes dividends) | 0.3142 | -0.3844 | 0.0237 |  | 0.1161 | 0.1627 |

Paired Sharpe differences (block bootstrap 95% intervals):

| method | versus | metric | estimate | ci_lo | ci_hi | excludes_zero |
|---|---|---|---|---|---|---|
| drmsd_cv | plugin_maxsharpe | sharpe | -0.0181 | -0.2497 | 0.2460 | no |
| drmsd_cv | equal_weight | sharpe | -0.0418 | -0.3031 | 0.2221 | no |
| drcvar_cv | plugin_maxsharpe | sharpe | -0.0650 | -0.4215 | 0.3124 | no |
| drcvar_cv | equal_weight | sharpe | -0.0887 | -0.2328 | 0.0278 | no |
| drmsd_regime | plugin_maxsharpe | sharpe | -0.0557 | -0.2769 | 0.1906 | no |
| drmsd_regime | equal_weight | sharpe | -0.0793 | -0.3436 | 0.1827 | no |
| drcvar_regime | plugin_maxsharpe | sharpe | 0.0447 | -0.3342 | 0.4007 | no |
| drcvar_regime | equal_weight | sharpe | 0.0210 | -0.1898 | 0.2134 | no |

H1 secondary metrics:

| method | versus | metric | estimate | ci_lo | ci_hi | excludes_zero |
|---|---|---|---|---|---|---|
| drmsd_cv | plugin_maxsharpe | cvar95_daily | -0.0018 | -0.0030 | -0.0006 | yes |
| drmsd_cv | plugin_maxsharpe | max_drawdown | -0.0255 | -0.0491 | 0.0989 | no |
| drcvar_cv | plugin_maxsharpe | cvar95_daily | -0.0017 | -0.0030 | -0.0002 | yes |
| drcvar_cv | plugin_maxsharpe | max_drawdown | -0.0513 | -0.0874 | 0.1065 | no |

Stress windows:

| window | start | end | n_days | method | cumulative_return | max_drawdown | cvar95_daily |
|---|---|---|---|---|---|---|---|
| covid_2020 | 2020-02-01 | 2020-04-30 | 58 | equal_weight | -0.1609 | -0.3568 | 0.0926 |
| covid_2020 | 2020-02-01 | 2020-04-30 | 58 | plugin_maxsharpe | -0.0952 | -0.3114 | 0.0802 |
| covid_2020 | 2020-02-01 | 2020-04-30 | 58 | drcvar_cv | -0.1470 | -0.3568 | 0.0926 |
| covid_2020 | 2020-02-01 | 2020-04-30 | 58 | drcvar_regime | -0.0497 | -0.2913 | 0.0770 |
| covid_2020 | 2020-02-01 | 2020-04-30 | 58 | drmsd_cv | -0.1389 | -0.3369 | 0.0856 |
| covid_2020 | 2020-02-01 | 2020-04-30 | 58 | drmsd_regime | -0.1389 | -0.3369 | 0.0856 |
| covid_2020 | 2020-02-01 | 2020-04-30 | 58 | nifty_price_index | -0.1757 | -0.3763 | 0.0970 |
| year_2022 | 2022-01-01 | 2022-12-31 | 248 | equal_weight | 0.1202 | -0.1429 | 0.0250 |
| year_2022 | 2022-01-01 | 2022-12-31 | 248 | plugin_maxsharpe | -0.0470 | -0.2233 | 0.0274 |
| year_2022 | 2022-01-01 | 2022-12-31 | 248 | drcvar_cv | 0.1202 | -0.1429 | 0.0250 |
| year_2022 | 2022-01-01 | 2022-12-31 | 248 | drcvar_regime | 0.1055 | -0.1456 | 0.0250 |
| year_2022 | 2022-01-01 | 2022-12-31 | 248 | drmsd_cv | 0.0934 | -0.1478 | 0.0247 |
| year_2022 | 2022-01-01 | 2022-12-31 | 248 | drmsd_regime | 0.0514 | -0.1806 | 0.0247 |
| year_2022 | 2022-01-01 | 2022-12-31 | 248 | nifty_price_index | 0.0433 | -0.1647 | 0.0250 |

Regime-aware comparison (exploratory):

| kind | version | method | sharpe | cvar95_covid_2020 | cum_return_covid_2020 | cvar95_year_2022 | cum_return_year_2022 |
|---|---|---|---|---|---|---|---|
| cvar | regime-aware (hmm2_stress) | drcvar_regime | 0.7890 | 0.0770 | -0.0497 | 0.0250 | 0.1055 |
| cvar | regime-aware (vol_two) | drcvar_regimevol | 0.7354 | 0.0770 | -0.0590 | 0.0223 | 0.0926 |
| cvar | soft eps_t (exploratory) | drcvar_soft | 0.7419 | 0.0827 | -0.0784 | 0.0250 | 0.1038 |
| cvar | static, cross-validated epsilon | drcvar_cv | 0.6794 | 0.0926 | -0.1470 | 0.0250 | 0.1202 |
| cvar | static, hindsight best Sharpe epsilon (uses future data) | drcvar_fix_12 | 0.7680 | 0.0926 | -0.1609 | 0.0250 | 0.1202 |
| msd | regime-aware (hmm2_stress) | drmsd_regime | 0.6887 | 0.0856 | -0.1389 | 0.0247 | 0.0514 |
| msd | regime-aware (vol_two) | drmsd_regimevol | 0.7545 | 0.0877 | -0.1467 | 0.0246 | 0.0820 |
| msd | soft eps_t (exploratory) | drmsd_soft | 0.7094 | 0.0856 | -0.1389 | 0.0247 | 0.0570 |
| msd | static, cross-validated epsilon | drmsd_cv | 0.7262 | 0.0856 | -0.1389 | 0.0247 | 0.0934 |
| msd | static, hindsight best Sharpe epsilon (uses future data) | drmsd_fix_01 | 0.7443 | 0.0802 | -0.0953 | 0.0274 | -0.0471 |

Sensitivities:

| sensitivity | method | sharpe | max_drawdown | cvar95_daily | avg_turnover | avg_effective_n | sharpe_diff_vs_plugin | ci_lo_vs_plugin | ci_hi_vs_plugin | sharpe_diff_vs_equal_weight | ci_lo_vs_equal_weight | ci_hi_vs_equal_weight |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| window_252 | w252_plugin_maxsharpe | 0.5963 | -0.3259 | 0.0239 | 0.4663 | 11.3505 | 0.0000 | 0.0000 | 0.0000 | -0.1717 | -0.5572 | 0.1528 |
| window_252 | w252_plugin_minvar | 0.5766 | -0.2595 | 0.0185 | 0.2254 | 13.7662 | -0.0197 | -0.4083 | 0.3406 | -0.1914 | -0.5574 | 0.1450 |
| window_252 | w252_lw_maxsharpe | 0.5958 | -0.3294 | 0.0240 | 0.4599 | 11.5250 | -0.0006 | -0.0131 | 0.0136 | -0.1722 | -0.5571 | 0.1493 |
| window_252 | w252_drcvar_cv | 0.6554 | -0.3628 | 0.0225 | 0.0842 | 37.7305 | 0.0591 | -0.2479 | 0.3932 | -0.1126 | -0.2819 | 0.0235 |
| window_252 | w252_drmsd_cv | 0.6490 | -0.3205 | 0.0224 | 0.2462 | 28.5115 | 0.0526 | -0.1616 | 0.2879 | -0.1191 | -0.3957 | 0.1256 |
| window_252 | w252_drcvar_regime | 0.7154 | -0.3170 | 0.0219 | 0.2539 | 34.3552 | 0.1190 | -0.2023 | 0.4538 | -0.0527 | -0.2509 | 0.1310 |
| window_252 | w252_drmsd_regime | 0.6366 | -0.3205 | 0.0225 | 0.3058 | 27.7200 | 0.0402 | -0.1691 | 0.2645 | -0.1314 | -0.4186 | 0.1063 |
| one_se_rule | drcvar_1se | 0.6194 | -0.2816 | 0.0187 | 0.2173 | 12.4161 | -0.1250 | -0.5382 | 0.2638 | -0.1487 | -0.5253 | 0.1889 |
| one_se_rule | drmsd_1se | 0.7443 | -0.3115 | 0.0242 | 0.3206 | 11.2833 | -0.0001 | -0.0003 | 0.0002 | -0.0237 | -0.4207 | 0.3494 |
| vol_two_labels | drcvar_regimevol | 0.7354 | -0.2905 | 0.0205 | 0.1906 | 32.0855 | -0.0089 | -0.3402 | 0.3121 | -0.0326 | -0.2749 | 0.1706 |
| vol_two_labels | drmsd_regimevol | 0.7545 | -0.3419 | 0.0229 | 0.4897 | 22.7383 | 0.0101 | -0.1966 | 0.2453 | -0.0135 | -0.3013 | 0.2634 |
| drmsd_eps_from_cvar_blocks | drmsd_cvarblocks | 0.6982 | -0.3419 | 0.0226 | 0.2272 | 24.9475 | -0.0462 | -0.2574 | 0.1940 | -0.0698 | -0.3285 | 0.1940 |
| drmsd_extended_grid | drmsd_extcv | 0.7773 | -0.3369 | 0.0224 | 0.1434 | 34.6746 | 0.0329 | -0.2549 | 0.3617 | 0.0092 | -0.2238 | 0.2458 |
<!-- results:end -->

## Plain-language reading

* **Sample.** 27 January 2017 to 29 October 2026, 2,405 trading days and 115 rebalances every 21 days, 40 stocks, all returns net of 10 bps costs. The first date is the earliest at which a 504-day window and the hidden-Markov stress label both exist.
* **Headline.** After costs, 1/N has a Sharpe ratio of 0.768, plug-in max-Sharpe 0.744, DR-MSD with cross-validated epsilon 0.726, DR-CVaR with cross-validated epsilon 0.679 and the regime-aware DR-CVaR 0.789. The NIFTY 50 price index (excludes dividends, no costs) has 0.314.
* **Do the robust portfolios beat plug-in and 1/N?** Not in a way the data can distinguish. Every block-bootstrap interval for a Sharpe difference between a primary DR portfolio and plug-in max-Sharpe or 1/N includes zero (for example DR-MSD minus plug-in: -0.018, interval -0.250 to +0.246; regime-aware DR-CVaR minus plug-in: +0.045, interval -0.334 to +0.401). The intervals are wide because the path is one 9.5-year history.
* **Tail risk.** The one pattern the bootstrap does separate from zero: the DR portfolios have a smaller CVaR95 of daily net returns than plug-in max-Sharpe (by 0.17 to 0.27 percentage points) and than 1/N (by 0.11 to 0.21 points). No maximum-drawdown difference has an interval excluding zero. These are descriptive; no multiplicity correction is applied.
* **Where epsilon sits.** The chosen epsilon is at or near the top of the grid. DR-MSD with the static cross-validated rule picks the largest value (0.2154) at 82 of 115 rebalances and the value two steps below it at 30; DR-CVaR picks 0.1 at 106 of 115, where its portfolio already has about 38 effective positions. For DR-MSD the plateau (1/N) lies above the grid, so the grid binds; the extended-grid sensitivity chooses almost 1/N and reaches 0.777.
* **Ablation (epsilon = 0).** DR-CVaR at epsilon 0 is exactly the plug-in CVaR linear program (Sharpe 0.613, checked to 2e-11 in CVaR). DR-MSD at epsilon 0 is exactly plug-in max-Sharpe, because theta is set from the window's own max-Sharpe ratio; its Sharpe of 0.744 is the same number. For DR-CVaR, raising epsilon moves the portfolio steadily toward 1/N and the Sharpe ratio toward 1/N's 0.768 (price-of-robustness curve). For DR-MSD the curve is flat to epsilon about 0.005, dips (0.681 at 0.046) and returns to 0.768 on the extended grid. The best fixed epsilon in hindsight is a diagnostic, not a strategy.
* **Stress windows (1 Feb to 30 Apr 2020).** Cumulative net return: plug-in -9.5%, regime-aware DR-CVaR -5.0%, DR-MSD -13.9%, DR-CVaR -14.7%, 1/N -16.1%, index -17.6%. **Calendar 2022:** plug-in -4.7%, 1/N +12.0%, DR-CVaR +12.0%, DR-MSD +9.3%, index +4.3%. The hidden-Markov model flags few days of 2022, so the regime-aware portfolios mostly equal their calm-regime counterparts there.
* **Regime-aware (exploratory, not confirmatory).** The two-regime DR-CVaR has a lower 2020 CVaR95 than the static cross-validated version (7.7% against 9.3%) and a higher full-sample Sharpe (0.789 against 0.679); the two-regime DR-MSD does not (0.689 against 0.726). Regime labels come from only 19 stress rebalances.
* **Sensitivities.** With 252-day windows every Sharpe ratio is lower (plug-in 0.596) and the DR portfolios sit 0.04 to 0.12 above plug-in with intervals that include zero. The one-SE rule, the alternative volatility labels and the DR-CVaR-validated epsilon for DR-MSD do not change the conclusion that intervals include zero.

## Deviations and disclosures

* **Validation objective.** `eps_for_date` validates epsilon with DR-CVaR portfolios on 252-day windows. DR-CVaR uses it as built. For DR-MSD the same rule is applied to validation blocks built with DR-MSD (the literal reading is the sensitivity `drmsd_cvarblocks`). The 252-day inner window against the 504-day backtest window is kept as built.
* **Evaluation start** is row 505 (2017-01-27), not 504, because the first labelled day is row 504.
* **Michaud** uses the full 500 draws; no method was cut. The pilot projected 4.2 minutes; the weights took 643 s on 18 workers, the validation blocks 252 s, and the first complete run 27.6 minutes.
* **Bug found by a check before the full run:** the DR-CVaR checkpoint group was computing DR-MSD weights; fixed and covered by a regression test, then the full run was computed once from scratch.
* **Tolerances changed after the first run, disclosed in `config.yaml`:** the look-ahead and reproducibility weight tolerances from 1e-6 to 1e-5 (plug-in weights match exactly; the interior-point DR-MSD weights differ by 2e-6 to 3e-6 across process state, the same behaviour as in the simulation study), and the 1/N turnover ceiling from 0.05 to 0.10 (the measured 0.0518 matches an independent calculation from the 21-day returns). The first run therefore exited with status 1 on those three checks; the second run reused the same stored weights and passed every check.
* **Look-ahead check** replaces every row from day t onward by zeros and verifies that plug-in weights, DR-MSD weights at a fixed epsilon and the regime-aware epsilon (DR-CVaR through `eps_for_date_info`, DR-MSD through the same pipeline) do not change; the hidden-Markov labels were additionally recomputed from truncated data on one date and were identical.
* **Optional era game** (step 6) was not run.
