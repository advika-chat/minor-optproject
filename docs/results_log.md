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
