import asyncio
import datetime
import logging
import random
import Config
from header import *
from StatusUpdate import *
import traceback
import talib
from FunctionCalls import *
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
            candel_for_quantity= histData
            if stopLoss == Config.stopLoss[0]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[1]:
                # quantity = (float(risk) / ((float(histData['high']) - float(histData['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[2]:
                high_value = 0
                high_candel = None
                recentBarDataTwo = connection.getHistoricalChartDataForEntry( ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDataTwo))):
                    if (high_value == 0 or high_value < recentBarDataTwo.get(data)['high']):
                        high_value = recentBarDataTwo.get(data)['high']
                        candel_for_quantity = recentBarDataTwo.get(data)
                        logging.info(f"high value found for %s recentBarData.get(data) %s ", recentBarDataTwo,  recentBarDataTwo.get(data))

                # quantity = (float(risk) / ((float(high_candel['high']) - float(high_candel['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[3]:
                low_value = 0
                low_candel= None
                recentBarDatat = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDatat))):
                    if (low_value == 0 or low_value > recentBarDatat.get(data)['low']):
                        low_value = recentBarDatat.get(data)['low']
                        candel_for_quantity = recentBarDatat.get(data)
                        logging.info(f"low value found for %s recentBarData.get(data) %s ", ibContract, recentBarDatat.get(data))
                # quantity = (float(risk) / ((float(low_candel['high']) - float(low_candel['low']))))
                # quantity = round(quantity, 0)
            else:
                pass
            logging.info(f"candel found for quantity %s recentBarData.get(data) %s ", ibContract, candel_for_quantity)
            quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
            logging.info(f"FB quantity before +0.02 {quantity}")
            quantity = quantity + 0.02
            quantity = round(quantity, 0)
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
        if Config.historicalData.get(key) == None:
            chartTime = getRecentChartTime(timeFrame)
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
            candel_for_quantity=histData
            if stopLoss == Config.stopLoss[0]:
                # quantity = (float(risk) / ((float(histData['high']) - float(histData['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[1]:
                # quantity = (float(risk) / ((float(histData['high']) - float(histData['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[2]:
                high_value = 0
                high_candel = None
                recentBarDataTwo = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDataTwo))):
                    if (high_value == 0 or high_value < recentBarDataTwo.get(data)['high']):
                        high_value = recentBarDataTwo.get(data)['high']
                        candel_for_quantity = recentBarDataTwo.get(data)
                        logging.info(f"high value found for %s recentBarData.get(data) %s ", recentBarDataTwo,
                                     recentBarDataTwo.get(data))

                # quantity = (float(risk) / ((float(high_candel['high']) - float(high_candel['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[3]:
                low_value = 0
                low_candel = None
                recentBarDatat = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDatat))):
                    if (low_value == 0 or low_value > recentBarDatat.get(data)['low']):
                        low_value = recentBarDatat.get(data)['low']
                        candel_for_quantity = recentBarDatat.get(data)
                        logging.info(f"low value found for %s recentBarData.get(data) %s ", ibContract,
                                     recentBarDatat.get(data))
                # quantity = (float(risk) / ((float(low_candel['high']) - float(low_candel['low']))))
                # quantity = round(quantity, 0)
            else:
                pass
            histData = candel_for_quantity
            price_range = float(candel_for_quantity['high']) - float(candel_for_quantity['low'])
            if price_range == 0 or price_range < 0.01:
                # Fallback: use lastPrice with a small percentage spread to avoid division by zero
                logging.warning(f"Price range is zero or too small ({price_range}) for {ibContract}, using fallback calculation")
                price_range = float(lastPrice) * 0.01  # Use 1% of lastPrice as fallback spread
            quantity = (float(risk) / price_range)
            logging.info(f"rb quantity before +0.02 {quantity}")
            quantity = quantity + 0.02
            quantity = round(quantity, 0)
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
            # Calculate stop size and limit offsets for entry
            stop_size, entry_offset, _ = _calculate_stop_limit_offsets(histData)
            logging.info(f"Pre-market/After-hours: Using STP LMT order. Stop size={stop_size}, Entry offset={entry_offset}")

            order_type = "STP LMT"
            if tradeType == 'BUY':
                # For BUY: Limit = Entry + entry_offset
                limit_price = aux_price + entry_offset
            else:
                # For SELL: Limit = Entry - entry_offset
                limit_price = aux_price - entry_offset
            
            limit_price = round(limit_price, Config.roundVal)
            logging.info(f"Pre-market/After-hours {tradeType}: Stop={aux_price}, Limit={limit_price}")
            
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
        if barType == Config.entryTradeType[2]:
            await rbb_loop_run(connection,key,response.order)
        break

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
                    aux_price = 0
                    if old_order['userBuySell'] == 'BUY':
                        aux_price = histData['high']
                        logging.info("RBRR auxprice high for  %s ", aux_price)
                    else:
                        aux_price = histData['low']
                        logging.info("RBRR auxprice low for  %s ", aux_price)

                    logging.info("RBBB going to update stp price for  newprice %s old_order %s", aux_price,order)
                    logging.info(f"rb aux limit price befor 0.01 plus minus aux {aux_price}")
                    if (old_order['userBuySell'] == 'BUY'):
                        aux_price = aux_price + 0.01
                    else:
                        aux_price = aux_price - 0.01
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
                candel_for_quantity = None
                if stopLoss == Config.stopLoss[0]:
                    # quantity = (float(risk) / ((float(histData['high']) - float(histData['low']))))
                    # quantity = round(quantity, 0)
                    pass
                elif stopLoss == Config.stopLoss[1]:
                    # quantity = (float(risk) / ((float(histData['high']) - float(histData['low']))))
                    # quantity = round(quantity, 0)
                    pass
                elif stopLoss == Config.stopLoss[2]:
                    high_value = 0
                    high_candel = None
                    recentBarDataTwo = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                    for data in range(0, (len(recentBarDataTwo))):
                        if (high_value == 0 or high_value < recentBarDataTwo.get(data)['high']):
                            high_value = recentBarDataTwo.get(data)['high']
                            candel_for_quantity = recentBarDataTwo.get(data)
                            logging.info(f"high value found for %s recentBarData.get(data) %s ", recentBarDataTwo,
                                         recentBarDataTwo.get(data))

                    # quantity = (float(risk) / ((float(high_candel['high']) - float(high_candel['low']))))
                    # quantity = round(quantity, 0)
                    pass
                elif stopLoss == Config.stopLoss[3]:
                    low_value = 0
                    low_candel = None
                    recentBarDatat = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                    for data in range(0, (len(recentBarDatat))):
                        if (low_value == 0 or low_value > recentBarDatat.get(data)['low']):
                            low_value = recentBarDatat.get(data)['low']
                            candel_for_quantity = recentBarDatat.get(data)
                            logging.info(f"low value found for %s recentBarData.get(data) %s ", ibContract,
                                         recentBarDatat.get(data))
                    # quantity = (float(risk) / ((float(low_candel['high']) - float(low_candel['low']))))
                    # quantity = round(quantity, 0)
                else:
                    # quantity = (float(risk) / ((float(histData['high']) - float(histData['low']))))
                    # quantity = round(quantity, 0)
                    pass
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))

            else:
                logging.info("user quantity")
            logging.info(f"pullback pbe1 quantity before +0.02 {quantity}")
            quantity = quantity + 0.02
            quantity = round(quantity, 0)
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
            candel_for_quantity = histData
            if stopLoss == Config.stopLoss[0]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[1]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[2]:
                high_value = 0
                high_candel = None
                recentBarDataTwo = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDataTwo))):
                    if (high_value == 0 or high_value < recentBarDataTwo.get(data)['high']):
                        high_value = recentBarDataTwo.get(data)['high']
                        candel_for_quantity = recentBarDataTwo.get(data)
                        logging.info(f"lb1 high value found for %s recentBarData.get(data) %s ", recentBarDataTwo,
                                     recentBarDataTwo.get(data))

                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[3]:
                low_value = 0
                low_candel = None
                recentBarDatat = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDatat))):
                    if (low_value == 0 or low_value > recentBarDatat.get(data)['low']):
                        low_value = recentBarDatat.get(data)['low']
                        candel_for_quantity = recentBarDatat.get(data)
                        logging.info(f"lb1 low value found for %s recentBarData.get(data) %s ", ibContract,
                                     recentBarDatat.get(data))
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
            else:
                pass

            if tradeType == "BUY":
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low'])) ) )
                aux_price = candel_for_quantity['high']-float(entry_points)
            else:
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low'])) ))
                aux_price = candel_for_quantity['low']+float(entry_points)
        else:
            logging.info("lb1 user quantity")
        logging.info(f"lb1 quantity before +0.02 {quantity}")
        quantity = quantity + 0.02
        quantity = round(quantity, 0)
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

        response = connection.placeTrade(contract=ibContract,
                                         order=Order(orderType="STP", action=tradeType, totalQuantity=quantity,
                                                     tif=tif, auxPrice=aux_price), outsideRth=outsideRth)
        StatusUpdate(response, 'Entry', ibContract, 'STP', tradeType, quantity, histData, lastPrice, symbol,
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
            candel_for_quantity = histData
            if stopLoss == Config.stopLoss[0]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[1]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[2]:
                high_value = 0
                high_candel = None
                recentBarDataTwo = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDataTwo))):
                    if (high_value == 0 or high_value < recentBarDataTwo.get(data)['high']):
                        high_value = recentBarDataTwo.get(data)['high']
                        candel_for_quantity = recentBarDataTwo.get(data)
                        logging.info(f"lb2 high value found for %s recentBarData.get(data) %s ", recentBarDataTwo,
                                     recentBarDataTwo.get(data))

                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[3]:
                low_value = 0
                low_candel = None
                recentBarDatat = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDatat))):
                    if (low_value == 0 or low_value > recentBarDatat.get(data)['low']):
                        low_value = recentBarDatat.get(data)['low']
                        candel_for_quantity = recentBarDatat.get(data)
                        logging.info(f"lb2 low value found for %s recentBarData.get(data) %s ", ibContract,
                                     recentBarDatat.get(data))
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
            else:
                pass

            if tradeType == "BUY":
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                aux_price = candel_for_quantity['high'] - float(entry_points)
                percent= candel_for_quantity['high'] - candel_for_quantity['low']
                percent  =((percent / 100) * 33)
                lmtPrice = aux_price + percent
            else:
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                aux_price = candel_for_quantity['low'] + float(entry_points)
                percent = candel_for_quantity['high'] - candel_for_quantity['low']
                percent = ((percent / 100) * 50)
                lmtPrice = aux_price - percent
        else:
            logging.info("lb2 user quantity")
        logging.info(f"lb2 quantity before +0.02 {quantity}")
        quantity = quantity + 0.02
        quantity = round(quantity, 0)
        lastPrice = aux_price
        aux_price = round(aux_price, Config.roundVal)
        lmtPrice = round(lmtPrice, Config.roundVal)

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
        logging.info(f"lb2 aux limit price befor 0.01 plus minus aux {aux_price} limit {lmtPrice}")
        if (tradeType == 'BUY'):
            aux_price= aux_price + 0.01
            lmtPrice =lmtPrice + 0.01
        else:
            aux_price = aux_price - 0.01
            lmtPrice = lmtPrice - 0.01

        response = connection.placeTrade(contract=ibContract,
                                         order=Order(orderType="STP LMT", action=tradeType, totalQuantity=quantity,
                                                     tif=tif, auxPrice=aux_price,lmtPrice=lmtPrice), outsideRth=outsideRth)
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
            candel_for_quantity = histData
            if stopLoss == Config.stopLoss[0]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[1]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[2]:
                high_value = 0
                high_candel = None
                recentBarDataTwo = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDataTwo))):
                    if (high_value == 0 or high_value < recentBarDataTwo.get(data)['high']):
                        high_value = recentBarDataTwo.get(data)['high']
                        candel_for_quantity = recentBarDataTwo.get(data)
                        logging.info(f"lb3 high value found for %s recentBarData.get(data) %s ", recentBarDataTwo,
                                     recentBarDataTwo.get(data))

                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[3]:
                low_value = 0
                low_candel = None
                recentBarDatat = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDatat))):
                    if (low_value == 0 or low_value > recentBarDatat.get(data)['low']):
                        low_value = recentBarDatat.get(data)['low']
                        candel_for_quantity = recentBarDatat.get(data)
                        logging.info(f"lb3 low value found for %s recentBarData.get(data) %s ", ibContract,
                                     recentBarDatat.get(data))
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
            else:
                pass

            if tradeType == "BUY":
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                aux_price = candel_for_quantity['high'] - float(entry_points)
                percent = candel_for_quantity['high'] - candel_for_quantity['low']
                percent = ((percent / 100) * 33)
                lmtPrice = aux_price + percent

            else:
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                aux_price = candel_for_quantity['low'] + float(entry_points)
                percent = candel_for_quantity['high'] - candel_for_quantity['low']
                percent = ((percent / 100) * 50)
                lmtPrice = aux_price - percent
        else:
            logging.info("lb3 user quantity")
        logging.info(f"lb3 quantity before +0.02 {quantity}")
        quantity = quantity + 0.02
        quantity = round(quantity, 0)
        lastPrice = aux_price
        aux_price = round(aux_price, Config.roundVal)
        lmtPrice = round(lmtPrice, Config.roundVal)

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
        logging.info(f"lb3 aux limit price befor 0.01 plus minus aux {aux_price} limit {lmtPrice}")
        if (tradeType == 'BUY'):
            lmtPrice = lmtPrice+ 0.01
            aux_price = aux_price + 0.1
        else:
            lmtPrice = lmtPrice - 0.01
            aux_price = aux_price - 0.1

        response = connection.placeTrade(contract=ibContract,
                                         order=Order(orderType="STP LMT", action=tradeType, totalQuantity=quantity,
                                                     tif=tif, auxPrice=aux_price,lmtPrice=lmtPrice), outsideRth=outsideRth)
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
            candel_for_quantity=  histData
            if stopLoss == Config.stopLoss[0]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[1]:
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[2]:
                high_value = 0
                high_candel = None
                recentBarDataTwo = connection.getHistoricalChartDataForEntry( ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDataTwo))):
                    if (high_value == 0 or high_value < recentBarDataTwo.get(data)['high']):
                        high_value = recentBarDataTwo.get(data)['high']
                        candel_for_quantity = recentBarDataTwo.get(data)
                        logging.info(f"high value found for %s recentBarData.get(data) %s ", recentBarDataTwo,  recentBarDataTwo.get(data))

                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
                pass
            elif stopLoss == Config.stopLoss[3]:
                low_value = 0
                low_candel= None
                recentBarDatat = connection.getHistoricalChartDataForEntry(ibContract, timeFrame, chartTime)
                for data in range(0, (len(recentBarDatat))):
                    if (low_value == 0 or low_value > recentBarDatat.get(data)['low']):
                        low_value = recentBarDatat.get(data)['low']
                        candel_for_quantity = recentBarDatat.get(data)
                        logging.info(f"low value found for %s recentBarData.get(data) %s ", ibContract, recentBarDatat.get(data))
                # quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                # quantity = round(quantity, 0)
            else:
                pass

            if tradeType == "BUY":
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                aux_price = candel_for_quantity['high']
            else:
                quantity = (float(risk) / ((float(candel_for_quantity['high']) - float(candel_for_quantity['low']))))
                aux_price = candel_for_quantity['low']
        else:
            logging.info("user quantity")
        logging.info(f"pbe2 quantity before +0.02 {quantity}")
        quantity = quantity + 0.02
        quantity = round(quantity, 0)
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
        if  barType == Config.entryTradeType[3]:
            barType = Config.entryTradeType[2]
        if entry_points == "":
            entry_points = 0
        logging.info("sending trade %s %s %s %s %s %s %s %s %s %s %s %s %s %s %s",  symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue , breakEven , outsideRth,entry_points)

        # Enforce client trading-session rules
        session = _get_current_session()
        logging.info("Current session detected: %s, outsideRth flag: %s", session, outsideRth)
        print(f"Current trading session: {session} (outsideRth={outsideRth})")
        
        if outsideRth:
            if session in ('PREMARKET', 'AFTERHOURS'):
                # Only allow RB/RBB
                if not (barType == Config.entryTradeType[1] or barType == Config.entryTradeType[2]):
                    logging.info("%s session: Only RB/RBB allowed; skipping barType %s", session, barType)
                    return
            elif session == 'OVERNIGHT':
                # Overnight: all strategies allowed, but orders will be converted to limit types
                logging.info("OVERNIGHT session: All strategies allowed, order types will be converted to limit-style")
            # Overnight: strategies allowed, order-type handling is done in placeTrade
        else:
            # If outsideRth is False but we're in an extended hours session, warn
            if session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT'):
                logging.warning("Session is %s but outsideRth=False. Consider setting outsideRth=True for extended hours trading.", session)
        if barType == Config.entryTradeType[0]:
            await (first_bar_fb(connection, symbol,timeFrame,profit,stopLoss,risk,tif,barType,buySellType,atrPercentage,quantity,pullBackNo,slValue ,breakEven,outsideRth,entry_points))
        elif barType == Config.entryTradeType[1] or barType == Config.entryTradeType[2]:
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
        Config.takeProfit[3]: 3,
        Config.takeProfit[5]: 2.5,
    }

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
        Config.takeProfit[3]: 3,
        Config.takeProfit[5]: 2.5,
    }

    multiplier = multiplier_map.get(profit_type)
    if multiplier is not None:
        price = float(filled_price) - (multiplier * stop_size)
    else:
        price = float(histData['low'])

    price = round(price, Config.roundVal)
    logging.info(
        f"tp calculation for buying %s , price %s histData %s filled_price %s profit_type %s stop_size %s multiplier %s ",
        contract, price, histData, filled_price, profit_type, stop_size, multiplier,
    )
    return price

def get_sl_for_selling(connection,stoploss_type,filled_price, histData,slValue ,contract,timeframe,chartTime):
    # we are sending sell sl order bcz entry buy
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
        for data in range(0, (len(recentBarData) )):
            if (high_value == 0 or high_value < recentBarData.get(data)['high']):
                high_value = recentBarData.get(data)['low']
                logging.info(f"high value found for %s recentBarData.get(data) %s ", contract, recentBarData.get(data))
        stpPrice = float(high_value) + float(slValue)
        logging.info(
            f"sl calculation for selling %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
        pass
    elif stoploss_type == Config.stopLoss[3]:
        low_value = 0
        recentBarData = connection.getHistoricalChartDataForEntry(contract, timeframe,   chartTime)
        for data in range(0, (len(recentBarData) )):
            if (low_value == 0 or low_value > recentBarData.get(data)['low']):
                low_value = recentBarData.get(data)['low']
                logging.info(f"low value found for %s recentBarData.get(data) %s ", contract,  recentBarData.get(data))

        stpPrice = float(low_value) - float(slValue)
        logging.info(
            f"sl calculation for selling %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
        pass

    stpPrice = round(stpPrice, Config.roundVal)
    return stpPrice
def get_sl_for_buying(connection,stoploss_type,filled_price, histData,slValue ,contract,timeframe,chartTime):
    # we are sending buy sl order
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
    elif stoploss_type== Config.stopLoss[2]:
        high_value = 0
        recentBarData = connection.getHistoricalChartDataForEntry(contract, timeframe,
                                                                  chartTime)
        for data in range(0, (len(recentBarData) )):
            if (high_value == 0 or high_value < recentBarData.get(data)['high']):
                high_value = recentBarData.get(data)['high']
                logging.info(f"high value found for %s recentBarData.get(data) %s ", contract, recentBarData.get(data))
        stpPrice = float(high_value) + float(slValue)
        logging.info(
            f"sl calculation for buying %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
        pass
    elif stoploss_type == Config.stopLoss[3]:
        low_value = 0
        recentBarData = connection.getHistoricalChartDataForEntry(contract, timeframe,
                                                                  chartTime)
        for data in range(0, (len(recentBarData) )):
            if (low_value == 0 or low_value > recentBarData.get(data)['low']):
                low_value = recentBarData.get(data)['high']
                logging.info(f"low value found for %s recentBarData.get(data) %s ", contract, recentBarData.get(data))

        stpPrice = float(low_value) - float(slValue)
        logging.info(
            f"sl calculation for buying %s , price %s  stoploss_type %s ,filled_price %s , histData %s ,slValue  %s ,timeframe %s ,chartTime %s",
            contract, stpPrice, stoploss_type, filled_price, histData, slValue, timeframe, chartTime)
        pass

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
        if (entryData['status'] == "Filled" and entryData['ordType'] == "Entry"):
            loop = asyncio.get_event_loop()
            if (entryData['action'] == "BUY"):
                logging.info("Market order filled we will send buy Order, market data is %s",entryData)
                asyncio.ensure_future(sendTpSlSell(connection, entryData))
            else:
                logging.info("Market order filled we will send sell Order, market data is %s",entryData)
                asyncio.ensure_future(sendTpSlBuy(connection, entryData))

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
        #  entry sell so buy tp sl order
        while True:
            histData = None
            if entryData['barType'] == Config.entryTradeType[0] or entryData['barType'] == Config.entryTradeType[1] or entryData['barType'] == Config.entryTradeType[2]  or entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7] or entryData['barType'] == Config.entryTradeType[8]:
                histData = entryData['histData']
            else:
                chartTime = getRecentChartTime(entryData['timeFrame'])
                histData = connection.getHistoricalChartData(entryData['contract'], entryData['timeFrame'], chartTime)
                if (len(histData) == 0):
                    logging.info("Chart Data is Not Comming for %s contract  and for %s time", entryData['contract'], chartTime)
                    await asyncio.sleep(2)
                    continue
                # histData = entryData['histData']

            price = 0
            filled_price = Config.orderFilledPrice.get(entryData['orderId'])
            logging.info("In TPSL %s contract  and for %s histdata",
                         entryData['contract'], histData)
            if (entryData['barType'] == Config.entryTradeType[3]) or (entryData['barType'] == Config.entryTradeType[4]) or (entryData['barType'] == Config.entryTradeType[5]):
                candleData = connection.getDailyCandle(entryData['contract'])
                if (candleData == None or len(candleData) < 1):
                    logging.info("candle data not found for %s", entryData['contract'])
                    await  asyncio.sleep(1)
                    continue
                logging.info(" Candle Data for takeProfit %s and contract is %s", candleData[-1], entryData['contract'])

                try:
                    stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                except Exception:
                    stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)

                multiplier_map = {
                    Config.takeProfit[0]: 1,
                    Config.takeProfit[1]: 1.5,
                    Config.takeProfit[2]: 2,
                    Config.takeProfit[3]: 3,
                    Config.takeProfit[5]: 2.5,
                }

                multiplier = multiplier_map.get(entryData['profit'])
                if multiplier is not None:
                    price = float(filled_price) - (multiplier * stop_size)
                else:
                    price = float(histData['low'])

                price = round(price, Config.roundVal)
                logging.info(
                    "Extended TP calculation (buy) %s stop_size=%s multiplier=%s filled=%s price=%s",
                    entryData['contract'], stop_size, multiplier, filled_price, price,
                )

            else:
                price = get_tp_for_buying(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], filled_price, histData)

            logging.info("Sending TP Trade EntryData is %s  and Price is %s  and action is BUY",entryData,price)
            sendTakeProfit(connection, entryData, price, "BUY")

            stpPrice=0
            chart_Time = datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime,
                                                   "%Y-%m-%d %H:%M:%S")
            stpPrice = get_sl_for_buying(connection, entryData['stopLoss'], filled_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
            logging.info(f"minus 0.01 in stop entry is buying {stpPrice}")
            stpPrice = stpPrice + 0.01
            logging.info("Sending STPLOSS Trade EntryData is %s  and Price is %s and action is BUY and hist Data [ %s ]", entryData, stpPrice,histData)
            sendStopLoss(connection, entryData, stpPrice, "BUY")
            mocPrice = 0
            logging.info("Sending Moc Order  of %s price ",mocPrice)
            sendMoc(connection,entryData,mocPrice,"BUY")
            break
    except Exception as e:
        logging.error("error in take profit and sl buy trade %s",e)
        print(e)


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

        extended_hist_supported = (
            is_extended and isinstance(hist_data, dict) and
            'high' in hist_data and 'low' in hist_data
        )

        if extended_hist_supported:
            stop_size, entry_offset, protection_offset = _calculate_stop_limit_offsets(hist_data)
            if action.upper() == "BUY":
                limit_price = adjusted_price + protection_offset
            else:
                limit_price = adjusted_price - protection_offset

            order_type = "STP LMT"
            order_kwargs['orderType'] = "STP LMT"
            order_kwargs['lmtPrice'] = round(limit_price, Config.roundVal)
            logging.info(
                "Extended hours protection stop-limit: action=%s stop=%s limit=%s stop_size=%s entry_offset=%s protection_offset=%s session=%s",
                action, adjusted_price, order_kwargs['lmtPrice'], stop_size, entry_offset, protection_offset, session
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
        # if action == "BUY":
        #     price = price - 1
        # else:
        #     price = price + 1
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
        #  if entry buy
        while True:
            histData = None
            if entryData['barType'] == Config.entryTradeType[0] or entryData['barType'] == Config.entryTradeType[6] or entryData['barType'] == Config.entryTradeType[7] or entryData['barType'] == Config.entryTradeType[8]:
                histData = entryData['histData']
            else:
                chartTime = getRecentChartTime(entryData['timeFrame'])
                histData = connection.getHistoricalChartData(entryData['contract'], entryData['timeFrame'], chartTime)
                if (len(histData) == 0):
                    logging.info("In TPSL SELL Chart Data is Not Comming for %s contract  and for %s time", entryData['contract'], chartTime)
                    await asyncio.sleep(1)
                    continue
                # histData = entryData['histData']
            price = 0
            filled_price = Config.orderFilledPrice.get(entryData['orderId'])
            logging.info("In TPSL %s contract  and for %s histdata",
                         entryData['contract'], histData)
            if (entryData['barType'] == Config.entryTradeType[3]) or (entryData['barType'] == Config.entryTradeType[4]) or (entryData['barType'] == Config.entryTradeType[5]):
                candleData = connection.getDailyCandle(entryData['contract'])
                if (candleData == None or len(candleData) < 1):
                    logging.info("candle data not found for %s", entryData['contract'])
                    await  asyncio.sleep(1)
                    continue
                logging.info(" Candle Data for takeProfit %s and contract is %s", candleData[-1],entryData['contract'])
                try:
                    stop_size, _, _ = _calculate_stop_limit_offsets(histData)
                except Exception:
                    stop_size = round((float(histData['high']) - float(histData['low']) + Config.add002), Config.roundVal)

                multiplier_map = {
                    Config.takeProfit[0]: 1,
                    Config.takeProfit[1]: 1.5,
                    Config.takeProfit[2]: 2,
                    Config.takeProfit[3]: 3,
                    Config.takeProfit[5]: 2.5,
                }

                multiplier = multiplier_map.get(entryData['profit'])
                if multiplier is not None:
                    price = float(filled_price) + (multiplier * stop_size)
                else:
                    price = float(histData['high'])

                price = round(price, Config.roundVal)
                logging.info(
                    "Extended TP calculation (sell) %s stop_size=%s multiplier=%s filled=%s price=%s",
                    entryData['contract'], stop_size, multiplier, filled_price, price,
                )
            else:
                price = get_tp_for_selling(connection,entryData['timeFrame'],entryData['contract'], entryData['profit'], filled_price, histData)

            logging.info("Sending TP Trade EntryData is %s  and Price is %s  and action is SELL", entryData, price)
            sendTakeProfit(connection, entryData, price, "SELL")


            stpPrice=0
            chart_Time = datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime,  "%Y-%m-%d %H:%M:%S")
            stpPrice = get_sl_for_selling(connection, entryData['stopLoss'], filled_price, entryData['histData'] , entryData['slValue'], entryData['contract'],  entryData['timeFrame'], chart_Time)
            logging.info(f"minus 0.01 in stop entry is buying {stpPrice}")
            stpPrice = stpPrice - 0.01
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

