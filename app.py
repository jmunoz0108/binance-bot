
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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

app = FastAPI(title="Binance Spot + Futures Bot PRO Final + Structure")

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

SCANNER_ENABLED_DEFAULT = os.getenv("SCANNER_ENABLED_DEFAULT", "false").lower() == "true"
SCANNER_EXCHANGE = os.getenv("SCANNER_EXCHANGE", "paper")
SCANNER_SYMBOLS = [s.strip().upper() for s in os.getenv("SCANNER_SYMBOLS", "DOGEUSDT,SOLUSDT,XRPUSDT,ETHUSDT,BTCUSDT").split(",") if s.strip()]
SCANNER_INTERVAL = os.getenv("SCANNER_INTERVAL", "5m")
SCANNER_SLEEP_SECONDS = int(os.getenv("SCANNER_SLEEP_SECONDS", "60"))
SCANNER_LIMIT = int(os.getenv("SCANNER_LIMIT", "120"))
SCANNER_COOLDOWN_SECONDS = int(os.getenv("SCANNER_COOLDOWN_SECONDS", "900"))
SCANNER_MIN_QUALITY = int(os.getenv("SCANNER_MIN_QUALITY", "2"))
SCANNER_LEVERAGE = int(os.getenv("SCANNER_LEVERAGE", "2"))


CONTINUATION_ENABLED = os.getenv("CONTINUATION_ENABLED", "true").lower() == "true"
CONTINUATION_PULLBACK_MIN_PCT = float(os.getenv("CONTINUATION_PULLBACK_MIN_PCT", "0.25"))
CONTINUATION_PULLBACK_MAX_PCT = float(os.getenv("CONTINUATION_PULLBACK_MAX_PCT", "2.50"))
CONTINUATION_LOOKBACK = int(os.getenv("CONTINUATION_LOOKBACK", "8"))


AUTO_DISCOVER_SYMBOLS = os.getenv("AUTO_DISCOVER_SYMBOLS", "false").lower() == "true"
AUTO_DISCOVER_TOP_N = int(os.getenv("AUTO_DISCOVER_TOP_N", "30"))
AUTO_DISCOVER_MIN_QUOTE_VOLUME = float(os.getenv("AUTO_DISCOVER_MIN_QUOTE_VOLUME", "50000000"))
AUTO_DISCOVER_REFRESH_SECONDS = int(os.getenv("AUTO_DISCOVER_REFRESH_SECONDS", "1800"))
AUTO_DISCOVER_EXCLUDE = [s.strip().upper() for s in os.getenv("AUTO_DISCOVER_EXCLUDE", "").split(",") if s.strip()]
AUTO_DISCOVER_INCLUDE_NEW = os.getenv("AUTO_DISCOVER_INCLUDE_NEW", "true").lower() == "true"

DISCOVERY_STATE: Dict[str, Any] = {
    "last_refresh": None,
    "symbols": [],
    "last_error": None,
    "raw_count": 0,
}

SCANNER_STATE: Dict[str, Any] = {
    "task": None,
    "running": False,
    "last_scan": None,
    "last_results": [],
    "last_error": None,
    "signals_sent": {},
}


class SignalPayload(BaseModel):
    signal: Literal["long", "short", "buy", "sell"]
    ticker: str
    tf: str = "5"
    close: float
    score: float = 0
    preset: str = "Day Trading"
    exchange: Optional[Literal["paper", "binance_spot", "binance_futures"]] = None
    secret: Optional[str] = None
    futures_mode: Optional[Literal["one_way", "hedge"]] = None
    margin_type: Optional[Literal["isolated", "cross"]] = None
    leverage: Optional[int] = None
    position_side: Optional[Literal["LONG", "SHORT", "BOTH"]] = None
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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_journal (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            ticker TEXT,
            timeframe TEXT,
            signal TEXT,
            exchange TEXT,
            approved INTEGER,
            reason TEXT,
            payload_json TEXT,
            response_json TEXT
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            ticker TEXT,
            timeframe TEXT,
            signal TEXT,
            exchange TEXT,
            status TEXT,
            entry_price REAL,
            stop_price REAL,
            qty REAL,
            risk_usd REAL,
            exchange_order_id TEXT,
            client_order_id TEXT,
            mode TEXT,
            position_side TEXT,
            metadata_json TEXT,
            break_even_armed INTEGER DEFAULT 0,
            trail_armed INTEGER DEFAULT 0,
            high_watermark REAL,
            low_watermark REAL
        )""")
        conn.commit()


init_db()


def signed_request(method: str, base_url: str, path: str, params: Dict[str, Any], api_key: str, api_secret: str):
    payload = dict(params)
    payload["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
    payload["recvWindow"] = 5000
    query = urlencode(payload)
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{base_url}{path}?{query}&signature={sig}"
    with httpx.Client(timeout=20.0, headers={"X-MBX-APIKEY": api_key}) as client:
        r = client.request(method.upper(), url)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return {"status_code": r.status_code, "json": data, "text": r.text}


def public_get(base_url: str, path: str, params: Optional[Dict[str, Any]] = None):
    with httpx.Client(timeout=20.0) as client:
        r = client.get(f"{base_url}{path}", params=params or {})
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        return {"status_code": r.status_code, "json": data, "text": r.text}


def api_key_request(method: str, base_url: str, path: str, api_key: str, params: Optional[Dict[str, Any]] = None):
    with httpx.Client(timeout=20.0, headers={"X-MBX-APIKEY": api_key}) as client:
        r = client.request(method.upper(), f"{base_url}{path}", params=params or {})
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


def get_open_positions_count():
    with sqlite3.connect(DB_PATH) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0] or 0)


def get_position(position_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        return dict(row) if row else None


def update_position(position_id: str, updates: Dict[str, Any]):
    if not updates:
        return
    keys = list(updates.keys())
    values = [updates[k] for k in keys]
    set_clause = ", ".join(f"{k}=?" for k in keys)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(f"UPDATE positions SET {set_clause} WHERE id=?", values + [position_id])
        conn.commit()


def append_position_metadata(position_id: str, patch: Dict[str, Any]):
    pos = get_position(position_id)
    if not pos:
        return
    try:
        meta = json.loads(pos.get("metadata_json") or "{}")
    except Exception:
        meta = {}
    meta.update(patch)
    update_position(position_id, {"metadata_json": json.dumps(meta, default=str)})


def journal(payload: SignalPayload, approved: bool, reason: str, response: Dict[str, Any]):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO signal_journal VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()), now_iso(), payload.ticker, payload.tf, payload.signal,
                payload.exchange or DEFAULT_EXCHANGE, 1 if approved else 0, reason,
                json.dumps(payload.model_dump(), default=str), json.dumps(response, default=str),
            ),
        )
        conn.commit()


def persist_position(**row):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["id"], row["created_at"], row["ticker"], row["timeframe"], row["signal"],
                row["exchange"], row["status"], row["entry_price"], row.get("stop_price"),
                row.get("qty"), row.get("risk_usd"), row.get("exchange_order_id"), row.get("client_order_id"),
                row.get("mode"), row.get("position_side"), json.dumps(row.get("metadata", {}), default=str),
                int(row.get("break_even_armed", 0)), int(row.get("trail_armed", 0)),
                row.get("high_watermark"), row.get("low_watermark"),
            ),
        )
        conn.commit()


def structure_filter(payload: SignalPayload):
    extra = payload.extra or {}
    highs = extra.get("highs", [])
    lows = extra.get("lows", [])

    if len(highs) < 3 or len(lows) < 3:
        return {"allow": True, "trend": "unknown", "reason": "no structure data"}

    try:
        highs = [float(x) for x in highs]
        lows = [float(x) for x in lows]
    except Exception:
        return {"allow": True, "trend": "unknown", "reason": "bad structure data ignored"}

    hh = highs[-1] > highs[-2]
    hl = lows[-1] > lows[-2]
    lh = highs[-1] < highs[-2]
    ll = lows[-1] < lows[-2]

    if hh and hl:
        return {"allow": True, "trend": "bullish", "reason": "bullish structure"}

    if lh and ll:
        return {"allow": True, "trend": "bearish", "reason": "bearish structure"}

    return {"allow": False, "trend": "sideways", "reason": "sideways structure"}



def get_orderbook_pressure(symbol: str):
    """
    Binance Futures orderbook pressure filter.
    Uses top 20 levels of futures depth.
    Returns buyers / sellers / neutral / unknown.
    """
    symbol = symbol.upper().replace(".P", "")
    try:
        r = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/depth", {"symbol": symbol, "limit": 20})
        if r["status_code"] >= 400:
            return {"pressure": "unknown", "reason": r["text"]}

        bids = r["json"].get("bids", [])
        asks = r["json"].get("asks", [])

        bid_qty = sum(float(b[1]) for b in bids)
        ask_qty = sum(float(a[1]) for a in asks)

        if bid_qty <= 0 or ask_qty <= 0:
            return {"pressure": "unknown", "reason": "empty book", "bid_qty": bid_qty, "ask_qty": ask_qty}

        buyer_ratio = bid_qty / ask_qty
        seller_ratio = ask_qty / bid_qty

        if buyer_ratio >= 1.20:
            return {
        "paper_long_bos_sweep": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"exchange":"paper","secret":"CHANGE_ME","extra":{"highs":[{{high[2]}},{{high[1]}},{{high}}],"lows":[{{low[2]}},{{low[1]}},{{low}}],"bos":true,"sweep":false,"sweep_direction":"none","volume_spike":true,"candle_confirm":true}}',
        "futures_long_bos_sweep": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"exchange":"binance_futures","futures_mode":"one_way","margin_type":"isolated","leverage":2,"position_side":"BOTH","secret":"CHANGE_ME","extra":{"highs":[{{high[2]}},{{high[1]}},{{high}}],"lows":[{{low[2]}},{{low[1]}},{{low}}],"bos":true,"sweep":false,"sweep_direction":"none","volume_spike":true,"candle_confirm":true}}',
        "futures_short_bos_sweep": '{"signal":"short","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"exchange":"binance_futures","futures_mode":"one_way","margin_type":"isolated","leverage":2,"position_side":"BOTH","secret":"CHANGE_ME","extra":{"highs":[{{high[2]}},{{high[1]}},{{high}}],"lows":[{{low[2]}},{{low[1]}},{{low}}],"bos":true,"sweep":false,"sweep_direction":"none","volume_spike":true,"candle_confirm":true}}',
                "pressure": "buyers",
                "ratio": buyer_ratio,
                "bid_qty": bid_qty,
                "ask_qty": ask_qty,
                "levels": 20
            }

        if seller_ratio >= 1.20:
            return {
                "pressure": "sellers",
                "ratio": seller_ratio,
                "bid_qty": bid_qty,
                "ask_qty": ask_qty,
                "levels": 20
            }

        return {
            "pressure": "neutral",
            "ratio": 1.0,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "levels": 20
        }

    except Exception as exc:
        return {"pressure": "unknown", "reason": str(exc)}




def continuation_filter(payload: SignalPayload, structure: Dict[str, Any]):
    """
    Optional continuation mode.
    TradingView can send:
      continuation: true/false
      continuation_direction: bullish/bearish/none
      pullback_pct: number
      minor_break: true/false

    This allows trend pullback continuation entries, so the bot can catch
    big moves AFTER the first BOS/sweep instead of only the first breakout.
    """
    extra = payload.extra or {}
    if not CONTINUATION_ENABLED:
        return {"allow": False, "reason": "continuation disabled", "mode": "off"}

    continuation = bool(extra.get("continuation", False))
    continuation_direction = str(extra.get("continuation_direction", "none")).lower()
    minor_break = bool(extra.get("minor_break", False))
    try:
        pullback_pct = float(extra.get("pullback_pct", 0))
    except Exception:
        pullback_pct = 0.0

    if not continuation:
        return {"allow": False, "reason": "not continuation setup", "mode": "none"}

    trend = structure.get("trend", "unknown")
    signal = payload.signal

    valid_pullback = CONTINUATION_PULLBACK_MIN_PCT <= pullback_pct <= CONTINUATION_PULLBACK_MAX_PCT

    if signal in ["long", "buy"]:
        ok = trend == "bullish" and continuation_direction == "bullish" and minor_break and valid_pullback
        return {
            "allow": ok,
            "reason": "continuation long passed" if ok else "continuation long failed",
            "mode": "continuation",
            "continuation": continuation,
            "continuation_direction": continuation_direction,
            "minor_break": minor_break,
            "pullback_pct": pullback_pct,
            "valid_pullback": valid_pullback,
        }

    if signal in ["short", "sell"]:
        ok = trend == "bearish" and continuation_direction == "bearish" and minor_break and valid_pullback
        return {
            "allow": ok,
            "reason": "continuation short passed" if ok else "continuation short failed",
            "mode": "continuation",
            "continuation": continuation,
            "continuation_direction": continuation_direction,
            "minor_break": minor_break,
            "pullback_pct": pullback_pct,
            "valid_pullback": valid_pullback,
        }

    return {"allow": False, "reason": "unknown signal", "mode": "continuation"}


def setup_quality_filter(payload: SignalPayload, structure: Dict[str, Any]):
    """
    BOS + Liquidity Sweep setup filter.
    TradingView sends these inside payload.extra:
      bos: true/false
      sweep: true/false
      sweep_direction: "bullish" / "bearish" / "none"
      volume_spike: true/false
      candle_confirm: true/false

    Rules:
      LONG: needs bullish structure AND (BOS or bullish sweep)
      SHORT: needs bearish structure AND (BOS or bearish sweep)
      volume_spike and candle_confirm are optional quality boosters.
    """
    extra = payload.extra or {}

    bos = bool(extra.get("bos", False))
    sweep = bool(extra.get("sweep", False))
    sweep_direction = str(extra.get("sweep_direction", "none")).lower()
    volume_spike = bool(extra.get("volume_spike", False))
    candle_confirm = bool(extra.get("candle_confirm", False))

    signal = payload.signal
    trend = structure.get("trend", "unknown")

    # If no structure data is sent, do not hard-block old alerts yet.
    if trend == "unknown":
        return {
            "allow": True,
            "reason": "no structure data, setup filter bypassed",
            "bos": bos,
            "sweep": sweep,
            "sweep_direction": sweep_direction,
            "volume_spike": volume_spike,
            "candle_confirm": candle_confirm,
            "quality_score": 0
        }

    bullish_trigger = bos or (sweep and sweep_direction == "bullish")
    bearish_trigger = bos or (sweep and sweep_direction == "bearish")

    quality_score = 0
    if bos:
        quality_score += 1
    if sweep:
        quality_score += 1
    if volume_spike:
        quality_score += 1
    if candle_confirm:
        quality_score += 1

    if signal in ["long", "buy"]:
        if trend != "bullish":
            return {
                "allow": False,
                "reason": "blocked: long requires bullish structure",
                "bos": bos,
                "sweep": sweep,
                "sweep_direction": sweep_direction,
                "volume_spike": volume_spike,
                "candle_confirm": candle_confirm,
                "quality_score": quality_score
            }

        if not bullish_trigger:
            cont = continuation_filter(payload, structure)
            if cont.get("allow"):
                quality_score += 1
                return {
                    "allow": True,
                    "reason": "setup quality passed by continuation mode",
                    "bos": bos,
                    "sweep": sweep,
                    "sweep_direction": sweep_direction,
                    "volume_spike": volume_spike,
                    "candle_confirm": candle_confirm,
                    "quality_score": quality_score,
                    "continuation": cont
                }
            return {
                "allow": False,
                "reason": "blocked: long needs BOS, bullish sweep, or continuation",
                "bos": bos,
                "sweep": sweep,
                "sweep_direction": sweep_direction,
                "volume_spike": volume_spike,
                "candle_confirm": candle_confirm,
                "quality_score": quality_score,
                "continuation": cont
            }

    if signal in ["short", "sell"]:
        if trend != "bearish":
            return {
                "allow": False,
                "reason": "blocked: short requires bearish structure",
                "bos": bos,
                "sweep": sweep,
                "sweep_direction": sweep_direction,
                "volume_spike": volume_spike,
                "candle_confirm": candle_confirm,
                "quality_score": quality_score
            }

        if not bearish_trigger:
            cont = continuation_filter(payload, structure)
            if cont.get("allow"):
                quality_score += 1
                return {
                    "allow": True,
                    "reason": "setup quality passed by continuation mode",
                    "bos": bos,
                    "sweep": sweep,
                    "sweep_direction": sweep_direction,
                    "volume_spike": volume_spike,
                    "candle_confirm": candle_confirm,
                    "quality_score": quality_score,
                    "continuation": cont
                }
            return {
                "allow": False,
                "reason": "blocked: short needs BOS, bearish sweep, or continuation",
                "bos": bos,
                "sweep": sweep,
                "sweep_direction": sweep_direction,
                "volume_spike": volume_spike,
                "candle_confirm": candle_confirm,
                "quality_score": quality_score,
                "continuation": cont
            }

    return {
        "allow": True,
        "reason": "setup quality passed",
        "bos": bos,
        "sweep": sweep,
        "sweep_direction": sweep_direction,
        "volume_spike": volume_spike,
        "candle_confirm": candle_confirm,
        "quality_score": quality_score
    }


def build_order_plan(payload: SignalPayload):
    entry = float(payload.close)
    risk_pct = min(MAX_RISK_PCT_PER_TRADE, 0.50 if payload.preset in ["Scalping", "Day Trading"] else 0.75)
    stop_distance_pct = 0.7 if payload.tf in ["1", "3", "5"] else 1.0 if payload.tf in ["15", "30"] else 1.5
    risk_usd = ACCOUNT_EQUITY_USD * (risk_pct / 100.0)

    if payload.signal in ["long", "buy"]:
        stop = entry * (1 - stop_distance_pct / 100.0)
        risk_per_unit = max(entry - stop, entry * 0.0005)
    else:
        stop = entry * (1 + stop_distance_pct / 100.0)
        risk_per_unit = max(stop - entry, entry * 0.0005)

    qty = risk_usd / risk_per_unit if risk_per_unit > 0 else 0.0
    return {"entry": entry, "stop": stop, "risk_pct": risk_pct, "risk_usd": risk_usd, "qty": qty, "risk_per_unit": risk_per_unit}


def symbol_filters_spot(symbol: str):
    info = public_get(BINANCE_SPOT_BASE_URL, "/api/v3/exchangeInfo", {"symbol": symbol})
    if info["status_code"] >= 400 or not info["json"].get("symbols"):
        raise RuntimeError(f"spot symbol not found: {symbol}")
    return {f["filterType"]: f for f in info["json"]["symbols"][0].get("filters", [])}


def symbol_filters_futures(symbol: str):
    info = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/exchangeInfo", {})
    if info["status_code"] >= 400:
        raise RuntimeError(info["text"])
    matches = [s for s in info["json"].get("symbols", []) if s.get("symbol") == symbol]
    if not matches:
        raise RuntimeError(f"futures symbol not found: {symbol}")
    return {f["filterType"]: f for f in matches[0].get("filters", [])}


def send_order_paper(payload: SignalPayload, plan: Dict[str, Any]):
    pos_id = str(uuid.uuid4())
    entry = plan["entry"]
    persist_position(
        id=pos_id, created_at=now_iso(), ticker=payload.ticker.upper(), timeframe=payload.tf, signal=payload.signal,
        exchange="paper", status="open", entry_price=entry, stop_price=plan["stop"], qty=plan["qty"],
        risk_usd=plan["risk_usd"], exchange_order_id=None, client_order_id=None, mode=None, position_side=None,
        metadata={"strategy": payload.strategy, "risk_per_unit": plan["risk_per_unit"]},
        break_even_armed=0, trail_armed=0,
        high_watermark=entry if payload.signal in ["long", "buy"] else None,
        low_watermark=entry if payload.signal in ["short", "sell"] else None,
    )
    return {"submitted": True, "exchange": "paper", "position_id": pos_id, "reason": "paper trade recorded"}


def send_order_spot(payload: SignalPayload, plan: Dict[str, Any]):
    if not BINANCE_SPOT_API_KEY or not BINANCE_SPOT_API_SECRET:
        return {"submitted": False, "reason": "missing Binance Spot credentials", "exchange": "binance_spot"}

    symbol = payload.ticker.upper().replace(".P", "")
    side = "BUY" if payload.signal in ["long", "buy"] else "SELL"
    filters = symbol_filters_spot(symbol)
    lot = filters.get("LOT_SIZE", {})
    qty = max(floor_to_step(plan["qty"], float(lot.get("stepSize", "0"))), float(lot.get("minQty", "0")))
    cid = f"spot-{uuid.uuid4().hex[:20]}"

    resp = signed_request("POST", BINANCE_SPOT_BASE_URL, "/api/v3/order", {
        "symbol": symbol, "side": side, "type": "MARKET",
        "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
        "newClientOrderId": cid, "newOrderRespType": "FULL",
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
            risk_usd=plan["risk_usd"], exchange_order_id=str(data.get("orderId", "")), client_order_id=cid,
            mode="spot", position_side="LONG_ONLY",
            metadata={"response": data, "strategy": payload.strategy, "risk_per_unit": plan["risk_per_unit"]},
            break_even_armed=0, trail_armed=0,
            high_watermark=avg if payload.signal in ["long", "buy"] else None,
            low_watermark=None,
        )
    return {"submitted": ok, "exchange": "binance_spot", "position_id": pos_id, "client_order_id": cid, "response": resp["json"], "reason": "ok" if ok else resp["text"]}


def configure_futures(symbol: str, mode: str, margin_type: str, leverage: int):
    dual = "true" if mode == "HEDGE" else "false"
    return {
        "position_mode": signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/positionSide/dual", {"dualSidePosition": dual}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET),
        "margin_type": signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET),
        "leverage": signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET),
    }


def send_order_futures(payload: SignalPayload, plan: Dict[str, Any]):
    if not BINANCE_FUTURES_API_KEY or not BINANCE_FUTURES_API_SECRET:
        return {"submitted": False, "reason": "missing Binance Futures credentials", "exchange": "binance_futures"}

    symbol = payload.ticker.upper().replace(".P", "")
    side = "BUY" if payload.signal in ["long", "buy"] else "SELL"
    mode = "HEDGE" if (payload.futures_mode or BINANCE_FUTURES_POSITION_MODE).upper() in ["HEDGE", "HEDGE_MODE"] else "ONE_WAY"
    margin_type = "CROSSED" if (payload.margin_type or BINANCE_FUTURES_MARGIN_TYPE).upper() in ["CROSS", "CROSSED"] else "ISOLATED"
    leverage = max(1, min(int(payload.leverage or BINANCE_FUTURES_LEVERAGE), 125))

    filters = symbol_filters_futures(symbol)
    lot = filters.get("LOT_SIZE", {})
    qty = max(floor_to_step(plan["qty"], float(lot.get("stepSize", "0"))), float(lot.get("minQty", "0")))

    cfg = configure_futures(symbol, mode, margin_type, leverage)
    cid = f"fut-{uuid.uuid4().hex[:20]}"
    params = {
        "symbol": symbol, "side": side, "type": "MARKET",
        "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
        "newClientOrderId": cid, "newOrderRespType": "RESULT",
    }
    params["positionSide"] = payload.position_side or (("LONG" if payload.signal in ["long", "buy"] else "SHORT") if mode == "HEDGE" else "BOTH")

    resp = signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/order", params, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)

    ok = resp["status_code"] < 400
    pos_id = None
    if ok:
        data = resp["json"]
        avg = float(data.get("avgPrice", 0) or 0) or plan["entry"]
        executed_qty = float(data.get("executedQty", qty) or qty)

        # Recalculate stop from real fill, not fake TradingView close.
        if payload.signal in ["long", "buy"]:
            fill_stop = avg - plan["risk_per_unit"]
        else:
            fill_stop = avg + plan["risk_per_unit"]

        pos_id = str(uuid.uuid4())
        persist_position(
            id=pos_id, created_at=now_iso(), ticker=symbol, timeframe=payload.tf, signal=payload.signal,
            exchange="binance_futures", status="open", entry_price=avg, stop_price=fill_stop, qty=executed_qty,
            risk_usd=plan["risk_usd"], exchange_order_id=str(data.get("orderId", "")), client_order_id=cid,
            mode=mode, position_side=params["positionSide"],
            metadata={"response": data, "account_config": cfg, "strategy": payload.strategy, "risk_per_unit": plan["risk_per_unit"], "original_plan": plan},
            break_even_armed=0, trail_armed=0,
            high_watermark=avg if payload.signal in ["long", "buy"] else None,
            low_watermark=avg if payload.signal in ["short", "sell"] else None,
        )

    return {"submitted": ok, "exchange": "binance_futures", "position_id": pos_id, "client_order_id": cid, "response": resp["json"], "account_config": cfg, "reason": "ok" if ok else resp["text"]}


def execute(payload: SignalPayload):
    if not TRADING_ENABLED:
        return {"approved": False, "reason": "trading disabled by kill switch"}

    if get_open_positions_count() >= MAX_CONCURRENT_POSITIONS:
        return {"approved": False, "reason": "max concurrent positions reached"}

    if BOT_SECRET != "CHANGE_ME" and payload.secret != BOT_SECRET:
        raise HTTPException(status_code=401, detail="invalid secret")

    structure = structure_filter(payload)

    orderbook = get_orderbook_pressure(payload.ticker)

    if payload.signal in ["long", "buy"] and orderbook.get("pressure") == "sellers":
        response = {
            "approved": False,
            "reason": "blocked: strong sellers in orderbook",
            "structure": structure,
            "orderbook": orderbook,
            "ts": now_iso()
        }
        journal(payload, False, "blocked: strong sellers in orderbook", response)
        return response

    if payload.signal in ["short", "sell"] and orderbook.get("pressure") == "buyers":
        response = {
            "approved": False,
            "reason": "blocked: strong buyers in orderbook",
            "structure": structure,
            "orderbook": orderbook,
            "ts": now_iso()
        }
        journal(payload, False, "blocked: strong buyers in orderbook", response)
        return response

    setup_quality = setup_quality_filter(payload, structure)

    if not setup_quality["allow"]:
        response = {
            "approved": False,
            "reason": setup_quality["reason"],
            "structure": structure,
            "orderbook": orderbook,
            "setup_quality": setup_quality,
            "ts": now_iso()
        }
        journal(payload, False, setup_quality["reason"], response)
        return response

    if not structure["allow"]:
        response = {"approved": False, "reason": structure["reason"], "structure": structure, "orderbook": orderbook, "setup_quality": setup_quality, "ts": now_iso()}
        journal(payload, False, structure["reason"], response)
        return response

    if payload.signal in ["long", "buy"] and structure.get("trend") == "bearish":
        response = {"approved": False, "reason": "blocked: bearish structure", "structure": structure, "orderbook": orderbook, "setup_quality": setup_quality, "ts": now_iso()}
        journal(payload, False, "blocked: bearish structure", response)
        return response

    if payload.signal in ["short", "sell"] and structure.get("trend") == "bullish":
        response = {"approved": False, "reason": "blocked: bullish structure", "structure": structure, "orderbook": orderbook, "setup_quality": setup_quality, "ts": now_iso()}
        journal(payload, False, "blocked: bullish structure", response)
        return response

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
    response = {"approved": approved, "exchange": exchange, "structure": structure, "orderbook": orderbook, "setup_quality": setup_quality, "order_plan": plan, "result": result, "ts": now_iso()}
    journal(payload, approved, result.get("reason", ""), response)
    return response


def close_spot_position(position):
    return signed_request("POST", BINANCE_SPOT_BASE_URL, "/api/v3/order", {
        "symbol": position["ticker"], "side": "SELL", "type": "MARKET",
        "quantity": f'{float(position["qty"]):.8f}'.rstrip("0").rstrip("."),
        "newOrderRespType": "FULL",
    }, BINANCE_SPOT_API_KEY, BINANCE_SPOT_API_SECRET)


def close_futures_position(position):
    side = "SELL" if position["signal"] in ["long", "buy"] else "BUY"
    params = {
        "symbol": position["ticker"], "side": side, "type": "MARKET",
        "quantity": f'{float(position["qty"]):.8f}'.rstrip("0").rstrip("."),
        "newOrderRespType": "RESULT",
    }
    if (position.get("mode") or "ONE_WAY").upper() == "HEDGE":
        params["positionSide"] = position.get("position_side") or ("LONG" if position["signal"] in ["long", "buy"] else "SHORT")
    else:
        params["positionSide"] = "BOTH"
    return signed_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/order", params, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)


def evaluate_reversal_score(position, payload: ManagePositionPayload):
    is_long = position["signal"] in ["long", "buy"]
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


def manage_position(position, current_price: float, signal_evidence: ManagePositionPayload):
    if position["status"] != "open":
        return {"ok": False, "reason": "position not open"}

    entry = float(position["entry_price"])
    stop = float(position["stop_price"] or entry)
    risk_per_unit = abs(entry - stop) or max(entry * 0.001, 0.000001)
    is_long = position["signal"] in ["long", "buy"]

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
    updates = {"high_watermark": high_watermark, "low_watermark": low_watermark}
    actions = []

    if current_r >= BE_TRIGGER_R and not break_even_armed:
        updates["break_even_armed"] = 1
        updates["stop_price"] = entry
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
            if close_result["status_code"] < 400:
                updates["status"] = "closed"
        elif position["exchange"] == "binance_futures":
            close_result = close_futures_position(position)
            if close_result["status_code"] < 400:
                updates["status"] = "closed"
        actions.append({"type": "close_position", "reason": reversal["reasons"], "close_result": close_result})

    append_position_metadata(position["id"], {"last_manage_check": {"at": now_iso(), "current_price": current_price, "current_r": current_r, "reversal_score": reversal["reversal_score"], "reversal_reasons": reversal["reasons"], "actions": actions}})
    update_position(position["id"], updates)

    return {"ok": True, "position_id": position["id"], "ticker": position["ticker"], "exchange": position["exchange"], "current_price": current_price, "current_r": current_r, "reversal_score": reversal["reversal_score"], "reversal_reasons": reversal["reasons"], "actions": actions, "position": get_position(position["id"])}


def get_futures_position_risk(symbol: str):
    return signed_request("GET", BINANCE_FUTURES_BASE_URL, "/fapi/v3/positionRisk", {"symbol": symbol}, BINANCE_FUTURES_API_KEY, BINANCE_FUTURES_API_SECRET)


def get_all_open_futures_positions():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM positions WHERE exchange='binance_futures' AND status='open' ORDER BY created_at DESC").fetchall()]


def sync_single_futures_position_from_exchange(position):
    resp = get_futures_position_risk(position["ticker"])
    if resp["status_code"] >= 400:
        return {"ok": False, "reason": resp["text"]}

    rows = resp.get("json", [])
    mode = (position.get("mode") or "ONE_WAY").upper()
    tracked_side = position.get("position_side") or "BOTH"
    is_long = position["signal"] in ["long", "buy"]
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


def get_open_futures_position_by_client_order_id(client_order_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM positions WHERE exchange='binance_futures' AND status='open' AND client_order_id=? ORDER BY created_at DESC LIMIT 1", (client_order_id,)).fetchone()
        return dict(row) if row else None


def get_open_futures_position_by_exchange_order_id(exchange_order_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM positions WHERE exchange='binance_futures' AND status='open' AND exchange_order_id=? ORDER BY created_at DESC LIMIT 1", (exchange_order_id,)).fetchone()
        return dict(row) if row else None


def handle_futures_order_trade_update(event):
    order = event.get("o", {})
    order_id = str(order.get("i", ""))
    client_order_id = str(order.get("c", ""))
    status = str(order.get("X", ""))
    avg_price = float(order.get("ap", 0) or 0)
    executed_qty = float(order.get("z", 0) or 0)
    position = get_open_futures_position_by_exchange_order_id(order_id) if order_id else None

    if not position and client_order_id:
        position = get_open_futures_position_by_client_order_id(client_order_id)

    if not position:
        return {"ok": True, "reason": "no matching tracked position"}

    patch = {}
    actions = []

    if avg_price > 0 and executed_qty > 0:
        patch["entry_price"] = avg_price
        patch["qty"] = executed_qty
        actions.append({"type": "sync_fill", "avg_price": avg_price, "qty": executed_qty})

    if status in ["FILLED", "PARTIALLY_FILLED"]:
        actions.append({"type": "exchange_sync", "result": sync_single_futures_position_from_exchange(position)})

    if patch:
        update_position(position["id"], patch)

    append_position_metadata(position["id"], {"last_order_trade_update": {"at": now_iso(), "event": order, "actions": actions}})
    return {"ok": True, "position_id": position["id"], "actions": actions}


def start_futures_listen_key():
    return api_key_request("POST", BINANCE_FUTURES_BASE_URL, "/fapi/v1/listenKey", BINANCE_FUTURES_API_KEY)


def keepalive_futures_listen_key(listen_key: str):
    return api_key_request("PUT", BINANCE_FUTURES_BASE_URL, "/fapi/v1/listenKey", BINANCE_FUTURES_API_KEY, {"listenKey": listen_key})


def close_futures_listen_key(listen_key: str):
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
            if lk["status_code"] >= 400:
                raise RuntimeError(lk["text"])

            WS_STATE["listen_key"] = lk["json"]["listenKey"]
            keepalive_task = asyncio.create_task(_futures_keepalive_loop())

            async with websockets.connect(f"wss://fstream.binance.com/ws/{WS_STATE['listen_key']}", ping_interval=BINANCE_FUTURES_WS_PING_SECONDS, ping_timeout=20) as ws:
                WS_STATE["last_event"] = {"type": "ws.connected", "ts": now_iso()}

                while WS_STATE["running"]:
                    data = json.loads(await ws.recv())
                    event_type = data.get("e")

                    if event_type == "ORDER_TRADE_UPDATE":
                        result = handle_futures_order_trade_update(data)
                        WS_STATE["last_event"] = {"type": event_type, "result": result, "ts": now_iso()}
                    else:
                        WS_STATE["last_event"] = {"type": event_type, "data": data, "ts": now_iso()}

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







def get_auto_discovered_symbols(force: bool = False):
    """
    Auto-discover Binance USDT perpetual futures symbols.
    Filters:
    - USDT contracts only
    - TRADING status
    - PERPETUAL contracts
    - minimum quote volume
    - top N by quote volume
    - excludes symbols in AUTO_DISCOVER_EXCLUDE
    """
    now_ts = int(datetime.now(timezone.utc).timestamp())

    if (
        not force
        and DISCOVERY_STATE["symbols"]
        and DISCOVERY_STATE["last_refresh"]
        and now_ts - int(DISCOVERY_STATE["last_refresh"]) < AUTO_DISCOVER_REFRESH_SECONDS
    ):
        return DISCOVERY_STATE["symbols"]

    try:
        info = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/exchangeInfo", {})
        if info["status_code"] >= 400:
            raise RuntimeError(info["text"])

        tickers = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/ticker/24hr", {})
        if tickers["status_code"] >= 400:
            raise RuntimeError(tickers["text"])

        valid = set()
        for s in info["json"].get("symbols", []):
            symbol = s.get("symbol", "")
            if s.get("status") != "TRADING":
                continue
            if s.get("contractType") != "PERPETUAL":
                continue
            if s.get("quoteAsset") != "USDT":
                continue
            if symbol in AUTO_DISCOVER_EXCLUDE:
                continue
            valid.add(symbol)

        ranked = []
        for t in tickers["json"]:
            symbol = t.get("symbol", "")
            if symbol not in valid:
                continue
            try:
                quote_volume = float(t.get("quoteVolume", 0) or 0)
                price_change_pct = abs(float(t.get("priceChangePercent", 0) or 0))
                last_price = float(t.get("lastPrice", 0) or 0)
            except Exception:
                continue

            if quote_volume < AUTO_DISCOVER_MIN_QUOTE_VOLUME:
                continue

            ranked.append({
                "symbol": symbol,
                "quote_volume": quote_volume,
                "price_change_pct": price_change_pct,
                "last_price": last_price,
            })

        ranked.sort(key=lambda x: x["quote_volume"], reverse=True)
        selected = [x["symbol"] for x in ranked[:AUTO_DISCOVER_TOP_N]]

        # Optionally append configured manual symbols too, useful for new listings or watchlist.
        if AUTO_DISCOVER_INCLUDE_NEW:
            for s in SCANNER_SYMBOLS:
                if s not in selected and s not in AUTO_DISCOVER_EXCLUDE:
                    selected.append(s)

        DISCOVERY_STATE["symbols"] = selected
        DISCOVERY_STATE["last_refresh"] = now_ts
        DISCOVERY_STATE["last_error"] = None
        DISCOVERY_STATE["raw_count"] = len(ranked)
        DISCOVERY_STATE["ranked_preview"] = ranked[:10]
        return selected

    except Exception as exc:
        DISCOVERY_STATE["last_error"] = str(exc)
        # fallback to manual symbols if discovery fails
        if DISCOVERY_STATE["symbols"]:
            return DISCOVERY_STATE["symbols"]
        return SCANNER_SYMBOLS


def get_scanner_symbols():
    if AUTO_DISCOVER_SYMBOLS:
        return get_auto_discovered_symbols()
    return SCANNER_SYMBOLS



def futures_klines(symbol: str, interval: str = "5m", limit: int = 120):
    r = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/klines", {"symbol": symbol.upper(), "interval": interval, "limit": limit})
    if r["status_code"] >= 400:
        raise RuntimeError(r["text"])
    rows = r["json"]
    candles = []
    for k in rows:
        candles.append({
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": int(k[6]),
        })
    return candles


def _pivot_highs_lows(candles: List[Dict[str, Any]], left: int = 3, right: int = 3):
    piv_highs = []
    piv_lows = []
    n = len(candles)
    for i in range(left, n - right):
        h = candles[i]["high"]
        l = candles[i]["low"]
        is_high = all(h > candles[j]["high"] for j in range(i-left, i)) and all(h >= candles[j]["high"] for j in range(i+1, i+right+1))
        is_low = all(l < candles[j]["low"] for j in range(i-left, i)) and all(l <= candles[j]["low"] for j in range(i+1, i+right+1))
        if is_high:
            piv_highs.append(h)
        if is_low:
            piv_lows.append(l)
    return piv_highs[-3:], piv_lows[-3:]


def analyze_symbol_for_setup(symbol: str):
    candles = futures_klines(symbol, SCANNER_INTERVAL, SCANNER_LIMIT)
    if len(candles) < 30:
        return {"symbol": symbol, "signal": None, "reason": "not enough candles"}

    last = candles[-1]
    prev = candles[-2]
    highs, lows = _pivot_highs_lows(candles)

    if len(highs) < 3 or len(lows) < 3:
        return {"symbol": symbol, "signal": None, "reason": "not enough pivots", "highs": highs, "lows": lows}

    bull_structure = highs[-1] > highs[-2] and lows[-1] > lows[-2]
    bear_structure = highs[-1] < highs[-2] and lows[-1] < lows[-2]

    bull_bos = last["close"] > highs[-1]
    bear_bos = last["close"] < lows[-1]
    bull_sweep = last["low"] < lows[-1] and last["close"] > lows[-1]
    bear_sweep = last["high"] > highs[-1] and last["close"] < highs[-1]

    vols = [c["volume"] for c in candles[-21:-1]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    volume_spike = last["volume"] > avg_vol * 1.5 if avg_vol > 0 else False

    bull_candle = last["close"] > last["open"]
    bear_candle = last["close"] < last["open"]

    recent_high = max(c["high"] for c in candles[-CONTINUATION_LOOKBACK-1:-1])
    recent_low = min(c["low"] for c in candles[-CONTINUATION_LOOKBACK-1:-1])

    bull_pullback_pct = ((recent_high - last["low"]) / recent_high) * 100 if recent_high > 0 else 0
    bear_pullback_pct = ((last["high"] - recent_low) / recent_low) * 100 if recent_low > 0 else 0

    bull_minor_break = last["close"] > prev["high"]
    bear_minor_break = last["close"] < prev["low"]

    bull_continuation = bull_structure and bull_minor_break and CONTINUATION_PULLBACK_MIN_PCT <= bull_pullback_pct <= CONTINUATION_PULLBACK_MAX_PCT
    bear_continuation = bear_structure and bear_minor_break and CONTINUATION_PULLBACK_MIN_PCT <= bear_pullback_pct <= CONTINUATION_PULLBACK_MAX_PCT

    orderbook = get_orderbook_pressure(symbol)

    long_quality = 0
    if bull_bos: long_quality += 1
    if bull_sweep: long_quality += 1
    if volume_spike: long_quality += 1
    if bull_candle: long_quality += 1

    short_quality = 0
    if bear_bos: short_quality += 1
    if bear_sweep: short_quality += 1
    if volume_spike: short_quality += 1
    if bear_candle: short_quality += 1

    long_ok = bull_structure and ((bull_bos or bull_sweep) or bull_continuation) and bull_candle and long_quality >= SCANNER_MIN_QUALITY and orderbook.get("pressure") != "sellers"
    short_ok = bear_structure and ((bear_bos or bear_sweep) or bear_continuation) and bear_candle and short_quality >= SCANNER_MIN_QUALITY and orderbook.get("pressure") != "buyers"

    if long_ok:
        return {
            "symbol": symbol,
            "signal": "long",
            "close": last["close"],
            "quality_score": long_quality,
            "structure": "bullish",
            "orderbook": orderbook,
            "extra": {
                "highs": highs,
                "lows": lows,
                "bos": bool(bull_bos),
                "sweep": bool(bull_sweep),
                "sweep_direction": "bullish" if bull_sweep else "none",
                "volume_spike": bool(volume_spike),
                "candle_confirm": bool(bull_candle),
                "scanner": True,
                "continuation": bool(bull_continuation),
                "continuation_direction": "bullish" if bull_continuation else "none",
                "minor_break": bool(bull_minor_break),
                "pullback_pct": float(bull_pullback_pct)
            }
        }

    if short_ok:
        return {
            "symbol": symbol,
            "signal": "short",
            "close": last["close"],
            "quality_score": short_quality,
            "structure": "bearish",
            "orderbook": orderbook,
            "extra": {
                "highs": highs,
                "lows": lows,
                "bos": bool(bear_bos),
                "sweep": bool(bear_sweep),
                "sweep_direction": "bearish" if bear_sweep else "none",
                "volume_spike": bool(volume_spike),
                "candle_confirm": bool(bear_candle),
                "scanner": True,
                "continuation": bool(bear_continuation),
                "continuation_direction": "bearish" if bear_continuation else "none",
                "minor_break": bool(bear_minor_break),
                "pullback_pct": float(bear_pullback_pct)
            }
        }

    return {
        "symbol": symbol,
        "signal": None,
        "reason": "no valid setup",
        "structure": "bullish" if bull_structure else "bearish" if bear_structure else "sideways",
        "highs": highs,
        "lows": lows,
        "orderbook": orderbook,
        "checks": {
            "bull_bos": bull_bos,
            "bear_bos": bear_bos,
            "bull_sweep": bull_sweep,
            "bear_sweep": bear_sweep,
            "volume_spike": volume_spike,
            "bull_candle": bull_candle,
            "bear_candle": bear_candle,
            "long_quality": long_quality,
            "short_quality": short_quality,
        }
    }


def run_scanner_once():
    results = []
    now_ts = int(datetime.now(timezone.utc).timestamp())

    for symbol in get_scanner_symbols():
        try:
            analysis = analyze_symbol_for_setup(symbol)
            results.append(analysis)

            signal = analysis.get("signal")
            if not signal:
                continue

            last_key = f"{symbol}:{signal}"
            last_sent = SCANNER_STATE["signals_sent"].get(last_key, 0)
            if now_ts - last_sent < SCANNER_COOLDOWN_SECONDS:
                analysis["execution"] = {"approved": False, "reason": "scanner cooldown active"}
                continue

            payload = SignalPayload(
                signal=signal,
                ticker=symbol,
                tf=SCANNER_INTERVAL,
                close=float(analysis["close"]),
                score=float(analysis.get("quality_score", 0)),
                preset="Day Trading",
                exchange=SCANNER_EXCHANGE,
                secret=BOT_SECRET,
                futures_mode="one_way",
                margin_type="isolated",
                leverage=SCANNER_LEVERAGE,
                position_side="BOTH",
                strategy="Auto Scanner BOS Sweep",
                extra=analysis["extra"],
            )

            execution_result = execute(payload)
            analysis["execution"] = execution_result
            if execution_result.get("approved"):
                SCANNER_STATE["signals_sent"][last_key] = now_ts

        except Exception as exc:
            results.append({"symbol": symbol, "signal": None, "reason": "scanner error", "error": str(exc)})

    SCANNER_STATE["last_scan"] = now_iso()
    SCANNER_STATE["last_results"] = results
    return results


async def _scanner_loop():
    while SCANNER_STATE["running"]:
        try:
            run_scanner_once()
            SCANNER_STATE["last_error"] = None
        except Exception as exc:
            SCANNER_STATE["last_error"] = str(exc)
        await asyncio.sleep(SCANNER_SLEEP_SECONDS)







@app.post("/scanner/discover-refresh")
def scanner_discover_refresh():
    symbols = get_auto_discovered_symbols(force=True)
    return {
        "ok": True,
        "auto_discovery": AUTO_DISCOVER_SYMBOLS,
        "symbols": symbols,
        "count": len(symbols),
        "discovery": DISCOVERY_STATE,
    }


@app.post("/scanner/start")
async def scanner_start():
    if SCANNER_STATE["running"]:
        return {"ok": True, "reason": "scanner already running"}
    SCANNER_STATE["running"] = True
    SCANNER_STATE["last_error"] = None
    SCANNER_STATE["task"] = asyncio.create_task(_scanner_loop())
    return {"ok": True, "reason": "scanner started", "symbols": get_scanner_symbols(), "exchange": SCANNER_EXCHANGE, "auto_discovery": AUTO_DISCOVER_SYMBOLS}


@app.post("/scanner/stop")
async def scanner_stop():
    SCANNER_STATE["running"] = False
    task = SCANNER_STATE.get("task")
    if task:
        task.cancel()
    SCANNER_STATE["task"] = None
    return {"ok": True, "reason": "scanner stopped"}


@app.post("/scanner/scan-once")
def scanner_scan_once():
    results = run_scanner_once()
    return {"ok": True, "count": len(results), "results": results}


@app.get("/scanner/status")
def scanner_status():
    return {
        "running": SCANNER_STATE["running"],
        "symbols": get_scanner_symbols(),
        "manual_symbols": SCANNER_SYMBOLS,
        "auto_discovery": AUTO_DISCOVER_SYMBOLS,
        "discovery": DISCOVERY_STATE,
        "exchange": SCANNER_EXCHANGE,
        "interval": SCANNER_INTERVAL,
        "sleep_seconds": SCANNER_SLEEP_SECONDS,
        "cooldown_seconds": SCANNER_COOLDOWN_SECONDS,
        "min_quality": SCANNER_MIN_QUALITY,
        "last_scan": SCANNER_STATE["last_scan"],
        "last_error": SCANNER_STATE["last_error"],
        "last_results": SCANNER_STATE["last_results"],
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Binance Bot Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #0b0f19;
            color: #e5e7eb;
        }
        header {
            padding: 22px;
            background: #111827;
            border-bottom: 1px solid #263244;
        }
        h1 {
            margin: 0;
            font-size: 24px;
        }
        .subtitle {
            color: #9ca3af;
            margin-top: 6px;
        }
        .wrap {
            padding: 20px;
            max-width: 1400px;
            margin: auto;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }
        .card {
            background: #111827;
            border: 1px solid #263244;
            border-radius: 14px;
            padding: 16px;
            box-shadow: 0 8px 22px rgba(0,0,0,0.25);
        }
        .card h3 {
            margin: 0 0 8px 0;
            font-size: 14px;
            color: #9ca3af;
            font-weight: normal;
        }
        .value {
            font-size: 24px;
            font-weight: bold;
        }
        .green { color: #22c55e; }
        .red { color: #ef4444; }
        .yellow { color: #eab308; }
        .blue { color: #38bdf8; }
        button {
            background: #2563eb;
            color: white;
            border: 0;
            padding: 10px 13px;
            border-radius: 10px;
            cursor: pointer;
            margin: 3px;
            font-weight: 600;
        }
        button:hover { background: #1d4ed8; }
        button.danger { background: #dc2626; }
        button.danger:hover { background: #b91c1c; }
        button.gray { background: #374151; }
        button.gray:hover { background: #4b5563; }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
            overflow: hidden;
            border-radius: 12px;
        }
        th, td {
            text-align: left;
            padding: 11px;
            border-bottom: 1px solid #263244;
            font-size: 13px;
        }
        th {
            color: #9ca3af;
            background: #0f172a;
        }
        tr:hover { background: #172033; }
        .pill {
            display: inline-block;
            padding: 4px 9px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: bold;
        }
        .pill.open { background: rgba(34,197,94,0.16); color: #22c55e; }
        .pill.closed { background: rgba(156,163,175,0.16); color: #9ca3af; }
        .pill.long { background: rgba(34,197,94,0.16); color: #22c55e; }
        .pill.short { background: rgba(239,68,68,0.16); color: #ef4444; }
        pre {
            background: #020617;
            border: 1px solid #263244;
            padding: 12px;
            border-radius: 12px;
            overflow: auto;
            max-height: 340px;
            font-size: 12px;
        }
        .actions {
            margin-top: 12px;
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }
        .small {
            color: #9ca3af;
            font-size: 12px;
        }
        a { color: #38bdf8; }
    </style>
</head>
<body>
<header>
    <h1>Binance Spot + Futures Bot PRO Dashboard</h1>
    <div class="subtitle">Structure + Orderbook + BOS/Sweep + Smart Exit Manager</div>
</header>

<div class="wrap">
    <div class="grid">
        <div class="card">
            <h3>Server</h3>
            <div id="serverStatus" class="value yellow">Loading...</div>
            <div id="serviceName" class="small"></div>
        </div>
        <div class="card">
            <h3>Execution</h3>
            <div id="executionStatus" class="value yellow">Loading...</div>
            <div id="defaultExchange" class="small"></div>
        </div>
        <div class="card">
            <h3>Futures Websocket</h3>
            <div id="wsStatus" class="value yellow">Loading...</div>
            <div id="wsError" class="small"></div>
        </div>
        <div class="card">
            <h3>Open Positions</h3>
            <div id="openCount" class="value blue">0</div>
            <div class="small">Active bot-tracked positions</div>
        </div>
    </div>

    <div class="card">
        <h3>Quick Controls</h3>
        <div class="actions">
            <button onclick="startWs()">Start Futures WS</button>
            <button class="gray" onclick="stopWs()">Stop Futures WS</button>
            <button onclick="syncFutures()">Sync Futures</button>
            <button onclick="startScanner()">Start Scanner</button><button class="gray" onclick="stopScanner()">Stop Scanner</button><button onclick="scanOnce()">Scan Once</button><button onclick="refreshDiscovery()">Refresh Symbols</button><button class="gray" onclick="refreshAll()">Refresh</button>
            <a href="/docs" target="_blank"><button class="gray">Open API Docs</button></a>
        </div>
        <div id="controlResult" class="small" style="margin-top:10px;"></div>
    </div>

    <div class="card">
        <h3>Open Positions</h3>
        <div style="overflow-x:auto;">
            <table>
                <thead>
                    <tr>
                        <th>Status</th>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Exchange</th>
                        <th>Entry</th>
                        <th>Stop</th>
                        <th>Qty</th>
                        <th>Risk</th>
                        <th>BE</th>
                        <th>Trail</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody id="positionsBody">
                    <tr><td colspan="11">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h3>Latest Journal</h3>
            <pre id="journalBox">Loading...</pre>
        </div>
        <div class="card">
            <h3>Websocket Detail</h3>
            <pre id="wsBox">Loading...</pre>
        </div>
        <div class="card">
            <h3>Scanner Status</h3>
            <pre id="scannerBox">Loading...</pre>
        </div>
    </div>
</div>

<script>
async function getJson(url, opts={}) {
    const res = await fetch(url, opts);
    if (!res.ok) {
        const txt = await res.text();
        throw new Error(res.status + " " + txt);
    }
    return await res.json();
}

function fmt(n) {
    if (n === null || n === undefined) return "";
    if (typeof n === "number") return Number(n).toFixed(6).replace(/0+$/,'').replace(/\.$/,'');
    return n;
}

async function loadRoot() {
    const data = await getJson("/");
    document.getElementById("serverStatus").textContent = data.status || "unknown";
    document.getElementById("serverStatus").className = "value green";
    document.getElementById("serviceName").textContent = data.service || "";
}

async function loadConfig() {
    const data = await getJson("/config");
    document.getElementById("executionStatus").textContent = data.execution_enabled ? "LIVE ON" : "SAFE / PAPER";
    document.getElementById("executionStatus").className = data.execution_enabled ? "value red" : "value green";
    document.getElementById("defaultExchange").textContent = "Default: " + data.default_exchange + " | Risk: " + data.max_risk_pct_per_trade + "%";
}

async function loadWs() {
    const data = await getJson("/futures/ws/status");
    document.getElementById("wsStatus").textContent = data.running ? "RUNNING" : "OFF";
    document.getElementById("wsStatus").className = data.running ? "value green" : "value yellow";
    document.getElementById("wsError").textContent = data.last_error ? "Error: " + data.last_error : "No error";
    document.getElementById("wsBox").textContent = JSON.stringify(data, null, 2);
}

async function loadPositions() {
    const data = await getJson("/positions/open");
    const rows = data.rows || [];
    document.getElementById("openCount").textContent = rows.length;
    const body = document.getElementById("positionsBody");
    if (rows.length === 0) {
        body.innerHTML = '<tr><td colspan="11" class="small">No open positions</td></tr>';
        return;
    }
    body.innerHTML = rows.map(p => `
        <tr>
            <td><span class="pill open">${p.status}</span></td>
            <td>${p.ticker}</td>
            <td><span class="pill ${p.signal}">${p.signal}</span></td>
            <td>${p.exchange}</td>
            <td>${fmt(p.entry_price)}</td>
            <td>${fmt(p.stop_price)}</td>
            <td>${fmt(p.qty)}</td>
            <td>${fmt(p.risk_usd)}</td>
            <td>${p.break_even_armed ? "YES" : "NO"}</td>
            <td>${p.trail_armed ? "YES" : "NO"}</td>
            <td><button class="danger" onclick="closePosition('${p.id}')">Close</button></td>
        </tr>
    `).join("");
}

async function loadJournal() {
    try {
        const data = await getJson("/journal/recent?limit=8");
        document.getElementById("journalBox").textContent = JSON.stringify(data.rows || [], null, 2);
    } catch (e) {
        document.getElementById("journalBox").textContent = "Journal unavailable: " + e.message;
    }
}

async function startWs() {
    try {
        const data = await getJson("/futures/ws/start", {method:"POST"});
        document.getElementById("controlResult").textContent = JSON.stringify(data);
        setTimeout(refreshAll, 1000);
    } catch(e) {
        document.getElementById("controlResult").textContent = e.message;
    }
}

async function stopWs() {
    try {
        const data = await getJson("/futures/ws/stop", {method:"POST"});
        document.getElementById("controlResult").textContent = JSON.stringify(data);
        setTimeout(refreshAll, 1000);
    } catch(e) {
        document.getElementById("controlResult").textContent = e.message;
    }
}

async function syncFutures() {
    try {
        const data = await getJson("/futures/sync-open", {method:"POST"});
        document.getElementById("controlResult").textContent = JSON.stringify(data);
        setTimeout(refreshAll, 1000);
    } catch(e) {
        document.getElementById("controlResult").textContent = e.message;
    }
}

async function closePosition(id) {
    if (!confirm("Close this position?")) return;
    try {
        const data = await getJson("/positions/close/" + id, {method:"POST"});
        document.getElementById("controlResult").textContent = JSON.stringify(data);
        setTimeout(refreshAll, 1000);
    } catch(e) {
        document.getElementById("controlResult").textContent = e.message;
    }
}


async function loadScanner() {
    try {
        const data = await getJson("/scanner/status");
        document.getElementById("scannerBox").textContent = JSON.stringify(data, null, 2);
    } catch (e) {
        const el = document.getElementById("scannerBox");
        if (el) el.textContent = "Scanner unavailable: " + e.message;
    }
}


async function refreshDiscovery() {
    try {
        const data = await getJson("/scanner/discover-refresh", {method:"POST"});
        document.getElementById("controlResult").textContent = JSON.stringify({ok:data.ok,count:data.count});
        setTimeout(refreshAll, 1000);
    } catch(e) {
        document.getElementById("controlResult").textContent = e.message;
    }
}

async function startScanner() {
    try {
        const data = await getJson("/scanner/start", {method:"POST"});
        document.getElementById("controlResult").textContent = JSON.stringify(data);
        setTimeout(refreshAll, 1000);
    } catch(e) {
        document.getElementById("controlResult").textContent = e.message;
    }
}

async function stopScanner() {
    try {
        const data = await getJson("/scanner/stop", {method:"POST"});
        document.getElementById("controlResult").textContent = JSON.stringify(data);
        setTimeout(refreshAll, 1000);
    } catch(e) {
        document.getElementById("controlResult").textContent = e.message;
    }
}

async function scanOnce() {
    try {
        const data = await getJson("/scanner/scan-once", {method:"POST"});
        document.getElementById("controlResult").textContent = JSON.stringify({ok:data.ok,count:data.count});
        setTimeout(refreshAll, 1000);
    } catch(e) {
        document.getElementById("controlResult").textContent = e.message;
    }
}

async function refreshAll() {
    await Promise.allSettled([loadRoot(), loadConfig(), loadWs(), loadPositions(), loadJournal(), loadScanner()]);
}

refreshAll();
setInterval(refreshAll, 10000);
</script>
</body>
</html>
"""



@app.get("/")
def root():
    return {"status": "ok", "service": "binance-spot-futures-bot-pro-final-auto-discovery", "time": now_iso()}


@app.get("/config")
def config():
    return {
        "default_exchange": DEFAULT_EXCHANGE,
        "execution_enabled": ENABLE_EXECUTION,
        "paper_enabled": ENABLE_PAPER_TRADING,
        "trading_enabled": TRADING_ENABLED,
        "account_equity_usd": ACCOUNT_EQUITY_USD,
        "max_risk_pct_per_trade": MAX_RISK_PCT_PER_TRADE,
        "be_trigger_r": BE_TRIGGER_R,
        "trail_trigger_r": TRAIL_TRIGGER_R,
        "trail_distance_r": TRAIL_DISTANCE_R,
        "reversal_score_to_exit": REVERSAL_SCORE_TO_EXIT,
        "scanner_exchange": SCANNER_EXCHANGE,
        "scanner_symbols": SCANNER_SYMBOLS,
        "scanner_interval": SCANNER_INTERVAL,
        "auto_discover_symbols": AUTO_DISCOVER_SYMBOLS,
        "auto_discover_top_n": AUTO_DISCOVER_TOP_N,
        "auto_discover_min_quote_volume": AUTO_DISCOVER_MIN_QUOTE_VOLUME,
        "continuation_enabled": CONTINUATION_ENABLED,
        "continuation_pullback_min_pct": CONTINUATION_PULLBACK_MIN_PCT,
        "continuation_pullback_max_pct": CONTINUATION_PULLBACK_MAX_PCT,
    }


@app.get("/pine-alert-templates")
def pine_alert_templates():
    return {
        "paper_long_with_structure": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"exchange":"paper","secret":"CHANGE_ME","extra":{"highs":[{{high[2]}},{{high[1]}},{{high}}],"lows":[{{low[2]}},{{low[1]}},{{low}}]}}',
        "futures_long_with_structure": '{"signal":"long","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"exchange":"binance_futures","futures_mode":"one_way","margin_type":"isolated","leverage":2,"position_side":"BOTH","secret":"CHANGE_ME","extra":{"highs":[{{high[2]}},{{high[1]}},{{high}}],"lows":[{{low[2]}},{{low[1]}},{{low}}]}}',
        "futures_short_with_structure": '{"signal":"short","ticker":"{{ticker}}","tf":"{{interval}}","close":{{close}},"score":7,"exchange":"binance_futures","futures_mode":"one_way","margin_type":"isolated","leverage":2,"position_side":"BOTH","secret":"CHANGE_ME","extra":{"highs":[{{high[2]}},{{high[1]}},{{high}}],"lows":[{{low[2]}},{{low[1]}},{{low}}]}}',
    }


@app.get("/journal/recent")
def journal_recent(limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM signal_journal ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        return {"rows": [dict(r) for r in rows]}


@app.get("/positions/open")
def positions_open():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM positions WHERE status='open' ORDER BY created_at DESC").fetchall()
        return {"rows": [dict(r) for r in rows]}


@app.get("/positions/{position_id}")
def position_detail(position_id: str):
    pos = get_position(position_id)
    if not pos:
        raise HTTPException(status_code=404, detail="position not found")
    return pos


@app.post("/webhook/tradingview")
async def webhook(payload: SignalPayload):
    return execute(payload)


@app.post("/positions/manage")
def manage_open_position(payload: ManagePositionPayload):
    position = get_position(payload.position_id)
    if not position:
        raise HTTPException(status_code=404, detail="position not found")
    return manage_position(position, payload.current_price, payload)


@app.post("/positions/close/{position_id}")
def close_position_manual(position_id: str):
    position = get_position(position_id)
    if not position:
        raise HTTPException(status_code=404, detail="position not found")

    if position["status"] != "open":
        return {"ok": False, "reason": "position already closed"}

    if position["exchange"] == "paper":
        update_position(position_id, {"status": "closed"})
        return {"ok": True, "exchange": "paper", "reason": "paper position closed"}

    if position["exchange"] == "binance_spot":
        result = close_spot_position(position)
        if result["status_code"] < 400:
            update_position(position_id, {"status": "closed"})
        return {"ok": result["status_code"] < 400, "exchange": "binance_spot", "result": result}

    if position["exchange"] == "binance_futures":
        result = close_futures_position(position)
        if result["status_code"] < 400:
            update_position(position_id, {"status": "closed"})
        return {"ok": result["status_code"] < 400, "exchange": "binance_futures", "result": result}

    raise HTTPException(status_code=400, detail="unsupported exchange")


@app.post("/positions/sync-market-price/{position_id}")
def sync_market_price(position_id: str):
    position = get_position(position_id)
    if not position:
        raise HTTPException(status_code=404, detail="position not found")

    if position["exchange"] == "binance_spot":
        price = float(public_get(BINANCE_SPOT_BASE_URL, "/api/v3/ticker/price", {"symbol": position["ticker"]})["json"]["price"])
    elif position["exchange"] == "binance_futures":
        price = float(public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/ticker/price", {"symbol": position["ticker"]})["json"]["price"])
    else:
        raise HTTPException(status_code=400, detail="use /positions/manage for paper positions")

    payload = ManagePositionPayload(position_id=position_id, current_price=price, dry_run=True)
    return manage_position(position, price, payload)


@app.post("/futures/ws/start")
async def futures_ws_start():
    if WS_STATE["running"]:
        return {"ok": True, "reason": "already running"}
    WS_STATE["running"] = True
    WS_STATE["last_error"] = None
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
    return {"running": WS_STATE["running"], "listen_key": WS_STATE["listen_key"], "last_event": WS_STATE["last_event"], "last_error": WS_STATE["last_error"]}


@app.post("/futures/sync-open")
def futures_sync_open_positions():
    positions = get_all_open_futures_positions()
    return {"count": len(positions), "results": [sync_single_futures_position_from_exchange(p) for p in positions]}
