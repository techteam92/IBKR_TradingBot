# Complete PB (Pullback) Trigger Conditions

## Overview
This document lists ALL trigger conditions for PBe1, PBe2, and PBe1e2 strategies.

---

## PBe1 (Pullback Entry 1) Trigger Conditions

### 1. Time Check
- **Regular Hours** (`outsideRth=False`):
  - Must wait until `Config.pull_back_PBe1_time` (09:31:01)
  - If current time < 09:31:01 → Sleep 1 second, retry
- **Premarket/After-Hours** (`outsideRth=True`):
  - Time check is **SKIPPED** → Proceeds immediately

### 2. Historical Data Check
- Calls: `connection.pbe1_entry_historical_data(ibContract, timeFrame, chartTime)`
- **CRITICAL ISSUE**: This function filters data to only include bars:
  - On current date AND
  - After `Config.tradingTime` (09:30:00)
- **Result**: In premarket, returns empty `{}` because no bars meet criteria
- **Condition**: `if(len(complete_bar_data) == 0)` → Sleep 1 second, retry

### 3. Last Candle Validation
- Gets last candle: `last_candel = complete_bar_data[len(complete_bar_data)-1]`
- **Condition**: `if (last_candel == None or len(last_candel) == 0)` → Sleep 1 second, retry
- Extracts: `lastPrice = last_candel['close']`

### 4. Pattern Detection (pbe1_result)
- Function: `pbe1_result(last_candel, complete_bar_data)`
- **Logic**:
  - Finds lowest bar and highest bar in `complete_bar_data`
  - **BUY Condition**: `current_bar['high'] > lowest_bar['high']`
    - Meaning: Current bar's high breaks above the lowest bar's high
  - **SELL Condition**: `current_bar['low'] < highest_bar['low']`
    - Meaning: Current bar's low breaks below the highest bar's low
- **Condition**: `if ((tradeType != 'BUY') and (tradeType != 'SELL'))` → Sleep 1 second, retry

### 5. User Direction Match
- **Condition**: `if buySellType != tradeType` → Sleep 1 second, retry
- Must match user's selected direction (BUY/SELL)

### 6. Quantity Calculation
- Entry price calculation:
  - **BUY**: `entry_price = last_candel['high']`
  - **SELL**: `entry_price = last_candel['low']`
- Stop loss calculation: `_calculate_pbe_stop_loss(...)`
- Quantity: `quantity = risk_amount / stop_size`
- **Validation**:
  - If `risk_amount <= 0` → Use quantity = 1
  - If `stop_size == 0 or stop_size < 0.01` → Use quantity = 1
  - If `quantity <= 0` → Use quantity = 1

### 7. Order Placement
- Calls: `sendEntryTrade(...)` which places **STOP LIMIT** order
- Entry order type: **STP LMT**
  - Stop price = `entry_price`
  - Limit price = `entry_price ± 0.5 × stop_size`

---

## PBe2 (Pullback Entry 2) Trigger Conditions

### 1. Time Check
- **Regular Hours** (`outsideRth=False`):
  - Must wait until `Config.pull_back_PBe2_time` (09:32:02)
  - If current time < 09:32:02 → Sleep 1 second, retry
- **Premarket/After-Hours** (`outsideRth=True`):
  - Time check is **SKIPPED** → Proceeds immediately

### 2. Historical Data Check
- Calls: `connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)`
- **Condition**: `if(len(recentBarData) == 0)` → Sleep 1 second, retry
- **Minimum bars required**: At least 2 bars
- **Condition**: `if((len(recentBarData)) < 2)` → Sleep 1 second, retry

### 3. PBe1 Saved State Check
- Checks: `Config.pbe1_saved.get(key)`

#### 3a. If PBe1 NOT Saved (First Condition Check)
- Calls: `pbe_result(buySellType, lastPrice, recentBarData)` (normal mode)
- **BUY Pattern**:
  - Finds lowest bar in historical data
  - **Condition**: `current_candel['high'] > lowest_row['high']`
- **SELL Pattern**:
  - Finds highest bar in historical data
  - **Condition**: `current_candel['low'] < highest_row['low']`
- **If pattern found**:
  - Saves to `Config.pbe1_saved[key] = row`
  - Sleeps and continues (waits for PBe1 to stop out)
- **If pattern NOT found** (`tradeType == ""`):
  - Sleeps and retries

#### 3b. If PBe1 IS Saved (Second Condition Check)
- Calls: `pbe_result(buySellType, lastPrice, recentBarData, True)` (reverse mode)
- **Reverse Mode Logic**:
  - Scans bars from end to beginning
  - Finds lowest/highest points
  - **BUY**: Returns "BUY" with lowest_row
  - **SELL**: Returns "SELL" with highest_row
- **Date Check**:
  - **Condition**: `if (row['date'] == Config.pbe1_saved.get(key)['date'])` → Sleep, retry
  - Must be a **different bar** than the one that triggered PBe1
- **If date is different**:
  - Pattern found → Proceeds to order placement

### 4. Trade Type Validation
- **Condition**: `if ((tradeType != 'BUY') and (tradeType != 'SELL'))` → Sleep 1 second, retry

### 5. Quantity Calculation
- Entry price calculation:
  - **BUY**: `aux_price = histData['high']`
  - **SELL**: `aux_price = histData['low']`
- Stop loss calculation: `_calculate_pbe_stop_loss(...)`
- Quantity: `quantity = risk_amount / stop_size`
- Same validations as PBe1

### 6. Order Placement
- Places **STOP LIMIT** order directly (not via sendEntryTrade)
- Entry order type: **STP LMT**
  - Stop price = `aux_price`
  - Limit price = `aux_price ± 0.5 × stop_size`
- After placement: Calls `pbe2_loop_run(...)` for continuous stop price updates

---

## PBe1e2 (Combined PBe1 + PBe2) Trigger Conditions

### Initial Setup
- When `barType == Config.entryTradeType[7]` (PBe1e2):
  - Calls `pull_back_PBe1(...)` with `barType=PBe1e2`
  - PBe1 order is placed with `barType=PBe1e2` (not `PBe1`)

### PBe1 Trigger (Same as above)
- All PBe1 conditions apply
- **Difference**: `barType` is stored as `PBe1e2` in order data

### PBe2 Auto-Trigger (After PBe1 Stops Out)
- **Trigger Location**: `sendTpAndSl()` function
- **Condition 1**: Entry order status is "Filled"
- **Condition 2**: `entryData['ordType'] == "StopLoss"` (NOT TakeProfit)
- **Condition 3**: `parentData['barType'] == Config.entryTradeType[5]` (PBe1) **OR**
  `parentData['barType'] == Config.entryTradeType[7]` (PBe1e2)
- **Action**: Automatically calls `pull_back_PBe2(...)` with:
  - All same parameters from PBe1
  - `barType = Config.entryTradeType[6]` (PBe2)

### PBe2 Conditions (After Auto-Trigger)
- All PBe2 conditions apply
- **Note**: PBe2 will check `Config.pbe1_saved[key]` which should already be set from PBe1

---

## Summary of Key Differences

| Aspect | PBe1 | PBe2 | PBe1e2 |
|--------|------|------|--------|
| **Time Check** | 09:31:01 (or skipped in premarket) | 09:32:02 (or skipped in premarket) | Same as PBe1 |
| **Data Source** | `pbe1_entry_historical_data()` | `getHistoricalChartDataForEntry()` | Same as PBe1 |
| **Pattern Check** | `pbe1_result()` - finds lowest/highest in all bars | `pbe_result()` - two-stage (first condition, then reverse) | Same as PBe1 |
| **PBe1 Dependency** | None | Requires PBe1 condition to be saved first | Runs PBe1 first |
| **Auto-Trigger** | No | Yes (after PBe1 stops out) | Yes (automatic) |
| **Entry Price** | `bar_high` (BUY) or `bar_low` (SELL) | `bar_high` (BUY) or `bar_low` (SELL) | Same as PBe1 |
| **Order Type** | STOP LIMIT | STOP LIMIT | Same as PBe1 |

---

## Known Issues

### Issue 1: PBe1 Historical Data Filter
- **Problem**: `pbe1_entry_historical_data()` filters out premarket bars
- **Impact**: PBe1 cannot trigger in premarket/after-hours
- **Location**: `IBConnection.py` line 537
- **Fix Needed**: Modify filter to include premarket bars when `outsideRth=True`

### Issue 2: PBe2 Requires PBe1 State
- **Problem**: PBe2 standalone may not work correctly if PBe1 state is not set
- **Impact**: PBe2 needs PBe1 condition to be calculated first (even if no order placed)
- **Note**: This is by design for PBe2, but may cause issues if PBe2 is selected alone

---

## Pattern Logic Details

### PBe1 Pattern (pbe1_result)
```
1. Scan all bars in complete_bar_data
2. Find lowest bar (lowest 'low' value)
3. Find highest bar (highest 'high' value)
4. BUY: if current_bar['high'] > lowest_bar['high'] → Return "BUY"
5. SELL: if current_bar['low'] < highest_bar['low'] → Return "SELL"
```

### PBe2 Pattern - First Condition (pbe_result, normal mode)
```
1. Scan all bars from start to end
2. Find lowest bar (lowest 'low' value)
3. Find highest bar (highest 'high' value)
4. BUY: if current_bar['high'] > lowest_bar['high'] → Return "BUY", save to pbe1_saved
5. SELL: if current_bar['low'] < highest_bar['low'] → Return "SELL", save to pbe1_saved
```

### PBe2 Pattern - Second Condition (pbe_result, reverse mode)
```
1. Scan all bars from END to START (reverse)
2. Find lowest point (lowest 'low' value)
3. Find highest point (highest 'high' value)
4. BUY: Return "BUY" with lowest_row
5. SELL: Return "SELL" with highest_row
6. Check: row['date'] != pbe1_saved[key]['date'] (must be different bar)
```

---

## Entry Order Specifications

### PBe1 Entry Order
- **Type**: STOP LIMIT (STP LMT)
- **Stop Price**: `entry_price` (bar high for BUY, bar low for SELL)
- **Limit Price**: `entry_price ± 0.5 × stop_size`
- **Quantity**: `risk_amount / stop_size`

### PBe2 Entry Order
- **Type**: STOP LIMIT (STP LMT)
- **Stop Price**: `aux_price` (bar high for BUY, bar low for SELL)
- **Limit Price**: `aux_price ± 0.5 × stop_size`
- **Quantity**: `risk_amount / stop_size`
- **Special**: After placement, `pbe2_loop_run()` continuously updates stop price

---

## Stop Loss Types for PB Strategies

All PB strategies support these stop loss types:
1. **EntryBar**: `stop = bar_low` (BUY) or `bar_high` (SELL), `stop_size = (high - low) + 0.02`
2. **HOD**: `stop = highest high`, `stop_size = abs(entry - HOD)`
3. **LOD**: `stop = lowest low`, `stop_size = abs(entry - LOD)`
4. **Custom**: `stop = custom_value`, `stop_size = abs(entry - custom_value)`

