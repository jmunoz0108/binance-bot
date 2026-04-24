# Binance Bot PRO Auto Scanner

Adds auto scanner:
- scans configured Binance Futures symbols
- detects structure, BOS, sweeps, volume, candle confirmation
- checks futures orderbook pressure
- can run in paper or live futures mode
- dashboard controls: start/stop scanner, scan once

Endpoints:
- POST /scanner/start
- POST /scanner/stop
- POST /scanner/scan-once
- GET /scanner/status
- GET /dashboard

Start safely with:
SCANNER_EXCHANGE=paper
ENABLE_EXECUTION=false
