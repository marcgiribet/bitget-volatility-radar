import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiohttp
import websockets

# Bitget Classic public market-data WebSocket. No API key is needed.
WS_URL = "wss://ws.bitget.com/v2/ws/public"
REST_URL = "https://api.bitget.com"
PRODUCT_TYPE = os.getenv("BITGET_PRODUCT_TYPE", "USDT-FUTURES")
WINDOWS_MINUTES = sorted(set(int(x.strip()) for x in os.getenv("WINDOWS_MINUTES", "5,10,15,20").split(",") if x.strip()))
ALERT_PERCENT = float(os.getenv("ALERT_PERCENT", "1"))
COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))
SYMBOLS_RAW = os.getenv(
    "SYMBOLS",
    "BTCUSDT,ETHUSDT,XAUUSD,XAUUSDT,XAGUSDT,AAVEUSDT,BNBUSDT,RAYUSDT,"
    "LINKUSDT,ENAUSDT,PEPEUSDT,ADAUSDT,XRPUSDT,SOLUSDT,RUNEUSDT,HYPEUSDT,"
    "HBARUSDT,RENDERUSDT,DOGEUSDT,AVAXUSDT",
)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("volatility-radar")

# Per-symbol rolling price history and per-direction alert priority state.
history = defaultdict(deque)
alert_state = {}
last_received_at = {}
MADRID = ZoneInfo("Europe/Madrid")


def requested_symbols():
    return list(dict.fromkeys(s.strip().upper().replace(".P", "") for s in SYMBOLS_RAW.split(",") if s.strip()))


async def telegram_send(session, message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado; aviso no enviado.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}) as resp:
            body = await resp.text()
            if resp.status != 200:
                log.error("Telegram devolvió HTTP %s: %s", resp.status, body[:300])
    except Exception:
        log.exception("Error enviando mensaje a Telegram")


async def get_available_symbols(session):
    url = f"{REST_URL}/api/v2/mix/market/contracts"
    params = {"productType": PRODUCT_TYPE}
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        resp.raise_for_status()
        payload = await resp.json()
    if payload.get("code") != "00000":
        raise RuntimeError(f"Bitget no pudo listar contratos: {payload}")
    return {item["symbol"] for item in payload.get("data", []) if item.get("symbol")}


def calculate_change(prices, now, window_seconds):
    cutoff = now - window_seconds
    reference = None
    # Find the latest price at or before the target time; if history is sparse,
    # use the oldest sample that is still within the rolling window.
    for ts, price in prices:
        if ts <= cutoff:
            reference = price
        else:
            break
    if reference is None and prices and prices[0][0] <= cutoff + 2:
        reference = prices[0][1]
    if reference is None or reference <= 0:
        return None
    return (prices[-1][1] / reference - 1.0) * 100.0


async def process_ticker(session, symbol, price, timestamp_ms):
    now = timestamp_ms / 1000.0 if timestamp_ms else time.time()
    last_received_at[symbol] = time.time()
    prices = history[symbol]
    prices.append((now, price))

    # Keep enough data for the largest window plus a safety margin.
    max_age = max(WINDOWS_MINUTES) * 60 + 30
    while prices and prices[0][0] < now - max_age:
        prices.popleft()

    for minutes in WINDOWS_MINUTES:
        change = calculate_change(prices, now, minutes * 60)
        if change is None or abs(change) < ALERT_PERCENT:
            continue

        direction = "SUBIDA" if change > 0 else "CAÍDA"
        key = (symbol, direction)
        previous = alert_state.get(key)
        # A shorter-window alert suppresses the same move on longer windows.
        # During cooldown, only a NEW, shorter-window alert may supersede it.
        if previous and now - previous[0] < COOLDOWN_SECONDS and minutes >= previous[1]:
            continue

        alert_state[key] = (now, minutes)
        emoji = "🟢" if change > 0 else "🔴"
        when = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%H:%M:%S UTC")
        message = (
            f"{emoji} RADAR BITGET — {direction}\n"
            f"Activo: {symbol}\n"
            f"Variación: {change:+.2f}% en {minutes} minutos\n"
            f"Último precio: {price:g}\n"
            f"Hora: {when}\n"
            "Aviso informativo; no es una señal automática de entrada."
        )
        log.info("%s %s %+.2f%% en %sm", symbol, direction, change, minutes)
        await telegram_send(session, message)
        break  # At most one alert per ticker update, prioritizing the shortest window.


async def run_connection(session, symbols):
    args = [
        {"instType": PRODUCT_TYPE, "channel": "ticker", "instId": symbol}
        for symbol in symbols
    ]
    async with websockets.connect(
        WS_URL,
        ping_interval=None,
        close_timeout=10,
        max_size=2**20,
    ) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": args}))
        log.info("Suscripción enviada para %d símbolos.", len(symbols))

        async def heartbeat():
            while True:
                await asyncio.sleep(25)
                await ws.send("ping")

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            async for raw in ws:
                if raw == "pong":
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("event") == "error":
                    log.error("Error de suscripción Bitget: %s", msg)
                    continue
                arg = msg.get("arg") or {}
                symbol = arg.get("instId")
                if not symbol:
                    continue
                for item in msg.get("data", []):
                    try:
                        price = float(item.get("lastPr") or item.get("last") or 0)
                        timestamp = int(msg.get("ts") or item.get("ts") or time.time() * 1000)
                        if price > 0:
                            await process_ticker(session, symbol, price, timestamp)
                    except (TypeError, ValueError):
                        continue
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)


async def daily_status(session, symbols):
    """Send one daily status at 08:00 Europe/Madrid (DST-aware)."""
    last_sent_date = None
    while True:
        local_now = datetime.now(MADRID)
        if local_now.hour == 8 and local_now.minute < 2 and last_sent_date != local_now.date():
            now = time.time()
            fresh = [s for s in symbols if now - last_received_at.get(s, 0) <= 120]
            stale = [s for s in symbols if s not in fresh]
            if fresh:
                status = f"🟢 Recibiendo precios recientes de {len(fresh)}/{len(symbols)} mercados."
            else:
                status = "🔴 Sin precios recientes de los mercados monitorizados."
            details = f"\\nSin datos recientes: {', '.join(stale)}" if stale else ""
            message = (
                f"📊 Estado diario Bitget Radar — {local_now:%d/%m/%Y} 08:00 "
                "(hora peninsular española)\\n"
                f"{status}\\n"
                f"Umbral: ±{ALERT_PERCENT:g}% | Ventanas: {', '.join(map(str, WINDOWS_MINUTES))} min"
                f"{details}"
            )
            await telegram_send(session, message)
            last_sent_date = local_now.date()
        await asyncio.sleep(20)


async def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID. El radar arrancará, pero no enviará avisos.")
    requested = requested_symbols()
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        available = await get_available_symbols(session)
        symbols = [s for s in requested if s in available]
        skipped = [s for s in requested if s not in available]
        if skipped:
            log.warning("Símbolos no encontrados en %s (se omiten): %s", PRODUCT_TYPE, ", ".join(skipped))
        if not symbols:
            raise RuntimeError(
                f"No se encontró ninguno de los símbolos solicitados en {PRODUCT_TYPE}. "
                "Comprueba SYMBOLS y BITGET_PRODUCT_TYPE."
            )
        log.info("Radar iniciado. Símbolos activos (%d): %s", len(symbols), ", ".join(symbols))
        log.info("Umbral: ±%.2f%% | Ventanas: %s minutos", ALERT_PERCENT, WINDOWS_MINUTES)
        status_task = asyncio.create_task(daily_status(session, symbols))
        try:
            while True:
                try:
                    await run_connection(session, symbols)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Conexión interrumpida; reintentando en 5 segundos.")
                    await asyncio.sleep(5)
        finally:
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Radar detenido.")
