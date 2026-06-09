"""
Stock tool — real-time stock/crypto quotes via Yahoo Finance (no API key).
"""

from __future__ import annotations

import httpx

SCHEMA = {
    "type": "function",
    "function": {
        "name": "stock",
        "description": (
            "Get the current price and daily change of a stock, ETF, or crypto by its "
            "ticker symbol. Use this for ANY question about stock/share prices, market "
            "quotes, or how a company's stock is doing. "
            "Examples: NVDA (Nvidia), AAPL (Apple), TSLA (Tesla), BTC-USD (Bitcoin)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. 'NVDA', 'AAPL', 'BTC-USD'.",
                },
            },
            "required": ["symbol"],
        },
    },
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def execute(symbol: str) -> str:
    symbol = symbol.strip().upper()
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        with httpx.Client(timeout=10.0, headers=_HEADERS, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"[ERROR] Quote request failed: {e}"

    try:
        meta = data["chart"]["result"][0]["meta"]
    except (KeyError, IndexError, TypeError):
        err = (data.get("chart") or {}).get("error")
        return f"[ERROR] No data for '{symbol}'. {err or 'Unknown ticker?'}"

    price = meta.get("regularMarketPrice")
    if price is None:
        return f"[ERROR] No price available for '{symbol}'."

    currency = meta.get("currency", "")
    name = meta.get("longName") or meta.get("shortName") or symbol
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")

    lines = [f"**{name}** ({symbol})", f"- Price: **{price:.2f} {currency}**"]
    if prev:
        change = price - prev
        pct = (change / prev * 100) if prev else 0
        arrow = "▲" if change >= 0 else "▼"
        lines.append(f"- Change: {arrow} {change:+.2f} ({pct:+.2f}%) vs prev close {prev:.2f}")

    hi, lo = meta.get("regularMarketDayHigh"), meta.get("regularMarketDayLow")
    if hi and lo:
        lines.append(f"- Day range: {lo:.2f} – {hi:.2f}")

    exch = meta.get("exchangeName") or meta.get("fullExchangeName")
    if exch:
        lines.append(f"- Exchange: {exch}")

    return "\n".join(lines)
