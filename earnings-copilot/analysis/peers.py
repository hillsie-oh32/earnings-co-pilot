"""
Peer comparison ("comps"): the same measures for a company and its peers, side
by side, with the peer median.

Every ratio here is a percentage or a multiple, so companies reporting in
different currencies can still be compared.
"""

import pandas as pd

from analysis.report import build_numbers
from data.market import get_quote

# Columns for ordinary companies and for banks. Each is (name, kind):
# "pct" shown as %, "pct2" as % to 2 decimals (for small ratios), "x" as a
# multiple, "n" as a count.
FORMATS = {"pct": "{:.1%}", "pct2": "{:.2%}", "x": "{:.1f}x", "n": "{:.0f}"}
COLUMNS = {
    False: [("Revenue growth", "pct"), ("Gross margin", "pct"), ("Operating margin", "pct"),
            ("Net margin", "pct"), ("FCF margin", "pct"), ("Net debt / EBITDA", "x"),
            ("P/E", "x"), ("Forward P/E", "x"), ("EV/EBITDA", "x"),
            ("Dividend yield", "pct"), ("1-year share price", "pct"), ("Red flags", "n")],
    True: [("Revenue growth", "pct"), ("Efficiency ratio", "pct"), ("Return on equity", "pct"),
           ("Cost of risk", "pct2"), ("Loans / deposits", "pct"), ("Equity / assets", "pct"),
           ("P/E", "x"), ("Forward P/E", "x"), ("Dividend yield", "pct"),
           ("1-year share price", "pct"), ("Red flags", "n")],
}

# For each measure, whether a higher figure is generally better (used to
# describe how the company compares). None = neither (e.g. valuations).
HIGHER_IS_BETTER = {
    "Revenue growth": True, "Gross margin": True, "Operating margin": True,
    "Net margin": True, "FCF margin": True, "Net debt / EBITDA": False,
    "Efficiency ratio": False, "Return on equity": True, "Cost of risk": False,
    "Loans / deposits": None, "Equity / assets": True, "P/E": None, "Forward P/E": None,
    "EV/EBITDA": None, "Dividend yield": None, "1-year share price": True, "Red flags": False,
}


def _ratio(a, b):
    return a / b if pd.notna(a) and pd.notna(b) and b else None


def company_row(n, quote, is_bank):
    """One row of the comparison for a company, from its SEC figures and quote."""
    now, ago = n["now"], n["ago"]
    growth = _ratio(now["Revenue"], ago["Revenue"])
    ebitda = now["Operating income"] + now["Depreciation"]
    row = {
        "Ticker": n["ticker"], "Company": n["name"],
        "Market value ($bn)": quote["market_cap"] / 1e9 if quote and quote["market_cap"]
                              and quote["currency"] == "USD" else None,
        "Revenue growth": growth - 1 if growth else None,
        "P/E": quote and quote["pe"], "Forward P/E": quote and quote["forward_pe"],
        "EV/EBITDA": quote and quote["ev_to_ebitda"],
        "Dividend yield": quote["dividend_yield"] / 100 if quote else None,
        "1-year share price": quote and quote["return_1y"],
        "Red flags": sum(f["status"] in ("RED", "AMBER") for f in n["flags"]),
    }
    if is_bank:
        row.update({
            "Efficiency ratio": _ratio(now["Operating costs"], now["Revenue"]),
            "Return on equity": _ratio(now["Net income"], now["Equity"]),
            "Cost of risk": _ratio(now["Credit loss provisions"], now["Loans"]),
            "Loans / deposits": _ratio(now["Loans"], now["Deposits"]),
            "Equity / assets": _ratio(now["Equity"], now["Total assets"]),
        })
    else:
        fcf = now["Operating cash flow"] - now["Capex"]
        row.update({
            "Gross margin": _ratio(now["Gross profit"], now["Revenue"]),
            "Operating margin": _ratio(now["Operating income"], now["Revenue"]),
            "Net margin": _ratio(now["Net income"], now["Revenue"]),
            "FCF margin": _ratio(fcf, now["Revenue"]),
            # Leverage only makes sense with positive EBITDA.
            "Net debt / EBITDA": (_ratio(now["Net debt"], ebitda)
                                  if pd.notna(ebitda) and ebitda > 0 else None),
        })
    return row


def compare(n, quote, peer_tickers, load=build_numbers, on_progress=None):
    """
    The comparison table: the company, each peer, and the peer median.
    `load` fetches a peer's figures (the website passes a cached version).
    Returns the table and a list of peers that couldn't be loaded.
    """
    is_bank = n["is_bank"]
    rows, failed = [company_row(n, quote, is_bank)], []
    for i, ticker in enumerate(peer_tickers):
        if on_progress:
            on_progress(i, ticker)
        try:
            peer = load(ticker)
            rows.append(company_row(peer, get_quote(peer["ticker"], not peer["quarterly"]),
                                    is_bank))
        except Exception as error:
            failed.append(f"{ticker} ({error})")

    table = pd.DataFrame(rows)
    columns = ["Ticker", "Company", "Market value ($bn)"] + [c for c, _ in COLUMNS[is_bank]]
    table = table.reindex(columns=columns)
    # Make every measure a proper number (missing ones become NaN), so the
    # median can be calculated.
    table[columns[2:]] = table[columns[2:]].apply(pd.to_numeric, errors="coerce")
    if len(table) > 1:
        median = table.iloc[1:].median(numeric_only=True)
        table.loc[len(table)] = {"Ticker": "", "Company": "Peer median", **median.to_dict()}
    return table, failed


def summary(table, is_bank):
    """Plain-English lines on how the company compares with the peer median."""
    if len(table) < 3:
        return []
    me, median = table.iloc[0], table.iloc[-1]
    lines = []
    for column, kind in COLUMNS[is_bank]:
        mine, typical = me[column], median[column]
        if pd.isna(mine) or pd.isna(typical) or column == "Red flags":
            continue
        better = HIGHER_IS_BETTER.get(column)
        fmt = FORMATS[kind].format
        if column == "Net debt / EBITDA" and min(mine, typical) <= 0:
            # Negative means net cash (more cash than debt).
            if mine <= 0 and typical <= 0:
                lines.append(f"**{column}:** net cash, like most peers")
            elif mine <= 0:
                lines.append(f"**{column}:** net cash, while the peer median is "
                             f"{fmt(typical)} ✅")
            else:
                lines.append(f"**{column}:** {fmt(mine)}, while the typical peer has "
                             "net cash ⚠️")
            continue
        # Within 10% of the median (relative) counts as in line.
        if abs(mine - typical) <= 0.1 * abs(typical):
            verdict = "in line with"
        else:
            verdict = "above" if mine > typical else "below"
        tone = ("" if better is None or verdict == "in line with" else
                " ✅" if (verdict == "above") == better else " ⚠️")
        lines.append(f"**{column}:** {fmt(mine)}, {verdict} the peer median of "
                     f"{fmt(typical)}{tone}")
    return lines
