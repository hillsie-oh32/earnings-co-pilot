"""
Week 3: the full Earnings Copilot report.

Combines the calculated numbers and red flags (weeks 1-2) with the AI's
reading of the actual filings, and saves the result to the reports folder.

Run it with:   python week3.py AAPL
"""

import sys

from analysis.ai import explain_error
from analysis.report import build_numbers, get_documents, run_ai_report, save_report
from data.sec import CompanyNotSupported

ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"

print(f"Fetching financials for {ticker.upper()}...")
try:
    numbers = build_numbers(ticker)
except CompanyNotSupported as error:
    sys.exit(str(error))
for warning in numbers["warnings"]:
    print(f"WARNING: {warning}")

print("Downloading filings...")
documents = get_documents(numbers)
for d in documents:
    print(f"  - {d['title']} ({len(d['text']):,} characters)")

print("\nWriting the report (this takes a minute or two)...\n")
try:
    report, usage, cost = run_ai_report(numbers, documents,
                                        on_text=lambda t: print(t, end="", flush=True))
except Exception as error:
    sys.exit(f"\n{explain_error(error)}")
print(f"\n\nTokens: {usage.input_tokens:,} in, {usage.output_tokens:,} out "
      f"(about ${cost:.2f})")

path = save_report(numbers, documents, report)
print(f"Saved to {path}")
