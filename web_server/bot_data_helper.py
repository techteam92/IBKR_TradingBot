"""
Bot Data Helper Module
Retrieves real account data from the trading bot (IBConnection)
"""

import sys
import os
import subprocess
import platform
from pathlib import Path

# Add parent directory to path to import bot modules
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

print("=" * 50)
print("DEBUG: bot_data_helper.py - Starting imports...")
print(f"DEBUG: Parent directory: {Path(__file__).parent.parent}")

try:
    print("DEBUG: Attempting to import IBConnection...")
    from IBConnection import connection
    print("DEBUG: Successfully imported IBConnection.connection")
    
    print("DEBUG: Attempting to import Config.currentPnl and clientId...")
    import Config
    from Config import currentPnl, pullBackNo
    print(f"DEBUG: Successfully imported Config.currentPnl = {currentPnl}, clientId = {Config.clientId}")
    
    print("DEBUG: Attempting to import SendTrade...")
    from SendTrade import SendTrade
    # Import _get_current_session - it's defined in SendTrade.py
    import SendTrade as send_trade_module
    _get_current_session = send_trade_module._get_current_session
    print("DEBUG: Successfully imported SendTrade and _get_current_session")
    
    import asyncio
    import logging
    print("DEBUG: Successfully imported logging and asyncio")
    print("DEBUG: All imports successful!")
except ImportError as e:
    import logging
    print(f"ERROR: Could not import bot modules: {e}")
    print(f"ERROR: Import error type: {type(e)}")
    import traceback
    print(f"ERROR: Import traceback:\n{traceback.format_exc()}")
    logging.warning(f"Could not import bot modules: {e}")
    connection = None
    currentPnl = 0
    print("DEBUG: Set connection=None, currentPnl=0")

print("=" * 50)


# Global connection instance (singleton pattern)
_bot_connection = None


def _check_main_bot_running():
    """
    Check if the main app.py (trading bot GUI) is running
    
    Returns:
        bool: True if app.py process is found, False otherwise
    """
    try:
        if platform.system() == 'Windows':
            # On Windows, check for python processes running app.py
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                # Check if any process command line contains app.py
                # Note: tasklist doesn't show command line, so we check for python.exe processes
                # A more reliable method would be to use wmic or check process names
                # For now, we'll use a simpler check - if python.exe is running, assume app.py might be
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:  # Header + at least one process
                    # Check if TWS_Trading_GUI.exe is running (the compiled version)
                    exe_result = subprocess.run(
                        ['tasklist', '/FI', 'IMAGENAME eq TWS_Trading_GUI.exe', '/FO', 'CSV'],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if exe_result.returncode == 0 and len(exe_result.stdout.strip().split('\n')) > 1:
                        print("DEBUG: TWS_Trading_GUI.exe process found - main bot is running")
                        return True
                    
                    # Also check for python.exe processes (could be app.py)
                    # Since we can't see command line with tasklist, we'll assume if python.exe exists, app.py might be running
                    # This is a heuristic - in production you might want a more reliable method
                    print("DEBUG: Python processes found, main bot may be running")
                    return True
        else:
            # On Unix/Linux, use pgrep to find app.py processes
            result = subprocess.run(
                ['pgrep', '-f', 'app.py'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0 and result.stdout.strip():
                print("DEBUG: app.py process found - main bot is running")
                return True
        
        print("DEBUG: Main bot (app.py) process not found")
        return False
    except Exception as e:
        print(f"DEBUG: Could not check for main bot process: {e}")
        return False


def get_bot_connection():
    """
    Get or create bot connection instance.
    First checks if main app.py is running and connected.
    If main bot is running, we know it's connected (it checks on startup).
    """
    global _bot_connection
    
    print("=" * 50)
    print("DEBUG: get_bot_connection() called")
    print(f"DEBUG: _bot_connection is None: {_bot_connection is None}")
    
    # First, check if main app.py is running
    main_bot_running = _check_main_bot_running()
    if main_bot_running:
        print("DEBUG: Main bot (app.py) is running - it should already be connected to TWS")
        print("DEBUG: Creating separate connection for web server API with different clientId")
    
    if _bot_connection is None:
        print("DEBUG: Creating new connection instance...")
        try:
            print("DEBUG: Attempting to import connection class...")
            
            # If main bot is running, use a different clientId to avoid conflicts
            if main_bot_running:
                try:
                    old_client_id = getattr(Config, 'clientId', None)
                    # Choose a different clientId for web server, e.g., 199
                    Config.clientId = 199
                    print(f"DEBUG: Overriding Config.clientId for web server connection: {old_client_id} -> {Config.clientId}")
                except Exception as cfg_err:
                    print(f"WARNING: Could not override Config.clientId: {cfg_err}")
            
            _bot_connection = connection()
            print(f"DEBUG: Connection object created: {_bot_connection}")
            print(f"DEBUG: Checking if connected: {_bot_connection.ib.isConnected()}")
            
            if not _bot_connection.ib.isConnected():
                print("DEBUG: Not connected, attempting to connect...")
                connect_result = _bot_connection.connect()
                if connect_result is False:
                    print("ERROR: Connection attempt returned False")
                    if main_bot_running:
                        print("WARNING: Main bot is running but web server connection failed.")
                        print("WARNING: This might be due to client ID conflict or TWS connection limit.")
                    return None
                print(f"DEBUG: After connect(), isConnected: {_bot_connection.ib.isConnected()}")
            else:
                print("DEBUG: Already connected!")
        except Exception as e:
            print(f"ERROR: Exception in get_bot_connection (creating): {e}")
            print(f"ERROR: Exception type: {type(e)}")
            import traceback
            print(f"ERROR: Traceback:\n{traceback.format_exc()}")
            logging.error(f"Error connecting to bot: {e}")
            if main_bot_running:
                print("WARNING: Main bot is running but web server failed to create connection.")
                print("WARNING: This might indicate a connection conflict or TWS issue.")
            return None
    
    # Check if still connected
    if _bot_connection:
        print(f"DEBUG: Checking connection status: {_bot_connection.ib.isConnected()}")
        if not _bot_connection.ib.isConnected():
            print("DEBUG: Connection lost, attempting to reconnect...")
            try:
                connect_result = _bot_connection.connect()
                if connect_result is False:
                    print("ERROR: Reconnection attempt returned False")
                    return None
                print(f"DEBUG: After reconnect(), isConnected: {_bot_connection.ib.isConnected()}")
            except Exception as e:
                print(f"ERROR: Exception in get_bot_connection (reconnecting): {e}")
                print(f"ERROR: Exception type: {type(e)}")
                import traceback
                print(f"ERROR: Traceback:\n{traceback.format_exc()}")
                logging.error(f"Error reconnecting to bot: {e}")
                return None
        else:
            print("DEBUG: Connection is active!")
            if main_bot_running:
                print("DEBUG: Both main bot and web server are connected to TWS")
    else:
        print("ERROR: _bot_connection is None after creation attempt")
        return None
    
    print(f"DEBUG: Returning connection: {_bot_connection}")
    print("=" * 50)
    return _bot_connection


def get_account_summary():
    """
    Get account summary from Interactive Brokers
    
    Returns:
        Dictionary with account data or None if error
    """
    print("=" * 50)
    print("DEBUG: get_account_summary() called")
    
    conn = get_bot_connection()
    print(f"DEBUG: Connection result: {conn}")
    if not conn:
        print("ERROR: get_bot_connection() returned None")
        return None
    
    try:
        print("DEBUG: Calling conn.getAccountValue()...")
        account_values = conn.getAccountValue()
        print(f"DEBUG: getAccountValue() returned: {account_values}")
        print(f"DEBUG: account_values type: {type(account_values)}")
        print(f"DEBUG: account_values length: {len(account_values) if account_values else 0}")
        
        if not account_values or len(account_values) == 0:
            print("ERROR: No account values returned")
            return None
        
        print(f"DEBUG: First 3 account values:")
        for i, av in enumerate(account_values[:3]):
            print(f"  [{i}] tag={av.tag}, value={av.value}, currency={av.currency}")
        
        # Parse account values into a dictionary
        print("DEBUG: Parsing account values...")
        account_data = {}
        currency = 'USD'  # Default currency
        
        for av in account_values:
            tag = av.tag
            value = av.value
            currency = av.currency
            print(f"DEBUG: Processing tag={tag}, value={value}, currency={currency}")
            
            # Map IB account value tags to our format
            if tag == 'NetLiquidation':
                account_data['netLiquidation'] = float(value) if value else 0
                account_data['totalBalance'] = float(value) if value else 0
                print(f"DEBUG: Set netLiquidation={account_data['netLiquidation']}")
            elif tag == 'BuyingPower':
                account_data['buyingPower'] = float(value) if value else 0
                print(f"DEBUG: Set buyingPower={account_data['buyingPower']}")
            elif tag == 'CashBalance':
                account_data['cashBalance'] = float(value) if value else 0
                print(f"DEBUG: Set cashBalance={account_data['cashBalance']}")
            elif tag == 'TotalCashValue':
                if 'cashBalance' not in account_data:
                    account_data['cashBalance'] = float(value) if value else 0
                    print(f"DEBUG: Set cashBalance from TotalCashValue={account_data['cashBalance']}")
            elif tag == 'GrossPositionValue':
                account_data['grossPositionValue'] = float(value) if value else 0
                print(f"DEBUG: Set grossPositionValue={account_data['grossPositionValue']}")
        
        print(f"DEBUG: currentPnl from Config: {currentPnl}")
        # Get daily PnL from Config
        account_data['dailyProfit'] = float(currentPnl) if currentPnl else 0
        print(f"DEBUG: Set dailyProfit={account_data['dailyProfit']}")
        
        # Calculate daily profit percent if we have net liquidation
        if 'netLiquidation' in account_data and account_data['netLiquidation'] > 0:
            # Estimate: daily profit / (net liquidation - daily profit) * 100
            net_liq = account_data['netLiquidation']
            daily_pnl = account_data['dailyProfit']
            if net_liq - daily_pnl > 0:
                account_data['dailyProfitPercent'] = round((daily_pnl / (net_liq - daily_pnl)) * 100, 2)
            else:
                account_data['dailyProfitPercent'] = 0
        else:
            account_data['dailyProfitPercent'] = 0
        
        # Set defaults if missing
        account_data.setdefault('netLiquidation', 0)
        account_data.setdefault('totalBalance', account_data.get('netLiquidation', 0))
        account_data.setdefault('buyingPower', 0)
        account_data.setdefault('cashBalance', 0)
        account_data.setdefault('currency', currency if account_values else 'USD')
        
        # Get open positions count
        print("DEBUG: Getting open positions count...")
        positions = get_open_positions()
        print(f"DEBUG: Positions result: {positions}")
        account_data['openPositionsCount'] = len(positions) if positions else 0
        print(f"DEBUG: openPositionsCount={account_data['openPositionsCount']}")
        
        print(f"DEBUG: Final account_data: {account_data}")
        print("=" * 50)
        return account_data
        
    except Exception as e:
        print(f"ERROR: Exception in get_account_summary: {e}")
        print(f"ERROR: Exception type: {type(e)}")
        import traceback
        print(f"ERROR: Traceback:\n{traceback.format_exc()}")
        logging.error(f"Error getting account summary: {e}")
        return None


def get_open_positions():
    """
    Get all open positions from Interactive Brokers
    
    Returns:
        List of position dictionaries or None if error
    """
    print("=" * 50)
    print("DEBUG: get_open_positions() called")
    
    conn = get_bot_connection()
    print(f"DEBUG: Connection result: {conn}")
    if not conn:
        print("ERROR: get_bot_connection() returned None")
        return None
    
    try:
        print("DEBUG: Calling conn.getAllOpenPosition()...")
        positions = conn.getAllOpenPosition()
        print(f"DEBUG: getAllOpenPosition() returned: {positions}")
        print(f"DEBUG: positions type: {type(positions)}")
        print(f"DEBUG: positions length: {len(positions) if positions else 0}")
        
        if not positions:
            print("DEBUG: No positions found, returning empty list")
            return []
        
        position_list = []
        for pos in positions:
            try:
                contract = pos.contract
                position_data = {
                    'symbol': contract.symbol if hasattr(contract, 'symbol') else 'N/A',
                    'quantity': pos.position,
                    'avgPrice': pos.avgCost if hasattr(pos, 'avgCost') else 0,
                    'marketValue': pos.marketValue if hasattr(pos, 'marketValue') else 0,
                    'unrealizedPnl': pos.unrealizedPNL if hasattr(pos, 'unrealizedPNL') else 0,
                }
                
                # Calculate unrealized PnL percent
                if position_data['avgPrice'] > 0 and position_data['quantity'] != 0:
                    cost_basis = abs(position_data['avgPrice'] * position_data['quantity'])
                    if cost_basis > 0:
                        position_data['unrealizedPnlPercent'] = round(
                            (position_data['unrealizedPnl'] / cost_basis) * 100, 2
                        )
                    else:
                        position_data['unrealizedPnlPercent'] = 0
                else:
                    position_data['unrealizedPnlPercent'] = 0
                
                # Get current price (market value / quantity)
                if position_data['quantity'] != 0:
                    position_data['currentPrice'] = round(
                        position_data['marketValue'] / abs(position_data['quantity']), 2
                    )
                else:
                    position_data['currentPrice'] = position_data['avgPrice']
                
                # Entry time (not available from IB positions, set to None)
                position_data['entryTime'] = None
                
                position_list.append(position_data)
                
            except Exception as e:
                logging.error(f"Error processing position: {e}")
                continue
        
        print(f"DEBUG: Returning {len(position_list)} positions")
        print("=" * 50)
        return position_list
        
    except Exception as e:
        print(f"ERROR: Exception in get_open_positions: {e}")
        print(f"ERROR: Exception type: {type(e)}")
        import traceback
        print(f"ERROR: Traceback:\n{traceback.format_exc()}")
        logging.error(f"Error getting open positions: {e}")
        return None


def get_daily_profit():
    """
    Get daily profit/loss
    
    Returns:
        Daily PnL value
    """
    try:
        return float(currentPnl) if currentPnl else 0
    except:
        return 0


def place_order(order_data):
    """
    Place a trading order through the bot
    
    Args:
        order_data: Dictionary with order parameters:
            - symbol: Stock symbol (required)
            - tradeType: Trade type (e.g., 'Limit Order', 'Custom', 'FB', etc.) (required)
            - buySell: 'BUY' or 'SELL' (required)
            - stopLoss: Stop loss type (required)
            - takeProfit: Take profit ratio (required)
            - timeFrame: Time frame (e.g., '1 min', '5 mins') (required)
            - timeInForce: 'DAY', 'OTH', or 'GTC' (required)
            - risk: Risk amount (required)
            - customStopLossValue: Custom stop loss value (optional, default "0")
            - entryPoints: Entry price for Limit Order/Stop Order (optional, default "0")
            - breakEven: Break even flag (optional, default False)
    
    Returns:
        Dictionary with success status and message, or None if error
    """
    print("=" * 50)
    print("DEBUG: place_order() called")
    print(f"DEBUG: order_data: {order_data}")
    
    conn = get_bot_connection()
    if not conn:
        print("ERROR: get_bot_connection() returned None")
        return {'success': False, 'error': 'Bot not connected. Please ensure TWS is running and bot is connected.'}
    
    try:
        # Extract and validate required fields
        symbol = order_data.get('symbol', '').strip().upper()
        if not symbol:
            return {'success': False, 'error': 'Symbol is required'}
        
        trade_type = order_data.get('tradeType', '').strip()
        if not trade_type:
            return {'success': False, 'error': 'Trade type is required'}
        
        buy_sell = order_data.get('buySell', '').strip().upper()
        if buy_sell not in ['BUY', 'SELL']:
            return {'success': False, 'error': 'buySell must be BUY or SELL'}
        
        stop_loss = order_data.get('stopLoss', '').strip()
        if not stop_loss:
            return {'success': False, 'error': 'Stop loss is required'}
        
        take_profit = order_data.get('takeProfit', '').strip()
        if not take_profit:
            return {'success': False, 'error': 'Take profit is required'}
        
        time_frame = order_data.get('timeFrame', '').strip()
        if not time_frame:
            return {'success': False, 'error': 'Time frame is required'}
        
        tif = order_data.get('timeInForce', 'DAY').strip().upper()
        if tif not in ['DAY', 'OTH', 'GTC']:
            return {'success': False, 'error': 'Time in force must be DAY, OTH, or GTC'}
        
        risk = order_data.get('risk', '').strip()
        if not risk:
            return {'success': False, 'error': 'Risk is required'}
        try:
            risk = float(risk)
        except ValueError:
            return {'success': False, 'error': 'Risk must be a valid number'}
        
        # Optional fields with defaults
        sl_value = str(order_data.get('customStopLossValue', '0') or '0')
        entry_points = str(order_data.get('entryPoints', '0') or '0')
        break_even = order_data.get('breakEven', False)
        if isinstance(break_even, str):
            break_even = break_even.lower() in ['true', '1', 'yes']
        
        # Determine outsideRth based on current session
        session = _get_current_session()
        outside_rth = session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT')
        
        # ATR is disabled, so use 0
        atr_percentage = "0"
        
        # Quantity is calculated from risk, so pass 0
        quantity = 0
        
        print(f"DEBUG: Calling SendTrade with:")
        print(f"  symbol={symbol}, timeFrame={time_frame}, profit={take_profit}")
        print(f"  stopLoss={stop_loss}, risk={risk}, tif={tif}, barType={trade_type}")
        print(f"  buySellType={buy_sell}, atrPercentage={atr_percentage}, quantity={quantity}")
        print(f"  pullBackNo={pullBackNo}, slValue={sl_value}, breakEven={break_even}")
        print(f"  outsideRth={outside_rth}, entry_points={entry_points}")
        
        # Run the async SendTrade function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                SendTrade(
                    conn,
                    symbol,
                    time_frame,
                    take_profit,
                    stop_loss,
                    str(risk),
                    tif,
                    trade_type,
                    buy_sell,
                    atr_percentage,
                    quantity,
                    pullBackNo,
                    sl_value,
                    break_even,
                    outside_rth,
                    entry_points
                )
            )
            print("DEBUG: SendTrade completed successfully")
            return {
                'success': True,
                'message': f'Order placed successfully for {symbol}',
                'symbol': symbol,
                'tradeType': trade_type,
                'session': session
            }
        finally:
            loop.close()
        
    except Exception as e:
        print(f"ERROR: Exception in place_order: {e}")
        print(f"ERROR: Exception type: {type(e)}")
        import traceback
        print(f"ERROR: Traceback:\n{traceback.format_exc()}")
        logging.error(f"Error placing order: {e}")
        return {'success': False, 'error': f'Failed to place order: {str(e)}'}
