# Binance Bot PRO Monster V1

Adds final reliability + quality upgrades:
- scanner auto-start on Railway boot
- watchdog restarts scanner if it stops
- emergency stop / resume endpoints
- safety status endpoint
- optional UTC session filter
- optional multi-timeframe confirmation
- auto-risk multiplier based on setup quality
- positions history endpoint
- dashboard emergency controls

Important safe defaults:
- SCANNER_EXCHANGE=paper
- ENABLE_EXECUTION=false
- SESSION_FILTER_ENABLED=false
- MTF_CONFIRM_ENABLED=false

After deploy:
- / should show binance-spot-futures-bot-pro-final-monster-v1
- /dashboard for control panel
- /scanner/status for scanner
- /safety/status for safety
