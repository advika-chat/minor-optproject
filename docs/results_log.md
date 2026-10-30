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
