"""
The Earnings Copilot website.

Run it with:   streamlit run app.py
It opens in your browser at http://localhost:8501
"""

import hmac
import os
from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from analysis.ai import explain_error
from analysis.report import (build_numbers, get_documents, run_ai_report, save_report,
                             report_markdown, REPORTS_FOLDER, EXAMPLES_FOLDER)
from analysis.peers import (compare, summary as peer_summary, COLUMNS as PEER_COLUMNS,
                            FORMATS as PEER_FORMATS)
from analysis.dcf import (default_assumptions, run_dcf, implied_growth, sensitivity,
                          RISK_FREE, EQUITY_PREMIUM)
from analysis.report import shareholder_returns
from data.market import get_market_data, get_quote, market_as_text, MarketDataUnavailable
from data.peers import suggest_peers, all_companies
from data.insiders import get_insider_trades, insiders_as_text, summarise as summarise_insiders
from data.sec import CompanyNotSupported

st.set_page_config(page_title="Earnings Copilot", page_icon=":material/query_stats:",
                   layout="wide")

# Chart colours: a colour-blind-safe palette, with darker-background versions.
DARK = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
SERIES = ["#3987e5", "#d95926", "#199e70"] if DARK else ["#2a78d6", "#eb6834", "#1baf7a"]

# Colours for the hand-built parts of the page (the main theme is in
# .streamlit/config.toml). They're mixed from the page's own text colour, so
# they work in both light and dark mode, whichever one the page is showing.
INK = "inherit"
MUTED = "color-mix(in srgb, currentColor 68%, transparent)"
ACCENT = "#3a6fc4"  # a mid navy-blue, readable on white and on dark backgrounds
LINE = "color-mix(in srgb, currentColor 14%, transparent)"
PANEL = "color-mix(in srgb, currentColor 3%, transparent)"
UP, DOWN = "#1e9a68", "#d64545"

STYLE = f"""<style>
[data-testid="stMainBlockContainer"] {{ max-width: 1180px; padding-top: 1.5rem; }}
[data-testid="stHeader"] {{ background: transparent; }}
/* Figures sit in light cards, like a factsheet. */
[data-testid="stMetric"] {{ border: 1px solid {LINE}; border-radius: 8px;
    padding: 0.7rem 0.9rem; background: {PANEL}; }}
.ec-bar {{ display: flex; justify-content: space-between; align-items: center;
    border-bottom: 1px solid {LINE}; padding-bottom: 0.6rem; }}
.ec-mark {{ display: flex; align-items: center; gap: 0.55rem; font-weight: 700;
    font-size: 1.05rem; color: {INK}; letter-spacing: -0.01em; }}
.ec-logo {{ display: flex; align-items: flex-end; gap: 2px; height: 18px; }}
.ec-logo span {{ width: 5px; background: {ACCENT}; border-radius: 1px; }}
.ec-links a {{ color: {MUTED}; text-decoration: none; font-size: 0.9rem; margin-left: 1.2rem; }}
.ec-links a:hover {{ color: {ACCENT}; }}
.ec-hero {{ padding: 2.6rem 0 1.2rem; max-width: 760px; }}
.ec-eyebrow {{ text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.75rem;
    font-weight: 600; color: {ACCENT}; margin-bottom: 0.8rem; }}
.ec-title {{ font-family: "Source Serif 4", Georgia, serif; font-size: 3rem; line-height: 1.08;
    font-weight: 700; color: {INK}; margin: 0 0 1rem; letter-spacing: -0.02em; }}
.ec-lede {{ font-size: 1.12rem; line-height: 1.6; color: {MUTED}; margin: 0; }}
.ec-step {{ border-top: 2px solid {ACCENT}; padding-top: 0.8rem; }}
.ec-step-n {{ font-size: 0.8rem; font-weight: 600; color: {ACCENT}; letter-spacing: 0.08em; }}
.ec-step-t {{ font-weight: 600; color: {INK}; margin: 0.25rem 0 0.35rem; font-size: 1.02rem; }}
.ec-step-d {{ color: {MUTED}; font-size: 0.92rem; line-height: 1.55; }}
.ec-facts {{ display: flex; flex-wrap: wrap; gap: 0.6rem 2.2rem; padding: 1rem 0;
    border-top: 1px solid {LINE}; border-bottom: 1px solid {LINE}; }}
.ec-fact b {{ display: block; font-size: 1.35rem; color: {INK};
    font-family: "Source Serif 4", Georgia, serif; }}
.ec-fact span {{ color: {MUTED}; font-size: 0.85rem; }}
.ec-co {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 1rem 2rem;
    align-items: flex-end; padding: 1.2rem 0 1rem; border-bottom: 1px solid {LINE}; }}
.ec-co-name {{ font-family: "Source Serif 4", Georgia, serif; font-size: 2rem;
    font-weight: 700; color: {INK}; line-height: 1.15; letter-spacing: -0.01em; }}
.ec-co-meta {{ color: {MUTED}; font-size: 0.9rem; margin-top: 0.4rem; }}
.ec-chip {{ display: inline-block; border: 1px solid {LINE}; border-radius: 4px;
    padding: 0 0.4rem; font-weight: 600; color: {INK}; margin-right: 0.4rem;
    font-size: 0.82rem; }}
.ec-stats {{ display: flex; gap: 2rem; flex-wrap: wrap; }}
.ec-stat span {{ display: block; color: {MUTED}; font-size: 0.78rem;
    text-transform: uppercase; letter-spacing: 0.06em; }}
.ec-stat b {{ font-size: 1.3rem; color: {INK}; font-weight: 600; }}
.ec-stat i {{ font-style: normal; font-size: 0.88rem; margin-left: 0.35rem; }}
.ec-foot {{ margin-top: 3rem; padding: 1.2rem 0 0.5rem; border-top: 1px solid {LINE};
    color: {MUTED}; font-size: 0.85rem; line-height: 1.6; }}
.ec-foot a {{ color: {MUTED}; }}
@media (max-width: 640px) {{ .ec-title {{ font-size: 2.2rem; }}
    [data-testid="stMainBlockContainer"] {{ padding-top: 3.2rem; }}
    .ec-co-name {{ font-size: 1.6rem; }} .ec-links a {{ margin-left: 0.8rem; }} }}
</style>"""

# Each red-flag status gets an icon and a word as well as a colour, so it never
# relies on colour alone.
BADGES = {"OK": ":green-badge[:material/check_circle: OK]",
          "AMBER": ":orange-badge[:material/warning: Amber]",
          "RED": ":red-badge[:material/error: Red]",
          "N/A": ":gray-badge[:material/remove: N/A]"}

GITHUB = "https://github.com/hillsie-oh32/earnings-co-pilot"

# The website version of the README (the GitHub one has setup instructions and
# file links that only work on GitHub).
ABOUT = f"""
**Earnings Copilot turns a company's official filings into an analyst-style briefing
pack in about three minutes.**

**What it does**
1. **Downloads the figures and filings from the SEC** (the US markets regulator):
   US, UK, European, Japanese and Chinese companies, in their own currency.
2. **Calculates the key numbers in Python:** growth, margins, free cash flow, net debt,
   and an **accounting red-flag scan** (receivables, cash conversion, inventory,
   goodwill, leverage, cash runway and the Beneish M-Score for earnings manipulation).
   Banks get their own checks: efficiency, return on equity, loan losses and funding.
3. **Shows the market view:** share price against the S&P 500, valuation, dividend yield
   and history, buybacks, results vs analysts' expectations, and analysts' consensus
   ratings and price targets.
4. **Compares it with its peers:** similar companies side by side on growth, margins,
   valuation and red flags, with the peer median. An interactive **DCF valuation**
   shows what growth the share price implies, and an **insider trading** tab shows
   directors' and executives' own share deals.
5. **Has Claude (Anthropic's AI) read the actual documents** and write a briefing pack:
   results summary, change in management tone, red flags explained, the questions
   analysts are likely to ask (with draft answers), a company brief, and questions to
   ask in an interview.

**How to use it:** type a company name or ticker and press **Analyse**. The numbers and
red flags are free and instant. The **Saved reports** tab has finished AI reports to
read. To write a new one, use your own Claude API key on the **AI report** tab.

**Key design choice:** the code does all the maths from official data; the AI only reads
and explains, and must cite its sources. Its output was checked against the original
filings, and the tool was stress-tested on 73 companies across six regions.

**Limitations:** foreign companies show annual figures (they only file structured data
with the SEC once a year); companies listed only outside the US (e.g. Tesco) aren't
covered; red flags are prompts to look closer, not verdicts; share prices and analyst
ratings come from Yahoo Finance and may be delayed. **Not investment advice.**

Built by a second-year Accounting & Finance student with Python, pandas, Streamlit and
the Claude API. **[View the code and full documentation on GitHub]({GITHUB})**
"""

SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "CNY": "CN¥", "CHF": "CHF ",
           "DKK": "DKK ", "SEK": "SEK ", "CAD": "C$", "AUD": "A$", "HKD": "HK$"}


@st.cache_data(ttl=900, show_spinner=False)
def load_market(ticker, foreign):
    """Share price and market data, cached for 15 minutes. Returns (data, error)."""
    try:
        return get_market_data(ticker, foreign), None
    except MarketDataUnavailable as error:
        return None, str(error)


def performance_chart(market, years, name):
    """The share price against the S&P 500, both rebased to 100 at the start."""
    prices = pd.DataFrame({name: market["prices"], "S&P 500": market["benchmark"]}).dropna()
    prices = prices[prices.index >= prices.index[-1] - pd.DateOffset(years=years)]
    rebased = (prices / prices.iloc[0] * 100).reset_index(names="Date")
    data = rebased.melt(id_vars="Date", var_name="Series", value_name="Value")
    base = alt.Chart(data, title=f"Share price vs the S&P 500 (start = 100)").encode(
        x=alt.X("Date:T", title=None),
        y=alt.Y("Value:Q", title=None, scale=alt.Scale(zero=False),
                axis=alt.Axis(grid=True, gridOpacity=0.3)),
        color=alt.Color("Series:N", scale=alt.Scale(domain=[name, "S&P 500"],
                                                    range=SERIES[:2]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("Series:N"),
                 alt.Tooltip("Value:Q", format=".1f")],
    )
    return base.mark_line(strokeWidth=2).properties(height=300)


# Analyst ratings run from positive to negative, so they use a diverging scale:
# blues for buy, a neutral grey for hold, reds for sell.
RATING_COLOURS = ["#1c5cab", "#6da7ec", "#a3a29d", "#ef9a99", "#d03b3b"]


def analyst_chart(counts):
    """How many analysts give each rating this month."""
    data = pd.DataFrame({"Rating": list(counts), "Analysts": list(counts.values())})
    return alt.Chart(data, title="Analyst ratings this month").mark_bar(
        cornerRadiusEnd=4, height={"band": 0.7},
    ).encode(
        y=alt.Y("Rating:N", sort=list(counts), title=None),
        x=alt.X("Analysts:Q", title=None, axis=alt.Axis(tickMinStep=1, grid=True,
                                                         gridOpacity=0.3)),
        color=alt.Color("Rating:N", scale=alt.Scale(domain=list(counts),
                                                    range=RATING_COLOURS), legend=None),
        tooltip=[alt.Tooltip("Rating:N"), alt.Tooltip("Analysts:Q")],
    ).properties(height=200)


def eps_chart(scorecard):
    """Reported earnings per share against analysts' estimates, each quarter."""
    rows = []
    for r in scorecard:
        label = r["date"].strftime("%b %Y")
        rows += [{"Quarter": label, "Type": "Analysts' estimate", "EPS": r["estimate"]},
                 {"Quarter": label, "Type": "Reported", "EPS": r["actual"]}]
    names = ["Analysts' estimate", "Reported"]
    return alt.Chart(pd.DataFrame(rows), title="Earnings per share: reported vs estimate").mark_bar(
        cornerRadiusEnd=4,
    ).encode(
        x=alt.X("Quarter:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("Type:N", sort=names),
        y=alt.Y("EPS:Q", title=None, axis=alt.Axis(grid=True, gridOpacity=0.3)),
        # Estimate in a neutral grey, the reported figure in the main colour.
        color=alt.Color("Type:N", scale=alt.Scale(domain=names, range=["#a3a29d", SERIES[0]]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Quarter:N"), alt.Tooltip("Type:N"),
                 alt.Tooltip("EPS:Q", format=".2f")],
    ).properties(height=260)


def year_label(year):
    """'2026 so far' for the current year, whose figures are incomplete."""
    return f"{year} so far" if year == pd.Timestamp.now().year else str(year)


def year_bars(by_year, title, value_title):
    """Bars for a series indexed by year."""
    data = by_year.rename_axis("Year").reset_index(name=value_title)
    data["Year"] = data["Year"].map(year_label)
    return alt.Chart(data, title=title).mark_bar(
        color=SERIES[0], cornerRadiusEnd=4, width={"band": 0.6},
    ).encode(
        x=alt.X("Year:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y(f"{value_title}:Q", title=None, axis=alt.Axis(grid=True, gridOpacity=0.3)),
        tooltip=[alt.Tooltip("Year:N"), alt.Tooltip(f"{value_title}:Q", format=",.2f")],
    ).properties(height=260)


def returns_chart(q, currency, quarterly):
    """Dividends and buybacks paid each year (millions), side by side."""
    yearly = q[["Dividends paid", "Share buybacks"]].groupby(q.index.year).sum(min_count=1)
    yearly = (yearly.tail(6) / 1e6).rename_axis("Year").reset_index()
    # Quarterly figures for the current year cover only part of it.
    yearly["Year"] = yearly["Year"].map(year_label if quarterly else str)
    data = yearly.melt(id_vars="Year", var_name="Type", value_name="Amount").dropna()
    names = ["Dividends paid", "Share buybacks"]
    return alt.Chart(data, title=f"Cash returned to shareholders ({currency} m)").mark_bar(
        cornerRadiusEnd=4,
    ).encode(
        x=alt.X("Year:N", title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("Type:N", sort=names),
        y=alt.Y("Amount:Q", title=None, axis=alt.Axis(grid=True, gridOpacity=0.3)),
        color=alt.Color("Type:N", scale=alt.Scale(domain=names, range=SERIES[:2]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Year:N"), alt.Tooltip("Type:N"),
                 alt.Tooltip("Amount:Q", format=",.0f")],
    ).properties(height=260)


@st.cache_data(ttl=3600, show_spinner=False)
def load_numbers(query):
    """Fetch and calculate everything for a company. Cached for an hour."""
    return build_numbers(query)


def money(value, currency):
    """Format money as $1.2bn / £345m / ¥48.0tn."""
    if pd.isna(value):
        return "n/a"
    symbol = SYMBOLS.get(currency, currency + " ")
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e12:
        return f"{sign}{symbol}{value / 1e12:,.1f}tn"
    if value >= 1e9:
        return f"{sign}{symbol}{value / 1e9:,.1f}bn"
    return f"{sign}{symbol}{value / 1e6:,.0f}m"


def with_labels(df):
    """The table with a readable 'Period' column, e.g. 'Sep 2026'."""
    data = df.reset_index()
    data["Period"] = data.iloc[:, 0].dt.strftime("%b %Y")
    return data


def bar_chart(df, field, title):
    """Single-series bar chart with a hover tooltip."""
    data = with_labels(df)
    return alt.Chart(data, title=title).mark_bar(
        color=SERIES[0], cornerRadiusEnd=4, width={"band": 0.6},
    ).encode(
        x=alt.X("Period:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y(f"{field}:Q", title=None, axis=alt.Axis(grid=True, gridOpacity=0.3)),
        tooltip=[alt.Tooltip("Period:N"), alt.Tooltip(f"{field}:Q", format=",.0f"),
                 *([alt.Tooltip("Growth YoY %:Q", format="+.1f")]
                   if field == "Revenue m" else [])],
    ).properties(height=280)


def line_chart(df, names, title):
    """One or more percentage series on a single axis, with a legend if several."""
    data = with_labels(df).melt(id_vars="Period", value_vars=names,
                                var_name="Measure", value_name="Percent")
    # Short legend labels ("Gross" not "Gross margin %") so they fit on a phone;
    # the chart title already says what they are.
    short = [n.replace(" margin %", "").replace(" %", "") for n in names]
    data["Measure"] = data["Measure"].map(dict(zip(names, short)))
    names = short
    base = alt.Chart(data, title=title).encode(
        x=alt.X("Period:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Percent:Q", title="%", axis=alt.Axis(grid=True, gridOpacity=0.3)),
        color=alt.Color("Measure:N", scale=alt.Scale(domain=names, range=SERIES[:len(names)]),
                        legend=alt.Legend(orient="top", title=None) if len(names) > 1 else None),
        tooltip=[alt.Tooltip("Period:N"), alt.Tooltip("Measure:N"),
                 alt.Tooltip("Percent:Q", format=".1f")],
    )
    return (base.mark_line(strokeWidth=2) + base.mark_point(size=64, filled=True)
            ).properties(height=280)


def show_chart(column, df, field, chart, *args):
    """Draw a chart only if `field` has data. `args` are passed on to `chart`."""
    if df[field].notna().any():
        column.altair_chart(chart(df, *args), width="stretch")
    else:
        label = {"FCF m": "free cash flow"}.get(field, field.replace(" m", "").lower())
        column.caption(f"No {label} figures reported.")


# Words that stay in capitals when tidying a company name.
ACRONYMS = {"PLC", "AG", "SA", "NV", "SE", "ASA", "LLC", "LP", "ADR", "HSBC", "BP", "SAP",
            "ASML", "UBS", "IBM", "CVS", "JD", "NIO", "PDD", "AB", "AS", "SPA", "II", "III",
            "USA", "US", "UK", "PJSC", "KGAA", "BHP", "RELX", "ING", "NXP", "ABB", "GSK"}


def display_name(name, ticker):
    """'TOYOTA MOTOR CORP/' -> 'Toyota Motor Corp'. SEC names are often in capitals
    with a state code on the end (e.g. 'APPLE INC /CA/')."""
    name = name.strip().rstrip("/").strip()
    if "/" in name and len(name.rsplit("/", 1)[1]) <= 3:
        name = name.rsplit("/", 1)[0].strip()
    if not name.isupper():
        return name
    words = []
    for word in name.split():
        core = word.strip(".,()").upper()
        if core == "JPMORGAN":
            words.append("JPMorgan")
        else:
            words.append(word if core in ACRONYMS or core == ticker.upper().split("-")[0]
                         else word.title())
    return " ".join(words)


def choose(query):
    if query and query.strip():
        st.session_state.query = query.strip()
        st.session_state.pop("report", None)


def go_home():
    st.session_state.pop("query", None)
    st.session_state.pop("report", None)


def footer():
    st.html(f"""<div class="ec-foot">
      Figures from SEC filings (EDGAR). Share prices, valuations and analyst ratings from
      Yahoo Finance, which may be delayed. A research and learning tool, not investment advice.<br>
      Built by a second-year Accounting &amp; Finance student with Python, Streamlit and the
      Claude API &middot; <a href="{GITHUB}" target="_blank">Source code and documentation</a>
    </div>""")


# Companies visitors can try in one click from the home page.
EXAMPLES = [("Apple", "AAPL"), ("Kraft Heinz", "KHC"), ("Nvidia", "NVDA"),
            ("JPMorgan", "JPM"), ("HSBC", "HSBC"), ("Toyota", "TM")]

# --- Page ---------------------------------------------------------------------
st.html(STYLE)
home = "query" not in st.session_state

st.html(f"""<div class="ec-bar">
  <div class="ec-mark"><div class="ec-logo"><span style="height:8px"></span>
    <span style="height:13px"></span><span style="height:18px"></span></div>Earnings Copilot</div>
  <div class="ec-links"><a href="{GITHUB}#readme" target="_blank">How it works</a>
    <a href="{GITHUB}" target="_blank">GitHub</a></div>
</div>""")

if home:
    st.html("""<div class="ec-hero">
      <div class="ec-eyebrow">Equity research from the source filings</div>
      <div class="ec-title">Read a company's results like an analyst.</div>
      <p class="ec-lede">Type a company. Get its numbers, an accounting red-flag scan,
      market valuation, peers, a DCF and insider dealing, all built from official SEC
      filings. Then have Claude read the documents and write the briefing pack.</p>
    </div>""")

with st.form("search", border=False):
    left, right = st.columns([4, 1], vertical_alignment="bottom")
    query = left.text_input("Company name or ticker", placeholder="e.g. Apple, KHC, HSBC, Toyota",
                            label_visibility="collapsed" if not home else "visible",
                            help="Names and tickers both work: Apple, AAPL, JP Morgan, HSBC, "
                                 "Toyota, SAP, BRK.B")
    if right.form_submit_button("Analyse", type="primary", width="stretch"):
        choose(query)

if home:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption("Try one:", width="content")
        for label, ticker in EXAMPLES:
            st.button(label, key=f"try-{ticker}", on_click=choose, args=(ticker,))

    st.write("")
    steps = [
        ("01", "The numbers", "Growth, margins, free cash flow and net debt, calculated "
         "in Python from the company's own SEC data. No AI guesswork in the figures."),
        ("02", "The red flags", "Checks an auditor would run: receivables, cash conversion, "
         "inventory, goodwill, leverage and the Beneish M-Score. Banks get bank checks."),
        ("03", "The briefing pack", "Claude reads the earnings releases and annual report, "
         "explains the flags, tracks management's tone and drafts the analyst Q&A."),
    ]
    for col, (number, title, text) in zip(st.columns(3, gap="large"), steps):
        col.html(f'<div class="ec-step"><div class="ec-step-n">{number}</div>'
                 f'<div class="ec-step-t">{title}</div><div class="ec-step-d">{text}</div></div>')

    st.write("")
    st.html("""<div class="ec-facts">
      <div class="ec-fact"><b>5 regions</b><span>US, UK, Europe, Japan, China</span></div>
      <div class="ec-fact"><b>7 views</b><span>Numbers to insider trades</span></div>
      <div class="ec-fact"><b>73 companies</b><span>used to stress-test it</span></div>
      <div class="ec-fact"><b>Every figure</b><span>traced to an official filing</span></div>
    </div>""")
    st.write("")
    with st.expander("About this project"):
        st.markdown(ABOUT)
    footer()
    st.stop()

st.button("← Home", on_click=go_home, type="tertiary")

try:
    with st.spinner(f"Fetching {st.session_state.query} from the SEC..."):
        n = load_numbers(st.session_state.query)
except CompanyNotSupported as error:
    st.warning(str(error))
    st.stop()
except Exception as error:
    st.error(f"Something went wrong fetching {st.session_state.query}. The SEC website may "
             f"be busy, so try again in a minute. (Details: {error})")
    st.stop()

cur = n["currency"]
market, market_error = load_market(n["ticker"], not n["quarterly"])

# The company header: who it is, and the key facts before opening any tab.
stats = []
if market:
    symbol = SYMBOLS.get(market["currency"], market["currency"] + " ")
    change = market["day_change"]
    move = (f'<i style="color:{UP if change >= 0 else DOWN}">'
            f'{"▲" if change >= 0 else "▼"} {abs(change):.2%}</i>' if change is not None else "")
    stats += [("Share price", f"{symbol}{market['price']:,.2f}{move}"),
              ("Market value", money(market["market_cap"], market["currency"]))]
raised = [f for f in n["flags"] if f["status"] in ("RED", "AMBER")]
reds = sum(f["status"] == "RED" for f in n["flags"])
stats.append(("Red flags", (f"{len(raised)} of {len(n['flags'])}"
                            + (f' <i style="color:{DOWN}">{reds} red</i>' if reds else ""))
              if raised else f'<span style="color:{UP}">None raised</span>'))
meta = [n["industry"].title() if n.get("industry") else None,
        "US filer, quarterly figures" if n["quarterly"] else "Foreign filer, annual figures",
        f"Reports in {cur}"]
st.html(f"""<div class="ec-co">
  <div><div class="ec-co-name">{escape(display_name(n['name'], n['ticker']))}</div>
    <div class="ec-co-meta"><span class="ec-chip">{escape(n['ticker'])}</span>
      {" · ".join(escape(m) for m in meta if m)}</div></div>
  <div class="ec-stats">{"".join(f'<div class="ec-stat"><span>{label}</span><b>{value}</b></div>'
                                 for label, value in stats)}</div>
</div>""")

if n["matched_name"]:
    st.caption(f"Matched from “{st.session_state.query}”. Not the right company?")
    if n["alternatives"]:
        for col, (ticker, title) in zip(st.columns(len(n["alternatives"])), n["alternatives"]):
            col.button(f"{title[:28]} ({ticker})", key=f"alt-{ticker}",
                       on_click=choose, args=(ticker,), width="stretch")
for note in n["notes"]:
    st.info(note, icon=":material/info:")
for warning in n["warnings"]:
    st.warning(warning, icon=":material/warning:")

# The AI report gets the market data too, when it's available.
n_ai = {**n, "market_text": market_as_text(market)} if market else n

numbers_tab, market_tab, peers_tab, dcf_tab, insiders_tab, ai_tab, saved_tab = st.tabs(
    [":material/table_chart: Numbers & red flags", ":material/show_chart: Market & dividends",
     ":material/compare_arrows: Peers", ":material/calculate: Valuation (DCF)",
     ":material/person_search: Insiders", ":material/article: AI report",
     ":material/folder_open: Saved reports"])

# --- Insiders: directors' and executives' own share trades (SEC Form 4) ---------------
with insiders_tab:
    st.markdown("When directors, senior executives and 10% owners buy or sell the "
                "company's shares, they must report it to the SEC on a **Form 4**. "
                "Open-market **buys** are the stronger signal: insiders sell for many "
                "reasons, and many sales are scheduled in advance under 10b5-1 plans.")
    if st.button("Load insider trades (last 12 months)", type="primary"):
        progress = st.progress(0.0, text="Reading Form 4 filings...")
        table, failed = get_insider_trades(
            n["cik"], n["filings"],
            on_progress=lambda i, total: progress.progress(i / max(total, 1),
                                                           text=f"Filing {i + 1} of {total}"))
        progress.empty()
        st.session_state.insiders = {"ticker": n["ticker"], "table": table, "failed": failed}
    loaded = st.session_state.get("insiders")
    if loaded and loaded["ticker"] == n["ticker"]:
        table, s = loaded["table"], summarise_insiders(loaded["table"])
        if s is None:
            st.info("No insider transactions reported in the last 12 months. Foreign "
                    "companies listed in the US don't have to file Form 4s.")
        else:
            cols = st.columns(3)
            cols[0].metric("Open-market buys", f"${s['buy_value'] / 1e6:,.1f}m",
                           help=f"{s['buy_count']} purchases by {s['buyers']} insiders.")
            cols[1].metric("Open-market sales", f"${s['sell_value'] / 1e6:,.1f}m",
                           help=f"{s['sell_count']} sales by {s['sellers']} insiders.")
            cols[2].metric("Sales under 10b5-1 plans",
                           f"{s['planned_share']:.0%}" if s["planned_share"] is not None
                           else "n/a", help="Share of sales value scheduled in advance, "
                                            "which says less about the insider's view.")
            shown = table.drop(columns=["Code"]).copy()
            shown["Date"] = shown["Date"].dt.strftime("%d %b %Y")
            # Format as text ourselves so blanks stay blank (not "None").
            for column, fmt in {"Shares": "{:,.0f}", "Price": "${:,.2f}", "Value": "${:,.0f}",
                                "Shares owned after": "{:,.0f}"}.items():
                shown[column] = [fmt.format(v) if pd.notna(v) else "" for v in shown[column]]
            shown["Planned (10b5-1)"] = [{True: "Yes", False: "No"}.get(v, "")
                                         for v in shown["Planned (10b5-1)"]]
            st.dataframe(shown, hide_index=True, width="stretch")
            st.caption("Awards, option exercises and tax withholdings are routine pay "
                       "matters and aren't counted as buys or sales. Where connected "
                       "holders (e.g. a fund and its owner) report the same trade, it's "
                       "counted once. Source: SEC Form 4."
                       + (f" {loaded['failed']} filings couldn't be read."
                          if loaded["failed"] else ""))
        # The AI report includes the insider summary once it has been loaded.
        n_ai = {**n_ai, "insiders_text": insiders_as_text(table)}

# --- Tab 1: the numbers ----------------------------------------------------------
with numbers_tab:
    period_word = "quarter" if n["quarterly"] else "year"
    st.subheader("Last 12 months" if n["quarterly"] else "Latest financial year")
    now, ago = n["now"], n["ago"]
    tiles = []
    for item in n["headlines"]:
        change = (now[item] / ago[item] - 1
                  if pd.notna(now[item]) and pd.notna(ago[item]) and ago[item] else None)
        tiles.append(dict(label=item, value=money(now[item], cur),
                          delta=f"{change:+.1%} vs prior year" if change is not None else None,
                          delta_color="inverse" if item == "Credit loss provisions"
                          else "normal"))
    if not n["is_bank"]:
        net_debt = now["Net debt"]
        tiles.append(dict(label="Net debt" if pd.isna(net_debt) or net_debt > 0 else "Net cash",
                          value=money(abs(net_debt), cur) if pd.notna(net_debt) else "n/a"))
    # At most 3 figures per row, so they aren't cut off on narrower screens.
    per_row = 3 if len(tiles) > 4 else len(tiles)
    for start in range(0, len(tiles), per_row):
        for col, tile in zip(st.columns(per_row), tiles[start:start + per_row]):
            col.metric(**tile)

    if pd.isna(ago["Net income"]):
        st.caption("Less than a year of history, so there is no prior-year comparison yet.")
    snapshot = n["snapshot"]
    unit = f"{cur} m"
    if snapshot["Revenue m"].isna().all():
        st.info("No revenue reported yet: this looks like a pre-revenue company. "
                "Watch the cash runway check below.")
    else:
        left, right = st.columns(2)
        show_chart(left, snapshot, "Revenue m", bar_chart, "Revenue m",
                   f"Revenue by {period_word} ({unit})")
        if n["is_bank"]:
            show_chart(right, snapshot, "Efficiency ratio %", line_chart,
                       ["Efficiency ratio %"], "Efficiency ratio: costs as % of income")
        else:
            right.altair_chart(line_chart(snapshot, ["Gross margin %", "Operating margin %",
                                                     "Net margin %"], "Margins (%)"),
                               width="stretch")
    left, right = st.columns(2)
    if n["is_bank"]:
        show_chart(left, snapshot, "Net income m", bar_chart, "Net income m",
                   f"Net income by {period_word} ({unit})")
    else:
        show_chart(left, snapshot, "FCF m", bar_chart, "FCF m",
                   f"Free cash flow by {period_word} ({unit})")

    with right:
        st.markdown(f"**Last {len(snapshot)} {period_word}s** (money in {unit})")
        table = snapshot.copy()
        table.index = table.index.strftime("%b %Y")
        st.dataframe(table, width="stretch", column_config={
            c: st.column_config.NumberColumn(format="%,.0f" if c.endswith(" m") else "%.1f")
            for c in table.columns})

    st.subheader("Red-flag scan" + (" (bank checks)" if n["is_bank"] else ""))
    counts = {s: sum(f["status"] == s for f in n["flags"]) for s in ("RED", "AMBER", "OK")}
    st.markdown(" ".join(f"{BADGES[s]} × {c}" for s, c in counts.items() if c))
    st.caption(f"{'Last 12 months' if n['quarterly'] else 'Latest year'} vs the year before. "
               "A flag is a reason to look closer, not proof of a problem.")
    # Raised flags first, each check in one bordered list.
    order = {"RED": 0, "AMBER": 1, "OK": 2, "N/A": 3}
    with st.container(border=True, gap="small"):
        for i, flag in enumerate(sorted(n["flags"], key=lambda f: order[f["status"]])):
            if i:
                st.html(f'<div style="border-top:1px solid {LINE}"></div>')
            # Badge and check name on one line, so they stay together on a phone.
            check, detail = st.columns([3, 5])
            check.markdown(f"{BADGES[flag['status']]} **{flag['check']}**")
            if flag["value"] != "-":
                check.caption(flag["value"])
            with detail:
                st.markdown(flag["detail"])
                if flag["status"] in ("RED", "AMBER") or n["is_bank"]:
                    st.caption(flag["why"])
    st.markdown("**Filing history (last 3 years):** " +
                ("; ".join(n["history"]) if n["history"]
                 else "no auditor changes, restatements or late filings ✅"))

# --- Tab 2: market & dividends -----------------------------------------------------
with market_tab:
    if market is None:
        st.warning(f"Share price data isn't available right now. {market_error}",
                   icon=":material/warning:")
    else:
        mcur = market["currency"]
        st.subheader("Share price and valuation")
        if market["foreign"]:
            st.caption("This is the US-listed share (ADR), priced in US dollars. One ADR can "
                       "represent several home-market shares, so per-share figures differ "
                       "from the home market, and ratios that mix the two are left out.")

        def ratio(x, suffix="x"):
            return f"{x:.1f}{suffix}" if x else "n/a"

        cols = st.columns(4)
        cols[0].metric("Share price", f"{SYMBOLS.get(mcur, mcur + ' ')}{market['price']:,.2f}",
                       delta=(f"{market['day_change']:+.2%} today"
                              if market["day_change"] is not None else None))
        cols[1].metric("Market value", money(market["market_cap"], mcur))
        cols[2].metric("P/E (last 12 months)", ratio(market["pe"]),
                       help="Share price ÷ earnings per share over the last 12 months.")
        cols[3].metric("Forward P/E", ratio(market["forward_pe"]),
                       help="Share price ÷ analysts' forecast earnings for the next 12 months.")

        cols = st.columns(4)
        ret, mret = market["return_1y"], market["market_return_1y"]
        cols[0].metric("1-year price change",
                       f"{ret:+.1%}" if ret is not None else "n/a",
                       delta=(f"{(ret - mret) * 100:+.1f} pts vs S&P 500"
                              if ret is not None and mret is not None else None))
        cols[1].metric("Dividend yield",
                       f"{market['dividend_yield']:.2f}%" if market["dividend_yield"] else "None",
                       help="Dividends paid over a year ÷ share price.")
        low, high = market["low_52w"], market["high_52w"]
        cols[2].metric("vs 52-week high",
                       f"{market['price'] / high - 1:+.1%}" if high else "n/a",
                       help=(f"52-week range: {low:,.2f} to {high:,.2f}" if low and high
                             else None))
        cols[3].metric("Beta", f"{market['beta']:.2f}" if market["beta"] else "n/a",
                       help="How much the share tends to move with the market. 1 = in line; "
                            "above 1 = more volatile; below 1 = less.")
        if market["price_to_book"] or market["ev_to_ebitda"]:
            cols = st.columns(4)
            cols[0].metric("Price-to-book", ratio(market["price_to_book"]),
                           help="Market value ÷ the accounting value of shareholders' equity.")
            cols[1].metric("EV/EBITDA", ratio(market["ev_to_ebitda"]),
                           help="Enterprise value (market value + net debt) ÷ EBITDA. "
                                "Widely used to compare companies' valuations.")

        period = st.segmented_control("Period", ["1 year", "5 years"], default="1 year",
                                      label_visibility="collapsed") or "1 year"
        st.altair_chart(performance_chart(market, 1 if period == "1 year" else 5, n["ticker"]),
                        width="stretch")

        st.subheader("Results vs expectations")
        card = market["scorecard"]
        nxt = market["next_results"]
        next_text = f"{nxt:%d %b %Y}" if nxt is not None else "Not announced"
        if not card:
            st.metric("Next results", next_text)
            if market["foreign"]:
                st.caption("Earnings-vs-estimate history isn't shown for foreign companies: "
                           "Yahoo's figures for US-listed ADRs often compare per-ADR and "
                           "home-market amounts, which gives misleading 'surprises'.")
        else:
            beats = sum(r["outcome"] == "Beat" for r in card)
            moves = [r["reaction"] for r in card if r["reaction"] is not None]
            cols = st.columns(3)
            cols[0].metric(f"Beat estimates", f"{beats} of last {len(card)}",
                           help="Quarters where reported earnings per share (EPS) were above "
                                "the analysts' average estimate.")
            cols[1].metric("Average move on results day",
                           f"{sum(abs(x) for x in moves) / len(moves):.1%}" if moves else "n/a",
                           help="The average size of the share price move (up or down) on "
                                "the first trading day after results. A guide to how much "
                                "results tend to move the shares.")
            cols[2].metric("Next results", next_text,
                           help=(f"Analysts' average EPS estimate: {market['next_estimate']:.2f}"
                                 if market["next_estimate"] is not None else None))
            left, right = st.columns(2)
            left.altair_chart(eps_chart(card), width="stretch")
            table = pd.DataFrame([{
                "Results": f"{r['date']:%d %b %Y}",
                "Result": {"Beat": "✅ Beat", "Miss": "❌ Miss"}.get(r["outcome"], "➖ In line"),
                "Surprise": f"{r['surprise']:+.1%}" if r["surprise"] is not None
                            else f"{r['actual'] - r['estimate']:+.2f}",
                "Price move": f"{r['reaction']:+.1%}" if r["reaction"] is not None else "n/a",
            } for r in reversed(card)])
            right.dataframe(table, hide_index=True, width="stretch")
            st.caption("EPS figures are those Yahoo Finance uses, usually the company's "
                       "adjusted EPS, so they can differ from the statutory figure. "
                       "'Price move' is the first trading day after the results. "
                       "A beat doesn't guarantee a rise: the share price reacts to how "
                       "results compare with the market's real hopes, and to guidance.")

        st.subheader("What analysts say")
        a = market["analysts"]
        if not a:
            st.info("No analyst ratings found for this company.")
        else:
            st.caption("Ratings and price targets from professional analysts at banks and "
                       "brokers, as collected by Yahoo Finance. These are their views, not "
                       "a recommendation from this tool.")
            left, right = st.columns([2, 3])
            with left:
                st.metric("Consensus", a["consensus"] or "n/a",
                          help="The average rating on a 1-5 scale (1 = Strong Buy, "
                               "5 = Strong Sell)"
                               + (f": {a['score']:.1f}." if a["score"] else "."))
                if a["target"]:
                    st.metric("Average price target",
                              f"{SYMBOLS.get(mcur, mcur + ' ')}{a['target']:,.2f}",
                              delta=(f"{a['upside']:+.1%} vs current price"
                                     if a["upside"] is not None else None),
                              help=(f"Analysts' targets range from {a['target_low']:,.2f} to "
                                    f"{a['target_high']:,.2f}. Targets are usually for 12 "
                                    "months ahead." if a["target_low"] and a["target_high"]
                                    else None))
            if a["total"]:
                right.altair_chart(analyst_chart(a["counts"]), width="stretch")
            notes = []
            if a["total"] and a["total"] < 6:
                notes.append(f"Only {a['total']} analysts are counted, so the consensus is a "
                             "small sample"
                             + (": Yahoo counts analysts covering the US-listed shares, and "
                                "many more cover this company in its home market."
                                if market["foreign"] else "."))
            if a["upside"] is not None and abs(a["upside"]) > 0.5:
                notes.append("The average target is far from today's price, a sign that "
                             "analysts' expectations differ sharply from the market's.")
            notes.append("Analysts' ratings and targets are often wrong, and can be "
                         "influenced by their firm's relationship with the company.")
            st.caption(" ".join(notes))

    st.subheader("Dividends and buybacks")
    returns = shareholder_returns(n["now"])
    cols = st.columns(3)
    cols[0].metric("Dividends paid", money(returns["dividends"], cur)
                   if pd.notna(returns["dividends"]) and returns["dividends"] > 0 else "None")
    cols[1].metric("Share buybacks", money(returns["buybacks"], cur)
                   if pd.notna(returns["buybacks"]) and returns["buybacks"] > 0 else "None")
    cols[2].metric("Dividend payout ratio",
                   f"{returns['payout']:.0%}" if returns["payout"] is not None else "n/a",
                   help="Dividends as a share of profit. " + returns["payout_text"].capitalize())
    st.caption(f"{'Last 12 months' if n['quarterly'] else 'Latest financial year'}, from the "
               "cash flow statement in the company's SEC filings. Buybacks return cash by "
               "reducing the number of shares, which raises each remaining share's slice "
               "of profit.")

    left, right = st.columns(2)
    if n["quarters"][["Dividends paid", "Share buybacks"]].notna().any().any():
        left.altair_chart(returns_chart(n["quarters"], cur, n["quarterly"]), width="stretch")
    if market is not None and not market["dividends_by_year"].empty:
        right.altair_chart(year_bars(market["dividends_by_year"],
                                     f"Dividend per share by year ({market['currency']})",
                                     "Dividend per share"), width="stretch")
        if market["dividends_look_stale"]:
            right.caption("Yahoo's dividend history for this company looks incomplete "
                          "(no recent payments recorded), so recent years may be missing.")
        elif market["foreign"]:
            right.caption("Per US-listed share (ADR).")

# --- Tab 3: peers -------------------------------------------------------------------
with peers_tab:
    st.markdown("Compare with similar companies: the same measures side by side, plus the "
                "peer median. Ratios are used throughout, so companies reporting in "
                "different currencies can be compared.")
    suggested = suggest_peers(n["cik"], n["sic"], not n["quarterly"])
    options = [f"{c['ticker']} – {c['name']}" for c in all_companies()
               if c["ticker"] != n["ticker"]]
    chosen = st.multiselect(
        "Peers", options, default=[f"{t} – {name}" for t, name in suggested],
        max_selections=8, accept_new_options=True,
        placeholder="Search for a company, or type a ticker",
        help="Suggested automatically: companies in the same SEC industry"
             + (f" ({n['industry']})" if n["industry"] else "")
             + " and of similar size. Add or remove any.")
    peer_tickers = [p.split(" – ")[0].strip().upper() for p in chosen]

    key = (n["ticker"], tuple(peer_tickers))
    if st.button("Compare", type="primary", disabled=not peer_tickers):
        progress = st.progress(0.0, text="Loading peers...")
        table, failed = compare(
            n, get_quote(n["ticker"], not n["quarterly"]), peer_tickers, load=load_numbers,
            on_progress=lambda i, t: progress.progress(i / len(peer_tickers),
                                                       text=f"Loading {t}..."))
        progress.empty()
        st.session_state.peers = {"key": key, "table": table, "failed": failed}

    result = st.session_state.get("peers")
    if result and result["key"][0] == n["ticker"]:
        table = result["table"]
        if result["key"] != key:
            st.caption("The peer list has changed: press **Compare** to update.")
        kinds = dict(PEER_COLUMNS[n["is_bank"]])
        formats = {c: PEER_FORMATS.get(kinds.get(c), "{:,.1f}")
                   for c in table.columns if c not in ("Ticker", "Company")}

        def bold_rows(row):
            """Bold the company being analysed and the peer median."""
            style = "font-weight: bold" if row.name in (0, len(table) - 1) else ""
            return [style] * len(row)

        # Format each figure as text ourselves, so missing ones always read "n/a".
        shown = table.copy()
        for column, fmt in formats.items():
            shown[column] = [fmt.format(v) if pd.notna(v) else "n/a" for v in table[column]]
        st.dataframe(shown.style.apply(bold_rows, axis=1), hide_index=True, width="stretch")
        st.caption("Market value is shown for US-dollar listings. 'Red flags' counts the "
                   "amber and red checks from each company's own red-flag scan. Valuations "
                   "and share prices from Yahoo Finance; everything else from SEC filings.")
        if result["failed"]:
            st.caption("Couldn't load: " + "; ".join(result["failed"]))

        lines = peer_summary(table, n["is_bank"])
        if lines:
            st.subheader(f"How {n['ticker']} compares")
            st.markdown("\n".join(f"- {line}" for line in lines))
            st.caption("✅ / ⚠️ mark measures where higher or lower is usually better. "
                       "Valuation multiples have no 'better' direction: a high P/E can mean "
                       "the market expects strong growth, or that the shares are expensive.")

        metric = st.selectbox("Chart a measure", [c for c, _ in PEER_COLUMNS[n["is_bank"]]
                                                  if table[c].notna().sum() > 1])
        data = table[["Ticker", "Company", metric]].dropna()
        data["Label"] = [t or "Median" for t in data["Ticker"]]
        data["Who"] = ["This company" if i == 0 else "Peer median" if i == len(table) - 1
                       else "Peer" for i in data.index]
        st.altair_chart(alt.Chart(data, title=metric).mark_bar(
            cornerRadiusEnd=4, height={"band": 0.7}).encode(
            y=alt.Y("Label:N", sort="-x", title=None),
            x=alt.X(f"{metric}:Q", title=None,
                    axis=alt.Axis(format={"pct": ".0%", "pct2": ".2%"}.get(kinds[metric], ",.1f"),
                                  grid=True, gridOpacity=0.3)),
            color=alt.Color("Who:N", scale=alt.Scale(
                domain=["This company", "Peer", "Peer median"],
                range=[SERIES[1], SERIES[0], "#a3a29d"]),
                legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("Company:N"),
                     alt.Tooltip(f"{metric}:Q", format={"pct": ".1%", "pct2": ".2%"}
                                 .get(kinds[metric], ",.2f"))],
        ).properties(height=40 * len(data) + 40), width="stretch")

# --- Tab 4: DCF valuation ------------------------------------------------------------
with dcf_tab:
    st.markdown("A **discounted cash flow (DCF)** model values a business by the cash it "
                "will generate in future, discounted back to today. Move the sliders to "
                "test different assumptions.")
    st.caption("An illustrative model for learning and discussion, not a valuation or a "
               "recommendation. The answer depends entirely on the assumptions.")

    if n["is_bank"]:
        st.info("A DCF doesn't suit banks: their 'cash flow' mostly reflects customers "
                "moving deposits and loans, and debt is their raw material rather than "
                "funding. Banks are usually valued on price-to-book against return on "
                "equity, or on their dividends. See the bank measures in the Numbers and "
                "Peers tabs.")
    elif not n["quarterly"]:
        st.info("The DCF is available for US companies. For foreign companies, the "
                "accounts are in their home currency while the US-listed share (ADR) is "
                "priced in dollars and may represent several home-market shares, so a "
                "per-share value can't be compared reliably with the price.")
    elif pd.isna(n["now"]["Revenue"]) or n["now"]["Revenue"] <= 0:
        st.info("A DCF needs a revenue history to project forward, and this company "
                "doesn't report revenue yet.")
    else:
        if n["sic"] and 6000 <= n["sic"] < 6500:
            st.warning("This is a financial company (insurer, broker or finance firm). A "
                       "DCF is less suitable here, because cash flows mix the company's own "
                       "money with customers' money. Treat the result with extra caution.",
                       icon=":material/warning:")
        base = default_assumptions(n, market)
        prefix = f"dcf_{n['ticker']}_"
        defaults = {"growth": base["growth"] * 100, "margin": base["margin"] * 100,
                    "terminal": base["terminal_growth"] * 100,
                    "rate": base["discount_rate"] * 100}
        for k, v in defaults.items():
            st.session_state.setdefault(prefix + k, round(v * 4) / 4)  # nearest 0.25

        def reset():
            for k, v in defaults.items():
                st.session_state[prefix + k] = round(v * 4) / 4

        st.subheader("Assumptions")
        left, right = st.columns(2)
        growth = left.slider(
            "Revenue growth per year, years 1-5", -10.0, 40.0, step=0.25, format="%.2f%%",
            key=prefix + "growth",
            help=f"Starts at the last 12 months' growth ({base['growth']:.1%}), limited "
                 "to -5% to 25%.") / 100
        margin = left.slider(
            "Free cash flow margin", -20.0, 60.0, step=0.25, format="%.2f%%",
            key=prefix + "margin",
            help=f"Free cash flow as a share of revenue. Starts at the last 12 months' "
                 f"figure ({base['margin']:.1%}).") / 100
        terminal = right.slider(
            "Long-term growth after year 5", 0.0, 4.0, step=0.25, format="%.2f%%",
            key=prefix + "terminal",
            help="Growth assumed every year after year 5, forever. Usually close to "
                 "long-run inflation (2-3%): nothing outgrows the economy for ever.") / 100
        rate = right.slider(
            "Discount rate (WACC)", 5.0, 15.0, step=0.25, format="%.2f%%",
            key=prefix + "rate",
            help=f"The return investors require for the risk. Starts at the weighted "
                 f"average cost of capital: cost of equity {base['cost_of_equity']:.1%} "
                 f"(risk-free {RISK_FREE:.2%} + beta {base['beta']:.2f} × equity risk "
                 f"premium {EQUITY_PREMIUM:.0%}), blended with the cost of debt.") / 100
        st.button("Reset to starting assumptions", on_click=reset)

        args = (base["revenue"], growth, margin, terminal, rate, base["net_debt"],
                base["shares"])
        result = run_dcf(*args)
        if result is None:
            st.error("The discount rate must be higher than the long-term growth rate: "
                     "otherwise the value of cash flows 'for ever' never stops growing.")
        else:
            price = market["price"] if market else None
            per_share = result["per_share"]
            st.subheader("Result")
            cols = st.columns(4)
            cols[0].metric("Model value per share",
                           f"${per_share:,.2f}" if per_share is not None else "n/a",
                           delta=(f"{per_share / price - 1:+.1%} vs price"
                                  if per_share is not None and price else None),
                           delta_color="off",
                           help=f"Current share price: ${price:,.2f}" if price else None)
            cols[1].metric("Enterprise value", money(result["enterprise_value"], cur),
                           help="Value of the whole business: today's value of the cash "
                                "flows in years 1-5 plus the terminal value.")
            cols[2].metric("Equity value", money(result["equity_value"], cur),
                           help="Enterprise value minus net debt (or plus net cash): "
                                "what's left for shareholders.")
            cols[3].metric("From the terminal value",
                           f"{result['terminal_share']:.0%}" if result["terminal_share"]
                           else "n/a",
                           help="The share of the value coming from years after year 5. "
                                "Typically 60-80%, which is why the long-term "
                                "assumptions matter so much.")

            implied = implied_growth(price, base["revenue"], margin, terminal, rate,
                                     base["net_debt"], base["shares"]) if price else None
            if implied is not None:
                st.info(f"**What the share price implies (reverse DCF):** with your other "
                        f"assumptions, today's price of ${price:,.2f} is consistent with "
                        f"revenue growth of **{implied:.1%} a year** for five years. Its "
                        f"recent growth was {base['growth']:.1%}. Whether that looks "
                        "achievable is the real question a DCF helps you ask.",
                        icon=":material/lightbulb:")

            left, right = st.columns(2)
            projection = result["projection"].copy()
            projection["Year"] = projection["Year"].map(lambda y: f"Year {y}")
            chart_data = projection.melt(id_vars="Year",
                                         value_vars=["Free cash flow", "Present value"],
                                         var_name="Measure", value_name="Amount")
            chart_data["Amount"] = chart_data["Amount"] / 1e9
            names = ["Free cash flow", "Present value"]
            left.altair_chart(alt.Chart(
                chart_data, title=f"Projected free cash flow ({cur} bn)").mark_bar(
                cornerRadiusEnd=4).encode(
                x=alt.X("Year:N", title=None, axis=alt.Axis(labelAngle=0)),
                xOffset=alt.XOffset("Measure:N", sort=names),
                y=alt.Y("Amount:Q", title=None, axis=alt.Axis(grid=True, gridOpacity=0.3)),
                color=alt.Color("Measure:N", scale=alt.Scale(domain=names, range=SERIES[:2]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=[alt.Tooltip("Year:N"), alt.Tooltip("Measure:N"),
                         alt.Tooltip("Amount:Q", format=",.1f")],
            ).properties(height=260), width="stretch")
            right.caption("'Present value' is each year's cash flow discounted to today: "
                          "cash further in the future is worth less.")
            shown = projection.set_index("Year")[["Revenue", "Free cash flow",
                                                  "Present value"]] / 1e9
            right.dataframe(shown.style.format("{:,.1f}"), width="stretch")
            right.caption(f"{cur} bn. Terminal value: {money(result['terminal_value'], cur)}, "
                          f"worth {money(result['terminal_pv'], cur)} today.")

            st.subheader("Sensitivity: value per share")
            st.caption("Rows: discount rate. Columns: long-term growth. The centre is your "
                       "chosen assumptions. Small changes move the answer a lot.")
            grid = sensitivity(*args)

            def highlight_centre(frame):
                styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
                styles.iloc[2, 2] = "font-weight: bold; text-decoration: underline"
                return styles

            grid = grid.map(lambda v: f"${v:,.2f}" if pd.notna(v) else "n/a")
            st.dataframe(grid.style.apply(highlight_centre, axis=None), width="stretch")

        with st.expander("How this model works, and its simplifications"):
            st.markdown(f"""
1. **Revenue** starts from the last 12 months ({money(base['revenue'], cur)}) and grows at
   your chosen rate for 5 years.
2. **Free cash flow** each year = revenue × your FCF margin. Free cash flow here is
   operating cash flow minus capital spending, a simple stand-in for the "unlevered"
   cash flow a full model would build from operating profit.
3. **Terminal value** = year 5 free cash flow × (1 + long-term growth) ÷ (discount rate −
   long-term growth): the value of every year after year 5.
4. **Enterprise value** = all of these discounted to today. **Equity value** = enterprise
   value − net debt ({money(base['net_debt'], cur)}). **Per share** = equity value ÷
   {f"{base['shares'] / 1e9:,.2f}bn" if base['shares'] else "the number of"} shares.

**Simplifications:** one growth rate for all 5 years; a constant margin; the discount
rate uses fixed market assumptions (risk-free rate {RISK_FREE:.2%}, equity risk premium
{EQUITY_PREMIUM:.0%}) and Yahoo's beta, limited to 0.8-2.0. Real models forecast each line
of the accounts and vary these over time.
""")

# --- Tab 5: AI report ------------------------------------------------------------
with ai_tab:
    if n["quarterly"]:
        st.markdown("Claude reads the last two earnings releases and the latest 10-K/10-Q, "
                    "then writes: results summary, management tone, red flags explained, "
                    "analyst Q&A prep, company brief and interview questions.")
        st.caption("Takes 2-3 minutes and uses your API credit (about $0.60-0.80 per report).")
    else:
        st.markdown("Claude reads the latest results announcements and the annual report "
                    "(20-F), then writes: results summary, management tone, red flags "
                    "explained, analyst Q&A prep, company brief and interview questions.")
        st.caption("Foreign annual reports are very long, so only the first part is read if "
                   "needed (the report says so). Takes 2-4 minutes and uses your API credit "
                   "(about $0.70-1.20 per report).")

    # On the public site, running a report needs either the owner's password
    # or the visitor's own Claude API key, so visitors can't spend the owner's
    # credit. With no password set (e.g. on your own computer), the button
    # just works with the key in .env.
    password = os.environ.get("APP_PASSWORD", "")
    unlocked, own_key = not password, None
    if password:
        st.caption("Each report costs API credit, so writing one needs a password or your "
                   "own Claude API key. The **Saved reports** tab has finished examples to "
                   "read for free.")
        use_own = st.radio("How do you want to run it?", ["Use my own Claude API key",
                                                          "I have the password"],
                           horizontal=True, label_visibility="collapsed")
        if use_own == "I have the password":
            typed = st.text_input("Password", type="password")
            unlocked = hmac.compare_digest(typed.encode(), password.encode())
            if typed and not unlocked:
                st.error("Wrong password.")
        else:
            own_key = st.text_input(
                "Your Claude API key", type="password", placeholder="sk-ant-...",
                help="Get one at console.anthropic.com. Your key is used only to write this "
                     "report: it isn't saved, logged or shared, and the cost goes on your "
                     "own Anthropic account.").strip() or None
            st.caption("Your key is used only for this report and is never saved. Reports "
                       "written with your own key aren't added to the public Saved reports.")
            if own_key and not own_key.startswith("sk-ant-"):
                st.error("That doesn't look like a Claude API key (they start with sk-ant-).")
                own_key = None
            unlocked = own_key is not None

    if st.button(f"Write AI report on {n['ticker']}", type="primary", disabled=not unlocked):
        try:
            with st.status("Downloading filings from the SEC...") as status:
                documents = get_documents(n)
                for d in documents:
                    st.write(f"{d['title']} ({len(d['text']):,} characters)")
                status.update(label="Filings downloaded", state="complete")

            output = st.empty()
            streamed = []

            def show(text):
                streamed.append(text)
                if len(streamed) % 25 == 0:  # refresh the page every few words
                    output.markdown("".join(streamed))

            with st.spinner("Claude is writing the report..."):
                report, usage, cost = run_ai_report(n_ai, documents, on_text=show,
                                                    api_key=own_key)
            output.empty()
            # Reports paid for with a visitor's own key stay private to them:
            # they can download it, but it isn't added to the shared folder.
            if own_key:
                saved_as = None
            else:
                saved_as = save_report(n_ai, documents, report).name
            st.session_state.report = {
                "ticker": n["ticker"], "text": report, "cost": cost, "saved_as": saved_as,
                "markdown": report_markdown(n_ai, documents, report),
                "tokens": (usage.input_tokens, usage.output_tokens)}
        except Exception as error:
            st.error(explain_error(error))

    saved = st.session_state.get("report")
    if saved and saved["ticker"] == n["ticker"]:
        st.success(f"Done: {saved['tokens'][0]:,} tokens in, {saved['tokens'][1]:,} out "
                   f"(about ${saved['cost']:.2f})."
                   + (f" Saved as {saved['saved_as']}." if saved["saved_as"] else ""))
        st.download_button("Download report (.md)", saved["markdown"].encode("utf-8"),
                           file_name=f"{n['ticker']}_report.md", mime="text/markdown")
        st.markdown(saved["text"])

# --- Tab 6: saved reports --------------------------------------------------------
with saved_tab:
    # Your own reports first, then the examples that come with the project.
    files = [p for folder in (REPORTS_FOLDER, EXAMPLES_FOLDER) if folder.exists()
             for p in sorted(folder.glob("*.md"), reverse=True)]
    if not files:
        st.info("No saved reports yet. Write one in the AI report tab.")
    else:
        choice = st.selectbox("Choose a report", files, format_func=lambda p: (
            f"{p.stem} (example)" if p.parent == EXAMPLES_FOLDER else p.stem))
        st.download_button("Download", choice.read_bytes(), file_name=choice.name,
                           mime="text/markdown")
        st.markdown(choice.read_text(encoding="utf-8"))

footer()
