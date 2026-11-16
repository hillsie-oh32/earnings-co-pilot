"""
Insider trading: when a company's directors, senior officers and 10% owners
buy or sell its shares, they must report it to the SEC within two business
days on a "Form 4". This reads those filings.

What matters most:
- Open-market purchases ("buys") are the strongest signal: insiders spend
  their own money only if they think the shares are worth more.
- Sales are weaker signals: insiders sell for many reasons (tax, diversifying,
  buying a house), and many sales are scheduled months in advance under a
  "Rule 10b5-1" trading plan.
- Share awards, option exercises and shares withheld for tax are routine pay
  matters, so they're listed but left out of the buy/sell totals.

Only US companies file Form 4s: foreign companies listed in the US are exempt.
"""

import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import pandas as pd
import requests

from config import SEC_USER_AGENT

ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

# What each transaction code means (only the common ones are named).
CODES = {"P": "Buy", "S": "Sell", "A": "Award", "M": "Option exercise",
         "F": "Tax withholding", "G": "Gift", "C": "Conversion", "X": "Option exercise",
         "D": "Returned to company", "J": "Other"}

_session = requests.Session()
_session.headers.update({"User-Agent": SEC_USER_AGENT})


def _get(url, attempts=3):
    """Download with a short timeout and a couple of retries for network blips."""
    for attempt in range(attempts):
        try:
            response = _session.get(url, timeout=15)
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            if attempt == attempts - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def _text(node, path):
    found = node.find(path)
    return found.text.strip() if found is not None and found.text else None


def _number(node, path):
    value = _text(node, path)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _role(owner):
    """'Director', 'CFO', '10% owner' etc. from the filing."""
    rel = owner.find("reportingOwnerRelationship")
    if rel is None:
        return ""
    roles = []
    if _text(rel, "isOfficer") in ("true", "1"):
        roles.append(_text(rel, "officerTitle") or "Officer")
    if _text(rel, "isDirector") in ("true", "1"):
        roles.append("Director")
    if _text(rel, "isTenPercentOwner") in ("true", "1"):
        roles.append("10% owner")
    return ", ".join(roles)


def _parse(xml, filed):
    """The share transactions in one Form 4."""
    root = ET.fromstring(xml)
    owners = root.findall("reportingOwner")
    name = " & ".join(_text(o, "reportingOwnerId/rptOwnerName") or "" for o in owners)
    role = _role(owners[0]) if owners else ""
    planned = _text(root, "aff10b5One") in ("true", "1")
    trades = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(t, "transactionCoding/transactionCode")
        shares = _number(t, "transactionAmounts/transactionShares/value")
        price = _number(t, "transactionAmounts/transactionPricePerShare/value")
        if not code or not shares:
            continue
        trades.append({
            "Date": pd.Timestamp(_text(t, "transactionDate/value") or filed),
            "Insider": name.title(), "Role": role,
            "Type": CODES.get(code, code), "Code": code,
            "Shares": shares, "Price": price,
            "Value": shares * price if price else None,
            "Planned (10b5-1)": planned if code == "S" else None,
            "Shares owned after": _number(
                t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
        })
    return trades


def get_insider_trades(cik, filings, months=12, max_filings=80, on_progress=None):
    """
    Insider transactions from Form 4s filed in the last `months` months
    (newest `max_filings` at most). Returns a table, newest first, and the
    number of filings that couldn't be read.
    """
    since = (date.today() - timedelta(days=31 * months)).isoformat()
    form4s = [f for f in filings if f["form"] == "4" and f["filingDate"] >= since][:max_filings]
    trades, failed = [], 0
    for i, filing in enumerate(form4s):
        if on_progress:
            on_progress(i, len(form4s))
        # The listed document is a formatted web page; the raw data has the
        # same name without the formatting folder in front.
        name = filing["primaryDocument"].split("/")[-1]
        url = f"{ARCHIVE}/{cik}/{filing['accessionNumber'].replace('-', '')}/{name}"
        time.sleep(0.11)  # stay under the SEC's limit of 10 requests a second
        try:
            trades += _parse(_get(url), filing["filingDate"])
        except (requests.RequestException, ET.ParseError):
            failed += 1
    table = pd.DataFrame(trades)
    if not table.empty:
        table = table.sort_values("Date", ascending=False).reset_index(drop=True)
    return table, failed


def summarise(table):
    """Totals of open-market buys and sells, and who made them."""
    if table.empty:
        return None
    buys = table[table["Code"] == "P"]
    sells = table[table["Code"] == "S"]
    planned = sells[sells["Planned (10b5-1)"] == True]  # noqa: E712 (column holds None too)
    return {
        "buy_value": buys["Value"].sum(), "sell_value": sells["Value"].sum(),
        "buyers": buys["Insider"].nunique(), "sellers": sells["Insider"].nunique(),
        "buy_count": len(buys), "sell_count": len(sells),
        "planned_share": (planned["Value"].sum() / sells["Value"].sum()
                          if sells["Value"].sum() else None),
        "biggest_buy": buys.loc[buys["Value"].idxmax()] if buys["Value"].notna().any() else None,
        "biggest_sell": (sells.loc[sells["Value"].idxmax()]
                         if sells["Value"].notna().any() else None),
    }


def insiders_as_text(table, months=12):
    """A short summary for the AI report."""
    s = summarise(table)
    if s is None:
        return f"Insider trading (Form 4, last {months} months): no transactions reported."
    lines = [f"Insider trading (SEC Form 4, last {months} months): open-market buys "
             f"${s['buy_value'] / 1e6:,.1f}m by {s['buyers']} insiders; sales "
             f"${s['sell_value'] / 1e6:,.1f}m by {s['sellers']} insiders"
             + (f" ({s['planned_share']:.0%} of sales value under pre-arranged 10b5-1 plans)"
                if s["planned_share"] is not None else "") + "."]
    for label, row in (("Largest buy", s["biggest_buy"]), ("Largest sale", s["biggest_sell"])):
        if row is not None:
            lines.append(f"{label}: {row['Insider']} ({row['Role']}), "
                         f"${row['Value'] / 1e6:,.1f}m on {row['Date']:%d %b %Y}.")
    return "\n".join(lines)
