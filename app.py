
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

BE_TRIGGER_R = float(os.getenv("BE_TRIGGER_R", "0.50"))
TRAIL_TRIGGER_R = float(os.getenv("TRAIL_TRIGGER_R", "1.8"))
TRAIL_DISTANCE_R = float(os.getenv("TRAIL_DISTANCE_R", "0.8"))
REVERSAL_SCORE_TO_EXIT = int(os.getenv("REVERSAL_SCORE_TO_EXIT", "3"))

WS_STATE: Dict[str, Any] = {"task": None, "running": False, "last_event": None, "listen_key": None, "last_error": None}

LIVE_MANAGER_ENABLED = os.getenv("LIVE_MANAGER_ENABLED", "true").lower() == "true"
LIVE_MANAGER_AUTO_START = os.getenv("LIVE_MANAGER_AUTO_START", "true").lower() == "true"
LIVE_MANAGER_SLEEP_SECONDS = int(os.getenv("LIVE_MANAGER_SLEEP_SECONDS", "10"))

PA_TRAIL_ENABLED = os.getenv("PA_TRAIL_ENABLED", "true").lower() == "true"
PA_TRAIL_BARS = int(os.getenv("PA_TRAIL_BARS", "3"))
PA_TRAIL_INTERVAL = os.getenv("PA_TRAIL_INTERVAL", "5m")
PA_TRAIL_ACTIVATE_R = float(os.getenv("PA_TRAIL_ACTIVATE_R", "0.75"))

ADAPTIVE_ENABLED = os.getenv("ADAPTIVE_ENABLED", "true").lower() == "true"
ADAPTIVE_LOOKBACK_TRADES = int(os.getenv("ADAPTIVE_LOOKBACK_TRADES", "20"))
ADAPTIVE_MIN_TRADES = int(os.getenv("ADAPTIVE_MIN_TRADES", "6"))
ADAPTIVE_MAX_LOSS_STREAK = int(os.getenv("ADAPTIVE_MAX_LOSS_STREAK", "3"))
ADAPTIVE_MIN_WIN_RATE = float(os.getenv("ADAPTIVE_MIN_WIN_RATE", "35"))
ADAPTIVE_PAUSE_MINUTES = int(os.getenv("ADAPTIVE_PAUSE_MINUTES", "30"))

# PRO Engine: regime + liquidity + confirmation + cooldown + symbol quality
PRO_ENGINE_ENABLED = os.getenv("PRO_ENGINE_ENABLED", "true").lower() == "true"
PRO_REGIME_INTERVAL = os.getenv("PRO_REGIME_INTERVAL", "15m")
PRO_REGIME_LIMIT = int(os.getenv("PRO_REGIME_LIMIT", "80"))
PRO_BLOCK_CHOP = os.getenv("PRO_BLOCK_CHOP", "true").lower() == "true"
PRO_CHOP_RANGE_MULT = float(os.getenv("PRO_CHOP_RANGE_MULT", "5.0"))
PRO_ENTRY_BODY_MIN = float(os.getenv("PRO_ENTRY_BODY_MIN", "0.55"))
PRO_ENTRY_CLOSE_TOP_BOTTOM = float(os.getenv("PRO_ENTRY_CLOSE_TOP_BOTTOM", "0.35"))

PRO_MAX_TRADES_PER_HOUR = int(os.getenv("PRO_MAX_TRADES_PER_HOUR", "2"))
PRO_SYMBOL_COOLDOWN_MINUTES = int(os.getenv("PRO_SYMBOL_COOLDOWN_MINUTES", "20"))
PRO_LOSS_STREAK_PAUSE_MINUTES = int(os.getenv("PRO_LOSS_STREAK_PAUSE_MINUTES", "45"))
PRO_MIN_24H_QUOTE_VOLUME = float(os.getenv("PRO_MIN_24H_QUOTE_VOLUME", "100000000"))
PRO_ALLOWED_SYMBOLS = [s.strip().upper() for s in os.getenv("PRO_ALLOWED_SYMBOLS", "").split(",") if s.strip()]
PRO_BLOCK_LOW_QUALITY_SYMBOLS = os.getenv("PRO_BLOCK_LOW_QUALITY_SYMBOLS", "true").lower() == "true"


# AI V2 controlled adaptive intelligence
AI_V2_ENABLED = os.getenv("AI_V2_ENABLED", "true").lower() == "true"
AI_V2_LOOKBACK_TRADES = int(os.getenv("AI_V2_LOOKBACK_TRADES", "50"))
AI_V2_MIN_SAMPLE = int(os.getenv("AI_V2_MIN_SAMPLE", "8"))
AI_V2_BAD_SYMBOL_WINRATE = float(os.getenv("AI_V2_BAD_SYMBOL_WINRATE", "35"))
AI_V2_BAD_HOUR_WINRATE = float(os.getenv("AI_V2_BAD_HOUR_WINRATE", "35"))
AI_V2_REVIEW_SECONDS = int(os.getenv("AI_V2_REVIEW_SECONDS", "300"))
AI_V2_AUTO_DEFENSIVE = os.getenv("AI_V2_AUTO_DEFENSIVE", "true").lower() == "true"
AI_V2_BLOCK_BAD_SYMBOLS = os.getenv("AI_V2_BLOCK_BAD_SYMBOLS", "true").lower() == "true"
AI_V2_BLOCK_BAD_HOURS = os.getenv("AI_V2_BLOCK_BAD_HOURS", "false").lower() == "true"


# AI V3 confidence + auto tuning + strategy guardrails
AI_V3_ENABLED = os.getenv("AI_V3_ENABLED", "true").lower() == "true"
AI_V3_MIN_CONFIDENCE = float(os.getenv("AI_V3_MIN_CONFIDENCE", "65"))
AI_V3_DEFENSIVE_CONFIDENCE = float(os.getenv("AI_V3_DEFENSIVE_CONFIDENCE", "78"))
AI_V3_AGGRESSIVE_CONFIDENCE = float(os.getenv("AI_V3_AGGRESSIVE_CONFIDENCE", "58"))
AI_V3_AUTO_TUNE = os.getenv("AI_V3_AUTO_TUNE", "true").lower() == "true"
AI_V3_TUNE_REVIEW_TRADES = int(os.getenv("AI_V3_TUNE_REVIEW_TRADES", "30"))
AI_V3_RISK_REDUCE_ON_BAD = float(os.getenv("AI_V3_RISK_REDUCE_ON_BAD", "0.50"))
AI_V3_RISK_BOOST_ON_GOOD = float(os.getenv("AI_V3_RISK_BOOST_ON_GOOD", "1.10"))
AI_V3_REQUIRE_MTF_WHEN_BAD = os.getenv("AI_V3_REQUIRE_MTF_WHEN_BAD", "true").lower() == "true"

AI_V3_STATE: Dict[str, Any] = {
    "mode": "learning",
    "last_review": None,
    "active_min_confidence": None,
    "risk_multiplier": 1.0,
    "recommendations": [],
    "last_confidence": {},
    "stats": {},
}

AI_V2_STATE: Dict[str, Any] = {
    "mode": "learning",
    "last_review": None,
    "bad_symbols": [],
    "best_symbols": [],
    "bad_hours_utc": [],
    "best_hours_utc": [],
    "recommendations": [],
    "stats": {},
    "task": None,
}

PRO_ENGINE_STATE: Dict[str, Any] = {
    "last_checks": {},
    "symbol_cooldowns": {},
    "global_pause_until": None,
    "last_error": None,
}

LIVE_MANAGER_STATE: Dict[str, Any] = {"task": None, "running": False, "last_run": None, "last_error": None, "last_actions": []}
ADAPTIVE_STATE: Dict[str, Any] = {"mode": "normal", "last_eval": None, "reason": None, "paused_until": None, "recommendations": []}


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


SCANNER_AUTO_START = os.getenv("SCANNER_AUTO_START", "false").lower() == "true"
WATCHDOG_ENABLED = os.getenv("WATCHDOG_ENABLED", "true").lower() == "true"
WATCHDOG_SLEEP_SECONDS = int(os.getenv("WATCHDOG_SLEEP_SECONDS", "60"))

SESSION_FILTER_ENABLED = os.getenv("SESSION_FILTER_ENABLED", "false").lower() == "true"
SESSION_ALLOWED_UTC_RANGES = [s.strip() for s in os.getenv("SESSION_ALLOWED_UTC_RANGES", "07:00-17:00").split(",") if s.strip()]

MTF_CONFIRM_ENABLED = os.getenv("MTF_CONFIRM_ENABLED", "false").lower() == "true"
MTF_INTERVAL = os.getenv("MTF_INTERVAL", "15m")

AUTO_RISK_ENABLED = os.getenv("AUTO_RISK_ENABLED", "true").lower() == "true"
AUTO_RISK_BASE_MULTIPLIER = float(os.getenv("AUTO_RISK_BASE_MULTIPLIER", "1.0"))
AUTO_RISK_HIGH_QUALITY_MULTIPLIER = float(os.getenv("AUTO_RISK_HIGH_QUALITY_MULTIPLIER", "1.25"))
AUTO_RISK_LOW_QUALITY_MULTIPLIER = float(os.getenv("AUTO_RISK_LOW_QUALITY_MULTIPLIER", "0.60"))

ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "false").lower() == "true"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SAFETY_STATE: Dict[str, Any] = {
    "emergency_stop": False,
    "reason": None,
    "changed_at": None,
    "watchdog_task": None,
}

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
    risk_multiplier = 1.0
    try:
        risk_multiplier = float((payload.extra or {}).get("risk_multiplier", 1.0))
    except Exception:
        risk_multiplier = 1.0
    risk_multiplier = max(0.10, min(risk_multiplier, 2.0))
    risk_usd = ACCOUNT_EQUITY_USD * (risk_pct / 100.0) * risk_multiplier

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



def pro_get_24h_ticker(symbol: str):
    r = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/ticker/24hr", {"symbol": symbol.upper()})
    if r["status_code"] >= 400:
        raise RuntimeError(r["text"])
    return r["json"]


def pro_symbol_quality(symbol: str):
    symbol = symbol.upper()
    try:
        t = pro_get_24h_ticker(symbol)
        quote_volume = float(t.get("quoteVolume", 0) or 0)
        price_change_pct = abs(float(t.get("priceChangePercent", 0) or 0))
        allow = True
        reasons = []

        if PRO_ALLOWED_SYMBOLS and symbol not in PRO_ALLOWED_SYMBOLS:
            allow = False
            reasons.append("not in PRO_ALLOWED_SYMBOLS")

        if PRO_BLOCK_LOW_QUALITY_SYMBOLS and quote_volume < PRO_MIN_24H_QUOTE_VOLUME:
            allow = False
            reasons.append("low 24h volume")

        return {
            "allow": allow,
            "symbol": symbol,
            "quote_volume": quote_volume,
            "price_change_pct": price_change_pct,
            "reasons": reasons or ["symbol quality ok"],
        }
    except Exception as exc:
        return {"allow": not PRO_BLOCK_LOW_QUALITY_SYMBOLS, "symbol": symbol, "error": str(exc), "reasons": ["symbol quality check failed"]}


def pro_market_regime(symbol: str):
    try:
        candles = futures_klines(symbol, PRO_REGIME_INTERVAL, PRO_REGIME_LIMIT)
        if len(candles) < 20:
            return {"regime": "unknown", "allow": True, "reason": "not enough candles"}

        recent = candles[-50:] if len(candles) >= 50 else candles
        highs = [c["high"] for c in recent]
        lows = [c["low"] for c in recent]
        closes = [c["close"] for c in recent]

        total_range = max(highs) - min(lows)
        avg_candle = sum((c["high"] - c["low"]) for c in recent) / len(recent)
        last_close = closes[-1]

        breakout_up = last_close > max(highs[:-5]) if len(highs) > 10 else False
        breakout_down = last_close < min(lows[:-5]) if len(lows) > 10 else False

        # simple trend strength by slope
        slope = closes[-1] - closes[-10] if len(closes) >= 10 else 0
        slope_pct = (slope / closes[-10] * 100) if len(closes) >= 10 and closes[-10] else 0

        chop = total_range < avg_candle * PRO_CHOP_RANGE_MULT if avg_candle > 0 else False

        if breakout_up:
            regime = "breakout_up"
        elif breakout_down:
            regime = "breakout_down"
        elif chop:
            regime = "chop"
        elif slope_pct > 0.25:
            regime = "trend_up"
        elif slope_pct < -0.25:
            regime = "trend_down"
        else:
            regime = "range"

        allow = not (PRO_BLOCK_CHOP and regime in ["chop", "range"])
        return {
            "regime": regime,
            "allow": allow,
            "reason": "regime allowed" if allow else f"blocked by regime: {regime}",
            "interval": PRO_REGIME_INTERVAL,
            "range": total_range,
            "avg_candle": avg_candle,
            "slope_pct": slope_pct,
            "breakout_up": breakout_up,
            "breakout_down": breakout_down,
        }
    except Exception as exc:
        return {"regime": "unknown", "allow": True, "reason": f"regime error ignored: {exc}"}


def pro_entry_confirmation(symbol: str, signal: str):
    try:
        candles = futures_klines(symbol, SCANNER_INTERVAL, 5)
        c = candles[-1]
        body = abs(c["close"] - c["open"])
        rng = max(c["high"] - c["low"], 0.00000001)
        body_ratio = body / rng
        close_position = (c["close"] - c["low"]) / rng

        if signal in ["long", "buy"]:
            strong_body = body_ratio >= PRO_ENTRY_BODY_MIN and c["close"] > c["open"]
            closes_strong = close_position >= (1 - PRO_ENTRY_CLOSE_TOP_BOTTOM)
            allow = strong_body and closes_strong
            reason = "bull entry confirmed" if allow else "weak bull entry candle"
        else:
            strong_body = body_ratio >= PRO_ENTRY_BODY_MIN and c["close"] < c["open"]
            closes_strong = close_position <= PRO_ENTRY_CLOSE_TOP_BOTTOM
            allow = strong_body and closes_strong
            reason = "bear entry confirmed" if allow else "weak bear entry candle"

        return {
            "allow": allow,
            "reason": reason,
            "body_ratio": body_ratio,
            "close_position": close_position,
            "candle": c,
        }
    except Exception as exc:
        return {"allow": True, "reason": f"entry confirmation error ignored: {exc}"}


def pro_recent_trades_for_symbol(symbol: Optional[str] = None, minutes: int = 60):
    rows = analytics_trade_rows() if "analytics_trade_rows" in globals() else []
    now_ts = datetime.now(timezone.utc).timestamp()
    out = []
    for r in rows:
        try:
            ts = datetime.fromisoformat((r.get("created_at") or "").replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if now_ts - ts <= minutes * 60:
            if symbol is None or r.get("ticker") == symbol:
                out.append(r)
    return out


def pro_cooldown_check(symbol: str):
    now_ts = int(datetime.now(timezone.utc).timestamp())

    pause_until = PRO_ENGINE_STATE.get("global_pause_until")
    if pause_until and now_ts < pause_until:
        return {"allow": False, "reason": f"global pause active until {pause_until}"}

    sym_pause = PRO_ENGINE_STATE.get("symbol_cooldowns", {}).get(symbol)
    if sym_pause and now_ts < sym_pause:
        return {"allow": False, "reason": f"{symbol} cooldown active until {sym_pause}"}

    recent_all = pro_recent_trades_for_symbol(None, 60)
    if len(recent_all) >= PRO_MAX_TRADES_PER_HOUR:
        return {"allow": False, "reason": "max trades per hour reached"}

    recent_symbol = pro_recent_trades_for_symbol(symbol, PRO_SYMBOL_COOLDOWN_MINUTES)
    if recent_symbol:
        return {"allow": False, "reason": f"symbol cooldown: recent trade on {symbol}"}

    return {"allow": True, "reason": "cooldown ok"}


def pro_loss_streak_guard():
    if "analytics_trade_rows" not in globals():
        return {"allow": True, "reason": "no analytics available"}

    rows = [r for r in analytics_trade_rows() if r.get("status") == "closed" and r.get("pnl") is not None]
    streak = 0
    for r in rows[:10]:
        if r["pnl"] < 0:
            streak += 1
        else:
            break

    if streak >= ADAPTIVE_MAX_LOSS_STREAK:
        until = int(datetime.now(timezone.utc).timestamp()) + PRO_LOSS_STREAK_PAUSE_MINUTES * 60
        PRO_ENGINE_STATE["global_pause_until"] = until
        return {"allow": False, "reason": f"loss streak guard active: {streak} losses", "pause_until": until}

    return {"allow": True, "reason": "loss streak ok", "loss_streak": streak}



def ai_v2_rows():
    rows = analytics_trade_rows() if "analytics_trade_rows" in globals() else []
    closed = [r for r in rows if r.get("status") == "closed" and r.get("pnl") is not None]
    return closed[:AI_V2_LOOKBACK_TRADES]


def ai_v2_group_stats(rows, key):
    stats = {}
    for r in rows:
        k = str(r.get(key, "UNKNOWN"))
        stats.setdefault(k, {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})
        stats[k]["trades"] += 1
        stats[k]["pnl"] += float(r.get("pnl") or 0)
        if float(r.get("pnl") or 0) > 0:
            stats[k]["wins"] += 1
        elif float(r.get("pnl") or 0) < 0:
            stats[k]["losses"] += 1
    for k, v in stats.items():
        counted = v["wins"] + v["losses"]
        v["win_rate"] = (v["wins"] / counted * 100.0) if counted else 0.0
    return stats



def ai_v3_market_confidence(symbol: str, signal: str, payload_extra: Optional[Dict[str, Any]] = None):
    """
    Confidence score 0-100.
    This is controlled scoring, not random strategy invention.
    """
    score = 0
    reasons = []
    payload_extra = payload_extra or {}

    # Base setup info from payload
    if payload_extra.get("bos"):
        score += 18; reasons.append("+BOS")
    if payload_extra.get("sweep"):
        score += 18; reasons.append("+sweep")
    if payload_extra.get("continuation"):
        score += 14; reasons.append("+continuation")
    if payload_extra.get("volume_spike"):
        score += 10; reasons.append("+volume")
    if payload_extra.get("candle_confirm"):
        score += 10; reasons.append("+candle")
    if payload_extra.get("minor_break"):
        score += 8; reasons.append("+minor_break")

    # PRO engine/context checks
    try:
        regime = pro_market_regime(symbol) if "pro_market_regime" in globals() else {"regime": "unknown", "allow": True}
        AI_V3_STATE["last_regime"] = regime
        rg = regime.get("regime")
        if signal in ["long", "buy"] and rg in ["breakout_up", "trend_up"]:
            score += 16; reasons.append("+regime_align")
        elif signal in ["short", "sell"] and rg in ["breakout_down", "trend_down"]:
            score += 16; reasons.append("+regime_align")
        elif rg in ["chop", "range"]:
            score -= 25; reasons.append("-chop")
    except Exception as exc:
        reasons.append(f"regime_unknown:{exc}")

    # Orderbook pressure
    try:
        ob = get_orderbook_pressure(symbol)
        AI_V3_STATE["last_orderbook"] = ob
        pressure = ob.get("pressure")
        if signal in ["long", "buy"] and pressure == "buyers":
            score += 10; reasons.append("+buyers")
        elif signal in ["short", "sell"] and pressure == "sellers":
            score += 10; reasons.append("+sellers")
        elif signal in ["long", "buy"] and pressure == "sellers":
            score -= 18; reasons.append("-sellers_against")
        elif signal in ["short", "sell"] and pressure == "buyers":
            score -= 18; reasons.append("-buyers_against")
    except Exception as exc:
        reasons.append(f"orderbook_unknown:{exc}")

    # AI V2 learned behavior
    try:
        state = ai_v2_review() if "ai_v2_review" in globals() else {}
        if symbol.upper() in state.get("best_symbols", []):
            score += 8; reasons.append("+best_symbol")
        if symbol.upper() in state.get("bad_symbols", []):
            score -= 20; reasons.append("-bad_symbol")
        hour = str(datetime.now(timezone.utc).hour)
        if hour in state.get("best_hours_utc", []):
            score += 6; reasons.append("+best_hour")
        if hour in state.get("bad_hours_utc", []):
            score -= 12; reasons.append("-bad_hour")
    except Exception as exc:
        reasons.append(f"ai_v2_unknown:{exc}")

    score = max(0, min(100, score))
    result = {"score": score, "reasons": reasons}
    AI_V3_STATE["last_confidence"][symbol.upper()] = result
    return result


def ai_v3_review():
    if not AI_V3_ENABLED:
        AI_V3_STATE.update({"mode": "disabled", "last_review": now_iso(), "active_min_confidence": 0})
        return AI_V3_STATE

    rows = ai_v2_rows() if "ai_v2_rows" in globals() else []
    rows = rows[:AI_V3_TUNE_REVIEW_TRADES]
    recs = []

    if len(rows) < max(6, AI_V2_MIN_SAMPLE if "AI_V2_MIN_SAMPLE" in globals() else 8):
        mode = "learning"
        active = AI_V3_MIN_CONFIDENCE
        risk_mult = 1.0
        recs.append("Collect more paper trades before aggressive tuning.")
    else:
        wins = len([r for r in rows if r.get("pnl", 0) > 0])
        losses = len([r for r in rows if r.get("pnl", 0) < 0])
        total = wins + losses
        win_rate = wins / total * 100 if total else 0
        pnl = sum(float(r.get("pnl") or 0) for r in rows)

        if win_rate < 40 or pnl < 0:
            mode = "defensive"
            active = AI_V3_DEFENSIVE_CONFIDENCE
            risk_mult = AI_V3_RISK_REDUCE_ON_BAD
            recs += [
                "AI V3 defensive: raise confidence threshold.",
                "Reduce risk multiplier.",
                "Require stronger setups only.",
            ]
        elif win_rate >= 58 and pnl > 0:
            mode = "stable"
            active = AI_V3_MIN_CONFIDENCE
            risk_mult = 1.0
            recs.append("System stable. Keep collecting data.")
        else:
            mode = "normal"
            active = AI_V3_MIN_CONFIDENCE
            risk_mult = 1.0
            recs.append("Normal mode: balanced threshold.")

        AI_V3_STATE["stats"] = {"sample": len(rows), "wins": wins, "losses": losses, "win_rate": win_rate, "pnl": pnl}

    AI_V3_STATE.update({
        "mode": mode,
        "last_review": now_iso(),
        "active_min_confidence": active,
        "risk_multiplier": risk_mult,
        "recommendations": recs,
    })
    return AI_V3_STATE


def ai_v3_allows_trade(symbol: str, signal: str, extra: Optional[Dict[str, Any]] = None):
    if not AI_V3_ENABLED:
        return True, "AI V3 disabled", {"score": 100, "reasons": []}

    state = ai_v3_review()
    confidence = ai_v3_market_confidence(symbol, signal, extra)
    min_conf = float(state.get("active_min_confidence") or AI_V3_MIN_CONFIDENCE)

    if confidence["score"] < min_conf:
        return False, f"AI V3 confidence too low: {confidence['score']} < {min_conf}", confidence

    return True, "AI V3 confidence passed", confidence


def ai_v2_review():
    if not AI_V2_ENABLED:
        AI_V2_STATE.update({"mode": "disabled", "last_review": now_iso(), "recommendations": ["AI V2 disabled"]})
        return AI_V2_STATE

    rows = ai_v2_rows()
    recommendations = []

    if len(rows) < AI_V2_MIN_SAMPLE:
        AI_V2_STATE.update({
            "mode": "learning",
            "last_review": now_iso(),
            "stats": {"sample": len(rows), "need": AI_V2_MIN_SAMPLE},
            "recommendations": ["Keep paper mode running. Need more closed trades before adapting."]
        })
        return AI_V2_STATE

    wins = len([r for r in rows if r["pnl"] > 0])
    losses = len([r for r in rows if r["pnl"] < 0])
    total = wins + losses
    win_rate = wins / total * 100.0 if total else 0
    pnl = sum(float(r["pnl"]) for r in rows)

    by_symbol = ai_v2_group_stats(rows, "ticker")
    by_hour = ai_v2_group_stats(rows, "hour_utc")

    bad_symbols = [s for s, st in by_symbol.items() if st["trades"] >= 3 and st["win_rate"] < AI_V2_BAD_SYMBOL_WINRATE]
    best_symbols = [s for s, st in by_symbol.items() if st["trades"] >= 3 and st["win_rate"] >= 55 and st["pnl"] > 0]

    bad_hours = [h for h, st in by_hour.items() if st["trades"] >= 3 and st["win_rate"] < AI_V2_BAD_HOUR_WINRATE]
    best_hours = [h for h, st in by_hour.items() if st["trades"] >= 3 and st["win_rate"] >= 55 and st["pnl"] > 0]

    mode = "normal"
    if win_rate < 35 or pnl < 0:
        mode = "defensive"
        recommendations += [
            "Defensive mode: keep SCANNER_MIN_QUALITY at 3.",
            "Keep MTF_CONFIRM_ENABLED=true.",
            "Avoid low-volume symbols and chop/range regimes."
        ]
    elif win_rate >= 55 and pnl > 0:
        mode = "stable"
        recommendations += [
            "Performance is acceptable.",
            "Do not increase risk until paper sample is much larger."
        ]

    if bad_symbols:
        recommendations.append("Avoid bad symbols: " + ", ".join(bad_symbols[:10]))
    if best_symbols:
        recommendations.append("Best symbols so far: " + ", ".join(best_symbols[:10]))
    if bad_hours:
        recommendations.append("Avoid weak UTC hours: " + ", ".join(bad_hours[:10]))
    if best_hours:
        recommendations.append("Best UTC hours so far: " + ", ".join(best_hours[:10]))

    AI_V2_STATE.update({
        "mode": mode,
        "last_review": now_iso(),
        "bad_symbols": bad_symbols,
        "best_symbols": best_symbols,
        "bad_hours_utc": bad_hours,
        "best_hours_utc": best_hours,
        "recommendations": recommendations,
        "stats": {
            "sample": len(rows),
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "pnl": pnl,
            "by_symbol": by_symbol,
            "by_hour_utc": by_hour,
        }
    })
    return AI_V2_STATE


def ai_v2_allows_trade(symbol: str):
    if not AI_V2_ENABLED:
        return True, "AI V2 disabled"

    state = ai_v2_review()
    symbol = symbol.upper()

    if AI_V2_BLOCK_BAD_SYMBOLS and symbol in state.get("bad_symbols", []):
        return False, f"AI V2 blocked bad symbol: {symbol}"

    if AI_V2_BLOCK_BAD_HOURS:
        hour = str(datetime.now(timezone.utc).hour)
        if hour in state.get("bad_hours_utc", []):
            return False, f"AI V2 blocked bad UTC hour: {hour}"

    return True, "AI V2 ok"


async def _ai_v2_loop():
    while True:
        try:
            ai_v2_review()
        except Exception as exc:
            AI_V2_STATE["last_error"] = str(exc)
        await asyncio.sleep(AI_V2_REVIEW_SECONDS)


def pro_engine_check(symbol: str, signal: str):
    if not PRO_ENGINE_ENABLED:
        return {"allow": True, "reason": "pro engine disabled"}

    ai_ok, ai_reason = ai_v2_allows_trade(symbol)
    if not ai_ok:
        return {"allow": False, "reason": ai_reason, "ai_v2": AI_V2_STATE}

    symbol = symbol.upper()
    checks = {}

    checks["loss_streak"] = pro_loss_streak_guard()
    if not checks["loss_streak"].get("allow", True):
        result = {"allow": False, "reason": checks["loss_streak"]["reason"], "checks": checks}
        PRO_ENGINE_STATE["last_checks"][symbol] = result
        return result

    checks["cooldown"] = pro_cooldown_check(symbol)
    if not checks["cooldown"].get("allow", True):
        result = {"allow": False, "reason": checks["cooldown"]["reason"], "checks": checks}
        PRO_ENGINE_STATE["last_checks"][symbol] = result
        return result

    checks["symbol_quality"] = pro_symbol_quality(symbol)
    if not checks["symbol_quality"].get("allow", True):
        result = {"allow": False, "reason": "symbol quality blocked", "checks": checks}
        PRO_ENGINE_STATE["last_checks"][symbol] = result
        return result

    checks["regime"] = pro_market_regime(symbol)
    if not checks["regime"].get("allow", True):
        result = {"allow": False, "reason": checks["regime"]["reason"], "checks": checks}
        PRO_ENGINE_STATE["last_checks"][symbol] = result
        return result

    # Regime direction alignment.
    regime = checks["regime"].get("regime")
    if signal in ["long", "buy"] and regime in ["breakout_down", "trend_down"]:
        result = {"allow": False, "reason": f"long blocked by bearish regime: {regime}", "checks": checks}
        PRO_ENGINE_STATE["last_checks"][symbol] = result
        return result

    if signal in ["short", "sell"] and regime in ["breakout_up", "trend_up"]:
        result = {"allow": False, "reason": f"short blocked by bullish regime: {regime}", "checks": checks}
        PRO_ENGINE_STATE["last_checks"][symbol] = result
        return result

    checks["entry_confirmation"] = pro_entry_confirmation(symbol, signal)
    if not checks["entry_confirmation"].get("allow", True):
        result = {"allow": False, "reason": checks["entry_confirmation"]["reason"], "checks": checks}
        PRO_ENGINE_STATE["last_checks"][symbol] = result
        return result

    result = {"allow": True, "reason": "pro engine passed", "checks": checks}
    PRO_ENGINE_STATE["last_checks"][symbol] = result
    return result


def pro_set_symbol_cooldown(symbol: str):
    until = int(datetime.now(timezone.utc).timestamp()) + PRO_SYMBOL_COOLDOWN_MINUTES * 60
    PRO_ENGINE_STATE.setdefault("symbol_cooldowns", {})[symbol.upper()] = until
    return until


def execute(payload: SignalPayload):
    if SAFETY_STATE.get("emergency_stop"):
        return {"approved": False, "reason": f"emergency stop active: {SAFETY_STATE.get('reason')}"}

    session_ok, session_reason = is_in_allowed_session()
    if not session_ok:
        return {"approved": False, "reason": session_reason}

    if not TRADING_ENABLED:
        return {"approved": False, "reason": "trading disabled by kill switch"}

    if get_open_positions_count() >= MAX_CONCURRENT_POSITIONS:
        return {"approved": False, "reason": "max concurrent positions reached"}

    ai_ok, ai_reason = ai_v2_allows_trade(payload.ticker)
    if not ai_ok:
        return {"approved": False, "reason": ai_reason, "ai_v2": AI_V2_STATE}

    ai3_ok, ai3_reason, ai3_conf = ai_v3_allows_trade(payload.ticker, payload.signal, payload.extra or {})
    if not ai3_ok:
        return {"approved": False, "reason": ai3_reason, "ai_v3": AI_V3_STATE, "confidence": ai3_conf}

    # Apply AI V3 risk multiplier into payload extra.
    if payload.extra is None:
        payload.extra = {}
    existing_mult = float(payload.extra.get("risk_multiplier", 1.0))
    payload.extra["risk_multiplier"] = existing_mult * float(AI_V3_STATE.get("risk_multiplier", 1.0))

    adaptive_ok, adaptive_reason = adaptive_allows_new_trade(payload.ticker)
    if not adaptive_ok:
        return {"approved": False, "reason": adaptive_reason, "adaptive": ADAPTIVE_STATE}

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

    pro = pro_engine_check(payload.ticker, payload.signal)
    if not pro.get("allow", True):
        response = {"approved": False, "reason": pro.get("reason"), "pro_engine": pro, "ts": now_iso()}
        journal(payload, False, pro.get("reason", "pro engine blocked"), response)
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
    response = {"approved": approved, "exchange": exchange, "structure": structure, "orderbook": orderbook, "setup_quality": setup_quality, "order_plan": plan, "result": result, "pro_engine": pro if "pro" in locals() else None, "ts": now_iso()}
    journal(payload, approved, result.get("reason", ""), response)

    if approved:
        pro_set_symbol_cooldown(payload.ticker)
        notify_user(
            "Trade opened",
            f"{payload.signal.upper()} {payload.ticker} on {exchange}",
            {"position_id": result.get("position_id"), "exchange": exchange, "plan": plan}
        )

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
        notify_user(
            "Position closed",
            f"{position['signal'].upper()} {position['ticker']} closed by manager",
            {"position_id": position["id"], "reason": reversal["reasons"], "close_result": close_result}
        )

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
                        WS_STATE["last_event"] = {"type": event_type, "result": result, "pro_engine": pro if "pro" in locals() else None, "ts": now_iso()}
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




def is_in_allowed_session():
    if not SESSION_FILTER_ENABLED:
        return True, "session filter disabled"

    now = datetime.now(timezone.utc)
    current_minutes = now.hour * 60 + now.minute

    for item in SESSION_ALLOWED_UTC_RANGES:
        try:
            start_s, end_s = item.split("-")
            sh, sm = [int(x) for x in start_s.split(":")]
            eh, em = [int(x) for x in end_s.split(":")]
            start_m = sh * 60 + sm
            end_m = eh * 60 + em

            if start_m <= end_m:
                if start_m <= current_minutes <= end_m:
                    return True, f"inside session {item}"
            else:
                # Overnight range like 22:00-03:00
                if current_minutes >= start_m or current_minutes <= end_m:
                    return True, f"inside overnight session {item}"
        except Exception:
            continue

    return False, "outside allowed trading session"


def scanner_safety_ok():
    if SAFETY_STATE.get("emergency_stop"):
        return False, f"emergency stop active: {SAFETY_STATE.get('reason')}"
    session_ok, session_reason = is_in_allowed_session()
    if not session_ok:
        return False, session_reason
    return True, "safe"


def structure_from_candles(candles: List[Dict[str, Any]]):
    highs, lows = _pivot_highs_lows(candles)
    if len(highs) < 3 or len(lows) < 3:
        return {"trend": "unknown", "highs": highs, "lows": lows}

    bull_structure = highs[-1] > highs[-2] and lows[-1] > lows[-2]
    bear_structure = highs[-1] < highs[-2] and lows[-1] < lows[-2]

    if bull_structure:
        return {"trend": "bullish", "highs": highs, "lows": lows}
    if bear_structure:
        return {"trend": "bearish", "highs": highs, "lows": lows}
    return {"trend": "sideways", "highs": highs, "lows": lows}


def mtf_confirmation(symbol: str, desired_signal: str):
    if not MTF_CONFIRM_ENABLED:
        return {"enabled": False, "allow": True, "reason": "mtf disabled"}

    try:
        candles = futures_klines(symbol, MTF_INTERVAL, SCANNER_LIMIT)
        st = structure_from_candles(candles)
        trend = st.get("trend")

        if desired_signal in ["long", "buy"]:
            allow = trend == "bullish"
            return {"enabled": True, "allow": allow, "trend": trend, "interval": MTF_INTERVAL, "reason": "mtf bullish" if allow else "mtf blocked long"}

        if desired_signal in ["short", "sell"]:
            allow = trend == "bearish"
            return {"enabled": True, "allow": allow, "trend": trend, "interval": MTF_INTERVAL, "reason": "mtf bearish" if allow else "mtf blocked short"}

        return {"enabled": True, "allow": False, "trend": trend, "interval": MTF_INTERVAL, "reason": "unknown signal"}

    except Exception as exc:
        return {"enabled": True, "allow": False, "reason": f"mtf error: {exc}"}


def elite_score_from_analysis(analysis: Dict[str, Any]):
    score = 0
    checks = analysis.get("checks", {})
    orderbook = analysis.get("orderbook", {})
    signal = analysis.get("signal")

    if signal in ["long", "buy"]:
        if checks.get("bull_bos"): score += 2
        if checks.get("bull_sweep"): score += 2
        if checks.get("volume_spike"): score += 1
        if checks.get("bull_candle"): score += 1
        if orderbook.get("pressure") == "buyers": score += 1
        if orderbook.get("pressure") == "sellers": score -= 2
    elif signal in ["short", "sell"]:
        if checks.get("bear_bos"): score += 2
        if checks.get("bear_sweep"): score += 2
        if checks.get("volume_spike"): score += 1
        if checks.get("bear_candle"): score += 1
        if orderbook.get("pressure") == "sellers": score += 1
        if orderbook.get("pressure") == "buyers": score -= 2

    if analysis.get("structure") in ["bullish", "bearish"]:
        score += 1

    return max(score, 0)


def auto_risk_multiplier_from_score(score: int):
    if not AUTO_RISK_ENABLED:
        return AUTO_RISK_BASE_MULTIPLIER
    if score >= 5:
        return AUTO_RISK_HIGH_QUALITY_MULTIPLIER
    if score <= 2:
        return AUTO_RISK_LOW_QUALITY_MULTIPLIER
    return AUTO_RISK_BASE_MULTIPLIER


async def _watchdog_loop():
    while WATCHDOG_ENABLED:
        try:
            if SCANNER_AUTO_START and not SAFETY_STATE.get("emergency_stop") and not SCANNER_STATE.get("running"):
                SCANNER_STATE["running"] = True
                SCANNER_STATE["last_error"] = None
                SCANNER_STATE["task"] = asyncio.create_task(_scanner_loop())
        except Exception as exc:
            SAFETY_STATE["watchdog_error"] = str(exc)
        await asyncio.sleep(WATCHDOG_SLEEP_SECONDS)



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

    safe, safety_reason = scanner_safety_ok()
    if not safe:
        SCANNER_STATE["last_scan"] = now_iso()
        SCANNER_STATE["last_results"] = [{"scanner_blocked": True, "reason": safety_reason}]
        return SCANNER_STATE["last_results"]

    for symbol in get_scanner_symbols():
        try:
            analysis = analyze_symbol_for_setup(symbol)
            results.append(analysis)

            signal = analysis.get("signal")
            if not signal:
                continue

            analysis["elite_score"] = elite_score_from_analysis(analysis)
            analysis["risk_multiplier"] = auto_risk_multiplier_from_score(analysis["elite_score"])

            mtf = mtf_confirmation(symbol, signal)
            analysis["mtf_confirmation"] = mtf
            if not mtf.get("allow", True):
                analysis["execution"] = {"approved": False, "reason": mtf.get("reason", "mtf blocked")}
                continue

            last_key = f"{symbol}:{signal}"
            last_sent = SCANNER_STATE["signals_sent"].get(last_key, 0)
            if now_ts - last_sent < SCANNER_COOLDOWN_SECONDS:
                analysis["execution"] = {"approved": False, "reason": "scanner cooldown active"}
                continue

            pro = pro_engine_check(symbol, signal)
            analysis["pro_engine"] = pro
            if not pro.get("allow", True):
                analysis["execution"] = {"approved": False, "reason": pro.get("reason"), "pro_engine": pro}
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
                extra={**analysis["extra"], "elite_score": analysis.get("elite_score", 0), "risk_multiplier": analysis.get("risk_multiplier", 1.0), "mtf_confirmation": analysis.get("mtf_confirmation", {})},
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




@app.post("/safety/emergency-stop")
def safety_emergency_stop(reason: str = "manual emergency stop"):
    SAFETY_STATE["emergency_stop"] = True
    SAFETY_STATE["reason"] = reason
    SAFETY_STATE["changed_at"] = now_iso()

    # Stop scanner immediately too.
    SCANNER_STATE["running"] = False
    task = SCANNER_STATE.get("task")
    if task:
        task.cancel()
    SCANNER_STATE["task"] = None

    return {"ok": True, "emergency_stop": True, "reason": reason}


@app.post("/safety/resume")
def safety_resume():
    SAFETY_STATE["emergency_stop"] = False
    SAFETY_STATE["reason"] = None
    SAFETY_STATE["changed_at"] = now_iso()
    return {"ok": True, "emergency_stop": False}


@app.get("/safety/status")
def safety_status():
    session_ok, session_reason = is_in_allowed_session()
    return {
        "emergency_stop": SAFETY_STATE.get("emergency_stop"),
        "reason": SAFETY_STATE.get("reason"),
        "changed_at": SAFETY_STATE.get("changed_at"),
        "watchdog_enabled": WATCHDOG_ENABLED,
        "scanner_auto_start": SCANNER_AUTO_START,
        "session_filter_enabled": SESSION_FILTER_ENABLED,
        "session_ok": session_ok,
        "session_reason": session_reason,
        "allowed_utc_ranges": SESSION_ALLOWED_UTC_RANGES,
        "mtf_confirm_enabled": MTF_CONFIRM_ENABLED,
        "mtf_interval": MTF_INTERVAL,
        "auto_risk_enabled": AUTO_RISK_ENABLED,
        "alerts_enabled": ALERTS_ENABLED,
        "live_manager_enabled": LIVE_MANAGER_ENABLED,
        "live_manager_auto_start": LIVE_MANAGER_AUTO_START,
        "pa_trail_enabled": PA_TRAIL_ENABLED,
        "pa_trail_bars": PA_TRAIL_BARS,
        "pa_trail_activate_r": PA_TRAIL_ACTIVATE_R,
        "adaptive_enabled": ADAPTIVE_ENABLED,
        "pro_engine_enabled": PRO_ENGINE_ENABLED,
        "pro_block_chop": PRO_BLOCK_CHOP,
        "pro_max_trades_per_hour": PRO_MAX_TRADES_PER_HOUR,
        "pro_min_24h_quote_volume": PRO_MIN_24H_QUOTE_VOLUME,
        "ai_v2_enabled": AI_V2_ENABLED,
        "ai_v2_mode": AI_V2_STATE.get("mode"),
        "ai_v3_enabled": AI_V3_ENABLED,
        "ai_v3_mode": AI_V3_STATE.get("mode"),
        "ai_v3_active_min_confidence": AI_V3_STATE.get("active_min_confidence"),
    }


@app.get("/positions/history")
def positions_history(limit: int = 100):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM positions ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return {"rows": [dict(r) for r in rows]}


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



def safe_json_loads(raw):
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def get_all_positions(limit: int = 500):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM positions ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
        return [dict(r) for r in rows]


def estimate_position_pnl(position: Dict[str, Any]):
    meta = safe_json_loads(position.get("metadata_json"))
    entry = float(position.get("entry_price") or 0)
    qty = float(position.get("qty") or 0)
    signal = position.get("signal")

    # Try to find close fill from manager close_result
    close_avg = None
    close_qty = qty
    last_check = meta.get("last_manage_check", {})
    for action in last_check.get("actions", []):
        if action.get("type") == "close_position":
            cr = action.get("close_result") or {}
            j = cr.get("json") or {}
            try:
                close_avg = float(j.get("avgPrice", 0) or 0)
                close_qty = float(j.get("executedQty", qty) or qty)
            except Exception:
                pass

    if close_avg is None:
        return {"realized": None, "close_price": None, "close_qty": close_qty}

    if signal in ["long", "buy"]:
        pnl = (close_avg - entry) * close_qty
    else:
        pnl = (entry - close_avg) * close_qty

    return {"realized": pnl, "close_price": close_avg, "close_qty": close_qty}



def notify_user(title: str, body: str, data: Optional[Dict[str, Any]] = None):
    if not ALERTS_ENABLED:
        return {"sent": False, "reason": "alerts disabled"}

    results = {}

    try:
        if DISCORD_WEBHOOK_URL:
            content = f"**{title}**\n{body}"
            if data:
                content += "\n```json\n" + json.dumps(data, default=str)[:1500] + "\n```"
            r = httpx.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10.0)
            results["discord"] = {"status_code": r.status_code, "ok": r.status_code < 300}
    except Exception as exc:
        results["discord"] = {"ok": False, "error": str(exc)}

    try:
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            text = f"{title}\n{body}"
            if data:
                text += "\n" + json.dumps(data, default=str)[:2500]
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            r = httpx.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10.0)
            results["telegram"] = {"status_code": r.status_code, "ok": r.status_code < 300}
    except Exception as exc:
        results["telegram"] = {"ok": False, "error": str(exc)}

    if not results:
        return {"sent": False, "reason": "no alert destination configured"}

    return {"sent": True, "results": results}


def _parse_dt_hour(created_at: str):
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00")).hour
    except Exception:
        return None


def analytics_trade_rows():
    positions = get_all_positions(1000) if "get_all_positions" in globals() else []
    rows = []
    for p in positions:
        pnl_info = estimate_position_pnl(p) if "estimate_position_pnl" in globals() else {"realized": None}
        meta = safe_json_loads(p.get("metadata_json")) if "safe_json_loads" in globals() else {}
        rows.append({
            "id": p.get("id"),
            "created_at": p.get("created_at"),
            "hour_utc": _parse_dt_hour(p.get("created_at") or ""),
            "ticker": p.get("ticker"),
            "signal": p.get("signal"),
            "exchange": p.get("exchange"),
            "status": p.get("status"),
            "entry_price": p.get("entry_price"),
            "stop_price": p.get("stop_price"),
            "qty": p.get("qty"),
            "risk_usd": p.get("risk_usd"),
            "pnl": pnl_info.get("realized"),
            "close_price": pnl_info.get("close_price"),
            "strategy": meta.get("strategy"),
            "risk_per_unit": meta.get("risk_per_unit"),
        })
    return rows


def analytics_summary_data():
    rows = analytics_trade_rows()
    closed = [r for r in rows if r.get("status") == "closed" and r.get("pnl") is not None]
    open_rows = [r for r in rows if r.get("status") == "open"]

    wins = len([r for r in closed if r["pnl"] > 0])
    losses = len([r for r in closed if r["pnl"] < 0])
    breakeven = len([r for r in closed if r["pnl"] == 0])
    total = wins + losses + breakeven
    realized = sum(float(r["pnl"]) for r in closed)
    gross_win = sum(float(r["pnl"]) for r in closed if r["pnl"] > 0)
    gross_loss = abs(sum(float(r["pnl"]) for r in closed if r["pnl"] < 0))

    equity = []
    running = 0.0
    for r in sorted(closed, key=lambda x: x.get("created_at") or ""):
        running += float(r["pnl"])
        equity.append({"time": r.get("created_at"), "equity": running, "pnl": r["pnl"], "ticker": r["ticker"]})

    by_symbol = {}
    by_hour = {}
    for r in closed:
        s = r.get("ticker") or "UNKNOWN"
        by_symbol.setdefault(s, {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})
        by_symbol[s]["trades"] += 1
        by_symbol[s]["pnl"] += float(r["pnl"])
        if r["pnl"] > 0:
            by_symbol[s]["wins"] += 1
        elif r["pnl"] < 0:
            by_symbol[s]["losses"] += 1

        h = r.get("hour_utc")
        if h is not None:
            by_hour.setdefault(str(h), {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})
            by_hour[str(h)]["trades"] += 1
            by_hour[str(h)]["pnl"] += float(r["pnl"])
            if r["pnl"] > 0:
                by_hour[str(h)]["wins"] += 1
            elif r["pnl"] < 0:
                by_hour[str(h)]["losses"] += 1

    return {
        "summary": {
            "open_positions": len(open_rows),
            "closed_counted": total,
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate": (wins / total * 100.0) if total else 0.0,
            "realized_pnl": realized,
            "avg_win": gross_win / wins if wins else 0.0,
            "avg_loss": -(gross_loss / losses) if losses else 0.0,
            "profit_factor": (gross_win / gross_loss) if gross_loss else None,
        },
        "equity_curve": equity,
        "by_symbol": by_symbol,
        "by_hour_utc": by_hour,
        "recent_trades": rows[:100],
    }


def dashboard_stats_data():
    positions = get_all_positions(500)
    open_positions = [p for p in positions if p.get("status") == "open"]
    closed_positions = [p for p in positions if p.get("status") == "closed"]

    realized_total = 0.0
    wins = 0
    losses = 0
    breakeven = 0
    pnl_rows = []

    for p in closed_positions:
        pnl_info = estimate_position_pnl(p)
        pnl = pnl_info.get("realized")
        if pnl is None:
            continue
        realized_total += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        else:
            breakeven += 1
        pnl_rows.append({
            "id": p.get("id"),
            "ticker": p.get("ticker"),
            "signal": p.get("signal"),
            "exchange": p.get("exchange"),
            "entry_price": p.get("entry_price"),
            "close_price": pnl_info.get("close_price"),
            "qty": pnl_info.get("close_qty"),
            "pnl": pnl,
            "created_at": p.get("created_at"),
        })

    total_counted = wins + losses + breakeven
    win_rate = (wins / total_counted * 100.0) if total_counted > 0 else 0.0

    watchlist = []
    scanner_results = SCANNER_STATE.get("last_results", []) or []
    for r in scanner_results:
        if r.get("signal"):
            watchlist.append({
                "symbol": r.get("symbol"),
                "signal": r.get("signal"),
                "quality_score": r.get("quality_score") or r.get("elite_score"),
                "structure": r.get("structure"),
                "reason": "valid setup",
                "orderbook": r.get("orderbook"),
                "checks": r.get("checks"),
                "execution": r.get("execution"),
            })
        else:
            checks = r.get("checks", {})
            quality = max(int(checks.get("long_quality", 0) or 0), int(checks.get("short_quality", 0) or 0))
            if quality >= 1 or r.get("structure") in ["bullish", "bearish"]:
                watchlist.append({
                    "symbol": r.get("symbol"),
                    "signal": None,
                    "quality_score": quality,
                    "structure": r.get("structure"),
                    "reason": r.get("reason"),
                    "orderbook": r.get("orderbook"),
                    "checks": checks,
                })

    return {
        "summary": {
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate": win_rate,
            "realized_pnl": realized_total,
            "scanner_running": SCANNER_STATE.get("running", False),
            "emergency_stop": SAFETY_STATE.get("emergency_stop", False),
        },
        "pnl_rows": pnl_rows[:100],
        "watchlist": watchlist[:100],
    }






def current_market_price_for_position(position: Dict[str, Any]):
    if position["exchange"] == "paper":
        return float(position["entry_price"])
    if position["exchange"] == "binance_spot":
        data = public_get(BINANCE_SPOT_BASE_URL, "/api/v3/ticker/price", {"symbol": position["ticker"]})
        if data["status_code"] >= 400:
            raise RuntimeError(data["text"])
        return float(data["json"]["price"])
    if position["exchange"] == "binance_futures":
        data = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/ticker/price", {"symbol": position["ticker"]})
        if data["status_code"] >= 400:
            raise RuntimeError(data["text"])
        return float(data["json"]["price"])
    raise RuntimeError("unsupported exchange")


def position_unrealized_pnl(position: Dict[str, Any], mark_price: float):
    entry = float(position.get("entry_price") or 0)
    qty = float(position.get("qty") or 0)
    return (mark_price - entry) * qty if position.get("signal") in ["long", "buy"] else (entry - mark_price) * qty


def r_multiple(position: Dict[str, Any], mark_price: float):
    entry = float(position.get("entry_price") or 0)
    stop = float(position.get("stop_price") or entry)
    risk = abs(entry - stop) or max(entry * 0.001, 0.000001)
    pnl_per_unit = mark_price - entry if position.get("signal") in ["long", "buy"] else entry - mark_price
    return pnl_per_unit / risk


def recent_price_action_trail(symbol: str, is_long: bool):
    if not PA_TRAIL_ENABLED:
        return None
    candles = futures_klines(symbol, PA_TRAIL_INTERVAL, max(PA_TRAIL_BARS + 3, 10))
    closed = candles[:-1] if len(candles) > PA_TRAIL_BARS else candles
    recent = closed[-PA_TRAIL_BARS:]
    if not recent:
        return None
    return min(c["low"] for c in recent) if is_long else max(c["high"] for c in recent)


def apply_price_action_trailing(position: Dict[str, Any], mark_price: float):
    if position.get("exchange") != "binance_futures":
        return {"applied": False, "reason": "futures only"}
    current_r = r_multiple(position, mark_price)
    if current_r < PA_TRAIL_ACTIVATE_R:
        return {"applied": False, "reason": "not activated", "current_r": current_r}
    is_long = position.get("signal") in ["long", "buy"]
    trail = recent_price_action_trail(position["ticker"], is_long)
    if trail is None:
        return {"applied": False, "reason": "no trail"}
    old_stop = float(position.get("stop_price") or 0)
    if is_long and trail > old_stop and trail < mark_price:
        update_position(position["id"], {"stop_price": trail})
        return {"applied": True, "new_stop": trail, "old_stop": old_stop, "current_r": current_r}
    if (not is_long) and (old_stop <= 0 or (trail < old_stop and trail > mark_price)):
        update_position(position["id"], {"stop_price": trail})
        return {"applied": True, "new_stop": trail, "old_stop": old_stop, "current_r": current_r}
    return {"applied": False, "reason": "trail not better", "trail": trail, "old_stop": old_stop, "current_r": current_r}


def adaptive_recent_closed_rows():
    if "analytics_trade_rows" not in globals():
        return []
    closed = [r for r in analytics_trade_rows() if r.get("status") == "closed" and r.get("pnl") is not None]
    return closed[:ADAPTIVE_LOOKBACK_TRADES]


def evaluate_adaptive_optimizer():
    if not ADAPTIVE_ENABLED:
        ADAPTIVE_STATE.update({"mode": "disabled", "reason": "adaptive disabled", "last_eval": now_iso()})
        return ADAPTIVE_STATE
    now_ts = int(datetime.now(timezone.utc).timestamp())
    paused_until = ADAPTIVE_STATE.get("paused_until")
    if paused_until and now_ts < paused_until:
        ADAPTIVE_STATE.update({"mode": "cooldown", "last_eval": now_iso()})
        return ADAPTIVE_STATE
    rows = adaptive_recent_closed_rows()
    if len(rows) < ADAPTIVE_MIN_TRADES:
        ADAPTIVE_STATE.update({"mode": "learning", "reason": f"need {ADAPTIVE_MIN_TRADES} closed trades", "last_eval": now_iso(), "recommendations": ["Keep paper mode running to collect more data."]})
        return ADAPTIVE_STATE
    wins = len([r for r in rows if r["pnl"] > 0])
    losses = len([r for r in rows if r["pnl"] < 0])
    total = wins + losses
    win_rate = wins / total * 100 if total else 0
    streak = 0
    for r in rows:
        if r["pnl"] < 0:
            streak += 1
        else:
            break
    mode = "normal"
    reason = "performance acceptable"
    recs = []
    if streak >= ADAPTIVE_MAX_LOSS_STREAK or win_rate < ADAPTIVE_MIN_WIN_RATE:
        mode = "defensive"
        reason = f"bad performance: win_rate={win_rate:.1f}%, loss_streak={streak}"
        ADAPTIVE_STATE["paused_until"] = now_ts + ADAPTIVE_PAUSE_MINUTES * 60
        recs = ["Pause entries temporarily.", "Increase SCANNER_MIN_QUALITY.", "Enable MTF_CONFIRM_ENABLED.", "Review best/worst symbols and hours."]
    ADAPTIVE_STATE.update({"mode": mode, "reason": reason, "last_eval": now_iso(), "win_rate": win_rate, "loss_streak": streak, "sample_size": len(rows), "recommendations": recs})
    return ADAPTIVE_STATE


def adaptive_allows_new_trade(symbol: str):
    state = evaluate_adaptive_optimizer()
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if state.get("paused_until") and now_ts < state["paused_until"]:
        return False, f"adaptive pause active until {state['paused_until']}"
    return True, "adaptive ok"


async def _live_manager_loop():
    while LIVE_MANAGER_ENABLED:
        try:
            actions = []
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                positions = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='open' ORDER BY created_at DESC").fetchall()]
            for p in positions:
                try:
                    mark = current_market_price_for_position(p)
                    pa = apply_price_action_trailing(p, mark)
                    p2 = get_position(p["id"]) or p
                    payload = ManagePositionPayload(position_id=p["id"], current_price=mark, dry_run=False)
                    managed = manage_position(p2, mark, payload)
                    actions.append({"position_id": p["id"], "ticker": p["ticker"], "mark_price": mark, "unrealized_pnl": position_unrealized_pnl(p, mark), "current_r": r_multiple(p, mark), "pa_trailing": pa, "managed_actions": managed.get("actions") if isinstance(managed, dict) else None})
                except Exception as exc:
                    actions.append({"position_id": p.get("id"), "ticker": p.get("ticker"), "error": str(exc)})
            LIVE_MANAGER_STATE["last_run"] = now_iso()
            LIVE_MANAGER_STATE["last_actions"] = actions[:50]
            LIVE_MANAGER_STATE["last_error"] = None
            evaluate_adaptive_optimizer()
        except Exception as exc:
            LIVE_MANAGER_STATE["last_error"] = str(exc)
        await asyncio.sleep(LIVE_MANAGER_SLEEP_SECONDS)


@app.get("/analytics/summary")
def analytics_summary():
    return analytics_summary_data()


@app.get("/analytics/equity-curve")
def analytics_equity_curve():
    return {"equity_curve": analytics_summary_data()["equity_curve"]}


@app.get("/analytics/session-performance")
def analytics_session_performance():
    data = analytics_summary_data()
    return {"by_hour_utc": data["by_hour_utc"]}


@app.post("/alerts/test")
def alerts_test():
    return notify_user("Bot alert test", "Your bot alerts are working.", {"time": now_iso()})



@app.get("/positions/live")
def positions_live():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        positions = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE status='open' ORDER BY created_at DESC").fetchall()]
    rows = []
    for p in positions:
        try:
            mark = current_market_price_for_position(p)
            rows.append({**p, "mark_price": mark, "unrealized_pnl": position_unrealized_pnl(p, mark), "current_r": r_multiple(p, mark)})
        except Exception as exc:
            rows.append({**p, "live_error": str(exc)})
    return {"rows": rows, "manager": LIVE_MANAGER_STATE}


@app.get("/manager/status")
def manager_status():
    return {"enabled": LIVE_MANAGER_ENABLED, "auto_start": LIVE_MANAGER_AUTO_START, "running": LIVE_MANAGER_STATE.get("running"), "sleep_seconds": LIVE_MANAGER_SLEEP_SECONDS, "pa_trailing_enabled": PA_TRAIL_ENABLED, "pa_trail_bars": PA_TRAIL_BARS, "pa_trail_interval": PA_TRAIL_INTERVAL, "pa_trail_activate_r": PA_TRAIL_ACTIVATE_R, "last_run": LIVE_MANAGER_STATE.get("last_run"), "last_error": LIVE_MANAGER_STATE.get("last_error"), "last_actions": LIVE_MANAGER_STATE.get("last_actions", [])}


@app.post("/manager/start")
async def manager_start():
    if LIVE_MANAGER_STATE.get("running"):
        return {"ok": True, "reason": "manager already running"}
    LIVE_MANAGER_STATE["running"] = True
    LIVE_MANAGER_STATE["task"] = asyncio.create_task(_live_manager_loop())
    return {"ok": True, "reason": "manager started"}


@app.post("/manager/stop")
async def manager_stop():
    LIVE_MANAGER_STATE["running"] = False
    task = LIVE_MANAGER_STATE.get("task")
    if task:
        task.cancel()
    LIVE_MANAGER_STATE["task"] = None
    return {"ok": True, "reason": "manager stopped"}


@app.get("/adaptive/status")
def adaptive_status():
    return evaluate_adaptive_optimizer()



@app.get("/pro-engine/status")
def pro_engine_status():
    return {
        "enabled": PRO_ENGINE_ENABLED,
        "block_chop": PRO_BLOCK_CHOP,
        "regime_interval": PRO_REGIME_INTERVAL,
        "entry_body_min": PRO_ENTRY_BODY_MIN,
        "max_trades_per_hour": PRO_MAX_TRADES_PER_HOUR,
        "symbol_cooldown_minutes": PRO_SYMBOL_COOLDOWN_MINUTES,
        "min_24h_quote_volume": PRO_MIN_24H_QUOTE_VOLUME,
        "allowed_symbols": PRO_ALLOWED_SYMBOLS,
        "state": PRO_ENGINE_STATE,
    }


@app.post("/pro-engine/reset")
def pro_engine_reset():
    PRO_ENGINE_STATE["symbol_cooldowns"] = {}
    PRO_ENGINE_STATE["global_pause_until"] = None
    PRO_ENGINE_STATE["last_checks"] = {}
    return {"ok": True, "reason": "pro engine state reset"}


@app.get("/pro-engine/check/{symbol}/{signal}")
def pro_engine_manual_check(symbol: str, signal: str):
    return pro_engine_check(symbol.upper(), signal.lower())




@app.get("/ai-v2/status")
def ai_v2_status():
    return ai_v2_review()


@app.post("/ai-v2/reset")
def ai_v2_reset():
    AI_V2_STATE.update({
        "mode": "learning",
        "last_review": None,
        "bad_symbols": [],
        "best_symbols": [],
        "bad_hours_utc": [],
        "best_hours_utc": [],
        "recommendations": [],
        "stats": {},
    })
    return {"ok": True, "reason": "AI V2 state reset"}


@app.get("/ai-v2/recommendations")
def ai_v2_recommendations():
    state = ai_v2_review()
    return {"mode": state.get("mode"), "recommendations": state.get("recommendations", []), "stats": state.get("stats", {})}



@app.get("/ai-v3/status")
def ai_v3_status():
    return ai_v3_review()


@app.get("/ai-v3/confidence/{symbol}/{signal}")
def ai_v3_confidence(symbol: str, signal: str):
    return ai_v3_market_confidence(symbol.upper(), signal.lower(), {})


@app.post("/ai-v3/reset")
def ai_v3_reset():
    AI_V3_STATE.update({
        "mode": "learning",
        "last_review": None,
        "active_min_confidence": None,
        "risk_multiplier": 1.0,
        "recommendations": [],
        "last_confidence": {},
        "stats": {},
    })
    return {"ok": True, "reason": "AI V3 reset"}



def market_top_movers(limit: int = 20):
    r = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/ticker/24hr", {})
    if r["status_code"] >= 400:
        raise RuntimeError(r["text"])

    rows = []
    for t in r["json"]:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        try:
            rows.append({
                "symbol": symbol,
                "price_change_percent": float(t.get("priceChangePercent", 0) or 0),
                "quote_volume": float(t.get("quoteVolume", 0) or 0),
                "last_price": float(t.get("lastPrice", 0) or 0),
                "high_price": float(t.get("highPrice", 0) or 0),
                "low_price": float(t.get("lowPrice", 0) or 0),
            })
        except Exception:
            continue

    return {
        "gainers": sorted(rows, key=lambda x: x["price_change_percent"], reverse=True)[:limit],
        "losers": sorted(rows, key=lambda x: x["price_change_percent"])[:limit],
        "volume": sorted(rows, key=lambda x: x["quote_volume"], reverse=True)[:limit],
        "count": len(rows),
        "ts": now_iso()
    }


def market_new_listings(limit: int = 20):
    r = public_get(BINANCE_FUTURES_BASE_URL, "/fapi/v1/exchangeInfo", {})
    if r["status_code"] >= 400:
        raise RuntimeError(r["text"])

    rows = []
    for s in r["json"].get("symbols", []):
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("status") != "TRADING":
            continue
        rows.append({
            "symbol": s.get("symbol"),
            "base_asset": s.get("baseAsset"),
            "quote_asset": s.get("quoteAsset"),
            "onboard_date": s.get("onboardDate"),
            "margin_asset": s.get("marginAsset"),
            "price_precision": s.get("pricePrecision"),
            "quantity_precision": s.get("quantityPrecision"),
        })

    return {"rows": sorted(rows, key=lambda x: x.get("onboard_date") or 0, reverse=True)[:limit], "ts": now_iso()}


def market_news(limit: int = 10):
    url = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    payload = {"type": 1, "catalogId": 48, "pageNo": 1, "pageSize": max(1, min(limit, 20))}
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.post(url, json=payload)
        data = r.json()
        articles = (((data or {}).get("data") or {}).get("articles") or [])
        rows = []
        for a in articles[:limit]:
            code = str(a.get("code", ""))
            rows.append({
                "title": a.get("title"),
                "code": code,
                "release_date": a.get("releaseDate"),
                "url": "https://www.binance.com/en/support/announcement/" + code,
            })
        return {"rows": rows, "source": "binance_announcements", "ts": now_iso()}
    except Exception as exc:
        return {"rows": [], "source": "binance_announcements", "error": str(exc), "ts": now_iso()}



@app.get("/market/top-movers")
def market_top_movers_endpoint(limit: int = 20):
    return market_top_movers(limit)


@app.get("/market/new-listings")
def market_new_listings_endpoint(limit: int = 20):
    return market_new_listings(limit)


@app.get("/market/news")
def market_news_endpoint(limit: int = 10):
    return market_news(limit)



@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Monster Bot Command Center</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root{
            --bg:#050816; --panel:#0c1324; --panel2:#101a31; --glass:rgba(15,23,42,.72);
            --line:rgba(148,163,184,.16); --text:#e5e7eb; --muted:#94a3b8;
            --green:#22c55e; --red:#ef4444; --yellow:#facc15; --blue:#38bdf8; --purple:#a78bfa; --orange:#fb923c; --cyan:#22d3ee;
        }
        *{box-sizing:border-box}
        body{
            margin:0; color:var(--text); font-family:Inter,Segoe UI,Arial,sans-serif; min-height:100vh;
            background:
              radial-gradient(circle at 12% 8%, rgba(34,211,238,.22), transparent 28%),
              radial-gradient(circle at 80% 0%, rgba(167,139,250,.23), transparent 30%),
              radial-gradient(circle at 50% 95%, rgba(34,197,94,.10), transparent 32%),
              linear-gradient(140deg,#030712,#08111f 45%,#0b1020);
            overflow-x:hidden;
        }
        body:before{
            content:""; position:fixed; inset:0; pointer-events:none; opacity:.38;
            background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);
            background-size:38px 38px;
            mask-image:linear-gradient(to bottom,black,transparent 85%);
        }
        header{
            position:sticky; top:0; z-index:50; padding:18px 24px; border-bottom:1px solid var(--line);
            background:rgba(3,7,18,.72); backdrop-filter:blur(20px);
        }
        .topbar{display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px}
        h1{margin:0; font-size:25px; letter-spacing:.2px; text-shadow:0 0 26px rgba(56,189,248,.25)}
        .sub{color:var(--muted); font-size:12px; margin-top:4px}
        .badge{
            display:inline-flex; align-items:center; gap:7px; padding:8px 12px; border-radius:999px;
            border:1px solid var(--line); background:rgba(15,23,42,.74); color:#cbd5e1; font-size:12px;
            box-shadow:inset 0 1px 0 rgba(255,255,255,.05);
        }
        .wrap{max-width:1680px; margin:auto; padding:18px}
        .hero{display:grid; grid-template-columns:1.35fr .65fr; gap:16px; margin-bottom:16px}
        .grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(215px,1fr)); gap:16px}
        .grid2{display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px}
        .grid3{display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; margin-top:16px}
        @media(max-width:1100px){.hero,.grid2,.grid3{grid-template-columns:1fr}}
        .card{
            position:relative; background:linear-gradient(180deg,rgba(15,23,42,.86),rgba(9,14,26,.92));
            border:1px solid var(--line); border-radius:24px; padding:18px;
            box-shadow:0 24px 70px rgba(0,0,0,.38), inset 0 1px 0 rgba(255,255,255,.04);
            overflow:hidden;
        }
        .card:after{
            content:""; position:absolute; inset:-1px; pointer-events:none; border-radius:24px;
            background:linear-gradient(135deg,rgba(56,189,248,.16),transparent 35%,rgba(167,139,250,.12));
            opacity:.55; mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0); -webkit-mask-composite:xor; mask-composite:exclude; padding:1px;
        }
        .card h3{
            margin:0 0 12px; color:#b6c3d5; font-size:12px; font-weight:900; text-transform:uppercase; letter-spacing:.12em;
        }
        .big{font-size:31px; font-weight:950; line-height:1}
        .small{font-size:12px; color:var(--muted); margin-top:7px}
        .green{color:var(--green)} .red{color:var(--red)} .yellow{color:var(--yellow)} .blue{color:var(--blue)} .purple{color:var(--purple)} .orange{color:var(--orange)}
        .kpi{min-height:118px}
        .kpi .orb{position:absolute; right:-30px; top:-30px; width:105px; height:105px; border-radius:50%; filter:blur(.2px); opacity:.2; background:var(--blue)}
        button{
            border:0; border-radius:14px; padding:10px 13px; margin:4px; background:#2563eb; color:white; cursor:pointer; font-weight:850;
            box-shadow:0 12px 28px rgba(37,99,235,.22), inset 0 1px 0 rgba(255,255,255,.12);
        }
        button:hover{filter:brightness(1.15); transform:translateY(-1px)}
        button.gray{background:#334155; box-shadow:none} button.danger{background:#dc2626} button.good{background:#16a34a} button.warn{background:#ca8a04}
        .controls{display:flex; flex-wrap:wrap; gap:6px}
        .pill{
            display:inline-flex; align-items:center; gap:5px; padding:5px 9px; border-radius:999px; font-size:12px; font-weight:900;
            border:1px solid transparent; white-space:nowrap;
        }
        .pill.long,.pill.buy,.pill.win,.pill.open{color:var(--green); background:rgba(34,197,94,.14); border-color:rgba(34,197,94,.25)}
        .pill.short,.pill.sell,.pill.loss,.pill.stop{color:var(--red); background:rgba(239,68,68,.14); border-color:rgba(239,68,68,.25)}
        .pill.watch,.pill.neutral{color:var(--yellow); background:rgba(250,204,21,.12); border-color:rgba(250,204,21,.25)}
        .pill.info{color:var(--blue); background:rgba(56,189,248,.12); border-color:rgba(56,189,248,.25)}
        .section{margin:18px 0 10px; font-size:15px; font-weight:950; color:#dbeafe}
        .scroll{overflow:auto; max-height:430px; border:1px solid rgba(148,163,184,.12); border-radius:16px}
        table{width:100%; border-collapse:collapse}
        th,td{padding:10px; border-bottom:1px solid rgba(148,163,184,.11); font-size:12px; text-align:left; white-space:nowrap}
        th{color:#9fb0c6; background:rgba(2,6,23,.55); position:sticky; top:0; z-index:1}
        tr:hover{background:rgba(56,189,248,.055)}
        canvas{width:100%; height:225px; background:rgba(2,6,23,.25); border-radius:16px; border:1px solid rgba(148,163,184,.12)}
        .meter{height:8px; background:#1e293b; border-radius:999px; overflow:hidden; margin-top:10px}.meter>div{height:100%; width:0%; background:linear-gradient(90deg,var(--red),var(--yellow),var(--green))}
        .dot{width:10px;height:10px;border-radius:50%;background:var(--yellow);box-shadow:0 0 18px currentColor}.dot.green{background:var(--green);color:var(--green)}.dot.red{background:var(--red);color:var(--red)}
        .health{display:flex;align-items:center;gap:10px;margin-top:13px}
        .marketGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
        .coinCard,.newsCard{
            background:linear-gradient(180deg,rgba(2,6,23,.48),rgba(15,23,42,.72)); border:1px solid rgba(148,163,184,.13);
            border-radius:18px; padding:13px; position:relative; overflow:hidden;
        }
        .coinCard:before{content:"";position:absolute;right:-25px;top:-25px;width:80px;height:80px;border-radius:50%;background:rgba(56,189,248,.09)}
        .coinTop{display:flex;justify-content:space-between;align-items:center;gap:8px}
        .sym{font-size:16px;font-weight:950}.price{font-size:12px;color:#cbd5e1}.chg{font-size:18px;font-weight:950;margin-top:8px}
        .vol{font-size:11px;color:var(--muted);margin-top:6px}
        .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
        .tab{padding:7px 10px;border-radius:999px;background:rgba(51,65,85,.65);color:#cbd5e1;font-size:12px;cursor:pointer;font-weight:850}
        .tab.active{background:rgba(56,189,248,.18);color:var(--blue);border:1px solid rgba(56,189,248,.22)}
        .newsTitle{font-weight:850;line-height:1.25}.newsDate{font-size:11px;color:var(--muted);margin-top:7px}
        pre{background:#020617;color:#cbd5e1;border:1px solid rgba(148,163,184,.12);padding:12px;border-radius:15px;overflow:auto;max-height:360px;font-size:11px}
        a{color:var(--blue);text-decoration:none}
    </style>
</head>
<body>
<header>
  <div class="topbar">
    <div><h1>Monster Bot Command Center</h1><div class="sub">Live trading brain • PRO engine • AI V3 confidence • Market intelligence</div></div>
    <div><span class="badge" id="serviceBadge">Loading...</span><span class="badge" id="clockBadge"></span></div>
  </div>
</header>

<div class="wrap">
  <div class="hero">
    <div class="card">
      <h3>Mission Control</h3>
      <div class="controls">
        <button class="good" onclick="startScanner()">Start Scanner</button><button class="gray" onclick="stopScanner()">Stop Scanner</button>
        <button onclick="scanOnce()">Scan Once</button><button onclick="refreshDiscovery()">Refresh Symbols</button>
        <button onclick="startManager()">Start Manager</button><button class="gray" onclick="stopManager()">Stop Manager</button>
        <button onclick="startWs()">Start Futures WS</button><button class="gray" onclick="stopWs()">Stop Futures WS</button>
        <button onclick="syncFutures()">Sync Futures</button><button class="warn" onclick="resetPro()">Reset PRO</button>
        <button class="danger" onclick="emergencyStop()">Emergency Stop</button><button class="good" onclick="resumeSafety()">Resume</button>
        <button class="gray" onclick="refreshAll()">Refresh</button><a href="/docs" target="_blank"><button class="gray">API Docs</button></a>
      </div>
      <div class="health"><span id="healthDot" class="dot"></span><span id="healthText" class="small">Checking system...</span></div>
      <div id="controlResult" class="small"></div>
    </div>
    <div class="card">
      <h3>Fast Trail System</h3>
      <div class="marketGrid">
        <div class="coinCard"><div class="sym">BE 0.50R</div><div class="small">Fast break-even protection</div></div>
        <div class="coinCard"><div class="sym">3-Bar Trail</div><div class="small">TP follows price action</div></div>
        <div class="coinCard"><div class="sym">10s Manager</div><div class="small">Near-live position checks</div></div>
        <div class="coinCard"><div class="sym">AI V3</div><div class="small">Confidence filter</div></div>
      </div>
    </div>
  </div>

  <div class="grid">
    <div class="card kpi"><span class="orb"></span><h3>Server</h3><div id="serverStatus" class="big yellow">...</div><div id="serverLine" class="small"></div></div>
    <div class="card kpi"><span class="orb"></span><h3>Execution</h3><div id="executionStatus" class="big yellow">...</div><div id="riskLine" class="small"></div></div>
    <div class="card kpi"><span class="orb"></span><h3>Scanner</h3><div id="scannerStatus" class="big yellow">...</div><div id="scannerLine" class="small"></div></div>
    <div class="card kpi"><span class="orb"></span><h3>Live Manager</h3><div id="managerStatus" class="big yellow">...</div><div id="managerLine" class="small"></div></div>
    <div class="card kpi"><span class="orb"></span><h3>Safety</h3><div id="safetyStatus" class="big yellow">...</div><div id="safetyLine" class="small"></div></div>
  </div>

  <div class="grid" style="margin-top:16px">
    <div class="card kpi"><h3>Open Positions</h3><div id="openCount" class="big blue">0</div><div class="small">Live tracked trades</div></div>
    <div class="card kpi"><h3>Unrealized PnL</h3><div id="unrealizedPnl" class="big yellow">$0.00</div><div class="small">Live estimate</div></div>
    <div class="card kpi"><h3>Realized PnL</h3><div id="realizedPnl" class="big yellow">$0.00</div><div class="small">Closed trades estimate</div></div>
    <div class="card kpi"><h3>Win Rate</h3><div id="winRate" class="big purple">0%</div><div class="meter"><div id="winBar"></div></div><div id="wlLine" class="small"></div></div>
    <div class="card kpi"><h3>Watchlist</h3><div id="watchCount" class="big orange">0</div><div class="small">Interesting setups</div></div>
  </div>

  <div class="grid2">
    <div class="card"><h3>Equity Curve</h3><canvas id="equityCanvas" width="900" height="260"></canvas></div>
    <div class="card"><h3>Open Positions Live</h3><div class="scroll"><table><thead><tr><th>Status</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Mark</th><th>Stop</th><th>PnL</th><th>R</th><th>Qty</th><th>Action</th></tr></thead><tbody id="positionsBody"><tr><td colspan="10">Loading...</td></tr></tbody></table></div></div>
  </div>

  <div class="section">Market Intelligence</div>
  <div class="grid3">
    <div class="card"><h3>Top Movers</h3><div class="tabs"><span class="tab active" onclick="renderMovers('gainers')">Gainers</span><span class="tab" onclick="renderMovers('losers')">Losers</span><span class="tab" onclick="renderMovers('volume')">Volume</span></div><div id="topMoversCards" class="marketGrid"></div></div>
    <div class="card"><h3>New Futures Listings</h3><div id="newListingsCards" class="marketGrid"></div></div>
    <div class="card"><h3>Binance News</h3><div id="newsCards"></div></div>
  </div>

  <div class="grid2">
    <div class="card"><h3>Scanner Watchlist / Near Setups</h3><div class="scroll"><table><thead><tr><th>Symbol</th><th>Signal</th><th>Structure</th><th>Quality</th><th>Orderbook</th><th>Reason</th></tr></thead><tbody id="watchBody"><tr><td colspan="6">Loading...</td></tr></tbody></table></div></div>
    <div class="card"><h3>Closed Trades / PnL</h3><div class="scroll"><table><thead><tr><th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>Qty</th><th>PnL</th><th>Time</th></tr></thead><tbody id="pnlBody"><tr><td colspan="7">Loading...</td></tr></tbody></table></div></div>
  </div>

  <div class="grid3">
    <div class="card"><h3>PRO Engine</h3><pre id="proBox">Loading...</pre></div>
    <div class="card"><h3>AI V3 Confidence Brain</h3><pre id="aiV3Box">Loading...</pre></div>
    <div class="card"><h3>System Detail</h3><pre id="systemBox">Loading...</pre></div>
  </div>
</div>

<script>
let moversData = {};
let moversMode = "gainers";
async function getJson(url, opts={}){const r=await fetch(url,opts);if(!r.ok)throw new Error(r.status+" "+await r.text());return await r.json();}
function money(n){if(n===null||n===undefined||isNaN(n))return "$0.00";return "$"+Number(n).toFixed(4)}
function fmt(n){if(n===null||n===undefined)return "";if(typeof n==="number")return Number(n).toFixed(6).replace(/0+$/,"").replace(/\.$/,"");return n}
function pill(txt,cls="neutral"){return `<span class="pill ${cls}">${txt??""}</span>`}
function compactVol(n){n=Number(n||0); if(n>=1e9)return (n/1e9).toFixed(2)+"B"; if(n>=1e6)return (n/1e6).toFixed(1)+"M"; if(n>=1e3)return (n/1e3).toFixed(1)+"K"; return n.toFixed(0);}
function drawEquity(points){const c=document.getElementById("equityCanvas");if(!c)return;const x=c.getContext("2d"),w=c.width,h=c.height;x.clearRect(0,0,w,h);x.fillStyle="rgba(2,6,23,.25)";x.fillRect(0,0,w,h);x.strokeStyle="rgba(148,163,184,.18)";for(let i=0;i<5;i++){let y=20+i*(h-40)/4;x.beginPath();x.moveTo(20,y);x.lineTo(w-20,y);x.stroke()}if(!points||!points.length){x.fillStyle="#94a3b8";x.font="16px Arial";x.fillText("No closed trades yet",30,45);return}const vals=points.map(p=>Number(p.equity||0));let mn=Math.min(...vals),mx=Math.max(...vals);if(mn===mx){mn-=1;mx+=1}const step=(w-50)/Math.max(points.length-1,1);function yy(v){return h-25-((v-mn)/(mx-mn))*(h-55)}x.beginPath();vals.forEach((v,i)=>{let xx=25+i*step;if(i===0)x.moveTo(xx,yy(v));else x.lineTo(xx,yy(v))});x.strokeStyle=vals.at(-1)>=0?"#22c55e":"#ef4444";x.lineWidth=3;x.stroke();x.fillStyle="#e5e7eb";x.font="12px Arial";x.fillText("Equity: "+money(vals.at(-1)),28,22)}
function renderMovers(mode){moversMode=mode;document.querySelectorAll(".tab").forEach(t=>t.classList.remove("active"));[...document.querySelectorAll(".tab")].find(t=>t.textContent.toLowerCase().includes(mode==="gainers"?"gainers":mode==="losers"?"losers":"volume"))?.classList.add("active");const box=document.getElementById("topMoversCards");const rows=(moversData[mode]||[]).slice(0,8);box.innerHTML=rows.map(r=>{const pos=Number(r.price_change_percent)>=0;return `<div class="coinCard"><div class="coinTop"><div class="sym">${r.symbol}</div>${pill(pos?"UP":"DOWN",pos?"long":"short")}</div><div class="chg ${pos?"green":"red"}">${pos?"+":""}${Number(r.price_change_percent).toFixed(2)}%</div><div class="price">Last: ${fmt(r.last_price)}</div><div class="vol">24h Volume: ${compactVol(r.quote_volume)}</div></div>`}).join("")||'<div class="small">No movers loaded</div>'}
function renderListings(rows){const box=document.getElementById("newListingsCards");box.innerHTML=(rows||[]).slice(0,8).map(r=>`<div class="coinCard"><div class="coinTop"><div class="sym">${r.symbol}</div>${pill("NEW","info")}</div><div class="price">Base: ${r.base_asset}</div><div class="vol">Onboard: ${r.onboard_date || "n/a"}</div></div>`).join("")||'<div class="small">No listing data</div>'}
function renderNews(rows){const box=document.getElementById("newsCards");box.innerHTML=(rows||[]).slice(0,6).map(r=>`<div class="newsCard" style="margin-bottom:10px"><div class="newsTitle"><a href="${r.url}" target="_blank">${r.title||"Announcement"}</a></div><div class="newsDate">${r.release_date||""}</div></div>`).join("")||'<div class="small">News unavailable right now</div>'}
async function loadRoot(){const d=await getJson("/");serverStatus.textContent=d.status||"unknown";serverStatus.className="big green";serverLine.textContent=d.service||"";serviceBadge.textContent=d.service||"service"}
async function loadConfig(){const d=await getJson("/config");executionStatus.textContent=d.execution_enabled?"LIVE ON":"SAFE";executionStatus.className=d.execution_enabled?"big red":"big green";riskLine.textContent=`Default ${d.default_exchange} | Risk ${d.max_risk_pct_per_trade}% | AI ${d.ai_v3_enabled}`}
async function loadSafety(){const d=await getJson("/safety/status");safetyStatus.textContent=d.emergency_stop?"STOPPED":"OK";safetyStatus.className=d.emergency_stop?"big red":"big green";safetyLine.textContent=d.session_reason||"";return d}
async function loadManager(){const d=await getJson("/manager/status");managerStatus.textContent=d.running?"RUNNING":"OFF";managerStatus.className=d.running?"big green":"big yellow";managerLine.textContent=`Every ${d.sleep_seconds}s | Trail ${d.pa_trailing_enabled} | Last ${d.last_run||"never"}`;return d}
async function loadScanner(){const d=await getJson("/scanner/status");scannerStatus.textContent=d.running?"RUNNING":"OFF";scannerStatus.className=d.running?"big green":"big yellow";scannerLine.textContent=`${d.symbols?.length||0} symbols | ${d.exchange} | ${d.interval}`;const interested=[];(d.last_results||[]).forEach(r=>{const c=r.checks||{};const q=r.quality_score??r.elite_score??Math.max(c.long_quality||0,c.short_quality||0);if(r.signal||q>=1||["bullish","bearish"].includes(r.structure))interested.push({...r,q})});watchCount.textContent=interested.length;watchBody.innerHTML=interested.length?interested.slice(0,80).map(r=>{const ob=r.orderbook||{};const cls=r.signal==="long"?"long":r.signal==="short"?"short":"watch";return `<tr><td>${r.symbol||""}</td><td>${pill(r.signal||"watch",cls)}</td><td>${r.structure||""}</td><td>${r.q??""}</td><td>${ob.pressure||""} ${ob.ratio?"("+Number(ob.ratio).toFixed(2)+")":""}</td><td>${r.reason||r.execution?.reason||""}</td></tr>`}).join(""):'<tr><td colspan="6" class="small">No interesting setups right now</td></tr>'}
async function loadPositions(){const d=await getJson("/positions/live");const rows=d.rows||[];openCount.textContent=rows.length;const unreal=rows.reduce((a,p)=>a+Number(p.unrealized_pnl||0),0);unrealizedPnl.textContent=money(unreal);unrealizedPnl.className=unreal>=0?"big green":"big red";positionsBody.innerHTML=rows.length?rows.map(p=>`<tr><td>${pill(p.status,"open")}</td><td>${p.ticker}</td><td>${pill(p.signal,p.signal)}</td><td>${fmt(p.entry_price)}</td><td>${fmt(p.mark_price)}</td><td>${fmt(p.stop_price)}</td><td class="${(p.unrealized_pnl||0)>=0?"green":"red"}">${money(p.unrealized_pnl||0)}</td><td>${fmt(p.current_r)}</td><td>${fmt(p.qty)}</td><td><button class="danger" onclick="closePosition('${p.id}')">Close</button></td></tr>`).join(""):'<tr><td colspan="10" class="small">No open positions</td></tr>'}
async function loadStats(){const d=await getJson("/dashboard/stats");const s=d.summary||{};realizedPnl.textContent=money(s.realized_pnl||0);realizedPnl.className=(s.realized_pnl||0)>=0?"big green":"big red";winRate.textContent=Number(s.win_rate||0).toFixed(1)+"%";winBar.style.width=Math.min(100,Math.max(0,s.win_rate||0))+"%";wlLine.textContent=`W ${s.wins||0} | L ${s.losses||0} | BE ${s.breakeven||0}`;const rows=d.pnl_rows||[];pnlBody.innerHTML=rows.length?rows.slice(0,80).map(r=>`<tr><td>${r.ticker}</td><td>${pill(r.signal,r.signal)}</td><td>${fmt(r.entry_price)}</td><td>${fmt(r.close_price)}</td><td>${fmt(r.qty)}</td><td class="${(r.pnl||0)>=0?"green":"red"}">${money(r.pnl)}</td><td>${r.created_at||""}</td></tr>`).join(""):'<tr><td colspan="7" class="small">No closed trades with PnL yet</td></tr>'}
async function loadAnalytics(){try{const d=await getJson("/analytics/summary");drawEquity(d.equity_curve||[])}catch(e){drawEquity([])}}
async function loadMarketIntel(){const [m,l,n]=await Promise.allSettled([getJson("/market/top-movers?limit=8"),getJson("/market/new-listings?limit=8"),getJson("/market/news?limit=6")]);moversData=m.value||{};renderMovers(moversMode);renderListings((l.value||{}).rows||[]);renderNews((n.value||{}).rows||[])}
async function loadAiV3(){try{const d=await getJson("/ai-v3/status");aiV3Box.textContent=JSON.stringify(d,null,2)}catch(e){aiV3Box.textContent="AI V3 unavailable: "+e.message}}
async function loadSystem(){const [ws,safety,manager,adaptive,pro]=await Promise.allSettled([getJson("/futures/ws/status"),getJson("/safety/status"),getJson("/manager/status"),getJson("/adaptive/status"),getJson("/pro-engine/status")]);systemBox.textContent=JSON.stringify({websocket:ws.value||ws.reason?.message,safety:safety.value||safety.reason?.message,manager:manager.value||manager.reason?.message,adaptive:adaptive.value||adaptive.reason?.message},null,2);proBox.textContent=JSON.stringify(pro.value||pro.reason?.message,null,2);const healthy=(safety.value&&!safety.value.emergency_stop)&&(manager.value&&manager.value.running);healthDot.className=healthy?"dot green":"dot red";healthText.textContent=healthy?"System healthy: manager online and safety OK":"Check safety/manager status"}
async function action(url,method){try{const d=await getJson(url,{method});controlResult.textContent=JSON.stringify(d).slice(0,650);setTimeout(refreshAll,700)}catch(e){controlResult.textContent=e.message}}
async function startScanner(){await action("/scanner/start","POST")} async function stopScanner(){await action("/scanner/stop","POST")} async function scanOnce(){await action("/scanner/scan-once","POST")} async function refreshDiscovery(){await action("/scanner/discover-refresh","POST")} async function startManager(){await action("/manager/start","POST")} async function stopManager(){await action("/manager/stop","POST")} async function startWs(){await action("/futures/ws/start","POST")} async function stopWs(){await action("/futures/ws/stop","POST")} async function syncFutures(){await action("/futures/sync-open","POST")} async function resetPro(){await action("/pro-engine/reset","POST")} async function emergencyStop(){if(confirm("Emergency stop scanner/trading?"))await action("/safety/emergency-stop?reason=dashboard","POST")} async function resumeSafety(){await action("/safety/resume","POST")} async function closePosition(id){if(confirm("Close this position?"))await action("/positions/close/"+id,"POST")}
async function refreshAll(){await Promise.allSettled([loadRoot(),loadConfig(),loadSafety(),loadManager(),loadScanner(),loadPositions(),loadStats(),loadAnalytics(),loadMarketIntel(),loadAiV3(),loadSystem()]);clockBadge.textContent=new Date().toLocaleTimeString()}
refreshAll();setInterval(refreshAll,5000);
</script>
</body>
</html>
"""



@app.get("/")
def root():
    return {"status": "ok", "service": "binance-spot-futures-bot-pro-final-3d-dashboard-v1", "time": now_iso()}


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
