# Binance Bot PRO Auto Discovery

Adds auto symbol discovery:
- pulls all Binance USDT perpetual futures symbols
- filters by TRADING + PERPETUAL + USDT
- ranks by 24h quote volume
- scans top N symbols automatically
- includes manual watchlist too if enabled
- refreshes symbols every AUTO_DISCOVER_REFRESH_SECONDS
- dashboard button: Refresh Symbols

Recommended safe variables:
SCANNER_EXCHANGE=paper
AUTO_DISCOVER_SYMBOLS=true
AUTO_DISCOVER_TOP_N=20
AUTO_DISCOVER_MIN_QUOTE_VOLUME=50000000
ENABLE_EXECUTION=false
