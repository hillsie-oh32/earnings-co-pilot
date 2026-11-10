"""
The results snapshot: margins, growth and cash flow (and bank ratios for banks).

All the maths is done here in Python, never by the AI, so every number can be
checked against the company's filings.
"""

import pandas as pd

# Figures that build up over a period, so can be added across quarters.
FLOWS = ["Revenue", "Cost of revenue", "Gross profit", "SG&A", "Operating income",
         "Net income", "Depreciation", "Operating cash flow", "Capex",
         "Net interest income", "Fee & other income", "Operating costs",
         "Credit loss provisions"]


def add_ratios(q, periods_per_year=4):
    """
    Add margins, growth and cash flow measures to the table.
    `periods_per_year` is 4 for quarterly figures and 1 for annual figures.
    """
    q = q.copy()

    # Some companies don't report gross profit directly but do report costs.
    q["Gross profit"] = q["Gross profit"].fillna(q["Revenue"] - q["Cost of revenue"])

    q["Gross margin %"] = q["Gross profit"] / q["Revenue"] * 100
    q["Operating margin %"] = q["Operating income"] / q["Revenue"] * 100
    q["Net margin %"] = q["Net income"] / q["Revenue"] * 100

    # Compare with the same period last year, because many businesses are
    # seasonal (e.g. Apple's December quarter is always biggest).
    q["Revenue growth YoY %"] = q["Revenue"].pct_change(periods_per_year, fill_method=None) * 100

    # Free cash flow: the cash left after investing in the business.
    q["Free cash flow"] = q["Operating cash flow"] - q["Capex"]

    debt = q[["Long-term debt", "Current debt", "Commercial paper"]].sum(axis=1, min_count=1)
    q["Total debt"] = debt
    # Short-term investments count as cash: they can be sold within days.
    # If no debt figure was found, leave net debt blank rather than guess.
    q["Net debt"] = debt - q["Cash"] - q["Short-term investments"].fillna(0)

    # Bank ratios (blank for other companies).
    # Efficiency ratio: costs as a share of income. Lower is better.
    q["Efficiency ratio %"] = q["Operating costs"] / q["Revenue"] * 100
    # Cost of risk: yearly loan-loss provisions as a share of loans.
    q["Cost of risk %"] = q["Credit loss provisions"] * periods_per_year / q["Loans"] * 100
    return q


def trailing_twelve_months(q, periods_per_year=4):
    """
    The latest twelve months compared with the twelve months before.
    Returns two rows of figures: now and one year ago.

    For quarterly figures, flow items (e.g. revenue) are the last four
    quarters added together ("TTM"). For annual figures, it's simply the
    latest year. Balance sheet items (e.g. cash) are taken at the period end.
    """
    flows = [c for c in FLOWS if c in q]
    summed = q[flows].rolling(periods_per_year).sum()
    balances = q.drop(columns=flows)
    now = pd.concat([summed.iloc[-1], balances.iloc[-1]])

    # Newly listed companies may not have a year of history yet: leave the
    # comparison blank rather than crash.
    if len(q) > periods_per_year:
        ago_row = -1 - periods_per_year
        year_ago = pd.concat([summed.iloc[ago_row], balances.iloc[ago_row]])
    else:
        year_ago = pd.Series(float("nan"), index=now.index)

    # Average free cash flow per quarter over the last year (or as many
    # periods as exist), used to work out how long the cash will last.
    now["Average quarterly FCF"] = (q["Free cash flow"].tail(periods_per_year).mean()
                                    * periods_per_year / 4)
    return now, year_ago
