"""Paper-ready figures built from existing result tables only (nothing is re-estimated or re-optimised).

Run from the repo root:  python -m scripts.make_paper_figures

Reads outputs/tables/*.csv and writes outputs/figures/paper/fig*_qa2.png (single column, 3.5 in wide, 300 dpi, fonts of at least 8 pt, no in-figure title,
colour-blind-safe palette with distinct line styles and markers so the figures survive greyscale). Existing figures are not touched.
A figure whose input table is missing is skipped and reported. Exit code 1 if a figure fails to build or fails the size and font checks.
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, NullLocator
from matplotlib.text import Text

ROOT = Path.cwd()
T = ROOT / "outputs" / "tables"
OUT = ROOT / "outputs" / "figures" / "paper"

# presentation constants (no result depends on them)
WIDTH_IN, DPI, FONT_PT = 3.5, 300, 8
INDEX_LABEL = "NIFTY 50 price index (excludes dividends)"
PRIMARY_GRID_MAX = 0.215443          # last epsilon of the primary 14-point grid (price_of_robustness_bt in_primary_grid)
BLUE, ORANGE, GREEN, VERMILION, PURPLE, SKY, BLACK, GREY = "#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#000000", "#666666"
LEG_ROW_IN = 0.165

plt.rcParams.update({"font.size": FONT_PT, "axes.labelsize": FONT_PT, "xtick.labelsize": FONT_PT, "ytick.labelsize": FONT_PT, "legend.fontsize": FONT_PT,
                     "axes.linewidth": 0.7, "lines.linewidth": 1.1, "figure.dpi": DPI, "savefig.dpi": DPI, "axes.grid": True, "grid.alpha": 0.3, "grid.linewidth": 0.5,
                     "font.family": "DejaVu Sans", "mathtext.default": "regular"})

FAILS, SKIPPED, MADE = [], [], []


def need(*names):
    """Return the tables if all exist, else None (the figure is skipped)."""
    missing = [n for n in names if not (T / n).exists()]
    if missing:
        return None, missing
    return [pd.read_csv(T / n) for n in names], []


def layout(fig, h_in, left_in=0.62, right_in=0.12, top_in=0.1, bottom_in=0.5, legend_rows=0, hspace=None):
    bottom = bottom_in + (legend_rows * LEG_ROW_IN + 0.08 if legend_rows else 0.0)
    kw = dict(left=left_in / WIDTH_IN, right=1 - right_in / WIDTH_IN, top=1 - top_in / h_in, bottom=bottom / h_in)
    if hspace is not None:
        kw["hspace"] = hspace
    fig.subplots_adjust(**kw)


def legend_below(fig, handles, labels, ncol=1):
    fig.legend(handles, labels, loc="lower center", ncol=ncol, frameon=False, bbox_to_anchor=(0.5, 0.0), handlelength=3.0, columnspacing=1.0, handletextpad=0.5,
               borderaxespad=0.2)


def check_and_save(fig, name):
    """Font-size check (every visible text >= FONT_PT), save without tight cropping so the width is exactly 3.5 in, check the pixel width."""
    for t in fig.findobj(Text):
        if t.get_visible() and t.get_text().strip() and t.get_fontsize() < FONT_PT - 1e-9:
            raise AssertionError(f"{name}: text '{t.get_text()[:30]}' is {t.get_fontsize()} pt")
    path = OUT / name
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    from PIL import Image
    w, h = Image.open(path).size
    if w != round(WIDTH_IN * DPI):
        raise AssertionError(f"{name}: width {w} px, expected {round(WIDTH_IN * DPI)}")
    MADE.append((name, w, h))
    print(f"  wrote {path.relative_to(ROOT).as_posix()} ({w} x {h} px)")


def pct_fmt(dec=0):
    return FuncFormatter(lambda v, _: f"{v:.{dec}f}")


# ---------------------------------------------------------------- 1 and 2: wealth and drawdown
SERIES = [("plugin_maxsharpe", "Plug-in max-Sharpe", BLUE, "-", 1.0),
          ("drmsd_cv", "DR-MSD (CV epsilon)", ORANGE, "--", 1.1),
          ("drcvar_cv", "DR-CVaR (CV epsilon)", GREEN, "-.", 1.1),
          ("drmsd_regime", "DR-MSD regime-aware", VERMILION, ":", 1.5),
          ("drcvar_regime", "DR-CVaR regime-aware", PURPLE, (0, (5, 1, 1, 1, 1, 1)), 1.1),
          ("equal_weight", "1/N", "#333333", "-", 1.6),
          ("nifty_price_index", INDEX_LABEL, BLACK, (0, (7, 2)), 1.2)]


def fig_wealth_drawdown():
    got, miss = need("daily_returns_bt.csv")
    if got is None:
        SKIPPED.append(("fig01_wealth, fig02_drawdown", miss))
        return
    r = pd.read_csv(T / "daily_returns_bt.csv", index_col=0, parse_dates=True)
    wealth = (1.0 + r[[s[0] for s in SERIES]]).cumprod()
    dd = wealth / wealth.cummax() - 1.0
    print(f"  evaluation window {r.index[0].date()} to {r.index[-1].date()} ({len(r)} days)")
    print("  final net wealth: " + "; ".join(f"{s[1]} {wealth[s[0]].iloc[-1]:.2f}" for s in SERIES))
    print("  maximum drawdown: " + "; ".join(f"{s[1]} {100 * dd[s[0]].min():.1f}%" for s in SERIES))
    for which, name, h, ylab in (("wealth", "fig01_wealth_qa2.png", 4.55, "Net wealth (start = 1, log scale)"), ("dd", "fig02_drawdown_qa2.png", 4.3, "Drawdown from running peak (%)")):
        fig, ax = plt.subplots(figsize=(WIDTH_IN, h))
        for col, lab, c, ls, lw in SERIES:
            ax.plot(r.index, wealth[col] if which == "wealth" else 100 * dd[col], color=c, ls=ls, lw=lw, label=lab)
        if which == "wealth":
            ax.set_yscale("log")
            ax.set_yticks([1, 2, 3, 4, 6])
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
            ax.yaxis.set_minor_locator(NullLocator())
        ax.set_ylabel(ylab)
        ax.set_xlabel("Date")
        ax.xaxis.set_major_locator(mdates.YearLocator(2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        layout(fig, h, legend_rows=len(SERIES), left_in=0.55)
        h_, l_ = ax.get_legend_handles_labels()
        legend_below(fig, h_, l_, ncol=1)
        check_and_save(fig, name)


# ---------------------------------------------------------------- 3: backtest price of robustness
def fig_price_of_robustness():
    got, miss = need("price_of_robustness_bt.csv", "backtest_summary_bt.csv")
    if got is None:
        SKIPPED.append(("fig03_price_of_robustness", miss))
        return
    por, summ = got
    summ = summ.set_index("method")
    ref = {"1/N": ("equal_weight", BLACK, ":", 1.5), "Plug-in max-Sharpe": ("plugin_maxsharpe", GREY, "-.", 1.1)}
    h = 5.3
    fig, axes = plt.subplots(2, 1, figsize=(WIDTH_IN, h), sharex=True)
    for ax, metric, scale, ylab in ((axes[0], "sharpe", 1.0, "Net Sharpe ratio\n(annualised)"), (axes[1], "cvar95_daily", 100.0, "CVaR95 of daily net\nreturns (% of wealth)")):
        for kind, lab, c, ls, mk in (("cvar", "DR-CVaR", BLUE, "-", "o"), ("msd", "DR-MSD", VERMILION, "--", "s")):
            d = por[por.kind == kind].sort_values("eps")
            ax.plot(d.eps, scale * d[metric], color=c, ls=ls, lw=1.0, label=lab)
            inn = d[d.in_primary_grid]
            ax.plot(inn.eps, scale * inn[metric], color=c, ls="none", marker=mk, ms=3.5, label=None)
            ext = d[~d.in_primary_grid]
            if len(ext):
                ax.plot(ext.eps, scale * ext[metric], color=c, ls="none", marker=mk, ms=4.5, mfc="white", mew=1.0, label="DR-MSD, beyond the primary grid")
        for lab, (m, c, ls, lw) in ref.items():
            ax.axhline(scale * summ.loc[m, metric], color=c, ls=ls, lw=lw, label=lab)
        ax.axvline(PRIMARY_GRID_MAX, color=GREY, lw=0.7, ls="-", alpha=0.7, label="End of the primary grid (0.215)")
        ax.set_xscale("log")
        ax.set_ylabel(ylab)
    axes[1].set_xlabel("Wasserstein radius (daily-return units)")
    layout(fig, h, legend_rows=6, left_in=0.8, hspace=0.12)
    hs, ls_ = [], []
    for hh, ll in zip(*axes[0].get_legend_handles_labels()):
        if ll not in ls_:
            hs.append(hh)
            ls_.append(ll)
    legend_below(fig, hs, ls_, ncol=1)
    check_and_save(fig, "fig03_price_of_robustness_qa2.png")
    for kind in ("cvar", "msd"):
        d = por[por.kind == kind].sort_values("eps")
        print(f"  {kind}: Sharpe at the smallest eps {d.sharpe.iloc[0]:.3f}, minimum {d.sharpe.min():.3f} at eps {d.eps[d.sharpe.idxmin()]:.3g}, at the largest eps {d.sharpe.iloc[-1]:.3f}; "
              f"CVaR95 {100 * d.cvar95_daily.iloc[0]:.2f}% to {100 * d.cvar95_daily.iloc[-1]:.2f}%")


# ---------------------------------------------------------------- 4 and 5: Monte Carlo
def fig_mc():
    got, miss = need("mc_h2_mc.csv")
    if got is None:
        SKIPPED.append(("fig04_mc_h2", miss))
    else:
        d = got[0].iloc[:3].copy()
        h = 3.0
        fig, ax = plt.subplots(figsize=(WIDTH_IN, h))
        x = np.arange(3)
        ax.errorbar(x, d.estimate, yerr=[d.estimate - d.ci_lo, d.ci_hi - d.estimate], fmt="o", color=BLUE, mec=BLACK, ms=5, capsize=3, lw=1.1)
        ax.axhline(0, color=BLACK, lw=0.8)
        ax.set_xticks(x, [f"{int(t)}" for t in d["T"]])
        ax.set_xlim(-0.5, 2.5)
        ax.set_xlabel("Training length T (days)")
        ax.set_ylabel("Paired true-Sharpe difference,\nDRO (CV epsilon) minus plug-in")
        layout(fig, h, left_in=1.0)
        check_and_save(fig, "fig04_mc_h2_qa2.png")
        print("  H2 Delta(T): " + "; ".join(f"T={int(r['T'])} {r.estimate:+.4f} [{r.ci_lo:+.4f}, {r.ci_hi:+.4f}]" for _, r in d.iterrows()))
    got, miss = need("mc_ablation_mc.csv")
    if got is None:
        SKIPPED.append(("fig05_mc_ablation", miss))
        return
    ab = got[0]
    spec = [("DRO (CV epsilon) minus DRO epsilon=0", "DRO (CV epsilon) minus DRO at epsilon = 0", BLUE, "///"),
            ("DRO (CV epsilon) minus 1/N", "DRO (CV epsilon) minus 1/N", ORANGE, "..."),
            ("DRO epsilon=0 minus plug-in max-Sharpe", "DRO at epsilon = 0 minus plug-in max-Sharpe", GREEN, "xxx")]
    Ts = sorted(ab["T"].unique())
    h = 4.0
    fig, ax = plt.subplots(figsize=(WIDTH_IN, h))
    bw = 0.26
    for j, (key, lab, c, hatch) in enumerate(spec):
        d = ab[ab.comparison == key].set_index("T").loc[Ts]
        xs = np.arange(len(Ts)) + (j - 1) * bw
        ax.bar(xs, d.mean_diff, width=bw, color=c, edgecolor=BLACK, hatch=hatch, lw=0.6, label=lab)
        ax.errorbar(xs, d.mean_diff, yerr=[d.mean_diff - d.ci_lo, d.ci_hi - d.mean_diff], fmt="none", ecolor=BLACK, capsize=2, lw=0.9)
        print(f"  ablation {key}: " + "; ".join(f"T={int(t)} {v.mean_diff:+.4f} [{v.ci_lo:+.4f}, {v.ci_hi:+.4f}]" for t, v in d.iterrows()))
    ax.axhline(0, color=BLACK, lw=0.8)
    ax.set_xticks(np.arange(len(Ts)), [f"{int(t)}" for t in Ts])
    ax.set_xlabel("Training length T (days)")
    ax.set_ylabel("Paired true-Sharpe difference")
    layout(fig, h, legend_rows=3, left_in=0.65)
    h_, l_ = ax.get_legend_handles_labels()
    legend_below(fig, h_, l_, ncol=1)
    check_and_save(fig, "fig05_mc_ablation_qa2.png")


# ---------------------------------------------------------------- 6: forest plot of backtest paired differences
def fig_forest():
    got, miss = need("backtest_bootstrap_bt.csv")
    if got is None:
        SKIPPED.append(("fig06_forest", miss))
        return
    b = got[0]
    methods = [("drcvar_cv", "DR-CVaR\n(CV epsilon)"), ("drmsd_cv", "DR-MSD\n(CV epsilon)"), ("drcvar_regime", "DR-CVaR\nregime-aware"), ("drmsd_regime", "DR-MSD\nregime-aware")]
    comps = [("plugin_maxsharpe", "versus plug-in max-Sharpe", BLUE, "o", "full"), ("equal_weight", "versus 1/N", VERMILION, "s", "none")]
    metrics = [("sharpe", 1.0, "Sharpe difference (annualised)"), ("cvar95_daily", 100.0, "CVaR95 difference (percentage points\nof daily loss; negative = lower risk)"),
               ("max_drawdown", 100.0, "Max-drawdown difference (percentage\npoints; positive = shallower)")]
    h = 6.9
    fig, axes = plt.subplots(3, 1, figsize=(WIDTH_IN, h))
    for ax, (metric, scale, xlab) in zip(axes, metrics):
        for i, (m, mlab) in enumerate(methods):
            for k, (cv, clab, c, mk, fill) in enumerate(comps):
                row = b[(b.method == m) & (b.versus == cv) & (b.metric == metric)]
                assert len(row) == 1, (m, cv, metric)
                row = row.iloc[0]
                y = i + (k - 0.5) * 0.3
                ax.errorbar(scale * row.estimate, y, xerr=[[scale * (row.estimate - row.ci_lo)], [scale * (row.ci_hi - row.estimate)]], fmt=mk, color=c, mec=c,
                            mfc=c if fill == "full" else "white", ms=4.5, capsize=2.5, lw=1.0, label=clab)
        ax.axvline(0, color=BLACK, lw=0.9)
        ax.set_yticks(range(len(methods)), [m[1] for m in methods])
        ax.set_ylim(len(methods) - 0.4, -0.6)
        ax.set_xlabel(xlab)
        ax.grid(axis="y", alpha=0)
    layout(fig, h, left_in=0.95, right_in=0.12, legend_rows=1, bottom_in=0.75, hspace=0.75)
    hs, ls_ = [], []
    for hh, ll in zip(*axes[0].get_legend_handles_labels()):
        if ll not in ls_:
            hs.append(hh)
            ls_.append(ll)
    legend_below(fig, hs, ls_, ncol=2)
    check_and_save(fig, "fig06_forest_qa2.png")
    for metric, scale, _ in metrics:
        d = b[(b.metric == metric) & (b.versus.isin(["plugin_maxsharpe", "equal_weight"])) & b.method.isin([m[0] for m in methods])]
        print(f"  forest {metric}: {int(d.excludes_zero.eq('yes').sum())} of {len(d)} intervals exclude zero")


# ---------------------------------------------------------------- 7: efficient frontier
def fig_frontier():
    got, miss = need("frontier_own.csv", "markowitz_insample_summary.csv")
    if got is None:
        SKIPPED.append(("fig07_frontier", miss))
        return
    fr, sm = got
    sm = sm.rename(columns={sm.columns[0]: "name"}).set_index("name")
    h = 3.3
    fig, ax = plt.subplots(figsize=(WIDTH_IN, h))
    ax.plot(100 * fr.vol_own, 100 * fr.return_own, color=BLUE, lw=1.6, label="Capped long-only frontier")
    pts = [("Min-variance", "Minimum variance", "*", GREEN, 11), ("Max-Sharpe", "Maximum Sharpe", "D", ORANGE, 6), ("1/N", "1/N", "s", "#FFFFFF", 6)]
    for key, lab, mk, c, ms in pts:
        ax.plot(100 * sm.loc[key, "vol"], 100 * sm.loc[key, "return"], mk, color=c, mec=BLACK, mew=0.9, ms=ms, ls="none", label=lab)
    ax.set_xlabel("Annualised volatility (%)")
    ax.set_ylabel("Annualised return (%)")
    ax.legend(loc="lower right", frameon=True, framealpha=0.95, edgecolor="none", handlelength=1.6)
    layout(fig, h, left_in=0.6)
    check_and_save(fig, "fig07_frontier_qa2.png")
    print("  in-sample: " + "; ".join(f"{k} return {100 * sm.loc[k, 'return']:.2f}% vol {100 * sm.loc[k, 'vol']:.2f}% Sharpe {sm.loc[k, 'sharpe']:.3f}" for k, *_ in pts))


# ---------------------------------------------------------------- 8: mean-perturbation sensitivity
def fig_mu_sensitivity():
    got, miss = need("mu_sensitivity_summary_sens.csv")
    if got is None:
        SKIPPED.append(("fig08_mu_sensitivity", miss))
        return
    s = got[0].sort_values("k").reset_index(drop=True)
    x = np.arange(len(s))
    h = 4.6
    fig, axes = plt.subplots(2, 1, figsize=(WIDTH_IN, h), sharex=True)
    ax = axes[0]
    ax.errorbar(x, s.l1_median, yerr=[s.l1_median - s.l1_median_boot_ci_lo, s.l1_median_boot_ci_hi - s.l1_median], fmt="o-", color=BLUE, mec=BLACK, ms=5, capsize=3, lw=1.1)
    ax.set_ylabel("Median L1 change of the\nmax-Sharpe weights")
    ax = axes[1]
    off = 0.06
    ax.errorbar(x - off, s.loss_base_truth_mean, yerr=[s.loss_base_truth_mean - s.loss_base_truth_mean_boot_ci_lo, s.loss_base_truth_mean_boot_ci_hi - s.loss_base_truth_mean],
                fmt="o-", color=BLUE, mec=BLACK, ms=5, capsize=3, lw=1.1, label="Truth = base mean")
    ax.errorbar(x + off, s.loss_draw_truth_mean, yerr=[s.loss_draw_truth_mean - s.loss_draw_truth_mean_boot_ci_lo, s.loss_draw_truth_mean_boot_ci_hi - s.loss_draw_truth_mean],
                fmt="s--", color=VERMILION, mec=BLACK, ms=5, mfc="white", capsize=3, lw=1.1, label="Truth = the perturbed draw")
    ax.set_ylabel("Mean Sharpe loss")
    ax.legend(loc="upper left", frameon=False)
    ax.set_xticks(x, [f"{k:g}" for k in s.k])
    ax.set_xlabel("Noise multiplier k (1 = sampling error)")
    layout(fig, h, left_in=0.7, hspace=0.12)
    check_and_save(fig, "fig08_mu_sensitivity_qa2.png")
    print("  " + "; ".join(f"k={r.k:g}: median L1 {r.l1_median:.3f} [{r.l1_median_boot_ci_lo:.3f}, {r.l1_median_boot_ci_hi:.3f}], mean Sharpe loss (base truth) {r.loss_base_truth_mean:.3f} "
                           f"[{r.loss_base_truth_mean_boot_ci_lo:.3f}, {r.loss_base_truth_mean_boot_ci_hi:.3f}]" for _, r in s.iterrows()))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("fig01-02 wealth and drawdown", fig_wealth_drawdown), ("fig03 price of robustness", fig_price_of_robustness), ("fig04-05 Monte Carlo", fig_mc),
                     ("fig06 forest", fig_forest), ("fig07 frontier", fig_frontier), ("fig08 mean sensitivity", fig_mu_sensitivity)):
        print(name)
        try:
            fn()
        except Exception as e:  # report and continue with the other figures
            plt.close("all")
            FAILS.append(name)
            print(f"  FAILED: {type(e).__name__}: {e}")
    print(f"\nproduced {len(MADE)} figures: " + ", ".join(m[0] for m in MADE))
    for n, miss in SKIPPED:
        print(f"SKIPPED {n}: missing input {miss}")
    print(f"FAILED: {FAILS}" if FAILS else "all figures built and passed the size and font checks")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
