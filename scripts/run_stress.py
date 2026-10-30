"""Calm/stress regimes, a continuous stress level, and per-regime eps selection for two regimes.

Run from the repo root:  python -m scripts.run_stress [--log]
Requires data/processed/{returns_simple,benchmark}.parquet (scripts/run_data.py). The saved hmm2 filtered
probabilities in outputs/tables/regimes_labels.csv (scripts/run_regimes.py) are reused after being checked
against truncated recomputations; if that file is missing they are recomputed. New files carry the suffix
set in config (regimes_stress.output_suffix); existing outputs are not overwritten. The script exits with
status 1 if any consistency check fails.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.run_eps_selection import identical_counts, plateau_info  # noqa: E402
from src.eps_selection import (POOLED, compute_blocks, cv_curves, eps_for_date_info, eps_schedule,  # noqa: E402
                               extension_grid)
from src.regimes import UNLABELLED  # noqa: E402
from src.stress import (CALM, LABEL_FUNCTIONS, STRESS, hard_labels, labels_vol_two, soft_eps,  # noqa: E402
                        stress_level, vol_percentile)
from src.utils import get_logger, load_config, set_seed  # noqa: E402
from src.wdro import eps_grid  # noqa: E402

log = get_logger("stress")
METHODS = ["hmm2_stress", "vol_two"]
COLOURS = {CALM: "tab:green", STRESS: "tab:red", POOLED: "black"}


def verify_saved_stress(bench, saved, cfg):
    """Failures (list of strings) from checking that the saved hmm2 probabilities are the filtered ones.

    Filtered probabilities at row t depend only on data up to t, so they must equal a fresh computation on the
    benchmark truncated at t. A smoothed posterior uses later data and would not match."""
    sec = cfg["regimes_stress"]
    fails = []
    if not {"hmm2_p_calm", "hmm2_p_crisis"}.issubset(saved.columns):
        return ["saved labels lack the hmm2 probability columns"]
    s_saved = saved["hmm2_p_crisis"].reindex(bench.index)
    lab = saved["hmm2_p_calm"].reindex(bench.index).notna()
    if not np.allclose((saved["hmm2_p_calm"] + saved["hmm2_p_crisis"]).reindex(bench.index)[lab], 1.0, atol=1e-9):
        fails.append("saved hmm2 probabilities do not sum to one")
    if int(s_saved.isna().sum()) != sec["hmm_min_history"]:
        fails.append(f"saved s_t has {int(s_saved.isna().sum())} NaN rows, expected the burn-in of {sec['hmm_min_history']}")
    for pos in sec["verify_positions"]:
        fresh = stress_level(bench.iloc[:pos + 1], cfg).iloc[-1]
        if not np.isclose(fresh, s_saved.iloc[pos], atol=1e-8):
            fails.append(f"saved s_t at row {pos} ({s_saved.iloc[pos]:.10f}) differs from a recomputation on data truncated at that row ({fresh:.10f})")
    return fails


def episodes(label, s, price, min_days, windows):
    """Runs of 'stress' labels of at least min_days: start, end, days, mean s, index return and worst path."""
    is_s = (label == STRESS).to_numpy()
    rows, start = [], None
    for i, v in enumerate(list(is_s) + [False]):
        if v and start is None:
            start = i
        if not v and start is not None:
            end = i - 1
            if end - start + 1 >= min_days:
                idx = label.index
                p0 = price.iloc[start - 1] if start > 0 else price.iloc[start]
                path = price.iloc[start:end + 1] / p0 - 1
                row = {"start": idx[start].date(), "end": idx[end].date(), "days": end - start + 1,
                       "mean_s": float(s.iloc[start:end + 1].mean()) if s is not None else np.nan,
                       "index_return": float(price.iloc[end] / p0 - 1), "worst_path_return": float(path.min())}
                for name, (a, b) in windows.items():
                    row[f"overlaps_{name}"] = bool(idx[start] <= pd.Timestamp(b) and idx[end] >= pd.Timestamp(a))
                rows.append(row)
            start = None
    return rows


def window_report(name, window, s, hard, price, thr):
    """Describe the stress level around a market peak/trough inside the window (text lines)."""
    a, b = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    w = price[a:b]
    trough = w.idxmin()
    peak = w[:trough].idxmax()
    lines = [f"    {name}: window {a.date()} to {b.date()}; market peak {peak.date()} ({price[peak]:.0f}), trough {trough.date()} "
             f"({price[trough]:.0f}), drawdown {price[trough] / price[peak] - 1:.1%}"]
    pos = {d: i for i, d in enumerate(price.index)}
    above = s[peak:] > thr
    if above.any():
        first = above.idxmax()
        run_start = pos[first]
        while run_start > 0 and s.iloc[run_start - 1] > thr:
            run_start -= 1
        lines.append(f"      s_t at peak {s[peak]:.3f}; first date on/after the peak with s_t > {thr}: {first.date()} "
                     f"({pos[first] - pos[peak]} trading days after the peak); that spell began {s.index[run_start].date()} "
                     f"({run_start - pos[peak]:+d} days relative to the peak)")
    else:
        lines.append(f"      s_t at peak {s[peak]:.3f}; s_t never exceeds {thr} on or after the peak within the data")
    if s[trough] > thr:
        k = pos[trough]
        while k < len(s) - 1 and s.iloc[k + 1] > thr:
            k += 1
        lines.append(f"      s_t at trough {s[trough]:.3f}; stays above {thr} until {s.index[k].date()} ({k - pos[trough]} trading days after the trough)")
    else:
        later = s[trough:] > thr
        lines.append(f"      s_t at trough {s[trough]:.3f} (not above {thr})" + (f"; next date above: {later.idxmax().date()}" if later.any() else ""))
    sw = s[a:b]
    lines.append(f"      days with s_t > {thr} inside the window: {int((sw > thr).sum())} of {int(sw.notna().sum())}; "
                 f"vol_two stress days inside the window: {int((hard.loc[a:b] == STRESS).sum())} of {int((hard.loc[a:b] != UNLABELLED).sum())}")
    return lines


def soft_series(sched, index, level, label):
    """Per date t: eps_calm, eps_stress from the latest refit at or before t, and eps_soft with the given level."""
    wide = sched.pivot(index="refit_position", columns="regime", values="eps")
    qs = wide.index.to_numpy()
    out = pd.DataFrame(index=index, columns=["refit_position", "eps_calm", "eps_stress"], dtype=float)
    pos = np.searchsorted(qs, np.arange(len(index)), side="right") - 1
    ok = pos >= 0
    out.loc[ok, "refit_position"] = qs[pos[ok]]
    out.loc[ok, "eps_calm"] = wide[CALM].to_numpy()[pos[ok]]
    out.loc[ok, "eps_stress"] = wide[STRESS].to_numpy()[pos[ok]]
    out[f"eps_soft_{label}"] = soft_eps(level.reindex(index), out["eps_calm"], out["eps_stress"])
    return out


def main(args):
    cfg = load_config()
    set_seed(cfg["seed"])
    sec, es, ext = cfg["regimes_stress"], cfg["eps_selection"], cfg["eps_selection_ext"]
    suffix, thr = sec["output_suffix"], sec["stress_threshold"]
    proc = Path(cfg["data"]["processed_dir"])
    rets = pd.read_parquet(proc / "returns_simple.parquet")
    bench_df = pd.read_parquet(proc / "benchmark.parquet")
    bench = bench_df["simple"]
    if not rets.index.equals(bench.index):
        raise RuntimeError("returns and benchmark are not aligned")
    grid, old_grid, rules = extension_grid(ext), eps_grid(cfg), ext["rules"]
    old_idx = [int(np.argmin(np.abs(grid - g))) for g in old_grid]
    if not np.allclose(grid[old_idx], old_grid, rtol=1e-9):
        print("FAILED: extended grid is not a superset of the original grid", file=sys.stderr)
        return 1
    for k in ["hmm_min_history", "hmm_refit_every", "hmm_restarts", "hmm_max_iter", "hmm_tol"]:
        if sec[k] != cfg["regimes"][k]:
            print(f"FAILED: regimes_stress.{k} differs from regimes.{k}", file=sys.stderr)
            return 1
    tables, figs = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables.mkdir(parents=True, exist_ok=True)
    figs.mkdir(parents=True, exist_ok=True)
    price = bench_df["price"].reindex(bench.index)

    # ---- step 1: stress level -------------------------------------------------------------------------------
    saved_path = tables / "regimes_labels.csv"
    if saved_path.exists():
        saved = pd.read_csv(saved_path, index_col=0, parse_dates=True)
        fails = verify_saved_stress(bench, saved, cfg)
        if fails:
            print("FAILED: " + "; ".join(fails), file=sys.stderr)
            return 1
        s = saved["hmm2_p_crisis"].reindex(bench.index).rename("s_t")
        src_note = ("reused from outputs/tables/regimes_labels.csv after checking that they sum to one, that the burn-in is "
                    f"{sec['hmm_min_history']} rows, and that at rows {sec['verify_positions']} they equal a recomputation on data truncated at "
                    "that row (to 1e-8), which smoothed posteriors would not")
    else:
        s = stress_level(bench, cfg)
        src_note = "computed fresh (no saved probabilities found)"
    pct = vol_percentile(bench, cfg).reindex(bench.index)
    hard_h = hard_labels(s, thr)
    hard_v = labels_vol_two(bench, cfg)
    if not ((hard_h == STRESS) == (s > thr)).all():
        print("FAILED: hard label differs from (s_t > threshold)", file=sys.stderr)
        return 1
    daily = pd.DataFrame({"s_t": s, "vol_percentile": pct, "hmm2_stress": hard_h, "vol_two": hard_v})
    daily.to_csv(tables / f"stress_level_daily{suffix}.csv", index_label="date")

    ok = s.notna() & pct.notna()
    pearson, spearman = s[ok].corr(pct[ok]), s[ok].corr(pct[ok], method="spearman")
    lines = [
        f"- Two regimes (calm/stress) plus a continuous stress level, decided because the three-regime crisis buckets had too few validation blocks, "
        f"and decided before any backtest. Config section regimes_stress; threshold {thr} (fixed in advance, not tuned on eps results).",
        f"- Stress level s_t = filtered probability of the higher-variance state of the 2-state Gaussian HMM (expanding window, refit every "
        f"{sec['hmm_refit_every']} days, {sec['hmm_restarts']} restarts, {sec['hmm_min_history']}-day minimum training length, seed {sec['seed']}); {src_note}.",
        f"- s_t is defined on {int(s.notna().sum())} of {len(s)} days ({s.first_valid_index().date()} to {s.index[-1].date()}). Cross-check: percentile rank of "
        f"{sec['vol_percentile_window']}-day realised volatility within its own past, defined on {int(pct.notna().sum())} days.",
        f"- Correlation of s_t with the cross-check over {int(ok.sum())} common days: Pearson {pearson:.3f}, Spearman {spearman:.3f}. "
        f"Mean |s_t - percentile| {float((s[ok] - pct[ok]).abs().mean()):.3f}; days where they differ by more than 0.5: "
        f"{float(((s[ok] - pct[ok]).abs() > 0.5).mean()):.1%}.",
    ]
    lab_h = s.notna()
    yearly = pd.DataFrame({"s_gt_thr": (s > thr)[lab_h].groupby(s.index[lab_h].year).mean(),
                           "days": s[lab_h].groupby(s.index[lab_h].year).size(),
                           "vol_two_stress": (hard_v == STRESS)[hard_v != UNLABELLED].groupby(s.index[hard_v != UNLABELLED].year).mean()})
    yearly.to_csv(tables / f"stress_year_fractions{suffix}.csv", index_label="year")
    lines.append(f"- Fraction of days with s_t > {thr}: {float((s > thr)[lab_h].mean()):.3f} overall ({int((s > thr).sum())} of {int(lab_h.sum())} days). By calendar year (s_t > {thr}; "
                 "vol_two stress share): " + "; ".join(f"{y} {r.s_gt_thr:.2f} ({r.vol_two_stress:.2f})" if np.isfinite(r.vol_two_stress) else f"{y} {r.s_gt_thr:.2f} (n/a)"
                                                     for y, r in yearly.iterrows()))
    lines.append("- s_t around known episodes:")
    for name, w in sec["episode_windows"].items():
        lines += window_report(name, w, s, hard_v, price, thr)
    ep_rows = []
    for m, lab, lvl in [("hmm2_stress", hard_h, s), ("vol_two", hard_v, None)]:
        for r in episodes(lab, lvl, price, sec["episode_min_days"], sec["episode_windows"]):
            ep_rows.append({"method": m, **r})
    ep = pd.DataFrame(ep_rows)
    ep.to_csv(tables / f"stress_episodes{suffix}.csv", index=False)
    for m in METHODS:
        e = ep[ep["method"] == m] if len(ep) else ep
        lines.append(f"- {m}: {len(e)} stress episodes of at least {sec['episode_min_days']} days; longest "
                     + (", ".join(f"{r.start} to {r.end} ({r.days} days, index {r.index_return:+.1%})" for r in e.sort_values("days", ascending=False).head(5).itertuples()) if len(e) else "none"))
    frac_h_total = float((hard_h == STRESS)[hard_h != UNLABELLED].mean())
    frac_v_total = float((hard_v == STRESS)[hard_v != UNLABELLED].mean())
    both = hard_h.ne(UNLABELLED) & hard_v.ne(UNLABELLED)
    agree = float((hard_h[both] == hard_v[both]).mean())
    conf = pd.crosstab(hard_h[both].rename("hmm2_stress"), hard_v[both].rename("vol_two"))
    lines.append(f"- Hard labels hmm2_stress vs vol_two agree on {agree:.1%} of {int(both.sum())} days labelled by both; confusion (rows hmm2_stress, columns vol_two):\n"
                 + "\n".join("    " + ln for ln in conf.to_string().splitlines()))

    n_h_stress = int((hard_h[both] == STRESS).sum())
    lines.append(f"- Base rates differ strongly: hmm2_stress labels {frac_h_total:.1%} of its labelled days as stress, vol_two {frac_v_total:.1%} (its calm bucket is only the "
                 f"lowest-volatility third by construction). Of the {n_h_stress} hmm2_stress days, {int(((hard_h == STRESS) & (hard_v == CALM)).sum())} are labelled calm by vol_two. "
                 "Eps results for the two methods are therefore not comparisons of the same regimes.")

    # ---- step 2: hard labels and sensitivity ------------------------------------------------------------------
    frac_h = float((hard_h == STRESS)[hard_h != UNLABELLED].mean())
    frac_v = float((hard_v == STRESS)[hard_v != UNLABELLED].mean())
    lines.append(f"- Fraction of stress days: hmm2_stress (threshold {thr}) {frac_h:.3f} over {int((hard_h != UNLABELLED).sum())} labelled days; "
                 f"vol_two {frac_v:.3f} over {int((hard_v != UNLABELLED).sum())} labelled days (stress = volatile + crisis of the existing method).")
    sens = pd.DataFrame([{"threshold": t, "stress_fraction": float((s > t)[lab_h].mean()), "stress_days": int((s > t).sum())}
                         for t in sec["sensitivity_thresholds"] + [thr]]).sort_values("threshold")
    sens.to_csv(tables / f"stress_threshold_sensitivity{suffix}.csv", index=False)
    lines.append("- Threshold sensitivity (stress fraction only, not used to choose a threshold): "
                 + "; ".join(f"{r.threshold:g}: {r.stress_fraction:.3f}" for r in sens.itertuples()))

    # ---- step 3: eps selection for two regimes ----------------------------------------------------------------
    labels = {"hmm2_stress": hard_h, "vol_two": hard_v}
    n_blocks = len(range(cfg["backtest"]["rolling_window_days"], len(rets) - es["block_length"] + 1, es["block_length"]))
    log.info("computing %d validation blocks x %d eps ...", n_blocks, len(grid))
    net = compute_blocks(rets, cfg, grid)
    lines += [
        f"- eps selection: {net.shape[1]} walk-forward blocks of {es['block_length']} days (WDRO {es['kind']} weights from the prior "
        f"{cfg['backtest']['rolling_window_days']}-day window, net of {cfg['costs']['transaction_cost_bps']} bps), pooled by the PRIOR-day regime label; "
        f"extended {len(grid)}-point grid {grid[0]:.0e} to {grid[-1]:.3g}; min {es['min_blocks']} blocks per regime else the pooled eps; refit every {es['refit_every']} days. "
        f"Primary rule: best (argmax net Sharpe). Sensitivity: one_se (smallest eps within one jackknife SE of the best; blocks treated as exchangeable, "
        f"so the SE is probably too small). No significance test was run.",
    ]
    scheds, curves, summary, stats = {}, {}, [], []
    for m in METHODS:
        parts = [eps_schedule(rets, bench, labels[m], cfg, net=net, grid=grid, rule=r) for r in rules]
        scheds[m] = dict(zip(rules, parts))
        pd.concat(parts, ignore_index=True).to_csv(tables / f"eps_selection_table_{m}{suffix}.csv", index=False)
        cur = cv_curves(rets, labels[m], cfg, net, grid)
        curves[m] = cur
        cur.to_csv(tables / f"eps_cv_curves_{m}{suffix}.csv", index=False)
        lines.append(f"- {m}:")
        for r, sched in zip(rules, parts):
            tag = "PRIMARY" if r == "best" else "sensitivity"
            final = sched[sched["refit_position"] == sched["refit_position"].max()]
            ic = identical_counts(sched)
            lines.append(f"    rule {r} ({tag}): final refit eps " + ", ".join(
                f"{x['regime']} {x['eps']:.3g} ({x['source']}, {int(x['n_blocks'])} blocks)" for _, x in final.iterrows()))
            lines.append(f"      refits {ic['refits']}; both regimes regime-sourced at {ic['multi']} refits, identical eps at {ic['multi_identical']} of them; "
                         f"all regime rows (incl. pooled fallbacks) share one eps at {ic['all_rows_identical']} refits")
            for reg in [CALM, STRESS]:
                d = sched[sched["regime"] == reg]
                counts = d["source"].value_counts().to_dict()
                nb = d["n_blocks"]
                lines.append(f"      {reg}: sources over refits {counts}; blocks per refit min {int(nb.min())}, median {nb.median():.0f}, max {int(nb.max())}")
                stats.append({"method": m, "rule": r, "regime": reg, "refits": len(d), **{f"n_{k}": v for k, v in counts.items()},
                              "share_regime_sourced": float((d["source"] == "regime").mean()),
                              "share_pooled_fallback": float((d["source"] == "pooled_fallback").mean()),
                              "share_default": float((d["source"] == "default").mean()), "min_blocks": int(nb.min()),
                              "median_blocks": float(nb.median()), "max_blocks": int(nb.max())})
            for _, x in final.iterrows():
                summary.append({"method": m, "rule": r, "regime": x["regime"], "final_eps": x["eps"], "best_eps": x["best_eps"], "source": x["source"],
                                "n_blocks": x["n_blocks"], "sharpe_net": x["sharpe_net"], "se_best": x["se_best"]})
        for reg, d in cur.groupby("regime"):
            best, start, kind = plateau_info(d.reset_index(drop=True))
            e = d["eps"].to_numpy()
            lines.append(f"    all-blocks CV curve, {reg} ({int(d['n_blocks'].iloc[0])} blocks): best eps {e[best]:.3g}, plateau (identical 1/N portfolio) starts at "
                         f"{e[start]:.3g} -> {kind}; Sharpe at best {d['sharpe_net'].iloc[best]:.3f} (jackknife SE {d['se_sharpe'].iloc[best]:.3f})")
            summary.append({"method": m, "rule": "all_blocks_curve", "regime": reg, "final_eps": e[best], "best_eps": e[best], "source": kind,
                            "n_blocks": d["n_blocks"].iloc[0], "sharpe_net": d["sharpe_net"].iloc[best], "se_best": d["se_sharpe"].iloc[best]})
    pd.DataFrame(summary).to_csv(tables / f"eps_selection_summary{suffix}.csv", index=False)
    pd.DataFrame(stats).to_csv(tables / f"eps_fallback_stats{suffix}.csv", index=False)

    # ---- step 4: soft eps series (exploratory, no performance comparison) --------------------------------------
    soft_frames = []
    for m in METHODS:
        for r in rules:
            a = soft_series(scheds[m][r], bench.index, s, "s")
            b = soft_series(scheds[m][r], bench.index, pct, "pct")
            fr = a.join(b[["eps_soft_pct"]])
            fr["s_t"], fr["vol_percentile"] = s, pct
            fr.insert(0, "rule", r)
            fr.insert(0, "method", m)
            soft_frames.append(fr)
            d = fr.dropna(subset=["eps_soft_s", "s_t"])
            gap = d["eps_stress"] - d["eps_calm"]
            corr = d["eps_soft_s"].corr(d["s_t"]) if d["eps_soft_s"].nunique() > 1 else np.nan
            lines.append(f"- soft eps {m}/{r}: eps_t = eps_calm + s_t (eps_stress - eps_calm) from the eps selected at the latest refit; defined on {len(d)} days; "
                         f"corr(eps_t, s_t) {corr:.3f}; stress eps above calm eps on {float((gap > 0).mean()):.1%} of those days, below on {float((gap < 0).mean()):.1%}, equal on {float((gap == 0).mean()):.1%}")
    pd.concat(soft_frames).to_csv(tables / f"eps_soft_series{suffix}.csv", index_label="date")

    # ---- consistency checks: eps_for_date recomputed from data before the date equals the schedule ----------------
    failures = []
    for m in METHODS:
        fn = LABEL_FUNCTIONS[m]
        own = scheds[m]["best"]
        rows = own[(own["source"] == "regime") & (own["regime"] != POOLED)]["refit_position"]
        if rows.empty:
            lines.append(f"- eps_for_date check {m}: no regime-sourced refit, skipped")
            continue
        q = int(rows.min())
        for r in rules:
            sched = scheds[m][r]
            # refit boundary (the stale-refit bug area) is covered for the primary rule; one_se is checked just after it
            offsets = [-1, 0, 5] if r == "best" else [5]
            for off in offsets:
                p = q + off
                date = rets.index[p]
                info = eps_for_date_info(date, m, cfg, returns=rets, benchmark=bench, grid=grid, rule=r, label_fn=fn)
                now = labels[m].iloc[p - 1]
                qq = info["refit_position"]
                if qq is None:
                    ok_ = False
                else:
                    row = sched[(sched["refit_position"] == qq) & (sched["regime"] == (now if now != UNLABELLED else POOLED))].iloc[0]
                    ok_ = bool(info["eps"] == row["eps"] and np.isclose(info["sharpe_net"], row["sharpe_net"], equal_nan=True))
                lines.append(f"- eps_for_date check {m}/{r}: date {date.date()} (refit {q}{off:+d}), regime {info['regime']}, eps {info['eps']:.3g} ({info['source']}); "
                             f"matches the full-sample schedule: {ok_}")
                if not ok_:
                    failures.append(f"eps_for_date disagrees with the schedule for {m}/{r} at row {p}")

    # ---- figures ------------------------------------------------------------------------------------------------
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1.4]})
    a1.plot(price.index, price.values, color="black", lw=1)
    a1.set_yscale("log")
    a1.set_ylabel("NIFTY 50 price index\n(excludes dividends)")
    for ax in (a1, a2):
        ax.fill_between(s.index, 0, 1, where=(s > thr).to_numpy(), transform=ax.get_xaxis_transform(), color="tab:red", alpha=0.15, linewidth=0,
                        label=f"s_t > {thr}" if ax is a1 else None)
    a2.plot(s.index, s.values, color="tab:blue", lw=0.9, label="s_t (filtered HMM stress probability)")
    a2.plot(pct.index, pct.values, color="tab:orange", lw=0.7, alpha=0.8, label="cross-check: volatility percentile")
    a2.axhline(thr, color="grey", ls="--", lw=0.8)
    a2.set_ylim(-0.02, 1.02)
    a2.set_ylabel("stress level")
    for name, (a, b) in sec["episode_windows"].items():
        for ax in (a1, a2):
            ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), facecolor="none", edgecolor="blue", hatch="//", alpha=0.4, label=name if ax is a1 else None)
    a1.legend(loc="upper left", fontsize=8)
    a2.legend(loc="upper left", fontsize=8, ncol=2)
    fig.suptitle("Stress level over time (causal; hatched = COVID window and 2022; shading = s_t above the threshold)", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs / f"stress_level_over_time{suffix}.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(2 * len(METHODS), len(rules), figsize=(7 * len(rules), 3.4 * len(METHODS)), sharex=True,
                             gridspec_kw={"height_ratios": [3, 0.6] * len(METHODS)}, squeeze=False)
    for i, m in enumerate(METHODS):
        for j, r in enumerate(rules):
            ax, strip = axes[2 * i, j], axes[2 * i + 1, j]
            sc = scheds[m][r]
            for reg in [POOLED, CALM, STRESS]:
                d = sc[sc["regime"] == reg]
                ax.step(d["refit_date"], d["eps"], where="post", color=COLOURS[reg], ls="--" if reg == POOLED else "-", label=reg, alpha=0.9)
            ax.set_yscale("log")
            ax.set_ylabel(f"{m}\nselected eps")
            ax.set_title(f"rule: {r}" + (" (primary)" if r == "best" else " (sensitivity)"), fontsize=9)
            ax.legend(fontsize=7, ncol=3, loc="upper left")
            ax.grid(alpha=0.3)
            for reg in [CALM, STRESS]:
                strip.fill_between(labels[m].index, 0, 1, where=(labels[m] == reg).to_numpy(), color=COLOURS[reg], step="mid", linewidth=0)
            strip.set_yticks([])
    fig.suptitle("Selected Wasserstein radius per regime at each refit, two regimes, walk-forward (strip: regime label)", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs / f"eps_selection_over_time{suffix}.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(2, len(METHODS), figsize=(6 * len(METHODS), 7), sharex=True, squeeze=False)
    for j, m in enumerate(METHODS):
        for reg, d in curves[m].groupby("regime"):
            c = COLOURS.get(reg, "grey")
            axes[0, j].plot(d["eps"], d["sharpe_net"], marker="o", ms=3, color=c, ls="--" if reg == POOLED else "-", label=f"{reg} ({int(d['n_blocks'].iloc[0])} blocks)")
            axes[0, j].fill_between(d["eps"], d["sharpe_net"] - d["se_sharpe"], d["sharpe_net"] + d["se_sharpe"], color=c, alpha=0.12)
            axes[1, j].plot(d["eps"], d["cvar_daily"], marker="o", ms=3, color=c, ls="--" if reg == POOLED else "-")
        axes[0, j].set_title(m)
        axes[0, j].set_ylabel("net Sharpe (band: +/- jackknife SE)")
        axes[1, j].set_ylabel("daily CVaR")
        axes[1, j].set_xscale("log")
        axes[1, j].set_xlabel("eps (daily-return units)")
        axes[0, j].legend(fontsize=7)
        for k in range(2):
            axes[k, j].grid(alpha=0.3)
    fig.suptitle("Walk-forward validation curves, all finished blocks (out-of-sample block returns, not a backtest of the final rule)", fontsize=9)
    fig.tight_layout()
    fig.savefig(figs / f"eps_cv_curves{suffix}.png", dpi=150)
    plt.close(fig)

    if failures:
        lines.append("- CONSISTENCY CHECKS FAILED: " + "; ".join(failures))
    print("\n".join(lines))
    if args.log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Calm/stress regimes, continuous stress level and two-regime eps selection (scripts/run_stress.py)\n"
                    + "\n".join(lines) + "\n")
        log.info("appended to %s", cfg["paths"]["results_log"])
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    sys.exit(main(ap.parse_args()))
