"""
Puts everything together: the numbers, the red flags, the documents, and the
AI write-up. Used by both week3.py (command line) and app.py (the website).
"""

from datetime import date
from pathlib import Path

import pandas as pd

from data import esef
from data.sec import get_financials, is_sec_ticker, CompanyNotSupported
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


def load_financials(query):
    """
    Find the company and its figures. The SEC comes first (US-listed
    companies, with quarterly figures for US ones). If the SEC has no match,
    or the query is a European ticker like TSCO.L, look in UK and European
    annual reports (ESEF) instead.
    """
    if esef.is_european_ticker(query):
        return esef.get_financials(query)
    if is_sec_ticker(query):
        return get_financials(query)
    # A company name whose best match on Yahoo is listed in the UK or Europe
    # (e.g. "next plc", not NextEra): look there first, then at the SEC.
    if esef.prefers_europe(query):
        try:
            return esef.get_financials(query)
        except CompanyNotSupported as europe_error:
            try:
                return get_financials(query)
            except CompanyNotSupported:
                raise europe_error from None
    try:
        return get_financials(query)
    except CompanyNotSupported as sec_error:
        try:
            return esef.get_financials(query)
        except CompanyNotSupported as europe_error:
            # Give the most useful reason: Europe's if a European share was
            # found or nothing matched anywhere, otherwise the SEC's.
            useful = (str(europe_error).startswith("Found")
                      or str(sec_error).startswith("No US-listed company"))
            raise (europe_error if useful else sec_error) from None


def build_numbers(query):
    """Everything the code calculates (no AI). Returns a dictionary of results."""
    data = load_financials(query)
    if data["source"] == "sec":
        # Commercial and savings banks (industry codes 6020-6036) get the bank
        # view even if their figures are hard to find, rather than company
        # checks that don't apply to them.
        sic, industry = get_industry(data["cik"])
        data["is_bank"] = data["is_bank"] or bool(sic and 6020 <= sic <= 6036)
        data["financial"] = bool(sic and 6000 <= sic < 6500)
        filings = get_filing_list(data["cik"])
        history = filing_history_flags(filings)
    else:
        # No SEC industry code: use Yahoo's industry instead.
        sic, industry, sector = None, *esef.get_industry(data["ticker"])
        data["is_bank"] = data["is_bank"] or bool(industry and industry.startswith("Banks"))
        data["financial"] = sector == "Financial Services"
        filings, history = data["filings"], None  # no SEC filing history to check
    data["sic"], data["industry"] = sic, industry
    per_year = 4 if data["quarterly"] else 1
    q = add_ratios(data["table"], per_year)
    now, ago = trailing_twelve_months(q, per_year)

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
    if n["source"] == "esef":
        notes.append(f"Annual figures from the company's annual reports, which UK and EU listed "
                     f"companies publish in a machine-readable format (ESEF). Half-year "
                     f"results aren't published that way, so the latest figures are from the "
                     f"last annual report. Figures are in {n['currency']}, under IFRS.")
    elif not n["quarterly"]:
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
        if n["financial"]:
            industry = n["industry"] or "financial services"
            warnings.append(f"This is a financial company ({industry}). The red-flag checks are "
                            "designed for non-financial businesses: insurers and finance "
                            "companies hold customer money, so measures like debt, cash flow "
                            "and receivables mean something different. Treat the flags with "
                            "caution.")
    age = (pd.Timestamp(date.today()) - q.index[-1]).days
    if n["source"] == "esef" and age > 460:
        warnings.append(f"The latest annual report in the filings.xbrl.org database is for the "
                        f"year to {q.index[-1]:%d %b %Y}, {age} days ago. A newer report may "
                        "have been published but not yet added, so check the company's "
                        "website for its latest results.")
    elif age > (150 if n["quarterly"] else 460):
        warnings.append(f"The latest {n['period']} in the SEC's data ends "
                        f"{q.index[-1]:%d %b %Y}, {age} days ago. More recent results may be "
                        "missing from the SEC's data, so check the company's latest filings.")
    return notes, warnings


def history_text(history):
    """The SEC filing-history check in words (None: not an SEC filer)."""
    if history is None:
        return ("not checked (the company doesn't file with the SEC; check its annual report "
                "for auditor changes or restatements)")
    return "; ".join(history) if history else "no auditor changes, restatements or late filings"


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
    returns = shareholder_returns(n["now"])
    lines.append(f"Dividends paid: {returns['dividends_text']}; share buybacks: "
                 f"{returns['buybacks_text']}; dividend payout ratio: {returns['payout_text']}")

    flag_lines = [f"[{f['status']}] {f['check']}: {f['detail']}" for f in n["flags"]]
    flag_lines.append("Filing history (last 3 years): " + history_text(n["history"]))

    return "\n\n".join([
        f"Currency: {n['currency']}. Money figures in millions. "
        f"Periods: {'quarters' if n['quarterly'] else 'financial years'}."
        + (" Company type: bank." if n["is_bank"] else ""),
        *[f"NOTE: {x}" for x in n["notes"]],
        *[f"WARNING: {w}" for w in n["warnings"]],
        f"Last {len(snapshot)} {n['period']}s:\n" + snapshot.round(1).to_string(),
        f"{period} vs the year before ({n['currency']}m):\n" + "\n".join(lines),
        "Red-flag scan:\n" + "\n".join(flag_lines),
        *([f"Market data (Yahoo Finance, {date.today():%d %b %Y}):\n{n['market_text']}"]
          if n.get("market_text") else []),
        *([n["insiders_text"]] if n.get("insiders_text") else []),
    ])


def shareholder_returns(now):
    """
    Cash returned to shareholders over the last 12 months (or latest year),
    from the cash flow statement, and the share of profit paid as dividends.
    """
    dividends, buybacks, profit = (now["Dividends paid"], now["Share buybacks"],
                                   now["Net income"])

    def amount(x):
        return "none reported" if pd.isna(x) or x <= 0 else f"{x / 1e6:,.0f}m"

    if pd.isna(dividends) or dividends <= 0:
        payout, payout_text = None, "no dividend"
    elif pd.isna(profit) or profit <= 0:
        payout, payout_text = None, "not meaningful (loss-making)"
    else:
        payout = dividends / profit
        # A tiny profit makes the ratio huge and meaningless (e.g. BP in 2025).
        payout_text = (f"{payout:.0%} of profit" if payout <= 5 else
                       "not meaningful (profit close to zero)")
        if payout > 5:
            payout = None
    return {"dividends": dividends, "buybacks": buybacks, "payout": payout,
            "dividends_text": amount(dividends), "buybacks_text": amount(buybacks),
            "payout_text": payout_text}


def get_documents(n):
    """
    The filings the AI reads. US companies: the last two earnings releases and
    the latest 10-Q/10-K. Foreign companies: the last two results
    announcements (6-K) and the annual report (20-F), cut to fit the budget.
    """
    if n["source"] == "esef":
        # Results announcements aren't in a free structured database, so
        # the AI reads the annual report.
        documents = [esef.get_annual_report_text(n["lei"], max_chars=MAX_DOCUMENT_CHARS)]
    elif n["quarterly"]:
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


STATUS_LABELS = {"RED": "**Red**", "AMBER": "**Amber**", "OK": "OK", "N/A": "n/a"}


def _cell(value, kind):
    """One table figure: money in millions with commas, or a percentage."""
    if pd.isna(value):
        return "n/a"
    return f"{value:,.0f}" if kind == "money" else f"{value:.1f}%"


def numbers_markdown(n, include_market=True):
    """The calculated numbers as readable Markdown tables, for the saved report."""
    cur = n["currency"]
    period = "Last 12 months" if n["quarterly"] else "Latest year"
    prior = "Previous 12 months" if n["quarterly"] else "Year before"
    out = [f"### {period} vs {prior.lower()}", "",
           f"| {cur} m | {prior} | {period} | Change |", "|:--|--:|--:|--:|"]
    for item in n["headlines"]:
        before, after = n["ago"][item], n["now"][item]
        change = (f"{after / before - 1:+.1%}" if pd.notna(before) and pd.notna(after) and before
                  else "n/a")
        out.append(f"| {item} | {_cell(before / 1e6, 'money')} | {_cell(after / 1e6, 'money')} "
                   f"| {change} |")
    if not n["is_bank"] and pd.notna(n["now"]["Net debt"]):
        net_debt = n["now"]["Net debt"]
        out.append(f"| {'Net debt' if net_debt > 0 else 'Net cash'} | | "
                   f"{abs(net_debt) / 1e6:,.0f} | |")
    returns = shareholder_returns(n["now"])
    out += ["", f"**Returned to shareholders:** dividends {returns['dividends_text']}, "
                f"buybacks {returns['buybacks_text']}. Dividend payout: {returns['payout_text']}."]

    # The recent periods, one row each.
    snapshot = n["snapshot"]
    names = [c.removesuffix(" m").removesuffix(" %") for c in snapshot.columns]
    kinds = ["money" if c.endswith(" m") else "pct" for c in snapshot.columns]
    out += ["", f"### Last {len(snapshot)} {n['period']}s ({cur} m)", "",
            "| " + " | ".join([n["period"].title() + " ending", *names]) + " |",
            "|:--|" + "--:|" * len(names)]
    for when, row in snapshot.iterrows():
        out.append(f"| {when:%b %Y} | "
                   + " | ".join(_cell(v, k) for v, k in zip(row, kinds)) + " |")

    # Red flags, the raised ones first.
    order = {"RED": 0, "AMBER": 1, "OK": 2, "N/A": 3}
    out += ["", "### Red-flag scan" + (" (bank checks)" if n["is_bank"] else ""), "",
            "| Status | Check | Finding |", "|:--|:--|:--|"]
    for f in sorted(n["flags"], key=lambda f: order[f["status"]]):
        out.append(f"| {STATUS_LABELS[f['status']]} | {f['check']} | "
                   f"{f['detail'].replace('|', '/').replace('->', '→')} |")
    out += ["", "**Filing history (last 3 years):** " + history_text(n["history"]) + "."]

    if include_market and n.get("market_text"):
        out += ["", f"### Market data (Yahoo Finance, {date.today():%d %b %Y})", ""]
        out += [f"- {line}" for line in n["market_text"].splitlines() if line.strip()]
    if include_market and n.get("insiders_text"):
        out += ["", "### Insider trading (SEC Form 4)", ""]
        out += [f"- {line}" for line in n["insiders_text"].splitlines() if line.strip()]
    return "\n".join(out)


def report_markdown(n, documents, report, generated=None):
    """The full report as a Markdown document: numbers, AI write-up and sources."""
    # Use the AI's own title (e.g. "Kraft Heinz (KHC): Q3 2026 Briefing Pack")
    # as the document title, so the report doesn't have two.
    title = f"{n['name']} ({n['ticker']})"
    body = report.strip()
    if body.startswith("# "):
        title, _, body = body.partition("\n")
        title, body = title[2:].strip(), body.strip()
    body = body.removesuffix("---").strip()  # the sources line below has its own divider
    when = generated or date.today()
    return (f"# {title}\n\n"
            f"_Earnings Copilot · generated {when:%d %B %Y} · figures in {n['currency']} "
            f"from SEC filings_\n\n"
            f"## The numbers\n\n{numbers_markdown(n)}\n\n---\n\n{body}\n\n---\n"
            f"_Sources: {'; '.join(d['title'] for d in documents)}. "
            f"Figures from SEC XBRL data._\n")


def save_report(n, documents, report):
    """Save the full report as a Markdown file and return its path."""
    REPORTS_FOLDER.mkdir(exist_ok=True)
    path = REPORTS_FOLDER / f"{n['ticker']}_{date.today().isoformat()}.md"
    path.write_text(report_markdown(n, documents, report), encoding="utf-8")
    return path
