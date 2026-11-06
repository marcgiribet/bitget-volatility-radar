"""Run-once Bitget radar for GitHub Actions (5-minute scheduled polling)."""
import asyncio
import logging
import os
from datetime import datetime, timezone

import aiohttp

from main import ALERT_PERCENT, PRODUCT_TYPE, REST_URL, requested_symbols, telegram_send

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduled-radar")

# Bitget V2 mix market candlesticks, 1-minute closed candles.
# A single run checks 5m and 10m price changes from recent completed candles.
async def check_symbol(session, symbol):
    params = {"symbol": symbol, "productType": PRODUCT_TYPE, "granularity": "1m", "limit": "15"}
    async with session.get(f"{REST_URL}/api/v2/mix/market/candles", params=params) as response:
        response.raise_for_status()
        payload = await response.json()
    if payload.get("code") != "00000":
        raise RuntimeError(f"{symbol}: Bitget API {payload.get('msg')}")
    candles = sorted(payload.get("data", []), key=lambda c: int(c[0]))
    # Exclude the current, unfinished 1-minute candle.
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    candles = [c for c in candles if int(c[0]) + 60_000 <= now_ms]
    if len(candles) < 11:
        log.warning("%s: insufficient candle history", symbol)
        return []
    current = float(candles[-1][4])
    alerts = []
    for minutes in (5, 10):
        if len(candles) < minutes + 1:
            continue
        previous = float(candles[-1-minutes][4])
        if previous <= 0:
            continue
        change = (current / previous - 1) * 100
        if abs(change) >= ALERT_PERCENT:
            direction = "SUBIDA 🟢" if change > 0 else "CAÍDA 🔴"
            alerts.append(
                f"{direction} {symbol}: {change:+.2f}% en {minutes} min. "
                f"Precio: {current:g}"
            )
    return alerts


async def main():
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID como GitHub Actions secrets.")
    symbols = requested_symbols()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.get(
            f"{REST_URL}/api/v2/mix/market/contracts", params={"productType": PRODUCT_TYPE}
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        if payload.get("code") != "00000":
            raise RuntimeError(f"Bitget contracts API: {payload.get('msg')}")
        available = {x["symbol"] for x in payload.get("data", []) if x.get("symbol")}
        active = [s for s in symbols if s in available]
        missing = [s for s in symbols if s not in available]
        if missing:
            log.warning("Not available in %s: %s", PRODUCT_TYPE, ", ".join(missing))
        if not active:
            raise RuntimeError("No requested symbols are available on Bitget.")
        semaphore = asyncio.Semaphore(5)

        async def limited(symbol):
            async with semaphore:
                try:
                    return await check_symbol(session, symbol)
                except Exception as exc:
                    log.error("Error checking %s: %s", symbol, exc)
                    return []

        results = await asyncio.gather(*(limited(s) for s in active))
        alerts = [message for batch in results for message in batch]
        if alerts:
            await telegram_send(
                session,
                "🚨 BITGET RADAR (comprobación periódica)\n"
                + "\n".join(alerts[:35])
                + "\nAvisos informativos, no señales de inversión."
            )
        else:
            log.info("No thresholds reached across %d symbols", len(active))


if __name__ == "__main__":
    asyncio.run(main())
