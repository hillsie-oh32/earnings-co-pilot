"""
The Earnings Copilot website.

Run it with:   streamlit run app.py
It opens in your browser at http://localhost:8501
"""

import hmac
import os

import altair as alt
import pandas as pd
import streamlit as st

from analysis.ai import explain_error
from analysis.report import (build_numbers, get_documents, run_ai_report, save_report,
                             report_markdown, REPORTS_FOLDER, EXAMPLES_FOLDER)
from data.sec import CompanyNotSupported

st.set_page_config(page_title="Earnings Copilot", page_icon="📊", layout="wide")

# Chart colours: a colour-blind-safe palette, with darker-background versions.
DARK = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
SERIES = ["#3987e5", "#d95926", "#199e70"] if DARK else ["#2a78d6", "#eb6834", "#1baf7a"]

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
3. **Has Claude (Anthropic's AI) read the actual documents** and write a briefing pack:
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
covered; red flags are prompts to look closer, not verdicts. **Not investment advice.**

Built by a second-year Accounting & Finance student with Python, pandas, Streamlit and
the Claude API. **[View the code and full documentation on GitHub]({GITHUB})**
"""

SYMBOLS = {"USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "CNY": "CN¥", "CHF": "CHF ",
           "DKK": "DKK ", "SEK": "SEK ", "CAD": "C$", "AUD": "A$", "HKD": "HK$"}


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


def choose(query):
    st.session_state.query = query
    st.session_state.pop("report", None)


# --- Page ---------------------------------------------------------------------
st.title("📊 Earnings Copilot")
st.caption("Official SEC filings, analysed by code and AI. Covers US-listed companies, "
           "including UK, European, Japanese and Chinese companies with US-listed shares.")

with st.form("search"):
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    query = left.text_input("Company name or ticker", value="Apple",
                            help="e.g. Apple, AAPL, JP Morgan, HSBC, Toyota, SAP, BRK.B")
    if right.form_submit_button("Analyse", type="primary", width="stretch"):
        choose(query)

# About the project: open on the home page, folded away once a company is shown.
with st.expander("ℹ️ About this project", expanded="query" not in st.session_state):
    st.markdown(ABOUT)

if "query" not in st.session_state:
    st.info("Enter a company name or ticker above and press **Analyse**.")
    st.stop()

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
st.header(f"{n['name']} ({n['ticker']})")
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

numbers_tab, ai_tab, saved_tab = st.tabs(["Numbers & red flags", "AI report", "Saved reports"])

# --- Tab 1: the numbers ----------------------------------------------------------
with numbers_tab:
    period_word = "quarter" if n["quarterly"] else "year"
    st.subheader("Last 12 months" if n["quarterly"] else "Latest financial year")
    now, ago = n["now"], n["ago"]
    extra = [] if n["is_bank"] else ["Net debt"]
    cols = st.columns(len(n["headlines"]) + len(extra))
    for col, item in zip(cols, n["headlines"]):
        change = (now[item] / ago[item] - 1
                  if pd.notna(now[item]) and pd.notna(ago[item]) and ago[item] else None)
        col.metric(item, money(now[item], cur),
                   delta=f"{change:+.1%} vs prior year" if change is not None else None,
                   delta_color="inverse" if item == "Credit loss provisions" else "normal")
    if extra:
        net_debt = now["Net debt"]
        cols[-1].metric("Net debt" if pd.isna(net_debt) or net_debt > 0 else "Net cash",
                        money(abs(net_debt), cur) if pd.notna(net_debt) else "n/a")

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
    st.caption(f"{'Last 12 months' if n['quarterly'] else 'Latest year'} vs the year before. "
               "A flag is a reason to look closer, not proof of a problem.")
    for flag in n["flags"]:
        badge, check, detail = st.columns([1, 2, 5])
        badge.markdown(BADGES[flag["status"]])
        check.markdown(f"**{flag['check']}**")
        if flag["value"] != "-":
            check.text(flag["value"])
        with detail:
            st.markdown(flag["detail"])
            if flag["status"] in ("RED", "AMBER") or n["is_bank"]:
                st.caption(flag["why"])
    st.markdown("**Filing history (last 3 years):** " +
                ("; ".join(n["history"]) if n["history"]
                 else "no auditor changes, restatements or late filings ✅"))

# --- Tab 2: AI report ------------------------------------------------------------
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
                report, usage, cost = run_ai_report(n, documents, on_text=show,
                                                    api_key=own_key)
            output.empty()
            # Reports paid for with a visitor's own key stay private to them:
            # they can download it, but it isn't added to the shared folder.
            if own_key:
                saved_as = None
            else:
                saved_as = save_report(n, documents, report).name
            st.session_state.report = {
                "ticker": n["ticker"], "text": report, "cost": cost, "saved_as": saved_as,
                "markdown": report_markdown(n, documents, report),
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

# --- Tab 3: saved reports --------------------------------------------------------
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
