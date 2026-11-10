"""
Puts everything together: the numbers, the red flags, the documents, and the
AI write-up. Used by both week3.py (command line) and app.py (the website).
"""

from datetime import date
from pathlib import Path

import pandas as pd

from data.sec import get_financials
from data.filings import (get_filing_list, get_earnings_releases, get_latest_report,
                          get_foreign_results, filing_history_flags, get_industry)
from analysis.metrics import add_ratios, trailing_twelve_months
from analysis.red_flags import scan
from analysis.ai import write_report

# Claude Opus 5.5 prices, US dollars per million tokens.
INPUT_PRICE, OUTPUT_PRICE = 4.00, 20.00

REPORTS_FOLDER = Path(__file__).parent.parent / "reports"  # your own (not uploaded)
EXAMPLES_FOLDER = Path(__file__).parent.parent / "examples"  # finished examples to show

# The most the AI reads in one report (about 150,000 tokens, roughly $0.60).
MAX_DOCUMENT_CHARS = 600_000

# What to show for ordinary companies and for banks. Columns ending " m" are
# money, shown in millions of the company's currency.
HEADLINES = {
    False: ["Revenue", "Operating income", "Net income", "Operating cash flow"],
    True: ["Revenue", "Net interest income", "Net income", "Credit loss provisions"],
}
SNAPSHOT_COLUMNS = {
    False: {"Revenue": "Revenue m", "Revenue growth YoY %": "Growth YoY %",
            "Gross margin %": "Gross margin %", "Operating margin %": "Operating margin %",
            "Net margin %": "Net margin %", "Free cash flow": "FCF m"},
    True: {"Revenue": "Revenue m", "Revenue growth YoY %": "Growth YoY %",
           "Net interest income": "Net interest income m", "Net income": "Net income m",
           "Efficiency ratio %": "Efficiency ratio %", "Cost of risk %": "Cost of risk %"},
}


def build_numbers(query):
    """Everything the code calculates (no AI). Returns a dictionary of results."""
    data = get_financials(query)
    # Commercial and savings banks (industry codes 6020-6036) get the bank view
    # even if their figures are hard to find, rather than company checks that
    # don't apply to them.
    sic, _ = get_industry(data["cik"])
    data["is_bank"] = data["is_bank"] or bool(sic and 6020 <= sic <= 6036)
    per_year = 4 if data["quarterly"] else 1
    q = add_ratios(data["table"], per_year)
    now, ago = trailing_twelve_months(q, per_year)

    filings = get_filing_list(data["cik"])
    history = filing_history_flags(filings)

    columns = SNAPSHOT_COLUMNS[data["is_bank"]]
    snapshot = q[list(columns)].tail(8 if data["quarterly"] else 5).rename(columns=columns)
    money = [c for c in snapshot.columns if c.endswith(" m")]
    snapshot[money] = snapshot[money] / 1e6

    n = {**data, "quarters": q, "snapshot": snapshot, "now": now, "ago": ago,
         "filings": filings, "history": history,
         "flags": scan(now, ago, data["is_bank"]),
         "headlines": HEADLINES[data["is_bank"]],
         "period": "quarter" if data["quarterly"] else "year"}
    n["notes"], n["warnings"] = _notes_and_warnings(n)
    return n


def _notes_and_warnings(n):
    """Things the reader should know: neutral notes, and cautions."""
    notes, warnings = [], []
    q = n["quarters"]
    if not n["quarterly"]:
        notes.append(f"Annual figures: foreign companies only file structured figures with "
                     f"the SEC once a year, with their annual report. Figures are in "
                     f"{n['currency']}, under "
                     f"{'IFRS' if n['standard'] == 'ifrs-full' else 'US GAAP'} accounting.")
    elif n["currency"] != "USD":
        notes.append(f"Figures are in {n['currency']}.")
    if n["is_bank"]:
        notes.append("Bank view: banks lend out customers' deposits, so they are judged on "
                     "different measures, such as efficiency, return on equity, loan losses "
                     "and funding, rather than margins and cash flow.")
    else:
        sic, industry = get_industry(n["cik"])
        if sic and 6000 <= sic < 6500:
            warnings.append(f"This is a financial company ({industry}). The red-flag checks are "
                            "designed for non-financial businesses: insurers and finance "
                            "companies hold customer money, so measures like debt, cash flow "
                            "and receivables mean something different. Treat the flags with "
                            "caution.")
    age = (pd.Timestamp(date.today()) - q.index[-1]).days
    if age > (150 if n["quarterly"] else 460):
        warnings.append(f"The latest {n['period']} in the SEC's data ends "
                        f"{q.index[-1]:%d %b %Y}, {age} days ago. More recent results may be "
                        "missing from the SEC's data, so check the company's latest filings.")
    return notes, warnings


def numbers_as_text(n):
    """The calculated numbers written out as text, for the AI to read."""
    snapshot = n["snapshot"].copy()
    snapshot.index = snapshot.index.strftime("%b %Y")
    period = "Last 12 months" if n["quarterly"] else "Latest financial year"

    lines = []
    for item in n["headlines"]:
        before, after = n["ago"][item], n["now"][item]
        change = f"{after / before - 1:+.1%}" if pd.notna(before) and before else "n/a"
        lines.append(f"{item}: {before / 1e6:,.0f} -> {after / 1e6:,.0f} ({change})")
    if not n["is_bank"]:
        lines.append(f"Net debt (negative = net cash): {n['now']['Net debt'] / 1e6:,.0f}")

    flag_lines = [f"[{f['status']}] {f['check']}: {f['detail']}" for f in n["flags"]]
    flag_lines.append("Filing history (last 3 years): " +
                      ("; ".join(n["history"]) if n["history"] else
                       "no auditor changes, restatements or late filings"))

    return "\n\n".join([
        f"Currency: {n['currency']}. Money figures in millions. "
        f"Periods: {'quarters' if n['quarterly'] else 'financial years'}."
        + (" Company type: bank." if n["is_bank"] else ""),
        *[f"NOTE: {x}" for x in n["notes"]],
        *[f"WARNING: {w}" for w in n["warnings"]],
        f"Last {len(snapshot)} {n['period']}s:\n" + snapshot.round(1).to_string(),
        f"{period} vs the year before ({n['currency']}m):\n" + "\n".join(lines),
        "Red-flag scan:\n" + "\n".join(flag_lines),
    ])


def get_documents(n):
    """
    The filings the AI reads. US companies: the last two earnings releases and
    the latest 10-Q/10-K. Foreign companies: the last two results
    announcements (6-K) and the annual report (20-F), cut to fit the budget.
    """
    if n["quarterly"]:
        documents = (get_earnings_releases(n["cik"], n["filings"])
                     + [get_latest_report(n["cik"], n["filings"])])
    else:
        releases = get_foreign_results(n["cik"], n["filings"])
        room = MAX_DOCUMENT_CHARS - sum(len(d["text"]) for d in releases)
        documents = releases + [get_latest_report(n["cik"], n["filings"],
                                                  max_chars=max(room, 150_000))]
    return [d for d in documents if d]


def run_ai_report(n, documents, on_text=None, api_key=None):
    """Ask Claude for the write-up. Returns the report text and its cost in dollars."""
    report, usage = write_report(n["name"], n["ticker"], numbers_as_text(n), documents,
                                 on_text, api_key)
    cost = (usage.input_tokens * INPUT_PRICE + usage.output_tokens * OUTPUT_PRICE) / 1e6
    return report, usage, cost


def report_markdown(n, documents, report):
    """The full report as a Markdown document: numbers, AI write-up and sources."""
    return (f"# Earnings Copilot: {n['name']} ({n['ticker']})\n\n"
            f"_Generated {date.today():%d %B %Y}_\n\n"
            f"## The numbers\n\n```\n{numbers_as_text(n)}\n```\n\n{report}\n\n---\n"
            f"_Sources: {'; '.join(d['title'] for d in documents)}. "
            f"Figures from SEC XBRL data._\n")


def save_report(n, documents, report):
    """Save the full report as a Markdown file and return its path."""
    REPORTS_FOLDER.mkdir(exist_ok=True)
    path = REPORTS_FOLDER / f"{n['ticker']}_{date.today().isoformat()}.md"
    path.write_text(report_markdown(n, documents, report), encoding="utf-8")
    return path
