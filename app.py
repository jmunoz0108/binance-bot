
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Literal, Dict, Any, List
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
import sqlite3, hashlib, hmac, httpx, json, os, uuid, asyncio
try:
    import websockets
except Exception:
    websockets = None

app = FastAPI(title="Binance Spot + Futures Bot PRO Final")
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.sqlite3"

BOT_SECRET = os.getenv("BOT_SECRET", "CHANGE_ME")
DEFAULT_EXCHANGE = os.getenv("DEFAULT_EXCHANGE", "paper")
ENABLE_EXECUTION = os.getenv("ENABLE_EXECUTION", "false").lower() == "true"
ENABLE_PAPER_TRADING = os.getenv("ENABLE_PAPER_TRADING", "true").lower() == "true"
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").lower() == "true"
ACCOUNT_EQUITY_USD = float(os.getenv("ACCOUNT_EQUITY_USD", "10000"))
MAX_RISK_PCT_PER_TRADE = float(os.getenv("MAX_RISK_PCT_PER_TRADE", "0.5"))
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", "3"))

BINANCE_SPOT_API_KEY = os.getenv("BINANCE_SPOT_API_KEY", "")
BINANCE_SPOT_API_SECRET = os.getenv("BINANCE_SPOT_API_SECRET", "")
BINANCE_SPOT_BASE_URL = os.getenv("BINANCE_SPOT_BASE_URL", "https://api.binance.com")
BINANCE_FUTURES_API_KEY = os.getenv("BINANCE_FUTURES_API_KEY", "")
BINANCE_FUTURES_API_SECRET = os.getenv("BINANCE_FUTURES_API_SECRET", "")
BINANCE_FUTURES_BASE_URL = os.getenv("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com")
BINANCE_FUTURES_POSITION_MODE = os.getenv("BINANCE_FUTURES_POSITION_MODE", "ONE_WAY")
BINANCE_FUTURES_MARGIN_TYPE = os.getenv("BINANCE_FUTURES_MARGIN_TYPE", "ISOLATED")
BINANCE_FUTURES_LEVERAGE = int(os.getenv("BINANCE_FUTURES_LEVERAGE", "3"))
BINANCE_FUTURES_WS_PING_SECONDS = int(os.getenv("BINANCE_FUTURES_WS_PING_SECONDS", "30"))

BE_TRIGGER_R = float(os.getenv("BE_TRIGGER_R", "1.0"))
TRAIL_TRIGGER_R = float(os.getenv("TRAIL_TRIGGER_R", "1.8"))
TRAIL_DISTANCE_R = float(os.getenv("TRAIL_DISTANCE_R", "0.8"))
REVERSAL_SCORE_TO_EXIT = int(os.getenv("REVERSAL_SCORE_TO_EXIT", "3"))

WS_STATE: Dict[str, Any] = {"task": None, "running": False, "last_event": None, "listen_key": None, "last_error": None}

class SignalPayload(BaseModel):
    signal: Literal["long","short","buy","sell"]
    ticker: str
    tf: str = "5"
    close: float
    score: float = 0
    preset: str = "Day Trading"
    exchange: Optional[Literal["paper","binance_spot","binance_futures"]] = None
    secret: Optional[str] = None
    futures_mode: Optional[Literal["one_way","hedge"]] = None
    margin_type: Optional[Literal["isolated","cross"]] = None
    leverage: Optional[int] = None
    position_side: Optional[Literal["LONG","SHORT","BOTH"]] = None
    strategy: Optional[str] = "AI Trading Bot"
    extra: Optional[Dict[str, Any]] = None

class ManagePositionPayload(BaseModel):
    position_id: str
    current_price: float
    seller_pressure: bool = False
    buyer_pressure: bool = False
    bearish_divergence: bool = False
    bullish_divergence: bool = False
    bos_failure: bool = False
    momentum_drop: bool = False
    trend_weakening: bool = False
    dry_run: bool = False

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS signal_journal (id TEXT PRIMARY KEY, created_at TEXT, ticker TEXT, timeframe TEXT, signal TEXT, exchange TEXT, approved INTEGER, reason TEXT, payload_json TEXT, response_json TEXT)")
        cur.execute("CREATE TABLE IF NOT EXISTS positions (id TEXT PRIMARY KEY, created_at TEXT, ticker TEXT, timeframe TEXT, signal TEXT, exchange TEXT, status TEXT, entry_price REAL, stop_price REAL, qty REAL, risk_usd REAL, exchange_order_id TEXT, client_order_id TEXT, mode TEXT, position_side TEXT, metadata_json TEXT, break_even_armed INTEGER DEFAULT 0, trail_armed INTEGER DEFAULT 0, high_watermark REAL, low_watermark REAL)")
        conn.commit()
init_db()

def public_get(base_url: str, path: str, params=None):
    with httpx.Client(timeout=20.0) as client:
        r = client.get(f"{base_url}{path}", params=params or {})
        try: data = r.json()
        except: data = {"raw": r.text}
        return {"status_code": r.status_code, "json": data, "text": r.text}

def signed_request(method: str, base_url: str, path: str, params: Dict[str, Any], api_key: str, api_secret: str):
    payload = dict(params)
    payload["timestamp"] = int(datetime.now(timezone.utc).timestamp()*1000)
    payload["recvWindow"] = 5000
    query = urlencode(payload)
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{base_url}{path}?{query}&signature={sig}"
    with httpx.Client(timeout=20.0, headers={"X-MBX-APIKEY": api_key}) as client:
        r = client.request(method.upper(), url)
        try: data = r.json()
        except: data = {"raw": r.text}
        return {"status_code": r.status_code, "json": data, "text": r.text}

def api_key_request(method: str, base_url: str, path: str, api_key: str, params=None):
    with httpx.Client(timeout=20.0, headers={"X-MBX-APIKEY": api_key}) as client:
        r = client.request(method.upper(), f"{base_url}{path}", params=params or {})
        try: data = r.json()
        except: data = {"raw": r.text}
        return {"status_code": r.status_code, "json": data, "text": r.text}

def floor_to_step(value: float, step: float) -> float:
    import math
    return value if step <= 0 else math.floor(value / step) * step

def update_position(position_id: str, updates: Dict[str, Any]):
    if not updates: return
    keys = list(updates.keys())
    values = [updates[k] for k in keys]
    set_clause = ", ".join([f"{k}=?" for k in keys])
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(f"UPDATE positions SET {set_clause} WHERE id=?", values+[position_id])
        conn.commit()

def get_position(position_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        return dict(row) if row else None

def append_position_metadata(position_id: str, patch: Dict[str, Any]):
    pos = get_position(position_id)
    if not pos: return
    try: meta = json.loads(pos.get("metadata_json") or "{}")
    except: meta = {}
    meta.update(patch)
    update_position(position_id, {"metadata_json": json.dumps(meta, default=str)})

def get_open_positions_count():
    with sqlite3.connect(DB_PATH) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0] or 0)

def get_all_open_futures_positions():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM positions WHERE exchange='binance_futures' AND status='open' ORDER BY created_at DESC").fetchall()]

def persist_position(**row):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            row["id"], row["created_at"], row["ticker"], row["timeframe"], row["signal"], row["exchange"], row["status"],
            row["entry_price"], row.get("stop_price"), row.get("qty"), row.get("risk_usd"), row.get("exchange_order_id"),
            row.get("client_order_id"), row.get("mode"), row.get("position_side"), json.dumps(row.get("metadata", {}), default=str),
            int(row.get("break_even_armed", 0)), int(row.get("trail_armed", 0)), row.get("high_watermark"), row.get("low_watermark")
        ))
        conn.commit()

def journal(payload, approved, reason, response):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO signal_journal VALUES (?,?,?,?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), now_iso(), payload.ticker, payload.tf, payload.signal, payload.exchange or DEFAULT_EXCHANGE,
            1 if approved else 0, reason, json.dumps(payload.model_dump(), default=str), json.dumps(response, default=str)
        ))
        conn.commit()

def build_order_plan(payload):
    entry = float(payload.close)
    risk_pct = min(MAX_RISK_PCT_PER_TRADE, 0.50 if payload.preset in ["Scalping","Day Trading"] else 0.75)
    stop_distance_pct = 0.7 if payload.tf in ["1","3","5"] else (1.0 if payload.tf in ["15","30"] else 1.5)
    risk_usd = ACCOUNT_EQUITY_USD * (risk_pct / 100.0)
    if payload.signal in ["long","buy"]:
        stop = entry * (1 - stop_distance_pct / 100.0)
        risk_per_unit = max(entry - stop, entry * 0.0005)
    else:
        stop = entry * (1 + stop_distance_pct / 100.0)
        risk_per_unit = max(stop - entry, entry * 0.0005)
    qty = risk_usd / risk_per_unit if risk_per_unit > 0 else 0.0
    return {"entry": entry, "stop": stop, "risk_pct": risk_pct, "risk_usd": risk_usd, "qty": qty, "risk_per_unit": risk_per_unit}

def send_order_paper(payload, plan):
    pos_id = str(uuid.uuid4())
    entry = plan["entry"]
    persist_position(
        id=pos_id, created_at=now_iso(), ticker=payload.ticker.upper(), timeframe=payload.tf, signal=payload.signal,
        exchange="paper", status="open", entry_price=entry, stop_price=plan["stop"], qty=plan["qty"], risk_usd=plan["risk_usd"],
        exchange_order_id=None, client_order_id=None, mode=None, position_side=None,
        metadata={"strategy": payload.strategy, "risk_per_unit": plan["risk_per_unit"]},
        break_even_armed=0, trail_armed=0,
        high_watermark=entry if payload.signal in ["long","buy"] else None, low_watermark=entry if payload.signal in ["short","sell"] else None
    )
    return {"submitted": True, "exchange": "paper", "position_id": pos_id, "reason": "paper trade recorded"}

def spot_symbol_filters(symbol):
    info = public_get(BINANCE_SPOT_BASE_URL, "/api/v3/exchangeInfo", {"symbol": symbol})
    if info["status_code"] >= 400 or not info["json"].get("symbols"): raise RuntimeError(f"spot symbol not found: {symbol}")
    return info["json"]["symbols"][0]

def futures_symbol_filters(symbol):
    info = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/exchangeInfo", {})
    if info["status_code"] >= 400: raise RuntimeError(info["text"])
    matches = [s for s in info["json"].get("symbols", []) if s.get("symbol") == symbol]
    if not matches: raise RuntimeError(f"futures symbol not found: {symbol}")
    return matches[0]

def send_order_spot(payload, plan):
    if not BINANCE_SPOT_API_KEY or not BINANCE_SPOT_API_SECRET:
        return {"submitted": False, "reason": "missing Binance Spot credentials", "exchange": "binance_spot"}
    symbol = payload.ticker.upper().replace(".P","")
    side = "BUY" if payload.signal in ["long","buy"] else "SELL"
    info = spot_symbol_filters(symbol)
    filters = {f["filterType"]: f for f in info.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    qty = max(floor_to_step(plan["qty"], float(lot.get("stepSize","0.0"))), float(lot.get("minQty","0.0")))
    cid = f"spot-{uuid.uuid4().hex[:20]}"
    resp = signed_request("POST", BINANCE_SPOT_BASE_URL, "/api/v3/order", {
        "symbol": symbol, "side": side, "type": "MARKET",
        "quantity": f"{qty:.8f}".rstrip("0").rstrip("."), "newClientOrderId": cid, "newOrderRespType": "FULL"
    }, BINANCE_SPOT_API_KEY, BINANCE_SPOT_API_SECRET)
    ok = resp["status_code"] < 400
    pos_id = None
    if ok:
        data = resp["json"]
        executed_qty = float(data.get("executedQty", qty) or qty)
        cqq = float(data.get("cummulativeQuoteQty", 0) or 0)
        avg = cqq / executed_qty if executed_qty > 0 else plan["entry"]
        pos_id = str(uuid.uuid4())
        persist_position(
            id=pos_id, created_at=now_iso(), ticker=symbol, timeframe=payload.tf, signal=payload.signal,
            exchange="binance_spot", status="open", entry_price=avg, stop_price=plan["stop"], qty=executed_qty,
            risk_usd=plan["risk_usd"], exchange_order_id=str(data.get("orderId","")), client_order_id=cid,
            mode="spot", position_side="LONG_ONLY", metadata={"response": data, "strategy": payload.strategy, "risk_per_unit": plan["risk_per_unit"]},
            break_even_armed=0, trail_armed=0, high_watermark=avg if payload.signal in ["long","buy"] else None, low_watermark=None
        )
    return {"submitted": ok, "exchange": "binance_spot", "position_id": pos_id, "client_order_id": cid, "response": resp["json"], "reason": "ok" if ok else resp["text"]}

def configure_futures(symbol, mode, margin_type, leverage):
    dual = "true" if mode == "HEDGE" else "false"
    return {
        "position_mode": signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/positionSide/dual", {"dualSidePosition": dual}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET),
        "margin_type": signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET),
        "leverage": signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET),
    }

def send_order_futures(payload, plan):
    if not BINANCE_FUTURES_API_KEY or not BINANCE_FUTURES_API_SECRET:
        return {"submitted": False, "reason": "missing Binance Futures credentials", "exchange": "binance_futures"}
    symbol = payload.ticker.upper().replace(".P","")
    side = "BUY" if payload.signal in ["long","buy"] else "SELL"
    mode = "HEDGE" if (payload.futures_mode or BINANCE_FUTURES_POSITION_MODE).upper() in ["HEDGE","HEDGE_MODE"] else "ONE_WAY"
    margin_type = "CROSSED" if (payload.margin_type or BINANCE_FUTURES_MARGIN_TYPE).upper() in ["CROSS","CROSSED"] else "ISOLATED"
    leverage = max(1, min(int(payload.leverage or BINANCE_FUTURES_LEVERAGE), 125))
    info = futures_symbol_filters(symbol)
    filters = {f["filterType"]: f for f in info.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    qty = max(floor_to_step(plan["qty"], float(lot.get("stepSize","0.0"))), float(lot.get("minQty","0.0")))
    cfg = configure_futures(symbol, mode, margin_type, leverage)
    cid = f"fut-{uuid.uuid4().hex[:20]}"
    params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": f"{qty:.8f}".rstrip("0").rstrip("."), "newClientOrderId": cid, "newOrderRespType": "RESULT"}
    params["positionSide"] = payload.position_side or (("LONG" if payload.signal in ["long","buy"] else "SHORT") if mode == "HEDGE" else "BOTH")
    resp = signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/order", params, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)
    ok = resp["status_code"] < 400
    pos_id = None
    if ok:
        data = resp["json"]
        avg = float(data.get("avgPrice", 0) or 0) or plan["entry"]
        executed_qty = float(data.get("executedQty", qty) or qty)
        pos_id = str(uuid.uuid4())
        persist_position(
            id=pos_id, created_at=now_iso(), ticker=symbol, timeframe=payload.tf, signal=payload.signal,
            exchange="binance_futures", status="open", entry_price=avg, stop_price=plan["stop"], qty=executed_qty, risk_usd=plan["risk_usd"],
            exchange_order_id=str(data.get("orderId","")), client_order_id=cid, mode=mode, position_side=params["positionSide"],
            metadata={"response": data, "account_config": cfg, "strategy": payload.strategy, "risk_per_unit": plan["risk_per_unit"]},
            break_even_armed=0, trail_armed=0, high_watermark=avg if payload.signal in ["long","buy"] else None, low_watermark=avg if payload.signal in ["short","sell"] else None
        )
    return {"submitted": ok, "exchange": "binance_futures", "position_id": pos_id, "client_order_id": cid, "response": resp["json"], "account_config": cfg, "reason": "ok" if ok else resp["text"]}

def execute(payload):
    if not TRADING_ENABLED: return {"approved": False, "reason": "trading disabled by kill switch"}
    if get_open_positions_count() >= MAX_CONCURRENT_POSITIONS: return {"approved": False, "reason": "max concurrent positions reached"}
    if BOT_SECRET != "CHANGE_ME" and payload.secret != BOT_SECRET: raise HTTPException(status_code=401, detail="invalid secret")
    exchange = payload.exchange or DEFAULT_EXCHANGE
    plan = build_order_plan(payload)
    if exchange == "paper":
        result = send_order_paper(payload, plan) if ENABLE_PAPER_TRADING else {"submitted": False, "reason": "paper trading disabled"}
    elif exchange == "binance_spot":
        result = send_order_spot(payload, plan) if ENABLE_EXECUTION else {"submitted": False, "reason": "live execution disabled"}
    elif exchange == "binance_futures":
        result = send_order_futures(payload, plan) if ENABLE_EXECUTION else {"submitted": False, "reason": "live execution disabled"}
    else:
        result = {"submitted": False, "reason": f"unsupported exchange: {exchange}"}
    approved = bool(result.get("submitted"))
    response = {"approved": approved, "exchange": exchange, "order_plan": plan, "result": result, "ts": now_iso()}
    journal(payload, approved, result.get("reason",""), response)
    return response

def close_spot_position(position):
    return signed_request("POST", BINANCE_SPOT_BASE_URL, "/api/v3/order", {
        "symbol": position["ticker"], "side": "SELL", "type": "MARKET",
        "quantity": f'{float(position["qty"]):.8f}'.rstrip("0").rstrip("."), "newOrderRespType": "FULL"
    }, BINANCE_SPOT_API_KEY, BINANCE_SPOT_API_SECRET)

def close_futures_position(position):
    side = "SELL" if position["signal"] in ["long","buy"] else "BUY"
    params = {"symbol": position["ticker"], "side": side, "type": "MARKET", "quantity": f'{float(position["qty"]):.8f}'.rstrip("0").rstrip("."), "newOrderRespType": "RESULT"}
    params["positionSide"] = position.get("position_side") or ("LONG" if position["signal"] in ["long","buy"] else "SHORT") if (position.get("mode") or "ONE_WAY").upper() == "HEDGE" else "BOTH"
    return signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/order", params, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)

def evaluate_reversal_score(position, payload):
    is_long = position["signal"] in ["long","buy"]
    checks = [
        (payload.seller_pressure if is_long else payload.buyer_pressure, "pressure_shift"),
        (payload.bearish_divergence if is_long else payload.bullish_divergence, "divergence"),
        (payload.bos_failure, "bos_failure"),
        (payload.momentum_drop, "momentum_drop"),
        (payload.trend_weakening, "trend_weakening"),
    ]
    score, reasons = 0, []
    for ok, name in checks:
        if ok:
            score += 1
            reasons.append(name)
    return {"reversal_score": score, "reasons": reasons}

def manage_position(position, current_price, signal_evidence):
    if position["status"] != "open": return {"ok": False, "reason": "position not open"}
    entry = float(position["entry_price"])
    stop = float(position["stop_price"] or entry)
    risk_per_unit = abs(entry - stop) or max(entry * 0.001, 0.000001)
    is_long = position["signal"] in ["long","buy"]
    high_watermark = float(position["high_watermark"] if position["high_watermark"] is not None else entry)
    low_watermark = float(position["low_watermark"] if position["low_watermark"] is not None else entry)
    if is_long:
        high_watermark = max(high_watermark, current_price)
        pnl_per_unit = current_price - entry
    else:
        low_watermark = min(low_watermark, current_price)
        pnl_per_unit = entry - current_price
    current_r = pnl_per_unit / risk_per_unit
    break_even_armed = int(position.get("break_even_armed") or 0) == 1
    trail_armed = int(position.get("trail_armed") or 0) == 1
    updates, actions = {"high_watermark": high_watermark, "low_watermark": low_watermark}, []
    if current_r >= BE_TRIGGER_R and not break_even_armed:
        updates["break_even_armed"] = 1
        updates["stop_price"] = entry
        break_even_armed = True
        actions.append({"type": "move_stop_to_be", "new_stop": entry})
    if current_r >= TRAIL_TRIGGER_R and not trail_armed:
        updates["trail_armed"] = 1
        trail_armed = True
        actions.append({"type": "arm_trailing", "trigger_r": current_r})
    current_stop = float(updates.get("stop_price", position["stop_price"] or entry))
    if trail_armed:
        if is_long:
            trailed = high_watermark - (risk_per_unit * TRAIL_DISTANCE_R)
            if trailed > current_stop:
                current_stop = trailed
                updates["stop_price"] = current_stop
                actions.append({"type": "raise_trailing_stop", "new_stop": current_stop})
        else:
            trailed = low_watermark + (risk_per_unit * TRAIL_DISTANCE_R)
            if trailed < current_stop:
                current_stop = trailed
                updates["stop_price"] = current_stop
                actions.append({"type": "lower_trailing_stop", "new_stop": current_stop})
    reversal = evaluate_reversal_score(position, signal_evidence)
    should_exit = reversal["reversal_score"] >= REVERSAL_SCORE_TO_EXIT
    stop_hit = (is_long and current_price <= current_stop) or ((not is_long) and current_price >= current_stop)
    if stop_hit:
        should_exit = True
        reversal["reasons"].append("stop_hit")
    close_result = None
    if should_exit and not signal_evidence.dry_run:
        if position["exchange"] == "paper":
            updates["status"] = "closed"
            close_result = {"submitted": True, "exchange": "paper", "reason": "paper position closed"}
        elif position["exchange"] == "binance_spot":
            close_result = close_spot_position(position)
            if close_result["status_code"] < 400: updates["status"] = "closed"
        elif position["exchange"] == "binance_futures":
            close_result = close_futures_position(position)
            if close_result["status_code"] < 400: updates["status"] = "closed"
        actions.append({"type": "close_position", "reason": reversal["reasons"], "close_result": close_result})
    append_position_metadata(position["id"], {"last_manage_check": {"at": now_iso(), "current_price": current_price, "current_r": current_r, "reversal_score": reversal["reversal_score"], "reversal_reasons": reversal["reasons"], "actions": actions}})
    update_position(position["id"], updates)
    return {"ok": True, "position_id": position["id"], "ticker": position["ticker"], "exchange": position["exchange"], "current_price": current_price, "current_r": current_r, "reversal_score": reversal["reversal_score"], "reversal_reasons": reversal["reasons"], "actions": actions, "position": get_position(position["id"])}

def get_open_futures_position_by_exchange_order_id(exchange_order_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM positions WHERE exchange='binance_futures' AND status='open' AND exchange_order_id=? ORDER BY created_at DESC LIMIT 1", (exchange_order_id,)).fetchone()
        return dict(row) if row else None

def get_open_futures_position_by_client_order_id(client_order_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM positions WHERE exchange='binance_futures' AND status='open' AND client_order_id=? ORDER BY created_at DESC LIMIT 1", (client_order_id,)).fetchone()
        return dict(row) if row else None

def sync_single_futures_position_from_exchange(position):
    resp = get_futures_position_risk(position["ticker"])
    if resp["status_code"] >= 400: return {"ok": False, "reason": resp["text"]}
    rows = resp.get("json", [])
    mode = (position.get("mode") or "ONE_WAY").upper()
    tracked_side = position.get("position_side") or "BOTH"
    is_long = position["signal"] in ["long","buy"]
    match = None
    for row in rows:
        row_side = row.get("positionSide", "BOTH")
        amt = float(row.get("positionAmt", 0) or 0)
        if mode == "HEDGE":
            if row_side == tracked_side and ((is_long and amt > 0) or ((not is_long) and amt < 0)):
                match = row
                break
        else:
            if row_side == "BOTH" and ((is_long and amt > 0) or ((not is_long) and amt < 0)):
                match = row
                break
    if not match:
        update_position(position["id"], {"status": "closed"})
        append_position_metadata(position["id"], {"ws_sync": {"at": now_iso(), "reason": "position not found on exchange, marked closed"}})
        return {"ok": True, "reason": "position missing, closed locally"}
    amt = abs(float(match.get("positionAmt", 0) or 0))
    entry = float(match.get("entryPrice", 0) or position["entry_price"])
    mark = float(match.get("markPrice", 0) or 0)
    update_position(position["id"], {"qty": amt, "entry_price": entry})
    append_position_metadata(position["id"], {"ws_sync": {"at": now_iso(), "mark_price": mark, "exchange_qty": amt, "exchange_entry": entry}})
    return {"ok": True, "reason": "synced", "mark_price": mark, "qty": amt, "entry_price": entry}

def handle_futures_order_trade_update(event):
    order = event.get("o", {})
    order_id = str(order.get("i", ""))
    client_order_id = str(order.get("c", ""))
    status = str(order.get("X", ""))
    avg_price = float(order.get("ap", 0) or 0)
    executed_qty = float(order.get("z", 0) or 0)
    symbol = order.get("s", "")
    position = get_open_futures_position_by_exchange_order_id(order_id) if order_id else None
    if not position and client_order_id:
        position = get_open_futures_position_by_client_order_id(client_order_id)
    if not position:
        return {"ok": True, "reason": "no matching tracked position", "symbol": symbol, "order_id": order_id}
    patch, actions = {}, []
    if avg_price > 0 and executed_qty > 0:
        patch["entry_price"] = avg_price
        patch["qty"] = executed_qty
        actions.append({"type": "sync_fill", "avg_price": avg_price, "qty": executed_qty})
    if status in ["FILLED","PARTIALLY_FILLED"]:
        actions.append({"type": "exchange_sync", "result": sync_single_futures_position_from_exchange(position)})
    if patch:
        update_position(position["id"], patch)
    append_position_metadata(position["id"], {"last_order_trade_update": {"at": now_iso(), "event": order, "actions": actions}})
    return {"ok": True, "position_id": position["id"], "actions": actions, "patch": patch}

def handle_futures_account_update(event):
    changed = []
    for pos in get_all_open_futures_positions():
        changed.append({"position_id": pos["id"], "result": sync_single_futures_position_from_exchange(pos)})
    return {"ok": True, "changed": changed}

def start_futures_listen_key():
    return api_key_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/listenKey", BINANCE_FUTURES_API_KEY)

def keepalive_futures_listen_key(listen_key):
    return api_key_request("PUT", BINANCE_FUTURES_BASE_URL, "/fapi/v1/listenKey", BINANCE_FUTURES_API_KEY, {"listenKey": listen_key})

def close_futures_listen_key(listen_key):
    return api_key_request("DELETE", BINANCE_FUTURES_BASE_URL, "/fapi/v1/listenKey", BINANCE_FUTURES_API_KEY, {"listenKey": listen_key})

async def _futures_keepalive_loop():
    while WS_STATE["running"] and WS_STATE["listen_key"]:
        try:
            await asyncio.sleep(45 * 60)
            WS_STATE["last_event"] = {"type": "listenKey.keepalive", "response": keepalive_futures_listen_key(WS_STATE["listen_key"]), "ts": now_iso()}
        except Exception as exc:
            WS_STATE["last_error"] = f"keepalive error: {exc}"
            await asyncio.sleep(5)

async def _futures_ws_loop():
    if websockets is None:
        WS_STATE["running"] = False
        WS_STATE["last_error"] = "websockets package not installed"
        return
    while WS_STATE["running"]:
        try:
            lk = start_futures_listen_key()
            if lk["status_code"] >= 400: raise RuntimeError(lk["text"])
            WS_STATE["listen_key"] = lk["json"]["listenKey"]
            keepalive_task = asyncio.create_task(_futures_keepalive_loop())
            async with websockets.connect(f"wss://fstream.binance.com/ws/{WS_STATE['listen_key']}", ping_interval=BINANCE_FUTURES_WS_PING_SECONDS, ping_timeout=20) as ws:
                WS_STATE["last_event"] = {"type": "ws.connected", "ts": now_iso()}
                while WS_STATE["running"]:
                    data = json.loads(await ws.recv())
                    event_type = data.get("e")
                    if event_type == "ORDER_TRADE_UPDATE":
                        WS_STATE["last_event"] = {"type": event_type, "result": handle_futures_order_trade_update(data), "ts": now_iso()}
                    elif event_type == "ACCOUNT_UPDATE":
                        WS_STATE["last_event"] = {"type": event_type, "result": handle_futures_account_update(data), "ts": now_iso()}
                    else:
                        WS_STATE["last_event"] = {"type": event_type, "data": data, "ts": now_iso()}
            keepalive_task.cancel()
        except Exception as exc:
            WS_STATE["last_error"] = str(exc)
            await asyncio.sleep(5)
        finally:
            if WS_STATE["listen_key"]:
                try: close_futures_listen_key(WS_STATE["listen_key"])
                except: pass
            WS_STATE["listen_key"] = None

@app.get("/")
def root():
    return {"status": "ok", "service": "binance-spot-futures-bot-pro-final", "time": now_iso()}

@app.get("/config")
def config():
    return {"default_exchange": DEFAULT_EXCHANGE, "execution_enabled": ENABLE_EXECUTION, "paper_enabled": ENABLE_PAPER_TRADING, "trading_enabled": TRADING_ENABLED, "be_trigger_r": BE_TRIGGER_R, "trail_trigger_r": TRAIL_TRIGGER_R, "trail_distance_r": TRAIL_DISTANCE_R, "reversal_score_to_exit": REVERSAL_SCORE_TO_EXIT}

@app.get("/pine-alert-templates")
def pine_alert_templates():
    return {
        "paper_long": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"paper","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "paper_short": '{"signal":"short","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"paper","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "spot_buy": '{"signal":"buy","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Swing Trading","exchange":"binance_spot","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "spot_sell": '{"signal":"sell","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Swing Trading","exchange":"binance_spot","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "futures_one_way_long": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"binance_futures","futures_mode":"one_way","margin_type":"isolated","leverage":3,"position_side":"BOTH","secret":"CHANGE_ME","strategy":"AI Trading Bot"}',
        "futures_one_way_short": '{"signal":"short","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"preset":"Day Trading","exchange":"binance_futures","futures_mode":"one_way","margin_type":"isolated","leverage":3,"position_side":"BOTH","secret":"CHANGE_ME","strategy":"AI Trading Bot"}'
    }

@app.get("/journal/recent")
def journal_recent(limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return {"rows": [dict(r) for r in conn.execute("SELECT * FROM signal_journal ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()]}

@app.get("/positions/open")
def positions_open():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return {"rows": [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='open' ORDER BY created_at DESC").fetchall()]}

@app.get("/positions/{position_id}")
def position_detail(position_id: str):
    pos = get_position(position_id)
    if not pos: raise HTTPException(status_code=404, detail="position not found")
    return pos

@app.post("/webhook/tradingview")
async def webhook(payload: SignalPayload):
    return execute(payload)

@app.post("/positions/manage")
def manage_open_position(payload: ManagePositionPayload):
    position = get_position(payload.position_id)
    if not position: raise HTTPException(status_code=404, detail="position not found")
    return manage_position(position, payload.current_price, payload)

@app.post("/positions/manage-all-paper")
def manage_all_paper(current_price_map: Dict[str, float]):
    out = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        positions = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='open' AND exchange='paper' ORDER BY created_at DESC").fetchall()]
    for p in positions:
        if p["ticker"] in current_price_map:
            out.append(manage_position(p, float(current_price_map[p["ticker"]]), ManagePositionPayload(position_id=p["id"], current_price=float(current_price_map[p["ticker"]]))))
    return {"count": len(out), "results": out}

@app.post("/positions/sync-market-price/{position_id}")
def sync_market_price(position_id: str):
    position = get_position(position_id)
    if not position: raise HTTPException(status_code=404, detail="position not found")
    if position["exchange"] == "binance_spot":
        current_price = get_spot_last_price(position["ticker"])
    elif position["exchange"] == "binance_futures":
        current_price = get_futures_last_price(position["ticker"])
    else:
        raise HTTPException(status_code=400, detail="use /positions/manage for paper positions")
    return manage_position(position, current_price, ManagePositionPayload(position_id=position_id, current_price=current_price, dry_run=True))

@app.post("/positions/close/{position_id}")
def close_position_manual(position_id: str):
    position = get_position(position_id)
    if not position: raise HTTPException(status_code=404, detail="position not found")
    if position["status"] != "open": return {"ok": False, "reason": "position already closed"}
    if position["exchange"] == "paper":
        update_position(position_id, {"status": "closed"})
        return {"ok": True, "exchange": "paper", "reason": "paper position closed"}
    if position["exchange"] == "binance_spot":
        result = close_spot_position(position)
        if result["status_code"] < 400: update_position(position_id, {"status": "closed"})
        return {"ok": result["status_code"] < 400, "exchange": "binance_spot", "result": result}
    if position["exchange"] == "binance_futures":
        result = close_futures_position(position)
        if result["status_code"] < 400: update_position(position_id, {"status": "closed"})
        return {"ok": result["status_code"] < 400, "exchange": "binance_futures", "result": result}
    raise HTTPException(status_code=400, detail="unsupported exchange")

@app.post("/futures/ws/start")
async def futures_ws_start():
    if WS_STATE["running"]: return {"ok": True, "reason": "already running"}
    WS_STATE["running"] = True
    WS_STATE["last_error"] = None
    WS_STATE["task"] = asyncio.create_task(_futures_ws_loop())
    return {"ok": True, "reason": "started"}

@app.post("/futures/ws/stop")
async def futures_ws_stop():
    WS_STATE["running"] = False
    task = WS_STATE.get("task")
    if task: task.cancel()
    WS_STATE["task"] = None
    return {"ok": True, "reason": "stopped"}

@app.get("/futures/ws/status")
def futures_ws_status():
    return {"running": WS_STATE["running"], "listen_key": WS_STATE["listen_key"], "last_event": WS_STATE["last_event"], "last_error": WS_STATE["last_error"]}

@app.post("/futures/sync-open")
def futures_sync_open_positions():
    positions = get_all_open_futures_positions()
    return {"count": len(positions), "results": [sync_single_futures_position_from_exchange(p) for p in positions]}
    def structure_filter(data):
    highs = data.get("highs", [])
    lows = data.get("lows", [])

    if len(highs) < 3 or len(lows) < 3:
        return {"allow": False, "reason": "not enough structure"}

    # simple structure logic
    hh = highs[-1] > highs[-2]
    hl = lows[-1] > lows[-2]
    lh = highs[-1] < highs[-2]
    ll = lows[-1] < lows[-2]

    trend = None

    if hh and hl:
        trend = "bullish"
    elif ll and lh:
        trend = "bearish"
    else:
        trend = "sideways"

    if trend == "sideways":
        return {"allow": False, "reason": "sideways market"}

    return {"allow": True, "trend": trend}
