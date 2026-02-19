"""
Option Trading Module
Handles all option trading functionality without modifying core SendTrade.py logic
"""
import asyncio
import datetime
import logging
import math
import threading
import time
import traceback
from ib_insync import Option, Order, Stock, PriceCondition
import Config

# Bid+ / Ask- adjustment settings
BID_ASK_ADJUST_INTERVAL_SEC = 2.0   # If not filled after this many seconds, adjust price
BID_ASK_ADJUST_INCREMENT = 0.05     # Bid+: increase by this; Ask-: decrease by this
BID_ASK_MAX_ADJUSTMENTS = 20        # Safety limit on number of adjustments
MIN_OPTION_LIMIT_PRICE = 0.01       # Ask- won't go below this

# Lock to prevent duplicate option exit orders when stock SL/TP fill and option entry fill
# can be processed concurrently (e.g. from different IB status events).
_option_exit_placement_lock = threading.Lock()


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

def _is_rth():
    """Return True if currently in Regular Trading Hours (RTH). Option logic runs only during RTH."""
    try:
        from SendTrade import _get_current_session
        session = _get_current_session()
        return session not in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT')
    except Exception:
        return True  # Default to allow if session unknown


def _get_nearest_strike_and_expiration(ib, stock_contract, desired_strike, desired_expiration_yyyymmdd):
    """
    Fetch option chain via reqSecDefOptParams and snap desired strike/expiration
    to the nearest available. Returns (strike, expiration_yyyymmdd, trading_class) or (None, None, None) on failure.
    """
    try:
        # For stocks use secType 'STK' and underlying conId; futFopExchange '' for stock options
        chains = ib.reqSecDefOptParams(stock_contract.symbol, '', stock_contract.secType, stock_contract.conId)
        if not chains:
            logging.warning("No option chains returned for %s", stock_contract.symbol)
            return None, None, None
        # Prefer the chain that matches the underlying (tradingClass == symbol) to avoid Flex/2x chains.
        # E.g. for SPY we want tradingClass 'SPY', not '2SPY' (Flex options reject IB-cleared orders).
        symbol_upper = (stock_contract.symbol or '').upper()
        chain = None
        for c in chains:
            tc = getattr(c, 'tradingClass', None) or ''
            if (tc.upper() == symbol_upper and getattr(c, 'exchange', None) == 'SMART'):
                chain = c
                break
        if chain is None:
            for c in chains:
                tc = getattr(c, 'tradingClass', None) or ''
                if tc.upper() == symbol_upper:
                    chain = c
                    break
        if chain is None:
            for c in chains:
                if getattr(c, 'exchange', None) == 'SMART':
                    chain = c
                    break
        if chain is None:
            chain = chains[0]
        expirations = getattr(chain, 'expirations', None) or []
        strikes = getattr(chain, 'strikes', None) or []
        trading_class = getattr(chain, 'tradingClass', None)
        if not strikes:
            logging.warning("Option chain for %s has no strikes", stock_contract.symbol)
            return None, None, None
        # Snap strike to nearest available
        strike_price = min(strikes, key=lambda s: abs(s - desired_strike))
        # Snap expiration to nearest available (prefer exact match, else nearest date)
        if not expirations:
            logging.warning("Option chain for %s has no expirations", stock_contract.symbol)
            return None, None, None
        if desired_expiration_yyyymmdd in expirations:
            expiration_date = desired_expiration_yyyymmdd
        else:
            # Find nearest expiration (by date string comparison)
            def date_dist(exp_str):
                try:
                    return abs(int(exp_str) - int(desired_expiration_yyyymmdd))
                except (ValueError, TypeError):
                    return float('inf')
            expiration_date = min(expirations, key=date_dist)
        logging.debug("Option chain snap: desired strike=%s -> %s, desired exp=%s -> %s (tradingClass=%s)",
                     desired_strike, strike_price, desired_expiration_yyyymmdd, expiration_date, trading_class)
        return strike_price, expiration_date, trading_class
    except Exception as e:
        logging.error("Error fetching option chain for %s: %s", stock_contract.symbol, e)
        logging.error(traceback.format_exc())
        return None, None, None


def _pre_resolve_option_contract(connection, symbol, entry_price, option_contract_str, option_expire, buy_sell_type):
    """
    Resolve option contract once using entry_price (e.g. stop trigger 684.77) so option is ATM at that level.
    Returns (qualified_option_contract, option_right) or (None, None). Used by price-level monitor to avoid
    refetching stock price and option chain when trigger fires (reduces delay).
    """
    try:
        stock_contract = Stock(symbol, 'SMART', 'USD')
        stock_contract = connection.ib.qualifyContracts(stock_contract)[0]
        option_right = 'C' if (buy_sell_type or 'BUY').upper() == 'BUY' else 'P'
        strike_price = None
        if option_contract_str == "ATM":
            strike_price = round(entry_price, 0)
        elif option_contract_str and str(option_contract_str).strip().startswith("OTM"):
            otm_steps = int(str(option_contract_str).replace("OTM", "").strip() or "1")
            if (buy_sell_type or 'BUY').upper() == 'BUY':
                strike_price = round(entry_price + (otm_steps * 1.0), 0)
            else:
                strike_price = round(entry_price - (otm_steps * 1.0), 0)
        else:
            return None, None
        expiry_weeks = int(option_expire) if option_expire else 0
        today = datetime.date.today()
        days_until_friday = (4 - today.weekday()) % 7
        target_date = today + datetime.timedelta(days=days_until_friday + (expiry_weeks * 7))
        expiration_date = target_date.strftime("%Y%m%d")
        snapped_strike, snapped_expiration, trading_class = _get_nearest_strike_and_expiration(
            connection.ib, stock_contract, strike_price, expiration_date
        )
        if snapped_strike is None or snapped_expiration is None:
            return None, None
        option_contract = Option(symbol, snapped_expiration, snapped_strike, option_right, 'SMART')
        if trading_class:
            option_contract.tradingClass = trading_class
        qualified = connection.ib.qualifyContracts(option_contract)
        if not qualified:
            return None, None
        logging.debug("Pre-resolved option for price-level trigger: %s %s %s @ entry=%.2f",
                     symbol, option_right, snapped_strike, entry_price)
        return qualified[0], option_right
    except Exception as e:
        logging.warning("_pre_resolve_option_contract failed: %s", e)
        return None, None


def get_option_params_for_entry(symbol, timeFrame, barType, buySellType):
    """
    Return the latest matching option_trade_params for (symbol, timeFrame, barType, buySellType)
    if enabled; otherwise None. Used to trigger option entry immediately when stock entry is placed.
    """
    if not hasattr(Config, 'option_trade_params') or not Config.option_trade_params:
        return None
    latest_ts = None
    matching_params = None
    for trade_key, params in list(Config.option_trade_params.items()):
        if not isinstance(trade_key, (tuple, list)) or len(trade_key) < 5:
            continue
        key_symbol, key_tf, key_bar, key_side, ts = trade_key[0], trade_key[1], trade_key[2], trade_key[3], trade_key[4]
        if (key_symbol == symbol and key_tf == timeFrame and key_bar == barType and key_side == buySellType
                and params.get('enabled')):
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
                matching_params = params
    return matching_params


async def placeOptionTradeAndStore(connection, symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price, 
                          entry_order_type, sl_order_type, tp_order_type, quantity, tif, buy_sell_type, risk_amount=None, stock_entry_order_id=None, outside_rth=False, from_entry_fill=False, pre_resolved_contract=None, pre_resolved_option_right=None):
    """
    Place option trade and store order IDs.
    Option logic runs only during RTH (Regular Trading Hours).
    When from_entry_fill=True (stock entry just filled):
    - BUY stock -> option buys call immediately. When stock TP or SL fills -> option sells call.
    - SELL stock -> option buys put immediately. When stock TP or SL fills -> option sells put.
    When pre_resolved_contract is set (from price-level trigger): skip stock price fetch and option chain
    resolution to minimize delay; option is already resolved at entry_price level.
    """
    try:
        if outside_rth or not _is_rth():
            logging.debug("Option trading only runs during RTH; skipping (outside_rth=%s)", outside_rth)
            return
        logging.debug("placeOptionTradeAndStore: Starting for %s, contract=%s, expire=%s, entry=%s, sl=%s, tp=%s, risk=%s%s",
                      symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price, risk_amount,
                      " (pre-resolved)" if pre_resolved_contract else "")
        
        # Get stock contract (needed for stock_exchange and possibly for non-pre-resolved path)
        stock_contract = Stock(symbol, 'SMART', 'USD')
        stock_contract = connection.ib.qualifyContracts(stock_contract)[0]
        stock_exchange = stock_contract.exchange or 'SMART'
        
        if pre_resolved_contract and pre_resolved_option_right:
            option_contract = pre_resolved_contract
            option_right = pre_resolved_option_right
        else:
            # Use entry_price when from_entry_fill (stock just filled) to avoid 0.5s mkt data wait and align strike with fill
            current_stock_price = None
            if from_entry_fill and entry_price is not None:
                try:
                    ep = float(entry_price)
                    if 0 < ep < 1e10:
                        current_stock_price = ep
                        logging.debug("placeOptionTradeAndStore: Using entry_price=%.2f for strike (from_entry_fill)", current_stock_price)
                except (TypeError, ValueError):
                    pass
            if current_stock_price is None:
                # Get current stock price from market data
                stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                await asyncio.sleep(0.5)
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
            # Find this week's Friday (weekly options expire on Friday). 0 = current week = this Friday.
            days_until_friday = (4 - today.weekday()) % 7
            target_date = today + datetime.timedelta(days=days_until_friday + (expiry_weeks * 7))
            expiration_date = target_date.strftime("%Y%m%d")
            
            # Snap to nearest available strike and expiration from option chain (avoids "No security definition" errors)
            snapped_strike, snapped_expiration, trading_class = _get_nearest_strike_and_expiration(
                connection.ib, stock_contract, strike_price, expiration_date
            )
            if snapped_strike is None or snapped_expiration is None:
                logging.error("Could not resolve option chain for %s; cannot place option order", symbol)
                return
            strike_price = snapped_strike
            expiration_date = snapped_expiration
            
            # Create and qualify option contract (use tradingClass if chain provided it for correct routing)
            option_contract = Option(symbol, expiration_date, strike_price, option_right, 'SMART')
            if trading_class:
                option_contract.tradingClass = trading_class
            qualified = connection.ib.qualifyContracts(option_contract)
            if not qualified:
                logging.error("Could not qualify option contract for %s %s %s %s", symbol, expiration_date, strike_price, option_right)
                return
            option_contract = qualified[0]
        
        # Get option prices for quantity calculation and order placement.
        # When pre-resolved or from_entry_fill, use shorter wait to place order faster.
        option_ticker = connection.ib.reqMktData(option_contract, '', False, False)
        opt_price = None
        opt_bid = None
        opt_ask = None
        fast_path = pre_resolved_contract or from_entry_fill
        max_wait_sec = 2.0 if fast_path else 5.0
        check_interval_sec = 0.1 if fast_path else 0.4
        waited = 0.0
        while waited < max_wait_sec:
            await asyncio.sleep(check_interval_sec)
            waited += check_interval_sec
            opt_bid = getattr(option_ticker, "bid", None)
            opt_ask = getattr(option_ticker, "ask", None)
            last = getattr(option_ticker, "last", None)
            close = getattr(option_ticker, "close", None)
            mark_price = getattr(option_ticker, "markPrice", None)
            if last is not None and last == last and last > 0:
                opt_price = last
                logging.debug("Option price from last: %.2f (waited %.1fs)", opt_price, waited)
                break
            if opt_bid is not None and opt_ask is not None and opt_bid == opt_bid and opt_ask == opt_ask and opt_bid > 0 and opt_ask > 0:
                opt_price = (opt_bid + opt_ask) / 2.0
                logging.debug("Option price from bid/ask: %.2f (waited %.1fs)", opt_price, waited)
                break
            if close is not None and close == close and close > 0:
                opt_price = close
                logging.debug("Option price from close: %.2f (waited %.1fs)", opt_price, waited)
                break
            if mark_price is not None and mark_price == mark_price and mark_price > 0:
                opt_price = mark_price
                logging.debug("Option price from mark: %.2f (waited %.1fs)", opt_price, waited)
                break
        connection.ib.cancelMktData(option_contract)
        # Re-read bid/ask for order placement (may have updated)
        opt_bid = getattr(option_ticker, "bid", None)
        opt_ask = getattr(option_ticker, "ask", None)
        if opt_bid is not None and isinstance(opt_bid, float) and (opt_bid != opt_bid or opt_bid <= 0):
            opt_bid = None
        if opt_ask is not None and isinstance(opt_ask, float) and (opt_ask != opt_ask or opt_ask <= 0):
            opt_ask = None
        
        if not opt_price or opt_price <= 0:
            logging.error("Could not get option price for %s after %.1fs (bid=%s, ask=%s)", symbol, max_wait_sec, opt_bid, opt_ask)
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
                    logging.debug("Option quantity calculated: risk=%s, opt_price=%s, quantity=%s (rounded)", risk_amt, opt_price, option_quantity)
            except (ValueError, TypeError):
                logging.warning("Invalid risk amount: %s, using quantity=1", risk_amount)
        
        # Determine action for option entry (always BUY option when stock entry fills)
        # BUY stock -> buy call. SELL stock -> buy put. (option_right already set above)
        action = "BUY"
        # Trigger method only used for conditional orders (when not from_entry_fill)
        trigger_method = 1 if buy_sell_type == "BUY" else 2  # Crosses above / Breaks below
        
        # When from_entry_fill: stock entry just filled -> place option entry IMMEDIATELY (no price condition).
        # Option uses the same stop size as the stock. BUY stock -> option buys call. SELL stock -> option buys put.
        # Option exit is when stock TP or SL order fills (price crosses TP or SL, same as stock logic).
        if from_entry_fill:
            # Place option entry immediately: buy call (long) or buy put (short)
            order_id = connection.get_next_order_id()
            order = Order()
            order.orderId = order_id
            order.action = action
            order.totalQuantity = option_quantity
            order.tif = tif
            if entry_order_type == 'Market':
                order.orderType = 'MKT'
                order.lmtPrice = 0.0
            elif entry_order_type == 'Bid+':
                order.orderType = 'LMT'
                order.lmtPrice = round(opt_bid, 2) if (opt_bid and opt_bid > 0) else (round(opt_price, 2) if opt_price else 0.01)
            elif entry_order_type == 'Ask-':
                order.orderType = 'LMT'
                order.lmtPrice = round(opt_ask, 2) if (opt_ask and opt_ask > 0) else (round(opt_price, 2) if opt_price else 0.01)
            else:
                order.orderType = 'MKT'
                order.lmtPrice = 0.0
            order.conditions = []
            order.conditionsIgnoreRth = False
            trade = connection.placeTrade(option_contract, order, outsideRth=False)
            if not trade:
                logging.error("Failed to place option entry order (immediate)")
                return
            option_entry_order_id = trade.order.orderId
            logging.info("Option entry placed: %s %d %s contract(s), orderId=%s",
                        action, option_quantity, "call" if option_right == 'C' else "put", option_entry_order_id)
            # Store order data and SL/TP params (option exit when stock TP or SL fills)
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
            })
            sl_action = "SELL"
            Config.orderStatusData[option_entry_order_id]['option_sl_params'] = {
                'action': sl_action,
                'order_type': sl_order_type,
                'condition_price': stop_loss_price,
                'trigger_method': 2,
            }
            Config.orderStatusData[option_entry_order_id]['option_tp_params'] = {
                'action': sl_action,
                'order_type': tp_order_type,
                'condition_price': profit_price,
                'trigger_method': 1 if buy_sell_type == "BUY" else 2,
            }
            if stock_entry_order_id:
                if stock_entry_order_id not in Config.orderStatusData:
                    Config.orderStatusData[stock_entry_order_id] = {}
                if 'option_orders' not in Config.orderStatusData[stock_entry_order_id]:
                    Config.orderStatusData[stock_entry_order_id]['option_orders'] = {}
                Config.orderStatusData[stock_entry_order_id]['option_orders']['entry'] = option_entry_order_id
                # Store so handleOptionEntryFill can place option exit after option entry fills (not here, to avoid "both sides" rejection).
                Config.orderStatusData[option_entry_order_id]['stock_entry_order_id'] = stock_entry_order_id
            logging.debug("Option entry order placed and stored: orderId=%s, stock_entry_order_id=%s",
                        option_entry_order_id, stock_entry_order_id)
            # Start Bid+/Ask- adjustment loop for immediate option entry (2s interval, ±$0.05, max 20)
            if entry_order_type in ('Bid+', 'Ask-') and getattr(order, 'lmtPrice', 0) and order.lmtPrice > 0:
                initial_limit = round(order.lmtPrice, 2)
                asyncio.ensure_future(
                    monitorAndAdjustBidAskOrder(connection, trade, entry_order_type, initial_limit, action)
                )
            return
        
        # Not from_entry_fill: legacy conditional path (e.g. when option is placed before stock fills)
        # Check if current price is already at or beyond entry price
        should_place_order = True
        if current_stock_price is not None:
            if trigger_method == 1:  # Crosses above (BUY)
                if current_stock_price >= entry_price:
                    # Price is already at or above entry - condition already met
                    # Don't place order yet, store for monitoring
                    should_place_order = False
                    logging.debug("Option entry: Current price (%.2f) is already at/above entry (%.2f). Storing order for monitoring instead of placing immediately.", 
                                current_stock_price, entry_price)
            else:  # Breaks below (SELL)
                if current_stock_price <= entry_price:
                    # Price is already at or below entry - condition already met
                    # Don't place order yet, store for monitoring
                    should_place_order = False
                    logging.debug("Option entry: Current price (%.2f) is already at/below entry (%.2f). Storing order for monitoring instead of placing immediately.", 
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
            logging.debug("Option entry order stored for monitoring. Will be placed when price crosses entry threshold.")
            # Start monitoring task
            asyncio.ensure_future(
                monitorAndPlacePendingOptionEntry(connection, pending_key, entry_price, trigger_method)
            )
            return
        
        # Current price is not at/beyond entry price - safe to place conditional order
        # Entry: If SPY crosses 680.54, buy X contracts
        logging.debug("Option entry: Using exact entry_price=%.2f for conditional order (current_price=%.2f)", 
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
        logging.debug("Option entry order will be conditional: If %s %s %.2f, then %s %d contracts (%s)",
                    symbol, "crosses above" if trigger_method == 1 else "breaks below", entry_price,
                    action, option_quantity, entry_order_type)
        
        # Place the entry order
        trade = connection.placeTrade(option_contract, order, outsideRth=False)
        if not trade:
            logging.error("Failed to place option entry order")
            return
        
        option_entry_order_id = trade.order.orderId
        logging.debug("Option entry order placed (conditional): If %s %s %.2f, then %s %d contracts (%s), orderId=%s",
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
        
        logging.debug("Option entry order placed and stored: orderId=%s, stock_entry_order_id=%s", 
                    option_entry_order_id, stock_entry_order_id)
        
    except Exception as e:
        logging.error("Error in placeOptionTradeAndStore: %s", e)
        logging.error(traceback.format_exc())

async def placeOptionEntryOrderImmediately(connection, stock_entry_order_id, symbol, entry_price, stop_loss_price, profit_price, 
                                         option_params, buy_sell_type, entry_data, outside_rth=False):
    """
    Place option entry order immediately when stock entry order is placed (not waiting for it to fill).
    Option logic runs only during RTH.
    Option entry order triggers when stock price crosses entry price.
    Option stop loss and take profit orders are placed when option entry fills (not waiting for stock orders to fill).
    """
    try:
        if outside_rth or not _is_rth():
            logging.debug("Option trading only runs during RTH; skipping placeOptionEntryOrderImmediately (outside_rth=%s)", outside_rth)
            return
        logging.debug("placeOptionEntryOrderImmediately: Placing option entry order for %s, entry=%.2f, sl=%.2f, tp=%.2f",
                      symbol, entry_price, stop_loss_price, profit_price)
        
        # Place option entry order immediately (conditional: if stock crosses entry price, buy option contracts)
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
            outside_rth=outside_rth,
            from_entry_fill=True,
        )
        logging.debug("Option entry order placed immediately for %s (will trigger when stock price crosses %.2f)", 
                    symbol, entry_price)
    except Exception as e:
        logging.error("Error in placeOptionEntryOrderImmediately: %s", e)
        logging.error(traceback.format_exc())

def on_stock_entry_fill(connection, stock_entry_order_id):
    """
    Single hook from stock when a stock entry order fills.
    Option logic is separate: we receive only order_id and read all numbers from Config
    (stock writes filledPrice, stop_loss_price, profit_price, etc. to Config.orderStatusData).
    Only place option entry when the stock entry order has status 'Filled' (prevents placing
    option entry before stock fills, e.g. replay re-entry).
    """
    try:
        entry_data = Config.orderStatusData.get(int(stock_entry_order_id), {})
        if not entry_data:
            logging.debug("on_stock_entry_fill: No orderStatusData for orderId=%s", stock_entry_order_id)
            return
        if entry_data.get('status') != 'Filled':
            logging.warning("on_stock_entry_fill: Skipping option entry for orderId=%s (status=%s, not Filled). Option entry only after stock fill.",
                            stock_entry_order_id, entry_data.get('status'))
            return
        handleOptionTradingForEntryFill(connection, stock_entry_order_id, entry_data)
    except Exception as e:
        logging.error("Error in on_stock_entry_fill: %s", e)
        logging.error(traceback.format_exc())


def handleOptionTradingForEntryFill(connection, stock_entry_order_id, entry_data):
    """
    When option_trigger_by_price_level is set (Custom entry + EntryBar etc.): option is
    independent of the stock; it triggers only when price crosses entry (monitor path).
    Do not place option from stock fill in that case.
    Otherwise: place option when stock entry fills (fill path).
    """
    try:
        # Resolve entry_data so we can check option_trigger_by_price_level and status
        if not isinstance(entry_data, dict):
            entry_data = Config.orderStatusData.get(stock_entry_order_id, {})
        # Only place option entry when stock entry has actually filled (prevents replay/second-trade race)
        if entry_data.get('status') != 'Filled':
            logging.warning("handleOptionTradingForEntryFill: Skipping option entry for orderId=%s (status=%s). Option entry only when stock entry is Filled.",
                            stock_entry_order_id, entry_data.get('status'))
            return
        # Option only triggers from price: do not place from stock fill; monitor will place when price crosses
        if entry_data.get('option_trigger_by_price_level'):
            logging.debug("handleOptionTradingForEntryFill: Option triggers by price level for orderId=%s, skipping (option independent of stock fill)", stock_entry_order_id)
            return
        # Avoid double placement: if option was already triggered (e.g. by price-level monitor), skip
        if Config.orderStatusData.get(int(stock_entry_order_id), {}).get('option_entry_triggered'):
            logging.debug("handleOptionTradingForEntryFill: Option already triggered for orderId=%s, skipping", stock_entry_order_id)
            return
        # Claim trigger so price-level monitor skips while we resolve params and place (prevents race)
        if int(stock_entry_order_id) not in Config.orderStatusData:
            Config.orderStatusData[int(stock_entry_order_id)] = {}
        Config.orderStatusData[int(stock_entry_order_id)]['option_entry_triggered'] = True
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
            logging.debug("Option trading: Using option_params from entryData for %s", symbol)
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
                logging.debug("Retrieved option params from Config.option_trade_params for %s", symbol)
        
        if not option_params or not option_params.get('enabled'):
            logging.warning("Option trading: No option params found or not enabled for %s", symbol)
            Config.orderStatusData[int(stock_entry_order_id)]['option_entry_triggered'] = False  # release so price-level monitor can place
            return
        
        # Get prices from entry_data
        # Use the monitored entry price if available (updated every second before fill)
        entry_price = entry_data.get('option_entry_price') or entry_data.get('filledPrice') or entry_data.get('lastPrice', 0)
        stop_loss_price = entry_data.get('stop_loss_price') or entry_data.get('stopLossPrice')
        profit_price = entry_data.get('tp_price') or entry_data.get('profit_price')
        
        # Try to get from stored TP/SL in entry order's orderStatusData (if sendTpSlSell already wrote them).
        # Option entry only needs entry_price and entry level; TP/SL are for option exit later (another path).
        if not stop_loss_price or not profit_price:
            entry_order_data = Config.orderStatusData.get(int(stock_entry_order_id), {})
            if not stop_loss_price:
                stop_loss_price = entry_order_data.get('stop_loss_price') or entry_order_data.get('stopLossPrice')
            if not profit_price:
                profit_price = entry_order_data.get('tp_price') or entry_order_data.get('profit_price')
        
        logging.debug("handleOptionTradingForEntryFill: Using entry_price=%.2f (from option_entry_price=%s, filledPrice=%s, lastPrice=%s); sl=%s, tp=%s (for exit path)", 
                    entry_price, entry_data.get('option_entry_price'), entry_data.get('filledPrice'), entry_data.get('lastPrice'), stop_loss_price, profit_price)
        
        # Place option entry with entry_price only; TP/SL can be None (filled later when stock TP/SL written).
        if entry_price:
            # Place option orders asynchronously (from_entry_fill=True so we place immediately, not deferred to monitoring)
            # option_entry_triggered already set at start of this function
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
                    from_entry_fill=True,
                )
            )
            logging.debug("Option by fill (entry only): %s entry=%s; sl/tp for exit path: sl=%s, tp=%s", 
                        symbol, entry_price, stop_loss_price, profit_price)
        else:
            logging.warning("Option trading: Cannot place option entry - missing entry_price (entry=%s)", entry_price)
            Config.orderStatusData[int(stock_entry_order_id)]['option_entry_triggered'] = False  # release so price-level monitor can place
    except Exception as e:
        logging.error("Error in handleOptionTradingForEntryFill: %s", e)
        logging.error(traceback.format_exc())
        # Release claim so price-level monitor can place if fill path failed
        try:
            if int(stock_entry_order_id) in Config.orderStatusData:
                Config.orderStatusData[int(stock_entry_order_id)]['option_entry_triggered'] = False
        except Exception:
            pass


async def _wait_next_tick(ticker, timeout_sec=0.4):
    """Wait for next market data update on ticker (streaming). Returns when updateEvent fires or timeout."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    fut = loop.create_future()
    def on_update(_):
        if not fut.done():
            try:
                fut.set_result(None)
            except Exception:
                pass
    try:
        ticker.updateEvent += on_update
        await asyncio.wait_for(fut, timeout=timeout_sec)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            ticker.updateEvent -= on_update
        except Exception:
            pass


async def _wait_for_option_entry_fill(connection, option_entry_order_id, timeout_sec=25):
    """
    Wait until the option entry order is Filled (or Cancelled/Inactive).
    IB does not allow open BUY and SELL on the same US option; we must place the exit only after entry has filled.
    Returns True if status is Filled, False if timeout or order cancelled.
    """
    start = time.monotonic()
    while (time.monotonic() - start) < timeout_sec:
        od = Config.orderStatusData.get(option_entry_order_id)
        status = od.get('status') if od else None
        if status == 'Filled':
            return True
        if status in ('Cancelled', 'Inactive', 'Canceled'):
            logging.debug("Option entry %s is %s, will not place exit", option_entry_order_id, status)
            return False
        await asyncio.sleep(0.25)
    logging.warning("Timeout waiting for option entry %s to fill (status=%s)", option_entry_order_id, Config.orderStatusData.get(option_entry_order_id, {}).get('status'))
    return False


async def monitorUnderlyingAndPlaceOptionOrders(connection, stock_entry_order_id, symbol, entry_price, stop_loss_price,
                                                tp_price, buy_sell_type, option_params, quantity, tif, pre_resolved_contract=None, pre_resolved_option_right=None):
    """
    Option entry uses the same condition as the stock: it can be triggered when the
    stock entry order fills (in handleOptionTradingForEntryFill) or when underlying
    price crosses entry (here). We avoid double placement by checking option_entry_triggered.
    Monitor underlying price; if entry not yet placed and last crosses entry, place option.
    Then place option SL/TP when price reaches those levels. Uses streaming market data.
    If pre_resolved_contract is provided, option is placed without refetching stock/chain.
    """
    stock_ticker = None
    stock_contract = None
    try:
        if not _is_rth():
            return
        stock_contract = Stock(symbol, 'SMART', 'USD')
        stock_contract = connection.ib.qualifyContracts(stock_contract)[0]
        entry_placed = False
        option_entry_order_id = None
        exit_placed = False
        # BUY: entry when last >= entry_price; SL when last <= stop_loss_price; TP when last >= tp_price
        # SELL: entry when last <= entry_price; SL when last >= stop_loss_price; TP when last <= tp_price
        is_buy = (buy_sell_type or 'BUY').upper() == 'BUY'
        no_data_count = 0
        iter_count = 0
        try:
            stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
            await asyncio.sleep(0.2)
        except Exception as e:
            logging.debug("monitorUnderlyingAndPlaceOptionOrders: reqMktData: %s", e)
        while True:
            # Wait for next tick (streaming) or short timeout so we can check order status
            if stock_ticker:
                await _wait_next_tick(stock_ticker, timeout_sec=0.35)
            else:
                await asyncio.sleep(0.4)
            iter_count += 1
            # Stop if stock entry order no longer exists or is cancelled
            entry_data = Config.orderStatusData.get(stock_entry_order_id)
            if not entry_data:
                logging.debug("monitorUnderlyingAndPlaceOptionOrders: No orderStatusData for stock entry %s, stopping", stock_entry_order_id)
                break
            if entry_data.get('status') in ('Cancelled', 'Inactive'):
                logging.debug("monitorUnderlyingAndPlaceOptionOrders: Stock entry %s is %s, stopping", stock_entry_order_id, entry_data.get('status'))
                break
            # Use stock bracket's stored levels when available so option and stock stay synchronized
            synced_entry = entry_data.get('entry_price')
            synced_tp = entry_data.get('tp_price')
            synced_sl = entry_data.get('stop_loss_price')
            if synced_entry is not None:
                entry_price = float(synced_entry)
            if synced_tp is not None:
                tp_price = float(synced_tp)
            if synced_sl is not None:
                stop_loss_price = float(synced_sl)
            last = None
            if stock_ticker:
                last = getattr(stock_ticker, 'last', None) or getattr(stock_ticker, 'close', None)
            if last is None or last <= 0:
                no_data_count += 1
                if no_data_count <= 3 or (no_data_count % 20) == 0:
                    logging.debug("monitorUnderlyingAndPlaceOptionOrders: %s no price yet (last=%s) waiting for market data", symbol, last)
                continue
            no_data_count = 0
            if iter_count <= 3 or (iter_count % 20) == 0:
                logging.debug("monitorUnderlyingAndPlaceOptionOrders: %s last=%.2f entry=%.2f (BUY: need last>=entry; SELL: need last<=entry)", symbol, last, entry_price)
            if not entry_placed:
                # Option may already have been placed by fill (same condition as stock entry)
                if Config.orderStatusData.get(stock_entry_order_id, {}).get('option_entry_triggered'):
                    entry_placed = True
                    option_entry_order_id = (Config.orderStatusData.get(stock_entry_order_id) or {}).get('option_orders', {}).get('entry')
                    logging.debug("monitorUnderlyingAndPlaceOptionOrders: Option already triggered (e.g. on fill) for %s, monitoring SL/TP only", symbol)
                    continue
                entry_hit = (is_buy and last >= entry_price) or (not is_buy and last <= entry_price)
                if entry_hit:
                    logging.debug("Option by price: %s last=%.2f crossed entry=%.2f, placing option entry",
                                 symbol, last, entry_price)
                    if stock_ticker:
                        connection.ib.cancelMktData(stock_contract)
                        stock_ticker = None
                    if stock_entry_order_id not in Config.orderStatusData:
                        Config.orderStatusData[stock_entry_order_id] = {}
                    Config.orderStatusData[stock_entry_order_id]['option_entry_triggered'] = True
                    await placeOptionTradeAndStore(
                        connection,
                        symbol,
                        option_params.get('contract'),
                        option_params.get('expire'),
                        entry_price,
                        stop_loss_price,
                        tp_price,
                        option_params.get('entry_order_type', 'Market'),
                        option_params.get('sl_order_type', 'Market'),
                        option_params.get('tp_order_type', 'Market'),
                        quantity,
                        tif,
                        buy_sell_type,
                        option_params.get('risk_amount'),
                        stock_entry_order_id=stock_entry_order_id,
                        from_entry_fill=True,
                        pre_resolved_contract=pre_resolved_contract,
                        pre_resolved_option_right=pre_resolved_option_right,
                    )
                    entry_placed = True
                    option_entry_order_id = (Config.orderStatusData.get(stock_entry_order_id) or {}).get('option_orders', {}).get('entry')
                    if not option_entry_order_id:
                        logging.warning("monitorUnderlyingAndPlaceOptionOrders: Option entry placed but order id not found")
                    else:
                        logging.debug("Option by price: Option entry placed orderId=%s; will monitor for SL/TP levels", option_entry_order_id)
                    await asyncio.sleep(1.5)
                    if not stock_ticker:
                        stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                        await asyncio.sleep(0.15)
                continue
            if not option_entry_order_id or exit_placed:
                if exit_placed:
                    logging.debug("monitorUnderlyingAndPlaceOptionOrders: Option exit placed for %s, stopping", symbol)
                    break
                option_entry_order_id = (Config.orderStatusData.get(stock_entry_order_id) or {}).get('option_orders', {}).get('entry')
                continue
            option_data = Config.orderStatusData.get(option_entry_order_id)
            if not option_data:
                continue
            sl_params = option_data.get('option_sl_params')
            tp_params = option_data.get('option_tp_params')
            if not sl_params or not tp_params:
                continue
            sl_hit = (is_buy and last <= stop_loss_price) or (not is_buy and last >= stop_loss_price)
            tp_hit = (is_buy and last >= tp_price) or (not is_buy and last <= tp_price)
            if sl_hit:
                logging.debug("Option by price: %s reached SL level (price=%.2f, sl=%.2f), waiting for option entry to fill before placing exit", symbol, last, stop_loss_price)
                if await _wait_for_option_entry_fill(connection, option_entry_order_id):
                    await placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, sl_params, 'OptionStopLoss')
                    exit_placed = True
                else:
                    logging.warning("Option by price: SL level hit but option entry did not fill in time, skipping option stop loss")
            elif tp_hit:
                logging.debug("Option by price: %s reached TP level (price=%.2f, tp=%.2f), waiting for option entry to fill before placing exit", symbol, last, tp_price)
                if await _wait_for_option_entry_fill(connection, option_entry_order_id):
                    await placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, tp_params, 'OptionProfit')
                    exit_placed = True
                else:
                    logging.warning("Option by price: TP level hit but option entry did not fill in time, skipping option take profit")
    except Exception as e:
        logging.error("Error in monitorUnderlyingAndPlaceOptionOrders: %s", e)
        logging.error(traceback.format_exc())
    finally:
        if stock_ticker and stock_contract:
            try:
                connection.ib.cancelMktData(stock_contract)
            except Exception:
                pass


def startOptionTradingByPriceLevel(connection, stock_entry_order_id, symbol, timeFrame, barType, buySellType,
                                   entry_price, stop_loss_price, tp_price, quantity, tif):
    """
    Start option trading triggered by underlying price levels (entry, SL, TP) instead of stock order fills.
    Used for Custom entry with bracket orders in RTH: option entry when price reaches entry,
    option SL/TP when price reaches those levels, so option and stock can trigger simultaneously.
    """
    try:
        if not _is_rth():
            logging.debug("startOptionTradingByPriceLevel: RTH only, skipping")
            return
        option_params = None
        latest_ts = None
        if hasattr(Config, 'option_trade_params') and Config.option_trade_params:
            for trade_key, params in list(Config.option_trade_params.items()):
                if not isinstance(trade_key, (tuple, list)) or len(trade_key) < 5:
                    continue
                key_symbol, key_tf, key_bar, key_side, ts = trade_key[0], trade_key[1], trade_key[2], trade_key[3], trade_key[4]
                if (key_symbol == symbol and key_tf == timeFrame and key_bar == barType and key_side == buySellType
                        and params.get('enabled')):
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts
                        option_params = params
        if not option_params or not option_params.get('enabled'):
            logging.debug("startOptionTradingByPriceLevel: No option params for %s %s %s %s (option by price level skipped)", symbol, timeFrame, barType, buySellType)
            return
        if stock_entry_order_id not in Config.orderStatusData:
            Config.orderStatusData[stock_entry_order_id] = {}
        Config.orderStatusData[stock_entry_order_id]['option_trigger_by_price_level'] = True
        logging.info("Option by price level: Starting monitor for %s entry=%.2f sl=%.2f tp=%.2f (option will trigger at these levels)",
                     symbol, entry_price, stop_loss_price, tp_price)
        pre_resolved_contract, pre_resolved_option_right = _pre_resolve_option_contract(
            connection, symbol, entry_price,
            option_params.get('contract'), option_params.get('expire'), buySellType
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = getattr(connection.ib, 'loop', None) or asyncio.get_event_loop()
        loop.create_task(
            monitorUnderlyingAndPlaceOptionOrders(
                connection, stock_entry_order_id, symbol, entry_price, stop_loss_price, tp_price,
                buySellType, option_params, quantity, tif,
                pre_resolved_contract=pre_resolved_contract,
                pre_resolved_option_right=pre_resolved_option_right,
            )
        )
    except Exception as e:
        logging.error("Error in startOptionTradingByPriceLevel: %s", e)
        logging.error(traceback.format_exc())


def handleOptionEntryFill(connection, option_entry_order_id):
    """
    Handle option entry order fill: Option exit (SL/TP) is placed only after the option entry is filled,
    to avoid IBKR "Cannot have open orders on both sides of the same US Option contract" (Error 201).
    If the corresponding stock TP/SL was already filled before the option entry filled, place the
    option exit here (once per type) so we do not place exit while the option entry is still open.
    """
    try:
        option_data = Config.orderStatusData.get(option_entry_order_id)
        if not option_data:
            logging.warning("Option entry order %s not found in orderStatusData", option_entry_order_id)
            return
        
        logging.debug("Option entry order %s filled. Option SL/TP will be placed when stock SL/TP fill, or now if already filled.", option_entry_order_id)
        
        # Resolve stock entry for this option (stored when option was placed from stock fill)
        stock_entry_order_id = option_data.get('stock_entry_order_id')
        if not stock_entry_order_id:
            for _sid, _sdata in list(Config.orderStatusData.items()):
                if _sdata.get('option_orders', {}).get('entry') == option_entry_order_id:
                    stock_entry_order_id = _sid
                    break
        if not stock_entry_order_id:
            return
        
        # When option is triggered by price level only, do not place option exit here;
        # the price-level monitor is the single source for option exit (avoids duplicate option sell orders).
        entry_data = Config.orderStatusData.get(stock_entry_order_id)
        if isinstance(entry_data, dict) and entry_data.get('option_trigger_by_price_level'):
            logging.debug("handleOptionEntryFill: Option triggers by price level for entry %s, skipping option exit (monitor handles exit)", stock_entry_order_id)
            return
        
        # Place option exit only if corresponding stock TP/SL is already filled (race: stock TP/SL filled before option entry).
        # Track placed exit types to avoid duplicate orders; use lock so only one of triggerOptionOrderOnStockFill vs
        # handleOptionEntryFill places the exit when events are processed concurrently.
        placed = option_data.setdefault('option_exits_placed', set())
        for oid, odata in list(Config.orderStatusData.items()):
            ed = odata.get('entryData')
            if not isinstance(ed, dict) or ed.get('orderId') != stock_entry_order_id:
                continue
            ord_type = odata.get('ordType')
            status = odata.get('status')
            if ord_type == 'TakeProfit' and status == 'Filled':
                tp_params = option_data.get('option_tp_params')
                if tp_params:
                    with _option_exit_placement_lock:
                        if 'OptionProfit' in placed:
                            continue
                        placed.add('OptionProfit')
                    logging.debug("Option entry %s filled; stock take profit already filled (orderId=%s) -> placing option exit now", option_entry_order_id, oid)
                    asyncio.ensure_future(
                        placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, tp_params, 'OptionProfit')
                    )
            elif ord_type == 'StopLoss' and status == 'Filled':
                sl_params = option_data.get('option_sl_params')
                if sl_params:
                    with _option_exit_placement_lock:
                        if 'OptionStopLoss' in placed:
                            continue
                        placed.add('OptionStopLoss')
                    logging.info("Option entry %s filled; stock stop loss already filled (orderId=%s) -> placing option exit now", option_entry_order_id, oid)
                    asyncio.ensure_future(
                        placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, sl_params, 'OptionStopLoss')
                    )
    except Exception as e:
        logging.error("Error in handleOptionEntryFill: %s", e)
        logging.error(traceback.format_exc())

def triggerOptionOrderOnStockFill(connection, stock_order_id, ord_type, bar_type):
    """
    Trigger option orders when stock orders fill.

    Option entry is NOT tied to stock SL/TP; it uses the same stop size as the stock
    and is triggered only when stock ENTRY fills.

    Option exit: when stock TP order fills (price crossed over TP = entry + mult*stop_size),
    place option take-profit exit. When stock SL order fills (price crossed below SL = entry - stop_size),
    place option stop-loss exit. Same logic as stock; for SELL stock the directions are opposite.
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
        
        # When option is triggered by price level only, do not place option exit from stock fill;
        # the price-level monitor handles option exit (avoids duplicate option sell orders).
        if entry_data.get('option_trigger_by_price_level'):
            logging.debug("triggerOptionOrderOnStockFill: Option triggers by price level for entry %s, skipping option exit from stock fill", entry_order_id)
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
        
        # Get the appropriate parameters based on ord_type.
        # Use lock so only one of triggerOptionOrderOnStockFill vs handleOptionEntryFill places the exit when events are processed concurrently.
        placed = option_entry_data.setdefault('option_exits_placed', set())
        if ord_type == 'StopLoss':
            sl_params = option_entry_data.get('option_sl_params')
            if not sl_params:
                logging.warning("Stock stop loss filled (orderId=%s) but option_sl_params not found for option entry %s", 
                              stock_order_id, option_entry_order_id)
                return
            with _option_exit_placement_lock:
                if 'OptionStopLoss' in placed:
                    return
                placed.add('OptionStopLoss')
            logging.info("Stock SL filled (orderId=%s) -> placing option exit", stock_order_id)
            asyncio.ensure_future(
                placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, sl_params, 'OptionStopLoss')
            )
        elif ord_type == 'TakeProfit':
            tp_params = option_entry_data.get('option_tp_params')
            if not tp_params:
                logging.warning("Stock take profit filled (orderId=%s) but option_tp_params not found for option entry %s", 
                              stock_order_id, option_entry_order_id)
                return
            with _option_exit_placement_lock:
                if 'OptionProfit' in placed:
                    return
                placed.add('OptionProfit')
            logging.info("Stock take profit ORDER FILLED (orderId=%s) -> Placing IMMEDIATE option exit order (SELL call/put)", stock_order_id)
            asyncio.ensure_future(
                placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, tp_params, 'OptionProfit')
            )
    except Exception as e:
        logging.error("Error in triggerOptionOrderOnStockFill: %s", e)
        logging.error(traceback.format_exc())

def handleOptionTpSlFill(connection, option_order_id, ord_type):
    """Handle option TP/SL fill: Cancel the other bracket order."""
    try:
        # Find the OptionEntry that has this order in its option_orders (stop_loss or profit)
        pair_id = None
        for oid, odata in list(Config.orderStatusData.items()):
            if odata.get('ordType') != 'OptionEntry':
                continue
            option_orders = odata.get('option_orders', {})
            if ord_type == 'OptionStopLoss' and option_orders.get('stop_loss') == option_order_id:
                pair_id = option_orders.get('profit')
                break
            if ord_type == 'OptionProfit' and option_orders.get('profit') == option_order_id:
                pair_id = option_orders.get('stop_loss')
                break
        if pair_id is not None:
            pair_id = int(pair_id)
            # Get trades - handle both dict-like and list returns
            trades = connection.ib.trades()
            pair_trade = None
            if isinstance(trades, dict) or (hasattr(trades, 'get') and hasattr(trades, '__contains__')):
                # Dict-like object - try to get by order ID
                try:
                    if pair_id in trades:
                        pair_trade = trades[pair_id] if isinstance(trades, dict) else trades.get(pair_id)
                except (TypeError, KeyError):
                    pass
            elif isinstance(trades, list):
                # List - find trade with matching order ID
                for trade in trades:
                    if hasattr(trade, 'order') and hasattr(trade.order, 'orderId') and trade.order.orderId == pair_id:
                        pair_trade = trade
                        break
            if pair_trade:
                connection.cancelTrade(pair_trade.order)
                logging.debug("Option %s filled (orderId=%s), cancelled bracket pair order %s", ord_type, option_order_id, pair_id)
    except Exception as e:
        logging.error("Error in handleOptionTpSlFill: %s", e)
        logging.error(traceback.format_exc())

async def monitorOptionEntryBeforeStockFill(connection, stock_entry_order_id, symbol, option_params, buy_sell_type):
    """
    Monitor stock price and update option entry condition every second BEFORE stock entry fills.
    This is similar to RBB logic - we monitor and update, but don't place the option order until stock entry fills.
    """
    try:
        logging.debug("monitorOptionEntryBeforeStockFill: Starting monitoring for stock_entry_order_id=%s, symbol=%s", 
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
                logging.debug("monitorOptionEntryBeforeStockFill: Initial entry price set to %.2f for stock_order=%s", 
                            initial_entry_price, stock_entry_order_id)
        
        while True:
            try:
                await asyncio.sleep(1)  # Check every second
                
                # Check if stock entry order still exists and is not filled
                entry_data = Config.orderStatusData.get(stock_entry_order_id)
                if not entry_data:
                    logging.debug("monitorOptionEntryBeforeStockFill: Stock entry order %s not found, stopping monitoring", 
                                stock_entry_order_id)
                    break
                
                # If stock entry is filled, option was placed by fill handler (same condition as stock); stop monitoring
                if entry_data.get('status') == 'Filled':
                    logging.debug("monitorOptionEntryBeforeStockFill: Stock entry order %s is FILLED, stopping monitoring", 
                                stock_entry_order_id)
                    break
                
                # If stock entry is cancelled/inactive, stop monitoring
                if entry_data.get('status') in ['Cancelled', 'Inactive']:
                    logging.debug("monitorOptionEntryBeforeStockFill: Stock entry order %s is %s, stopping monitoring", 
                                stock_entry_order_id, entry_data.get('status'))
                    break
                
                # Get current stock price
                stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                await asyncio.sleep(0.1)  # Small delay for market data
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
                    logging.debug("monitorOptionEntryBeforeStockFill: Updated option entry price to %.2f for stock_order=%s (current_stock_price=%.2f)", 
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
            logging.debug("updateOptionOrdersForRBB: Updated option entry price to %.2f for stock_order=%s", 
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
                            logging.debug("monitorAndPlacePendingOptionEntry: Price moved below entry (%.2f < %.2f), waiting for crossing", 
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
                        logging.debug("monitorAndPlacePendingOptionEntry: Entry condition met (price=%.2f, entry=%.2f), placing option entry order", 
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
        logging.debug("Option entry order placed from pending (conditional): If %s %s %.2f, then %s %d contracts (%s), orderId=%s",
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
        
        logging.debug("Option entry order placed and stored from pending: orderId=%s, stock_entry_order_id=%s", 
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
                    logging.debug("Option order %s: Stock condition not yet met (price=%.2f, condition=%.2f, method=%s). Waiting...", 
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
                        logging.debug("Option order %s: Stock condition still not met. Will monitor order status instead.", order_id)
            except Exception as e:
                logging.warning("Could not check stock condition for order %s: %s. Proceeding with price adjustments.", order_id, e)
        
        current_price = initial_price
        adjustment_count = 0
        
        logging.info("Starting Bid+/Ask- adjustment for order %s: type=%s, initial=%.2f, action=%s (interval=%.0fs, increment=%.2f, max=%d)", 
                    order_id, order_type, initial_price, action, BID_ASK_ADJUST_INTERVAL_SEC, BID_ASK_ADJUST_INCREMENT, BID_ASK_MAX_ADJUSTMENTS)
        
        while adjustment_count < BID_ASK_MAX_ADJUSTMENTS:
            await asyncio.sleep(BID_ASK_ADJUST_INTERVAL_SEC)
            
            try:
                order_status = getattr(getattr(trade, 'orderStatus', None), 'status', None)
                if order_status is None and order_id in Config.orderStatusData:
                    order_status = Config.orderStatusData[order_id].get('status')
                
                if order_status in ('Filled', 'Cancelled'):
                    logging.debug("Option order %s status is %s. Stopping price adjustments.", order_id, order_status)
                    break
                
                if order_status not in ('PreSubmitted', 'Submitted', 'PendingSubmit', 'PendingCancel'):
                    logging.debug("Option order %s status: %s", order_id, order_status)
                    continue
                
                if order_type == 'Bid+':
                    new_price = round(current_price + BID_ASK_ADJUST_INCREMENT, 2)
                    logging.debug("Bid+ order %s not filled at %.2f, adjusting to %.2f (adjustment %d/%d)", 
                               order_id, current_price, new_price, adjustment_count + 1, BID_ASK_MAX_ADJUSTMENTS)
                elif order_type == 'Ask-':
                    new_price = round(max(current_price - BID_ASK_ADJUST_INCREMENT, MIN_OPTION_LIMIT_PRICE), 2)
                    if new_price <= MIN_OPTION_LIMIT_PRICE and current_price <= MIN_OPTION_LIMIT_PRICE:
                        logging.warning("Ask- order %s: Already at minimum %.2f. Stopping.", order_id, MIN_OPTION_LIMIT_PRICE)
                        break
                    logging.debug("Ask- order %s not filled at %.2f, adjusting to %.2f (adjustment %d/%d)", 
                               order_id, current_price, new_price, adjustment_count + 1, BID_ASK_MAX_ADJUSTMENTS)
                else:
                    logging.error("Unknown order type for adjustment: %s", order_type)
                    break
                
                try:
                    trade.order.lmtPrice = new_price
                    connection.ib.placeOrder(trade.contract, trade.order)
                    current_price = new_price
                    adjustment_count += 1
                    logging.debug("Option order %s price adjusted to %.2f", order_id, new_price)
                except Exception as e:
                    logging.error("Error modifying order %s price: %s", order_id, e)
                    break
                    
            except Exception as e:
                logging.error("Error checking order status for %s: %s", order_id, e)
                break
        
        if adjustment_count >= BID_ASK_MAX_ADJUSTMENTS:
            logging.warning("Option order %s: Reached maximum adjustments (%d). Stopping.", order_id, BID_ASK_MAX_ADJUSTMENTS)
        else:
            logging.info("Option order %s: Bid+/Ask- monitoring done (adjustments made: %d)", order_id, adjustment_count)
            
    except Exception as e:
        logging.error("Error in monitorAndAdjustBidAskOrder: %s", e)
        logging.error(traceback.format_exc())

async def placeOptionStopLossOrTakeProfit(connection, option_entry_order_id, params, ord_type_name):
    """
    Place option stop loss or take profit order when corresponding stock order fills.
    This function is called from triggerOptionOrderOnStockFill.
    
    IMPORTANT: This function places IMMEDIATE market orders (no price conditions).
    It is ONLY called when stock TP/SL orders actually FILL, not when stock price reaches those levels.
    Option exit orders are NOT conditional orders based on stock price.
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
        
        # Note: condition_price is stored for reference only (from stock TP/SL order); may be None if option entry was placed before TP/SL written
        # Option exit orders are placed IMMEDIATELY as market orders when stock TP/SL orders fill
        # They are NOT conditional orders based on stock price
        logging.debug("Option %s: Stock %s order filled, placing immediate option exit order (stock TP/SL price was %s, current_price=%s)", 
                    ord_type_name, ord_type_name.replace('Option', ''), condition_price if condition_price is not None else "N/A", current_stock_price if current_stock_price is not None else "N/A")
        
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
        
        # When called from triggerOptionOrderOnStockFill (stock SL/TP filled), place market order immediately
        # Do NOT use conditional orders - they can trigger incorrectly based on price spikes
        # Place market order directly since stock SL/TP has already filled
        order.conditions = []  # No conditions - place immediately as market order
        order.conditionsIgnoreRth = False
        
        # If order_type is Market, ensure it's a market order
        if order_type == 'Market':
            order.orderType = 'MKT'
            order.lmtPrice = 0.0
        
        # Place the order immediately (not conditional)
        try:
            trade = connection.placeTrade(option_contract, order, outsideRth=False)
            if trade:
                initial_limit_price = order.lmtPrice if order.orderType == 'LMT' else None
                
                logging.info("Option %s placed: %s %d contract(s), orderId=%s", ord_type_name, action, quantity, order_id)
                
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
            logging.error("Error placing option %s order: %s", ord_type_name, e)
            logging.error(traceback.format_exc())
            return None
    except Exception as e:
        logging.error("Error in placeOptionStopLossOrTakeProfit: %s", e)
        logging.error(traceback.format_exc())
        return None
