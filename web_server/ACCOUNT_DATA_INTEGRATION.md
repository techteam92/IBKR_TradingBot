# Account Data Integration

## Overview

The web server now retrieves **real account data** from your trading bot instead of using mock data.

## Available Data

The following account information is now retrieved from Interactive Brokers:

### ✅ Total Balance
- Retrieved from IB account values (`NetLiquidation`)
- Shows your total account value

### ✅ Daily Profit
- Retrieved from `Config.currentPnl` (updated by bot's PnL tracking)
- Shows today's profit/loss

### ✅ Open Positions
- Retrieved from IB positions API
- Includes:
  - Symbol
  - Quantity
  - Average price
  - Current price
  - Market value
  - Unrealized P&L
  - Unrealized P&L percentage

### ✅ Buying Power
- Retrieved from IB account values (`BuyingPower`)
- Shows available buying power

### ✅ Cash Balance
- Retrieved from IB account values (`CashBalance` or `TotalCashValue`)
- Shows available cash

## API Endpoints

### Get Account Summary
```
GET /api/account/summary
Authorization: Bearer <token>
```

**Response:**
```json
{
  "totalBalance": 125000.50,
  "netLiquidation": 125000.50,
  "buyingPower": 50000.00,
  "cashBalance": 75000.00,
  "openPositionsCount": 3,
  "dailyProfit": 1250.25,
  "dailyProfitPercent": 1.01,
  "currency": "USD",
  "lastUpdate": "2024-01-15T10:30:00",
  "connected": true
}
```

### Get Open Positions
```
GET /api/account/positions
Authorization: Bearer <token>
```

**Response:**
```json
{
  "positions": [
    {
      "symbol": "AAPL",
      "quantity": 100,
      "avgPrice": 150.25,
      "currentPrice": 175.50,
      "marketValue": 17550.00,
      "unrealizedPnl": 2525.00,
      "unrealizedPnlPercent": 16.81,
      "entryTime": null
    }
  ],
  "connected": true
}
```

## Requirements

1. **TWS (Trader Workstation) must be running**
2. **Bot must be connected to TWS** (or the web server will create its own connection)
3. **IB account must be logged in** in TWS

## How It Works

1. The web server uses `bot_data_helper.py` to connect to Interactive Brokers
2. It retrieves account values using IB's API
3. It gets positions using `getAllOpenPosition()`
4. It reads daily PnL from `Config.currentPnl`

## Error Handling

If the bot is not connected or TWS is not running:
- Returns HTTP 503 (Service Unavailable)
- Response includes `"connected": false`
- Error message explains the issue

## Notes

- The web server creates its own IB connection for data retrieval
- This doesn't interfere with the main bot's connection
- Data is cached in balance history for offline viewing
- Daily profit is updated in real-time from the bot's PnL tracking

## Troubleshooting

**Error: "Unable to connect to trading bot"**
- Make sure TWS is running
- Check that TWS is logged in
- Verify the bot can connect to TWS (test with main bot first)

**No positions showing:**
- Check if you have open positions in TWS
- Verify the bot connection is working
- Check TWS logs for errors

**Daily profit is 0:**
- Make sure the bot's PnL tracking is enabled (`reqPnl()` called)
- Check `Config.currentPnl` is being updated
- Verify today's trading session has started
