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
