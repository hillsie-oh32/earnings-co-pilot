"""
Directors' share dealings for UK-listed companies.

Under the UK Market Abuse Regulation (Article 19), directors and other
"persons discharging managerial responsibilities" (PDMRs), and people closely
associated with them, must report every dealing in the company's shares. The
company publishes each one as a "Director/PDMR Shareholding" announcement,
and the FCA keeps them all in its National Storage Mechanism (NSM), the
official archive of company announcements.

Unlike the SEC's Form 4, these are documents rather than data. But they
follow a standard template set out in the regulation (name, position,
nature of the transaction, price and volume, date, place), laid out as a
table, so the figures can be read reliably from it.

The FCA's terms allow the material to be used for any lawful purpose; they
ask that sites link only to the NSM home page, so we credit it as the source
without linking to individual notices.
"""

import re
import time
from datetime import date, timedelta
from functools import lru_cache

import pandas as pd
import requests
from bs4 import BeautifulSoup

SEARCH = "https://api.data.fca.org.uk/search?index=nsm-search"
DOCUMENTS = "https://data.fca.org.uk/artefacts/"

_session = requests.Session()

# How each kind of dealing is labelled, checked in this order. Code letters
# match the US Form 4 codes used by data/insiders.py, so the same summary
# works for both: only P (open-market buy) and S (open-market sale) count
# as buys and sales; everything else is routine pay or admin.
KINDS = [
    # A sale to pay the tax due when shares vest.
    ("F", "Sale to cover tax",
     r"(cover|satisfy|settle|meet|pay|discharge)\w*\b.{0,60}\b(tax|nics?\b|national insurance|"
     r"liabilit)|sell[- ]to[- ]cover|withh(e|o)ld"),
    ("J", "Dividend reinvestment", r"dividends?.{0,40}re-?invest|re-?invest\w*.{0,40}dividend|"
                                   r"\bdrip\b|scrip"),
    ("M", "Option exercise", r"exercis"),
    ("J", "Share plan purchase",
     r"share incentive plan|\bsip\b|partnership share|matching share|free share|sharesave|"
     r"save as you earn|\bsaye\b|employee share|share purchase (plan|scheme)|\bespp\b|"
     r"share ownership plan|all[- ]employee|share investment plan|matching plan|"
     r"investment shares"),
    ("A", "Award / vesting",
     r"\bvest|award|grant|release of|deferred|ltip|long[- ]term incentive|performance share|"
     r"restricted share|bonus|conditional|nil[- ]cost|allot|dividend equivalent|"
     r"under the .{0,40}(share|incentive|award|bonus|equity) plan"),
    ("G", "Gift / transfer", r"\bgift|transfer|spouse|into (an )?isa|bed and"),
    ("S", "Sell", r"\b(sale|sell|sold|dispos)"),
    ("P", "Buy", r"\b(purchas|buy|bought|acqui)"),
]
PLANNED = r"trading plan|dealing plan|pre-?arranged|10b5|standing instruction|discretionary manag"


def _search(lei, since):
    """The company's Director/PDMR Shareholding notices since a date, newest first."""
    body = {"from": 0, "size": 100,
            "primarySearch": [{"name": "lei", "value": lei},
                              {"name": "headline", "value": [["match_all_words", "PDMR"]]}],
            "latestFlag": "Y",
            "criteriaObj": {"criteria": [], "dateCriteria": [
                {"name": "publication_date",
                 "value": {"from": f"{since}T00:00:00Z", "to": None}}]},
            "sort": "submitted_date", "sortorder": "desc"}
    response = _session.post(SEARCH, json=body, timeout=30)
    response.raise_for_status()
    hits = response.json()["hits"]["hits"]
    # Keep the dealing notices (type code DSH), not e.g. holdings in other companies.
    return [h["_source"] for h in hits if h["_source"].get("type_code") in ("DSH", "", None)]


@lru_cache(maxsize=512)
def _download(link):
    for attempt in range(3):
        try:
            response = _session.get(DOCUMENTS + link, timeout=30)
            response.raise_for_status()
            return response.content
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))


def _clean(text):
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


NUMBER = r"\d[\d,]*(?:\.\d+)?"
# An amount of money: '£5.40', 'GBP 5.40', 'GBP00.9588', '€12.30', '540.5p', '540.5 pence'.
MONEY = (rf"(?:£|€|\$|\bGBP|\bEUR|\bUSD|\bGBX)\s*{NUMBER}"
         rf"|{NUMBER}\s*(?:p\b|pence\b|GBX\b|GBp\b)")


def _tidy_numbers(text):
    """Fix stray spaces inside numbers, e.g. '£7 .29' -> '£7.29'."""
    return re.sub(r"(\d)\s+\.(\d)", r"\1.\2", text)


def _amount(token, pence_currency):
    """A money token in pounds (or euros etc.): pence are divided by 100."""
    value = float(re.search(NUMBER, token).group().replace(",", ""))
    in_pence = re.search(r"p\b|pence|GBX|GBp", token) or (pence_currency and not
                                                       re.search(r"£|GBP|€|EUR|\$", token))
    return value / 100 if in_pence else value


def _prices_and_volumes(text, currency):
    """
    Pairs of (price, volume) from the "Price(s) and volume(s)" part, e.g.
    'Price(s) Volume(s) £14.35 1,383 £14.40 500'. Prices are the amounts of
    money; volumes are the plain numbers. If no price has a currency sign,
    the numbers alternate price, volume.
    """
    pence = bool(re.search(r"GBX|GBp|pence", currency or ""))
    text = _tidy_numbers(re.sub(r"see\s+\S+|\([ivx]+\)|\(\w\)|4\s*\(c\)", " ", text, flags=re.I))
    money = [(m.start(), _amount(m.group(), pence)) for m in re.finditer(MONEY, text)]
    taken = [m.span() for m in re.finditer(MONEY, text)]
    plain = [(m.start(), float(m.group().replace(",", ""))) for m in re.finditer(NUMBER, text)
             if not any(a <= m.start() < b for a, b in taken)]
    if money:
        prices, volumes = [p for _, p in money], [v for _, v in plain]
        # A "Total" column: either the plain numbers alternate volume, total,
        # or the amounts of money alternate price, total.
        if re.search(r"total", text, re.I):
            if len(volumes) == 2 * len(prices):
                volumes = volumes[0::2]
            elif len(prices) == 2 * len(volumes):
                prices = prices[0::2]
    else:
        numbers = [v for _, v in plain]
        prices, volumes = numbers[0::2], numbers[1::2]
        if pence:
            prices = [p / 100 for p in prices]
    if prices and len(prices) == len(volumes):
        return list(zip(prices, volumes))
    return []


def _tidy(text):
    """Names and roles written in capitals ('GREG McCALL') in normal case, so
    the same person is spelt the same way in every notice."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        return text.title().replace("Ceo", "CEO").replace("Cfo", "CFO").replace("Pdmr", "PDMR").replace("Pca", "PCA")
    return text


def _date(text, fallback):
    """The transaction date: '2026-10-23', '23 October 2026' or '23/10/2026'."""
    text = text or ""
    iso = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if iso:
        when = pd.to_datetime(iso.group(), errors="coerce")
    else:
        # '23 October 2026', '23/10/2026' or 'October 30, 2026'.
        written = re.search(r"\d{1,2}(st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}|\d{1,2}/\d{1,2}/\d{4}|"
                            r"[A-Za-z]+\s+\d{1,2},?\s+\d{4}", text)
        when = (pd.to_datetime(written.group().replace("st ", " ").replace("nd ", " ")
                               .replace("rd ", " ").replace("th ", " "),
                               errors="coerce", dayfirst=True) if written else pd.NaT)
    if pd.isna(when):
        when = pd.Timestamp(fallback[:10])
    return pd.Timestamp(when.date())


def _classify(nature):
    text = nature.lower()
    for code, label, pattern in KINDS:
        if re.search(pattern, text):
            return code, label
    return "J", "Other"


def _aggregate(text, currency, reference_price):
    """
    (volume, price) from the "Aggregated information" part, used only when
    the line-by-line prices can't be read. Companies fill it in differently:
    some give volume and price, some volume and total value, so a price is
    only accepted if it's plausible next to today's share price.
    """
    pence = bool(re.search(r"GBX|GBp|pence", currency or ""))
    text = _tidy_numbers(text)
    taken = [m.span() for m in re.finditer(MONEY, text)]
    money = [_amount(m.group(), pence) for m in re.finditer(MONEY, text)]
    plain = [float(m.group().replace(",", "")) for m in re.finditer(NUMBER, text)
             if not any(a <= m.start() < b for a, b in taken)]
    if not plain or not plain[0]:
        return None, None
    volume = plain[0]
    if not money:
        return volume, None

    def plausible(p):
        return reference_price is None or reference_price / 5 <= p <= reference_price * 5

    for amount in sorted(money):
        if plausible(amount):
            return volume, amount
        if plausible(amount / volume):  # it was the total value
            return volume, amount / volume
    return volume, None


# The labels of the regulation's template, and the field each one fills.
LABELS = [("name", r"^(name|name of natural person|full name)$"),
          ("first name", r"^first name"), ("last name", r"^(last name|surname)"),
          ("position", r"^position"),
          ("nature", r"^nature of (the )?trans"), ("currency", r"^currency"),
          ("prices", r"^prices?( ?\(s\))? and volumes?"), ("aggregated", r"^aggregated?\b"),
          ("price", r"^prices?( ?\(s\))?$"), ("volume", r"^volumes?( ?\(s\))?$"),
          ("total", r"^totals?( ?\(s\))?$"),
          ("date", r"^date of (the )?trans"), ("place", r"^place of (the )?trans"),
          ("instrument", r"^description of (the )?financial instrument"),
          ("other", r"^(initial notif|lei|legal entity|identification code|full name of the entity)")]


def _join_letters(text):
    """Mend words split after their first letter by the document's formatting
    ('T ransfer' -> 'Transfer', 'N athan Coe' -> 'Nathan Coe')."""
    return re.sub(r"\b([A-Z]) (?=[a-z]{2,})", r"\1", text)


def _parse(html, published, reference_price=None):
    """The dealings in one notice, read from the regulation's standard table."""
    # Python's own parser: lxml gives up part-way through some of these
    # documents because of untidy HTML, losing the tables.
    soup = BeautifulSoup(html, "html.parser")
    trades, person, role, held, deal = [], None, "", "", {}

    def finish():
        """Turn the transaction collected so far into a row."""
        if not deal.get("nature") or person is None:
            return
        code, label = _classify(deal["nature"])
        currency = deal.get("currency", "")
        # Bonds, options etc. aren't shares (e.g. a director buying the
        # company's new bonds).
        instrument = deal.get("instrument", "")
        if ((instrument and not re.search(r"share|ordinary|stock|\bads\b|\badr|depositary|"
                                          r"equity", instrument, re.I))
                or re.search(r"\bnotes?\b|\bbonds?\b|debenture|warrant", deal["nature"], re.I)):
            code, label = "J", "Other instrument"
        # Dealings in another currency (e.g. US-listed ADRs in dollars, or
        # shares bought in Amsterdam in euros) are listed but left out of the
        # totals, which are in the company's own currency.
        text = f"{currency} {deal.get('prices', '')} {deal.get('aggregated', '')} {instrument}"
        foreign = next((c for c, pattern in (("USD", r"\$|\bUSD\b|\bADS\b|\bADRs?\b|depositary"),
                                             ("EUR", r"€|\bEUR\b"))
                        if re.search(pattern, text, re.I)), None)
        if foreign and not re.search(r"£|\bGBP\b|\bGBX\b|pence", text):
            label += f" ({foreign}, not in totals)"
        pairs = _prices_and_volumes(deal.get("prices", ""), currency)
        agg_volume, agg_price = _aggregate(deal.get("aggregated", ""), currency, reference_price)
        if pairs and sum(v for _, v in pairs):
            volume = sum(v for _, v in pairs)
            price = sum(p * v for p, v in pairs) / volume
            # A typo in the line-by-line volume (e.g. '177.273' for 177,273)
            # shows up against the aggregated figures: trust those if they
            # agree with the price.
            if (len(pairs) == 1 and agg_volume and abs(agg_volume - volume) > 0.01 * agg_volume
                    and agg_price and abs(agg_price - price) <= 0.01 * price):
                volume = agg_volume
        else:
            volume, price = agg_volume, agg_price
        # Some notices quote pence without saying so: a price far above
        # today's share price can only be pence.
        if price and reference_price and price > 30 * reference_price:
            price /= 100
        # If the price still isn't plausible, the notice was laid out in a
        # way we couldn't read reliably: leave it blank rather than guess.
        not_in_totals = "not in totals" in label
        if (price and reference_price and not not_in_totals
                and not reference_price / 5 <= price <= reference_price * 5):
            price = None
        # Shares received for nothing are awards, whatever the notice calls them.
        if code == "P" and price == 0:
            code, label = "A", "Award / vesting"
        if not_in_totals:
            code = "J"  # listed, but not counted as an open-market buy or sale
        trades.append({
            "Date": _date(deal.get("date"), published),
            "Insider": person, "Role": role, "Type": label, "Code": code,
            "Shares": volume, "Price": price,
            "Value": volume * price if volume and price else None,
            "Planned (10b5-1)": (True if code == "S" and re.search(PLANNED, deal["nature"].lower())
                                 else None),
            "Shares owned after": None,
            "Held": held,
            # The notice's own wording, kept for checking (not displayed).
            "_source": f"{deal['nature']} || {deal.get('prices', '')} || "
                       f"{deal.get('aggregated', '')} || {instrument}",
        })

    # Walk through the table rows. A row with a template label starts a field;
    # rows without one (e.g. a nested table of prices) add to the current field.
    def own_text(td):
        """A cell's text, leaving out any table nested inside it (its rows
        come next in the loop and are read on their own)."""
        table = td.find_parent("table")
        return _clean(" ".join(s for s in td.find_all(string=True)
                               if s.find_parent("table") is table))

    section, field, columns = None, None, None
    for tr in soup.find_all("tr"):
        table = tr.find_parent("table")
        raw = [own_text(td) for td in tr.find_all(["td", "th"]) if td.find_parent("table") is table]
        cells = [_join_letters(c) for c in raw if c]
        if not cells:
            continue

        # A section heading: '1', '4.1', '1.', or '1 - Details of ...' in one cell.
        heading = re.match(r"^(\d+(?:\.\d+)?)\.?\s*[-–]?\s*(.*)$", cells[0])
        if heading and (len(cells[0]) <= 4 or re.search(r"details|reason", cells[0], re.I)):
            text = (heading.group(2) + " " + " ".join(cells[1:])).lower()
            section = ("issuer" if "issuer" in text else "trans" if "transaction" in text
                       else "reason" if "reason" in text else "person")
            if section == "trans" and deal.get("nature"):
                finish()
                deal = {}
            field = None
            continue

        # A table with headings in one row and values in the next (e.g. HSBC):
        # remember which column holds the date and the currency.
        lower = [c.lower() for c in raw]
        if sum(bool(re.search(r"^(date of trans|currency|financial instrument|place of)", c))
               for c in lower) >= 2:
            columns = lower
            continue
        if columns and len(raw) == len(columns):
            for heading_text, value in zip(columns, raw):
                if heading_text.startswith("date of trans") and value:
                    deal["date"] = value
                elif heading_text.startswith("currency") and value:
                    deal["currency"] = value
                elif heading_text.startswith("financial instrument") and value:
                    deal["instrument"] = value
            columns = None
            continue
        columns = None

        if re.fullmatch(r"[a-g]\)", cells[0]):
            cells = cells[1:]
        matched = None
        for i, cell in enumerate(cells[:2]):
            label_text = cell.lower().lstrip("- ").rstrip(": ")
            for name, pattern in LABELS:
                if re.search(pattern, label_text):
                    # 'Nature of Transaction: Disposal' keeps its value in the same cell.
                    inline = cell.split(":", 1)[1].strip() if ":" in cell else ""
                    matched, rest = name, " ".join([inline] * bool(inline) + cells[i + 1:])
                    break
            if matched:
                break
        if matched is None:
            if field in ("prices", "aggregated"):
                deal[field] = deal.get(field, "") + " " + " ".join(cells)
            continue

        if matched in ("price", "volume", "total"):
            # Separate Price / Volume / Total rows add to the current figures.
            if field in ("prices", "aggregated"):
                deal[field] = deal.get(field, "") + f" {matched} " + rest
            else:
                field, deal["prices"] = "prices", f" {matched} " + rest
            continue
        field = matched
        if matched in ("name", "first name") and section in ("person", None):
            finish()
            deal = {}
            person, role, held = _tidy(rest), "", ""
        elif matched == "last name" and section in ("person", None):
            person = _tidy(f"{person or ''} {rest}".strip())
        elif matched == "position":
            role = _tidy(rest)
            if re.search(r"\bpca\b|closely associated|spouse|partner of|connected", rest.lower()):
                held = "Person closely associated"
        elif matched == "instrument":
            if deal.get("nature"):
                finish()
                deal = {}
            deal["instrument"] = rest
        elif matched == "nature":
            if deal.get("nature"):
                finish()
                deal = {k: v for k, v in deal.items() if k in ("date", "currency", "instrument")}
            deal["nature"] = rest
            # HSBC-style: the price headings follow in the same row.
            if re.search(r"\bprice", rest, re.I):
                deal["nature"] = re.split(r"\bprice", rest, flags=re.I)[0].strip()
                field, deal["prices"] = "prices", ""
        elif matched in ("currency", "prices", "aggregated", "date"):
            deal[matched] = rest
    finish()
    return trades


def get_insider_trades(lei, months=12, max_notices=60, on_progress=None, reference_price=None):
    """
    Directors' dealings from the FCA archive over the last `months` months.
    Returns a table in the same format as data/insiders.py, and the number of
    notices that couldn't be read.
    """
    since = (date.today() - timedelta(days=31 * months)).isoformat()
    notices = _search(lei, since)[:max_notices]
    trades, failed = [], 0
    for i, notice in enumerate(notices):
        if on_progress:
            on_progress(i, len(notices))
        try:
            found = _parse(_download(notice["download_link"]), notice["publication_date"],
                           reference_price)
        except Exception:
            failed += 1
            continue
        if not found:
            failed += 1
        trades += found
        time.sleep(0.2)  # be gentle with the FCA's servers
    table = pd.DataFrame(trades)
    if not table.empty:
        # The same dealing can appear in a notice and in a later correction.
        table = (table.drop_duplicates(["Date", "Insider", "Code", "Shares", "Price"], keep="first")
                 .sort_values("Date", ascending=False).reset_index(drop=True))
    return table, failed
