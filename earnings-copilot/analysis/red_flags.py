"""
The red-flag scanner: accounting warning signs, with a separate set for banks.

Each check compares the last twelve months (or latest year) with the twelve
months before.
A flag is a reason to look more closely, not proof that anything is wrong:
there is often a perfectly good business explanation.
"""

import pandas as pd

RED, AMBER, OK, NA = "RED", "AMBER", "OK", "N/A"


def _flag(name, status, value, detail, why):
    return {"check": name, "status": status, "value": value, "detail": detail, "why": why}


def _missing(*values):
    return any(pd.isna(v) for v in values)


def receivables_vs_revenue(now, ago):
    why = ("If customers owe more and more relative to sales, revenue may be booked "
           "before cash is collected, or customers may be struggling to pay.")
    name = "Receivables vs revenue"
    if _missing(now["Receivables"], ago["Receivables"], now["Revenue"], ago["Revenue"]):
        return _flag(name, NA, "-", "Receivables not reported", why)
    dso_now = now["Receivables"] / now["Revenue"] * 365
    dso_ago = ago["Receivables"] / ago["Revenue"] * 365
    change = dso_now / dso_ago - 1
    status = RED if change > 0.25 else AMBER if change > 0.10 else OK
    return _flag(name, status, f"{dso_now:.0f} days",
                 f"Days sales outstanding {dso_ago:.0f} -> {dso_now:.0f} days ({change:+.0%})", why)


def cash_conversion(now, ago):
    why = ("Profit should turn into cash. If operating cash flow is consistently below "
           "net income, earnings may rely on accounting estimates rather than real cash.")
    name = "Cash conversion"
    if _missing(now["Operating cash flow"], now["Net income"]):
        return _flag(name, NA, "-", "Cash flow not reported", why)
    if now["Net income"] <= 0:
        return _flag(name, AMBER, "loss", "Company made a loss over the last 12 months", why)
    ratio = now["Operating cash flow"] / now["Net income"]
    status = RED if ratio < 0.7 else AMBER if ratio < 0.9 else OK
    return _flag(name, status, f"{ratio:.2f}x",
                 f"Operating cash flow is {ratio:.2f}x net income", why)


def inventory_build(now, ago):
    why = ("Inventory piling up faster than sales can mean demand is slowing and "
           "write-downs may follow. It can also be a deliberate stockpile.")
    name = "Inventory build-up"
    if _missing(now["Inventory"], ago["Inventory"], now["Cost of revenue"], ago["Cost of revenue"]) \
            or ago["Inventory"] == 0:
        return _flag(name, NA, "-", "No inventory reported", why)
    if now["Inventory"] < 0.02 * now["Total assets"]:
        return _flag(name, OK, "immaterial", "Inventory is under 2% of total assets", why)
    days_now = now["Inventory"] / now["Cost of revenue"] * 365
    days_ago = ago["Inventory"] / ago["Cost of revenue"] * 365
    change = days_now / days_ago - 1
    status = RED if change > 0.25 else AMBER if change > 0.10 else OK
    return _flag(name, status, f"{days_now:.0f} days",
                 f"Inventory days {days_ago:.0f} -> {days_now:.0f} ({change:+.0%})", why)


def gross_margin_trend(now, ago):
    why = ("A falling gross margin means the company is earning less on each sale: "
           "pricing pressure, rising costs or a weaker product mix.")
    name = "Gross margin trend"
    if _missing(now["Gross profit"], ago["Gross profit"]):
        return _flag(name, NA, "-", "Gross profit not reported", why)
    gm_now = now["Gross profit"] / now["Revenue"] * 100
    gm_ago = ago["Gross profit"] / ago["Revenue"] * 100
    change = gm_now - gm_ago
    status = RED if change < -3 else AMBER if change < -1.5 else OK
    return _flag(name, status, f"{gm_now:.1f}%",
                 f"Gross margin {gm_ago:.1f}% -> {gm_now:.1f}% ({change:+.1f} pts)", why)


def cash_runway(now, ago):
    why = ("A company burning cash needs to raise more money (diluting shareholders or "
           "adding debt) before it runs out. Under about a year leaves little room.")
    name = "Cash runway"
    burn = now["Average quarterly FCF"]
    if _missing(burn, now["Cash"]):
        return _flag(name, NA, "-", "Not enough cash flow data", why)
    if burn >= 0:
        return _flag(name, OK, "self-funding", "Free cash flow is positive", why)
    cash = now["Cash"] + (0 if pd.isna(now["Short-term investments"])
                          else now["Short-term investments"])
    months = cash / -burn * 3
    status = RED if months < 12 else AMBER if months < 24 else OK
    value = "under 1 month" if months < 1 else f"{months:.0f} months"
    return _flag(name, status, value,
                 f"Cash of ${cash / 1e6:,.0f}m vs burn of ${-burn / 1e6:,.0f}m per quarter", why)


def leverage(now, ago):
    why = ("Net debt / EBITDA shows how many years of earnings it would take to repay "
           "debt. Above about 3x, a downturn could make the debt hard to service.")
    name = "Leverage"
    ebitda = now["Operating income"] + now["Depreciation"]
    if _missing(ebitda, now["Net debt"]):
        return _flag(name, NA, "-", "Not enough data", why)
    if now["Net debt"] <= 0:
        return _flag(name, OK, "net cash", "Holds more cash than debt", why)
    if ebitda <= 0:
        return _flag(name, RED, "neg. EBITDA", "Has net debt but negative EBITDA", why)
    ratio = now["Net debt"] / ebitda
    status = RED if ratio > 3 else AMBER if ratio > 2 else OK
    return _flag(name, status, f"{ratio:.1f}x", f"Net debt is {ratio:.1f}x EBITDA", why)


def goodwill_vs_equity(now, ago):
    why = ("Goodwill comes from paying more than book value for acquisitions. If those "
           "deals disappoint it gets written down, which can wipe out equity.")
    name = "Goodwill vs equity"
    if _missing(now["Goodwill"]):
        return _flag(name, NA, "-", "No goodwill reported", why)
    if now["Equity"] <= 0:
        return _flag(name, AMBER, "neg. equity",
                     "Equity is negative (often caused by large share buybacks)", why)
    ratio = now["Goodwill"] / now["Equity"]
    status = RED if ratio > 1 else AMBER if ratio > 0.5 else OK
    return _flag(name, status, f"{ratio:.0%}", f"Goodwill is {ratio:.0%} of equity", why)


def beneish_m_score(now, ago):
    """
    Beneish M-Score (Beneish, 1999): eight ratios combined into one score that
    estimates how likely a company is to be manipulating its earnings.
    Above -1.78 suggests a higher likelihood of manipulation.
    """
    why = ("An academic model that famously flagged Enron before its collapse. It "
           "combines eight signs of earnings manipulation into one score.")
    name = "Beneish M-Score"

    def ratio(a, b):
        return a / b if not _missing(a, b) and b != 0 else None

    def side(x):
        sales = x["Revenue"]
        assets = x["Total assets"]
        hard_assets = (x["Current assets"] + x["PP&E"]) / assets
        return {
            "receivables": ratio(x["Receivables"], sales),
            "gross margin": ratio(x["Gross profit"], sales),
            "asset quality": None if _missing(hard_assets) else 1 - hard_assets,
            "sales": sales,
            "depreciation": ratio(x["Depreciation"], x["Depreciation"] + x["PP&E"]),
            "sga": ratio(x["SG&A"], sales),
            "leverage": ratio(x["Current liabilities"] + x["Long-term debt"], assets),
        }

    t, p = side(now), side(ago)
    variables = {
        "DSRI": ratio(t["receivables"], p["receivables"]),
        "GMI": ratio(p["gross margin"], t["gross margin"]),
        "AQI": ratio(t["asset quality"], p["asset quality"]),
        "SGI": ratio(t["sales"], p["sales"]),
        "DEPI": ratio(p["depreciation"], t["depreciation"]),
        "SGAI": ratio(t["sga"], p["sga"]),
        "LVGI": ratio(t["leverage"], p["leverage"]),
        "TATA": ratio(now["Net income"] - now["Operating cash flow"], now["Total assets"]),
    }

    # If an input is missing, use the neutral value (no change year on year).
    assumed = [k for k, v in variables.items() if v is None]
    for k in assumed:
        variables[k] = 0.0 if k == "TATA" else 1.0
    if len(assumed) > 3:
        return _flag(name, NA, "-", "Too many inputs missing to calculate", why)

    v = variables
    score = (-4.84 + 0.92 * v["DSRI"] + 0.528 * v["GMI"] + 0.404 * v["AQI"]
             + 0.892 * v["SGI"] + 0.115 * v["DEPI"] - 0.172 * v["SGAI"]
             + 4.679 * v["TATA"] - 0.327 * v["LVGI"])
    status = RED if score > -1.78 else AMBER if score > -2.22 else OK
    detail = f"Score {score:.2f} (above -1.78 = higher manipulation risk)"
    if assumed:
        detail += f"; assumed neutral: {', '.join(assumed)}"
    return _flag(name, status, f"{score:.2f}", detail, why)


# --- Bank checks --------------------------------------------------------------
# Banks lend out customers' deposits, so ordinary checks (debt, cash flow,
# receivables) don't work for them. These look at what matters for a bank.

def bank_efficiency(now, ago):
    why = ("The efficiency ratio is costs as a share of income. Lower is better: "
           "well-run large banks are often around 50-60%.")
    name = "Efficiency ratio"
    if _missing(now["Operating costs"], now["Revenue"]) or now["Revenue"] <= 0:
        return _flag(name, NA, "-", "Operating costs not reported", why)
    ratio = now["Operating costs"] / now["Revenue"] * 100
    detail = f"Costs are {ratio:.0f}% of income"
    change = None
    if not _missing(ago["Operating costs"], ago["Revenue"]) and ago["Revenue"] > 0:
        change = ratio - ago["Operating costs"] / ago["Revenue"] * 100
        detail += f" ({change:+.1f} pts vs prior year)"
    status = (RED if ratio > 75 else AMBER if ratio > 65 or (change or 0) > 5 else OK)
    if now.get("Revenue is estimate") == 1:
        # Income here is interest + fees only, so the ratio is overstated.
        detail += ("; income estimated from interest and fees only (excludes trading "
                   "income), so the true ratio is likely lower")
        status = AMBER if status == RED else status
    return _flag(name, status, f"{ratio:.0f}%", detail, why)


def bank_return_on_equity(now, ago):
    why = ("Return on equity (ROE) is profit as a share of shareholders' money. Banks "
           "need roughly 10%+ to beat the return investors expect.")
    name = "Return on equity"
    if _missing(now["Net income"], now["Equity"]) or now["Equity"] <= 0:
        return _flag(name, NA, "-", "Not enough data", why)
    roe = now["Net income"] / now["Equity"] * 100
    status = RED if roe < 5 else AMBER if roe < 9 else OK
    return _flag(name, status, f"{roe:.1f}%", f"Profit is {roe:.1f}% of equity", why)


def bank_cost_of_risk(now, ago):
    why = ("Cost of risk is the yearly charge for expected loan losses as a share of "
           "loans. A rising figure is often the first sign of trouble in the loan book.")
    name = "Cost of risk"
    if _missing(now["Credit loss provisions"], now["Loans"]) or now["Loans"] <= 0:
        return _flag(name, NA, "-", "Loan loss provisions or loans not reported", why)
    cost = now["Credit loss provisions"] / now["Loans"] * 100
    detail = f"Loan loss charges are {cost:.2f}% of loans"
    rising = False
    if not _missing(ago["Credit loss provisions"], ago["Loans"]) and ago["Loans"] > 0:
        before = ago["Credit loss provisions"] / ago["Loans"] * 100
        detail += f" (was {before:.2f}%)"
        rising = before > 0 and cost > before * 1.5
    status = RED if cost > 1.5 else AMBER if cost > 1.0 or rising else OK
    return _flag(name, status, f"{cost:.2f}%", detail, why)


def bank_loans_to_deposits(now, ago):
    why = ("Loans as a share of deposits. Above 100% the bank relies on borrowing from "
           "markets, which can dry up in a crisis.")
    name = "Loan-to-deposit ratio"
    if _missing(now["Loans"], now["Deposits"]) or now["Deposits"] <= 0:
        return _flag(name, NA, "-", "Loans or deposits not reported", why)
    ratio = now["Loans"] / now["Deposits"] * 100
    status = RED if ratio > 120 else AMBER if ratio > 100 else OK
    return _flag(name, status, f"{ratio:.0f}%", f"Loans are {ratio:.0f}% of deposits", why)


def bank_deposit_trend(now, ago):
    why = ("Deposits are a bank's main funding. A sharp fall can signal customers "
           "losing confidence, as at Silicon Valley Bank in 2023.")
    name = "Deposit trend"
    if _missing(now["Deposits"], ago["Deposits"]) or ago["Deposits"] <= 0:
        return _flag(name, NA, "-", "No prior-year deposits to compare", why)
    change = now["Deposits"] / ago["Deposits"] - 1
    status = RED if change < -0.2 else AMBER if change < -0.1 else OK
    return _flag(name, status, f"{change:+.0%}", f"Deposits {change:+.1%} vs a year ago", why)


def bank_equity_cushion(now, ago):
    why = ("Equity as a share of total assets: how much loss the bank can absorb. This "
           "is a simple stand-in for regulatory capital ratios (e.g. CET1), which the "
           "AI report reads from the filings.")
    name = "Equity cushion"
    if _missing(now["Equity"], now["Total assets"]) or now["Total assets"] <= 0:
        return _flag(name, NA, "-", "Not enough data", why)
    ratio = now["Equity"] / now["Total assets"] * 100
    status = RED if ratio < 4 else AMBER if ratio < 5 else OK
    return _flag(name, status, f"{ratio:.1f}%", f"Equity is {ratio:.1f}% of total assets", why)


CHECKS = [receivables_vs_revenue, cash_conversion, inventory_build, gross_margin_trend,
          cash_runway, leverage, goodwill_vs_equity, beneish_m_score]

BANK_CHECKS = [bank_efficiency, bank_return_on_equity, bank_cost_of_risk,
               bank_loans_to_deposits, bank_deposit_trend, bank_equity_cushion]


def scan(now, ago, is_bank=False):
    """Run every check and return a list of results."""
    return [check(now, ago) for check in (BANK_CHECKS if is_bank else CHECKS)]
