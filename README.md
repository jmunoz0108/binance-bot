# Binance Bot AI V3

Adds confidence scoring and controlled auto tuning:
- confidence score 0-100 per trade
- blocks low-confidence trades
- adjusts risk multiplier in bad conditions
- defensive confidence threshold after bad performance
- AI V3 dashboard panel

New endpoints:
- GET /ai-v3/status
- GET /ai-v3/confidence/{symbol}/{signal}
- POST /ai-v3/reset

Service:
binance-spot-futures-bot-pro-final-ai-v3
