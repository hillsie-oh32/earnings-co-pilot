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

import pandas as pd
import yfinance as yf

BENCHMARK = "SPY"  # an S&P 500 tracker fund, used as "the market"


class MarketDataUnavailable(RuntimeError):
    """Raised when Yahoo Finance can't be reached or has no data."""


def _price_history(ticker, period="5y"):
    history = yf.Ticker(ticker).history(period=period, auto_adjust=False)
    if history.empty:
        raise MarketDataUnavailable(f"No share price history found for {ticker}.")
    history.index = history.index.tz_localize(None).normalize()
    return history


def get_market_data(ticker, foreign=False):
    """
    Share price, valuation and dividend information for a ticker.
    `foreign` is True for ADRs, to leave out ratios that are unreliable for them.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}
        history = _price_history(ticker)
        benchmark = _price_history(BENCHMARK)["Close"]
        dividends = stock.dividends
        try:
            ratings = stock.recommendations
        except Exception:
            ratings = None  # analyst data is optional
    except MarketDataUnavailable:
        raise
    except Exception as error:
        raise MarketDataUnavailable(
            "Market data is unavailable right now (Yahoo Finance may be limiting requests). "
            f"Details: {error}") from None

    close = history["Close"]
    price = info.get("currentPrice") or float(close.iloc[-1])
    previous = info.get("previousClose") or float(close.iloc[-2])

    def total_return(series, days):
        """Price change over the last `days` calendar days."""
        start = series[series.index >= series.index[-1] - pd.Timedelta(days=days)]
        return float(start.iloc[-1] / start.iloc[0] - 1) if len(start) > 1 else None

    # Dividends per share, added up by calendar year.
    if dividends is not None and not dividends.empty:
        dividends.index = dividends.index.tz_localize(None)
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
        "foreign": foreign,
    }


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
    a = m["analysts"]
    if a:
        breakdown = ", ".join(f"{n} {name}" for name, n in a["counts"].items() if n)
        lines.append(
            f"Analyst consensus (as reported by Yahoo Finance, {a['total']} analysts): "
            f"{a['consensus'] or 'n/a'}" + (f" ({breakdown})" if breakdown else "")
            + (f"; average price target {a['target']:,.2f} ({pct(a['upside'])} vs current "
               "price)" if a["target"] else ""))
    return "\n".join(lines)
