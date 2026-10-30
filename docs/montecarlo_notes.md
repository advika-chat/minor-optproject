# Monte Carlo with known ground truth

This document describes the simulation study that tests hypotheses H1 and H2 (docs/hypotheses.md). All parameters
are in `config.yaml`, section `montecarlo`; they were fixed before the study was run, with one addition described below.

## Idea

In real data the true mean and covariance of returns are unknown, so out-of-sample performance has to be estimated
from a finite test sample. Here the truth is chosen by us. We simulate a short training window from it, build each
portfolio from that window alone, and then evaluate the portfolio exactly under the truth: the true annual Sharpe
ratio of weights w is (mu_true'w - rf) / sqrt(w' Sigma_true w). No test sample, and so no test-sample noise, is involved.
Repeating this for many independent training windows gives the distribution of the true out-of-sample Sharpe of every method.

## The assumed truth

* Sigma_true is the full-sample annualised covariance of the 40 stocks in the data.
* mu_true = grand_mean + s (mu_hat_full - grand_mean), where mu_hat_full is the full-sample annualised mean vector and grand_mean
  is its average over stocks. The primary study uses s = 0.5: half of the spread of historical means is treated as real. s = 1
  treats all of it as real, s = 0 treats none of it as real (all stocks have the same expected return).
* Daily returns are drawn independently over days from a multivariate Student-t distribution with 5 degrees of freedom,
  scaled so that the daily covariance is exactly Sigma_true / 252 and the daily mean is exactly mu_true / 252. The Gaussian
  case is a sensitivity run.
* Portfolios are long-only, fully invested, with a per-stock cap of 10% and risk-free rate 6.5%, as in the rest of the project.

## Protocol

For each training length T in {126, 252, 504} days, 500 independent runs. In each run one training window is simulated and
all methods below see the same window (the array is read-only and its hash is checked before and after).

1. plug-in max-Sharpe: sample mean and covariance, maximum Sharpe ratio (own active-set solver with golden-section search);
2. DRO with cross-validated epsilon (the portfolio tested in H1 and H2);
3. DRO at epsilon = 0 (ablation);
4. DRO at each fixed epsilon of the grid (the price-of-robustness curve; weights from the whole window);
5. equal weight 1/N;
6. plug-in minimum variance;
7. Ledoit-Wolf covariance with the sample mean, maximum Sharpe ratio;
8. the oracle: maximum Sharpe ratio under the true parameters (an upper bound for every other row).

For each portfolio the study records the true Sharpe ratio, expected return, volatility, number of positions, effective number
of positions (1 / sum of squared weights), L1 distance to the oracle weights and regret (oracle Sharpe minus the portfolio's Sharpe).

## The distributionally robust portfolio used here

The Wasserstein DRO mean-variance problem in `src/wdro.py` minimises sqrt(w' V w) + epsilon ||w||_p over capped long-only
portfolios, where V is the 1/T covariance of the daily returns in the window and p is dual to the ground norm. That objective has no
risk-aversion weight and no mean term, and at epsilon = 0 it is the minimum-variance portfolio, so it cannot be calibrated to the
max-Sharpe portfolio. The study therefore uses

    minimise  sqrt(w' V w) + epsilon ||w||_p - theta (m'w - rf/252),

with m the sample mean of the daily returns. At theta = 0 this is exactly the objective in `src/wdro.py` (checked). The weight theta
(the role of the risk-aversion parameter) is set once, on the true parameters only: theta = sqrt(252) / S*, where S* is the oracle
max-Sharpe ratio. Because (mu'w - rf) / sqrt(w' Sigma w) <= S* for every feasible w, the oracle max-Sharpe portfolio is exactly the
minimiser of sqrt(w' Sigma w) - (mu'w - rf)/S* under the true parameters, so with the truth as input and epsilon = 0 the
mean-variance portfolio equals the max-Sharpe portfolio (verified: see the calibration check below). The same theta is used in every run of a scenario.
Epsilon is in daily-return units.

**Choosing epsilon.** Inside each run, five-fold cross-validation on the T training days: contiguous blocks of T/5 days are held out in turn,
the portfolio is built on the rest for every epsilon of the grid, and the pooled out-of-fold excess Sharpe ratio is computed. The epsilon with the
highest value is chosen, ties going to the smaller epsilon. Contiguous blocks are valid here because the simulated returns are independent
over days; this differs from the walk-forward validation used on the real data, where returns are dependent over time.
The grid is the 14-point grid of `eps_selection_ext` (1e-5 to 0.2154, log-spaced).

## From hypotheses to tests

* **H1.** Paired difference d_r = true Sharpe(DRO, CV epsilon) - true Sharpe(plug-in max-Sharpe) for run r at T = 252. Bootstrap (10,000
  resamples over runs) gives the standard error and the 95% percentile interval of the mean. H1 is supported if the interval lies above 0;
  it is falsified if the interval includes 0 or lies below it. T = 126 and 504 are reported for context.
* **H2.** Delta(T) is the mean of d_r at training length T. The difference-in-differences is Delta(126) - Delta(504). The runs at different T are
  independent, so each Delta is bootstrapped separately and the bootstrap differences give the 95% interval. H2 is supported if the interval lies above 0.
* **Ablation and benchmark.** Paired differences DRO - DRO at epsilon = 0, DRO - 1/N and (DRO at epsilon = 0) - plug-in max-Sharpe, with the same intervals.
* **Always reported, not tested.** The price-of-robustness curve (true Sharpe, volatility and number of positions against epsilon),
  the distribution of the chosen epsilon, and the share of runs in which the chosen portfolio is the 1/N portfolio.

## Sensitivity runs (reported, not tested)

Each changes one thing relative to the primary study; 200 runs each, T = 252 unless stated.

* `s1.0`: all of the historical spread of mean returns is treated as real.
* `s0.0`: all stocks have the same true expected return.
* `gaussian`: Gaussian instead of Student-t returns.
* `ext_grid`: the primary setting with extra epsilon values 0.5, 1, 2, 5 and 10 appended to the grid, at T = 126, 252 and 504. This scenario was added after
  a probe, run before the main study, of the portfolio weights alone (no performance numbers were looked at): with the mean term the portfolio only becomes
  exactly 1/N at epsilon of about 2 to 5, above the largest value of the pre-specified grid. The primary study keeps the pre-specified grid unchanged, so
  H1 and H2 are tested on it; `ext_grid` shows what happens when the grid reaches the 1/N portfolio.

## Checks run by the script

Simulated moments match the truth within Monte Carlo error; the oracle's true Sharpe is at least that of every method in every run; epsilon = 0
equals the plug-in mean-variance solution (compared with the own active-set solver at the same return) and theta = 0 equals the objective of `src/wdro.py`;
at the plateau epsilon the weights equal 1/N; with the true parameters as input the plug-in max-Sharpe and the DR mean-variance (epsilon = 0) equal the oracle;
re-running stored (T, run) pairs reproduces them; the average of the estimated means over runs equals the true means within Monte Carlo error; all methods in a run used the same simulated data.

## Results

<!-- results:start -->
**H1 (T = 252): FALSIFIED.** Mean paired difference -0.0049 (median -0.0038), bootstrap SE 0.0012, 95% interval [-0.0073, -0.0025]; DRO better in 41.6% of runs.

| T | primary | mean_diff | median_diff | boot_se | ci_lo | ci_hi | share_dro_wins | verdict |
|---|---|---|---|---|---|---|---|---|
| 126 | False | -0.0079 | -0.0064 | 0.0013 | -0.0105 | -0.0054 | 0.3720 | (not the primary test) |
| 252 | True | -0.0049 | -0.0038 | 0.0012 | -0.0073 | -0.0025 | 0.4160 | FALSIFIED |
| 504 | False | 0.0001 | -0.0012 | 0.0013 | -0.0025 | 0.0027 | 0.4700 | (not the primary test) |

**H2: NOT SUPPORTED.** Delta(126) = -0.0079, Delta(504) = +0.0001; difference-in-differences -0.0080, 95% interval [-0.0117, -0.0043].

| quantity | T | estimate | ci_lo | ci_hi | verdict |
|---|---|---|---|---|---|
| Delta(T=126) | 126.0000 | -0.0079 | -0.0104 | -0.0054 |  |
| Delta(T=252) | 252.0000 | -0.0049 | -0.0073 | -0.0025 |  |
| Delta(T=504) | 504.0000 | 0.0001 | -0.0025 | 0.0027 |  |
| DiD = Delta(126) - Delta(504) |  | -0.0080 | -0.0117 | -0.0043 | NOT SUPPORTED |

Ablation and benchmark:

| T | comparison | mean_diff | median_diff | boot_se | ci_lo | ci_hi | share_first_wins |
|---|---|---|---|---|---|---|---|
| 126 | DRO (CV epsilon) minus DRO epsilon=0 | 0.0088 | 0.0000 | 0.0008 | 0.0072 | 0.0105 | 0.6040 |
| 126 | DRO (CV epsilon) minus 1/N | -0.0377 | -0.0332 | 0.0019 | -0.0415 | -0.0342 | 0.1740 |
| 126 | DRO epsilon=0 minus plug-in max-Sharpe | -0.0167 | -0.0131 | 0.0012 | -0.0190 | -0.0145 | 0.2380 |
| 252 | DRO (CV epsilon) minus DRO epsilon=0 | 0.0127 | 0.0000 | 0.0010 | 0.0107 | 0.0147 | 0.6740 |
| 252 | DRO (CV epsilon) minus 1/N | -0.0255 | -0.0261 | 0.0017 | -0.0289 | -0.0222 | 0.2580 |
| 252 | DRO epsilon=0 minus plug-in max-Sharpe | -0.0176 | -0.0135 | 0.0010 | -0.0196 | -0.0156 | 0.2300 |
| 504 | DRO (CV epsilon) minus DRO epsilon=0 | 0.0157 | 0.0000 | 0.0012 | 0.0133 | 0.0180 | 0.7100 |
| 504 | DRO (CV epsilon) minus 1/N | -0.0143 | -0.0097 | 0.0017 | -0.0178 | -0.0110 | 0.3960 |
| 504 | DRO epsilon=0 minus plug-in max-Sharpe | -0.0156 | -0.0119 | 0.0009 | -0.0174 | -0.0138 | 0.2240 |

Summary by training length (primary scenario):

| T | method | mean_true_sharpe | ci_lo | ci_hi | mean_true_vol | mean_n_pos | mean_regret |
|---|---|---|---|---|---|---|---|
| 126 | plugin_maxsharpe | 0.6674 | 0.6639 | 0.6708 | 0.1639 | 12.4520 | 0.1792 |
| 126 | dro_cv | 0.6595 | 0.6558 | 0.6631 | 0.1741 | 12.9700 | 0.1872 |
| 126 | dro_eps0 | 0.6507 | 0.6469 | 0.6545 | 0.1771 | 10.8800 | 0.1960 |
| 126 | equal_weight | 0.6972 | 0.6972 | 0.6972 | 0.1617 | 40.0000 | 0.1494 |
| 126 | plugin_minvar | 0.6905 | 0.6884 | 0.6925 | 0.1383 | 16.9400 | 0.1561 |
| 126 | lw_maxsharpe | 0.6676 | 0.6641 | 0.6710 | 0.1645 | 13.1420 | 0.1790 |
| 126 | oracle | 0.8466 | 0.8466 | 0.8466 | 0.1561 | 17.0000 | 0.0000 |
| 252 | plugin_maxsharpe | 0.6766 | 0.6734 | 0.6797 | 0.1622 | 12.7780 | 0.1700 |
| 252 | dro_cv | 0.6717 | 0.6684 | 0.6751 | 0.1710 | 14.6740 | 0.1749 |
| 252 | dro_eps0 | 0.6590 | 0.6556 | 0.6625 | 0.1753 | 11.0780 | 0.1876 |
| 252 | equal_weight | 0.6972 | 0.6972 | 0.6972 | 0.1617 | 40.0000 | 0.1494 |
| 252 | plugin_minvar | 0.6934 | 0.6916 | 0.6951 | 0.1351 | 17.8280 | 0.1533 |
| 252 | lw_maxsharpe | 0.6768 | 0.6737 | 0.6800 | 0.1625 | 13.1000 | 0.1698 |
| 252 | oracle | 0.8466 | 0.8466 | 0.8466 | 0.1561 | 17.0000 | 0.0000 |
| 504 | plugin_maxsharpe | 0.6828 | 0.6794 | 0.6862 | 0.1625 | 13.0140 | 0.1638 |
| 504 | dro_cv | 0.6829 | 0.6796 | 0.6863 | 0.1685 | 17.6060 | 0.1637 |
| 504 | dro_eps0 | 0.6672 | 0.6635 | 0.6709 | 0.1744 | 11.5140 | 0.1794 |
| 504 | equal_weight | 0.6972 | 0.6972 | 0.6972 | 0.1617 | 40.0000 | 0.1494 |
| 504 | plugin_minvar | 0.6944 | 0.6929 | 0.6958 | 0.1328 | 18.2240 | 0.1522 |
| 504 | lw_maxsharpe | 0.6829 | 0.6795 | 0.6864 | 0.1627 | 13.2520 | 0.1637 |
| 504 | oracle | 0.8466 | 0.8466 | 0.8466 | 0.1561 | 17.0000 | 0.0000 |

Sensitivity:

| scenario | T | n_runs | oracle_sharpe | plugin_mean_sharpe | dro_mean_sharpe | equal_weight_mean_sharpe | diff_dro_minus_plugin | diff_ci_lo | diff_ci_hi | diff_dro_minus_equal | diff2_ci_lo | diff2_ci_hi | plateau_share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| primary | 252 | 500 | 0.8466 | 0.6766 | 0.6717 | 0.6972 | -0.0049 | -0.0073 | -0.0025 | -0.0255 | -0.0290 | -0.0221 | 0.0000 |
| s1.0 | 252 | 200 | 1.0051 | 0.6874 | 0.7038 | 0.6972 | 0.0164 | 0.0098 | 0.0230 | 0.0066 | -0.0027 | 0.0157 | 0.0000 |
| s0.0 | 252 | 200 | 0.8723 | 0.7081 | 0.6783 | 0.6972 | -0.0299 | -0.0351 | -0.0246 | -0.0189 | -0.0269 | -0.0111 | 0.0000 |
| gaussian | 252 | 200 | 0.8466 | 0.6776 | 0.6726 | 0.6972 | -0.0050 | -0.0090 | -0.0012 | -0.0246 | -0.0297 | -0.0196 | 0.0000 |
| ext_grid | 126 | 200 | 0.8466 | 0.6683 | 0.6762 | 0.6972 | 0.0079 | 0.0031 | 0.0127 | -0.0210 | -0.0254 | -0.0167 | 0.2250 |
| ext_grid | 252 | 200 | 0.8466 | 0.6742 | 0.6858 | 0.6972 | 0.0116 | 0.0073 | 0.0159 | -0.0114 | -0.0156 | -0.0074 | 0.2600 |
| ext_grid | 504 | 200 | 0.8466 | 0.6823 | 0.6868 | 0.6972 | 0.0045 | 0.0002 | 0.0090 | -0.0104 | -0.0149 | -0.0059 | 0.2400 |
<!-- results:end -->
