# Paper figures: captions and LaTeX snippets

All figures are produced by `python -m scripts.make_paper_figures` from existing tables in `outputs/tables` (nothing is re-estimated), saved in `outputs/figures/paper/` at 3.5 in wide, 300 dpi, text of at least 8 pt, with no in-figure title. Colours are from the Okabe-Ito palette and every series also differs in line style or marker, so the figures can be read in greyscale. Numbers in the captions are taken from the tables named under each figure and from the console output saved in `outputs/tables/qa2_make_paper_figures_output_qa2.txt`.

The section names below are suggestions for where each figure belongs; the report itself is not part of this repository.

---

## Figure 1: net wealth

- File: `outputs/figures/paper/fig01_wealth_qa2.png`
- Source: `outputs/tables/daily_returns_bt.csv` (wealth is the cumulative product of one plus the daily net return)
- Caption: Net wealth of the five primary portfolios, 1/N and the NIFTY 50 price index on a log scale, 2017-01-27 to 2026-10-29 (2,405 days, 10 bps transaction cost, start = 1); final wealth is 5.36 for 1/N and for plug-in max-Sharpe, 5.16 for regime-aware DR-CVaR, 4.86 for DR-MSD, 4.62 for regime-aware DR-MSD, 4.57 for DR-CVaR and 2.67 for the index, which is a price index and excludes dividends while the portfolio prices are dividend-adjusted.
- Report section: Backtest results

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\columnwidth]{outputs/figures/paper/fig01_wealth_qa2.png}
  \caption{Net wealth of the five primary portfolios, 1/N and the NIFTY 50 price index on a log scale, 2017-01-27 to 2026-10-29 (2,405 days, 10 bps transaction cost, start $=1$); final wealth is 5.36 for 1/N and for plug-in max-Sharpe, 5.16 for regime-aware DR-CVaR, 4.86 for DR-MSD, 4.62 for regime-aware DR-MSD, 4.57 for DR-CVaR and 2.67 for the index, which is a price index and excludes dividends while the portfolio prices are dividend-adjusted.}
  \label{fig:wealth}
\end{figure}
```

## Figure 2: drawdown

- File: `outputs/figures/paper/fig02_drawdown_qa2.png`
- Source: `outputs/tables/daily_returns_bt.csv`
- Caption: Drawdown from the running peak of net wealth for the same portfolios and the index; the deepest drawdowns, all on 2020-03-23, are -29.8% (regime-aware DR-CVaR), -31.1% (plug-in max-Sharpe), -33.7% (DR-MSD, both versions), -36.3% (DR-CVaR and 1/N) and -38.4% (index), while in 2022 and 2025 the plug-in portfolio's drawdowns (-23% and -24%) are deeper than those of the other portfolios (-14% to -18% and about -16%).
- Report section: Backtest results

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\columnwidth]{outputs/figures/paper/fig02_drawdown_qa2.png}
  \caption{Drawdown from the running peak of net wealth for the same portfolios and the index; the deepest drawdowns, all on 2020-03-23, are $-29.8\%$ (regime-aware DR-CVaR), $-31.1\%$ (plug-in max-Sharpe), $-33.7\%$ (DR-MSD, both versions), $-36.3\%$ (DR-CVaR and 1/N) and $-38.4\%$ (index), while in 2022 and 2025 the plug-in portfolio's drawdowns ($-23\%$ and $-24\%$) are deeper than those of the other portfolios ($-14\%$ to $-18\%$ and about $-16\%$).}
  \label{fig:drawdown}
\end{figure}
```

## Figure 3: backtest price of robustness

- File: `outputs/figures/paper/fig03_price_of_robustness_qa2.png`
- Source: `outputs/tables/price_of_robustness_bt.csv`, reference levels from `outputs/tables/backtest_summary_bt.csv`
- Caption: Net Sharpe ratio and CVaR95 of daily net returns for fixed-radius DR-CVaR and DR-MSD portfolios over the 2017-2026 backtest (a hindsight curve, not a selectable rule): DR-CVaR rises from a Sharpe of 0.613 to 0.768 (equal to 1/N) at a radius of 0.1 and above, while DR-MSD starts at the plug-in level of 0.744, dips to 0.681 at 0.046 and reaches 0.768 only at radii of 2 and above, beyond the primary grid whose last point is 0.215 (open markers); CVaR95 moves from 1.87% to 2.36% for DR-CVaR.
- Report section: Backtest results, price of robustness

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\columnwidth]{outputs/figures/paper/fig03_price_of_robustness_qa2.png}
  \caption{Net Sharpe ratio and CVaR95 of daily net returns for fixed-radius DR-CVaR and DR-MSD portfolios over the 2017--2026 backtest (a hindsight curve, not a selectable rule): DR-CVaR rises from a Sharpe of 0.613 to 0.768 (equal to 1/N) at a radius of 0.1 and above, while DR-MSD starts at the plug-in level of 0.744, dips to 0.681 at 0.046 and reaches 0.768 only at radii of 2 and above, beyond the primary grid whose last point is 0.215 (open markers); CVaR95 moves from 1.87\% to 2.36\% for DR-CVaR.}
  \label{fig:price-of-robustness}
\end{figure}
```

## Figure 4: Monte Carlo, hypothesis H2

- File: `outputs/figures/paper/fig04_mc_h2_qa2.png`
- Source: `outputs/tables/mc_h2_mc.csv`
- Caption: Mean paired difference in true out-of-sample Sharpe between cross-validated DRO and plug-in max-Sharpe in the known-truth Monte Carlo, by training length, with 95% intervals: -0.0079 at T = 126, -0.0049 at T = 252 and +0.0001 at T = 504; the difference-in-differences is -0.0080 with interval [-0.0117, -0.0043], the wrong sign for H2, which is therefore not supported.
- Report section: Monte Carlo study

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\columnwidth]{outputs/figures/paper/fig04_mc_h2_qa2.png}
  \caption{Mean paired difference in true out-of-sample Sharpe between cross-validated DRO and plug-in max-Sharpe in the known-truth Monte Carlo, by training length, with 95\% intervals: $-0.0079$ at $T=126$, $-0.0049$ at $T=252$ and $+0.0001$ at $T=504$; the difference-in-differences is $-0.0080$ with interval $[-0.0117,-0.0043]$, the wrong sign for H2, which is therefore not supported.}
  \label{fig:mc-h2}
\end{figure}
```

## Figure 5: Monte Carlo ablation

- File: `outputs/figures/paper/fig05_mc_ablation_qa2.png`
- Source: `outputs/tables/mc_ablation_mc.csv`
- Caption: Paired true-Sharpe differences with 95% intervals at each training length: cross-validated DRO is better than DRO at radius zero (+0.0088, +0.0127, +0.0157 at T = 126, 252, 504) but worse than 1/N (-0.0377, -0.0255, -0.0143), and DRO at radius zero is worse than plug-in max-Sharpe (-0.0167, -0.0176, -0.0156); all nine intervals exclude zero.
- Report section: Monte Carlo study, ablation

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\columnwidth]{outputs/figures/paper/fig05_mc_ablation_qa2.png}
  \caption{Paired true-Sharpe differences with 95\% intervals at each training length: cross-validated DRO is better than DRO at radius zero ($+0.0088$, $+0.0127$, $+0.0157$ at $T=126,252,504$) but worse than 1/N ($-0.0377$, $-0.0255$, $-0.0143$), and DRO at radius zero is worse than plug-in max-Sharpe ($-0.0167$, $-0.0176$, $-0.0156$); all nine intervals exclude zero.}
  \label{fig:mc-ablation}
\end{figure}
```

## Figure 6: forest plot of backtest paired differences

- File: `outputs/figures/paper/fig06_forest_qa2.png`
- Source: `outputs/tables/backtest_bootstrap_bt.csv`
- Caption: Paired differences of the four primary DR portfolios against plug-in max-Sharpe and against 1/N in the backtest, with circular block-bootstrap 95% intervals (block 21 days, 5,000 resamples): none of the eight Sharpe or eight maximum-drawdown intervals excludes zero, while all eight CVaR95 intervals do, with DR portfolios 0.17 to 0.27 percentage points lower than plug-in and 0.11 to 0.21 percentage points lower than 1/N.
- Report section: Backtest results, paired tests

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\columnwidth]{outputs/figures/paper/fig06_forest_qa2.png}
  \caption{Paired differences of the four primary DR portfolios against plug-in max-Sharpe and against 1/N in the backtest, with circular block-bootstrap 95\% intervals (block 21 days, 5,000 resamples): none of the eight Sharpe or eight maximum-drawdown intervals excludes zero, while all eight CVaR95 intervals do, with DR portfolios 0.17 to 0.27 percentage points lower than plug-in and 0.11 to 0.21 percentage points lower than 1/N.}
  \label{fig:forest}
\end{figure}
```

## Figure 7: efficient frontier

- File: `outputs/figures/paper/fig07_frontier_qa2.png`
- Source: `outputs/tables/frontier_own.csv` (own solver frontier), `outputs/tables/markowitz_insample_summary.csv`
- Caption: In-sample capped (10%) long-only mean-variance frontier for the 40 stocks, with the minimum-variance portfolio (return 13.2%, volatility 12.9%), the maximum-Sharpe portfolio (23.8%, 17.2%, Sharpe 1.005) and 1/N (17.8%, 16.2%, Sharpe 0.697); all values are fitted and evaluated on the same data and are not out-of-sample results.
- Report section: In-sample Markowitz analysis

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\columnwidth]{outputs/figures/paper/fig07_frontier_qa2.png}
  \caption{In-sample capped (10\%) long-only mean-variance frontier for the 40 stocks, with the minimum-variance portfolio (return 13.2\%, volatility 12.9\%), the maximum-Sharpe portfolio (23.8\%, 17.2\%, Sharpe 1.005) and 1/N (17.8\%, 16.2\%, Sharpe 0.697); all values are fitted and evaluated on the same data and are not out-of-sample results.}
  \label{fig:frontier}
\end{figure}
```

## Figure 8: sensitivity to the mean estimate

- File: `outputs/figures/paper/fig08_mu_sensitivity_qa2.png`
- Source: `outputs/tables/mu_sensitivity_summary_sens.csv`
- Caption: Effect of perturbing the mean vector by k sampling standard errors (1,000 seeded draws per k; in-sample estimates): the median L1 change of the max-Sharpe weights is 0.27, 0.55 and 0.92 for k = 0.25, 0.5 and 1, and the mean Sharpe loss with the base mean as truth is 0.017, 0.071 and 0.175; bootstrap 95% intervals are drawn but are narrower than the markers.
- Report section: Sensitivity analysis

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=\columnwidth]{outputs/figures/paper/fig08_mu_sensitivity_qa2.png}
  \caption{Effect of perturbing the mean vector by $k$ sampling standard errors (1,000 seeded draws per $k$; in-sample estimates): the median L1 change of the max-Sharpe weights is 0.27, 0.55 and 0.92 for $k=0.25$, $0.5$ and $1$, and the mean Sharpe loss with the base mean as truth is 0.017, 0.071 and 0.175; bootstrap 95\% intervals are drawn but are narrower than the markers.}
  \label{fig:mu-sensitivity}
\end{figure}
```
