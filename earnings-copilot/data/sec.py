"""
Downloads official company financials from the SEC (the US markets regulator).

Every company listed in the US, including foreign companies with US-listed
shares, files its accounts with the SEC in a machine-readable format called
XBRL. The SEC publishes all of it for free, so we never have to type numbers
in by hand, and every figure can be traced back to a real filing.

Two accounting "languages" are supported:
- US GAAP ("us-gaap"): US companies, and many Chinese companies (e.g. Alibaba).
- IFRS ("ifrs-full"): most UK, European and Japanese companies (e.g. BP, Toyota).

US companies file figures every quarter. Foreign companies only file
structured figures once a year (with their annual report, the 20-F), so for
them we show annual figures instead.
"""

import re
from datetime import date, timedelta
from functools import lru_cache

import requests
import pandas as pd

from config import SEC_USER_AGENT, SEC_CONTACT_EMAIL

HEADERS = {"User-Agent": SEC_USER_AGENT}

# Companies don't all use the same label for the same line item. For each
# metric we list the labels ("tags") to try, in each accounting language.

# Income statement and cash flow items: amounts earned or spent OVER a period.
FLOW_TAGS = {
    "us-gaap": {
        "Revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "RevenueFromContractWithCustomerIncludingAssessedTax",
                    "RevenuesNetOfInterestExpense",  # banks
                    "SalesRevenueNet", "SalesRevenueGoodsNet"],
        "Cost of revenue": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
        "Gross profit": ["GrossProfit"],
        "SG&A": ["SellingGeneralAndAdministrativeExpense"],
        "Selling & marketing": ["SellingAndMarketingExpense"],
        "G&A": ["GeneralAndAdministrativeExpense"],
        "Operating income": ["OperatingIncomeLoss"],
        "Net income": ["NetIncomeLoss", "ProfitLoss"],
        "Depreciation": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                         "DepreciationAmortizationAndAccretionNet", "Depreciation"],
        "Operating cash flow": ["NetCashProvidedByUsedInOperatingActivities"],
        "Capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        # Returns to shareholders (cash paid out, from the cash flow statement)
        "Dividends paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
        "Share buybacks": ["PaymentsForRepurchaseOfCommonStock"],
        # Banks
        "Net interest income": ["InterestIncomeExpenseNet"],
        "Fee & other income": ["NoninterestIncome"],
        "Operating costs": ["NoninterestExpense"],
        "Credit loss provisions": ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForCreditLosses",
                                   "ProvisionForLoanAndLeaseLosses"],
        "Interest income after provisions": ["InterestIncomeExpenseAfterProvisionForLoanLoss"],
    },
    "ifrs-full": {
        "Revenue": ["Revenue", "RevenueFromContractsWithCustomers",
                    "RevenueAndOperatingIncome",  # banks: total operating income
                    "RevenueFromSaleOfGoods"],  # e.g. Novartis, Sanofi
        # Insurers under IFRS 17 (e.g. Aviva): used only if there's no revenue line.
        "Insurance revenue": ["InsuranceRevenue"],
        "Cost of revenue": ["CostOfSales"],
        "Gross profit": ["GrossProfit"],
        "SG&A": ["SellingGeneralAndAdministrativeExpense"],
        "Selling & marketing": ["DistributionCosts"],
        "G&A": ["AdministrativeExpense"],
        "Operating income": ["ProfitLossFromOperatingActivities"],
        "Net income": ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
        "Depreciation": ["DepreciationAndAmortisationExpense",
                         "AdjustmentsForDepreciationAndAmortisationExpense"],
        "Operating cash flow": ["CashFlowsFromUsedInOperatingActivities"],
        "Capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                  "PurchaseOfPropertyPlantAndEquipment",
                  "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets"],
        # Returns to shareholders
        "Dividends paid": ["DividendsPaidClassifiedAsFinancingActivities", "DividendsPaid",
                           "DividendsPaidToEquityHoldersOfParentClassifiedAsFinancingActivities"],
        "Share buybacks": ["PaymentsToAcquireOrRedeemEntitysShares"],
        # Banks
        "Net interest income": ["InterestRevenueExpense"],
        # Some banks (e.g. BNP Paribas) report the two sides separately.
        "Interest income": ["RevenueFromInterest", "InterestIncome"],
        "Interest expense": ["InterestExpense"],
        "Fee & other income": ["FeeAndCommissionIncomeExpense"],
        "Fee income": ["FeeAndCommissionIncome"],
        "Fee expense": ["FeeAndCommissionExpense"],
        "Operating costs": ["OperatingExpense"],
        "Credit loss provisions": [
            "AdjustmentsForImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLossLoansAndAdvances",
            "AdjustmentsForImpairmentLossesReversalOfImpairmentLossesRecognisedInProfitOrLossFinancialAssets",
            "ImpairmentLossOnFinancialAssets",
            "ImpairmentLossImpairmentGainAndReversalOfImpairmentLossDeterminedInAccordanceWithIFRS9",
            # e.g. Lloyds: a bank's impairment charge is almost all loan losses.
            "ImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"],
    },
}

# Balance sheet items: amounts held AT a point in time (the period end).
BALANCE_TAGS = {
    "us-gaap": {
        "Cash": ["CashAndCashEquivalentsAtCarryingValue"],
        "Short-term investments": ["MarketableSecuritiesCurrent", "ShortTermInvestments",
                                   "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                                   "DebtSecuritiesCurrent"],
        "Receivables": ["AccountsReceivableNetCurrent"],
        "Inventory": ["InventoryNet"],
        "Current assets": ["AssetsCurrent"],
        "Non-current assets": ["AssetsNoncurrent"],
        "PP&E": ["PropertyPlantAndEquipmentNet",
                 "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
        "Goodwill": ["Goodwill"],
        "Total assets": ["Assets"],
        "Current liabilities": ["LiabilitiesCurrent"],
        "Long-term debt": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
        "Current debt": ["LongTermDebtCurrent", "DebtCurrent",
                         "LongTermDebtAndCapitalLeaseObligationsCurrent"],
        "Commercial paper": ["CommercialPaper", "ShortTermBorrowings"],
        "Equity": ["StockholdersEquity"],
        # Banks
        "Loans": ["FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
                  "LoansAndLeasesReceivableNetReportedAmountCoveredAndNotCovered",
                  "LoansAndLeasesReceivableNetReportedAmountCovered",
                  "LoansAndLeasesReceivableNetReportedAmount"],
        "Deposits": ["Deposits"],
    },
    "ifrs-full": {
        "Cash": ["CashAndCashEquivalents"],
        "Short-term investments": ["CurrentInvestments"],
        "Receivables": ["TradeAndOtherCurrentReceivables", "CurrentTradeReceivables"],
        "Inventory": ["Inventories"],
        "Current assets": ["CurrentAssets"],
        "Non-current assets": ["NoncurrentAssets"],
        "PP&E": ["PropertyPlantAndEquipment"],
        "Goodwill": ["Goodwill"],
        "Total assets": ["Assets"],
        "Current liabilities": ["CurrentLiabilities"],
        "Long-term debt": ["LongtermBorrowings", "NoncurrentPortionOfNoncurrentBorrowings"],
        "Current debt": ["CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
                         "CurrentPortionOfLongtermBorrowings"],
        "Commercial paper": ["ShorttermBorrowings"],
        "Equity": ["EquityAttributableToOwnersOfParent", "Equity"],
        # Banks
        "Loans": ["LoansAndAdvancesToCustomers", "LoansAndAdvancesToCustomersAtAmortisedCost"],
        "Deposits": ["DepositsFromCustomers", "DepositsFromCustomersAtAmortisedCost"],
    },
}

# Common company-name endings to ignore when matching a typed name.
NAME_ENDINGS = r"\b(INC|CORP|CORPORATION|CO|COMPANY|PLC|LTD|LIMITED|GROUP|HOLDINGS?|SA|NV|AG|THE)\b"


class CompanyNotSupported(ValueError):
    """Raised with a plain-English message when we can't analyse a ticker."""


def sec_json(url):
    """
    Download data from the SEC. If the SEC refuses (it blocks automated
    requests without a contact email), say so plainly instead of failing with
    a confusing error.
    """
    response = requests.get(url, headers=HEADERS, timeout=30)
    if response.status_code == 404:
        return None
    try:
        return response.json()
    except ValueError:
        hint = ("No contact email is set: add SEC_CONTACT_EMAIL to your .env file "
                "(or the app's Secrets on Streamlit Cloud)." if not SEC_CONTACT_EMAIL else
                "It may be limiting requests from this server; try again in a few minutes.")
        raise RuntimeError(f"The SEC refused the request (status {response.status_code}). "
                           f"{hint}") from None


@lru_cache(maxsize=1)
def _company_list():
    """Every company on the SEC's ticker list, roughly largest first."""
    return list(sec_json("https://www.sec.gov/files/company_tickers.json").values())


def _name_words(text):
    """Upper-case words of a name, without endings like 'Inc'."""
    text = re.sub(NAME_ENDINGS, " ", text.upper().replace("&", " "))
    return re.findall(r"[A-Z0-9]+", text)


def resolve_ticker(query):
    """
    Work out which company the user means, from a ticker ('AAPL', 'BRK.B')
    or a company name ('apple', 'JP Morgan'). Returns a dictionary with the
    ticker, CIK, name, and any other close matches.
    """
    query = query.strip()
    if not query:
        raise CompanyNotSupported("Please enter a ticker or company name, e.g. AAPL or Apple.")
    companies = _company_list()

    # 1. An exact ticker. Share classes are listed with a dash (BRK.B -> BRK-B).
    as_ticker = re.sub(r"[.\s/]+", "-", query.upper())
    for c in companies:
        if c["ticker"] == as_ticker:
            return {"ticker": c["ticker"], "cik": c["cik_str"], "name": c["title"],
                    "matched_name": False, "alternatives": []}

    # 2. A company name. The SEC's list is roughly in order of size, so the
    # first match is usually the one people mean. The query must match from
    # the start of a word ("tesco" shouldn't match "Mitesco"), ignoring spaces
    # ("jp morgan" matches "JPMorgan"). A name that starts with the query beats
    # one where it appears later. Each company is listed once per share class,
    # so keep one entry per company.
    wanted = "".join(_name_words(query))
    if not wanted:
        raise CompanyNotSupported(f"Couldn't understand '{query}'. Try a ticker such as AAPL.")
    starts, contains, seen = [], [], set()
    for c in companies:
        words = _name_words(c["title"])
        position = next((i for i in range(len(words))
                         if "".join(words[i:]).startswith(wanted)), None)
        if position is None or c["cik_str"] in seen:
            continue
        seen.add(c["cik_str"])
        (starts if position == 0 else contains).append(c)
    matches = starts + contains
    if not matches:
        raise CompanyNotSupported(
            f"No US-listed company or ticker matches '{query}'. Companies only listed "
            "outside the US (e.g. Tesco, or Tokyo-only shares) aren't in the SEC's data.")
    best = matches[0]
    return {"ticker": best["ticker"], "cik": best["cik_str"], "name": best["title"],
            "matched_name": True,
            "alternatives": [(c["ticker"], c["title"]) for c in matches[1:6]]}


def is_sec_ticker(query):
    """True if the text is exactly a ticker on the SEC's list (e.g. AAPL, BRK.B)."""
    as_ticker = re.sub(r"[.\s/]+", "-", query.strip().upper())
    return any(c["ticker"] == as_ticker for c in _company_list())


def get_cik(ticker):
    """The SEC's company ID number (CIK) and name for a ticker or company name."""
    company = resolve_ticker(ticker)
    return company["cik"], company["name"]


def get_company_facts(cik):
    """Download every financial figure the company has ever filed."""
    data = sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    if data is None:
        raise CompanyNotSupported("The SEC has no financial statements for this ticker. "
                                  "It may be an ETF or fund, which this tool doesn't cover.")
    return data["facts"]


def _latest_end(facts, tags):
    """The most recent period end across some tags (any currency)."""
    ends = [e["end"] for tag in tags for unit in facts.get(tag, {}).get("units", {}).values()
            for e in unit]
    return max(ends) if ends else ""


def _choose_standard(all_facts):
    """Pick US GAAP or IFRS: whichever has the most recent profit figures."""
    best, best_end = None, ""
    for standard in FLOW_TAGS:
        facts = all_facts.get(standard, {})
        end = _latest_end(facts, FLOW_TAGS[standard]["Net income"] + ["Assets"])
        if end > best_end:
            best, best_end = standard, end
    if best is None:
        raise CompanyNotSupported("The SEC holds no financial statements in a format this "
                                  "tool can read for this company.")
    return best


def _choose_currency(facts):
    """The currency of the most recent total assets figure, e.g. USD, GBP, JPY."""
    latest = {}
    for unit, entries in facts.get("Assets", {}).get("units", {}).items():
        if entries:
            latest[unit] = max(e["end"] for e in entries)
    if latest:
        return max(latest, key=latest.get)
    # No total assets figure (some UK balance sheets show net assets instead):
    # use the currency most figures are in.
    counts = {}
    for tag in facts.values():
        for unit, entries in tag.get("units", {}).items():
            if re.fullmatch(r"[A-Z]{3}", unit):
                counts[unit] = counts.get(unit, 0) + len(entries)
    return max(counts, key=counts.get) if counts else "USD"


def _is_quarterly(facts, standard):
    """US companies file quarterly reports (10-Q); foreign companies don't."""
    cutoff = (date.today() - timedelta(days=500)).isoformat()
    for tag in FLOW_TAGS[standard]["Net income"]:
        for entries in facts.get(tag, {}).get("units", {}).values():
            if any(e.get("form") == "10-Q" and e["end"] >= cutoff for e in entries):
                return True
    return False


def _combine_tags(facts, tags, to_series, currency):
    """
    Build one series from all the possible tags. Companies sometimes switch
    tag (e.g. when accounting rules change), so we use the tag with the newest
    data first and fill any older gaps from the others.
    """
    series = []
    for tag in tags:
        entries = facts.get(tag, {}).get("units", {}).get(currency, [])
        s = to_series(entries) if entries else pd.Series(dtype=float)
        if not s.empty:
            series.append(s)
    series.sort(key=lambda s: s.index.max(), reverse=True)
    combined = pd.Series(dtype=float)
    for s in series:
        combined = s if combined.empty else combined.combine_first(s)
    return combined


def _to_frame(entries):
    """Put raw filing entries into a table, keeping the latest-filed version of each."""
    df = pd.DataFrame(entries)
    if df.empty:
        return df
    df["end"] = pd.to_datetime(df["end"])
    if "start" in df:
        df["start"] = pd.to_datetime(df["start"])
    # The same figure appears in several filings (e.g. last year's number is
    # repeated as a comparative). Keep the most recently filed version, which
    # includes any restatements.
    keys = ["start", "end"] if "start" in df else ["end"]
    return df.sort_values("filed").drop_duplicates(keys, keep="last")


def _quarterly_flow(entries):
    """
    Turn raw filing entries into one value per quarter.

    Companies report some figures for the quarter alone (3 months) and others
    only "year to date" (3, 6, 9 then 12 months). Cash flow is almost always
    year to date, and there's no quarterly report for the fourth quarter at
    all, only the annual report. So where the quarter isn't given directly,
    we take the difference between two year-to-date figures, e.g.
    full year - nine months = fourth quarter.
    """
    df = _to_frame([e for e in entries if "start" in e])
    if df.empty:
        return pd.Series(dtype=float)
    df["days"] = (df["end"] - df["start"]).dt.days

    # A quarter is usually ~91 days, but some retailers (e.g. Costco) have a
    # 16-week (112-day) quarter, so allow up to 120.
    values = {}
    for _, row in df[df["days"].between(80, 120)].iterrows():
        values[row["end"]] = row["val"]

    for _, periods in df.groupby("start"):
        previous = None
        for _, row in periods.sort_values("end").iterrows():
            if (previous is not None and row["end"] not in values
                    and 80 <= (row["end"] - previous["end"]).days <= 120):
                values[row["end"]] = row["val"] - previous["val"]
            previous = row

    return pd.Series(values, dtype=float).sort_index()


def _annual_flow(entries):
    """One value per financial year (periods of roughly 12 months)."""
    df = _to_frame([e for e in entries if "start" in e])
    if df.empty:
        return pd.Series(dtype=float)
    df = df[(df["end"] - df["start"]).dt.days.between(350, 380)]
    df = df.drop_duplicates("end", keep="last")
    return pd.Series(df["val"].values, index=df["end"], dtype=float).sort_index()


def _balance(entries):
    """One value per date for balance sheet items."""
    df = _to_frame([e for e in entries if "start" not in e])
    if df.empty:
        return pd.Series(dtype=float)
    return pd.Series(df["val"].values, index=df["end"], dtype=float).sort_index()


def get_financials(ticker):
    """
    Main function: give it a ticker or company name, get back a table with one
    row per period (quarters for US companies, years for foreign companies)
    plus details of the company and how its figures are reported.
    """
    company = resolve_ticker(ticker)
    all_facts = get_company_facts(company["cik"])
    # Foreign companies on the SEC trade in the US as ADRs; prices are compared
    # with the S&P 500.
    return financials_from_facts({"region": "US", **company}, all_facts)


def financials_from_facts(company, all_facts):
    """
    Build the table of figures from XBRL facts in the SEC's layout. Used for
    SEC filers and, after conversion, for UK and European annual reports
    (data/esef.py).
    """
    standard = _choose_standard(all_facts)
    facts = all_facts[standard]
    currency = _choose_currency(facts)
    quarterly = _is_quarterly(facts, standard)
    to_flow = _quarterly_flow if quarterly else _annual_flow

    flows = pd.DataFrame({
        metric: _combine_tags(facts, tags, to_flow, currency)
        for metric, tags in FLOW_TAGS[standard].items()
    })
    if "Insurance revenue" in flows:
        flows["Revenue"] = flows["Revenue"].fillna(flows["Insurance revenue"])
        flows = flows.drop(columns=["Insurance revenue"])
    # Only keep periods with results. Early-stage companies may have no
    # revenue yet, so a reported profit or loss also counts.
    flows = flows[flows["Revenue"].notna() | flows["Net income"].notna()]
    if flows.empty:
        raise CompanyNotSupported("No results found in the filed figures for this company.")

    # Some companies (e.g. Microsoft) report selling and admin costs separately.
    flows["SG&A"] = flows["SG&A"].fillna(flows["Selling & marketing"] + flows["G&A"])
    flows = flows.drop(columns=["Selling & marketing", "G&A"])
    # Banks: work out provisions if only "interest income after provisions" is
    # given, and total revenue if only its two parts are.
    if "Interest income after provisions" in flows:
        flows["Credit loss provisions"] = flows["Credit loss provisions"].fillna(
            flows["Net interest income"] - flows["Interest income after provisions"])
        flows = flows.drop(columns=["Interest income after provisions"])
    # Some banks report interest earned and paid separately: net them.
    if "Interest income" in flows:
        flows["Net interest income"] = flows["Net interest income"].fillna(
            flows["Interest income"] - flows["Interest expense"])
        flows = flows.drop(columns=["Interest income", "Interest expense"])
    # Some banks report fee income and fee expense separately: net them.
    if "Fee income" in flows:
        flows["Fee & other income"] = flows["Fee & other income"].fillna(
            flows["Fee income"] - flows["Fee expense"])
        flows = flows.drop(columns=["Fee income", "Fee expense"])
    # A bank's total income is at least its interest plus fee income; some
    # banks tag only a narrow line as "revenue", so take whichever is larger.
    bank_income = flows["Net interest income"] + flows["Fee & other income"]
    # Remember when we had to estimate: it excludes trading and other income.
    flows["Revenue is estimate"] = (bank_income > flows["Revenue"].fillna(0)).astype(float)
    flows["Revenue"] = pd.concat([flows["Revenue"], bank_income], axis=1).max(axis=1)
    flows.loc[flows["Revenue"].isna() & bank_income.isna(), "Revenue"] = None

    balances = pd.DataFrame({
        metric: _combine_tags(facts, tags, _balance, currency)
        for metric, tags in BALANCE_TAGS[standard].items()
    })
    # Combining items with different histories can shuffle the dates, so make
    # sure both tables use real dates in order, then line the balance sheet up
    # with the period-end dates.
    flows.index = pd.to_datetime(flows.index)
    flows = flows.sort_index()
    balances.index = pd.to_datetime(balances.index)
    # Some balance sheets (common in the UK) show "net assets" with no total
    # assets line: add the two halves up instead.
    balances["Total assets"] = balances["Total assets"].fillna(
        balances["Non-current assets"] + balances["Current assets"])
    balances = balances.drop(columns=["Non-current assets"])
    table = flows.join(balances.reindex(flows.index))
    table.index.name = "Quarter ending" if quarterly else "Year ending"

    # A bank if it reports deposits and net interest income recently.
    recent = table.tail(4)
    is_bank = bool(recent["Deposits"].notna().any() and recent["Net interest income"].notna().any())

    # Diluted share count from the latest results (a backup for per-share
    # values when there's no market data).
    shares = next((facts[tag]["units"].get("shares", []) for tag in
                   ("WeightedAverageNumberOfDilutedSharesOutstanding", "AdjustedWeightedAverageShares")
                   if tag in facts), [])
    latest_shares = max(shares, key=lambda e: (e["end"], e["filed"]))["val"] if shares else None

    return {"source": "sec", **company, "table": table, "standard": standard,
            "currency": currency, "quarterly": quarterly, "is_bank": is_bank,
            "diluted_shares": latest_shares,
            # An ADR: a foreign company's shares traded in the US.
            "adr": company.get("region") == "US" and not quarterly}


def get_quarterly_financials(ticker):
    """Shortcut used by the week 1 and 2 scripts: (name, table)."""
    result = get_financials(ticker)
    return result["name"], result["table"]
