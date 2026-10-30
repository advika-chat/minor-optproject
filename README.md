# nifty-wdro

Regime-aware Wasserstein distributionally robust portfolio optimisation on the NIFTY 50.

Status: project scaffold only; no analysis code yet.

## Setup

Run everything from the repo root.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

All seeds, dates and parameters are in `config.yaml`.

## Layout

- `src/` - library modules (data, Markowitz, sensitivity, robust, WDRO, regimes, game, Monte Carlo, backtest, metrics, plotting)
- `scripts/` - runnable entry points
- `tests/` - pytest suite
- `data/raw`, `data/processed` - downloaded and derived data (raw is not tracked)
- `outputs/figures`, `outputs/tables` - generated results
- `docs/results_log.md` - running log of key numbers and findings
