"""
Suggests peer companies to compare against.

Uses data/industries.json (built by build_industry_list.py): the official SEC
industry code of the ~1,500 largest US-listed companies. Peers are companies in
the same industry, of similar size, and preferably also US (or also foreign)
companies, since those report on the same basis.
"""

import json
from functools import lru_cache
from pathlib import Path

INDUSTRIES = Path(__file__).parent / "industries.json"


@lru_cache(maxsize=1)
def all_companies():
    """The companies in the industry list, largest first."""
    if not INDUSTRIES.exists():
        return []
    return json.loads(INDUSTRIES.read_text(encoding="utf-8"))


def _family(sic):
    """Commercial and savings banks (6020-6036) count as one industry."""
    return "bank" if 6020 <= sic <= 6036 else sic


def suggest_peers(cik, sic, foreign, count=5):
    """
    Up to `count` peers as (ticker, name) pairs. Starts with the exact
    industry code and widens to related industries if there are too few.
    """
    companies = all_companies()
    if not companies or not sic:
        return []
    me = next((c for c in companies if c["cik"] == cik), None)
    rank = me["size_rank"] if me else len(companies)  # not in the list: treat as small
    others = [c for c in companies if c["cik"] != cik]

    # Same industry; then the same industry group (first 3 digits); then
    # the same major group (first 2 digits).
    for same_industry in (lambda c: _family(c["sic"]) == _family(sic),
                          lambda c: c["sic"] // 10 == sic // 10,
                          lambda c: c["sic"] // 100 == sic // 100):
        candidates = [c for c in others if same_industry(c)]
        if len(candidates) >= 3:
            break

    # Prefer companies that report the same way (US vs foreign), then the
    # closest in size.
    candidates.sort(key=lambda c: (c["foreign"] != foreign, abs(c["size_rank"] - rank)))
    return [(c["ticker"], c["name"]) for c in candidates[:count]]
