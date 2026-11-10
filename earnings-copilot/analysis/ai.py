"""
The AI analyst: sends the filings and our calculated numbers to Claude and
gets back the written sections of the report.

Division of labour: Python does all the maths (data/sec.py, analysis/metrics.py,
analysis/red_flags.py). The AI only reads, compares and explains, and is told
to use only figures from the numbers and documents we give it.
"""

import os

import anthropic

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are a senior equity research analyst preparing a briefing \
pack on a listed company for an investment professional.

Rules:
- Use only figures that appear in the calculated numbers or the documents provided. \
Never use figures from memory. If something isn't covered, say so.
- When you quote a figure or a statement, say which document it came from.
- If you work out a figure yourself (e.g. a ratio from two reported numbers), label \
it "(my calculation)". Never present your own calculation as the company's reported \
figure, especially where the company reports its own version of that measure.
- If the documents give different figures for the same item (e.g. cash paid in the \
cash flow statement vs the amount in the narrative), use one consistently and \
briefly explain the difference.
- Use the company's reporting currency, as stated in the calculated numbers.
- For banks, cover what matters for a bank: net interest margin, capital ratios \
(e.g. CET1) and how they compare with requirements, credit quality (impairments, \
non-performing loans), funding and liquidity, and returns. Take capital ratios \
from the documents.
- If a document is marked as truncated, mention once that later parts of it were \
not reviewed, and don't guess at what they contain.
- The calculated numbers were produced by code from the company's official SEC data. \
Treat them as correct.
- Be balanced: give the bull and bear reading where it matters, and say when a \
red flag has an innocent explanation in the documents.
- Write in clear UK English for a finance graduate. Be concise: no filler."""

REPORT_INSTRUCTIONS = """Write a briefing pack on {name} ({ticker}) in Markdown, \
with exactly these sections:

## 1. Results summary
The key takeaways from the latest results in 5-7 bullet points: growth, margins, \
cash flow, guidance, and anything unusual.

## 2. Management tone: latest results vs the previous ones
Compare the two most recent earnings releases or results announcements. How has \
the language changed? Note anything more cautious or more confident, topics that \
appeared or disappeared, and any change to guidance. Quote short phrases as \
evidence. If only one is provided (e.g. a newly listed company), say so and assess \
the tone of that release and the periodic report instead.

## 3. Red flags explained
For each AMBER or RED flag in the calculated red-flag scan, look for the \
explanation in the documents and say whether it looks concerning or innocent. \
Then scan the documents for anything the numbers can't catch: going-concern \
doubts, material weaknesses in internal controls, auditor issues, restatements, \
large legal cases, big gaps between adjusted and statutory profit, or one-off \
charges. Say plainly if nothing significant is found.

## 4. Analyst Q&A prep
The 8 questions analysts are most likely to ask on the next earnings call, most \
likely first. For each, a draft answer management could give using only their \
own disclosures, with the source.

## 5. Company brief
What the company does, how it makes money (main segments or products), its \
strategy, and the main risks, in under 250 words.

## 6. Questions to ask in an interview
5 thoughtful questions a candidate could ask someone who works at or covers this \
company, each grounded in something specific from these results.

CALCULATED NUMBERS AND RED-FLAG SCAN:
{numbers}"""


def explain_error(error):
    """Turn an API error into a plain-English message."""
    if isinstance(error, anthropic.AuthenticationError):
        return "The Claude API key was rejected. Check it was copied in full and is still active."
    if isinstance(error, anthropic.PermissionDeniedError):
        return "Your API key doesn't have permission to use this model."
    if isinstance(error, anthropic.RateLimitError):
        return "Too many requests to Claude right now. Wait a minute and try again."
    if isinstance(error, anthropic.BadRequestError) and "credit" in str(error).lower():
        return ("The API key's credit balance is too low. Add credit at "
                "console.anthropic.com under Billing.")
    if isinstance(error, anthropic.APIConnectionError):
        return "Couldn't reach Claude. Check your internet connection."
    if isinstance(error, anthropic.APIStatusError):
        return f"Claude API error ({error.status_code}): {error.message}"
    return str(error)


def write_report(name, ticker, numbers, documents, on_text=None, api_key=None):
    """
    Ask Claude to write the report. `documents` is a list of
    {"title": ..., "text": ...}. Text is streamed to `on_text` as it arrives.
    `api_key` is a visitor's own key; without it, the owner's key from the
    environment is used. Returns the full report and the token usage.
    """
    if api_key:
        # A visitor's key: used for this one request only, never stored.
        client = anthropic.Anthropic(api_key=api_key)
    elif os.environ.get("ANTHROPIC_API_KEY"):
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    else:
        raise RuntimeError("No Claude API key found. Copy .env.example to a file called .env "
                           "and paste your key after ANTHROPIC_API_KEY=")

    content = [
        {"type": "document", "title": doc["title"],
         "source": {"type": "text", "media_type": "text/plain", "data": doc["text"]}}
        for doc in documents
    ]
    content.append({"type": "text", "text": REPORT_INSTRUCTIONS.format(
        name=name, ticker=ticker, numbers=numbers)})

    # Streaming shows the report as it's written. "fallbacks" means that if the
    # model declines a request, the API retries it on another Claude model.
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        for text in stream.text_stream:
            if on_text:
                on_text(text)
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to write this report.")
    report = "".join(block.text for block in response.content if block.type == "text")
    return report, response.usage
