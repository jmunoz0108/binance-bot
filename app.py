from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
import sqlite3
import hashlib
import hmac
import httpx
import json
import os
import uuid
import asyncio

try:
    import websockets
except Exception:
    websockets = None

app = FastAPI(title="Binance Spot + Futures Bot")

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.sqlite3"

BOT_SECRET = os.getenv("BOT_SECRET", "CHANGE_ME")
DEFAULT_EXCHANGE = os.getenv("DEFAULT_EXCHANGE", "paper")  # paper, binance_spot, binance_futures
ENABLE_EXECUTION = os.getenv("ENABLE_EXECUTION", "false").lower() == "true"
ENABLE_PAPER_TRADING = os.getenv("ENABLE_PAPER_TRADING", "true").lower() == "true"
ACCOUNT_EQUITY_USD = float(os.getenv("ACCOUNT_EQUITY_USD", "10000"))
MAX_RISK_PCT_PER_TRADE = float(os.getenv("MAX_RISK_PCT_PER_TRADE", "0.5"))
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", "3"))

BINANCE_SPOT_API_KEY = os.getenv("BINANCE_SPOT_API_KEY", "")
BINANCE_SPOT_API_SECRET = os.getenv("BINANCE_SPOT_API_SECRET", "")
BINANCE_SPOT_BASE_URL = os.getenv("BINANCE_SPOT_BASE_URL", "https://api.binance.com")

BINANCE_FUTURES_API_KEY = os.getenv("BINANCE_FUTURES_API_KEY", "")
BINANCE_FUTURES_API_SECRET = os.getenv("BINANCE_FUTURES_API_SECRET", "")
BINANCE_FUTURES_BASE_URL = os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com")
BINANCE_FUTURES_POSITION_MODE = os.getenv("BINANCE_FUTURES_POSITION_MODE", "ONE_WAY")  # ONE_WAY or HEDGE
BINANCE_FUTURES_MARGIN_TYPE = os.getenv("BINANCE_FUTURES_MARGIN_TYPE", "ISOLATED")  # ISOLATED or CROSSED
BINANCE_FUTURES_LEVERAGE = int(os.getenv("BINANCE_FUTURES_LEVERAGE", "3"))
BINANCE_FUTURES_WS_PING_SECONDS = int(os.getenv("BINANCE_FUTURES_WS_PING_SECONDS", "30"))

WS_STATE: Dict[str, Any] = {"task": None, "running": False, "last_event": None, "listen_key": None, "last_error": None}


class SignalPayload(BaseModel):
    signal: Literal["long", "short", "buy", "sell"]
    ticker: str
    tf: str = "5"
    close: float
    score: float = 0
    preset: str = "Day Trading"
    exchange: Optional[Literal["paper", "binance_spot", "binance_futures"]] = None
    secret: Optional[str] = None
    # futures only
    futures_mode: Optional[Literal["one_way", "hedge"]] = None
    margin_type: Optional[Literal["isolated", "cross"]] = None
    leverage: Optional[int] = None
    position_side: Optional[Literal["LONG", "SHORT", "BOTH"]] = None
    strategy: Optional[str] = "AI Trading Bot"
    extra: Optional[Dict[str, Any]] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_journal (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            signal TEXT NOT NULL,
            exchange TEXT NOT NULL,
            approved INTEGER NOT NULL,
            reason TEXT,
            payload_json TEXT NOT NULL,
            response_json TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            signal TEXT NOT NULL,
            exchange TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL,
            qty REAL,
            risk_usd REAL,
            exchange_order_id TEXT,
            client_order_id TEXT,
            mode TEXT,
            position_side TEXT,
            metadata_json TEXT
        )""")
        conn.commit()


init_db()


def signed_request(method: str, base_url: str, path: str, params: Dict[str, Any], api_key: str, api_secret: str) -> Dict[str, Any]:
    payload = dict(params)
    payload["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
    payload["recvWindow"] = 5000
    query = urlencode(payload)
    signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{base_url}{path}?{query}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}
    with httpx.Client(timeout=20.0, headers=headers) as client:
        r = client.request(method.upper(), url)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return {"status_code": r.status_code, "json": data, "text": r.text}


def api_key_request(method: str, base_url: str, path: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {"X-MBX-APIKEY": api_key}
    with httpx.Client(timeout=20.0, headers=headers) as client:
        r = client.request(method.upper(), f"{base_url}{path}", params=params or {})
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return {"status_code": r.status_code, "json": data, "text": r.text}


def public_get(base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with httpx.Client(timeout=20.0) as client:
        r = client.get(f"{base_url}{path}", params=params or {})
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return {"status_code": r.status_code, "json": data, "text": r.text}


def floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    import math
    return math.floor(value / step) * step


def journal(payload: SignalPayload, approved: bool, reason: str, response: Dict[str, Any]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO signal_journal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), now_iso(), payload.ticker, payload.tf, payload.signal,
                payload.exchange or DEFAULT_EXCHANGE, 1 if approved else 0, reason,
                json.dumps(payload.model_dump(), default=str), json.dumps(response, default=str)
            )
        )
        conn.commit()


def persist_position(**row) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"], row["created_at"], row["ticker"], row["timeframe"], row["signal"],
                row["exchange"], row["status"], row["entry_price"], row.get("stop_price"),
                row.get("qty"), row.get("risk_usd"), row.get("exchange_order_id"), row.get("client_order_id"),
                row.get("mode"), row.get("position_side"), json.dumps(row.get("metadata", {}), default=str)
            )
        )
        conn.commit()


def get_open_positions_count() -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'open'")
        row = cur.fetchone()
        return int(row[0] or 0)


def build_order_plan(payload: SignalPayload) -> Dict[str, Any]:
    entry = float(payload.close)
    risk_pct = min(MAX_RISK_PCT_PER_TRADE, 0.50 if payload.preset in ["Scalping", "Day Trading"] else 0.75)
    if payload.tf in ["1", "3", "5"]:
        stop_distance_pct = 0.7
    elif payload.tf in ["15", "30"]:
        stop_distance_pct = 1.0
    else:
        stop_distance_pct = 1.5
    risk_usd = ACCOUNT_EQUITY_USD * (risk_pct / 100.0)
    if payload.signal in ["long", "buy"]:
        stop = entry * (1 - stop_distance_pct / 100.0)
        risk_per_unit = max(entry - stop, entry * 0.0005)
    else:
        stop = entry * (1 + stop_distance_pct / 100.0)
        risk_per_unit = max(stop - entry, entry * 0.0005)
    qty = risk_usd / risk_per_unit if risk_per_unit > 0 else 0.0
    return {
        "entry": entry,
        "stop": stop,
        "risk_pct": risk_pct,
        "risk_usd": risk_usd,
        "qty": qty
    }


def send_order_paper(payload: SignalPayload, plan: Dict[str, Any]) -> Dict[str, Any]:
    pos_id = str(uuid.uuid4())
    persist_position(
        id=pos_id,
        created_at=now_iso(),
        ticker=payload.ticker.upper(),
        timeframe=payload.tf,
        signal=payload.signal,
        exchange="paper",
        status="open",
        entry_price=plan["entry"],
        stop_price=plan["stop"],
        qty=plan["qty"],
        risk_usd=plan["risk_usd"],
        exchange_order_id=None,
        client_order_id=None,
        mode=None,
        position_side=None,
        metadata={"strategy": payload.strategy},
    )
    return {"submitted": True, "exchange": "paper", "client_order_id": pos_id, "reason": "paper trade recorded"}


def spot_symbol_filters(symbol: str) -> Dict[str, Any]:
    info = public_get(BINANCE_SPOT_BASE_URL, "/api/v3/exchangeInfo", {"symbol": symbol})
    if info["status_code"] >= 400 or not info["json"].get("symbols"):
        raise RuntimeError(f"spot symbol not found: {symbol}")
    return info["json"]["symbols"][0]


def futures_symbol_filters(symbol: str) -> Dict[str, Any]:
    info = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/exchangeInfo", {})
    if info["status_code"] >= 400:
        raise RuntimeError(f"futures exchangeInfo failed: {info['text']}")
    matches = [s for s in info["json"].get("symbols", []) if s.get("symbol") == symbol]
    if not matches:
        raise RuntimeError(f"futures symbol not found: {symbol}")
    return matches[0]


def send_order_spot(payload: SignalPayload, plan: Dict[str, Any]) -> Dict[str, Any]:
    if not BINANCE_SPOT_API_KEY or not BINANCE_SPOT_API_SECRET:
        return {"submitted": False, "reason": "missing Binance Spot credentials", "exchange": "binance_spot"}

    symbol = payload.ticker.upper().replace(".P", "")
    side = "BUY" if payload.signal in ["long", "buy"] else "SELL"

    info = spot_symbol_filters(symbol)
    filters = {f["filterType"]: f for f in info.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    step = float(lot.get("stepSize", "0.0"))
    min_qty = float(lot.get("minQty", "0.0"))

    qty = floor_to_step(plan["qty"], step)
    qty = max(qty, min_qty)

    client_order_id = f"spot-{uuid.uuid4().hex[:20]}"
    resp = signed_request(
        "POST",
        BINANCE_SPOT_BASE_URL,
        "/api/v3/order",
        {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
            "newClientOrderId": client_order_id,
            "newOrderRespType": "FULL",
        },
        BINANCE_SPOT_API_KEY,
        BINANCE_SPOT_API_SECRET,
    )
    ok = resp["status_code"] < 400
    if ok:
        data = resp["json"]
        executed_qty = float(data.get("executedQty", qty) or qty)
        cqq = float(data.get("cummulativeQuoteQty", 0) or 0)
        avg = cqq / executed_qty if executed_qty > 0 else plan["entry"]
        persist_position(
            id=str(uuid.uuid4()),
            created_at=now_iso(),
            ticker=symbol,
            timeframe=payload.tf,
            signal=payload.signal,
            exchange="binance_spot",
            status="open",
            entry_price=avg,
            stop_price=plan["stop"],
            qty=executed_qty,
            risk_usd=plan["risk_usd"],
            exchange_order_id=str(data.get("orderId", "")),
            client_order_id=client_order_id,
            mode="spot",
            position_side="LONG_ONLY",
            metadata={"response": data, "strategy": payload.strategy},
        )
    return {"submitted": ok, "exchange": "binance_spot", "client_order_id": client_order_id, "response": resp["json"], "reason": "ok" if ok else resp["text"]}


def configure_futures(symbol: str, mode: str, margin_type: str, leverage: int) -> Dict[str, Any]:
    dual = "true" if mode == "HEDGE" else "false"
    out = {}
    out["position_mode"] = signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/positionSide/dual", {"dualSidePosition": dual}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)
    out["margin_type"] = signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)
    out["leverage"] = signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)
    return out


def send_order_futures(payload: SignalPayload, plan: Dict[str, Any]) -> Dict[str, Any]:
    if not BINANCE_FUTURES_API_KEY or not BINANCE_FUTURES_API_SECRET:
        return {"submitted": False, "reason": "missing Binance Futures credentials", "exchange": "binance_futures"}

    symbol = payload.ticker.upper().replace(".P", "")
    side = "BUY" if payload.signal in ["long", "buy"] else "SELL"
    mode = "HEDGE" if (payload.futures_mode or BINANCE_FUTURES_POSITION_MODE).upper() in ["HEDGE", "HEDGE_MODE"] else "ONE_WAY"
    margin_type = "CROSSED" if (payload.margin_type or BINANCE_FUTURES_MARGIN_TYPE).upper() in ["CROSS", "CROSSED"] else "ISOLATED"
    leverage = max(1, min(int(payload.leverage or BINANCE_FUTURES_LEVERAGE), 125))

    info = futures_symbol_filters(symbol)
    filters = {f["filterType"]: f for f in info.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    step = float(lot.get("stepSize", "0.0"))
    min_qty = float(lot.get("minQty", "0.0"))

    qty = floor_to_step(plan["qty"], step)
    qty = max(qty, min_qty)

    cfg = configure_futures(symbol, mode, margin_type, leverage)

    client_order_id = f"fut-{uuid.uuid4().hex[:20]}"
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
        "newClientOrderId": client_order_id,
        "newOrderRespType": "RESULT",
    }
    if mode == "HEDGE":
        params["positionSide"] = payload.position_side or ("LONG" if payload.signal in ["long", "buy"] else "SHORT")
    else:
        params["positionSide"] = "BOTH"

    resp = signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/order", params, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)
    ok = resp["status_code"] < 400
    if ok:
        data = resp["json"]
        avg = float(data.get("avgPrice", 0) or 0) or plan["entry"]
        executed_qty = float(data.get("executedQty", qty) or qty)
        persist_position(
            id=str(uuid.uuid4()),
            created_at=now_iso(),
            ticker=symbol,
            timeframe=payload.tf,
            signal=payload.signal,
            exchange="binance_futures",
            status="open",
            entry_price=avg,
            stop_price=plan["stop"],
            qty=executed_qty,
            risk_usd=plan["risk_usd"],
            exchange_order_id=str(data.get("orderId", "")),
            client_order_id=client_order_id,
            mode=mode,
            position_side=params["positionSide"],
            metadata={"response": data, "account_config": cfg, "strategy": payload.strategy},
        )
    return {"submitted": ok, "exchange": "binance_futures", "client_order_id": client_order_id, "response": resp["json"], "account_config": cfg, "reason": "ok" if ok else resp["text"]}


def execute(payload: SignalPayload) -> Dict[str, Any]:
    if get_open_positions_count() >= MAX_CONCURRENT_POSITIONS:
        return {"approved": False, "reason": "max concurrent positions reached"}

    if BOT_SECRET != "CHANGE_ME" and payload.secret != BOT_SECRET:
        raise HTTPException(status_code=401, detail="invalid secret")

    exchange = payload.exchange or DEFAULT_EXCHANGE
    plan = build_order_plan(payload)

    if exchange == "paper":
        if not ENABLE_PAPER_TRADING:
            return {"approved": False, "reason": "paper trading disabled"}
        result = send_order_paper(payload, plan)
    elif exchange == "binance_spot":
        if not ENABLE_EXECUTION:
            return {"approved": False, "reason": "live execution disabled"}
        result = send_order_spot(payload, plan)
    elif exchange == "binance_futures":
        if not ENABLE_EXECUTION:
            return {"approved": False, "reason": "live execution disabled"}
        result = send_order_futures(payload, plan)
    else:
        result = {"approved": False, "reason": f"unsupported exchange: {exchange}"}

    approved = bool(result.get("submitted"))
    response = {"approved": approved, "exchange": exchange, "order_plan": plan, "result": result, "ts": now_iso()}
    journal(payload, approved, result.get("reason", ""), response)
    return response


@app.get("/")
def root():
    return {"status": "ok", "service": "binance-spot-futures-bot", "time": now_iso()}


@app.get("/config")
def config():
    return {
        "default_exchange": DEFAULT_EXCHANGE,
        "execution_enabled": ENABLE_EXECUTION,
        "paper_enabled": ENABLE_PAPER_TRADING,
        "equity_usd": ACCOUNT_EQUITY_USD,
        "max_risk_pct_per_trade": MAX_RISK_PCT_PER_TRADE,
        "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
        "futures_default_mode": BINANCE_FUTURES_POSITION_MODE,
        "futures_default_margin_type": BINANCE_FUTURES_MARGIN_TYPE,
        "futures_default_leverage": BINANCE_FUTURES_LEVERAGE,
    }


@app.get("/pine-alert-templates")
def pine_alert_templates():
    return {
        "paper_long": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"paper","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "paper_short": '{"signal":"short","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"paper","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "spot_buy": '{"signal":"buy","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Swing Trading","exchange":"binance_spot","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "spot_sell": '{"signal":"sell","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Swing Trading","exchange":"binance_spot","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "futures_one_way_long": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"binance_futures","futures_mode":"one_way","margin_type":"isolated","leverage":3,"position_side":"BOTH","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "futures_one_way_short": '{"signal":"short","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"binance_futures","futures_mode":"one_way","margin_type":"isolated","leverage":3,"position_side":"BOTH","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "futures_hedge_long": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"binance_futures","futures_mode":"hedge","margin_type":"isolated","leverage":3,"position_side":"LONG","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "futures_hedge_short": '{"signal":"short","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"binance_futures","futures_mode":"hedge","margin_type":"isolated","leverage":3,"position_side":"SHORT","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
    }


@app.get("/journal/recent")
def journal_recent(limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM signal_journal ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),))
        return {"rows": [dict(r) for r in cur.fetchall()]}


@app.get("/positions/open")
def positions_open():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM positions WHERE status = 'open' ORDER BY created_at DESC")
        return {"rows": [dict(r) for r in cur.fetchall()]}


@app.post("/webhook/tradingview")
async def webhook(payload: SignalPayload):
    return execute(payload)


# ---- Futures websocket skeleton ----
def start_futures_listen_key() -> Dict[str, Any]:
    return api_key_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/listenKey", BINANCE_FUTURES_API_KEY)


def keepalive_futures_listen_key(listen_key: str) -> Dict[str, Any]:
    return api_key_request("PUT", BINANCE_FUTURES_BASE_URL, "/fapi/v1/listenKey", BINANCE_FUTURES_API_KEY, {"listenKey": listen_key})


def close_futures_listen_key(listen_key: str) -> Dict[str, Any]:
    return api_key_request("DELETE", BINANCE_FUTURES_BASE_URL, "/fapi/v1/listenKey", BINANCE_FUTURES_API_KEY, {"listenKey": listen_key})


async def _futures_keepalive_loop():
    while WS_STATE["running"] and WS_STATE["listen_key"]:
        await asyncio.sleep(45 * 60)
        try:
            WS_STATE["last_event"] = {"type": "listenKey.keepalive", "response": keepalive_futures_listen_key(WS_STATE["listen_key"]), "ts": now_iso()}
        except Exception as exc:
            WS_STATE["last_error"] = str(exc)


async def _futures_ws_loop():
    if websockets is None:
        WS_STATE["running"] = False
        WS_STATE["last_error"] = "websockets package not installed"
        return
    while WS_STATE["running"]:
        try:
            lk = start_futures_listen_key()
            if lk["status_code"] >= 400:
                raise RuntimeError(lk["text"])
            listen_key = lk["json"]["listenKey"]
            WS_STATE["listen_key"] = listen_key
            keepalive_task = asyncio.create_task(_futures_keepalive_loop())
            async with websockets.connect(f"wss://fstream.binance.com/ws/{listen_key}", ping_interval=BINANCE_FUTURES_WS_PING_SECONDS, ping_timeout=20) as ws:
                WS_STATE["last_event"] = {"type": "ws.connected", "ts": now_iso()}
                while WS_STATE["running"]:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    WS_STATE["last_event"] = {"type": data.get("e"), "data": data, "ts": now_iso()}
            keepalive_task.cancel()
        except Exception as exc:
            WS_STATE["last_error"] = str(exc)
            await asyncio.sleep(5)
        finally:
            if WS_STATE["listen_key"]:
                try:
                    close_futures_listen_key(WS_STATE["listen_key"])
                except Exception:
                    pass
            WS_STATE["listen_key"] = None


@app.post("/futures/ws/start")
async def futures_ws_start():
    if WS_STATE["running"]:
        return {"ok": True, "reason": "already running"}
    WS_STATE["running"] = True
    WS_STATE["task"] = asyncio.create_task(_futures_ws_loop())
    return {"ok": True, "reason": "started"}


@app.post("/futures/ws/stop")
async def futures_ws_stop():
    WS_STATE["running"] = False
    task = WS_STATE.get("task")
    if task:
        task.cancel()
    WS_STATE["task"] = None
    return {"ok": True, "reason": "stopped"}


@app.get("/futures/ws/status")
def futures_ws_status():
    return {
        "running": WS_STATE["running"],
        "listen_key": WS_STATE["listen_key"],
        "last_event": WS_STATE["last_event"],
        "last_error": WS_STATE["last_error"],
    }
