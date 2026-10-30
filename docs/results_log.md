# Results log

Key numbers and findings are appended here as they are produced.

- Scaffold created; no results yet.

## Markowitz in-sample frontier (scripts/run_markowitz_frontier.py)
- IN-SAMPLE only. Sample 2015-01-05 to 2026-10-28, 2909 daily obs, 43 assets, annualised x252.
- Weight cap 0.1, rf 0.065 (ASSUMPTION, unconfirmed). Survivorship bias: current constituents only.
- Tickers dropped (short history): none; not returned by download: none.
- Min-variance: return 0.1331, vol 0.1288, Sharpe 0.529
- Max-Sharpe: return 0.2334, vol 0.1654, Sharpe 1.018
- 1/N: return 0.1701, vol 0.1584, Sharpe 0.663
- NIFTY 50 index: return 0.0993, vol 0.1615, Sharpe 0.212
- Max-Sharpe nonzero weights: DIVISLAB.NS 0.100, BAJFINANCE.NS 0.100, TITAN.NS 0.100, POWERGRID.NS 0.100, JSWSTEEL.NS 0.100, BRITANNIA.NS 0.100, APOLLOHOSP.NS 0.099, BHARTIARTL.NS 0.091, HCLTECH.NS 0.080, BAJAJFINSV.NS 0.057, NESTLEIND.NS 0.035, TATACONSUM.NS 0.021, RELIANCE.NS 0.016
- Caveat: stocks use dividend-adjusted prices (auto_adjust=True) but ^NSEI is a price index without dividends, so the index return is understated relative to the stocks and Sharpe comparisons against it are biased in the portfolios' favour.

## Data pipeline (scripts/run_data.py)
- Snapshot: prices_raw_20261030T061556Z.parquet. Window 2015-01-01 to 2026-10-30 (config); adjusted closes (auto_adjust=True); benchmark ^NSEI is a PRICE index (no dividends).
- Tickers requested: 50 (list from memory, UNVERIFIED vs NSE). Failed to download: {'TATAMOTORS.NS': 'returned only NaN'}.
- Kept 45 stocks; dropped 4: ETERNAL.NS (no price at window start; first valid price 2021-07-23); HDFCLIFE.NS (no price at window start; first valid price 2017-11-17); JIOFIN.NS (no price at window start; first valid price 2023-08-21); SBILIFE.NS (no price at window start; first valid price 2017-10-03)
- Calendar: 2911 benchmark trading days (2015-01-02 to 2026-10-29); 2910 return rows. Forward-filled cells (gap <= 3d): 6. Stock dates not on benchmark calendar (discarded): 9.
- Daily moves with |return| > 20% flagged (not removed): 21 (see outputs/tables/data_flagged_jumps.csv).
- ^NSEI: ann. mean 0.1002, ann. vol 0.1615, skew -0.995, excess kurtosis 16.98.
- Stocks, median ann. vol 0.2862; median excess kurtosis 7.36.
- Note: the Markowitz in-sample section above was run on the earlier 43-ticker list that lived in config.yaml. The universe now comes from data/nifty50_tickers.csv (50 tickers, unverified), so re-running scripts/run_markowitz_frontier.py will give different numbers. Not re-run yet.

## 2026-10-30 - Official universe, data-quality review (PROVISIONAL, awaiting decision on 3 stocks)
- Universe rebuilt from the official NSE file (data/raw/ind_nifty50list.csv, 50 rows): added 4 (BSE, INDIGO, MAXHEALTH, TMPV), removed 4 (HEROMOTOCO, INDUSINDBK, TATAMOTORS, WIPRO), 46 unchanged. Diff in outputs/tables/data_ticker_diff.csv.
- Tata Motors: the NSE file lists only "Tata Motors Passenger Vehicles Ltd., TMPV" (no TATAMOTORS). Only TMPV.NS was used. Incremental download fetched only BSE, INDIGO, MAXHEALTH, TMPV; no ticker failed.
- Provisional pipeline run (not final, three issues below unresolved): 50 requested, 50 downloaded, 43 full-history survivors; dropped 7 for a first price after 2015-01-02: BSE (2017-02-03), ETERNAL (2021-07-23), HDFCLIFE (2017-11-17), INDIGO (2015-11-10), JIOFIN (2023-08-21), MAXHEALTH (2020-08-21), SBILIFE (2017-10-03). 7 forward-filled cells, 2911 trading days, 19 moves above 20% flagged.
- Price jumps examined (details in docs/limitations.md): ADANIENT.NS 2015-06-03 (-38.7%) is a demerger ex-date with an unreliable adjustment; TRENT.NS 2026-01-01 (-33.0%) is a 1:2 bonus adjustment placed at the wrong date (ex-date reported as 2026-06-04); TMPV.NS 2025-10-14 (-40.2%) is the Tata Motors demerger record date, history unadjusted. None corrected or removed yet.
- The Markowitz entry above was computed on the earlier 43-ticker list and has NOT been re-run; it will be marked SUPERSEDED once the data decision is made.

## 2026-10-30 - Data pipeline re-run on official NSE universe (scripts/run_data.py)
- Source of universe: data/nifty50_tickers.csv (built from the official NSE file). Snapshot: prices_raw_20261030T063316Z.parquet. Window 2015-01-01 to 2026-10-30 (config); adjusted closes (auto_adjust=True).
- Tickers requested: 50; downloaded: 50; failed: none.
- Excluded by config (data-quality, prices not edited): 3: ADANIENT.NS, TRENT.NS, TMPV.NS
- Final universe size: 40 stocks (+ benchmark). Dropped/excluded 10: ADANIENT.NS (data-quality exclusion: unadjusted corporate action); TRENT.NS (data-quality exclusion: unadjusted corporate action); TMPV.NS (data-quality exclusion: unadjusted corporate action); BSE.NS (no price at window start; first valid price 2017-02-03); ETERNAL.NS (no price at window start; first valid price 2021-07-23); HDFCLIFE.NS (no price at window start; first valid price 2017-11-17); INDIGO.NS (no price at window start; first valid price 2015-11-10); JIOFIN.NS (no price at window start; first valid price 2023-08-21); MAXHEALTH.NS (no price at window start; first valid price 2020-08-21); SBILIFE.NS (no price at window start; first valid price 2017-10-03)
- Calendar: 2911 benchmark trading days (2015-01-02 to 2026-10-29); 2910 return rows. Forward-filled cells (gap <= 3d): 7. Stock dates not on benchmark calendar (discarded): 9.
- Daily moves with |return| > 20% flagged (not removed): 6 (outputs/tables/data_flagged_jumps.csv).
- NIFTY 50 price index (excludes dividends), daily simple returns: ann. mean 0.1002, ann. vol 0.1615, skew -0.995, excess kurtosis 16.98.
- Single stocks: median ann. vol 0.2841 vs index 0.1615 (ratio 1.76); median excess kurtosis 7.15.

## 2026-10-30 - Markowitz in-sample frontier, final data (scripts/run_markowitz_frontier.py)
- IN-SAMPLE only, from data/processed. Sample 2015-01-05 to 2026-10-29, 2910 daily obs, 40 assets, annualised x252.
- Weight cap 0.1, rf 0.065 (ASSUMPTION, unconfirmed). Survivorship bias: current constituents only.
- Headline comparison is 1/N. The index is a PRICE index without dividends; shown for reference only.
- Min-variance: return 0.1323, vol 0.1292, Sharpe 0.520
- Max-Sharpe: return 0.2377, vol 0.1719, Sharpe 1.005
- 1/N: return 0.1777, vol 0.1617, Sharpe 0.697
- NIFTY 50 price index (excludes dividends): return 0.1002, vol 0.1615, Sharpe 0.218
- Max-Sharpe: 13 nonzero weights, 6 at the cap: BAJFINANCE.NS 0.100, TITAN.NS 0.100, BEL.NS 0.100, APOLLOHOSP.NS 0.100, JSWSTEEL.NS 0.100, POWERGRID.NS 0.100, HCLTECH.NS 0.095, BHARTIARTL.NS 0.092, NESTLEIND.NS 0.070, BAJAJFINSV.NS 0.059, BAJAJ-AUTO.NS 0.040, TATACONSUM.NS 0.038, RELIANCE.NS 0.007

## 2026-10-30 - Supersession notes and findings (computed from data/processed)
- SUPERSEDED: the entry "Data pipeline (scripts/run_data.py)" (first run, TATAMOTORS failed, 43-ticker list from memory) and the entry "Data pipeline re-run ... PROVISIONAL" (43 survivors incl. ADANIENT, TRENT, TMPV). The final data entry is the 40-stock re-run above; the three stocks were excluded by decision (config key data.excluded_tickers).
- SUPERSEDED: the entry "Markowitz in-sample frontier (scripts/run_markowitz_frontier.py)" (43 assets from the memory list, downloaded directly, sample ending 2026-10-28). The final Markowitz entry above uses the 40-stock official-list universe read from data/processed (sample ending 2026-10-29).
- Change vs superseded Markowitz entry (new minus old; return, vol, Sharpe; universe and one extra day both changed, so not a like-for-like comparison):
  - Min-variance: -0.0008, +0.0004, -0.009 (0.1331/0.1288/0.529 -> 0.1323/0.1292/0.520)
  - Max-Sharpe: +0.0043, +0.0065, -0.013 (0.2334/0.1654/1.018 -> 0.2377/0.1719/1.005); the set of nonzero weights changed (old top weights DIVISLAB, BRITANNIA are no longer in the universe).
  - 1/N: +0.0076, +0.0033, +0.034 (0.1701/0.1584/0.663 -> 0.1777/0.1617/0.697)
  - Assets: 43 -> 40.
- Finding: NIFTY 50 price index daily simple returns, skew -0.995, excess kurtosis 16.98 (n = 2910).
- Finding: median single-stock annualised volatility 0.2841 vs index 0.1615 (ratio 1.76); single-stock range 0.2232 to 0.4240.
- Finding: final universe 40 stocks, 2015-01-05 to 2026-10-29.

## 2026-10-30 - Robust methods demo (scripts/run_robust_demo.py)
- IN-SAMPLE illustration, first rolling window 2015-01-05 to 2016-01-14 (252 days, 40 assets, cap 0.1, rf 0.065 assumed).
- Parameters (config.yaml robust section): n_draws 500, box_z 1.0, ellipsoid confidence 0.95, risk_aversion 5.0; CVaR alpha 0.95.
- Ledoit-Wolf shrinkage coefficient: 0.0893.
- ledoit_wolf_min_variance: 19 positions, max weight 0.100, effective N 14.28, vol 0.1207, daily CVaR 0.0171
- michaud_resampled: 37 positions, max weight 0.091, effective N 22.52, vol 0.1498, daily CVaR 0.0217
- worst_case_box: failed: cvxpy status: infeasible
- worst_case_ellipsoid: 18 positions, max weight 0.100, effective N 12.48, vol 0.1283, daily CVaR 0.0177
- cvar_min: 15 positions, max weight 0.100, effective N 11.82, vol 0.1256, daily CVaR 0.0155
- equal_weight: 40 positions, max weight 0.025, effective N 40.00, vol 0.1604, daily CVaR 0.0241

## 2026-10-30 - WDRO eps sweep (scripts/run_wdro_sweep.py)
- IN-SAMPLE illustration, first rolling window 2015-01-05 to 2016-01-14 (252 days, 40 assets, cap 0.1).
- Ground norm 1 (dual norm in the penalty), CVaR confidence 0.95 (tail probability 0.05), mean weight 0.0, support = R^m (no return bounds). eps grid: 13 log-spaced values from 1e-05 to 1e-01, daily-return units.
- cvar: eps 1e-05 -> 1e-01: effective N 11.82 -> 40.00, positions 15 -> 40, nominal return 0.0396 -> -0.0128, empirical daily CVaR 0.0155 -> 0.0241, worst-case CVaR bound 0.0155 -> 0.0741, bound gained vs eps=0 portfolio at eps_max 0.1414.
- meanvar: eps 1e-05 -> 1e-01: effective N 13.42 -> 32.15, positions 19 -> 33, nominal return 0.0152 -> 0.0445, empirical daily CVaR 0.0171 -> 0.0213, worst-case CVaR bound 0.0171 -> 0.0849, bound gained vs eps=0 portfolio at eps_max 0.1322.

## 2026-10-30 - Regime detection (scripts/run_regimes.py)
- Benchmark returns 2015-01-05 to 2026-10-29 (2910 days). Labelling uses data up to and including each date; the label of date t should drive the portfolio held from t+1.
- Parameters (config.yaml regimes section): vol window 21, quantiles [0.33, 0.67], vol min history 252, HMM min history 504, refit every 21, restarts 5.
- Dates labelled by all three methods: 2406 (2017-01-25 to 2026-10-29).
- vol_threshold: days per regime {'calm': 1116, 'crisis': 770, 'volatile': 752, 'unlabelled': 272}; empirical transition matrix (row = from):
    to          calm  crisis  volatile
    from                              
    calm      0.9498  0.0000    0.0502
    crisis    0.0000  0.9428    0.0572
    volatile  0.0745  0.0585    0.8670
- hmm2: days per regime {'calm': 2035, 'unlabelled': 504, 'crisis': 371}; empirical transition matrix (row = from):
    to        calm  crisis
    from                  
    calm    0.9779  0.0221
    crisis  0.1213  0.8787
- hmm3: days per regime {'volatile': 1150, 'calm': 1131, 'unlabelled': 504, 'crisis': 125}; empirical transition matrix (row = from):
    to          calm  crisis  volatile
    from                              
    calm      0.9134  0.0035    0.0831
    crisis    0.0240  0.6880    0.2880
    volatile  0.0827  0.0305    0.8869
- duration vol_threshold/calm: 56 spells, mean 19.9 days, max 163
- duration vol_threshold/crisis: 45 spells, mean 17.1 days, max 115
- duration vol_threshold/volatile: 100 spells, mean 7.5 days, max 46
- duration hmm2/calm: 46 spells, mean 44.2 days, max 430
- duration hmm2/crisis: 45 spells, mean 8.2 days, max 56
- duration hmm3/calm: 98 spells, mean 11.5 days, max 169
- duration hmm3/crisis: 39 spells, mean 3.2 days, max 23
- duration hmm3/volatile: 131 spells, mean 8.8 days, max 96
- realised annualised vol vol_threshold/crisis: 0.2452 over 770 days (for vol_threshold this is partly by construction: the label is a function of recent vol)
- realised annualised vol vol_threshold/volatile: 0.1270 over 752 days (for vol_threshold this is partly by construction: the label is a function of recent vol)
- realised annualised vol vol_threshold/calm: 0.0940 over 1116 days (for vol_threshold this is partly by construction: the label is a function of recent vol)
- realised annualised vol hmm2/calm: 0.1146 over 2035 days (for vol_threshold this is partly by construction: the label is a function of recent vol)
- realised annualised vol hmm2/crisis: 0.3159 over 371 days (for vol_threshold this is partly by construction: the label is a function of recent vol)
- realised annualised vol hmm3/volatile: 0.1534 over 1150 days (for vol_threshold this is partly by construction: the label is a function of recent vol)
- realised annualised vol hmm3/calm: 0.0900 over 1131 days (for vol_threshold this is partly by construction: the label is a function of recent vol)
- realised annualised vol hmm3/crisis: 0.4705 over 125 days (for vol_threshold this is partly by construction: the label is a function of recent vol)
- hmm2 fit diagnostics: 115 refits, 0 not converged (best restart), 0 with all restarts failed, median restarts converged 5.0 of 5
- hmm3 fit diagnostics: 115 refits, 0 not converged (best restart), 0 with all restarts failed, median restarts converged 5.0 of 5
- agreement vol_threshold vs hmm3: exact 0.565, calm-vs-not-calm 0.824; confusion (rows vol_threshold, columns hmm3):
    hmm3           calm  crisis  volatile
    vol_threshold                        
    calm            850       0       142
    crisis           37      92       590
    volatile        244      33       418
- agreement vol_threshold vs hmm2: exact n/a (different label sets), calm-vs-not-calm 0.567; confusion (rows vol_threshold, columns hmm2):
    hmm2           calm  crisis
    vol_threshold              
    calm            992       0
    crisis          445     274
    volatile        598      97
- agreement hmm2 vs hmm3: exact n/a (different label sets), calm-vs-not-calm 0.621; confusion (rows hmm2, columns hmm3):
    hmm3    calm  crisis  volatile
    hmm2                          
    calm    1127       3       905
    crisis     4     122       245
- Correction to the regime entry above: the phrase "for vol_threshold this is partly by construction" was printed on the hmm2 and hmm3 realised-vol lines by mistake. It applies only to the vol_threshold lines (the label is a function of recent volatility). The script is fixed; the numbers are unchanged.

## 2026-10-30 - Per-regime eps selection (scripts/run_eps_selection.py)
- Walk-forward blocks of 21 days after a 252-day window; kind cvar; eps grid 13 log-spaced values 1e-05 to 1e-01 (daily-return units); ground norm 1; cost 10 bps; criterion: net annualised Sharpe (rf 0.065 assumed), tie-break lower CVaR; min 6 blocks per regime; refit every 63 days; 126 blocks in total.
- vol_threshold: 42 refits; sources over regime rows {'regime': 99, 'pooled_fallback': 21, 'default': 6}.
    final refit eps by regime: pooled 1.0e-01 (pooled, 124 blocks), calm 1.0e-01 (regime, 52 blocks), crisis 1.0e-01 (regime, 37 blocks), volatile 4.6e-03 (regime, 34 blocks)
    unique eps selected per regime across refits (regime-sourced only): {'calm': 2, 'crisis': 3, 'volatile': 2}; refits where at least two regimes had their own choice: 31, of which all identical: 0
    Sharpe range across the eps grid per regime (all finished blocks; a small range means eps barely matters): calm 0.736, crisis 0.309, pooled 0.294, volatile 0.213
    all-blocks best eps per regime (not walk-forward, for reference): calm 1.0e-01, crisis 1.0e-01, pooled 1.0e-01, volatile 4.6e-03
- hmm2: 42 refits; sources over regime rows {'regime': 65, 'pooled_fallback': 15, 'default': 4}.
    final refit eps by regime: pooled 1.0e-01 (pooled, 124 blocks), calm 1.0e-01 (regime, 94 blocks), crisis 1.0e-03 (regime, 17 blocks)
    unique eps selected per regime across refits (regime-sourced only): {'calm': 3, 'crisis': 2}; refits where at least two regimes had their own choice: 29, of which all identical: 0
    Sharpe range across the eps grid per regime (all finished blocks; a small range means eps barely matters): calm 0.426, crisis 0.322, pooled 0.294
    all-blocks best eps per regime (not walk-forward, for reference): calm 1.0e-01, crisis 1.0e-03, pooled 1.0e-01
- hmm3: 42 refits; sources over regime rows {'regime': 94, 'pooled_fallback': 26, 'default': 6}.
    final refit eps by regime: pooled 1.0e-01 (pooled, 124 blocks), calm 4.6e-02 (regime, 54 blocks), crisis 4.6e-04 (regime, 9 blocks), volatile 1.0e-01 (regime, 48 blocks)
    unique eps selected per regime across refits (regime-sourced only): {'calm': 4, 'crisis': 1, 'volatile': 3}; refits where at least two regimes had their own choice: 34, of which all identical: 0
    Sharpe range across the eps grid per regime (all finished blocks; a small range means eps barely matters): calm 0.211, crisis 0.514, pooled 0.294, volatile 0.446
    all-blocks best eps per regime (not walk-forward, for reference): calm 4.6e-02, crisis 4.6e-04, pooled 1.0e-01, volatile 1.0e-01
- Consistency check: eps_for_date(2017-03-06, vol_threshold) recomputed from data before that date = 4.6e-02 (regime calm, regime); matches the full-sample schedule: True.

## 2026-10-30 - Eps grid extension scan (scripts/run_eps_selection.py --find-grid)
- Plateau scan (weights only, no performance used): kind cvar, ground norm 1, 126 training windows (one per validation block). Condition: effective number of positions 1/sum(w^2) >= 0.99 N in every window. Old grid maximum 0.1.
    eps 0.1: min ratio 0.9750, median ratio 1.0000, windows below 4/126
    eps 0.2154: min ratio 1.0000, median ratio 1.0000, windows below 0/126
- First candidate meeting the condition in every window: 0.215443; extension values above the old grid maximum: 1 (scan took 14 s).

## 2026-10-30 - Robust methods demo (scripts/run_robust_demo.py)
- IN-SAMPLE illustration, first rolling window 2015-01-05 to 2016-01-14 (252 days, 40 assets, cap 0.1, rf 0.065 assumed).
- Parameters (config.yaml robust section): n_draws 500, box_z 1.0, ellipsoid confidence 0.95, risk_aversion 5.0; CVaR alpha 0.95.
- Ledoit-Wolf shrinkage coefficient: 0.0893.
- ledoit_wolf_min_variance: 19 positions, max weight 0.100, effective N 14.28, vol 0.1207, daily CVaR 0.0171
- michaud_resampled: 37 positions, max weight 0.091, effective N 22.52, vol 0.1498, daily CVaR 0.0217
- worst_case_box: 11 positions, max weight 0.100, effective N 10.70, vol 0.1579, daily CVaR 0.0219
- worst_case_ellipsoid: 18 positions, max weight 0.100, effective N 12.48, vol 0.1283, daily CVaR 0.0177
- cvar_min: 15 positions, max weight 0.100, effective N 11.82, vol 0.1256, daily CVaR 0.0155
- equal_weight: 40 positions, max weight 0.025, effective N 40.00, vol 0.1604, daily CVaR 0.0241
- Note on the robust demo entry above: worst_case_box was changed from worst-case max-Sharpe (which was infeasible on this window, see the earlier robust demo entry) to worst-case mean-variance with the box lower endpoint, objective (mu_hat - d)'w - risk_aversion * w'Sigma w. It is now feasible on this window. The earlier entry's worst_case_box failure is SUPERSEDED by this entry; the other five methods are unchanged (identical numbers).

## 2026-10-30 - Per-regime eps selection, extended grid and one-SE rule (scripts/run_eps_selection.py)
- Extended-grid run (the original-grid run above is kept, not overwritten). Walk-forward blocks of 21 days after a 252-day window; kind cvar; ground norm 1; cost 10 bps; criterion net annualised Sharpe (rf 0.065 assumed); min 6 blocks per regime; refit every 63 days; 126 blocks in total.
- Grid: 14 log-spaced values 1e-05 to 0.215; the original 13 values are included (1 added above 0.1). Rules: best = highest Sharpe (ties: lower CVaR, then smaller eps); one_se = smallest eps with Sharpe >= best Sharpe minus the best eps's leave-one-block-out jackknife SE (treats blocks as exchangeable; autocorrelation between blocks is ignored, so the SE is probably too small).
- No significance test was run, and nothing here shows that regime-dependent eps beats a fixed eps.
- vol_threshold:
    rule best: final refit eps by regime: pooled 0.1 (pooled, 124 blocks), calm 0.1 (regime, 52 blocks), crisis 0.215 (regime, 37 blocks), volatile 0.00464 (regime, 34 blocks)
      sources (regime rows) {'regime': 99, 'pooled_fallback': 21, 'default': 6}; refits 42; refits with >=2 regime-sourced choices 31, of which identical across those regimes 0; refits where ALL regime rows (incl. pooled fallbacks) share one eps 4
    rule one_se: final refit eps by regime: pooled 1e-05 (pooled, 124 blocks), calm 0.00464 (regime, 52 blocks), crisis 1e-05 (regime, 37 blocks), volatile 1e-05 (regime, 34 blocks)
      sources (regime rows) {'regime': 99, 'pooled_fallback': 21, 'default': 6}; refits 42; refits with >=2 regime-sourced choices 31, of which identical across those regimes 15; refits where ALL regime rows (incl. pooled fallbacks) share one eps 19
    all-blocks curve, calm (54 blocks): best eps 0.1, plateau starts at 0.1 -> plateau (1/N: same portfolio for every larger eps); Sharpe at best 1.166 (jackknife SE 0.572)
    all-blocks curve, crisis (37 blocks): best eps 0.215, plateau starts at 0.215 -> edge (best at the largest grid value, no plateau reached); Sharpe at best 1.108 (jackknife SE 0.922)
    all-blocks curve, pooled (126 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.784 (jackknife SE 0.389)
    all-blocks curve, volatile (34 blocks): best eps 0.00464, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.341 (jackknife SE 0.534)
- hmm2:
    rule best: final refit eps by regime: pooled 0.1 (pooled, 124 blocks), calm 0.1 (regime, 94 blocks), crisis 0.001 (regime, 17 blocks)
      sources (regime rows) {'regime': 65, 'pooled_fallback': 15, 'default': 4}; refits 42; refits with >=2 regime-sourced choices 29, of which identical across those regimes 0; refits where ALL regime rows (incl. pooled fallbacks) share one eps 10
    rule one_se: final refit eps by regime: pooled 1e-05 (pooled, 124 blocks), calm 0.00464 (regime, 94 blocks), crisis 1e-05 (regime, 17 blocks)
      sources (regime rows) {'regime': 65, 'pooled_fallback': 15, 'default': 4}; refits 42; refits with >=2 regime-sourced choices 29, of which identical across those regimes 26; refits where ALL regime rows (incl. pooled fallbacks) share one eps 38
    all-blocks curve, calm (96 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.892 (jackknife SE 0.383)
    all-blocks curve, crisis (17 blocks): best eps 0.001, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.703 (jackknife SE 1.275)
    all-blocks curve, pooled (126 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.784 (jackknife SE 0.389)
- hmm3:
    rule best: final refit eps by regime: pooled 0.1 (pooled, 124 blocks), calm 0.0464 (regime, 54 blocks), crisis 0.000464 (regime, 9 blocks), volatile 0.1 (regime, 48 blocks)
      sources (regime rows) {'regime': 94, 'pooled_fallback': 26, 'default': 6}; refits 42; refits with >=2 regime-sourced choices 34, of which identical across those regimes 0; refits where ALL regime rows (incl. pooled fallbacks) share one eps 8
    rule one_se: final refit eps by regime: pooled 1e-05 (pooled, 124 blocks), calm 1e-05 (regime, 54 blocks), crisis 1e-05 (regime, 9 blocks), volatile 1e-05 (regime, 48 blocks)
      sources (regime rows) {'regime': 94, 'pooled_fallback': 26, 'default': 6}; refits 42; refits with >=2 regime-sourced choices 34, of which identical across those regimes 34; refits where ALL regime rows (incl. pooled fallbacks) share one eps 42
    all-blocks curve, calm (56 blocks): best eps 0.0464, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.824 (jackknife SE 0.490)
    all-blocks curve, crisis (9 blocks): best eps 0.000464, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 2.080 (jackknife SE 0.927)
    all-blocks curve, pooled (126 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.784 (jackknife SE 0.389)
    all-blocks curve, volatile (48 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.502 (jackknife SE 0.747)
- Reproduction check vol_threshold: restricting the extended run to the original grid reproduces the saved original selections (168 rows): True
- Reproduction check hmm2: restricting the extended run to the original grid reproduces the saved original selections (126 rows): True
- Reproduction check hmm3: restricting the extended run to the original grid reproduces the saved original selections (168 rows): False
- eps_for_date check vol_threshold/best: date 2017-03-06, regime calm, eps 0.0464 (regime); matches the full-sample schedule: True
- eps_for_date check vol_threshold/one_se: date 2017-03-06, regime calm, eps 0.00215 (regime); matches the full-sample schedule: True
- eps_for_date check hmm2/best: date 2017-09-07, regime calm, eps 0.1 (regime); matches the full-sample schedule: True
- eps_for_date check hmm2/one_se: date 2017-09-07, regime calm, eps 0.0215 (regime); matches the full-sample schedule: True
- eps_for_date check hmm3/best: date 2017-12-07, regime calm, eps 0.1 (pooled_fallback); matches the full-sample schedule: True
- eps_for_date check hmm3/one_se: date 2017-12-07, regime calm, eps 1e-05 (pooled_fallback); matches the full-sample schedule: True
- CONSISTENCY CHECKS FAILED: original-grid selections for hmm3 are not reproduced

## 2026-10-30 - Per-regime eps selection, extended grid and one-SE rule (scripts/run_eps_selection.py)
- Extended-grid run (the original-grid run above is kept, not overwritten). Walk-forward blocks of 21 days after a 252-day window; kind cvar; ground norm 1; cost 10 bps; criterion net annualised Sharpe (rf 0.065 assumed); min 6 blocks per regime; refit every 63 days; 126 blocks in total.
- Grid: 14 log-spaced values 1e-05 to 0.215; the original 13 values are included (1 added above 0.1). Rules: best = highest Sharpe (ties: lower CVaR, then smaller eps); one_se = smallest eps with Sharpe >= best Sharpe minus the best eps's leave-one-block-out jackknife SE (treats blocks as exchangeable; autocorrelation between blocks is ignored, so the SE is probably too small).
- No significance test was run, and nothing here shows that regime-dependent eps beats a fixed eps.
- vol_threshold:
    rule best: final refit eps by regime: pooled 0.1 (pooled, 124 blocks), calm 0.1 (regime, 52 blocks), crisis 0.215 (regime, 37 blocks), volatile 0.00464 (regime, 34 blocks)
      sources (regime rows) {'regime': 99, 'pooled_fallback': 21, 'default': 6}; refits 42; refits with >=2 regime-sourced choices 31, of which identical across those regimes 0; refits where ALL regime rows (incl. pooled fallbacks) share one eps 4
    rule one_se: final refit eps by regime: pooled 1e-05 (pooled, 124 blocks), calm 0.00464 (regime, 52 blocks), crisis 1e-05 (regime, 37 blocks), volatile 1e-05 (regime, 34 blocks)
      sources (regime rows) {'regime': 99, 'pooled_fallback': 21, 'default': 6}; refits 42; refits with >=2 regime-sourced choices 31, of which identical across those regimes 15; refits where ALL regime rows (incl. pooled fallbacks) share one eps 19
    all-blocks curve, calm (54 blocks): best eps 0.1, plateau starts at 0.1 -> plateau (1/N: same portfolio for every larger eps); Sharpe at best 1.166 (jackknife SE 0.572)
    all-blocks curve, crisis (37 blocks): best eps 0.215, plateau starts at 0.215 -> edge (best at the largest grid value, no plateau reached); Sharpe at best 1.108 (jackknife SE 0.922)
    all-blocks curve, pooled (126 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.784 (jackknife SE 0.389)
    all-blocks curve, volatile (34 blocks): best eps 0.00464, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.341 (jackknife SE 0.534)
- hmm2:
    rule best: final refit eps by regime: pooled 0.1 (pooled, 124 blocks), calm 0.1 (regime, 94 blocks), crisis 0.001 (regime, 17 blocks)
      sources (regime rows) {'regime': 65, 'pooled_fallback': 15, 'default': 4}; refits 42; refits with >=2 regime-sourced choices 29, of which identical across those regimes 0; refits where ALL regime rows (incl. pooled fallbacks) share one eps 10
    rule one_se: final refit eps by regime: pooled 1e-05 (pooled, 124 blocks), calm 0.00464 (regime, 94 blocks), crisis 1e-05 (regime, 17 blocks)
      sources (regime rows) {'regime': 65, 'pooled_fallback': 15, 'default': 4}; refits 42; refits with >=2 regime-sourced choices 29, of which identical across those regimes 26; refits where ALL regime rows (incl. pooled fallbacks) share one eps 38
    all-blocks curve, calm (96 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.892 (jackknife SE 0.383)
    all-blocks curve, crisis (17 blocks): best eps 0.001, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.703 (jackknife SE 1.275)
    all-blocks curve, pooled (126 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.784 (jackknife SE 0.389)
- hmm3:
    rule best: final refit eps by regime: pooled 0.1 (pooled, 124 blocks), calm 0.0464 (regime, 54 blocks), crisis 0.000464 (regime, 9 blocks), volatile 0.1 (regime, 48 blocks)
      sources (regime rows) {'regime': 94, 'pooled_fallback': 26, 'default': 6}; refits 42; refits with >=2 regime-sourced choices 34, of which identical across those regimes 0; refits where ALL regime rows (incl. pooled fallbacks) share one eps 8
    rule one_se: final refit eps by regime: pooled 1e-05 (pooled, 124 blocks), calm 1e-05 (regime, 54 blocks), crisis 1e-05 (regime, 9 blocks), volatile 1e-05 (regime, 48 blocks)
      sources (regime rows) {'regime': 94, 'pooled_fallback': 26, 'default': 6}; refits 42; refits with >=2 regime-sourced choices 34, of which identical across those regimes 34; refits where ALL regime rows (incl. pooled fallbacks) share one eps 42
    all-blocks curve, calm (56 blocks): best eps 0.0464, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.824 (jackknife SE 0.490)
    all-blocks curve, crisis (9 blocks): best eps 0.000464, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 2.080 (jackknife SE 0.927)
    all-blocks curve, pooled (126 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.784 (jackknife SE 0.389)
    all-blocks curve, volatile (48 blocks): best eps 0.1, plateau starts at 0.215 -> interior (best below the plateau, lower value at larger eps); Sharpe at best 0.502 (jackknife SE 0.747)
- Reproduction check vol_threshold: restricting the extended run to the original grid reproduces the saved original selections (168 rows): True; rows where the eps differs only through a float-level tie (|dSharpe| <= 1e-06, the new rule keeps the smaller eps): 0
- Reproduction check hmm2: restricting the extended run to the original grid reproduces the saved original selections (126 rows): True; rows where the eps differs only through a float-level tie (|dSharpe| <= 1e-06, the new rule keeps the smaller eps): 0
- Reproduction check hmm3: restricting the extended run to the original grid reproduces the saved original selections (168 rows): True; rows where the eps differs only through a float-level tie (|dSharpe| <= 1e-06, the new rule keeps the smaller eps): 4, max |dSharpe| 7.5e-08
- eps_for_date check vol_threshold/best: date 2017-03-06, regime calm, eps 0.0464 (regime); matches the full-sample schedule: True
- eps_for_date check vol_threshold/one_se: date 2017-03-06, regime calm, eps 0.00215 (regime); matches the full-sample schedule: True
- eps_for_date check hmm2/best: date 2017-09-07, regime calm, eps 0.1 (regime); matches the full-sample schedule: True
- eps_for_date check hmm2/one_se: date 2017-09-07, regime calm, eps 0.0215 (regime); matches the full-sample schedule: True
- eps_for_date check hmm3/best: date 2017-12-07, regime calm, eps 0.1 (pooled_fallback); matches the full-sample schedule: True
- eps_for_date check hmm3/one_se: date 2017-12-07, regime calm, eps 1e-05 (pooled_fallback); matches the full-sample schedule: True
- Note on the two entries "Per-regime eps selection, extended grid and one-SE rule": the first extended run reported the hmm3 original-grid reproduction check as FAILED and exited with status 1. The cause was four rows where the earlier tie-break picked an arbitrary eps among numerically identical portfolios (|Sharpe difference| at most 7.5e-8), not a different selection. The check now accepts such float-level ties and reports their count. The second entry (all checks passed, exit status 0) supersedes the first; the selection numbers are identical in both.
