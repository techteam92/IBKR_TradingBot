"""
Flask Backend Server for Bot Management Website
Handles user authentication (signup/login) and user data storage
"""

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import jwt
import os
import subprocess
import platform
from functools import wraps
from balance_history import get_balance_history, save_balance_snapshot
from bot_data_helper import get_account_summary, get_open_positions, get_daily_profit

app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///bot_users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_EXPIRATION_DELTA'] = timedelta(days=7)  # Token expires in 7 days

# Initialize extensions
db = SQLAlchemy(app)
CORS(app)  # Enable CORS for React.js frontend

# Bot process management
bot_process = None
bot_status = {
    'isRunning': False,
    'startTime': None,
    'pid': None,
    'startedByWeb': False,  # Track if started by web API or manually
}


# User Model
class User(db.Model):
    """User model for storing user information"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    
    def to_dict(self):
        """Convert user object to dictionary (excluding password)"""
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'is_active': self.is_active
        }


# JWT Token Helper Functions
def generate_token(user_id):
    """Generate JWT token for user"""
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + app.config['JWT_EXPIRATION_DELTA'],
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')


def verify_token(token):
    """Verify JWT token and return user_id"""
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload['user_id']
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# Authentication Decorator
def token_required(f):
    """Decorator to protect routes that require authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Check for token in Authorization header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(' ')[1]  # Format: "Bearer <token>"
            except IndexError:
                return jsonify({'error': 'Invalid token format'}), 401
        
        if not token:
            return jsonify({'error': 'Token is missing'}), 401
        
        user_id = verify_token(token)
        if not user_id:
            return jsonify({'error': 'Token is invalid or expired'}), 401
        
        user = User.query.get(user_id)
        if not user or not user.is_active:
            return jsonify({'error': 'User not found or inactive'}), 401
        
        return f(user, *args, **kwargs)
    
    return decorated


# Routes

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'message': 'Server is running'}), 200


@app.route('/api/auth/signup', methods=['POST'])
def signup():
    """User signup endpoint"""
    try:
        data = request.get_json()
        print(data)
        # Validate input
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        name = data.get('name', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        
        # Validation
        if not name or not email or not password:
            return jsonify({'error': 'name, email, and password are required'}), 400
        
        if len(name) < 3:
            return jsonify({'error': 'name must be at least 3 characters'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        if '@' not in email:
            return jsonify({'error': 'Invalid email format'}), 400
        
        # Check if user already exists
        if User.query.filter_by(name=name).first():
            return jsonify({'error': 'name already exists'}), 409
        
        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'Email already registered'}), 409
        
        # Create new user
        password_hash = generate_password_hash(password)
        new_user = User(
            name=name,
            email=email,
            password_hash=password_hash
        )
        
        db.session.add(new_user)
        db.session.commit()
        
        # Generate token
        token = generate_token(new_user.id)
        
        return jsonify({
            'message': 'User created successfully',
            'token': token,
            'user': new_user.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/api/auth/login', methods=['POST'])
def login():
    """User login endpoint"""
    try:
        data = request.get_json()
        print(data)
        print("--------------------------------")
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Accept email (standard) or name (fallback)
        email = data.get('email', '').strip()
        name = data.get('name', '').strip()
        password = data.get('password', '')
        
        # Use email if provided, otherwise use name
        identifier = email if email else name
        
        if not identifier or not password:
            return jsonify({'error': 'Email/name and password are required'}), 400
        
        # Find user by email or name
        user = User.query.filter(
            (User.email == identifier) | (User.name == identifier)
        ).first()
        
        if not user:
            return jsonify({'error': 'Invalid email/name or password'}), 401
        
        if not user.is_active:
            return jsonify({'error': 'Account is inactive'}), 403
        
        # Check password
        if not check_password_hash(user.password_hash, password):
            return jsonify({'error': 'Invalid email/name or password'}), 401
        
        # Update last login
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        # Generate token
        token = generate_token(user.id)
        
        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user': user.to_dict()
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/api/user/profile', methods=['GET'])
@token_required
def get_profile(user):
    """Get current user profile (protected route)"""
    return jsonify({'user': user.to_dict()}), 200


@app.route('/api/user/profile', methods=['PUT'])
@token_required
def update_profile(user):
    """Update user profile (protected route)"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Update email if provided and not already taken
        if 'email' in data:
            new_email = data['email'].strip().lower()
            if new_email != user.email:
                if User.query.filter_by(email=new_email).first():
                    return jsonify({'error': 'Email already registered'}), 409
                user.email = new_email
        
        # Update name if provided and not already taken
        if 'name' in data:
            new_name = data['name'].strip()
            if new_name != user.name:
                if len(new_name) < 3:
                    return jsonify({'error': 'name must be at least 3 characters'}), 400
                if User.query.filter_by(name=new_name).first():
                    return jsonify({'error': 'name already taken'}), 409
                user.name = new_name
        
        db.session.commit()
        
        return jsonify({
            'message': 'Profile updated successfully',
            'user': user.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/api/user/change-password', methods=['POST'])
@token_required
def change_password(user):
    """Change user password (protected route)"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        old_password = data.get('old_password', '')
        new_password = data.get('new_password', '')
        
        if not old_password or not new_password:
            return jsonify({'error': 'Old password and new password are required'}), 400
        
        if len(new_password) < 6:
            return jsonify({'error': 'New password must be at least 6 characters'}), 400
        
        # Verify old password
        if not check_password_hash(user.password_hash, old_password):
            return jsonify({'error': 'Current password is incorrect'}), 401
        
        # Update password
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        return jsonify({'message': 'Password changed successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@app.route('/api/auth/verify', methods=['GET'])
@token_required
def verify_token_endpoint(user):
    """Verify token and return user information"""
    return jsonify({'user': user.to_dict()}), 200


# Bot control routes (protected)
def check_bot_process_running(pid):
    """Check if a process with given PID is actually running"""
    if pid is None:
        return False
    try:
        if platform.system() == 'Windows':
            # On Windows, use tasklist to check if process exists
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}'],
                capture_output=True,
                text=True,
                timeout=2
            )
            return str(pid) in result.stdout
        else:
            # On Unix, send signal 0 to check if process exists
            os.kill(pid, 0)
            return True
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False


@app.route('/api/bot/status', methods=['GET'])
@token_required
def bot_status_endpoint(user):
    """Get bot status"""
    global bot_process, bot_status
    
    # Check if web-started process is still running
    if bot_process:
        if bot_process.poll() is None:
            # Process is still running
            bot_status['isRunning'] = True
            bot_status['pid'] = bot_process.pid
        else:
            # Process has ended
            bot_status['isRunning'] = False
            bot_status['pid'] = None
            bot_status['startTime'] = None
            bot_status['startedByWeb'] = False
            bot_process = None
    elif bot_status.get('pid'):
        # Check if manually started bot is still running
        if check_bot_process_running(bot_status['pid']):
            bot_status['isRunning'] = True
        else:
            # Process no longer exists
            bot_status['isRunning'] = False
            bot_status['pid'] = None
            bot_status['startTime'] = None
            bot_status['startedByWeb'] = False
    else:
        # No process tracked
        bot_status['isRunning'] = False
    
    return jsonify({
        'isRunning': bot_status['isRunning'],
        'startTime': bot_status['startTime'],
        'pid': bot_status['pid'],
        'startedByWeb': bot_status.get('startedByWeb', False),
    }), 200


@app.route('/api/bot/start', methods=['POST'])
@token_required
def bot_start(user):
    """Start the bot process (idempotent - if already running, return success)"""
    global bot_process, bot_status
    
    print("=" * 50)
    print("DEBUG: /api/bot/start endpoint called")
    
    # Check current status first
    if bot_process:
        if bot_process.poll() is None:
            # Process is running
            print(f"DEBUG: Bot already running (PID: {bot_process.pid})")
            return jsonify({
                'message': 'Bot is already running',
                'isRunning': True,
                'pid': bot_process.pid,
                'startTime': bot_status.get('startTime'),
                'startedByWeb': bot_status.get('startedByWeb', False),
            }), 200
        else:
            # Process ended, clean up
            bot_process = None
            bot_status['isRunning'] = False
    
    # Check if bot is running manually (by checking if app.py process exists)
    try:
        if platform.system() == 'Windows':
            # Check for python processes running app.py
            result = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'],
                capture_output=True,
                text=True,
                timeout=2
            )
            # This is a simple check - in production you might want more sophisticated detection
        else:
            # On Unix, check for python processes
            result = subprocess.run(
                ['pgrep', '-f', 'app.py'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                # Bot is running manually
                pids = result.stdout.strip().split('\n')
                if pids and pids[0]:
                    manual_pid = int(pids[0])
                    print(f"DEBUG: Detected manually started bot (PID: {manual_pid})")
                    bot_status['isRunning'] = True
                    bot_status['pid'] = manual_pid
                    bot_status['startedByWeb'] = False
                    return jsonify({
                        'message': 'Bot is already running (started manually)',
                        'isRunning': True,
                        'pid': manual_pid,
                        'startTime': bot_status.get('startTime'),
                        'startedByWeb': False,
                    }), 200
    except Exception as e:
        print(f"DEBUG: Could not check for manually started bot: {e}")
    
    # Bot is not running, start it
    try:
        data = request.get_json() or {}
        bot_path = os.environ.get('BOT_PATH') or data.get('botPath') or 'app.py'
        python_cmd = os.environ.get('PYTHON_CMD') or data.get('pythonCmd') or 'python'
        bot_dir = os.environ.get('BOT_DIR') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        print(f"DEBUG: Starting bot - path: {bot_path}, cmd: {python_cmd}, dir: {bot_dir}")
        
        # Start the Python bot
        bot_process = subprocess.Popen(
            [python_cmd, bot_path],
            cwd=bot_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        bot_status['isRunning'] = True
        bot_status['startTime'] = datetime.utcnow().isoformat()
        bot_status['pid'] = bot_process.pid
        bot_status['startedByWeb'] = True
        
        print(f"DEBUG: Bot started successfully (PID: {bot_process.pid})")
        print("=" * 50)
        
        return jsonify({
            'message': 'Bot started successfully',
            'isRunning': True,
            'pid': bot_process.pid,
            'startTime': bot_status['startTime'],
            'startedByWeb': True,
        }), 200
        
    except Exception as e:
        print(f"ERROR: Failed to start bot: {e}")
        import traceback
        print(f"ERROR: Traceback:\n{traceback.format_exc()}")
        bot_status['isRunning'] = False
        bot_process = None
        return jsonify({
            'error': f'Failed to start bot: {str(e)}',
            'isRunning': False
        }), 500


@app.route('/api/bot/stop', methods=['POST'])
@token_required
def bot_stop(user):
    """Stop the bot process"""
    global bot_process, bot_status
    
    print("=" * 50)
    print("DEBUG: /api/bot/stop endpoint called")
    print(f"DEBUG: bot_status: {bot_status}")
    print(f"DEBUG: bot_process: {bot_process}")
    
    # Check if bot is running
    is_running = False
    pid_to_stop = None
    
    if bot_process:
        if bot_process.poll() is None:
            # Web-started process is running
            is_running = True
            pid_to_stop = bot_process.pid
            print(f"DEBUG: Stopping web-started bot (PID: {pid_to_stop})")
        else:
            # Process already ended
            bot_process = None
            bot_status['isRunning'] = False
    elif bot_status.get('pid') and check_bot_process_running(bot_status['pid']):
        # Manually started bot is running
        is_running = True
        pid_to_stop = bot_status['pid']
        print(f"DEBUG: Stopping manually started bot (PID: {pid_to_stop})")
    
    if not is_running:
        print("DEBUG: Bot is not running")
        return jsonify({
            'message': 'Bot is not running',
            'isRunning': False
        }), 200  # Return 200 (success) instead of 400, since stopping a stopped bot is idempotent
    
    try:
        # Kill the bot process
        if platform.system() == 'Windows':
            print(f"DEBUG: Using taskkill to stop PID {pid_to_stop}")
            result = subprocess.run(
                ['taskkill', '/pid', str(pid_to_stop), '/f', '/t'],
                capture_output=True,
                text=True,
                timeout=10
            )
            print(f"DEBUG: taskkill result: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}")
        else:
            print(f"DEBUG: Using terminate() to stop PID {pid_to_stop}")
            if bot_process:
                bot_process.terminate()
                bot_process.wait(timeout=5)
            else:
                os.kill(pid_to_stop, 15)  # SIGTERM
        
        # Clean up status
        bot_status['isRunning'] = False
        bot_status['startTime'] = None
        bot_status['pid'] = None
        bot_status['startedByWeb'] = False
        bot_process = None
        
        print("DEBUG: Bot stopped successfully")
        print("=" * 50)
        
        return jsonify({
            'message': 'Bot stopped successfully',
            'isRunning': False,
        }), 200
        
    except Exception as e:
        print(f"ERROR: Failed to stop bot: {e}")
        import traceback
        print(f"ERROR: Traceback:\n{traceback.format_exc()}")
        return jsonify({
            'error': f'Failed to stop bot: {str(e)}',
            'isRunning': bot_status['isRunning']
        }), 500


# Account data routes (protected)
@app.route('/api/account/summary', methods=['GET'])
@token_required
def account_summary(user):
    """Get account summary"""
    print("=" * 50)
    print("DEBUG: /api/account/summary endpoint called")
    print(f"DEBUG: User: {user.email if user else 'None'}")
    
    try:
        # Try to get real data from bot
        print("DEBUG: Calling get_account_summary()...")
        account_data = get_account_summary()
        print(f"DEBUG: get_account_summary() returned: {account_data}")
        print(f"DEBUG: account_data is None: {account_data is None}")
        
        # If bot is not connected or data unavailable, return fallback data instead of error
        if account_data is None:
            print("WARNING: account_data is None, returning fallback/default data")
            print("DEBUG: This usually means:")
            print("  1. TWS is not running")
            print("  2. Bot cannot connect to TWS")
            print("  3. IBConnection import failed")
            print("  4. Account values are empty")
            
            # Return default/empty data instead of 503 error so frontend doesn't break
            account_data = {
                'totalBalance': 0,
                'netLiquidation': 0,
                'buyingPower': 0,
                'cashBalance': 0,
                'openPositionsCount': 0,
                'closedPositionsCount': 0,
                'dailyProfit': 0,
                'dailyProfitPercent': 0,
                'totalProfit': 0,
                'totalProfitPercent': 0,
                'currency': 'USD',
                'lastUpdate': datetime.utcnow().isoformat(),
                'connected': False,
                'warning': 'Bot not connected. Showing default values. Make sure TWS is running and bot is connected.'
            }
            print(f"DEBUG: Returning fallback data: {account_data}")
            # Don't return 503, return 200 with connected: false so frontend can handle it gracefully
            return jsonify(account_data), 200
        
        # Add additional fields
        print("DEBUG: Adding additional fields...")
        account_data['closedPositionsCount'] = 0  # TODO: Track closed positions
        account_data['totalProfit'] = account_data.get('dailyProfit', 0)  # TODO: Calculate total profit
        account_data['totalProfitPercent'] = account_data.get('dailyProfitPercent', 0)  # TODO: Calculate total profit %
        account_data['lastUpdate'] = datetime.utcnow().isoformat()
        account_data['connected'] = True
        print(f"DEBUG: Final account_data before saving: {account_data}")
        
        # Save balance snapshot to history
        try:
            user_id = str(user.id) if user.id else user.email
            print(f"DEBUG: Saving balance snapshot for user_id: {user_id}")
            save_balance_snapshot(user_id, account_data)
            print("DEBUG: Balance snapshot saved successfully")
        except Exception as err:
            print(f'ERROR: Error saving balance snapshot: {err}')
            import traceback
            print(f'ERROR: Traceback:\n{traceback.format_exc()}')
            # Don't fail the request if saving history fails
        
        print("DEBUG: Returning success response with account_data")
        print("=" * 50)
        return jsonify(account_data), 200
        
    except Exception as e:
        print(f"ERROR: Exception in account_summary endpoint: {e}")
        print(f"ERROR: Exception type: {type(e)}")
        import traceback
        print(f"ERROR: Traceback:\n{traceback.format_exc()}")
        return jsonify({'error': f'Failed to fetch account summary: {str(e)}'}), 500


@app.route('/api/account/balance-history', methods=['GET'])
@token_required
def account_balance_history(user):
    """Get balance history"""
    try:
        days = int(request.args.get('days', 30))
        user_id = str(user.id) if user.id else user.email
        history = get_balance_history(user_id, days)
        
        return jsonify({'history': history}), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to fetch balance history: {str(e)}'}), 500


@app.route('/api/account/positions', methods=['GET'])
@token_required
def account_positions(user):
    """Get open positions"""
    print("=" * 50)
    print("DEBUG: /api/account/positions endpoint called")
    print(f"DEBUG: User: {user.email if user else 'None'}")
    
    try:
        # Get real positions from bot
        print("DEBUG: Calling get_open_positions()...")
        positions = get_open_positions()
        print(f"DEBUG: get_open_positions() returned: {positions}")
        
        if positions is None:
            print("WARNING: positions is None, returning empty list")
            # Return empty list instead of 503 error so frontend doesn't break
            return jsonify({
                'positions': [],
                'connected': False,
                'warning': 'Bot not connected. Showing empty positions. Make sure TWS is running and bot is connected.'
            }), 200
        
        print(f"DEBUG: Returning {len(positions)} positions")
        print("=" * 50)
        return jsonify({
            'positions': positions,
            'connected': True
        }), 200
        
    except Exception as e:
        print(f"ERROR: Exception in account_positions endpoint: {e}")
        print(f"ERROR: Exception type: {type(e)}")
        import traceback
        print(f"ERROR: Traceback:\n{traceback.format_exc()}")
        return jsonify({'error': f'Failed to fetch positions: {str(e)}'}), 500


@app.route('/api/account/trades', methods=['GET'])
@token_required
def account_trades(user):
    """Get trade history"""
    try:
        # TODO: Integrate with your Python bot or IBKR API to get trade history
        # Mock data structure
        today = datetime.utcnow().date().isoformat()
        requested_date = request.args.get('date', today)
        
        trades = [
            {
                'symbol': 'GOOGL',
                'action': 'BUY',
                'quantity': 25,
                'price': 120.00,
                'time': '09:30:00',
                'date': today,
                'profit': 125.50
            },
            {
                'symbol': 'TSLA',
                'action': 'SELL',
                'quantity': 10,
                'price': 250.50,
                'time': '14:15:00',
                'date': today,
                'profit': -50.25
            },
            {
                'symbol': 'AAPL',
                'action': 'BUY',
                'quantity': 50,
                'price': 175.25,
                'time': '10:45:00',
                'date': today,
                'profit': 87.50
            },
            {
                'symbol': 'MSFT',
                'action': 'SELL',
                'quantity': 15,
                'price': 380.75,
                'time': '15:20:00',
                'date': today,
                'profit': 225.00
            },
            {
                'symbol': 'NVDA',
                'action': 'BUY',
                'quantity': 20,
                'price': 450.00,
                'time': '11:30:00',
                'date': today,
                'profit': 150.00
            },
        ]
        
        # Filter by date if provided
        filtered_trades = [
            t for t in trades if t['date'] == requested_date
        ] if requested_date else trades
        
        return jsonify({
            'trades': filtered_trades,
            'today': filtered_trades,  # Keep for backward compatibility
            'date': requested_date,
            'totalTrades': len(filtered_trades),
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'Failed to fetch trades: {str(e)}'}), 500


# Initialize database
def init_db():
    """Initialize database tables"""
    with app.app_context():
        db.create_all()


# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    # Initialize database
    init_db()
    
    # Run server
    # For development, use debug=True
    # For production, use a proper WSGI server like gunicorn
    app.run(host='0.0.0.0', port=5000, debug=True)
