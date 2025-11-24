import asyncio
import datetime
import math
import threading
import time
import traceback

import Config
from header import *
from SendTrade import *

class connection:

    def __init__(self):
        self.ib = IB()
        self._order_id_lock = threading.Lock()
        self._order_id_counter = None

    # it will set trade order status value in global variable.
    def orderStatusEvent(self,trade: Trade):
        if  trade.orderStatus.status == 'Filled':
            Config.orderFilledPrice.update({ trade.order.orderId :  trade.orderStatus.avgFillPrice })

        if Config.orderStatusData.get(trade.order.orderId) != None:
            data = Config.orderStatusData.get(trade.order.orderId)
            data.update({'status': trade.orderStatus.status})
            Config.orderStatusData.update({trade.order.orderId: data})
            # Exclude manual orders (Stop Order, Limit Order) from sendTpAndSl during regular hours
            # because they already send bracket orders. Extended hours manual orders need sendTpAndSl.
            is_manual_order = data.get('barType', '') in Config.manualOrderTypes
            is_extended_hours = data.get('outsideRth', False)
            should_send_tp_sl = (
                data['barType'] != Config.entryTradeType[0] and
                not (is_manual_order and not is_extended_hours)  # Skip manual orders in regular hours
            )
            if should_send_tp_sl:
                sendTpAndSl(self, data)

    # tws connection stablish
    def connect(self):
        try:
            self.ib.connect(host=Config.host, port=Config.port, clientId=Config.clientId)
            # self.ib.waitOnUpdate()
            self.ib.orderStatusEvent += self.orderStatusEvent
            self.pnlEvent = self.pnlData
            # self.ib.pendingTickersEvent += self.onPendingTickers
            # self.reqPnl()
            self._initialize_order_ids()
        except Exception as e:
            logging.error("Error in ib connection " + str(e))
            return False

    def _initialize_order_ids(self):
        """Fetch the next valid order id from IB."""
        try:
            # In ib_insync, reqIds is on the client object, not the IB object
            if hasattr(self.ib.client, 'reqIds'):
                self.ib.client.reqIds(1)
            else:
                # Alternative: wait for nextValidId to be set automatically
                logging.info("reqIds not available, waiting for nextValidId from connection")
            
            start = time.time()
            # Wait for orderIdSeq to be populated (this is set when nextValidId is received)
            while getattr(self.ib.client, "orderIdSeq", None) is None:
                if time.time() - start > 5:
                    break
                self.ib.waitOnUpdate(timeout=1)
            
            next_id = getattr(self.ib.client, "orderIdSeq", None)
            if next_id is None:
                # Fallback: try to get nextValidId directly if available
                if hasattr(self.ib, 'nextValidOrderId') and self.ib.nextValidOrderId:
                    next_id = self.ib.nextValidOrderId
                else:
                    next_id = int(time.time())
                    logging.warning("nextValidId not received, defaulting order id seed to %s", next_id)
            else:
                logging.info("Order ID initialized from orderIdSeq: %s", next_id)
            
            with self._order_id_lock:
                self._order_id_counter = int(next_id)
        except Exception as err:
            logging.error("Unable to initialize order ids: %s", err)
            # Fallback to time-based ID
            with self._order_id_lock:
                self._order_id_counter = int(time.time())
                logging.warning("Using time-based order ID seed: %s", self._order_id_counter)

    def get_next_order_id(self):
        with self._order_id_lock:
            if self._order_id_counter is None:
                self._order_id_counter = int(time.time())
            next_id = self._order_id_counter
            self._order_id_counter += 1
            return next_id
    def reqPnl(self):
        try:
            print("req pnl initializing...")
            accountValues = self.getAccountValue()
            if accountValues and len(accountValues) > 0:
                account = accountValues[0].account
                self.ib.reqPnL(account=account)
                asyncio.ensure_future(self.pnlData())
                print(f"PnL request successful for account: {account}")
            else:
                logging.warning("Could not get account values. PnL tracking disabled.")
                print("Warning: No account info available. PnL tracking disabled.")
                print("This is normal if TWS is not connected yet.")
        except Exception as e:
            logging.error(f"Error requesting PnL: {e}")
            print(f"Warning: Could not initialize PnL tracking: {e}")

    async def pnlData(self):
        try:
            nest_asyncio.apply()
            await asyncio.sleep(1)
            accountValues = self.getAccountValue()
            if not accountValues or len(accountValues) == 0:
                logging.warning("No account values available for PnL tracking")
                return
            
            account = accountValues[0].account
            while True:
                try:
                    acc = self.ib.pnl(account=account)
                    if len(acc) > 0:
                        pnl = acc[0].dailyPnL
                        print(pnl)
                        if not math.isnan(pnl):
                            Config.currentPnl = pnl
                    else:
                        # print(acc)
                        pass
                except Exception as e:
                    logging.error(f"Error getting PnL data: {e}")
                await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"Error in pnlData loop: {e}")
            print(f"PnL tracking stopped: {e}")

    # when application will start if tws not connected then tkinter will check ib status regularly
    def ibStatusCheck(self):
        if self.ib.isConnected():
            return True
        else:
            return False

    # place trade on tws
    def placeTrade(self, contract, order , outsideRth =False):
        # nest_asyncio.apply()
        session = self._get_current_session()
        logging.info("placeTrade: session=%s, outsideRth=%s, orderType=%s", session, outsideRth, order.orderType)
        
        if outsideRth == False or outsideRth == 'False':
            order.outsideRth = False
        else:
            # Outside regular hours: decide behavior by session
            order.outsideRth = True
            if session == 'OVERNIGHT':
                # Overnight: ALL orders must be converted to LMT - no exceptions
                originalOrderType = order.orderType
                logging.info(f"Overnight session: Converting order type {originalOrderType} to LMT")
                
                try:
                    # If already LMT, check if lmtPrice exists, otherwise get price
                    if order.orderType == 'LMT':
                        if not hasattr(order, 'lmtPrice') or order.lmtPrice is None or order.lmtPrice == 0:
                            # LMT order without price - need to get price
                            logging.info("Overnight session: LMT order without price, getting price from market data")
                            limitPrice = self._get_price_for_overnight_order(contract, order.action)
                            order.lmtPrice = limitPrice
                            logging.info(f"Overnight session: Set LMT price to {limitPrice}")
                        else:
                            logging.info(f"Overnight session: LMT order already has price {order.lmtPrice}")
                    
                    # Convert MKT to LMT
                    elif order.orderType == 'MKT':
                        limitPrice = self._get_price_for_overnight_order(contract, order.action)
                        order.orderType = 'LMT'
                        order.lmtPrice = limitPrice
                        logging.info(f"Overnight session: Converting MKT to LMT at {limitPrice}")
                    
                    # Convert STP/STP LMT to LMT
                    elif order.orderType == 'STP' or order.orderType == 'STP LMT':
                        # Use auxPrice if available, otherwise get market price
                        if hasattr(order, 'auxPrice') and order.auxPrice:
                            limitPrice = order.auxPrice
                            logging.info(f"Overnight session: Using auxPrice {limitPrice} for STP conversion")
                        else:
                            logging.info("Overnight session: No auxPrice, getting market price for STP conversion")
                            limitPrice = self._get_price_for_overnight_order(contract, order.action)
                        order.orderType = 'LMT'
                        order.lmtPrice = limitPrice
                        # Clear auxPrice since we're converting to LMT
                        if hasattr(order, 'auxPrice'):
                            order.auxPrice = 0
                        logging.info(f"Overnight session: Converting {originalOrderType} to LMT at {limitPrice}")
                    
                    # Any other order type - convert to LMT
                    else:
                        # For any other order type, try to get price from existing fields or market data
                        limitPrice = None
                        if hasattr(order, 'lmtPrice') and order.lmtPrice:
                            limitPrice = order.lmtPrice
                            logging.info(f"Overnight session: Using existing lmtPrice {limitPrice}")
                        elif hasattr(order, 'auxPrice') and order.auxPrice:
                            limitPrice = order.auxPrice
                            logging.info(f"Overnight session: Using auxPrice {limitPrice}")
                        else:
                            logging.info("Overnight session: Getting market price for order conversion")
                            limitPrice = self._get_price_for_overnight_order(contract, order.action)
                        
                        order.orderType = 'LMT'
                        order.lmtPrice = limitPrice
                        # Clear auxPrice if it exists
                        if hasattr(order, 'auxPrice'):
                            order.auxPrice = 0
                        logging.info(f"Overnight session: Converting {originalOrderType} to LMT at {limitPrice} (all orders must be LMT during overnight)")
                except Exception as e:
                    logging.error(f"Overnight session: Error converting order {originalOrderType} to LMT: {e}")
                    logging.error(f"Overnight session: Order details - action={order.action}, auxPrice={getattr(order, 'auxPrice', 'N/A')}, lmtPrice={getattr(order, 'lmtPrice', 'N/A')}")
                    # Re-raise the exception so the caller knows the order failed
                    raise
            else:
                # Pre-market / After-hours: allow same types as RTH, no conversion
                logging.info("%s session: passing order type %s without conversion", session, order.orderType)

        # Check if a Trade already exists for this order ID and handle it
        # This prevents AssertionError when ib_insync detects an order in done state
        try:
            existing_trades = self.ib.trades()
            # ib.trades() returns a dict-like object keyed by order ID
            if order.orderId and hasattr(existing_trades, '__contains__') and order.orderId in existing_trades:
                existing_trade = existing_trades[order.orderId]
                if hasattr(existing_trade, 'orderStatus') and existing_trade.orderStatus.status in ['Filled', 'Cancelled', 'Inactive']:
                    logging.warning("Order ID %s already has a Trade in done state (%s). This may cause issues.", 
                                  order.orderId, existing_trade.orderStatus.status)
                    # Try to remove it from the trades collection
                    try:
                        if hasattr(existing_trades, '__delitem__'):
                            del existing_trades[order.orderId]
                            logging.info("Removed existing done Trade for orderId %s", order.orderId)
                    except Exception as e:
                        logging.warning("Could not remove existing Trade for orderId %s: %s", order.orderId, e)
        except Exception as e:
            logging.debug("Error checking existing trades: %s (this is usually fine)", e)
        
        try:
            response = self.ib.placeOrder(contract=contract, order=order)
            return response
        except AssertionError as e:
            # This happens when ib_insync detects the order is already in a done state
            # Usually means the order ID was reused or there's a cached Trade object
            error_msg = f"AssertionError placing order {order.orderId}: Order may already be in a done state. " \
                       f"This can happen if order IDs are reused. Try again with a new order ID."
            logging.error(error_msg)
            logging.error("Order details: orderId=%s, orderType=%s, action=%s, status=%s", 
                         order.orderId, order.orderType, order.action, 
                         getattr(self.ib.trades().get(order.orderId, None), 'orderStatus.status', 'N/A') if order.orderId in self.ib.trades() else 'N/A')
            raise Exception(error_msg) from e

    def _get_price_for_overnight_order(self, contract, action):
        """Get price for overnight order - tries multiple methods"""
        # Try to get live price from tick data
        try:
            self.subscribeTicker(contract)
            priceObj = self.getTickByTick(contract)
            if priceObj != None:
                lastPrice = priceObj.marketPrice()
                self.cancelTickData(contract)
                # Add 2% buffer for BUY, subtract 2% for SELL
                if action == 'BUY':
                    lastPrice = lastPrice + ((lastPrice / 100) * 2)
                else:
                    lastPrice = lastPrice - ((lastPrice / 100) * 2)
                logging.info("Overnight: Got price from tick data: %s", lastPrice)
                return round(lastPrice, 2)
        except Exception as e:
            logging.warning("Overnight: Could not get tick data: %s", e)
        
        # Fallback 1: use 1-min historical data
        try:
            logging.info("Overnight: Trying 1-min historical data for price")
            histData = self.getChartData(contract, '1 min', datetime.datetime.now())
            if len(histData) > 0:
                lastPrice = histData[-1].close
                # Add 2% buffer for BUY, subtract 2% for SELL
                if action == 'BUY':
                    lastPrice = lastPrice + ((lastPrice / 100) * 2)
                else:
                    lastPrice = lastPrice - ((lastPrice / 100) * 2)
                logging.info("Overnight: Got price from 1-min historical data: %s", lastPrice)
                return round(lastPrice, 2)
        except Exception as e:
            logging.warning("Overnight: Could not get 1-min historical data: %s", e)
        
        # Fallback 2: use daily candle data (last close price)
        try:
            logging.info("Overnight: Trying daily candle data for price")
            dailyData = self.getDailyCandle(contract)
            if len(dailyData) > 0:
                lastPrice = dailyData[-1].close
                # Add 2% buffer for BUY, subtract 2% for SELL
                if action == 'BUY':
                    lastPrice = lastPrice + ((lastPrice / 100) * 2)
                else:
                    lastPrice = lastPrice - ((lastPrice / 100) * 2)
                logging.info("Overnight: Got price from daily candle data: %s", lastPrice)
                return round(lastPrice, 2)
        except Exception as e:
            logging.warning("Overnight: Could not get daily candle data: %s", e)
        
        # If all else fails, we can't get price - this should not happen but log it
        error_msg = f"Cannot get price for overnight order - no tick data or historical data available for {contract}"
        logging.error(error_msg)
        raise Exception(error_msg)

    def _get_current_session(self):
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

    def cancelTrade(self, order):
        logging.info("Going to Cancel Trade For " + str(order))
        response = self.ib.cancelOrder(order)
        return response


    def getFullDayData(self, ibcontract, timeFrame, configTime):
        nest_asyncio.apply()
        logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime, timeFrame, ibcontract)
        histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                             useRTH=False)
        # if (len(histData) < (Config.pullBackNo + 2
        if (len(histData) < (Config.pullBackNo)):
            logging.info("historical data not found for %s contract , time frame %s, time %s", ibcontract, timeFrame, configTime)
            return {}

        historical={}
        histData.reverse()
        x=0;
        for data in histData:
            if data.date.date() == datetime.datetime.now().date():
                if(configTime.time() <= data.date.time()):
                    # checking trading time......
                    if(data.date.time() >= datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime,"%Y-%m-%d %H:%M:%S").time() ):
                        historical.update({x:{"date":data.date,"close": data.close, "open": data.open, "high": data.high, "low": data.low}})
                        x = x +1

        return historical

    def BracketOrder(self,parentOrderId, action, quantity, limitPrice, takeProfitLimitPrice, stopLossPrice):
        parent = Order()
        parent.orderId = parentOrderId
        parent.action = action
        parent.orderType = "MKT"
        parent.totalQuantity = quantity
        parent.lmtPrice = limitPrice
        parent.transmit = True

        takeProfit = Order()
        takeProfit.orderId = parent.orderId + 1
        takeProfit.action = "SELL" if action.upper() == "BUY" else "BUY"
        takeProfit.orderType = "LMT"
        takeProfit.totalQuantity = quantity
        takeProfit.lmtPrice = takeProfitLimitPrice
        takeProfit.parentId = parentOrderId
        takeProfit.transmit = True

        stopLoss = Order()
        stopLoss.orderId = parent.orderId + 2
        stopLoss.action = "SELL" if action.upper() == "BUY" else "BUY"
        stopLoss.orderType = "STP"
        stopLoss.auxPrice = stopLossPrice
        stopLoss.totalQuantity = quantity
        stopLoss.parentId = parentOrderId
        stopLoss.transmit = True
        bracketOrder = [parent, takeProfit, stopLoss]
        return bracketOrder

    def getHistoricalChartDataForEntry(self, ibcontract, timeFrame, configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime, timeFrame, ibcontract)
            histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                                 useRTH=False)
            if (len(histData) < 2):
                logging.info("historical data not found for %s contract , time frame %s, time %s", ibcontract, timeFrame, configTime)
                return {}

            oldRow = None
            historical = {}
            i=0
            configTime = configTime.time().replace(microsecond=0)

            # for x in range(Config.pullBackNo + 1):
            #     no = (i - (x + 1))
            #     historical.update({(x + 1): {"date":histData[no].date,"close": histData[no].close, "open": histData[no].open, "high": histData[no].high, "low": histData[no].low}})
                # print(histData[no])
            x=0
            for d in histData:
                if (d.date.date() == datetime.datetime.now().date()) and (d.date.time() >= datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime, "%Y-%m-%d %H:%M:%S").time()):
                    historical.update({x: {"date": d.date, "close": d.close,
                                                 "open": d.open, "high": d.high,
                                                 "low": d.low}})
                    x = x+1

            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def getDailyCandle(self, ibcontract):
        try:
            nest_asyncio.apply()
            # Request enough days for ATR calculation: atrPeriod (20) + buffer for weekends/holidays
            # Request 40 days to ensure we have at least 21 trading days
            duration_days = max(40, Config.atrPeriod + 20)  # At least 40 days, or atrPeriod + 20
            logging.info("we are getting %s days candle data for %s contract (ATR period=%s)", duration_days, ibcontract, Config.atrPeriod)
            histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=f'{duration_days} D', barSizeSetting='1 day',
                                                 useRTH=False)

            return histData
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def getChartData(self,ibcontract,timeFrame,configTime):
        histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                             useRTH=False)
        return histData

    def get_recent_close_price_data(self, ibcontract, timeFrame, configTime):
        try:
            nest_asyncio.apply()
            logging.info("for close price we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime,
                         timeFrame, ibcontract)
            histData = self.getChartData(ibcontract, timeFrame, configTime)
            if (len(histData) == 0):
                logging.info("historical data not found for close price %s contract , time frame %s, time %s", ibcontract,
                             timeFrame, configTime)
                return {}

            oldRow = None
            historical = {}
            oldRow = histData[-1]
            historical = {"close": oldRow.close, "open": oldRow.open, "high": oldRow.high,
                          "low": oldRow.low, "dateTime": oldRow.date}

            logging.info("historical data found %s ", historical)
            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def lb1_entry_historical_data(self, ibcontract, timeFrame, configTime):
        try:
            nest_asyncio.apply()
            logging.info("entry_historical_data we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime, timeFrame, ibcontract)
            histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                                 useRTH=False)
            if (len(histData) < 2):
                logging.info("historical data not found for %s contract , time frame %s, time %s", ibcontract, timeFrame, configTime)
                return {}

            oldRow = None
            historical = {}
            i=0
            configTime = configTime.time().replace(microsecond=0)
            x=0
            for d in histData:
                if (d.date.date() == datetime.datetime.now().date()) and (d.date.time() >= configTime):
                    historical.update({x: {"date": d.date, "close": d.close,
                                                 "open": d.open, "high": d.high,
                                                 "low": d.low}})
                    x = x+1

            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def pbe1_entry_historical_data(self, ibcontract, timeFrame, configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ", configTime, timeFrame, ibcontract)
            histData = self.ib.reqHistoricalData(contract=ibcontract, endDateTime='', formatDate=1, whatToShow=Config.whatToShow, durationStr=Config.durationStr, barSizeSetting=timeFrame,
                                                 useRTH=False)
            if (len(histData) < 2):
                logging.info("historical data not found for %s contract , time frame %s, time %s", ibcontract, timeFrame, configTime)
                return {}

            oldRow = None
            historical = {}
            i=0
            configTime = configTime.time().replace(microsecond=0)
            x=0
            for d in histData:

                if (d.date.date() == datetime.datetime.now().date()) and (d.date.time() >= datetime.datetime.strptime(str(datetime.datetime.now().date()) + " " + Config.tradingTime, "%Y-%m-%d %H:%M:%S").time()):

                    historical.update({x: {"date": d.date, "close": d.close,
                                                 "open": d.open, "high": d.high,
                                                 "low": d.low}})
                    x = x + 1

            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def fb_entry_historical_data(self,ibcontract,timeFrame,configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ",configTime,timeFrame,ibcontract)
            histData = self.getChartData(ibcontract,timeFrame,configTime)
            if(len(histData) == 0):
                logging.info("historical data not found for %s contract , time frame %s, time %s",ibcontract,timeFrame,configTime)
                return {}

            oldRow=None
            historical ={}
            configTime = configTime.time().replace(microsecond=0)
            for data in histData:
                chart_date = data.date.date()
                if (datetime.datetime.now().date() == chart_date) and (data.date.time() >= configTime):
                    # here we are checking if time 9:31 thenwe will get 9:30 data...
                    if(oldRow != None and (oldRow.date.time() == configTime)):
                        logging.info("we are adding this row in historical %s   {For %s contract }",oldRow,ibcontract)
                        if (data.date.date() == datetime.datetime.now().date()) and (data.date.time() >= datetime.datetime.strptime( str(datetime.datetime.now().date()) + " " + Config.tradingTime,  "%Y-%m-%d %H:%M:%S").time()):
                            historical = {"close": oldRow.close, "open": oldRow.open, "high": oldRow.high, "low": oldRow.low,"dateTime":oldRow.date}
                            break
                oldRow = data
            logging.info("historical data found %s ",historical)
            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    def rbb_entry_historical_data(self,ibcontract,timeFrame,configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ",configTime,timeFrame,ibcontract)
            histData = self.getChartData(ibcontract,timeFrame,configTime)
            if(len(histData) == 0):
                logging.info("historical data not found for %s contract , time frame %s, time %s",ibcontract,timeFrame,configTime)
                return {}

            oldRow=None
            historical ={}
            # prev_time = configTime - datetime.timedelta(minutes=1)
            # prev_time = prev_time.time().replace(microsecond=0)
            # prev_time = prev_time.replace(second=0)
            configTime = configTime.time().replace(microsecond=0)
            configTime = configTime.replace(second=0)
            for data in histData:
                chart_date = data.date.date()
                #   todo  need to remove
                # chart_date =datetime.datetime.now().date()
                # configTime = datetime.datetime.strptime("2023-04-01 17:15:00","%Y-%m-%d %H:%M:%S").time()
                if (datetime.datetime.now().date() == chart_date) and (data.date.time() == configTime):
                    logging.info("we are adding this row in historical %s   {For %s contract }",data,ibcontract)
                    # if (data.date.time() == prev_time) and (data.date.time() >= datetime.datetime.strptime( str(datetime.datetime.now().date()) + " " + Config.tradingTime,  "%Y-%m-%d %H:%M:%S").time()):
                    historical = {"close": data.close, "open": data.open, "high": data.high, "low": data.low,"dateTime":data.date}
                    break
                oldRow = data
            logging.info("historical data found %s ",historical)
            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))


    def getHistoricalChartData(self,ibcontract,timeFrame,configTime):
        try:
            nest_asyncio.apply()
            logging.info("we are getting chart date of %s time and for %s time frame and  for %s contract ",configTime,timeFrame,ibcontract)
            histData = self.getChartData(ibcontract,timeFrame,configTime)
            if(len(histData) == 0):
                logging.info("historical data not found for %s contract , time frame %s, time %s",ibcontract,timeFrame,configTime)
                return {}

            oldRow=None
            historical ={}


            configTime = configTime.time().replace(microsecond=0)
            for data in histData:
                chart_date = data.date.date()

                #   todo  need to remove
                # chart_date =datetime.datetime.now().date()
                # configTime = datetime.datetime.strptime("2023-04-01 17:15:00","%Y-%m-%d %H:%M:%S").time()

                if (datetime.datetime.now().date() == chart_date) and (data.date.time() >= configTime):
                    if(oldRow != None and (oldRow.date.time() == configTime)):
                        logging.info("we are adding this row in historical %s   {For %s contract }",oldRow,ibcontract)
                        if (data.date.date() == datetime.datetime.now().date()) and (data.date.time() >= datetime.datetime.strptime( str(datetime.datetime.now().date()) + " " + Config.tradingTime,  "%Y-%m-%d %H:%M:%S").time()):
                            historical = {"close": oldRow.close, "open": oldRow.open, "high": oldRow.high, "low": oldRow.low,"dateTime":oldRow.date}
                            break
                oldRow = data
            logging.info("historical data found %s ",historical)
            return historical
        except Exception as e:
            logging.error('getHistoricalData ' + str(e))

    #  with the help of this function we are unsubscribe ticker event. activate ticker event by getTickByTick function.
    def cancelTickData(self,currencyPair):
        try:
            nest_asyncio.apply()
            execution = self.ib.cancelMktData(contract=currencyPair)
            return execution
        except Exception as e:
            logging.error('cancel market data ' + str(e))

    def getAccountValue(self):
        try:
            val = self.ib.accountValues()
            if val and len(val) > 0:
                logging.info("Account value found: " + str(val[:3]))  # Log first 3 items
            else:
                logging.warning("No account values returned from IB")
            return val
        except Exception as e:
            logging.error(f"Error getting account values: {e}")
            return []


    # req market data gives data in ticker so firstly we need to define event function, see  onPendingTickers.
    def subscribeTicker(self,currencyPair):
        try:
            nest_asyncio.apply()
            self.ib.qualifyContracts(currencyPair)
            self.ib.reqMktData(currencyPair,'', False, False)
            self.ib.waitOnUpdate()
            self.ib.sleep(2)
        except Exception as e:
            logging.error('req market data ' + str(e))

    def cancelTickData(self,currencyPair):
        try:
            nest_asyncio.apply()
            execution = self.ib.cancelMktData(contract=currencyPair)

        except Exception as e:
            logging.error('cancel market data ' + str(e))

    def getTickByTick(self,currencyPair):
        try:
            tickers = self.ib.ticker(currencyPair)
            logging.info("Ticker Found " + str(tickers))
            return tickers
        except Exception as e:
            logging.error('req market data ' + str(e))

    def getAllOpenOrder(self):
        try:
            trades = self.ib.openTrades()
            logging.info('open trades --------------- %s ',trades)
            return trades
        except Exception as e:
            logging.error('get all open Trade ' + str(e))

    def getAllOpenPosition(self):
        try:
            trades = self.ib.positions()
            logging.info('open position --------------- %s ',trades)
            return trades
        except Exception as e:
            logging.error('get all open Trade ' + str(e))

    #  for tws disconnect
    def connection_close(self):
        if (self.ib.isConnected()):
            self.ib.disconnect()
            logging.info('TWS disconnect')
