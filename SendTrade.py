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
        logging.info("Custom stop loss for %s: entry=%s, stop_loss=%s", contract, entry_price, stop_loss_price)
    
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
        logging.info("RB stop loss for %s: entry=%s, recent_bar=%s, stop_loss=%s",
                     contract, entry_price, hist_data, stop_loss_price)
    
    # EntryBar, HOD, LOD - use existing logic
    else:
        hist_data = _get_latest_hist_bar(connection, contract, time_frame)
        if hist_data is None:
            raise ValueError("Cannot calculate stop loss: No historical data available")
        
        if buy_sell_type == 'BUY':
            if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                stop_loss_price = float(hist_data['high'])
            elif stop_loss_type == Config.stopLoss[2]:  # HOD
                recent_bar_data = connection.getHistoricalChartDataForEntry(contract, time_frame, chart_time)
                high_value = 0
                for data in range(0, len(recent_bar_data)):
                    if high_value == 0 or high_value < recent_bar_data.get(data)['high']:
                        high_value = recent_bar_data.get(data)['high']
                stop_loss_price = float(high_value)
            elif stop_loss_type == Config.stopLoss[3]:  # LOD
                recent_bar_data = connection.getHistoricalChartDataForEntry(contract, time_frame, chart_time)
                low_value = 0
                for data in range(0, len(recent_bar_data)):
                    if low_value == 0 or low_value > recent_bar_data.get(data)['low']:
                        low_value = recent_bar_data.get(data)['low']
                stop_loss_price = float(low_value)
            else:
                stop_loss_price = float(hist_data['high'])
        else:  # SELL
            if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                stop_loss_price = float(hist_data['low'])
            elif stop_loss_type == Config.stopLoss[2]:  # HOD
                recent_bar_data = connection.getHistoricalChartDataForEntry(contract, time_frame, chart_time)
                high_value = 0
                for data in range(0, len(recent_bar_data)):
                    if high_value == 0 or high_value < recent_bar_data.get(data)['high']:
                        high_value = recent_bar_data.get(data)['high']
                stop_loss_price = float(high_value)
            elif stop_loss_type == Config.stopLoss[3]:  # LOD
                recent_bar_data = connection.getHistoricalChartDataForEntry(contract, time_frame, chart_time)
                low_value = 0
                for data in range(0, len(recent_bar_data)):
                    if low_value == 0 or low_value > recent_bar_data.get(data)['low']:
                        low_value = recent_bar_data.get(data)['low']
                stop_loss_price = float(low_value)
            else:
                stop_loss_price = float(hist_data['low'])
        logging.info("Stop loss for %s: entry=%s, type=%s, stop_loss=%s",
                     contract, entry_price, stop_loss_type, stop_loss_price)
    
    stop_loss_price = round(stop_loss_price, Config.roundVal)
    return stop_loss_price

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
            if (len(histData) == 0):
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

        if (atrCheck(histData, ibContract, connection, atrPercentage)):
            await  asyncio.sleep(1)
            continue
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
            if (len(histData) == 0):
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

        # During overnight, skip ATR check as it may be too restrictive with limited liquidity
        if outsideRth:
            session = _get_current_session()
            if session == 'OVERNIGHT':
                logging.info("OVERNIGHT session: Skipping ATR check to allow trade execution")
            else:
                # Pre-market/After-hours: still check ATR
                if (atrCheck(histData, ibContract, connection, atrPercentage)):
                    await  asyncio.sleep(1)
                    continue
        else:
            # Regular hours: check ATR
            if (atrCheck(histData, ibContract, connection, atrPercentage)):
                await  asyncio.sleep(1)
                continue
        logging.info("RBRR Trade action found for market order %s for %s contract ", tradeType, ibContract)
        # connection.cancelTickData(ibContract)

        if (quantity == ''):
            quantity = 0

        if int(quantity) == 0:
            # Calculate entry price (aux_price) first
            aux_price = 0
            if buySellType == 'BUY':
                aux_price = histData['high'] - float(entry_points)
            else:
                aux_price = histData['low'] + float(entry_points)
            
            # Calculate stop size first
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
        aux_price = 0
        if buySellType == 'BUY':
            aux_price = histData['high'] - float(entry_points)
            logging.info("RBRR auxprice high for %s %s ",symbol,aux_price)
        else:
            aux_price = histData['low'] + float(entry_points)
            logging.info("RBRR auxprice low for %s %s ", symbol, aux_price)

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
        
        if is_extended:
            # Calculate stop size for entry order
            # If ATR stop loss: use ATR × percentage
            # Otherwise: use (bar_high - bar_low) + 0.02
            if stopLoss in Config.atrStopLossMap:
                stop_size = _get_atr_stop_offset(connection, ibContract, stopLoss)
                if stop_size is None or stop_size <= 0:
                    # Fallback to bar range if ATR unavailable
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    logging.warning(f"RB Extended hours: ATR unavailable, using bar range stop_size={stop_size}")
                else:
                    logging.info(f"RB Extended hours: Using ATR stop_size={stop_size}")
            else:
                # Non-ATR: use bar range
                stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                logging.info(f"RB Extended hours: Using bar range stop_size={stop_size}")
            
            stop_size = round(stop_size, Config.roundVal)
            
            # Limit price = stop_price ± 0.5 × stop_size
            entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
            
            order_type = "STP LMT"
            if tradeType == 'BUY':
                # For BUY: Limit = Stop + 0.5 × stop_size
                limit_price = aux_price + entry_limit_offset
            else:
                # For SELL: Limit = Stop - 0.5 × stop_size
                limit_price = aux_price - entry_limit_offset
            
            limit_price = round(limit_price, Config.roundVal)
            logging.info(f"RB Extended hours {tradeType}: Stop={aux_price}, Limit={limit_price} (stop ± 0.5×stop_size={entry_limit_offset}), stop_size={stop_size}")
            
            response = connection.placeTrade(contract=ibContract,
                                           order=Order(orderType=order_type, action=tradeType, totalQuantity=quantity,
                                                       tif=tif, auxPrice=aux_price, lmtPrice=limit_price), outsideRth=outsideRth)
        else:
            # Regular hours or overnight: use regular STP order (will be converted to LMT in overnight by placeTrade)
            response = connection.placeTrade(contract=ibContract,
                                           order=Order(orderType=order_type, action=tradeType, totalQuantity=quantity,
                                                       tif=tif, auxPrice=aux_price), outsideRth=outsideRth)
        StatusUpdate(response, 'Entry', ibContract, order_type, tradeType, quantity, histData, lastPrice, symbol,
                     timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue, breakEven,
                     outsideRth)
        # Config.rbbb_dict.update({key:response.order})
        # sendEntryTrade(connection, ibContract, tradeType, quantity, histData, lastPrice, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage,slValue,breakEven,outsideRth)
        logging.info("rb_and_rbb entry order done %s ",symbol)
        # Only RBB (entryTradeType[4]) should continuously update stop price
        # RB (entryTradeType[3]) should have fixed stop price - no updates
        logging.info("RB/RBB check: barType=%s, entryTradeType[3]=%s (RB), entryTradeType[4]=%s (RBB)", 
                    barType, Config.entryTradeType[3], Config.entryTradeType[4])
        if barType == Config.entryTradeType[4]:
            logging.info("RBB detected: Starting rbb_loop_run for continuous stop price updates")
            await rbb_loop_run(connection,key,response.order)
        else:
            logging.info("RB detected (barType=%s): Fixed stop price, NOT calling rbb_loop_run", barType)
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
                    raw_stop_loss_price = _calculate_manual_stop_loss(
                        connection, contract, entry_price, stopLoss, buySellType, timeFrame, slValue
                    )
                except ValueError as err:
                    logging.error("Error calculating stop loss for manual limit order: %s", err)
                    return
                
                stop_size = abs(entry_price - raw_stop_loss_price)
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
            Config.takeProfit[3]: 3,    # 3:1
        }
        # Note: '2.5:1' is at Config.takeProfit[3], already included in multiplier_map
        
        multiplier = multiplier_map.get(profit, 1)  # Default to 1:1 if not found
        tp_offset = stop_size * multiplier
        
        if buySellType == 'BUY':
            tp_price = entry_price + tp_offset
        else:  # SELL
            tp_price = entry_price - tp_offset
        
        # Check if regular market hours (not extended hours)
        is_extended, session = _is_extended_outside_rth(outsideRth)
        
        tp_price = round(tp_price, Config.roundVal)
        
        logging.info("Manual limit order calculation: symbol=%s, entry=%s, stop_size=%s, tp=%s, stop_loss=%s, risk=%s, quantity=%s, session=%s, is_extended=%s",
                     symbol, entry_price, stop_size, tp_price, stop_loss_price, risk_amount, qty, session, is_extended)
        
        # Generate entry order ID
        parent_order_id = connection.get_next_order_id()
        
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
                             breakEven, outsideRth)
                
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
        histData = _get_latest_hist_bar(connection, contract, timeFrame)
        if histData is None:
            logging.error("Unable to fetch historical data for manual stop order %s %s", symbol, timeFrame)
            return
        
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
            # For Custom stop loss, calculate stop_size directly (similar to ATR logic)
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
                # Calculate stop_size directly (similar to ATR): stop_size = |entry - custom_stop|
                stop_size = abs(entry_price - custom_stop)
                if stop_size == 0 or math.isnan(stop_size):
                    logging.error("Stop size invalid (%s) for custom stop loss %s stop order %s", stop_size, custom_stop, symbol)
                    return
                logging.info("Custom stop loss for %s stop order: entry=%s, custom_stop=%s, stop_size=%s", symbol, entry_price, custom_stop, stop_size)
                # Calculate stop_loss_price from actual_entry_price (similar to ATR logic)
                if buySellType == 'BUY':
                    stop_loss_price = actual_entry_price - stop_size
                else:
                    stop_loss_price = actual_entry_price + stop_size
                stop_loss_price = round(stop_loss_price, Config.roundVal)
            else:
                # For other stop loss types (EntryBar, HOD, LOD, etc.), use existing logic
                try:
                    raw_stop_loss_price = _calculate_manual_stop_loss(
                        connection, contract, entry_price, stopLoss, buySellType, timeFrame, slValue
                    )
                except ValueError as err:
                    logging.error("Error calculating stop loss for manual stop order: %s", err)
                    return
                
                stop_size = abs(entry_price - raw_stop_loss_price)
                if stop_size == 0 or math.isnan(stop_size):
                    logging.error("Stop size invalid (%s) for manual stop order %s", stop_size, symbol)
                    return
                if buySellType == 'BUY':
                    stop_loss_price = actual_entry_price - stop_size
                else:
                    stop_loss_price = actual_entry_price + stop_size
                stop_loss_price = round(stop_loss_price, Config.roundVal)

        risk_amount = _to_float(risk, 0)
        if risk_amount is None or math.isnan(risk_amount) or risk_amount <= 0:
            logging.error("Invalid risk amount for manual stop order: %s", risk)
            return
        
        qty = _calculate_manual_quantity(actual_entry_price, stop_loss_price, risk_amount)
        
        # Calculate take profit price based on stop size and profit multiplier
        # TP = actual_entry + (stop_size × multiplier) for BUY
        # TP = actual_entry - (stop_size × multiplier) for SELL
        multiplier_map = {
            Config.takeProfit[0]: 1,    # 1:1
            Config.takeProfit[1]: 1.5,  # 1.5:1
            Config.takeProfit[2]: 2,    # 2:1
            Config.takeProfit[3]: 3,    # 3:1
        }
        # Note: '2.5:1' is at Config.takeProfit[3], already included in multiplier_map
        
        multiplier = multiplier_map.get(profit, 1)  # Default to 1:1 if not found
        tp_offset = stop_size * multiplier
        
        if buySellType == 'BUY':
            tp_price = actual_entry_price + tp_offset
        else:  # SELL
            tp_price = actual_entry_price - tp_offset
        
        # Check if regular market hours (not extended hours)
        is_extended, session = _is_extended_outside_rth(outsideRth)
        
        tp_price = round(tp_price, Config.roundVal)
        
        logging.info("Manual stop order calculation: symbol=%s, trigger=%s, actual_entry=%s, stop_size=%s, tp=%s, stop_loss=%s, risk=%s, quantity=%s, session=%s, is_extended=%s",
                     symbol, entry_price, actual_entry_price, stop_size, tp_price, stop_loss_price, risk_amount, qty, session, is_extended)
        
        # Generate entry order ID
        parent_order_id = connection.get_next_order_id()
        
        if is_extended:
            # Extended hours (premarket/after-hours) for Stop Order:
            # ALL Stop Orders in extended hours should use STP LMT (Stop-Limit Order)
            # Stop price = entry_price, Limit price = entry_price ± 0.5 × stop_size
            # Stop Loss: STP LMT with stop = entry ± stop_size, limit = entry ± 2×stop_size
            # Take Profit: LMT (same as others)
            
            # For ALL stop orders in extended hours: Entry should be STP LMT
            # Validate stop_size before calculating limit offset
            if stop_size <= 0 or math.isnan(stop_size):
                logging.error(f"Invalid stop_size={stop_size} for extended hours Stop Order: symbol={symbol}, stopLoss={stopLoss}, entry_price={entry_price}")
                return
            
            entry_limit_offset = round(stop_size * 0.5, Config.roundVal)
            # Ensure minimum limit offset to allow order execution
            min_limit_offset = 0.01  # Minimum $0.01 offset
            if entry_limit_offset < min_limit_offset:
                entry_limit_offset = min_limit_offset
                logging.warning(f"Stop size too small ({stop_size}), using minimum limit offset={min_limit_offset} for {symbol}")
            
            if buySellType == 'BUY':
                limit_price = entry_price + entry_limit_offset
            else:  # SELL
                limit_price = entry_price - entry_limit_offset
            limit_price = round(limit_price, Config.roundVal)
            
            logging.info(f"Extended hours Stop Order: Entry STP LMT - Stop={entry_price}, Limit={limit_price} (stop ± 0.5×stop_size={entry_limit_offset}), stop_size={stop_size}, stopLoss={stopLoss}, symbol={symbol}")
            
            entry_order = Order(
                orderId=parent_order_id,
                orderType="STP LMT",
                action=buySellType,
                totalQuantity=qty,
                auxPrice=entry_price,  # Stop trigger price
                lmtPrice=limit_price,  # Limit price
                tif=tif,
                transmit=True  # Transmit immediately - TP/SL will be sent after fill
            )
            
            # Place ONLY entry order - TP and SL will be sent after fill via sendTpAndSl()
            entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
            StatusUpdate(entry_response, 'Entry', contract, 'STP LMT', buySellType, qty, histData, entry_price, symbol,
                         timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                         breakEven, outsideRth)
            
            # Store stop_size in orderStatusData for use in sendStopLoss
            if int(entry_response.order.orderId) in Config.orderStatusData:
                Config.orderStatusData[int(entry_response.order.orderId)]['stopSize'] = stop_size
                logging.info(f"Stored stop_size={stop_size} in orderStatusData for order {entry_response.order.orderId}")
            
            logging.info("Extended hours: Entry STP LMT order placed (all stop orders use stop-limit in extended hours). TP and SL will be sent automatically after entry fills.")
        else:
            # Regular market hours: Send bracket orders (Entry STP, TP LMT, SL STP)
            # Generate separate unique order IDs for each order in the bracket
            tp_order_id = connection.get_next_order_id()
            sl_order_id = connection.get_next_order_id()
            
            logging.info("Generated unique order IDs for bracket: entry=%s, tp=%s, sl=%s", 
                        parent_order_id, tp_order_id, sl_order_id)
            
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
            entry_response = connection.placeTrade(contract=contract, order=entry_order, outsideRth=outsideRth)
            StatusUpdate(entry_response, 'Entry', contract, 'STP', buySellType, qty, histData, entry_price, symbol,
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
            
            logging.info("Regular market: Bracket orders placed for %s: trigger=%s, tp=%s, sl=%s, quantity=%s", 
                         symbol, entry_price, tp_price, stop_loss_price, qty)
    except Exception as e:
        logging.error("error in manual stop order %s", e)
        traceback.print_exc()

async def rbb_loop_run(connection,key,entry_order):
    order = entry_order
    while True:
        try:
            await asyncio.sleep(1)
            # for entry_key, entry_value in list(Config.rbbb_dict.items()):
            old_order = Config.orderStatusData[order.orderId]
            logging.info("old order rbb %s ",old_order)
            if old_order != None:
                sleep_time = getTimeInterval(old_order['timeFrame'], datetime.datetime.now())
                await asyncio.sleep(sleep_time)
            old_order = Config.orderStatusData[order.orderId]
            
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
                            stop_size, _, protection_offset = _calculate_stop_limit_offsets(histData)
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
                            
                            logging.info(f"RBBB Protection order: {protection_action} STP LMT, Stop={stop_price}, Limit={limit_price}, Stop size={stop_size}, Entry offset={entry_offset}, Protection offset={protection_offset}")
                            
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

                newChartTime = getRecentChartTime(old_order['timeFrame'])
                # newChartTime = newChartTime.replace(second=0,microsecond=0)
                histData = connection.rbb_entry_historical_data(old_order['contract'], old_order['timeFrame'], newChartTime)
                if (len(histData) == 0):
                    logging.info("Loop RBB Chart Data is Not Comming for %s contract  and for %s time", old_order['contract'],
                                 newChartTime)
                    await asyncio.sleep(1)
                    continue
                else:
                    # Get the original bar's datetime from initial entry
                    original_histData = old_order.get('histData', {})
                    if not original_histData:
                        # No original data, skip update
                        logging.info("RBBB: No original histData found, skipping update")
                        await asyncio.sleep(1)
                        continue
                    
                    original_bar_datetime = original_histData.get('dateTime')
                    new_bar_datetime = histData.get('dateTime')
                    
                    # Check if a new bar has closed (different datetime)
                    if original_bar_datetime and new_bar_datetime:
                        if original_bar_datetime == new_bar_datetime:
                            # Same bar, no update needed
                            logging.info("RBBB: Same bar (datetime=%s), skipping update", new_bar_datetime)
                            await asyncio.sleep(1)
                            continue
                        else:
                            # New bar closed, update to new bar's high/low
                            logging.info("RBBB: New bar closed (original=%s, new=%s), updating stop price", original_bar_datetime, new_bar_datetime)
                    else:
                        # Can't compare datetimes, skip update
                        logging.info("RBBB: Cannot compare bar datetimes, skipping update")
                        await asyncio.sleep(1)
                        continue
                    
                    # Calculate new stop price based on new bar's high/low
                    aux_price = 0
                    if old_order['userBuySell'] == 'BUY':
                        aux_price = histData['high'] + 0.01
                        logging.info("RBRR auxprice high for %s (new bar high=%s)", aux_price, histData['high'])
                    else:
                        aux_price = histData['low'] - 0.01
                        logging.info("RBRR auxprice low for %s (new bar low=%s)", aux_price, histData['low'])
                    
                    logging.info("RBBB going to update stp price for  newprice %s old_order %s", aux_price,order)
                    logging.info(f"rb aux limit price befor 0.01 plus minus aux {aux_price}")
                    order.auxPrice = aux_price
                    old_orderId=order.orderId
                    
                    # For Pre-Market/After-Hours: Use Stop Limit orders
                    is_extended, _ = _is_extended_outside_rth(old_order.get('outsideRth', False))
                    if is_extended:
                        # Calculate limit price for stop limit order using 50% entry offset
                        _, entry_offset, _ = _calculate_stop_limit_offsets(histData)
                        
                        if old_order['userBuySell'] == 'BUY':
                            limit_price = aux_price + entry_offset
                        else:
                            limit_price = aux_price - entry_offset
                        
                        limit_price = round(limit_price, Config.roundVal)
                        logging.info(f"RBBB Update: Pre-market/After-hours STP LMT, Stop={aux_price}, Limit={limit_price}")
                        new_order = Order(orderType="STP LMT", action=order.action, totalQuantity=order.totalQuantity, 
                                        tif='DAY', auxPrice=aux_price, lmtPrice=limit_price)
                    else:
                        new_order = Order(orderType="STP", action=order.action, totalQuantity=order.totalQuantity,  
                                        tif='DAY', auxPrice=aux_price)
                    
                    connection.cancelTrade(order)
                    response = connection.placeTrade(contract=old_order['contract'], order=new_order, 
                                                   outsideRth=old_order.get('outsideRth', False))
                    logging.info("RBBB  response of updating stp order %s ",response)
                    order =response.order
                    if(Config.orderStatusData.get(old_orderId) != None ):
                        d=Config.orderStatusData.get(old_orderId)
                        d['histData'] = histData
                        d['orderId']= int(response.order.orderId)
                        d['status']= response.orderStatus.status
                        d['lastPrice'] = round(aux_price, Config.roundVal)
                        d['entryData'] = Config.orderStatusData.get(int(entry_order.orderId))
                        Config.orderStatusData.update({ order.orderId:d })
            else:
                break

        except Exception as e:
            traceback.format_exc()
            logging.info("error in rbb aucprice updation %s ", e)
            break
        await asyncio.sleep(1)
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
    try:
        logging.info("pull_back_PBe1 mkt pbe1 trade is sending. %s %s",Config.tradingTime,symbol)
        ibContract = getContract(symbol, None)
        # priceObj = subscribePrice(ibContract, connection)
        key = (symbol + str(datetime.datetime.now()))
        logging.info("Key for this trade is- %s ", key)
        chartTime = await get_first_chart_time(timeFrame , outsideRth)
        while True:
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
            logging.info("RecentBar for PullBack E1",complete_bar_data)
            if(len(complete_bar_data) ==0):
                logging.info("last 1 record not found we will try after 1 sec.")
                await  asyncio.sleep(1)
                continue

            # logging.info("RecentBar for PullBack E1 %s ",recentBarData)
            tradeType = ""
            # last_candel =   recentBarData[len( recentBarData)-1]

            last_candel = complete_bar_data[len(complete_bar_data)-1]
            logging.info("last candel found for for PullBack E1 %s ", last_candel)
            if (last_candel == None or len(last_candel) == 0 ):
                logging.info("Last Price Not Found for %s contract for mkt order", ibContract)
                await  asyncio.sleep(1)
                continue
            lastPrice = last_candel['close']
            lastPrice = round(lastPrice, Config.roundVal)
            logging.info("Price found for market order %s for %s contract ", lastPrice, ibContract)
            histData = last_candel

            # if ((len(recentBarData) - 2) < 1):
            #     logging.info("pull_back_PBe1- minimum three row required for other process")
            #     await asyncio.sleep(1)
            #     continue

            # if (float(lastPrice) > float(last_candel['high'])):
            #     logging.info("pull_back_PBe1 Price Buy for market order last price  %s for High %s bar %s ", float(lastPrice), float(last_candel['high']), last_candel)
            #     tradeType = "BUY"
            # elif (float(lastPrice) < float(last_candel['low'])):
            #     logging.info("pull_back_PBe1 Price Sell for market order last price  %s for Low %s bar %s ", float(lastPrice), float(last_candel['low']), last_candel)
            #     tradeType = "SELL"
            # else:
            #     logging.info("pull_back_PBe1 Trade Type not found retrying with in %s second for %s contract prev candel %s prev price %s", 2, ibContract,last_candel,lastPrice)
            #     await asyncio.sleep(1)
            #     continue
            tradeType , row = pbe1_result(last_candel, complete_bar_data)


            # tradeType = 'BUY'
            if ((tradeType != 'BUY') and (tradeType != 'SELL')):
                logging.info("condition is not satisfying. we will get chart data again after 2 second.recent prev candel [ %s ] [ %s ]",last_candel,lastPrice)
                tradeType = ""
                await  asyncio.sleep(1)
                continue
            if buySellType != tradeType:
                logging.info("trade type not satisfy, User want %s trade, trade type is comming %s", buySellType, tradeType)
                logging.info("price is %s, high is %s, low is %s", lastPrice, last_candel['high'], last_candel['low'])
                await  asyncio.sleep(1)
                continue

            # if(atrCheck(histData, ibContract, connection, atrPercentage)):
            #     await  asyncio.sleep(1)
            #     continue
            logging.info("Trade action found for market order %s user side %s for %s contract ", tradeType, buySellType, ibContract)
            # connection.cancelTickData(ibContract)

            # candleData = connection.get_recent_close_price_data(ibContract, timeFrame, chartTime)
            # if( candleData == None or len(candleData) < 1):
            #     logging.info("candle data not found for %s", ibContract)
            #     await  asyncio.sleep(1)
            #     continue

            quantity = 0
            logging.info("before placing quantity is %s",quantity)
            if quantity == 0:
                # Calculate stop size first
                stop_size = _calculate_stop_size(connection, ibContract, lastPrice, stopLoss, buySellType, last_candel, timeFrame, chartTime, slValue)
                
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
                    logging.info(f"PBe1 quantity calculated: entry=%s, stop_size=%s, risk=%s, quantity=%s", 
                               lastPrice, stop_size, risk_amount, quantity)
            else:
                logging.info("user quantity")
            logging.info("Trade quantity found for market order %s for %s contract and candle data is %s and last bar data %s", quantity, ibContract,last_candel,last_candel)
            logging.info("everything found we are placing mkt trade")
            conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
            logging.info("main Entry Data , historical  data [%s] Recent Bar Data [%s] price is [ %s ] tradeType is [%s], TimeFrame [%s], configTime [%s] , quantity [%s], ibContract [%s]",Config.historicalData.get(key),last_candel,lastPrice,tradeType,timeFrame,conf_trading_time,quantity,ibContract)


            sendEntryTrade(connection, ibContract, tradeType, quantity, last_candel, lastPrice, symbol, timeFrame, profit, stopLoss, risk, tif, barType,buySellType,atrPercentage,slValue , breakEven,outsideRth)


            logging.info("pull_back_PBe1 entry task done %s",symbol)
            break
    except Exception as e:
        # traceback.print_exc()
        logging.info("e1 error  %s",e)
        await  asyncio.sleep(1)


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
        if (atrCheck(histData, ibContract, connection, atrPercentage)):
            await  asyncio.sleep(1)
            continue
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

        # Check if extended hours - use STP LMT for extended hours
        is_extended, session = _is_extended_outside_rth(outsideRth)
        order_type = "STP"
        limit_price = None
        
        if is_extended:
            # Calculate stop size and limit offsets for entry
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
        else:
            # Regular hours: use regular STP order
            response = connection.placeTrade(contract=ibContract,
                                           order=Order(orderType=order_type, action=tradeType, totalQuantity=quantity,
                                                     tif=tif, auxPrice=aux_price), outsideRth=outsideRth)
        StatusUpdate(response, 'Entry', ibContract, order_type, tradeType, quantity, histData, lastPrice, symbol,
                     timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                     breakEven,
                     outsideRth)
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
        if (atrCheck(histData, ibContract, connection, atrPercentage)):
            await  asyncio.sleep(1)
            continue
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
        if (atrCheck(histData, ibContract, connection, atrPercentage)):
            await  asyncio.sleep(1)
            continue
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
    logging.info("mkt trade is sending.")
    ibContract = getContract(symbol, None)
    currentDateTime = datetime.datetime.now()
    # priceObj = subscribePrice(ibContract, connection)
    key = (symbol + str(datetime.datetime.now()))
    logging.info("Key for this trade is- %s ", key)
    chartTime = await get_first_chart_time(timeFrame , outsideRth)
    while True:
        dtime = str(datetime.datetime.now().date()) + " "+Config.pull_back_PBe2_time
        if (datetime.datetime.now() < datetime.datetime.strptime(dtime, '%Y-%m-%d %H:%M:%S')):
            await asyncio.sleep(1)
            continue
        logging.info("send trade loop is running..")
        histData = None
        tradeType = ""
        histData = None

        # logging.info("now we will get last three record (firstly we will calculate time can we get data or not)")
        # sleepTime = getSleepTime(timeFrame,outsideRth)
        # logging.info("we cant fetch last three data we are going to sleep %s",sleepTime)
        # await asyncio.sleep(sleepTime)
        # chartTime = getRecentChartTime(timeFrame)
        # logging.info("we will get chart data for %s time", chartTime)
        recentBarData = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
        if(len(recentBarData) ==0):
            logging.info("last 3 record not found we will try after 2 sec.")
            await  asyncio.sleep(1)
            continue
        logging.info("RecentBar for PullBack E2 %s", recentBarData)

        lastPrice = 0
        #     it will check e1 condition
        histData = recentBarData[len(recentBarData)-1]

        tradeType = ""
        logging.info("e2 first two  bar for contract %s [ %s ] and low bar [ %s ] ",ibContract,recentBarData.get(0),recentBarData.get(1))
        if((len(recentBarData)) < 2 ):
            logging.info("pull_back_PBe2- minimum three row required for other process")
            await asyncio.sleep(1)
            continue


        continueLoop = False
        if(Config.pbe1_saved.get(key) == None):
            tradeType , row = pbe_result(buySellType,lastPrice, recentBarData)
            if tradeType == "":
                sleepTime = getSleepTime(timeFrame, outsideRth)
                if sleepTime == 0:
                    sleepTime = 1
                await asyncio.sleep(sleepTime)
                continue
            else:
                logging.info("pbe2 first condition found %s %s",tradeType,row)
                Config.pbe1_saved.update({key: row })
                sleepTime = getSleepTime(timeFrame,outsideRth)
                if sleepTime == 0:
                    sleepTime = 1
                await asyncio.sleep(sleepTime)
                continue
        else:
            tradeType, row = pbe_result(buySellType,lastPrice, recentBarData,True)
            if tradeType == "":
                sleepTime = getSleepTime(timeFrame, outsideRth)
                await asyncio.sleep(sleepTime)
                continue
            if (row['date'] == Config.pbe1_saved.get(key)['date'] ):
                logging.info(" in pbe2 first row and second row datetime is same so we will not execute new trade  [ %s ] old date [ %s ]",
                    row['date'] ,  Config.pbe1_saved.get(key)['date'] )
                sleepTime = getSleepTime(timeFrame,outsideRth)
                if sleepTime == 0:
                    sleepTime = 1
                await asyncio.sleep(sleepTime)
                continue
            else:
                logging.info("pbe2 second condition found %s %s", tradeType, row)
                histData = row
                # try:
                #     del Config.pbe1_saved[key]
                # except Exception as e:
                #     logging.error(f"error in delete key {traceback.format_exc()}")


        if ((tradeType != 'BUY') and (tradeType != 'SELL')):
            logging.info("condition is not satisfying. we will get chart data again after 2 second. last trade is [ %s ]",histData)
            tradeType = ""
            await  asyncio.sleep(1)
            continue

        quantity = 0
        aux_price = 0
        if quantity == 0:
            # Calculate entry price (aux_price) first
            if tradeType == "BUY":
                aux_price = histData['high']
            else:
                aux_price = histData['low']
            
            # Calculate stop size first
            stop_size = _calculate_stop_size(connection, ibContract, aux_price, stopLoss, buySellType, histData, timeFrame, chartTime, slValue)
            
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
                logging.info(f"PBe2 quantity calculated: entry=%s, stop_size=%s, risk=%s, quantity=%s", 
                           aux_price, stop_size, risk_amount, quantity)
        else:
            logging.info("user quantity")
        lastPrice = aux_price
        aux_price = round(aux_price, Config.roundVal)

        lastPrice = round(lastPrice, Config.roundVal)
        logging.info("Price found for market order %s for %s contract ", lastPrice, ibContract)
        # if buySellType != tradeType:
        #     logging.info("trade type not satisfy, User want %s trade, trade type is comming %s", buySellType, tradeType)
        #     if histData != None:
        #         logging.info("price is %s, high is %s, low is %s", lastPrice, histData['high'], histData['low'])
        #     await  asyncio.sleep(1)
        #     continue

        if (atrCheck(histData, ibContract, connection, atrPercentage)):
            await  asyncio.sleep(1)
            continue
        logging.info("Trade action found for market order %s for %s contract ", tradeType, ibContract)
        logging.info("Trade quantity found for market order %s for %s contract and candle data is %s and last bar data %s", quantity, ibContract, histData, histData)
        logging.info("everything found we are placing stp trade")
        conf_trading_time = accordingRthTradingTimeCalculate(outsideRth)
        logging.info("main Entry Data , historical  data [%s] Recent Bar Data [%s] price is [ %s ] tradeType is [%s], TimeFrame [%s], configTime [%s] , quantity [%s], ibContract [%s]",Config.historicalData.get(key),recentBarData,lastPrice,tradeType,timeFrame,conf_trading_time,quantity,ibContract)

        response = connection.placeTrade(contract=ibContract,
                                         order=Order(orderType="STP", action=tradeType, totalQuantity=quantity,
                                                     tif=tif, auxPrice=aux_price), outsideRth=outsideRth)
        StatusUpdate(response, 'Entry', ibContract, 'STP', tradeType, quantity, histData, lastPrice, symbol,
                     timeFrame, profit, stopLoss, risk, '', tif, barType, buySellType, atrPercentage, slValue,
                     breakEven,
                     outsideRth)
        logging.info("pbe2 entry order done %s ", symbol)
        await pbe2_loop_run(connection, key, response.order,buySellType, lastPrice)

        # sendEntryTrade(connection, ibContract, tradeType, quantity, histData, lastPrice, symbol, timeFrame, profit, stopLoss, risk, tif, barType,buySellType,atrPercentage,slValue,breakEven,outsideRth)

        break

async def pbe2_loop_run(connection,key,entry_order,buySellType, lastPrice):
    order = entry_order
    while True:
        try:
            await asyncio.sleep(1)
            # for entry_key, entry_value in list(Config.rbbb_dict.items()):
            old_order = Config.orderStatusData[order.orderId]
            logging.info("old order pbe2_loop_run %s ",old_order)
            if old_order != None:
                sleep_time = getTimeInterval(old_order['timeFrame'], datetime.datetime.now())
                await asyncio.sleep(sleep_time)
            old_order = Config.orderStatusData[order.orderId]
            if (old_order['status'] != 'Filled' and old_order['status'] != 'Cancelled' and old_order['status'] != 'Inactive'):
                logging.info("pbe2 stp updation start  %s %s", order.orderId , old_order['status'].upper()  )
                chartTime = await get_first_chart_time(old_order['timeFrame'], old_order['outsideRth'])
                recentBarData = connection.getHistoricalChartDataForEntry(old_order['contract'], old_order['timeFrame'], chartTime)
                if (len(recentBarData) == 0):
                    logging.info("last 3 record not found we will try after 2 sec.")
                    await  asyncio.sleep(1)
                    continue
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
                    aux_price = 0
                    if old_order['userBuySell'] == 'BUY':
                        aux_price = histData['high']
                        logging.info("pbe2 auxprice high for  %s ", aux_price)
                    else:
                        aux_price = histData['low']
                        logging.info("pbe2 auxprice low for  %s ", aux_price)

                    logging.info("pbe2 going to update stp price for  newprice %s old_order %s", aux_price,order)
                    order.auxPrice = aux_price
                    old_orderId=order.orderId
                    new_order = Order( orderType="STP", action=order.action, totalQuantity=order.totalQuantity,  tif='DAY',auxPrice=aux_price)
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

async def SendTrade(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue , breakEven , outsideRth,entry_points):
    try:
        symbol=symbol.upper()
        if entry_points == "":
            entry_points = 0
        logging.info("sending trade %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s",  symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue , breakEven , outsideRth,entry_points)

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
                # Only allow RB/RBB and manual order types
                allowed_types = (Config.entryTradeType[3], Config.entryTradeType[4]) + MANUAL_ORDER_TYPES
                if barType not in allowed_types:
                    logging.info("%s session: Only RB/RBB or manual orders allowed; skipping barType %s", session, barType)
                    return
            elif session == 'OVERNIGHT':
                # Overnight: all strategies allowed, but orders will be converted to limit types
                logging.info("OVERNIGHT session: All strategies allowed, order types will be converted to limit-style")
            # Overnight: strategies allowed, order-type handling is done in placeTrade
        else:
            # If outsideRth is False but we're in an extended hours session, warn
            if session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT'):
                logging.warning("Session is %s but outsideRth=False. Consider setting outsideRth=True for extended hours trading.", session)
        if barType == 'Limit Order':
            await manual_limit_order(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                                     atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points)
            return
        elif barType == 'Stop Order':
            await manual_stop_order(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                                    atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points)
            return
        if barType == Config.entryTradeType[0]:
            await (first_bar_fb(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[3] or barType == Config.entryTradeType[4]:
            await (rb_and_rbb(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage,quantity, pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[3] or barType == Config.entryTradeType[5]:
            await (pull_back_PBe1(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[4]:
            await (pull_back_PBe2(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage,quantity, pullBackNo,slValue ,breakEven,outsideRth))
        elif barType == Config.entryTradeType[6]:
            await (lb1(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType, atrPercentage,quantity, pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[7]:
            await (lb2(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                                  atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points))
        elif barType == Config.entryTradeType[8]:
            await (lb3(connection, symbol, timeFrame, profit, stopLoss, risk, tif, barType, buySellType,
                                  atrPercentage, quantity, pullBackNo, slValue, breakEven, outsideRth, entry_points))

        logging.info("task done for %s symbol",symbol)

    except Exception as e:
        logging.error("error in sending mkt trade %s", e)
        logging.error(traceback.format_exc())
        traceback.print_exc()


def sendEntryTrade(connection,ibcontract,tradeType,quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,tif,barType,userBuySell,userAtr,slValue=0,breakEven=False ,outsideRth=False):
    try:
        current_session = _get_current_session()
        print(f"Placing order in session: {current_session} (outsideRth={outsideRth})")
        if barType == Config.entryTradeType[0]:
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
                    "Extended hours bracket: entry %s stop=%s limit=%s | protection %s stop=%s limit=%s (entry_offset=%s protection_offset=%s)",
                    entry_order_type,
                    entry_kwargs['auxPrice'],
                    entry_kwargs['lmtPrice'],
                    stop_order_type,
                    stop_order.auxPrice,
                    stop_order.lmtPrice,
                    entry_offset,
                    protection_offset
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
                         breakEven, outsideRth)
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
            StatusUpdate(response, 'Entry', ibcontract, 'MKT', tradeType, quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,'',tif,barType,userBuySell,userAtr,slValue,breakEven,outsideRth)
        else:
            # Log bar high and low values for review
            bar_high = float(histData.get('high', 0))
            bar_low = float(histData.get('low', 0))
            logging.info(f"ENTRY ORDER (Other) - Bar values: Bar's high={bar_high}, Bar's low={bar_low}, range={bar_high - bar_low} for {symbol} {tradeType}")
            response = connection.placeTrade(contract=ibcontract,
                                         order=Order(orderType="MKT", action=tradeType, totalQuantity=quantity,tif=tif)  , outsideRth = outsideRth )
            StatusUpdate(response, 'Entry', ibcontract, 'MKT', tradeType, quantity,histData,lastPrice, symbol,timeFrame,profit,stopLoss,risk,'',tif,barType,userBuySell,userAtr,slValue,breakEven,outsideRth)


    except Exception as e:
        logging.error("error in sending entry trade %s ", e)
        print(e)

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
        Config.takeProfit[0]: 1,
        Config.takeProfit[1]: 1.5,
        Config.takeProfit[2]: 2,
        Config.takeProfit[3]: 3.5,  # '2.5:1' is at index 3
    }
    # Add 3:1 if it exists (index 4)
    if len(Config.takeProfit) > 4:
        multiplier_map[Config.takeProfit[4]] = 3

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
        Config.takeProfit[0]: 1,
        Config.takeProfit[1]: 1.5,
        Config.takeProfit[2]: 2,
        Config.takeProfit[4]: 2.5,
        Config.takeProfit[3]: 3.5,  # '2.5:1' is at index 3
    }
    # Add 3:1 if it exists (index 4)
    if len(Config.takeProfit) > 4:
        multiplier_map[Config.takeProfit[4]] = 3

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
            bar_type = entryData.get('barType', '')
            is_manual_order = bar_type in Config.manualOrderTypes
            is_extended_hours = entryData.get('outsideRth', False)
            logging.info("Entry order filled - barType=%s, is_manual_order=%s, is_extended_hours=%s, action=%s, orderId=%s", 
                        bar_type, is_manual_order, is_extended_hours, entryData.get('action'), entryData.get('orderId'))
            
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
            parentData = entryData['entryData']
            if(parentData['barType'] == Config.entryTradeType[5]):
                asyncio.ensure_future(pull_back_PBe2(connection, parentData['usersymbol'],  parentData['timeFrame'], parentData['profit'],
                                                parentData['stopLoss'], parentData['risk'], parentData['tif'],  Config.entryTradeType[4],
                                                parentData['userBuySell'], parentData['userAtr'],Config.pullBackNo,parentData['slValue'],parentData['breakEven'],parentData['outsideRth']))


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
            
            if entryData['barType'] == Config.entryTradeType[0] or entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[2] or entryData['barType'] == Config.entryTradeType[3] or entryData['barType'] == Config.entryTradeType[4] or entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7] or entryData['barType'] == Config.entryTradeType[8]:
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
                if (len(histData) == 0):
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
            entry_stop_price = entryData.get('lastPrice', filled_price)
            if entry_stop_price is None or entry_stop_price == 0:
                entry_stop_price = filled_price
            logging.info("In TPSL %s contract  and for %s histdata. Entry stop price=%s, Filled price=%s, barType=%s, stopLoss=%s",
                         entryData['contract'], histData, entry_stop_price, filled_price, entryData.get('barType'), entryData.get('stopLoss'))
            
            # Skip RB/RBB section for manual orders - they have their own logic
            if (entryData['barType'] == Config.entryTradeType[3]) or (entryData['barType'] == Config.entryTradeType[4]) or (entryData['barType'] == Config.entryTradeType[5]):
                candleData = connection.getDailyCandle(entryData['contract'])
                if (candleData == None or len(candleData) < 1):
                    logging.info("candle data not found for %s", entryData['contract'])
                    await  asyncio.sleep(1)
                    continue
                logging.info(" Candle Data for takeProfit %s and contract is %s", candleData[-1], entryData['contract'])

                # Check if stop loss is ATR-based or Custom - use same stop_size as entry and stop loss
                stop_loss_type = entryData.get('stopLoss')
                if stop_loss_type in Config.atrStopLossMap:
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
                        # stop_size = |entry - custom_stop|
                        stop_size = abs(float(entry_stop_price) - custom_stop)
                        logging.info(f"RB/RBB Custom TP (SHORT): entry={entry_stop_price}, custom_stop={custom_stop}, stop_size={stop_size} for take profit")
                else:
                    # Non-ATR, Non-Custom stop loss: use bar-based stop_size (same as entry and stop loss)
                    try:
                        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                    except Exception:
                        stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                    logging.info(f"RB/RBB bar-based TP (SHORT): Using bar-based stop_size={stop_size} for take profit")

                multiplier_map = {
                    Config.takeProfit[0]: 2,  # "1:1" means 2× stop_size for short positions
                    Config.takeProfit[1]: 2.5,  # "1.5:1" means 2.5× stop_size
                    Config.takeProfit[2]: 3,  # "2:1" means 3× stop_size
                    Config.takeProfit[3]: 3.5,  # "2.5:1" means 3.5× stop_size (index 3)
                }
                # Add 3:1 if it exists (index 4)
                if len(Config.takeProfit) > 4:
                    multiplier_map[Config.takeProfit[4]] = 4  # "3:1" means 4× stop_size

                multiplier = multiplier_map.get(entryData['profit'])
                if multiplier is not None:
                    price = float(entry_stop_price) - (multiplier * stop_size)
                else:
                    price = float(histData['low'])

                price = round(price, Config.roundVal)
                logging.info(
                    "Extended TP calculation (buy/SHORT) %s stop_size=%s multiplier=%s entry_stop_price=%s filled_price=%s price=%s",
                    entryData['contract'], stop_size, multiplier, entry_stop_price, filled_price, price,
                )

            else:
                # Check if this is a manual order (Stop Order or Limit Order) with custom stop loss
                if entryData['barType'] in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop_size for take profit (same as entry and stop loss)
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop == 0:
                        # Fallback to regular calculation if custom value missing
                        price = get_tp_for_buying(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                        logging.warning(f"Manual Order Custom TP (SHORT): Custom stop loss value missing, using fallback calculation")
                    else:
                        # stop_size = |entry - custom_stop|
                        stop_size = abs(float(entry_stop_price) - custom_stop)
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
            if price == 0:
                logging.warning("Manual Order: TP price is 0, checking if custom stop loss. barType=%s, stopLoss=%s", 
                               entryData.get('barType'), entryData.get('stopLoss'))
                # Try to calculate fallback TP for custom stop loss
                if entryData.get('barType') in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop > 0 and entry_stop_price > 0:
                        stop_size = abs(float(entry_stop_price) - custom_stop)
                        multiplier_map = {
                            Config.takeProfit[0]: 1,    # 1:1
                            Config.takeProfit[1]: 1.5,  # 1.5:1
                            Config.takeProfit[2]: 2,    # 2:1
                            Config.takeProfit[3]: 2.5,  # 2.5:1
                        }
                        if len(Config.takeProfit) > 4:
                            multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                        multiplier = multiplier_map.get(entryData.get('profit'), 2.0)  # Default 2:1
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
            
            # For RB, LB, LB2, LB3: calculate stop size in advance and use it for stop loss calculation
            # Note: RBB (entryTradeType[2]) uses different logic - it updates stop price continuously via rbb_loop_run
            if (entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7] or entryData['barType'] == Config.entryTradeType[8]):
                # Calculate stop size for stop loss
                stop_loss_type = entryData.get('stopLoss')
                if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                    # For EntryBar: stop_size = (bar_high - bar_low) + 0.02
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    stop_size = round(stop_size, Config.roundVal)
                    # In extended hours: stop = entry ± stop_size, limit = entry ± 2 × stop_size
                    # For SHORT position (BUY stop loss): stop_loss = entry + stop_size
                    stpPrice = float(filled_price) + float(stop_size)
                    logging.info(f"RB/LB/LB2/LB3 EntryBar stop loss (for SHORT): filled_price={filled_price}, stop_size={stop_size}, stpPrice={stpPrice}")
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
                            stpPrice = float(filled_price) + float(stop_size)
                        else:
                            # For Custom: stop_size = |entry - custom_stop|, stop_price = custom_stop
                            stop_size = abs(float(filled_price) - custom_stop)
                            protection_offset = stop_size * 2.0
                            # Stop loss price is the custom value directly
                            stpPrice = round(custom_stop, Config.roundVal)
                            logging.info(f"RB/LB/LB2/LB3 Custom stop loss (for SHORT): filled_price={filled_price}, custom_stop={custom_stop}, stop_size={stop_size}, stpPrice={stpPrice}")
                        # Store stop_size in entryData for sendStopLoss to use in extended hours
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"RB/LB/LB2/LB3 BUY stop loss (for SHORT): filled_price={filled_price}, stop_size={stop_size}, stpPrice={stpPrice}")
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
                        stpPrice = float(filled_price) + float(stop_size)
                        logging.info(f"RB/LB/LB2/LB3 BUY stop loss (for SHORT): filled_price={filled_price}, stop_size={stop_size}, stpPrice={stpPrice}")
                        # Store stop_size in entryData for sendStopLoss to use in extended hours
                        entryData['calculated_stop_size'] = stop_size
            else:
                # Check if this is a manual order (Stop Order or Limit Order) with custom stop loss
                if entryData['barType'] in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop loss value directly
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop == 0:
                        # Fallback to existing logic if custom value missing
                        stpPrice = get_sl_for_buying(connection, entryData['stopLoss'], filled_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
                        logging.warning(f"Manual Order Custom stop loss (for SHORT): Custom stop loss value missing, using fallback calculation")
                        stpPrice = stpPrice + 0.01
                    else:
                        # For Custom: stop_size = |entry - custom_stop|, stop_price = custom_stop
                        stop_size = abs(float(filled_price) - custom_stop)
                        # Stop loss price is the custom value directly
                        stpPrice = round(custom_stop, Config.roundVal)
                        # Store stop_size in entryData for sendStopLoss to use in extended hours
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"Manual Order Custom stop loss (for SHORT): filled_price={filled_price}, custom_stop={custom_stop}, stop_size={stop_size}, stpPrice={stpPrice}")
                    # Custom stop loss logic complete - stpPrice is set, continue to send order
                # For other strategies (including RBB), use existing logic
                else:
                    stpPrice = get_sl_for_buying(connection, entryData['stopLoss'], filled_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
                    logging.info(f"BUY stop loss (for SHORT): Base price from get_sl_for_buying={stpPrice}, bar high={entryData['histData'].get('high')}, bar low={entryData['histData'].get('low')}")
                    stpPrice = stpPrice + 0.01
                    logging.info(f"BUY stop loss (for SHORT): After +0.01 adjustment={stpPrice}, filled_price={filled_price}")
            
            # Ensure stpPrice is set before sending SL
            if stpPrice == 0:
                logging.warning("Manual Order: SL price is 0, checking if custom stop loss. barType=%s, stopLoss=%s", 
                               entryData.get('barType'), entryData.get('stopLoss'))
                # Try to calculate fallback SL for custom stop loss
                if entryData.get('barType') in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:
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
            
            logging.info("Manual Order Custom: About to send SL - stpPrice=%s, barType=%s, stopLoss=%s, orderId=%s", 
                        stpPrice, entryData.get('barType'), entryData.get('stopLoss'), entryData.get('orderId'))
            if stpPrice is not None and stpPrice > 0:
                logging.info("Sending STPLOSS Trade EntryData is %s  and Price is %s and action is BUY and hist Data [ %s ]", entryData, stpPrice,histData)
                sendStopLoss(connection, entryData, stpPrice, "BUY")
            else:
                logging.error("Manual Order: Skipping SL order due to invalid price=%s", stpPrice)
            
            mocPrice = 0
            logging.info("Sending Moc Order  of %s price ",mocPrice)
            sendMoc(connection,entryData,mocPrice,"BUY")
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
        is_extended, session = _is_extended_outside_rth(entryData.get('outsideRth', False))
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
        order_kwargs['auxPrice'] = adjusted_price

        # Check if this is a manual stop order
        bar_type = entryData.get('barType', '')
        is_manual_stop_order = bar_type == 'Stop Order'
        
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

        if extended_hist_supported:
            # Get entry filled price for manual stop order calculations
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
                    
                    # Limit offset = 2 × stop_size (same as entry order uses)
                    limit_offset = round(manual_stop_size * 2.0, Config.roundVal)
                    
                    if action.upper() == "SELL":
                        # SELL stop loss means LONG position, so limit is below stop
                        limit_price = stop_loss_price - limit_offset
                        logging.info(f"Manual Stop Order Custom OTH: SELL stop loss (LONG position): entry={filled_price}, stop={stop_loss_price} (custom_stop), limit={limit_price} (stop - {limit_offset}), stop_size={manual_stop_size}")
                    else:
                        # BUY stop loss means SHORT position, so limit is above stop
                        limit_price = stop_loss_price + limit_offset
                        logging.info(f"Manual Stop Order Custom OTH: BUY stop loss (SHORT position): entry={filled_price}, stop={stop_loss_price} (custom_stop), limit={limit_price} (stop + {limit_offset}), stop_size={manual_stop_size}")
                    
                    # Update adjusted_price to use custom stop loss price
                    adjusted_price = round(stop_loss_price, Config.roundVal)
                    order_kwargs['auxPrice'] = adjusted_price
                else:
                    # Non-Custom stop loss: Stop = entry ± stop_size, Limit = entry ± 2 × stop_size
                    # Use stop_size from entryData if stored, otherwise recalculate
                    manual_stop_size = entryData.get('stopSize')
                    if manual_stop_size is None or manual_stop_size <= 0:
                        # Recalculate stop_size the same way as in manual_stop_order
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
                    
                    # For Stop Order in extended hours: stop = entry ± stop_size, limit = entry ± 2 × stop_size
                    protection_offset = round(manual_stop_size * 1.0, Config.roundVal)  # 1x for stop price
                    limit_offset = round(manual_stop_size * 2.0, Config.roundVal)  # 2x for limit price
                    logging.info(f"Manual Stop Order OTH: stop_size={manual_stop_size}, protection_offset={protection_offset} (for stop), limit_offset={limit_offset} (for limit)")
                    
                    if action.upper() == "SELL":
                        # SELL stop loss means LONG position, so stop loss is below entry
                        stop_loss_price = filled_price - protection_offset  # entry - stop_size
                        limit_price = filled_price - limit_offset  # entry - 2 × stop_size
                        logging.info(f"Manual Stop Order OTH: SELL stop loss (LONG position): entry={filled_price}, stop={stop_loss_price} (entry - {protection_offset}), limit={limit_price} (entry - {limit_offset})")
                    else:
                        # BUY stop loss means SHORT position, so stop loss is above entry
                        stop_loss_price = filled_price + protection_offset  # entry + stop_size
                        limit_price = filled_price + limit_offset  # entry + 2 × stop_size
                        logging.info(f"Manual Stop Order OTH: BUY stop loss (SHORT position): entry={filled_price}, stop={stop_loss_price} (entry + {protection_offset}), limit={limit_price} (entry + {limit_offset})")
                    
                    # Update adjusted_price to use calculated stop loss price
                    adjusted_price = round(stop_loss_price, Config.roundVal)
                    order_kwargs['auxPrice'] = adjusted_price
            else:
                # Regular trade types (including RB/LB/LB2/LB3): use existing logic
                # Check if this is RB/LB/LB2/LB3 - need to use entry_price for limit calculation
                bar_type = entryData.get('barType', '')
                is_rb_lb = bar_type in [Config.entryTradeType[1], Config.entryTradeType[6], Config.entryTradeType[7], Config.entryTradeType[8]]
                
                if is_rb_lb and calculated_stop_size is not None:
                    # For RB/LB/LB2/LB3 in extended hours:
                    # Stop price: entry_price ± stop_size (already in adjusted_price)
                    # Limit price: entry_price ± 2 × stop_size
                    if action.upper() == "BUY":
                        # SHORT position: stop = entry + stop_size, limit = entry + 2 × stop_size
                        limit_price = filled_price + limit_offset
                        logging.info(f"RB/LB/LB2/LB3 BUY stop loss (SHORT): stop={adjusted_price} (entry + stop_size), limit={limit_price} (entry + 2×stop_size={limit_offset}), entry={filled_price}")
                    else:
                        # LONG position: stop = entry - stop_size, limit = entry - 2 × stop_size
                        limit_price = filled_price - limit_offset
                        logging.info(f"RB/LB/LB2/LB3 SELL stop loss (LONG): stop={adjusted_price} (entry - stop_size), limit={limit_price} (entry - 2×stop_size={limit_offset}), entry={filled_price}")
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
                                    # SELL stop loss means LONG position, so limit is below stop
                                    limit_price = stop_loss_price - limit_offset
                                    logging.info(f"Limit Order Custom OTH: SELL stop loss (LONG position): entry={filled_price}, stop={stop_loss_price} (custom_stop), limit={limit_price} (stop - {limit_offset}), stop_size={manual_stop_size}")
                                else:
                                    # BUY stop loss means SHORT position, so limit is above stop
                                    limit_price = stop_loss_price + limit_offset
                                    logging.info(f"Limit Order Custom OTH: BUY stop loss (SHORT position): entry={filled_price}, stop={stop_loss_price} (custom_stop), limit={limit_price} (stop + {limit_offset}), stop_size={manual_stop_size}")
                                
                                # Update adjusted_price to use custom stop loss price
                                adjusted_price = round(stop_loss_price, Config.roundVal)
                                order_kwargs['auxPrice'] = adjusted_price
                        else:
                            # Limit Order with non-Custom stop loss: use original logic (unchanged)
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

            # For extended hours, ALWAYS use STP LMT (Stop-Limit Order) for stop loss
            order_type = "STP LMT"
            order_kwargs['orderType'] = "STP LMT"
            order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
            logging.info(
                "Extended hours protection stop-limit: action=%s stop=%s limit=%s stop_size=%s protection_offset=%s session=%s barType=%s",
                action, adjusted_price, order_kwargs['lmtPrice'], stop_size, protection_offset, session, entryData.get('barType', '')
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

        lmtResponse = connection.placeTrade(contract=entryData['contract'],
                                            order=Order(**order_kwargs), outsideRth=entryData['outsideRth'] )
        StatusUpdate(lmtResponse, 'StopLoss', entryData['contract'], order_type, action, entryData['totalQuantity'], entryData['histData'], adjusted_price, entryData['usersymbol'], entryData['timeFrame'], entryData['profit'], entryData['stopLoss'], entryData['risk'],entryData,'','','','',entryData['slValue'],entryData['breakEven'],entryData['outsideRth'] )

        print(lmtResponse)
        if(entryData['stopLoss'] == Config.stopLoss[1]):
            loop = asyncio.get_event_loop()
            asyncio.ensure_future(stopLossThread(connection, entryData,adjusted_price,action,lmtResponse.order.orderId))
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
        print("tttttttttttt")
        print(lmtResponse)
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
        print("(first time) Thread is going to sleep %s   current datetime is %s  and chart timming  %s", sleepTime, currentTime,chartTime)
        await asyncio.sleep(sleepTime)
        logging.info("take profit status after sleep %s", lmtData['status'])
        nextSleepTime = Config.timeDict.get(lmtData['timeFrame'])
        while(lmtData != None and (lmtData['status'] != 'Filled' and lmtData['status'] != 'Cancelled' and lmtData['status'] != 'Inactive')):
            print("updating date in %s time ", datetime.datetime.now())
            logging.info("running  take profit in while loop, status is %s",lmtData['status'])
            histData = connection.getHistoricalChartData(lmtData['contract'], lmtData['timeFrame'],chartTime)
            logging.info("hist data for %s contract id, hist data is { %s }  and for %s time", lmtData['contract'], histData, chartTime)
            if (len(histData) == 0):
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
        print("update take profit")
        print(lmtResponse)
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
            
            if entryData['barType'] == Config.entryTradeType[0] or entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[2] or entryData['barType'] == Config.entryTradeType[3] or entryData['barType'] == Config.entryTradeType[4] or entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7] or entryData['barType'] == Config.entryTradeType[8]:
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
                if (len(histData) == 0):
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
            entry_stop_price = entryData.get('lastPrice', filled_price)
            if entry_stop_price is None or entry_stop_price == 0:
                entry_stop_price = filled_price
            logging.info("In TPSL %s contract  and for %s histdata. Entry stop price=%s, Filled price=%s, barType=%s, stopLoss=%s",
                         entryData['contract'], histData, entry_stop_price, filled_price, entryData.get('barType'), entryData.get('stopLoss'))
            
            # Skip RB/RBB section for manual orders - they have their own logic
            if (entryData['barType'] == Config.entryTradeType[3]) or (entryData['barType'] == Config.entryTradeType[4]) or (entryData['barType'] == Config.entryTradeType[5]):
                candleData = connection.getDailyCandle(entryData['contract'])
                if (candleData == None or len(candleData) < 1):
                    logging.info("candle data not found for %s", entryData['contract'])
                    await  asyncio.sleep(1)
                    continue
                logging.info(" Candle Data for takeProfit %s and contract is %s", candleData[-1],entryData['contract'])
                
                # Check if stop loss is ATR-based or Custom - use same stop_size as entry and stop loss
                stop_loss_type = entryData.get('stopLoss')
                if stop_loss_type in Config.atrStopLossMap:
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
                        # stop_size = |entry - custom_stop|
                        stop_size = abs(float(entry_stop_price) - custom_stop)
                        logging.info(f"RB/RBB Custom TP (LONG): entry={entry_stop_price}, custom_stop={custom_stop}, stop_size={stop_size} for take profit")
                else:
                    # Non-ATR, Non-Custom stop loss: use bar-based stop_size (same as entry and stop loss)
                    try:
                        stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                    except Exception:
                        stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)
                    logging.info(f"RB/RBB bar-based TP (LONG): Using bar-based stop_size={stop_size} for take profit")

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
                    price = float(entry_stop_price) + (multiplier * stop_size)
                else:
                    price = float(histData['high'])

                price = round(price, Config.roundVal)
                logging.info(
                    "Extended TP calculation (sell/LONG) %s stop_size=%s multiplier=%s entry_stop_price=%s filled_price=%s price=%s",
                    entryData['contract'], stop_size, multiplier, entry_stop_price, filled_price, price,
                )
            else:
                # Check if this is a manual order (Stop Order or Limit Order) with custom stop loss
                if entryData['barType'] in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop_size for take profit (same as entry and stop loss)
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop == 0:
                        # Fallback to regular calculation if custom value missing
                        price = get_tp_for_selling(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], entry_stop_price, histData)
                        logging.warning(f"Manual Order Custom TP (LONG): Custom stop loss value missing, using fallback calculation")
                    else:
                        # stop_size = |entry - custom_stop|
                        stop_size = abs(float(entry_stop_price) - custom_stop)
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

            logging.info("Sending TP Trade EntryData is %s  and Price is %s  and action is SELL", entryData, price)
            sendTakeProfit(connection, entryData, price, "SELL")

            # Calculate stop size in advance for RB, LB, LB2, LB3 (RBB uses different logic)
            stpPrice = 0
            chart_Time = datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime,  "%Y-%m-%d %H:%M:%S")
            
            # For RB, LB, LB2, LB3: calculate stop size in advance and use it for stop loss calculation
            # Note: RBB (entryTradeType[2]) uses different logic - it updates stop price continuously via rbb_loop_run
            if (entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7] or entryData['barType'] == Config.entryTradeType[8]):
                # Calculate stop size for stop loss
                stop_loss_type = entryData.get('stopLoss')
                if stop_loss_type == Config.stopLoss[0]:  # EntryBar
                    # For EntryBar: stop_size = (bar_high - bar_low) + 0.02
                    stop_size = (float(histData['high']) - float(histData['low'])) + Config.add002
                    stop_size = round(stop_size, Config.roundVal)
                    # In extended hours: stop = entry ± stop_size, limit = entry ± 2 × stop_size
                    # For LONG position (SELL stop loss): stop_loss = entry - stop_size
                    stpPrice = float(filled_price) - float(stop_size)
                    logging.info(f"RB/LB/LB2/LB3 EntryBar stop loss (for LONG): filled_price={filled_price}, stop_size={stop_size}, stpPrice={stpPrice}")
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
                            stpPrice = float(filled_price) - float(stop_size)
                        else:
                            # For Custom: stop_size = |entry - custom_stop|, stop_price = custom_stop
                            stop_size = abs(float(filled_price) - custom_stop)
                            protection_offset = stop_size * 2.0
                            # Stop loss price is the custom value directly
                            stpPrice = round(custom_stop, Config.roundVal)
                            logging.info(f"RB/LB/LB2/LB3 Custom stop loss (for LONG): filled_price={filled_price}, custom_stop={custom_stop}, stop_size={stop_size}, stpPrice={stpPrice}")
                        # Store stop_size in entryData for sendStopLoss to use in extended hours
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"RB/LB/LB2/LB3 SELL stop loss (for LONG): filled_price={filled_price}, stop_size={stop_size}, stpPrice={stpPrice}")
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
                        stpPrice = float(filled_price) - float(stop_size)
                        logging.info(f"RB/LB/LB2/LB3 SELL stop loss (for LONG): filled_price={filled_price}, stop_size={stop_size}, stpPrice={stpPrice}")
                        # Store stop_size in entryData for sendStopLoss to use in extended hours
                        entryData['calculated_stop_size'] = stop_size
            else:
                # Check if this is a manual order (Stop Order or Limit Order) with custom stop loss
                if entryData['barType'] in Config.manualOrderTypes and entryData.get('stopLoss') == Config.stopLoss[1]:  # 'Custom'
                    # Use Custom stop loss value directly
                    custom_stop = _to_float(entryData.get('slValue', 0), 0)
                    if custom_stop == 0:
                        # Fallback to existing logic if custom value missing
                        stpPrice = get_sl_for_selling(connection, entryData['stopLoss'], filled_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
                        logging.warning(f"Manual Order Custom stop loss (for LONG): Custom stop loss value missing, using fallback calculation")
                        stpPrice = stpPrice - 0.01
                    else:
                        # For Custom: stop_size = |entry - custom_stop|, stop_price = custom_stop
                        stop_size = abs(float(filled_price) - custom_stop)
                        # Stop loss price is the custom value directly
                        stpPrice = round(custom_stop, Config.roundVal)
                        # Store stop_size in entryData for sendStopLoss to use in extended hours
                        entryData['calculated_stop_size'] = stop_size
                        logging.info(f"Manual Order Custom stop loss (for LONG): filled_price={filled_price}, custom_stop={custom_stop}, stop_size={stop_size}, stpPrice={stpPrice}")
                # For other strategies (including RBB), use existing logic
                else:
                    stpPrice = get_sl_for_selling(connection, entryData['stopLoss'], filled_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
                    logging.info(f"minus 0.01 in stop entry is buying {stpPrice}")
                    stpPrice = stpPrice - 0.01
                    logging.info(f"SELL stop loss (for LONG): After -0.01 adjustment={stpPrice}, filled_price={filled_price}")
            
            logging.info("Sending STPLOSS Trade EntryData is %s  and Price is %s  and hist Data [ %s ] and action is Sell", entryData, stpPrice,histData)

            sendStopLoss(connection, entryData, stpPrice, "SELL")
            mocPrice = 0
            logging.info("Sending Moc Order  of %s price ", mocPrice)
            sendMoc(connection, entryData, mocPrice, "SELL")
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
            print("updating date in %s time ",datetime.datetime.now())
            logging.info("running  stop loss in while loop, status is %s", lmtData['status'])
            histData = connection.getHistoricalChartData(lmtData['contract'], lmtData['timeFrame'], chartTime)
            logging.info("hist data for %s contract id, hist data is { %s }  and of %s time", lmtData['contract'], histData, chartTime)
            if (len(histData) == 0):
                logging.info("hist data not found going to sleep for 1 second")
                print("hist data not found")
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
            print("updating barBybar stop loss, entrydata is %s, price is %s and order id is %s",entryData,price,orderId)

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
            print("(second time) BarByBar stopLoss thread is sleeping for %s time in second ", Config.timeDict.get(lmtData['timeFrame']))

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

        print("update stop loss response %s",lmtResponse)
        StatusUpdate(lmtResponse, 'StopLoss', entryData['contract'], 'STP', action, entryData['totalQuantity'], entryData['histData'], price, entryData['usersymbol'], entryData['timeFrame'], entryData['profit'], entryData['stopLoss'], entryData['risk'],entryData,'','','','',entryData['slValue'],entryData['breakEven'],entryData['outsideRth'] )
    except Exception as e:
        logging.error("error in updating stop loss %s ", e)
        print(e)

