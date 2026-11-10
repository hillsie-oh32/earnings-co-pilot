# 📊 Earnings Copilot

**An AI-assisted equity research tool that turns a company's official filings into an analyst-style briefing pack in about three minutes.**

Type a company name or ticker. The tool downloads the company's financial data and filings from the SEC, calculates the key figures and runs an accounting red-flag scan in Python, then has Claude (Anthropic's AI model) read the actual documents and write a six-part briefing pack.

**[▶ Live demo](#)** · **[Example report: Apple](examples/AAPL_2026-11-10.md)** · **[Example report: HSBC](examples/HSBC_2026-11-10.md)**

---

## What it produces

**Instantly (no AI, free):**
- Headline figures for the last 12 months (or latest financial year) vs the year before
- Charts of revenue, margins and free cash flow
- An **accounting red-flag scan**: receivables vs revenue, cash conversion, inventory build-up, gross margin trend, cash runway, leverage, goodwill vs equity, and the **Beneish M-Score** for earnings manipulation
- A check of the company's filing history for auditor changes, restatements and late filings

**AI briefing pack (about 3 minutes, $0.60–1.20 of API credit):**
1. **Results summary:** what actually moved, including one-offs that flatter the headline
2. **Management tone:** how the language changed between the last two results announcements
3. **Red flags explained:** the reason behind each flag, from the notes to the accounts
4. **Analyst Q&A prep:** the 8 questions analysts are most likely to ask, with draft answers from the company's own disclosures (what an investor relations team prepares before results day)
5. **Company brief:** business model, segments, strategy and risks
6. **Interview questions:** five questions grounded in the latest results

## Coverage

| | Data | Example companies |
|---|---|---|
| **US companies** | Quarterly | Apple, Microsoft, Nvidia, Kraft Heinz, Costco |
| **Banks** (US and foreign) | Bank-specific view | JPMorgan, Bank of America, HSBC, Barclays, MUFG |
| **UK** | Annual (IFRS) | BP, Shell, Unilever, AstraZeneca, GSK, Diageo |
| **Europe** | Annual (IFRS) | ASML, SAP, Novo Nordisk, Novartis, TotalEnergies, UBS |
| **Japan** | Annual | Toyota, Sony, Honda, Mizuho |
| **China** | Annual (US GAAP) | Alibaba, JD.com, PDD, Baidu, NIO |

Figures are shown in each company's own currency. Company names work as well as tickers ("jp morgan" → JPM, "brk.b" → BRK-B).

**Banks get a different view**, because the usual measures don't apply to a business that lends out its customers' deposits. They are judged on efficiency ratio, return on equity, cost of risk (loan losses as a share of loans), loan-to-deposit ratio, deposit trend and equity cushion. The AI report adds capital ratios (CET1), net interest margin, credit quality and liquidity, read from the filings.

## How it works

```
                 ┌──────────────────────────┐
  "Apple" ─────► │ data/sec.py              │  SEC XBRL data: every figure from
                 │ resolve name, download    │  official filings, US GAAP or IFRS,
                 │ figures, any currency     │  quarterly or annual
                 └────────────┬─────────────┘
                              ▼
                 ┌──────────────────────────┐
                 │ analysis/metrics.py       │  Margins, growth, free cash flow,
                 │ analysis/red_flags.py     │  net debt, bank ratios, red flags.
                 │ (pure Python maths)       │  No AI involved.
                 └────────────┬─────────────┘
                              ▼
                 ┌──────────────────────────┐
                 │ data/filings.py           │  Earnings releases, 10-Q/10-K,
                 │                           │  or 6-K results + 20-F for foreign
                 └────────────┬─────────────┘  companies
                              ▼
                 ┌──────────────────────────┐
                 │ analysis/ai.py (Claude)   │  Reads the documents + calculated
                 │                           │  numbers, writes the briefing pack
                 └────────────┬─────────────┘
                              ▼
                   app.py (Streamlit website)
```

### Key design decisions

- **Code does the maths; the AI does the reading.** AI models are good at reading and explaining but can get numbers wrong. Every figure is calculated in Python from official data, and the AI is told to use only figures from those numbers or the documents, to cite its source, and to label any figure it works out itself.
- **Every output was checked against the filings.** I spot-checked the AI's claims in each report against the source documents (e.g. 10 of 10 for Kraft Heinz). When the HSBC report presented its own calculation (a 47.4% cost efficiency ratio) as HSBC's reported figure (46%), I changed the instructions to prevent it.
- **Honest about gaps.** If a figure isn't in the data, the tool shows "n/a" rather than guessing. It warns when the SEC's data is out of date, and when a long foreign annual report had to be cut to fit (and the AI says which parts it didn't read).
- **Stress-tested.** Run against 73 companies and inputs across six regions. This found, among other things, a bug that silently showed years-old data for some companies, which was more dangerous than a crash because the output looked believable.

## Limitations

- **Foreign companies show annual figures only.** They file structured data with the SEC once a year. The AI still reads their latest quarterly results announcements.
- **Companies listed only outside the US** (e.g. Tesco, Tokyo-only shares) aren't in the SEC's data. Covering them would need other sources (Companies House, EDINET).
- **Some banks' figures are incomplete** in the SEC data (e.g. Deutsche Bank, Santander), so some bank checks show "n/a". Regulatory capital ratios come from the AI reading the filings, not from the structured data.
- **Red flags are prompts to look closer, not verdicts.** The Beneish M-Score, for example, flags fast-growing companies like Nvidia (score −0.40) even without manipulation, because the model was built on slower-growing firms.
- **Not investment advice.** This is a research and learning tool.

## Running it yourself

You'll need Python 3.11+ and a Claude API key from [console.anthropic.com](https://console.anthropic.com) (only for the AI reports).

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your API key and email address. Then:

```bash
python -m streamlit run app.py
```

It opens at http://localhost:8501. There's also a command-line version: `python week3.py AAPL`.

## Project structure

```
app.py                 The website (Streamlit)
config.py              Settings, read from .env
data/sec.py            Financial figures from the SEC; name → ticker matching
data/filings.py        Filing text: earnings releases, 10-K/10-Q, 6-K, 20-F
analysis/metrics.py    Margins, growth, cash flow, bank ratios
analysis/red_flags.py  Accounting red flags and bank checks
analysis/ai.py         The instructions to Claude and the API call
analysis/report.py     Ties it all together
examples/              Finished example reports
week1.py ... week3.py  The build stages, kept as simple command-line scripts
```

## Built with

Python · pandas · Streamlit · Altair · SEC EDGAR APIs · Claude API (Anthropic)

---

*Built by a second-year Accounting & Finance student, with AI-assisted development (Claude Code). Every design choice, test and fix in this project is explained in the code comments.*
