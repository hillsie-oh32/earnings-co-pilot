# 📊 Earnings Copilot

**An AI-assisted equity research tool that turns a company's official filings into an analyst-style briefing pack in about three minutes.**

**[▶ Try the live site](https://earnings-co-pilot-wetfsnutab3gtqugzjveai.streamlit.app/)** · **[Full documentation](earnings-copilot/README.md)** · **[Example report: Apple](earnings-copilot/examples/AAPL_2026-11-10.md)** · **[Example report: HSBC](earnings-copilot/examples/HSBC_2026-11-10.md)**

---

Type a company name or ticker, and Earnings Copilot:

1. **Downloads the company's figures and filings from the SEC:** US, UK, European, Japanese and Chinese companies, in their own currency.
2. **Calculates the key numbers in Python:** margins, growth, free cash flow, net debt, and an **accounting red-flag scan** including the Beneish M-Score. Banks get their own view: efficiency, return on equity, loan losses and funding.
3. **Has Claude (Anthropic's AI) read the actual documents** and write a briefing pack: results summary, change in management tone, red flags explained, likely analyst questions with draft answers, a company brief, and interview questions.

**The key design choice:** the code does all the maths from official data, and the AI only reads and explains, citing its sources. The AI's output was checked against the original filings, and the tool was stress-tested on 73 companies across six regions.

Built with Python, pandas, Streamlit and the Claude API. The code is in the [`earnings-copilot`](earnings-copilot) folder; see its [README](earnings-copilot/README.md) for how it works, design decisions, limitations and how to run it.

*Built by a second-year Accounting & Finance student. Not investment advice.*
