"""
Downloads the text of a company's filings from the SEC, so the AI can read
what management actually said.

- Earnings press releases: filed as an attachment ("exhibit 99.1") to an 8-K
  under item 2.02, "Results of Operations".
- The latest quarterly (10-Q) or annual (10-K) report, which contains the
  management discussion, risk factors and notes to the accounts.

It also scans the company's filing history for warning signs that are
recorded as specific filing types rather than as numbers.
"""

import re
import time
from datetime import date, timedelta
from functools import lru_cache

import requests
from bs4 import BeautifulSoup

from config import SEC_USER_AGENT
from data.sec import sec_json

HEADERS = {"User-Agent": SEC_USER_AGENT}
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"


@lru_cache(maxsize=32)
def _submissions(cik):
    """The SEC's record of the company: its industry and its filing history."""
    return sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")


def get_filing_list(cik):
    """Every recent filing the company has made, newest first."""
    recent = _submissions(cik)["filings"]["recent"]
    keys = ["form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "items",
            "primaryDocDescription"]
    return [dict(zip(keys, row)) for row in zip(*(recent[k] for k in keys))]


def get_industry(cik):
    """The SEC's industry code (SIC) and description, e.g. (6021, 'National Commercial Banks')."""
    info = _submissions(cik)
    code = int(info["sic"]) if info.get("sic") else None
    return code, info.get("sicDescription") or ""


def _folder(cik, filing):
    return f"{ARCHIVE}/{cik}/{filing['accessionNumber'].replace('-', '')}"


def _html_to_text(html):
    """Strip out the web page formatting and hidden data, keeping the readable text."""
    soup = BeautifulSoup(html, "html.parser")
    for hidden in soup.find_all(["ix:header", "script", "style"]):
        hidden.decompose()
    text = soup.get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def _download_text(url):
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return _html_to_text(response.text)


def _press_release_url(cik, filing):
    """Find the press release attachment (exhibit 99.1) inside an 8-K."""
    folder = _folder(cik, filing)
    files = sec_json(f"{folder}/index.json")
    names = [f["name"] for f in files["directory"]["item"]
             if f["name"].lower().endswith((".htm", ".html"))]
    exhibits = [n for n in names if re.search(r"ex-?_?99", n.lower())]
    # Prefer exhibit 99.1 if a filing has several exhibits.
    exhibits.sort(key=lambda n: 0 if re.search(r"99-?_?\.?0?1", n.lower()) else 1)
    return f"{folder}/{exhibits[0]}" if exhibits else None


def get_earnings_releases(cik, filings, count=2):
    """The latest few earnings press releases, newest first."""
    releases = []
    for filing in filings:
        if filing["form"] == "8-K" and "2.02" in filing["items"]:
            url = _press_release_url(cik, filing)
            if url:
                releases.append({"title": f"Earnings press release filed {filing['filingDate']}",
                                 "text": _download_text(url)})
        if len(releases) == count:
            break
    return releases


def get_latest_report(cik, filings, max_chars=None):
    """
    The latest periodic report: 10-Q or 10-K for US companies, 20-F or 40-F
    (annual report) for foreign ones. Foreign annual reports can run to two
    million characters, so `max_chars` caps the length; the title then says
    how much was kept, so the AI and the reader know.
    """
    for filing in filings:
        if filing["form"] in ("10-Q", "10-K", "20-F", "40-F"):
            url = f"{_folder(cik, filing)}/{filing['primaryDocument']}"
            text = _download_text(url)
            title = f"{filing['form']} for period ending {filing['reportDate']}"
            if max_chars and len(text) > max_chars:
                title += (f" (TRUNCATED: first {max_chars:,} of {len(text):,} characters; "
                          "later sections were not provided)")
                text = text[:max_chars]
            return {"title": title, "text": text}
    return None


RESULTS_WORDS = re.compile(r"\b(results|half[- ]year|interim|trading statement|earnings|"
                           r"financial highlights|[1-4]Q\s?\d{2,4}|Q[1-4]\s?\d{2,4})\b",
                           re.IGNORECASE)
# Announcements that mention "results" but aren't company results.
NOT_RESULTS = re.compile(r"\b(tender offers?|redemption|general meeting|AGM|voting|"
                         r"exchange offer|consent solicitation)\b", re.IGNORECASE)


def get_foreign_results(cik, filings, count=2, days=200, max_checked=30):
    """
    Foreign companies announce results in 6-K filings. Big companies file
    dozens of 6-Ks for other things (share buybacks, director dealings), often
    labelled just "6-K". So: look at the last ~200 days, check filings whose
    description mentions results first, and only open documents big enough to
    be a results release. Keep those with results wording near the start.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    candidates = [f for f in filings if f["form"] == "6-K" and f["filingDate"] >= since]
    candidates.sort(key=lambda f: not RESULTS_WORDS.search(f.get("primaryDocDescription") or ""))

    releases, checked = [], 0
    for filing in candidates[:max_checked]:
        folder = _folder(cik, filing)
        time.sleep(0.12)  # stay under the SEC's limit of 10 requests a second
        try:
            files = requests.get(f"{folder}/index.json", headers=HEADERS, timeout=30).json()
        except ValueError:
            continue
        # The largest web documents in the filing, if big enough (results
        # releases are long; buyback notices are short).
        docs = sorted((f for f in files["directory"]["item"]
                       if f["name"].lower().endswith((".htm", ".html"))
                       and int(f.get("size") or 0) > 40_000),
                      key=lambda f: -int(f["size"]))
        for doc in docs[:2]:
            time.sleep(0.12)
            text = _download_text(f"{folder}/{doc['name']}")
            if RESULTS_WORDS.search(text[:5000]) and not NOT_RESULTS.search(text[:1500]):
                releases.append({"title": f"Results announcement (6-K) filed {filing['filingDate']}",
                                 "text": text, "date": filing["filingDate"]})
                break
        if len(releases) == count:
            break
    # Newest first, as with US earnings releases.
    releases.sort(key=lambda r: r.pop("date"), reverse=True)
    return releases


# Filing types that are warning signs in themselves.
WARNING_FILINGS = {
    "4.01": "Change of auditor",
    "4.02": "Previous financial statements can no longer be relied on (restatement)",
    "NT 10-K": "Annual report filed late",
    "NT 10-Q": "Quarterly report filed late",
}


def filing_history_flags(filings, years=3):
    """Look for auditor changes, restatements and late filings in recent years."""
    since = (date.today() - timedelta(days=365 * years)).isoformat()
    found = []
    for filing in filings:
        if filing["filingDate"] < since:
            break
        for code, meaning in WARNING_FILINGS.items():
            if code == filing["form"] or (filing["form"] == "8-K" and code in filing["items"]):
                found.append(f"{filing['filingDate']}: {meaning}")
    return found
