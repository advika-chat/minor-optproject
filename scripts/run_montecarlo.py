"""Monte Carlo with known ground truth: tests H1 and H2 (docs/hypotheses.md).

Run from the repository root:  python -m scripts.run_montecarlo [--log]
Requires data/processed/returns_simple.parquet. Parameters: config.yaml section ``montecarlo``. Per-run results are
checkpointed one file per (scenario, T, run) in montecarlo.checkpoint_dir, so an interrupted job resumes where it
stopped. New files carry the suffix montecarlo.output_suffix. Exits with status 1 if any check fails.
"""
import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):   # one BLAS thread per worker process
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import itertools  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from joblib import Parallel, delayed  # noqa: E402

from src import montecarlo as mc  # noqa: E402
from src import solver_own as so  # noqa: E402
from src import wdro  # noqa: E402

MAIN_METHODS = ["plugin_maxsharpe", "dro_cv", "dro_eps0", "equal_weight", "plugin_minvar", "lw_maxsharpe", "oracle"]
LABELS = {"plugin_maxsharpe": "plug-in max-Sharpe", "dro_cv": "DRO (CV epsilon)", "dro_eps0": "DRO epsilon=0",
          "equal_weight": "1/N", "plugin_minvar": "plug-in min-variance", "lw_maxsharpe": "Ledoit-Wolf max-Sharpe",
          "oracle": "oracle"}


# ------------------------------------------------------------------------------------------- utilities
def update_notes_blocks(path, blocks):
    p = Path(path)
    if not p.exists():
        return []
    text, found = p.read_text(encoding="utf-8"), []
    for name, body in blocks.items():
        pat = re.compile(rf"(<!-- {name}:start -->\n).*?(\n<!-- {name}:end -->)", re.S)
        if pat.search(text):
            text = pat.sub(lambda m: m.group(1) + body + m.group(2), text)
            found.append(name)
    p.write_text(text, encoding="utf-8")
    return found


def md_table(df, digits=4):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            cells.append(f"{v:.{digits}f}" if isinstance(v, (float, np.floating)) and np.isfinite(v)
                         else ("" if isinstance(v, float) else str(v)))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def build_contexts(cfg, rets):
    """{scenario name: (scenario dict, context)}; one truth, oracle and theta per scenario."""
    out = {}
    for scen in mc.scenarios(cfg):
        out[scen["name"]] = (scen, mc.make_context(rets, cfg, scen["mu_shrink_s"], scen["extra_eps"]))
    return out


def plan_tasks(cfg, ctxs):
    tasks = []
    for scen, _ in ctxs.values():
        for T in sorted(scen["T_list"], reverse=True):               # slowest first
            tasks.extend((scen["name"], T, run) for run in range(scen["n_runs"]))
    return tasks


def execute(cfg, ctxs, tasks, n_jobs):
    """Run the tasks that have no checkpoint yet. Returns the number computed."""
    pending = [t for t in tasks if not mc.ckpt_path(cfg, ctxs[t[0]][0], t[1], t[2]).exists()]
    gen = (delayed(mc.worker)(ctxs[s][1], cfg, ctxs[s][0], T, run) for s, T, run in pending)
    if n_jobs == 1:
        for g in gen:
            g[0](*g[1], **g[2])
    else:
        done = 0
        for _ in Parallel(n_jobs=n_jobs, backend="loky", return_as="generator_unordered")(gen):
            done += 1
            if done % 250 == 0:
                print(f"  {done}/{len(pending)} runs done", flush=True)
    return len(pending)


def collect(cfg, ctxs):
    runs, metas = [], []
    for scen, _ in ctxs.values():
        for T in scen["T_list"]:
            m, r = mc.load_checkpoints(cfg, scen, T, range(scen["n_runs"]))
            metas.extend(m)
            runs.extend(r)
    return pd.DataFrame(runs), pd.DataFrame(metas)


# ------------------------------------------------------------------------------------------- analysis
def _stats_row(d, cfg, offset):
    s, _ = mc.paired_stats(d, cfg, offset)
    return s


def analyse(cfg, runs, metas, ctxs):
    mcc = cfg["montecarlo"]
    Ts, Tp = mcc["T_list"], mcc["T_primary"]
    ctr = itertools.count(1000, 10)
    tabs, lines = {}, []

    # summary by T (primary scenario)
    rows = []
    for T in Ts:
        w = mc.wide(runs, "primary", T)
        sub = runs[(runs.scenario == "primary") & (runs["T"] == T)]
        for m in MAIN_METHODS:
            s = _stats_row(w[m].to_numpy(), cfg, next(ctr))
            g = sub[sub.method == m]
            rows.append({"T": T, "method": m, "mean_true_sharpe": s["mean"], "ci_lo": s["ci_lo"], "ci_hi": s["ci_hi"],
                         "median_true_sharpe": s["median"], "mean_true_return": g.true_return.mean(),
                         "mean_true_vol": g.true_vol.mean(), "mean_n_pos": g.n_pos.mean(), "mean_eff_n": g.eff_n.mean(),
                         "mean_l1_oracle": g.l1_oracle.mean(), "mean_regret": g.regret.mean()})
    tabs["mc_summary_by_T"] = pd.DataFrame(rows)

    # H1
    h1 = []
    for T in Ts:
        w = mc.wide(runs, "primary", T)
        s = _stats_row((w["dro_cv"] - w["plugin_maxsharpe"]).to_numpy(), cfg, next(ctr))
        verdict = ("SUPPORTED" if s["ci_lo"] > 0 else "FALSIFIED") if T == Tp else "(not the primary test)"
        h1.append({"T": T, "primary": T == Tp, "mean_diff": s["mean"], "median_diff": s["median"], "boot_se": s["boot_se"],
                   "ci_lo": s["ci_lo"], "ci_hi": s["ci_hi"], "share_dro_wins": s["share_positive"], "verdict": verdict})
    tabs["mc_h1"] = pd.DataFrame(h1)

    # ablations and benchmark
    ab = []
    pairs = [("dro_cv", "dro_eps0"), ("dro_cv", "equal_weight"), ("dro_eps0", "plugin_maxsharpe")]
    for T in Ts:
        w = mc.wide(runs, "primary", T)
        for a, b in pairs:
            s = _stats_row((w[a] - w[b]).to_numpy(), cfg, next(ctr))
            ab.append({"T": T, "comparison": f"{LABELS[a]} minus {LABELS[b]}", "mean_diff": s["mean"], "median_diff": s["median"],
                       "boot_se": s["boot_se"], "ci_lo": s["ci_lo"], "ci_hi": s["ci_hi"], "share_first_wins": s["share_positive"]})
    tabs["mc_ablation"] = pd.DataFrame(ab)

    # H2
    deltas = {}
    for T in Ts:
        w = mc.wide(runs, "primary", T)
        deltas[T] = (w["dro_cv"] - w["plugin_maxsharpe"]).to_numpy()
    did, s_short, s_long = mc.diff_in_diff(deltas[mcc["T_short"]], deltas[mcc["T_long"]], cfg, next(ctr))
    h2 = []
    for T in Ts:
        s_T = _stats_row(deltas[T], cfg, next(ctr))
        h2.append({"quantity": f"Delta(T={T})", "T": T, "estimate": s_T["mean"], "ci_lo": s_T["ci_lo"], "ci_hi": s_T["ci_hi"], "verdict": ""})
    h2.append({"quantity": f"DiD = Delta({mcc['T_short']}) - Delta({mcc['T_long']})", "T": np.nan, "estimate": did["did"],
               "ci_lo": did["ci_lo"], "ci_hi": did["ci_hi"], "verdict": "SUPPORTED" if did["ci_lo"] > 0 else "NOT SUPPORTED"})
    tabs["mc_h2"] = pd.DataFrame(h2)

    # price-of-robustness curves and chosen epsilon
    curve = []
    for sname in ("primary", "ext_grid"):
        if sname not in ctxs:
            continue
        grid = ctxs[sname][1].grid
        for T in ctxs[sname][0]["T_list"]:
            sub = runs[(runs.scenario == sname) & (runs["T"] == T)]
            w = mc.wide(runs, sname, T)
            ref_pi, ref_eq = w["plugin_maxsharpe"].mean(), w["equal_weight"].mean()
            vals = []
            for k, e in enumerate(grid):
                col = f"dro_fixed_{k:02d}"
                s = _stats_row(w[col].to_numpy(), cfg, next(ctr))
                g = sub[sub.method == col]
                vals.append({"scenario": sname, "T": T, "eps_index": k, "eps": float(e), "mean_true_sharpe": s["mean"],
                             "ci_lo": s["ci_lo"], "ci_hi": s["ci_hi"], "mean_true_vol": g.true_vol.mean(),
                             "mean_n_pos": g.n_pos.mean(), "plugin_mean_sharpe": ref_pi, "equal_weight_mean_sharpe": ref_eq})
            best = int(np.argmax([v["mean_true_sharpe"] for v in vals]))
            for k, v in enumerate(vals):
                v["best_fixed_eps_in_hindsight"] = k == best
            curve.extend(vals)
    tabs["mc_eps_curve"] = pd.DataFrame(curve)

    ch = []
    for sname, (scen, ctx) in ctxs.items():
        if sname not in ("primary", "ext_grid"):
            continue
        for T in scen["T_list"]:
            m = metas[(metas.scenario == sname) & (metas["T"] == T)]
            share_plateau = float(m.plateau.mean())
            for k, e in enumerate(ctx.grid):
                c = int((m.eps_index == k).sum())
                ch.append({"scenario": sname, "T": T, "eps_index": k, "eps": float(e), "count": c, "share": c / len(m),
                           "plateau_share_all_runs": share_plateau})
    tabs["mc_eps_chosen"] = pd.DataFrame(ch)

    # sensitivity (T = T_primary for the one-factor scenarios; ext_grid at every T)
    se = []
    for sname, (scen, ctx) in ctxs.items():
        for T in scen["T_list"]:
            if sname != "ext_grid" and T != Tp:
                continue
            w = mc.wide(runs, sname, T)
            s = _stats_row((w["dro_cv"] - w["plugin_maxsharpe"]).to_numpy(), cfg, next(ctr))
            s2 = _stats_row((w["dro_cv"] - w["equal_weight"]).to_numpy(), cfg, next(ctr))
            m = metas[(metas.scenario == sname) & (metas["T"] == T)]
            se.append({"scenario": sname, "T": T, "n_runs": s["n"], "oracle_sharpe": ctx.s_orc, "plugin_mean_sharpe": w["plugin_maxsharpe"].mean(),
                       "dro_mean_sharpe": w["dro_cv"].mean(), "equal_weight_mean_sharpe": w["equal_weight"].mean(),
                       "diff_dro_minus_plugin": s["mean"], "diff_ci_lo": s["ci_lo"], "diff_ci_hi": s["ci_hi"],
                       "diff_dro_minus_equal": s2["mean"], "diff2_ci_lo": s2["ci_lo"], "diff2_ci_hi": s2["ci_hi"],
                       "plateau_share": float(m.plateau.mean())})
    tabs["mc_sensitivity"] = pd.DataFrame(se)
    return tabs, did, s_short, s_long


# ------------------------------------------------------------------------------------------- checks
def sanity_checks(cfg, ctxs, runs, metas, timings=None):
    mcc, ck = cfg["montecarlo"], cfg["montecarlo"]["checks"]
    td, cap, rf = cfg["trading_days"], cfg["portfolio"]["weight_cap"], cfg["risk_free_rate"]
    out = []

    def add(name, ok, detail):
        out.append((name, bool(ok), detail))

    # calibration (lambda) and (1)
    for sname, (scen, ctx) in ctxs.items():
        w = mc.oracle_mv_weights(ctx, cfg)
        dw = float(np.abs(w - ctx.w_orc).max())
        add(f"calibration [{sname}]: oracle mean-variance equals oracle max-Sharpe", dw <= mcc["weights_match_tol"],
            f"max |dw| = {dw:.2e}; S* = {ctx.s_orc:.6f}; theta = {ctx.theta:.6f}")
        dists = {scen["dist"]}
        for dist in dists:
            zm, zc = mc.moment_check(ctx.truth, dist, cfg, np.random.SeedSequence(mcc["base_seed"], spawn_key=(99, scen["index"])))
            add(f"(1) simulated moments [{sname}, {dist}]", max(zm, zc) <= ck["moment_z_tol"],
                f"max |z| mean {zm:.2f}, covariance {zc:.2f} (tolerance {ck['moment_z_tol']})")

    # (2) oracle bound
    ex = float(metas.max_oracle_excess.max())
    add("(2) oracle true Sharpe >= every method in every run", ex <= ck["oracle_tol"], f"largest method-minus-oracle = {ex:.2e}")

    # (3) eps = 0 against independent computations
    scen, ctx = ctxs["primary"]
    Tp = mcc["T_primary"]
    drmv = mc._drmv(len(ctx.truth.mu), cap, wdro._dual_p(cfg), mcc["solver_tol"])
    worst_qp, worst_wd, worst_obj = 0.0, 0.0, 0.0
    for run in range(ck["n_eps0_runs"]):
        r = mc.simulate(ctx.truth, Tp, "t", cfg, mc.child_seed(cfg, 0, mcc["T_list"].index(Tp), run))
        mu_hat, cov_hat = r.mean(axis=0) * td, np.cov(r, rowvar=False) * td
        w0 = drmv.solve(r, 0.0, ctx.theta)
        qp = so.solve_qp(cov_hat, mu_hat, cap, cfg["solver_own"], target=float(mu_hat @ w0))
        worst_qp = max(worst_qp, float(np.abs(w0 - qp.weights).max()))
        v = np.cov(r, rowvar=False, bias=True)
        for e in (0.0, 0.01):
            a = drmv.solve(r, e, 0.0)
            b = wdro.wdro_weights(pd.DataFrame(r), e, "meanvar", cfg)
            worst_wd = max(worst_wd, float(np.abs(a - b).max()))
            f = lambda x: np.sqrt(x @ v @ x) + e * np.abs(x).max()      # ground norm 1, dual norm = max norm
            worst_obj = max(worst_obj, abs(f(a) - f(b)))
    add("(3a) DR mean-variance at eps=0 equals the plug-in mean-variance solution (own active-set solver, same return)",
        worst_qp <= ck["eps0_weight_tol"], f"max |dw| over {ck['n_eps0_runs']} runs = {worst_qp:.2e}")
    add("(3b) at theta=0 the objective equals the one in src/wdro.py (objective values and weights)",
        worst_obj <= ck["wdro_objective_tol"] and worst_wd <= ck["wdro_weight_tol"],
        f"max objective difference {worst_obj:.2e}, max |dw| {worst_wd:.2e} (that solver's weights are accurate to about 1e-5)")

    # (4) plateau: in the extended-grid scenario the largest epsilon must give 1/N
    if "ext_grid" in ctxs:
        m = metas[metas.scenario == "ext_grid"]
        dev = float(m.top_eps_maxdev_equal.max())
        add("(4) at the plateau epsilon (largest of the extended grid) the weights equal 1/N", dev <= ck["plateau_weight_tol"],
            f"largest deviation from 1/N over {len(m)} runs = {dev:.2e}")
    mp = metas[metas.scenario == "primary"]
    out.append(("(4-info) primary grid: share of runs whose chosen portfolio is 1/N", True, f"{mp.plateau.mean():.3f}; share of runs where the largest "
                f"grid epsilon gives 1/N: {(mp.top_eps_maxdev_equal <= ck['plateau_weight_tol']).mean():.3f}"))

    # (5) known answer
    r = mc.exact_moment_sample(ctx.truth, Tp, cfg, np.random.SeedSequence(mcc["base_seed"], spawn_key=(98,)))
    res = mc.plugin_maxsharpe(r.mean(axis=0) * td, np.cov(r, rowvar=False, bias=True) * td, cfg)
    d1 = float(np.abs(res.weights - ctx.w_orc).max())
    d2 = float(np.abs(drmv.solve(r, 0.0, ctx.theta) - ctx.w_orc).max())
    add("(5a) known answer: plug-in max-Sharpe fed the true parameters equals the oracle", d1 <= ck["known_answer_tol"], f"max |dw| = {d1:.2e}")
    add("(5b) known answer: DR mean-variance (eps=0) fed the true parameters equals the oracle", d2 <= mcc["weights_match_tol"],
        f"max |dw| = {d2:.2e}")

    # (6) reproducibility of three stored runs
    n_pairs, nr = ck["n_repro_pairs"], scen["n_runs"]
    worst, same, n_eps_diff, worst_gap = 0.0, True, 0, 0.0
    for i in range(n_pairs):
        T, run = mcc["T_list"][i % len(mcc["T_list"])], [0, nr // 2, nr - 1][i % 3]
        again = mc.run_one(ctx, cfg, scen, T, run)
        old_meta, old_rows = mc.load_checkpoints(cfg, scen, T, [run])
        same &= again["meta"]["data_hash"] == old_meta[0]["data_hash"]
        if again["meta"]["eps_index"] != old_meta[0]["eps_index"]:
            n_eps_diff += 1
            o = np.array(old_meta[0]["oof_sharpe"])
            worst_gap = max(worst_gap, abs(o[again["meta"]["eps_index"]] - o[old_meta[0]["eps_index"]]))
        for a, b in zip(again["rows"], old_rows):
            worst = max(worst, max(abs(a[c] - b[c]) for c in ("true_sharpe", "true_return", "true_vol", "l1_oracle")))
    add("(6) re-running stored (T, run) pairs reproduces them to solver accuracy",
        same and worst <= ck["repro_tol"] and worst_gap <= ck["repro_tol"],
        f"{n_pairs} pairs; same simulated data: {same}; max metric difference {worst:.2e}; epsilon index changed in {n_eps_diff} pairs "
        f"(out-of-fold Sharpe gap {worst_gap:.2e}, a near-tie)")

    # (7) average of mu_hat over runs equals mu_true
    worst_z = 0.0
    for sname, (sc, cx) in ctxs.items():
        for T in sc["T_list"]:
            m = metas[(metas.scenario == sname) & (metas["T"] == T)]
            mu_hat = np.array(m.mu_hat.tolist())
            z = (mu_hat.mean(0) - cx.truth.mu) / (mu_hat.std(0, ddof=1) / np.sqrt(len(m)))
            worst_z = max(worst_z, float(np.abs(z).max()))
    add("(7) average of mu_hat over runs equals mu_true within Monte Carlo error", worst_z <= ck["moment_z_tol"], f"max |z| = {worst_z:.2f}")

    # (8) every method saw the data simulated from the seed
    bad = 0
    for sname, (sc, cx) in ctxs.items():
        for T in sc["T_list"]:
            ti = mcc["T_list"].index(T)
            m = metas[(metas.scenario == sname) & (metas["T"] == T)]
            for run, h in zip(m.run, m.data_hash):
                r = mc.simulate(cx.truth, T, sc["dist"], cfg, mc.child_seed(cfg, sc["index"], ti, int(run)))
                bad += mc.data_hash(r) != h
    add("(8) all methods in a run used the same simulated data (hash of the seeded simulation = hash recorded before and after the methods)", bad == 0,
        f"{bad} mismatches in {len(metas)} runs")

    exp = sum(len(sc["T_list"]) * sc["n_runs"] for sc, _ in ctxs.values())
    add("completeness: one checkpoint per planned run", len(metas) == exp, f"{len(metas)} of {exp}")
    return out


# --------------------------------------------------------------------------------------------- figures
def make_figures(cfg, tabs, runs, did, figs_dir, suffix):
    mcc = cfg["montecarlo"]
    Ts = mcc["T_list"]
    figs_dir.mkdir(parents=True, exist_ok=True)

    sm = tabs["mc_summary_by_T"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    methods = [m for m in MAIN_METHODS if m != "oracle"]
    off = np.linspace(-0.3, 0.3, len(methods))
    for j, m in enumerate(methods):
        d = sm[sm.method == m]
        ax.errorbar(np.arange(len(Ts)) + off[j], d.mean_true_sharpe, yerr=[d.mean_true_sharpe - d.ci_lo, d.ci_hi - d.mean_true_sharpe],
                    fmt="o", capsize=3, label=LABELS[m])
    orc = sm[sm.method == "oracle"].mean_true_sharpe.iloc[0]
    ax.axhline(orc, color="k", ls="--", lw=1, label="oracle")
    ax.set_xticks(range(len(Ts)), [f"T={T}" for T in Ts])
    ax.set_ylabel("true annual Sharpe (mean over runs, 95% interval)")
    ax.set_title("True out-of-sample Sharpe by method and training length")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figs_dir / f"mc_sharpe_by_method{suffix}.png", dpi=150)
    plt.close(fig)

    for sname in ("primary", "ext_grid"):
        cv = tabs["mc_eps_curve"]
        cv = cv[cv.scenario == sname]
        if cv.empty:
            continue
        Tl = sorted(cv["T"].unique())
        fig, axs = plt.subplots(3, len(Tl), figsize=(4.2 * len(Tl), 8), sharex="col", squeeze=False)
        for j, T in enumerate(Tl):
            d = cv[cv["T"] == T]
            ax = axs[0, j]
            ax.plot(d.eps, d.mean_true_sharpe, "o-", ms=3, label="DRO, fixed epsilon")
            ax.fill_between(d.eps, d.ci_lo, d.ci_hi, alpha=0.25)
            ax.axhline(d.plugin_mean_sharpe.iloc[0], color="tab:red", ls="--", label="plug-in max-Sharpe")
            ax.axhline(d.equal_weight_mean_sharpe.iloc[0], color="tab:green", ls=":", label="1/N")
            ax.set_title(f"T={T}")
            axs[1, j].plot(d.eps, d.mean_true_vol, "o-", ms=3)
            axs[2, j].plot(d.eps, d.mean_n_pos, "o-", ms=3)
            for a in axs[:, j]:
                a.set_xscale("log")
            axs[2, j].set_xlabel("epsilon (daily-return units)")
        axs[0, 0].set_ylabel("true Sharpe")
        axs[1, 0].set_ylabel("true volatility")
        axs[2, 0].set_ylabel("number of positions")
        axs[0, 0].legend(fontsize=7)
        fig.suptitle("Price of robustness" + (" (extended grid)" if sname == "ext_grid" else ""))
        fig.tight_layout()
        fig.savefig(figs_dir / f"mc_price_of_robustness{'_ext' if sname == 'ext_grid' else ''}{suffix}.png", dpi=150)
        plt.close(fig)

    w = mc.wide(runs, "primary", mcc["T_primary"])
    d = (w["dro_cv"] - w["plugin_maxsharpe"]).to_numpy()
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(d, bins=40, color="tab:blue", alpha=0.8)
    ax.axvline(0, color="k", lw=1)
    ax.axvline(d.mean(), color="tab:red", ls="--", label=f"mean {d.mean():.4f}")
    ax.set_xlabel(f"true Sharpe: DRO minus plug-in max-Sharpe (T={mcc['T_primary']}, paired)")
    ax.set_ylabel("runs")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figs_dir / f"mc_paired_difference_T{mcc['T_primary']}{suffix}.png", dpi=150)
    plt.close(fig)

    h2 = tabs["mc_h2"]
    d2 = h2[h2["T"].notna()]
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.errorbar(range(len(d2)), d2.estimate, yerr=[d2.estimate - d2.ci_lo, d2.ci_hi - d2.estimate], fmt="o", capsize=4)
    ax.axhline(0, color="k", lw=1)
    ax.set_xticks(range(len(d2)), [f"T={int(t)}" for t in d2["T"]])
    ax.set_ylabel("Delta(T): mean true-Sharpe gain of DRO over plug-in")
    last = h2.iloc[-1]
    ax.set_title(f"H2: DiD = {last.estimate:.4f}, 95% interval [{last.ci_lo:.4f}, {last.ci_hi:.4f}]", fontsize=10)
    fig.tight_layout()
    fig.savefig(figs_dir / f"mc_h2_delta_by_T{suffix}.png", dpi=150)
    plt.close(fig)

    ch = tabs["mc_eps_chosen"]
    ch = ch[ch.scenario == "primary"]
    fig, axs = plt.subplots(1, len(Ts), figsize=(4.2 * len(Ts), 3.6), sharey=True, squeeze=False)
    for j, T in enumerate(Ts):
        d = ch[ch["T"] == T]
        axs[0, j].bar(range(len(d)), d["share"])
        axs[0, j].set_xticks(range(len(d)), [f"{e:.0e}" for e in d.eps], rotation=90, fontsize=6)
        axs[0, j].set_title(f"T={T}")
    axs[0, 0].set_ylabel("share of runs")
    fig.suptitle("Cross-validated epsilon (primary scenario)")
    fig.tight_layout()
    fig.savefig(figs_dir / f"mc_eps_chosen{suffix}.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------------------------- notes
NOTES_TEMPLATE = """# Monte Carlo with known ground truth

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
(filled in by scripts/run_montecarlo.py)
<!-- results:end -->
"""

LIMITATIONS_MARK = "<!-- montecarlo-limitations -->"
LIMITATIONS_TEXT = f"""
{LIMITATIONS_MARK}
## Monte Carlo with known truth (scripts/run_montecarlo.py)

* The results depend on the assumed truth: the shrink factor s for the mean vector (primary 0.5; 1.0 and 0.0 are sensitivity runs) and the
  Student-t(5) return distribution. A different truth can reverse the ranking of methods.
* Simulated returns are independent over days. Real returns show volatility clustering and regime changes, which this study does not represent.
* The epsilon of each run is chosen by five-fold contiguous-block cross-validation, valid for independent returns. This differs from the walk-forward
  cross-validation used on the real data, so the two chosen-epsilon distributions are not directly comparable.
* The risk-aversion weight theta of the DR mean-variance objective is calibrated on the true parameters (so that the oracle mean-variance portfolio equals the oracle
  max-Sharpe portfolio). The plug-in max-Sharpe rival does not use the truth, so the comparison favours the DR objective in this one respect.
* The objective in `src/wdro.py` has no mean term, so the study adds one (equal to the original at theta = 0).
* Reproducibility holds to solver accuracy, not bit for bit: in a diagnostic re-run of 60 stored (T, run) pairs, 25 chose a neighbouring epsilon because the
  out-of-fold Sharpe values were tied to within 2.7e-6; the true Sharpe of the chosen portfolio changed by at most 7.3e-8 (the H1 effect is about 5e-3).
* With this objective the 1/N portfolio is reached only at epsilon of about 2 to 5, above the largest value of the pre-specified grid; in the primary study the chosen portfolio is
  therefore never exactly 1/N. The `ext_grid` scenario, added after a weights-only probe, extends the grid to show the plateau.
"""


def results_markdown(tabs, did, verdicts):
    parts = [f"**H1 (T = {verdicts['Tp']}): {verdicts['h1']}.** " + verdicts["h1_text"], "", md_table(tabs["mc_h1"]), "",
             f"**H2: {verdicts['h2']}.** " + verdicts["h2_text"], "", md_table(tabs["mc_h2"]), "", "Ablation and benchmark:", "",
             md_table(tabs["mc_ablation"]), "", "Summary by training length (primary scenario):", "",
             md_table(tabs["mc_summary_by_T"][["T", "method", "mean_true_sharpe", "ci_lo", "ci_hi", "mean_true_vol", "mean_n_pos", "mean_regret"]]), "",
             "Sensitivity:", "", md_table(tabs["mc_sensitivity"])]
    return "\n".join(parts)


# -------------------------------------------------------------------------------------------- driver
def run_all(cfg, rets, n_jobs=None, check_pilot=True):
    mcc = cfg["montecarlo"]
    n_jobs = n_jobs or mcc["n_jobs"]
    lines, failures, timing = [], [], {}
    ctxs = build_contexts(cfg, rets)
    tasks = plan_tasks(cfg, ctxs)
    lines.append(f"- Scenarios: {[s for s in ctxs]}; epsilon grid (primary) {len(ctxs['primary'][1].grid)} points "
                 f"({ctxs['primary'][1].grid[0]:.2e} to {ctxs['primary'][1].grid[-1]:.4g}), grid_stride {mcc['grid_stride']}; "
                 f"{len(tasks)} runs planned, n_jobs {n_jobs}.")
    for sname, (scen, ctx) in ctxs.items():
        lines.append(f"- Truth [{sname}]: oracle Sharpe S* = {ctx.s_orc:.6f}, theta = {ctx.theta:.6f}, oracle positions "
                     f"{int((ctx.w_orc > cfg['robust']['nonzero_threshold']).sum())}.")

    if check_pilot:
        pil = mcc["pilot"]
        t0 = time.perf_counter()
        ptasks = [("primary", pil["T"], r) for r in range(min(pil["runs"], ctxs["primary"][0]["n_runs"]))]
        execute(cfg, ctxs, ptasks, n_jobs)
        pwall = time.perf_counter() - t0
        pm = mc.load_checkpoints(cfg, ctxs["primary"][0], pil["T"], range(len(ptasks)))[0]
        secs = float(np.mean([m["seconds"] for m in pm]))
        base = len(ctxs["primary"][1].grid)
        est = sum(secs * len(ctxs[s][1].grid) / base for s, _, _ in tasks) / n_jobs
        timing.update(pilot_seconds_per_run=float(secs), pilot_wall=pwall, projected_seconds=float(est))
        lines.append(f"- Pilot: {len(ptasks)} runs at T={pil['T']}, {secs:.2f} s per run per worker (pilot wall {pwall:.1f} s); projected total "
                     f"{est / 60:.1f} min on {n_jobs} workers (budget {mcc['time_budget_hours']:.1f} h).")
        if est > mcc["time_budget_hours"] * 3600:
            failures.append(f"pilot projects {est / 3600:.2f} h > budget {mcc['time_budget_hours']} h: set montecarlo.grid_stride: 2 in config.yaml and re-run")
            return {"tables": {}, "lines": lines, "failures": failures, "timing": timing}

    t0 = time.perf_counter()
    n_new = execute(cfg, ctxs, tasks, n_jobs)
    timing["main_wall"] = time.perf_counter() - t0
    lines.append(f"- Main job: {n_new} runs computed this invocation (the rest came from checkpoints), wall time {timing['main_wall']:.1f} s.")

    runs, metas = collect(cfg, ctxs)
    tabs, did, s_short, s_long = analyse(cfg, runs, metas, ctxs)
    checks = sanity_checks(cfg, ctxs, runs, metas)
    for name, ok, detail in checks:
        lines.append(f"- Check {'PASS' if ok else 'FAIL'}: {name}: {detail}")
        if not ok:
            failures.append(name)
    h1 = tabs["mc_h1"]
    h1p = h1[h1.primary].iloc[0]
    h2 = tabs["mc_h2"].iloc[-1]
    tp = mcc["T_primary"]
    verdicts = {
        "Tp": tp, "h1": h1p.verdict, "h2": h2.verdict,
        "h1_text": (f"Mean paired difference {h1p.mean_diff:+.4f} (median {h1p.median_diff:+.4f}), bootstrap SE {h1p.boot_se:.4f}, 95% interval "
                    f"[{h1p.ci_lo:+.4f}, {h1p.ci_hi:+.4f}]; DRO better in {100 * h1p.share_dro_wins:.1f}% of runs."),
        "h2_text": (f"Delta({mcc['T_short']}) = {s_short['mean']:+.4f}, Delta({mcc['T_long']}) = {s_long['mean']:+.4f}; difference-in-differences "
                    f"{h2.estimate:+.4f}, 95% interval [{h2.ci_lo:+.4f}, {h2.ci_hi:+.4f}]."),
    }
    lines.insert(0, f"- H1 verdict: {verdicts['h1']} (T={tp}). {verdicts['h1_text']}")
    lines.insert(1, f"- H2 verdict: {verdicts['h2']}. {verdicts['h2_text']}")
    return {"tables": tabs, "lines": lines, "failures": failures, "timing": timing, "runs": runs, "metas": metas, "did": did,
            "verdicts": verdicts, "checks": checks}


def main(args, cfg=None, n_jobs=None):
    from src.utils import load_config
    cfg = load_config() if cfg is None else cfg
    suffix = cfg["montecarlo"]["output_suffix"]
    rets = pd.read_parquet(Path(cfg["data"]["processed_dir"]) / "returns_simple.parquet")
    t0 = time.time()
    res = run_all(cfg, rets, n_jobs=n_jobs)
    tables_dir, figs_dir = Path(cfg["paths"]["tables_dir"]), Path(cfg["paths"]["figures_dir"])
    tables_dir.mkdir(parents=True, exist_ok=True)
    lines, failures = res["lines"], res["failures"]
    if res["tables"]:
        res["runs"].to_csv(tables_dir / f"mc_runs{suffix}.csv", index=False)
        for name, df in res["tables"].items():
            df.to_csv(tables_dir / f"{name}{suffix}.csv", index=False)
        make_figures(cfg, res["tables"], res["runs"], res["did"], figs_dir, suffix)
        notes = Path(cfg["montecarlo"]["notes_path"])
        if not notes.exists():
            notes.write_text(NOTES_TEMPLATE, encoding="utf-8")
        found = update_notes_blocks(notes, {"results": results_markdown(res["tables"], res["did"], res["verdicts"])})
        lim = Path("docs/limitations.md")
        if lim.exists() and LIMITATIONS_MARK not in lim.read_text(encoding="utf-8"):
            with open(lim, "a", encoding="utf-8") as f:
                f.write(LIMITATIONS_TEXT)
        lines.append(f"- Tables written with suffix {suffix}; notes blocks regenerated: {found}.")
    lines.append(f"- Total run time {time.time() - t0:.0f} s." + (" ALL CHECKS PASSED." if not failures else ""))
    if failures:
        lines.append("- CHECKS FAILED: " + "; ".join(failures))
    print("\n".join(lines))
    if args.log:
        with open(cfg["paths"]["results_log"], "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d} - Monte Carlo with known truth, H1 and H2 (scripts/run_montecarlo.py)\n" + "\n".join(lines) + "\n")
            for name in ("mc_h1", "mc_h2", "mc_ablation"):
                if name in res["tables"]:
                    f.write(f"\n{name}:\n\n" + md_table(res["tables"][name]) + "\n")
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true")
    sys.exit(main(ap.parse_args()))
