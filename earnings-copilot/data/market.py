"""
Share price and market data from Yahoo Finance (via the yfinance library).

Unlike the SEC data, this is an unofficial source: it's free and needs no key,
but Yahoo sometimes limits requests, especially from cloud servers. So every
call here can fail with MarketDataUnavailable, and the rest of the tool works
without it.

Foreign companies trade in the US as ADRs (American Depositary Receipts),
where one US share can represent several home-market shares. Some of Yahoo's
ratios mix up the two (e.g. price-to-book for Toyota), so for foreign
companies we only use figures that are consistent: price, market value,
P/E ratios and dividend yield.
"""

import time

import pandas as pd
import yfinance as yf

BENCHMARK = "SPY"  # an S&P 500 tracker fund, used as "the market"


class MarketDataUnavailable(RuntimeError):
    """Raised when Yahoo Finance can't be reached or has no data."""


def _retry(fetch, attempts=2, pause=2.0):
    """Try a Yahoo request again once after a short pause if it's refused."""
    for attempt in range(attempts):
        try:
            return fetch()
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(pause)


def _price_history(ticker, period="10y"):
    """Daily prices, with dividends paid, for up to 10 years."""
    history = _retry(lambda: yf.Ticker(ticker).history(period=period, auto_adjust=False))
    if history.empty:
        raise MarketDataUnavailable(f"No share price history found for {ticker}.")
    history.index = history.index.tz_localize(None).normalize()
    return history


_benchmark = {"fetched": 0.0, "prices": None}


def _benchmark_prices():
    """S&P 500 prices, downloaded at most once an hour and shared by all companies."""
    if _benchmark["prices"] is None or time.time() - _benchmark["fetched"] > 3600:
        _benchmark["prices"] = _price_history(BENCHMARK, "5y")["Close"]
        _benchmark["fetched"] = time.time()
    return _benchmark["prices"]


def _optional(fetch):
    """Extra data (analyst ratings, earnings dates): None if it can't be fetched."""
    try:
        return _retry(fetch)
    except Exception:
        return None


def get_market_data(ticker, foreign=False):
    """
    Share price, valuation and dividend information for a ticker.
    `foreign` is True for ADRs, to leave out ratios that are unreliable for them.
    """
    try:
        stock = yf.Ticker(ticker)
        info = _retry(lambda: stock.info) or {}
        history = _price_history(ticker)
        benchmark = _benchmark_prices()
    except MarketDataUnavailable:
        raise
    except Exception as error:
        raise MarketDataUnavailable(
            "Market data is unavailable right now (Yahoo Finance may be limiting requests). "
            f"Details: {error}") from None
    ratings = _optional(lambda: stock.recommendations)
    earnings_dates = _optional(lambda: stock.get_earnings_dates(limit=16))
    # The price history includes each dividend paid, so no separate request.
    dividends = history["Dividends"][history["Dividends"] > 0] \
        if "Dividends" in history else None

    close = history["Close"]
    # Charts show up to 5 years; the extra history is only for dividends.
    close = close[close.index >= close.index[-1] - pd.DateOffset(years=5)]
    price = info.get("currentPrice") or float(close.iloc[-1])
    previous = info.get("previousClose") or float(close.iloc[-2])

    def total_return(series, days):
        """Price change over the last `days` calendar days."""
        start = series[series.index >= series.index[-1] - pd.Timedelta(days=days)]
        return float(start.iloc[-1] / start.iloc[0] - 1) if len(start) > 1 else None

    # Dividends per share, added up by calendar year.
    if dividends is not None and not dividends.empty:
        by_year = dividends.groupby(dividends.index.year).sum().tail(10)
        last_dividend = dividends.index[-1]
    else:
        by_year, last_dividend = pd.Series(dtype=float), None

    def positive(key):
        """A ratio from Yahoo, or None if missing or impossible (zero or negative)."""
        value = info.get(key)
        return value if isinstance(value, (int, float)) and value > 0 else None

    return {
        "currency": info.get("currency", "USD"),
        "price": price,
        "day_change": price / previous - 1 if previous else None,
        "market_cap": positive("marketCap"),
        "pe": positive("trailingPE"),
        "forward_pe": positive("forwardPE"),
        # Yahoo gives dividend yield as a percentage (e.g. 0.3 means 0.3%).
        # None means no dividend.
        "dividend_yield": positive("dividendYield"),
        "price_to_book": None if foreign else positive("priceToBook"),
        "ev_to_ebitda": None if foreign else positive("enterpriseToEbitda"),
        "beta": info.get("beta"),
        "high_52w": info.get("fiftyTwoWeekHigh"),
        "low_52w": info.get("fiftyTwoWeekLow"),
        "return_1y": total_return(close, 365),
        "market_return_1y": total_return(benchmark, 365),
        "prices": close,
        "benchmark": benchmark,
        "dividends_by_year": by_year,
        "last_dividend": last_dividend,
        # Yahoo's dividend history is sometimes incomplete (e.g. some ADRs).
        "dividends_look_stale": bool(
            _pays_dividend(info) and last_dividend is not None
            and (pd.Timestamp.now() - last_dividend).days > 450),
        "analysts": _analyst_view(info, ratings, price),
        **_earnings_scorecard(earnings_dates, close, foreign),
        "foreign": foreign,
    }


def _earnings_scorecard(earnings_dates, close, foreign):
    """
    For each recent results announcement: what analysts expected for earnings
    per share (EPS), what the company reported, whether it beat or missed,
    and how the share price reacted. Plus the next results date.

    Only for US companies: for ADRs, Yahoo often compares figures on
    different bases (per ADR vs per home-market share), producing
    nonsensical "surprises".
    """
    empty = {"scorecard": [], "next_results": None, "next_estimate": None}
    if earnings_dates is None or earnings_dates.empty:
        return empty
    df = earnings_dates.copy()
    df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    df = df[~df.index.normalize().duplicated(keep="last")].sort_index()

    now = pd.Timestamp.now()
    future = df[(df.index > now) & df["Reported EPS"].isna()]
    result = {
        "next_results": future.index[0] if len(future) else None,
        "next_estimate": (None if foreign or not len(future)
                          else _number(future["EPS Estimate"].iloc[0])),
        "scorecard": [],
    }
    if foreign:
        return result

    reported = df[df["Reported EPS"].notna() & df["EPS Estimate"].notna()]
    reported = reported[reported.index > now - pd.DateOffset(years=2, months=3)].tail(8)
    days = close.index
    for when, row in reported.iterrows():
        estimate, actual = float(row["EPS Estimate"]), float(row["Reported EPS"])
        difference = actual - estimate
        # A surprise in % only means something if the estimate is a profit.
        surprise = difference / estimate if estimate > 0.01 else None
        outcome = ("In line" if abs(difference) < 0.005 or
                   (surprise is not None and abs(surprise) < 0.005)
                   else "Beat" if difference > 0 else "Miss")

        # Share price reaction: results released before midday move that
        # day's price; results after the close move the next trading day's.
        react_day = when.normalize() if when.hour < 12 else when.normalize() + pd.Timedelta(days=1)
        after = days[days >= react_day]
        before = days[days < react_day]
        reaction = (float(close[after[0]] / close[before[-1]] - 1)
                    if len(after) and len(before) else None)
        result["scorecard"].append({"date": when, "estimate": estimate, "actual": actual,
                                    "surprise": surprise, "outcome": outcome,
                                    "reaction": reaction})
    return result


def _number(x):
    return None if pd.isna(x) else float(x)


RATINGS = ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]


def _analyst_view(info, ratings, price):
    """
    What professional analysts say about the share, as collected by Yahoo:
    how many rate it Strong Buy to Strong Sell this month, the consensus,
    and their price targets. None if there's no coverage.
    """
    counts = {}
    if ratings is not None and len(ratings):
        latest = ratings.iloc[0]
        counts = {name: int(latest.get(key, 0) or 0) for name, key in zip(
            RATINGS, ["strongBuy", "buy", "hold", "sell", "strongSell"])}
    total = sum(counts.values())
    if not total and not info.get("targetMeanPrice"):
        return None

    # Consensus score: 1 = Strong Buy ... 5 = Strong Sell. Use Yahoo's if
    # given, otherwise the average of this month's ratings.
    score = info.get("recommendationMean")
    if not score and total:
        score = sum((i + 1) * n for i, n in enumerate(counts.values())) / total
    # Same cut-offs as Yahoo: up to 1.5 is Strong Buy, up to 2.5 Buy, and so on.
    consensus = (None if not score else
                 next(name for name, top in zip(RATINGS, [1.5, 2.5, 3.5, 4.5, 99])
                      if score <= top))

    target = info.get("targetMeanPrice")
    return {"counts": counts, "total": total, "score": score, "consensus": consensus,
            "target": target, "target_low": info.get("targetLowPrice"),
            "target_high": info.get("targetHighPrice"),
            "upside": target / price - 1 if target and price else None}


def _pays_dividend(info):
    """True if Yahoo says the company currently pays a dividend."""
    value = info.get("dividendYield")
    return isinstance(value, (int, float)) and value > 0


def market_as_text(m):
    """Market data written out as text, for the AI report."""
    def pct(x):
        return "n/a" if x is None else f"{x:+.1%}"

    def num(x, fmt="{:.1f}"):
        return "n/a" if x is None else fmt.format(x)

    lines = [
        f"Share price: {m['price']:,.2f} {m['currency']}"
        + (" (US-listed ADR; one ADR may represent several home-market shares)"
           if m["foreign"] else ""),
        f"Market value: {num(m['market_cap'] and m['market_cap'] / 1e9, '{:,.1f}')}bn "
        f"{m['currency']}",
        f"P/E (last 12 months): {num(m['pe'])}; forward P/E: {num(m['forward_pe'])}",
        "Dividend yield: " + (f"{m['dividend_yield']:.2f}%" if m["dividend_yield"]
                              else "none (no dividend)"),
        f"Share price change, last 12 months: {pct(m['return_1y'])} "
        f"(S&P 500: {pct(m['market_return_1y'])})",
        f"52-week range: {num(m['low_52w'], '{:,.2f}')} to {num(m['high_52w'], '{:,.2f}')}",
    ]
    if m["price_to_book"] is not None:
        lines.append(f"Price-to-book: {m['price_to_book']:.1f}")
    if m["ev_to_ebitda"] is not None:
        lines.append(f"EV/EBITDA: {m['ev_to_ebitda']:.1f}")
    if m["scorecard"]:
        beats = sum(r["outcome"] == "Beat" for r in m["scorecard"])
        last = m["scorecard"][-1]
        lines.append(
            f"Earnings vs analysts' estimates: beat in {beats} of the last "
            f"{len(m['scorecard'])} quarters. Latest ({last['date']:%d %b %Y}): EPS "
            f"{last['actual']:.2f} vs estimate {last['estimate']:.2f} ({last['outcome']}); "
            f"share price reaction {pct(last['reaction'])}")
    if m["next_results"] is not None:
        lines.append(f"Next results expected: {m['next_results']:%d %b %Y}"
                     + (f" (EPS estimate {m['next_estimate']:.2f})"
                        if m["next_estimate"] is not None else ""))
    a = m["analysts"]
    if a:
        breakdown = ", ".join(f"{n} {name}" for name, n in a["counts"].items() if n)
        lines.append(
            f"Analyst consensus (as reported by Yahoo Finance, {a['total']} analysts): "
            f"{a['consensus'] or 'n/a'}" + (f" ({breakdown})" if breakdown else "")
            + (f"; average price target {a['target']:,.2f} ({pct(a['upside'])} vs current "
               "price)" if a["target"] else ""))
    return "\n".join(lines)
