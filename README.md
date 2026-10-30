# nifty-wdro

Regime-aware Wasserstein distributionally robust portfolio optimisation on the NIFTY 50.

Status: the analysis is implemented and has been run: data pipeline, in-sample Markowitz and robust comparators, Wasserstein DRO (CVaR and mean-standard-deviation), regime detection, per-regime epsilon selection, an own QP solver with KKT checks, sensitivity and game-theory analyses, a known-truth Monte Carlo study (hypotheses H1 and H2) and a rolling-window backtest. An independent validation of the numbers is in `docs/qa_report_qa.md`. The report itself is not part of this repository.

## Setup

Run everything from the repo root.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

All seeds, dates and parameters are in `config.yaml`. Stock prices are dividend-adjusted; the benchmark is the NIFTY 50 price index (excludes dividends).

## Running

Scripts are run as modules from the repo root (most accept `--log` to append the key numbers to `docs/results_log.md`). They read `data/processed` unless noted.

| Command | What it does |
|---|---|
| `python -m scripts.run_data` | download, clean and summarise prices (needs network; `--refresh`, `--rebuild-tickers`, `--inspect TICKER DATE`) |
| `python -m scripts.run_markowitz_frontier` | in-sample Markowitz frontier |
| `python -m scripts.run_robust_demo` | robust comparators on one window |
| `python -m scripts.run_wdro_sweep` | Wasserstein radius sweep |
| `python -m scripts.run_regimes` | volatility and HMM regimes |
| `python -m scripts.run_eps_selection` | per-regime epsilon selection (`--find-grid`) |
| `python -m scripts.run_stress` | calm/stress regimes and the continuous stress level |
| `python -m scripts.run_solver_own` | own active-set QP solver and KKT report |
| `python -m scripts.run_sensitivity` | mu, Sigma, period, cap, rf and scenario sensitivity |
| `python -m scripts.run_game` | matrix games, ellipsoid saddle point, Wasserstein bounds |
| `python -m scripts.run_montecarlo` | known-truth Monte Carlo (long run, checkpointed) |
| `python -m scripts.run_backtest` | rolling-window backtest (long run, checkpointed) |
| `python -m scripts.run_qa` | independent validation (`--tests`, `--clean`, `--hmm`, `--eps` for the slow items) |

Tests:

```bash
.venv/Scripts/python -m pytest -q
```

## Layout

- `src/` - library modules (data, Markowitz, sensitivity, robust, WDRO, regimes, game, Monte Carlo, backtest, metrics, plotting)
- `scripts/` - runnable entry points
- `tests/` - pytest suite
- `data/raw`, `data/processed` - downloaded and derived data (raw is not tracked)
- `outputs/tables`, `outputs/figures` - generated results (suffixes `_mc` Monte Carlo, `_bt` backtest, `_qa` validation)
- `docs/results_log.md` - running log of key numbers and findings
- `docs/limitations.md` - data and method limitations
- `docs/hypotheses.md`, `docs/montecarlo_notes.md`, `docs/backtest_spec.md`, `docs/backtest_notes.md`, `docs/kkt_duality_notes.md`, `docs/game_theory_notes.md` - specifications and notes
