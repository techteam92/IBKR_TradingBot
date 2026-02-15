from header import *
from NewTradeFrame import *
from ManagePositionFrame import *
from IBConnection import connection
from Config import app_version,tradingTime, pullBackNo
from StatusSaveInFile import *
from SendTrade import SendTrade, _get_current_session
from flask import Flask, request, jsonify
from flask_cors import CORS
from api_routes import init_api_routes
import threading
import asyncio
import sys

# Create Flask app for API endpoints
api_app = Flask(__name__)
# Enable CORS for frontend requests with proper preflight handling
# Set automatic_options=True to handle OPTIONS automatically
CORS(api_app, 
     origins=['https://www.stocktrademanagement.com', 'http://localhost:3000', 'http://localhost:3001'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
     allow_headers=['Content-Type', 'Authorization'],
     supports_credentials=True,
     automatic_options=True)

# Global reference to TkApp instance (will be set after creation)
tk_app_instance = None

class TkApp:

    #  dialog box will open from this
    def __init__(self):
        global tk_app_instance
        logging.info(f'front gui start.  {app_version} {tradingTime}')
        self.connection = connection()
        self.connection.connect()
        # Wait a moment for connection to establish, then request PnL.
        # Run reqPnl on the main thread's event loop (ib_insync requires it); the timer runs in a worker thread.
        self.loop = asyncio.get_event_loop()
        def schedule_reqPnl():
            try:
                self.loop.call_soon_threadsafe(self.connection.reqPnl)
            except Exception as e:
                logging.warning("Could not schedule reqPnl: %s", e)
        threading.Timer(0.5, schedule_reqPnl).start()
        self.frame = Tk()
        self.dialog()
        self.frontLayout()
        tk_app_instance = self  # Set global reference for API endpoints

    # this will run our tkinter and Ib  event will not override.
    async def _tkLoop(self):
        while self.frame:
            self.frame.update()
            await asyncio.sleep(0.03)

    def run(self):
        try:
            logging.info("App Initializing")
            self.loop.run_until_complete(self._tkLoop())
        except Exception as e:
            print(str(e))

    def dialog(self):
        self.frame.title(Config.title)
        self.frame.protocol("WM_DELETE_WINDOW", self.close_window)
        window_width = 1200
        window_height = 620
        screen_width = self.frame.winfo_screenwidth()
        screen_height = self.frame.winfo_screenheight()
        pos_x = int((screen_width - window_width) / 2)
        pos_y = int((screen_height - window_height) / 2)
        self.frame.geometry(f"{window_width}x{window_height}+{pos_x}+{pos_y}")
        menubar = Menu(self.frame, borderwidth=1, bg="#20232A")
        menubar.add_command(label="Manage Position", command=self.openManagePosition)
        menubar.add_command(label="Setting", command=self.Setting)
        menubar.add_command(label="Exit", command=self.close_window)
        self.frame.config(menu=menubar)
        loadCache(self.connection)





    def frontLayout(self):
        s = ttk.Style(self.frame)
        s.theme_use('clam')
        s.configure('raised.TMenubutton', borderwidth=1)
        # ManagePositionFrame(self.frame,self.connection)
        NewTradeFrame(self.frame, self.connection)
        #  Ib Connection Check
        self.connectionCheck()

    def close_window(self):
        StatusSaveInFile()
        logging.info("Shutdown Gui")
        self.connection.cancelTickData(Config.ibContract)
        self.connection.connection_close()
        self.frame = None
        sys.exit()

    def openManagePosition(self):
        if(Config.manage_frame_check):
            tkinter.messagebox.showinfo('Connection', 'Manage Position Frame Already Opened')
        else:
            Config.manage_frame_check = True
            ManagePositionFrame(self.connection)

    def Setting(self):
        DefaultSetting(self.connection)

    def connectionCheck(self):
        loop = 1
        error = 1
        while loop == 1:
            conStatus = self.connection.ibStatusCheck()
            loop += 1
            if not conStatus:
                logging.info("IB Connection failed want to retry")
                retryvar = tkinter.messagebox.askretrycancel('Connection', 'IB Connection failed want to retry?')
                if not retryvar:
                    loop += 1
                    self.close_window()
                else:
                    error = 2
                    self.connection.connect()
                    loop = 1
            else:
                if error == 2:
                    logging.info("TWS Connected")
                    tkinter.messagebox.showinfo('Connection', 'TWS connected')
                loop = 2


# Flask API Endpoints
@api_app.route('/api/place-order', methods=['POST'])
def api_place_order():
    """
    Place a trading order via API
    
    Expected JSON body examples:
    
    For Custom/Limit Order:
    {
        "symbol": "SPY",
        "tradeType": "Limit Order",
        "buySell": "BUY",
        "stopLoss": "Custom",
        "takeProfit": "1:1",
        "timeFrame": "1 min",
        "timeInForce": "DAY",
        "risk": "100",
        "entryPrice": "451.00",
        "customStopLossValue": "450.00",
        "breakEven": false
    }
    
    For Conditional Order:
    {
        "symbol": "SPY",
        "tradeType": "Conditional Order",
        "buySell": "BUY",
        "stopLoss": "EntryBar",
        "takeProfit": "1:1",
        "timeFrame": "5 mins",
        "timeInForce": "DAY",
        "risk": "100",
        "conditionalOrderParams": "1,76.50,Above,77.00,0,Above,0,Above,0",
        "breakEven": false
    }
    
    For other trade types (RB, RBB, FB, etc.):
    {
        "symbol": "SPY",
        "tradeType": "RB",
        "buySell": "BUY",
        "stopLoss": "EntryBar",
        "takeProfit": "1:1",
        "timeFrame": "1 min",
        "timeInForce": "DAY",
        "risk": "100",
        "entryPoints": "0",
        "breakEven": false
    }
    """
    try:
        # Debug: Check request details
        print(f"API: Request method: {request.method}")
        print(f"API: Content-Type: {request.content_type}")
        print(f"API: Raw data length: {len(request.data) if request.data else 0}")
        print(f"API: Raw data: {request.data}")
        
        # Try to get JSON data
        data = request.get_json(silent=True)
        
        # If get_json returns None, try parsing raw data
        if data is None:
            if request.data:
                try:
                    import json
                    data = json.loads(request.data.decode('utf-8'))
                    print(f"API: Parsed JSON from raw data: {data}")
                except Exception as parse_err:
                    print(f"API: Error parsing JSON from raw data: {parse_err}")
                    return jsonify({
                        'success': False,
                        'error': f'Invalid JSON format: {str(parse_err)}'
                    }), 400
            else:
                print("API: WARNING - No data received in request body")
                return jsonify({
                    'success': False,
                    'error': 'No JSON data received. Please send JSON with Content-Type: application/json'
                }), 400
        
        print(f"API: Received order request: {data}")
        logging.info(f"API: Received order request: {data}")
        
        if not tk_app_instance or not tk_app_instance.connection:
            return jsonify({
                'success': False,
                'error': 'Bot not initialized or not connected'
            }), 500
        
        # Validate required fields
        symbol = data.get('symbol', '').strip().upper()
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol is required'}), 400
        
        trade_type = data.get('tradeType', '').strip()
        if not trade_type:
            return jsonify({'success': False, 'error': 'Trade type is required'}), 400
        
        buy_sell = data.get('buySell', '').strip().upper()
        if buy_sell not in ['BUY', 'SELL']:
            return jsonify({'success': False, 'error': 'buySell must be BUY or SELL'}), 400
        
        stop_loss = data.get('stopLoss', '').strip()
        if not stop_loss:
            return jsonify({'success': False, 'error': 'Stop loss is required'}), 400
        
        take_profit = data.get('takeProfit', '').strip()
        if not take_profit:
            return jsonify({'success': False, 'error': 'Take profit is required'}), 400
        
        time_frame = data.get('timeFrame', '').strip()
        if not time_frame:
            return jsonify({'success': False, 'error': 'Time frame is required'}), 400
        
        # Normalize time frame to match Config.timeDict format
        # Handle variations like "1min", "1mins", "1 min", "1 mins" -> "1 min"
        # Handle "5mins", "5 mins" -> "5 mins", etc.
        import re
        original_time_frame = time_frame
        
        # Remove all spaces first for pattern matching
        time_frame_clean = time_frame.replace(' ', '').lower()
        
        # Pattern: number followed by "min", "mins", "hour", "hours"
        match = re.match(r'^(\d+)(min|mins|hour|hours)$', time_frame_clean)
        if match:
            number = match.group(1)
            unit = match.group(2)
            
            # Normalize unit
            if unit in ['min', 'mins']:
                if number == '1':
                    time_frame = '1 min'  # Special case: "1 min" not "1 mins"
                else:
                    time_frame = f'{number} mins'
            elif unit in ['hour', 'hours']:
                if number == '1':
                    time_frame = '1 hour'
                else:
                    time_frame = f'{number} hours'
        else:
            # If no match, try to find in Config.timeDict as-is
            # This handles cases that are already in correct format
            if time_frame not in Config.timeDict:
                # Try common variations
                if time_frame == '1 mins':
                    time_frame = '1 min'
                elif time_frame.lower() == '1min':
                    time_frame = '1 min'
                elif time_frame.lower() == '1mins':
                    time_frame = '1 min'
        
        # Validate that normalized time_frame exists in Config.timeDict
        if time_frame not in Config.timeDict:
            logging.warning(f"API: Invalid time frame '{original_time_frame}' (normalized to '{time_frame}') not found in Config.timeDict")
            return jsonify({
                'success': False, 
                'error': f'Invalid time frame: {original_time_frame}. Valid values: {", ".join(Config.timeFrame)}'
            }), 400
        
        tif = data.get('timeInForce', 'DAY').strip().upper()
        if tif not in ['DAY', 'OTH', 'GTC']:
            return jsonify({'success': False, 'error': 'Time in force must be DAY, OTH, or GTC'}), 400
        
        risk = data.get('risk', '').strip()
        if not risk:
            return jsonify({'success': False, 'error': 'Risk is required'}), 400
        try:
            risk = float(risk)
        except ValueError:
            return jsonify({'success': False, 'error': 'Risk must be a valid number'}), 400
        
        # Optional fields with defaults
        break_even = data.get('breakEven', False)
        if isinstance(break_even, str):
            break_even = break_even.lower() in ['true', '1', 'yes']
        
        # Replay mode: if stop loss is triggered, automatically re-enter the trade
        # Accept both 'replay' and 'replayEnabled' for flexibility
        replay_enabled = data.get('replay', data.get('replayEnabled', False))
        if isinstance(replay_enabled, str):
            replay_enabled = replay_enabled.lower() in ['true', '1', 'yes']
        
        # Validate customStopLossValue if stopLoss is Custom
        sl_value = str(data.get('customStopLossValue', '0') or '0')
        if stop_loss == 'Custom':
            if sl_value == '0' or float(sl_value) == 0:
                return jsonify({
                    'success': False,
                    'error': 'customStopLossValue is required when stopLoss is "Custom"'
                }), 400
        
        # Handle entryPoints vs specific parameters based on trade type
        # entryPoints: Small offset (0-1.0) for general trade types (RB, RBB, FB, etc.)
        # entryPrice: Entry/trigger price for Custom and Limit Order
        # conditionalOrderParams: Comma-separated string for Conditional Order
        entry_points = str(data.get('entryPoints', '0') or '0')
        
        # For Custom and Limit Order: use entryPrice if provided, otherwise fallback to entryPoints
        if trade_type in ['Custom', 'Limit Order']:
            entry_price = data.get('entryPrice')
            if entry_price is not None:
                entry_points = str(entry_price)
            elif entry_points == '0' or float(entry_points) > 1.0:
                return jsonify({
                    'success': False,
                    'error': f'{trade_type} requires entryPrice parameter (entry price/limit price)'
                }), 400
        
        # For Conditional Order: use conditionalOrderParams if provided
        if trade_type == 'Conditional Order':
            conditional_params = data.get('conditionalOrderParams')
            if conditional_params:
                entry_points = str(conditional_params)
            elif entry_points == '0':
                return jsonify({
                    'success': False,
                    'error': 'Conditional Order requires conditionalOrderParams parameter (comma-separated string with 9 values)'
                }), 400
        
        # Determine outsideRth based on current session
        session = _get_current_session()
        outside_rth = session in ('PREMARKET', 'AFTERHOURS', 'OVERNIGHT')
        
        # ATR is disabled, so use 0
        atr_percentage = "0"
        
        # Quantity is calculated from risk, so pass 0
        quantity = 0
        
        # Store replay state in Config.order_replay_pending (same mechanism as GUI)
        # This will be retrieved by StatusUpdate when the Entry order is placed
        trade_key = (symbol, time_frame, trade_type, buy_sell, datetime.datetime.now().timestamp())
        Config.order_replay_pending[trade_key] = replay_enabled
        logging.info(f"API: Stored replay state for trade: key={trade_key}, replay={replay_enabled}")
        
        logging.info(f"API: Calling SendTrade for {symbol}")
        logging.info(f"  tradeType={trade_type}, buySell={buy_sell}, stopLoss={stop_loss}")
        logging.info(f"  takeProfit={take_profit}, timeFrame={time_frame}, risk={risk}, replay={replay_enabled}")
        
        # Run SendTrade in the existing event loop
        loop = tk_app_instance.loop
        future = asyncio.run_coroutine_threadsafe(
            SendTrade(
                tk_app_instance.connection,
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
            ),
            loop
        )
        
        # Wait for the order to be placed (with timeout)
        try:
            future.result(timeout=30)  # 30 second timeout
            logging.info(f"API: Order placed successfully for {symbol}")
            return jsonify({
                'success': True,
                'message': f'Order placed successfully for {symbol}',
                'symbol': symbol,
                'tradeType': trade_type,
                'session': session
            }), 200
        except Exception as e:
            logging.error(f"API: Error placing order: {e}")
            return jsonify({
                'success': False,
                'error': f'Failed to place order: {str(e)}'
            }), 500
        
    except Exception as e:
        logging.error(f"API: Exception in api_place_order: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500


@api_app.route('/api/bot/open-position', methods=['POST'])
def api_bot_open_position():
    """
    Alias for /api/place-order - Open a new position via API
    This endpoint accepts the same JSON format as /api/place-order
    """
    # Simply call the same function as api_place_order
    return api_place_order()


@api_app.route('/api/status', methods=['GET'])
def api_status():
    """Get bot status and connection info"""
    try:
        print(f"API: Checking status")
        if not tk_app_instance or not tk_app_instance.connection:
            return jsonify({
                'connected': False,
                'error': 'Bot not initialized'
            }), 500
        
        is_connected = tk_app_instance.connection.ib.isConnected() if hasattr(tk_app_instance.connection, 'ib') else False
        session = _get_current_session()
        
        return jsonify({
            'connected': is_connected,
            'session': session,
            'version': app_version,
            'tradingTime': tradingTime
        }), 200
    except Exception as e:
        logging.error(f"API: Error in api_status: {e}")
        return jsonify({
            'connected': False,
            'error': str(e)
        }), 500


# Catch-all OPTIONS handler for CORS preflight requests
# This ensures OPTIONS requests always return proper CORS headers, even for non-existent routes
@api_app.before_request
def handle_preflight():
    """Handle CORS preflight OPTIONS requests"""
    if request.method == 'OPTIONS':
        # Get the origin from the request
        origin = request.headers.get('Origin', '')
        allowed_origins = ['https://www.stocktrademanagement.com', 'http://localhost:3000', 'http://localhost:3001']
        
        # Create a response with CORS headers
        from flask import Response
        response = Response()
        
        # Set the origin if it's in the allowed list
        if origin in allowed_origins:
            response.headers.add('Access-Control-Allow-Origin', origin)
        else:
            # Default to the main website origin
            response.headers.add('Access-Control-Allow-Origin', 'https://www.stocktrademanagement.com')
        
        response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        response.headers.add('Access-Control-Max-Age', '3600')
        return response


def run_flask_server():
    """Run Flask server in a separate thread"""
    logging.info("Starting Flask API server on http://127.0.0.1:5000")
    api_app.run(host='localhost', port=5000, debug=False, use_reloader=False)


if __name__ == '__main__':
    # Initialize API routes (auth + account endpoints)
    init_api_routes(api_app, lambda: tk_app_instance)

    # Start Flask server in background thread
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    # Start Tkinter GUI
    app = TkApp()
    app.run()
