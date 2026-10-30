"""Regime detection on the benchmark and comparison of the three methods.

Run from the repo root:  python -m scripts.run_regimes [--log]
Requires data/processed/benchmark.parquet (written by scripts/run_data.py).
"""
import argparse
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.regimes import UNLABELLED, regime_series  # noqa: E402
from src.utils import get_logger, load_config, set_seed  # noqa: E402

log = get_logger("regimes")
METHODS = ["vol_threshold", "hmm2", "hmm3"]
COLOURS = {"calm": "tab:green", "volatile": "gold", "crisis": "tab:red", UNLABELLED: "lightgrey"}


def runs(labels):
    """Run-length table of consecutive identical labels, ignoring unlabelled dates."""
    lab = labels[labels != UNLABELLED]
    grp = (lab != lab.shift()).cumsum()
    return lab.groupby(grp).agg(regime="first", length="size")


def transition_matrix(labels):
    lab = labels[labels != UNLABELLED]
    pair = pd.DataFrame({"from": lab.iloc[:-1].to_numpy(), "to": lab.iloc[1:].to_numpy()})
    counts = pd.crosstab(pair["from"], pair["to"])
    return counts.div(counts.sum(axis=1), axis=0)


def collapse_calm(labels):
    return labels.where(labels == UNLABELLED, np.where(labels == "calm", "calm", "not_calm"))


def main(write_log=False):
    cfg = load_config()
    set_seed(cfg["seed"])
    td = cfg["trading_days"]
    bench = pd.read_parquet(Path(cfg["data"]["processed_dir"]) / "benchmark.parquet")
    ret = bench["simple"]
    out = {}
    for m in METHODS:
        log.info("computing %s", m)
        out[m] = regime_series(ret, m, cfg)
    labels = pd.DataFrame({m: out[m]["regime"] for m in METHODS})
    tables, figs = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    full = labels.copy()
    for m in ["hmm2", "hmm3"]:
        for c in [c for c in out[m].columns if c.startswith("p_")]:
            full[f"{m}_{c}"] = out[m][c]
    full.to_csv(tables / "regimes_labels.csv", index_label="date")

    common = labels[(labels != UNLABELLED).all(axis=1)]
    lines = [
        f"- Benchmark returns {ret.index[0].date()} to {ret.index[-1].date()} ({len(ret)} days). Labelling uses data up to and "
        f"including each date; the label of date t should drive the portfolio held from t+1.",
        f"- Parameters (config.yaml regimes section): vol window {cfg['regimes']['vol_window']}, quantiles "
        f"{cfg['regimes']['vol_quantiles']}, vol min history {cfg['regimes']['vol_min_history']}, HMM min history "
        f"{cfg['regimes']['hmm_min_history']}, refit every {cfg['regimes']['hmm_refit_every']}, restarts {cfg['regimes']['hmm_restarts']}.",
        f"- Dates labelled by all three methods: {len(common)} ({common.index[0].date()} to {common.index[-1].date()}).",
    ]

    # per-method summaries
    dur_rows, vol_rows, diag_rows = [], [], []
    for m in METHODS:
        lab = labels[m]
        counts = lab.value_counts()
        r = runs(lab)
        for reg, g in r.groupby("regime"):
            dur_rows.append({"method": m, "regime": reg, "spells": len(g), "avg_duration_days": g["length"].mean(),
                             "max_duration_days": g["length"].max()})
        for reg in [x for x in lab.unique() if x != UNLABELLED]:
            sel = ret[lab == reg]
            vol_rows.append({"method": m, "regime": reg, "days": len(sel),
                             "ann_vol": sel.std() * np.sqrt(td), "ann_mean": sel.mean() * td})
        tm = transition_matrix(lab)
        tm.to_csv(tables / f"regimes_transition_{m}.csv")
        lines.append(f"- {m}: days per regime {counts.to_dict()}; empirical transition matrix (row = from):\n"
                     + "\n".join("    " + ln for ln in tm.round(4).to_string().splitlines()))
        if m != "vol_threshold":
            o = out[m].dropna(subset=["refit_date"])
            fits = o.drop_duplicates("refit_date")
            diag_rows.append({"method": m, "refits": len(fits),
                              "refits_not_converged": int((fits["fit_converged"] == 0).sum()),
                              "refits_all_restarts_failed": int(fits["fit_failed"].sum()),
                              "median_restarts_converged": float(fits["restarts_converged"].median())})
    durations, vols, diag = pd.DataFrame(dur_rows), pd.DataFrame(vol_rows), pd.DataFrame(diag_rows)
    durations.to_csv(tables / "regimes_durations.csv", index=False)
    vols.to_csv(tables / "regimes_vol_by_regime.csv", index=False)
    diag.to_csv(tables / "regimes_hmm_fit_diagnostics.csv", index=False)
    for _, row in durations.iterrows():
        lines.append(f"- duration {row['method']}/{row['regime']}: {int(row['spells'])} spells, mean {row['avg_duration_days']:.1f} days, "
                     f"max {int(row['max_duration_days'])}")
    for _, row in vols.iterrows():
        lines.append(f"- realised annualised vol {row['method']}/{row['regime']}: {row['ann_vol']:.4f} over {int(row['days'])} days "
                     f"{'(partly by construction: the label is a function of recent vol)' if row['method'] == 'vol_threshold' else ''}")
    for _, row in diag.iterrows():
        lines.append(f"- {row['method']} fit diagnostics: {int(row['refits'])} refits, {int(row['refits_not_converged'])} not converged "
                     f"(best restart), {int(row['refits_all_restarts_failed'])} with all restarts failed, median restarts converged "
                     f"{row['median_restarts_converged']:.1f} of {cfg['regimes']['hmm_restarts']}")

    # agreement and confusion tables on commonly labelled dates
    agree_rows = []
    for a, b in [("vol_threshold", "hmm3"), ("vol_threshold", "hmm2"), ("hmm2", "hmm3")]:
        conf = pd.crosstab(common[a], common[b])
        conf.to_csv(tables / f"regimes_confusion_{a}_vs_{b}.csv")
        same = a != "hmm2" and b != "hmm2"
        exact = float((common[a] == common[b]).mean()) if same else np.nan
        calm = float((collapse_calm(common[a]) == collapse_calm(common[b])).mean())
        agree_rows.append({"pair": f"{a} vs {b}", "exact_agreement": exact, "calm_vs_not_calm_agreement": calm})
        lines.append(f"- agreement {a} vs {b}: exact {exact:.3f}" if same else f"- agreement {a} vs {b}: exact n/a (different label sets)")
        lines[-1] += f", calm-vs-not-calm {calm:.3f}; confusion (rows {a}, columns {b}):\n" + "\n".join(
            "    " + ln for ln in conf.to_string().splitlines())
    pd.DataFrame(agree_rows).to_csv(tables / "regimes_agreement.csv", index=False)

    # figure
    fig, axes = plt.subplots(4, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1, 1, 1]})
    axes[0].plot(bench.index, bench["price"], color="black", lw=1)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("NIFTY 50 price index\n(excludes dividends)")
    for ax, m in zip(axes[1:], METHODS):
        for reg, colour in COLOURS.items():
            ax.fill_between(labels.index, 0, 1, where=(labels[m] == reg).to_numpy(), color=colour, step="mid",
                            label=reg, linewidth=0)
        ax.set_yticks([])
        ax.set_ylabel(m, rotation=0, ha="right", va="center")
    for name, (s, e) in cfg["data"]["plots"]["shaded_periods"].items():
        for ax in axes:
            ax.axvspan(pd.Timestamp(s), pd.Timestamp(e), facecolor="none", edgecolor="blue", hatch="//", alpha=0.5,
                       label=name if ax is axes[0] else None)
    axes[0].legend(loc="upper left", fontsize=8)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in COLOURS.values()]
    axes[-1].legend(handles, list(COLOURS), loc="lower left", ncol=4, fontsize=7)
    fig.suptitle("Regime labels (no look-ahead; hatched = COVID crash window and 2022)", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs / "regimes_over_time.png", dpi=150)
    plt.close(fig)

    print("\n".join(lines))
    if write_log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Regime detection (scripts/run_regimes.py)\n" + "\n".join(lines) + "\n")
        log.info("appended to %s", cfg["paths"]["results_log"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    main(ap.parse_args().log)
