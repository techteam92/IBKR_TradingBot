"""
Option Trading Module
Handles all option trading functionality without modifying core SendTrade.py logic
"""
import asyncio
import datetime
import logging
import math
import traceback
from ib_insync import Option, Order, Stock, PriceCondition
import Config


# Placeholder functions - will be implemented
def _resolve_option_from_dropdowns(connection, symbol, strike_code, expiry_code, right):
    """Resolve option contract from dropdown selections"""
    # Implementation needed
    pass

def getOptionContract(connection, symbol, strike, expiration_date, right='C'):
    """Get option contract"""
    # Implementation needed
    pass

async def placeOptionTrade(connection, symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price, 
                          entry_order_type, sl_order_type, tp_order_type, quantity, tif, buy_sell_type, risk_amount=None):
    """Place option trade"""
    # Implementation needed
    pass

async def placeOptionTradeAndStore(connection, symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price, 
                          entry_order_type, sl_order_type, tp_order_type, quantity, tif, buy_sell_type, risk_amount=None, stock_entry_order_id=None):
    """
    Place option trade and store order IDs.
    This function:
    1. Resolves option contract from dropdowns (ATM/OTM, weeks out)
    2. Calculates quantity from risk amount
    3. Places option entry order as conditional order
    4. Stores SL/TP parameters for later use
    """
    try:
        logging.info("placeOptionTradeAndStore: Starting for %s, contract=%s, expire=%s, entry=%s, sl=%s, tp=%s, risk=%s", 
                    symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price, risk_amount)
        
        # Get stock contract
        stock_contract = Stock(symbol, 'SMART', 'USD')
        stock_contract = connection.ib.qualifyContracts(stock_contract)[0]
        stock_exchange = stock_contract.exchange or 'SMART'
        
        # Get current stock price to determine strike
        stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
        connection.ib.sleep(0.5)
        current_stock_price = None
        if stock_ticker:
            last = getattr(stock_ticker, "last", None)
            close = getattr(stock_ticker, "close", None)
            if last and last > 0:
                current_stock_price = last
            elif close and close > 0:
                current_stock_price = close
        connection.ib.cancelMktData(stock_contract)
        
        if not current_stock_price:
            logging.error("Could not get current stock price for %s", symbol)
            return
        
        # Resolve strike from dropdown (ATM/OTM)
        strike_price = None
        if option_contract_str == "ATM":
            strike_price = round(current_stock_price, 0)  # Round to nearest dollar
        elif option_contract_str.startswith("OTM"):
            # OTM 1, OTM 2, OTM 3
            otm_steps = int(option_contract_str.replace("OTM", "").strip())
            if buy_sell_type == "BUY":
                # For BUY: OTM = above current price (call)
                strike_price = round(current_stock_price + (otm_steps * 1.0), 0)
            else:
                # For SELL: OTM = below current price (put)
                strike_price = round(current_stock_price - (otm_steps * 1.0), 0)
        else:
            logging.error("Unknown option contract code: %s", option_contract_str)
            return
        
        # Determine option right (Call for BUY, Put for SELL)
        option_right = 'C' if buy_sell_type == 'BUY' else 'P'
        
        # Resolve expiration date from weeks out
        expiry_weeks = int(option_expire) if option_expire else 0
        today = datetime.date.today()
        # Find next Friday (weekly options expire on Friday)
        days_until_friday = (4 - today.weekday()) % 7
        if days_until_friday == 0 and today.weekday() == 4:
            # If today is Friday, use next Friday
            days_until_friday = 7
        target_date = today + datetime.timedelta(days=days_until_friday + (expiry_weeks * 7))
        expiration_date = target_date.strftime("%Y%m%d")
        
        # Create and qualify option contract
        option_contract = Option(symbol, expiration_date, strike_price, option_right, 'SMART')
        qualified = connection.ib.qualifyContracts(option_contract)
        if not qualified:
            logging.error("Could not qualify option contract for %s %s %s %s", symbol, expiration_date, strike_price, option_right)
            return
        option_contract = qualified[0]
        
        # Get option prices for quantity calculation and order placement
        option_ticker = connection.ib.reqMktData(option_contract, '', False, False)
        connection.ib.sleep(0.5)
        opt_bid = getattr(option_ticker, "bid", None)
        opt_ask = getattr(option_ticker, "ask", None)
        opt_price = None
        if getattr(option_ticker, "last", None) and getattr(option_ticker, "last", None) > 0:
            opt_price = getattr(option_ticker, "last", None)
        elif opt_bid and opt_ask and opt_bid > 0 and opt_ask > 0:
            opt_price = (opt_bid + opt_ask) / 2.0
        elif getattr(option_ticker, "close", None) and getattr(option_ticker, "close", None) > 0:
            opt_price = getattr(option_ticker, "close", None)
        connection.ib.cancelMktData(option_contract)
        
        if not opt_price or opt_price <= 0:
            logging.error("Could not get option price for %s", symbol)
            return
        
        # Calculate quantity from risk amount
        option_quantity = 1
        if risk_amount:
            try:
                risk_amt = float(risk_amount)
                if risk_amt > 0:
                    # quantity = risk_amount / (contract_price * 100), rounded to nearest integer
                    # Use round() instead of ceil() to avoid buying too many contracts
                    option_quantity = int(round(risk_amt / (opt_price * 100)))
                    if option_quantity <= 0:
                        option_quantity = 1
                    logging.info("Option quantity calculated: risk=%s, opt_price=%s, quantity=%s (rounded)", risk_amt, opt_price, option_quantity)
            except (ValueError, TypeError):
                logging.warning("Invalid risk amount: %s, using quantity=1", risk_amount)
        
        # Determine action and trigger method for entry order
        # For BUY stock: Buy calls when stock crosses above entry_price
        # For SELL stock: Buy puts when stock crosses below entry_price
        if buy_sell_type == "BUY":
            action = "BUY"
            trigger_method = 1  # Crosses above
        else:
            action = "BUY"
            trigger_method = 2  # Breaks below
        
        # Check if current price is already at or beyond entry price
        # If so, don't place the order yet - store it for monitoring
        # The order should only trigger when price actually crosses the entry threshold
        should_place_order = True
        if current_stock_price is not None:
            if trigger_method == 1:  # Crosses above (BUY)
                if current_stock_price >= entry_price:
                    # Price is already at or above entry - condition already met
                    # Don't place order yet, store for monitoring
                    should_place_order = False
                    logging.info("Option entry: Current price (%.2f) is already at/above entry (%.2f). Storing order for monitoring instead of placing immediately.", 
                                current_stock_price, entry_price)
            else:  # Breaks below (SELL)
                if current_stock_price <= entry_price:
                    # Price is already at or below entry - condition already met
                    # Don't place order yet, store for monitoring
                    should_place_order = False
                    logging.info("Option entry: Current price (%.2f) is already at/below entry (%.2f). Storing order for monitoring instead of placing immediately.", 
                                current_stock_price, entry_price)
        
        if not should_place_order:
            # Store order parameters for monitoring
            # We'll place the order when price moves away and then crosses back
            pending_key = f"option_entry_{stock_entry_order_id}"
            Config.pending_option_orders[pending_key] = {
                'symbol': symbol,
                'option_contract': option_contract,
                'entry_price': entry_price,
                'trigger_method': trigger_method,
                'action': action,
                'option_quantity': option_quantity,
                'entry_order_type': entry_order_type,
                'tif': tif,
                'stock_contract': stock_contract,
                'stock_exchange': stock_exchange,
                'opt_bid': opt_bid,
                'opt_ask': opt_ask,
                'opt_price': opt_price,
                'stop_loss_price': stop_loss_price,
                'profit_price': profit_price,
                'sl_order_type': sl_order_type,
                'tp_order_type': tp_order_type,
                'buy_sell_type': buy_sell_type,
                'stock_entry_order_id': stock_entry_order_id
            }
            logging.info("Option entry order stored for monitoring. Will be placed when price crosses entry threshold.")
            # Start monitoring task
            asyncio.ensure_future(
                monitorAndPlacePendingOptionEntry(connection, pending_key, entry_price, trigger_method)
            )
            return
        
        # Current price is not at/beyond entry price - safe to place conditional order
        # Entry: If SPY crosses 680.54, buy X contracts
        logging.info("Option entry: Using exact entry_price=%.2f for conditional order (current_price=%.2f)", 
                    entry_price, current_stock_price)
        
        # Create entry order (using OptionEntry trade type for unique ID range)
        order_id = connection.get_next_order_id()
        order = Order()
        order.orderId = order_id
        order.action = action
        order.totalQuantity = option_quantity
        order.tif = tif
        
        # Set order type based on Market/Bid+/Ask-
        if entry_order_type == 'Market':
            order.orderType = 'MKT'
        elif entry_order_type == 'Bid+':
            order.orderType = 'LMT'
            if opt_bid and opt_bid > 0:
                order.lmtPrice = round(opt_bid, 2)
            elif opt_price:
                order.lmtPrice = round(opt_price, 2)
            else:
                order.lmtPrice = 0.01
        elif entry_order_type == 'Ask-':
            order.orderType = 'LMT'
            if opt_ask and opt_ask > 0:
                order.lmtPrice = round(opt_ask, 2)
            elif opt_price:
                order.lmtPrice = round(opt_price, 2)
            else:
                order.lmtPrice = 0.01
        else:
            logging.error("Unknown entry order type: %s", entry_order_type)
            return
        
        # Always add condition using exact entry price
        # Entry: If SPY crosses 680.54, buy X contracts
        condition = PriceCondition(
            price=entry_price,
            triggerMethod=trigger_method,
            conId=stock_contract.conId,
            exch=stock_exchange
        )
        order.conditions = [condition]
        order.conditionsIgnoreRth = False
        logging.info("Option entry order will be conditional: If %s %s %.2f, then %s %d contracts (%s)",
                    symbol, "crosses above" if trigger_method == 1 else "breaks below", entry_price,
                    action, option_quantity, entry_order_type)
        
        # Place the entry order
        trade = connection.placeTrade(option_contract, order, outsideRth=False)
        if not trade:
            logging.error("Failed to place option entry order")
            return
        
        option_entry_order_id = trade.order.orderId
        logging.info("Option entry order placed (conditional): If %s %s %.2f, then %s %d contracts (%s), orderId=%s",
                    symbol, "crosses above" if trigger_method == 1 else "breaks below", entry_price,
                    action, option_quantity, entry_order_type, option_entry_order_id)
        
        # Store order data
        if option_entry_order_id not in Config.orderStatusData:
            Config.orderStatusData[option_entry_order_id] = {}
        Config.orderStatusData[option_entry_order_id].update({
            'contract': option_contract,
            'action': action,
            'totalQuantity': option_quantity,
            'type': entry_order_type,
            'tif': tif,
            'ordType': 'OptionEntry',
            'usersymbol': symbol,
            'option_contract': option_contract,
            'option_quantity': option_quantity,
            'option_tif': tif,
            'option_symbol': symbol,
            'stock_exchange': stock_exchange,
            'stock_contract': stock_contract,
            'opt_bid': opt_bid,
            'opt_ask': opt_ask,
            'opt_price': opt_price,
            'condition_price': entry_price,  # Store condition price for adjustment monitoring
            'trigger_method': trigger_method,  # Store trigger method for adjustment monitoring
        })
        
        # Store SL/TP parameters for later use when stock orders fill
        # Stop Loss: Sell when stock breaks below stop_loss_price
        sl_action = "SELL"
        sl_trigger_method = 2  # Breaks below
        Config.orderStatusData[option_entry_order_id]['option_sl_params'] = {
            'action': sl_action,
            'order_type': sl_order_type,
            'condition_price': stop_loss_price,
            'trigger_method': sl_trigger_method,
        }
        
        # Take Profit: Sell when stock crosses above profit_price (for BUY) or below (for SELL)
        tp_action = "SELL"
        if buy_sell_type == "BUY":
            tp_trigger_method = 1  # Crosses above
        else:
            tp_trigger_method = 2  # Breaks below
        Config.orderStatusData[option_entry_order_id]['option_tp_params'] = {
            'action': tp_action,
            'order_type': tp_order_type,
            'condition_price': profit_price,
            'trigger_method': tp_trigger_method,
        }
        
        # Link option entry order to stock entry order
        if stock_entry_order_id:
            if stock_entry_order_id not in Config.orderStatusData:
                Config.orderStatusData[stock_entry_order_id] = {}
            if 'option_orders' not in Config.orderStatusData[stock_entry_order_id]:
                Config.orderStatusData[stock_entry_order_id]['option_orders'] = {}
            Config.orderStatusData[stock_entry_order_id]['option_orders']['entry'] = option_entry_order_id
        
        # Start monitoring and adjustment for Bid+/Ask- orders
        if entry_order_type in ('Bid+', 'Ask-') and order.orderType == 'LMT':
            initial_limit_price = order.lmtPrice
            asyncio.ensure_future(
                monitorAndAdjustBidAskOrder(connection, trade, entry_order_type, initial_limit_price, action)
            )
        
        logging.info("Option entry order placed and stored: orderId=%s, stock_entry_order_id=%s", 
                    option_entry_order_id, stock_entry_order_id)
        
    except Exception as e:
        logging.error("Error in placeOptionTradeAndStore: %s", e)
        logging.error(traceback.format_exc())

async def placeOptionEntryOrderImmediately(connection, stock_entry_order_id, symbol, entry_price, stop_loss_price, profit_price, 
                                         option_params, buy_sell_type, entry_data):
    """
    Place option entry order immediately when stock entry order is placed (not waiting for it to fill).
    Option entry order triggers when stock price crosses entry price.
    Option stop loss and take profit orders are placed when option entry fills (not waiting for stock orders to fill).
    """
    try:
        logging.info("placeOptionEntryOrderImmediately: Placing option entry order for %s, entry=%.2f, sl=%.2f, tp=%.2f", 
                    symbol, entry_price, stop_loss_price, profit_price)
        
        # Place option entry order immediately
        await placeOptionTradeAndStore(
            connection,
            symbol,
            option_params.get('contract'),
            option_params.get('expire'),
            entry_price,
            stop_loss_price,
            profit_price,
            option_params.get('entry_order_type', 'Market'),
            option_params.get('sl_order_type', 'Market'),
            option_params.get('tp_order_type', 'Market'),
            entry_data.get('totalQuantity', 1),
            entry_data.get('tif', 'DAY'),
            buy_sell_type or entry_data.get('action', 'BUY'),
            option_params.get('risk_amount'),
            stock_entry_order_id,
        )
        logging.info("Option entry order placed immediately for %s (will trigger when stock price crosses %.2f)", 
                    symbol, entry_price)
    except Exception as e:
        logging.error("Error in placeOptionEntryOrderImmediately: %s", e)
        logging.error(traceback.format_exc())

def handleOptionTrading(connection, entryData):
    """
    Handle option trading after stock entry fills.
    Delegates to handleOptionTradingForEntryFill with stock entry order ID and entry data.
    """
    try:
        stock_entry_order_id = entryData.get('orderId') if isinstance(entryData, dict) else None
        if stock_entry_order_id:
            handleOptionTradingForEntryFill(connection, stock_entry_order_id, entryData)
        else:
            logging.warning("handleOptionTrading: No orderId in entryData, cannot place option orders")
    except Exception as e:
        logging.error("Error in handleOptionTrading: %s", e)
        logging.error(traceback.format_exc())

def handleOptionTradingForEntryFill(connection, stock_entry_order_id, entry_data):
    """
    Handle option trading when stock entry order fills.
    This is called for Custom/Limit Order trades that use bracket orders.
    """
    try:
        symbol = entry_data.get('usersymbol') if isinstance(entry_data, dict) else None
        timeFrame = entry_data.get('timeFrame') if isinstance(entry_data, dict) else None
        barType = entry_data.get('barType') if isinstance(entry_data, dict) else None
        buySellType = entry_data.get('userBuySell') or (entry_data.get('action') if isinstance(entry_data, dict) else None)
        
        # Get entry_data from orderStatusData if not provided
        if not isinstance(entry_data, dict):
            entry_data = Config.orderStatusData.get(stock_entry_order_id, {})
            symbol = entry_data.get('usersymbol')
            timeFrame = entry_data.get('timeFrame')
            barType = entry_data.get('barType')
            buySellType = entry_data.get('userBuySell') or entry_data.get('action')
        
        # Retrieve option params: first from entry_data (passed by sendTpAndSl), else from Config.option_trade_params
        option_params = None
        if isinstance(entry_data, dict) and entry_data.get('option_params'):
            option_params = entry_data.get('option_params')
            logging.info("Option trading: Using option_params from entryData for %s", symbol)
        elif hasattr(Config, 'option_trade_params') and Config.option_trade_params:
            matching_key = None
            matching_params = None
            latest_ts = None
            logging.info("Option trading: Searching for option params. Looking for: symbol=%s, timeFrame=%s, barType=%s, buySellType=%s. Available keys: %s", 
                        symbol, timeFrame, barType, buySellType, list(Config.option_trade_params.keys()))
            for trade_key, params in list(Config.option_trade_params.items()):
                if len(trade_key) >= 5:
                    key_symbol, key_tf, key_bar, key_side, ts = trade_key
                    if (key_symbol == symbol and key_tf == timeFrame and key_bar == barType and key_side == buySellType):
                        if latest_ts is None or ts > latest_ts:
                            latest_ts = ts
                            matching_key = trade_key
                            matching_params = params
            
            if matching_key and matching_params and matching_params.get('enabled'):
                option_params = matching_params
                del Config.option_trade_params[matching_key]
                logging.info("Retrieved option params from Config.option_trade_params for %s", symbol)
        
        if not option_params or not option_params.get('enabled'):
            logging.warning("Option trading: No option params found or not enabled for %s", symbol)
            return
        
        # Get prices from entry_data
        # Use the monitored entry price if available (updated every second before fill)
        entry_price = entry_data.get('option_entry_price') or entry_data.get('filledPrice') or entry_data.get('lastPrice', 0)
        stop_loss_price = entry_data.get('stop_loss_price') or entry_data.get('stopLossPrice')
        profit_price = entry_data.get('tp_price') or entry_data.get('profit_price')
        
        # Try to get from stored TP/SL prices in entry order's orderStatusData
        if not stop_loss_price or not profit_price:
            entry_order_data = Config.orderStatusData.get(int(stock_entry_order_id), {})
            if not stop_loss_price:
                stop_loss_price = entry_order_data.get('stop_loss_price') or entry_order_data.get('stopLossPrice')
            if not profit_price:
                profit_price = entry_order_data.get('tp_price') or entry_order_data.get('profit_price')
        
        logging.info("handleOptionTradingForEntryFill: Using entry_price=%.2f (from option_entry_price=%s, filledPrice=%s, lastPrice=%s)", 
                    entry_price, entry_data.get('option_entry_price'), entry_data.get('filledPrice'), entry_data.get('lastPrice'))
        
        if entry_price and stop_loss_price and profit_price:
            # Place option orders asynchronously
            asyncio.ensure_future(
                placeOptionTradeAndStore(
                    connection,
                    symbol,
                    option_params.get('contract'),
                    option_params.get('expire'),
                    entry_price,
                    stop_loss_price,
                    profit_price,
                    option_params.get('entry_order_type', 'Market'),
                    option_params.get('sl_order_type', 'Market'),
                    option_params.get('tp_order_type', 'Market'),
                    entry_data.get('totalQuantity', 1),
                    entry_data.get('tif', 'DAY'),
                    buySellType or entry_data.get('action', 'BUY'),
                    option_params.get('risk_amount'),
                    stock_entry_order_id,
                )
            )
            logging.info("Option orders scheduled for %s (entry filled): entry=%s, sl=%s, tp=%s", 
                        symbol, entry_price, stop_loss_price, profit_price)
        else:
            logging.warning("Option trading: Cannot place option orders - missing prices: entry=%s, sl=%s, tp=%s", 
                           entry_price, stop_loss_price, profit_price)
    except Exception as e:
        logging.error("Error in handleOptionTradingForEntryFill: %s", e)
        logging.error(traceback.format_exc())

def handleOptionEntryFill(connection, option_entry_order_id):
    """
    Handle option entry order fill: Place stop loss and take profit orders immediately.
    These orders trigger when stock price reaches stop loss or take profit (not waiting for stock orders to fill).
    """
    try:
        option_data = Config.orderStatusData.get(option_entry_order_id)
        if not option_data:
            logging.warning("Option entry order %s not found in orderStatusData", option_entry_order_id)
            return
        
        # Get stop loss and take profit parameters
        sl_params = option_data.get('option_sl_params')
        tp_params = option_data.get('option_tp_params')
        
        if sl_params:
            logging.info("Option entry order %s filled. Placing option stop loss order immediately (triggers when stock price reaches stop loss).", option_entry_order_id)
            asyncio.ensure_future(
                placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, sl_params, 'OptionStopLoss')
            )
        else:
            logging.warning("Option stop loss parameters not found for option entry order %s", option_entry_order_id)
        
        if tp_params:
            logging.info("Option entry order %s filled. Placing option take profit order immediately (triggers when stock price reaches take profit).", option_entry_order_id)
            asyncio.ensure_future(
                placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, tp_params, 'OptionProfit')
            )
        else:
            logging.warning("Option take profit parameters not found for option entry order %s", option_entry_order_id)
    except Exception as e:
        logging.error("Error in handleOptionEntryFill: %s", e)
        logging.error(traceback.format_exc())

def triggerOptionOrderOnStockFill(connection, stock_order_id, ord_type, bar_type):
    """
    Trigger option orders when stock orders fill.
    - If stock stop loss or profit fills: Place corresponding option stop loss or profit orders
    """
    try:
        if ord_type not in ('StopLoss', 'TakeProfit'):
            return
        
        # Find the stock entry order that this TP/SL belongs to
        stock_data = Config.orderStatusData.get(stock_order_id)
        if not stock_data:
            return
        
        # Get the entry order ID from parentId or entryData
        entry_order_id = None
        if 'entryData' in stock_data:
            entry_data = stock_data.get('entryData', {})
            if isinstance(entry_data, dict):
                entry_order_id = entry_data.get('orderId')
        
        if not entry_order_id:
            parent_id = stock_data.get('parentId')
            if parent_id:
                entry_order_id = int(parent_id)
        
        if not entry_order_id:
            return
        
        entry_data = Config.orderStatusData.get(entry_order_id)
        if not entry_data:
            return
        
        # Get option entry order ID
        option_orders = entry_data.get('option_orders', {})
        option_entry_order_id = option_orders.get('entry')
        
        if not option_entry_order_id:
            return
        
        # Get option entry order data to retrieve SL/TP parameters
        option_entry_data = Config.orderStatusData.get(option_entry_order_id)
        if not option_entry_data:
            return
        
        # Get the appropriate parameters based on ord_type
        if ord_type == 'StopLoss':
            sl_params = option_entry_data.get('option_sl_params')
            if not sl_params:
                return
            logging.info("Stock stop loss filled (orderId=%s), placing option stop loss order", stock_order_id)
            asyncio.ensure_future(
                placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, sl_params, 'OptionStopLoss')
            )
        elif ord_type == 'TakeProfit':
            tp_params = option_entry_data.get('option_tp_params')
            if not tp_params:
                return
            logging.info("Stock take profit filled (orderId=%s), placing option take profit order", stock_order_id)
            asyncio.ensure_future(
                placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, tp_params, 'OptionProfit')
            )
    except Exception as e:
        logging.error("Error in triggerOptionOrderOnStockFill: %s", e)
        logging.error(traceback.format_exc())

def handleOptionTpSlFill(connection, option_order_id, ord_type):
    """Handle option TP/SL fill: Cancel the other bracket order"""
    try:
        option_data = Config.orderStatusData.get(option_order_id)
        if not option_data:
            return
        
        bracket_pair_id = option_data.get('bracket_pair_order_id')
        if bracket_pair_id:
            # Cancel the other bracket order
            logging.info("Option %s filled (orderId=%s), cancelling bracket pair order %s", ord_type, option_order_id, bracket_pair_id)
            # Implementation to cancel order
    except Exception as e:
        logging.error("Error in handleOptionTpSlFill: %s", e)
        logging.error(traceback.format_exc())

async def monitorOptionEntryBeforeStockFill(connection, stock_entry_order_id, symbol, option_params, buy_sell_type):
    """
    Monitor stock price and update option entry condition every second BEFORE stock entry fills.
    This is similar to RBB logic - we monitor and update, but don't place the option order until stock entry fills.
    """
    try:
        logging.info("monitorOptionEntryBeforeStockFill: Starting monitoring for stock_entry_order_id=%s, symbol=%s", 
                    stock_entry_order_id, symbol)
        
        # Get stock contract
        stock_contract = Stock(symbol, 'SMART', 'USD')
        stock_contract = connection.ib.qualifyContracts(stock_contract)[0]
        stock_exchange = stock_contract.exchange or 'SMART'
        
        # Store current entry price (will be updated every second)
        current_entry_price = None
        
        # Initialize entry price from orderStatusData if available
        entry_data = Config.orderStatusData.get(stock_entry_order_id)
        if entry_data:
            initial_entry_price = entry_data.get('lastPrice') or entry_data.get('auxPrice') or entry_data.get('entryPrice')
            if initial_entry_price:
                current_entry_price = initial_entry_price
                if stock_entry_order_id not in Config.orderStatusData:
                    Config.orderStatusData[stock_entry_order_id] = {}
                Config.orderStatusData[stock_entry_order_id]['option_entry_price'] = initial_entry_price
                logging.info("monitorOptionEntryBeforeStockFill: Initial entry price set to %.2f for stock_order=%s", 
                            initial_entry_price, stock_entry_order_id)
        
        while True:
            try:
                await asyncio.sleep(1)  # Check every second
                
                # Check if stock entry order still exists and is not filled
                entry_data = Config.orderStatusData.get(stock_entry_order_id)
                if not entry_data:
                    logging.info("monitorOptionEntryBeforeStockFill: Stock entry order %s not found, stopping monitoring", 
                                stock_entry_order_id)
                    break
                
                # If stock entry is filled, stop monitoring (option order will be placed by handleOptionTradingForEntryFill)
                if entry_data.get('status') == 'Filled':
                    logging.info("monitorOptionEntryBeforeStockFill: Stock entry order %s is FILLED, stopping monitoring", 
                                stock_entry_order_id)
                    break
                
                # If stock entry is cancelled/inactive, stop monitoring
                if entry_data.get('status') in ['Cancelled', 'Inactive']:
                    logging.info("monitorOptionEntryBeforeStockFill: Stock entry order %s is %s, stopping monitoring", 
                                stock_entry_order_id, entry_data.get('status'))
                    break
                
                # Get current stock price
                stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                connection.ib.sleep(0.1)  # Small delay for market data
                current_stock_price = None
                if stock_ticker:
                    last = getattr(stock_ticker, "last", None)
                    close = getattr(stock_ticker, "close", None)
                    if last and last > 0:
                        current_stock_price = last
                    elif close and close > 0:
                        current_stock_price = close
                connection.ib.cancelMktData(stock_contract)
                
                if not current_stock_price:
                    continue  # Skip this iteration if we can't get price
                
                # Get current entry price from orderStatusData (updated by RBB or other logic)
                # For Custom/Limit Order: Use the entry price from the order
                # For RBB: Use the updated entry price from the loop
                new_entry_price = entry_data.get('lastPrice') or entry_data.get('auxPrice') or entry_data.get('entryPrice')
                
                if new_entry_price and new_entry_price != current_entry_price:
                    current_entry_price = new_entry_price
                    # Store updated entry price for when stock entry fills
                    if stock_entry_order_id not in Config.orderStatusData:
                        Config.orderStatusData[stock_entry_order_id] = {}
                    Config.orderStatusData[stock_entry_order_id]['option_entry_price'] = current_entry_price
                    logging.info("monitorOptionEntryBeforeStockFill: Updated option entry price to %.2f for stock_order=%s (current_stock_price=%.2f)", 
                                current_entry_price, stock_entry_order_id, current_stock_price)
                
            except Exception as e:
                logging.error("monitorOptionEntryBeforeStockFill: Error in monitoring loop: %s", e)
                await asyncio.sleep(1)
                continue
                
    except Exception as e:
        logging.error("Error in monitorOptionEntryBeforeStockFill: %s", e)
        logging.error(traceback.format_exc())

def updateOptionOrdersForRBB(connection, stock_entry_order_id, new_entry_price, new_sl_price):
    """Update option orders for RBB when stock entry order is updated"""
    try:
        # For RBB, we update the stored entry price so monitorOptionEntryBeforeStockFill can use it
        if stock_entry_order_id in Config.orderStatusData:
            Config.orderStatusData[stock_entry_order_id]['option_entry_price'] = new_entry_price
            logging.info("updateOptionOrdersForRBB: Updated option entry price to %.2f for stock_order=%s", 
                        new_entry_price, stock_entry_order_id)
    except Exception as e:
        logging.error("Error in updateOptionOrdersForRBB: %s", e)
        logging.error(traceback.format_exc())

async def monitorAndPlacePendingOptionEntry(connection, pending_key, entry_price, trigger_method):
    """
    Monitor stock price and place option entry order when price crosses entry threshold.
    This is used when the current price is already at/beyond the entry price when stock entry fills.
    We wait for price to move away and then cross back.
    """
    try:
        params = Config.pending_option_orders.get(pending_key)
        if not params:
            logging.warning("Pending option entry order %s not found", pending_key)
            return
        
        symbol = params['symbol']
        stock_contract = params['stock_contract']
        stock_exchange = params['stock_exchange']
        
        logging.info("monitorAndPlacePendingOptionEntry: Starting monitoring for %s, entry_price=%.2f", symbol, entry_price)
        
        # First, wait for price to move away from entry price
        price_moved_away = False
        check_interval = 1.0  # Check every second
        
        while not price_moved_away:
            try:
                # Get current stock price
                stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                connection.ib.sleep(0.3)
                current_stock_price = None
                if stock_ticker:
                    last = getattr(stock_ticker, "last", None)
                    if last and last > 0:
                        current_stock_price = last
                connection.ib.cancelMktData(stock_contract)
                
                if current_stock_price is not None:
                    if trigger_method == 1:  # Crosses above (BUY)
                        if current_stock_price < entry_price:
                            price_moved_away = True
                            logging.info("monitorAndPlacePendingOptionEntry: Price moved below entry (%.2f < %.2f), waiting for crossing", 
                                        current_stock_price, entry_price)
                    else:  # Breaks below (SELL)
                        if current_stock_price > entry_price:
                            price_moved_away = True
                            logging.info("monitorAndPlacePendingOptionEntry: Price moved above entry (%.2f > %.2f), waiting for crossing", 
                                        current_stock_price, entry_price)
                
                await asyncio.sleep(check_interval)
            except Exception as e:
                logging.warning("Error checking price in monitorAndPlacePendingOptionEntry: %s", e)
                await asyncio.sleep(check_interval)
        
        # Now wait for price to cross the entry threshold
        while True:
            try:
                # Get current stock price
                stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                connection.ib.sleep(0.3)
                current_stock_price = None
                if stock_ticker:
                    last = getattr(stock_ticker, "last", None)
                    if last and last > 0:
                        current_stock_price = last
                connection.ib.cancelMktData(stock_contract)
                
                if current_stock_price is not None:
                    condition_met = False
                    if trigger_method == 1:  # Crosses above (BUY)
                        if current_stock_price >= entry_price:
                            condition_met = True
                    else:  # Breaks below (SELL)
                        if current_stock_price <= entry_price:
                            condition_met = True
                    
                    if condition_met:
                        logging.info("monitorAndPlacePendingOptionEntry: Entry condition met (price=%.2f, entry=%.2f), placing option entry order", 
                                    current_stock_price, entry_price)
                        # Place the option entry order now
                        await placeOptionEntryOrderFromPending(connection, pending_key, entry_price, trigger_method)
                        # Remove from pending
                        if pending_key in Config.pending_option_orders:
                            del Config.pending_option_orders[pending_key]
                        return
                
                await asyncio.sleep(check_interval)
            except Exception as e:
                logging.warning("Error monitoring price in monitorAndPlacePendingOptionEntry: %s", e)
                await asyncio.sleep(check_interval)
    except Exception as e:
        logging.error("Error in monitorAndPlacePendingOptionEntry: %s", e)
        logging.error(traceback.format_exc())

async def placeOptionEntryOrderFromPending(connection, pending_key, entry_price, trigger_method):
    """Place option entry order from pending parameters"""
    try:
        params = Config.pending_option_orders.get(pending_key)
        if not params:
            logging.warning("Pending option entry order %s not found", pending_key)
            return
        
        option_contract = params['option_contract']
        action = params['action']
        option_quantity = params['option_quantity']
        entry_order_type = params['entry_order_type']
        tif = params['tif']
        stock_contract = params['stock_contract']
        stock_exchange = params['stock_exchange']
        opt_bid = params['opt_bid']
        opt_ask = params['opt_ask']
        opt_price = params['opt_price']
        symbol = params['symbol']
        stop_loss_price = params['stop_loss_price']
        profit_price = params['profit_price']
        sl_order_type = params['sl_order_type']
        tp_order_type = params['tp_order_type']
        buy_sell_type = params['buy_sell_type']
        stock_entry_order_id = params['stock_entry_order_id']
        
        # Create entry order (using OptionEntry trade type for unique ID range)
        order_id = connection.get_next_order_id()
        order = Order()
        order.orderId = order_id
        order.action = action
        order.totalQuantity = option_quantity
        order.tif = tif
        
        # Set order type based on Market/Bid+/Ask-
        if entry_order_type == 'Market':
            order.orderType = 'MKT'
        elif entry_order_type == 'Bid+':
            order.orderType = 'LMT'
            if opt_bid and opt_bid > 0:
                order.lmtPrice = round(opt_bid, 2)
            elif opt_price:
                order.lmtPrice = round(opt_price, 2)
            else:
                order.lmtPrice = 0.01
        elif entry_order_type == 'Ask-':
            order.orderType = 'LMT'
            if opt_ask and opt_ask > 0:
                order.lmtPrice = round(opt_ask, 2)
            elif opt_price:
                order.lmtPrice = round(opt_price, 2)
            else:
                order.lmtPrice = 0.01
        
        # Add condition using exact entry price
        condition = PriceCondition(
            price=entry_price,
            triggerMethod=trigger_method,
            conId=stock_contract.conId,
            exch=stock_exchange
        )
        order.conditions = [condition]
        order.conditionsIgnoreRth = False
        
        # Place the entry order
        trade = connection.placeTrade(option_contract, order, outsideRth=False)
        if not trade:
            logging.error("Failed to place option entry order from pending")
            return
        
        option_entry_order_id = trade.order.orderId
        logging.info("Option entry order placed from pending (conditional): If %s %s %.2f, then %s %d contracts (%s), orderId=%s",
                    symbol, "crosses above" if trigger_method == 1 else "breaks below", entry_price,
                    action, option_quantity, entry_order_type, option_entry_order_id)
        
        # Store order data (same as in placeOptionTradeAndStore)
        if option_entry_order_id not in Config.orderStatusData:
            Config.orderStatusData[option_entry_order_id] = {}
        Config.orderStatusData[option_entry_order_id].update({
            'contract': option_contract,
            'option_contract': option_contract,
            'option_quantity': option_quantity,
            'option_tif': tif,
            'option_symbol': symbol,
            'stock_contract': stock_contract,
            'stock_exchange': stock_exchange,
            'opt_bid': opt_bid,
            'opt_ask': opt_ask,
            'opt_price': opt_price,
            'stock_entry_order_id': stock_entry_order_id,
            'ordType': 'OptionEntry'
        })
        
        # Store SL/TP parameters for later use
        Config.orderStatusData[option_entry_order_id]['option_sl_params'] = {
            'action': 'SELL' if action == 'BUY' else 'BUY',
            'order_type': sl_order_type,
            'condition_price': stop_loss_price,
            'trigger_method': 2 if buy_sell_type == 'BUY' else 1  # Breaks below for BUY, crosses above for SELL
        }
        Config.orderStatusData[option_entry_order_id]['option_tp_params'] = {
            'action': 'SELL' if action == 'BUY' else 'BUY',
            'order_type': tp_order_type,
            'condition_price': profit_price,
            'trigger_method': 1 if buy_sell_type == 'BUY' else 2  # Crosses above for BUY, breaks below for SELL
        }
        
        logging.info("Option entry order placed and stored from pending: orderId=%s, stock_entry_order_id=%s", 
                    option_entry_order_id, stock_entry_order_id)
    except Exception as e:
        logging.error("Error in placeOptionEntryOrderFromPending: %s", e)
        logging.error(traceback.format_exc())

async def monitorAndPlacePendingOptionOrder(connection, pending_key, condition_price, trigger_method, ord_type_name):
    """Monitor and place pending option order when condition is met"""
    try:
        # Implementation needed
        pass
    except Exception as e:
        logging.error("Error in monitorAndPlacePendingOptionOrder: %s", e)
        logging.error(traceback.format_exc())

async def monitorAndAdjustBidAskOrder(connection, trade, order_type, initial_price, action):
    """
    Monitor Bid+ or Ask- order and adjust price by $0.05 increments until filled.
    
    Bid+ (for buying options):
    - Starts at bid price (e.g., $1.00)
    - If not filled after 2 seconds → increase by $0.05 ($1.05)
    - Continues: $1.00 → $1.05 → $1.10 → $1.15... until filled
    - Maximum 20 adjustments (safety limit)
    
    Ask- (for selling options):
    - Starts at ask price (e.g., $2.00)
    - If not filled after 2 seconds → decrease by $0.05 ($1.95)
    - Continues: $2.00 → $1.95 → $1.90 → $1.85... until filled
    - Maximum 20 adjustments (safety limit)
    - Safety check: won't go below $0.01
    """
    try:
        order_id = trade.order.orderId
        option_data = Config.orderStatusData.get(order_id)
        if not option_data:
            logging.warning("Order data not found for orderId=%s in monitorAndAdjustBidAskOrder", order_id)
            return
        
        # Get stock contract and condition price to check if condition is met
        stock_contract = option_data.get('stock_contract')
        condition_price = option_data.get('condition_price')
        trigger_method = option_data.get('trigger_method')
        symbol = option_data.get('usersymbol') or option_data.get('option_symbol')
        
        # First, check if the stock price condition has been met
        # Only start adjusting if the condition is met (order is active)
        if stock_contract and condition_price is not None:
            try:
                stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                await asyncio.sleep(0.5)
                current_stock_price = None
                if stock_ticker:
                    last = getattr(stock_ticker, "last", None)
                    close = getattr(stock_ticker, "close", None)
                    if last and last > 0:
                        current_stock_price = last
                    elif close and close > 0:
                        current_stock_price = close
                connection.ib.cancelMktData(stock_contract)
                
                # Check if condition is met
                condition_met = False
                if current_stock_price is not None:
                    if trigger_method == 1:  # Crosses above
                        condition_met = current_stock_price >= condition_price
                    elif trigger_method == 2:  # Breaks below
                        condition_met = current_stock_price <= condition_price
                
                if not condition_met:
                    logging.info("Option order %s: Stock condition not yet met (price=%.2f, condition=%.2f, method=%s). Waiting...", 
                               order_id, current_stock_price or 0, condition_price, trigger_method)
                    # Wait and check again in 1 second
                    await asyncio.sleep(1)
                    # Retry once more
                    stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                    await asyncio.sleep(0.5)
                    if stock_ticker:
                        last = getattr(stock_ticker, "last", None)
                        if last and last > 0:
                            current_stock_price = last
                    connection.ib.cancelMktData(stock_contract)
                    
                    if current_stock_price is not None:
                        if trigger_method == 1:
                            condition_met = current_stock_price >= condition_price
                        elif trigger_method == 2:
                            condition_met = current_stock_price <= condition_price
                    
                    if not condition_met:
                        logging.info("Option order %s: Stock condition still not met. Will monitor order status instead.", order_id)
            except Exception as e:
                logging.warning("Could not check stock condition for order %s: %s. Proceeding with price adjustments.", order_id, e)
        
        # Monitor order and adjust price
        current_price = initial_price
        adjustment_count = 0
        max_adjustments = 20
        check_interval = 2.0  # Check every 2 seconds
        
        logging.info("Starting Bid+/Ask- adjustment monitoring for order %s: type=%s, initial_price=%.2f, action=%s", 
                    order_id, order_type, initial_price, action)
        
        while adjustment_count < max_adjustments:
            await asyncio.sleep(check_interval)
            
            # Check order status
            try:
                # Get updated trade status
                order_status = trade.orderStatus.status if hasattr(trade, 'orderStatus') else None
                
                if order_status in ('Filled', 'Cancelled'):
                    logging.info("Option order %s status is %s. Stopping price adjustments.", order_id, order_status)
                    break
                
                # Check if order is still pending
                if order_status in ('PreSubmitted', 'Submitted', 'PendingSubmit', 'PendingCancel'):
                    # Order not filled yet, adjust price
                    if order_type == 'Bid+':
                        # Increase price by $0.05
                        new_price = round(current_price + 0.05, 2)
                        logging.info("Bid+ order %s not filled at %.2f, adjusting to %.2f (adjustment %d/%d)", 
                                   order_id, current_price, new_price, adjustment_count + 1, max_adjustments)
                    elif order_type == 'Ask-':
                        # Decrease price by $0.05, but not below $0.01
                        new_price = round(max(current_price - 0.05, 0.01), 2)
                        if new_price < 0.01:
                            logging.warning("Ask- order %s: Cannot adjust below $0.01. Current price: %.2f", order_id, current_price)
                            break
                        logging.info("Ask- order %s not filled at %.2f, adjusting to %.2f (adjustment %d/%d)", 
                                   order_id, current_price, new_price, adjustment_count + 1, max_adjustments)
                    else:
                        logging.error("Unknown order type for adjustment: %s", order_type)
                        break
                    
                    # Modify the order with new price
                    try:
                        trade.order.lmtPrice = new_price
                        connection.ib.placeOrder(trade.contract, trade.order)
                        current_price = new_price
                        adjustment_count += 1
                        logging.info("Option order %s price adjusted to %.2f", order_id, new_price)
                    except Exception as e:
                        logging.error("Error modifying order %s price: %s", order_id, e)
                        break
                else:
                    # Order might be in a different state, check again
                    logging.debug("Option order %s status: %s", order_id, order_status)
                    
            except Exception as e:
                logging.error("Error checking order status for %s: %s", order_id, e)
                break
        
        if adjustment_count >= max_adjustments:
            logging.warning("Option order %s: Reached maximum adjustments (%d). Stopping price adjustments.", 
                          order_id, max_adjustments)
        else:
            logging.info("Option order %s: Price adjustment monitoring completed (adjustments made: %d)", 
                        order_id, adjustment_count)
            
    except Exception as e:
        logging.error("Error in monitorAndAdjustBidAskOrder: %s", e)
        logging.error(traceback.format_exc())

async def placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, params, ord_type_name):
    """
    Place option stop loss or take profit order when corresponding stock order fills.
    This function is called from triggerOptionOrderOnStockFill.
    """
    try:
        option_data = Config.orderStatusData.get(option_entry_order_id)
        if not option_data:
            logging.warning("Option entry order %s not found in orderStatusData", option_entry_order_id)
            return
        
        option_contract = option_data.get('option_contract')
        quantity = option_data.get('option_quantity', 1)
        tif = option_data.get('option_tif', 'DAY')
        symbol = option_data.get('option_symbol')
        stock_exchange = option_data.get('stock_exchange', 'SMART')
        stock_contract = option_data.get('stock_contract')
        opt_bid = option_data.get('opt_bid')
        opt_ask = option_data.get('opt_ask')
        opt_price = option_data.get('opt_price')
        
        if not option_contract or not stock_contract:
            logging.error("Option contract or stock contract not found for option entry order %s", option_entry_order_id)
            return
        
        # Get current stock price to verify conditions are not already met
        try:
            stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
            connection.ib.sleep(0.5)
            current_stock_price = None
            if stock_ticker:
                last = getattr(stock_ticker, "last", None)
                close = getattr(stock_ticker, "close", None)
                if last and last > 0:
                    current_stock_price = last
                elif close and close > 0:
                    current_stock_price = close
            connection.ib.cancelMktData(stock_contract)
        except Exception as e:
            logging.warning("Could not get current stock price: %s", e)
            current_stock_price = None
        
        action = params.get('action')
        order_type = params.get('order_type')
        condition_price = params.get('condition_price')
        trigger_method = params.get('trigger_method')
        
        # Always use exact condition_price from stock trade
        # Stop loss: If SPY breaks below 680.30, sell X contracts
        # Take profit: If SPY crosses 680.78, sell X contracts
        logging.info("Option %s: Using exact condition_price=%.2f for conditional order (current_price=%.2f)", 
                    ord_type_name, condition_price, current_stock_price)
        
        # Create and place the conditional order (using OptionStopLoss or OptionProfit trade type)
        trade_type = ord_type_name  # 'OptionStopLoss' or 'OptionProfit' passed by caller
        order_id = connection.get_next_order_id()
        order = Order()
        order.orderId = order_id
        order.action = action
        order.totalQuantity = quantity
        order.tif = tif
        
        # Set order type based on Market/Bid+/Ask-
        if order_type == 'Market':
            order.orderType = 'MKT'
        elif order_type == 'Bid+':
            order.orderType = 'LMT'
            if opt_bid and opt_bid > 0:
                order.lmtPrice = round(opt_bid, 2)
            elif opt_price:
                order.lmtPrice = round(opt_price, 2)
            else:
                order.lmtPrice = 0.01
        elif order_type == 'Ask-':
            order.orderType = 'LMT'
            if opt_ask and opt_ask > 0:
                order.lmtPrice = round(opt_ask, 2)
            elif opt_price:
                order.lmtPrice = round(opt_price, 2)
            else:
                order.lmtPrice = 0.01
        else:
            logging.error("Unknown option order type: %s", order_type)
            return
        
        # Create condition: Monitor stock price
        condition = PriceCondition(
            price=condition_price,
            triggerMethod=trigger_method,
            conId=stock_contract.conId,
            exch=stock_exchange
        )
        order.conditions = [condition]
        order.conditionsIgnoreRth = False
        
        # Place the conditional order
        try:
            trade = connection.placeTrade(option_contract, order, outsideRth=False)
            if trade:
                initial_limit_price = order.lmtPrice if order.orderType == 'LMT' else None
                
                logging.info(
                    "Option %s conditional order placed: If %s %s %.2f, then %s %d contracts (%s), orderId=%s",
                    ord_type_name, symbol,
                    "crosses above" if trigger_method == 1 else "breaks below", condition_price,
                    action, quantity, order_type, order_id
                )
                
                # Store order data
                order_id = trade.order.orderId
                if order_id not in Config.orderStatusData:
                    Config.orderStatusData[order_id] = {}
                Config.orderStatusData[order_id].update({
                    'contract': option_contract,
                    'action': action,
                    'totalQuantity': quantity,
                    'type': order_type,
                    'tif': tif,
                    'ordType': ord_type_name,
                    'usersymbol': symbol,
                    'condition_price': condition_price,
                })
                
                # Start monitoring and adjustment for Bid+/Ask- orders
                if order_type in ('Bid+', 'Ask-') and initial_limit_price:
                    asyncio.ensure_future(
                        monitorAndAdjustBidAskOrder(connection, trade, order_type, initial_limit_price, action)
                    )
                
                # Store in option_orders for reference
                if 'option_orders' not in option_data:
                    option_data['option_orders'] = {}
                if ord_type_name == 'OptionStopLoss':
                    option_data['option_orders']['stop_loss'] = order_id
                elif ord_type_name == 'OptionProfit':
                    option_data['option_orders']['profit'] = order_id
                
                return trade
        except Exception as e:
            logging.error("Error placing conditional option order: %s", e)
            logging.error(traceback.format_exc())
            return None
    except Exception as e:
        logging.error("Error in placeOptionStopLossOrTakeProfit: %s", e)
        logging.error(traceback.format_exc())
        return None
