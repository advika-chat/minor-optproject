"""Part 2 of task A11b: how close to 1/N are the cross-validated DR portfolios, and where does the Sharpe gap to 1/N come from?

Run from the repo root:  python -m scripts.run_qa2

Read-only analysis of existing outputs (weights in outputs/cache_bt, tables in outputs/tables). Writes outputs/tables/qa2_dr_vs_eqw_qa2.csv
(one row per rebalance and method), qa2_decomposition_qa2.csv and qa2_cvar95_comparisons_qa2.csv. Exit code 0 if every internal consistency check passes.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path.cwd()
T = ROOT / "outputs" / "tables"
CFG = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
RF, TD, COST = CFG["risk_free_rate"], CFG["trading_days"], CFG["costs"]["transaction_cost_bps"] / 1e4
THRESHOLDS = (1e-6, 1e-3, 0.05)
NEAR = 0.05
FAILS = []


def check(name, ok, detail):
    print(("PASS " if ok else "FAIL ") + name + " | " + detail)
    if not ok:
        FAILS.append(name)


def sharpe(r):
    r = np.asarray(r, float)
    return float((r.mean() - RF / TD) / r.std(ddof=1) * np.sqrt(TD))


def engine(rn, pos, targets):
    """Gross and net daily returns and the cost per rebalance for a list of target weights (drifting weights, cost on the rebalance day)."""
    n_days, n = rn.shape
    first = pos[0]
    gross, net = np.empty(n_days - first), np.empty(n_days - first)
    drift = np.zeros(n)
    turns = []
    for i, p in enumerate(pos):
        end = pos[i + 1] if i + 1 < len(pos) else n_days
        w = targets[i]
        turn = np.abs(w - drift).sum()
        g = np.cumprod(1.0 + rn[p:end], axis=0)
        v = g @ w
        d = v / np.concatenate([[1.0], v[:-1]]) - 1.0
        gross[p - first:end - first] = d
        d = d.copy()
        d[0] -= COST * turn
        net[p - first:end - first] = d
        drift = w * g[-1] / v[-1]
        turns.append(turn)
    return gross, net, np.array(turns)


def main():
    rets = pd.read_parquet(ROOT / "data" / "processed" / "returns_simple.parquet")
    dr = pd.read_csv(T / "daily_returns_bt.csv", index_col=0, parse_dates=True)
    cb = pd.read_csv(T / "chosen_epsilon_over_time_bt.csv")
    summ = pd.read_csv(T / "backtest_summary_bt.csv").set_index("method")
    wm = pd.read_csv(T / "weights_drmsd_cv_bt.csv", index_col=0, parse_dates=True)
    pos = cb["position"].astype(int).tolist()
    n = rets.shape[1]
    ew = np.full(n, 1.0 / n)
    rn = rets.to_numpy()

    def ck(p):
        return json.load(open(ROOT / "outputs" / "cache_bt" / "W504" / f"p{p:05d}_drcvar.json"))

    W = {"drcvar_cv": [np.array(ck(p)[f"fix_{int(i):02d}"], float) for p, i in zip(pos, cb["drcvar_cv_idx"])],
         "drmsd_cv": [wm.loc[rets.index[p]].to_numpy(float) for p in pos]}
    rows = []
    for m, ws in W.items():
        for i, (p, w) in enumerate(zip(pos, ws)):
            rows.append({"method": m, "position": p, "date": str(rets.index[p].date()), "eps": float(cb.loc[i, f"{m}_eps"]), "eps_index": int(cb.loc[i, f"{m}_idx"]),
                         "effective_n": float(1 / np.sum(w ** 2)), "l1_to_equal_weight": float(np.abs(w - ew).sum()), "max_abs_diff_to_equal_weight": float(np.abs(w - ew).max()),
                         "sum_w": float(w.sum())})
    tab = pd.DataFrame(rows)
    tab.to_csv(T / "qa2_dr_vs_eqw_qa2.csv", index=False)
    check("weights sum to 1", bool((tab.sum_w - 1).abs().max() < 1e-8), f"max |sum - 1| = {(tab.sum_w - 1).abs().max():.1e}")
    print("\n2a: distance of the cross-validated portfolios to 1/N, per rebalance (115 rebalances)")
    for m in W:
        d = tab[tab.method == m]
        cnt = {f"L1 < {t:g}": int((d.l1_to_equal_weight < t).sum()) for t in THRESHOLDS}
        print(f"  {m}: {cnt}; effective N mean {d.effective_n.mean():.2f}, min {d.effective_n.min():.2f}; max L1 {d.l1_to_equal_weight.max():.3f}; "
              f"eps values {d.eps.round(5).value_counts().to_dict()}")
        print(f"     among rebalances with L1 >= 1e-6: L1 range {d[d.l1_to_equal_weight >= 1e-6].l1_to_equal_weight.min():.4f} to {d.l1_to_equal_weight.max():.4f}; effective N range "
              f"{d[d.l1_to_equal_weight >= 1e-6].effective_n.min():.2f} to {d[d.l1_to_equal_weight >= 1e-6].effective_n.max():.2f}")
    # 2b decomposition for both methods
    out = []
    n_days = len(dr)
    for m in W:
        d = tab[tab.method == m].reset_index(drop=True)
        gross, net, turns = engine(rn, pos, W[m])
        check(f"{m}: rebuilt net series equals the stored daily series", float(np.abs(net - dr[m].to_numpy()).max()) < 1e-12, f"max diff {np.abs(net - dr[m].to_numpy()).max():.1e}")
        g_ew, n_ew, t_ew = engine(rn, pos, [ew] * len(pos))
        check("1/N: rebuilt net series equals the stored daily series", float(np.abs(n_ew - dr["equal_weight"].to_numpy()).max()) < 1e-12, f"max diff {np.abs(n_ew - dr['equal_weight'].to_numpy()).max():.1e}")
        diff = net - n_ew
        near_period = np.zeros(n_days, bool)
        for i, p in enumerate(pos):
            end = pos[i + 1] if i + 1 < len(pos) else len(rets)
            near_period[p - pos[0]:end - pos[0]] = bool(d.l1_to_equal_weight[i] < NEAR)
        far = ~near_period
        s_dr, s_ew = sharpe(net), sharpe(n_ew)
        hyb_far = n_ew + diff * far        # DR portfolio only in the periods that are not near 1/N
        hyb_near = n_ew + diff * near_period
        cost_dr, cost_ew = COST * turns.sum(), COST * t_ew.sum()
        gross_gap = sharpe(gross) - sharpe(g_ew)
        out.append({"method": m, "sharpe_dr": s_dr, "sharpe_1n": s_ew, "sharpe_gap": s_dr - s_ew,
                    "periods_near": int(d.l1_to_equal_weight.lt(NEAR).sum()), "periods_far": int(d.l1_to_equal_weight.ge(NEAR).sum()),
                    "days_near": int(near_period.sum()), "days_far": int(far.sum()),
                    "mean_daily_diff_near": float(diff[near_period].mean()) if near_period.any() else np.nan, "mean_daily_diff_far": float(diff[far].mean()) if far.any() else np.nan,
                    "sum_daily_diff_near": float(diff[near_period].sum()), "sum_daily_diff_far": float(diff[far].sum()),
                    "mean_daily_diff_all": float(diff.mean()),
                    "sharpe_1n_plus_far_differences": sharpe(hyb_far), "sharpe_1n_plus_near_differences": sharpe(hyb_near),
                    "avg_turnover_dr": float(turns[1:].mean()), "avg_turnover_1n": float(t_ew[1:].mean()),
                    "total_cost_dr": cost_dr, "total_cost_1n": cost_ew, "cost_difference_sum": cost_dr - cost_ew,
                    "sharpe_gap_before_costs": gross_gap, "sharpe_gap_after_costs": s_dr - s_ew,
                    "mean_daily_diff_before_costs": float((gross - g_ew).mean())})
        check(f"{m}: summary table agrees (Sharpe, turnover, cost)", abs(s_dr - summ.loc[m, "sharpe"]) < 1e-9 and abs(turns[1:].mean() - summ.loc[m, "avg_turnover"]) < 1e-9 and abs(cost_dr - summ.loc[m, "total_cost"]) < 1e-9,
              f"Sharpe {s_dr:.6f} vs {summ.loc[m, 'sharpe']:.6f}; turnover {turns[1:].mean():.6f} vs {summ.loc[m, 'avg_turnover']:.6f}")
    dec = pd.DataFrame(out)
    dec.to_csv(T / "qa2_decomposition_qa2.csv", index=False)
    print("\n2b: decomposition (DR minus 1/N, daily net returns)")
    print(dec.T.to_string(header=False))
    # 2c
    bt = pd.read_csv(T / "backtest_bootstrap_bt.csv")
    d = bt[(bt.metric == "cvar95_daily") & (bt.versus != "(level)") & bt.method.isin(["drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime"])].copy()
    for c in ("estimate", "ci_lo", "ci_hi"):
        d[c + "_pp"] = d[c] * 100.0
    d = d[["method", "versus", "estimate_pp", "ci_lo_pp", "ci_hi_pp", "excludes_zero"]]
    d.to_csv(T / "qa2_cvar95_comparisons_qa2.csv", index=False)
    check("eight CVaR95 comparisons found", len(d) == 8, f"{len(d)} rows")
    print("\n2c: CVaR95 difference (DR minus comparator), percentage points of daily loss; negative = lower tail risk")
    print(d.round(3).to_string(index=False))
    print(f"\nranges: versus plug-in {-d[d.versus == 'plugin_maxsharpe'].estimate_pp.max():.3f} to {-d[d.versus == 'plugin_maxsharpe'].estimate_pp.min():.3f} pp lower; "
          f"versus 1/N {-d[d.versus == 'equal_weight'].estimate_pp.max():.3f} to {-d[d.versus == 'equal_weight'].estimate_pp.min():.3f} pp lower")
    print(f"\nFAILED: {FAILS}" if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
