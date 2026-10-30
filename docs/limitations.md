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
- The full-history filter adds to this: recently listed constituents are excluded
  (for the 2026-10-30 run: ETERNAL, HDFCLIFE, JIOFIN, SBILIFE; see
  `outputs/tables/data_dropped_tickers.csv`), which favours older, established companies.
- Any in-sample or backtest figure on this data should be read as optimistic. It
  is not evidence of real out-of-sample performance.

A fix needs point-in-time constituent lists and prices for delisted stocks, which
yfinance does not provide.

## Other known issues

- **Unverified ticker list.** The 50 tickers were written from the assistant's
  memory, not taken from NSE. They must be checked against NSE's official
  constituents. `TATAMOTORS.NS` returned no data (Yahoo reports it as unavailable,
  likely because of the company's demerger); it needs to be replaced by whatever
  the verified list specifies.
- **Benchmark is a price index.** `^NSEI` excludes dividends while the stocks use
  dividend-adjusted prices, so comparisons against the index favour the stocks.
- **Flagged jumps are not corrected.** Daily moves above 20% are flagged in
  `outputs/tables/data_flagged_jumps.csv` but left in the data. Some are real
  (COVID crash, Adani Feb 2023) and some may be data or corporate-action
  artefacts. They should be checked by hand.
- **Free data.** Yahoo Finance data is unaudited and can be revised between downloads.
  Raw snapshots are timestamped in `data/raw/` and not tracked by git.
