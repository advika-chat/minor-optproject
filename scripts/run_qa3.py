"""Audit of the final-wealth values quoted in the caption of paper figure 1 (task A11d).

Run from the repo root:  python -m scripts.run_qa3

Pandas and numpy only. Recomputes final net wealth and the geometric annualised return from outputs/tables/daily_returns_bt.csv, compares them with the
caption values in docs/figure_captions_qa2.md and the cagr column of outputs/tables/backtest_summary_bt.csv, and writes outputs/tables/qa3_final_wealth_qa3.csv.
Nothing existing is changed. Exit code 1 if any caption value does not match.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.cwd()
T = ROOT / "outputs" / "tables"
CAPTION_VALUES = {"equal_weight": 5.36, "plugin_maxsharpe": 5.36, "drcvar_regime": 5.16, "drmsd_cv": 4.86, "drmsd_regime": 4.62, "drcvar_cv": 4.57, "nifty_price_index": 2.67}
METHODS = ["equal_weight", "plugin_maxsharpe", "plugin_minvar", "drmsd_cv", "drcvar_cv", "drmsd_regime", "drcvar_regime", "nifty_price_index"]
CAGR_TOL = 1e-9


def main():
    r = pd.read_csv(T / "daily_returns_bt.csv", index_col=0, parse_dates=True)
    print("columns of daily_returns_bt.csv:", list(r.columns))
    print(f"index name {r.index.name!r}; rows {len(r)}; first {r.index[0].date()}; last {r.index[-1].date()}; NaN in the audited columns: {int(r[METHODS].isna().sum().sum())}")
    summ = pd.read_csv(T / "backtest_summary_bt.csv").set_index("method")
    caption_text = (ROOT / "docs" / "figure_captions_qa2.md").read_text(encoding="utf-8")
    wealth = (1.0 + r[METHODS].astype(float)).cumprod()
    n = len(r)
    rows = []
    print("\nmethod | rows | first | last | final wealth | caption | diff | cagr (table) | cagr (recomputed) | diff")
    for m in METHODS:
        fw = float(np.prod(1.0 + r[m].to_numpy(float)))
        assert abs(fw - wealth[m].iloc[-1]) < 1e-12
        cagr = fw ** (252.0 / n) - 1.0
        cap = CAPTION_VALUES.get(m, np.nan)
        cagr_tab = float(summ.loc[m, "cagr"])
        wealth_match = bool(round(fw, 2) == cap) if not np.isnan(cap) else None
        cagr_match = bool(abs(cagr - cagr_tab) < CAGR_TOL)
        rows.append({"method": m, "rows": n, "first_date": str(r.index[0].date()), "last_date": str(r.index[-1].date()), "caption_value": cap, "recomputed_value": round(fw, 6),
                     "difference": (round(fw, 6) - cap) if not np.isnan(cap) else np.nan, "cagr_from_table": cagr_tab, "cagr_recomputed": cagr, "cagr_difference": cagr - cagr_tab,
                     "match_flag": ("match" if wealth_match and cagr_match else "MISMATCH") if wealth_match is not None else ("not in caption; cagr " + ("match" if cagr_match else "MISMATCH"))})
        print(f"{m} | {n} | {r.index[0].date()} | {r.index[-1].date()} | {fw:.6f} | {cap} | {fw - cap if not np.isnan(cap) else float('nan'):+.6f} | {cagr_tab:.9f} | {cagr:.9f} | {cagr - cagr_tab:+.2e}")
    out = pd.DataFrame(rows)
    out.to_csv(T / "qa3_final_wealth_qa3.csv", index=False)

    print("\n1/N versus plug-in max-Sharpe")
    a, b = wealth["equal_weight"], wealth["plugin_maxsharpe"]
    print(f"final wealth: 1/N {a.iloc[-1]:.6f}; plug-in max-Sharpe {b.iloc[-1]:.6f}; difference (1/N minus plug-in) {a.iloc[-1] - b.iloc[-1]:+.6f}")
    for label, y in (("end of 2019", 2019), ("end of 2020", 2020), ("end of 2022", 2022), ("end of 2024", 2024)):
        d = r.index[r.index.year <= y][-1]
        print(f"  {label} ({d.date()}): 1/N {a.loc[d]:.6f}; plug-in {b.loc[d]:.6f}; difference {a.loc[d] - b.loc[d]:+.6f}")
    print(f"  last day ({r.index[-1].date()}): 1/N {a.iloc[-1]:.6f}; plug-in {b.iloc[-1]:.6f}; difference {a.iloc[-1] - b.iloc[-1]:+.6f}")
    dr = (r["equal_weight"] - r["plugin_maxsharpe"]).abs()
    print(f"  daily return series: days with |difference| > 1e-12: {int((dr > 1e-12).sum())} of {n}; maximum |difference| {dr.max():.6f}; correlation {r['equal_weight'].corr(r['plugin_maxsharpe']):.4f}")
    gap = (a - b)
    cross = int((np.sign(gap).diff().fillna(0) != 0).sum())
    print(f"  sign changes of the wealth gap over the window: {cross}; gap range {gap.min():+.4f} to {gap.max():+.4f}")
    print(f"  Sharpe check from the summary table: 1/N {summ.loc['equal_weight', 'sharpe']:.6f}; plug-in {summ.loc['plugin_maxsharpe', 'sharpe']:.6f}; "
          f"ann_return {summ.loc['equal_weight', 'ann_return']:.6f} vs {summ.loc['plugin_maxsharpe', 'ann_return']:.6f}; cagr {summ.loc['equal_weight', 'cagr']:.6f} vs {summ.loc['plugin_maxsharpe', 'cagr']:.6f}")

    bad = out[out.match_flag.str.contains("MISMATCH")]
    print("\ncaption sentence present in docs/figure_captions_qa2.md:", "final wealth is 5.36 for 1/N and for plug-in max-Sharpe" in caption_text)
    print(f"\nmismatches: {bad.method.tolist()}" if len(bad) else "\nall caption values and cagr values match")
    return 1 if len(bad) else 0


if __name__ == "__main__":
    sys.exit(main())
