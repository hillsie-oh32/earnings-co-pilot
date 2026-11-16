"""
Builds data/industries.json: the official SEC industry code (SIC) of the
largest ~1,500 US-listed companies, used to suggest peers automatically.

The SEC only gives one company's industry at a time, so this collects them
all once (it takes a few minutes) and saves the result. Re-run it now and then
to pick up new listings:   python build_industry_list.py
"""

import json
import time
from pathlib import Path

from data.sec import _company_list
from data.filings import _submissions

HOW_MANY = 1500
OUTPUT = Path(__file__).parent / "data" / "industries.json"
FOREIGN_FORMS = {"20-F", "40-F", "6-K"}

companies, seen = [], set()
for company in _company_list():  # roughly largest first
    if company["cik_str"] in seen:
        continue  # one entry per company, not per share class
    seen.add(company["cik_str"])
    companies.append(company)
    if len(companies) == HOW_MANY:
        break

results = []
for i, company in enumerate(companies, 1):
    time.sleep(0.13)  # stay under the SEC's limit of 10 requests a second
    try:
        info = _submissions(company["cik_str"])
    except Exception as error:
        print(f"  skipped {company['ticker']}: {error}")
        continue
    if not info or not info.get("sic"):
        continue
    forms = set(info["filings"]["recent"]["form"][:100])
    results.append({
        "cik": company["cik_str"], "ticker": company["ticker"], "name": company["title"],
        "sic": int(info["sic"]), "industry": info.get("sicDescription") or "",
        "foreign": bool(forms & FOREIGN_FORMS) and "10-K" not in forms,
        "size_rank": i,  # 1 = largest
    })
    if i % 100 == 0:
        print(f"{i} of {len(companies)} done")

OUTPUT.write_text(json.dumps(results, indent=0), encoding="utf-8")
print(f"Saved {len(results)} companies to {OUTPUT}")
