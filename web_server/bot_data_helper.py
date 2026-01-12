"""
Bot Data Helper Module
Retrieves real account data from the trading bot (IBConnection)
"""

import sys
import os
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
    
    print("DEBUG: Attempting to import Config.currentPnl...")
    from Config import currentPnl
    print(f"DEBUG: Successfully imported Config.currentPnl = {currentPnl}")
    
    import logging
    print("DEBUG: Successfully imported logging")
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


def get_bot_connection():
    """Get or create bot connection instance"""
    global _bot_connection
    
    print("=" * 50)
    print("DEBUG: get_bot_connection() called")
    print(f"DEBUG: _bot_connection is None: {_bot_connection is None}")
    
    if _bot_connection is None:
        print("DEBUG: Creating new connection instance...")
        try:
            print("DEBUG: Attempting to import connection class...")
            _bot_connection = connection()
            print(f"DEBUG: Connection object created: {_bot_connection}")
            print(f"DEBUG: Checking if connected: {_bot_connection.ib.isConnected()}")
            
            if not _bot_connection.ib.isConnected():
                print("DEBUG: Not connected, attempting to connect...")
                _bot_connection.connect()
                print(f"DEBUG: After connect(), isConnected: {_bot_connection.ib.isConnected()}")
            else:
                print("DEBUG: Already connected!")
        except Exception as e:
            print(f"ERROR: Exception in get_bot_connection (creating): {e}")
            print(f"ERROR: Exception type: {type(e)}")
            import traceback
            print(f"ERROR: Traceback:\n{traceback.format_exc()}")
            logging.error(f"Error connecting to bot: {e}")
            return None
    
    # Check if still connected
    if _bot_connection:
        print(f"DEBUG: Checking connection status: {_bot_connection.ib.isConnected()}")
        if not _bot_connection.ib.isConnected():
            print("DEBUG: Connection lost, attempting to reconnect...")
            try:
                _bot_connection.connect()
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
