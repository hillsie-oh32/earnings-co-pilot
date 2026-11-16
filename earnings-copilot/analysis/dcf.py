"""
A simple discounted cash flow (DCF) valuation, for learning and discussion.

A DCF says a business is worth the cash it will generate in future, with
later cash worth less than cash today ("discounting"). The steps:

1. Project free cash flow (FCF) for 5 years: revenue grows at a chosen rate,
   and a chosen share of revenue becomes free cash flow (the FCF margin).
2. Add a "terminal value" for all the years after year 5, assuming the cash
   flow then grows at a steady long-term rate forever.
3. Discount everything back to today at the discount rate (the return
   investors require for the risk), giving the enterprise value.
4. Subtract net debt to get the value of the shares, and divide by the
   number of shares.

The result depends heavily on the assumptions, which is why the page shows
what the current share price implies (a "reverse DCF") and how the value
changes with different assumptions (the sensitivity table). It is an
illustrative model, not a recommendation.
"""

import pandas as pd

YEARS = 5
RISK_FREE = 0.0425       # yield on long-term government bonds (assumption)
EQUITY_PREMIUM = 0.05    # extra return investors expect from shares (assumption)
DEBT_SPREAD = 0.015      # a company's borrowing cost above the risk-free rate
TAX_RATE = 0.21          # US corporate tax rate (interest is tax-deductible)


def _clip(x, low, high):
    return max(low, min(high, x))


def default_assumptions(n, market):
    """
    Starting assumptions, taken from the company's recent figures. These are
    a reasonable place to start, not a forecast: the user adjusts them.
    """
    now, ago = n["now"], n["ago"]
    revenue = now["Revenue"]
    growth = now["Revenue"] / ago["Revenue"] - 1 if pd.notna(ago["Revenue"]) and ago["Revenue"] else 0.05
    fcf = now["Operating cash flow"] - now["Capex"]
    margin = fcf / revenue if pd.notna(fcf) and revenue else 0.10

    # Discount rate: the weighted average cost of capital (WACC). Shares cost
    # the risk-free rate plus beta x the equity risk premium (CAPM); debt
    # costs a little more than the risk-free rate, less tax relief.
    # Yahoo's beta can be implausibly low (e.g. 0.1), which would make the
    # shares look almost risk-free, so keep it within a sensible range.
    beta = _clip((market or {}).get("beta") or 1.0, 0.8, 2.0)
    cost_of_equity = RISK_FREE + beta * EQUITY_PREMIUM
    equity_value = (market or {}).get("market_cap")
    debt = now["Total debt"] if pd.notna(now["Total debt"]) else 0.0
    if equity_value:
        weight_debt = debt / (debt + equity_value)
        wacc = ((1 - weight_debt) * cost_of_equity
                + weight_debt * (RISK_FREE + DEBT_SPREAD) * (1 - TAX_RATE))
    else:
        wacc = cost_of_equity

    return {
        "revenue": revenue,
        "growth": round(_clip(growth, -0.05, 0.25), 3),
        "margin": round(_clip(margin, -0.10, 0.50), 3),
        # Long-term growth around inflation; less for a shrinking business.
        "terminal_growth": 0.01 if growth < 0 else 0.025,
        "discount_rate": round(_clip(wacc, 0.07, 0.15), 4),
        "beta": beta, "cost_of_equity": cost_of_equity,
        # If debt isn't reported, count only the cash (or nothing if that's
        # missing too).
        "net_debt": (now["Net debt"] if pd.notna(now["Net debt"])
                     else -now["Cash"] if pd.notna(now["Cash"]) else 0.0),
        "shares": _shares(n, market),
    }


def _shares(n, market):
    """Shares in issue: market value / share price if available, else diluted shares."""
    if market and market.get("market_cap") and market.get("price"):
        return market["market_cap"] / market["price"]
    return n.get("diluted_shares")


def run_dcf(revenue, growth, margin, terminal_growth, discount_rate, net_debt, shares):
    """
    The DCF itself. Returns the projection table and the valuation, or None
    if the discount rate isn't above the long-term growth rate (the formula
    for the terminal value only works if it is).
    """
    if discount_rate <= terminal_growth:
        return None
    rows, sales = [], revenue
    for year in range(1, YEARS + 1):
        sales *= 1 + growth
        fcf = sales * margin
        factor = 1 / (1 + discount_rate) ** year
        rows.append({"Year": year, "Revenue": sales, "Free cash flow": fcf,
                     "Discount factor": factor, "Present value": fcf * factor})
    projection = pd.DataFrame(rows)

    final_fcf = projection["Free cash flow"].iloc[-1]
    terminal_value = final_fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    terminal_pv = terminal_value / (1 + discount_rate) ** YEARS
    enterprise = projection["Present value"].sum() + terminal_pv
    equity = enterprise - net_debt
    return {
        "projection": projection,
        "pv_cash_flows": projection["Present value"].sum(),
        "terminal_value": terminal_value, "terminal_pv": terminal_pv,
        "terminal_share": terminal_pv / enterprise if enterprise > 0 else None,
        "enterprise_value": enterprise, "equity_value": equity,
        "per_share": equity / shares if shares else None,
    }


def implied_growth(price, revenue, margin, terminal_growth, discount_rate, net_debt, shares):
    """
    Reverse DCF: the yearly revenue growth (years 1-5) that, with the other
    assumptions, would make the model value equal today's share price. In
    other words, what the market price is "pricing in".
    """
    if not price or not shares:
        return None

    def gap(g):
        result = run_dcf(revenue, g, margin, terminal_growth, discount_rate, net_debt, shares)
        return result["per_share"] - price if result else None

    low, high = -0.30, 0.80
    if gap(low) is None or gap(low) > 0 or gap(high) < 0:
        return None  # no growth rate in a sensible range matches the price
    for _ in range(60):  # halve the range each time (bisection)
        mid = (low + high) / 2
        if gap(mid) < 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def sensitivity(revenue, growth, margin, terminal_growth, discount_rate, net_debt, shares):
    """
    Value per share for discount rates and long-term growth rates around the
    chosen ones: a standard way to show how much the answer depends on them.
    """
    rates = [discount_rate + d for d in (-0.01, -0.005, 0, 0.005, 0.01)]
    growths = [terminal_growth + d for d in (-0.01, -0.005, 0, 0.005, 0.01)]
    table = {}
    for g in growths:
        column = []
        for r in rates:
            result = run_dcf(revenue, growth, margin, g, r, net_debt, shares)
            column.append(result["per_share"] if result else None)
        table[f"{g:.1%}"] = column
    return pd.DataFrame(table, index=[f"{r:.1%}" for r in rates])
