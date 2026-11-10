"""
Week 2: results snapshot and accounting red-flag scan.

Run it with:   python week2.py AAPL
"""

import sys

from data.sec import get_quarterly_financials
from analysis.metrics import add_ratios, trailing_twelve_months
from analysis.red_flags import scan

ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
name, table = get_quarterly_financials(ticker)
q = add_ratios(table)

print(f"\n{'=' * 70}\n{name} ({ticker.upper()})\n{'=' * 70}")

# --- Results snapshot: last 8 quarters -------------------------------------
snapshot = q[["Revenue", "Revenue growth YoY %", "Gross margin %", "Operating margin %",
              "Net margin %", "Free cash flow"]].tail(8).copy()
snapshot["Revenue"] = snapshot["Revenue"] / 1e6
snapshot["Free cash flow"] = snapshot["Free cash flow"] / 1e6
snapshot = snapshot.rename(columns={"Revenue": "Revenue $m", "Free cash flow": "FCF $m",
                                    "Revenue growth YoY %": "Growth YoY %"})
snapshot.index = snapshot.index.strftime("%b %Y")

print("\nRESULTS SNAPSHOT (last 8 quarters)\n")
print(snapshot.round(1).to_string())

# --- Last twelve months vs the year before ----------------------------------
now, ago = trailing_twelve_months(q)
print("\nLAST 12 MONTHS vs PREVIOUS 12 MONTHS ($m)\n")
for item in ["Revenue", "Operating income", "Net income", "Operating cash flow"]:
    change = now[item] / ago[item] - 1
    print(f"  {item:<22}{ago[item] / 1e6:>12,.0f}{now[item] / 1e6:>12,.0f}   {change:+.1%}")
fcf_now = now["Operating cash flow"] - now["Capex"]
print(f"  {'Free cash flow':<22}{'':>12}{fcf_now / 1e6:>12,.0f}")
print(f"  {'Net debt (cash)':<22}{'':>12}{now['Net debt'] / 1e6:>12,.0f}")

# --- Red flags ---------------------------------------------------------------
flags = scan(now, ago)
print("\nRED-FLAG SCAN\n")
for flag in flags:
    print(f"  [{flag['status']:^5}] {flag['check']:<24}{flag['value']:>12}   {flag['detail']}")

print("\nWhy each check matters:")
for flag in flags:
    if flag["status"] in ("RED", "AMBER"):
        print(f"  - {flag['check']}: {flag['why']}")
print()
