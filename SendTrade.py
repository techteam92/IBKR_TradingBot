import asyncio
import datetime
import logging
import math
import random
import Config
from header import *
from StatusUpdate import *
import traceback
import talib
from FunctionCalls import *
from ib_insync import util
import pandas as pd
import numpy as np
import nest_asyncio
def getContract(symbol,currency):
    try:
        logging.info("creating contract for %s symbole is %s and currency is %s",Config.ibContract,symbol,currency)
        if (Config.ibContract == "Forex"):
            if currency == None:
                return Forex(symbol)
            else:
                return Forex(symbol+currency)
        else:
            if symbol == 'MSFT' or symbol == 'CSCO':
                return Stock(symbol, exchange='SMART', currency='USD',primaryExchange='NASDAQ')
            else:
                return Stock(symbol, exchange='SMART', currency='USD')
    except Exception as e:
        logging.error("error in contract making %s ",e)
        print(e)



def getSleepTime(timeFrame, outsideRth=False):
    conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
    configTime = datetime.datetime.strptime(conf_trading_time, "%H:%M:%S")
    configTime = datetime.datetime.combine(datetime.datetime.now().date(), configTime.time())
    # It will add timeframe in config trading time
    secOfTimeFrame = Config.timeDict.get(timeFrame)
    configTime = (configTime + datetime.timedelta(seconds=(secOfTimeFrame)))
    #  it will change date. changed date into current date
    if(datetime.datetime.now().time() < configTime.time()):
        return (configTime - datetime.datetime.now()).total_seconds()
    else:
        return 0



def getHistoricalBarData(barType,connection,ibContract,timeFrame,chartTime):
    if (barType == Config.entryTradeType[0]) or (barType == Config.entryTradeType[2]):
        return connection.getHistoricalChartData(ibContract, timeFrame, chartTime)
    else:
        chartTime = getRecentChartTime(timeFrame)
        logging.info("we will get chart data for %s time", chartTime)
        return connection.getHistoricalChartData(ibContract, timeFrame, chartTime)

def accordingRthTradingTimeCalculate(outsideRth=False):
    conf_trading_time = Config.tradingTime
    if outsideRth:
        conf_trading_time = Config.outsideRthTradingtime
    return conf_trading_time


def _is_extended_outside_rth(outsideRth: bool):
    """
    Returns (is_extended, session) where is_extended is True only when the user
    allows outsideRth trading and the detected session is PREMARKET or AFTERHOURS.
    """
    if not outsideRth:
        return False, None
    session = _get_current_session()
    if session in ('PREMARKET', 'AFTERHOURS'):
        return True, session
    return False, session


def _calculate_stop_limit_offsets(histData):
    """
    Calculate the stop size along with entry and protection limit offsets based on
    the latest client requirements.
    - stop_size: (high - low) + add002
    - entry_offset: 50% of stop size
    - protection_offset: 2x stop size
    """
    high = float(histData['high'])
    low = float(histData['low'])
    stop_size = (high - low) + Config.add002
    entry_offset = stop_size * 0.50
    protection_offset = stop_size * 2
    stop_size = round(stop_size, Config.roundVal)
    entry_offset = round(entry_offset, Config.roundVal)
    protection_offset = round(protection_offset, Config.roundVal)
    return stop_size, entry_offset, protection_offset

MANUAL_ORDER_TYPES = tuple(Config.manualOrderTypes)

def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _get_atr_value(connection, contract):
    try:
        candle_data = connection.getDailyCandle(contract)
        if candle_data is None or len(candle_data) == 0:
            logging.warning("ATR data not available for contract %s", contract)
            return None
        candle_df = util.df(candle_data)
        for column in ('high', 'low', 'close'):
            if column in candle_df:
                candle_df[column] = pd.to_numeric(candle_df[column], errors='coerce')
        candle_df = candle_df.dropna(subset=['high', 'low', 'close'])
        if len(candle_df) < Config.atrPeriod + 1:
            logging.warning("Not enough data to compute ATR for %s", contract)
            return None

        atr_series = talib.ATR(candle_df['high'], candle_df['low'], candle_df['close'], Config.atrPeriod)
        atr_value = None
        try:
            atr_clean = atr_series.dropna()
            if len(atr_clean) > 0:
                atr_value = float(atr_clean.iloc[-1])
        except AttributeError:
            atr_array = np.array(atr_series, dtype=float)
            atr_array = atr_array[~np.isnan(atr_array)]
            if atr_array.size > 0:
                atr_value = float(atr_array[-1])

        if atr_value is None:
            logging.info("TA-Lib ATR returned no values for %s; using fallback calculation", contract)
            tr1 = candle_df['high'] - candle_df['low']
            tr2 = (candle_df['high'] - candle_df['close'].shift(1)).abs()
            tr3 = (candle_df['low'] - candle_df['close'].shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_fallback = tr.ewm(alpha=1 / Config.atrPeriod, adjust=False).mean().dropna()
            if len(atr_fallback) == 0:
                logging.warning("Fallback ATR calculation failed for %s", contract)
                return None
            atr_value = float(atr_fallback.iloc[-1])

        logging.info("ATR value for %s is %s", contract, atr_value)
        return atr_value
    except Exception as e:
        logging.error("error while calculating ATR for %s: %s", contract, e)
        return None

def _get_atr_stop_offset(connection, contract, stoploss_type):
    percent = Config.atrStopLossMap.get(stoploss_type)
    if percent is None:
        return None
    atr_value = _get_atr_value(connection, contract)
    if atr_value is None:
        return None
    offset = atr_value * percent
    offset = round(offset, Config.roundVal)
    logging.info("ATR stop offset for %s is %s", contract, offset)
    return offset

def _parse_entry_price(entry_points):
    price = _to_float(entry_points, None)
    if price is None or price == 0:
        raise ValueError("Entry price is required for manual orders.")
    return round(price, Config.roundVal)

def _calculate_manual_stop_loss(connection, contract, entry_price, stop_loss_type, buy_sell_type, time_frame, sl_value):
    """
    Calculate stop loss price for manual orders (Limit Order and Stop Order).
    
    Args:
        connection: IB connection
        contract: Contract object
        entry_price: Entry/trigger price
        stop_loss_type: Stop loss type from Config.stopLoss
        buy_sell_type: 'BUY' or 'SELL'
        time_frame: Time frame for historical data
        sl_value: Custom stop loss value (for Custom type)
    
    Returns:
        stop_loss_price: Calculated stop loss price
    """
    chart_time = getRecentChartTime(time_frame)
    
    # ATR-based stop loss
    if stop_loss_type in Config.atrStopLossMap:
        atr_percent = Config.atrStopLossMap[stop_loss_type]
        atr_value = _get_atr_value(connection, contract)
        if atr_value is None:
            logging.warning("ATR not available, falling back to EntryBar for %s", contract)
            hist_data = _get_latest_hist_bar(connection, contract, time_frame)
            if hist_data is None:
                raise ValueError("Cannot calculate stop loss: No historical data available")
            if buy_sell_type == 'BUY':
                stop_loss_price = float(hist_data['high'])
            else:
                stop_loss_price = float(hist_data['low'])
            # Calculate stop_size from entry and stop loss price
            stop_size = abs(entry_price - stop_loss_price)
        else:
            stop_size = atr_value * atr_percent
            if buy_sell_type == 'BUY':
                stop_loss_price = entry_price - stop_size
            else:  # SELL
                stop_loss_price = entry_price + stop_size
        logging.info("ATR stop loss for %s: entry=%s, ATR=%s, percent=%s, stop_size=%s, stop_loss=%s",
                     contract, entry_price, atr_value, atr_percent, stop_size if atr_value else 'N/A', stop_loss_price)
    
    # Custom stop loss
    elif stop_loss_type == Config.stopLoss[1]:  # 'Custom'
        custom_value = _to_float(sl_value, 0)
        if custom_value == 0:
            raise ValueError("Custom stop loss requires a valid value")
        if buy_sell_type == 'BUY':
            stop_loss_price = custom_value
            if stop_loss_price >= entry_price:
                raise ValueError(f"Custom stop loss ({stop_loss_price}) must be below entry price ({entry_price}) for BUY orders")
        else:  # SELL
            stop_loss_price = custom_value
            if stop_loss_price <= entry_price:
                raise ValueError(f"Custom stop loss ({stop_loss_price}) must be above entry price ({entry_price}) for SELL orders")
        # Calculate stop_size from entry and stop loss price
        stop_size = abs(entry_price - stop_loss_price)
        logging.info("Custom stop loss for %s: entry=%s, stop_loss=%s, stop_size=%s", contract, entry_price, stop_loss_price, stop_size)
    
    # RB (Recent Bar)
    elif stop_loss_type == Config.stopLoss[1]:  # 'BarByBar' (RB)
        hist_data = _get_latest_hist_bar(connection, contract, time_frame)
        if hist_data is None:
            raise ValueError("Cannot calculate RB stop loss: No historical data available")
        if buy_sell_type == 'BUY':
            recent_bar_low = float(hist_data['low'])
            stop_loss_price = recent_bar_low - 0.1
        else:  # SELL
            recent_bar_high = float(hist_data['high'])
            stop_loss_price = recent_bar_high + 0.1
        # Calculate stop_size from entry and stop loss price
        stop_size = abs(entry_price - stop_loss_price)
        logging.info("RB stop loss for %s: entry=%s, recent_bar=%s, stop_loss=%s, stop_size=%s",
                     contract, entry_price, hist_data, stop_loss_price, stop_size)
    
    # EntryBar, HOD, LOD - use existing logic
    else:
        hist_data = _get_latest_hist_bar(connection, contract, time_frame)
        if hist_data is None:
            raise ValueError("Cannot calculate stop loss: No historical data available")
        
        if buy_sell_type == 'BUY':
            if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                stop_loss_price = float(hist_data['high'])
            elif stop_loss_type == Config.stopLoss[2]:  # HOD
                # Uses premarket data for premarket, RTH data for after hours
                lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, contract, time_frame)
                if lod is not None and hod is not None:
                    stop_loss_price = float(hod)
                else:
                    stop_loss_price = float(hist_data['high'])
            elif stop_loss_type == Config.stopLoss[3]:  # LOD
                # Uses premarket data for premarket, RTH data for after hours
                lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, contract, time_frame)
                if lod is not None and hod is not None:
                    stop_loss_price = float(lod)
                else:
                    stop_loss_price = float(hist_data['high'])
            else:
                stop_loss_price = float(hist_data['high'])
        else:  # SELL
            if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                stop_loss_price = float(hist_data['low'])
            elif stop_loss_type == Config.stopLoss[2]:  # HOD
                # Uses premarket data for premarket, RTH data for after hours
                lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, contract, time_frame)
                if lod is not None and hod is not None:
                    stop_loss_price = float(hod)
                else:
                    stop_loss_price = float(hist_data['low'])
            elif stop_loss_type == Config.stopLoss[3]:  # LOD
                # Uses premarket data for premarket, RTH data for after hours
                lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, contract, time_frame)
                if lod is not None and hod is not None:
                    stop_loss_price = float(lod)
                else:
                    stop_loss_price = float(hist_data['low'])
            else:
                stop_loss_price = float(hist_data['low'])
        # Calculate stop_size from entry and stop loss price
        stop_size = abs(entry_price - stop_loss_price)
        logging.info("Stop loss for %s: entry=%s, type=%s, stop_loss=%s, stop_size=%s",
                     contract, entry_price, stop_loss_type, stop_loss_price, stop_size)
    
    stop_loss_price = round(stop_loss_price, Config.roundVal)
    stop_size = round(stop_size, Config.roundVal)
    return stop_loss_price, stop_size

def _calculate_stop_size(connection, contract, entry_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue):
    """
    Calculate stop size for quantity calculation.
    
    Args:
        connection: IB connection
        contract: Contract object
        entry_price: Entry price
        stopLoss: Stop loss type from Config.stopLoss
        buySellType: 'BUY' or 'SELL'
        histData: Historical bar data
        timeFrame: Time frame
        chartTime: Chart time
        slValue: Custom stop loss value (for Custom type)
    
    Returns:
        stop_size: Stop size in dollars
    """
    # Custom stop loss: stop_size = |entry - custom_stop|
    if stopLoss == Config.stopLoss[1]:  # 'Custom'
        custom_stop = _to_float(slValue, 0)
        if custom_stop == 0:
            logging.warning("Custom stop loss value missing, using bar range as fallback")
            stop_size = float(histData['high']) - float(histData['low'])
        else:
            stop_size = abs(entry_price - custom_stop)
        logging.info(f"Custom stop size: entry=%s, custom_stop=%s, stop_size=%s", entry_price, custom_stop, stop_size)
        return stop_size
    
    # ATR stop loss: stop_size = ATR * percentage
    if stopLoss in Config.atrStopLossMap:
        atr_offset = _get_atr_stop_offset(connection, contract, stopLoss)
        if atr_offset is None or atr_offset <= 0:
            logging.warning("ATR stop offset invalid, using bar range as fallback")
            stop_size = float(histData['high']) - float(histData['low'])
        else:
            stop_size = atr_offset
        logging.info(f"ATR stop size: atr_offset=%s, stop_size=%s", atr_offset, stop_size)
        return stop_size
    
    # EntryBar: stop_size = (bar_high - bar_low) + add002
    if stopLoss == Config.stopLoss[0]:  # 'EntryBar'
        stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
        stop_size = round(stop_size, Config.roundVal)
        logging.info(f"EntryBar stop size: bar_high=%s, bar_low=%s, stop_size=%s", 
                    histData['high'], histData['low'], stop_size)
        return stop_size
    
    # BarByBar: stop_size = (bar_high - bar_low) + add002
    if stopLoss == Config.stopLoss[2]:  # 'BarByBar'
        stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
        stop_size = round(stop_size, Config.roundVal)
        logging.info(f"BarByBar stop size: bar_high=%s, bar_low=%s, stop_size=%s", 
                    histData['high'], histData['low'], stop_size)
        return stop_size
    
    # HOD: stop_size = (bar_high - bar_low) + add002
    if stopLoss == Config.stopLoss[3]:  # 'HOD'
        stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
        stop_size = round(stop_size, Config.roundVal)
        logging.info(f"HOD stop size: bar_high=%s, bar_low=%s, stop_size=%s", 
                    histData['high'], histData['low'], stop_size)
        return stop_size
    
    # LOD: stop_size = (bar_high - bar_low) + add002
    if stopLoss == Config.stopLoss[4]:  # 'LOD'
        stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
        stop_size = round(stop_size, Config.roundVal)
        logging.info(f"LOD stop size: bar_high=%s, bar_low=%s, stop_size=%s", 
                    histData['high'], histData['low'], stop_size)
        return stop_size
    
    # Default: use bar range (high - low) + add002
    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
    stop_size = round(stop_size, Config.roundVal)
    logging.warning(f"Unknown stop loss type {stopLoss}, using bar range + add002 as fallback: stop_size=%s", stop_size)
    return stop_size

def _calculate_manual_quantity(entry_price, stop_loss_price, risk_amount):
    """
    Calculate quantity for manual orders based on risk and stop size.
    
    Formula: share size = risk size / stop size
    
    Args:
        entry_price: Entry/trigger price
        stop_loss_price: Stop loss price
        risk_amount: Risk amount in dollars (risk size)
    
    Returns:
        quantity: Number of shares to trade
    """
    stop_size = abs(entry_price - stop_loss_price)
    if stop_size == 0 or math.isnan(stop_size):
        logging.warning("Stop size invalid (%s); using default quantity of 1", stop_size)
        return 1
    
    # Calculate share size using formula: risk / stop_size
    quantity = risk_amount / stop_size
    quantity = int(round(quantity, 0))
    if quantity <= 0:
        quantity = 1
    
    logging.info("Quantity calculation: entry=%s, stop_loss=%s, stop_size=%s, risk=%s, quantity=%s",
                 entry_price, stop_loss_price, stop_size, risk_amount, quantity)
    return quantity


def _resolve_manual_quantity(risk):
    """Legacy function - kept for backward compatibility but should use _calculate_manual_quantity instead"""
    qty = _to_float(risk, 0)
    try:
        qty = int(qty)
    except Exception:
        qty = 0
    if qty <= 0:
        qty = 1
    return qty

def _normalize_bar(latest_bar):
    normalized_bar = {}
    if isinstance(latest_bar, dict):
        for key in ("open", "high", "low", "close", "volume"):
            if key in latest_bar:
                normalized_bar[key] = latest_bar[key]
            elif key.upper() in latest_bar:
                normalized_bar[key] = latest_bar[key.upper()]
    else:
        for key in ("open", "high", "low", "close", "volume"):
            if hasattr(latest_bar, key):
                normalized_bar[key] = getattr(latest_bar, key)
    return normalized_bar


def _extract_latest_bar(hist_dataset):
    if isinstance(hist_dataset, dict) and len(hist_dataset) > 0:
        last_key = sorted(hist_dataset.keys())[-1]
        return hist_dataset.get(last_key)
    if isinstance(hist_dataset, list) and len(hist_dataset) > 0:
        return hist_dataset[-1]
    return hist_dataset


def _get_latest_hist_bar(connection, contract, timeFrame):
    chartTime = getRecentChartTime(timeFrame)
    hist_dataset = connection.getHistoricalChartDataForEntry(contract, timeFrame, chartTime)
    latest_bar = _extract_latest_bar(hist_dataset)

    if not latest_bar:
        logging.info(
            "Primary historical source empty for %s %s, falling back to raw chart data",
            contract,
            timeFrame,
        )
        raw_hist = connection.getChartData(contract, timeFrame, chartTime)
        latest_bar = raw_hist[-1] if raw_hist else None

    if latest_bar is None:
        logging.warning("Historical data not available for %s %s", contract, timeFrame)
        return None

    normalized_bar = _normalize_bar(latest_bar)

    if normalized_bar:
        return normalized_bar

    logging.warning("Historical bar missing price fields for %s %s: %s", contract, timeFrame, latest_bar)
    return None

async def get_first_chart_time_lb(config_tradingTime, timeFrame ,outsideRth=False):
    # // trading time if time is perfect we can send trade then it will return none else it will give config trading time
    conf_trading_time = config_tradingTime
    tradingTime = checkTradingTimeForLb(conf_trading_time,timeFrame , outsideRth)
    if tradingTime != None:
        logging.info("trading is not started yet")
        logging.info("thread will sleep for %s , because trading time is %s ", (tradingTime - datetime.datetime.now()).total_seconds(), conf_trading_time)
        sec = (tradingTime - datetime.datetime.now()).total_seconds()
        chartTime = (tradingTime - datetime.timedelta(seconds=Config.timeDict.get((timeFrame))))
        await  asyncio.sleep(sec)
        return chartTime
    else:
        chartTime = datetime.datetime.strptime(conf_trading_time, "%H:%M:%S")
        return chartTime

async def get_first_chart_time(timeFrame ,outsideRth=False):
    # // trading time if time is perfect we can send trade then it will return none else it will give config trading time
    conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
    tradingTime = checkTradingTime(timeFrame , outsideRth)
    if tradingTime != None:
        logging.info("trading is not started yet")
        logging.info("thread will sleep for %s , because trading time is %s ", (tradingTime - datetime.datetime.now()).total_seconds(), conf_trading_time)
        sec = (tradingTime - datetime.datetime.now()).total_seconds()
        chartTime = (tradingTime - datetime.timedelta(seconds=Config.timeDict.get((timeFrame))))
        await  asyncio.sleep(sec)
        return chartTime
    else:
        chartTime = datetime.datetime.strptime(conf_trading_time, "%H:%M:%S")
        return chartTime


def atrCheck(histData,ibContract,connection,atrPercentage):
    mainAmount = ((float(histData['high']) - float(histData['low'])) + Config.add002)
    logging.info("Candle Data for atr %s for contract %s ",histData,ibContract)
    candleData = connection.getDailyCandle(ibContract)
    logging.info("Daily Candle Data for atr %s ", candleData)
    candleData = util.df(candleData)
    atr = talib.ATR(candleData['high'], candleData['low'], candleData['close'], 9)
    logging.info("before calculate atr value is %s",atr[len(atr) - 1])
    atrAm = (atr[len(atr) - 1] / 100) * float(atrPercentage)

    logging.info("attr value %s and main Amount %s ", atrAm, mainAmount)
    if (mainAmount > atrAm):
        logging.info("we cannot place trade attr value is smaller than main value attr value %s and main value %s", atrAm, mainAmount)
        return True
        # return False
    else:
        return False


async def first_bar_fb(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue,breakEven,outsideRth,entry_points):
    logging.info("first_bar_fb mkt trade is sending. %s ",symbol)
    ibContract = getContract(symbol, None)
    # priceObj = subscribePrice(ibContract, connection)
    key = symbol +str(datetime.datetime.now().date())
    logging.info("Key for this trade is- %s ", key)
    chartTime = await get_first_chart_time(timeFrame , outsideRth)
    while True:
        dtime = str(datetime.datetime.now().date()) + " "+Config.first_bar_fb_time
        # dtime = config_time + datetime.timedelta(minutes=2)
        if (datetime.datetime.now() < datetime.datetime.strptime(dtime, '%Y-%m-%d %H:%M:%S')):
            await asyncio.sleep(1)
            continue

        logging.info("send trade loop is running..")
        histData = None
        tradeType = ""
        if Config.historicalData.get(key) == None:
            histData = connection.fb_entry_historical_data(ibContract, timeFrame, chartTime)
            if (histData is None or len(histData) == 0):
                logging.info("Chart Data is Not Comming for %s contract  and for %s time", ibContract, chartTime)
                await asyncio.sleep(1)
                continue
            else:
                Config.historicalData.update({key: histData})
        histData = Config.historicalData.get(key)
        lastPrice = connection.get_recent_close_price_data(ibContract, timeFrame, chartTime)
        if (lastPrice == None or len(lastPrice) == 0 ):
            logging.info("Last Price Not Found for %s contract for mkt order", ibContract)
            await  asyncio.sleep(1)
            continue
        lastPrice = lastPrice['close']
        lastPrice = round(lastPrice, Config.roundVal)
        logging.info("Price found for market order %s for %s contract ", lastPrice, ibContract)
        # if (float(lastPrice) > float(histData['high'])):
        #     logging.info("Price Buy for market order last price  %s for High %s Candle %s ", float(lastPrice), float(histData['high']), histData)
        #     tradeType = "BUY"
        # elif (float(lastPrice) < float(histData['low'])):
        #     logging.info("Price Sell for market order last price  %s for High %s Candle %s ", float(lastPrice), float(histData['low']), histData)
        #     tradeType = "SELL"
        # else:
        #     logging.info("Trade Type not found retrying with in %s second for %s contract", 2, ibContract)
        #     await asyncio.sleep(1)
        #     continue
        tradeType = buySellType
        if tradeType.upper() == 'BUY':
            lastPrice = histData['high'] -float(entry_points)
        else:
            lastPrice = histData['low'] +float(entry_points)

        if buySellType != tradeType:
            logging.info("trade type not satisfy, User want %s trade, trade type is comming %s", buySellType, tradeType)
            logging.info("price is %s, high is %s, low is %s", lastPrice, histData['high'], histData['low'])
            await  asyncio.sleep(1)
            continue

        # ATR check functionality removed
        # if (atrCheck(histData, ibContract, connection, atrPercentage)):
        #     await  asyncio.sleep(1)
        #     continue
        logging.info("Trade action found for market order %s for %s contract ", tradeType, ibContract)
        # connection.cancelTickData(ibContract)

        if(quantity == ''):
            quantity = 0
        if int(quantity) == 0:
            # Calculate stop size first
            stop_size = _calculate_stop_size(connection, ibContract, lastPrice, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
            
            # Calculate quantity: qty = risk / stop_size
            risk_amount = _to_float(risk, 0)
            if risk_amount <= 0:
                logging.warning("Invalid risk amount for FB: %s, using default quantity of 1", risk)
                quantity = 1
            elif stop_size == 0 or stop_size < 0.01:
                logging.warning("Stop size is zero or too small (%s) for FB, using default quantity of 1", stop_size)
                quantity = 1
            else:
                quantity = risk_amount / stop_size
                quantity = int(round(quantity, 0))
                if quantity <= 0:
                    quantity = 1
                logging.info(f"FB quantity calculated: entry=%s, stop_size=%s, risk=%s, quantity=%s", 
                           lastPrice, stop_size, risk_amount, quantity)
        else:
            logging.info("user quantity %s",quantity)

        logging.info("Trade quantity found for market order %s for %s contract ", quantity, ibContract)
        logging.info("everything found we are placing mkt trade")
        conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
        logging.info("main Entry Data For FB, historical  data [%s]  price is [ %s ] tradeType is [%s], TimeFrame [%s], configTime [%s] , quantity [%s], ibContract [%s]",
                     Config.historicalData.get(key), lastPrice, tradeType, timeFrame, conf_trading_time, quantity, ibContract)
        sendEntryTrade(connection, ibContract, tradeType, quantity, histData, lastPrice, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage,slValue , breakEven , outsideRth)
        logging.info("fb entry trade send done %s ",symbol)
        break


async def rb_and_rbb(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue ,breakEven,outsideRth,entry_points):
    logging.info("rb_and_rbb mkt trade is sending. %s ",symbol)
    ibContract = getContract(symbol, None)
    # priceObj = subscribePrice(ibContract, connection)
    key = (symbol + str(datetime.datetime.now()))
    logging.info("Key for this trade is- %s ", key)
    chartTime = await get_first_chart_time(timeFrame, outsideRth)
    while True:
        # During overnight, skip the time check and proceed immediately
        if outsideRth:
            session = _get_current_session()
            if session == 'OVERNIGHT':
                logging.info("OVERNIGHT session: Skipping 02:31:01 time check, proceeding immediately")
            else:
                # For pre-market/after-hours, still check the time
                dtime = str(datetime.datetime.now().date()) + " 02:31:01"
                if (datetime.datetime.now() < datetime.datetime.strptime(dtime, '%Y-%m-%d %H:%M:%S')):
                    await asyncio.sleep(1)
                    continue
        else:
            # Regular hours: check the time
            dtime = str(datetime.datetime.now().date()) + " 02:31:01"
            if (datetime.datetime.now() < datetime.datetime.strptime(dtime, '%Y-%m-%d %H:%M:%S')):
                await asyncio.sleep(1)
                continue

        logging.info("RBRR send trade loop is running..")
        histData = None
        tradeType = ""
        # Always get chartTime - needed for stop size calculation and price data
        chartTime = getRecentChartTime(timeFrame)
        
        if Config.historicalData.get(key) == None:
            # chartTime = datetime.datetime.now()
            logging.info("recent chart time for rbb request  %s %s",chartTime,symbol)
            histData = connection.rbb_entry_historical_data(ibContract, timeFrame, chartTime)
            if (histData is None or len(histData) == 0):
                logging.info("RBB Chart Data is Not Comming for %s contract  and for %s time", ibContract, chartTime)
                # During overnight, try to get daily candle data as fallback
                if outsideRth:
                    session = _get_current_session()
                    if session == 'OVERNIGHT':
                        logging.info("OVERNIGHT: Trying daily candle data as fallback for historical data")
                        try:
                            dailyData = connection.getDailyCandle(ibContract)
                            if len(dailyData) > 0:
                                # Create a mock histData from daily candle
                                lastCandle = dailyData[-1]
                                histData = {
                                    'close': lastCandle.close,
                                    'open': lastCandle.open,
                                    'high': lastCandle.high,
                                    'low': lastCandle.low,
                                    'dateTime': lastCandle.date
                                }
                                Config.historicalData.update({key: histData})
                                logging.info("OVERNIGHT: Using daily candle data as historical data: %s", histData)
                            else:
                                logging.warning("OVERNIGHT: No daily candle data available, will retry")
                                await asyncio.sleep(1)
                                continue
                        except Exception as e:
                            logging.error("OVERNIGHT: Error getting daily candle data: %s", e)
                            await asyncio.sleep(1)
                            continue
                    else:
                        await asyncio.sleep(1)
                        continue
                else:
                    await asyncio.sleep(1)
                    continue
            else:
                Config.historicalData.update({key: histData})

        # if barType == Config.entryTradeType[2]:
        #     newChartTime = getRecentChartTime(timeFrame)
        #     newChartTime = newChartTime.replace(second=0,microsecond=0)
        #     old_chart_time = chartTime.replace(second=0, microsecond=0)
        #     logging.info("RBRR we are comparing time for new chart data, now datetime is %s and after adding time frame is %s", datetime.datetime.now(), newChartTime)
        #     if old_chart_time != newChartTime:
        #         logging.info("RBRR New Time frame found we are removing data from historicalDict for for %s key and %s contract", key, ibContract)
        #         Config.historicalData.pop(key)
        #         await  asyncio.sleep(1)
        #         continue

        histData = Config.historicalData.get(key)
        lastPrice = connection.get_recent_close_price_data(ibContract, timeFrame, chartTime)
        if (lastPrice == None or len(lastPrice) == 0 ):
            logging.info("RBRR Last Price Not Found for %s contract for mkt order", ibContract)
            # During overnight, try to get price from daily candle or tick data
            if outsideRth:
                session = _get_current_session()
                if session == 'OVERNIGHT':
                    logging.info("OVERNIGHT: Trying to get price from daily candle or tick data")
                    try:
                        # Try daily candle first
                        dailyData = connection.getDailyCandle(ibContract)
                        if len(dailyData) > 0:
                            lastPrice = {'close': dailyData[-1].close}
                            logging.info("OVERNIGHT: Got price from daily candle: %s", lastPrice['close'])
                        else:
                            # Try tick data
                            connection.subscribeTicker(ibContract)
                            priceObj = connection.getTickByTick(ibContract)
                            if priceObj != None:
                                lastPrice = {'close': priceObj.marketPrice()}
                                connection.cancelTickData(ibContract)
                                logging.info("OVERNIGHT: Got price from tick data: %s", lastPrice['close'])
                            else:
                                logging.warning("OVERNIGHT: Could not get price, will retry")
                                await asyncio.sleep(1)
                                continue
                    except Exception as e:
                        logging.error("OVERNIGHT: Error getting price: %s", e)
                        await asyncio.sleep(1)
                        continue
                else:
                    await asyncio.sleep(1)
                    continue
            else:
                await asyncio.sleep(1)
                continue
        lastPrice= lastPrice['close']

        lastPrice = round(lastPrice, Config.roundVal)
        logging.info("RBRR Price found for market order %s for %s contract ", lastPrice, ibContract)

        tradeType = buySellType
        # if (float(lastPrice) > float(histData['high'])):
        #     logging.info("RBRR Price Buy for market order last price  %s for High %s Candle %s ", float(lastPrice), float(histData['high']), histData)
        #     tradeType = "BUY"
        # elif (float(lastPrice) < float(histData['low'])):
        #     logging.info("RBRR Price Sell for market order last price  %s for High %s Candle %s ", float(lastPrice), float(histData['low']), histData)
        #     tradeType = "SELL"
        # else:
        #     logging.info("RBRR Trade Type not found retrying with in %s second for %s contract", 2, ibContract)
        #     await asyncio.sleep(1)
        #     continue

        if buySellType != tradeType:
            logging.info("RBRBB trade type not satisfy, User want %s trade, trade type is comming %s", buySellType, tradeType)
            logging.info("price is %s, high is %s, low is %s", lastPrice, histData['high'], histData['low'])
            await  asyncio.sleep(1)
            continue

        # ATR check functionality removed
        # During overnight, skip ATR check as it may be too restrictive with limited liquidity
        # if outsideRth:
        #     session = _get_current_session()
        #     if session == 'OVERNIGHT':
        #         logging.info("OVERNIGHT session: Skipping ATR check to allow trade execution")
        #     else:
        #         # Pre-market/After-hours: still check ATR
        #         logging.info("RB: Checking ATR for pre-market/after-hours (session=%s)", session)
        #         if (atrCheck(histData, ibContract, connection, atrPercentage)):
        #             logging.warning("RB: ATR check failed - bar range too large, retrying...")
        #             await  asyncio.sleep(1)
        #             continue
        #         logging.info("RB: ATR check passed for pre-market/after-hours")
        # else:
        #     # Regular hours: check ATR
        #     logging.info("RB RTH: Checking ATR before placing order")
        #     atr_check_result = atrCheck(histData, ibContract, connection, atrPercentage)
        #     if atr_check_result:
        #         logging.warning("RB RTH: ATR check failed - bar range too large, retrying... (histData=%s, atrPercentage=%s)", histData, atrPercentage)
        #         await  asyncio.sleep(1)
        #         continue
        #     logging.info("RB RTH: ATR check passed, proceeding to place order")
        logging.info("RBRR Trade action found for market order %s for %s contract ", tradeType, ibContract)
        # connection.cancelTickData(ibContract)

        if (quantity == ''):
            quantity = 0
        
        # Check if HOD or LOD stop loss - need special calculation
        is_lod_hod = (stopLoss == Config.stopLoss[3]) or (stopLoss == Config.stopLoss[4])  # HOD (index 3) or LOD (index 4)
        calculated_stop_size = None  # Store calculated stop_size for HOD/LOD to reuse in extended hours
        stop_size = None  # Initialize stop_size for use in bracket orders
        
        # For HOD/LOD: Get LOD/HOD values once and reuse them
        lod = None
        hod = None
        recent_bar_data = None
        if is_lod_hod:
            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, ibContract, timeFrame)

        # Calculate entry price (aux_price) first (needed for both quantity calculation and order placement)
        aux_price = 0
        is_extended, session = _is_extended_outside_rth(outsideRth)
        
        if is_lod_hod and lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
            # For HOD/LOD with RBB: Entry stop price uses EntryBar logic (bar_high/low ± entry_points ± 0.01)
            # LOD/HOD is ONLY used for stop loss price, NOT for entry stop price
            # For RBB+EntryBar: Entry stop price = bar_high - entry_points + 0.01 (BUY) or bar_low + entry_points - 0.01 (SELL)
            if buySellType == 'BUY':
                aux_price = float(histData['high']) - float(entry_points) + 0.01
                logging.info(f"RB HOD/LOD BUY: Setting entry stop price to bar_high - entry_points + 0.01 = {aux_price} (EntryBar logic, LOD={lod} is only for stop loss), entry_points={entry_points}")
            else:  # SELL
                aux_price = float(histData['low']) + float(entry_points) - 0.01
                logging.info(f"RB HOD/LOD SELL: Setting entry stop price to bar_low + entry_points - 0.01 = {aux_price} (EntryBar logic, HOD={hod} is only for stop loss), entry_points={entry_points}")
            
            aux_price = round(aux_price, Config.roundVal)
            logging.info(f"RB HOD/LOD entry stop price FINAL: buySellType={buySellType}, stopLoss={stopLoss}, LOD={lod}, HOD={hod}, aux_price={aux_price}, is_extended={is_extended}, entry_points={entry_points}")
        else:
            # Regular stop loss or HOD/LOD data unavailable: use current bar's high/low
            if is_lod_hod:
                logging.warning("RB LOD/HOD: No historical data, using current bar's high/low as fallback")
            if buySellType == 'BUY':
                aux_price = histData['high'] - float(entry_points)
            else:
                aux_price = histData['low'] + float(entry_points)
        
        if int(quantity) == 0:
            if is_lod_hod and lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
                # For HOD/LOD: Calculate stop_size from bar high/low and HOD/LOD
                # stop_size = |bar_high/low - HOD/LOD|
                # Auto-detect: BUY uses LOD, SELL uses HOD
                if buySellType == 'BUY':
                    # For BUY: use bar_high and LOD
                    bar_price = float(histData['high'])
                    stop_size = abs(bar_price - lod)
                else:  # SELL
                    # For SELL: use bar_low and HOD
                    bar_price = float(histData['low'])
                    stop_size = abs(bar_price - hod)
                    
                stop_size = round(stop_size, Config.roundVal)
                if stop_size <= 0:
                    # Fallback: use bar range
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    stop_size = round(stop_size, Config.roundVal)
                    logging.warning("RB LOD/HOD: Invalid stop_size, using bar range: %s", stop_size)
                
                calculated_stop_size = stop_size  # Store for reuse in extended hours
                logging.info(f"RB LOD/HOD stop_size: bar_price={bar_price} (high={histData['high']}, low={histData['low']}), LOD={lod}, HOD={hod}, stop_size={stop_size}")
            elif is_lod_hod:
                    # Fallback to regular calculation if no historical data
                    stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
                    calculated_stop_size = stop_size
                    logging.warning("RB LOD/HOD: No historical data, using fallback stop_size calculation")
            else:
                # Regular stop loss types: use existing calculation
                stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
            
            # Calculate quantity: qty = risk / stop_size
            risk_amount = _to_float(risk, 0)
            if risk_amount <= 0:
                logging.warning("Invalid risk amount for RB: %s, using default quantity of 1", risk)
                quantity = 1
            elif stop_size == 0 or stop_size < 0.01:
                logging.warning("Stop size is zero or too small (%s) for RB, using default quantity of 1", stop_size)
                quantity = 1
            else:
                quantity = risk_amount / stop_size
                quantity = int(round(quantity, 0))
                if quantity <= 0:
                    quantity = 1
                logging.info(f"RB quantity calculated: entry=%s, stop_size=%s, risk=%s, quantity=%s", 
                           aux_price, stop_size, risk_amount, quantity)
        else:
            logging.info("user quantity")
        
        # Ensure quantity is at least 1 share and is an integer (IB requires minimum 1 share)
        quantity = int(quantity)
        if quantity < 1:
            logging.warning(f"Quantity {quantity} is less than 1, setting to minimum of 1 share")
            quantity = 1
        
        logging.info("RBRR Trade quantity found for market order %s for %s contract ", quantity, ibContract)
        logging.info("RBRR everything found we are placing mkt trade")
        conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
        logging.info("RBRR main Entry Data For RB AND RBB , historical  data [%s]  price is [ %s ] tradeType is [%s], TimeFrame [%s], configTime [%s] , quantity [%s], ibContract [%s]",
                     Config.historicalData.get(key), lastPrice, tradeType, timeFrame, conf_trading_time, quantity, ibContract)
        
        # For HOD/LOD: Don't adjust aux_price (it's already bar_high/low ± 0.01)
        # For regular stop loss: adjust aux_price for order placement
        if not is_lod_hod:
            logging.info(f"rb aux limit price befor 0.01 plus minus aux {aux_price}")
            if (tradeType == 'BUY'):
                aux_price= aux_price + 0.01
            else:
                aux_price = aux_price - 0.01

        # Log bar high and low values for review
        bar_high = float(histData.get('high', 0))
        bar_low = float(histData.get('low', 0))
        logging.info(f"ENTRY ORDER - Bar values: Bar's high={bar_high}, Bar's low={bar_low}, range={bar_high - bar_low} for {symbol} {tradeType}")
        
        is_extended, session = _is_extended_outside_rth(outsideRth)
        order_type = "STP"
        limit_price = None
        
        # For extended hours: Use stop-limit order (for both HOD/LOD and regular stop loss)
        # For RTH: Use stop order (for both HOD/LOD and regular stop loss)
        if is_extended:
            # For Custom stop loss in extended hours: Calculate entry price from bar high/low
            # Entry price: Low - 0.01 for long (BUY), High + 0.01 for short (SELL)
            # Entry limit price: entry_price ± 0.5 * stop_size
            if stopLoss == Config.stopLoss[1]:  # 'Custom'
                custom_stop = _to_float(slValue, 0)
                if custom_stop == 0:
                    logging.error("Custom stop loss requires a valid value for %s in extended hours %s", stopLoss, symbol)
                    return
                
                # Calculate entry price from bar high/low
                bar_high = float(histData.get('high', 0))
                bar_low = float(histData.get('low', 0))
                
                if tradeType == 'BUY':
                    # For BUY (long): entry_price = bar_low - 0.01
                    aux_price = round(bar_low - 0.01, Config.roundVal)
                else:  # SELL
                    # For SELL (short): entry_price = bar_high + 0.01
                    aux_price = round(bar_high + 0.01, Config.roundVal)
                
                # Calculate stop_size: |entry_price - custom_stop| + 0.02
                stop_size = abs(aux_price - custom_stop) + 0.02
                stop_size = round(stop_size, Config.roundVal)
                
                if stop_size <= 0:
                    logging.error("Stop size invalid (%s) for custom stop loss %s in extended hours %s", stop_size, custom_stop, symbol)
                    return
                
                logging.info(f"RB/RBB Extended hours Custom: entry_price={aux_price} (from bar high/low), custom_stop={custom_stop}, stop_size={stop_size}")
                
                # Calculate entry limit price: entry_price ± 0.5 * stop_size
                entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                if tradeType == 'BUY':
                    limit_price = round(aux_price + entry_limit_offset, Config.roundVal)
                else:  # SELL
                    limit_price = round(aux_price - entry_limit_offset, Config.roundVal)
                
                order_type = "STP LMT"
                logging.info(f"RB/RBB Extended hours Custom {tradeType}: Stop={aux_price}, Limit={limit_price} (entry ± 0.5×stop_size={entry_limit_offset}), stop_size={stop_size}")
            # Calculate stop size for entry order limit price calculation
            # For HOD/LOD: stop_size = |bar_high/low - HOD/LOD|
            # If ATR stop loss: use ATR × percentage
            # Otherwise: use (bar_high - bar_low) + 0.02
            elif is_lod_hod and lod is not None and hod is not None:
                # For HOD/LOD entry orders: stop_size = |bar_high/low - HOD/LOD|
                # Auto-detect: BUY uses LOD, SELL uses HOD
                if buySellType == 'BUY':
                    # For BUY: use bar_high and LOD
                    bar_price = float(histData['high'])
                    stop_size = abs(bar_price - lod)
                else:  # SELL
                    # For SELL: use bar_low and HOD
                    bar_price = float(histData['low'])
                    stop_size = abs(bar_price - hod)
                
                stop_size = round(stop_size, Config.roundVal)
                if stop_size <= 0:
                    # Fallback: use bar range
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    stop_size = round(stop_size, Config.roundVal)
                    logging.warning(f"RB Extended hours HOD/LOD: Invalid stop_size, using bar range: {stop_size}")
                
                logging.info(f"RB Extended hours HOD/LOD: stop_size={stop_size} (bar_price={bar_price}, HOD={hod}, LOD={lod}) for entry limit price")
            elif stopLoss in Config.atrStopLossMap:
                stop_size = _get_atr_stop_offset(connection, ibContract, stopLoss)
                if stop_size is None or stop_size <= 0:
                    # Fallback to bar range if ATR unavailable
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    logging.warning(f"RB Extended hours: ATR unavailable, using bar range stop_size={stop_size}")
                else:
                    logging.info(f"RB Extended hours: Using ATR stop_size={stop_size}")
            
            stop_size = round(stop_size, Config.roundVal)
            
            # Limit price = stop_price ± 0.5 × stop_size
            entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
            
            # Ensure minimum offset of 0.01 to avoid limit = stop
            min_limit_offset = 0.01
            if entry_limit_offset < min_limit_offset:
                entry_limit_offset = min_limit_offset
                logging.warning(f"RB Extended hours: stop_size too small ({stop_size}), using minimum limit offset={min_limit_offset}")
            
            order_type = "STP LMT"
            if is_lod_hod and lod is not None and hod is not None:
                # For HOD/LOD in extended hours: Limit price uses HOD/LOD, not aux_price
                # Auto-detect: BUY uses LOD, SELL uses HOD
                if tradeType == 'BUY':
                    # For BUY: Limit = LOD + 0.5 × stop_size
                    limit_price = lod + entry_limit_offset
                    logging.info(f"RB Extended hours HOD/LOD BUY: Stop={aux_price} (bar_high - entry_points + 0.01, EntryBar logic), Limit={limit_price} (LOD + 0.5×stop_size={entry_limit_offset}), LOD={lod}, stop_size={stop_size}")
                else:  # SELL
                    # For SELL: Limit = HOD - 0.5 × stop_size
                    limit_price = hod - entry_limit_offset
                    logging.info(f"RB Extended hours HOD/LOD SELL: Stop={aux_price} (bar_low + entry_points - 0.01, EntryBar logic), Limit={limit_price} (HOD - 0.5×stop_size={entry_limit_offset}), HOD={hod}, stop_size={stop_size}")
            else:
                # Regular stop loss: Limit = Stop ± 0.5 × stop_size
                if tradeType == 'BUY':
                    # For BUY: Limit = Stop + 0.5 × stop_size
                    limit_price = aux_price + entry_limit_offset
                else:
                    # For SELL: Limit = Stop - 0.5 × stop_size
                    limit_price = aux_price - entry_limit_offset
                    logging.info(f"RB Extended hours {tradeType}: Stop={aux_price}, Limit={limit_price} (stop ± 0.5×stop_size={entry_limit_offset}), stop_size={stop_size}")
            
           
            limit_price = round(limit_price, Config.roundVal)
            
            response = connection.placeTrade(contract=ibContract,
                                           order=Order(orderType=order_type, action=tradeType, totalQuantity=quantity,
                                                       tif=tif, auxPrice=aux_price, lmtPrice=limit_price), outsideRth=outsideRth)
            
            # For RBB in extended hours: Start rbb_loop_run for continuous entry order updates
            if barType == Config.entryTradeType[5]:  # RBB
                StatusUpdate(response, 'Entry', ibContract, order_type, tradeType, quantity, histData, lastPrice, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue, breakEven,
                             outsideRth, False, entry_points)
                logging.info("RBB Extended hours: Entry order placed, starting rbb_loop_run for continuous entry order updates")
                
                # Start rbb_loop_run for continuous entry order updates
                logging.info("RBB Extended hours: Starting rbb_loop_run for continuous entry order updates")
                await rbb_loop_run(connection, key, response.order)
            else:
                # RB: Use bracket orders in extended hours
                StatusUpdate(response, 'Entry', ibContract, order_type, tradeType, quantity, histData, lastPrice, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue, breakEven,
                             outsideRth, False, entry_points)
        else:
            # Regular hours (RTH): For RBB, place only entry order (TP/SL sent after fill)
            # For RB, use bracket orders (Entry STP, TP LMT, SL STP)
            if barType == Config.entryTradeType[5]:  # RBB - place only entry order, TP/SL after fill
                logging.info(f"RBB RTH: Placing only entry order, TP/SL will be sent after fill")
                
                # Place only entry order (STP)
                response = connection.placeTrade(contract=ibContract,
                                               order=Order(orderType=order_type, action=tradeType, totalQuantity=quantity,
                                                           tif=tif, auxPrice=aux_price), outsideRth=outsideRth)
                StatusUpdate(response, 'Entry', ibContract, order_type, tradeType, quantity, histData, lastPrice, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue, breakEven,
                             outsideRth, False, entry_points)
                logging.info("RBB RTH: Entry order placed, TP/SL will be sent after fill via sendTpAndSl")
                
                # Start rbb_loop_run for continuous entry order updates
                logging.info("RBB RTH: Starting rbb_loop_run for continuous entry order updates")
                await rbb_loop_run(connection, key, response.order)
            else:
                # RB: Use bracket orders in RTH (Entry STP, TP LMT, SL STP) - same as RBB
                logging.info(f"RB RTH: Using bracket orders (Entry STP, TP LMT, SL STP)")
                
                # Calculate stop loss price (same logic as RBB)
                stop_loss_price = 0
                if is_lod_hod and lod is not None and hod is not None:
                    # For HOD/LOD: stop loss price is LOD (for BUY) or HOD (for SELL)
                    if tradeType == 'BUY':
                        stop_loss_price = lod
                    else:  # SELL
                        stop_loss_price = hod
                    stop_loss_price = round(stop_loss_price, Config.roundVal)
                    logging.info(f"RB RTH HOD/LOD: stop_loss_price={stop_loss_price} (LOD={lod}, HOD={hod})")
                else:
                    # Calculate stop loss price using existing logic
                    try:
                        stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
                        if tradeType == 'BUY':
                            stop_loss_price = aux_price - stop_size
                        else:  # SELL
                            stop_loss_price = aux_price + stop_size
                        stop_loss_price = round(stop_loss_price, Config.roundVal)
                        logging.info(f"RB RTH: Calculated stop_loss_price={stop_loss_price} from stop_size={stop_size}")
                    except Exception as e:
                        logging.error(f"RB RTH: Error calculating stop loss: {e}")
                        # Fallback: use bar range
                        if tradeType == 'BUY':
                            stop_loss_price = round(float(histData['low']) - 0.01, Config.roundVal)
                        else:
                            stop_loss_price = round(float(histData['high']) + 0.01, Config.roundVal)
                        logging.warning(f"RB RTH: Using fallback stop_loss_price={stop_loss_price}")
                
                # Calculate take profit price (same logic as RBB)
                if is_lod_hod and lod is not None and hod is not None:
                    # For HOD/LOD: stop_size = |bar_high/low - HOD/LOD|
                    if tradeType == 'BUY':
                        bar_price = float(histData['high'])
                        tp_stop_size = abs(bar_price - lod)
                    else:  # SELL
                        bar_price = float(histData['low'])
                        tp_stop_size = abs(bar_price - hod)
                    tp_stop_size = round(tp_stop_size, Config.roundVal)
                else:
                    # Use calculated stop_size from quantity calculation
                    if stop_size is not None and stop_size > 0:
                        tp_stop_size = stop_size
                        logging.info(f"RB RTH: Using existing stop_size={tp_stop_size} for TP calculation")
                    else:
                        # Recalculate stop_size
                        tp_stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
                        tp_stop_size = round(tp_stop_size, Config.roundVal)
                        logging.info(f"RB RTH: Recalculated stop_size={tp_stop_size} for TP calculation")
                
                # Calculate TP using multiplier
                multiplier_map = {
                    Config.takeProfit[0]: 1,    # 1:1
                    Config.takeProfit[1]: 1.5,  # 1.5:1
                    Config.takeProfit[2]: 2,    # 2:1
                    Config.takeProfit[3]: 2.5,  # 2.5:1
                }
                if len(Config.takeProfit) > 4:
                    multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                
                multiplier = multiplier_map.get(profit, 2.0)  # Default 2:1
                tp_offset = tp_stop_size * multiplier
                
                if tradeType == 'BUY':
                    tp_price = round(aux_price + tp_offset, Config.roundVal)
                else:  # SELL
                    tp_price = round(aux_price - tp_offset, Config.roundVal)
                
                logging.info(f"RB RTH Bracket: entry={aux_price}, stop_loss={stop_loss_price}, tp={tp_price}, stop_size={tp_stop_size}, multiplier={multiplier}")
                
                # Generate unique order IDs for bracket orders
                parent_order_id = connection.get_next_order_id()
                tp_order_id = connection.get_next_order_id()
                sl_order_id = connection.get_next_order_id()
                
                logging.info("RB RTH: Generated unique order IDs for bracket: entry=%s, tp=%s, sl=%s", 
                            parent_order_id, tp_order_id, sl_order_id)
                
                # Entry order (Stop order)
                entry_order = Order(
                    orderId=parent_order_id,
                    orderType="STP",
                    action=tradeType,
                    totalQuantity=quantity,
                    auxPrice=aux_price,
                    tif=tif,
                    transmit=False  # Don't transmit until all orders are ready
                )
                
                # Take Profit order (Limit)
                tp_order = Order(
                    orderId=tp_order_id,
                    orderType="LMT",
                    action="SELL" if tradeType == "BUY" else "BUY",
                    totalQuantity=quantity,
                    lmtPrice=tp_price,
                    parentId=parent_order_id,
                    transmit=False
                )
                
                # Stop Loss order (Stop order)
                sl_order = Order(
                    orderId=sl_order_id,
                    orderType="STP",
                    action="SELL" if tradeType == "BUY" else "BUY",
                    totalQuantity=quantity,
                    auxPrice=stop_loss_price,
                    parentId=parent_order_id,
                    transmit=True  # Last order transmits all
                )
                
                # Place all bracket orders with error handling
                try:
                    logging.info("RB RTH: Placing entry order - orderId=%s, orderType=%s, action=%s, auxPrice=%s, quantity=%s", 
                                parent_order_id, entry_order.orderType, entry_order.action, entry_order.auxPrice, entry_order.totalQuantity)
                    entry_response = connection.placeTrade(contract=ibContract, order=entry_order, outsideRth=outsideRth)
                    logging.info("RB RTH: Entry order placed successfully - orderId=%s", entry_response.order.orderId)
                    StatusUpdate(entry_response, 'Entry', ibContract, 'STP', tradeType, quantity, histData, lastPrice, symbol,
                                 timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue, breakEven,
                                 outsideRth, False, entry_points)
                    
                    # Place TP order with retry logic if order ID is in done state
                    try:
                        logging.info("RB RTH: Placing TP order - orderId=%s, orderType=%s, action=%s, lmtPrice=%s", 
                                    tp_order_id, tp_order.orderType, tp_order.action, tp_order.lmtPrice)
                        tp_response = connection.placeTrade(contract=ibContract, order=tp_order, outsideRth=outsideRth)
                        logging.info("RB RTH: TP order placed successfully - orderId=%s", tp_response.order.orderId)
                    except Exception as e:
                        if "done state" in str(e) or "AssertionError" in str(e):
                            logging.warning("RB RTH: TP order ID %s in done state, generating new ID and retrying", tp_order_id)
                            # Generate new TP order ID and retry
                            tp_order_id = connection.get_next_order_id()
                            tp_order.orderId = tp_order_id
                            tp_response = connection.placeTrade(contract=ibContract, order=tp_order, outsideRth=outsideRth)
                            logging.info("RB RTH: TP order placed with new ID: %s", tp_order_id)
                        else:
                            logging.error("RB RTH: Error placing TP order: %s", e)
                            raise
                    StatusUpdate(tp_response, 'TakeProfit', ibContract, 'LMT', tradeType, quantity, histData, lastPrice, symbol,
                                 timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                                 breakEven, outsideRth)
                    
                    # Place SL order with retry logic if order ID is in done state
                    try:
                        logging.info("RB RTH: Placing SL order - orderId=%s, orderType=%s, action=%s, auxPrice=%s", 
                                    sl_order_id, sl_order.orderType, sl_order.action, sl_order.auxPrice)
                        sl_response = connection.placeTrade(contract=ibContract, order=sl_order, outsideRth=outsideRth)
                        logging.info("RB RTH: SL order placed successfully - orderId=%s", sl_response.order.orderId)
                    except Exception as e:
                        if "done state" in str(e) or "AssertionError" in str(e):
                            logging.warning("RB RTH: SL order ID %s in done state, generating new ID and retrying", sl_order_id)
                            # Generate new SL order ID and retry
                            sl_order_id = connection.get_next_order_id()
                            sl_order.orderId = sl_order_id
                            sl_response = connection.placeTrade(contract=ibContract, order=sl_order, outsideRth=outsideRth)
                            logging.info("RB RTH: SL order placed with new ID: %s", sl_order_id)
                        else:
                            logging.error("RB RTH: Error placing SL order: %s", e)
                            raise
                    StatusUpdate(sl_response, 'StopLoss', ibContract, 'STP', tradeType, quantity, histData, lastPrice, symbol,
                                 timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                                 breakEven, outsideRth)
                    
                    logging.info("RB RTH: Bracket orders placed successfully - entry=%s, tp=%s, sl=%s", 
                                entry_response.order.orderId, tp_response.order.orderId, sl_response.order.orderId)
                except Exception as e:
                    logging.error("RB RTH: Error placing bracket orders: %s", e)
                    logging.error("RB RTH: Traceback: %s", traceback.format_exc())
                    traceback.print_exc()
                    # Don't break - let the loop continue to retry
                    await asyncio.sleep(1)
                    continue
                
                # Store TP/SL order IDs in orderStatusData for potential future updates
                entry_order_id = int(entry_response.order.orderId)
                if entry_order_id in Config.orderStatusData:
                    Config.orderStatusData[entry_order_id]['tp_order_id'] = int(tp_response.order.orderId)
                    Config.orderStatusData[entry_order_id]['sl_order_id'] = int(sl_response.order.orderId)
                    Config.orderStatusData[entry_order_id]['tp_price'] = tp_price
                    Config.orderStatusData[entry_order_id]['stop_loss_price'] = stop_loss_price
                    Config.orderStatusData[entry_order_id]['profit'] = profit
                    Config.orderStatusData[entry_order_id]['stopLoss'] = stopLoss
                    Config.orderStatusData[entry_order_id]['tp_stop_size'] = tp_stop_size
                    logging.info("RB RTH: Stored TP/SL order IDs in orderStatusData: tp=%s, sl=%s", 
                                tp_response.order.orderId, sl_response.order.orderId)
        logging.info("rb_and_rbb entry order done %s ",symbol)
        break

async def manual_limit_order(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                             atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points):
    """
    Place a manual limit order with bracket orders:
    - Entry: Limit order at specified price
    - Take Profit: Limit order
    - Stop Loss: Stop-Limit order
    - Calculate quantity = Risk / Stop Size
    """
    try:
        entry_price = _parse_entry_price(entry_points)
    except ValueError as err:
        logging.error("Manual limit order requires a valid entry price: %s", err)
        return
    
    try:
        contract = getContract(symbol, None)
        histData = _get_latest_hist_bar(connection, contract, timeFrame)
        if histData is None:
            logging.error("Unable to fetch historical data for manual limit order %s %s", symbol, timeFrame)
            return
        
        if stopLoss in Config.atrStopLossMap:
            logging.info("Calculating ATR stop size for %s limit order: stopLoss=%s, symbol=%s", symbol, stopLoss, symbol)
            stop_size = _get_atr_stop_offset(connection, contract, stopLoss)
            if stop_size is None or math.isnan(stop_size) or stop_size <= 0:
                logging.error("ATR stop size unavailable for %s limit order %s (stop_size=%s). Check if daily candle data is available.", stopLoss, symbol, stop_size)
                return
            logging.info("ATR stop size calculated successfully: %s for %s", stop_size, symbol)
            if buySellType == 'BUY':
                stop_loss_price = entry_price - stop_size
            else:
                stop_loss_price = entry_price + stop_size
            stop_loss_price = round(stop_loss_price, Config.roundVal)
        else:
            # For Custom stop loss, calculate stop_size directly (similar to ATR logic and stop orders)
            if stopLoss == Config.stopLoss[1]:  # 'Custom'
                custom_stop = _to_float(slValue, 0)
                if custom_stop == 0:
                    logging.error("Custom stop loss requires a valid value for %s limit order %s", stopLoss, symbol)
                    return
                # Validate custom stop loss value
                if buySellType == 'BUY':
                    if custom_stop >= entry_price:
                        logging.error("Custom stop loss (%s) must be below entry price (%s) for BUY orders", custom_stop, entry_price)
                        return
                else:  # SELL
                    if custom_stop <= entry_price:
                        logging.error("Custom stop loss (%s) must be above entry price (%s) for SELL orders", custom_stop, entry_price)
                        return
                # Calculate stop_size directly (similar to ATR): stop_size = |entry - custom_stop|
                stop_size = abs(entry_price - custom_stop)
                if stop_size == 0 or math.isnan(stop_size):
                    logging.error("Stop size invalid (%s) for custom stop loss %s limit order %s", stop_size, custom_stop, symbol)
                    return
                logging.info("Custom stop loss for %s limit order: entry=%s, custom_stop=%s, stop_size=%s", symbol, entry_price, custom_stop, stop_size)
                # Calculate stop_loss_price from entry_price (similar to ATR logic)
                if buySellType == 'BUY':
                    stop_loss_price = entry_price - stop_size
                else:
                    stop_loss_price = entry_price + stop_size
                stop_loss_price = round(stop_loss_price, Config.roundVal)
            else:
                # For other stop loss types (EntryBar, HOD, LOD, etc.), use existing logic
                try:
                    raw_stop_loss_price, calculated_stop_size = _calculate_manual_stop_loss(
                        connection, contract, entry_price, stopLoss, buySellType, timeFrame, slValue
                    )
                    # Use the calculated stop_size from the function, or calculate from prices
                    if calculated_stop_size and calculated_stop_size > 0:
                        stop_size = calculated_stop_size
                    else:
                        stop_size = abs(entry_price - raw_stop_loss_price)
                except ValueError as err:
                    logging.error("Error calculating stop loss for manual limit order: %s", err)
                    return
                except Exception as e:
                    logging.error("Unexpected error calculating stop loss for manual limit order: %s", e)
                    logging.error("Traceback: %s", traceback.format_exc())
                    return
            if stop_size == 0 or math.isnan(stop_size):
                logging.error("Stop size invalid (%s) for manual limit order %s", stop_size, symbol)
                return
            if buySellType == 'BUY':
                stop_loss_price = entry_price - stop_size
            else:
                stop_loss_price = entry_price + stop_size
            stop_loss_price = round(stop_loss_price, Config.roundVal)
        
        # Calculate quantity based on risk and stop size
        risk_amount = _to_float(risk, 0)
        if risk_amount is None or math.isnan(risk_amount) or risk_amount <= 0:
            logging.error("Invalid risk amount for manual limit order: %s", risk)
            return
        
        qty = _calculate_manual_quantity(entry_price, stop_loss_price, risk_amount)
        
        # Calculate take profit price based on stop size and profit multiplier
        # TP = entry + (stop_size × multiplier) for BUY
        # TP = entry - (stop_size × multiplier) for SELL
        multiplier_map = {
            Config.takeProfit[0]: 1,    # 1:1
            Config.takeProfit[1]: 1.5,  # 1.5:1
            Config.takeProfit[2]: 2,    # 2:1
            Config.takeProfit[3]: 2.5,  # 2.5:1
        }
        # Add 3:1 if it exists (index 4)
        if len(Config.takeProfit) > 4:
            multiplier_map[Config.takeProfit[4]] = 3  # 3:1
        
        multiplier = multiplier_map.get(profit, 1)  # Default to 1:1 if not found
        tp_offset = stop_size * multiplier
        
        if buySellType == 'BUY':
            tp_price = entry_price + tp_offset
        else:  # SELL
            tp_price = entry_price - tp_offset
        
        # Check if regular market hours (not extended hours)
        is_extended, session = _is_extended_outside_rth(outsideRth)
        
        tp_price = round(tp_price, Config.roundVal)
        
        # Check if LOD or HOD is selected - entry should be STP in all sessions
        is_lod_hod = (stopLoss == Config.stopLoss[3]) or (stopLoss == Config.stopLoss[4])  # HOD (index 3) or LOD (index 4)
        
        if is_lod_hod:
            # For LOD/HOD: Calculate entry stop price based on LOD/HOD
            # Uses premarket data for premarket, RTH data for after hours
            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, contract, timeFrame)
            
            if lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
                # For HOD/LOD: Auto-detect based on order type
                # BUY orders use LOD, SELL orders use HOD
                if buySellType == 'BUY':
                    # BUY: Stop loss is at LOD
                    stop_loss_price = round(lod, Config.roundVal)
                    entry_stop_price = round(entry_price, Config.roundVal)
                    # Entry must be above LOD (stop loss below entry)
                    if entry_stop_price <= stop_loss_price:
                        entry_stop_price = round(stop_loss_price + 0.01, Config.roundVal)
                        logging.warning("HOD/LOD BUY: Entry price adjusted to be above LOD: %s -> %s", entry_price, entry_stop_price)
                    logging.info(f"Manual Limit Order HOD/LOD BUY: Auto-detected LOD, stop_loss={stop_loss_price}, entry={entry_stop_price}")
                else:  # SELL
                    # SELL: Stop loss is at HOD
                    stop_loss_price = round(hod, Config.roundVal)
                    entry_stop_price = round(entry_price, Config.roundVal)
                    # Entry must be below HOD (stop loss above entry)
                    if entry_stop_price >= stop_loss_price:
                        entry_stop_price = round(stop_loss_price - 0.01, Config.roundVal)
                        logging.warning("HOD/LOD SELL: Entry price adjusted to be below HOD: %s -> %s", entry_price, entry_stop_price)
                    logging.info(f"Manual Limit Order HOD/LOD SELL: Auto-detected HOD, stop_loss={stop_loss_price}, entry={entry_stop_price}")
                
                # Calculate stop_size: |bar_high/low - HOD/LOD|
                if buySellType == 'BUY':
                    # For BUY: use bar_high and LOD
                    bar_price = float(histData['high']) if histData else entry_stop_price
                    stop_size = abs(bar_price - lod)
                else:  # SELL
                    # For SELL: use bar_low and HOD
                    bar_price = float(histData['low']) if histData else entry_stop_price
                    stop_size = abs(bar_price - hod)
                
                stop_size = round(stop_size, Config.roundVal)
                if stop_size <= 0:
                    # Fallback: use bar range or minimum
                    if histData:
                        bar_range = (float(histData['high']) - float(histData['low'])) + Config.add002
                        stop_size = max(0.01, round(bar_range, Config.roundVal))
                        logging.warning("LOD/HOD: Invalid stop_size, using bar range: %s", stop_size)
                    else:
                        stop_size = 0.01
                        logging.warning("LOD/HOD: Invalid stop_size, using minimum: 0.01")
                
                logging.info(f"Manual Limit Order HOD/LOD: bar_price={bar_price} (high={histData.get('high') if histData else 'N/A'}, low={histData.get('low') if histData else 'N/A'}), stop_loss={stop_loss_price}, stop_size={stop_size}")
                
                # Recalculate quantity based on new stop_size
                qty = _calculate_manual_quantity(entry_stop_price, stop_loss_price, risk_amount)
                
                logging.info("LOD/HOD entry: symbol=%s, stopLoss=%s, entry_stop=%s, stop_loss=%s, LOD=%s, HOD=%s, stop_size=%s, qty=%s",
                             symbol, stopLoss, entry_stop_price, stop_loss_price, lod, hod, stop_size, qty)
            else:
                logging.error("Cannot get historical data for LOD/HOD calculation for %s", symbol)
                return
        
        logging.info("Manual limit order calculation: symbol=%s, entry=%s, stop_size=%s, tp=%s, stop_loss=%s, risk=%s, quantity=%s, session=%s, is_extended=%s, is_lod_hod=%s",
                     symbol, entry_price, stop_size, tp_price, stop_loss_price, risk_amount, qty, session, is_extended, is_lod_hod)
        
        # Generate entry order ID
        parent_order_id = connection.get_next_order_id()
        
        # For LOD/HOD: Always use STP order in all sessions
        if is_lod_hod:
            # Entry order as STP (Stop Order) for all sessions
            # entry_stop_price is already calculated in the is_lod_hod block above
            entry_order = Order(
                orderId=parent_order_id,
                orderType="STP",
                action=buySellType,
                totalQuantity=qty,
                auxPrice=entry_stop_price,
                tif=tif,
                transmit=True  # Transmit immediately - TP/SL will be sent after fill
            )
            
            try:
                logging.info("Placing LOD/HOD STP order: orderId=%s, orderType=STP, action=%s, quantity=%s, auxPrice=%s, tif=%s, outsideRth=%s",
                            parent_order_id, buySellType, qty, entry_stop_price, tif, outsideRth)
                entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
                if entry_response is None:
                    logging.error("placeTrade returned None for LOD/HOD STP order %s", symbol)
                    return
                logging.info("placeTrade successful: orderId=%s, status=%s", 
                            entry_response.order.orderId, entry_response.orderStatus.status)
                StatusUpdate(entry_response, 'Entry', contract, 'STP', buySellType, qty, histData, entry_stop_price, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                             breakEven, outsideRth, False, entry_points)
                
                # Store stop_size and stop_loss_price in orderStatusData
                if int(entry_response.order.orderId) in Config.orderStatusData:
                    Config.orderStatusData[int(entry_response.order.orderId)]['stopSize'] = stop_size
                    Config.orderStatusData[int(entry_response.order.orderId)]['stopLossPrice'] = stop_loss_price
                    logging.info(f"Stored stop_size={stop_size}, stop_loss_price={stop_loss_price} in orderStatusData for LOD/HOD order {entry_response.order.orderId}")
                
                logging.info("LOD/HOD: Entry STP order placed. TP and SL will be sent automatically after entry fills.")
            except Exception as place_error:
                logging.error("Error placing LOD/HOD STP order for %s: %s", symbol, place_error)
                logging.error("Traceback: %s", traceback.format_exc())
                raise
            return  # Exit early for LOD/HOD
        
        if is_extended:
            # Extended hours (premarket/after-hours): Send ONLY entry order (keep original type: LMT)
            # TP and SL will be sent automatically after entry fills via sendTpAndSl()
            logging.info(f"Extended hours: Sending entry as LMT (Limit Order). TP and SL will be sent after fill.")
            
            # Entry order as LMT (Limit Order - keep original type)
            entry_order = Order(
                orderId=parent_order_id,
                orderType="LMT",
                action=buySellType,
                totalQuantity=qty,
                lmtPrice=entry_price,
                tif=tif,
                transmit=True  # Transmit immediately - TP/SL will be sent after fill
            )
            
            # Place ONLY entry order - TP and SL will be sent after fill via sendTpAndSl()
            try:
                logging.info("Placing limit order: orderId=%s, orderType=LMT, action=%s, quantity=%s, limitPrice=%s, tif=%s, outsideRth=%s",
                            parent_order_id, buySellType, qty, entry_price, tif, outsideRth)
                entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
                if entry_response is None:
                    logging.error("placeTrade returned None for limit order %s", symbol)
                    return
                logging.info("placeTrade successful: orderId=%s, status=%s", 
                            entry_response.order.orderId, entry_response.orderStatus.status)
                StatusUpdate(entry_response, 'Entry', contract, 'LMT', buySellType, qty, histData, entry_price, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                             breakEven, outsideRth, False, entry_points)
                
                # Store stop_size in orderStatusData for use in sendStopLoss (only for Custom stop loss Limit Orders)
                if stopLoss == Config.stopLoss[1] and int(entry_response.order.orderId) in Config.orderStatusData:  # Only for Custom stop loss
                    Config.orderStatusData[int(entry_response.order.orderId)]['stopSize'] = stop_size
                    logging.info(f"Stored stop_size={stop_size} in orderStatusData for Custom stop loss Limit Order {entry_response.order.orderId}")
                
                logging.info("Extended hours: Entry LMT order placed. TP and SL will be sent automatically after entry fills.")
            except Exception as place_error:
                logging.error("Error placing limit order for %s: %s", symbol, place_error)
                logging.error("Traceback: %s", traceback.format_exc())
                raise  # Re-raise to be caught by outer exception handler
        else:
            # Regular market hours: Send bracket orders (Entry LMT, TP LMT, SL STP)
            # Generate separate unique order IDs for each order in the bracket
            tp_order_id = connection.get_next_order_id()
            sl_order_id = connection.get_next_order_id()
            
            logging.info("Generated unique order IDs for bracket: entry=%s, tp=%s, sl=%s", 
                        parent_order_id, tp_order_id, sl_order_id)
            
            # Entry order (Limit)
            entry_order = Order(
                orderId=parent_order_id,
                orderType="LMT",
                action=buySellType,
                totalQuantity=qty,
                lmtPrice=entry_price,
                tif=tif,
                transmit=False  # Don't transmit until all orders are ready
            )
            
            # Take Profit order (Limit)
            tp_order = Order(
                orderId=tp_order_id,
                orderType="LMT",
                action="SELL" if buySellType == "BUY" else "BUY",
                totalQuantity=qty,
                lmtPrice=tp_price,
                parentId=parent_order_id,
                transmit=False
            )
            
            # Stop Loss order (Stop market for regular hours)
            sl_order = Order(
                orderId=sl_order_id,
                orderType="STP",
                action="SELL" if buySellType == "BUY" else "BUY",
                totalQuantity=qty,
                auxPrice=round(stop_loss_price, Config.roundVal),  # Stop price
                parentId=parent_order_id,
                transmit=True  # Last order transmits all
            )
            
            # Place all bracket orders
            entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
            StatusUpdate(entry_response, 'Entry', contract, 'LMT', buySellType, qty, histData, entry_price, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                         breakEven, outsideRth)
            
            tp_response = connection.placeTrade(contract=contract, order=tp_order, outsideRth=outsideRth)
            StatusUpdate(tp_response, 'TakeProfit', contract, 'LMT', buySellType, qty, histData, entry_price, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                         breakEven, outsideRth)
            
            sl_response = connection.placeTrade(contract=contract, order=sl_order, outsideRth=outsideRth)
            StatusUpdate(sl_response, 'StopLoss', contract, 'STP', buySellType, qty, histData, entry_price, symbol,
                         timeFrame, profit, stopLoss, risk, Config.orderStatusData.get(int(entry_response.order.orderId)), tif, barType, buySellType, atrPercentage, slValue,
                         breakEven, outsideRth)
            
            logging.info("Regular market: Bracket orders placed for %s: entry=%s, tp=%s, sl=%s, quantity=%s", 
                         symbol, entry_price, tp_price, stop_loss_price, qty)
    except Exception as e:
        logging.error("error in manual limit order %s", e)
        traceback.print_exc()

async def manual_stop_order(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                            atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points):
    """
    Place a manual stop order with bracket orders:
    - Entry: Stop order at specified trigger price
    - Take Profit: Limit order
    - Stop Loss: Stop-Limit order
    - Calculate quantity = Risk / Stop Size
    """
    try:
        entry_price = _parse_entry_price(entry_points)
    except ValueError as err:
        logging.error("Manual stop order requires a valid entry price: %s", err)
        return
    
    try:
        contract = getContract(symbol, None)
        
        # For Custom stop orders, histData is optional (we have entry_price and custom_stop)
        # For other stop loss types, we need histData for calculations
        is_custom_stop = (stopLoss == Config.stopLoss[1])  # 'Custom'
        histData = None
        
        if not is_custom_stop:
            # For non-Custom stop orders, try to get historical data with retry
            max_retries = 3
            retry_count = 0
            while retry_count < max_retries:
                histData = _get_latest_hist_bar(connection, contract, timeFrame)
                if histData is not None:
                    break
                retry_count += 1
                if retry_count < max_retries:
                    logging.warning("Unable to fetch historical data for manual stop order %s %s (retry %s/%s), waiting...", 
                                  symbol, timeFrame, retry_count, max_retries)
                    await asyncio.sleep(2)
            
            if histData is None:
                # Try fallback: get price from tick data or daily candle
                logging.info("Trying fallback methods to get price data for %s", symbol)
                try:
                    if outsideRth:
                        # Try daily candle first
                        dailyData = connection.getDailyCandle(contract)
                        if dailyData and len(dailyData) > 0:
                            last_close = dailyData[-1].close
                            histData = {'high': last_close, 'low': last_close, 'close': last_close}
                            logging.info("Using daily candle data as fallback: %s", histData)
                        else:
                            # Try tick data
                            connection.subscribeTicker(contract)
                            priceObj = connection.getTickByTick(contract)
                            if priceObj is not None:
                                tick_price = priceObj.marketPrice()
                                connection.cancelTickData(contract)
                                histData = {'high': tick_price, 'low': tick_price, 'close': tick_price}
                                logging.info("Using tick data as fallback: %s", histData)
                except Exception as e:
                    logging.warning("Fallback methods failed: %s", e)
                
                if histData is None:
                    logging.error("Unable to fetch historical data for manual stop order %s %s after retries and fallbacks", symbol, timeFrame)
                    return
        else:
            # For Custom stop orders, try to get histData but don't fail if unavailable
            histData = _get_latest_hist_bar(connection, contract, timeFrame)
            if histData is None:
                logging.info("Historical data not available for Custom stop order %s %s, proceeding with entry_price and custom_stop only", symbol, timeFrame)
        
        # Calculate stop loss price based on stop loss type
        # For stop orders, when triggered, the actual entry will be slightly different
        # BUY stop: entry = trigger - 0.01
        # SELL stop: entry = trigger + 0.01
        actual_entry_price = entry_price
        if buySellType == 'BUY':
            actual_entry_price = entry_price - 0.01
        else:  # SELL
            actual_entry_price = entry_price + 0.01
        actual_entry_price = round(actual_entry_price, Config.roundVal)
        
        if stopLoss in Config.atrStopLossMap:
            stop_size = _get_atr_stop_offset(connection, contract, stopLoss)
            if stop_size is None or math.isnan(stop_size) or stop_size <= 0:
                logging.error("ATR stop size unavailable for %s stop order %s", stopLoss, symbol)
                return
            if buySellType == 'BUY':
                stop_loss_price = actual_entry_price - stop_size
            else:
                stop_loss_price = actual_entry_price + stop_size
            stop_loss_price = round(stop_loss_price, Config.roundVal)
        else:
            # For Custom stop loss, calculate stop_size with +0.02 (only for Stop Order entry)
            if stopLoss == Config.stopLoss[1]:  # 'Custom'
                custom_stop = _to_float(slValue, 0)
                if custom_stop == 0:
                    logging.error("Custom stop loss requires a valid value for %s stop order %s", stopLoss, symbol)
                    return
                # Validate custom stop loss value
                if buySellType == 'BUY':
                    if custom_stop >= entry_price:
                        logging.error("Custom stop loss (%s) must be below entry price (%s) for BUY orders", custom_stop, entry_price)
                        return
                else:  # SELL
                    if custom_stop <= entry_price:
                        logging.error("Custom stop loss (%s) must be above entry price (%s) for SELL orders", custom_stop, entry_price)
                        return
                # Calculate stop_size: stop_size = |entry_price - custom_stop|
                # Use entry_price (not actual_entry_price) for consistency with entry order calculation
                stop_size = abs(entry_price - custom_stop)
                stop_size = round(stop_size, Config.roundVal)
                if stop_size == 0 or math.isnan(stop_size):
                    logging.error("Stop size invalid (%s) for custom stop loss %s stop order %s", stop_size, custom_stop, symbol)
                    return
                logging.info("Custom stop loss for %s stop order: entry=%s, custom_stop=%s, stop_size=%s (|entry - custom_stop|)", symbol, entry_price, custom_stop, stop_size)
                # For Custom stop loss: stop_loss_price = custom_stop (the custom stop value itself)
                stop_loss_price = round(custom_stop, Config.roundVal)
                
                # IMPORTANT: For Custom stop loss, TP should be calculated using entry_price (not actual_entry_price)
                # to ensure consistency with stop_size calculation
                # This prevents TP and SL from being the same price
                # Recalculate actual_entry_price for TP calculation to use entry_price as base
                # For BUY: actual_entry = entry - 0.01, but for TP we use entry_price
                # For SELL: actual_entry = entry + 0.01, but for TP we use entry_price
                # So we'll override actual_entry_price for TP calculation only
                tp_base_price = entry_price  # Use entry_price for TP calculation
            else:
                # For other stop loss types (EntryBar, HOD, LOD, etc.), use existing logic
                try:
                    raw_stop_loss_price, calculated_stop_size = _calculate_manual_stop_loss(
                        connection, contract, entry_price, stopLoss, buySellType, timeFrame, slValue
                    )
                    # Use the calculated stop_size from the function, or calculate from prices
                    if calculated_stop_size and calculated_stop_size > 0:
                        stop_size = calculated_stop_size
                    else:
                        stop_size = abs(entry_price - raw_stop_loss_price)
                except ValueError as err:
                    logging.error("Error calculating stop loss for manual stop order: %s", err)
                    return
                except Exception as e:
                    logging.error("Unexpected error calculating stop loss for manual stop order: %s", e)
                    logging.error("Traceback: %s", traceback.format_exc())
                    return
                # For EntryBar stop loss: use raw_stop_loss_price directly (bar's high/low, no buffer)
                # For other non-Custom stop loss types: calculate stop_loss_price from actual_entry_price
            if stop_size == 0 or math.isnan(stop_size):
                logging.error("Stop size invalid (%s) for manual stop order %s", stop_size, symbol)
                return
            # Only recalculate stop_loss_price for non-Custom, non-EntryBar stop loss types
            # For Custom stop loss, stop_loss_price was already set to custom_stop at line 1642
            # For EntryBar stop loss, use raw_stop_loss_price directly (bar's high/low)
            if stopLoss == Config.stopLoss[0]:  # EntryBar
                stop_loss_price = round(raw_stop_loss_price, Config.roundVal)
            elif stopLoss != Config.stopLoss[1]:  # Not 'Custom' and not EntryBar
                if buySellType == 'BUY':
                    stop_loss_price = actual_entry_price - stop_size
                else:
                    stop_loss_price = actual_entry_price + stop_size
                stop_loss_price = round(stop_loss_price, Config.roundVal)
            
            # Validate stop_size for Custom stop loss (already validated for others above)
            if stopLoss == Config.stopLoss[1]:  # 'Custom'
                if stop_size == 0 or math.isnan(stop_size):
                    logging.error("Stop size invalid (%s) for custom stop loss %s stop order %s", stop_size, custom_stop, symbol)
                    return

        risk_amount = _to_float(risk, 0)
        if risk_amount is None or math.isnan(risk_amount) or risk_amount <= 0:
            logging.error("Invalid risk amount for manual stop order: %s", risk)
            return
        
        # For ATR stop loss: calculate quantity directly using ATR stop_size
        # For Custom stop loss: calculate quantity directly using stop_size
        if stopLoss in Config.atrStopLossMap:
            # Quantity = risk / stop_size (ATR-based), rounded UP
            qty = risk_amount / stop_size
            qty = int(math.ceil(qty))  # Round UP
            if qty <= 0:
                qty = 1
            logging.info("ATR stop loss quantity: risk=%s, stop_size=%s (ATR-based), quantity=%s (rounded up)", risk_amount, stop_size, qty)
        elif stopLoss == Config.stopLoss[1]:  # 'Custom'
            # Quantity = risk / stop_size, rounded UP
            qty = risk_amount / stop_size
            qty = int(math.ceil(qty))  # Round UP
            if qty <= 0:
                qty = 1
            logging.info("Custom stop loss quantity: risk=%s, stop_size=%s, quantity=%s (rounded up)", risk_amount, stop_size, qty)
        else:
            # For other stop loss types, use existing calculation
            qty = _calculate_manual_quantity(actual_entry_price, stop_loss_price, risk_amount)
        
        # Calculate take profit price based on stop size and profit multiplier
        # TP = base_price + (stop_size × multiplier) for BUY
        # TP = base_price - (stop_size × multiplier) for SELL
        # For ATR and Custom stop loss: Use entry_price (not actual_entry_price) for TP calculation
        # to ensure consistency with stop_size calculation
        multiplier_map = {
            Config.takeProfit[0]: 1,    # 1:1
            Config.takeProfit[1]: 1.5,  # 1.5:1
            Config.takeProfit[2]: 2,    # 2:1
            Config.takeProfit[3]: 2.5,  # 2.5:1
        }
        # Add 3:1 if it exists (index 4)
        if len(Config.takeProfit) > 4:
            multiplier_map[Config.takeProfit[4]] = 3  # 3:1
        
        multiplier = multiplier_map.get(profit, 1)  # Default to 1:1 if not found
        tp_offset = stop_size * multiplier
        
        # For ATR stop loss: Use entry_price for TP calculation (consistent with ATR stop_size)
        # For Custom stop loss: Use entry_price for TP calculation (tp_base_price was set above)
        # For other stop loss types: Use actual_entry_price
        if stopLoss in Config.atrStopLossMap:
            tp_base_price = entry_price  # Use entry_price for consistency with ATR stop_size calculation
        elif stopLoss == Config.stopLoss[1]:  # 'Custom'
            tp_base_price = entry_price  # Use entry_price for consistency with stop_size calculation
        else:
            tp_base_price = actual_entry_price  # Use actual_entry_price for other stop loss types
        
        if buySellType == 'BUY':
            tp_price = tp_base_price + tp_offset
        else:  # SELL
            tp_price = tp_base_price - tp_offset
        
        # Check if regular market hours (not extended hours)
        is_extended, session = _is_extended_outside_rth(outsideRth)
        
        tp_price = round(tp_price, Config.roundVal)
        
        # Check if LOD or HOD is selected - entry should be STP in all sessions
        is_lod_hod = (stopLoss == Config.stopLoss[3]) or (stopLoss == Config.stopLoss[4])  # HOD (index 3) or LOD (index 4)
        
        if is_lod_hod:
            # For LOD/HOD: Calculate entry stop price based on LOD/HOD
            # Uses premarket data for premarket, RTH data for after hours
            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, contract, timeFrame)
            
            if lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
                # For LOD/HOD: Stop loss price is always LOD or HOD
                # Entry stop price uses user's entry_price, but must be valid relative to stop loss
                if stopLoss == Config.stopLoss[4]:  # LOD (index 4)
                    # Stop loss is always at LOD
                    stop_loss_price = round(lod, Config.roundVal)
                    # Entry stop price: use user's entry_price
                    entry_stop_price = round(entry_price, Config.roundVal)
                    # For BUY: entry must be above LOD (stop loss below entry)
                    if buySellType == 'BUY' and entry_stop_price <= stop_loss_price:
                        # Adjust entry to be above LOD
                        entry_stop_price = round(stop_loss_price + 0.01, Config.roundVal)
                        logging.warning("LOD BUY: Entry price adjusted to be above LOD: %s -> %s", entry_price, entry_stop_price)
                else:  # HOD
                    # Stop loss is always at HOD
                    stop_loss_price = round(hod, Config.roundVal)
                    # Entry stop price: use user's entry_price
                    entry_stop_price = round(entry_price, Config.roundVal)
                    # For SELL: entry must be below HOD (stop loss above entry)
                    if buySellType == 'SELL' and entry_stop_price >= stop_loss_price:
                        # Adjust entry to be below HOD
                        entry_stop_price = round(stop_loss_price - 0.01, Config.roundVal)
                        logging.warning("HOD SELL: Entry price adjusted to be below HOD: %s -> %s", entry_price, entry_stop_price)
                
                # Calculate stop_size: |bar_high/low - HOD/LOD|
                if buySellType == 'BUY':
                    # For BUY: use bar_high
                    bar_price = float(histData['high']) if histData else entry_stop_price
                else:  # SELL
                    # For SELL: use bar_low
                    bar_price = float(histData['low']) if histData else entry_stop_price
                
                stop_size = abs(bar_price - stop_loss_price)
                stop_size = round(stop_size, Config.roundVal)
                if stop_size <= 0:
                    # Fallback: use bar range or minimum
                    if histData:
                        bar_range = (float(histData['high']) - float(histData['low'])) + Config.add002
                        stop_size = max(0.01, round(bar_range, Config.roundVal))
                        logging.warning("LOD/HOD: Invalid stop_size, using bar range: %s", stop_size)
                    else:
                        stop_size = 0.01
                        logging.warning("LOD/HOD: Invalid stop_size, using minimum: 0.01")
                
                logging.info(f"Manual Stop Order HOD/LOD: bar_price={bar_price} (high={histData.get('high') if histData else 'N/A'}, low={histData.get('low') if histData else 'N/A'}), stop_loss={stop_loss_price}, stop_size={stop_size}")
                
                # Recalculate actual_entry_price (for stop orders, actual fill is slightly different)
                actual_entry_price = entry_stop_price
                if buySellType == 'BUY':
                    actual_entry_price = entry_stop_price - 0.01
                else:
                    actual_entry_price = entry_stop_price + 0.01
                actual_entry_price = round(actual_entry_price, Config.roundVal)
                
                # Recalculate quantity based on actual_entry_price and stop_loss_price
                # For stop orders, use actual_entry_price (where order actually fills)
                qty = _calculate_manual_quantity(actual_entry_price, stop_loss_price, risk_amount)
                
                # Recalculate TP based on new actual_entry_price
                multiplier = multiplier_map.get(profit, 1)
                tp_offset = stop_size * multiplier
                if buySellType == 'BUY':
                    tp_price = actual_entry_price + tp_offset
                else:
                    tp_price = actual_entry_price - tp_offset
                tp_price = round(tp_price, Config.roundVal)
                
                logging.info("LOD/HOD entry: symbol=%s, stopLoss=%s, entry_stop=%s, actual_entry=%s, stop_loss=%s, LOD=%s, HOD=%s, stop_size=%s, qty=%s",
                             symbol, stopLoss, entry_stop_price, actual_entry_price, stop_loss_price, lod, hod, stop_size, qty)
            else:
                logging.error("Cannot get historical data for LOD/HOD calculation for %s", symbol)
                return
        
        logging.info("Manual stop order calculation: symbol=%s, trigger=%s, actual_entry=%s, stop_size=%s, tp=%s, stop_loss=%s, risk=%s, quantity=%s, session=%s, is_extended=%s, is_lod_hod=%s",
                     symbol, entry_price, actual_entry_price, stop_size, tp_price, stop_loss_price, risk_amount, qty, session, is_extended, is_lod_hod)
        
        # Generate entry order ID
        parent_order_id = connection.get_next_order_id()
        
        # For LOD/HOD: Always use STP order in all sessions
        if is_lod_hod:
            # Entry order as STP (Stop Order) for all sessions
            # entry_stop_price is already calculated in the is_lod_hod block above
            entry_order = Order(
                orderId=parent_order_id,
                orderType="STP",
                action=buySellType,
                totalQuantity=qty,
                auxPrice=entry_stop_price,
                tif=tif,
                transmit=True  # Transmit immediately - TP/SL will be sent after fill
            )
            
            try:
                logging.info("Placing LOD/HOD STP order: orderId=%s, orderType=STP, action=%s, quantity=%s, auxPrice=%s, tif=%s, outsideRth=%s",
                            parent_order_id, buySellType, qty, entry_stop_price, tif, outsideRth)
                entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
                if entry_response is None:
                    logging.error("placeTrade returned None for LOD/HOD STP order %s", symbol)
                    return
                logging.info("placeTrade successful: orderId=%s, status=%s", 
                            entry_response.order.orderId, entry_response.orderStatus.status)
                StatusUpdate(entry_response, 'Entry', contract, 'STP', buySellType, qty, histData, entry_stop_price, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                             breakEven, outsideRth, False, entry_points)
                
                # Store stop_size and stop_loss_price in orderStatusData
                if int(entry_response.order.orderId) in Config.orderStatusData:
                    Config.orderStatusData[int(entry_response.order.orderId)]['stopSize'] = stop_size
                    Config.orderStatusData[int(entry_response.order.orderId)]['stopLossPrice'] = stop_loss_price
                    logging.info(f"Stored stop_size={stop_size}, stop_loss_price={stop_loss_price} in orderStatusData for LOD/HOD order {entry_response.order.orderId}")
                
                logging.info("LOD/HOD: Entry STP order placed. TP and SL will be sent automatically after entry fills.")
            except Exception as place_error:
                logging.error("Error placing LOD/HOD STP order for %s: %s", symbol, place_error)
                logging.error("Traceback: %s", traceback.format_exc())
                raise
            return  # Exit early for LOD/HOD
        
        # Initialize entry_limit_price (used for Custom orders in extended hours)
        entry_limit_price = None
        
        if is_extended:
            # Extended hours (premarket/after-hours) for Custom trade type:
            # Entry: STP LMT (Stop Limit Order)
            # - Entry price: high + 0.01 (BUY) or low - 0.01 (SELL)
            # - Entry limit price: entry_price ± 0.5 * stop_size
            # Stop Loss: STP LMT (unchanged)
            # Take Profit: LMT (unchanged)
            
            # For Custom trade type in extended hours:
            # - Entry order is STP LMT (Stop Limit)
            # - Entry stop price = user custom entry (entry_points)
            # - Entry limit price = entry stop price ± 0.5 * stop_size
            # - For EntryBar stop loss: stop_size is derived from bar high/low ± 0.01 minus entry stop price
            if barType == 'Custom':
                # CASE 1: Custom entry + ATR stop loss
                # In this case we already calculated:
                #   - entry_price  = custom entry (from entry_points)
                #   - stop_size  = ATR * percentage (from _get_atr_stop_offset)
                #   - stop_loss_price = actual_entry_price ± stop_size
                #   - qty = risk / stop_size (ATR-based)
                # Entry STP LMT should be:
                #   - Stop  = custom entry
                #   - Limit = custom entry ± 0.5 * stop_size (ATR-based)
                if stopLoss in Config.atrStopLossMap:
                    entry_stop_price = round(entry_price, Config.roundVal)
                    entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                    if buySellType == 'BUY':
                        entry_limit_price = round(entry_stop_price + entry_limit_offset, Config.roundVal)
                    else:
                        entry_limit_price = round(entry_stop_price - entry_limit_offset, Config.roundVal)

                    logging.info(
                        "Extended hours Custom+ATR: Entry STP LMT - Stop=%s, Limit=%s, "
                        "entry=%s, stop_loss=%s, stop_size=%s (ATR-based), qty=%s, symbol=%s, action=%s",
                        entry_stop_price, entry_limit_price, entry_price, stop_loss_price, stop_size, qty, symbol, buySellType
                    )

                    entry_order = Order(
                        orderId=parent_order_id,
                        orderType="STP LMT",
                        action=buySellType,
                        totalQuantity=qty,
                        auxPrice=entry_stop_price,      # Stop trigger = custom entry
                        lmtPrice=entry_limit_price,     # Limit around custom entry (ATR-based)
                        tif=tif,
                        transmit=True  # TP/SL will be sent after fill
                    )
                # CASE 2: Custom entry + Custom stop loss
                # In this case we already calculated:
                #   - entry_price  = custom entry (from entry_points)
                #   - stop_loss_price = custom_stop (from slValue)
                #   - stop_size  = |entry_price - custom_stop|
                # We must NOT override these with bar-based values. Entry STP LMT should be:
                #   - Stop  = custom entry
                #   - Limit = custom entry ± 0.5 * stop_size
                elif stopLoss == Config.stopLoss[1]:  # 'Custom' stop loss
                    entry_stop_price = round(entry_price, Config.roundVal)
                    entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                    if buySellType == 'BUY':
                        entry_limit_price = round(entry_stop_price + entry_limit_offset, Config.roundVal)
                    else:
                        entry_limit_price = round(entry_stop_price - entry_limit_offset, Config.roundVal)

                    logging.info(
                        "Extended hours Custom+Custom: Entry STP LMT - Stop=%s, Limit=%s, "
                        "entry=%s, custom_stop=%s, stop_size=%s, symbol=%s, action=%s",
                        entry_stop_price, entry_limit_price, entry_price, stop_loss_price, stop_size, symbol, buySellType
                    )

                    entry_order = Order(
                        orderId=parent_order_id,
                        orderType="STP LMT",
                        action=buySellType,
                        totalQuantity=qty,
                        auxPrice=entry_stop_price,      # Stop trigger = custom entry
                        lmtPrice=entry_limit_price,     # Limit around custom entry
                        tif=tif,
                        transmit=True  # TP/SL will be sent after fill
                    )
                else:
                    # CASE 2: Other stop loss types (EntryBar, BarByBar, HOD/LOD) with Custom entry
                    bar_high = float(histData.get('high', 0)) if histData else entry_price
                    bar_low = float(histData.get('low', 0)) if histData else entry_price

                    # User-specified custom entry (entry_points) is the entry stop price for the STP LMT order
                    entry_stop_price = round(entry_price, Config.roundVal)

                    if stopLoss == Config.stopLoss[0]:  # EntryBar
                    # For EntryBar in OTH:
                    #   - BUY: reference price = bar_high + 0.01
                    #   - SELL: reference price = bar_low - 0.01
                    #   - stop_size = |reference_price - entry_stop_price|
                        if buySellType == 'BUY':
                            reference_price = round(bar_high + 0.01, Config.roundVal)
                        else:  # SELL
                            reference_price = round(bar_low - 0.01, Config.roundVal)

                        stop_size = abs(reference_price - entry_stop_price)
                        stop_size = round(stop_size, Config.roundVal)

                        # Recalculate quantity based on this stop_size
                        qty = risk_amount / stop_size
                        qty = int(math.ceil(qty))
                        if qty <= 0:
                            qty = 1

                        logging.info(
                            "Custom OTH EntryBar: bar_high=%s, bar_low=%s, reference_price=%s, "
                            "entry_stop_price=%s, stop_size=%s, qty=%s",
                            bar_high, bar_low, reference_price, entry_stop_price, stop_size, qty
                        )
                    else:
                        # For other stop loss types: keep previous behaviour (recalculate based on bar high/low)
                        # Calculate "entry price" from bar high/low as before, then derive stop_size/qty.
                        if buySellType == 'BUY':
                            entry_price = round(bar_high + 0.01, Config.roundVal)
                        else:  # SELL
                            entry_price = round(bar_low - 0.01, Config.roundVal)

                        if stopLoss not in Config.atrStopLossMap:  # Non-ATR stop loss (EntryBar, BarByBar, HOD, LOD)
                            # Recalculate stop_loss_price and stop_size with new entry_price
                            try:
                                raw_stop_loss_price, calculated_stop_size = _calculate_manual_stop_loss(
                                    connection, contract, entry_price, stopLoss, buySellType, timeFrame, slValue
                                )
                                stop_loss_price = round(raw_stop_loss_price, Config.roundVal)
                                # Stop size = |entry_price - stop_loss_price|
                                if calculated_stop_size and calculated_stop_size > 0:
                                    stop_size = calculated_stop_size
                                else:
                                    stop_size = abs(entry_price - stop_loss_price)
                                stop_size = round(stop_size, Config.roundVal)
                                # Recalculate quantity with new stop_size
                                qty = risk_amount / stop_size
                                qty = int(math.ceil(qty))  # Round UP
                                if qty <= 0:
                                    qty = 1
                                logging.info("Custom extended hours (non-Custom/non-ATR stop loss): Recalculated entry_price=%s (from bar high/low), stop_loss_price=%s, stop_size=%s, quantity=%s", entry_price, stop_loss_price, stop_size, qty)
                            except Exception as e:
                                logging.error("Error recalculating stop loss for Custom trade type in extended hours: %s", e)
                                logging.error("Traceback: %s", traceback.format_exc())
                                return

                        # For non-EntryBar stop loss types, the entry stop price is still the custom entry (entry_price),
                        # but stop_size was derived from the bar/stop logic above.
                        entry_stop_price = round(entry_price, Config.roundVal)

                # Calculate entry limit price: entry_stop_price ± 0.5 * stop_size
                entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                if buySellType == 'BUY':
                    entry_limit_price = round(entry_stop_price + entry_limit_offset, Config.roundVal)
                else:  # SELL
                    entry_limit_price = round(entry_stop_price - entry_limit_offset, Config.roundVal)
                
                logging.info(
                    "Extended hours Custom: Entry STP LMT - Stop=%s, Limit=%s, stop_size=%s, "
                    "stopLoss=%s, symbol=%s, action=%s",
                    entry_stop_price, entry_limit_price, stop_size, stopLoss, symbol, buySellType
                )
                
                entry_order = Order(
                    orderId=parent_order_id,
                    orderType="STP LMT",
                    action=buySellType,
                    totalQuantity=qty,
                    auxPrice=entry_stop_price,  # Stop trigger price (custom entry)
                    lmtPrice=entry_limit_price,  # Limit price
                    tif=tif,
                    transmit=True  # Transmit immediately - TP/SL will be sent after fill
                )
            else:
                # For other trade types in extended hours: Use STP LMT order
                # Calculate entry limit price: entry_price ± 0.5 * stop_size
                entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                if buySellType == 'BUY':
                    entry_limit_price = round(entry_price + entry_limit_offset, Config.roundVal)
                else:  # SELL
                    entry_limit_price = round(entry_price - entry_limit_offset, Config.roundVal)
                
                logging.info(f"Extended hours Stop Order: Entry STP LMT - Stop={entry_price}, Limit={entry_limit_price}, stop_size={stop_size}, stopLoss={stopLoss}, symbol={symbol}, action={buySellType}")
                
                # For SELL stop orders: Validate that stop price is below current market price
                if buySellType == 'SELL':
                    current_price = float(histData.get('close', entry_price)) if histData else entry_price
                    if entry_price >= current_price:
                        logging.warning(f"SELL stop order: Stop price ({entry_price}) should be below current price ({current_price}) to trigger. Order may not trigger until price falls.")
            
            entry_order = Order(
                orderId=parent_order_id,
                orderType="STP LMT" if is_extended else "STP",
                action=buySellType,
                totalQuantity=qty,
                auxPrice=entry_price,  # Stop trigger price
                lmtPrice=entry_limit_price if is_extended else None,  # Limit price for extended hours
                tif=tif,
                transmit=True  # Transmit immediately - TP/SL will be sent after fill
            )
            
            # Place ONLY entry order - TP and SL will be sent after fill via sendTpAndSl()
            # Retry if duplicate order ID error occurs
            max_retries = 3
            retry_count = 0
            entry_response = None
            while retry_count < max_retries:
                try:
                    entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
                    if entry_response and entry_response.orderStatus.status != 'Cancelled':
                        break  # Success
                    elif entry_response and 'Duplicate order id' in str(entry_response.orderStatus):
                        # Duplicate order ID - get new ID and retry
                        logging.warning(f"Duplicate order ID {parent_order_id}, getting new ID and retrying...")
                        parent_order_id = connection.get_next_order_id()
                        entry_order.orderId = parent_order_id
                        retry_count += 1
                        await asyncio.sleep(0.1)  # Small delay before retry
                    else:
                        break  # Other error, don't retry
                except Exception as e:
                    if 'Duplicate order id' in str(e) or '103' in str(e):
                        logging.warning(f"Duplicate order ID error: {e}, getting new ID and retrying...")
                        parent_order_id = connection.get_next_order_id()
                        entry_order.orderId = parent_order_id
                        retry_count += 1
                        await asyncio.sleep(0.1)  # Small delay before retry
                    else:
                        raise  # Re-raise other exceptions
            
            if entry_response is None or entry_response.orderStatus.status == 'Cancelled':
                logging.error(f"Failed to place Stop Order after {max_retries} retries. Order may have duplicate ID issue.")
                return
            
            # Determine order type for StatusUpdate (STP LMT for extended hours, STP for regular hours)
            order_type = 'STP LMT' if is_extended else 'STP'
            StatusUpdate(entry_response, 'Entry', contract, order_type, buySellType, qty, histData, entry_price, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                         breakEven, outsideRth, False, entry_points)
            
            # Store stop_size in orderStatusData for use in sendStopLoss
            if int(entry_response.order.orderId) in Config.orderStatusData:
                Config.orderStatusData[int(entry_response.order.orderId)]['stopSize'] = stop_size
                if is_extended and entry_limit_price is not None:
                    Config.orderStatusData[int(entry_response.order.orderId)]['entryLimitPrice'] = entry_limit_price
                logging.info(f"Stored stop_size={stop_size} in orderStatusData for order {entry_response.order.orderId}")
            
            logging.info("Extended hours: Entry %s order placed. TP (LMT) and SL (STP LMT) will be sent automatically after entry fills.", order_type)
        else:
            # Regular market hours: Send bracket orders (Entry STP, TP LMT, SL STP)
            # Generate separate unique order IDs for each order in the bracket
            tp_order_id = connection.get_next_order_id()
            sl_order_id = connection.get_next_order_id()
            
            logging.info("Generated unique order IDs for bracket: entry=%s, tp=%s, sl=%s", 
                        parent_order_id, tp_order_id, sl_order_id)
            
            # For SELL stop orders: Validate that stop price is below current market price
            # SELL stop orders trigger when price goes DOWN to or below the stop price
            if buySellType == 'SELL':
                current_price = float(histData.get('close', entry_price)) if histData else entry_price
                if entry_price >= current_price:
                    logging.warning(f"RTH SELL stop order: Stop price ({entry_price}) should be below current price ({current_price}) to trigger. Order may not trigger until price falls.")
            
            # Entry order (Stop order)
            entry_order = Order(
                orderId=parent_order_id,
                orderType="STP",
                action=buySellType,
                totalQuantity=qty,
                auxPrice=entry_price,
                tif=tif,
                transmit=False  # Don't transmit until all orders are ready
            )
            
            # Take Profit order (Limit)
            tp_order = Order(
                orderId=tp_order_id,
                orderType="LMT",
                action="SELL" if buySellType == "BUY" else "BUY",
                totalQuantity=qty,
                lmtPrice=tp_price,
                parentId=parent_order_id,
                transmit=False
            )
            
            # Stop Loss order (Stop market for regular hours)
            sl_order = Order(
                orderId=sl_order_id,
                orderType="STP",
                action="SELL" if buySellType == "BUY" else "BUY",
                totalQuantity=qty,
                auxPrice=round(stop_loss_price, Config.roundVal),  # Stop price
                parentId=parent_order_id,
                transmit=True  # Last order transmits all
            )
            
            # Place all bracket orders
            # For bracket orders: Entry (transmit=False), TP (transmit=False), SL (transmit=True)
            # The last order (SL) with transmit=True will transmit the entire bracket
            logging.info("RTH Stop Order: Placing bracket orders - Entry (STP, auxPrice=%s, transmit=False), TP (LMT, lmtPrice=%s, transmit=False), SL (STP, auxPrice=%s, transmit=True)", 
                         entry_price, tp_price, stop_loss_price)
            
            entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
            logging.info("RTH Stop Order: Entry order placed - orderId=%s, status=%s", 
                         entry_response.order.orderId, entry_response.orderStatus.status)
            StatusUpdate(entry_response, 'Entry', contract, 'STP', buySellType, qty, histData, entry_price, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                         breakEven, outsideRth, False, entry_points)
            
            tp_response = connection.placeTrade(contract=contract, order=tp_order, outsideRth=outsideRth)
            logging.info("RTH Stop Order: TP order placed - orderId=%s, status=%s, parentId=%s", 
                         tp_response.order.orderId, tp_response.orderStatus.status, tp_response.order.parentId)
            StatusUpdate(tp_response, 'TakeProfit', contract, 'LMT', buySellType, qty, histData, entry_price, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                         breakEven, outsideRth)
            
            sl_response = connection.placeTrade(contract=contract, order=sl_order, outsideRth=outsideRth)
            logging.info("RTH Stop Order: SL order placed - orderId=%s, status=%s, parentId=%s, transmit=%s (should transmit entire bracket)", 
                         sl_response.order.orderId, sl_response.orderStatus.status, sl_response.order.parentId, sl_response.order.transmit)
            StatusUpdate(sl_response, 'StopLoss', contract, 'STP', buySellType, qty, histData, entry_price, symbol,
                         timeFrame, profit, stopLoss, risk, Config.orderStatusData.get(int(entry_response.order.orderId)), tif, barType, buySellType, atrPercentage, slValue,
                         breakEven, outsideRth)
            
            # Store TP/SL prices in entry order's orderStatusData for option trading
            entry_order_id = int(entry_response.order.orderId)
            if entry_order_id in Config.orderStatusData:
                Config.orderStatusData[entry_order_id]['tp_price'] = tp_price
                Config.orderStatusData[entry_order_id]['stop_loss_price'] = stop_loss_price
                Config.orderStatusData[entry_order_id]['tp_order_id'] = int(tp_response.order.orderId)
                Config.orderStatusData[entry_order_id]['sl_order_id'] = int(sl_response.order.orderId)
                logging.info("RTH Stop Order: Stored TP/SL prices in entry orderStatusData: entry=%s, tp=%s, sl=%s", 
                            entry_order_id, tp_price, stop_loss_price)
            
            logging.info("Regular market: Bracket orders placed for %s: trigger=%s, tp=%s, sl=%s, quantity=%s", 
                         symbol, entry_price, tp_price, stop_loss_price, qty)
            
            # For SELL stop orders: Log warning if stop price might not trigger
            if buySellType == 'SELL':
                current_price = float(histData.get('close', entry_price)) if histData else entry_price
                if entry_price < current_price:
                    logging.info("RTH SELL stop order: Stop price (%s) is below current price (%s). Order will trigger when price falls to or below %s", 
                                entry_price, current_price, entry_price)
                else:
                    logging.warning("RTH SELL stop order: Stop price (%s) is at or above current price (%s). Order may not trigger until price falls below %s", 
                                   entry_price, current_price, entry_price)
    except Exception as e:
        logging.error("error in manual stop order %s", e)
        traceback.print_exc()

async def rbb_loop_run(connection,key,entry_order):
    order = entry_order
    logging.info(f"RBB_LOOP_RUN: Starting loop for orderId={order.orderId}, key={key}")
    iteration_count = 0
    while True:
        try:
            iteration_count += 1
            if iteration_count % 10 == 0:  # Log every 10 iterations
                logging.info(f"RBB_LOOP_RUN: Still running, iteration={iteration_count}, orderId={order.orderId}")
            await asyncio.sleep(1)
            # for entry_key, entry_value in list(Config.rbbb_dict.items()):
            # Check if order exists in orderStatusData
            if order.orderId not in Config.orderStatusData:
                logging.warning(f"rbb_loop_run: Order {order.orderId} not found in orderStatusData, waiting...")
                await asyncio.sleep(1)
                continue
            
            old_order = Config.orderStatusData.get(order.orderId)
            if old_order is None:
                logging.warning(f"rbb_loop_run: Order {order.orderId} data is None, waiting...")
                await asyncio.sleep(1)
                continue
                
            logging.info("old order rbb %s ",old_order)
            
            # Safety check: Only update if this is RBB, not RB
            bar_type = old_order.get('barType', '')
            # RBB is at entryTradeType[5] (after adding Conditional Order, indices shifted)
            if bar_type != Config.entryTradeType[5]:  # Not RBB
                logging.info("rbb_loop_run: barType=%s is not RBB (%s), exiting loop to prevent updates", 
                            bar_type, Config.entryTradeType[5])
                break
        
            sleep_time = getTimeInterval(old_order['timeFrame'], datetime.datetime.now())
            await asyncio.sleep(sleep_time)
            
            # Refresh order data after sleep
            if order.orderId not in Config.orderStatusData:
                logging.warning(f"rbb_loop_run: Order {order.orderId} not found in orderStatusData after sleep, waiting...")
                await asyncio.sleep(1)
                continue
                
            old_order = Config.orderStatusData.get(order.orderId)
            if old_order is None:
                logging.warning(f"rbb_loop_run: Order {order.orderId} data is None after sleep, waiting...")
                await asyncio.sleep(1)
                continue
            
            # Check if entry is filled - place protection order for pre-market/after-hours
            if old_order['status'] == 'Filled' and old_order.get('outsideRth', False):
                is_extended, session = _is_extended_outside_rth(old_order.get('outsideRth', False))
                if is_extended:
                    # Check if protection order already placed
                    if not old_order.get('protection_placed', False):
                        logging.info("RBBB Entry filled in %s, placing protection stop limit order", session)
                        
                        # Get historical data for stop loss calculation
                        histData = old_order.get('histData', {})
                        if histData:
                            # Check if this is HOD/LOD stop loss
                            stop_loss_type = old_order.get('stopLoss', '')
                            is_lod_hod = (stop_loss_type == Config.stopLoss[3]) or (stop_loss_type == Config.stopLoss[4])  # HOD or LOD
                            
                            if is_lod_hod:
                                # For HOD/LOD: Get LOD/HOD values for stop loss calculation
                                lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, old_order['contract'], old_order['timeFrame'])
                                
                                if lod is not None and hod is not None:
                                    # For HOD/LOD in extended hours: Protection order uses same logic as sendTpSlBuy/sendTpSlSell
                                    # Extended hours: Stop loss = Entry bar High (BUY) or Entry bar Low - 0.01 (SELL)
                                    # Limit price uses HOD/LOD ± 2×stop_size
                                    if old_order['action'] == 'BUY':
                                        # For BUY (LONG): Stop loss at entry bar Low - 0.01 (same as sendTpSlSell)
                                        stop_price = round(float(histData.get('low', 0)) - 0.01, Config.roundVal)
                                        protection_action = 'SELL'
                                        
                                        # Calculate stop_size = |bar_high - LOD|
                                        bar_high = float(histData.get('high', stop_price))
                                        stop_size = abs(bar_high - lod)
                                        if stop_size <= 0:
                                            stop_size = (float(histData.get('high', 0)) - float(histData.get('low', 0))) + Config.add002
                                        stop_size = round(stop_size, Config.roundVal)
                                        
                                        # Limit price = LOD + 2×stop_size (same as sendStopLoss)
                                        limit_offset = round(stop_size * 2.0, Config.roundVal)
                                        limit_price = round(lod + limit_offset, Config.roundVal)
                                        
                                        logging.info(f"RBBB Protection order HOD/LOD (LONG): {protection_action} STP LMT, Stop={stop_price} (Entry bar Low - 0.01), Limit={limit_price} (LOD + 2×stop_size={limit_offset}), LOD={lod}, stop_size={stop_size}")
                                    else:  # SELL
                                        # For SELL (SHORT): Stop loss at entry bar High (same as sendTpSlBuy)
                                        stop_price = round(float(histData.get('high', 0)), Config.roundVal)
                                        protection_action = 'BUY'
                                        
                                        # Calculate stop_size = |bar_low - HOD|
                                        bar_low = float(histData.get('low', stop_price))
                                        stop_size = abs(bar_low - hod)
                                        if stop_size <= 0:
                                            stop_size = (float(histData.get('high', 0)) - float(histData.get('low', 0))) + Config.add002
                                        stop_size = round(stop_size, Config.roundVal)
                                        
                                        # Limit price = HOD - 2×stop_size (same as sendStopLoss)
                                        limit_offset = round(stop_size * 2.0, Config.roundVal)
                                        limit_price = round(hod - limit_offset, Config.roundVal)
                                        
                                        logging.info(f"RBBB Protection order HOD/LOD (SHORT): {protection_action} STP LMT, Stop={stop_price} (Entry bar High), Limit={limit_price} (HOD - 2×stop_size={limit_offset}), HOD={hod}, stop_size={stop_size}")
                                else:
                                    # Fallback to regular logic if HOD/LOD unavailable
                                    logging.warning("RBBB Protection order: HOD/LOD unavailable, using fallback logic")
                                    stop_size, entry_offset, protection_offset = _calculate_stop_limit_offsets(histData)
                                    if old_order['action'] == 'BUY':
                                        stop_price = histData['low'] - 0.01 - protection_offset
                                        protection_action = 'SELL'
                                    else:
                                        stop_price = histData['high'] + 0.01 + protection_offset
                                        protection_action = 'BUY'
                                    
                                    if protection_action == 'SELL':
                                        limit_price = stop_price - entry_offset
                                    else:
                                        limit_price = stop_price + entry_offset
                                    
                                    limit_price = round(limit_price, Config.roundVal)
                                    stop_price = round(stop_price, Config.roundVal)
                                    logging.info(f"RBBB Protection order (fallback): {protection_action} STP LMT, Stop={stop_price}, Limit={limit_price}")
                            else:
                                # Regular stop loss: Use existing logic
                                stop_size, entry_offset, protection_offset = _calculate_stop_limit_offsets(histData)
                            # Calculate stop loss price (opposite of entry direction)
                            if old_order['action'] == 'BUY':
                                # For BUY: Stop loss at bar low minus 2x stop size
                                stop_price = histData['low'] - 0.01 - protection_offset
                                protection_action = 'SELL'
                            else:
                                # For SELL: Stop loss at bar high plus 2x stop size
                                stop_price = histData['high'] + 0.01 + protection_offset
                                protection_action = 'BUY'
                            
                            if protection_action == 'SELL':
                                # For SELL protection: limit = stop - 50% stop size
                                limit_price = stop_price - entry_offset
                            else:
                                # For BUY protection: limit = stop + 50% stop size
                                limit_price = stop_price + entry_offset
                            
                            limit_price = round(limit_price, Config.roundVal)
                            stop_price = round(stop_price, Config.roundVal)
                            
                            logging.info(f"RBBB Protection order (regular): {protection_action} STP LMT, Stop={stop_price}, Limit={limit_price}, Stop size={stop_size}, Entry offset={entry_offset}, Protection offset={protection_offset}")
                            
                            # Place protection order
                            protection_order = Order(
                                orderType="STP LMT",
                                action=protection_action,
                                totalQuantity=old_order['totalQuantity'],
                                tif='DAY',
                                auxPrice=stop_price,
                                lmtPrice=limit_price
                            )
                            
                            protection_response = connection.placeTrade(
                                contract=old_order['contract'],
                                order=protection_order,
                                outsideRth=True
                            )
                            
                            logging.info("RBBB Protection order placed: %s", protection_response)
                            
                            # Mark protection as placed
                            old_order['protection_placed'] = True
                            old_order['protection_order_id'] = protection_response.order.orderId
                            Config.orderStatusData[order.orderId] = old_order
                
                break  # Exit loop after entry is filled
            
            if (old_order['status'] != 'Filled' and old_order['status'] != 'Cancelled' and old_order['status'] != 'Inactive'):
                logging.info("RBBB stp updation start  %s %s", order.orderId , old_order['status'].upper()  )

                # Check if we're in RTH (for bracket order updates) or extended hours
                is_extended, session = _is_extended_outside_rth(old_order.get('outsideRth', False))
                
                # Check if this is HOD/LOD stop loss
                stop_loss_type = old_order.get('stopLoss', '')
                is_lod_hod = (stop_loss_type == Config.stopLoss[3]) or (stop_loss_type == Config.stopLoss[4])  # HOD or LOD
                
                if is_lod_hod:
                    # For HOD/LOD with RBB: Entry stop price uses EntryBar logic (bar_high/low ± entry_points ± 0.01)
                    # LOD/HOD is ONLY used for stop loss price, NOT for entry stop price
                    # Get current LOD/HOD (for stop loss calculation only)
                    lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, old_order['contract'], old_order['timeFrame'])
                    if lod is None or hod is None:
                        logging.warning("RBBB HOD/LOD: Could not get current LOD/HOD, skipping update")
                        await asyncio.sleep(1)
                        continue
                    
                    # Get current entry stop price
                    current_entry_stop = round(float(order.auxPrice), Config.roundVal)
                    
                    # Get current bar data for entry stop price calculation
                    histData = connection.rbb_entry_historical_data(old_order['contract'], old_order['timeFrame'], getRecentChartTime(old_order['timeFrame']))
                    if not histData or len(histData) == 0:
                        logging.warning("RBBB HOD/LOD: No bar data available, skipping entry stop price update")
                        await asyncio.sleep(1)
                        continue
                    
                    # Check if a new bar has closed by comparing datetimes (for continuous updates)
                    last_processed_histData = old_order.get('histData', {})
                    last_processed_datetime = last_processed_histData.get('dateTime') if last_processed_histData else None
                    current_bar_datetime = histData.get('dateTime')
                    
                    should_update = False
                    entry_points = float(old_order.get('entry_points', '0'))
                    
                    # Calculate what the new entry price should be using EntryBar logic
                    if old_order['userBuySell'] == 'BUY':
                        new_aux_price_calc = round(float(histData['high']) - entry_points + 0.01, Config.roundVal)
                    else:  # SELL
                        new_aux_price_calc = round(float(histData['low']) + entry_points - 0.01, Config.roundVal)
                    
                    if last_processed_datetime and current_bar_datetime:
                        # Compare datetimes - convert to comparable format if needed
                        try:
                            # Handle datetime objects
                            if hasattr(last_processed_datetime, 'replace'):
                                last_dt = last_processed_datetime.replace(microsecond=0)
                            else:
                                last_dt = last_processed_datetime
                            
                            if hasattr(current_bar_datetime, 'replace'):
                                current_dt = current_bar_datetime.replace(microsecond=0)
                            else:
                                current_dt = current_bar_datetime
                            
                            if last_dt != current_dt:
                                # New bar closed, update entry order
                                should_update = True
                                logging.info(f"RBBB HOD/LOD: New bar closed (last=%s, new=%s), updating entry order", last_processed_datetime, current_bar_datetime)
                            else:
                                # Same bar datetime, but check if entry price needs updating anyway
                                if abs(new_aux_price_calc - current_entry_stop) > 0.01:
                                    # Entry price changed even though same bar (bar high/low might have changed)
                                    should_update = True
                                    logging.info(f"RBBB HOD/LOD: Same bar datetime but entry price changed ({current_entry_stop} -> {new_aux_price_calc}), updating")
                                else:
                                    # Same bar, no update needed
                                    logging.info(f"RBBB HOD/LOD: Same bar (datetime=%s), no update needed", current_bar_datetime)
                                    await asyncio.sleep(1)
                                    continue
                        except Exception as e:
                            logging.warning(f"RBBB HOD/LOD: Error comparing datetimes: {e}, using price-based check")
                            # Fall through to price-based check
                            if abs(new_aux_price_calc - current_entry_stop) > 0.01:
                                should_update = True
                                logging.info(f"RBBB HOD/LOD: Entry price changed ({current_entry_stop} -> {new_aux_price_calc}), updating")
                            else:
                                logging.info(f"RBBB HOD/LOD: No update needed (entry stop={current_entry_stop}, new entry stop={new_aux_price_calc})")
                                await asyncio.sleep(1)
                                continue
                    else:
                        # Missing datetime data, use price-based check
                        logging.info(f"RBBB HOD/LOD: Missing datetime data (last=%s, current=%s), using price-based check", last_processed_datetime, current_bar_datetime)
                        if not last_processed_datetime:
                            # First iteration: always update to current bar's high/low
                            should_update = True
                            logging.info(f"RBBB HOD/LOD: First iteration (no previous histData), updating entry order to current bar (datetime=%s)", current_bar_datetime)
                        elif abs(new_aux_price_calc - current_entry_stop) > 0.01:
                            # Entry price changed, update
                            should_update = True
                            logging.info(f"RBBB HOD/LOD {old_order['userBuySell']}: Entry stop changed ({current_entry_stop} -> {new_aux_price_calc}), updating")
                        else:
                            # No change needed
                            logging.info(f"RBBB HOD/LOD: No update needed (entry stop={current_entry_stop}, new entry stop={new_aux_price_calc})")
                            await asyncio.sleep(1)
                            continue
                    
                    # Only proceed with update if should_update is True
                    if not should_update:
                        logging.warning(f"RBBB HOD/LOD: should_update is False, skipping update")
                        await asyncio.sleep(1)
                        continue
                    
                    # Determine new entry stop price based on EntryBar logic (bar_high/low ± entry_points)
                    # Use the already calculated new_aux_price_calc from above
                    new_aux_price = new_aux_price_calc
                    
                    logging.info(f"RBBB HOD/LOD {old_order['userBuySell']}: Entry stop={current_entry_stop}, New entry stop={new_aux_price} (bar_high/low ± entry_points ± 0.01, EntryBar logic), LOD={lod}, HOD={hod} (for stop loss only), entry_points={entry_points}")
                    
                    # Calculate stop_size for limit price: |bar_high/low - HOD/LOD|
                    if histData and len(histData) > 0:
                        # For HOD/LOD entry orders: stop_size = |bar_high/low - HOD/LOD|
                        # Auto-detect: BUY uses LOD, SELL uses HOD
                        if old_order['userBuySell'] == 'BUY':
                            # For BUY: use bar_high and LOD
                            bar_price = float(histData['high'])
                            stop_size = abs(bar_price - lod)
                        else:  # SELL
                            # For SELL: use bar_low and HOD
                            bar_price = float(histData['low'])
                            stop_size = abs(bar_price - hod)
                        
                        stop_size = round(stop_size, Config.roundVal)
                        if stop_size <= 0:
                            # Fallback: use bar range
                            stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                            stop_size = round(stop_size, Config.roundVal)
                            logging.warning(f"RBBB HOD/LOD: Invalid stop_size, using bar range: {stop_size}")
                    else:
                        # Fallback: use difference or default
                        stop_size = abs(new_aux_price - current_entry_stop)  # Fallback: use difference
                        if stop_size <= 0:
                            stop_size = 0.5  # Default fallback
                    
                    aux_price = new_aux_price
                    logging.info(f"RBBB HOD/LOD: Updating entry stop price from {current_entry_stop} to {aux_price} (new LOD/HOD), stop_size={stop_size} (bar_price={bar_price if histData and len(histData) > 0 else 'N/A'}, for limit price calculation)")
                    # Store old orderId before updating order
                    old_orderId = order.orderId
                else:
                    # Regular stop loss: Update based on new bar's high/low
                    newChartTime = getRecentChartTime(old_order['timeFrame'])
                    histData = connection.rbb_entry_historical_data(old_order['contract'], old_order['timeFrame'], newChartTime)
                    if (histData is None or len(histData) == 0):
                        logging.info("Loop RBB Chart Data is Not Comming for %s contract  and for %s time", old_order['contract'],
                                     newChartTime)
                        await asyncio.sleep(1)
                        continue
                    
                    # Get the last processed bar's datetime from orderStatusData (updated after each bar)
                    # This allows continuous updates on every new bar, not just the first one
                    last_processed_histData = old_order.get('histData', {})
                    if not last_processed_histData:
                        # No previous data, use current bar as baseline
                        logging.info("RBBB: No previous histData found, using current bar as baseline")
                        last_processed_histData = histData
                    
                    last_processed_datetime = last_processed_histData.get('dateTime')
                    current_bar_datetime = histData.get('dateTime')
                    
                    # Check if a new bar has closed (different datetime)
                    if last_processed_datetime and current_bar_datetime:
                        if last_processed_datetime == current_bar_datetime:
                            # Same bar, no update needed
                            logging.info("RBBB: Same bar (datetime=%s), skipping update", current_bar_datetime)
                            await asyncio.sleep(1)
                            continue
                        else:
                            # New bar closed, update to new bar's high/low
                            logging.info("RBBB: New bar closed (last=%s, new=%s), updating entry order", last_processed_datetime, current_bar_datetime)
                    else:
                        # Can't compare datetimes, still update to be safe
                        logging.info("RBBB: Cannot compare bar datetimes, updating entry order anyway")
                    
                    # Calculate new stop price based on new bar's high/low
                    aux_price = 0
                    if old_order['userBuySell'] == 'BUY':
                        aux_price = round(float(histData['high']) + 0.01, Config.roundVal)
                        logging.info("RBRR auxprice high for %s (new bar high=%s)", aux_price, histData['high'])
                    else:
                        aux_price = round(float(histData['low']) - 0.01, Config.roundVal)
                        logging.info("RBRR auxprice low for %s (new bar low=%s)", aux_price, histData['low'])
                    
                    # Calculate stop_size for limit price
                    _, entry_offset, _ = _calculate_stop_limit_offsets(histData)
                    stop_size = entry_offset * 2  # entry_offset is 0.5 * stop_size, so stop_size = entry_offset * 2
                    
                    logging.info("RBBB going to update entry order for newprice %s old_order %s", aux_price,order)
                    order.auxPrice = aux_price
                    old_orderId=order.orderId
                    
                # For extended hours: Use stop-limit order (for both HOD/LOD and regular stop loss)
                # For RTH: Use stop order (for both HOD/LOD and regular stop loss)
                    is_extended, _ = _is_extended_outside_rth(old_order.get('outsideRth', False))
                    if is_extended:
                        # Calculate limit price for stop limit order using 0.5 × stop_size
                        entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                        
                        # Ensure minimum offset of 0.01 to avoid limit = stop
                        min_limit_offset = 0.01
                        if entry_limit_offset < min_limit_offset:
                            entry_limit_offset = min_limit_offset
                            logging.warning(f"RBBB Extended hours: stop_size too small ({stop_size}), using minimum limit offset={min_limit_offset}")
                        
                        if is_lod_hod and lod is not None and hod is not None:
                            # For HOD/LOD in extended hours: Limit price uses HOD/LOD, not aux_price (same as initial placement)
                            # Auto-detect: BUY uses LOD, SELL uses HOD
                            if old_order['userBuySell'] == 'BUY':
                                # For BUY: Limit = LOD + 0.5 × stop_size
                                limit_price = lod + entry_limit_offset
                            else:  # SELL
                                # For SELL: Limit = HOD - 0.5 × stop_size
                                limit_price = hod - entry_limit_offset
                            limit_price = round(limit_price, Config.roundVal)
                            logging.info(f"RBBB Extended hours HOD/LOD Update: STP LMT, Stop={aux_price} (bar_high/low ± entry_points ± 0.01, EntryBar logic), Limit={limit_price} (LOD/HOD ± 0.5×stop_size={entry_limit_offset}), LOD={lod}, HOD={hod}, stop_size={stop_size}")
                        else:
                            # Regular stop loss: Limit = Stop ± 0.5 × stop_size
                            if old_order['userBuySell'] == 'BUY':
                                limit_price = aux_price + entry_limit_offset
                            else:
                                limit_price = aux_price - entry_limit_offset
                            limit_price = round(limit_price, Config.roundVal)
                            logging.info(f"RBBB Extended hours Update: STP LMT, Stop={aux_price}, Limit={limit_price}")
                        new_order = Order(orderType="STP LMT", action=order.action, totalQuantity=order.totalQuantity, 
                                        tif='DAY', auxPrice=aux_price, lmtPrice=limit_price)
                    else:
                        # RTH: Update only entry order (TP/SL are sent separately after fill)
                        if is_lod_hod:
                            logging.info(f"RBBB RTH HOD/LOD Update: STP, Stop={aux_price} (bar_high/low ± entry_points ± 0.01, EntryBar logic)")
                        else:
                            logging.info(f"RBBB RTH Update: STP, Stop={aux_price}")
                        new_order = Order(orderType="STP", action=order.action, totalQuantity=order.totalQuantity,  
                                        tif='DAY', auxPrice=aux_price)
                    
                    connection.cancelTrade(order)
                    response = connection.placeTrade(contract=old_order['contract'], order=new_order, 
                                                   outsideRth=old_order.get('outsideRth', False))
                    logging.info("RBBB  response of updating entry order %s ",response)
                    order =response.order
                    if(Config.orderStatusData.get(old_orderId) != None ):
                        d=Config.orderStatusData.get(old_orderId)
                        # Update histData to current bar so next iteration can detect the next new bar
                        # This enables continuous updates on every bar change (for both HOD/LOD and regular stop loss)
                        d['histData'] = histData
                        d['orderId']= int(response.order.orderId)
                        d['status']= response.orderStatus.status
                        d['lastPrice'] = round(aux_price, Config.roundVal)
                        Config.orderStatusData.update({ order.orderId:d })
                        old_order = d  # Update old_order for next iteration
                        logging.info("RBBB: Updated orderStatusData with new bar data (datetime=%s) for continuous entry order updates", histData.get('dateTime'))
                        
                        # Update option orders if option trading is enabled for this RBB trade
                        try:
                            from OptionTrading import updateOptionOrdersForRBB
                            # Calculate new stop loss price for option update
                            new_sl_price = None
                            if 'stop_loss_price' in d:
                                new_sl_price = d['stop_loss_price']
                            # Update option entry order to match new stock entry price
                            updateOptionOrdersForRBB(connection, order.orderId, round(aux_price, Config.roundVal), new_sl_price)
                        except Exception as e:
                            logging.error("RBBB: Error updating option orders: %s", e)
            else:
                break

        except KeyError as e:
            # Order not found - might be temporary, wait and retry
            logging.warning(f"RBB_LOOP_RUN: KeyError - Order data not found: {e}, orderId={order.orderId if hasattr(order, 'orderId') else 'unknown'}, waiting and retrying...")
            await asyncio.sleep(5)  # Wait longer before retry
            continue
        except Exception as e:
            error_msg = f"RBB_LOOP_RUN: Error in rbb loop iteration: {e}"
            logging.error(error_msg)
            logging.error(traceback.format_exc())
            # Don't break on error - continue loop to keep monitoring
            # Only break if it's a critical error that prevents continuation
            if "orderId" in str(e).lower() or "orderStatusData" in str(e).lower():
                logging.error(f"RBB_LOOP_RUN: Critical error related to order data, exiting loop")
            break
            await asyncio.sleep(5)  # Wait before retrying
            continue
        await asyncio.sleep(1)
    
    logging.warning(f"RBB_LOOP_RUN: Loop exited for orderId={order.orderId if hasattr(order, 'orderId') else 'unknown'}")
def pbe_result(user_side,price,hist_data,reverse = False):
    try:
        highest_row,lowest_row = None,None
        highest_price, lowest_price = 0, 0
        current_candel= hist_data[len(hist_data)-1]
        current_candel_price= hist_data[len(hist_data)-1]['close']
        if reverse:        # here we are getting lowest and highest point
            logging.info(f"we will check reverse loop {price} data  {hist_data}")
            for data in range(len(hist_data)-1 , -1, -1 ):
                if (lowest_price == 0 or lowest_price >= hist_data.get(data)['low']):

                    lowest_price = hist_data.get(data)['low']
                    lowest_row = hist_data.get(data)

                if (highest_price == 0 or highest_price <= hist_data.get(data)['high']):
                    highest_price = hist_data.get(data)['high']
                    highest_row = hist_data.get(data)

            if user_side.upper() == 'BUY':
                logging.info(
                    f"pbe2sec lowest row {lowest_row} and price {current_candel_price} {hist_data[len(hist_data) - 1]}")
                return "BUY", lowest_row
            else:
                logging.info(
                    f"pbe2sec highest row {highest_row} and price {current_candel_price}   {hist_data[len(hist_data) - 1]}")
                return "SELL", highest_row
        else:
            logging.info(f"we will check normal loop {price} data  {hist_data}")
            for data in range(0, (len(hist_data) ) ):
                if(lowest_price == 0  or  lowest_price > hist_data.get(data)['low'] ):
                    lowest_price = hist_data.get(data)['low']
                    lowest_row = hist_data.get(data)
                if (highest_price == 0 or highest_price < hist_data.get(data)['high']):
                    highest_price = hist_data.get(data)['high']
                    highest_row = hist_data.get(data)
            if user_side.upper() == 'BUY':
                if(lowest_row != None and current_candel['high'] > lowest_row['high'] ):
                    #  we are assuming prev is low so currently we are checking high....
                    logging.info(f"pbe2first lowest row {lowest_row} and price {current_candel_price} {hist_data[len(hist_data)-1]}")
                    return "BUY" , lowest_row
            else:
                if(highest_row != None and current_candel['low'] < highest_row['low'] ):
                    logging.info(f"pbe2first highest row {highest_row} and price {current_candel_price}   {hist_data[len(hist_data)-1]}")
                    return "SELL" , highest_row
    except Exception as e:
        logging.error(f"error in pbeCheck {traceback.format_exc()}")
    return  "" , None

def pbe1_result(price,hist_data,reverse = False):
    try:
        highest_row,lowest_row = None,None
        highest_price, lowest_price = 0, 0
        current_candel_price= price['close']
        if reverse:        # here we are getting lowest and highest point
            logging.info(f"we will check reverse loop {price} data  {hist_data}")

        else:
            logging.info(f"we will check normal loop {price} data  {hist_data}")
            for data in range(0, (len(hist_data)) ):
                if(lowest_price == 0  or  lowest_price > hist_data.get(data)['low'] ):
                    lowest_price = hist_data.get(data)['low']
                    lowest_row = hist_data.get(data)
                if (highest_price == 0 or highest_price < hist_data.get(data)['high']):
                    highest_price = hist_data.get(data)['high']
                    highest_row = hist_data.get(data)
        if(lowest_row != None and price['high'] > lowest_row['high'] ):
            #  we are assuming prev is low so currently we are checking high....
            logging.info(f"pbe1 lowest row {lowest_row} and price {current_candel_price}")
            return "BUY" , lowest_row
        elif(highest_row != None and price['low'] < highest_row['low'] ):
            logging.info(f"pbe1 highest row {highest_row} and price {current_candel_price}")
            return "SELL" , highest_row
    except Exception as e:
        logging.error(f"error in pbeCheck {traceback.format_exc()}")
    return  "" , None

async def pull_back_PBe1(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue,breakEven,outsideRth,entry_points):
        logging.info("pull_back_PBe1 mkt pbe1 trade is sending. %s %s",Config.tradingTime,symbol)
        ibContract = getContract(symbol, None)
        # priceObj = subscribePrice(ibContract, connection)
        key = (symbol + str(datetime.datetime.now()))
        logging.info("Key for this trade is- %s ", key)
        chartTime = await get_first_chart_time(timeFrame , outsideRth)
        while True:
            try:
                # Skip time check for premarket/after-hours trading
                if not outsideRth:
                    # config_time = datetime.datetime.strptime(str(datetime.datetime.now().date())+" "+Config.tradingTime,'%Y-%m-%d %H:%M:%S')
                    dtime=str(datetime.datetime.now().date())+" "+Config.pull_back_PBe1_time
                    # dtime = config_time + datetime.timedelta(minutes=2)
                    if(datetime.datetime.now() < datetime.datetime.strptime(dtime,'%Y-%m-%d %H:%M:%S') ):
                        await asyncio.sleep(1)
                        continue
                logging.info("send trade loop is running..")
                histData = None
                tradeType = ""
                # logging.info("now we will get last three record (firstly we will calculate time can we get data or not)")
                # sleepTime = getSleepTime(timeFrame,outsideRth)
                # logging.info("we cant fetch last three data we are going to sleep %s",sleepTime)
                # await asyncio.sleep(sleepTime)
                # chartTime = getRecentChartTime(timeFrame)
                # logging.info("we will get chart data for %s time", chartTime)
                complete_bar_data = connection.pbe1_entry_historical_data(ibContract, timeFrame, chartTime)
                logging.info("RecentBar for PullBack E1 %s", complete_bar_data)
                if(len(complete_bar_data) ==0):
                    logging.info("last 1 record not found we will try after 1 sec.")
                    await  asyncio.sleep(1)
                    continue

                # logging.info("RecentBar for PullBack E1 %s ",recentBarData)
                tradeType = ""
                
                # Get the most recent CLOSED bar (previous bar, not the current forming bar)
                # Use the second-to-last bar to ensure it's definitely closed
                if len(complete_bar_data) >= 2:
                    # Get the second-to-last bar (most recent closed bar)
                    last_candel = complete_bar_data[len(complete_bar_data)-2]
                    logging.info("PBe1: Using second-to-last bar (index=%s) as most recent closed bar for initial entry", len(complete_bar_data)-2)
                else:
                    # Only one bar available, use it (might be the initial bar)
                    last_candel = complete_bar_data[len(complete_bar_data)-1]
                    logging.info("PBe1: Only one bar available, using it (index=0) for initial entry")
                
                logging.info("last candel found for for PullBack E1 %s ", last_candel)
                if (last_candel == None or len(last_candel) == 0 ):
                    logging.info("Last Price Not Found for %s contract for mkt order", ibContract)
                    await  asyncio.sleep(1)
                    continue
                lastPrice = last_candel['close']
                lastPrice = round(lastPrice, Config.roundVal)
                logging.info("Price found for market order %s for %s contract ", lastPrice, ibContract)
                histData = last_candel

                # PBe1: Use buySellType directly (no condition checking) - same as RBB
                tradeType = buySellType
                logging.info("PBe1: Using user-selected trade type %s (no condition checking) - same as RBB", tradeType)
                # connection.cancelTickData(ibContract)

                # candleData = connection.get_recent_close_price_data(ibContract, timeFrame, chartTime)
                # if( candleData == None or len(candleData) < 1):
                #     logging.info("candle data not found for %s", ibContract)
                #     await  asyncio.sleep(1)
                #     continue

                # PBe1: Entry stop price = bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL) - same as regular RBB
                # LOD/HOD is ONLY used for stop loss price, NOT for entry stop price
                # Get LOD/HOD first (for stop loss calculation only)
                lod, hod = _get_pbe1_lod_hod(connection, ibContract, timeFrame, tradeType)
                
                # Entry stop price is simple: bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL) - like regular RBB
                # Initialize entry_price to avoid UnboundLocalError
                entry_price = None
                if tradeType == 'BUY':
                    entry_price = float(last_candel.get('high', 0)) + 0.01
                    logging.info(f"PBe1 BUY: Setting entry stop price to bar_high + 0.01 = {entry_price} (like RBB, LOD={lod} is only for stop loss)")
                elif tradeType == 'SELL':
                    entry_price = float(last_candel.get('low', 0))
                    logging.info(f"PBe1 SELL: Setting entry stop price to bar_low = {entry_price} (prior bar low, HOD={hod} is only for stop loss)")
                else:
                    # Safety check: if tradeType is not BUY or SELL, log error and skip
                    logging.error(f"PBe1: Invalid tradeType '{tradeType}' after validation check. Expected BUY or SELL. Skipping this iteration.")
                    await asyncio.sleep(1)
                    continue
                
                if lod is None or lod == 0 or hod is None or hod == 0:
                    logging.warning("PBe1: Could not get LOD/HOD for stop loss calculation")
                
                entry_price = round(entry_price, Config.roundVal)
                
                # PBe1: Always calculate quantity from risk / stop_size (ignore user-provided quantity)
                # PBe1: Always use LOD (for long) or HOD (for short), regardless of stopLoss type
                # Note: stopLoss and slValue from UI are ignored - only HOD/LOD is used
                # Stop size = |bar_high/low - LOD/HOD|
                bar_high = float(last_candel.get('high', 0))
                bar_low = float(last_candel.get('low', 0))
                if tradeType == 'BUY':
                    # For BUY: stop_size = |bar_high - LOD|
                    stop_size = abs(bar_high - lod) if lod > 0 else (bar_high - bar_low) + Config.add002
                    stop_loss_price = lod
                else:  # SELL
                    # For SELL: stop_size = |bar_low - HOD|
                    stop_size = abs(bar_low - hod) if hod > 0 else (bar_high - bar_low) + Config.add002
                    stop_loss_price = hod
                
                stop_size = round(stop_size, Config.roundVal)
                stop_loss_price = round(stop_loss_price, Config.roundVal)
                
                if stop_size <= 0:
                    # Fallback: use bar range
                    stop_size = (bar_high - bar_low) + Config.add002
                    stop_size = round(stop_size, Config.roundVal)
                    logging.warning(f"PBe1: Invalid stop_size, using bar range: {stop_size}")
                
                logging.info(f"PBe1 stop_size calculation: bar_high={bar_high}, bar_low={bar_low}, LOD={lod}, HOD={hod}, entry_price={entry_price}, stop_loss_price={stop_loss_price}, stop_size={stop_size}")
                
                # Calculate quantity: qty = risk / stop_size
                risk_amount = _to_float(risk, 0)
                if risk_amount <= 0:
                    logging.warning("Invalid risk amount for PBe1: %s, using default quantity of 1", risk)
                    quantity = 1
                elif stop_size == 0 or stop_size < 0.01:
                    logging.warning("Stop size is zero or too small (%s) for PBe1, using default quantity of 1", stop_size)
                    quantity = 1
                else:
                    quantity = risk_amount / stop_size
                    quantity = int(round(quantity, 0))
                    if quantity <= 0:
                        quantity = 1
                logging.info(f"PBe1 quantity calculated: entry={entry_price}, stop_loss={stop_loss_price}, stop_size={stop_size}, risk={risk_amount}, quantity={quantity}")
                logging.info("Trade quantity found for market order %s for %s contract and candle data is %s and last bar data %s", quantity, ibContract,last_candel,last_candel)
                logging.info("everything found we are placing mkt trade")
                conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
                logging.info("main Entry Data , historical  data [%s] Recent Bar Data [%s] price is [ %s ] tradeType is [%s], TimeFrame [%s], configTime [%s] , quantity [%s], ibContract [%s]",Config.historicalData.get(key),last_candel,entry_price,tradeType,timeFrame,conf_trading_time,quantity,ibContract)


                # Store LOD/HOD in orderStatusData before placing order (will be used later)
                # We'll store it in the orderStatusData after StatusUpdate
                
                # Place entry order using sendEntryTrade (similar to RBB)
                # PBe1 uses RBB-like entry logic with HOD/LOD stop loss
                # Get the response from sendEntryTrade to start pbe1_loop_run directly (like RBB)
                response = sendEntryTrade(connection, ibContract, tradeType, quantity, last_candel, entry_price, symbol, timeFrame, profit, stopLoss, risk, tif, barType,buySellType,atrPercentage,slValue , breakEven,outsideRth)
                
                if response and hasattr(response, 'order') and response.order:
                    # Start pbe1_loop_run for continuous entry order updates (similar to RBB)
                    logging.info("PBe1: Starting pbe1_loop_run for continuous entry order updates with orderId=%s", response.order.orderId)
                    # Start as background task (don't await, let it run in background)
                    asyncio.ensure_future(pbe1_loop_run(connection, key, response.order))
                    logging.info("PBe1: pbe1_loop_run scheduled as background task")
                else:
                    # Fallback: search for order in orderStatusData if response not available
                    logging.warning("PBe1: Response not available from sendEntryTrade, searching orderStatusData as fallback")
                    await asyncio.sleep(0.2)
                    entry_order = None
                    logging.info(f"PBe1: Searching orderStatusData for entry order (symbol={symbol}, barType={barType})")
                    for order_id, order_data in Config.orderStatusData.items():
                        logging.info(f"PBe1: Checking orderId={order_id}, usersymbol={order_data.get('usersymbol')}, barType={order_data.get('barType')}, ordType={order_data.get('ordType')}, status={order_data.get('status')}")
                        if (order_data.get('usersymbol') == symbol and 
                            order_data.get('barType') == barType and
                            order_data.get('ordType') == 'Entry' and
                            order_data.get('status') != 'Filled' and
                            order_data.get('status') != 'Cancelled'):
                            # Create a mock Order object for pbe1_loop_run
                            from ib_insync import Order
                            entry_order = Order()
                            entry_order.orderId = order_id
                            entry_order.action = tradeType
                            entry_order.totalQuantity = quantity
                            logging.info(f"PBe1: Found entry order in orderStatusData: orderId={order_id}, status={order_data.get('status')}")
                            break
                    
                    if entry_order:
                        logging.info("PBe1: Starting pbe1_loop_run for continuous entry order updates (fallback method)")
                        asyncio.ensure_future(pbe1_loop_run(connection, key, entry_order))
                        logging.info("PBe1: pbe1_loop_run scheduled as background task (fallback)")
                    else:
                        logging.warning("PBe1: Could not find entry order in orderStatusData to start pbe1_loop_run. Available orders: %s", list(Config.orderStatusData.keys()))

                logging.info("pull_back_PBe1 entry task done %s",symbol)
                break
            except Exception as e:
                # Log error but continue monitoring
                logging.error("PBe1 error in monitoring loop: %s. Will retry after 1 second.", e)
                logging.error(traceback.format_exc())
                await asyncio.sleep(1)
                # Continue the loop to keep monitoring
                continue


async def lb1(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                         atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth,entry_points):
    logging.info("mkt trade is sending.")
    ibContract = getContract(symbol, None)
    currentDateTime = datetime.datetime.now()
    key = (symbol + str(datetime.datetime.now()))
    logging.info("lb1 Key for this trade is- %s ", key)
    end_dtime = str(datetime.datetime.now().date()) + " " + Config.LB1_PBe1_time
    end_dtime = datetime.datetime.strptime(end_dtime, '%Y-%m-%d %H:%M:%S')
    dtime = end_dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
    dtime = dtime.time().replace(microsecond=0)
    chartTime = await get_first_chart_time_lb(str(dtime), timeFrame, outsideRth)
    logging.info(f"chart time found for lb1   {chartTime}")
    while True:
        end_dtime = str(datetime.datetime.now().date()) + " " + Config.LB1_PBe1_time
        end_dtime = datetime.datetime.strptime(end_dtime, '%Y-%m-%d %H:%M:%S')
        dtime = end_dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
        if (datetime.datetime.now() < dtime):
            await asyncio.sleep(1)
            continue
        logging.info("send lb1 trade loop is running..")
        histData = None
        tradeType = ""
        histData = None
        recentBarData = connection.lb1_entry_historical_data(ibContract, timeFrame, dtime)
        if (recentBarData == None or len(recentBarData) == 0):
            logging.info("lb1 last 3 record not found we will try after 2 sec.")
            await  asyncio.sleep(1)
            continue
        logging.info("RecentBar for lb1   %s", recentBarData)

        lastPrice = 0
        #     it will check e1 condition
        histData = recentBarData.get(0)

        tradeType = ""
        logging.info("lb1  first two  bar for contract %s [ %s ] and low bar [ %s ] ", ibContract, recentBarData.get(0),
                     recentBarData.get(1))
        if ((len(recentBarData)) < 2):
            logging.info("lb1 - minimum three row required for other process")
            await asyncio.sleep(1)
            continue

        tradeType = buySellType
        if ((tradeType != 'BUY') and (tradeType != 'SELL')):
            logging.info(
                "lb1 condition is not satisfying. we will get chart data again after 2 second. last trade is [ %s ]",
                histData)
            tradeType = ""
            await  asyncio.sleep(1)
            continue

        quantity = 0
        aux_price = 0
        if quantity == 0:
            # Calculate entry price (aux_price) first
            if tradeType == "BUY":
                aux_price = histData['high'] - float(entry_points)
            else:
                aux_price = histData['low'] + float(entry_points)
            
            # Calculate stop size first
            stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
            
            # Calculate quantity: qty = risk / stop_size
            risk_amount = _to_float(risk, 0)
            if risk_amount <= 0:
                logging.warning("Invalid risk amount for LB1: %s, using default quantity of 1", risk)
                quantity = 1
            elif stop_size == 0 or stop_size < 0.01:
                logging.warning("Stop size is zero or too small (%s) for LB1, using default quantity of 1", stop_size)
                quantity = 1
            else:
                quantity = risk_amount / stop_size
                quantity = int(round(quantity, 0))
                if quantity <= 0:
                    quantity = 1
                logging.info(f"LB1 quantity calculated: entry=%s, stop_size=%s, risk=%s, quantity=%s", 
                           aux_price, stop_size, risk_amount, quantity)
        else:
            logging.info("lb1 user quantity")
        lastPrice = aux_price
        aux_price = round(aux_price, Config.roundVal)

        lastPrice = round(lastPrice, Config.roundVal)
        logging.info("lb1 Price found for market order %s for %s contract ", lastPrice, ibContract)
        # ATR check functionality removed
        # if (atrCheck(histData, ibContract, connection, atrPercentage)):
        #     await  asyncio.sleep(1)
        #     continue
        logging.info("lb1 Trade action found for market order %s for %s contract ", tradeType, ibContract)
        logging.info(
            "lb1 Trade quantity found for market order %s for %s contract and candle data is %s and last bar data %s",
            quantity, ibContract, histData, histData)
        logging.info("everything found we are placing stp trade")
        conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
        logging.info(
            "lb1 main Entry Data , historical  data [%s] Recent Bar Data [%s] price is [ %s ] tradeType is [%s], TimeFrame [%s], configTime [%s] , quantity [%s], ibContract [%s]",
            Config.historicalData.get(key), recentBarData, lastPrice, tradeType, timeFrame, conf_trading_time, quantity,
            ibContract)
        logging.info(f"lb1 aux limit price befor 0.01 plus minus aux {aux_price}")
        if (tradeType == 'BUY'):
            aux_price = aux_price + 0.01
        else:
            aux_price = aux_price - 0.01

        # Check if extended hours - use STP LMT for extended hours, bracket orders for RTH
        is_extended, session = _is_extended_outside_rth(outsideRth)
        order_type = "STP"
        limit_price = None
        
        if is_extended:
            # Extended hours: Calculate stop size and limit offsets for entry
            stop_size, entry_offset, _ = _calculate_stop_limit_offsets(histData)
            logging.info(f"LB1 Pre-market/After-hours: Using STP LMT order. Stop size={stop_size}, Entry offset={entry_offset}")

            order_type = "STP LMT"
            if tradeType == 'BUY':
                # For BUY: Limit = Entry + entry_offset
                limit_price = aux_price + entry_offset
            else:
                # For SELL: Limit = Entry - entry_offset
                limit_price = aux_price - entry_offset
            
            limit_price = round(limit_price, Config.roundVal)
            logging.info(f"LB1 Pre-market/After-hours {tradeType}: Stop={aux_price}, Limit={limit_price}")

            response = connection.placeTrade(contract=ibContract,
                                               order=Order(orderType=order_type, action=tradeType, totalQuantity=quantity,
                                                           tif=tif, auxPrice=aux_price, lmtPrice=limit_price), outsideRth=outsideRth)
            StatusUpdate(response, 'Entry', ibContract, order_type, tradeType, quantity, histData, lastPrice, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                         breakEven,
                         outsideRth)
        else:
            # RTH: Use bracket orders (Entry STP, TP LMT, SL STP) - same as RB
            logging.info(f"LB1 RTH: Using bracket orders (Entry STP, TP LMT, SL STP)")
            
            # Calculate stop loss price
            stop_loss_price = 0
            try:
                stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
                if tradeType == 'BUY':
                    stop_loss_price = aux_price - stop_size
                else:  # SELL
                    stop_loss_price = aux_price + stop_size
                stop_loss_price = round(stop_loss_price, Config.roundVal)
                logging.info(f"LB1 RTH: Calculated stop_loss_price={stop_loss_price} from stop_size={stop_size}")
            except Exception as e:
                logging.error(f"LB1 RTH: Error calculating stop loss: {e}")
                # Fallback: use bar range
                if tradeType == 'BUY':
                    stop_loss_price = round(float(histData['low']) - 0.01, Config.roundVal)
                else:
                    stop_loss_price = round(float(histData['high']) + 0.01, Config.roundVal)
                logging.warning(f"LB1 RTH: Using fallback stop_loss_price={stop_loss_price}")
            
            # Calculate take profit price
            if stop_size is not None and stop_size > 0:
                tp_stop_size = stop_size
                logging.info(f"LB1 RTH: Using existing stop_size={tp_stop_size} for TP calculation")
            else:
                # Recalculate stop_size
                tp_stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
                tp_stop_size = round(tp_stop_size, Config.roundVal)
                logging.info(f"LB1 RTH: Recalculated stop_size={tp_stop_size} for TP calculation")
            
            # Calculate TP using multiplier
            multiplier_map = {
                Config.takeProfit[0]: 1,    # 1:1
                Config.takeProfit[1]: 1.5,  # 1.5:1
                Config.takeProfit[2]: 2,    # 2:1
                Config.takeProfit[3]: 2.5,  # 2.5:1
            }
            if len(Config.takeProfit) > 4:
                multiplier_map[Config.takeProfit[4]] = 3  # 3:1
            
            multiplier = multiplier_map.get(profit, 2.0)  # Default 2:1
            tp_offset = tp_stop_size * multiplier
            
            if tradeType == 'BUY':
                tp_price = round(aux_price + tp_offset, Config.roundVal)
            else:  # SELL
                tp_price = round(aux_price - tp_offset, Config.roundVal)
            
            logging.info(f"LB1 RTH Bracket: entry={aux_price}, stop_loss={stop_loss_price}, tp={tp_price}, stop_size={tp_stop_size}, multiplier={multiplier}")
            
            # Generate unique order IDs for bracket orders
            parent_order_id = connection.get_next_order_id()
            tp_order_id = connection.get_next_order_id()
            sl_order_id = connection.get_next_order_id()
            
            logging.info("LB1 RTH: Generated unique order IDs for bracket: entry=%s, tp=%s, sl=%s", 
                        parent_order_id, tp_order_id, sl_order_id)
            
            # Entry order (Stop order)
            entry_order = Order(
                orderId=parent_order_id,
                orderType="STP",
                action=tradeType,
                totalQuantity=quantity,
                auxPrice=aux_price,
                tif=tif,
                transmit=False  # Don't transmit until all orders are ready
            )
            
            # Take Profit order (Limit)
            tp_order = Order(
                orderId=tp_order_id,
                orderType="LMT",
                action="SELL" if tradeType == "BUY" else "BUY",
                totalQuantity=quantity,
                lmtPrice=tp_price,
                parentId=parent_order_id,
                transmit=False
            )
            
            # Stop Loss order (Stop order)
            sl_order = Order(
                orderId=sl_order_id,
                orderType="STP",
                action="SELL" if tradeType == "BUY" else "BUY",
                totalQuantity=quantity,
                auxPrice=stop_loss_price,
                parentId=parent_order_id,
                transmit=True  # Last order transmits all
            )
            
            # Place all bracket orders with error handling
            try:
                logging.info("LB1 RTH: Placing entry order - orderId=%s, orderType=%s, action=%s, auxPrice=%s, quantity=%s", 
                            parent_order_id, entry_order.orderType, entry_order.action, entry_order.auxPrice, entry_order.totalQuantity)
                entry_response = connection.placeTrade(contract=ibContract, order=entry_order, outsideRth=outsideRth)
                logging.info("LB1 RTH: Entry order placed successfully - orderId=%s", entry_response.order.orderId)
                StatusUpdate(entry_response, 'Entry', ibContract, 'STP', tradeType, quantity, histData, lastPrice, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue, breakEven,
                             outsideRth, False, entry_points)
                
                # Place TP order with retry logic if order ID is in done state
                try:
                    logging.info("LB1 RTH: Placing TP order - orderId=%s, orderType=%s, action=%s, lmtPrice=%s", 
                                tp_order_id, tp_order.orderType, tp_order.action, tp_order.lmtPrice)
                    tp_response = connection.placeTrade(contract=ibContract, order=tp_order, outsideRth=outsideRth)
                    logging.info("LB1 RTH: TP order placed successfully - orderId=%s", tp_response.order.orderId)
                except Exception as e:
                    if "done state" in str(e) or "AssertionError" in str(e):
                        logging.warning("LB1 RTH: TP order ID %s in done state, generating new ID and retrying", tp_order_id)
                        # Generate new TP order ID and retry
                        tp_order_id = connection.get_next_order_id()
                        tp_order.orderId = tp_order_id
                        tp_response = connection.placeTrade(contract=ibContract, order=tp_order, outsideRth=outsideRth)
                        logging.info("LB1 RTH: TP order placed with new ID: %s", tp_order_id)
                    else:
                        logging.error("LB1 RTH: Error placing TP order: %s", e)
                        raise
                StatusUpdate(tp_response, 'TakeProfit', ibContract, 'LMT', tradeType, quantity, histData, lastPrice, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                             breakEven, outsideRth)
                
                # Place SL order with retry logic if order ID is in done state
                try:
                    logging.info("LB1 RTH: Placing SL order - orderId=%s, orderType=%s, action=%s, auxPrice=%s", 
                                sl_order_id, sl_order.orderType, sl_order.action, sl_order.auxPrice)
                    sl_response = connection.placeTrade(contract=ibContract, order=sl_order, outsideRth=outsideRth)
                    logging.info("LB1 RTH: SL order placed successfully - orderId=%s", sl_response.order.orderId)
                except Exception as e:
                    if "done state" in str(e) or "AssertionError" in str(e):
                        logging.warning("LB1 RTH: SL order ID %s in done state, generating new ID and retrying", sl_order_id)
                        # Generate new SL order ID and retry
                        sl_order_id = connection.get_next_order_id()
                        sl_order.orderId = sl_order_id
                        sl_response = connection.placeTrade(contract=ibContract, order=sl_order, outsideRth=outsideRth)
                        logging.info("LB1 RTH: SL order placed with new ID: %s", sl_order_id)
                    else:
                        logging.error("LB1 RTH: Error placing SL order: %s", e)
                        raise
                StatusUpdate(sl_response, 'StopLoss', ibContract, 'STP', tradeType, quantity, histData, lastPrice, symbol,
                             timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                             breakEven, outsideRth)
                
                logging.info("LB1 RTH: Bracket orders placed successfully - entry=%s, tp=%s, sl=%s", 
                            entry_response.order.orderId, tp_response.order.orderId, sl_response.order.orderId)
            except Exception as e:
                logging.error("LB1 RTH: Error placing bracket orders: %s", e)
                logging.error("LB1 RTH: Traceback: %s", traceback.format_exc())
                traceback.print_exc()
                # Don't break - let the loop continue to retry
                await asyncio.sleep(1)
                continue
        
        logging.info("lb1 entry order done %s ", symbol)
        break

async def lb2(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                         atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth,entry_points):
    logging.info("mkt trade is sending.")
    ibContract = getContract(symbol, None)
    currentDateTime = datetime.datetime.now()
    key = (symbol + str(datetime.datetime.now()))
    logging.info("lb2 Key for this trade is- %s ", key)
    end_dtime = str(datetime.datetime.now().date()) + " " + Config.LB1_PBe1_time
    end_dtime = datetime.datetime.strptime(end_dtime, '%Y-%m-%d %H:%M:%S')
    dtime = end_dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
    dtime = dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
    dtime = dtime.time().replace(microsecond=0)
    chartTime = await get_first_chart_time_lb(str(dtime), timeFrame, outsideRth)
    logging.info(f"chart time found for lb2   {chartTime}")
    while True:
        end_dtime = str(datetime.datetime.now().date()) + " " + Config.LB1_PBe1_time
        end_dtime = datetime.datetime.strptime(end_dtime, '%Y-%m-%d %H:%M:%S')
        dtime = end_dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
        dtime = dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
        if (datetime.datetime.now() < dtime):
            await asyncio.sleep(1)
            continue
        logging.info("lb2 send lb2 trade loop is running..")
        histData = None
        tradeType = ""
        histData = None
        recentBarData = connection.lb1_entry_historical_data(ibContract, timeFrame, dtime)
        if (recentBarData == None or len(recentBarData) == 0):
            logging.info("lb2 last 3 record not found we will try after 2 sec.")
            await  asyncio.sleep(1)
            continue
        logging.info("lb2 RecentBar for lb2 %s", recentBarData)

        lastPrice = 0
        #     it will check e1 condition
        histData = recentBarData.get(0)

        tradeType = ""
        logging.info("lb2 first two  bar for contract %s [ %s ] and low bar [ %s ] ", ibContract, recentBarData.get(0),
                     recentBarData.get(1))
        if ((len(recentBarData)) < 2):
            logging.info("lb2- minimum three row required for other process")
            await asyncio.sleep(1)
            continue

        tradeType = buySellType
        if ((tradeType != 'BUY') and (tradeType != 'SELL')):
            logging.info(
                "lb2 condition is not satisfying. we will get chart data again after 2 second. last trade is [ %s ]",
                histData)
            tradeType = ""
            await  asyncio.sleep(1)
            continue

        quantity = 0
        aux_price = 0
        lmtPrice = 0
        if quantity == 0:
            # Calculate entry price (aux_price) first
            if tradeType == "BUY":
                aux_price = histData['high'] - float(entry_points)
            else:
                aux_price = histData['low'] + float(entry_points)
            
            # Calculate stop size first
            stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
            
            # Calculate quantity: qty = risk / stop_size
            risk_amount = _to_float(risk, 0)
            if risk_amount <= 0:
                logging.warning("Invalid risk amount for LB2: %s, using default quantity of 1", risk)
                quantity = 1
            elif stop_size == 0 or stop_size < 0.01:
                logging.warning("Stop size is zero or too small (%s) for LB2, using default quantity of 1", stop_size)
                quantity = 1
            else:
                quantity = risk_amount / stop_size
                quantity = int(round(quantity, 0))
                if quantity <= 0:
                    quantity = 1
                logging.info(f"LB2 quantity calculated: entry=%s, stop_size=%s, risk=%s, quantity=%s", 
                           aux_price, stop_size, risk_amount, quantity)
            
        else:
            logging.info("lb2 user quantity")
        lastPrice = aux_price
        aux_price = round(aux_price, Config.roundVal)

        lastPrice = round(lastPrice, Config.roundVal)
        logging.info("lb2 Price found for market order %s for %s contract ", lastPrice, ibContract)
        # ATR check functionality removed
        # if (atrCheck(histData, ibContract, connection, atrPercentage)):
        #     await  asyncio.sleep(1)
        #     continue
        logging.info("lb2 Trade action found for market order %s for %s contract ", tradeType, ibContract)
        logging.info(
            "lb2 Trade quantity found for market order %s for %s contract and candle data is %s and last bar data %s",
            quantity, ibContract, histData, histData)
        logging.info("everything found we are placing stp trade")
        conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
        logging.info(
            "lb2 main Entry Data , historical  data [%s] Recent Bar Data [%s] price is [ %s ] tradeType is [%s], TimeFrame [%s], configTime [%s] , quantity [%s], ibContract [%s]",
            Config.historicalData.get(key), recentBarData, lastPrice, tradeType, timeFrame, conf_trading_time, quantity,
            ibContract)
        logging.info(f"lb2 aux limit price befor 0.01 plus minus aux {aux_price}")
        if (tradeType == 'BUY'):
            aux_price = aux_price + 0.01
        else:
            aux_price = aux_price - 0.01

        # Check if extended hours - use STP LMT for extended hours (same logic as RB/RBB)
        is_extended, session = _is_extended_outside_rth(outsideRth)
        order_type = "STP LMT"  # LB2 always uses STP LMT
        limit_price = None
        
        # Calculate stop size and limit offsets for entry (same as RB/RBB)
        stop_size, entry_offset, _ = _calculate_stop_limit_offsets(histData)
        logging.info(f"LB2: Using STP LMT order. Stop size={stop_size}, Entry offset={entry_offset}")

        if tradeType == 'BUY':
            # For BUY: Limit = Entry + entry_offset
            limit_price = aux_price + entry_offset
        else:
            # For SELL: Limit = Entry - entry_offset
            limit_price = aux_price - entry_offset
        
        limit_price = round(limit_price, Config.roundVal)
        logging.info(f"LB2 {tradeType}: Stop={aux_price}, Limit={limit_price}")

        response = connection.placeTrade(contract=ibContract,
                                         order=Order(orderType=order_type, action=tradeType, totalQuantity=quantity,
                                                     tif=tif, auxPrice=aux_price, lmtPrice=limit_price), outsideRth=outsideRth)
        StatusUpdate(response, 'Entry', ibContract, 'STP LMT', tradeType, quantity, histData, lastPrice, symbol,
                     timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                     breakEven,
                     outsideRth)
        logging.info("lb2 entry order done %s ", symbol)
        break


async def lb3(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                         atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth,entry_points):
    logging.info("mkt trade is sending.")
    ibContract = getContract(symbol, None)
    currentDateTime = datetime.datetime.now()
    key = (symbol + str(datetime.datetime.now()))
    logging.info("lb3 Key for this trade is- %s ", key)
    end_dtime = str(datetime.datetime.now().date()) + " " + Config.LB1_PBe1_time
    end_dtime = datetime.datetime.strptime(end_dtime, '%Y-%m-%d %H:%M:%S')
    dtime = end_dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
    dtime = dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
    dtime = dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
    dtime = dtime.time().replace(microsecond=0)
    chartTime = await get_first_chart_time_lb(str(dtime), timeFrame, outsideRth)
    logging.info(f"chart time found for lb2   {chartTime}")
    while True:
        end_dtime = str(datetime.datetime.now().date()) + " " + Config.LB1_PBe1_time
        end_dtime = datetime.datetime.strptime(end_dtime, '%Y-%m-%d %H:%M:%S')
        dtime = end_dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
        dtime = dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
        dtime = dtime - datetime.timedelta(minutes=Config.timeDictInMinute.get(timeFrame))
        if (datetime.datetime.now() < dtime):
            await asyncio.sleep(1)
            continue
        logging.info("lb3 send lb3 trade loop is running..")
        histData = None
        tradeType = ""
        histData = None
        recentBarData = connection.lb1_entry_historical_data(ibContract, timeFrame, dtime)
        if (recentBarData == None or len(recentBarData) == 0):
            logging.info("lb3 last 3 record not found we will try after 2 sec.")
            await  asyncio.sleep(1)
            continue
        logging.info("lb3 RecentBar for lb3 %s", recentBarData)

        lastPrice = 0
        #     it will check e1 condition
        histData = recentBarData.get(0)

        tradeType = ""
        logging.info("lb3 first two  bar for contract %s [ %s ] and low bar [ %s ] ", ibContract, recentBarData.get(0),
                     recentBarData.get(1))
        if ((len(recentBarData)) < 2):
            logging.info("lb3- minimum three row required for other process")
            await asyncio.sleep(1)
            continue

        tradeType = buySellType
        if ((tradeType != 'BUY') and (tradeType != 'SELL')):
            logging.info(
                "lb3 condition is not satisfying. we will get chart data again after 2 second. last trade is [ %s ]",
                histData)
            tradeType = ""
            await  asyncio.sleep(1)
            continue

        quantity = 0
        aux_price = 0
        lmtPrice=0
        if quantity == 0:
            # Calculate entry price (aux_price) first
            if tradeType == "BUY":
                aux_price = histData['high'] - float(entry_points)
            else:
                aux_price = histData['low'] + float(entry_points)
            
            # Calculate stop size first
            stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
            
            # Calculate quantity: qty = risk / stop_size
            risk_amount = _to_float(risk, 0)
            if risk_amount <= 0:
                logging.warning("Invalid risk amount for LB3: %s, using default quantity of 1", risk)
                quantity = 1
            elif stop_size == 0 or stop_size < 0.01:
                logging.warning("Stop size is zero or too small (%s) for LB3, using default quantity of 1", stop_size)
                quantity = 1
            else:
                quantity = risk_amount / stop_size
                quantity = int(round(quantity, 0))
                if quantity <= 0:
                    quantity = 1
                logging.info(f"LB3 quantity calculated: entry=%s, stop_size=%s, risk=%s, quantity=%s", 
                           aux_price, stop_size, risk_amount, quantity)
            
        else:
            logging.info("lb3 user quantity")
        lastPrice = aux_price
        aux_price = round(aux_price, Config.roundVal)

        lastPrice = round(lastPrice, Config.roundVal)
        logging.info("lb3 Price found for market order %s for %s contract ", lastPrice, ibContract)
        # ATR check functionality removed
        # if (atrCheck(histData, ibContract, connection, atrPercentage)):
        #     await  asyncio.sleep(1)
        #     continue
        logging.info("lb3 Trade action found for market order %s for %s contract ", tradeType, ibContract)
        logging.info(
            "lb3 Trade quantity found for market order %s for %s contract and candle data is %s and last bar data %s",
            quantity, ibContract, histData, histData)
        logging.info("everything found we are placing stp trade")
        conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
        logging.info(
            "lb3 main Entry Data , historical  data [%s] Recent Bar Data [%s] price is [ %s ] tradeType is [%s], TimeFrame [%s], configTime [%s] , quantity [%s], ibContract [%s]",
            Config.historicalData.get(key), recentBarData, lastPrice, tradeType, timeFrame, conf_trading_time, quantity,
            ibContract)
        logging.info(f"lb3 aux limit price befor 0.01 plus minus aux {aux_price}")
        if (tradeType == 'BUY'):
            aux_price = aux_price + 0.01
        else:
            aux_price = aux_price - 0.01

        # Check if extended hours - use STP LMT for extended hours (same logic as RB/RBB)
        is_extended, session = _is_extended_outside_rth(outsideRth)
        order_type = "STP LMT"  # LB3 always uses STP LMT
        limit_price = None
        
        # Calculate stop size and limit offsets for entry (same as RB/RBB)
        stop_size, entry_offset, _ = _calculate_stop_limit_offsets(histData)
        logging.info(f"LB3: Using STP LMT order. Stop size={stop_size}, Entry offset={entry_offset}")

        if tradeType == 'BUY':
            # For BUY: Limit = Entry + entry_offset
            limit_price = aux_price + entry_offset
        else:
            # For SELL: Limit = Entry - entry_offset
            limit_price = aux_price - entry_offset
        
        limit_price = round(limit_price, Config.roundVal)
        logging.info(f"LB3 {tradeType}: Stop={aux_price}, Limit={limit_price}")

        response = connection.placeTrade(contract=ibContract,
                                         order=Order(orderType=order_type, action=tradeType, totalQuantity=quantity,
                                                     tif=tif, auxPrice=aux_price, lmtPrice=limit_price), outsideRth=outsideRth)
        StatusUpdate(response, 'Entry', ibContract, 'STP LMT', tradeType, quantity, histData, lastPrice, symbol,
                     timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                     breakEven,
                     outsideRth)
        logging.info("lb3 entry order done %s ", symbol)
        break


async def pull_back_PBe2(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue ,breakEven,outsideRth):
    """
    PBe2 logic:
    - Simulate PBe1 without placing any order:
      * Use buySellType directly (no condition checking) - same as PBe1
      * Calculate entry price and stop loss (same as PBe1)
      * Monitor if price hits stop loss level (LOD for BUY, HOD for SELL)
    - After PBe1 would have stopped out, replay PBe1:
      * Use same logic as PBe1 (sendEntryTrade, same entry/stop loss calculation)
      * Place actual order and start pbe1_loop_run
    """
    logging.info("pull_back_PBe2 mkt trade is sending.")
    ibContract = getContract(symbol, None)
    currentDateTime = datetime.datetime.now()
    # priceObj = subscribePrice(ibContract, connection)
    key = (symbol + str(datetime.datetime.now()))
    logging.info("Key for this trade is- %s ", key)
    chartTime = await get_first_chart_time(timeFrame , outsideRth)
    while True:
        # Skip time check for premarket/after-hours trading
        if not outsideRth:
            dtime = str(datetime.datetime.now().date()) + " "+Config.pull_back_PBe2_time
            if (datetime.datetime.now() < datetime.datetime.strptime(dtime, '%Y-%m-%d %H:%M:%S')):
                await asyncio.sleep(1)
                continue
        logging.info("send trade loop is running..")
        histData = None
        tradeType = ""
        
        # PBe2 logic:
        # 1. Simulate PBe1 without placing order (use buySellType directly, calculate entry/stop loss)
        # 2. Monitor if price hits stop loss level (LOD for BUY, HOD for SELL)
        # 3. After PBe1 would have stopped out, replay PBe1 (place order using same logic as PBe1)
        
        logging.info("PBe2: Starting PBe1 simulation (no order will be placed until after stop out)")
        
        # Step 1: Check PBe1 condition first (simulate PBe1 without placing order)
        complete_bar_data = connection.pbe1_entry_historical_data(ibContract, timeFrame, chartTime)
        logging.info("PBe2: RecentBar for PBe1 condition check %s", complete_bar_data)
        if(len(complete_bar_data) == 0):
            logging.info("PBe2: last 1 record not found for PBe1 condition check, will try after 1 sec.")
            await asyncio.sleep(1)
            continue
        
        # Use the same bar selection logic as PBe1 (second-to-last bar if available)
        if len(complete_bar_data) >= 2:
            # Get the second-to-last bar (most recent closed bar) - same as PBe1
            last_candel = complete_bar_data[len(complete_bar_data)-2]
            logging.info("PBe2: Using second-to-last bar (index=%s) as most recent closed bar for PBe1 condition check", len(complete_bar_data)-2)
        else:
            # Only one bar available, use it (might be the initial bar) - same as PBe1
            last_candel = complete_bar_data[len(complete_bar_data)-1]
            logging.info("PBe2: Only one bar available, using it (index=0) for PBe1 condition check")
        
        logging.info("PBe2: last candel found for PBe1 condition check %s ", last_candel)
        if (last_candel == None or len(last_candel) == 0):
            logging.info("PBe2: Last Price Not Found for %s contract for PBe1 condition check", ibContract)
            await asyncio.sleep(1)
            continue
        
        # PBe2: First check PBe1 condition (must wait until PBe1 condition is met)
        # Use pbe1_result() to check if PBe1 condition is satisfied
        pbe1_tradeType, pbe1_result_row = pbe1_result(last_candel, complete_bar_data)
        logging.info("PBe2: Checking PBe1 condition using pbe1_result: tradeType=%s, result_row=%s", pbe1_tradeType, pbe1_result_row)
        
        # Wait until PBe1 condition is met (but don't place any order for PBe1)
        if pbe1_tradeType == "" or pbe1_tradeType is None:
            logging.info("PBe2: PBe1 condition not met yet (tradeType=%s). Waiting for PBe1 condition to be satisfied...", pbe1_tradeType)
            sleepTime = getSleepTime(timeFrame, outsideRth)
            if sleepTime == 0:
                sleepTime = 1
            await asyncio.sleep(sleepTime)
            continue
        
        # Check if PBe1 condition matches user's selected direction
        if buySellType != pbe1_tradeType:
            logging.info("PBe2: PBe1 condition found (%s) but doesn't match user direction (%s). Waiting for matching condition...", 
                        pbe1_tradeType, buySellType)
            sleepTime = getSleepTime(timeFrame, outsideRth)
            if sleepTime == 0:
                sleepTime = 1
            await asyncio.sleep(sleepTime)
            continue
        
        logging.info("PBe2: PBe1 condition is met! tradeType=%s matches user direction. Proceeding to simulate PBe1 (NO ORDER WILL BE PLACED)", pbe1_tradeType)
        
        # PBe1 simulation: Calculate entry price and stop loss (but DON'T place order)
        logging.info("PBe2: Simulating PBe1! tradeType=%s. Calculating entry price and stop loss (NO ORDER WILL BE PLACED)", pbe1_tradeType)
        
        # Get LOD/HOD for stop loss calculation
        lod, hod = _get_pbe1_lod_hod(connection, ibContract, timeFrame, pbe1_tradeType)
        
        # Calculate PBe1 entry stop price (same as PBe1)
        pbe1_entry_price = None
        if pbe1_tradeType == 'BUY':
            pbe1_entry_price = round(float(last_candel.get('high', 0)) + 0.01, Config.roundVal)
            pbe1_stop_loss_price = lod  # BUY uses LOD
        else:  # SELL
            pbe1_entry_price = round(float(last_candel.get('low', 0)) - 0.01, Config.roundVal)
            pbe1_stop_loss_price = hod  # SELL uses HOD
        
        # Calculate PBe1 stop size
        bar_high = float(last_candel.get('high', 0))
        bar_low = float(last_candel.get('low', 0))
        if pbe1_tradeType == 'BUY':
            pbe1_stop_size = abs(bar_high - lod) if lod > 0 else (bar_high - bar_low) + Config.add002
        else:  # SELL
            pbe1_stop_size = abs(bar_low - hod) if hod > 0 else (bar_high - bar_low) + Config.add002
        
        pbe1_stop_size = round(pbe1_stop_size, Config.roundVal)
        pbe1_stop_loss_price = round(pbe1_stop_loss_price, Config.roundVal) if pbe1_stop_loss_price else 0
        
        logging.info(f"PBe2: PBe1 would have: entry_price={pbe1_entry_price}, stop_loss_price={pbe1_stop_loss_price}, stop_size={pbe1_stop_size}, LOD={lod}, HOD={hod}")
        logging.info("PBe2: Monitoring for PBe1 entry fill, then stop out (price must hit entry price first, then stop loss)")
        
        # Step 2: Monitor PBe1 simulation - first wait for entry fill, then wait for stop out
        # CRITICAL: We need to wait for:
        # 1. PBe1 entry to be filled (price must hit entry price)
        # 2. THEN PBe1 to stop out (price must hit stop loss)
        # Use historical data instead of live market data to avoid subscription issues
        ticker = None
        try:
            # Try to get market data subscription once (optional, will fallback to historical data)
            ticker = connection.ib.reqMktData(ibContract, '', False, False)
            await asyncio.sleep(0.5)  # Wait for initial price update
        except Exception as e:
            logging.debug("PBe2: Could not subscribe to market data, will use historical data: %s", e)
            ticker = None
        
        pbe1_entry_filled = False  # Track if PBe1 entry would have been filled
        last_processed_bar_datetime = last_candel.get('date') if last_candel else None  # Track last processed bar to detect new bars
        
        try:
            while True:
                try:
                    # Get current price - prefer historical data to avoid subscription issues
                    current_price = None
                    
                    # Try to get price from ticker if available
                    if ticker:
                        try:
                            price = ticker.marketPrice()
                            if price is not None and price != 0:
                                current_price = float(price)
                        except (AttributeError, ValueError):
                            pass
                    
                    # Fallback to historical data (more reliable and doesn't require subscription)
                    latest_bar_data = None
                    latest_bar = None
                    if current_price is None:
                        latest_bar_data = connection.pbe1_entry_historical_data(ibContract, timeFrame, chartTime)
                        if latest_bar_data and len(latest_bar_data) > 0:
                            # Use high/low from latest bar to check if entry was hit
                            latest_bar = latest_bar_data[-1]
                            if pbe1_tradeType == 'BUY':
                                current_price = float(latest_bar.get('high', latest_bar.get('close', 0)))  # Use high for BUY entry check
                            else:  # SELL
                                current_price = float(latest_bar.get('low', latest_bar.get('close', 0)))  # Use low for SELL entry check
                        else:
                            current_price = float(last_candel.get('close', 0))
                    
                    current_price = round(current_price, Config.roundVal)
                    
                    # Check if a new bar has closed (detect new bar by comparing datetime)
                    new_bar_closed = False
                    if latest_bar and 'date' in latest_bar:
                        current_bar_datetime = latest_bar.get('date')
                        if last_processed_bar_datetime and current_bar_datetime:
                            try:
                                # Compare datetimes
                                if hasattr(last_processed_bar_datetime, 'replace'):
                                    last_dt = last_processed_bar_datetime.replace(microsecond=0)
                                else:
                                    last_dt = last_processed_bar_datetime
                                
                                if hasattr(current_bar_datetime, 'replace'):
                                    current_dt = current_bar_datetime.replace(microsecond=0)
                                else:
                                    current_dt = current_bar_datetime
                                
                                if current_dt > last_dt:
                                    # New bar closed!
                                    new_bar_closed = True
                                    last_processed_bar_datetime = current_bar_datetime
                                    logging.info("PBe2: New bar closed! (last=%s, new=%s). Will recalculate LOD/HOD if needed.", last_dt, current_dt)
                            except Exception as e:
                                logging.debug("PBe2: Error comparing bar datetimes: %s", e)
                    
                    # Step 2a: First check if PBe1 entry would have been filled
                    if not pbe1_entry_filled:
                        # CRITICAL: Recalculate LOD/HOD when new bar closes (LOD/HOD may have changed)
                        # Only recalculate when a new bar closes to be efficient
                        if new_bar_closed:
                            updated_lod, updated_hod = _get_pbe1_lod_hod(connection, ibContract, timeFrame, pbe1_tradeType)
                            
                            # Update stop loss price with new LOD/HOD (for when entry is filled)
                            if pbe1_tradeType == 'BUY':
                                if updated_lod != lod:
                                    logging.info("PBe2: New bar closed - LOD updated while waiting for entry fill! Old LOD=%s, New LOD=%s. Updating stop loss price.", lod, updated_lod)
                                    lod = updated_lod
                                    pbe1_stop_loss_price = lod
                                    pbe1_stop_loss_price = round(pbe1_stop_loss_price, Config.roundVal)
                            else:  # SELL
                                if updated_hod != hod:
                                    logging.info("PBe2: New bar closed - HOD updated while waiting for entry fill! Old HOD=%s, New HOD=%s. Updating stop loss price.", hod, updated_hod)
                                    hod = updated_hod
                                    pbe1_stop_loss_price = hod
                                    pbe1_stop_loss_price = round(pbe1_stop_loss_price, Config.roundVal)
                        
                        if pbe1_tradeType == 'BUY':
                            # BUY entry: filled if price >= entry_price
                            if current_price >= pbe1_entry_price:
                                pbe1_entry_filled = True
                                logging.info("PBe2: PBe1 entry would have been FILLED! Current price=%s >= entry_price=%s. Now monitoring for stop out (LOD=%s)...", 
                                           current_price, pbe1_entry_price, lod)
                            else:
                                logging.info("PBe2: Waiting for PBe1 entry fill. Current price=%s < entry_price=%s. Waiting... (LOD=%s)", 
                                           current_price, pbe1_entry_price, lod)
                                sleepTime = getSleepTime(timeFrame, outsideRth)
                                if sleepTime == 0:
                                    sleepTime = 1
                                await asyncio.sleep(sleepTime)
                                continue
                        else:  # SELL
                            # SELL entry: filled if price <= entry_price
                            if current_price <= pbe1_entry_price:
                                pbe1_entry_filled = True
                                logging.info("PBe2: PBe1 entry would have been FILLED! Current price=%s <= entry_price=%s. Now monitoring for stop out (HOD=%s)...", 
                                           current_price, pbe1_entry_price, hod)
                            else:
                                logging.info("PBe2: Waiting for PBe1 entry fill. Current price=%s > entry_price=%s. Waiting... (HOD=%s)", 
                                           current_price, pbe1_entry_price, hod)
                                sleepTime = getSleepTime(timeFrame, outsideRth)
                                if sleepTime == 0:
                                    sleepTime = 1
                                await asyncio.sleep(sleepTime)
                                continue
                    
                    # Step 2b: After entry is filled, monitor for stop out
                    if pbe1_entry_filled:
                        # CRITICAL: Recalculate LOD/HOD when new bar closes (LOD/HOD may have changed)
                        # Only recalculate when a new bar closes to be efficient
                        if new_bar_closed:
                            updated_lod, updated_hod = _get_pbe1_lod_hod(connection, ibContract, timeFrame, pbe1_tradeType)
                            
                            # Update stop loss price with new LOD/HOD
                            if pbe1_tradeType == 'BUY':
                                pbe1_stop_loss_price = updated_lod  # BUY uses LOD
                                if updated_lod != lod:
                                    logging.info("PBe2: New bar closed - LOD updated! Old LOD=%s, New LOD=%s. Updating stop loss price.", lod, updated_lod)
                                    lod = updated_lod  # Update for logging
                            else:  # SELL
                                pbe1_stop_loss_price = updated_hod  # SELL uses HOD
                                if updated_hod != hod:
                                    logging.info("PBe2: New bar closed - HOD updated! Old HOD=%s, New HOD=%s. Updating stop loss price.", hod, updated_hod)
                                    hod = updated_hod  # Update for logging
                            
                            pbe1_stop_loss_price = round(pbe1_stop_loss_price, Config.roundVal)
                        
                        # Get current price for stop loss check (use close price, not high/low)
                        if current_price is None or (ticker is None):
                            latest_bar_data = connection.pbe1_entry_historical_data(ibContract, timeFrame, chartTime)
                            if latest_bar_data and len(latest_bar_data) > 0:
                                current_price = float(latest_bar_data[-1].get('close', 0))
                            else:
                                current_price = float(last_candel.get('close', 0))
                            current_price = round(current_price, Config.roundVal)
                        
                        if pbe1_tradeType == 'BUY':
                            # BUY position: stop out if price <= LOD (price hits or goes below stop loss)
                            if current_price > pbe1_stop_loss_price:
                                logging.info("PBe2: PBe1 entry filled. Current price=%s > stop_loss=%s (LOD=%s). PBe1 would not have stopped out yet. Waiting...", 
                                           current_price, pbe1_stop_loss_price, lod)
                                sleepTime = getSleepTime(timeFrame, outsideRth)
                                if sleepTime == 0:
                                    sleepTime = 1
                                await asyncio.sleep(sleepTime)
                                continue
                            else:
                                logging.info("PBe2: PBe1 entry filled AND stopped out! Current price=%s <= stop_loss=%s (LOD=%s). Proceeding to replay PBe1...", 
                                           current_price, pbe1_stop_loss_price, lod)
                                break  # Exit monitoring loop, proceed to replay PBe1
                        else:  # SELL
                            # SELL position: stop out if price >= HOD (price hits or goes above stop loss)
                            if current_price < pbe1_stop_loss_price:
                                logging.info("PBe2: PBe1 entry filled. Current price=%s < stop_loss=%s (HOD=%s). PBe1 would not have stopped out yet. Waiting...", 
                                           current_price, pbe1_stop_loss_price, hod)
                                sleepTime = getSleepTime(timeFrame, outsideRth)
                                if sleepTime == 0:
                                    sleepTime = 1
                                await asyncio.sleep(sleepTime)
                                continue
                            else:
                                logging.info("PBe2: PBe1 entry filled AND stopped out! Current price=%s >= stop_loss=%s (HOD=%s). Proceeding to replay PBe1...", 
                                           current_price, pbe1_stop_loss_price, hod)
                                break  # Exit monitoring loop, proceed to replay PBe1
                except Exception as e:
                    logging.warning("PBe2: Error checking price for PBe1 entry/stop out: %s. Retrying...", e)
                    await asyncio.sleep(1)
                    continue
        finally:
            # Cancel market data subscription if it was created
            if ticker:
                try:
                    connection.ib.cancelMktData(ibContract)
                except Exception as e:
                    logging.debug("PBe2: Error canceling market data: %s", e)
        
        # Step 3: PBe1 would have stopped out! Now replay PBe1 logic (REPLAY MODE)
        logging.info("PBe2: PBe1 would have stopped out. Now replaying PBe1 logic (REPLAY MODE - no condition check, just replay PBe1)...")
        
        # Get fresh bar data for replaying PBe1 (use same logic as PBe1)
        complete_bar_data = connection.pbe1_entry_historical_data(ibContract, timeFrame, chartTime)
        if(len(complete_bar_data) == 0):
            logging.info("PBe2: last 1 record not found for replaying PBe1, will try after 1 sec.")
            await asyncio.sleep(1)
            continue
        
        # Use the same bar selection logic as PBe1 (second-to-last bar if available)
        if len(complete_bar_data) >= 2:
            # Get the second-to-last bar (most recent closed bar) - same as PBe1
            last_candel = complete_bar_data[len(complete_bar_data)-2]
            logging.info("PBe2: Using second-to-last bar (index=%s) as most recent closed bar for replaying PBe1", len(complete_bar_data)-2)
        else:
            # Only one bar available, use it (might be the initial bar) - same as PBe1
            last_candel = complete_bar_data[len(complete_bar_data)-1]
            logging.info("PBe2: Only one bar available, using it (index=0) for replaying PBe1")
        
        if (last_candel == None or len(last_candel) == 0):
            logging.info("PBe2: Last Price Not Found for %s contract for replaying PBe1", ibContract)
            await asyncio.sleep(1)
            continue
        
        # PBe2 REPLAY MODE: Use buySellType directly (same as PBe1, no condition checking)
        tradeType = buySellType
        logging.info("PBe2 REPLAY MODE: Using user-selected trade type %s (no condition checking) - same as PBe1", tradeType)
        
        # Use last_candel for entry price calculation (same as PBe1)
        histData = last_candel
        lastPrice = last_candel.get('close', 0) if last_candel else 0
        
        # Get LOD/HOD for stop loss calculation
        lod, hod = _get_pbe1_lod_hod(connection, ibContract, timeFrame, tradeType)
        
        # Entry stop price = bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL) - same as PBe1
        # Use histData (from PBe2 condition) for entry price calculation
        entry_price = None
        if tradeType == 'BUY':
            entry_price = round(float(histData.get('high', 0)) + 0.01, Config.roundVal)
            stop_loss_price = lod  # BUY uses LOD
        else:  # SELL
            entry_price = round(float(histData.get('low', 0)) - 0.01, Config.roundVal)
            stop_loss_price = hod  # SELL uses HOD
        
        # Stop size = |bar_high/low - LOD/HOD|
        bar_high = float(histData.get('high', 0))
        bar_low = float(histData.get('low', 0))
        if tradeType == 'BUY':
            stop_size = abs(bar_high - lod) if lod > 0 else (bar_high - bar_low) + Config.add002
        else:  # SELL
            stop_size = abs(bar_low - hod) if hod > 0 else (bar_high - bar_low) + Config.add002
        
        stop_size = round(stop_size, Config.roundVal)
        stop_loss_price = round(stop_loss_price, Config.roundVal) if stop_loss_price else 0
        
        if stop_size <= 0:
            stop_size = round((bar_high - bar_low) + Config.add002, Config.roundVal)
            logging.warning(f"PBe2: Invalid stop_size, using bar range: {stop_size}")
        
        logging.info(f"PBe2: entry_price={entry_price}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, LOD={lod}, HOD={hod}")
        
        # Calculate quantity: qty = risk / stop_size
        risk_amount = _to_float(risk, 0)
        if risk_amount <= 0:
            logging.warning("Invalid risk amount for PBe2: %s, using default quantity of 1", risk)
            quantity = 1
        elif stop_size == 0 or stop_size < 0.01:
            logging.warning("Stop size is zero or too small (%s) for PBe2, using default quantity of 1", stop_size)
            quantity = 1
        else:
            quantity = risk_amount / stop_size
            quantity = int(round(quantity, 0))
            if quantity <= 0:
                quantity = 1
        logging.info(f"PBe2 quantity calculated: entry={entry_price}, stop_loss={stop_loss_price}, stop_size={stop_size}, risk={risk_amount}, quantity={quantity}")
        
        # Place PBe2 order using sendEntryTrade (same logic as PBe1 - REPLAY MODE)
        logging.info("PBe2 REPLAY MODE: Placing PBe2 order using sendEntryTrade (same logic as PBe1)")
        sendEntryTrade(connection, ibContract, tradeType, quantity, histData, entry_price, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage, slValue, breakEven, outsideRth)
        
        # Wait a moment for StatusUpdate to complete and store order in orderStatusData
        await asyncio.sleep(0.2)
        
        # Get the order ID from orderStatusData to start pbe2_loop_run (PBe2 uses pbe2_loop_run, not pbe1_loop_run)
        entry_order = None
        logging.info(f"PBe2: Searching orderStatusData for entry order (symbol={symbol}, barType={barType})")
        for order_id, order_data in Config.orderStatusData.items():
            logging.info(f"PBe2: Checking orderId={order_id}, usersymbol={order_data.get('usersymbol')}, barType={order_data.get('barType')}, ordType={order_data.get('ordType')}, status={order_data.get('status')}")
            if (order_data.get('usersymbol') == symbol and 
                order_data.get('barType') == barType and
                order_data.get('ordType') == 'Entry' and
                order_data.get('status') != 'Filled' and
                order_data.get('status') != 'Cancelled'):
                # Create a mock Order object for pbe2_loop_run
                from ib_insync import Order
                entry_order = Order()
                entry_order.orderId = order_id
                entry_order.action = tradeType
                entry_order.totalQuantity = quantity
                logging.info(f"PBe2: Found entry order in orderStatusData: orderId={order_id}, status={order_data.get('status')}")
                break
        
        if entry_order:
            # Start pbe2_loop_run for continuous entry order updates (PBe2 uses pbe2_loop_run, not pbe1_loop_run)
            logging.info("PBe2: Starting pbe2_loop_run for continuous entry order updates")
            # Start as background task (don't await, let it run in background)
            asyncio.ensure_future(pbe2_loop_run(connection, key, entry_order, buySellType, lastPrice))
            logging.info("PBe2: pbe2_loop_run scheduled as background task")
        else:
            logging.warning("PBe2: Could not find entry order in orderStatusData to start pbe2_loop_run. Available orders: %s", list(Config.orderStatusData.keys()))
        
        logging.info("PBe2 entry order done %s", symbol)
        break  # Exit main loop after placing PBe2 order
async def pbe2_loop_run(connection,key,entry_order,buySellType, lastPrice):
    order = entry_order
    last_stop_size_update = datetime.datetime.now()
    
    while True:
        try:
            await asyncio.sleep(1)
            
            # Update stop size and share size every 30 seconds
            now = datetime.datetime.now()
            if (now - last_stop_size_update).total_seconds() >= 30:
                await _update_pbe_stop_size_and_quantity(connection, order.orderId, is_pbe1=False)
                last_stop_size_update = now
            # for entry_key, entry_value in list(Config.rbbb_dict.items()):
            old_order = Config.orderStatusData[order.orderId]
            logging.info("old order pbe2_loop_run %s ",old_order)
            if old_order != None:
                sleep_time = getTimeInterval(old_order['timeFrame'], datetime.datetime.now())
                await asyncio.sleep(sleep_time)
            old_order = Config.orderStatusData[order.orderId]
            
            # If order is cancelled or inactive, exit loop
            if old_order is None or old_order['status'] in ['Cancelled', 'Inactive']:
                if old_order:
                    logging.info(f"PBe2 loop: Order {order.orderId} status={old_order['status']}, exiting loop")
                break
            
            # If entry order is filled, continue loop for 30-second updates but skip entry order updates
            if old_order['status'] == 'Filled':
                # Entry order is filled - continue loop for 30-second stop size/share size updates
                # but skip entry order price updates
                await asyncio.sleep(1)
                continue
            
            # Entry order is still active - proceed with entry order updates
            if (old_order['status'] != 'Filled' and old_order['status'] != 'Cancelled' and old_order['status'] != 'Inactive'):
                logging.info("pbe2 stp updation start  %s %s", order.orderId , old_order['status'].upper()  )
                chartTime = await get_first_chart_time(old_order['timeFrame'], old_order['outsideRth'])
                recentBarData = connection.getHistoricalChartDataForEntry(old_order['contract'], old_order['timeFrame'], chartTime)
                if (len(recentBarData) == 0):
                    logging.info("last 3 record not found we will try after 2 sec.")
                    await  asyncio.sleep(1)
                    continue
                
                # Get current bar (last bar)
                current_bar = recentBarData[len(recentBarData) - 1]
                current_bar_high = float(current_bar.get('high', 0))
                current_bar_low = float(current_bar.get('low', 0))
                current_bar_datetime = current_bar.get('dateTime')
                
                # Get original bar's datetime to check if new bar closed
                original_histData = old_order.get('histData', {})
                original_bar_datetime = original_histData.get('dateTime') if original_histData else None
                
                # Check if a new bar has closed (different datetime) - similar to RBB
                should_update_entry = False
                if original_bar_datetime and current_bar_datetime:
                    if original_bar_datetime != current_bar_datetime:
                        # New bar closed, update entry stop price (like RBB)
                        should_update_entry = True
                        logging.info(f"PBe2: New bar closed (original={original_bar_datetime}, new={current_bar_datetime}), updating entry stop price")
                else:
                    # Can't compare datetimes, skip entry update but still check HOD/LOD
                    logging.info(f"PBe2: Cannot compare bar datetimes, skipping entry update")
                
                # Update entry stop price if new bar closed (like RBB)
                if should_update_entry:
                    # Calculate new entry stop price: bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL)
                    new_aux_price = 0
                    if old_order['userBuySell'] == 'BUY':
                        new_aux_price = round(current_bar_high + 0.01, Config.roundVal)
                    else:  # SELL
                        new_aux_price = round(current_bar_low - 0.01, Config.roundVal)
                    
                    # Get current entry stop price
                    current_entry_stop = round(float(order.auxPrice), Config.roundVal)
                    
                    # Only update if different
                    if abs(new_aux_price - current_entry_stop) > 0.001:
                        logging.info(f"PBe2: Updating entry stop price from {current_entry_stop} to {new_aux_price} (bar_high={current_bar_high}, bar_low={current_bar_low})")
                        
                        # Calculate stop_size for limit price (if extended hours)
                        is_extended, _ = _is_extended_outside_rth(old_order.get('outsideRth', False))
                        if is_extended:
                            # For PB: stop_size = |bar_high/low - LOD/HOD|
                            stored_lod = old_order.get('pbe1_lod', 0)
                            stored_hod = old_order.get('pbe1_hod', 0)
                            if old_order['userBuySell'] == 'BUY':
                                stop_size = abs(current_bar_high - stored_lod) if stored_lod > 0 else (current_bar_high - current_bar_low) + Config.add002
                            else:  # SELL
                                stop_size = abs(current_bar_low - stored_hod) if stored_hod > 0 else (current_bar_high - current_bar_low) + Config.add002
                            stop_size = round(stop_size, Config.roundVal)
                            
                            # Calculate limit price: stop ± 0.5 × stop_size
                            entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                            min_limit_offset = 0.01
                            if entry_limit_offset < min_limit_offset:
                                entry_limit_offset = min_limit_offset
                            
                            if old_order['userBuySell'] == 'BUY':
                                limit_price = round(new_aux_price + entry_limit_offset, Config.roundVal)
                            else:  # SELL
                                limit_price = round(new_aux_price - entry_limit_offset, Config.roundVal)
                            
                            logging.info(f"PBe2 Extended hours Entry Update: STP LMT, Stop={new_aux_price}, Limit={limit_price} (stop ± 0.5×stop_size={entry_limit_offset})")
                            new_order = Order(orderType="STP LMT", action=order.action, totalQuantity=order.totalQuantity, 
                                            tif='DAY', auxPrice=new_aux_price, lmtPrice=limit_price)
                        else:
                            # RTH: Use stop order (STP)
                            logging.info(f"PBe2 RTH Entry Update: STP, Stop={new_aux_price}")
                            new_order = Order(orderType="STP", action=order.action, totalQuantity=order.totalQuantity,  
                                            tif='DAY', auxPrice=new_aux_price)
                        
                        old_orderId = order.orderId
                        connection.cancelTrade(order)
                        response = connection.placeTrade(contract=old_order['contract'], order=new_order, 
                                                       outsideRth=old_order.get('outsideRth', False))
                        logging.info(f"PBe2: Entry stop price update response: {response}")
                        order = response.order
                        
                        # Update orderStatusData
                        if Config.orderStatusData.get(old_orderId) is not None:
                            d = Config.orderStatusData.get(old_orderId)
                            d['histData'] = current_bar
                            d['orderId'] = int(response.order.orderId)
                            d['status'] = response.orderStatus.status
                            d['lastPrice'] = round(new_aux_price, Config.roundVal)
                            Config.orderStatusData[order.orderId] = d
                            old_order = d  # Update old_order for subsequent checks
                
                # Get stored HOD/LOD from orderStatusData (refresh from updated old_order)
                stored_lod = old_order.get('pbe1_lod', 0)
                stored_hod = old_order.get('pbe1_hod', 0)
                
                # Check if current bar breaks HOD/LOD
                hod_broken = current_bar_high > stored_hod
                lod_broken = current_bar_low < stored_lod
                
                if hod_broken or lod_broken:
                    # IMPORTANT: When HOD/LOD breaks, we update stored LOD/HOD values
                    # but we do NOT update the entry order price (aux_price) - it only updates on new bar
                    # Stop size and share size will be updated by the 30-second update function
                    new_hod = max(stored_hod, current_bar_high) if hod_broken else stored_hod
                    new_lod = min(stored_lod, current_bar_low) if lod_broken else stored_lod
                    
                    current_entry_stop = round(float(order.auxPrice), Config.roundVal)
                    logging.info(f"PBe2 HOD/LOD BREAK: current_bar_high={current_bar_high}, current_bar_low={current_bar_low}, old_HOD={stored_hod}, old_LOD={stored_lod}, new_HOD={new_hod}, new_LOD={new_lod}")
                    logging.info(f"PBe2: Entry order price (aux_price={current_entry_stop}) NOT changed - only updates on new bar. Stop size/share size will update every 30 seconds.")
                    
                    # Update stored LOD/HOD values (stop size/share size will be recalculated by 30-second update)
                    old_order = Config.orderStatusData.get(order.orderId)  # Refresh in case it was updated above
                    if old_order:
                        old_order['pbe1_lod'] = new_lod
                        old_order['pbe1_hod'] = new_hod
                        Config.orderStatusData[order.orderId] = old_order
                        
                        # Note: Stop size and share size will be updated by _update_pbe_stop_size_and_quantity() every 30 seconds
                        # We don't update them here to avoid conflicts with the 30-second update cycle
                
                # Continue with original PBe2 logic (pattern detection)
                tradeType, row = pbe_result(buySellType, lastPrice, recentBarData, True)
                if tradeType == "":
                    continue
                if (row['date'] == Config.pbe1_saved.get(key)['date']):
                    logging.info(
                        " in pbe2 first row and second row datetime is same so we will not execute new trade  [ %s ] old date [ %s ]",
                        row['date'], Config.pbe1_saved.get(key)['date'])
                    continue
                else:
                    logging.info("pbe2 second condition found %s %s", tradeType, row)
                    histData = row
                    # Calculate entry stop price: bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL) - same as RBB
                    aux_price = 0
                    if old_order['userBuySell'] == 'BUY':
                        aux_price = round(float(histData['high']) + 0.01, Config.roundVal)
                        logging.info("pbe2 auxprice high for  %s (bar_high=%s)", aux_price, histData['high'])
                    else:
                        aux_price = round(float(histData['low']) - 0.01, Config.roundVal)
                        logging.info("pbe2 auxprice low for  %s (bar_low=%s)", aux_price, histData['low'])

                    # Calculate stop_size for limit price (if extended hours)
                    is_extended, _ = _is_extended_outside_rth(old_order.get('outsideRth', False))
                    if is_extended:
                        # For PB: stop_size = |bar_high/low - LOD/HOD|
                        stored_lod = old_order.get('pbe1_lod', 0)
                        stored_hod = old_order.get('pbe1_hod', 0)
                        if old_order['userBuySell'] == 'BUY':
                            stop_size = abs(float(histData['high']) - stored_lod) if stored_lod > 0 else (float(histData['high']) - float(histData['low'])) + Config.add002
                        else:  # SELL
                            stop_size = abs(float(histData['low']) - stored_hod) if stored_hod > 0 else (float(histData['high']) - float(histData['low'])) + Config.add002
                        stop_size = round(stop_size, Config.roundVal)
                        
                        # Calculate limit price: stop ± 0.5 × stop_size
                        entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                        min_limit_offset = 0.01
                        if entry_limit_offset < min_limit_offset:
                            entry_limit_offset = min_limit_offset
                        
                        if old_order['userBuySell'] == 'BUY':
                            limit_price = round(aux_price + entry_limit_offset, Config.roundVal)
                        else:  # SELL
                            limit_price = round(aux_price - entry_limit_offset, Config.roundVal)
                        
                        logging.info(f"PBe2 Extended hours Entry Update: STP LMT, Stop={aux_price}, Limit={limit_price} (stop ± 0.5×stop_size={entry_limit_offset})")
                        new_order = Order(orderType="STP LMT", action=order.action, totalQuantity=order.totalQuantity, 
                                        tif='DAY', auxPrice=aux_price, lmtPrice=limit_price)
                    else:
                        # RTH: Use stop order (STP)
                        logging.info(f"PBe2 RTH Entry Update: STP, Stop={aux_price}")
                        new_order = Order(orderType="STP", action=order.action, totalQuantity=order.totalQuantity,  
                                        tif='DAY', auxPrice=aux_price)

                    logging.info("pbe2 going to update stp price for  newprice %s old_order %s", aux_price,order)
                    order.auxPrice = aux_price
                    old_orderId=order.orderId
                    connection.cancelTrade(order)
                    response = connection.placeTrade(contract=old_order['contract'], order=new_order)
                    logging.info("pbe2  response of updating stp order %s ",response)
                    order =response.order
                    if(Config.orderStatusData.get(old_orderId) != None ):
                        d=Config.orderStatusData.get(old_orderId)
                        d['histData'] = histData
                        d['orderId']= int(response.order.orderId)
                        d['status']= response.orderStatus.status
                        d['lastPrice'] = round(aux_price,Config.roundVal)
                        d['entryData'] = Config.orderStatusData.get(int(entry_order.orderId))
                        Config.orderStatusData.update({ order.orderId:d })
            else:
                break

        except Exception as e:
            traceback.format_exc()
            logging.info("error in pbe2 aucprice updation %s ", e)
            break
        await asyncio.sleep(1)

async def _update_pbe_stop_size_and_quantity(connection, order_id, is_pbe1=True):
    """
    Update stop size and share size for PBe1/PBe2 orders every 30 seconds.
    Recalculates based on current LOD/HOD and updates TP/SL orders if entry is filled.
    """
    try:
        old_order = Config.orderStatusData.get(order_id)
        if old_order is None:
            return
        
        # Skip if order is cancelled or inactive
        if old_order['status'] in ['Cancelled', 'Inactive']:
            return
        
        # Get current LOD/HOD
        contract = old_order.get('contract')
        timeFrame = old_order.get('timeFrame')
        buySellType = old_order.get('userBuySell')
        
        if not contract or not timeFrame or not buySellType:
            logging.warning(f"PBe1/PBe2: Missing required data for stop size update: contract={contract}, timeFrame={timeFrame}, buySellType={buySellType}")
            return
        
        # Get current LOD/HOD
        lod, hod = _get_pbe1_lod_hod(connection, contract, timeFrame, buySellType)
        if lod == 0 and hod == 0:
            logging.warning(f"PBe1/PBe2: Could not get LOD/HOD for stop size update")
            return
        
        # Get current entry price (use lastPrice - this is the entry stop price, which should NOT change when LOD/HOD changes)
        # Entry order price only updates on new bar, not when LOD/HOD changes
        entry_price = float(old_order.get('lastPrice', 0))
        if entry_price == 0:
            # Fallback to current bar (should rarely happen)
            histData = old_order.get('histData', {})
            if buySellType == 'BUY':
                entry_price = float(histData.get('high', 0)) + 0.01
            else:
                entry_price = float(histData.get('low', 0))  # SELL: prior bar low (no -0.01)
        
        # IMPORTANT: entry_price (lastPrice) should NOT be updated here - it only updates on new bar
        # We use the current entry_price to recalculate stop_size based on current LOD/HOD
        
        # Recalculate stop_size and stop_loss_price
        if buySellType == 'BUY':
            new_stop_loss_price = round(lod, Config.roundVal)
            new_stop_size = abs(entry_price - lod) if lod > 0 else 0
        else:  # SELL
            new_stop_loss_price = round(hod, Config.roundVal)
            new_stop_size = abs(entry_price - hod) if hod > 0 else 0
        
        new_stop_size = round(new_stop_size, Config.roundVal)
        
        if new_stop_size <= 0:
            logging.warning(f"PBe1/PBe2: Invalid stop_size ({new_stop_size}), skipping update")
            return
        
        # Recalculate quantity based on new stop_size
        risk_amount = _to_float(old_order.get('risk', 0), 0)
        if risk_amount <= 0:
            logging.warning(f"PBe1/PBe2: Invalid risk amount ({risk_amount}), skipping quantity update")
            return
        
        new_quantity = risk_amount / new_stop_size
        new_quantity = int(math.ceil(new_quantity))  # Round UP
        if new_quantity <= 0:
            new_quantity = 1
        
        # Update stored values
        old_quantity = old_order.get('totalQuantity', old_order.get('quantity', 0))
        old_order['pbe1_lod'] = lod
        old_order['pbe1_hod'] = hod
        old_order['stopLossPrice'] = new_stop_loss_price
        old_order['stopSize'] = new_stop_size
        old_order['calculated_stop_size'] = new_stop_size
        old_order['totalQuantity'] = new_quantity  # Update quantity for TP/SL orders
        old_order['quantity'] = new_quantity  # Also update quantity field for compatibility
        Config.orderStatusData[order_id] = old_order
        
        bar_type_name = "PBe1" if is_pbe1 else "PBe2"
        logging.info(f"{bar_type_name} 30s Update: stop_size={new_stop_size} (old={old_order.get('stopSize', 'N/A')}), quantity={new_quantity} (old={old_quantity}), stop_loss={new_stop_loss_price}, LOD={lod}, HOD={hod}")
        
        # If entry is filled, update TP/SL orders with new quantity
        if old_order['status'] == 'Filled':
            # Find TP and SL orders and update their quantity
            for tp_sl_order_id, tp_sl_order_data in Config.orderStatusData.items():
                parent_id = tp_sl_order_data.get('parentOrderId')
                if parent_id == order_id:
                    ord_type = tp_sl_order_data.get('ordType')
                    if ord_type in ['TakeProfit', 'StopLoss']:
                        # Update quantity in TP/SL order data
                        tp_sl_order_data['totalQuantity'] = new_quantity
                        tp_sl_order_data['quantity'] = new_quantity
                        tp_sl_order_data['stopSize'] = new_stop_size
                        tp_sl_order_data['stopLossPrice'] = new_stop_loss_price
                        tp_sl_order_data['needs_update'] = True
                        Config.orderStatusData[tp_sl_order_id] = tp_sl_order_data
                        logging.info(f"{bar_type_name} 30s Update: Updated {ord_type} order {tp_sl_order_id} with new quantity={new_quantity}, stop_size={new_stop_size}")
                        
                        # Try to update the actual order if it's still active
                        try:
                            if tp_sl_order_data.get('status') not in ['Filled', 'Cancelled', 'Inactive']:
                                # Get the order from IB
                                ib_order = connection.ib.openOrders()
                                tp_sl_contract = tp_sl_order_data.get('contract')
                                for ib_o in ib_order:
                                    if ib_o.orderId == tp_sl_order_id:
                                        # Update order quantity
                                        ib_o.totalQuantity = new_quantity
                                        
                                        # Update stop loss price (auxPrice) if it's a stop loss order and price changed
                                        stop_price_updated = False
                                        if ord_type == 'StopLoss':
                                            old_stop_price = tp_sl_order_data.get('stopLossPrice', 0)
                                            if old_stop_price != new_stop_loss_price:
                                                ib_o.auxPrice = new_stop_loss_price
                                                stop_price_updated = True
                                                logging.info(f"{bar_type_name} 30s Update: Updating {ord_type} order {tp_sl_order_id} stop price from {old_stop_price} to {new_stop_loss_price} (HOD/LOD changed)")
                                        
                                        if tp_sl_contract:
                                            connection.ib.placeOrder(tp_sl_contract, ib_o)
                                            if stop_price_updated:
                                                logging.info(f"{bar_type_name} 30s Update: Updated {ord_type} order {tp_sl_order_id} - quantity={new_quantity}, stop_price={new_stop_loss_price} via IB")
                                            else:
                                                logging.info(f"{bar_type_name} 30s Update: Updated {ord_type} order {tp_sl_order_id} quantity to {new_quantity} via IB")
                                        break
                        except Exception as e:
                            logging.warning(f"{bar_type_name} 30s Update: Could not update {ord_type} order {tp_sl_order_id} via IB: {e}")
    except Exception as e:
        logging.error(f"Error updating PBe stop size and quantity: {e}")
        logging.error(traceback.format_exc())

async def pbe1_loop_run(connection, key, entry_order):
    """
    Monitor PBe1 entry order and continuously update entry stop price like RBB.
    Similar to rbb_loop_run but for PBe1 strategies with HOD/LOD stop loss.
    Also updates stop size and share size every 30 seconds.
    """
    order = entry_order
    last_stop_size_update = datetime.datetime.now()
    
    while True:
        try:
            await asyncio.sleep(1)
            
            # Update stop size and share size every 30 seconds
            now = datetime.datetime.now()
            if (now - last_stop_size_update).total_seconds() >= 30:
                await _update_pbe_stop_size_and_quantity(connection, order.orderId, is_pbe1=True)
                last_stop_size_update = now
            old_order = Config.orderStatusData.get(order.orderId)
            if old_order is None:
                logging.warning(f"PBe1 loop: Order {order.orderId} not found in orderStatusData")
                break
            
            # If order is cancelled or inactive, exit loop
            if old_order['status'] in ['Cancelled', 'Inactive']:
                logging.info(f"PBe1 loop: Order {order.orderId} status={old_order['status']}, exiting loop")
                break
            
            # If entry order is filled, continue loop for 30-second updates but skip entry order updates
            if old_order['status'] == 'Filled':
                # Entry order is filled - continue loop for 30-second stop size/share size updates
                # but skip entry order price updates
                await asyncio.sleep(1)
                continue
            
            # Entry order is still active - proceed with entry order updates
            if (old_order['status'] != 'Filled' and old_order['status'] != 'Cancelled' and old_order['status'] != 'Inactive'):
                logging.info(f"PBe1 loop: Monitoring order {order.orderId}, status={old_order['status']}")
                
                # Get current bar data - use getHistoricalChartDataForEntry to get the most recent bar (like PBe2)
                # This ensures we get the latest bar, not a bar at a specific time
                chartTime = getRecentChartTime(old_order['timeFrame'])
                recentBarData = connection.getHistoricalChartDataForEntry(old_order['contract'], old_order['timeFrame'], chartTime)
                if recentBarData is None or len(recentBarData) == 0:
                    logging.info("PBe1 loop: Chart Data is Not Coming for %s contract  and for %s time", old_order['contract'], chartTime)
                    await asyncio.sleep(1)
                    continue
                
                # Get the most recent CLOSED bar (previous bar, not the current forming bar)
                # Use the second-to-last bar to ensure it's definitely closed
                # Only update when a new bar closes (when the most recent closed bar changes)
                if isinstance(recentBarData, dict) and len(recentBarData) > 0:
                    sorted_keys = sorted(recentBarData.keys())
                    if len(sorted_keys) >= 2:
                        # Get the second-to-last bar (most recent closed bar)
                        prev_key = sorted_keys[-2]
                        current_bar = recentBarData.get(prev_key)
                        logging.info("PBe1: Using second-to-last bar (key=%s) as most recent closed bar", prev_key)
                    elif len(sorted_keys) == 1:
                        # Only one bar available, use it (might be the initial bar)
                        current_bar = recentBarData.get(sorted_keys[0])
                        logging.info("PBe1: Only one bar available, using it (key=%s)", sorted_keys[0])
                    else:
                        logging.warning("PBe1 loop: Not enough bars in recentBarData (need at least 1)")
                        await asyncio.sleep(1)
                        continue
                elif isinstance(recentBarData, list) and len(recentBarData) > 0:
                    if len(recentBarData) >= 2:
                        # Get the second-to-last bar (most recent closed bar)
                        current_bar = recentBarData[-2]
                        logging.info("PBe1: Using second-to-last bar (index=%s) as most recent closed bar", len(recentBarData) - 2)
                    else:
                        # Only one bar available, use it
                        current_bar = recentBarData[-1]
                        logging.info("PBe1: Only one bar available, using it (index=0)")
                else:
                    logging.warning("PBe1 loop: Could not extract current bar from recentBarData (type=%s, len=%s)", type(recentBarData), len(recentBarData) if recentBarData else 0)
                    await asyncio.sleep(1)
                    continue
                
                if not current_bar:
                    logging.warning("PBe1 loop: Current bar is None or empty")
                    await asyncio.sleep(1)
                    continue
                
                # Convert to the format expected by the rest of the code
                # getHistoricalChartDataForEntry returns 'date', not 'dateTime'
                histData = {
                    'close': current_bar.get('close', 0),
                    'open': current_bar.get('open', 0),
                    'high': current_bar.get('high', 0),
                    'low': current_bar.get('low', 0),
                    'dateTime': current_bar.get('dateTime') or current_bar.get('date')  # Handle both field names
                }
                
                # Get the last processed bar's datetime from orderStatusData (updated after each bar)
                # Note: pbe1_entry_historical_data returns 'date', rbb_entry_historical_data returns 'dateTime'
                last_processed_histData = old_order.get('histData', {})
                # Handle both 'date' (from pbe1) and 'dateTime' (from rbb) field names
                last_processed_datetime = None
                if last_processed_histData:
                    last_processed_datetime = last_processed_histData.get('dateTime') or last_processed_histData.get('date')
                # rbb_entry_historical_data returns 'dateTime', but we need to handle 'date' too
                current_bar_datetime = histData.get('dateTime') or histData.get('date')
                
                # Check if a new bar has closed (different datetime)
                should_update = False
                if last_processed_datetime and current_bar_datetime:
                    # Compare datetimes - convert to comparable format if needed
                    try:
                        # Handle datetime objects
                        if hasattr(last_processed_datetime, 'replace'):
                            last_dt = last_processed_datetime.replace(microsecond=0)
                        else:
                            last_dt = last_processed_datetime
                        
                        if hasattr(current_bar_datetime, 'replace'):
                            current_dt = current_bar_datetime.replace(microsecond=0)
                        else:
                            current_dt = current_bar_datetime
                        
                        if current_dt > last_dt:
                            # New bar closed (newer than last), update entry order
                            should_update = True
                            logging.info("PBe1: New bar closed (last=%s, new=%s), updating entry order", last_processed_datetime, current_bar_datetime)
                        elif current_dt < last_dt:
                            # Current bar is older than last processed bar, skip update (data issue or timezone issue)
                            logging.warning("PBe1: Current bar (datetime=%s) is older than last processed bar (datetime=%s), skipping update. This may indicate a data issue.", current_bar_datetime, last_processed_datetime)
                            await asyncio.sleep(1)
                            continue
                        else:
                        # Same bar, no update needed
                            logging.info("PBe1: Same bar (datetime=%s), skipping update", current_bar_datetime)
                            await asyncio.sleep(1)
                            continue
                            # Same bar, no update needed
                            logging.info("PBe1: Same bar (datetime=%s), skipping update", current_bar_datetime)
                            await asyncio.sleep(1)
                            continue
                    except Exception as e:
                        logging.warning(f"PBe1: Error comparing datetimes: {e}, skipping update")
                        await asyncio.sleep(1)
                        continue
                else:
                    # First iteration or can't compare datetimes
                    if not last_processed_datetime:
                        # First iteration: Check if current bar is newer than initial bar
                        # If current bar is same or older, skip update and wait for new bar
                        initial_histData = old_order.get('histData', {})
                        # Handle both 'date' (from pbe1) and 'dateTime' (from rbb) field names
                        initial_datetime = None
                        if initial_histData:
                            initial_datetime = initial_histData.get('dateTime') or initial_histData.get('date')
                        
                        if initial_datetime and current_bar_datetime:
                            try:
                                # Compare current bar with initial bar
                                if hasattr(initial_datetime, 'replace'):
                                    init_dt = initial_datetime.replace(microsecond=0)
                                else:
                                    init_dt = initial_datetime
                                
                                if hasattr(current_bar_datetime, 'replace'):
                                    curr_dt = current_bar_datetime.replace(microsecond=0)
                                else:
                                    curr_dt = current_bar_datetime
                                
                                if curr_dt > init_dt:
                                    # Current bar is newer, update
                                    should_update = True
                                    logging.info("PBe1: First iteration, current bar (datetime=%s) is newer than initial bar (datetime=%s), updating entry order", current_bar_datetime, initial_datetime)
                                elif curr_dt == init_dt:
                                    # Same bar as initial, skip update
                                    logging.info("PBe1: First iteration, current bar (datetime=%s) is same as initial bar, skipping update", current_bar_datetime)
                                    await asyncio.sleep(1)
                                    continue
                                else:
                                    # Current bar is older, skip update
                                    logging.info("PBe1: First iteration, current bar (datetime=%s) is older than initial bar (datetime=%s), skipping update", current_bar_datetime, initial_datetime)
                                    await asyncio.sleep(1)
                                    continue
                            except Exception as e:
                                logging.warning(f"PBe1: Error comparing initial bar datetime: {e}, skipping update")
                                await asyncio.sleep(1)
                                continue
                        else:
                            # Can't compare, skip update to be safe
                            logging.info("PBe1: First iteration, cannot compare bar datetimes, skipping update")
                            await asyncio.sleep(1)
                            continue
                    else:
                        # Can't compare datetimes, skip update
                        logging.info("PBe1: Cannot compare bar datetimes, skipping update")
                        await asyncio.sleep(1)
                        continue
                
                # Only update if should_update is True (new bar closed)
                if not should_update:
                    await asyncio.sleep(1)
                    continue
                
                # Calculate new stop price based on new bar's high/low - same as regular RBB
                # Entry stop price = bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL)
                aux_price = 0
                if old_order['userBuySell'] == 'BUY':
                    aux_price = round(float(histData['high']) + 0.01, Config.roundVal)
                    logging.info("PBe1 auxprice high for %s (new bar high=%s) - like RBB", aux_price, histData['high'])
                else:  # SELL
                    aux_price = round(float(histData['low']), Config.roundVal)
                    logging.info("PBe1 auxprice low for %s (new bar low=%s) - prior bar low", aux_price, histData['low'])
                
                # Get stored LOD/HOD for stop_size calculation
                stored_lod = old_order.get('pbe1_lod', 0)
                stored_hod = old_order.get('pbe1_hod', 0)
                
                # Calculate stop_size for limit price: |bar_high/low - HOD/LOD|
                if old_order['userBuySell'] == 'BUY':
                    bar_price = float(histData['high'])
                    stop_size = abs(bar_price - stored_lod) if stored_lod > 0 else (float(histData['high']) - float(histData['low'])) + Config.add002
                else:  # SELL
                    bar_price = float(histData['low'])
                    stop_size = abs(bar_price - stored_hod) if stored_hod > 0 else (float(histData['high']) - float(histData['low'])) + Config.add002
                
                stop_size = round(stop_size, Config.roundVal)
                if stop_size <= 0:
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    stop_size = round(stop_size, Config.roundVal)
                
                logging.info("PBe1 going to update entry order for newprice %s old_order %s", aux_price, order)
                order.auxPrice = aux_price
                old_orderId = order.orderId
                
                # Check if the order still exists and is active before trying to cancel
                current_order_status = old_order.get('status', '')
                if current_order_status in ['Filled', 'Cancelled', 'Inactive']:
                    logging.warning(f"PBe1: Order {old_orderId} status is {current_order_status}, cannot update. Exiting loop.")
                    break
                
                # For extended hours: Use stop-limit order
                # For RTH: Use stop order
                is_extended, _ = _is_extended_outside_rth(old_order.get('outsideRth', False))
                if is_extended:
                    # Calculate limit price for stop limit order using 0.5 × stop_size
                    entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                    
                    # Ensure minimum offset of 0.01 to avoid limit = stop
                    min_limit_offset = 0.01
                    if entry_limit_offset < min_limit_offset:
                        entry_limit_offset = min_limit_offset
                        logging.warning(f"PBe1 Extended hours: stop_size too small ({stop_size}), using minimum limit offset={min_limit_offset}")
                    
                    if old_order['userBuySell'] == 'BUY':
                        limit_price = aux_price + entry_limit_offset
                    else:
                        limit_price = aux_price - entry_limit_offset
                    
                    limit_price = round(limit_price, Config.roundVal)
                    logging.info(f"PBe1 Extended hours Update: STP LMT, Stop={aux_price} (bar_high/low ± 0.01), Limit={limit_price} (stop ± 0.5×stop_size={entry_limit_offset}), stop_size={stop_size}")
                    new_order = Order(orderType="STP LMT", action=order.action, totalQuantity=order.totalQuantity, 
                                    tif='DAY', auxPrice=aux_price, lmtPrice=limit_price)
                else:
                    # RTH: Update only entry order (TP/SL are sent separately after fill)
                    logging.info(f"PBe1 RTH Update: STP, Stop={aux_price} (bar_high/low ± 0.01)")
                    new_order = Order(orderType="STP", action=order.action, totalQuantity=order.totalQuantity,  
                                    tif='DAY', auxPrice=aux_price)
                
                # Try to cancel the old order (may fail if already cancelled, but that's okay)
                try:
                    connection.cancelTrade(order)
                    logging.info(f"PBe1: Cancelled old order {old_orderId}")
                except Exception as e:
                    logging.warning(f"PBe1: Could not cancel old order {old_orderId} (may already be cancelled): {e}")
                
                response = connection.placeTrade(contract=old_order['contract'], order=new_order, 
                                               outsideRth=old_order.get('outsideRth', False))
                logging.info("PBe1 response of updating entry order %s ", response)
                order = response.order
                new_orderId = int(response.order.orderId)
                
                # Update orderStatusData: remove old entry and add new one
                if old_orderId in Config.orderStatusData:
                    d = Config.orderStatusData[old_orderId]
                    # Update histData to current bar so next iteration can detect the next new bar
                    d['histData'] = histData
                    d['orderId'] = new_orderId
                    d['status'] = response.orderStatus.status
                    d['lastPrice'] = round(aux_price, Config.roundVal)
                    # Move data to new orderId
                    Config.orderStatusData[new_orderId] = d
                    # Remove old orderId if different
                    if old_orderId != new_orderId:
                        del Config.orderStatusData[old_orderId]
                        logging.info(f"PBe1: Moved orderStatusData from old orderId {old_orderId} to new orderId {new_orderId}")
                    old_order = d  # Update old_order for next iteration
                    # Handle both 'date' and 'dateTime' field names for logging
                    bar_datetime = histData.get('dateTime') or histData.get('date')
                    logging.info("PBe1: Updated orderStatusData with new bar data (datetime=%s) for continuous entry order updates", bar_datetime)
                else:
                    logging.warning(f"PBe1: Old orderId {old_orderId} not found in orderStatusData, creating new entry")
                    # Create new entry for the new order
                    d = old_order.copy()
                    d['histData'] = histData
                    d['orderId'] = new_orderId
                    d['status'] = response.orderStatus.status
                    d['lastPrice'] = round(aux_price, Config.roundVal)
                    Config.orderStatusData[new_orderId] = d
                    old_order = d
                    logging.info("PBe1: Created new orderStatusData entry for orderId %s", new_orderId)
                
                # CRITICAL: Update order.orderId so next iteration uses the new order ID
                order.orderId = new_orderId
                logging.info(f"PBe1: Updated order.orderId from {old_orderId} to {new_orderId} for next iteration")
                
                # Check if current bar breaks HOD/LOD (for stop loss update)
                current_bar_high = float(histData.get('high', 0))
                current_bar_low = float(histData.get('low', 0))
                
                hod_broken = current_bar_high > stored_hod
                lod_broken = current_bar_low < stored_lod
                
                if hod_broken or lod_broken:
                    # IMPORTANT: When HOD/LOD breaks, we update stored LOD/HOD values
                    # but we do NOT update the entry order price (aux_price) - it only updates on new bar
                    # Stop size and share size will be updated by the 30-second update function
                    new_hod = max(stored_hod, current_bar_high) if hod_broken else stored_hod
                    new_lod = min(stored_lod, current_bar_low) if lod_broken else stored_lod
                    
                    logging.info(f"PBe1 HOD/LOD BREAK: current_bar_high={current_bar_high}, current_bar_low={current_bar_low}, old_HOD={stored_hod}, old_LOD={stored_lod}, new_HOD={new_hod}, new_LOD={new_lod}")
                    logging.info(f"PBe1: Entry order price (aux_price={aux_price}) NOT changed - only updates on new bar. Stop size/share size will update every 30 seconds.")
                    
                    # Update stored LOD/HOD values (stop size/share size will be recalculated by 30-second update)
                    old_order = Config.orderStatusData.get(order.orderId)  # Refresh
                    if old_order:
                        old_order['pbe1_lod'] = new_lod
                        old_order['pbe1_hod'] = new_hod
                        Config.orderStatusData[order.orderId] = old_order
                        
                        # Note: Stop size and share size will be updated by _update_pbe_stop_size_and_quantity() every 30 seconds
                        # We don't update them here to avoid conflicts with the 30-second update cycle
                
        except Exception as e:
            logging.error(f"PBe1 loop error: {e}")
            logging.error(traceback.format_exc())
            await asyncio.sleep(1)
            continue
        
        await asyncio.sleep(1)

def _get_current_session():
    now = datetime.datetime.now().time().replace(microsecond=0)
    pre_start = datetime.time(4, 0, 0)
    rth_start = datetime.time(9, 30, 0)
    rth_end = datetime.time(16, 0, 0)
    after_end = datetime.time(20, 0, 0)
    if rth_start <= now < rth_end:
        return 'RTH'
    if pre_start <= now < rth_start:
        return 'PREMARKET'
    if rth_end <= now < after_end:
        return 'AFTERHOURS'
    return 'OVERNIGHT'

def _get_lod_hod_for_stop_loss(connection, contract, timeFrame):
    """
    Get Low of Day (LOD) and High of Day (HOD) for stop loss calculation.
    
    For premarket: Uses premarket data (bars from 04:00 to 09:30)
    For after hours: Uses RTH data (bars from 09:30 onwards)
    Otherwise: Uses RTH data (bars from 09:30 onwards)
    
    Args:
        connection: IB connection
        contract: Contract object
        timeFrame: Time frame
    
    Returns:
        (lod, hod, recent_bar_data): Tuple of LOD, HOD, and the bar data used
    """
    session = _get_current_session()
    today = datetime.datetime.now().date()
    pre_start = datetime.time(4, 0, 0)
    rth_start = datetime.time(9, 30, 0)
    
    if session == 'PREMARKET':
        # For premarket: use premarket data (04:00 to 09:30)
        logging.info("PREMARKET: Using premarket data for HOD/LOD calculation")
        raw_hist_data = connection.getChartData(contract, timeFrame, None)
        
        if raw_hist_data and len(raw_hist_data) > 0:
            low_value = float('inf')
            high_value = float('-inf')  # Initialize to negative infinity to find maximum
            bars_count = 0
            recent_bar_data = {}
            
            # Filter to only today's premarket bars (04:00 to 09:30)
            for i, bar in enumerate(raw_hist_data):
                bar_date = bar.date.date()
                bar_time = bar.date.time()
                if bar_date == today and pre_start <= bar_time < rth_start:
                    bar_low = float(bar.low)
                    bar_high = float(bar.high)
                    if bar_low < low_value:
                        low_value = bar_low
                    if bar_high > high_value:
                        high_value = bar_high
                    # Store bar data in dict format for compatibility
                    recent_bar_data[bars_count] = {
                        'low': bar_low,
                        'high': bar_high,
                        'open': float(bar.open),
                        'close': float(bar.close),
                        'date': bar.date
                    }
                    bars_count += 1
            
            if low_value != float('inf') and high_value != float('-inf'):
                lod = low_value
                hod = high_value
                
                # Validation: LOD should always be less than or equal to HOD
                if lod > hod:
                    logging.error(f"PREMARKET HOD/LOD ERROR: LOD ({lod}) > HOD ({hod})! This should never happen. Swapping values.")
                    lod, hod = hod, lod  # Swap if incorrectly calculated
                
                logging.info(f"PREMARKET HOD/LOD: LOD={lod}, HOD={hod} (from {bars_count} premarket bars)")
                return lod, hod, recent_bar_data
            else:
                logging.warning(f"PREMARKET: No premarket bars found for today")
    
    # For AFTERHOURS or other sessions: use RTH data (09:30 onwards)
    if session == 'AFTERHOURS':
        logging.info("AFTERHOURS: Using RTH data for HOD/LOD calculation")
    else:
        logging.info(f"{session}: Using RTH data for HOD/LOD calculation")
    
    # Use RTH data (bars from 09:30 onwards)
    chart_Time = datetime.datetime.strptime(str(today) + " " + Config.tradingTime, "%Y-%m-%d %H:%M:%S")
    recent_bar_data = connection.getHistoricalChartDataForEntry(contract, timeFrame, chart_Time)
    
    if recent_bar_data and len(recent_bar_data) > 0:
        # Initialize with first bar's values
        first_bar_low = float(recent_bar_data.get(0)['low'])
        first_bar_high = float(recent_bar_data.get(0)['high'])
        low_value = first_bar_low
        high_value = first_bar_high
        
        # Find minimum low (LOD) and maximum high (HOD) across all bars
        for data in range(1, len(recent_bar_data)):
            bar_low = float(recent_bar_data.get(data)['low'])
            bar_high = float(recent_bar_data.get(data)['high'])
            if bar_low < low_value:
                low_value = bar_low
            if bar_high > high_value:
                high_value = bar_high
        
        lod = float(low_value)
        hod = float(high_value)
        
        # Validation: LOD should always be less than or equal to HOD
        if lod > hod:
            logging.error(f"RTH HOD/LOD ERROR: LOD ({lod}) > HOD ({hod})! This should never happen. Swapping values.")
            lod, hod = hod, lod  # Swap if incorrectly calculated
        
        logging.info(f"RTH HOD/LOD: LOD={lod}, HOD={hod} (from {len(recent_bar_data)} RTH bars)")
        return lod, hod, recent_bar_data
    
    # Fallback: try getChartData and filter for RTH bars
    logging.warning("RTH data not available via getHistoricalChartDataForEntry, trying getChartData as fallback")
    raw_hist_data = connection.getChartData(contract, timeFrame, None)
    
    if raw_hist_data and len(raw_hist_data) > 0:
        low_value = float('inf')
        high_value = float('-inf')  # Initialize to negative infinity to find maximum
        bars_count = 0
        recent_bar_data = {}
        
        # Filter to only today's RTH bars (09:30 onwards)
        for i, bar in enumerate(raw_hist_data):
            bar_date = bar.date.date()
            bar_time = bar.date.time()
            if bar_date == today and bar_time >= rth_start:
                bar_low = float(bar.low)
                bar_high = float(bar.high)
                if bar_low < low_value:
                    low_value = bar_low
                if bar_high > high_value:
                    high_value = bar_high
                # Store bar data in dict format for compatibility
                recent_bar_data[bars_count] = {
                    'low': bar_low,
                    'high': bar_high,
                    'open': float(bar.open),
                    'close': float(bar.close),
                    'date': bar.date
                }
                bars_count += 1
        
        if low_value != float('inf') and high_value != float('-inf'):
            lod = low_value
            hod = high_value
            
            # Validation: LOD should always be less than or equal to HOD
            if lod > hod:
                logging.error(f"RTH HOD/LOD (fallback) ERROR: LOD ({lod}) > HOD ({hod})! This should never happen. Swapping values.")
                lod, hod = hod, lod  # Swap if incorrectly calculated
            
            logging.info(f"RTH HOD/LOD (fallback): LOD={lod}, HOD={hod} (from {bars_count} RTH bars)")
            return lod, hod, recent_bar_data
    
    logging.warning("No historical data available for HOD/LOD calculation")
    return None, None, {}

async def SendTrade(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue , breakEven , outsideRth,entry_points, option_enabled=False, option_contract="", option_expire="", option_entry_order_type="Market", option_sl_order_type="Market", option_tp_order_type="Market", option_risk_amount=""):
    try:
        symbol=symbol.upper()
        if entry_points == "":
            entry_points = 0
        logging.info("sending trade %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s option_enabled=%s option_contract=%s option_expire=%s option_risk_amount=%s",  
                    symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue , breakEven , outsideRth,entry_points,
                    option_enabled, option_contract, option_expire, option_risk_amount)
        logging.info("SendTrade: barType='%s', Config.entryTradeType=%s", barType, Config.entryTradeType)
        logging.info("SendTrade: Checking routing - barType='%s', entryTradeType[6]='%s'", barType, Config.entryTradeType[6] if len(Config.entryTradeType) > 6 else "N/A")

        # Validate Time In Force: if outside trading hours, must be OTH
        session = _get_current_session()
        if outsideRth and tif != 'OTH':
            error_msg = f"Order rejected: Orders outside trading hours require 'OTH' (Outside Trading Hours) in Time In Force. Current session: {session}, Time In Force: {tif}"
            logging.error(error_msg)
            print(error_msg)
            return
        
        # Convert OTH to DAY for IB (OTH is just a validation flag, IB uses DAY for outside hours)
        # Replace tif with DAY if it's OTH, so all subsequent code uses the correct IB value
        if tif == 'OTH':
            logging.info(f"Converting OTH to DAY for IB order placement (session: {session})")
            tif = 'DAY'

        # Enforce client trading-session rules
        logging.info("Current session detected: %s, outsideRth flag: %s", session, outsideRth)
        print(f"Current trading session: {session} (outsideRth={outsideRth})")
        
        if outsideRth:
            if session in ('PREMARKET', 'AFTERHOURS'):
                # Allow RB/RBB, Conditional Order, manual order types, and PBe1/PBe2
                allowed_types = (Config.entryTradeType[2],  # Conditional Order
                                Config.entryTradeType[4], Config.entryTradeType[5],  # RB, RBB (indices shifted after adding Conditional Order)
                                Config.entryTradeType[6], Config.entryTradeType[7]) + MANUAL_ORDER_TYPES  # PBe1, PBe2
                logging.info("Session check: session=%s, barType='%s', allowed_types=%s, barType in allowed_types=%s", session, barType, allowed_types, barType in allowed_types)
                if barType not in allowed_types:
                    logging.warning("%s session: Only Conditional Order, RB/RBB, PBe1/PBe2, or manual orders allowed; skipping barType %s", session, barType)
                    return
            elif session == 'OVERNIGHT':
                # Overnight: all strategies allowed, but orders will be converted to limit types
                logging.info("OVERNIGHT session: All strategies allowed, order types will be converted to limit-style")
            # Overnight: strategies allowed, order-type handling is done in placeTrade
        else:
            # If outsideRth is False but we're in an extended hours session, warn
            if session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT'):
                logging.warning("Session is %s but outsideRth=False. Consider setting outsideRth=True for extended hours trading.", session)
        # Handle option trading if enabled (BEFORE routing to avoid early returns)
        # Note: Option orders will be placed after the stock entry order fills
        # The option parameters are stored and will be used in StatusUpdate when entry fills
        # Note: option_expire can be 0 (current week), so check for not None/empty string instead of truthiness
        if option_enabled and option_contract and (option_expire is not None and option_expire != ""):
            logging.info("Option trading enabled for %s: contract=%s, expire=%s, entry_order_type=%s, sl_order_type=%s, tp_order_type=%s, risk_amount=%s", 
                        symbol, option_contract, option_expire, option_entry_order_type, option_sl_order_type, option_tp_order_type, option_risk_amount)
            # Store option parameters in Config for later use when entry fills
            trade_key = (symbol, timeFrame, barType, buySellType, datetime.datetime.now().timestamp())
            Config.option_trade_params = Config.option_trade_params if hasattr(Config, 'option_trade_params') else {}
            Config.option_trade_params[trade_key] = {
                'enabled': True,
                'contract': option_contract,
                'expire': option_expire,
                'entry_order_type': option_entry_order_type,
                'sl_order_type': option_sl_order_type,
                'tp_order_type': option_tp_order_type,
                'risk_amount': option_risk_amount,
            }
            logging.info("Option trading parameters stored for trade key: %s", trade_key)

        if barType == 'Limit Order':
            logging.info("Routing to manual_limit_order for barType='%s'", barType)
            await manual_limit_order(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                                     atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points)
            return
        elif barType == 'Custom':
            logging.info("Routing to manual_stop_order for barType='%s'", barType)
            await manual_stop_order(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                                    atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points)
            return
        
        # Check all trade types
        logging.info("Checking trade type routing: barType='%s'", barType)
        if barType == Config.entryTradeType[3]:  # FB
            logging.info("Routing to first_bar_fb for barType='%s' (Config.entryTradeType[3]='%s')", barType, Config.entryTradeType[3])
            await (first_bar_fb(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[2]:  # Conditional Order
            logging.info("Routing to conditional_order for barType='%s'", barType)
            await (conditional_order(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points))
        elif barType == Config.entryTradeType[4] or barType == Config.entryTradeType[5]:  # RB or RBB (indices shifted after adding Conditional Order)
            logging.info("Routing to rb_and_rbb for barType='%s'", barType)
            await (rb_and_rbb(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage,quantity, pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[6]:  # PBe1
            logging.info("Routing to pull_back_PBe1 for barType='%s' (Config.entryTradeType[6]='%s')", barType, Config.entryTradeType[6])
            await (pull_back_PBe1(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[7]:  # PBe2
            logging.info("Routing to pull_back_PBe2 for barType='%s' (Config.entryTradeType[7]='%s')", barType, Config.entryTradeType[7])
            await (pull_back_PBe2(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue ,breakEven,outsideRth))
        elif barType == Config.entryTradeType[8]:  # LB
            logging.info("Routing to lb1 for barType='%s' (Config.entryTradeType[8]='%s')", barType, Config.entryTradeType[8])
            await (lb1(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage,quantity, pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[9]:  # LB2
            logging.info("Routing to lb2 for barType='%s' (Config.entryTradeType[9]='%s')", barType, Config.entryTradeType[9])
            await (lb2(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                                  atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points))
        elif barType == Config.entryTradeType[10]:  # LB3
            logging.info("Routing to lb3 for barType='%s' (Config.entryTradeType[10]='%s')", barType, Config.entryTradeType[10])
            await (lb3(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                                  atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points))

        logging.info("task done for %s symbol",symbol)

    except Exception as e:
        logging.error("error in sending mkt trade %s", e)
        logging.error(traceback.format_exc())
        traceback.print_exc()


def _get_pbe1_lod_hod(connection, contract, timeFrame, tradeType):
    """
    Get Low of Day (LOD) for long positions or High of Day (HOD) for short positions.
    This is specifically for PBe1 which always uses LOD/HOD regardless of stop loss type.
    
    For premarket: Uses premarket data (bars from 04:00 to 09:30)
    For after hours: Uses RTH data (bars from 09:30 onwards)
    Otherwise: Uses RTH data (bars from 09:30 onwards)
    
    Args:
        connection: IB connection
        contract: Contract object
        timeFrame: Time frame
        tradeType: 'BUY' (use LOD) or 'SELL' (use HOD)
    
    Returns:
        (lod, hod): Tuple of LOD and HOD values
    """
    try:
        session = _get_current_session()
        today = datetime.datetime.now().date()
        pre_start = datetime.time(4, 0, 0)
        rth_start = datetime.time(9, 30, 0)
        
        if session == 'PREMARKET':
            # For premarket: use premarket data (04:00 to 09:30)
            logging.info("PBe1 PREMARKET: Using premarket data for HOD/LOD calculation")
        raw_hist_data = connection.getChartData(contract, timeFrame, None)
        
        if raw_hist_data and len(raw_hist_data) > 0:
            low_value = float('inf')
            high_value = float('-inf')
            bars_count = 0
            
            # Filter to only today's premarket bars (04:00 to 09:30)
            for bar in raw_hist_data:
                bar_date = bar.date.date()
                bar_time = bar.date.time()
                if bar_date == today and pre_start <= bar_time < rth_start:
                    bar_low = float(bar.low)
                    bar_high = float(bar.high)
                    if bar_low < low_value:
                        low_value = bar_low
                    if bar_high > high_value:
                        high_value = bar_high
                    bars_count += 1
            
            if low_value != float('inf') and high_value != float('-inf'):
                lod = low_value
                hod = high_value
                
                # Validation: LOD should always be less than or equal to HOD
                if lod > hod:
                    logging.error(f"PBe1 PREMARKET HOD/LOD ERROR: LOD ({lod}) > HOD ({hod})! This should never happen. Swapping values.")
                    lod, hod = hod, lod
                
                logging.info(f"PBe1 PREMARKET HOD/LOD: LOD={lod}, HOD={hod} (from {bars_count} premarket bars)")
                return lod, hod
            else:
                    logging.warning(f"PBe1 PREMARKET: No premarket bars found for today")
        
        # For AFTERHOURS or other sessions: use RTH data (09:30 onwards)
        if session == 'AFTERHOURS':
            logging.info("PBe1 AFTERHOURS: Using RTH data for HOD/LOD calculation")
        else:
            logging.info(f"PBe1 {session}: Using RTH data for HOD/LOD calculation")
        
        # Use RTH data (bars from 09:30 onwards)
        chart_Time = datetime.datetime.strptime(str(today) + " " + Config.tradingTime, "%Y-%m-%d %H:%M:%S")
        recent_bar_data = connection.getHistoricalChartDataForEntry(contract, timeFrame, chart_Time)
        
        if recent_bar_data and len(recent_bar_data) > 0:
            # Initialize with first bar's values
            first_bar_low = float(recent_bar_data.get(0)['low'])
            first_bar_high = float(recent_bar_data.get(0)['high'])
            low_value = first_bar_low
            high_value = first_bar_high
            
            # Find minimum low (LOD) and maximum high (HOD) across all bars
            for data in range(1, len(recent_bar_data)):
                bar_low = float(recent_bar_data.get(data)['low'])
                bar_high = float(recent_bar_data.get(data)['high'])
                if bar_low < low_value:
                        low_value = bar_low
                if bar_high > high_value:
                        high_value = bar_high
            
            lod = float(low_value)
            hod = float(high_value)
            
            # Validation: LOD should always be less than or equal to HOD
            if lod > hod:
                logging.error(f"PBe1 RTH HOD/LOD ERROR: LOD ({lod}) > HOD ({hod})! This should never happen. Swapping values.")
                lod, hod = hod, lod
            
            logging.info(f"PBe1 RTH HOD/LOD: LOD={lod}, HOD={hod} (from {len(recent_bar_data)} RTH bars)")
            return lod, hod
        
        # Fallback: try getChartData and filter for RTH bars
        logging.warning("PBe1 RTH data not available via getHistoricalChartDataForEntry, trying getChartData as fallback")
        raw_hist_data = connection.getChartData(contract, timeFrame, None)
        
        if raw_hist_data and len(raw_hist_data) > 0:
            low_value = float('inf')
            high_value = float('-inf')
            bars_count = 0
            
            # Filter to only today's RTH bars (09:30 onwards)
            for bar in raw_hist_data:
                bar_date = bar.date.date()
                bar_time = bar.date.time()
                if bar_date == today and bar_time >= rth_start:
                    bar_low = float(bar.low)
                    bar_high = float(bar.high)
                    if bar_low < low_value:
                        low_value = bar_low
                    if bar_high > high_value:
                        high_value = bar_high
                    bars_count += 1
            
            if low_value != float('inf') and high_value != float('-inf'):
                lod = low_value
                hod = high_value
                
                # Validation: LOD should always be less than or equal to HOD
                if lod > hod:
                    logging.error(f"PBe1 RTH HOD/LOD (fallback) ERROR: LOD ({lod}) > HOD ({hod})! This should never happen. Swapping values.")
                    lod, hod = hod, lod
                
                logging.info(f"PBe1 RTH HOD/LOD (fallback): LOD={lod}, HOD={hod} (from {bars_count} RTH bars)")
            return lod, hod
        
        logging.warning("No historical data available for PBe1 LOD/HOD calculation")
        return 0, 0
    except Exception as e:
        logging.warning(f"Error calculating PBe1 LOD/HOD: {e}")
        return 0, 0

def _calculate_pbe_stop_loss(connection, contract, entry_price, stopLoss, tradeType, histData, timeFrame, slValue, is_pbe1=False):
    """
    Calculate stop loss price and stop size for PBe1/PBe2.
    
    IMPORTANT: PB strategies (PBe1, PBe2) ALWAYS use LOD/HOD for stop loss.
    The stopLoss and slValue parameters are IGNORED - they are kept for API compatibility only.
    PB logic inherently uses LOD/HOD, so stop loss type selection in the UI is not needed.
    
    Args:
        connection: IB connection
        contract: Contract object
        entry_price: Entry price (bar_high for BUY, bar_low for SELL)
        stopLoss: Stop loss type from Config.stopLoss (IGNORED - kept for compatibility)
        tradeType: 'BUY' or 'SELL'
        histData: Historical bar data
        timeFrame: Time frame
        slValue: Custom stop loss value (IGNORED - kept for compatibility)
        is_pbe1: Always True for PB strategies - forces LOD/HOD usage
    
    Returns:
        (stop_loss_price, stop_size): Tuple of stop loss price and stop size
    """
    # PB strategies (PBe1, PBe2): ALWAYS use LOD for BUY, HOD for SELL
    # IMPORTANT: stopLoss and slValue parameters are IGNORED - only HOD/LOD is used
    # This is by design - PB logic inherently uses LOD/HOD, so stop loss type selection is not needed
    lod, hod = _get_pbe1_lod_hod(connection, contract, timeFrame, tradeType)
    if tradeType == 'BUY':
        stop_loss_price = lod
    else:  # SELL
        stop_loss_price = hod
    
    # Calculate stop_size: |bar_high/low - LOD/HOD|
    # For PB: entry_price is bar_high (BUY) or bar_low (SELL)
    stop_size = abs(entry_price - stop_loss_price)
    stop_loss_price = round(stop_loss_price, Config.roundVal)
    stop_size = round(stop_size, Config.roundVal)
    logging.info(f"PB strategy (PBe1/PBe2): ALWAYS uses LOD/HOD - entry={entry_price}, stop={stop_loss_price} (LOD={lod}, HOD={hod}), stop_size={stop_size}. UI stopLoss/slValue ignored.")
    return stop_loss_price, stop_size
    
def _calculate_manual_stop_loss(connection, contract, entry_price, stopLoss, buySellType, timeFrame, slValue):
    """Calculate stop loss for manual orders."""
    histData = connection.getHistoricalChartData(contract, timeFrame, None)
    if not histData or len(histData) == 0:
        logging.warning("No historical data for manual stop loss calculation")
        return entry_price - 0.5, 0.5  # Fallback
    
    bar_high = float(histData.get('high', 0))
    bar_low = float(histData.get('low', 0))
    
    if stopLoss == Config.stopLoss[0]:  # EntryBar
        # EntryBar: stop = low (long) or high (short), stop_size = high-low+0.02
        if buySellType == 'BUY':
            stop_loss_price = bar_low
        else:  # SELL
            stop_loss_price = bar_high
        stop_size = (bar_high - bar_low) + Config.add002
        logging.info(f"Manual EntryBar: entry={entry_price}, stop={stop_loss_price}, stop_size={stop_size}")
    
    elif stopLoss == Config.stopLoss[2]:  # HOD
        # HOD: stop = HOD, stop_size = abs(entry - HOD)
        try:
            # Get historical data to find highest high
            recent_bar_data = connection.getHistoricalChartDataForEntry(contract, timeFrame, None)
            if recent_bar_data and len(recent_bar_data) > 0:
                high_value = 0
                for data in range(0, len(recent_bar_data)):
                    if high_value == 0 or high_value < recent_bar_data.get(data)['high']:
                        high_value = recent_bar_data.get(data)['high']
                stop_loss_price = float(high_value)
            else:
                stop_loss_price = bar_high
        except Exception as e:
            logging.warning(f"Error calculating HOD, using bar high as fallback: {e}")
            stop_loss_price = bar_high
        stop_size = abs(entry_price - stop_loss_price)
        logging.info(f"Manual HOD: entry={entry_price}, stop={stop_loss_price}, stop_size={stop_size}")
    
    elif stopLoss == Config.stopLoss[3] or stopLoss == Config.stopLoss[4]:  # LOD or HOD
        # LOD: stop = LOD, stop_size = abs(entry - LOD)
        try:
            # Get historical data to find lowest low
            recent_bar_data = connection.getHistoricalChartDataForEntry(contract, timeFrame, None)
            if recent_bar_data and len(recent_bar_data) > 0:
                low_value = 0
                for data in range(0, len(recent_bar_data)):
                    if low_value == 0 or low_value > recent_bar_data.get(data)['low']:
                        low_value = recent_bar_data.get(data)['low']
                stop_loss_price = float(low_value)
            else:
                stop_loss_price = bar_low
        except Exception as e:
            logging.warning(f"Error calculating LOD, using bar low as fallback: {e}")
            stop_loss_price = bar_low
        stop_size = abs(entry_price - stop_loss_price)
        logging.info(f"Manual LOD: entry={entry_price}, stop={stop_loss_price}, stop_size={stop_size}")
    
    elif stopLoss == Config.stopLoss[1]:  # Custom
        # Custom: stop = Custom, stop_size = abs(entry - Custom)
        custom_stop = _to_float(slValue, 0)
        if custom_stop == 0:
            logging.warning("Custom stop loss value missing for manual order, using bar range as fallback")
            if buySellType == 'BUY':
                stop_loss_price = bar_low
            else:  # SELL
                stop_loss_price = bar_high
            stop_size = (bar_high - bar_low) + Config.add002
        else:
            stop_loss_price = custom_stop
            stop_size = abs(entry_price - custom_stop)
        logging.info(f"Manual Custom: entry={entry_price}, stop={stop_loss_price} (custom={custom_stop}), stop_size={stop_size}")
    
    else:
        # Default: EntryBar logic
        if buySellType == 'BUY':
            stop_loss_price = bar_low
        else:  # SELL
            stop_loss_price = bar_high
        stop_size = (bar_high - bar_low) + Config.add002
        logging.warning(f"Unknown stop loss type {stopLoss} for manual order, using EntryBar logic")
    
    stop_loss_price = round(stop_loss_price, Config.roundVal)
    stop_size = round(stop_size, Config.roundVal)
    return stop_loss_price, stop_size

def sendEntryTrade(connection,ibcontract,tradeType,quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,tif,barType,userBuySell,userAtr,slValue=0,breakEven=False ,outsideRth=False,entry_points='0'):
    try:
        logging.info(f"sendEntryTrade CALLED: barType='{barType}', Config.entryTradeType[6]='{Config.entryTradeType[6] if len(Config.entryTradeType) > 6 else 'N/A'}', Config.entryTradeType[7]='{Config.entryTradeType[7] if len(Config.entryTradeType) > 7 else 'N/A'}'")
        current_session = _get_current_session()
        print(f"Placing order in session: {current_session} (outsideRth={outsideRth})")
        if barType == Config.entryTradeType[3]:  # FB
            tp,sl = TpSlForFB(connection,ibcontract,tradeType,quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,tif,barType,userBuySell,userAtr,slValue,breakEven,outsideRth)
            logging.info(f"tp %s sl %s found for ,ibcontract %s ,tradeType %s ,quantity %s ,histData %s ,lastPrice %s , symbol %s ,timeFrame %s ,profit %s ,stopLoss %s ,risk %s ,tif %s ,barType %s ,userBuySell %s ,userAtr %s ,slValue %s ,breakEven %s ,outsideRth %s ",tp,sl ,ibcontract,tradeType,quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,tif,barType,userBuySell,userAtr,slValue,breakEven,outsideRth )
            parentOrderId = random.randint(3, 500)+int(datetime.datetime.now().time().hour)+int(datetime.datetime.now().time().minute)+int(datetime.datetime.now().time().second)+int(datetime.datetime.now().time().microsecond)
            br_order = connection.ib.bracketOrder( userBuySell, quantity, tp, tp, sl)
            logging.info(f"bracket order ------------------  %s ",br_order)
            ord = br_order[0]
            # Log bar high and low values for review
            bar_high = float(histData.get('high', 0))
            bar_low = float(histData.get('low', 0))
            logging.info(f"ENTRY ORDER (FB) - Bar values: Bar's high={bar_high}, Bar's low={bar_low}, range={bar_high - bar_low} for {symbol} {tradeType}")
            
            is_extended, session = _is_extended_outside_rth(outsideRth)
            entry_order_type = 'STP'
            entry_kwargs = dict(
                orderId=ord.orderId,
                orderType='STP',
                auxPrice=round(lastPrice, Config.roundVal),
                totalQuantity=ord.totalQuantity,
                action=ord.action,
                transmit=True
            )
            stop_order = br_order[2]
            stop_order_type = 'STP'
            stop_order.auxPrice = round(sl, Config.roundVal)
            stop_order.orderType = 'STP'
            stop_order.lmtPrice = getattr(stop_order, "lmtPrice", 0.0)

            if is_extended:
                # For Custom stop loss in extended hours: Calculate entry price from bar high/low
                # Entry price: Low - 0.01 for long (BUY), High + 0.01 for short (SELL)
                # Entry limit price: entry_price ± 0.5 * stop_size
                if stopLoss == Config.stopLoss[1]:  # 'Custom'
                    custom_stop = _to_float(slValue, 0)
                    if custom_stop == 0:
                        logging.error("Custom stop loss requires a valid value for FB %s in extended hours %s", stopLoss, symbol)
                        return
                    
                    # Calculate entry price from bar high/low
                    if tradeType == 'BUY':
                        # For BUY (long): entry_price = bar_low - 0.01
                        entry_kwargs['auxPrice'] = round(bar_low - 0.01, Config.roundVal)
                    else:  # SELL
                        # For SELL (short): entry_price = bar_high + 0.01
                        entry_kwargs['auxPrice'] = round(bar_high + 0.01, Config.roundVal)
                    
                    # Calculate stop_size: |entry_price - custom_stop| + 0.02
                    stop_size = abs(entry_kwargs['auxPrice'] - custom_stop) + 0.02
                    stop_size = round(stop_size, Config.roundVal)
                    
                    if stop_size <= 0:
                        logging.error("Stop size invalid (%s) for custom stop loss %s in extended hours FB %s", stop_size, custom_stop, symbol)
                        return
                    
                    # Calculate entry limit price: entry_price ± 0.5 * stop_size
                    entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                    if tradeType == 'BUY':
                        entry_limit = round(entry_kwargs['auxPrice'] + entry_limit_offset, Config.roundVal)
                    else:  # SELL
                        entry_limit = round(entry_kwargs['auxPrice'] - entry_limit_offset, Config.roundVal)
                    
                    # Protection order limit uses existing logic
                    _, _, protection_offset = _calculate_stop_limit_offsets(histData)
                    if tradeType == 'BUY':
                        protection_limit = stop_order.auxPrice - protection_offset
                    else:
                        protection_limit = stop_order.auxPrice + protection_offset
                    
                    logging.info(f"FB Extended hours Custom: entry_price={entry_kwargs['auxPrice']} (from bar high/low), custom_stop={custom_stop}, stop_size={stop_size}, entry_limit={entry_limit}")
                else:
                    # For other stop loss types: use existing logic
                    _, entry_offset, protection_offset = _calculate_stop_limit_offsets(histData)
                if tradeType == 'BUY':
                    entry_limit = entry_kwargs['auxPrice'] + entry_offset
                    protection_limit = stop_order.auxPrice - protection_offset
                else:
                    entry_limit = entry_kwargs['auxPrice'] - entry_offset
                    protection_limit = stop_order.auxPrice + protection_offset

                entry_order_type = 'STP LMT'
                entry_kwargs['orderType'] = 'STP LMT'
                entry_kwargs['lmtPrice'] = round(entry_limit, Config.roundVal)

                stop_order_type = 'STP LMT'
                stop_order.orderType = 'STP LMT'
                stop_order.lmtPrice = round(protection_limit, Config.roundVal)

                logging.info(
                    "Extended hours bracket: entry %s stop=%s limit=%s | protection %s stop=%s limit=%s",
                    entry_order_type,
                    entry_kwargs['auxPrice'],
                    entry_kwargs['lmtPrice'],
                    stop_order_type,
                    stop_order.auxPrice,
                    stop_order.lmtPrice
                )
            else:
                # Ensure limit price is cleared in regular hours
                if hasattr(stop_order, "lmtPrice"):
                    stop_order.lmtPrice = 0.0

            ent_order = Order(**entry_kwargs)
            stop_order.auxPrice = round(stop_order.auxPrice, Config.roundVal)
            if stop_order_type == 'STP':
                stop_order.lmtPrice = getattr(stop_order, "lmtPrice", 0.0)


            entry_res = connection.placeTrade(contract=ibcontract, order=ent_order, outsideRth=outsideRth)
            logging.info(f"response of  bracket order first %s   contract %s",entry_res,ibcontract)
            StatusUpdate(entry_res, 'Entry', ibcontract, entry_order_type, tradeType, quantity, histData, lastPrice, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, userBuySell, userAtr, slValue,
                         breakEven, outsideRth, False, entry_points)
            res = connection.placeTrade(contract=ibcontract, order=br_order[1], outsideRth=outsideRth)
            logging.info(f"response of  bracket order second %s   contract %s", res, ibcontract)
            StatusUpdate(res, 'TakeProfit', ibcontract, 'LMT', tradeType, quantity, histData, lastPrice, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, userBuySell, userAtr, slValue,
                         breakEven, outsideRth)
            res = connection.placeTrade(contract=ibcontract, order=br_order[2], outsideRth=outsideRth)
            logging.info(f"response of  bracket order third %s   contract %s", res, ibcontract)
            StatusUpdate(res, 'StopLoss', ibcontract, stop_order_type, tradeType, quantity, histData, lastPrice, symbol,
                         timeFrame, profit, stopLoss, risk,Config.orderStatusData.get(int(entry_res.order.orderId)), tif, barType, userBuySell, userAtr, slValue,
                         breakEven, outsideRth)

        elif barType == Config.entryTradeType[1] or barType == Config.entryTradeType[2]:
            # RB and RBB implementation - place market order
            # Log bar high and low values for review
            bar_high = float(histData.get('high', 0))
            bar_low = float(histData.get('low', 0))
            logging.info(f"ENTRY ORDER (RB/RBB) - Bar values: Bar's high={bar_high}, Bar's low={bar_low}, range={bar_high - bar_low} for {symbol} {tradeType}")
            logging.info(f"Placing RB/RBB entry trade for {symbol} - {barType}")
            response = connection.placeTrade(contract=ibcontract,
                                         order=Order(orderType="MKT", action=tradeType, totalQuantity=quantity,tif=tif)  , outsideRth = outsideRth )
            StatusUpdate(response, 'Entry', ibcontract, 'MKT', tradeType, quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,'',tif,barType,userBuySell,userAtr,slValue,breakEven,outsideRth,False,'0')
        elif barType == Config.entryTradeType[7]:  # PBe2 - same logic as PBe1
            logging.info(f"sendEntryTrade: PBe2 block reached! barType='{barType}', Config.entryTradeType[7]='{Config.entryTradeType[7]}'")
            # PBe2: Entry similar to RBB, always uses HOD/LOD for stop loss (same as PBe1)
            # Entry stop price = bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL)
            # Stop size = |entry price - HOD/LOD|
            bar_high = float(histData.get('high', 0))
            bar_low = float(histData.get('low', 0))
            logging.info(f"ENTRY ORDER (PBe2) - Bar values: Bar's high={bar_high}, Bar's low={bar_low}, range={bar_high - bar_low} for {symbol} {tradeType}")
            
            # Get LOD/HOD for stop loss calculation
            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, ibcontract, timeFrame)
            
            # Entry stop price = bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL)
            if tradeType == 'BUY':
                entry_stop_price = round(bar_high + 0.01, Config.roundVal)
                stop_loss_price = lod  # BUY uses LOD
            else:  # SELL
                entry_stop_price = round(bar_low - 0.01, Config.roundVal)
                stop_loss_price = hod  # SELL uses HOD
            
            # Stop size = |entry price - HOD/LOD|
            if tradeType == 'BUY':
                stop_size = abs(entry_stop_price - lod) if lod is not None and lod > 0 else (bar_high - bar_low) + Config.add002
            else:  # SELL
                stop_size = abs(entry_stop_price - hod) if hod is not None and hod > 0 else (bar_high - bar_low) + Config.add002
            
            stop_size = round(stop_size, Config.roundVal)
            stop_loss_price = round(stop_loss_price, Config.roundVal) if stop_loss_price else 0
            
            if stop_size <= 0:
                stop_size = round((bar_high - bar_low) + Config.add002, Config.roundVal)
                logging.warning(f"PBe2: Invalid stop_size, using bar range: {stop_size}")
            
            logging.info(f"PBe2: entry_stop_price={entry_stop_price}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, LOD={lod}, HOD={hod}")
            
            # Determine order type based on session
            is_extended, session = _is_extended_outside_rth(outsideRth)
            order_type = "STP"
            limit_price = None
            
            if is_extended:
                # For Custom stop loss in extended hours: Calculate entry price from bar high/low
                # Entry price: Low - 0.01 for long (BUY), High + 0.01 for short (SELL)
                # Entry limit price: entry_price ± 0.5 * stop_size
                if stopLoss == Config.stopLoss[1]:  # 'Custom'
                    custom_stop = _to_float(slValue, 0)
                    if custom_stop == 0:
                        logging.error("Custom stop loss requires a valid value for PBe2 %s in extended hours %s", stopLoss, symbol)
                        return
                    
                    # Calculate entry price from bar high/low
                    if tradeType == 'BUY':
                        # For BUY (long): entry_price = bar_low - 0.01
                        entry_stop_price = round(bar_low - 0.01, Config.roundVal)
                    else:  # SELL
                        # For SELL (short): entry_price = bar_high + 0.01
                        entry_stop_price = round(bar_high + 0.01, Config.roundVal)
                    
                    # Calculate stop_size: |entry_price - custom_stop| + 0.02
                    stop_size = abs(entry_stop_price - custom_stop) + 0.02
                    stop_size = round(stop_size, Config.roundVal)
                    stop_loss_price = round(custom_stop, Config.roundVal)
                    
                    if stop_size <= 0:
                        logging.error("Stop size invalid (%s) for custom stop loss %s in extended hours PBe2 %s", stop_size, custom_stop, symbol)
                        return
                    
                    logging.info(f"PBe2 Extended hours Custom: entry_price={entry_stop_price} (from bar high/low), custom_stop={custom_stop}, stop_size={stop_size}")
                
                # Extended hours: STOP LIMIT order
                # Limit price = entry_price ± 0.5*stop_size (for Custom) or HOD/LOD ± 0.5*stop_size (for HOD/LOD)
                order_type = "STP LMT"
                entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                
                # Ensure minimum offset
                min_limit_offset = 0.01
                if entry_limit_offset < min_limit_offset:
                    entry_limit_offset = min_limit_offset
                
                if stopLoss == Config.stopLoss[1]:  # Custom
                    # For Custom: Limit = entry_price ± 0.5*stop_size
                    if tradeType == 'BUY':
                        limit_price = round(entry_stop_price + entry_limit_offset, Config.roundVal)
                    else:  # SELL
                        limit_price = round(entry_stop_price - entry_limit_offset, Config.roundVal)
                    logging.info(f"PBe2 Extended hours Custom {tradeType}: Stop={entry_stop_price}, Limit={limit_price} (entry ± 0.5×stop_size={entry_limit_offset}), stop_size={stop_size}")
                else:
                    # For HOD/LOD: Limit = HOD/LOD ± 0.5*stop_size
                    if tradeType == 'BUY':
                        # BUY: Limit = LOD + 0.5*stop_size
                        limit_price = round(lod + entry_limit_offset, Config.roundVal) if lod else round(entry_stop_price + entry_limit_offset, Config.roundVal)
                    else:  # SELL
                        # SELL: Limit = HOD - 0.5*stop_size
                        limit_price = round(hod - entry_limit_offset, Config.roundVal) if hod else round(entry_stop_price - entry_limit_offset, Config.roundVal)
                logging.info(f"PBe2 Extended hours: STP LMT, Stop={entry_stop_price}, Limit={limit_price}, stop_size={stop_size}")
            else:
                # RTH: STOP order
                logging.info(f"PBe2 RTH: STP, Stop={entry_stop_price}, stop_loss={stop_loss_price}, stop_size={stop_size}")
            
            # Place entry order
            if is_extended:
                entry_order = Order(
                    orderType="STP LMT",
                    action=tradeType,
                    totalQuantity=quantity,
                    tif=tif,
                    auxPrice=entry_stop_price,
                    lmtPrice=limit_price
                )
            else:
                entry_order = Order(
                    orderType="STP",
                    action=tradeType,
                    totalQuantity=quantity,
                    tif=tif,
                    auxPrice=entry_stop_price
                )
            
            response = connection.placeTrade(contract=ibcontract, order=entry_order, outsideRth=outsideRth)
            StatusUpdate(response, 'Entry', ibcontract, order_type, tradeType, quantity, histData, entry_stop_price, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, userBuySell, userAtr, slValue,
                         breakEven, outsideRth, False, '0')
            
            # Store data for TP/SL orders and continuous updates
            if int(response.order.orderId) in Config.orderStatusData:
                Config.orderStatusData[int(response.order.orderId)]['stopSize'] = stop_size
                Config.orderStatusData[int(response.order.orderId)]['stopLossPrice'] = stop_loss_price
                Config.orderStatusData[int(response.order.orderId)]['pbe1_lod'] = lod
                Config.orderStatusData[int(response.order.orderId)]['pbe1_hod'] = hod
                Config.orderStatusData[int(response.order.orderId)]['calculated_stop_size'] = stop_size
                # Store histData for pbe2_loop_run to detect new bars
                Config.orderStatusData[int(response.order.orderId)]['histData'] = histData
                logging.info(f"PBe2: Stored stop_size={stop_size}, stop_loss_price={stop_loss_price}, LOD={lod}, HOD={hod}, histData datetime={histData.get('dateTime')} in orderStatusData")
            
            # Store key in orderStatusData for pbe2_loop_run (will be started from pull_back_PBe2)
            key = (symbol + str(datetime.datetime.now()))
            if int(response.order.orderId) in Config.orderStatusData:
                Config.orderStatusData[int(response.order.orderId)]['key'] = key
                logging.info(f"PBe2: Stored key={key} in orderStatusData for orderId={response.order.orderId}")
            logging.info("PBe2: Entry order placed via sendEntryTrade, pbe2_loop_run will be started from pull_back_PBe2")
        
        elif barType == Config.entryTradeType[6]:  # PBe1 - similar to RBB+LOD/HOD
            logging.info(f"sendEntryTrade: PBe1 block reached! barType='{barType}', Config.entryTradeType[6]='{Config.entryTradeType[6]}'")
            # PBe1: Entry similar to RBB, always uses HOD/LOD for stop loss
            # Entry stop price = bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL)
            # Stop size = |entry price - HOD/LOD|
            bar_high = float(histData.get('high', 0))
            bar_low = float(histData.get('low', 0))
            logging.info(f"ENTRY ORDER (PBe1) - Bar values: Bar's high={bar_high}, Bar's low={bar_low}, range={bar_high - bar_low} for {symbol} {tradeType}")
            
            # Get LOD/HOD for stop loss calculation (use same function as pull_back_PBe1 for consistency)
            lod, hod = _get_pbe1_lod_hod(connection, ibcontract, timeFrame, tradeType)
            
            # Entry stop price = bar_high + 0.01 (BUY) or bar_low (SELL) - prior bar low for SELL
            if tradeType == 'BUY':
                entry_stop_price = round(bar_high + 0.01, Config.roundVal)
                stop_loss_price = lod  # BUY uses LOD
            else:  # SELL
                entry_stop_price = round(bar_low, Config.roundVal)
                stop_loss_price = hod  # SELL uses HOD
            
            # Stop size = |bar_high/low - HOD/LOD| (same as pull_back_PBe1, using bar high/low, not entry_stop_price)
            if tradeType == 'BUY':
                stop_size = abs(bar_high - lod) if lod is not None and lod > 0 else (bar_high - bar_low) + Config.add002
            else:  # SELL
                stop_size = abs(bar_low - hod) if hod is not None and hod > 0 else (bar_high - bar_low) + Config.add002
            
            stop_size = round(stop_size, Config.roundVal)
            stop_loss_price = round(stop_loss_price, Config.roundVal) if stop_loss_price else 0
            
            if stop_size <= 0:
                stop_size = round((bar_high - bar_low) + Config.add002, Config.roundVal)
                logging.warning(f"PBe1: Invalid stop_size, using bar range: {stop_size}")
            
            logging.info(f"PBe1: entry_stop_price={entry_stop_price}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, LOD={lod}, HOD={hod}, bar_high={bar_high}, bar_low={bar_low}")
            
            # Determine order type based on session
            # For PBe1: If outsideRth=True, always use STP LMT (extended hours behavior)
            is_extended, session = _is_extended_outside_rth(outsideRth)
            # Force extended hours behavior if outsideRth=True (user explicitly wants extended hours trading)
            if outsideRth and not is_extended:
                # User set outsideRth=True but session detection didn't catch it - force extended hours behavior
                is_extended = True
                logging.warning(f"PBe1: outsideRth=True but session={session} not detected as extended. Forcing extended hours behavior (STP LMT)")
            logging.info(f"PBe1: outsideRth={outsideRth}, is_extended={is_extended}, session={session}")
            order_type = "STP"
            limit_price = None
            
            if is_extended:
                # PBe1 Extended hours: Entry stop price = bar_high + 0.01 (BUY) or bar_low - 0.01 (SELL) - like RBB
                # Entry limit price = entry_stop_price ± 0.5 * stop_size
                # Note: PBe1 always uses HOD/LOD for stop loss, but if Custom is selected, we still use HOD/LOD calculation
                # The entry_stop_price is already calculated above (bar_high + 0.01 for BUY, bar_low - 0.01 for SELL)
                # No need to recalculate for Custom - use the same entry_stop_price as HOD/LOD
                
                # Extended hours: STOP LIMIT order
                # Limit price = entry_stop_price ± 0.5*stop_size (same for all stop loss types, like RBB)
                order_type = "STP LMT"
                entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                
                # Ensure minimum offset
                min_limit_offset = 0.01
                if entry_limit_offset < min_limit_offset:
                    entry_limit_offset = min_limit_offset
                
                # For all stop loss types (Custom, HOD/LOD): Limit = entry_stop_price ± 0.5*stop_size (like RBB)
                if tradeType == 'BUY':
                    limit_price = round(entry_stop_price + entry_limit_offset, Config.roundVal)
                else:  # SELL
                    limit_price = round(entry_stop_price - entry_limit_offset, Config.roundVal)
                logging.info(f"PBe1 Extended hours {tradeType}: Stop={entry_stop_price}, Limit={limit_price} (entry_stop ± 0.5×stop_size={entry_limit_offset}), stop_size={stop_size}")
            else:
                # RTH: STOP order
                logging.info(f"PBe1 RTH: STP, Stop={entry_stop_price}, stop_loss={stop_loss_price}, stop_size={stop_size}")
            
            # Place entry order
            if is_extended:
                entry_order = Order(
                    orderType="STP LMT",
                    action=tradeType,
                    totalQuantity=quantity,
                    tif=tif,
                    auxPrice=entry_stop_price,
                    lmtPrice=limit_price,
                    outsideRth=True  # Explicitly set outsideRth for extended hours
                )
                logging.info(f"PBe1 Extended hours: Placing STP LMT order - Stop={entry_stop_price}, Limit={limit_price}, outsideRth=True")
            else:
                entry_order = Order(
                    orderType="STP",
                    action=tradeType,
                    totalQuantity=quantity,
                    tif=tif,
                    auxPrice=entry_stop_price
                )
                logging.info(f"PBe1 RTH: Placing STP order - Stop={entry_stop_price}, outsideRth={outsideRth}")
            
            response = connection.placeTrade(contract=ibcontract, order=entry_order, outsideRth=outsideRth)
            logging.info(f"PBe1: Order placed - orderType={response.order.orderType}, auxPrice={response.order.auxPrice}, lmtPrice={getattr(response.order, 'lmtPrice', None)}, outsideRth={outsideRth}")
            StatusUpdate(response, 'Entry', ibcontract, order_type, tradeType, quantity, histData, entry_stop_price, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, userBuySell, userAtr, slValue,
                         breakEven, outsideRth, False, '0')
            
            # Store data for TP/SL orders and continuous updates
            if int(response.order.orderId) in Config.orderStatusData:
                Config.orderStatusData[int(response.order.orderId)]['stopSize'] = stop_size
                Config.orderStatusData[int(response.order.orderId)]['stopLossPrice'] = stop_loss_price
                Config.orderStatusData[int(response.order.orderId)]['pbe1_lod'] = lod
                Config.orderStatusData[int(response.order.orderId)]['pbe1_hod'] = hod
                Config.orderStatusData[int(response.order.orderId)]['calculated_stop_size'] = stop_size
                # Store histData for pbe1_loop_run to detect new bars
                Config.orderStatusData[int(response.order.orderId)]['histData'] = histData
                logging.info(f"PBe1: Stored stop_size={stop_size}, stop_loss_price={stop_loss_price}, LOD={lod}, HOD={hod}, histData datetime={histData.get('dateTime')} in orderStatusData")
            
            logging.info("PBe1: Entry order placed via sendEntryTrade, pbe1_loop_run will be started from pull_back_PBe1")
            # Return response for PBe1 so pull_back_PBe1 can start pbe1_loop_run directly (like RBB)
            return response
        
        else:
            # Log bar high and low values for review
            bar_high = float(histData.get('high', 0))
            bar_low = float(histData.get('low', 0))
            logging.info(f"ENTRY ORDER (Other) - Bar values: Bar's high={bar_high}, Bar's low={bar_low}, range={bar_high - bar_low} for {symbol} {tradeType}")
            response = connection.placeTrade(contract=ibcontract,
                                         order=Order(orderType="MKT", action=tradeType, totalQuantity=quantity,tif=tif)  , outsideRth = outsideRth )
            StatusUpdate(response, 'Entry', ibcontract, 'MKT', tradeType, quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,'',tif,barType,userBuySell,userAtr,slValue,breakEven,outsideRth,False,'0')
            return response


    except Exception as e:
        logging.error("error in sending entry trade %s ", e)
        print(e)
        return None

def get_tp_for_selling(connection, timeFrame, contract, profit_type, filled_price, histData):
    # Take-profit levels for long positions (sell orders) use the configured stop size.
    stop_size = None
    try:
        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
    except Exception:
        high = float(histData['high'])
        low = float(histData['low'])
        stop_size = round((high - low) + Config.add002, Config.roundVal)

    multiplier_map = {
        Config.takeProfit[0]: 1,    # 1:1
        Config.takeProfit[1]: 1.5,  # 1.5:1
        Config.takeProfit[2]: 2,    # 2:1
        Config.takeProfit[3]: 2.5,  # 2.5:1
    }
    # Add 3:1 if it exists (index 4)
    if len(Config.takeProfit) > 4:
        multiplier_map[Config.takeProfit[4]] = 3  # 3:1

    multiplier = multiplier_map.get(profit_type)
    if multiplier is not None:
        price = float(filled_price) + (multiplier * stop_size)
    else:
        # Bar-by-bar logic
        price = float(histData['high'])

    price = round(price, Config.roundVal)
    logging.info(
        f"tp calculation for selling %s , price %s histData %s filled_price %s profit_type %s stop_size %s multiplier %s ",
        contract, price, histData, filled_price, profit_type, stop_size, multiplier,
    )
    return price


def get_tp_for_buying(connection, timeFrame, contract, profit_type, filled_price, histData):
    # Take-profit levels for short positions (buy orders) use the configured stop size.
    stop_size = None
    try:
        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
    except Exception:
        high = float(histData['high'])
        low = float(histData['low'])
        stop_size = round((high - low) + Config.add002, Config.roundVal)

    multiplier_map = {
        Config.takeProfit[0]: 1,    # 1:1
        Config.takeProfit[1]: 1.5,  # 1.5:1
        Config.takeProfit[2]: 2,    # 2:1
        Config.takeProfit[3]: 2.5,  # 2.5:1
    }
    # Add 3:1 if it exists (index 4)
    if len(Config.takeProfit) > 4:
        multiplier_map[Config.takeProfit[4]] = 3  # 3:1

    multiplier = multiplier_map.get(profit_type)
    if multiplier is not None:
        price = float(filled_price) - (multiplier * stop_size)
    else:
        price = float(filled_price) - (1 * stop_size)

    price = round(price, Config.roundVal)
    logging.info(
        f"tp calculation for buying %s , price %s histData %s filled_price %s profit_type %s stop_size %s multiplier %s ",
        contract, price, histData, filled_price, profit_type, stop_size, multiplier,
    )
    return price

def get_sl_for_selling(connection,stoploss_type,filled_price, histData,slValue ,contract,timeframe,chartTime):
    # we are sending sell sl order bcz entry buy
    if stoploss_type in Config.atrStopLossMap:
        atr_offset = _get_atr_stop_offset(connection, contract, stoploss_type)
        if atr_offset is not None:
            stpPrice = float(filled_price) - atr_offset
            logging.info("ATR stop loss (long) %s offset %s filled_price %s stop %s",
                         stoploss_type, atr_offset, filled_price, stpPrice)
            stpPrice = round(stpPrice, Config.roundVal)
            return stpPrice
        else:
            logging.warning("ATR stop loss offset missing for %s, falling back to EntryBar", stoploss_type)

    if stoploss_type == Config.stopLoss[1]:  # 'Custom'
        custom_price = _to_float(slValue, 0)
        if custom_price == 0:
            logging.warning("Custom stop loss value missing for %s, falling back to entry price", contract)
            custom_price = float(filled_price)
        if custom_price >= float(filled_price):
            logging.warning("Custom stop loss %s should be below filled price %s for long positions", custom_price, filled_price)
        stpPrice = round(custom_price, Config.roundVal)
        logging.info("Custom stop loss (long) price %s filled_price %s", stpPrice, filled_price)
        return stpPrice

    if stoploss_type == Config.stopLoss[0]:
        stpPrice = float(histData['low'])
        logging.info(f"sl calculation for selling %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s", contract,   stpPrice,stoploss_type,filled_price, histData,slValue ,timeframe,chartTime)
    elif stoploss_type == Config.stopLoss[1]:
        # need to re -code barbybar
        stpPrice = float(histData['low'])
        logging.info(
            f"sl calculation for selling %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
    elif stoploss_type == Config.stopLoss[2]:
        high_value = 0
        recentBarData = connection.getHistoricalChartDataForEntry(contract, timeframe, chartTime)
        if recentBarData and len(recentBarData) > 0:
            # Handle both dict and list formats
            if isinstance(recentBarData, dict):
                for data in range(0, len(recentBarData)):
                    bar = recentBarData.get(data)
                    if bar and 'high' in bar:
                        if (high_value == 0 or high_value < float(bar['high'])):
                            high_value = float(bar['high'])
                            logging.info(f"high value found for %s recentBarData.get(data) %s ", contract, bar)
            elif isinstance(recentBarData, list):
                for bar in recentBarData:
                    if bar and 'high' in bar:
                        if (high_value == 0 or high_value < float(bar['high'])):
                            high_value = float(bar['high'])
                            logging.info(f"high value found for %s bar %s ", contract, bar)
        
        if high_value == 0:
            # Fallback to histData if no high value found
            high_value = float(histData.get('high', filled_price))
            logging.warning(f"No high value found in recentBarData for %s, using histData high: %s", contract, high_value)
        
        stpPrice = float(high_value) + float(slValue)
        logging.info(
            f"sl calculation for selling %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
    elif stoploss_type == Config.stopLoss[3]:
        low_value = 0
        recentBarData = connection.getHistoricalChartDataForEntry(contract, timeframe, chartTime)
        if recentBarData and len(recentBarData) > 0:
            # Handle both dict and list formats
            if isinstance(recentBarData, dict):
                for data in range(0, len(recentBarData)):
                    bar = recentBarData.get(data)
                    if bar and 'low' in bar:
                        if (low_value == 0 or low_value > float(bar['low'])):
                            low_value = float(bar['low'])
                            logging.info(f"low value found for %s recentBarData.get(data) %s ", contract, bar)
            elif isinstance(recentBarData, list):
                for bar in recentBarData:
                    if bar and 'low' in bar:
                        if (low_value == 0 or low_value > float(bar['low'])):
                            low_value = float(bar['low'])
                            logging.info(f"low value found for %s bar %s ", contract, bar)
        
        if low_value == 0:
            # Fallback to histData if no low value found
            low_value = float(histData.get('low', filled_price))
            logging.warning(f"No low value found in recentBarData for %s, using histData low: %s", contract, low_value)

        stpPrice = float(low_value) - float(slValue)
        logging.info(
            f"sl calculation for selling %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)

    stpPrice = round(stpPrice, Config.roundVal)
    return stpPrice
def get_sl_for_buying(connection,stoploss_type,filled_price, histData,slValue ,contract,timeframe,chartTime):
    # we are sending buy sl order
    if stoploss_type in Config.atrStopLossMap:
        atr_offset = _get_atr_stop_offset(connection, contract, stoploss_type)
        if atr_offset is not None:
            stpPrice = float(filled_price) + atr_offset
            logging.info("ATR stop loss (short) %s offset %s filled_price %s stop %s",
                         stoploss_type, atr_offset, filled_price, stpPrice)
            stpPrice = round(stpPrice, Config.roundVal)
            return stpPrice
        else:
            logging.warning("ATR stop loss offset missing for %s, falling back to EntryBar", stoploss_type)

    if stoploss_type == Config.stopLoss[1]:  # 'Custom'
        custom_price = _to_float(slValue, 0)
        if custom_price == 0:
            logging.warning("Custom stop loss value missing for %s, falling back to entry price", contract)
            custom_price = float(filled_price)
        if custom_price <= float(filled_price):
            logging.warning("Custom stop loss %s should be above filled price %s for short positions", custom_price, filled_price)
        stpPrice = round(custom_price, Config.roundVal)
        logging.info("Custom stop loss (short) price %s filled_price %s", stpPrice, filled_price)
        return stpPrice

    if stoploss_type == Config.stopLoss[0]:
        stpPrice = float(histData['high'])
        logging.info(
            f"sl calculation for buying %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
    elif stoploss_type == Config.stopLoss[1]:
        #  need to recode barbybar
        stpPrice = float(histData['high'])
        logging.info(
            f"sl calculation for buying %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
    elif stoploss_type == Config.stopLoss[2]:
        high_value = 0
        recentBarData = connection.getHistoricalChartDataForEntry(contract, timeframe, chartTime)
        if recentBarData and len(recentBarData) > 0:
            # Handle both dict and list formats
            if isinstance(recentBarData, dict):
                for data in range(0, len(recentBarData)):
                    bar = recentBarData.get(data)
                    if bar and 'high' in bar:
                        if (high_value == 0 or high_value < float(bar['high'])):
                            high_value = float(bar['high'])
                            logging.info(f"high value found for %s recentBarData.get(data) %s ", contract, bar)
            elif isinstance(recentBarData, list):
                for bar in recentBarData:
                    if bar and 'high' in bar:
                        if (high_value == 0 or high_value < float(bar['high'])):
                            high_value = float(bar['high'])
                            logging.info(f"high value found for %s bar %s ", contract, bar)
        
        if high_value == 0:
            # Fallback to histData if no high value found
            high_value = float(histData.get('high', filled_price))
            logging.warning(f"No high value found in recentBarData for %s, using histData high: %s", contract, high_value)
        
        stpPrice = float(high_value) + float(slValue)
        logging.info(
            f"sl calculation for buying %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
    elif stoploss_type == Config.stopLoss[3]:
        low_value = 0
        recentBarData = connection.getHistoricalChartDataForEntry(contract, timeframe, chartTime)
        if recentBarData and len(recentBarData) > 0:
            # Handle both dict and list formats
            if isinstance(recentBarData, dict):
                for data in range(0, len(recentBarData)):
                    bar = recentBarData.get(data)
                    if bar and 'low' in bar:
                        if (low_value == 0 or low_value > float(bar['low'])):
                            low_value = float(bar['low'])
                            logging.info(f"low value found for %s recentBarData.get(data) %s ", contract, bar)
            elif isinstance(recentBarData, list):
                for bar in recentBarData:
                    if bar and 'low' in bar:
                        if (low_value == 0 or low_value > float(bar['low'])):
                            low_value = float(bar['low'])
                            logging.info(f"low value found for %s bar %s ", contract, bar)
        
        if low_value == 0:
            # Fallback to histData if no low value found
            low_value = float(histData.get('low', filled_price))
            logging.warning(f"No low value found in recentBarData for %s, using histData low: %s", contract, low_value)

        stpPrice = float(low_value) - float(slValue)
        logging.info(
            f"sl calculation for buying %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)

        # stpPrice = stpPrice + 0.01
    stpPrice = round(stpPrice, Config.roundVal)
    return stpPrice

def TpSlForFB(connection,ibcontract,tradeType,quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,tif,barType,userBuySell,userAtr,slValue=0,breakEven=False ,outsideRth=False):
    try:
        tp,sl=0,0
        if tradeType == 'BUY':
            tp = get_tp_for_selling(connection,timeFrame, ibcontract,profit,lastPrice, histData)
            chartTime = datetime.datetime.strptime(str(datetime.datetime.now().date())+" "+Config.tradingTime,"%Y-%m-%d %H:%M:%S")
            sl = get_sl_for_selling(connection,stopLoss,lastPrice, histData,slValue ,ibcontract,timeFrame,chartTime)
        else:
            tp = get_tp_for_buying(connection,timeFrame, ibcontract,profit,lastPrice, histData)
            chartTime = datetime.datetime.strptime(str(datetime.datetime.now().date())+" "+Config.tradingTime,"%Y-%m-%d %H:%M:%S")
            sl = get_sl_for_buying(connection, stopLoss, lastPrice, histData, slValue, ibcontract, timeFrame,
                                    chartTime)

        return tp,sl
    except Exception as e:
        logging.error("error in sending tpsl price for fb %s ", e)
    return 0,0

def sendTpAndSl(connection, entryData):
    try:
        logging.info("sendTpAndSl called: status=%s, ordType=%s, barType=%s, orderId=%s, outsideRth=%s",
                    entryData.get('status'), entryData.get('ordType'), entryData.get('barType'), 
                    entryData.get('orderId'), entryData.get('outsideRth'))
        
        if (entryData['status'] == "Filled" and entryData['ordType'] == "Entry"):
            # Check if TP/SL have already been sent for this order to prevent duplicates
            order_id = entryData.get('orderId')
            if order_id and order_id in Config.orderStatusData:
                order_data = Config.orderStatusData[order_id]
                if order_data.get('tp_sl_sent', False):
                    logging.warning(f"sendTpAndSl: TP/SL already sent for orderId={order_id}, skipping duplicate send. barType=%s", 
                                  entryData.get('barType'))
                    return
                # Mark TP/SL as sent to prevent duplicates
                order_data['tp_sl_sent'] = True
                Config.orderStatusData[order_id] = order_data
                logging.info(f"sendTpAndSl: Marked TP/SL as sent for orderId={order_id}, barType=%s", entryData.get('barType'))
            bar_type = entryData.get('barType', '')
            is_manual_order = bar_type in Config.manualOrderTypes
            is_extended_hours = entryData.get('outsideRth', False)
            logging.info("Entry order filled - barType=%s, is_manual_order=%s, is_extended_hours=%s, action=%s, orderId=%s", 
                        bar_type, is_manual_order, is_extended_hours, entryData.get('action'), entryData.get('orderId'))
            
            # For RBB in extended hours: Cancel protection order if it exists (TP/SL orders will replace it)
            if bar_type == Config.entryTradeType[5] and is_extended_hours:  # RBB in extended hours
                protection_order_id = entryData.get('protection_order_id')
                if protection_order_id:
                    # Check if protection order is already filled
                    protection_filled = False
                    if protection_order_id in Config.orderStatusData:
                        protection_status = Config.orderStatusData[protection_order_id].get('status', '')
                        if protection_status == 'Filled':
                            protection_filled = True
                            logging.warning(f"sendTpAndSl: Protection order {protection_order_id} is already Filled. Will skip placing new stop loss order.")
                    else:
                        # Check in IB trades if not in orderStatusData
                        try:
                            existing_trades = connection.ib.trades()
                            if protection_order_id in existing_trades:
                                protection_trade = existing_trades[protection_order_id]
                                if hasattr(protection_trade, 'orderStatus') and protection_trade.orderStatus.status == 'Filled':
                                    protection_filled = True
                                    logging.warning(f"sendTpAndSl: Protection order {protection_order_id} is already Filled (from IB trades). Will skip placing new stop loss order.")
                        except Exception as e:
                            logging.debug(f"sendTpAndSl: Could not check protection order status from IB trades: {e}")
                    
                    if not protection_filled:
                        try:
                            logging.info(f"sendTpAndSl: Cancelling protection order {protection_order_id} before sending TP/SL orders")
                            # Get the protection order from orderStatusData or create Order object directly
                            from ib_insync import Order
                            protection_order = Order(orderId=protection_order_id)
                            connection.cancelTrade(protection_order)
                            logging.info(f"sendTpAndSl: Protection order {protection_order_id} cancelled successfully")
                        except Exception as e:
                            logging.error(f"sendTpAndSl: Error cancelling protection order {protection_order_id}: {e}")
                            logging.error("Traceback: %s", traceback.format_exc())
                    else:
                        # Mark that protection order already filled, so we should skip stop loss in sendTpSlBuy/sendTpSlSell
                        entryData['protection_order_filled'] = True
                        logging.info(f"sendTpAndSl: Marked protection_order_filled=True for entryData, will skip stop loss order placement")
            
            try:
                # Use nest_asyncio to allow nested event loops
                nest_asyncio.apply()
                loop = asyncio.get_event_loop()
                
                def task_done_callback(task):
                    try:
                        if task.exception():
                            logging.error("TP/SL async task failed with exception: %s", task.exception())
                            logging.error("Traceback: %s", traceback.format_exc())
                    except Exception as e:
                        logging.error("Error in task_done_callback: %s", e)
                
                # Check if option trading is enabled for this trade
                symbol = entryData.get('usersymbol', '')
                timeFrame = entryData.get('timeFrame', '')
                barType = entryData.get('barType', '')
                userBuySell = entryData.get('userBuySell', '')
                
                # Try to find matching option trade parameters
                option_params = None
                if hasattr(Config, 'option_trade_params') and Config.option_trade_params:
                    for trade_key, params in list(Config.option_trade_params.items()):
                        if (trade_key[0] == symbol and trade_key[1] == timeFrame and 
                            trade_key[2] == barType and trade_key[3] == userBuySell):
                            option_params = params
                            # Remove from pending after use
                            del Config.option_trade_params[trade_key]
                            logging.info("Found option trade parameters for %s: %s", symbol, option_params)
                            break
                
                # Store option params in entryData for use in sendTpSlBuy/sendTpSlSell
                if option_params:
                    entryData['option_params'] = option_params
                    logging.info("Stored option_params in entryData for %s", symbol)
                
                if (entryData['action'] == "BUY"):
                    logging.info("Market order filled we will send buy Order, market data is %s",entryData)
                    future = asyncio.ensure_future(sendTpSlSell(connection, entryData))
                    future.add_done_callback(task_done_callback)
                    logging.info("Scheduled sendTpSlSell future: %s", future)
                else:
                    logging.info("Market order filled we will send sell Order, market data is %s",entryData)
                    future = asyncio.ensure_future(sendTpSlBuy(connection, entryData))
                    future.add_done_callback(task_done_callback)
                    logging.info("Scheduled sendTpSlBuy future: %s", future)
            except Exception as e:
                logging.error("Error scheduling TP/SL async function: %s", e)
                logging.error("Traceback: %s", traceback.format_exc())
                traceback.print_exc()

        if ( (entryData['status'] == "Filled") and ((entryData['ordType'] == "StopLoss") or (entryData['ordType'] == "TakeProfit")) ):
            parentData = entryData.get('entryData', {})
            
            # Debug logging to trace barType
            logging.info("sendTpAndSl: StopLoss/TakeProfit filled - entryData barType=%s, parentData keys=%s, parentData barType=%s", 
                        entryData.get('barType'), list(parentData.keys()) if parentData else 'None', parentData.get('barType') if parentData else 'None')
            logging.info("sendTpAndSl: Config.entryTradeType[6]=%s (PBe1)", 
                        Config.entryTradeType[6] if len(Config.entryTradeType) > 6 else 'N/A')
            
            # If parentData is empty or doesn't have barType, try to get it from orderStatusData
            if not parentData or not parentData.get('barType'):
                # Try to get parent order ID from entryData
                parent_order_id = entryData.get('orderId')
                if parent_order_id:
                    # Get the entry order ID from the stop loss order's ocaGroup
                    oca_group = getattr(entryData.get('order', None), 'ocaGroup', None) if entryData.get('order') else None
                    if oca_group and oca_group.startswith('tp'):
                        try:
                            entry_order_id = int(oca_group.replace('tp', ''))
                            entry_order_data = Config.orderStatusData.get(entry_order_id, {})
                            if entry_order_data:
                                parentData = entry_order_data
                                logging.info("sendTpAndSl: Retrieved parentData from orderStatusData[%s], barType=%s", entry_order_id, parentData.get('barType'))
                        except (ValueError, TypeError) as e:
                            logging.warning("sendTpAndSl: Could not parse entry order ID from ocaGroup=%s: %s", oca_group, e)
            
            if not parentData:
                logging.error("sendTpAndSl: parentData is empty or None, cannot trigger PBe2")
            
            # Check if replay is enabled and stop loss was triggered
            replay_enabled = parentData.get('replayEnabled', False) if parentData else False
            if entryData['ordType'] == "StopLoss" and replay_enabled:
                logging.info("Replay enabled: Stop loss triggered, re-entering trade for %s", parentData.get('usersymbol'))
                # Re-enter the same trade with same parameters
                try:
                    # Get current session for outsideRth
                    from NewTradeFrame import _get_current_session
                    session = _get_current_session()
                    outsideRth = session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT')
                    
                    asyncio.ensure_future(SendTrade(
                        connection,
                        parentData['usersymbol'],
                        parentData['timeFrame'],
                        parentData['profit'],
                        parentData['stopLoss'],
                        parentData['risk'],
                        parentData['tif'],
                        parentData['barType'],
                        parentData['userBuySell'],
                        parentData['userAtr'],
                        0,  # quantity
                        Config.pullBackNo,
                        parentData.get('slValue', 0),
                        parentData.get('breakEven', False),
                        outsideRth,
                        parentData.get('entry_points', '0')
                    ))
                    logging.info("Replay: Re-entry order placed for %s", parentData.get('usersymbol'))
                except Exception as replay_error:
                    logging.error("Error in replay re-entry: %s", replay_error)
                    logging.error("Traceback: %s", traceback.format_exc())
            
            # PB logic removed - PBe2 trigger disabled


    except Exception as e:
        logging.error("error in sending TPandSL %s ", e)
        print(e)

async def sendTpSlBuy(connection, entryData):
    try:
        logging.info("sendTpSlBuy called for barType=%s, stopLoss=%s, orderId=%s, action=%s", 
                     entryData.get('barType'), entryData.get('stopLoss'), entryData.get('orderId'), entryData.get('action'))
        logging.info("sendTpSlBuy entryData keys: %s", list(entryData.keys()))
        #  entry sell so buy tp sl order
        max_retries = 5  # Limit retries to avoid infinite loop
        retry_count = 0
        while True:
            histData = None
            # Check if this is a manual order (Stop Order or Limit Order)
            is_manual_order = entryData.get('barType', '') in Config.manualOrderTypes
            
            if entryData['barType'] == Config.entryTradeType[0] or entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[2] or entryData['barType'] == Config.entryTradeType[3] or entryData['barType'] == Config.entryTradeType[4] or entryData['barType'] == Config.entryTradeType[5] or entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7]:
                histData = entryData['histData']
            elif is_manual_order:
                # For manual orders, try to use stored histData first, otherwise create fallback immediately
                histData = entryData.get('histData')
                if not histData or not isinstance(histData, dict) or 'high' not in histData:
                    # Create fallback histData from filled price immediately (no retries needed)
                    filled_price = Config.orderFilledPrice.get(entryData['orderId'])
                    if filled_price is None:
                        filled_price = entryData.get('lastPrice', 0)
                    if filled_price > 0:
                        # Create a minimal histData with 1% range around filled price
                        histData = {
                            'high': filled_price * 1.005,
                            'low': filled_price * 0.995,
                            'close': filled_price
                        }
                        logging.info("Manual Order: Using fallback histData from filled price: %s", histData)
                    else:
                        logging.error("Manual Order: Cannot create fallback histData: filled_price is invalid")
                        # Don't break - continue with None histData, the custom stop loss logic doesn't need it
                else:
                    logging.info("Manual Order: Using stored histData: %s", histData)
            else:
                chartTime = getRecentChartTime(entryData['timeFrame'])
                histData = connection.getHistoricalChartData(entryData['contract'], entryData['timeFrame'], chartTime)
                if (histData is None or len(histData) == 0):
                    retry_count += 1
                    if retry_count >= max_retries:
                        logging.warning("Chart Data is Not Coming after %s retries for %s contract, using fallback calculation", max_retries, entryData['contract'])
                        # Use fallback: create a minimal histData from filled price
                        filled_price = Config.orderFilledPrice.get(entryData['orderId'])
                        if filled_price is None:
                            filled_price = entryData.get('lastPrice', 0)
                        if filled_price > 0:
                            # Create a minimal histData with 1% range around filled price
                            histData = {
                                'high': filled_price * 1.005,
                                'low': filled_price * 0.995,
                                'close': filled_price
                            }
                            logging.info("Using fallback histData for premarket: %s", histData)
                        else:
                            logging.error("Cannot create fallback histData: filled_price is invalid")
                            break
                    else:
                        logging.info("Chart Data is Not Comming for %s contract  and for %s time (retry %s/%s)", entryData['contract'], chartTime, retry_count, max_retries)
                    await asyncio.sleep(2)
                    continue
                # histData = entryData['histData']

            price = 0
            filled_price = Config.orderFilledPrice.get(entryData['orderId'])
            if filled_price is None:
                filled_price = entryData.get('lastPrice', 0)
            if filled_price is None or filled_price == 0:
                # Also check entryData for avgFillPrice or fillPrice
                filled_price = entryData.get('avgFillPrice', 0)
                if filled_price == 0:
                    filled_price = entryData.get('fillPrice', 0)
            if filled_price == 0 or filled_price is None:
                logging.warning("Manual Order: filled_price is 0 or None, waiting for fill price. OrderId=%s, retry=%s/%s, orderFilledPrice keys=%s", 
                             entryData.get('orderId'), retry_count, max_retries, list(Config.orderFilledPrice.keys()))
                if retry_count >= max_retries:
                    logging.error("Manual Order: Max retries reached, cannot proceed without filled_price. Using entry_stop_price as fallback.")
                    # Use entry_stop_price as fallback for custom stop loss
                    if entryData.get('barType') in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:
                        filled_price = entryData.get('lastPrice', entryData.get('entryPrice', 0))
                        logging.warning("Manual Order Custom: Using entry_stop_price=%s as filled_price fallback", filled_price)
                        if filled_price == 0:
                            logging.error("Manual Order Custom: Cannot proceed - no valid price available")
                            break
                    else:
                        logging.error("Manual Order: Cannot proceed without filled_price and no fallback available")
                        break
                else:
                    retry_count += 1
                    await asyncio.sleep(0.5)
                    continue
            
            # Use entry stop price (lastPrice) instead of filled price for take profit calculation
            # For Stop Order: entry_stop_price should be the original auxPrice (stop trigger price), not the filled price
            entry_stop_price = entryData.get('lastPrice', filled_price)
            if entry_stop_price is None or entry_stop_price == 0:
                entry_stop_price = filled_price
            
            # For Stop Order: Ensure entry_stop_price is the original entry stop price (auxPrice), not filled price
            if entryData.get('barType') == Config.entryTradeType[0]:  # Stop Order
                # Try to get the original auxPrice from the order
                order_aux_price = None
                if 'order' in entryData and hasattr(entryData['order'], 'auxPrice'):
                    order_aux_price = entryData['order'].auxPrice
                elif 'auxPrice' in entryData:
                    order_aux_price = entryData['auxPrice']
                
                # If we have the original auxPrice, use it instead of filled_price
                if order_aux_price is not None and order_aux_price > 0:
                    entry_stop_price = float(order_aux_price)
                    logging.info("Stop Order: Using original auxPrice=%s as entry_stop_price (filled_price=%s)", 
                               entry_stop_price, filled_price)
                elif entry_stop_price == filled_price:
                    # If entry_stop_price equals filled_price, try to get it from entry_points
                    entry_points = entryData.get('entry_points', '0')
                    try:
                        entry_stop_price = float(entry_points)
                        if entry_stop_price > 0:
                            logging.info("Stop Order: Using entry_points=%s as entry_stop_price (filled_price=%s)", 
                                       entry_stop_price, filled_price)
                    except (ValueError, TypeError):
                        logging.warning("Stop Order: Could not get original entry stop price, using filled_price as fallback")
            
            # For PBe1/PBe2: Ensure entry_stop_price is the original entry stop price (bar_high/low ± 0.01), not filled price
            if entryData.get('barType') == Config.entryTradeType[6] or entryData.get('barType') == Config.entryTradeType[7]:  # PBe1 or PBe2
                # Try to get the original auxPrice from the order (entry stop price)
                order_aux_price = None
                if 'order' in entryData and hasattr(entryData['order'], 'auxPrice'):
                    order_aux_price = entryData['order'].auxPrice
                elif 'auxPrice' in entryData:
                    order_aux_price = entryData['auxPrice']
                
                # If we have the original auxPrice, use it instead of filled_price
                if order_aux_price is not None and order_aux_price > 0:
                    entry_stop_price = float(order_aux_price)
                    logging.info("PBe1/PBe2: Using original auxPrice=%s as entry_stop_price (filled_price=%s)", 
                               entry_stop_price, filled_price)
                elif entry_stop_price == filled_price:
                    # If entry_stop_price equals filled_price, try to get it from lastPrice (stored during StatusUpdate)
                    stored_last_price = entryData.get('lastPrice', 0)
                    if stored_last_price and stored_last_price > 0:
                        entry_stop_price = float(stored_last_price)
                        logging.info("PBe1/PBe2: Using stored lastPrice=%s as entry_stop_price (filled_price=%s)", 
                                   entry_stop_price, filled_price)
                    else:
                        logging.warning("PBe1/PBe2: Could not get original entry stop price, using filled_price as fallback")
            
            logging.info("In TPSL %s contract  and for %s histdata. Entry stop price=%s, Filled price=%s, barType=%s, stopLoss=%s",
                         entryData['contract'], histData, entry_stop_price, filled_price, entryData.get('barType'), entryData.get('stopLoss'))
            
            # Skip RB/RBB/PBe1 section for manual orders - they have their own logic
            # Conditional Order, FB, RB, RBB, PBe1, LB, LB2, LB3 use same TP/SL logic
            if (entryData['barType'] == Config.entryTradeType[2]) or (entryData['barType'] == Config.entryTradeType[3]) or (entryData['barType'] == Config.entryTradeType[4]) or (entryData['barType'] == Config.entryTradeType[5]) or (entryData['barType'] == Config.entryTradeType[6]) or (entryData['barType'] == Config.entryTradeType[9]) or (entryData['barType'] == Config.entryTradeType[10]):
                candleData = connection.getDailyCandle(entryData['contract'])
                if (candleData == None or len(candleData) < 1):
                    logging.info("candle data not found for %s", entryData['contract'])
                    await  asyncio.sleep(1)
                    continue
                logging.info(" Candle Data for takeProfit %s and contract is %s", candleData[-1], entryData['contract'])

                # Check if stop loss is ATR-based or Custom - use same stop_size as entry and stop loss
                # PBe1/PBe2: Always uses HOD/LOD for stop loss (similar to RBB+HOD/LOD)
                stop_loss_type = entryData.get('stopLoss')
                is_pbe1 = (entryData['barType'] == Config.entryTradeType[6])  # PBe1
                is_pbe2 = (entryData['barType'] == Config.entryTradeType[7])  # PBe2
                is_pbe1_or_pbe2 = is_pbe1 or is_pbe2  # PBe1 or PBe2
                is_lod_hod = (stop_loss_type == Config.stopLoss[3]) or (stop_loss_type == Config.stopLoss[4])  # HOD or LOD
                
                if is_pbe1:  # PBe1 - always uses HOD/LOD for stop loss (similar to RBB+HOD/LOD)
                    # PBe1: Entry price for TP = entry_stop_price (bar_high/low ± 0.01), stop_size = |entry_stop_price - HOD/LOD|
                    # For RTH: Recalculate stop_size using current HOD/LOD (same as stop loss)
                    # For extended hours: Use stored stop_size (HOD/LOD doesn't change during extended hours)
                    is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                    
                    if not is_extended:
                        # RTH: Recalculate stop_size using current HOD/LOD (same as stop loss calculation)
                        logging.info(f"PBe1 RTH: Recalculating stop_size for TP using current HOD/LOD")
                        try:
                            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                            
                            if entryData.get('action') == 'SELL':  # SHORT position
                                pbe_stop_size = abs(float(entry_stop_price) - hod) if hod else 0
                                logging.info(f"PBe1 TP RTH (SHORT): Recalculated stop_size={pbe_stop_size} from current HOD={hod}, entry_stop_price={entry_stop_price}")
                            else:  # LONG position
                                pbe_stop_size = abs(float(entry_stop_price) - lod) if lod else 0
                                logging.info(f"PBe1 TP RTH (LONG): Recalculated stop_size={pbe_stop_size} from current LOD={lod}, entry_stop_price={entry_stop_price}")
                            
                            pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                            if pbe_stop_size <= 0:
                                logging.error(f"PBe1 TP RTH: Invalid stop_size ({pbe_stop_size}) from HOD/LOD. HOD={hod}, LOD={lod}, entry_stop_price={entry_stop_price}")
                                pbe_stop_size = 0
                        except Exception as e:
                            logging.error(f"PBe1 TP RTH: Error recalculating stop_size: {e}. Using stored value as fallback.")
                            # Fallback to stored stop_size
                            order_id = entryData.get('orderId')
                            order_data = Config.orderStatusData.get(order_id) if order_id else None
                            if order_data:
                                stored_stop_size = order_data.get('stopSize', 0)
                                pbe_stop_size = stored_stop_size if stored_stop_size > 0 else 0
                                logging.warning(f"PBe1 TP RTH: Using stored stop_size={pbe_stop_size} as fallback")
                            else:
                                pbe_stop_size = 0
                    else:
                        # Extended hours: Use stored stop_size (HOD/LOD doesn't change during extended hours)
                        order_id = entryData.get('orderId')
                        order_data = Config.orderStatusData.get(order_id) if order_id else None
                        
                        if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                            lod = order_data.get('pbe1_lod', 0)
                            hod = order_data.get('pbe1_hod', 0)
                            stored_stop_size = order_data.get('stopSize', 0)
                            
                            # For SHORT position (SELL entry): stop_size = |entry_stop_price - HOD|
                            if entryData.get('action') == 'SELL':  # SHORT position
                                pbe_stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(entry_stop_price) - hod)
                                logging.info(f"PBe1 TP Extended hours (SHORT): Using stored stop_size={pbe_stop_size} from HOD={hod}, entry_stop_price={entry_stop_price}")
                            else:  # LONG position (BUY entry)
                                pbe_stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(entry_stop_price) - lod)
                                logging.info(f"PBe1 TP Extended hours (LONG): Using stored stop_size={pbe_stop_size} from LOD={lod}, entry_stop_price={entry_stop_price}")
                            
                            pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                            if pbe_stop_size <= 0:
                                logging.error(f"PBe1 TP Extended hours: Invalid stop_size ({pbe_stop_size}) from HOD/LOD. HOD={hod}, LOD={lod}, entry_stop_price={entry_stop_price}")
                                pbe_stop_size = 0
                        else:
                            # Fallback: recalculate from HOD/LOD
                            logging.warning(f"PBe1 TP Extended hours: LOD/HOD not found in orderStatusData, recalculating")
                            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                            if entryData.get('action') == 'SELL':  # SHORT position
                                pbe_stop_size = abs(float(entry_stop_price) - hod) if hod else 0
                            else:  # LONG position
                                pbe_stop_size = abs(float(entry_stop_price) - lod) if lod else 0
                            pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                            logging.warning(f"PBe1 TP Extended hours: Recalculated stop_size={pbe_stop_size} from HOD/LOD")
                    
                    # Calculate TP: entry_stop_price ± stop_size * multiplier
                    multiplier_map = {
                        Config.takeProfit[0]: 1,    # 1:1
                        Config.takeProfit[1]: 1.5,  # 1.5:1
                        Config.takeProfit[2]: 2,    # 2:1
                        Config.takeProfit[3]: 2.5,  # 2.5:1
                    }
                    if len(Config.takeProfit) > 4:
                        multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                    
                    multiplier = multiplier_map.get(entryData['profit'], 2.0)  # Default 2:1
                    # For PBe1: Use entry_stop_price (entry price) for TP calculation
                    # TP = entry_stop_price ± (multiplier × stop_size)
                    if entryData.get('action') == 'SELL':  # SHORT position
                        # For SHORT: TP = entry_stop_price - (multiplier × stop_size)
                        price = float(entry_stop_price) - (multiplier * pbe_stop_size)
                    else:  # LONG position
                        # For LONG: TP = entry_stop_price + (multiplier × stop_size)
                        price = float(entry_stop_price) + (multiplier * pbe_stop_size)
                    
                    price = round(price, Config.roundVal)
                    logging.info(f"PBe1 TP calculation: entry_stop_price={entry_stop_price} (entry price), stop_size={pbe_stop_size}, multiplier={multiplier}, tp={price}")
                    
                    # Send TP order directly for PBe1 - don't fall through to duplicate send
                    if entryData.get('action') == 'SELL':  # SHORT position
                        tp_action = "BUY"  # To close short
                    else:  # LONG position
                        tp_action = "SELL"  # To close long
                    logging.info(f"PBe1 Sending TP Trade EntryData is %s  and Price is %s  and action is {tp_action}", entryData, price)
                    sendTakeProfit(connection, entryData, price, tp_action)
                    # Skip the rest of TP calculation - already sent
                    price = None  # Mark as already sent
                    # Continue to stop loss calculation - don't return early
                elif stop_loss_type in Config.atrStopLossMap:
                    # Use ATR stop_size for take profit (same as entry and stop loss)
                    atr_offset = _get_atr_stop_offset(connection, entryData['contract'], stop_loss_type)
                    if atr_offset is not None and atr_offset > 0:
                        stop_size = atr_offset
                        logging.info(f"RB/RBB ATR TP (SHORT): Using ATR stop_size={stop_size} for take profit")
                    else:
                        # Fallback to bar-based if ATR unavailable
                        try:
                            stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                            logging.warning(f"RB/RBB: ATR unavailable, using bar-based stop_size={stop_size} for TP")
                        except Exception:
                            stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                            logging.warning(f"RB/RBB: Using fallback bar-based stop_size={stop_size} for TP")
                elif stop_loss_type == Config.stopLoss[3] or stop_loss_type == Config.stopLoss[4]:  # HOD or LOD
                    # For LOD/HOD: Use stored stop_size from entryData (calculated from LOD/HOD)
                    stored_stop_size = entryData.get('calculated_stop_size')
                    if stored_stop_size is not None and stored_stop_size > 0:
                        stop_size = stored_stop_size
                        logging.info(f"RB/RBB HOD/LOD TP (SHORT): Using stored stop_size={stop_size} from LOD/HOD for take profit")
                    else:
                        # Fallback: recalculate from LOD/HOD
                        # Uses premarket data for premarket, RTH data for after hours
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                        if lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
                            # For HOD/LOD: stop_size = |bar_low - HOD/LOD| (for SHORT position)
                            bar_price = float(histData['low'])  # For SHORT, use bar_low
                            # SHORT position: auto-detect HOD
                            stop_size = abs(bar_price - hod)  # SHORT uses HOD
                            logging.warning(f"RB/RBB HOD/LOD TP (SHORT): Recalculated stop_size={stop_size} from bar_low={bar_price} and HOD={hod}")
                        else:
                            # Fallback to bar-based
                            try:
                                stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                                logging.warning(f"RB/RBB: HOD/LOD historical data missing, using bar-based stop_size={stop_size} for TP")
                            except Exception:
                                stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                logging.warning(f"RB/RBB: Using fallback bar-based stop_size={stop_size} for TP")
                elif stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop_size for take profit (same as entry and stop loss)
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop == 0:
                        # Fallback to bar-based if custom value missing
                        try:
                            stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                            logging.warning(f"RB/RBB: Custom stop loss value missing, using bar-based stop_size={stop_size} for TP")
                        except Exception:
                            stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                            logging.warning(f"RB/RBB: Using fallback bar-based stop_size={stop_size} for TP")
                    else:
                        # For RBB with Custom stop loss: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|
                        # Use entry_price (not filled_price) - entry_price is stored in lastPrice
                        entry_price = entry_stop_price  # Use entry_stop_price (from lastPrice)
                        if histData and isinstance(histData, dict):
                            # For SHORT position (BUY TP): entry was SELL, so use bar_low
                            bar_price = float(histData.get('low', entry_price))
                            stop_size = abs(bar_price - custom_stop)
                        else:
                            # Fallback: use entry_price if histData not available
                            stop_size = abs(float(entry_price) - custom_stop)
                        stop_size = round(stop_size, Config.roundVal)
                        logging.info(f"RB/RBB Custom TP (SHORT): entry={entry_price}, bar_low={histData.get('low') if histData else 'N/A'}, custom_stop={custom_stop}, stop_size={stop_size} for take profit")
                else:
                    # Non-ATR, Non-Custom stop loss: use bar-based stop_size (same as entry and stop loss)
                    try:
                        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                    except Exception:
                        stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                    logging.info(f"RB/RBB bar-based TP (SHORT): Using bar-based stop_size={stop_size} for take profit")

                # For PBe2: Calculate TP immediately using stored stop_size (PBe1 already handled above)
                if is_pbe2:  # PBe2 - similar to PBe1
                    # PBe2: Entry price for TP = entry_stop_price (bar_high/low ± 0.01), stop_size = |entry_stop_price - HOD/LOD|
                    # Get stored LOD/HOD from orderStatusData
                    order_id = entryData.get('orderId')
                    order_data = Config.orderStatusData.get(order_id) if order_id else None
                    
                    if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                        lod = order_data.get('pbe1_lod', 0)
                        hod = order_data.get('pbe1_hod', 0)
                        stored_stop_size = order_data.get('stopSize', 0)
                        
                        # For SHORT position (SELL entry): stop_size = |entry_stop_price - HOD|
                        if entryData.get('action') == 'SELL':  # SHORT position
                            pbe_stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(entry_stop_price) - hod)
                            logging.info(f"PBe2 TP (SHORT): Using stored stop_size={pbe_stop_size} from HOD={hod}, entry_stop_price={entry_stop_price}")
                        else:  # LONG position (BUY entry)
                            pbe_stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(entry_stop_price) - lod)
                            logging.info(f"PBe2 TP (LONG): Using stored stop_size={pbe_stop_size} from LOD={lod}, entry_stop_price={entry_stop_price}")
                        
                        pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                        if pbe_stop_size <= 0:
                            logging.error(f"PBe2 TP: Invalid stop_size ({pbe_stop_size}) from HOD/LOD. HOD={hod}, LOD={lod}, entry_stop_price={entry_stop_price}")
                            pbe_stop_size = 0
                    else:
                        # Fallback: recalculate from HOD/LOD
                        logging.warning(f"PBe2: LOD/HOD not found in orderStatusData, recalculating")
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                        if entryData.get('action') == 'SELL':  # SHORT position
                            pbe_stop_size = abs(float(entry_stop_price) - hod) if hod else 0
                        else:  # LONG position
                            pbe_stop_size = abs(float(entry_stop_price) - lod) if lod else 0
                        pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                        logging.warning(f"PBe2 TP: Recalculated stop_size={pbe_stop_size} from HOD/LOD")
                    
                    # Calculate TP: entry_stop_price ± stop_size * multiplier
                    multiplier_map = {
                        Config.takeProfit[0]: 1,    # 1:1
                        Config.takeProfit[1]: 1.5,  # 1.5:1
                        Config.takeProfit[2]: 2,    # 2:1
                        Config.takeProfit[3]: 2.5,  # 2.5:1
                    }
                    if len(Config.takeProfit) > 4:
                        multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                    
                    multiplier = multiplier_map.get(entryData['profit'], 2.0)  # Default 2:1
                    if entryData.get('action') == 'SELL':  # SHORT position
                        # For SHORT: TP = entry_stop_price - (multiplier × stop_size)
                        price = float(entry_stop_price) - (multiplier * pbe_stop_size)
                    else:  # LONG position
                        # For LONG: TP = entry_stop_price + (multiplier × stop_size)
                        price = float(entry_stop_price) + (multiplier * pbe_stop_size)
                    
                    price = round(price, Config.roundVal)
                    logging.info(f"PBe2 TP calculation: entry_stop_price={entry_stop_price}, stop_size={pbe_stop_size}, multiplier={multiplier}, tp={price}")
                    
                    # Send TP order directly for PBe2 - don't fall through to duplicate send
                    if entryData.get('action') == 'SELL':  # SHORT position
                        tp_action = "BUY"  # To close short
                    else:  # LONG position
                        tp_action = "SELL"  # To close long
                    logging.info(f"PBe2 Sending TP Trade EntryData is %s  and Price is %s  and action is {tp_action}", entryData, price)
                    sendTakeProfit(connection, entryData, price, tp_action)
                    # Skip the rest of TP calculation - already sent
                    price = None  # Mark as already sent
                    # Continue to stop loss calculation - don't return early
                else:
                    # For other trade types (FB/RB/RBB/LB/LB2/LB3): use shared calculation
                    # Skip if price is already None (PBe1/PBe2 already sent TP)
                    if price is None:
                        logging.info(f"FB/RB/RBB/LB/LB2/LB3 TP: price is None (already sent by PBe1/PBe2), skipping duplicate TP send")
                    else:
                        # Check if this is HOD/LOD stop loss - need special handling for TP calculation
                        if is_lod_hod:
                            # For HOD/LOD: Calculate stop_size using HOD/LOD values (same as SL calculation)
                            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                            is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                            
                            if lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
                                # For SHORT position (BUY TP): use HOD for stop_size calculation
                                if stop_loss_type == Config.stopLoss[3]:  # HOD
                                    if is_extended:
                                        # Extended hours: stop_size = |bar_low - HOD| (same as SL calculation)
                                        bar_low = float(histData.get('low', entry_stop_price))
                                        stop_size = abs(bar_low - hod)
                                        logging.info(f"RB/RBB HOD Extended hours TP (SHORT): stop_size = |bar_low - HOD| = |{bar_low} - {hod}| = {stop_size}")
                                    else:
                                        # RTH: stop_size = |entry_stop_price - HOD|
                                        stop_size = abs(float(entry_stop_price) - hod)
                                        logging.info(f"RB/RBB HOD RTH TP (SHORT): stop_size = |entry_stop_price - HOD| = |{entry_stop_price} - {hod}| = {stop_size}")
                                else:  # LOD (should not happen for SHORT, but handle it)
                                    if is_extended:
                                        bar_high = float(histData.get('high', entry_stop_price))
                                        stop_size = abs(bar_high - lod)
                                    else:
                                        stop_size = abs(float(entry_stop_price) - lod)
                                    logging.warning(f"RB/RBB LOD TP (SHORT): Using LOD as fallback, stop_size={stop_size}")
                                
                                stop_size = round(stop_size, Config.roundVal)
                                if stop_size <= 0:
                                    # Fallback to bar range
                                    try:
                                        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                                        logging.warning(f"RB/RBB HOD/LOD TP: Invalid stop_size, using bar range={stop_size}")
                                    except Exception:
                                        stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                        logging.warning(f"RB/RBB HOD/LOD TP: Using fallback bar-based stop_size={stop_size}")
                            else:
                                # Fallback to bar range if HOD/LOD unavailable
                                try:
                                    stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                                    logging.warning(f"RB/RBB HOD/LOD TP: HOD/LOD unavailable, using bar range stop_size={stop_size}")
                                except Exception:
                                    stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                    logging.warning(f"RB/RBB HOD/LOD TP: Using fallback bar-based stop_size={stop_size}")
                        else:
                            # Non-HOD/LOD: Calculate stop_size normally
                            # First, calculate stop_size if not already calculated
                            if 'stop_size' not in locals() and 'stop_size' not in globals():
                                # stop_size was not set in the elif blocks above, need to calculate it
                                # Try to get stored stop_size from entryData
                                stored_stop_size = entryData.get('calculated_stop_size')
                                if stored_stop_size is not None and stored_stop_size > 0:
                                    stop_size = stored_stop_size
                                    logging.info(f"FB/RB/RBB/LB/LB2/LB3 TP (SHORT): Using stored stop_size={stop_size} from entryData")
                                else:
                                    # Fallback: calculate from bar range
                                    try:
                                        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                                        logging.info(f"FB/RB/RBB/LB/LB2/LB3 TP (SHORT): Calculated stop_size={stop_size} from bar range")
                                    except Exception:
                                        stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                        logging.warning(f"FB/RB/RBB/LB/LB2/LB3 TP (SHORT): Using fallback bar-based stop_size={stop_size}")
                            
                            # Ensure stop_size is defined before using it
                            if 'stop_size' not in locals():
                                # stop_size was not set in the elif blocks above, need to calculate it
                                # Try to get stored stop_size from entryData
                                stored_stop_size = entryData.get('calculated_stop_size')
                                if stored_stop_size is not None and stored_stop_size > 0:
                                    stop_size = stored_stop_size
                                    logging.info(f"FB/RB/RBB/LB/LB2/LB3 TP (SHORT): Using stored stop_size={stop_size} from entryData")
                                else:
                                    # Fallback: calculate from bar range
                                    try:
                                        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                                        logging.info(f"FB/RB/RBB/LB/LB2/LB3 TP (SHORT): Calculated stop_size={stop_size} from bar range")
                                    except Exception:
                                        stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                        logging.warning(f"FB/RB/RBB/LB/LB2/LB3 TP (SHORT): Using fallback bar-based stop_size={stop_size}")
                        
                        multiplier_map = {
                            Config.takeProfit[0]: 1,    # "1:1" means 1× stop_size
                            Config.takeProfit[1]: 1.5,  # "1.5:1" means 1.5× stop_size
                            Config.takeProfit[2]: 2,    # "2:1" means 2× stop_size
                            Config.takeProfit[3]: 2.5,  # "2.5:1" means 2.5× stop_size (index 3)
                        }
                        # Add 3:1 if it exists (index 4)
                        if len(Config.takeProfit) > 4:
                            multiplier_map[Config.takeProfit[4]] = 3  # "3:1" means 3× stop_size

                        multiplier = multiplier_map.get(entryData['profit'])
                        if multiplier is not None:
                            # For SHORT position: TP = entry_stop_price - (multiplier × stop_size)
                            # This ensures TP is BELOW entry (to buy back and close short at profit)
                            price = float(entry_stop_price) - (multiplier * stop_size)
                            logging.info(f"RB/RBB TP (SHORT): entry_stop_price={entry_stop_price}, stop_size={stop_size}, multiplier={multiplier}, TP={price}")
                        else:
                            # Fallback if multiplier not available
                            price = float(histData['low'])
                            logging.warning(f"FB/RB/RBB/LB/LB2/LB3 TP (SHORT): Using fallback price={price} (multiplier={multiplier})")

                        price = round(price, Config.roundVal)
                        logging.info(
                            "Extended TP calculation (buy/SHORT) %s stop_size=%s multiplier=%s entry_stop_price=%s filled_price=%s price=%s",
                            entryData['contract'], stop_size, multiplier, entry_stop_price, filled_price, price,
                        )
                        
                        # Send TP order directly for FB/RB/RBB/LB/LB2/LB3 - don't fall through to get_tp_for_selling
                        # For SHORT position (entry was SELL), TP should be BUY (to close the short)
                        logging.info("Sending TP Trade EntryData is %s  and Price is %s  and action is BUY (SHORT position)", entryData, price)
                        sendTakeProfit(connection, entryData, price, "BUY")
                        # Skip the rest of TP calculation - already sent
                        price = None  # Mark as already sent

            else:
                # Check if this is a manual order (Stop Order or Limit Order) with custom or HOD/LOD stop loss
                if entryData['barType'] in Config.manualOrderTypes:
                    stop_loss_type = entryData.get('stopLoss')
                    
                    # Check if this is HOD/LOD stop loss
                    if stop_loss_type == Config.stopLoss[3] or stop_loss_type == Config.stopLoss[4]:  # HOD or LOD
                        # For HOD/LOD: recalculate stop_size using entry price and LOD/HOD
                        # Uses premarket data for premarket, RTH data for after hours
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                        
                        if lod is not None and hod is not None:
                            # For SHORT position (BUY TP): auto-detect HOD
                            stop_size = abs(float(entry_stop_price) - hod)  # SHORT uses HOD
                            
                            stop_size = round(stop_size, Config.roundVal)
                            multiplier_map = {
                                Config.takeProfit[0]: 1,    # 1:1
                                Config.takeProfit[1]: 1.5,  # 1.5:1
                                Config.takeProfit[2]: 2,    # 2:1
                                Config.takeProfit[3]: 2.5,  # 2.5:1
                            }
                            if len(Config.takeProfit) > 4:
                                multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                            multiplier = multiplier_map.get(entryData['profit'], 1)
                            # For SHORT position (BUY TP): TP = entry - (multiplier × stop_size)
                            price = float(entry_stop_price) - (multiplier * stop_size)
                            price = round(price, Config.roundVal)
                            logging.info(f"Manual Order HOD/LOD TP (SHORT): entry={entry_stop_price}, LOD={lod}, HOD={hod}, stop_size={stop_size}, multiplier={multiplier}, tp={price}")
                        else:
                            # Fallback to regular calculation if HOD/LOD unavailable
                            price = get_tp_for_buying(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                            logging.warning(f"Manual Order HOD/LOD TP (SHORT): LOD/HOD data unavailable, using fallback calculation")
                        # Skip further processing for HOD/LOD stop loss - price is already set
                    elif stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop_size for take profit (same as entry and stop loss)
                        custom_stop = _to_float(entryData.get('slValue', 0), 0)
                        if custom_stop == 0:
                            # Fallback to regular calculation if custom value missing
                            price = get_tp_for_buying(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                            logging.warning(f"Manual Order Custom TP (SHORT): Custom stop loss value missing, using fallback calculation")
                        else:
                            # stop_size = |entry - custom_stop| + 0.02 (same as entry order calculation)
                            stop_size = abs(float(entry_stop_price) - custom_stop) + 0.02
                            stop_size = round(stop_size, Config.roundVal)
                            multiplier_map = {
                                Config.takeProfit[0]: 1,    # 1:1
                                Config.takeProfit[1]: 1.5,  # 1.5:1
                                Config.takeProfit[2]: 2,    # 2:1
                                Config.takeProfit[3]: 2.5,  # 2.5:1
                            }
                            if len(Config.takeProfit) > 4:
                                multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                            multiplier = multiplier_map.get(entryData['profit'], 1)
                            # For SHORT position (BUY TP): TP = entry - (multiplier × stop_size)
                            price = float(entry_stop_price) - (multiplier * stop_size)
                            price = round(price, Config.roundVal)
                            logging.info(f"Manual Order Custom TP (SHORT): entry={entry_stop_price}, custom_stop={custom_stop}, stop_size={stop_size}, multiplier={multiplier}, tp={price}")
                    # Skip further processing for custom stop loss - price is already set
                    elif entryData['barType'] == 'Custom' and stop_loss_type not in Config.atrStopLossMap and stop_loss_type != Config.stopLoss[1]:  # Custom entry type with non-Custom, non-ATR stop loss
                        # For Custom entry type in extended hours with non-Custom, non-ATR stop loss: use stored stop_size
                        order_id = entryData.get('orderId')
                        order_data = Config.orderStatusData.get(order_id) if order_id else None
                        stored_stop_size = order_data.get('stopSize') if order_data else None
                        
                        if stored_stop_size is not None and stored_stop_size > 0:
                            stop_size = stored_stop_size
                            multiplier_map = {
                                Config.takeProfit[0]: 1,    # 1:1
                                Config.takeProfit[1]: 1.5,  # 1.5:1
                                Config.takeProfit[2]: 2,    # 2:1
                                Config.takeProfit[3]: 2.5,  # 2.5:1
                            }
                            if len(Config.takeProfit) > 4:
                                multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                            multiplier = multiplier_map.get(entryData['profit'], 1)
                            # For SHORT position (BUY TP): TP = entry - (multiplier × stop_size)
                            price = float(entry_stop_price) - (multiplier * stop_size)
                            price = round(price, Config.roundVal)
                            logging.info(f"Custom entry OTH TP (SHORT): entry={entry_stop_price}, stop_size={stored_stop_size} (from orderStatusData), stopLoss={stop_loss_type}, multiplier={multiplier}, tp={price}")
                        else:
                            # Fallback to regular calculation if stop_size not found
                            logging.warning(f"Custom entry OTH TP (SHORT): stop_size not found in orderStatusData, using fallback calculation")
                            price = get_tp_for_buying(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                # Check if this is a Limit Order with ATR stop loss - use same ATR stop_size for TP
                elif entryData['barType'] == Config.entryTradeType[0] and entryData.get('stopLoss') in Config.atrStopLossMap:
                    # Use ATR stop_size for take profit calculation (same as entry and stop loss)
                    atr_offset = _get_atr_stop_offset(connection, entryData['contract'], entryData['stopLoss'])
                    if atr_offset is not None and atr_offset > 0:
                        stop_size = atr_offset
                        multiplier_map = {
                            Config.takeProfit[0]: 1,    # 1:1
                            Config.takeProfit[1]: 1.5,  # 1.5:1
                            Config.takeProfit[2]: 2,    # 2:1
                            Config.takeProfit[3]: 2.5,  # 2.5:1
                        }
                        if len(Config.takeProfit) > 4:
                            multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                        multiplier = multiplier_map.get(entryData['profit'], 1)
                        # For SHORT position (BUY TP): TP = entry - (multiplier × stop_size)
                        price = float(entry_stop_price) - (multiplier * stop_size)
                        price = round(price, Config.roundVal)
                        logging.info(f"Limit Order ATR TP (SHORT): entry={entry_stop_price}, stop_size={stop_size} (ATR), multiplier={multiplier}, tp={price}")
                    else:
                        # Fallback to regular calculation if ATR unavailable
                        price = get_tp_for_buying(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                else:
                    price = get_tp_for_buying(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)

            # Ensure price is set before sending TP
            if price == 0 or price is None:
                logging.warning("Manual Order: TP price is 0 or None, checking if custom stop loss. barType=%s, stopLoss=%s", 
                               entryData.get('barType'), entryData.get('stopLoss'))
                # Try to calculate fallback TP for custom stop loss
                if entryData.get('barType') in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop > 0 and entry_stop_price > 0:
                        # stop_size = |entry - custom_stop| + 0.02 (same as entry order calculation)
                        stop_size = abs(float(entry_stop_price) - custom_stop) + 0.02
                        stop_size = round(stop_size, Config.roundVal)
                        multiplier_map = {
                            Config.takeProfit[0]: 1,    # 1:1
                            Config.takeProfit[1]: 1.5,  # 1.5:1
                            Config.takeProfit[2]: 2,    # 2:1
                            Config.takeProfit[3]: 2.5,  # 2.5:1
                        }
                        if len(Config.takeProfit) > 4:
                            multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                        multiplier = multiplier_map.get(entryData.get('profit'), 2.0)  # Default 2:1
                        # For SHORT position (BUY TP): TP = entry - (multiplier × stop_size)
                        price = float(entry_stop_price) - (multiplier * stop_size)
                        price = round(price, Config.roundVal)
                        logging.warning("Manual Order Custom: Recalculated TP using fallback: entry=%s, custom_stop=%s, stop_size=%s, multiplier=%s, price=%s", 
                                      entry_stop_price, custom_stop, stop_size, multiplier, price)
                    else:
                        logging.error("Manual Order Custom: Cannot calculate fallback TP - custom_stop=%s, entry_stop_price=%s", 
                                    custom_stop, entry_stop_price)
                        price = None
                else:
                    logging.error("Manual Order: Cannot calculate TP, skipping TP order. barType=%s, stopLoss=%s", 
                                entryData.get('barType'), entryData.get('stopLoss'))
                    price = None
            
            # Skip TP send for PBe1/PBe2 - they already sent TP above
            is_pbe1 = (entryData['barType'] == Config.entryTradeType[6])  # PBe1
            is_pbe2 = (entryData['barType'] == Config.entryTradeType[7])  # PBe2
            is_pbe1_or_pbe2 = is_pbe1 or is_pbe2
            
            if is_pbe1_or_pbe2:
                logging.info(f"PBe1/PBe2: Skipping duplicate TP send - already sent TP above. barType=%s, orderId=%s", 
                           entryData.get('barType'), entryData.get('orderId'))
            else:
                logging.info("Manual Order Custom: About to send TP - price=%s, barType=%s, stopLoss=%s, orderId=%s", 
                            price, entryData.get('barType'), entryData.get('stopLoss'), entryData.get('orderId'))
                if price is not None and price > 0:
                    logging.info("Sending TP Trade EntryData is %s  and Price is %s  and action is BUY",entryData,price)
                    sendTakeProfit(connection, entryData, price, "BUY")
                else:
                    logging.error("Manual Order: Skipping TP order due to invalid price=%s", price)

            # Calculate stop size in advance for RB, LB, LB2, LB3 (RBB uses different logic)
            stpPrice = 0
            chart_Time = datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime,
                                                   "%Y-%m-%d %H:%M:%S")
            
            # PBe1/PBe2: For RTH, recalculate LOD/HOD from current RTH data (not stored value)
            # Note: sendTpSlBuy is called for SELL entries (SHORT position), but we check entry action to be safe
            if (entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7]):  # PBe1 or PBe2
                bar_type_name = "PBe1" if entryData['barType'] == Config.entryTradeType[6] else "PBe2"
                
                # Check if RTH or extended hours
                is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                
                if not is_extended:
                    # RTH: Always recalculate LOD/HOD from current RTH data (same as RBB)
                    logging.info(f"{bar_type_name} RTH: Recalculating LOD/HOD from current RTH data for stop loss calculation")
                    try:
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                        
                        # Check entry action to determine position type
                        entry_action = entryData.get('action', 'SELL')  # Default to SELL since this is sendTpSlBuy
                        base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                        
                        if entry_action == 'BUY':  # LONG position - should use LOD
                            stop_loss_price = round(lod, Config.roundVal) if lod else 0
                            stop_size = abs(float(base_price) - lod) if lod else 0
                            logging.info(f"{bar_type_name} RTH: Using recalculated LOD={lod} for LONG position stop loss")
                        else:  # SELL entry (SHORT position) - use HOD
                            stop_loss_price = round(hod, Config.roundVal) if hod else 0
                            stop_size = abs(float(base_price) - hod) if hod else 0
                            logging.info(f"{bar_type_name} RTH: Using recalculated HOD={hod} for SHORT position stop loss")
                        
                        stpPrice = round(stop_loss_price, Config.roundVal)
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"{bar_type_name} RTH stop loss in sendTpSlBuy: base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), LOD={lod}, HOD={hod}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    except Exception as e:
                        logging.error(f"{bar_type_name} RTH: Error recalculating LOD/HOD: {e}. Using stored values as fallback.")
                        # Fallback: use stored LOD/HOD
                        order_id = entryData.get('orderId')
                        order_data = Config.orderStatusData.get(order_id) if order_id else None
                        if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                            lod = order_data.get('pbe1_lod', 0)
                            hod = order_data.get('pbe1_hod', 0)
                            stored_stop_size = order_data.get('stopSize', 0)
                            entry_action = entryData.get('action', 'SELL')
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            if entry_action == 'BUY':
                                stop_loss_price = round(lod, Config.roundVal)
                                stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(base_price) - lod)
                            else:
                                stop_loss_price = round(hod, Config.roundVal)
                                stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(base_price) - hod)
                            stpPrice = round(stop_loss_price, Config.roundVal)
                            entryData['calculated_stop_size'] = stop_size
                            logging.warning(f"{bar_type_name} RTH: Using stored LOD/HOD as fallback: LOD={lod}, HOD={hod}, stop_loss_price={stop_loss_price}")
                        else:
                            # If stored LOD/HOD not available, use bar-based fallback
                            logging.error(f"{bar_type_name} RTH: Stored LOD/HOD not available in fallback, using bar-based calculation")
                            entry_action = entryData.get('action', 'SELL')
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            if entry_action == 'BUY':
                                bar_low = float(histData.get('low', filled_price))
                                stop_loss_price = round(bar_low - 0.01, Config.roundVal)
                                stop_size = abs(float(base_price) - stop_loss_price)
                            else:
                                bar_high = float(histData.get('high', filled_price))
                                stop_loss_price = round(bar_high + 0.01, Config.roundVal)
                                stop_size = abs(float(base_price) - stop_loss_price)
                            stpPrice = round(stop_loss_price, Config.roundVal)
                            entryData['calculated_stop_size'] = stop_size
                            logging.warning(f"{bar_type_name} RTH: Using bar-based fallback: stop_loss_price={stop_loss_price}, stop_size={stop_size}")
                else:
                    # Extended hours: Use stored LOD/HOD (same as before)
                    order_id = entryData.get('orderId')
                    order_data = Config.orderStatusData.get(order_id) if order_id else None
                    if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                        lod = order_data.get('pbe1_lod', 0)
                        hod = order_data.get('pbe1_hod', 0)
                        stored_stop_size = order_data.get('stopSize', 0)
                        entry_action = entryData.get('action', 'SELL')
                        base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                        if entry_action == 'BUY':
                            stop_loss_price = round(lod, Config.roundVal)
                            stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(base_price) - lod)
                        else:
                            stop_loss_price = round(hod, Config.roundVal)
                            stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(base_price) - hod)
                        stpPrice = round(stop_loss_price, Config.roundVal)
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"{bar_type_name} Extended hours stop loss in sendTpSlBuy: base_price={base_price}, LOD={lod}, HOD={hod}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, stpPrice={stpPrice}")
                    else:
                        # Fallback: recalculate LOD/HOD for extended hours
                        logging.warning(f"{bar_type_name} Extended hours: LOD/HOD not found in orderStatusData, recalculating")
                        entry_action = entryData.get('action', 'SELL')  # Default to SELL since this is sendTpSlBuy
                        
                        if entry_action == 'BUY':  # LONG position - should use LOD
                            lod, hod = _get_pbe1_lod_hod(connection, entryData['contract'], entryData.get('timeFrame', '1 min'), "BUY")
                            if lod is None or lod == 0:
                                logging.error(f"{bar_type_name}: Could not get valid LOD after recalculation (lod={lod}). Using bar low as fallback.")
                                bar_low = float(histData.get('low', filled_price))
                                lod = bar_low - 0.01
                                logging.warning(f"{bar_type_name}: Using fallback LOD={lod} (bar_low - 0.01)")
                            stop_loss_price = round(lod, Config.roundVal)
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            stop_size = abs(float(base_price) - lod)
                            logging.info(f"{bar_type_name} Extended hours stop loss (LONG, recalculated) in sendTpSlBuy: base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), LOD={lod}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                        else:  # SELL entry (SHORT position) - use HOD
                            lod, hod = _get_pbe1_lod_hod(connection, entryData['contract'], entryData.get('timeFrame', '1 min'), "SELL")
                            if hod is None or hod == 0:
                                logging.error(f"{bar_type_name}: Could not get valid HOD after recalculation (hod={hod}). Using bar high as fallback.")
                                bar_high = float(histData.get('high', filled_price))
                                hod = bar_high + 0.01
                                logging.warning(f"{bar_type_name}: Using fallback HOD={hod} (bar_high + 0.01)")
                            stop_loss_price = round(hod, Config.roundVal)
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            stop_size = abs(float(base_price) - hod)
                            logging.info(f"{bar_type_name} Extended hours stop loss (SHORT, recalculated) in sendTpSlBuy: base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), HOD={hod}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                        
                        stpPrice = round(stop_loss_price, Config.roundVal)
                        entryData['calculated_stop_size'] = stop_size
            
            # For Conditional Order, RB, RBB, LB, LB2, LB3: calculate stop size in advance and use it for stop loss calculation
            # Note: RBB (entryTradeType[5]) uses different logic - it updates stop price continuously via rbb_loop_run, but still needs initial LOD/HOD calculation
            elif (entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[2] or entryData['barType'] == Config.entryTradeType[4] or entryData['barType'] == Config.entryTradeType[5] or 
                entryData['barType'] == Config.entryTradeType[8] or entryData['barType'] == Config.entryTradeType[9] or entryData['barType'] == Config.entryTradeType[10]):
                # Calculate stop size for stop loss
                stop_loss_type = entryData.get('stopLoss')
                if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                    # For EntryBar: stop_size = (bar_high - bar_low) + 0.02
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    stop_size = round(stop_size, Config.roundVal)
                    # In extended hours: stop = entry ± stop_size, limit = entry ± 2 × stop_size
                    # For SHORT position (BUY stop loss): stop_loss = entry + stop_size
                    # For Stop Order: Use entry_stop_price instead of filled_price
                    base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                    stpPrice = float(base_price) + float(stop_size)
                    logging.info(f"RB/LB/LB2/LB3 EntryBar stop loss (for SHORT): base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    entryData['calculated_stop_size'] = stop_size
                else:
                    # For other stop loss types: calculate stop size and use filled_price ± stop_size
                    # Check if stop loss type is ATR-based
                    if stop_loss_type in Config.atrStopLossMap:
                        # Use ATR offset for stop size
                        atr_offset = _get_atr_stop_offset(connection, entryData['contract'], stop_loss_type)
                        if atr_offset is not None and atr_offset > 0:
                            stop_size = atr_offset
                            protection_offset = stop_size * 2.0
                            logging.info(f"RB/LB/LB2/LB3 ATR stop loss: stop_size={stop_size} (ATR offset), protection_offset={protection_offset}")
                        else:
                            # Fallback to bar range if ATR unavailable
                            try:
                                stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
                                logging.warning(f"RB/LB/LB2/LB3: ATR unavailable, using bar range stop_size={stop_size}")
                            except Exception:
                                stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                protection_offset = stop_size * 2.0
                                logging.warning(f"RB/LB/LB2/LB3: Using fallback stop_size={stop_size}")
                    elif stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                        # Use Custom stop loss value directly
                        custom_stop = _to_float(entryData.get('slValue', 0), 0)
                        if custom_stop == 0:
                            # Fallback to bar range if custom value missing
                            try:
                                stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
                                logging.warning(f"RB/LB/LB2/LB3: Custom stop loss value missing, using bar range stop_size={stop_size}")
                            except Exception:
                                stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                protection_offset = stop_size * 2.0
                                logging.warning(f"RB/LB/LB2/LB3: Using fallback stop_size={stop_size}")
                            # Calculate stop loss price using stop size
                            # For Stop Order: Use entry_stop_price instead of filled_price
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            stpPrice = float(base_price) + float(stop_size)
                        else:
                            # For Custom: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|, stop_price = custom_stop
                            # For RBB: Use entry_stop_price (not filled_price) and bar high/low for stop_size calculation
                            # For Stop Order: Use entry_stop_price instead of filled_price
                            entry_price = entry_stop_price  # Use entry_price (not filled_price)
                            
                            if entryData.get('barType') == Config.entryTradeType[5]:  # RBB
                                # For RBB with Custom stop loss: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|
                                if histData and isinstance(histData, dict):
                                    # For SHORT position (BUY stop loss): entry was SELL, so use bar_low
                                    bar_price = float(histData.get('low', entry_price))
                                    stop_size = abs(bar_price - custom_stop)
                                else:
                                    # Fallback: use entry_price if histData not available
                                    stop_size = abs(float(entry_price) - custom_stop)
                            elif entryData.get('barType') == Config.entryTradeType[0]:  # Stop Order
                                # For Stop Order: use entry_price
                                stop_size = abs(float(entry_price) - custom_stop) + 0.02
                            else:
                                # For other trade types: use filled_price
                                stop_size = abs(float(filled_price) - custom_stop) + 0.02
                            
                            stop_size = round(stop_size, Config.roundVal)
                            protection_offset = stop_size * 2.0
                            # Stop loss price is the custom value directly (should NOT update - fixed at custom_stop)
                            stpPrice = round(custom_stop, Config.roundVal)
                            logging.info(f"RB/LB/LB2/LB3/RBB Custom stop loss (for SHORT): entry_price={entry_price}, bar_low={histData.get('low') if (histData and isinstance(histData, dict)) else 'N/A'}, custom_stop={custom_stop}, stop_size={stop_size}, stpPrice={stpPrice} (FIXED, should NOT update), barType={entryData.get('barType')}")
                            # Store stop_size in entryData for sendStopLoss to use in extended hours
                            entryData['calculated_stop_size'] = stop_size
                            logging.info(f"RB/LB/LB2/LB3/RBB BUY stop loss (for SHORT): entry_price={entry_price}, stop_size={stop_size}, stpPrice={stpPrice} (FIXED)")
                    elif stop_loss_type == Config.stopLoss[3] or stop_loss_type == Config.stopLoss[4]:  # HOD or LOD
                        # For LOD/HOD: Calculate LOD/HOD from historical data
                        # Uses premarket data for premarket, RTH data for after hours
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                        
                        # Check if extended hours
                        is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                        
                        if lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
                            # For SHORT position (BUY stop loss): use HOD
                            if stop_loss_type == Config.stopLoss[3]:  # HOD
                                if is_extended:
                                    # Extended hours: Stop loss price = Entry bar High (NOT HOD)
                                    # For Stop Order: Use entry_stop_price as fallback instead of filled_price
                                    base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                                    stop_loss_price = round(float(histData.get('high', base_price)), Config.roundVal)
                                    logging.info(f"RB/LB/LB2/LB3 HOD Extended hours: Stop loss price = Entry bar High = {stop_loss_price} (base_price={base_price}, entry_stop_price={entry_stop_price}, filled_price={filled_price}, barType={entryData.get('barType')})")
                                else:
                                    # RTH: Stop loss price = HOD (auto-detect for SHORT)
                                    stop_loss_price = round(hod, Config.roundVal)
                                    logging.info(f"RB/LB/LB2/LB3 HOD/LOD RTH SHORT: Auto-detected HOD, Stop loss price = HOD = {stop_loss_price}")
                            else:  # LOD (should not happen for SHORT, but handle it)
                                stop_loss_price = round(lod, Config.roundVal)
                                logging.warning(f"RB/LB/LB2/LB3: LOD selected for SHORT position, using LOD as fallback")
                            
                            # Calculate stop_size = |bar_low - HOD| for extended hours, |entry - HOD| for RTH
                            # Auto-detect: SHORT uses HOD
                            if is_extended:
                                bar_low = float(histData.get('low', entry_stop_price))
                                stop_size = abs(bar_low - hod)
                                logging.info(f"RB/LB/LB2/LB3 HOD/LOD Extended hours SHORT: stop_size = |bar_low - HOD| = |{bar_low} - {hod}| = {stop_size}")
                            else:
                                stop_size = abs(float(entry_stop_price) - stop_loss_price)
                            
                            protection_offset = stop_size * 2.0
                            
                            # Stop loss price
                            stpPrice = round(stop_loss_price, Config.roundVal)
                            logging.info(f"RB/LB/LB2/LB3 HOD/LOD stop loss (for SHORT): entry_stop_price={entry_stop_price}, filled_price={filled_price}, stop_loss_price={stop_loss_price}, LOD={lod}, HOD={hod}, stop_size={stop_size}, stpPrice={stpPrice}, is_extended={is_extended}")
                            
                            # Store stop_size and HOD for sendStopLoss to use in extended hours
                            entryData['calculated_stop_size'] = stop_size
                            # Store HOD for limit price calculation in extended hours (auto-detect for SHORT)
                            entryData['lod_hod_stop_price'] = hod
                        else:
                            # Fallback to bar range if no historical data
                            try:
                                stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
                                logging.warning(f"RB/LB/LB2/LB3: HOD/LOD historical data missing, using bar range stop_size={stop_size}")
                            except Exception:
                                stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                protection_offset = stop_size * 2.0
                                logging.warning(f"RB/LB/LB2/LB3: Using fallback stop_size={stop_size}")
                            # Calculate stop loss price using stop size
                            # For Stop Order: Use entry_stop_price instead of filled_price
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            stpPrice = float(base_price) + float(stop_size)
                            entryData['calculated_stop_size'] = stop_size
                            logging.info(f"RB/LB/LB2/LB3 HOD/LOD fallback stop loss (for SHORT): base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    else:
                        # Non-ATR, Non-Custom stop loss: use bar range
                        try:
                            stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
                            logging.info(f"RB/LB/LB2/LB3: Calculated stop_size={stop_size}, protection_offset={protection_offset} for stop loss")
                        except Exception:
                            stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                            protection_offset = stop_size * 2.0
                            logging.warning(f"RB/LB/LB2/LB3: Using fallback stop_size={stop_size}")
                    
                    # Calculate stop loss price using stop size
                    # For SHORT position (BUY stop loss): stop_loss = entry + stop_size
                    # In extended hours, this will be used as stop price for STP LMT, with limit = entry + 2 × stop_size
                    # For Stop Order: Use entry_stop_price instead of filled_price
                    base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                    stpPrice = float(base_price) + float(stop_size)
                    logging.info(f"RB/LB/LB2/LB3 BUY stop loss (for SHORT): base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    # Store stop_size in entryData for sendStopLoss to use in extended hours
                    entryData['calculated_stop_size'] = stop_size
            else:
                # Check if this is a manual order (Stop Order or Limit Order) with custom stop loss
                if entryData['barType'] in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop loss value directly
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    # For Stop Order: Use entry_stop_price instead of filled_price
                    base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                    if custom_stop == 0:
                        # Fallback to existing logic if custom value missing
                        stpPrice = get_sl_for_buying(connection, entryData['stopLoss'], base_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
                        logging.warning(f"Manual Order Custom stop loss (for SHORT): Custom stop loss value missing, using fallback calculation with base_price={base_price}")
                        stpPrice = stpPrice + 0.01
                    else:
                        # For Custom: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|, stop_price = custom_stop
                        # For RBB: Use entry_stop_price (not filled_price) and bar high/low for stop_size calculation
                        entry_price = entry_stop_price  # Use entry_price (not filled_price)
                        
                        if entryData.get('barType') == Config.entryTradeType[5]:  # RBB
                            # For RBB with Custom stop loss: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|
                            if histData and isinstance(histData, dict):
                                # For SHORT position (BUY stop loss): entry was SELL, so use bar_low
                                bar_price = float(histData.get('low', entry_price))
                                stop_size = abs(bar_price - custom_stop)
                            else:
                                # Fallback: use entry_price if histData not available
                                stop_size = abs(float(entry_price) - custom_stop)
                        else:
                            # For Stop Order: use entry_price with +0.02
                            stop_size = abs(float(entry_price) - custom_stop) + 0.02
                        
                        stop_size = round(stop_size, Config.roundVal)
                        # Stop loss price is the custom value directly (should NOT update - fixed at custom_stop)
                        stpPrice = round(custom_stop, Config.roundVal)
                        # Store stop_size in entryData for sendStopLoss to use in extended hours
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"Manual Order/RBB Custom stop loss (for SHORT): entry_price={entry_price}, bar_low={histData.get('low') if (histData and isinstance(histData, dict)) else 'N/A'}, custom_stop={custom_stop}, stop_size={stop_size}, stpPrice={stpPrice} (FIXED, should NOT update), barType={entryData.get('barType')}")
                    # Custom stop loss logic complete - stpPrice is set, continue to send order
                # For other strategies (including RBB), use existing logic
                else:
                    # For Stop Order: Use entry_stop_price instead of filled_price
                    base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                    stpPrice = get_sl_for_buying(connection, entryData['stopLoss'], base_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
                    logging.info(f"Manual Order stop loss (for SHORT): base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    logging.info(f"BUY stop loss (for SHORT): Base price from get_sl_for_buying={stpPrice}, bar high={entryData['histData'].get('high')}, bar low={entryData['histData'].get('low')}")
                    stpPrice = stpPrice + 0.01
                    logging.info(f"BUY stop loss (for SHORT): After +0.01 adjustment={stpPrice}, filled_price={filled_price}")
            
            # Ensure stpPrice is set before sending SL
            if stpPrice == 0:
                # Check if this is PBe1/PBe2 with invalid HOD/LOD
                if (entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7]):  # PBe1 or PBe2
                    bar_type_name = "PBe1" if entryData['barType'] == Config.entryTradeType[6] else "PBe2"
                    logging.warning(f"{bar_type_name}: SL price is 0, attempting fallback calculation. barType=%s, orderId=%s", 
                                   entryData.get('barType'), entryData.get('orderId'))
                    # Try to recalculate HOD/LOD one more time
                    lod, hod = _get_pbe1_lod_hod(connection, entryData['contract'], entryData.get('timeFrame', '1 min'), 
                                                "SELL" if entryData.get('action') == 'SELL' else "BUY")
                    if entryData.get('action') == 'SELL':  # SHORT position
                        if hod is not None and hod > 0:
                            stpPrice = round(hod, Config.roundVal)
                            logging.info(f"{bar_type_name}: Fallback HOD={hod}, stpPrice={stpPrice}")
                        else:
                            # Last resort: use bar high + 0.01
                            bar_high = float(histData.get('high', filled_price))
                            stpPrice = round(bar_high + 0.01, Config.roundVal)
                            logging.warning(f"{bar_type_name}: Using last resort fallback: bar_high + 0.01 = {stpPrice}")
                    else:  # LONG position
                        if lod is not None and lod > 0:
                            stpPrice = round(lod, Config.roundVal)
                            logging.info(f"{bar_type_name}: Fallback LOD={lod}, stpPrice={stpPrice}")
                        else:
                            # Last resort: use bar low - 0.01
                            bar_low = float(histData.get('low', filled_price))
                            stpPrice = round(bar_low - 0.01, Config.roundVal)
                            logging.warning(f"{bar_type_name}: Using last resort fallback: bar_low - 0.01 = {stpPrice}")
                elif entryData.get('barType') in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:
                    logging.warning("Manual Order: SL price is 0, checking if custom stop loss. barType=%s, stopLoss=%s", 
                               entryData.get('barType'), entryData.get('stopLoss'))
                    # Try to calculate fallback SL for custom stop loss
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop > 0:
                        stpPrice = round(custom_stop, Config.roundVal)
                        stop_size = abs(float(filled_price) - custom_stop)
                        entryData['calculated_stop_size'] = stop_size
                        logging.warning("Manual Order Custom: Recalculated SL using fallback: filled_price=%s, custom_stop=%s, stop_size=%s, stpPrice=%s", 
                                      filled_price, custom_stop, stop_size, stpPrice)
                    else:
                        logging.error("Manual Order Custom: Cannot calculate fallback SL - custom_stop=%s, filled_price=%s", 
                                    custom_stop, filled_price)
                        stpPrice = None
                else:
                    logging.error("Manual Order: Cannot calculate SL, skipping SL order. barType=%s, stopLoss=%s", 
                                entryData.get('barType'), entryData.get('stopLoss'))
                    stpPrice = None
            
            logging.info("sendTpSlBuy: About to send SL - stpPrice=%s, barType=%s, stopLoss=%s, orderId=%s, action=BUY (SHORT position)", 
                        stpPrice, entryData.get('barType'), entryData.get('stopLoss'), entryData.get('orderId'))
            logging.info("sendTpSlBuy: Checking condition - stpPrice is not None: %s, stpPrice > 0: %s", 
                        stpPrice is not None, stpPrice > 0 if stpPrice is not None else False)
            
            # Check if protection order already filled (for RBB in extended hours)
            if entryData.get('protection_order_filled', False):
                logging.warning("sendTpSlBuy: Protection order already filled, skipping stop loss order placement. barType=%s, orderId=%s", 
                            entryData.get('barType'), entryData.get('orderId'))
            elif stpPrice is not None and stpPrice > 0:
                # Determine stop loss action based on entry action
                # sendTpSlBuy is called for SELL entries (SHORT position), so stop loss is BUY (to close SHORT)
                # sendTpSlSell is called for BUY entries (LONG position), so stop loss is SELL (to close LONG)
                entry_action = entryData.get('action', 'SELL')  # Default to SELL since this is sendTpSlBuy
                if entry_action == 'SELL':  # SHORT position
                    stop_loss_action = "BUY"  # BUY to close SHORT position
                    logging.info("sendTpSlBuy: Sending STPLOSS Trade EntryData is %s  and Price is %s and action is BUY (SHORT position stop loss) and hist Data [ %s ]", entryData, stpPrice,histData)
                    logging.info("sendTpSlBuy: CALLING sendStopLoss NOW - barType=%s, orderId=%s, stpPrice=%s, action=BUY (SELL entry = SHORT position, stop loss is BUY)", 
                                entryData.get('barType'), entryData.get('orderId'), stpPrice)
                else:  # BUY entry (LONG position) - should not happen in sendTpSlBuy, but handle for safety
                    stop_loss_action = "SELL"  # SELL to close LONG position
                    logging.warning("sendTpSlBuy: Unexpected BUY entry in sendTpSlBuy, using SELL stop loss. barType=%s, orderId=%s", 
                                entryData.get('barType'), entryData.get('orderId'))
                    logging.info("sendTpSlBuy: Sending STPLOSS Trade EntryData is %s  and Price is %s and action is SELL (LONG position stop loss) and hist Data [ %s ]", entryData, stpPrice,histData)
                    logging.info("sendTpSlBuy: CALLING sendStopLoss NOW - barType=%s, orderId=%s, stpPrice=%s, action=SELL (BUY entry = LONG position, stop loss is SELL)", 
                                entryData.get('barType'), entryData.get('orderId'), stpPrice)
                try:
                    sendStopLoss(connection, entryData, stpPrice, stop_loss_action)
                    logging.info("sendTpSlBuy: sendStopLoss returned successfully for barType=%s, orderId=%s, action=%s, stpPrice=%s", 
                                entryData.get('barType'), entryData.get('orderId'), stop_loss_action, stpPrice)
                except Exception as e:
                    logging.error("sendTpSlBuy: Exception in sendStopLoss: %s", e)
                    logging.error("sendTpSlBuy: Traceback: %s", traceback.format_exc())
                    traceback.print_exc()
            else:
                logging.error("sendTpSlBuy: Skipping SL order due to invalid price=%s, barType=%s, orderId=%s", 
                            stpPrice, entryData.get('barType'), entryData.get('orderId'))
            
            mocPrice = 0
            logging.info("Sending Moc Order  of %s price ",mocPrice)
            sendMoc(connection,entryData,mocPrice,"BUY")
            
            # Handle option trading if enabled (using external module)
            try:
                from OptionTrading import handleOptionTrading
                # Store calculated prices in entryData for option trading
                if 'stpPrice' in locals() and stpPrice and stpPrice > 0:
                    entryData['stop_loss_price'] = stpPrice
                if 'price' in locals() and price and price > 0:
                    entryData['profit_price'] = price
                if 'filled_price' in locals():
                    entryData['filledPrice'] = filled_price
                handleOptionTrading(connection, entryData)
            except ImportError:
                logging.warning("OptionTrading module not found, skipping option trading")
            except Exception as e:
                logging.error("Error in option trading: %s", e)
            
            break
    except Exception as e:
        logging.error("error in take profit and sl buy trade %s",e)
        logging.error("Traceback: %s", traceback.format_exc())
        print(e)
        traceback.print_exc()


def sendMoc(connection, entryData,price,action):
    try:
        print("moc trade sending")
        mocResponse = connection.placeTrade(contract=entryData['contract'],
                                            order=Order(orderType="MOC", action=action,
                                                        totalQuantity=entryData['totalQuantity'], ocaGroup="tp" + str(entryData['orderId']), ocaType=1) ,outsideRth = entryData['outsideRth'] )
        StatusUpdate(mocResponse, 'Marketonclose', entryData['contract'], 'MOC', action, entryData['totalQuantity'], entryData['histData'], price, entryData['usersymbol'], entryData['timeFrame'], entryData['profit'], entryData['stopLoss'], entryData['risk'],entryData,'','','','',entryData['slValue'],entryData['breakEven'],entryData['outsideRth'] )
    except Exception as e:
        logging.error("error in sending moc trade %s ", e)
        print(e)



def sendStopLoss(connection, entryData,price,action):
    try:
        logging.info(f"sendStopLoss FUNCTION ENTRY: barType={entryData.get('barType', '')}, action={action}, price={price}, orderId={entryData.get('orderId')}")
        is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
        bar_type = entryData.get('barType', '')
        logging.info(f"sendStopLoss CALLED: barType={bar_type}, action={action}, price={price}, is_extended={is_extended}, session={session}, orderId={entryData.get('orderId')}")
        
        # Validate input price
        if price is None or price <= 0:
            logging.error(f"sendStopLoss: Invalid price parameter={price} for barType={bar_type}, action={action}")
            raise ValueError(f"Invalid price parameter: {price}")
        
        order_type = "STP"
        order_kwargs = dict(
            orderType="STP",
            action=action,
            totalQuantity=entryData['totalQuantity'],
            tif=entryData['tif'],
            ocaGroup="tp" + str(entryData['orderId']),
            ocaType=1
        )

        hist_data = entryData.get('histData')
        adjusted_price = round(price, Config.roundVal)
        logging.info(f"sendStopLoss: Initial adjusted_price={adjusted_price} (from price={price})")

        # Check if this is a manual stop order
        bar_type = entryData.get('barType', '')
        is_manual_stop_order = bar_type == 'Custom'
        
        # PBe1/PBe2: In regular hours, ALWAYS recalculate LOD/HOD from current RTH data (not stored value)
        # This ensures we use the most current LOD/HOD, which can change as new bars form during RTH
        if not is_extended and bar_type in [Config.entryTradeType[6], Config.entryTradeType[7]]:  # PBe1 or PBe2 in regular hours
            bar_type_name = "PBe1" if bar_type == Config.entryTradeType[6] else "PBe2"
            
            # For RTH, always recalculate LOD/HOD using the same function as RBB to get current values
            logging.info(f"{bar_type_name} RTH: Recalculating LOD/HOD from current RTH data (not using stored value)")
            try:
                # Use _get_lod_hod_for_stop_loss (same as RBB) to get current LOD/HOD from RTH data
                lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                
                # For LONG position (SELL stop loss): use LOD
                # For SHORT position (BUY stop loss): use HOD
                if action.upper() == "SELL":  # LONG position
                    correct_stop_price = round(lod, Config.roundVal)
                    logging.info(f"{bar_type_name} RTH: Using recalculated LOD={lod} for LONG position stop loss (action=SELL)")
                else:  # BUY stop loss (SHORT position)
                    correct_stop_price = round(hod, Config.roundVal)
                    logging.info(f"{bar_type_name} RTH: Using recalculated HOD={hod} for SHORT position stop loss (action=BUY)")
                
                # ALWAYS use recalculated LOD/HOD for PBe1/PBe2 in RTH, regardless of passed price
                if correct_stop_price > 0:
                    adjusted_price = correct_stop_price
                    logging.info(f"{bar_type_name} RTH: Using recalculated LOD/HOD stop price={adjusted_price} (LOD={lod}, HOD={hod}, action={action}, passed_price={price})")
                else:
                    logging.error(f"{bar_type_name} RTH: Recalculated LOD/HOD is zero! LOD={lod}, HOD={hod}. Using passed price={adjusted_price} as fallback.")
            except Exception as e:
                logging.error(f"{bar_type_name} RTH: Error recalculating LOD/HOD: {e}. Using passed price={adjusted_price} as fallback.")
                # Fallback: try using stored LOD/HOD if recalculation fails
                order_id = entryData.get('orderId')
                order_data = Config.orderStatusData.get(order_id) if order_id else None
                if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                    lod = order_data.get('pbe1_lod', 0)
                    hod = order_data.get('pbe1_hod', 0)
                    if action.upper() == "SELL":  # LONG position
                        correct_stop_price = round(lod, Config.roundVal)
                    else:  # BUY stop loss (SHORT position)
                        correct_stop_price = round(hod, Config.roundVal)
                    if correct_stop_price > 0:
                        adjusted_price = correct_stop_price
                        logging.warning(f"{bar_type_name} RTH: Using stored LOD/HOD as fallback: stop_price={adjusted_price} (LOD={lod}, HOD={hod})")
        
        # Ensure adjusted_price is valid
        if adjusted_price is None or adjusted_price <= 0:
            logging.error(f"sendStopLoss: Invalid adjusted_price={adjusted_price} for barType={bar_type}, action={action}, price={price}")
            raise ValueError(f"Invalid stop price: adjusted_price={adjusted_price}")
        
        order_kwargs['auxPrice'] = adjusted_price
        logging.info(f"sendStopLoss: Set auxPrice={adjusted_price} in order_kwargs for barType={bar_type}, action={action}")
        
        extended_hist_supported = (
            is_extended and isinstance(hist_data, dict) and
            'high' in hist_data and 'low' in hist_data
        )

        # During extended hours, always use STP LMT orders, even if hist_data is not available
        if is_extended and not extended_hist_supported:
            # Fallback: try to get hist_data from connection or use a default calculation
            logging.warning(f"Extended hours detected but hist_data not available or invalid. Attempting to fetch or use fallback.")
            try:
                chartTime = getRecentChartTime(entryData.get('timeFrame', '1 min'))
                hist_data = connection.getHistoricalChartData(entryData['contract'], entryData.get('timeFrame', '1 min'), chartTime)
                if isinstance(hist_data, dict) and 'high' in hist_data and 'low' in hist_data:
                    extended_hist_supported = True
                    logging.info(f"Successfully fetched hist_data for extended hours stop loss: {hist_data}")
                else:
                    # Use filled price with a default stop size percentage
                    filled_price = Config.orderFilledPrice.get(entryData['orderId'])
                    if filled_price is None:
                        filled_price = entryData.get('lastPrice', adjusted_price)
                    # Use 1% of filled price as default stop size
                    default_stop_size = filled_price * 0.01
                    stop_size = default_stop_size
                    protection_offset = default_stop_size * 2.0
                    limit_offset = default_stop_size * 1.0
                    logging.warning(f"Using default stop size calculation (1% of filled price): stop_size={stop_size}, filled_price={filled_price}")
                    extended_hist_supported = True  # Set to True to use STP LMT logic
            except Exception as e:
                logging.error(f"Error fetching hist_data for extended hours: {e}")
                # Use filled price with a default stop size percentage
                filled_price = Config.orderFilledPrice.get(entryData['orderId'])
                if filled_price is None:
                    filled_price = entryData.get('lastPrice', adjusted_price)
                default_stop_size = filled_price * 0.01
                stop_size = default_stop_size
                protection_offset = default_stop_size * 2.0
                limit_offset = default_stop_size * 1.0
                logging.warning(f"Using default stop size calculation (1% of filled price) after error: stop_size={stop_size}, filled_price={filled_price}")
                extended_hist_supported = True  # Set to True to use STP LMT logic

        if extended_hist_supported and is_extended:
            # Extended hours: Get entry filled price for manual stop order calculations
            filled_price = Config.orderFilledPrice.get(entryData['orderId'])
            if filled_price is None:
                filled_price = entryData.get('lastPrice', adjusted_price)
            
            # Check if stop loss type is ATR-based
            stop_loss_type = entryData.get('stopLoss')
            if stop_loss_type in Config.atrStopLossMap:
                # Use ATR for stop size calculation
                atr_offset = _get_atr_stop_offset(connection, entryData['contract'], stop_loss_type)
                if atr_offset is not None:
                    stop_size = atr_offset
                    protection_offset = 2 * atr_offset  # 2x for stop price
                    limit_offset = 1 * atr_offset  # 1x for limit price
                    logging.info(f"Extended hours ATR stop-limit: action={action}, ATR stop_size={stop_size}, protection_offset={protection_offset}, limit_offset={limit_offset}")
                else:
                    # Fallback to bar-based calculation if ATR unavailable
                    stop_size, entry_offset, protection_offset = _calculate_stop_limit_offsets(hist_data)
                    limit_offset = stop_size * 1.0  # 1x for limit price
                    logging.warning(f"ATR unavailable, using bar-based stop size: {stop_size}")
            else:
                # Check if this is RB/LB/LB2/LB3 with calculated stop_size
                calculated_stop_size = entryData.get('calculated_stop_size')
                if calculated_stop_size is not None and calculated_stop_size > 0:
                    # RB/LB/LB2/LB3: use the pre-calculated stop_size
                    stop_size = calculated_stop_size
                    # For RB in extended hours: stop = entry ± stop_size, limit = entry ± 2 × stop_size
                    protection_offset = stop_size * 1.0  # 1x for stop price (already calculated in sendTpSlBuy/Sell)
                    limit_offset = stop_size * 2.0  # 2x for limit price
                    logging.info(f"RB/LB/LB2/LB3 Extended hours stop-limit: stop_size={stop_size}, protection_offset={protection_offset} (for stop), limit_offset={limit_offset} (for limit)")
                else:
                    # Non-ATR stop loss: use bar-based calculation
                    stop_size, entry_offset, protection_offset = _calculate_stop_limit_offsets(hist_data)
                    limit_offset = stop_size * 1.0  # 1x for limit price
                    logging.info(f"Extended hours stop-limit calculation: action={action}, bar high={hist_data.get('high')}, bar low={hist_data.get('low')}, stop_size={stop_size}, protection_offset={protection_offset}, limit_offset={limit_offset}")
            
            if is_manual_stop_order:
                # Manual Stop Order in extended hours - MUST use STP LMT (Stop-Limit Order)
                logging.info(f"Manual Stop Order detected in extended hours: barType={bar_type}, stopLoss={entryData.get('stopLoss')}, action={action}")
                stop_loss_type = entryData.get('stopLoss')
                
                # Check if this is Custom stop loss
                if stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                    # For Custom stop loss: stop_price = custom_stop, limit = custom_stop ± 2 × stop_size
                    # Use stop_size from entryData (stored as calculated_stop_size)
                    manual_stop_size = entryData.get('calculated_stop_size')
                    if manual_stop_size is None or manual_stop_size <= 0:
                        # Recalculate stop_size from custom_stop and filled_price
                        custom_stop = _to_float(entryData.get('slValue', 0), 0)
                        if custom_stop == 0:
                            # Fallback to bar-based if custom value missing
                            manual_stop_size = stop_size
                            logging.warning(f"Manual Stop Order Custom OTH: Custom stop loss value missing, using bar-based stop_size={manual_stop_size}")
                        else:
                            manual_stop_size = abs(float(filled_price) - custom_stop)
                            logging.info(f"Manual Stop Order Custom OTH: Recalculated stop_size={manual_stop_size} from filled_price={filled_price} and custom_stop={custom_stop}")
                    else:
                        logging.info(f"Manual Stop Order Custom OTH: Using stored stop_size={manual_stop_size}")
                    
                    # Stop loss price is the custom value directly
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop == 0:
                        # Fallback: use filled_price ± stop_size
                        if action.upper() == "SELL":
                            stop_loss_price = filled_price - manual_stop_size
                        else:
                            stop_loss_price = filled_price + manual_stop_size
                        logging.warning(f"Manual Stop Order Custom OTH: Custom stop loss value missing, using calculated stop_loss_price={stop_loss_price}")
                    else:
                        stop_loss_price = round(custom_stop, Config.roundVal)
                    
                    # Limit offset = stop_size * 2.0 (for Custom stop loss in extended hours)
                    limit_offset = round(manual_stop_size * 2.0, Config.roundVal)
                    
                    if action.upper() == "SELL":
                        # SELL stop limit: when stop is hit, becomes SELL limit. Limit should be >= stop (above stop)
                        limit_price = round(stop_loss_price + limit_offset, Config.roundVal)
                        logging.info(f"Manual Stop Order Custom OTH: SELL stop loss (LONG position): entry={filled_price}, stop={stop_loss_price} (custom_stop), limit={limit_price} (stop + {limit_offset}), stop_size={manual_stop_size}")
                    else:
                        # BUY stop limit: when stop is hit, becomes BUY limit. Limit should be <= stop (below stop)
                        limit_price = round(stop_loss_price - limit_offset, Config.roundVal)
                        logging.info(f"Manual Stop Order Custom OTH: BUY stop loss (SHORT position): entry={filled_price}, stop={stop_loss_price} (custom_stop), limit={limit_price} (stop - {limit_offset}), stop_size={manual_stop_size}")
                    
                    # Update adjusted_price to use custom stop loss price
                    adjusted_price = round(stop_loss_price, Config.roundVal)
                    order_kwargs['auxPrice'] = adjusted_price
                    # Set orderType and lmtPrice for extended hours stop-limit order
                    order_type = "STP LMT"
                    order_kwargs['orderType'] = "STP LMT"
                    order_kwargs['lmtPrice'] = limit_price
                    logging.info(f"Custom entry OTH Custom stop loss: Set orderType=STP LMT, auxPrice={adjusted_price} (custom), lmtPrice={limit_price}")
                else:
                    # Check if this is HOD/LOD stop loss (same logic as Custom)
                    if stop_loss_type == Config.stopLoss[3] or stop_loss_type == Config.stopLoss[4]:  # HOD or LOD
                        # For HOD/LOD: Use stored stop_size (same as Custom uses stored calculated_stop_size)
                        manual_stop_size = entryData.get('calculated_stop_size')
                        if manual_stop_size is None or manual_stop_size <= 0:
                            # Recalculate stop_size from HOD/LOD and filled_price (same as Custom fallback)
                            # Uses premarket data for premarket, RTH data for after hours
                            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                            
                            if lod is not None and hod is not None:
                                # Determine stop loss price based on action
                                if action.upper() == "SELL":  # LONG position: use LOD
                                    lod_hod_stop_price = round(lod, Config.roundVal)
                                else:  # BUY stop loss (SHORT position): use HOD
                                    lod_hod_stop_price = round(hod, Config.roundVal)
                                
                                # Recalculate stop_size using entry price and LOD/HOD
                                # stop_size = |entry - HOD/LOD|
                                manual_stop_size = abs(float(filled_price) - lod_hod_stop_price)
                                manual_stop_size = round(manual_stop_size, Config.roundVal)
                                
                                # Stop loss price = LOD/HOD (from price parameter, same as Custom uses custom_stop)
                                stop_loss_price = lod_hod_stop_price
                                
                                logging.info(f"Manual Stop Order HOD/LOD OTH: Recalculated stop_size={manual_stop_size} from filled_price={filled_price} and HOD/LOD={lod_hod_stop_price}")
                            else:
                                # Fallback: use bar-based
                                manual_stop_size = stop_size
                                if action.upper() == "SELL":
                                    stop_loss_price = filled_price - manual_stop_size
                                else:
                                    stop_loss_price = filled_price + manual_stop_size
                                logging.warning(f"Manual Stop Order HOD/LOD OTH: LOD/HOD data unavailable, using bar-based stop_size={manual_stop_size}")
                        else:
                            # Use stored stop_size (same as Custom)
                            # Stop loss price: use stored lod_hod_stop_price if available, otherwise use price parameter (same as Custom uses custom_stop)
                            lod_hod_stop_price = entryData.get('lod_hod_stop_price')
                            if lod_hod_stop_price is not None and lod_hod_stop_price > 0:
                                stop_loss_price = round(lod_hod_stop_price, Config.roundVal)
                            else:
                                stop_loss_price = round(adjusted_price, Config.roundVal)  # price parameter is already HOD/LOD
                            logging.info(f"Manual Stop Order HOD/LOD OTH: Using stored stop_size={manual_stop_size}, stop_loss_price={stop_loss_price}")
                        
                        # Limit offset = stop_size * 0.5 (same as entry order uses for Custom entry type)
                        limit_offset = round(manual_stop_size * 0.5, Config.roundVal)
                        
                        if action.upper() == "SELL":
                            # SELL stop limit: when stop is hit, becomes SELL limit. Limit should be >= stop (above stop)
                            limit_price = round(stop_loss_price + limit_offset, Config.roundVal)
                            logging.info(f"Custom entry OTH HOD/LOD: SELL stop loss (LONG position): entry={filled_price}, stop={stop_loss_price} (LOD), limit={limit_price} (stop + {limit_offset}), stop_size={manual_stop_size}")
                        else:
                            # BUY stop limit: when stop is hit, becomes BUY limit. Limit should be <= stop (below stop)
                            limit_price = round(stop_loss_price - limit_offset, Config.roundVal)
                            logging.info(f"Custom entry OTH HOD/LOD: BUY stop loss (SHORT position): entry={filled_price}, stop={stop_loss_price} (HOD), limit={limit_price} (stop - {limit_offset}), stop_size={manual_stop_size}")
                        
                        # Update adjusted_price to use HOD/LOD stop loss price
                        adjusted_price = round(stop_loss_price, Config.roundVal)
                        order_kwargs['auxPrice'] = adjusted_price
                        # Set orderType and lmtPrice for extended hours stop-limit order
                        order_type = "STP LMT"
                        order_kwargs['orderType'] = "STP LMT"
                        order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
                        logging.info(f"Custom entry OTH HOD/LOD: Set orderType=STP LMT, auxPrice={adjusted_price}, lmtPrice={limit_price}")
                    else:
                        # Non-Custom, non-HOD/LOD stop loss (EntryBar, BarByBar): For Custom entry type, stop price = custom value
                        # For Custom entry type in extended hours: stop price = custom value (slValue / entry_points),
                        # then stop_size should be derived from entry vs that custom stop.
                        manual_stop_size = entryData.get('stopSize')
                        if manual_stop_size is None or manual_stop_size <= 0:
                            # Recalculate stop_size the same way as in manual_stop_order (bar-based as initial estimate)
                            if stop_loss_type in Config.atrStopLossMap:
                                manual_stop_size = _get_atr_stop_offset(connection, entryData['contract'], stop_loss_type)
                                if manual_stop_size is None:
                                    manual_stop_size = stop_size  # Fallback to bar-based
                            else:
                                # For non-ATR, try to calculate from entry and stop loss price
                                # Use bar-based calculation as approximation
                                manual_stop_size = stop_size
                            logging.info(f"Manual Stop Order OTH: Recalculated stop_size={manual_stop_size} (was not stored)")
                        else:
                            logging.info(f"Manual Stop Order OTH: Using stored stop_size={manual_stop_size}")
                        
                        # For Custom entry type: stop price should always be the custom value
                        # First try slValue, then try entry_points (custom entry price)
                        custom_stop = _to_float(entryData.get('slValue', 0), 0)
                        if custom_stop == 0:
                            # Fallback: use entry_points (custom entry price) as stop loss price
                            entry_points_value = _to_float(entryData.get('entry_points', 0), 0)
                            if entry_points_value > 0:
                                custom_stop = entry_points_value
                                stop_loss_price = round(custom_stop, Config.roundVal)
                                logging.info(f"Custom entry OTH: Using entry_points={entry_points_value} as stop_loss_price (slValue was 0)")
                            else:
                                # Final fallback: calculate based on stop loss type
                                try:
                                    entry_price_for_sl = filled_price
                                    raw_stop_loss_price, calculated_stop_size = _calculate_manual_stop_loss(
                                        connection, entryData['contract'], entry_price_for_sl, stop_loss_type, 
                                        "BUY" if action.upper() == "SELL" else "SELL",
                                        entryData.get('timeFrame', '1 min'), entryData.get('slValue', 0)
                                    )
                                    if calculated_stop_size and calculated_stop_size > 0:
                                        manual_stop_size = calculated_stop_size
                                    stop_loss_price = round(raw_stop_loss_price, Config.roundVal)
                                    logging.warning(f"Custom entry OTH: Custom stop loss value and entry_points missing, using calculated stop_loss_price={stop_loss_price}")
                                except Exception as e:
                                    logging.error(f"Error calculating stop loss price for Custom entry OTH: {e}")
                                    if action.upper() == "SELL":
                                        stop_loss_price = filled_price - manual_stop_size
                                    else:
                                        stop_loss_price = filled_price + manual_stop_size
                                    stop_loss_price = round(stop_loss_price, Config.roundVal)
                                    logging.warning(f"Custom entry OTH: Using fallback stop_loss_price={stop_loss_price}")
                        else:
                            # Stop loss price is the custom value directly (e.g. user custom entry)
                            stop_loss_price = round(custom_stop, Config.roundVal)
                            logging.info(f"Custom entry OTH (non-Custom/non-ATR stop loss): Using custom stop_loss_price={stop_loss_price} (from slValue/entry_points), stop_size={manual_stop_size}")
                        
                        # For EntryBar stop loss after fill (Custom entry OTH), match RBB+EntryBar behaviour:
                        #   stop_price is offset from custom entry by ± 2 × stop_size.
                        #   - LONG (SELL stop): custom - 2 * stop_size
                        #   - SHORT (BUY stop): custom + 2 * stop_size
                        if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                            base_entry = stop_loss_price  # this is the custom entry price
                            if action.upper() == "SELL":  # LONG position
                                stop_loss_price = round(base_entry - (manual_stop_size * 2.0), Config.roundVal)
                            else:  # BUY stop for SHORT position
                                stop_loss_price = round(base_entry + (manual_stop_size * 2.0), Config.roundVal)
                            logging.info(
                                f"Custom entry OTH EntryBar: base_entry={base_entry}, stop_size={manual_stop_size}, "
                                f"final_stop={stop_loss_price} (custom ± 2×stop_size)"
                            )
                        else:
                            # For non-EntryBar stop loss types, recompute stop_size from entry vs this stop.
                            # This matches the intended behaviour: user-set custom price is the stop, and the distance
                            # between entry and stop defines the stop size used for the limit leg and risk.
                            try:
                                manual_stop_size = abs(float(filled_price) - float(stop_loss_price))
                                manual_stop_size = round(manual_stop_size, Config.roundVal)
                                logging.info(
                                    f"Custom entry OTH: Recalculated manual_stop_size from entry={filled_price}, "
                                    f"stop={stop_loss_price} -> stop_size={manual_stop_size}"
                                )
                            except Exception as e:
                                logging.error(f"Custom entry OTH: Error recalculating stop_size from entry and stop price: {e}")
                                # If recalculation fails for any reason, keep previous manual_stop_size value.
                        
                        # Limit offset = stop_size * 0.5 (same as entry order uses)
                        limit_offset = round(manual_stop_size * 0.5, Config.roundVal)
                        
                        if action.upper() == "SELL":
                            # SELL stop limit: when stop is hit, becomes SELL limit. Limit should be >= stop (above stop)
                            limit_price = round(stop_loss_price + limit_offset, Config.roundVal)
                            logging.info(f"Custom entry OTH: SELL stop loss (LONG position): entry={filled_price}, stop={stop_loss_price}, limit={limit_price} (stop + {limit_offset}), stop_size={manual_stop_size}")
                        else:
                            # BUY stop limit: when stop is hit, becomes BUY limit. Limit should be <= stop (below stop)
                            limit_price = round(stop_loss_price - limit_offset, Config.roundVal)
                            logging.info(f"Custom entry OTH: BUY stop loss (SHORT position): entry={filled_price}, stop={stop_loss_price}, limit={limit_price} (stop - {limit_offset}), stop_size={manual_stop_size}")
                        
                        # Update adjusted_price to use calculated stop loss price
                        adjusted_price = round(stop_loss_price, Config.roundVal)
                        order_kwargs['auxPrice'] = adjusted_price
                        # Set orderType and lmtPrice for extended hours stop-limit order
                        order_type = "STP LMT"
                        order_kwargs['orderType'] = "STP LMT"
                        order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
                        logging.info(f"Custom entry OTH: Set orderType=STP LMT, auxPrice={adjusted_price}, lmtPrice={limit_price}")
            else:
                # Regular trade types (including RB/LB/LB2/LB3, PBe1/PBe2): use existing logic
                bar_type = entryData.get('barType', '')
                
                # PBe1/PBe2: Use stored LOD/HOD for stop price (not entry ± stop_size)
                if bar_type in [Config.entryTradeType[6], Config.entryTradeType[7]]:  # PBe1 or PBe2
                    # Get stored LOD/HOD from orderStatusData
                    order_id = entryData.get('orderId')
                    order_data = Config.orderStatusData.get(order_id) if order_id else None
                    bar_type_name = "PBe1" if bar_type == Config.entryTradeType[6] else "PBe2"
                    
                    if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                        lod = order_data.get('pbe1_lod', 0)
                        hod = order_data.get('pbe1_hod', 0)
                        stored_stop_size = order_data.get('stopSize', 0)
                        
                        # For LONG position (SELL stop loss): use LOD
                        # For SHORT position (BUY stop loss): use HOD
                        if action.upper() == "SELL":  # LONG position
                            stop_loss_price = round(lod, Config.roundVal)
                            # Limit price: LOD + 2 × stop_size (above stop for SELL stop limit)
                            limit_offset = round(stored_stop_size * 2.0, Config.roundVal)
                            limit_price = round(stop_loss_price + limit_offset, Config.roundVal)
                            logging.info(f"{bar_type_name} stop loss (LONG) Extended hours: stop={stop_loss_price} (LOD), limit={limit_price} (LOD + 2×stop_size={limit_offset}), entry={filled_price}, stop_size={stored_stop_size}")
                        else:  # BUY stop loss (SHORT position)
                            stop_loss_price = round(hod, Config.roundVal)
                            # Limit price: HOD - 2 × stop_size (below stop for BUY stop limit)
                            limit_offset = round(stored_stop_size * 2.0, Config.roundVal)
                            limit_price = round(stop_loss_price - limit_offset, Config.roundVal)
                            logging.info(f"{bar_type_name} stop loss (SHORT) Extended hours: stop={stop_loss_price} (HOD), limit={limit_price} (HOD - 2×stop_size={limit_offset}), entry={filled_price}, stop_size={stored_stop_size}")
                        
                        # Update adjusted_price to use LOD/HOD
                        adjusted_price = stop_loss_price
                        order_kwargs['auxPrice'] = adjusted_price
                        # Set orderType and lmtPrice for extended hours stop-limit order
                        order_type = "STP LMT"
                        order_kwargs['orderType'] = "STP LMT"
                        order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
                        logging.info(f"{bar_type_name} stop loss Extended hours: Set orderType=STP LMT, auxPrice={adjusted_price}, lmtPrice={limit_price}")
                    else:
                        # Fallback: recalculate LOD/HOD
                        logging.warning(f"{bar_type_name}: LOD/HOD not found in orderStatusData, recalculating")
                        lod, hod = _get_pbe1_lod_hod(connection, entryData['contract'], entryData.get('timeFrame', '1 min'), action)
                        if action.upper() == "SELL":  # LONG position
                            stop_loss_price = round(lod, Config.roundVal)
                            stored_stop_size = abs(float(filled_price) - lod)
                        else:  # BUY stop loss (SHORT position)
                            stop_loss_price = round(hod, Config.roundVal)
                            stored_stop_size = abs(float(filled_price) - hod)
                        
                        limit_offset = round(stored_stop_size * 2.0, Config.roundVal)
                        if action.upper() == "SELL":
                            # SELL stop limit: limit should be >= stop (above stop)
                            limit_price = round(stop_loss_price + limit_offset, Config.roundVal)
                        else:
                            # BUY stop limit: limit should be <= stop (below stop)
                            limit_price = round(stop_loss_price - limit_offset, Config.roundVal)
                        
                        adjusted_price = stop_loss_price
                        order_kwargs['auxPrice'] = adjusted_price
                        # Set orderType and lmtPrice for extended hours stop-limit order
                        order_type = "STP LMT"
                        order_kwargs['orderType'] = "STP LMT"
                        order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
                        logging.info(f"{bar_type_name} stop loss (recalculated) Extended hours: Set orderType=STP LMT, auxPrice={adjusted_price}, lmtPrice={limit_price}, entry={filled_price}, stop_size={stored_stop_size}")
                
                # Conditional Order, RB, RBB, LB, LB2, LB3: use existing logic
                elif bar_type in [Config.entryTradeType[2], Config.entryTradeType[4], Config.entryTradeType[5], Config.entryTradeType[8], Config.entryTradeType[9], Config.entryTradeType[10]]:  # Conditional Order, RB, RBB, LB, LB2, LB3 (excluding PBe1/PBe2)
                    if calculated_stop_size is not None:
                        # Check if this is LOD/HOD stop loss (same logic as Custom)
                        lod_hod_stop_price = entryData.get('lod_hod_stop_price')
                        if lod_hod_stop_price is not None and lod_hod_stop_price > 0:
                            # For LOD/HOD: Use stored stop_size (same as Custom uses stored calculated_stop_size)
                            # stop_size is already stored in calculated_stop_size from sendTpSlBuy/sendTpSlSell
                            stop_size = calculated_stop_size
                            # Limit price = HOD/LOD ± 2 × stop_size
                            limit_offset = round(stop_size * 2.0, Config.roundVal)
                            
                            # Stop loss price is passed as 'price' parameter (entry bar high/low)
                            # lod_hod_stop_price contains HOD/LOD for limit calculation only
                            stop_loss_price = round(price, Config.roundVal)
                            
                            if action.upper() == "BUY":
                                # BUY stop limit: when stop is hit, becomes BUY limit. Limit should be <= stop (below stop)
                                # lod_hod_stop_price contains HOD for limit calculation
                                limit_price = round(lod_hod_stop_price - limit_offset, Config.roundVal)
                                logging.info(f"RB/LB/LB2/LB3 HOD/LOD BUY stop loss (SHORT) Extended hours: stop={stop_loss_price} (Entry bar High), limit={limit_price} (HOD - 2×stop_size={limit_offset}), HOD={lod_hod_stop_price}, stop_size={stop_size}, entry={filled_price}")
                            else:
                                # SELL stop limit: when stop is hit, becomes SELL limit. Limit should be >= stop (above stop)
                                # lod_hod_stop_price contains LOD for limit calculation
                                limit_price = round(lod_hod_stop_price + limit_offset, Config.roundVal)
                                logging.info(f"RB/LB/LB2/LB3 HOD/LOD SELL stop loss (LONG) Extended hours: stop={stop_loss_price} (Entry bar Low - 0.01), limit={limit_price} (LOD + 2×stop_size={limit_offset}), LOD={lod_hod_stop_price}, stop_size={stop_size}, entry={filled_price}")
                            
                            # Update adjusted_price to use the actual stop loss price
                            adjusted_price = stop_loss_price
                            order_kwargs['auxPrice'] = adjusted_price
                            
                            # Update adjusted_price to use LOD/HOD stop price (same as Custom uses custom_stop)
                            adjusted_price = round(lod_hod_stop_price, Config.roundVal)
                            order_kwargs['auxPrice'] = adjusted_price
                        else:
                            # For RB/LB/LB2/LB3/RBB in extended hours (non-LOD/HOD):
                            # Check if this is Custom stop loss
                            stop_loss_type = entryData.get('stopLoss')
                            if stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                                # For Custom stop loss: stop_price = custom_stop, limit = custom_stop ± 2 × stop_size
                                custom_stop = _to_float(entryData.get('slValue', 0), 0)
                                if custom_stop == 0:
                                    # Fallback: use entry ± stop_size
                                    stop_size = calculated_stop_size if calculated_stop_size is not None else stop_size
                                    if action.upper() == "BUY":
                                        stop_loss_price = filled_price + stop_size
                                        limit_price = filled_price + (stop_size * 2.0)
                                    else:
                                        stop_loss_price = filled_price - stop_size
                                        limit_price = filled_price - (stop_size * 2.0)
                                    logging.warning(f"RB/LB/LB2/LB3/RBB Custom OTH: Custom stop loss value missing, using calculated stop_loss_price={stop_loss_price}")
                                else:
                                    # Stop loss price is the custom value directly (should NOT update)
                                    stop_loss_price = round(custom_stop, Config.roundVal)
                                    
                                    # Use stored stop_size or recalculate
                                    if calculated_stop_size is not None and calculated_stop_size > 0:
                                        custom_stop_size = calculated_stop_size
                                    else:
                                        # For RBB: Use entry_stop_price (not filled_price) and bar high/low for stop_size calculation
                                        # For other trade types: Use filled_price
                                        entry_price = entryData.get('lastPrice', filled_price)  # Use entry_stop_price (lastPrice)
                                        bar_price = None  # Initialize for logging
                                        
                                        if bar_type == Config.entryTradeType[5]:  # RBB
                                            # For RBB with Custom stop loss: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|
                                            hist_data = entryData.get('histData', {})
                                            if hist_data and isinstance(hist_data, dict):
                                                # Determine bar price based on action
                                                # BUY action = SHORT position, so use bar_low
                                                # SELL action = LONG position, so use bar_high
                                                if action.upper() == "BUY":  # SHORT position
                                                    bar_price = float(hist_data.get('low', entry_price))
                                                else:  # SELL action = LONG position
                                                    bar_price = float(hist_data.get('high', entry_price))
                                                custom_stop_size = abs(bar_price - custom_stop)
                                            else:
                                                # Fallback: use entry_price if histData not available
                                                custom_stop_size = abs(float(entry_price) - custom_stop)
                                        else:
                                            # For other trade types: use entry_price with +0.02
                                            custom_stop_size = abs(float(entry_price) - custom_stop) + 0.02
                                        
                                        custom_stop_size = round(custom_stop_size, Config.roundVal)
                                        logging.info(f"RB/LB/LB2/LB3/RBB Custom OTH: Recalculated stop_size={custom_stop_size} from entry_price={entry_price} (lastPrice), bar_price={bar_price if bar_price is not None else 'N/A'}, custom_stop={custom_stop}, barType={bar_type}")
                                    
                                    # Limit offset = 2 × stop_size
                                    limit_offset = round(custom_stop_size * 2.0, Config.roundVal)
                                    
                                    if action.upper() == "BUY":
                                        # BUY stop limit: when stop is hit, becomes BUY limit. Limit should be <= stop (below stop)
                                        limit_price = round(stop_loss_price - limit_offset, Config.roundVal)
                                        entry_price_for_log = entryData.get('lastPrice', filled_price)  # Use entry_price for logging
                                        logging.info(f"RB/LB/LB2/LB3/RBB Custom OTH: BUY stop loss (SHORT): entry={entry_price_for_log}, stop={stop_loss_price} (custom_stop, FIXED), limit={limit_price} (stop - {limit_offset}), stop_size={custom_stop_size}")
                                    else:
                                        # SELL stop limit: when stop is hit, becomes SELL limit. Limit should be >= stop (above stop)
                                        limit_price = round(stop_loss_price + limit_offset, Config.roundVal)
                                        entry_price_for_log = entryData.get('lastPrice', filled_price)  # Use entry_price for logging
                                        logging.info(f"RB/LB/LB2/LB3/RBB Custom OTH: SELL stop loss (LONG): entry={entry_price_for_log}, stop={stop_loss_price} (custom_stop, FIXED), limit={limit_price} (stop + {limit_offset}), stop_size={custom_stop_size}")
                                
                                # Update adjusted_price to use custom stop loss price
                                adjusted_price = round(stop_loss_price, Config.roundVal)
                                order_kwargs['auxPrice'] = adjusted_price
                                order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
                            else:
                                # Non-Custom stop loss: Stop price: entry_price ± stop_size (already in adjusted_price)
                                # Limit price: entry_price ± 2 × stop_size
                                stop_size = calculated_stop_size if calculated_stop_size is not None else stop_size
                                limit_offset = round(stop_size * 2.0, Config.roundVal)
                                if action.upper() == "BUY":
                                    # SHORT position: stop = entry + stop_size, limit = entry + 2 × stop_size
                                    limit_price = filled_price + limit_offset
                                    logging.info(f"RB/LB/LB2/LB3/RBB BUY stop loss (SHORT): stop={adjusted_price} (entry + stop_size), limit={limit_price} (entry + 2×stop_size={limit_offset}), entry={filled_price}")
                                else:
                                    # LONG position: stop = entry - stop_size, limit = entry - 2 × stop_size
                                    limit_price = filled_price - limit_offset
                                    logging.info(f"RB/LB/LB2/LB3/RBB SELL stop loss (LONG): stop={adjusted_price} (entry - stop_size), limit={limit_price} (entry - 2×stop_size={limit_offset}), entry={filled_price}")
                                order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
                else:
                    # Check if this is Limit Order
                    if bar_type == 'Limit Order':
                        stop_loss_type = entryData.get('stopLoss')
                        limit_order_stop_size = entryData.get('stopSize')
                        
                        # Check if this is Custom stop loss
                        if stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                            # For Custom stop loss: stop_price = custom_stop, limit = custom_stop ± 2 × stop_size
                            custom_stop = _to_float(entryData.get('slValue', 0), 0)
                            if custom_stop == 0:
                                # Fallback: use filled_price ± stop_size
                                if limit_order_stop_size is not None and limit_order_stop_size > 0:
                                    if action.upper() == "SELL":
                                        stop_loss_price = filled_price - limit_order_stop_size
                                    else:
                                        stop_loss_price = filled_price + limit_order_stop_size
                                    limit_offset = limit_order_stop_size * 2.0
                                    limit_price = stop_loss_price - limit_offset if action.upper() == "SELL" else stop_loss_price + limit_offset
                                    logging.warning(f"Limit Order Custom OTH: Custom stop loss value missing, using calculated stop_loss_price={stop_loss_price}")
                                else:
                                    # Use bar-based calculation as fallback
                                    stop_loss_price = adjusted_price
                                    limit_offset = stop_size * 2.0
                                    limit_price = adjusted_price - limit_offset if action.upper() == "SELL" else adjusted_price + limit_offset
                            else:
                                # Stop loss price is the custom value directly
                                stop_loss_price = round(custom_stop, Config.roundVal)
                                
                                # Use stored stop_size or recalculate
                                if limit_order_stop_size is not None and limit_order_stop_size > 0:
                                    manual_stop_size = limit_order_stop_size
                                else:
                                    # Recalculate stop_size from custom_stop and filled_price
                                    manual_stop_size = abs(float(filled_price) - custom_stop)
                                    logging.info(f"Limit Order Custom OTH: Recalculated stop_size={manual_stop_size} from filled_price={filled_price} and custom_stop={custom_stop}")
                                
                                # Limit offset = 2 × stop_size
                                limit_offset = round(manual_stop_size * 2.0, Config.roundVal)
                                
                                if action.upper() == "SELL":
                                    # SELL stop limit: when stop is hit, becomes SELL limit. Limit should be >= stop (above stop)
                                    limit_price = stop_loss_price + limit_offset
                                    logging.info(f"Limit Order Custom OTH: SELL stop loss (LONG position): entry={filled_price}, stop={stop_loss_price} (custom_stop), limit={limit_price} (stop + {limit_offset}), stop_size={manual_stop_size}")
                                else:
                                    # BUY stop limit: when stop is hit, becomes BUY limit. Limit should be <= stop (below stop)
                                    limit_price = stop_loss_price - limit_offset
                                    logging.info(f"Limit Order Custom OTH: BUY stop loss (SHORT position): entry={filled_price}, stop={stop_loss_price} (custom_stop), limit={limit_price} (stop - {limit_offset}), stop_size={manual_stop_size}")
                                
                                # Update adjusted_price to use custom stop loss price
                                adjusted_price = round(stop_loss_price, Config.roundVal)
                                order_kwargs['auxPrice'] = adjusted_price
                        else:
                            # Check if this is HOD/LOD stop loss (same logic as Custom)
                            if stop_loss_type == Config.stopLoss[3] or stop_loss_type == Config.stopLoss[4]:  # HOD or LOD
                                # For HOD/LOD: Use stored stop_size (same as Custom uses stored stopSize)
                                # Stop loss price is from price parameter (HOD/LOD), same as Custom uses custom_stop
                                stop_loss_price = round(adjusted_price, Config.roundVal)  # price parameter is already HOD/LOD
                                
                                # Use stored stop_size or recalculate
                                if limit_order_stop_size is not None and limit_order_stop_size > 0:
                                    manual_stop_size = limit_order_stop_size
                                    logging.info(f"Limit Order HOD/LOD OTH: Using stored stop_size={manual_stop_size}")
                                else:
                                    # Recalculate stop_size from HOD/LOD and filled_price (same as Custom fallback)
                                    # Uses premarket data for premarket, RTH data for after hours
                                    lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                                    
                                    if lod is not None and hod is not None:
                                        # Determine stop loss price based on action
                                        if action.upper() == "SELL":  # LONG position: use LOD
                                            lod_hod_stop_price = round(lod, Config.roundVal)
                                        else:  # BUY stop loss (SHORT position): use HOD
                                            lod_hod_stop_price = round(hod, Config.roundVal)
                                        
                                        # Recalculate stop_size using entry price and LOD/HOD
                                        # stop_size = |entry - HOD/LOD|
                                        manual_stop_size = abs(float(filled_price) - lod_hod_stop_price)
                                        manual_stop_size = round(manual_stop_size, Config.roundVal)
                                        
                                        # Stop loss price = LOD/HOD
                                        stop_loss_price = lod_hod_stop_price
                                        
                                        logging.info(f"Limit Order HOD/LOD OTH: Recalculated stop_size={manual_stop_size} from filled_price={filled_price} and HOD/LOD={lod_hod_stop_price}")
                                    else:
                                        # Fallback: use bar-based
                                        manual_stop_size = stop_size
                                        logging.warning(f"Limit Order HOD/LOD OTH: LOD/HOD data unavailable, using bar-based stop_size={manual_stop_size}")
                                
                                # Limit price = stop ± 2 × stop_size (same as Custom)
                                limit_offset = round(manual_stop_size * 2.0, Config.roundVal)
                                if action.upper() == "SELL":
                                    # SELL stop limit: when stop is hit, becomes SELL limit. Limit should be >= stop (above stop)
                                    limit_price = round(stop_loss_price + limit_offset, Config.roundVal)
                                    logging.info(f"Limit Order HOD/LOD SELL stop loss (LONG) Extended hours: entry={filled_price}, stop={stop_loss_price} (LOD), limit={limit_price} (stop + {limit_offset}), stop_size={manual_stop_size}")
                                else:
                                    # BUY stop limit: when stop is hit, becomes BUY limit. Limit should be <= stop (below stop)
                                    limit_price = round(stop_loss_price - limit_offset, Config.roundVal)
                                    logging.info(f"Limit Order HOD/LOD BUY stop loss (SHORT) Extended hours: entry={filled_price}, stop={stop_loss_price} (HOD), limit={limit_price} (stop - {limit_offset}), stop_size={manual_stop_size}")
                                
                                # Update adjusted_price to use LOD/HOD stop price (same as Custom uses custom_stop)
                                adjusted_price = round(stop_loss_price, Config.roundVal)
                                order_kwargs['auxPrice'] = adjusted_price
                            else:
                                # Limit Order with non-Custom, non-HOD/LOD stop loss: use original logic (unchanged)
                                # Check if stored stop_size exists (for ATR or other types that store it)
                                if limit_order_stop_size is not None and limit_order_stop_size > 0:
                                    # Limit Order in extended hours: stop = entry ± stop_size, limit = entry ± 2 × stop_size
                                    limit_offset = limit_order_stop_size * 2.0
                                    if action.upper() == "BUY":
                                        # SHORT position: stop = entry + stop_size, limit = entry + 2 × stop_size
                                        limit_price = filled_price + limit_offset
                                        logging.info(f"Limit Order BUY stop loss (SHORT): stop={adjusted_price} (entry + stop_size), limit={limit_price} (entry + 2×stop_size={limit_offset}), entry={filled_price}")
                                    else:
                                        # LONG position: stop = entry - stop_size, limit = entry - 2 × stop_size
                                        limit_price = filled_price - limit_offset
                                        logging.info(f"Limit Order SELL stop loss (LONG): stop={adjusted_price} (entry - stop_size), limit={limit_price} (entry - 2×stop_size={limit_offset}), entry={filled_price}")
                                else:
                                    # Limit Order without stored stop_size: fall through to original logic below
                                    pass
                    else:
                        # Other trade types: use adjusted_price ± protection_offset
                        if action.upper() == "BUY":
                            limit_price = adjusted_price + protection_offset
                            logging.info(f"BUY stop loss (SHORT position): stop={adjusted_price}, limit={limit_price} (stop + {protection_offset})")
                        else:
                            limit_price = adjusted_price - protection_offset
                            logging.info(f"SELL stop loss (LONG position): stop={adjusted_price}, limit={limit_price} (stop - {protection_offset})")

            # For extended hours, use STP LMT (Stop-Limit Order) for stop loss
            # For RTH, use simple STP order (no limit price)
            if is_extended:
                order_type = "STP LMT"
                order_kwargs['orderType'] = "STP LMT"
                order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
                logging.info(
                    "Extended hours protection stop-limit: action=%s stop=%s limit=%s stop_size=%s protection_offset=%s session=%s barType=%s",
                    action, adjusted_price, order_kwargs['lmtPrice'], stop_size, protection_offset, session, entryData.get('barType', '')
                )
            else:
                # RTH: Use simple STP order (no limit price)
                order_type = "STP"
                order_kwargs['orderType'] = "STP"
                # Remove lmtPrice if it exists (shouldn't be there for RTH, but clean up just in case)
                if 'lmtPrice' in order_kwargs:
                    del order_kwargs['lmtPrice']
                logging.info(
                    "RTH stop order: action=%s stop=%s stop_size=%s barType=%s",
                    action, adjusted_price, stop_size, entryData.get('barType', '')
                )
        elif is_extended:
            # Extended hours but hist_data not available - use fallback calculation
            filled_price = Config.orderFilledPrice.get(entryData['orderId'])
            if filled_price is None:
                filled_price = entryData.get('lastPrice', adjusted_price)
            
            # Use default stop size (1% of filled price) if not already calculated
            if 'stop_size' not in locals():
                default_stop_size = filled_price * 0.01
                stop_size = default_stop_size
                protection_offset = default_stop_size * 2.0
                limit_offset = default_stop_size * 1.0
            
            # Calculate stop loss and limit prices
            if action.upper() == "SELL":
                # SELL stop loss means LONG position, so stop loss is below entry
                stop_loss_price = filled_price - protection_offset
                limit_price = filled_price - limit_offset
            else:
                # BUY stop loss means SHORT position, so stop loss is above entry
                stop_loss_price = filled_price + protection_offset
                limit_price = filled_price + limit_offset
            
            adjusted_price = round(stop_loss_price, Config.roundVal)
            order_kwargs['auxPrice'] = adjusted_price
            order_type = "STP LMT"
            order_kwargs['orderType'] = "STP LMT"
            order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
            logging.info(
                "Extended hours fallback stop-limit: action=%s stop=%s limit=%s stop_size=%s protection_offset=%s session=%s",
                action, adjusted_price, order_kwargs['lmtPrice'], stop_size, protection_offset, session
            )

        # Ensure auxPrice is set before placing order (critical for regular hours)
        if 'auxPrice' not in order_kwargs or order_kwargs['auxPrice'] is None or order_kwargs['auxPrice'] <= 0:
            logging.error(f"sendStopLoss: auxPrice is missing or invalid! auxPrice={order_kwargs.get('auxPrice')}, adjusted_price={adjusted_price}, price={price}, barType={bar_type}, action={action}, is_extended={is_extended}")
            # Use adjusted_price as fallback
            if adjusted_price > 0:
                order_kwargs['auxPrice'] = adjusted_price
                logging.warning(f"sendStopLoss: Using adjusted_price={adjusted_price} as fallback for auxPrice")
            else:
                logging.error(f"sendStopLoss: Cannot place order - both auxPrice and adjusted_price are invalid!")
                raise ValueError(f"Invalid stop price: auxPrice={order_kwargs.get('auxPrice')}, adjusted_price={adjusted_price}")
        
        logging.info(f"sendStopLoss: Placing order - orderType={order_type}, action={action}, auxPrice={order_kwargs['auxPrice']}, totalQuantity={order_kwargs['totalQuantity']}, barType={bar_type}, is_extended={is_extended}, session={session}")
        if 'lmtPrice' in order_kwargs:
            logging.info(f"sendStopLoss: Extended hours - lmtPrice={order_kwargs['lmtPrice']}")
        else:
            logging.info(f"sendStopLoss: RTH - Using simple STP order (no limit price)")

        lmtResponse = connection.placeTrade(contract=entryData['contract'],
                                            order=Order(**order_kwargs), outsideRth=entryData['outsideRth'] )
        StatusUpdate(lmtResponse, 'StopLoss', entryData['contract'], order_type, action, entryData['totalQuantity'], entryData['histData'], adjusted_price, entryData['usersymbol'], entryData['timeFrame'], entryData['profit'], entryData['stopLoss'], entryData['risk'],entryData,'','','','',entryData['slValue'],entryData['breakEven'],entryData['outsideRth'] )

        print(lmtResponse)
        # Only start stopLossThread for Custom stop loss if it's NOT RBB
        # RBB with Custom stop loss should have a FIXED stop loss price (custom_stop) that does NOT update
        if(entryData['stopLoss'] == Config.stopLoss[1] and entryData.get('barType') != Config.entryTradeType[5]):
            loop = asyncio.get_event_loop()
            asyncio.ensure_future(stopLossThread(connection, entryData,adjusted_price,action,lmtResponse.order.orderId))
            logging.info(f"sendStopLoss: Started stopLossThread for Custom stop loss (barType={entryData.get('barType')}, NOT RBB)")
        elif entryData['stopLoss'] == Config.stopLoss[1] and entryData.get('barType') == Config.entryTradeType[5]:
            logging.info(f"sendStopLoss: NOT starting stopLossThread for RBB with Custom stop loss - stop loss price is FIXED at custom_stop and should NOT update")
    except Exception as e:
        logging.error("error in sending StopLoss %s ", e)
        print(e)


def sendTakeProfit(connection, entryData,price,action):
    try:
        # Take Profit is ALWAYS a LIMIT order (LMT), regardless of session (regular or extended hours)
        is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
        logging.info(f"Sending Take Profit: barType={entryData.get('barType', '')}, action={action}, price={price}, session={session}, is_extended={is_extended}")
        
        lmtResponse = connection.placeTrade(contract=entryData['contract'],
                                            order=Order(orderType="LMT", action=action,
                                                        totalQuantity=entryData['totalQuantity'], lmtPrice=price, tif=entryData['tif'], ocaGroup="tp" + str(entryData['orderId']), ocaType=1)  ,outsideRth = entryData['outsideRth'] )
        StatusUpdate(lmtResponse, 'TakeProfit', entryData['contract'], 'LMT', action, entryData['totalQuantity'], entryData['histData'], price, entryData['usersymbol'], entryData['timeFrame'], entryData['profit'], entryData['stopLoss'], entryData['risk'],entryData,'','','','',entryData['slValue'],entryData['breakEven'],entryData['outsideRth'] )
        if(entryData['profit'] == Config.takeProfit[4]):
            loop = asyncio.get_event_loop()
            asyncio.ensure_future(takeProfitThread(connection, entryData,price,action,lmtResponse.order.orderId))
    except Exception as e:
        logging.error("error in sending Take Profit %s ", e)
        print(e)
def place_position_close_order(contract,order,connection):
    connection.placeTrade(contract=contract, order=order)

async def pnl_check(connection):
        await asyncio.sleep(5)
        asyncio.ensure_future(breakEvenCheck((connection)))
        while True:
            try:
                break
                # logging.info(f"current pnl {Config.currentPnl}")
                # if Config.defaultValue.get("pnl") != None and Config.defaultValue.get("pnl") != "" and Config.currentPnl != 0 and float(Config.defaultValue.get("pnl")) <= Config.currentPnl:
                #     positions = connection.getAllOpenPosition()
                #     logging.info(f"closing all position bcz current pnl is {Config.currentPnl} user pnl {Config.defaultValue.get('pnl')}")
                #     logging.info(f"position found {positions}")
                #     for pos in positions:
                #         if pos.position == 0:
                #             continue
                #         contract = pos.contract
                #         action = 'BUY'
                #         if pos.position > 0:
                #             action = 'SELL'
                #         quan = abs(pos.position)
                #         logging.info("placed done.....")
                #         c = getContract(pos.contract.symbol,pos.contract.currency)
                #         o = Order(orderType="MKT", action=action, totalQuantity=int(quan))
                #         place_position_close_order(c,o,connection)
                #
                #     await asyncio.sleep(60)
            except Exception as e:
                logging.error(f"error in pnl {traceback.format_exc()}")
            await asyncio.sleep(1)

async def takeProfitThread(connection, entryData,price,action,orderId):
    try:
        logging.info("take profit Bar By Bar thread is running %s ",entryData)
        lmtData = Config.orderStatusData.get(orderId)

        currentTime = datetime.datetime.now()
        minuteInterval = getTimeInterval(lmtData['timeFrame'],currentTime)
        chartTime = ((currentTime + datetime.timedelta(seconds=minuteInterval))- datetime.timedelta(seconds=Config.timeDict.get((lmtData['timeFrame']))))
        sleepTime = ((minuteInterval) + 1)
        logging.info("Thread is going to sleep %s  in second and timeframe is %s", sleepTime, lmtData['timeFrame'])
        await asyncio.sleep(sleepTime)
        logging.info("take profit status after sleep %s", lmtData['status'])
        nextSleepTime = Config.timeDict.get(lmtData['timeFrame'])
        while(lmtData != None and (lmtData['status'] != 'Filled' and lmtData['status'] != 'Cancelled' and lmtData['status'] != 'Inactive')):
            logging.info("running  take profit in while loop, status is %s",lmtData['status'])
            histData = connection.getHistoricalChartData(lmtData['contract'], lmtData['timeFrame'],chartTime)
            logging.info("hist data for %s contract id, hist data is { %s }  and for %s time", lmtData['contract'], histData, chartTime)
            if (histData is None or len(histData) == 0):
                nextSleepTime = nextSleepTime - 1
                if(nextSleepTime == 0):
                    nextSleepTime = Config.timeDict.get(lmtData['timeFrame'])
                await asyncio.sleep(1)
                continue
            if lmtData['action'] == "BUY":
                price = float(histData['low'])
            else:
                price = float(histData['high'])
            price = round(price, Config.roundVal)
            logging.info("updating barBybar take profit entrydata is %s, price is %s and order id is %s",entryData,price,orderId)
            order_data = None
            orders = connection.getAllOpenOrder()
            for ord in orders:
                if ord.order.orderId == orderId:
                    logging.info(f"old order found for takeprofitThread updation {ord.order}")
                    order_data = ord
                    break
            order_data.order.lmtPrice = price
            # Get outsideRth from order data or default to False
            outsideRth = getattr(order_data.order, 'outsideRth', False)
            res = connection.placeTrade(contract=order_data.contract, order=order_data.order, outsideRth=outsideRth)
            logging.info(f"response of takeprofit updation {res}")

            # updateTakeProfit(connection, entryData, price, lmtData['action'],orderId)
            logging.info("BarByBar takeprofit thread is sleeping for %s time in second ",Config.timeDict.get(lmtData['timeFrame']))
            chartTime = (chartTime + datetime.timedelta(seconds=Config.timeDict.get((lmtData['timeFrame']))))
            logging.info("barByBar take profit new chart data time %s ",chartTime)
            await asyncio.sleep(nextSleepTime)
            lmtData = Config.orderStatusData.get(orderId)
        logging.info("take profit BarByBar thread end")
    except Exception as e:
        logging.error("error in take profit thread %s ", e)
        print(e)


def updateTakeProfit(connection, entryData,price,action,orderId):
    try:
        logging.info("update BarByBar take profit with new price %s",price)
        lmtResponse = connection.placeTrade(contract=entryData['contract'],
                                            order=Order(orderType="LMT", action=action,
                                                        totalQuantity=entryData['totalQuantity'], lmtPrice=price, ocaGroup="tp" + str(entryData['orderId']), ocaType=1, orderId=orderId) ,outsideRth = entryData['outsideRth'] )
        StatusUpdate(lmtResponse, 'TakeProfit', entryData['contract'], 'LMT', action, entryData['totalQuantity'], entryData['histData'], price, entryData['usersymbol'], entryData['timeFrame'], entryData['profit'], entryData['stopLoss'], entryData['risk'],entryData,'','','','',entryData['slValue'],entryData['breakEven'],entryData['outsideRth'] )
    except Exception as e:
        logging.error("error in updating take profit %s ", e)
        print(e)


async def sendTpSlSell(connection, entryData):
    try:
        logging.info("sendTpSlSell called for barType=%s, stopLoss=%s, orderId=%s", 
                     entryData.get('barType'), entryData.get('stopLoss'), entryData.get('orderId'))
        #  if entry buy
        max_retries = 5  # Limit retries to avoid infinite loop
        retry_count = 0
        while True:
            histData = None
            # Check if this is a manual order (Stop Order or Limit Order)
            is_manual_order = entryData.get('barType', '') in Config.manualOrderTypes
            
            if entryData['barType'] == Config.entryTradeType[0] or entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[2] or entryData['barType'] == Config.entryTradeType[3] or entryData['barType'] == Config.entryTradeType[4] or entryData['barType'] == Config.entryTradeType[5] or entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7]:
                histData = entryData['histData']
            elif is_manual_order:
                # For manual orders, try to use stored histData first, otherwise create fallback immediately
                histData = entryData.get('histData')
                if not histData or not isinstance(histData, dict) or 'high' not in histData:
                    # Create fallback histData from filled price immediately (no retries needed)
                    filled_price = Config.orderFilledPrice.get(entryData['orderId'])
                    if filled_price is None:
                        filled_price = entryData.get('lastPrice', 0)
                    if filled_price > 0:
                        # Create a minimal histData with 1% range around filled price
                        histData = {
                            'high': filled_price * 1.005,
                            'low': filled_price * 0.995,
                            'close': filled_price
                        }
                        logging.info("Manual Order: Using fallback histData from filled price: %s", histData)
                    else:
                        logging.error("Manual Order: Cannot create fallback histData: filled_price is invalid")
                        # Don't break - continue with None histData, the custom stop loss logic doesn't need it
                else:
                    logging.info("Manual Order: Using stored histData: %s", histData)
            else:
                chartTime = getRecentChartTime(entryData['timeFrame'])
                histData = connection.getHistoricalChartData(entryData['contract'], entryData['timeFrame'], chartTime)
                if (histData is None or len(histData) == 0):
                    retry_count += 1
                    if retry_count >= max_retries:
                        logging.warning("Chart Data is Not Coming after %s retries for %s contract, using fallback calculation", max_retries, entryData['contract'])
                        # Use fallback: create a minimal histData from filled price
                        filled_price = Config.orderFilledPrice.get(entryData['orderId'])
                        if filled_price is None:
                            filled_price = entryData.get('lastPrice', 0)
                        if filled_price > 0:
                            # Create a minimal histData with 1% range around filled price
                            histData = {
                                'high': filled_price * 1.005,
                                'low': filled_price * 0.995,
                                'close': filled_price
                            }
                            logging.info("Using fallback histData for premarket: %s", histData)
                        else:
                            logging.error("Cannot create fallback histData: filled_price is invalid")
                            break
                    else:
                        logging.info("In TPSL SELL Chart Data is Not Comming for %s contract  and for %s time (retry %s/%s)", entryData['contract'], chartTime, retry_count, max_retries)
                    await asyncio.sleep(1)
                    continue
                # histData = entryData['histData']
            price = 0
            filled_price = Config.orderFilledPrice.get(entryData['orderId'])
            if filled_price is None:
                filled_price = entryData.get('lastPrice', 0)
            if filled_price is None or filled_price == 0:
                # Also check entryData for avgFillPrice or fillPrice
                filled_price = entryData.get('avgFillPrice', 0)
                if filled_price == 0:
                    filled_price = entryData.get('fillPrice', 0)
            if filled_price == 0 or filled_price is None:
                logging.warning("Manual Order: filled_price is 0 or None, waiting for fill price. OrderId=%s, retry=%s/%s, orderFilledPrice keys=%s", 
                             entryData.get('orderId'), retry_count, max_retries, list(Config.orderFilledPrice.keys()))
                if retry_count >= max_retries:
                    logging.error("Manual Order: Max retries reached, cannot proceed without filled_price. Using entry_stop_price as fallback.")
                    # Use entry_stop_price as fallback for custom stop loss
                    if entryData.get('barType') in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:
                        filled_price = entryData.get('lastPrice', entryData.get('entryPrice', 0))
                        logging.warning("Manual Order Custom: Using entry_stop_price=%s as filled_price fallback", filled_price)
                        if filled_price == 0:
                            logging.error("Manual Order Custom: Cannot proceed - no valid price available")
                            break
                    else:
                        logging.error("Manual Order: Cannot proceed without filled_price and no fallback available")
                        break
                else:
                    retry_count += 1
                    await asyncio.sleep(0.5)
                    continue
            
            # Use entry stop price (lastPrice) instead of filled price for take profit calculation
            # For Stop Order: entry_stop_price should be the original auxPrice (stop trigger price), not the filled price
            entry_stop_price = entryData.get('lastPrice', filled_price)
            if entry_stop_price is None or entry_stop_price == 0:
                entry_stop_price = filled_price
            
            # For Stop Order: Ensure entry_stop_price is the original entry stop price (auxPrice), not filled price
            if entryData.get('barType') == Config.entryTradeType[0]:  # Stop Order
                # Try to get the original auxPrice from the order
                order_aux_price = None
                if 'order' in entryData and hasattr(entryData['order'], 'auxPrice'):
                    order_aux_price = entryData['order'].auxPrice
                elif 'auxPrice' in entryData:
                    order_aux_price = entryData['auxPrice']
                
                # If we have the original auxPrice, use it instead of filled_price
                if order_aux_price is not None and order_aux_price > 0:
                    entry_stop_price = float(order_aux_price)
                    logging.info("Stop Order: Using original auxPrice=%s as entry_stop_price (filled_price=%s)", 
                               entry_stop_price, filled_price)
                elif entry_stop_price == filled_price:
                    # If entry_stop_price equals filled_price, try to get it from entry_points
                    entry_points = entryData.get('entry_points', '0')
                    try:
                        entry_stop_price = float(entry_points)
                        if entry_stop_price > 0:
                            logging.info("Stop Order: Using entry_points=%s as entry_stop_price (filled_price=%s)", 
                                       entry_stop_price, filled_price)
                    except (ValueError, TypeError):
                        logging.warning("Stop Order: Could not get original entry stop price, using filled_price as fallback")
            
            # For PBe1/PBe2: Ensure entry_stop_price is the original entry stop price (bar_high/low ± 0.01), not filled price
            if entryData.get('barType') == Config.entryTradeType[6] or entryData.get('barType') == Config.entryTradeType[7]:  # PBe1 or PBe2
                # Try to get the original auxPrice from the order (entry stop price)
                order_aux_price = None
                if 'order' in entryData and hasattr(entryData['order'], 'auxPrice'):
                    order_aux_price = entryData['order'].auxPrice
                elif 'auxPrice' in entryData:
                    order_aux_price = entryData['auxPrice']
                
                # If we have the original auxPrice, use it instead of filled_price
                if order_aux_price is not None and order_aux_price > 0:
                    entry_stop_price = float(order_aux_price)
                    logging.info("PBe1/PBe2: Using original auxPrice=%s as entry_stop_price (filled_price=%s)", 
                               entry_stop_price, filled_price)
                elif entry_stop_price == filled_price:
                    # If entry_stop_price equals filled_price, try to get it from lastPrice (stored during StatusUpdate)
                    stored_last_price = entryData.get('lastPrice', 0)
                    if stored_last_price and stored_last_price > 0:
                        entry_stop_price = float(stored_last_price)
                        logging.info("PBe1/PBe2: Using stored lastPrice=%s as entry_stop_price (filled_price=%s)", 
                                   entry_stop_price, filled_price)
                    else:
                        logging.warning("PBe1/PBe2: Could not get original entry stop price, using filled_price as fallback")
            logging.info("In TPSL %s contract  and for %s histdata. Entry stop price=%s, Filled price=%s, barType=%s, stopLoss=%s",
                         entryData['contract'], histData, entry_stop_price, filled_price, entryData.get('barType'), entryData.get('stopLoss'))
            
            # Conditional Order, FB, RB, RBB, LB, LB2, LB3, PBe1, PBe2 use same TP/SL logic (skip for manual orders - they have their own logic)
            if (entryData['barType'] == Config.entryTradeType[2]) or (entryData['barType'] == Config.entryTradeType[3]) or (entryData['barType'] == Config.entryTradeType[4]) or (entryData['barType'] == Config.entryTradeType[5]) or (entryData['barType'] == Config.entryTradeType[6]) or (entryData['barType'] == Config.entryTradeType[7]) or (entryData['barType'] == Config.entryTradeType[9]) or (entryData['barType'] == Config.entryTradeType[10]):
                candleData = connection.getDailyCandle(entryData['contract'])
                if (candleData == None or len(candleData) < 1):
                    logging.info("candle data not found for %s", entryData['contract'])
                    await  asyncio.sleep(1)
                    continue
                logging.info(" Candle Data for takeProfit %s and contract is %s", candleData[-1],entryData['contract'])
                
                # Check if stop loss is ATR-based or Custom - use same stop_size as entry and stop loss
                # PBe1/PBe2: Always uses HOD/LOD for stop loss (similar to RBB+HOD/LOD)
                stop_loss_type = entryData.get('stopLoss')
                is_pbe1_or_pbe2 = (entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7])  # PBe1 or PBe2
                
                if is_pbe1_or_pbe2:  # PBe1/PBe2 - always uses HOD/LOD for stop loss (similar to RBB+HOD/LOD)
                    # PBe1/PBe2: Entry price for TP = entry_stop_price (bar_high/low ± 0.01), stop_size = |entry_stop_price - HOD/LOD|
                    # For RTH: Recalculate stop_size using current HOD/LOD (same as stop loss)
                    # For extended hours: Use stored stop_size (HOD/LOD doesn't change during extended hours)
                    is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                    bar_type_name = "PBe1" if entryData['barType'] == Config.entryTradeType[6] else "PBe2"
                    
                    if not is_extended:
                        # RTH: Recalculate stop_size using current HOD/LOD (same as stop loss calculation)
                        logging.info(f"{bar_type_name} RTH: Recalculating stop_size for TP using current HOD/LOD")
                        try:
                            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                            
                            if entryData.get('action') == 'BUY':  # LONG position
                                pbe_stop_size = abs(float(entry_stop_price) - lod) if lod else 0
                                logging.info(f"{bar_type_name} TP RTH (LONG): Recalculated stop_size={pbe_stop_size} from current LOD={lod}, entry_stop_price={entry_stop_price}")
                            else:  # SHORT position
                                pbe_stop_size = abs(float(entry_stop_price) - hod) if hod else 0
                                logging.info(f"{bar_type_name} TP RTH (SHORT): Recalculated stop_size={pbe_stop_size} from current HOD={hod}, entry_stop_price={entry_stop_price}")
                            
                            pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                            if pbe_stop_size <= 0:
                                logging.error(f"{bar_type_name} TP RTH: Invalid stop_size ({pbe_stop_size}) from HOD/LOD. HOD={hod}, LOD={lod}, entry_stop_price={entry_stop_price}")
                                pbe_stop_size = 0
                        except Exception as e:
                            logging.error(f"{bar_type_name} TP RTH: Error recalculating stop_size: {e}. Using stored value as fallback.")
                            # Fallback to stored stop_size
                            order_id = entryData.get('orderId')
                            order_data = Config.orderStatusData.get(order_id) if order_id else None
                            if order_data:
                                stored_stop_size = order_data.get('stopSize', 0)
                                pbe_stop_size = stored_stop_size if stored_stop_size > 0 else 0
                                logging.warning(f"{bar_type_name} TP RTH: Using stored stop_size={pbe_stop_size} as fallback")
                            else:
                                pbe_stop_size = 0
                    else:
                        # Extended hours: Use stored stop_size (HOD/LOD doesn't change during extended hours)
                        order_id = entryData.get('orderId')
                        order_data = Config.orderStatusData.get(order_id) if order_id else None
                        
                        if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                            lod = order_data.get('pbe1_lod', 0)
                            hod = order_data.get('pbe1_hod', 0)
                            stored_stop_size = order_data.get('stopSize', 0)
                            
                            # For LONG position (BUY entry): stop_size = |entry_stop_price - LOD|
                            if entryData.get('action') == 'BUY':  # LONG position
                                pbe_stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(entry_stop_price) - lod)
                                logging.info(f"{bar_type_name} TP Extended hours (LONG): Using stored stop_size={pbe_stop_size} from LOD={lod}, entry_stop_price={entry_stop_price}")
                            else:  # SHORT position (SELL entry)
                                pbe_stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(entry_stop_price) - hod)
                                logging.info(f"{bar_type_name} TP Extended hours (SHORT): Using stored stop_size={pbe_stop_size} from HOD={hod}, entry_stop_price={entry_stop_price}")
                            
                            pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                            if pbe_stop_size <= 0:
                                logging.error(f"{bar_type_name} TP Extended hours: Invalid stop_size ({pbe_stop_size}) from HOD/LOD. HOD={hod}, LOD={lod}, entry_stop_price={entry_stop_price}")
                                pbe_stop_size = 0
                        else:
                            # Fallback: recalculate from HOD/LOD
                            logging.warning(f"{bar_type_name} TP Extended hours: LOD/HOD not found in orderStatusData, recalculating")
                            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                            if entryData.get('action') == 'BUY':  # LONG position
                                pbe_stop_size = abs(float(entry_stop_price) - lod) if lod else 0
                            else:  # SHORT position
                                pbe_stop_size = abs(float(entry_stop_price) - hod) if hod else 0
                            pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                            logging.warning(f"{bar_type_name} TP Extended hours: Recalculated stop_size={pbe_stop_size} from HOD/LOD")
                            pbe_stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(entry_stop_price) - hod)
                            logging.info(f"{bar_type_name} TP (SHORT): Using stored stop_size={pbe_stop_size} from HOD={hod}, entry_stop_price={entry_stop_price}")
                        
                        pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                        if pbe_stop_size <= 0:
                            logging.error(f"PBe1 TP: Invalid stop_size ({pbe_stop_size}) from HOD/LOD. HOD={hod}, LOD={lod}, entry_stop_price={entry_stop_price}")
                            pbe_stop_size = 0
                        else:
                            # Fallback: recalculate from HOD/LOD
                            bar_type_name = "PBe1" if entryData['barType'] == Config.entryTradeType[6] else "PBe2"
                            logging.warning(f"{bar_type_name}: LOD/HOD not found in orderStatusData, recalculating")
                            lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                            if entryData.get('action') == 'BUY':  # LONG position
                                pbe_stop_size = abs(float(entry_stop_price) - lod) if lod else 0
                            else:  # SHORT position
                                pbe_stop_size = abs(float(entry_stop_price) - hod) if hod else 0
                            pbe_stop_size = round(pbe_stop_size, Config.roundVal)
                            logging.warning(f"{bar_type_name} TP: Recalculated stop_size={pbe_stop_size} from HOD/LOD")
                    
                    # Calculate TP: entry_stop_price ± stop_size * multiplier
                    multiplier_map = {
                        Config.takeProfit[0]: 1,    # 1:1
                        Config.takeProfit[1]: 1.5,  # 1.5:1
                        Config.takeProfit[2]: 2,    # 2:1
                        Config.takeProfit[3]: 2.5,  # 2.5:1
                    }
                    if len(Config.takeProfit) > 4:
                        multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                    
                    multiplier = multiplier_map.get(entryData['profit'], 2.0)  # Default 2:1
                    # For PBe1/PBe2: Use entry_stop_price (entry price) for TP calculation
                    # TP = entry_stop_price ± (multiplier × stop_size)
                    if entryData.get('action') == 'BUY':  # LONG position
                        # For LONG: TP = entry_stop_price + (multiplier × stop_size)
                        price = float(entry_stop_price) + (multiplier * pbe_stop_size)
                    else:  # SHORT position
                        # For SHORT: TP = entry_stop_price - (multiplier × stop_size)
                        price = float(entry_stop_price) - (multiplier * pbe_stop_size)
                    
                    price = round(price, Config.roundVal)
                    bar_type_name = "PBe1" if entryData['barType'] == Config.entryTradeType[6] else "PBe2"
                    logging.info(f"{bar_type_name} TP calculation: entry_stop_price={entry_stop_price} (entry price), stop_size={pbe_stop_size}, multiplier={multiplier}, tp={price}")
                    
                    # Send TP order directly for PBe1/PBe2 - don't fall through to duplicate send
                    if entryData.get('action') == 'BUY':  # LONG position
                        tp_action = "SELL"  # To close long
                    else:  # SHORT position
                        tp_action = "BUY"  # To close short
                    logging.info(f"{bar_type_name} Sending TP Trade EntryData is %s  and Price is %s  and action is {tp_action}", entryData, price)
                    sendTakeProfit(connection, entryData, price, tp_action)
                    # Skip the rest of TP calculation - already sent
                    price = None  # Mark as already sent
                    # Continue to stop loss calculation - don't return early
                elif stop_loss_type in Config.atrStopLossMap:
                    # Use ATR stop_size for take profit (same as entry and stop loss)
                    atr_offset = _get_atr_stop_offset(connection, entryData['contract'], stop_loss_type)
                    if atr_offset is not None and atr_offset > 0:
                        stop_size = atr_offset
                        logging.info(f"RB/RBB ATR TP (LONG): Using ATR stop_size={stop_size} for take profit")
                    else:
                        # Fallback to bar-based if ATR unavailable
                        try:
                            stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                            logging.warning(f"RB/RBB: ATR unavailable, using bar-based stop_size={stop_size} for TP")
                        except Exception:
                            stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                            logging.warning(f"RB/RBB: Using fallback bar-based stop_size={stop_size} for TP")
                elif stop_loss_type == Config.stopLoss[3] or stop_loss_type == Config.stopLoss[4]:  # HOD or LOD
                    # For LOD/HOD: Use stored stop_size from entryData (calculated from LOD/HOD)
                    stored_stop_size = entryData.get('calculated_stop_size')
                    if stored_stop_size is not None and stored_stop_size > 0:
                        stop_size = stored_stop_size
                        logging.info(f"RB/RBB HOD/LOD TP (LONG): Using stored stop_size={stop_size} from LOD/HOD for take profit")
                    else:
                        # Fallback: recalculate from LOD/HOD
                        # Uses premarket data for premarket, RTH data for after hours
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                        if lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
                            # For HOD/LOD: stop_size = |bar_high - LOD| (for LONG position, auto-detect LOD)
                            bar_price = float(histData['high'])  # For LONG, use bar_high
                            stop_size = abs(bar_price - lod)  # LONG uses LOD
                            logging.warning(f"RB/RBB HOD/LOD TP (LONG): Recalculated stop_size={stop_size} from bar_high={bar_price} and LOD={lod}")
                        else:
                            # Fallback to bar-based
                            try:
                                stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                                logging.warning(f"RB/RBB: HOD/LOD historical data missing, using bar-based stop_size={stop_size} for TP")
                            except Exception:
                                stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                logging.warning(f"RB/RBB: Using fallback bar-based stop_size={stop_size} for TP")
                elif stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop_size for take profit (same as entry and stop loss)
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop == 0:
                        # Fallback to bar-based if custom value missing
                        try:
                            stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                            logging.warning(f"RB/RBB: Custom stop loss value missing, using bar-based stop_size={stop_size} for TP")
                        except Exception:
                            stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                            logging.warning(f"RB/RBB: Using fallback bar-based stop_size={stop_size} for TP")
                    else:
                        # For RBB with Custom stop loss: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|
                        # Use entry_price (not filled_price) - entry_price is stored in lastPrice
                        entry_price = entry_stop_price  # Use entry_stop_price (from lastPrice)
                        if histData and isinstance(histData, dict):
                            # For LONG position (SELL TP): entry was BUY, so use bar_high
                            bar_price = float(histData.get('high', entry_price))
                            stop_size = abs(bar_price - custom_stop)
                        else:
                            # Fallback: use entry_price if histData not available
                            stop_size = abs(float(entry_price) - custom_stop)
                        stop_size = round(stop_size, Config.roundVal)
                        logging.info(f"RB/RBB Custom TP (LONG): entry={entry_price}, bar_high={histData.get('high') if histData else 'N/A'}, custom_stop={custom_stop}, stop_size={stop_size} for take profit")
                else:
                    # Non-ATR, Non-Custom stop loss: use bar-based stop_size (same as entry and stop loss)
                    try:
                        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                    except Exception:
                        stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                    logging.info(f"RB/RBB bar-based TP (LONG): Using bar-based stop_size={stop_size} for take profit")

                # PBe1/PBe2 TP was already sent in the earlier block (lines 6393-6460), skip duplicate logic here
                # For other trade types (FB/RB/RBB/LB/LB2/LB3): use shared calculation
                if not is_pbe1_or_pbe2:
                    # For other trade types (FB/RB/RBB/LB/LB2/LB3): use shared calculation
                    # Ensure stop_size is initialized (fallback to bar-based if not set)
                    if 'stop_size' not in locals() or stop_size is None or stop_size <= 0:
                        try:
                            stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                            logging.warning(f"sendTpSlSell: stop_size was not initialized, using bar-based calculation: stop_size={stop_size}")
                        except Exception as e:
                            # Final fallback: use bar range
                            stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                            logging.warning(f"sendTpSlSell: Error calculating stop_size, using fallback bar range: stop_size={stop_size}, error={e}")
                    
                    multiplier_map = {
                        Config.takeProfit[0]: 1,
                        Config.takeProfit[1]: 1.5,
                        Config.takeProfit[2]: 2,
                        Config.takeProfit[3]: 2.5,  # '2.5:1' is at index 3
                    }
                    # Add 3:1 if it exists (index 4)
                    if len(Config.takeProfit) > 4:
                        multiplier_map[Config.takeProfit[4]] = 3

                    multiplier = multiplier_map.get(entryData['profit'])
                    if multiplier is not None:
                        # For RTH: Use filled_price (not entry_stop_price) for TP calculation
                        # For extended hours: Use entry_stop_price
                        is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                        tp_base_price = entry_stop_price if is_extended else filled_price
                        price = float(tp_base_price) + (multiplier * stop_size)
                        logging.info(f"LB/RB/RBB/LB2/LB3 TP (LONG): tp_base_price={tp_base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}, is_extended={is_extended}), stop_size={stop_size}, multiplier={multiplier}, TP={price}")
                    else:
                        price = float(histData['high'])

                    price = round(price, Config.roundVal)
                    logging.info(
                        "Extended TP calculation (sell/LONG) %s stop_size=%s multiplier=%s entry_stop_price=%s filled_price=%s price=%s",
                        entryData['contract'], stop_size, multiplier, entry_stop_price, filled_price, price,
                    )
                    
                    # Send TP order directly for RBB/RB/FB/LB/LB2/LB3 - don't fall through to duplicate send
                    logging.info("Sending TP Trade EntryData is %s  and Price is %s  and action is SELL (LONG position)", entryData, price)
                    sendTakeProfit(connection, entryData, price, "SELL")
                    # Skip the rest of TP calculation - already sent
                    price = None  # Mark as already sent
            else:
                # Check if this is a manual order (Stop Order or Limit Order) with custom or HOD/LOD stop loss
                if entryData['barType'] in Config.manualOrderTypes:
                    stop_loss_type = entryData.get('stopLoss')
                    
                    # Check if this is HOD/LOD stop loss
                    if stop_loss_type == Config.stopLoss[3] or stop_loss_type == Config.stopLoss[4]:  # HOD or LOD
                        # For HOD/LOD: recalculate stop_size using entry price and LOD/HOD
                        # Uses premarket data for premarket, RTH data for after hours
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                        
                        if lod is not None and hod is not None:
                            # For HOD/LOD: stop_size = |bar_high/low - HOD/LOD|
                            # Get histData for bar price
                            histData_manual = entryData.get('histData')
                            if histData_manual and isinstance(histData_manual, dict):
                                # For LONG position (action='BUY'): use bar_high
                                # For SHORT position (action='SELL'): use bar_low
                                if entryData.get('action') == 'BUY':  # LONG
                                    bar_price = float(histData_manual.get('high', entry_stop_price))
                                else:  # SELL (SHORT)
                                    bar_price = float(histData_manual.get('low', entry_stop_price))
                            else:
                                bar_price = float(entry_stop_price)  # Fallback
                            
                            if stop_loss_type == Config.stopLoss[4]:  # LOD
                                stop_size = abs(bar_price - lod)
                            else:  # HOD
                                stop_size = abs(bar_price - hod)
                            
                            stop_size = round(stop_size, Config.roundVal)
                            multiplier_map = {
                                Config.takeProfit[0]: 1,    # 1:1
                                Config.takeProfit[1]: 1.5,  # 1.5:1
                                Config.takeProfit[2]: 2,    # 2:1
                                Config.takeProfit[3]: 2.5,  # 2.5:1
                            }
                            if len(Config.takeProfit) > 4:
                                multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                            multiplier = multiplier_map.get(entryData['profit'], 1)
                            # For LONG position (SELL TP): TP = entry + (multiplier × stop_size)
                            price = float(entry_stop_price) + (multiplier * stop_size)
                            price = round(price, Config.roundVal)
                            logging.info(f"Manual Order HOD/LOD TP (LONG): entry={entry_stop_price}, LOD={lod}, HOD={hod}, stop_size={stop_size}, multiplier={multiplier}, tp={price}")
                        else:
                            # Fallback to regular calculation if HOD/LOD unavailable
                            price = get_tp_for_selling(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                            logging.warning(f"Manual Order HOD/LOD TP (LONG): LOD/HOD data unavailable, using fallback calculation")
                        # Skip further processing for HOD/LOD stop loss - price is already set
                    elif stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                        # Use Custom stop_size for take profit (same as entry and stop loss)
                        custom_stop = _to_float(entryData.get('slValue', 0), 0)
                        if custom_stop == 0:
                            # Fallback to regular calculation if custom value missing
                            price = get_tp_for_selling(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                            logging.warning(f"Manual Order Custom TP (LONG): Custom stop loss value missing, using fallback calculation")
                        else:
                            # stop_size = |entry - custom_stop| + 0.02 (same as entry order calculation)
                            stop_size = abs(float(entry_stop_price) - custom_stop) + 0.02
                            stop_size = round(stop_size, Config.roundVal)
                            multiplier_map = {
                                Config.takeProfit[0]: 1,    # 1:1
                                Config.takeProfit[1]: 1.5,  # 1.5:1
                                Config.takeProfit[2]: 2,    # 2:1
                                Config.takeProfit[3]: 2.5,  # 2.5:1
                            }
                            if len(Config.takeProfit) > 4:
                                multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                            multiplier = multiplier_map.get(entryData['profit'], 1)
                            # For LONG position (SELL TP): TP = entry + (multiplier × stop_size)
                            price = float(entry_stop_price) + (multiplier * stop_size)
                            price = round(price, Config.roundVal)
                            logging.info(f"Manual Order Custom TP (LONG): entry={entry_stop_price}, custom_stop={custom_stop}, stop_size={stop_size}, multiplier={multiplier}, tp={price}")
                        # Skip further processing for custom stop loss - price is already set
                    elif entryData['barType'] == 'Custom' and stop_loss_type not in Config.atrStopLossMap and stop_loss_type != Config.stopLoss[1]:  # Custom entry type with non-Custom, non-ATR stop loss
                        # For Custom entry type in extended hours with non-Custom, non-ATR stop loss: use stored stop_size
                        order_id = entryData.get('orderId')
                        order_data = Config.orderStatusData.get(order_id) if order_id else None
                        stored_stop_size = order_data.get('stopSize') if order_data else None
                        
                        if stored_stop_size is not None and stored_stop_size > 0:
                            stop_size = stored_stop_size
                            multiplier_map = {
                                Config.takeProfit[0]: 1,    # 1:1
                                Config.takeProfit[1]: 1.5,  # 1.5:1
                                Config.takeProfit[2]: 2,    # 2:1
                                Config.takeProfit[3]: 2.5,  # 2.5:1
                            }
                            if len(Config.takeProfit) > 4:
                                multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                            multiplier = multiplier_map.get(entryData['profit'], 1)
                            # For LONG position (SELL TP): TP = entry + (multiplier × stop_size)
                            price = float(entry_stop_price) + (multiplier * stop_size)
                            price = round(price, Config.roundVal)
                            logging.info(f"Custom entry OTH TP (LONG): entry={entry_stop_price}, stop_size={stored_stop_size} (from orderStatusData), stopLoss={stop_loss_type}, multiplier={multiplier}, tp={price}")
                        else:
                            # Fallback to regular calculation if stop_size not found
                            logging.warning(f"Custom entry OTH TP (LONG): stop_size not found in orderStatusData, using fallback calculation")
                            price = get_tp_for_selling(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                # Check if this is a Limit Order with ATR stop loss - use same ATR stop_size for TP
                elif entryData['barType'] == Config.entryTradeType[0] and entryData.get('stopLoss') in Config.atrStopLossMap:
                    # Use ATR stop_size for take profit calculation (same as entry and stop loss)
                    atr_offset = _get_atr_stop_offset(connection, entryData['contract'], entryData['stopLoss'])
                    if atr_offset is not None and atr_offset > 0:
                        stop_size = atr_offset
                        multiplier_map = {
                            Config.takeProfit[0]: 1,    # 1:1
                            Config.takeProfit[1]: 1.5,  # 1.5:1
                            Config.takeProfit[2]: 2,    # 2:1
                            Config.takeProfit[3]: 2.5,  # 2.5:1
                        }
                        if len(Config.takeProfit) > 4:
                            multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                        multiplier = multiplier_map.get(entryData['profit'], 1)
                        # For LONG position (SELL TP): TP = entry + (multiplier × stop_size)
                        price = float(entry_stop_price) + (multiplier * stop_size)
                        price = round(price, Config.roundVal)
                        logging.info(f"Limit Order ATR TP (LONG): entry={entry_stop_price}, stop_size={stop_size} (ATR), multiplier={multiplier}, tp={price}")
                    else:
                        # Fallback to regular calculation if ATR unavailable
                        price = get_tp_for_selling(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                else:
                    price = get_tp_for_selling(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)

            # Only send TP if not already sent (RBB/RB/FB/LB/LB2/LB3/PBe1/PBe2 send it directly)
            if price is not None and price != 0:
                logging.info("Sending TP Trade EntryData is %s  and Price is %s  and action is SELL", entryData, price)
                sendTakeProfit(connection, entryData, price, "SELL")

            # Calculate stop size in advance for RB, LB, LB2, LB3 (RBB uses different logic)
            stpPrice = 0
            chart_Time = datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime,  "%Y-%m-%d %H:%M:%S")
            
            # For RB, RBB, LB, LB2, LB3: calculate stop size in advance and use it for stop loss calculation
            # Note: RBB (entryTradeType[5]) uses different logic - it updates stop price continuously via rbb_loop_run, but still needs initial LOD/HOD calculation
            # PBe1/PBe2: Always uses HOD/LOD for stop loss (similar to RBB+HOD/LOD)
            # For RTH: Always recalculate LOD/HOD from current RTH data (not stored value)
            if (entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7]):  # PBe1 or PBe2
                bar_type_name = "PBe1" if entryData['barType'] == Config.entryTradeType[6] else "PBe2"
                
                # Check if RTH or extended hours
                is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                
                if not is_extended:
                    # RTH: Always recalculate LOD/HOD from current RTH data (same as RBB)
                    logging.info(f"{bar_type_name} RTH: Recalculating LOD/HOD from current RTH data for stop loss calculation")
                    try:
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                        
                        # For LONG position (SELL stop loss): use LOD
                        # For SHORT position (BUY stop loss): use HOD
                        base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                        if entryData.get('action') == 'BUY':  # LONG position
                            stop_loss_price = round(lod, Config.roundVal) if lod else 0
                            stop_size = abs(float(base_price) - lod) if lod else 0
                            logging.info(f"{bar_type_name} RTH: Using recalculated LOD={lod} for LONG position stop loss")
                        else:  # SHORT position
                            stop_loss_price = round(hod, Config.roundVal) if hod else 0
                            stop_size = abs(float(base_price) - hod) if hod else 0
                            logging.info(f"{bar_type_name} RTH: Using recalculated HOD={hod} for SHORT position stop loss")
                        
                        stpPrice = round(stop_loss_price, Config.roundVal)
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"{bar_type_name} RTH stop loss in sendTpSlSell: base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), LOD={lod}, HOD={hod}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    except Exception as e:
                        logging.error(f"{bar_type_name} RTH: Error recalculating LOD/HOD: {e}. Using stored values as fallback.")
                        # Fallback: use stored LOD/HOD
                        order_id = entryData.get('orderId')
                        order_data = Config.orderStatusData.get(order_id) if order_id else None
                        if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                            lod = order_data.get('pbe1_lod', 0)
                            hod = order_data.get('pbe1_hod', 0)
                            stored_stop_size = order_data.get('stopSize', 0)
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            if entryData.get('action') == 'BUY':  # LONG position
                                stop_loss_price = round(lod, Config.roundVal)
                                stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(base_price) - lod)
                            else:  # SHORT position
                                stop_loss_price = round(hod, Config.roundVal)
                                stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(base_price) - hod)
                            stpPrice = round(stop_loss_price, Config.roundVal)
                            entryData['calculated_stop_size'] = stop_size
                            logging.warning(f"{bar_type_name} RTH: Using stored LOD/HOD as fallback: LOD={lod}, HOD={hod}, stop_loss_price={stop_loss_price}")
                else:
                    # Extended hours: Use stored LOD/HOD (same as before)
                    order_id = entryData.get('orderId')
                    order_data = Config.orderStatusData.get(order_id) if order_id else None
                    if order_data and 'pbe1_lod' in order_data and 'pbe1_hod' in order_data:
                        lod = order_data.get('pbe1_lod', 0)
                        hod = order_data.get('pbe1_hod', 0)
                        stored_stop_size = order_data.get('stopSize', 0)
                        base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                        if entryData.get('action') == 'BUY':  # LONG position
                            stop_loss_price = round(lod, Config.roundVal)
                            stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(base_price) - lod)
                        else:  # SHORT position
                            stop_loss_price = round(hod, Config.roundVal)
                            stop_size = stored_stop_size if stored_stop_size > 0 else abs(float(base_price) - hod)
                        stpPrice = round(stop_loss_price, Config.roundVal)
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"{bar_type_name} Extended hours stop loss in sendTpSlSell: base_price={base_price}, LOD={lod}, HOD={hod}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, stpPrice={stpPrice}")
                    else:
                        # Fallback: recalculate LOD/HOD
                        logging.warning(f"{bar_type_name} Extended hours: LOD/HOD not found in orderStatusData, recalculating")
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData.get('timeFrame', '1 min'))
                        base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                        if entryData.get('action') == 'BUY':  # LONG position
                            stop_loss_price = round(lod, Config.roundVal) if lod else 0
                            stop_size = abs(float(base_price) - lod) if lod else 0
                        else:  # SHORT position
                            stop_loss_price = round(hod, Config.roundVal) if hod else 0
                            stop_size = abs(float(base_price) - hod) if hod else 0
                        stpPrice = round(stop_loss_price, Config.roundVal)
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"{bar_type_name} Extended hours stop loss (recalculated) in sendTpSlSell: LOD={lod}, HOD={hod}, stop_loss_price={stop_loss_price}, stop_size={stop_size}, stpPrice={stpPrice}")
            elif (entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[2] or entryData['barType'] == Config.entryTradeType[4] or entryData['barType'] == Config.entryTradeType[5] or 
                entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7] or entryData['barType'] == Config.entryTradeType[8] or
                entryData['barType'] == Config.entryTradeType[9] or entryData['barType'] == Config.entryTradeType[10]):
                # Calculate stop size for stop loss
                stop_loss_type = entryData.get('stopLoss')
                if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                    # For EntryBar: stop_size = (bar_high - bar_low) + 0.02
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    stop_size = round(stop_size, Config.roundVal)
                    # In extended hours: stop = entry ± stop_size, limit = entry ± 2 × stop_size
                    # For LONG position (SELL stop loss): stop_loss = entry - stop_size
                    # For Stop Order: Use entry_stop_price instead of filled_price
                    base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                    stpPrice = float(base_price) - float(stop_size)
                    logging.info(f"RB/LB/LB2/LB3 EntryBar stop loss (for LONG): base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    entryData['calculated_stop_size'] = stop_size
                else:
                    # For other stop loss types: calculate stop size and use filled_price ± stop_size
                    # Check if stop loss type is ATR-based
                    if stop_loss_type in Config.atrStopLossMap:
                        # Use ATR offset for stop size
                        atr_offset = _get_atr_stop_offset(connection, entryData['contract'], stop_loss_type)
                        if atr_offset is not None and atr_offset > 0:
                            stop_size = atr_offset
                            protection_offset = stop_size * 2.0
                            logging.info(f"RB/LB/LB2/LB3 ATR stop loss: stop_size={stop_size} (ATR offset), protection_offset={protection_offset}")
                        else:
                            # Fallback to bar range if ATR unavailable
                            try:
                                stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
                                logging.warning(f"RB/LB/LB2/LB3: ATR unavailable, using bar range stop_size={stop_size}")
                            except Exception:
                                stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                protection_offset = stop_size * 2.0
                                logging.warning(f"RB/LB/LB2/LB3: Using fallback stop_size={stop_size}")
                    elif stop_loss_type == Config.stopLoss[1]:  # 'Custom'
                        # Use Custom stop loss value directly
                        custom_stop = _to_float(entryData.get('slValue', 0), 0)
                        if custom_stop == 0:
                            # Fallback to bar range if custom value missing
                            try:
                                stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
                                logging.warning(f"RB/LB/LB2/LB3: Custom stop loss value missing, using bar range stop_size={stop_size}")
                            except Exception:
                                stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                protection_offset = stop_size * 2.0
                                logging.warning(f"RB/LB/LB2/LB3: Using fallback stop_size={stop_size}")
                            # Calculate stop loss price using stop size
                            # For Stop Order: Use entry_stop_price instead of filled_price
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            stpPrice = float(base_price) - float(stop_size)
                        else:
                            # For Custom: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|, stop_price = custom_stop
                            # For RBB: Use entry_stop_price (not filled_price) and bar high/low for stop_size calculation
                            # For Stop Order: Use entry_stop_price instead of filled_price
                            entry_price = entry_stop_price  # Use entry_price (not filled_price)
                            
                            if entryData.get('barType') == Config.entryTradeType[5]:  # RBB
                                # For RBB with Custom stop loss: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|
                                if histData and isinstance(histData, dict):
                                    # For LONG position (SELL stop loss): entry was BUY, so use bar_high
                                    bar_price = float(histData.get('high', entry_price))
                                    stop_size = abs(bar_price - custom_stop)
                                else:
                                    # Fallback: use entry_price if histData not available
                                    stop_size = abs(float(entry_price) - custom_stop)
                            elif entryData.get('barType') == Config.entryTradeType[0]:  # Stop Order
                                # For Stop Order: use entry_price
                                stop_size = abs(float(entry_price) - custom_stop) + 0.02
                            else:
                                # For other trade types: use filled_price
                                stop_size = abs(float(filled_price) - custom_stop) + 0.02
                            
                            stop_size = round(stop_size, Config.roundVal)
                            protection_offset = stop_size * 2.0
                            # Stop loss price is the custom value directly (should NOT update - fixed at custom_stop)
                            stpPrice = round(custom_stop, Config.roundVal)
                            logging.info(f"RB/LB/LB2/LB3/RBB Custom stop loss (for LONG): entry_price={entry_price}, bar_high={histData.get('high') if (histData and isinstance(histData, dict)) else 'N/A'}, custom_stop={custom_stop}, stop_size={stop_size}, stpPrice={stpPrice} (FIXED, should NOT update), barType={entryData.get('barType')}")
                            # Store stop_size in entryData for sendStopLoss to use in extended hours
                            entryData['calculated_stop_size'] = stop_size
                            logging.info(f"RB/LB/LB2/LB3/RBB SELL stop loss (for LONG): entry_price={entry_price}, stop_size={stop_size}, stpPrice={stpPrice} (FIXED)")
                    elif stop_loss_type == Config.stopLoss[3] or stop_loss_type == Config.stopLoss[4]:  # HOD or LOD
                        # For LOD/HOD: Calculate LOD/HOD from historical data
                        # Uses premarket data for premarket, RTH data for after hours
                        lod, hod, recent_bar_data = _get_lod_hod_for_stop_loss(connection, entryData['contract'], entryData['timeFrame'])
                        
                        # Check if extended hours
                        is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                        
                        if lod is not None and hod is not None and recent_bar_data and len(recent_bar_data) > 0:
                            # For LONG position (SELL stop loss): auto-detect LOD
                            if is_extended:
                                # Extended hours: Stop loss price = Entry bar Low - 0.01 (NOT LOD)
                                # For Stop Order: Use entry_stop_price as fallback instead of filled_price
                                base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                                stop_loss_price = round(float(histData.get('low', base_price)) - 0.01, Config.roundVal)
                                logging.info(f"RB/LB/LB2/LB3 HOD/LOD Extended hours LONG: Stop loss price = Entry bar Low - 0.01 = {stop_loss_price} (base_price={base_price}, entry_stop_price={entry_stop_price}, filled_price={filled_price}, barType={entryData.get('barType')})")
                            else:
                                # RTH: Stop loss price = LOD (auto-detect for LONG)
                                stop_loss_price = round(lod, Config.roundVal)
                                logging.info(f"RB/LB/LB2/LB3 HOD/LOD RTH LONG: Auto-detected LOD, Stop loss price = LOD = {stop_loss_price}")
                            
                            # Calculate stop_size = |bar_high - LOD| for extended hours, |filled_price - LOD| for RTH
                            if is_extended:
                                bar_high = float(histData.get('high', entry_stop_price))
                                stop_size = abs(bar_high - lod)
                                logging.info(f"RB/LB/LB2/LB3 HOD/LOD Extended hours LONG: stop_size = |bar_high - LOD| = |{bar_high} - {lod}| = {stop_size}")
                            else:
                                # For RTH: Use filled_price (not entry_stop_price) for stop_size calculation
                                stop_size = abs(float(filled_price) - stop_loss_price)
                                logging.info(f"RB/LB/LB2/LB3 HOD/LOD RTH LONG: stop_size = |filled_price - LOD| = |{filled_price} - {stop_loss_price}| = {stop_size}")
                            
                            protection_offset = stop_size * 2.0
                            
                            # Stop loss price
                            stpPrice = round(stop_loss_price, Config.roundVal)
                            logging.info(f"RB/LB/LB2/LB3 HOD/LOD stop loss (for LONG): entry_stop_price={entry_stop_price}, filled_price={filled_price}, stop_loss_price={stop_loss_price}, LOD={lod}, HOD={hod}, stop_size={stop_size}, stpPrice={stpPrice}, is_extended={is_extended}")
                            
                            # Store stop_size and LOD for sendStopLoss to use in extended hours
                            entryData['calculated_stop_size'] = stop_size
                            # Store LOD for limit price calculation in extended hours (auto-detect for LONG)
                            entryData['lod_hod_stop_price'] = lod
                        else:
                            # Fallback to bar range if no historical data
                            try:
                                stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
                                logging.warning(f"RB/LB/LB2/LB3: LOD/HOD historical data missing, using bar range stop_size={stop_size}")
                            except Exception:
                                stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                                protection_offset = stop_size * 2.0
                                logging.warning(f"RB/LB/LB2/LB3: Using fallback stop_size={stop_size}")
                            # Calculate stop loss price using stop size
                            # For Stop Order: Use entry_stop_price instead of filled_price
                            base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                            stpPrice = float(base_price) - float(stop_size)
                            entryData['calculated_stop_size'] = stop_size
                            logging.info(f"RB/LB/LB2/LB3 HOD/LOD fallback stop loss (for LONG): base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    else:
                        # Non-ATR, Non-Custom stop loss: use bar range
                        try:
                            stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
                            logging.info(f"RB/LB/LB2/LB3: Calculated stop_size={stop_size}, protection_offset={protection_offset} for stop loss")
                        except Exception:
                            stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                            protection_offset = stop_size * 2.0
                            logging.warning(f"RB/LB/LB2/LB3: Using fallback stop_size={stop_size}")
                    
                    # Calculate stop loss price using stop size
                    # For LONG position (SELL stop loss): stop_loss = entry - stop_size
                    # In extended hours, this will be used as stop price for STP LMT, with limit = entry - 2 × stop_size
                    # For Stop Order: Use entry_stop_price instead of filled_price
                    # For RTH: Use filled_price (not entry_stop_price) for stop loss calculation
                    # Only calculate if stpPrice hasn't been set yet (e.g., for non-EntryBar, non-Custom, non-HOD/LOD stop loss types)
                    if 'stpPrice' not in locals() or stpPrice is None:
                        is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
                        # For Stop Order: Use entry_stop_price instead of filled_price
                        # For RTH: Use filled_price (not entry_stop_price) for LB/RB/RBB/LB2/LB3
                        if entryData.get('barType') == Config.entryTradeType[0]:  # Stop Order
                            base_price = entry_stop_price
                        elif is_extended:
                            base_price = entry_stop_price  # Extended hours: use entry_stop_price
                        else:
                            base_price = filled_price  # RTH: use filled_price
                        stpPrice = float(base_price) - float(stop_size)
                        logging.info(f"RB/LB/LB2/LB3 SELL stop loss (for LONG): base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}, is_extended={is_extended}), stop_size={stop_size}, stpPrice={stpPrice}, barType={entryData.get('barType')}")
                    # Store stop_size in entryData for sendStopLoss to use in extended hours
                    entryData['calculated_stop_size'] = stop_size
            else:
                # Check if this is a manual order (Stop Order or Limit Order) with custom stop loss
                if entryData['barType'] in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop loss value directly
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    # For Stop Order: Use entry_stop_price instead of filled_price
                    base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                    if custom_stop == 0:
                        # Fallback to existing logic if custom value missing
                        stpPrice = get_sl_for_selling(connection, entryData['stopLoss'], base_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
                        logging.warning(f"Manual Order Custom stop loss (for LONG): Custom stop loss value missing, using fallback calculation with base_price={base_price}")
                        stpPrice = stpPrice - 0.01
                    else:
                        # For Custom: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|, stop_price = custom_stop
                        # For RBB: Use entry_stop_price (not filled_price) and bar high/low for stop_size calculation
                        entry_price = entry_stop_price  # Use entry_price (not filled_price)
                        
                        if entryData.get('barType') == Config.entryTradeType[5]:  # RBB
                            # For RBB with Custom stop loss: stop_size = |bar_high (for BUY) or bar_low (for SELL) - custom_stop|
                            if histData and isinstance(histData, dict):
                                # For LONG position (SELL stop loss): entry was BUY, so use bar_high
                                bar_price = float(histData.get('high', entry_price))
                                stop_size = abs(bar_price - custom_stop)
                            else:
                                # Fallback: use entry_price if histData not available
                                stop_size = abs(float(entry_price) - custom_stop)
                        else:
                            # For Stop Order: use entry_price with +0.02
                            stop_size = abs(float(entry_price) - custom_stop) + 0.02
                        
                        stop_size = round(stop_size, Config.roundVal)
                        # Stop loss price is the custom value directly (should NOT update - fixed at custom_stop)
                        stpPrice = round(custom_stop, Config.roundVal)
                        # Store stop_size in entryData for sendStopLoss to use in extended hours
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"Manual Order/RBB Custom stop loss (for LONG): entry_price={entry_price}, bar_high={histData.get('high') if (histData and isinstance(histData, dict)) else 'N/A'}, custom_stop={custom_stop}, stop_size={stop_size}, stpPrice={stpPrice} (FIXED, should NOT update), barType={entryData.get('barType')}")
                # For other strategies (FB, PBe1, PBe2, etc.), use existing logic
                # Note: LB, RB, RBB, LB2, LB3 are already handled in the elif block above, so they should NOT reach here
                else:
                    # Skip if this is LB, RB, RBB, LB2, LB3 (already handled above)
                    bar_type = entryData.get('barType', '')
                    if bar_type in [Config.entryTradeType[4], Config.entryTradeType[5], Config.entryTradeType[8], 
                                    Config.entryTradeType[9], Config.entryTradeType[10]]:  # RB, RBB, LB, LB2, LB3
                        logging.warning(f"sendTpSlSell: {bar_type} should have been handled in elif block above, skipping else clause to prevent duplicate stop loss")
                    else:
                        # For Stop Order: Use entry_stop_price instead of filled_price
                        base_price = entry_stop_price if entryData.get('barType') == Config.entryTradeType[0] else filled_price
                        stpPrice = get_sl_for_selling(connection, entryData['stopLoss'], base_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
                        logging.info(f"minus 0.01 in stop entry is buying {stpPrice}")
                        stpPrice = stpPrice - 0.01
                        logging.info(f"SELL stop loss (for LONG): After -0.01 adjustment={stpPrice}, base_price={base_price} (entry_stop_price={entry_stop_price}, filled_price={filled_price}), barType={entryData.get('barType')}")
            
            logging.info("Sending STPLOSS Trade EntryData is %s  and Price is %s  and hist Data [ %s ] and action is Sell", entryData, stpPrice,histData)
            
            # Check if protection order already filled (for RBB in extended hours)
            if entryData.get('protection_order_filled', False):
                logging.warning("sendTpSlSell: Protection order already filled, skipping stop loss order placement. barType=%s, orderId=%s", 
                            entryData.get('barType'), entryData.get('orderId'))
            else:
                # Determine stop loss action based on entry action
                # sendTpSlSell is called for BUY entries (LONG position), so stop loss is SELL (to close LONG)
                # sendTpSlBuy is called for SELL entries (SHORT position), so stop loss is BUY (to close SHORT)
                entry_action = entryData.get('action', 'BUY')  # Default to BUY since this is sendTpSlSell
                if entry_action == 'BUY':  # LONG position
                    stop_loss_action = "SELL"  # SELL to close LONG position
                    logging.info("sendTpSlSell: CALLING sendStopLoss NOW - barType=%s, orderId=%s, stpPrice=%s, action=SELL (BUY entry = LONG position, stop loss is SELL)", 
                                entryData.get('barType'), entryData.get('orderId'), stpPrice)
                else:  # SELL entry (SHORT position) - should not happen in sendTpSlSell, but handle for safety
                    stop_loss_action = "BUY"  # BUY to close SHORT position
                    logging.warning("sendTpSlSell: Unexpected SELL entry in sendTpSlSell, using BUY stop loss. barType=%s, orderId=%s", 
                                entryData.get('barType'), entryData.get('orderId'))
                    logging.info("sendTpSlSell: CALLING sendStopLoss NOW - barType=%s, orderId=%s, stpPrice=%s, action=BUY (SELL entry = SHORT position, stop loss is BUY)", 
                                entryData.get('barType'), entryData.get('orderId'), stpPrice)
                try:
                    sendStopLoss(connection, entryData, stpPrice, stop_loss_action)
                    logging.info("sendTpSlSell: sendStopLoss returned successfully for barType=%s, orderId=%s, action=%s, stpPrice=%s", 
                                entryData.get('barType'), entryData.get('orderId'), stop_loss_action, stpPrice)
                except Exception as e:
                    logging.error("sendTpSlSell: Exception in sendStopLoss: %s", e)
                    logging.error("sendTpSlSell: Traceback: %s", traceback.format_exc())
                traceback.print_exc()
            mocPrice = 0
            logging.info("Sending Moc Order  of %s price ", mocPrice)
            sendMoc(connection, entryData, mocPrice, "SELL")
            
            # Handle option trading if enabled (using external module)
            try:
                from OptionTrading import handleOptionTrading
                # Store calculated prices in entryData for option trading
                if 'stpPrice' in locals() and stpPrice and stpPrice > 0:
                    entryData['stop_loss_price'] = stpPrice
                if 'price' in locals() and price and price > 0:
                    entryData['profit_price'] = price
                if 'filled_price' in locals():
                    entryData['filledPrice'] = filled_price
                handleOptionTrading(connection, entryData)
            except ImportError:
                logging.warning("OptionTrading module not found, skipping option trading")
            except Exception as e:
                logging.error("Error in option trading: %s", e)
            
            break
    except Exception as e:
        traceback.print_exc()
        logging.error("error in take profit and sl sell trade %s",e)
        print(e)

async def breakEvenCheck(connection):
    while True:
        try:
            for key,value in list(Config.orderStatusData.items()):
                stp_order_id = key
                if(value.get('ordType') == 'StopLoss') and Config.orderFilledPrice.get(key) == None and value.get('breakEven') == 'True' and value.get('entryData') != None and value.get('entryData') != '' and value.get('entryData').get('orderId') != None:
                    stp_order_id = key
                    diff_price = 0
                    priceObj = subscribePrice(value['contract'], connection)
                    lastPrice = priceObj.marketPrice()
                    current_price = lastPrice   #  live attach
                    connection.cancelTickData(value['contract'])
                    stp_price = value['lastPrice']
                    stp_side = value['action']
                    entry_order_id = value.get('entryData').get('orderId')
                    entry_filled_price = 0 if Config.orderFilledPrice.get(entry_order_id) == None else Config.orderFilledPrice.get(entry_order_id)
                    if(entry_filled_price !=0) and current_price !=0:
                        diff = 0
                        logging.info(f"breakeven check stp orderid {key}  value {value}  currentprice {current_price}")

                        if stp_side.upper() == 'BUY':
                            diff  = stp_price - entry_filled_price
                            diff_price =  entry_filled_price - diff
                            if current_price <= diff_price:
                                logging.info(f"breakeven check stp buy side diff {diff} diff_price {diff_price} currentprice {current_price}   new auxPrice {entry_filled_price}  id {key} ")

                                order_data= None
                                orders = connection.getAllOpenOrder()
                                for ord in orders:
                                    if ord.order.orderId  == key:
                                        logging.info(f"old order found for breakeven updation {ord.order}")
                                        order_data = ord
                                        break
                                order_data.order.auxPrice = entry_filled_price
                                # Get outsideRth from order data or default to False
                                outsideRth = getattr(order_data.order, 'outsideRth', False)
                                res = connection.placeTrade(contract=order_data.contract, order=order_data.order, outsideRth=outsideRth)
                                logging.info(f"update breakeven buy response   {res}")
                                # updateStopLoss(connection, value.get('entryData'), entry_filled_price, stp_side, key)
                        else:
                            diff  = entry_filled_price - stp_price
                            diff_price = entry_filled_price + diff
                            if current_price >= diff_price:
                                logging.info(f"breakeven check stp sell side diff {diff} diff_price {diff_price} currentprice {current_price}   new auxPrice {entry_filled_price}  id {key} ")
                                order_data = None
                                orders = connection.getAllOpenOrder()
                                for ord in orders:
                                    if ord.order.orderId == key:
                                        logging.info(f"old order found for breakeven updation {ord.order}")
                                        order_data = ord
                                        break
                                order_data.order.auxPrice = entry_filled_price
                                # Get outsideRth from order data or default to False
                                outsideRth = getattr(order_data.order, 'outsideRth', False)
                                res = connection.placeTrade(contract=order_data.contract, order=order_data.order, outsideRth=outsideRth)
                                logging.info(f"update breakeven sell response    {res}")
                                # updateStopLoss(connection, value.get('entryData'), entry_filled_price, stp_side, key)
        except Exception as e:
            logging.error(f"error in break even {traceback.format_exc()}")
        await asyncio.sleep(1)


async def stopLossThread(connection, entryData,price,action,orderId):
    try:
        logging.info("stop Loss BarByBar  thread is running for %s ",entryData)
        lmtData = Config.orderStatusData.get(orderId)
        currentTime = datetime.datetime.now()
        minuteInterval = getTimeInterval(lmtData['timeFrame'],currentTime)
        chartTime = ((currentTime + datetime.timedelta(seconds=minuteInterval))- datetime.timedelta(seconds=Config.timeDict.get((lmtData['timeFrame']))))
        sleepTime = ((minuteInterval) + 1)
        logging.info("Thread is going to sleep %s  in second and timeframe is %s", sleepTime, lmtData['timeFrame'])
        print("(first time) Thread is going to sleep %s   current datetime is %s  and chart timming  %s", sleepTime, currentTime,chartTime)
        await asyncio.sleep(sleepTime)
        nextSleepTime = Config.timeDict.get(lmtData['timeFrame'])
        while(lmtData != None and (lmtData['status'] != 'Filled' and lmtData['status'] != 'Cancelled' and lmtData['status'] != 'Inactive')):
            logging.info("running  stop loss in while loop, status is %s", lmtData['status'])
            histData = connection.getHistoricalChartData(lmtData['contract'], lmtData['timeFrame'], chartTime)
            logging.info("hist data for %s contract id, hist data is { %s }  and of %s time", lmtData['contract'], histData, chartTime)
            if (histData is None or len(histData) == 0):
                logging.info("hist data not found going to sleep for 1 second")
                nextSleepTime = nextSleepTime - 1
                if(nextSleepTime == 0):
                    nextSleepTime = Config.timeDict.get(lmtData['timeFrame'])
                await asyncio.sleep(1)
                continue
            if lmtData['action'] == "BUY":
                price = float(histData['high'])
                price = price + 0.01
            else:
                price = float(histData['low'])
                price = price - 0.01


            price = round(price, Config.roundVal)
            logging.info("updating barBybar stop loss, entrydata is %s, price is %s and order id is %s",entryData,price,orderId)

            order_data = None
            orders = connection.getAllOpenOrder()
            for ord in orders:
                if ord.order.orderId == orderId:
                    logging.info(f"old order found for stopLossThread updation {ord.order}")
                    order_data = ord
                    break
            order_data.order.auxPrice = price
            # Get outsideRth from order data or default to False
            outsideRth = getattr(order_data.order, 'outsideRth', False)
            res = connection.placeTrade(contract=order_data.contract, order=order_data.order, outsideRth=outsideRth)
            logging.info(f"response of stoploss updation {res}")

            # updateStopLoss(connection, entryData, price, lmtData['action'],orderId)
            logging.info("BarByBar stopLoss thread is sleeping for %s time in second ", Config.timeDict.get(lmtData['timeFrame']))

            chartTime = (chartTime + datetime.timedelta(seconds=Config.timeDict.get((lmtData['timeFrame']))))
            logging.info("barByBar stop loss new chart data time %s ",chartTime)
            await asyncio.sleep(nextSleepTime)
            lmtData = Config.orderStatusData.get(orderId)
        logging.info("stop loss thread end")
    except Exception as e:
        logging.error("error in stopLoss thread %s ", e)
        print(e)

def updateStopLoss(connection, entryData,price,action,orderId):
    try:
        logging.info("update barbybar stop loss with new price %s  and totalQuantityIs %s ",price,entryData['totalQuantity'])
        print("update barbybar stop loss with new price %s  and totalQuantityIs %s ",price,entryData['totalQuantity'])
        # lmtResponse = connection.placeTrade(contract=entryData['contract'],
        #                                     order=Order(auxPrice=price,orderId=orderId))
        lmtResponse = connection.placeTrade(contract=entryData['contract'],
                                            order=Order(orderType="STP", action=action,
                                                        totalQuantity=entryData['totalQuantity'], auxPrice=price, ocaGroup="tp" + str(entryData['orderId']), ocaType=1, orderId=orderId) ,outsideRth = entryData['outsideRth'] )

        StatusUpdate(lmtResponse, 'StopLoss', entryData['contract'], 'STP', action, entryData['totalQuantity'], entryData['histData'], price, entryData['usersymbol'], entryData['timeFrame'], entryData['profit'], entryData['stopLoss'], entryData['risk'],entryData,'','','','',entryData['slValue'],entryData['breakEven'],entryData['outsideRth'] )
    except Exception as e:
        logging.error("error in updating stop loss %s ", e)
        print(e)

async def conditional_order(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                            atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points):
    """
    Conditional Order logic:
    - Conditional Order 1: Single condition (Above/Below price)
      - If condition is "Below": Trigger when price drops TO the condition_price
      - If condition is "Above": Trigger when price rises TO the condition_price
      - Then place Stop Order at stop_order_price
    - Conditional Order 2: Either condition can trigger (OR logic)
      - If condition1 is "Below" and price drops to price1 → trigger
      - OR if condition2 is "Above" and price rises to price2 → trigger
      - Then place Stop Order at stop_order_price
    - Stop size = abs(stop_order_price - condition_price)
    - Entry: Stop order (STP) for both Regular Market and Premarket/Afterhours (same as PBe1/PBe2)
    - Protected Order and Take Profit: Same as RB logic
    """
    logging.info("conditional_order mkt trade is sending. %s", symbol)
    ibContract = getContract(symbol, None)
    key = (symbol + str(datetime.datetime.now()))
    logging.info("Key for this trade is- %s ", key)
    
    # Parse entry_points to get conditional order parameters
    # Format: selected_order,co1_stop,co1_condition,co1_price,co2_stop,co2_cond1,co2_price1,co2_cond2,co2_price2
    if not entry_points or entry_points == "0":
        logging.error("No conditional order parameters provided")
        return
    
    values = entry_points.split(",")
    if len(values) < 9:
        logging.error("Invalid conditional order parameters format")
        return
    
    selected_order = values[0] if len(values) > 0 else "0"
    co1_stop = _to_float(values[1] if len(values) > 1 else "0", 0)
    co1_condition = values[2] if len(values) > 2 else "Above"
    co1_price = _to_float(values[3] if len(values) > 3 else "0", 0)
    co2_stop = _to_float(values[4] if len(values) > 4 else "0", 0)
    co2_cond1 = values[5] if len(values) > 5 else "Above"
    co2_price1 = _to_float(values[6] if len(values) > 6 else "0", 0)
    co2_cond2 = values[7] if len(values) > 7 else "Above"
    co2_price2 = _to_float(values[8] if len(values) > 8 else "0", 0)
    
    # Determine which conditional order to use
    if selected_order == "1":
        # Conditional Order 1
        stop_order_price = co1_stop
        condition = co1_condition
        condition_price = co1_price
        logging.info(f"Conditional Order 1: stop_price={stop_order_price}, condition={condition}, condition_price={condition_price}")
    elif selected_order == "2":
        # Conditional Order 2
        stop_order_price = co2_stop
        condition1 = co2_cond1
        condition_price1 = co2_price1
        condition2 = co2_cond2
        condition_price2 = co2_price2
        logging.info(f"Conditional Order 2: stop_price={stop_order_price}, cond1={condition1} {condition_price1}, cond2={condition2} {condition_price2}")
    else:
        logging.error("No conditional order selected (selected_order=%s)", selected_order)
        return
    
    # Validate parameters
    if stop_order_price <= 0:
        logging.error("Invalid stop order price: %s", stop_order_price)
        return
    
    if selected_order == "1" and condition_price <= 0:
        logging.error("Invalid condition price for CO1: %s", condition_price)
        return
    
    if selected_order == "2" and (condition_price1 <= 0 or condition_price2 <= 0):
        logging.error("Invalid condition prices for CO2: %s, %s", condition_price1, condition_price2)
        return
    
    chartTime = await get_first_chart_time(timeFrame, outsideRth)
    
    # Subscribe to market data once before the loop (reuse the same subscription)
    ticker = None
    try:
        ticker = connection.ib.reqMktData(ibContract, '', False, False)
        await asyncio.sleep(0.5)  # Wait for initial price update
        logging.info("Conditional Order: Started monitoring market price. Will continuously check conditions until they are met.")
    except Exception as e:
        logging.error(f"Error subscribing to market data: {e}")
    
    while True:
        logging.info("conditional_order loop is running..")
        
        # Get current price
        try:
            # Use existing market data subscription
            # Ticker object from ib_insync uses marketPrice() method, not lastPrice attribute
            if ticker:
                try:
                    # Try marketPrice() method first
                    price = ticker.marketPrice()
                    if price is not None and price != 0:
                        current_price = float(price)
                    else:
                        # Try close price as fallback
                        price = ticker.close if hasattr(ticker, 'close') else None
                        if price is not None and price != 0:
                            current_price = float(price)
                        else:
                            raise ValueError("Ticker price not available")
                except (AttributeError, ValueError) as e:
                    # Fallback to historical data if market data not available
                    logging.debug(f"Ticker price not available ({e}), using historical data")
                    histData = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                    if histData and len(histData) > 0:
                        current_price = float(histData[len(histData)-1].get('close', 0))
                    else:
                        logging.info("No price data available, retrying...")
                        await asyncio.sleep(1)
                        continue
            else:
                # Fallback to historical data if ticker not available
                histData = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                if histData and len(histData) > 0:
                    current_price = float(histData[len(histData)-1].get('close', 0))
                else:
                    logging.info("No price data available, retrying...")
                    await asyncio.sleep(1)
                    continue
            
            current_price = round(current_price, Config.roundVal)
            logging.info(f"Current price: {current_price}")
            
        except Exception as e:
            logging.error(f"Error getting current price: {e}")
            await asyncio.sleep(1)
            continue
        
        # Check conditions
        conditions_met = False
        
        if selected_order == "1":
            # Conditional Order 1: Single condition
            # If condition is "Below": price must drop TO (reach) the condition_price
            # If condition is "Above": price must rise TO (reach) the condition_price
            if condition == "Below":
                if current_price <= condition_price:
                    conditions_met = True
                    logging.info(f"CO1 condition met: price {current_price} dropped to/below condition_price {condition_price}")
                else:
                    logging.info(f"CO1 condition is not satisfying. Current price {current_price} has not reached condition_price {condition_price} yet. Will check again after 1 second.")
            elif condition == "Above":
                if current_price >= condition_price:
                    conditions_met = True
                    logging.info(f"CO1 condition met: price {current_price} rose to/above condition_price {condition_price}")
                else:
                    logging.info(f"CO1 condition is not satisfying. Current price {current_price} has not reached condition_price {condition_price} yet. Will check again after 1 second.")
        
        elif selected_order == "2":
            # Conditional Order 2: Either condition can trigger (OR logic)
            # If condition1 is "Below" and price drops to price1 → trigger
            # OR if condition2 is "Above" and price rises to price2 → trigger
            cond1_met = False
            cond2_met = False
            
            # Check condition 1
            if condition1 == "Below":
                if current_price <= condition_price1:
                    cond1_met = True
                    logging.info(f"CO2 condition 1 met: price {current_price} dropped to/below condition_price1 {condition_price1}")
                else:
                    logging.info(f"CO2 condition 1 is not satisfying. Current price {current_price} has not reached condition_price1 {condition_price1} yet.")
            elif condition1 == "Above":
                if current_price >= condition_price1:
                    cond1_met = True
                    logging.info(f"CO2 condition 1 met: price {current_price} rose to/above condition_price1 {condition_price1}")
                else:
                    logging.info(f"CO2 condition 1 is not satisfying. Current price {current_price} has not reached condition_price1 {condition_price1} yet.")
            
            # Check condition 2
            if condition2 == "Below":
                if current_price <= condition_price2:
                    cond2_met = True
                    logging.info(f"CO2 condition 2 met: price {current_price} dropped to/below condition_price2 {condition_price2}")
                else:
                    logging.info(f"CO2 condition 2 is not satisfying. Current price {current_price} has not reached condition_price2 {condition_price2} yet.")
            elif condition2 == "Above":
                if current_price >= condition_price2:
                    cond2_met = True
                    logging.info(f"CO2 condition 2 met: price {current_price} rose to/above condition_price2 {condition_price2}")
                else:
                    logging.info(f"CO2 condition 2 is not satisfying. Current price {current_price} has not reached condition_price2 {condition_price2} yet.")
            
            # Either condition can trigger (OR logic)
            if cond1_met or cond2_met:
                conditions_met = True
                logging.info(f"CO2 condition met (OR logic): cond1={cond1_met}, cond2={cond2_met}, ready to place order")
        
        # If conditions are met, place the Stop Order using Custom entry logic
        if conditions_met:
            logging.info(f"Conditions met! Placing Stop Order at {stop_order_price} using Custom entry logic")
            
            # Use the same logic as Custom entry (manual_stop_order)
            # stop_order_price is the entry trigger price (like entry_price in Custom)
            entry_price = stop_order_price
            contract = ibContract
            # buySellType is already a parameter, use it directly
            
            # Calculate actual_entry_price (entry ± 0.01 for bracket orders)
            if buySellType == 'BUY':
                actual_entry_price = round(entry_price - 0.01, Config.roundVal)
            else:  # SELL
                actual_entry_price = round(entry_price + 0.01, Config.roundVal)
            
            # Calculate stop loss and stop size using same logic as Custom entry
            if stopLoss == Config.stopLoss[1]:  # 'Custom'
                custom_stop = _to_float(slValue, 0)
                if custom_stop == 0:
                    logging.error("Custom stop loss requires a valid slValue for Conditional Order")
                    return
                
                # Validate custom stop position relative to entry
                if buySellType == 'BUY':
                    if custom_stop >= entry_price:
                        logging.error("Custom stop loss (%s) must be below entry price (%s) for BUY orders", custom_stop, entry_price)
                        return
                else:  # SELL
                    if custom_stop <= entry_price:
                        logging.error("Custom stop loss (%s) must be above entry price (%s) for SELL orders", custom_stop, entry_price)
                        return
                
                # Calculate stop_size: stop_size = |entry_price - custom_stop| (no buffer)
                stop_size = abs(entry_price - custom_stop)
                stop_size = round(stop_size, Config.roundVal)
                if stop_size == 0 or math.isnan(stop_size):
                    logging.error("Stop size invalid (%s) for custom stop loss %s stop order %s", stop_size, custom_stop, symbol)
                    return
                logging.info("Conditional Order Custom stop loss: entry=%s, custom_stop=%s, stop_size=%s (|entry - custom_stop|)", entry_price, custom_stop, stop_size)
                
                # For Custom stop loss: stop_loss_price = custom_stop
                stop_loss_price = round(custom_stop, Config.roundVal)
                tp_base_price = entry_price  # Use entry_price for TP calculation
            else:
                # For other stop loss types (EntryBar, HOD, LOD, etc.), use _calculate_manual_stop_loss
                try:
                    raw_stop_loss_price, calculated_stop_size = _calculate_manual_stop_loss(
                        connection, contract, entry_price, stopLoss, buySellType, timeFrame, slValue
                    )
                    if calculated_stop_size and calculated_stop_size > 0:
                        stop_size = calculated_stop_size
                    else:
                        stop_size = abs(entry_price - raw_stop_loss_price)
                except Exception as e:
                    logging.error("Error calculating stop loss for Conditional Order: %s", e)
                    return
                
                # For EntryBar stop loss: use raw_stop_loss_price directly (bar's high/low, no buffer)
                # For other non-Custom stop loss types: calculate stop_loss_price from actual_entry_price
                if stopLoss == Config.stopLoss[0]:  # EntryBar
                    stop_loss_price = round(raw_stop_loss_price, Config.roundVal)
                elif stopLoss != Config.stopLoss[1]:  # Not 'Custom' and not EntryBar
                    if buySellType == 'BUY':
                        stop_loss_price = actual_entry_price - stop_size
                    else:
                        stop_loss_price = actual_entry_price + stop_size
                    stop_loss_price = round(stop_loss_price, Config.roundVal)
                tp_base_price = actual_entry_price
            
            if stop_size == 0 or math.isnan(stop_size):
                logging.error("Stop size invalid (%s) for Conditional Order %s", stop_size, symbol)
                return
            
            # Calculate quantity using same logic as Custom entry
            risk_amount = _to_float(risk, 0)
            if risk_amount is None or math.isnan(risk_amount) or risk_amount <= 0:
                logging.error("Invalid risk amount for Conditional Order: risk='%s' (type: %s), risk_amount=%s. Risk must be a positive number.", 
                            risk, type(risk).__name__, risk_amount)
                return
            
            if stopLoss == Config.stopLoss[1]:  # 'Custom'
                qty = risk_amount / stop_size
                qty = int(math.ceil(qty))  # Round UP
                if qty <= 0:
                    qty = 1
                logging.info("Conditional Order Custom stop loss quantity: risk=%s, stop_size=%s, quantity=%s (rounded up)", risk_amount, stop_size, qty)
            else:
                qty = _calculate_manual_quantity(actual_entry_price, stop_loss_price, risk_amount)
            
            if quantity > 0:
                qty = quantity  # Use provided quantity if specified
            
            # Calculate take profit price (same as Custom entry)
            multiplier_map = {
                Config.takeProfit[0]: 1,    # 1:1
                Config.takeProfit[1]: 1.5,  # 1.5:1
                Config.takeProfit[2]: 2,    # 2:1
                Config.takeProfit[3]: 2.5,  # 2.5:1
            }
            # Add 3:1 if it exists (index 4)
            if len(Config.takeProfit) > 4:
                multiplier_map[Config.takeProfit[4]] = 3  # 3:1
            multiplier = multiplier_map.get(profit, 1)
            tp_offset = stop_size * multiplier
            
            # For Custom and EntryBar stop loss: use entry_price (not actual_entry_price) for TP calculation
            # This ensures consistency with stop_size calculation which uses entry_price
            if stopLoss == Config.stopLoss[1]:  # 'Custom'
                tp_base_price = entry_price
            elif stopLoss == Config.stopLoss[0]:  # 'EntryBar'
                tp_base_price = entry_price
            else:
                tp_base_price = actual_entry_price
            
            if buySellType == 'BUY':
                tp_price = round(tp_base_price + tp_offset, Config.roundVal)
            else:  # SELL
                tp_price = round(tp_base_price - tp_offset, Config.roundVal)
            
            # Determine if extended hours
            is_extended, session = _is_extended_outside_rth(outsideRth)
            
            # Get historical data
            histData = connection.getHistoricalChartData(contract, timeFrame, chartTime)
            if not histData or len(histData) == 0:
                histData = {
                    'high': entry_price,
                    'low': entry_price - stop_size if buySellType == 'BUY' else entry_price + stop_size,
                    'close': current_price,
                    'open': current_price,
                    'dateTime': datetime.datetime.now()
                }
            
            # Generate unique order IDs for bracket orders
            parent_order_id = connection.get_next_order_id()
            tp_order_id = connection.get_next_order_id()
            sl_order_id = connection.get_next_order_id()
            
            logging.info(f"Generated unique order IDs for Conditional Order bracket: entry={parent_order_id}, tp={tp_order_id}, sl={sl_order_id}")
            
            if is_extended:
                # Extended hours: Use STOP LIMIT order (STP LMT) for entry
                order_type = "STP LMT"
                entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
                
                if buySellType == 'BUY':
                    limit_price = round(entry_price + entry_limit_offset, Config.roundVal)
                else:  # SELL
                    limit_price = round(entry_price - entry_limit_offset, Config.roundVal)
                
                logging.info(f"Conditional Order Extended hours {buySellType}: Stop={entry_price}, Limit={limit_price} (stop ± 0.5×stop_size={entry_limit_offset}), stop_size={stop_size}")
                
                entry_order = Order(
                    orderId=parent_order_id,
                    orderType=order_type,
                    action=buySellType,
                    totalQuantity=qty,
                    tif=tif,
                    auxPrice=entry_price,
                    lmtPrice=limit_price,
                    transmit=False,
                    outsideRth=True
                )
            else:
                # Regular hours: Use bracket orders (Entry STP, TP LMT, SL STP) like Custom entry
                order_type = "STP"
                logging.info(f"Conditional Order RTH {buySellType}: Entry Stop={entry_price}, TP={tp_price}, SL={stop_loss_price}, stop_size={stop_size}")
                
                # Entry order (STP)
                entry_order = Order(
                    orderId=parent_order_id,
                    orderType=order_type,
                    action=buySellType,
                    totalQuantity=qty,
                    tif=tif,
                    auxPrice=entry_price,
                    transmit=False
                )
                
                # Take Profit order (LMT)
                tp_order = Order(
                    orderId=tp_order_id,
                    orderType="LMT",
                    action="SELL" if buySellType == "BUY" else "BUY",
                    totalQuantity=qty,
                    lmtPrice=tp_price,
                    parentId=parent_order_id,
                    transmit=False
                )
                
                # Stop Loss order (STP)
                sl_order = Order(
                    orderId=sl_order_id,
                    orderType="STP",
                    action="SELL" if buySellType == "BUY" else "BUY",
                    totalQuantity=qty,
                    auxPrice=stop_loss_price,
                    parentId=parent_order_id,
                    transmit=True  # Transmit entire bracket
                )
            
            # Place entry order
            entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
            logging.info(f"Conditional Order entry placed: {entry_response}")
            
            # For RTH: Place TP and SL orders as bracket
            if not is_extended:
                tp_response = connection.placeTrade(contract=contract, order=tp_order, outsideRth=outsideRth)
                logging.info(f"Conditional Order TP placed: {tp_response}")
                
                sl_response = connection.placeTrade(contract=contract, order=sl_order, outsideRth=outsideRth)
                logging.info(f"Conditional Order SL placed: {sl_response}")
            
            # Store order data
            StatusUpdate(entry_response, 'Entry', contract, order_type, buySellType, qty, histData, entry_price, symbol,
                        timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                        breakEven, outsideRth, False, entry_points)
            
            # Store stop_size for use in sendStopLoss (if needed for extended hours)
            if int(entry_response.order.orderId) in Config.orderStatusData:
                Config.orderStatusData[int(entry_response.order.orderId)]['stopSize'] = stop_size
                Config.orderStatusData[int(entry_response.order.orderId)]['stopLossPrice'] = stop_loss_price
                logging.info(f"Stored stop_size={stop_size}, stop_loss_price={stop_loss_price} in orderStatusData for Conditional Order {entry_response.order.orderId}")
            
            logging.info("Conditional Order entry task done %s", symbol)
            break
        
        # Conditions not met yet, continue monitoring
        await asyncio.sleep(1)
    
    # Cancel market data subscription
    try:
        connection.ib.cancelMktData(ibContract)
    except:
        pass