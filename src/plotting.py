"""Plotting helpers."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def plot_prices_and_vol(norm_prices, norm_bench, roll_vol, shaded, path, vol_window):
    """Top: normalised prices (=100 at start) with the index bold. Bottom: rolling
    annualised index volatility. `shaded` maps label -> (start, end) date strings."""
    import pandas as pd

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 8), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1]})
    a1.plot(norm_prices.index, norm_prices.values, color="grey", alpha=0.25, lw=0.7)
    a1.plot(norm_bench.index, norm_bench.values, color="black", lw=2, label="NIFTY 50 (^NSEI)")
    a1.set_yscale("log")
    a1.set_ylabel("Normalised price (start = 100, log scale)")
    a1.set_title("NIFTY 50 constituents: normalised adjusted prices")
    a2.plot(roll_vol.index, roll_vol.values, color="tab:red", lw=1.5)
    a2.set_ylabel(f"{vol_window}-day rolling vol (annualised)")
    a2.set_xlabel("Date")
    colours = ["tab:orange", "tab:blue", "tab:green"]
    for (label, (s, e)), c in zip(shaded.items(), colours):
        for ax in (a1, a2):
            ax.axvspan(pd.Timestamp(s), pd.Timestamp(e), color=c, alpha=0.2,
                       label=label if ax is a1 else None)
    a1.legend(loc="upper left", fontsize=8)
    a1.grid(alpha=0.3)
    a2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_frontier(frontier, stocks, points, path, title):
    """frontier: DataFrame(return, vol). stocks: DataFrame(return, vol) indexed by ticker.
    points: dict label -> (vol, return), drawn as labelled markers."""
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.scatter(stocks["vol"], stocks["return"], s=18, color="grey", alpha=0.7,
               label="Individual stocks")
    ax.plot(frontier["vol"], frontier["return"], color="tab:blue", lw=2,
            label="Efficient frontier (capped)")
    markers = ["*", "D", "s", "^"]
    for (label, (v, r)), m in zip(points.items(), markers):
        ax.scatter([v], [r], marker=m, s=120, zorder=5, edgecolor="black", label=label)
    ax.set_xlabel("Annualised volatility")
    ax.set_ylabel("Annualised return")
    ax.set_title(title)
    ax.text(0.5, 0.01, "IN-SAMPLE ILLUSTRATION - not an out-of-sample result",
            transform=ax.transAxes, ha="center", va="bottom", fontsize=9,
            color="firebrick", bbox=dict(fc="white", ec="firebrick", alpha=0.8))
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
