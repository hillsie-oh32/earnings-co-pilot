"""
Directors' share dealings for companies listed in Paris.

French directors and senior managers (and people closely associated with
them) must declare their dealings to the AMF, France's markets regulator,
which publishes each one in its BDIF database as a "Déclaration des
dirigeants". The declarations are PDFs generated from the AMF's own online
form, so every one has the same layout, in French and English. We read the
English part.

The AMF allows its content to be reused for personal, educational or
professional (non-commercial) purposes, with the source, its date and the
notice "©Autorité des marchés financiers – droits réservés", which the
website shows under the table.
"""

import io
import re
import time
from datetime import date, timedelta
from difflib import SequenceMatcher
from functools import lru_cache

import pandas as pd
import requests
from pypdf import PdfReader

API = "https://bdif.amf-france.org/back/api/v1/"
NOTICE = "©Autorité des marchés financiers – droits réservés"

_session = requests.Session()

# Kinds of dealing, checked in this order (same code letters as the US and
# UK versions: only P and S count as open-market buys and sales).
KINDS = [
    ("M", "Option exercise", r"exercis|exercice"),
    ("A", "Award / vesting", r"free share|gratuite|attribution|grant|vest|performance|livraison|"
                             r"delivery"),
    ("J", "Dividend reinvestment", r"dividend|r[ée]investissement"),
    ("J", "Subscription", r"subscri|souscri"),
    ("G", "Gift / transfer", r"gift|donation|don\b|transfer|apport|succession|inherit"),
    ("J", "Pledge / loan", r"pledge|nantissement|lend|loan|pr[eê]t"),
    ("S", "Sell", r"disposal|cession|\bsale\b|\bsell|vente"),
    ("P", "Buy", r"acquisition|purchase|achat"),
]


def _plain(name):
    import unicodedata
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    words = re.findall(r"[A-Z0-9]+", text)
    ignore = {"SA", "SE", "SCA", "SAS", "GROUP", "GROUPE", "THE", "ET", "DE", "LA", "LE", "LES",
              "SOCIETE", "EUROPEENNE", "COMPAGNIE", "CIE"}
    return " ".join(w for w in words if w not in ignore)


def _search(company_name, since, max_items=200):
    """The company's declarations since a date, newest first. The search is by
    text, so results are kept only if they're filed under this company."""
    target = _plain(company_name)
    word = next((w for w in target.split() if len(w) >= 3), target.split()[0])
    found, start = [], 0
    while start < max_items:
        response = _session.get(API + "informations", timeout=30, params={
            "TypesInformation": "DD", "RechercheTexte": word, "From": start, "Size": 50})
        response.raise_for_status()
        items = response.json().get("result", [])
        if not items:
            break
        for item in items:
            if item.get("datePublication", "")[:10] < since:
                return found
            names = [s["raisonSociale"] for s in item.get("societes", [])]
            if any(SequenceMatcher(None, target, _plain(n)).ratio() >= 0.6
                   or _plain(n).startswith(word) for n in names):
                found.append(item)
        start += 50
        time.sleep(0.3)
    return found


@lru_cache(maxsize=512)
def _pdf_text(path):
    for attempt in range(3):
        try:
            response = _session.get(API + "documents/" + path, timeout=30)
            response.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(response.content)).pages)


def _field(text, label):
    """The value after 'LABEL :' on the same line."""
    match = re.search(label + r"\s*:\s*(.*)", text)
    return match.group(1).strip() if match else ""


def _number(text):
    """'50 000.0000' -> 50000.0 (French forms separate thousands with spaces)."""
    match = re.search(r"-?\d[\d   ]*(?:[.,]\d+)?", text or "")
    if not match:
        return None
    return float(re.sub(r"[   ]", "", match.group()).replace(",", "."))


MONTHS = {"janvier": "January", "février": "February", "fevrier": "February", "mars": "March",
          "avril": "April", "mai": "May", "juin": "June", "juillet": "July", "août": "August",
          "aout": "August", "septembre": "September", "octobre": "October",
          "novembre": "November", "décembre": "December", "decembre": "December"}


def _date(text, fallback):
    """'13 November 2026' or '22 juin 2026'."""
    for french, english in MONTHS.items():
        text = re.sub(rf"\b{french}\b", english, text, flags=re.I)
    when = pd.to_datetime(text, errors="coerce", dayfirst=True)
    return pd.Timestamp(when.date()) if pd.notna(when) else pd.Timestamp(fallback[:10])


def _tidy(text):
    """Names written in capitals ('BERNARD ARNAULT') in normal case."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        return text.title()
    return text


# The form's labels in English and in French (some declarations are only in French).
LABELS = {
    "en": {"who": r"PERSON CLOSELY ASSOCIATED\s*:\s*(.+?)\s*INITIAL NOTIFICATION",
           "date": "DATE OF THE TRANSACTION", "nature": "NATURE OF THE TRANSACTION",
           "instrument": r"DESCRIPTION OF THE FINANCIAL INSTRUMENT, TYPE OF INSTRUMENT",
           "aggregated": "AGGREGATED INFORMATION", "price": r"\bPRICE",
           "options": r"SHARE OPTION PROGRAMMES.*?:\s*(YES|NO)"},
    "fr": {"who": r"PERSONNE ETROITEMENT LIEE\s*:\s*(.+?)\s*NOTIFICATION INITIALE",
           "date": "DATE DE LA TRANSACTION", "nature": "NATURE DE LA TRANSACTION",
           "instrument": r"DESCRIPTION DE L.INSTRUMENT FINANCIER",
           "aggregated": "INFORMATIONS AGREGEES", "price": r"\bPRIX",
           "options": r"PROGRAMMES D.OPTIONS.*?:\s*(OUI|NON)"},
}


def _parse(text, published):
    """The dealings in one declaration (its English part if there is one)."""
    if "Individual notification" in text:
        part, L = text[text.find("Individual notification"):], LABELS["en"]
    else:
        part, L = text, LABELS["fr"]
    who = re.search(L["who"], part, re.S)
    who = re.sub(r"\s+", " ", who.group(1)).strip() if who else ""
    # 'Name, Position' or 'X SAS legal person closely associated with Name, Position'.
    insider, role = who.rsplit(",", 1) if "," in who else (who, "")
    held = ("Person closely associated" if re.search(r"closely associated|li[ée]e", who, re.I)
            else "")
    options = re.search(L["options"], part, re.S)
    option_linked = bool(options and options.group(1) in ("YES", "OUI"))

    trades = []
    # A declaration can contain several transactions: each starts with its date.
    for block in re.split(rf"(?={L['date']}\s*:)", part)[1:]:
        nature = _field(block, L["nature"])
        instrument = _field(block, L["instrument"])
        aggregated = block.split(L["aggregated"], 1)[-1]
        price_text = _field(aggregated, L["price"])
        price, volume = _number(price_text), _number(_field(aggregated, r"\bVOLUME"))
        currency = "EUR" if re.search(r"Euro", price_text, re.I) else price_text.split()[-1:]
        code, label = "J", "Other"
        for c, l, pattern in KINDS:
            if re.search(pattern, nature, re.I):
                code, label = c, l
                break
        if option_linked and code == "P":
            code, label = "M", "Option exercise / free shares"
        # Only the company's shares count: not employee funds (FCP), options, bonds etc.
        is_share = (re.match(r"\s*(shares?|actions?)\b", instrument, re.I)
                    and not re.search(r"FCP|fonds|fund|unit|unit[ée]|option|warrant|bon|"
                                      r"obligation|bond|droit|right", instrument, re.I))
        if instrument and not is_share:
            code, label = "J", "Other instrument"
        # Free or performance shares, or anything received at a price of
        # zero, are awards, even when the form calls them an "acquisition".
        if code == "P" and (price == 0 or re.search(r"gratuit|performance|free", instrument, re.I)):
            code, label = "A", "Award / vesting"
        trades.append({
            "Date": _date(_field(block, L["date"]), published),
            "Insider": _tidy(insider.strip()), "Role": role.strip(), "Type": label, "Code": code,
            "Shares": volume, "Price": price,
            "Value": volume * price if volume and price else None,
            "Planned (10b5-1)": None, "Shares owned after": None, "Held": held,
            "_source": f"{nature} || {instrument} || {currency} || {option_linked}",
        })
    return trades


def get_insider_trades(company_name, months=12, max_notices=80, on_progress=None):
    """
    Directors' dealings declared to the AMF over the last `months` months.
    Returns a table in the same format as data/insiders.py, and the number of
    declarations that couldn't be read.
    """
    since = (date.today() - timedelta(days=31 * months)).isoformat()
    items = _search(company_name, since)[:max_notices]
    trades, failed = [], 0
    for i, item in enumerate(items):
        if on_progress:
            on_progress(i, len(items))
        try:
            path = item["documents"][0]["path"]
            found = _parse(_pdf_text(path), item.get("datePublication", ""))
        except Exception:
            failed += 1
            continue
        if not found:
            failed += 1
        trades += found
        time.sleep(0.2)  # be gentle with the AMF's servers
    table = pd.DataFrame(trades)
    if not table.empty:
        table = (table.drop_duplicates(["Date", "Insider", "Code", "Shares", "Price"], keep="first")
                 .sort_values("Date", ascending=False).reset_index(drop=True))
    return table, failed
