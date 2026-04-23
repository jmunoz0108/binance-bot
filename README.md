# Binance Spot + Futures Bot

This package supports:
- paper mode
- Binance Spot market orders
- Binance USDⓈ-M Futures market orders
- TradingView webhook intake
- SQLite journal
- Railway deployment
- Futures websocket listener skeleton

## Files
- `app.py` main FastAPI server
- `requirements.txt`
- `railway.json`
- `.env.example`
- `.gitignore`

## Local run
```bash
pip install -r requirements.txt
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

## Railway
- Deploy from GitHub
- Add a volume mounted at `/app/data`
- Set env vars from `.env.example`

## TradingView
Use:
- `/pine-alert-templates`

Webhook URL:
`https://YOUR_APP_DOMAIN/webhook/tradingview`

## Notes
This package is a stable base. It does not yet include:
- advanced exit manager
- TP/SL order placement on Binance
- fill-aware partial management
- live AI filtering
