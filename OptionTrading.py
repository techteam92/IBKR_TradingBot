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


def _resolve_option_from_dropdowns(connection, symbol, strike_code, expiry_code, right):
    """
    Resolve ATM/OTM and week-offset dropdown values into:
      - concrete strike
      - concrete expiration (YYYYMMDD)
      - preferred exchange
      - tradingClass

    - strike_code: 'ATM', 'OTM1', 'OTM 1', 'OTM2', etc., or a numeric strike as string
    - expiry_code: '0', '1', '2', '4', '8', or a concrete YYYYMMDD string
    """
    stock = None
    try:
        # If both already look concrete, just return them with default exchange
        if strike_code and expiry_code and strike_code.replace('.', '', 1).isdigit() and len(expiry_code) == 8 and expiry_code.isdigit():
            return float(strike_code), expiry_code, 'SMART', None
        
        # Qualify underlying stock to get conId
        stock = Stock(symbol, 'SMART', 'USD')
        qualified_stocks = connection.ib.qualifyContracts(stock)
        if not qualified_stocks:
            logging.error("Failed to qualify underlying stock for options: %s", symbol)
            return None, None
        stock = qualified_stocks[0]
        
        # Get current underlying price (for ATM/OTM)
        ticker = connection.ib.reqMktData(stock, '', False, False)
        connection.ib.sleep(1)
        underlying_price = None
        if ticker:
            if ticker.last:
                underlying_price = ticker.last
            elif ticker.close:
                underlying_price = ticker.close
            elif hasattr(ticker, "marketPrice") and ticker.marketPrice():
                underlying_price = ticker.marketPrice()
        if underlying_price is None:
            logging.error("Could not determine underlying price for %s to resolve ATM/OTM", symbol)
            return None, None
        
        # Request option parameters (strikes/expirations)
        params_list = connection.ib.reqSecDefOptParams(symbol, '', 'STK', stock.conId)
        if not params_list:
            logging.error("reqSecDefOptParams returned no data for %s", symbol)
            return None, None
        
        # Prefer non-SMART exchange (IB rejects SMART for option contracts)
        # If no non-SMART found, use first entry
        params = None
        for p in params_list:
            exchange = getattr(p, "exchange", "")
            if exchange and exchange != 'SMART':
                params = p
                break
        if params is None:
            params = params_list[0]  # Fallback to first entry if all are SMART
        
        strikes = sorted([s for s in getattr(params, "strikes", []) if s and s > 0])
        expirations = sorted(getattr(params, "expirations", []))
        if not strikes or not expirations:
            logging.error("No strikes or expirations available for %s options", symbol)
            return None, None, None, None
        
        # Resolve expiration by weeks out
        resolved_expiry = None
        if expiry_code and len(expiry_code) == 8 and expiry_code.isdigit():
            resolved_expiry = expiry_code
        else:
            try:
                weeks_out = int(expiry_code) if expiry_code is not None and expiry_code != "" else 0
            except ValueError:
                weeks_out = 0
            index = min(max(weeks_out, 0), len(expirations) - 1)
            resolved_expiry = expirations[index]
        
        # Resolve strike: ATM / OTM steps
        normalized = (strike_code or '').replace(' ', '').upper()
        step_index = 0
        if normalized.startswith('OTM'):
            try:
                step_index = int(normalized.replace('OTM', ''))
            except ValueError:
                step_index = 1
        
        # Find ATM index
        closest_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - underlying_price))
        
        if normalized == 'ATM' or not normalized:
            resolved_strike = strikes[closest_idx]
        else:
            # For calls (right='C'), OTM is higher strikes; for puts, lower strikes
            direction = 1 if right == 'C' else -1
            target_idx = closest_idx + direction * step_index
            target_idx = max(0, min(target_idx, len(strikes) - 1))
            resolved_strike = strikes[target_idx]

        # Use exchange/tradingClass from params so IB can qualify the contract correctly
        # Don't use 'SMART' - IB rejects it for options. Use the first valid exchange or None
        opt_exchange = getattr(params, "exchange", None)
        if opt_exchange == 'SMART' or not opt_exchange:
            opt_exchange = None  # Let IB choose the exchange during qualification
        opt_trading_class = getattr(params, "tradingClass", None)

        return resolved_strike, resolved_expiry, opt_exchange, opt_trading_class
    except Exception as e:
        logging.error("Error resolving option from dropdowns for %s: %s", symbol, e)
        logging.error(traceback.format_exc())
        return None, None, None, None
    finally:
        # Best-effort: cancel mkt data on stock
        if stock is not None:
            try:
                connection.ib.cancelMktData(stock)
            except Exception:
                pass


def getOptionContract(connection, symbol, strike, expiration_date, right='C'):
    """
    Create an Option contract for the given symbol, strike price, and expiration date.
    Supports:
      - Direct strike/expiration (e.g. '255', '20260119')
      - Dropdown-style inputs: 'ATM', 'OTM1', 'OTM 2' and expiry codes '0','1','2','4','8'
    """
    try:
        strike_str = "" if strike is None else str(strike)
        expiry_str = "" if expiration_date is None else str(expiration_date)
        resolved_strike, resolved_expiry, opt_exchange, opt_trading_class = _resolve_option_from_dropdowns(
            connection, symbol, strike_str, expiry_str, right
        )
        if resolved_strike is None or resolved_expiry is None:
            logging.error(
                "Failed to resolve option from dropdowns for %s (strike=%s, expiry=%s)",
                symbol,
                strike,
                expiration_date,
            )
            return None
        
        # IB expects lastTradeDateOrContractMonth as 'YYYYMMDD' string, not a datetime.date.
        # resolved_expiry is already in 'YYYYMMDD' format (from reqSecDefOptParams), so use it directly.
        if not (len(resolved_expiry) == 8 and resolved_expiry.isdigit()):
            logging.error("Invalid resolved expiration date format: %s (expected YYYYMMDD)", resolved_expiry)
            return None
        expiration_str = resolved_expiry

        strike_float = float(resolved_strike)
        
        # Create option contract without specifying exchange/tradingClass.
        # Let IB/ib_insync fill those fields during qualification.
        option = Option(symbol, expiration_str, strike_float, right)
        
        # Qualify the contract to get full details (including correct exchange/tradingClass)
        # Note: IB may return multiple contracts (one per exchange) - ib_insync logs "Ambiguous contract"
        # but still returns a list. We'll use the first one (usually SMART).
        try:
            qualified = connection.ib.qualifyContracts(option)
            if qualified and len(qualified) > 0:
                # IB may return multiple contracts (one per exchange) - just pick the first one
                # Usually SMART is first, which is fine for trading
                option = qualified[0]
                logging.info(
                    "Option contract created and qualified: %s %s %s %s @ %s (tradingClass=%s) - %d possible contracts, using first",
                    symbol,
                    expiration_str,
                    right,
                    strike_float,
                    option.exchange,
                    getattr(option, 'tradingClass', 'N/A'),
                    len(qualified),
                )
                return option
            else:
                # Empty result - IB may have found ambiguous contracts but returned empty list
                # Try again with SMART exchange explicitly specified
                logging.warning(
                    "qualifyContracts returned empty for %s %s %s %s (ambiguous contract). Retrying with SMART exchange...",
                    symbol, expiration_str, right, strike_float
                )
                option_smart = Option(symbol, expiration_str, strike_float, right, exchange='SMART')
                qualified_smart = connection.ib.qualifyContracts(option_smart)
                if qualified_smart and len(qualified_smart) > 0:
                    option = qualified_smart[0]
                    logging.info(
                        "Option contract qualified with SMART exchange: %s %s %s %s @ %s",
                        symbol, expiration_str, right, strike_float, option.exchange
                    )
                    return option
                else:
                    logging.error(
                        "Failed to qualify option contract (empty result even with SMART): %s %s %s %s",
                        symbol,
                        expiration_str,
                        right,
                        strike_float,
                    )
                    return None
        except Exception as qual_ex:
            # If qualification raises an exception (e.g., ambiguous contract), try to get contracts from the exception
            # or retry with a more specific contract
            logging.warning(
                "Exception during contract qualification for %s %s %s %s: %s. Trying to use SMART exchange explicitly...",
                symbol, expiration_str, right, strike_float, qual_ex
            )
            try:
                # Try with SMART exchange explicitly
                option_smart = Option(symbol, expiration_str, strike_float, right, exchange='SMART')
                qualified_smart = connection.ib.qualifyContracts(option_smart)
                if qualified_smart and len(qualified_smart) > 0:
                    option = qualified_smart[0]
                    logging.info(
                        "Option contract qualified with SMART exchange: %s %s %s %s @ %s",
                        symbol, expiration_str, right, strike_float, option.exchange
                    )
                    return option
            except Exception as e2:
                logging.error("Failed to qualify with SMART exchange: %s", e2)
            logging.error(
                "Failed to qualify option contract: %s %s %s %s",
                symbol,
                expiration_str,
                right,
                strike_float,
            )
            return None
    except Exception as e:
        logging.error("Error creating option contract for %s: %s", symbol, e)
        logging.error(traceback.format_exc())
        return None


async def monitorAndPlacePendingOptionOrder(connection, pending_key, condition_price, trigger_method, ord_type_name):
    """
    Monitor stock price and place pending option order when condition becomes valid.
    
    For stop loss (trigger_method=2): Wait until stock price > condition_price, then place order
    For take profit (trigger_method=1): Wait until stock price < condition_price, then place order
    
    Args:
        connection: IB connection object
        pending_key: Key in Config.pending_option_orders
        condition_price: The price threshold
        trigger_method: 1 (crosses above) or 2 (breaks below)
        ord_type_name: 'OptionStopLoss' or 'OptionProfit'
    """
    try:
        pending_order = Config.pending_option_orders.get(pending_key)
        if not pending_order:
            logging.warning("Pending option order %s not found, stopping monitoring", pending_key)
            return
        
        stock_contract = pending_order['stock_contract']
        symbol = pending_order['symbol']
        check_interval = 1  # Check every 1 second
        max_checks = 3600  # Monitor for up to 1 hour (3600 seconds)
        check_count = 0
        
        logging.info("Starting monitoring for pending option order %s: %s (condition: stock %s %.2f)", 
                    pending_key, ord_type_name,
                    "breaks below" if trigger_method == 2 else "crosses above", condition_price)
        
        while check_count < max_checks:
            await asyncio.sleep(check_interval)
            check_count += 1
            
            try:
                # Get current stock price
                stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
                await asyncio.sleep(0.5)  # Wait for market data
                current_price = None
                if stock_ticker:
                    last = getattr(stock_ticker, "last", None)
                    close = getattr(stock_ticker, "close", None)
                    if last and last > 0:
                        current_price = last
                    elif close and close > 0:
                        current_price = close
                connection.ib.cancelMktData(stock_contract)
                
                if current_price is None:
                    continue  # Skip this check if we couldn't get price
                
                # Check if condition is now valid
                condition_valid = False
                if trigger_method == 2:  # Stop loss - breaks below
                    # Condition valid when price > stop_loss (can now place order safely)
                    if current_price > condition_price:
                        condition_valid = True
                        logging.info("Pending option order %s: Stock price (%.2f) recovered above stop loss (%.2f). Condition is now valid, placing order.", 
                                   pending_key, current_price, condition_price)
                elif trigger_method == 1:  # Take profit - crosses above
                    # Condition valid when price < take_profit (can now place order safely)
                    if current_price < condition_price:
                        condition_valid = True
                        logging.info("Pending option order %s: Stock price (%.2f) dropped below take profit (%.2f). Condition is now valid, placing order.", 
                                   pending_key, current_price, condition_price)
                
                if condition_valid:
                    # Place the order now
                    order_id = connection.get_next_order_id()
                    order = Order()
                    order.orderId = order_id
                    order.action = pending_order['action']
                    order.totalQuantity = pending_order['quantity']
                    order.tif = pending_order['tif']
                    
                    # Set order type
                    order_type = pending_order['order_type']
                    if order_type == 'Market':
                        order.orderType = 'MKT'
                    elif order_type == 'Bid+':
                        order.orderType = 'LMT'
                        opt_bid = pending_order.get('opt_bid')
                        opt_price = pending_order.get('opt_price')
                        if opt_bid and opt_bid > 0:
                            order.lmtPrice = round(opt_bid, 2)
                        elif opt_price:
                            order.lmtPrice = round(opt_price, 2)
                        else:
                            order.lmtPrice = 0.01
                    elif order_type == 'Ask-':
                        order.orderType = 'LMT'
                        opt_ask = pending_order.get('opt_ask')
                        opt_price = pending_order.get('opt_price')
                        if opt_ask and opt_ask > 0:
                            order.lmtPrice = round(opt_ask, 2)
                        elif opt_price:
                            order.lmtPrice = round(opt_price, 2)
                        else:
                            order.lmtPrice = 0.01
                    
                    # Create condition
                    condition = PriceCondition(
                        price=condition_price,
                        triggerMethod=trigger_method,
                        conId=stock_contract.conId,
                        exch=pending_order['stock_exchange']
                    )
                    order.conditions = [condition]
                    order.conditionsIgnoreRth = False
                    
                    # Place the order
                    trade = connection.placeTrade(pending_order['option_contract'], order, outsideRth=False)
                    if trade:
                        logging.info("Pending option order %s: Successfully placed conditional order. orderId=%s", pending_key, order_id)
                        # Remove from pending orders
                        Config.pending_option_orders.pop(pending_key, None)
                        
                        # Store order data
                        if order_id not in Config.orderStatusData:
                            Config.orderStatusData[order_id] = {}
                        Config.orderStatusData[order_id].update({
                            'contract': pending_order['option_contract'],
                            'action': pending_order['action'],
                            'totalQuantity': pending_order['quantity'],
                            'type': order_type,
                            'tif': pending_order['tif'],
                            'ordType': ord_type_name,
                            'usersymbol': symbol,
                            'condition_price': condition_price,
                        })
                        
                        # Start Bid+/Ask- monitoring if needed
                        if order_type in ('Bid+', 'Ask-'):
                            initial_price = order.lmtPrice if order.orderType == 'LMT' else None
                            if initial_price:
                                asyncio.ensure_future(
                                    monitorAndAdjustBidAskOrder(connection, trade, order_type, initial_price, pending_order['action'])
                                )
                        break  # Exit monitoring loop
                    else:
                        logging.error("Pending option order %s: Failed to place order", pending_key)
                
                # Log progress every 30 seconds
                if check_count % 30 == 0:
                    logging.info("Pending option order %s: Still monitoring... (check %d/%d, current_price=%.2f, condition_price=%.2f)", 
                               pending_key, check_count, max_checks, current_price or 0, condition_price)
            
            except Exception as e:
                logging.error("Error in monitoring loop for pending option order %s: %s", pending_key, e)
                logging.error(traceback.format_exc())
                await asyncio.sleep(check_interval)
        
        if check_count >= max_checks:
            logging.warning("Pending option order %s: Monitoring timeout after %d checks. Removing from pending orders.", pending_key, max_checks)
            Config.pending_option_orders.pop(pending_key, None)
    
    except Exception as e:
        logging.error("Error in monitorAndPlacePendingOptionOrder for %s: %s", pending_key, e)
        logging.error(traceback.format_exc())
        Config.pending_option_orders.pop(pending_key, None)


async def monitorAndAdjustBidAskOrder(connection, trade, order_type, initial_price, action):
    """
    Monitor Bid+ or Ask- order and adjust price by $0.05 increments until filled.
    
    Bid+: Start at bid, increase by $0.05 if no fill (e.g., $1.00 → $1.05 → $1.10)
    Ask-: Start at ask, decrease by $0.05 if no fill (e.g., $2.00 → $1.95 → $1.90)
    
    For conditional orders: Waits until condition is met and order becomes active before adjusting.
    
    Args:
        connection: IB connection object
        trade: Trade object from initial order placement
        order_type: 'Bid+' or 'Ask-'
        initial_price: Starting limit price
        action: 'BUY' or 'SELL'
    """
    if order_type not in ('Bid+', 'Ask-'):
        return  # Only monitor Bid+ and Ask- orders
    
    order_id = trade.order.orderId
    current_price = initial_price
    adjustment = 0.05
    check_interval = 1  # Check every 1 second (client requirement)
    max_adjustments = 20  # Maximum number of adjustments (safety limit)
    adjustment_count = 0
    condition_met = False  # For conditional orders, wait until condition is met
    
    logging.info(
        "Starting Bid+/Ask- monitor for orderId=%s, order_type=%s, initial_price=%.2f",
        order_id, order_type, initial_price
    )
    
    while adjustment_count < max_adjustments:
        await asyncio.sleep(check_interval)
        
        # Check order status
        order_data = Config.orderStatusData.get(order_id)
        if not order_data:
            logging.warning("Bid+/Ask- monitor: Order %s not found in orderStatusData, stopping", order_id)
            break
        
        status = order_data.get('status', '')
        
        # If order is filled, cancelled, or inactive, stop monitoring
        if status in ('Filled', 'Cancelled', 'Inactive'):
            if status == 'Filled':
                logging.info("Bid+/Ask- monitor: Order %s FILLED at price %.2f after %d adjustments", 
                           order_id, current_price, adjustment_count)
            else:
                logging.info("Bid+/Ask- monitor: Order %s %s, stopping adjustments", order_id, status)
            break
        
        # For conditional orders: Wait until condition is met (order becomes active)
        if not condition_met:
            # Check if order has conditions (it's a conditional order)
            has_conditions = hasattr(trade.order, 'conditions') and trade.order.conditions
            if has_conditions:
                # Order is conditional - wait until it becomes active (condition met)
                if status in ('PreSubmitted', 'Submitted', 'PendingSubmit'):
                    condition_met = True
                    logging.info("Bid+/Ask- monitor: Conditional order %s condition met, starting price adjustments", order_id)
                else:
                    # Still waiting for condition to be met
                    continue
            else:
                # Not a conditional order - start adjusting immediately
                condition_met = True
        
        # Only adjust if condition is met (or not a conditional order)
        if condition_met and status in ('PreSubmitted', 'Submitted', 'PendingSubmit'):
            # Adjust price
            if order_type == 'Bid+':
                new_price = round(current_price + adjustment, 2)
                logging.info("Bid+ adjustment: Order %s not filled, increasing price from %.2f to %.2f", 
                           order_id, current_price, new_price)
            else:  # Ask-
                new_price = round(current_price - adjustment, 2)
                if new_price < 0.01:  # Safety check: don't go below $0.01
                    logging.warning("Ask- adjustment: Price would go below $0.01, stopping adjustments")
                    break
                logging.info("Ask- adjustment: Order %s not filled, decreasing price from %.2f to %.2f", 
                           order_id, current_price, new_price)
            
            # Modify the order with new price
            try:
                # Get the current trade object to modify
                trade.order.lmtPrice = new_price
                modified_trade = connection.ib.placeOrder(trade.contract, trade.order)
                current_price = new_price
                adjustment_count += 1
                logging.info("Bid+/Ask- order modified: orderId=%s, new_price=%.2f, adjustment_count=%d", 
                           order_id, new_price, adjustment_count)
            except Exception as e:
                logging.error("Error modifying Bid+/Ask- order %s: %s", order_id, e)
                logging.error(traceback.format_exc())
                break
    
    if adjustment_count >= max_adjustments:
        logging.warning("Bid+/Ask- monitor: Reached max adjustments (%d) for order %s", max_adjustments, order_id)


async def placeOptionOrderWithSpecialType(connection, option_contract, action, quantity, price, order_type, tif):
    """
    Place an option order with special order types: Market, Bid+, Ask-
    
    Bid+: Starts at bid price, increases by $0.05 every 2 seconds if no fill
    Ask-: Starts at ask price, decreases by $0.05 every 2 seconds if no fill
    
    Args:
        connection: IB connection object
        option_contract: Option contract object
        action: 'BUY' or 'SELL'
        quantity: Number of contracts
        price: Target price (ignored for Bid+/Ask-, will use current bid/ask)
        order_type: 'Market', 'Bid+', or 'Ask-'
        tif: Time in Force ('DAY', 'GTC', etc.)
    
    Returns:
        Trade object or None if error
    """
    try:
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.tif = tif
        initial_price = None
        
        if order_type == 'Market':
            order.orderType = 'MKT'
        elif order_type == 'Bid+':
            # Start with bid price, will increase by $0.05 if no fill
            order.orderType = 'LMT'
            # Get current bid price
            ticker = connection.ib.reqMktData(option_contract, '', False, False)
            connection.ib.sleep(1)  # Wait for market data
            if ticker and ticker.bid and ticker.bid > 0:
                initial_price = round(ticker.bid, 2)
                order.lmtPrice = initial_price
            else:
                # Fallback to provided price or mid price
                if price:
                    initial_price = round(float(price), 2)
                else:
                    # Try to get mid price
                    if ticker and ticker.ask and ticker.ask > 0:
                        initial_price = round((ticker.bid + ticker.ask) / 2.0, 2) if ticker.bid else round(ticker.ask, 2)
                    else:
                        initial_price = 0.01  # Minimum fallback
                order.lmtPrice = initial_price
            connection.ib.cancelMktData(option_contract)
            logging.info("Bid+ order: Starting at bid price %.2f, will increase by 0.05 every 2s if no fill", initial_price)
        elif order_type == 'Ask-':
            # Start with ask price, will decrease by $0.05 if no fill
            order.orderType = 'LMT'
            # Get current ask price
            ticker = connection.ib.reqMktData(option_contract, '', False, False)
            connection.ib.sleep(1)  # Wait for market data
            if ticker and ticker.ask and ticker.ask > 0:
                initial_price = round(ticker.ask, 2)
                order.lmtPrice = initial_price
            else:
                # Fallback to provided price or mid price
                if price:
                    initial_price = round(float(price), 2)
                else:
                    # Try to get mid price
                    if ticker and ticker.bid and ticker.bid > 0:
                        initial_price = round((ticker.bid + ticker.ask) / 2.0, 2) if ticker.ask else round(ticker.bid, 2)
                    else:
                        initial_price = 0.01  # Minimum fallback
                order.lmtPrice = initial_price
            connection.ib.cancelMktData(option_contract)
            logging.info("Ask- order: Starting at ask price %.2f, will decrease by 0.05 every 2s if no fill", initial_price)
        else:
            logging.error("Unknown option order type: %s", order_type)
            return None

        # Let connection.placeTrade handle orderId assignment and duplicate-id retry logic
        if hasattr(connection, "placeTrade"):
            trade = connection.placeTrade(option_contract, order, outsideRth=False)
        else:
            # Fallback: direct placeOrder with a fresh orderId (older connections)
            try:
                order.orderId = connection.get_next_order_id()
            except Exception:
                pass
            trade = connection.ib.placeOrder(option_contract, order)
        
        logging.info(
            "Option order placed: %s %s %s contracts @ %s (order_type=%s, orderId=%s)",
            action,
            quantity,
            option_contract.symbol,
            order.lmtPrice if order.orderType == "LMT" else "MKT",
            order_type,
            getattr(order, "orderId", getattr(getattr(trade, "order", None), "orderId", None)),
        )
        
        # Start monitoring and adjustment for Bid+/Ask- orders
        if order_type in ('Bid+', 'Ask-') and initial_price:
            asyncio.ensure_future(
                monitorAndAdjustBidAskOrder(connection, trade, order_type, initial_price, action)
            )
        
        return trade
    except Exception as e:
        logging.error("Error placing option order: %s", e)
        logging.error(traceback.format_exc())
        return None


async def placeOptionTrade(connection, symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price, 
                          entry_order_type, sl_order_type, tp_order_type, quantity, tif, buy_sell_type, risk_amount=None):
    """
    Place option orders as conditional orders based on stock prices.
    All orders are conditional: they trigger when stock price reaches certain levels.
    
    Entry: If stock crosses entry_price → buy X option contracts (using entry_order_type: Market/Bid+/Ask-)
    Stop Loss: If stock breaks below stop_loss_price → sell X option contracts (using sl_order_type: Market/Bid+/Ask-)
    Take Profit: If stock crosses profit_price → sell X option contracts (using tp_order_type: Market/Bid+/Ask-)
    
    Args:
        connection: IB connection object
        symbol: Stock symbol
        option_contract_str: Strike price (string)
        option_expire: Expiration date (YYYYMMDD)
        entry_price: Stock entry price (condition: stock crosses this price)
        stop_loss_price: Stock stop loss price (condition: stock breaks below this price)
        profit_price: Stock profit price (condition: stock crosses this price)
        entry_order_type: 'Market', 'Bid+', or 'Ask-' for entry order
        sl_order_type: 'Market', 'Bid+', or 'Ask-' for stop loss order
        tp_order_type: 'Market', 'Bid+', or 'Ask-' for take profit order
        quantity: Number of option contracts
        tif: Time in Force
        buy_sell_type: 'BUY' or 'SELL' (from stock trade)
        risk_amount: Optional risk amount for calculating quantity
    
    Returns:
        List of Trade objects or None if error
    """
    try:
        # Determine option right (Call for BUY, Put for SELL)
        option_right = 'C' if buy_sell_type == 'BUY' else 'P'
        
        # Create option contract
        option_contract = getOptionContract(connection, symbol, option_contract_str, option_expire, option_right)
        if not option_contract:
            logging.error("Failed to create option contract for %s", symbol)
            return None

        # Create stock contract to monitor for conditions
        stock_contract = Stock(symbol, 'SMART', 'USD')
        qualified_stock = connection.ib.qualifyContracts(stock_contract)
        if not qualified_stock:
            logging.error("Failed to qualify stock contract for %s", symbol)
            return None
        stock_contract = qualified_stock[0]
        # Ensure we have exchange information for PriceCondition
        stock_exchange = getattr(stock_contract, 'exchange', None) or getattr(stock_contract, 'primaryExchange', None) or 'SMART'
        logging.info("Stock contract for condition: symbol=%s, conId=%s, exchange=%s", symbol, stock_contract.conId, stock_exchange)

        # Get current option price for risk calculation
        ticker = connection.ib.reqMktData(option_contract, '', False, False)
        connection.ib.sleep(1)
        opt_price = None
        if ticker:
            bid = getattr(ticker, "bid", None)
            ask = getattr(ticker, "ask", None)
            last = getattr(ticker, "last", None)
            close = getattr(ticker, "close", None)
            if bid is not None and ask is not None and bid > 0 and ask > 0:
                opt_price = (bid + ask) / 2.0
            elif last is not None and last > 0:
                opt_price = last
            elif close is not None and close > 0:
                opt_price = close
        
        # If a risk amount is provided, compute number of contracts from option price
        if risk_amount is not None and risk_amount != "":
            try:
                risk_val = float(risk_amount)
                if risk_val > 0:
                    if opt_price is None or opt_price <= 0:
                        logging.warning("Option risk calc: could not get valid option price, falling back to 1.0 for %s", symbol)
                        opt_price = 1.0
                    notional_per_contract = opt_price * 100.0
                    contracts = max(1, math.ceil(risk_val / notional_per_contract))
                    quantity = contracts
                    logging.info(
                        "Option risk calc for %s: risk=$%s, opt_price=%s, contracts=%s",
                        symbol,
                        risk_val,
                        opt_price,
                        contracts,
                    )
            except Exception as e:
                logging.error("Error computing option quantity from risk amount %s: %s", risk_amount, e)
                logging.error(traceback.format_exc())

        # Cancel market data subscription
        if opt_price:
            connection.ib.cancelMktData(option_contract)

        trades = []
        
        # Get option bid/ask prices once for Bid+/Ask- orders
        opt_bid = None
        opt_ask = None
        if entry_order_type in ('Bid+', 'Ask-') or sl_order_type in ('Bid+', 'Ask-') or tp_order_type in ('Bid+', 'Ask-'):
            ticker = connection.ib.reqMktData(option_contract, '', False, False)
            connection.ib.sleep(1)
            if ticker:
                opt_bid = getattr(ticker, "bid", None)
                opt_ask = getattr(ticker, "ask", None)
            connection.ib.cancelMktData(option_contract)
        
        # Helper function to create option order with condition
        def createConditionalOptionOrder(action, order_type, condition_price, trigger_method, ord_type_name):
            """Create a conditional option order based on stock price"""
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
                    order.lmtPrice = 0.01  # Minimum fallback
                logging.info("Bid+ order: Starting at bid price %s", order.lmtPrice)
            elif order_type == 'Ask-':
                order.orderType = 'LMT'
                if opt_ask and opt_ask > 0:
                    order.lmtPrice = round(opt_ask, 2)
                elif opt_price:
                    order.lmtPrice = round(opt_price, 2)
                else:
                    order.lmtPrice = 0.01  # Minimum fallback
                logging.info("Ask- order: Starting at ask price %s", order.lmtPrice)
            else:
                logging.error("Unknown option order type: %s", order_type)
                return None
            
            # Create condition: Monitor stock price
            # IB requires exchange to be specified for PriceCondition to work properly
            # Use the stock_exchange we determined earlier
            condition = PriceCondition(
                price=condition_price,
                triggerMethod=trigger_method,  # 1=More (crosses above), 2=Less (crosses below)
                conId=stock_contract.conId,
                exch=stock_exchange
            )
            logging.info("Created PriceCondition: price=%.2f, triggerMethod=%d, conId=%s, exch=%s", 
                       condition_price, trigger_method, stock_contract.conId, stock_exchange)
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
                    # Note: For conditional orders, monitoring starts when condition is met and order becomes active
                    if order_type in ('Bid+', 'Ask-') and initial_limit_price:
                        asyncio.ensure_future(
                            monitorAndAdjustBidAskOrder(connection, trade, order_type, initial_limit_price, action)
                        )
                return trade
            except Exception as e:
                logging.error("Error placing conditional option order: %s", e)
                logging.error(traceback.format_exc())
                return None
        
        # IMPORTANT: IB doesn't allow having both BUY and SELL orders open for the same option contract.
        # Solution: Only place the entry order first. After it fills, we'll place stop loss and take profit orders.
        # This avoids Error 201: "Cannot have open orders on both sides of the same US Option contract"
        
        # 1. ENTRY CONDITIONAL ORDER: If stock crosses entry_price, buy X option contracts
        entry_trade = createConditionalOptionOrder(
            buy_sell_type,
            entry_order_type,
            entry_price,
            1 if buy_sell_type == 'BUY' else 2,  # 1=More (crosses above for BUY), 2=Less (crosses below for SELL)
            'OptionEntry'
        )
        if entry_trade:
            trades.append(entry_trade)
            # Store stop loss and take profit parameters in orderStatusData so we can place them after entry fills
            entry_order_id = entry_trade.order.orderId
            if entry_order_id not in Config.orderStatusData:
                Config.orderStatusData[entry_order_id] = {}
            Config.orderStatusData[entry_order_id].update({
                'option_sl_params': {
                    'action': 'SELL' if buy_sell_type == 'BUY' else 'BUY',
                    'order_type': sl_order_type,
                    'condition_price': stop_loss_price,
                    'trigger_method': 2,  # 2=Less (breaks below)
                    'ord_type_name': 'OptionStopLoss'
                },
                'option_tp_params': {
                    'action': 'SELL' if buy_sell_type == 'BUY' else 'BUY',
                    'order_type': tp_order_type,
                    'condition_price': profit_price,
                    'trigger_method': 1 if buy_sell_type == 'BUY' else 2,  # 1=More (crosses above for BUY), 2=Less (crosses below for SELL)
                    'ord_type_name': 'OptionProfit'
                },
                'option_contract': option_contract,
                'option_quantity': quantity,
                'option_tif': tif,
                'option_symbol': symbol,
                'stock_exchange': stock_exchange,
                'stock_contract': stock_contract,
                'opt_bid': opt_bid,
                'opt_ask': opt_ask,
                'opt_price': opt_price
            })
            logging.info("Option entry order placed. Stop loss and take profit orders will be placed after entry fills. orderId=%s", entry_order_id)
        
        # Note: Stop loss and take profit orders will be placed in handleOptionEntryFill when entry order fills
        return trades
    except Exception as e:
        logging.error("Error placing option conditional orders: %s", e)
        logging.error(traceback.format_exc())
        return None




def handleOptionTrading(connection, entryData):
    """
    Handle option trading after stock entry fills and TP/SL orders are placed.
    This function is called from sendTpSlBuy/sendTpSlSell when stock entry fills.
    
    Args:
        connection: IB connection object
        entryData: Entry order data dictionary containing option_params if enabled
    """
    try:
        option_params = entryData.get('option_params')
        if not option_params or not option_params.get('enabled'):
            return
        
        symbol = entryData.get('usersymbol')
        order_id = entryData.get('orderId')
        
        logging.info("Option trading enabled for %s (orderId=%s), placing option orders", symbol, order_id)
        
        # Get calculated prices - try to extract from entryData or orderStatusData
        entry_price = entryData.get('filledPrice') or entryData.get('lastPrice', 0)
        stop_loss_price = None
        profit_price = None
        
        # Try to get TP/SL prices from orderStatusData
        if order_id and order_id in Config.orderStatusData:
            # Look for TP/SL orders in orderStatusData
            for oid, odata in Config.orderStatusData.items():
                entry_data_ref = odata.get('entryData', {})
                if isinstance(entry_data_ref, dict) and entry_data_ref.get('orderId') == order_id:
                    if odata.get('ordType') == 'TakeProfit':
                        profit_price = odata.get('lmtPrice') or odata.get('lastPrice')
                    elif odata.get('ordType') == 'StopLoss':
                        stop_loss_price = odata.get('auxPrice') or odata.get('lastPrice')
        
        # If still not found, try to get from entryData if stored there
        if not stop_loss_price:
            stop_loss_price = entryData.get('stop_loss_price')
        if not profit_price:
            profit_price = entryData.get('profit_price')
        
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
                    entryData.get('totalQuantity', 1),
                    entryData.get('tif', 'DAY'),
                    entryData.get('action', 'BUY'),
                    option_params.get('risk_amount'),
                    order_id,  # Store option order IDs linked to stock entry order
                )
            )
            logging.info("Option orders scheduled for %s: entry=%s, sl=%s, tp=%s", 
                        symbol, entry_price, stop_loss_price, profit_price)
        else:
            logging.warning("Option trading: Cannot place option orders - missing prices: entry=%s, sl=%s, tp=%s",
                           entry_price, stop_loss_price, profit_price)
    except Exception as e:
        logging.error("Error in handleOptionTrading: %s", e)
        logging.error(traceback.format_exc())


async def placeOptionTradeAndStore(connection, symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price, 
                          entry_order_type, sl_order_type, tp_order_type, quantity, tif, buy_sell_type, risk_amount=None, stock_entry_order_id=None):
    """
    Place option orders and store their IDs in orderStatusData linked to stock entry order.
    This allows tracking and updating option orders when stock orders update (e.g., RBB).
    """
    try:
        trades = await placeOptionTrade(
            connection, symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price,
            entry_order_type, sl_order_type, tp_order_type, quantity, tif, buy_sell_type, risk_amount
        )
        
        if trades and stock_entry_order_id:
            # Store option order IDs in orderStatusData for the stock entry order
            if stock_entry_order_id in Config.orderStatusData:
                stock_data = Config.orderStatusData[stock_entry_order_id]
                if 'option_orders' not in stock_data:
                    stock_data['option_orders'] = {}
                
                # Store option order IDs by type
                for trade in trades:
                    if trade and trade.order:
                        order_id = trade.order.orderId
                        # Determine order type from action and price
                        if trade.order.action == buy_sell_type:
                            stock_data['option_orders']['entry'] = order_id
                        elif trade.order.action != buy_sell_type:
                            # Check if it's stop loss or profit based on price
                            if abs(trade.order.auxPrice - stop_loss_price) < 0.01 or (hasattr(trade.order, 'lmtPrice') and abs(trade.order.lmtPrice - stop_loss_price) < 0.01):
                                stock_data['option_orders']['stop_loss'] = order_id
                            elif abs(trade.order.lmtPrice - profit_price) < 0.01:
                                stock_data['option_orders']['profit'] = order_id
                
                logging.info("Stored option order IDs for stock entry %s: %s", stock_entry_order_id, stock_data.get('option_orders', {}))
    except Exception as e:
        logging.error("Error in placeOptionTradeAndStore: %s", e)
        logging.error(traceback.format_exc())


def handleOptionTradingForEntryFill(connection, stock_entry_order_id, entry_data):
    """
    Handle option trading when stock entry order fills.
    This is called for RB/RBB trades that use bracket orders (sendTpAndSl is not called).
    
    Args:
        connection: IB connection object
        stock_entry_order_id: Stock entry order ID that just filled
        entry_data: Entry order data from orderStatusData
    """
    try:
        symbol = entry_data.get('usersymbol')
        timeFrame = entry_data.get('timeFrame')
        barType = entry_data.get('barType')
        buySellType = entry_data.get('userBuySell') or entry_data.get('action')
        
        # First check if option_params is already in entry_data (from sendTpAndSl)
        option_params = entry_data.get('option_params')
        
        # If not found, retrieve from Config.option_trade_params
        if not option_params or not option_params.get('enabled'):
            if hasattr(Config, 'option_trade_params') and Config.option_trade_params:
                # Find matching option params by trade key
                matching_key = None
                matching_params = None
                latest_ts = None
                logging.info("Option trading: Searching for option params. Looking for: symbol=%s, timeFrame=%s, barType=%s, buySellType=%s. Available keys: %s", 
                            symbol, timeFrame, barType, buySellType, list(Config.option_trade_params.keys()))
                for trade_key, params in list(Config.option_trade_params.items()):
                    # trade_key: (symbol, timeFrame, barType, buySellType, timestamp)
                    if len(trade_key) >= 5:
                        key_symbol, key_tf, key_bar, key_side, ts = trade_key
                        logging.info("Option trading: Comparing - stored: (%s, %s, %s, %s) vs looking for: (%s, %s, %s, %s)", 
                                    key_symbol, key_tf, key_bar, key_side, symbol, timeFrame, barType, buySellType)
                        if (
                            key_symbol == symbol
                            and key_tf == timeFrame
                            and key_bar == barType
                            and key_side == buySellType
                        ):
                            if latest_ts is None or ts > latest_ts:
                                latest_ts = ts
                                matching_key = trade_key
                                matching_params = params
                
                if matching_key and matching_params and matching_params.get('enabled'):
                    option_params = matching_params
                    # Remove from pending params
                    del Config.option_trade_params[matching_key]
                    logging.info("Retrieved option params from Config.option_trade_params for %s", symbol)
                else:
                    logging.warning("Option trading: No matching option params found. matching_key=%s, matching_params=%s", 
                                  matching_key, matching_params)
        
        if not option_params or not option_params.get('enabled'):
            logging.info("Option trading: Option params not found or not enabled for %s (orderId=%s). option_params=%s", 
                        symbol, stock_entry_order_id, option_params)
            return
        
        logging.info("Option trading: Stock entry filled for %s (orderId=%s), placing option orders. option_params=%s", 
                    symbol, stock_entry_order_id, option_params)
        
        # Get prices from entry_data or orderStatusData
        # Use filledPrice if available (order filled), otherwise use lastPrice (trigger price when order placed)
        entry_price = entry_data.get('filledPrice') or entry_data.get('lastPrice', 0)
        # If still no price, try to get from the order's auxPrice (trigger price) stored in orderStatusData
        if not entry_price or entry_price == 0:
            # Get the actual order from IB to find the trigger price
            try:
                # Try to get from stored order data
                if 'auxPrice' in entry_data:
                    entry_price = entry_data.get('auxPrice')
                elif hasattr(connection, 'ib') and connection.ib:
                    # Get all open orders and find this one
                    open_trades = connection.ib.openTrades()
                    for trade in open_trades:
                        if int(trade.order.orderId) == int(stock_entry_order_id):
                            if hasattr(trade.order, 'auxPrice') and trade.order.auxPrice:
                                entry_price = trade.order.auxPrice
                                logging.info("Retrieved entry price from order auxPrice: %s", entry_price)
                                break
            except Exception as e:
                logging.warning("Could not retrieve entry price from order: %s", e)
        
        stop_loss_price = entry_data.get('stop_loss_price')
        profit_price = entry_data.get('tp_price') or entry_data.get('profit_price')

        # Additional fallbacks for strategies like PBe1 that store fields differently
        # PBe1 stores stop loss as 'stopLossPrice' and stop size as 'stopSize' in orderStatusData
        if not stop_loss_price:
            stop_loss_price = entry_data.get('stopLossPrice')
        
        # If profit price is still missing, try to derive it from stop size and profit setting
        if not profit_price:
            try:
                # Only attempt this for strategies that behave like PBe1/RBB
                if barType in ('PBe1', 'RBB', 'RB'):
                    stop_size = entry_data.get('stopSize')
                    if (stop_size is None or stop_size == 0) and stop_loss_price and entry_price:
                        stop_size = abs(float(entry_price) - float(stop_loss_price))
                    
                    profit_setting = entry_data.get('profit')
                    if stop_size and profit_setting:
                        multiplier_map = {
                            Config.takeProfit[0]: 1,    # 1:1
                            Config.takeProfit[1]: 1.5,  # 1.5:1
                            Config.takeProfit[2]: 2,    # 2:1
                            Config.takeProfit[3]: 2.5,  # 2.5:1
                        }
                        # Add 3:1 if it exists (index 4)
                        if len(Config.takeProfit) > 4:
                            multiplier_map[Config.takeProfit[4]] = 3  # 3:1
                        
                        multiplier = multiplier_map.get(profit_setting, 1)
                        tp_offset = stop_size * multiplier
                        
                        if buySellType == 'BUY':
                            profit_price = round(entry_price + tp_offset, Config.roundVal)
                        else:
                            profit_price = round(entry_price - tp_offset, Config.roundVal)
                        logging.info(
                            "Option trading: Derived profit_price=%s from entry=%s, stop_loss=%s, stop_size=%s, profit=%s for barType=%s",
                            profit_price, entry_price, stop_loss_price, stop_size, profit_setting, barType
                        )
            except Exception as e:
                logging.warning("Option trading: Failed to derive profit_price fallback: %s", e)
                logging.warning(traceback.format_exc())
        
        # If prices not in entry_data, try to get from stored TP/SL prices in entry order's orderStatusData
        if not stop_loss_price or not profit_price:
            # First, try to get from entry order's orderStatusData (stored when bracket orders are placed)
            entry_order_data = Config.orderStatusData.get(int(stock_entry_order_id), {})
            if not stop_loss_price:
                stop_loss_price = entry_order_data.get('stop_loss_price') or entry_order_data.get('stopLossPrice')
            if not profit_price:
                profit_price = entry_order_data.get('tp_price') or entry_order_data.get('profit_price')
            
            # If still not found, look for TP/SL orders in orderStatusData that are children of this entry
            if not stop_loss_price or not profit_price:
                for oid, odata in Config.orderStatusData.items():
                    parent_id = odata.get('parentId')
                    if isinstance(parent_id, (int, float)) and int(parent_id) == int(stock_entry_order_id):
                        if odata.get('ordType') == 'TakeProfit':
                            profit_price = profit_price or odata.get('lmtPrice') or odata.get('lastPrice')
                        elif odata.get('ordType') == 'StopLoss':
                            stop_loss_price = stop_loss_price or odata.get('auxPrice') or odata.get('lastPrice')
        
        # If still not found, try to get from entry_data's stored values (last fallback)
        if not stop_loss_price:
            stop_loss_price = entry_data.get('stop_loss_price') or entry_data.get('stopLossPrice')
        if not profit_price:
            profit_price = entry_data.get('tp_price') or entry_data.get('profit_price')
        
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
                    stock_entry_order_id,  # Store option order IDs linked to stock entry order
                )
            )
            logging.info("Option orders scheduled for %s (entry filled): entry=%s, sl=%s, tp=%s", 
                        symbol, entry_price, stop_loss_price, profit_price)
        else:
            logging.warning("Option trading: Cannot place option orders - missing prices: entry=%s, sl=%s, tp=%s. entry_data keys: %s",
                           entry_price, stop_loss_price, profit_price, list(entry_data.keys()) if entry_data else 'None')
    except Exception as e:
        logging.error("Error in handleOptionTradingForEntryFill: %s", e)
        logging.error(traceback.format_exc())


def handleOptionEntryFill(connection, option_entry_order_id):
    """
    Handle option entry order fill: Place stop loss and take profit orders.
    This is called when an option entry order fills, to avoid Error 201 (cannot have both BUY and SELL orders open).
    """
    try:
        option_data = Config.orderStatusData.get(option_entry_order_id)
        if not option_data:
            logging.warning("Option entry order %s not found in orderStatusData", option_entry_order_id)
            return
        
        sl_params = option_data.get('option_sl_params')
        tp_params = option_data.get('option_tp_params')
        
        if not sl_params or not tp_params:
            logging.warning("Option stop loss or take profit params not found for option entry order %s", option_entry_order_id)
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
        
        logging.info("Option entry order %s filled. Placing stop loss and take profit orders.", option_entry_order_id)
        
        # Get current stock price to verify conditions are not already met
        # This prevents immediate execution if the condition is already satisfied
        try:
            stock_ticker = connection.ib.reqMktData(stock_contract, '', False, False)
            connection.ib.sleep(0.5)  # Wait for market data
            current_stock_price = None
            if stock_ticker:
                last = getattr(stock_ticker, "last", None)
                close = getattr(stock_ticker, "close", None)
                if last and last > 0:
                    current_stock_price = last
                elif close and close > 0:
                    current_stock_price = close
            connection.ib.cancelMktData(stock_contract)
            
            if current_stock_price:
                logging.info("Current stock price for %s: %.2f (stop_loss=%.2f, profit=%.2f)", 
                           symbol, current_stock_price, sl_params['condition_price'], tp_params['condition_price'])
        except Exception as e:
            logging.warning("Could not get current stock price: %s", e)
            current_stock_price = None
        
        # Helper function to create conditional option order (reuse from placeOptionTrade)
        def createConditionalOptionOrder(action, order_type, condition_price, trigger_method, ord_type_name):
            """Create a conditional option order based on stock price"""
            # IMPORTANT: Check if condition is already met - if so, don't place order yet
            # IB conditional orders execute immediately if the condition is already met when placed
            # For stop loss (trigger_method=2, breaks below): Only place if current_price > condition_price
            # For take profit (trigger_method=1, crosses above): Only place if current_price < condition_price
            if current_stock_price is not None:
                if trigger_method == 2:  # Stop loss - breaks below (condition: stock breaks below price)
                    if current_stock_price <= condition_price:
                        logging.warning("Option %s: Current stock price (%.2f) is already at/below stop loss (%.2f). Conditional order would execute immediately. Storing order for monitoring - will place when price recovers above stop loss.", 
                                      ord_type_name, current_stock_price, condition_price)
                        # Store order params to place later when condition is no longer met
                        pending_key = f"{option_entry_order_id}_{ord_type_name}"
                        Config.pending_option_orders[pending_key] = {
                            'action': action,
                            'order_type': order_type,
                            'condition_price': condition_price,
                            'trigger_method': trigger_method,
                            'ord_type_name': ord_type_name,
                            'option_contract': option_contract,
                            'quantity': quantity,
                            'tif': tif,
                            'symbol': symbol,
                            'stock_contract': stock_contract,
                            'stock_exchange': stock_exchange,
                            'opt_bid': opt_bid,
                            'opt_ask': opt_ask,
                            'opt_price': opt_price,
                            'entry_order_id': option_entry_order_id
                        }
                        # Start monitoring task
                        asyncio.ensure_future(
                            monitorAndPlacePendingOptionOrder(connection, pending_key, condition_price, trigger_method, ord_type_name)
                        )
                        logging.info("Option %s: Monitoring started - will place order when stock price recovers above %.2f", ord_type_name, condition_price)
                        return None
                elif trigger_method == 1:  # Take profit - crosses above (condition: stock crosses above price)
                    if current_stock_price >= condition_price:
                        logging.warning("Option %s: Current stock price (%.2f) is already at/above take profit (%.2f). Conditional order would execute immediately. Storing order for monitoring - will place when price drops below take profit.", 
                                      ord_type_name, current_stock_price, condition_price)
                        # Store order params to place later when condition is no longer met
                        pending_key = f"{option_entry_order_id}_{ord_type_name}"
                        Config.pending_option_orders[pending_key] = {
                            'action': action,
                            'order_type': order_type,
                            'condition_price': condition_price,
                            'trigger_method': trigger_method,
                            'ord_type_name': ord_type_name,
                            'option_contract': option_contract,
                            'quantity': quantity,
                            'tif': tif,
                            'symbol': symbol,
                            'stock_contract': stock_contract,
                            'stock_exchange': stock_exchange,
                            'opt_bid': opt_bid,
                            'opt_ask': opt_ask,
                            'opt_price': opt_price,
                            'entry_order_id': option_entry_order_id
                        }
                        # Start monitoring task
                        asyncio.ensure_future(
                            monitorAndPlacePendingOptionOrder(connection, pending_key, condition_price, trigger_method, ord_type_name)
                        )
                        logging.info("Option %s: Monitoring started - will place order when stock price drops below %.2f", ord_type_name, condition_price)
                        return None
            
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
                    order.lmtPrice = 0.01  # Minimum fallback
                logging.info("Bid+ order: Starting at bid price %s", order.lmtPrice)
            elif order_type == 'Ask-':
                order.orderType = 'LMT'
                if opt_ask and opt_ask > 0:
                    order.lmtPrice = round(opt_ask, 2)
                elif opt_price:
                    order.lmtPrice = round(opt_price, 2)
                else:
                    order.lmtPrice = 0.01  # Minimum fallback
                logging.info("Ask- order: Starting at ask price %s", order.lmtPrice)
            else:
                logging.error("Unknown option order type: %s", order_type)
                return None
            
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
                return trade
            except Exception as e:
                logging.error("Error placing conditional option order: %s", e)
                logging.error(traceback.format_exc())
                return None
        
        # Place stop loss order
        sl_trade = createConditionalOptionOrder(
            sl_params['action'],
            sl_params['order_type'],
            sl_params['condition_price'],
            sl_params['trigger_method'],
            sl_params['ord_type_name']
        )
        
        # Place take profit order
        tp_trade = createConditionalOptionOrder(
            tp_params['action'],
            tp_params['order_type'],
            tp_params['condition_price'],
            tp_params['trigger_method'],
            tp_params['ord_type_name']
        )
        
        if sl_trade and tp_trade:
            sl_order_id = sl_trade.order.orderId
            tp_order_id = tp_trade.order.orderId
            
            # Store order IDs in each other's orderStatusData for bracket order cancellation
            # When one fills, we'll cancel the other
            if sl_order_id not in Config.orderStatusData:
                Config.orderStatusData[sl_order_id] = {}
            Config.orderStatusData[sl_order_id]['bracket_pair_order_id'] = tp_order_id
            Config.orderStatusData[sl_order_id]['is_bracket_order'] = True
            
            if tp_order_id not in Config.orderStatusData:
                Config.orderStatusData[tp_order_id] = {}
            Config.orderStatusData[tp_order_id]['bracket_pair_order_id'] = sl_order_id
            Config.orderStatusData[tp_order_id]['is_bracket_order'] = True
            
            # Also store in entry order's data for reference
            option_data['option_sl_order_id'] = sl_order_id
            option_data['option_tp_order_id'] = tp_order_id
            
            logging.info("Option stop loss and take profit orders placed after entry fill. SL orderId=%s, TP orderId=%s (bracket orders - one will cancel the other when filled)", 
                        sl_order_id, tp_order_id)
        else:
            logging.warning("Failed to place option stop loss or take profit orders after entry fill")
            
    except Exception as e:
        logging.error("Error in handleOptionEntryFill: %s", e)
        logging.error(traceback.format_exc())


def triggerOptionOrderOnStockFill(connection, stock_order_id, ord_type, bar_type):
    """
    Trigger option orders when stock orders fill.
    - If stock entry fills: Option orders are placed via handleOptionTradingForEntryFill
    - If stock stop loss or profit fills: Trigger corresponding option stop loss or profit
    """
    try:
        if ord_type not in ('StopLoss', 'TakeProfit'):
            return  # Only handle stop loss and profit fills
        
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
            # Try to find entry order by looking for orders with this TP/SL as child
            for oid, odata in Config.orderStatusData.items():
                if odata.get('ordType') == 'Entry' and odata.get('barType') == bar_type:
                    # Check if this entry has option orders
                    if 'option_orders' in odata:
                        entry_order_id = oid
                        break
        
        if not entry_order_id:
            return
        
        entry_data = Config.orderStatusData.get(entry_order_id)
        if not entry_data or 'option_orders' not in entry_data:
            return
        
        option_orders = entry_data.get('option_orders', {})
        
        # Trigger corresponding option order
        if ord_type == 'StopLoss' and 'stop_loss' in option_orders:
            option_sl_id = option_orders['stop_loss']
            logging.info("Stock stop loss filled (orderId=%s), triggering option stop loss (orderId=%s)", 
                        stock_order_id, option_sl_id)
            # Option stop loss should already be placed, but we can verify it's active
            # If needed, we can modify the order or ensure it's triggered
        elif ord_type == 'TakeProfit' and 'profit' in option_orders:
            option_tp_id = option_orders['profit']
            logging.info("Stock profit filled (orderId=%s), triggering option profit (orderId=%s)", 
                        stock_order_id, option_tp_id)
            # Option profit should already be placed, but we can verify it's active
    except Exception as e:
        logging.error("Error in triggerOptionOrderOnStockFill: %s", e)
        logging.error(traceback.format_exc())


def handleOptionTpSlFill(connection, option_order_id, ord_type):
    """
    Handle option TP/SL fill: Cancel the other bracket order (bracket order behavior).
    When option take profit OR stop loss fills, cancel the other order.
    
    Args:
        connection: IB connection object
        option_order_id: Order ID of the option TP or SL that just filled
        ord_type: 'OptionStopLoss' or 'OptionProfit'
    """
    try:
        option_data = Config.orderStatusData.get(option_order_id)
        if not option_data:
            return
        
        # Get the bracket pair order ID (the other order to cancel)
        pair_order_id = option_data.get('bracket_pair_order_id')
        if not pair_order_id:
            logging.warning("Option bracket pair order ID not found for order %s", option_order_id)
            return
        
        # Check if pair order still exists and is active
        pair_data = Config.orderStatusData.get(pair_order_id)
        if not pair_data:
            logging.warning("Option bracket pair order %s not found in orderStatusData", pair_order_id)
            return
        
        pair_status = pair_data.get('status', '')
        if pair_status in ('Filled', 'Cancelled', 'Inactive'):
            logging.info("Option bracket pair order %s already %s, no need to cancel", pair_order_id, pair_status)
            return
        
        # Cancel the pair order
        try:
            # Get the trade object from IB
            trades = connection.ib.trades()
            pair_trade = None
            for trade in trades:
                if int(trade.order.orderId) == int(pair_order_id):
                    pair_trade = trade
                    break
            
            if pair_trade:
                connection.ib.cancelOrder(pair_trade.order)
                logging.info("Option bracket order: %s filled (orderId=%s), cancelled pair order (orderId=%s)", 
                           ord_type, option_order_id, pair_order_id)
            else:
                logging.warning("Option bracket pair order %s not found in IB trades, cannot cancel", pair_order_id)
        except Exception as e:
            logging.error("Error cancelling option bracket pair order %s: %s", pair_order_id, e)
            logging.error(traceback.format_exc())
    except Exception as e:
        logging.error("Error in handleOptionTpSlFill: %s", e)
        logging.error(traceback.format_exc())


def updateOptionOrdersForRBB(connection, stock_entry_order_id, new_entry_price, new_stop_loss_price=None):
    """
    Update option orders when RBB stock entry order updates.
    This is called from rbb_loop_run when the stock entry order is updated.
    
    For RBB, option entry order should update to match the new stock entry price.
    Option stop loss should also update if new_stop_loss_price is provided.
    """
    try:
        stock_data = Config.orderStatusData.get(stock_entry_order_id)
        if not stock_data or 'option_orders' not in stock_data:
            return
        
        option_orders = stock_data.get('option_orders', {})
        if 'entry' not in option_orders:
            return
        
        option_entry_id = option_orders.get('entry')
        
        # Get option entry order data
        if option_entry_id not in Config.orderStatusData:
            logging.warning("Option entry order %s not found in orderStatusData", option_entry_id)
            return
        
        option_data = Config.orderStatusData[option_entry_id]
        option_contract = option_data.get('contract')
        if not option_contract:
            logging.warning("Option contract not found for option order %s", option_entry_id)
            return
        
        # Update option entry order price to match new stock entry price
        logging.info("RBB: Updating option entry order %s to match stock entry price %s", option_entry_id, new_entry_price)
        
        # Get current option entry order from IB
        option_trades = connection.ib.trades()
        option_trade = option_trades.get(option_entry_id)
        
        if not option_trade:
            logging.warning("Option entry order %s not found in IB trades", option_entry_id)
            return
        
        # Cancel old option entry order
        try:
            connection.ib.cancelOrder(option_trade.order)
            logging.info("RBB: Cancelled old option entry order %s", option_entry_id)
        except Exception as e:
            logging.warning("RBB: Error cancelling option entry order %s: %s", option_entry_id, e)
        
        # Get order details from option_data
        action = option_data.get('action', 'BUY')
        quantity = option_data.get('totalQuantity', 1)
        tif = option_data.get('tif', 'DAY')
        entry_order_type = option_data.get('entry_order_type', 'Market')
        
        # Create new order with updated price
        new_order = Order()
        new_order.action = action
        new_order.totalQuantity = quantity
        new_order.tif = tif
        
        if entry_order_type == 'Market':
            new_order.orderType = 'MKT'
        elif entry_order_type == 'Bid+':
            # Get current bid and use it as limit price
            ticker = connection.ib.reqMktData(option_contract, '', False, False)
            connection.ib.sleep(0.5)
            if ticker and ticker.bid:
                new_order.orderType = 'LMT'
                new_order.lmtPrice = round(ticker.bid, 2)
            else:
                new_order.orderType = 'MKT'
            connection.ib.cancelMktData(option_contract)
        elif entry_order_type == 'Ask-':
            # Get current ask and use it as limit price
            ticker = connection.ib.reqMktData(option_contract, '', False, False)
            connection.ib.sleep(0.5)
            if ticker and ticker.ask:
                new_order.orderType = 'LMT'
                new_order.lmtPrice = round(ticker.ask, 2)
            else:
                new_order.orderType = 'MKT'
            connection.ib.cancelMktData(option_contract)
        else:
            new_order.orderType = 'MKT'
        
        # Place updated order
        trade = connection.placeTrade(option_contract, new_order, outsideRth=False)
        if trade and trade.order:
            new_option_entry_id = trade.order.orderId
            option_orders['entry'] = new_option_entry_id
            stock_data['option_orders'] = option_orders
            
            # Update option_data in orderStatusData
            Config.orderStatusData[new_option_entry_id] = option_data.copy()
            Config.orderStatusData[new_option_entry_id]['orderId'] = new_option_entry_id
            Config.orderStatusData[new_option_entry_id]['lastPrice'] = new_entry_price
            
            logging.info("RBB: Updated option entry order: old=%s, new=%s, price=%s", 
                        option_entry_id, new_option_entry_id, new_entry_price)
        
        # Update option stop loss if new_stop_loss_price is provided
        if new_stop_loss_price and 'stop_loss' in option_orders:
            option_sl_id = option_orders.get('stop_loss')
            if option_sl_id and option_sl_id in Config.orderStatusData:
                logging.info("RBB: Option stop loss order %s will be updated when stock stop loss updates", option_sl_id)
                # Stop loss update can be handled separately when stock stop loss order updates
    except Exception as e:
        logging.error("Error in updateOptionOrdersForRBB: %s", e)
        logging.error(traceback.format_exc())
