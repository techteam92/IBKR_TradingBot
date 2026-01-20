"""
Option Trading Module
Handles all option trading functionality without modifying core SendTrade.py logic
"""
import asyncio
import datetime
import logging
import traceback
from ib_insync import Option, Order
import Config


def getOptionContract(connection, symbol, strike, expiration_date, right='C'):
    """
    Create an Option contract for the given symbol, strike price, and expiration date.
    
    Args:
        connection: IB connection object
        symbol: Stock symbol (e.g., 'AAPL')
        strike: Strike price (float or string)
        expiration_date: Expiration date in YYYYMMDD format (e.g., '20260119')
        right: Option right - 'C' for Call, 'P' for Put (default: 'C')
    
    Returns:
        Option contract object or None if error
    """
    try:
        strike_float = float(strike)
        
        # Parse expiration date
        if len(expiration_date) == 8 and expiration_date.isdigit():
            year = int(expiration_date[0:4])
            month = int(expiration_date[4:6])
            day = int(expiration_date[6:8])
            expiration = datetime.date(year, month, day)
        else:
            logging.error("Invalid expiration date format: %s (expected YYYYMMDD)", expiration_date)
            return None
        
        # Create option contract
        option = Option(symbol, expiration, strike_float, right, exchange='SMART')
        
        # Qualify the contract to get full details
        qualified = connection.ib.qualifyContracts(option)
        if qualified:
            option = qualified[0]
            logging.info("Option contract created: %s %s %s %s @ %s", symbol, expiration, right, strike_float, option.exchange)
            return option
        else:
            logging.error("Failed to qualify option contract: %s %s %s %s", symbol, expiration, right, strike_float)
            return None
    except Exception as e:
        logging.error("Error creating option contract for %s: %s", symbol, e)
        logging.error(traceback.format_exc())
        return None


async def placeOptionOrderWithSpecialType(connection, option_contract, action, quantity, price, order_type, tif):
    """
    Place an option order with special order types: Market, Bid+, Ask-
    
    Args:
        connection: IB connection object
        option_contract: Option contract object
        action: 'BUY' or 'SELL'
        quantity: Number of contracts
        price: Target price (for Bid+/Ask- orders, this is the starting price)
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
        
        if order_type == 'Market':
            order.orderType = 'MKT'
        elif order_type == 'Bid+':
            # Start with bid price, increase by 5 cents until fill
            # Use a limit order starting at bid, then adjust upward
            order.orderType = 'LMT'
            # Get current bid price
            ticker = connection.ib.reqMktData(option_contract, '', False, False)
            connection.ib.sleep(1)  # Wait for market data
            if ticker and ticker.bid:
                order.lmtPrice = round(ticker.bid, 2)
            else:
                # Fallback to provided price
                order.lmtPrice = round(float(price), 2)
            # Note: Bid+ logic would need to be implemented with order modification
            # For now, we'll use a limit order at bid price
            logging.info("Bid+ order: Starting at bid price %s, will increase by 0.05 until fill", order.lmtPrice)
        elif order_type == 'Ask-':
            # Start with ask price, decrease by 5 cents until fill
            order.orderType = 'LMT'
            # Get current ask price
            ticker = connection.ib.reqMktData(option_contract, '', False, False)
            connection.ib.sleep(1)  # Wait for market data
            if ticker and ticker.ask:
                order.lmtPrice = round(ticker.ask, 2)
            else:
                # Fallback to provided price
                order.lmtPrice = round(float(price), 2)
            # Note: Ask- logic would need to be implemented with order modification
            # For now, we'll use a limit order at ask price
            logging.info("Ask- order: Starting at ask price %s, will decrease by 0.05 until fill", order.lmtPrice)
        else:
            logging.error("Unknown option order type: %s", order_type)
            return None
        
        order.orderId = connection.get_next_order_id()
        
        # Place the order
        trade = connection.ib.placeOrder(option_contract, order)
        logging.info("Option order placed: %s %s %s contracts @ %s (order_type=%s, orderId=%s)", 
                    action, quantity, option_contract.symbol, order.lmtPrice if order.orderType == 'LMT' else 'MKT', 
                    order_type, order.orderId)
        
        # Cancel market data subscription
        connection.ib.cancelMktData(option_contract)
        
        return trade
    except Exception as e:
        logging.error("Error placing option order: %s", e)
        logging.error(traceback.format_exc())
        return None


async def placeOptionTrade(connection, symbol, option_contract_str, option_expire, entry_price, stop_loss_price, profit_price, 
                          entry_order_type, sl_order_type, tp_order_type, quantity, tif, buy_sell_type):
    """
    Place option orders (entry, stop loss, profit) based on stock trade prices.
    
    Args:
        connection: IB connection object
        symbol: Stock symbol
        option_contract_str: Strike price (string)
        option_expire: Expiration date (YYYYMMDD)
        entry_price: Stock entry price (used for option entry)
        stop_loss_price: Stock stop loss price (used for option stop loss)
        profit_price: Stock profit price (used for option profit)
        entry_order_type: Order type for entry ('Market', 'Bid+', 'Ask-')
        sl_order_type: Order type for stop loss ('Market', 'Bid+', 'Ask-')
        tp_order_type: Order type for profit ('Market', 'Bid+', 'Ask-')
        quantity: Number of option contracts
        tif: Time in Force
        buy_sell_type: 'BUY' or 'SELL' (from stock trade)
    
    Returns:
        List of Trade objects or None if error
    """
    try:
        # Determine option right (Call for BUY, Put for SELL - this is a simplification)
        # In practice, the user might want to specify this, but for now we'll use this logic
        option_right = 'C' if buy_sell_type == 'BUY' else 'P'
        
        # Create option contract
        option_contract = getOptionContract(connection, symbol, option_contract_str, option_expire, option_right)
        if not option_contract:
            logging.error("Failed to create option contract for %s", symbol)
            return None
        
        trades = []
        
        # Place entry order
        entry_trade = await placeOptionOrderWithSpecialType(
            connection, option_contract, buy_sell_type, quantity, entry_price, entry_order_type, tif
        )
        if entry_trade:
            trades.append(entry_trade)
            logging.info("Option entry order placed: %s", entry_trade.order.orderId)
        
        # Place stop loss order (opposite action)
        sl_action = 'SELL' if buy_sell_type == 'BUY' else 'BUY'
        sl_trade = await placeOptionOrderWithSpecialType(
            connection, option_contract, sl_action, quantity, stop_loss_price, sl_order_type, tif
        )
        if sl_trade:
            trades.append(sl_trade)
            logging.info("Option stop loss order placed: %s", sl_trade.order.orderId)
        
        # Place profit order (opposite action)
        tp_action = 'SELL' if buy_sell_type == 'BUY' else 'BUY'
        tp_trade = await placeOptionOrderWithSpecialType(
            connection, option_contract, tp_action, quantity, profit_price, tp_order_type, tif
        )
        if tp_trade:
            trades.append(tp_trade)
            logging.info("Option profit order placed: %s", tp_trade.order.orderId)
        
        return trades
    except Exception as e:
        logging.error("Error placing option trade: %s", e)
        logging.error(traceback.format_exc())
        return None


def handleOptionTrading(connection, entryData):
    """
    Handle option trading after stock TP/SL orders are placed.
    This function is called from sendTpSlBuy/sendTpSlSell without modifying their core logic.
    
    Args:
        connection: IB connection object
        entryData: Entry order data dictionary containing option_params if enabled
    """
    try:
        option_params = entryData.get('option_params')
        if not option_params or not option_params.get('enabled'):
            return
        
        logging.info("Option trading enabled for %s, placing option orders", entryData.get('usersymbol'))
        
        # Get calculated prices - try to extract from entryData or orderStatusData
        entry_price = entryData.get('lastPrice') or entryData.get('filledPrice', 0)
        stop_loss_price = None
        profit_price = None
        
        # Try to get TP/SL prices from orderStatusData
        order_id = entryData.get('orderId')
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
            asyncio.ensure_future(placeOptionTrade(
                connection,
                entryData.get('usersymbol'),
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
                entryData.get('action', 'BUY')
            ))
            logging.info("Option orders scheduled for %s: entry=%s, sl=%s, tp=%s", 
                        entryData.get('usersymbol'), entry_price, stop_loss_price, profit_price)
        else:
            logging.warning("Option trading: Cannot place option orders - missing prices: entry=%s, sl=%s, tp=%s",
                           entry_price, stop_loss_price, profit_price)
    except Exception as e:
        logging.error("Error in handleOptionTrading: %s", e)
        logging.error(traceback.format_exc())
