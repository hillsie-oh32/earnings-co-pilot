"""
Week 1 test: pull a company's last 8 quarters of results from the SEC.

Run it with:   python week1.py AAPL
(swap AAPL for any US ticker, e.g. MSFT, NVDA, JPM)
"""

import sys

from data.sec import get_quarterly_financials

ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
name, table = get_quarterly_financials(ticker)
table = table[["Revenue", "Gross profit", "Operating income", "Net income"]].tail(8)

# Show figures in millions of dollars so they're easier to read.
millions = (table / 1_000_000).round(0)
millions.index = millions.index.strftime("%b %Y")

print(f"\n{name} ({ticker.upper()}) - last 8 quarters, $ millions\n")
print(millions.to_string())

# Quick sanity check: gross margin = gross profit / revenue.
margin = (table["Gross profit"] / table["Revenue"] * 100).round(1)
print(f"\nGross margin, latest quarter: {margin.iloc[-1]}%")
