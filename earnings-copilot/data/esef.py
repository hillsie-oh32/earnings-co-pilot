"""
UK and European companies, from their annual reports in ESEF format.

Since 2021, every company listed on a UK or EU regulated market must publish
its annual report in ESEF ("European Single Electronic Format"): the report
is a web page with every figure in the accounts tagged in XBRL, the same
machine-readable standard the SEC uses. filings.xbrl.org collects these
reports and offers them for free.

This lets the tool cover companies that aren't listed in the US at all, such
as Tesco, Rolls-Royce or Siemens. The figures are IFRS, so once converted they
go through exactly the same calculations as foreign companies from the SEC.

Two limitations:
- Only annual reports are in ESEF, so these companies get annual figures.
- There's no list of stock market tickers, so we find the company's share
  on Yahoo Finance first, then match its name to the filer.
"""

import re
import time
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher
from functools import lru_cache

import requests
import yfinance as yf
from bs4 import BeautifulSoup, NavigableString

from data.sec import financials_from_facts, CompanyNotSupported

BASE = "https://filings.xbrl.org"

# Yahoo Finance ticker endings for UK and European stock exchanges, with the
# country and which index to compare the share price against.
EXCHANGES = {
    "L": ("UK", "London"), "IL": ("UK", "London"),
    "DE": ("EU", "Germany (XETRA)"), "PA": ("EU", "Paris"), "AS": ("EU", "Amsterdam"),
    "MC": ("EU", "Madrid"), "MI": ("EU", "Milan"), "BR": ("EU", "Brussels"),
    "LS": ("EU", "Lisbon"), "IR": ("EU", "Dublin"), "VI": ("EU", "Vienna"),
    "HE": ("EU", "Helsinki"), "ST": ("EU", "Stockholm"), "CO": ("EU", "Copenhagen"),
    "OL": ("EU", "Oslo"), "WA": ("EU", "Warsaw"), "AT": ("EU", "Athens"),
    "PR": ("EU", "Prague"),
}
TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9\-]*\.(" + "|".join(EXCHANGES) + r")$")

# Words to ignore when comparing company names ("Tesco PLC" = "TESCO PLC").
NAME_ENDINGS = {"PLC", "AG", "SA", "SE", "NV", "N", "V", "AB", "ASA", "AS", "SPA", "S", "P", "A",
                "OYJ", "GROUP", "HOLDINGS", "HOLDING", "THE", "LTD", "LIMITED", "CO", "AND",
                "AKTIENGESELLSCHAFT", "SOCIETE", "EUROPEENNE", "KGAA", "BV", "INC", "CORP",
                "COMPANY", "DE", "ET"}


class EsefUnavailable(RuntimeError):
    """filings.xbrl.org couldn't be reached."""


_session = requests.Session()


def _get(path, attempts=4):
    """Download from filings.xbrl.org. It limits how fast you can ask, so wait
    and try again when told to."""
    url = path if path.startswith("http") else BASE + path
    for attempt in range(attempts):
        try:
            response = _session.get(url, timeout=60)
        except requests.RequestException as error:
            if attempt == attempts - 1:
                raise EsefUnavailable(f"Couldn't reach filings.xbrl.org ({error}).") from None
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code == 429 and attempt < attempts - 1:
            time.sleep(float(response.headers.get("Retry-After") or 0) or 5 * (attempt + 1))
            continue
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise EsefUnavailable(f"filings.xbrl.org refused the request "
                                  f"(status {response.status_code}). Try again in a minute.")
        return response
    raise EsefUnavailable("filings.xbrl.org is busy. Try again in a minute.")


def is_european_ticker(text):
    """True for Yahoo tickers like TSCO.L or SIE.DE."""
    return bool(TICKER_PATTERN.match(text.strip().upper()))


def _plain(name):
    """Upper-case name without accents, punctuation or endings like PLC."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    words = re.findall(r"[A-Z0-9]+", text.replace("&", " "))
    return " ".join(w for w in words if w not in NAME_ENDINGS)


@lru_cache(maxsize=256)
def _yahoo_search(query):
    """Yahoo Finance's search results for a name or ticker (empty if unavailable)."""
    try:
        return yf.Search(query, max_results=10, news_count=0).quotes
    except Exception:
        return []


def prefers_europe(query):
    """
    True if Yahoo's best match for a company name is a share listed in the UK
    or Europe. Used to decide where to look first: typing "next plc" or
    "BT group" should find the UK companies, not a US company whose name
    happens to start the same way. Big companies with a US listing (e.g.
    Shell, Unilever) still go to the SEC.
    """
    equities = [q for q in _yahoo_search(query) if q.get("quoteType") == "EQUITY"]
    if not equities:
        return False
    symbol = equities[0].get("symbol", "")
    return "." in symbol and symbol.rsplit(".", 1)[1] in EXCHANGES


@lru_cache(maxsize=256)
def _yahoo_matches(query):
    """Shares on UK and European exchanges that Yahoo finds for a name or ticker."""
    found = []
    for q in _yahoo_search(query):
        symbol = q.get("symbol", "")
        suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
        if q.get("quoteType") == "EQUITY" and suffix in EXCHANGES:
            found.append((symbol, q.get("longname") or q.get("shortname") or symbol))
    return found


@lru_cache(maxsize=256)
def _entities(word):
    """Filers whose name contains a word."""
    query = '[{"name":"name","op":"ilike","val":"%' + word.replace('"', "") + '%"}]'
    response = _get(f"/api/entities?filter={requests.utils.quote(query)}&page[size]=50")
    if response is None:
        return []
    return [(e["attributes"]["identifier"], e["attributes"]["name"])
            for e in response.json()["data"]]


def _find_filer(company_name):
    """The ESEF filer (its LEI code and name) that best matches a company name."""
    target = _plain(company_name)
    words = [w for w in target.split() if len(w) >= 3] or target.split()
    if not words:
        return None
    best, best_score = None, 0.0
    for lei, name in _entities(words[0]):
        score = SequenceMatcher(None, target, _plain(name)).ratio()
        if score > best_score:
            best, best_score = (lei, name), score
    return best if best_score >= 0.75 else None


@lru_cache(maxsize=64)
def get_filings(lei):
    """The company's annual reports, newest first (one per financial year)."""
    response = _get(f"/api/entities/{lei}/filings?page[size]=100")
    if response is None:
        return []
    by_year = {}
    for f in response.json()["data"]:
        a = f["attributes"]
        if not a.get("json_url"):
            continue
        # A report can be filed more than once (e.g. a corrected version):
        # keep the latest.
        if a["period_end"] not in by_year or a["date_added"] > by_year[a["period_end"]]["date_added"]:
            by_year[a["period_end"]] = a
    return sorted(by_year.values(), key=lambda a: a["period_end"], reverse=True)


def resolve(query):
    """
    Work out which UK or European company the user means. Returns the same
    kind of dictionary as data.sec.resolve_ticker, plus the LEI (the
    company's global identifier) and the region (UK or EU).
    """
    query = query.strip()
    matches = _yahoo_matches(query.upper() if is_european_ticker(query) else query)
    if is_european_ticker(query):
        matches = [m for m in matches if m[0] == query.upper()] or matches
    if not matches:
        raise CompanyNotSupported(
            f"No company matches '{query}' in the SEC's data (US-listed companies) or in UK "
            "and European annual reports. Try the company's ticker, e.g. AAPL or TSCO.L. "
            "Companies listed only outside the US, UK and EU (e.g. in Switzerland, like "
            "Nestlé, or in Asia) aren't covered.")
    # Prefer a share whose name is exactly what was typed ("siemens" ->
    # Siemens AG, not Siemens Energy); otherwise Yahoo's order.
    wanted = _plain(query)
    matches.sort(key=lambda m: _plain(m[1]) != wanted)
    for ticker, name in matches[:4]:
        filer = _find_filer(name)
        if filer and get_filings(filer[0]):
            others = [(t, n) for t, n in matches if t != ticker][:5]
            suffix = ticker.rsplit(".", 1)[1]
            return {"source": "esef", "ticker": ticker, "cik": None, "lei": filer[0],
                    "name": name,
                    "matched_name": not is_european_ticker(query), "alternatives": others,
                    "region": EXCHANGES[suffix][0], "exchange": EXCHANGES[suffix][1]}
    ticker, name = matches[0]
    raise CompanyNotSupported(
        f"Found {name} ({ticker}), listed in {EXCHANGES[ticker.rsplit('.', 1)[1]][1]}, but its "
        "annual reports aren't in the filings.xbrl.org database, which this tool uses for UK "
        "and European companies. Coverage is good for UK and French companies but patchy "
        "elsewhere: German companies, for example, are missing.")


def _day_before(timestamp):
    """xBRL-JSON period ends are the start of the next day: 2026-03-01T00:00
    means the end of 28 February."""
    return (date.fromisoformat(timestamp[:10]) - timedelta(days=1)).isoformat()


@lru_cache(maxsize=32)
def _report_facts(json_url):
    response = _get(json_url)
    return response.json()["facts"] if response is not None else {}


# Companies may label some lines with their own names instead of the standard
# IFRS ones (e.g. Tesco's "PurchaseOfPropertyPlantAndEquipmentAndInvestment
# Property..."). If a standard name is missing, a company-specific line
# matching the pattern is used instead, filed under the standard name.
FALLBACKS = [
    # (standard names that would be used, pattern for company-specific names)
    # Operating profit, e.g. LVMH "ProfitLossFromOperatingActivitiesAfterShare...",
    # Airbus "ProfitLossBeforeFinancialResultAndIncomeTaxes" (EBIT).
    (["ProfitLossFromOperatingActivities"],
     r"^(ProfitLossFromOperatingActivities|ProfitLossBeforeFinancialResult|OperatingProfit"
     r"|ProfitFromOperations|OperatingIncome$|EBIT$)"),
    (["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
      "PurchaseOfPropertyPlantAndEquipment"],
     r"^PurchaseOfPropertyPlantAndEquipment"),
    (["LongtermBorrowings", "NoncurrentPortionOfNoncurrentBorrowings"],
     r"^(Noncurrent\w*Borrowings|LongtermBorrowings)"),
    (["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
      "CurrentPortionOfLongtermBorrowings", "ShorttermBorrowings"],
     r"^Current\w*Borrowings"),
    (["PaymentsToAcquireOrRedeemEntitysShares"],
     r"^((Purchase|Repurchase|Buyback|OwnSharesPurchased)\w*(Cancellation|Buyback|Repurchase)"
     r"|ShareBuyback|SharesBoughtBack)"),
]
# Cash paid out is sometimes tagged as a negative number (e.g. in the statement
# of changes in equity); these items are always shown as positive amounts.
ALWAYS_POSITIVE = {"PaymentsToAcquireOrRedeemEntitysShares"}


def get_company_facts(lei):
    """
    Every IFRS figure from the company's recent annual reports, in the same
    layout as the SEC's company facts, so the SEC code can use it unchanged.
    Each report also shows the year before, so every other report is enough
    for six years of figures.
    """
    filings = get_filings(lei)[:5:2]
    facts, own = {}, {}
    for filing in filings:
        filed = filing["date_added"][:10]
        for fact in _report_facts(filing["json_url"]).values():
            d = fact.get("dimensions", {})
            concept, unit, period = d.get("concept", ""), d.get("unit"), d.get("period")
            # Only whole-company figures (no breakdowns by segment etc.).
            if (":" not in concept or not unit or not period
                    or set(d) - {"concept", "entity", "period", "unit"}):
                continue
            standard = concept.startswith("ifrs-full:")
            try:
                value = float(fact["value"])
            except (TypeError, ValueError):
                continue
            entry = {"val": value, "filed": filed, "form": "ESEF"}
            if "/" in period:
                start, end = period.split("/")
                entry.update(start=start[:10], end=_day_before(end))
            else:
                entry["end"] = _day_before(period)
            # "iso4217:GBP" -> "GBP", "xbrli:shares" -> "shares",
            # "iso4217:GBP/xbrli:shares" (per share) -> "GBP/shares".
            unit_name = "/".join(part.split(":")[-1] for part in unit.split("/"))
            tag = concept.split(":", 1)[1]
            (facts if standard else own).setdefault(tag, {"units": {}})["units"] \
                .setdefault(unit_name, []).append(entry)

    def latest(tag):
        return max((e["end"] for entries in tag["units"].values() for e in entries), default="")

    newest = max((latest(t) for t in facts.values()), default="")
    for names, pattern in FALLBACKS:
        present = [facts[name] for name in names if name in facts]
        # Use the standard name if it covers the latest year; a company may
        # have switched to its own name later on (e.g. LVMH).
        if any(latest(tag) >= newest for tag in present):
            continue
        # The company's own line with the newest figures, preferring statutory
        # figures to "recurring", "adjusted" or "underlying" ones, then the
        # simplest (shortest) name.
        found = sorted((t for t in own if re.match(pattern, t)),
                       key=lambda t: (latest(own[t]),
                                      not re.search(r"Recurring|Adjusted|Underlying", t),
                                      -len(t)),
                       reverse=True)
        if not found:
            continue
        extra = own[found[0]]
        if names[0] in ALWAYS_POSITIVE:
            extra = {"units": {u: [{**e, "val": abs(e["val"])} for e in entries]
                               for u, entries in extra["units"].items()}}
        # Add the company's own figures for any years the standard name lacks.
        target = facts.setdefault(names[0], {"units": {}})
        for unit, entries in extra["units"].items():
            have = {(e.get("start"), e["end"]) for e in target["units"].get(unit, [])}
            target["units"].setdefault(unit, []).extend(
                e for e in entries if (e.get("start"), e["end"]) not in have)
    return {"ifrs-full": facts}


@lru_cache(maxsize=256)
def get_industry(ticker):
    """Yahoo's industry and sector for a share, e.g. ('Grocery Stores',
    'Consumer Defensive'). (None, None) if unavailable."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None, None
    return info.get("industry"), info.get("sector")


def get_financials(query):
    """Like data.sec.get_financials, for a UK or European company. Raises
    CompanyNotSupported, with the reason, if it can't be found."""
    company = resolve(query)
    data = financials_from_facts(company, get_company_facts(company["lei"]))
    data["filings"] = get_filings(company["lei"])
    return data


def get_annual_report_text(lei, max_chars=None):
    """The text of the latest annual report, for the AI to read."""
    filings = get_filings(lei)
    if not filings or not filings[0].get("report_url"):
        return None
    latest = filings[0]
    response = _get(latest["report_url"])
    if response is None:
        return None
    soup = BeautifulSoup(response.content, "lxml")
    for tag in soup(["script", "style", "ix:header"]):
        tag.decompose()
    # Many reports are converted from PDF, with words split across pieces of
    # text. Join the pieces directly, and start a new line only after
    # paragraph-like elements, so words stay whole ("improved", not "imp roved").
    for tag in soup.find_all(["div", "p", "tr", "li", "br", "h1", "h2", "h3", "h4", "table"]):
        tag.append(NavigableString("\n"))
    for tag in soup.find_all(["td", "th"]):
        tag.append(NavigableString(" | "))
    text = re.sub(r"[ \t\xa0]+", " ", soup.get_text(""))
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    title = f"Annual report for the year to {latest['period_end']} (ESEF)"
    if max_chars and len(text) > max_chars:
        title += (f" (TRUNCATED: only the first {max_chars:,} of {len(text):,} characters "
                  "are included)")
        text = text[:max_chars]
    return {"title": title, "text": text}
