from flask import request, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from functools import wraps
import os
import math
import logging

import Config

# Import PyJWT - ensure PyJWT is installed, not the 'jwt' package
try:
    import jwt
    # Verify it's PyJWT by checking for decode method
    if not hasattr(jwt, 'decode'):
        raise ImportError(
            "Wrong JWT package installed. Please uninstall 'jwt' and install 'PyJWT': "
            "pip uninstall jwt && pip install PyJWT"
        )
except ImportError as e:
    raise ImportError("PyJWT is required. Install it with: pip install PyJWT") from e


db = SQLAlchemy()


def init_api_routes(api_app, get_tk_app):
    """
    Initialize authentication and account-related API routes on the given Flask app.

    get_tk_app: callable that returns the current TkApp instance (or None).
    """
    # Basic configuration (only set defaults if not already configured)
    api_app.config.setdefault('SECRET_KEY', os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production'))
    
    # Use absolute path for database to ensure it works regardless of working directory
    db_path = os.environ.get('DATABASE_URL')
    if not db_path:
        # Get the directory where the script is located
        script_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = f'sqlite:///{os.path.join(script_dir, "bot_users.db")}'
    api_app.config.setdefault('SQLALCHEMY_DATABASE_URI', db_path)
    api_app.config.setdefault('SQLALCHEMY_TRACK_MODIFICATIONS', False)
    api_app.config.setdefault('JWT_EXPIRATION_DELTA', timedelta(days=7))

    db.init_app(api_app)

    # User model is defined inside so it is bound to this db/app
    class User(db.Model):
        """User model for storing user information"""
        __tablename__ = 'users'

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(80), unique=True, nullable=False, index=True)
        email = db.Column(db.String(120), unique=True, nullable=False, index=True)
        password_hash = db.Column(db.String(255), nullable=False)
        created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
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
                'is_active': self.is_active,
            }

    # Create tables
    with api_app.app_context():
        try:
            db.create_all()
            logging.info("Database tables created/verified successfully")
        except Exception as db_init_error:
            logging.error(f"Database initialization error: {db_init_error}")
            import traceback
            logging.error(traceback.format_exc())

    # Health check endpoint
    @api_app.route('/api/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        try:
            # Test database connection
            with api_app.app_context():
                # Simple query to test database
                User.query.limit(1).all()
            return jsonify({'status': 'ok', 'message': 'API is running'}), 200
        except Exception as e:
            logging.error(f"Health check error: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return jsonify({'status': 'error', 'message': str(e)}), 500

    # JWT helpers
    def generate_token(user_id):
        """Generate JWT token for user"""
        try:
            # Ensure we have a usable secret key
            secret = api_app.config.get('SECRET_KEY') or os.environ.get('SECRET_KEY')
            if not secret:
                # Fallback to a default (not ideal for production, but prevents crashes)
                secret = 'your-secret-key-change-in-production'
                logging.warning("JWT: SECRET_KEY not set, using fallback value. Please configure SECRET_KEY in environment or app config.")

            # Build payload with numeric timestamps for maximum compatibility
            now = datetime.now(timezone.utc)
            exp_time = now + api_app.config['JWT_EXPIRATION_DELTA']
            payload = {
                'user_id': user_id,
                'exp': int(exp_time.timestamp()),
                'iat': int(now.timestamp()),
            }

            token = jwt.encode(payload, secret, algorithm='HS256')
            # PyJWT 1.x returns bytes, 2.x returns str
            if isinstance(token, bytes):
                token = token.decode('utf-8')
            return token
        except Exception as e:
            logging.error(f"JWT: Error generating token for user_id={user_id}: {e}")
            import traceback
            logging.error(traceback.format_exc())
            raise

    def verify_token(token):
        """Verify JWT token and return user_id"""
        try:
            payload = jwt.decode(token, api_app.config['SECRET_KEY'], algorithms=['HS256'])
            return payload['user_id']
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    # Auth decorator
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
            if user_id is None:
                return jsonify({'error': 'Token is invalid or expired'}), 401

            user = User.query.get(user_id)
            if not user or not user.is_active:
                return jsonify({'error': 'User not found or inactive'}), 401

            return f(user, *args, **kwargs)

        return decorated

    # Helper functions for account data
    def _get_account_value(conn, key):
        """Get a specific account value by key"""
        try:
            account_values = conn.getAccountValue()
            if not account_values:
                return None

            for av in account_values:
                if av.tag == key:
                    return float(av.value) if av.value else 0.0
            return None
        except Exception as e:
            logging.error(f"Error getting account value {key}: {e}")
            return None

    def _format_position(position):
        """Format position object to dictionary"""
        try:
            return {
                'symbol': position.contract.symbol,
                'exchange': getattr(position.contract, 'exchange', ''),
                'currency': getattr(position.contract, 'currency', 'USD'),
                'position': float(position.position),
                'avgCost': float(getattr(position, 'avgCost', 0.0)),
                'marketPrice': float(getattr(position, 'marketPrice', 0.0)),
                'marketValue': float(getattr(position, 'marketValue', 0.0)),
                'unrealizedPnL': float(getattr(position, 'unrealizedPnL', 0.0)),
                'realizedPnL': float(getattr(position, 'realizedPnL', 0.0)),
            }
        except Exception as e:
            logging.error(f"Error formatting position: {e}")
            return None

    def _get_closed_positions():
        """Get closed positions from order status data"""
        try:
            closed_positions = []
            for order_id, order_data in Config.orderStatusData.items():
                if order_data.get('ordType') in ['TakeProfit', 'StopLoss']:
                    status = order_data.get('status', '')
                    if status == 'Filled':
                        # Try to get related entry order to get symbol info
                        entry_order_id = order_data.get('entryOrderId')
                        if entry_order_id and entry_order_id in Config.orderStatusData:
                            entry_data = Config.orderStatusData[entry_order_id]
                            closed_positions.append(
                                {
                                    'symbol': entry_data.get('usersymbol', ''),
                                    'orderId': order_id,
                                    'orderType': order_data.get('ordType', ''),
                                    'status': status,
                                    'filledPrice': Config.orderFilledPrice.get(order_id, 0.0),
                                    'quantity': order_data.get('quantity', 0),
                                    'filledTime': order_data.get('filledTime', ''),
                                    'entryPrice': entry_data.get('lastPrice', 0.0),
                                    'entryOrderId': entry_order_id,
                                }
                            )
            return closed_positions
        except Exception as e:
            logging.error(f"Error getting closed positions: {e}")
            return []

    #
    # Authentication endpoints
    #
    @api_app.route('/api/auth/signup', methods=['POST'])
    def signup():
        """User signup endpoint"""
        try:
            data = request.get_json()

            # Validate input
            if not data:
                logging.warning("Signup: No data provided")
                return jsonify({'error': 'No data provided'}), 400

            name = data.get('name', '').strip() if data.get('name') else ''
            email = data.get('email', '').strip().lower() if data.get('email') else ''
            password = data.get('password', '')

            # Validation
            if not name or not email or not password:
                logging.warning(f"Signup: Missing required fields - name={bool(name)}, email={bool(email)}, password={bool(password)}")
                return jsonify({'error': 'name, email, and password are required'}), 400

            if len(name) < 3:
                return jsonify({'error': 'name must be at least 3 characters'}), 400

            if len(password) < 6:
                return jsonify({'error': 'Password must be at least 6 characters'}), 400

            if '@' not in email:
                return jsonify({'error': 'Invalid email format'}), 400

            # Check if user already exists
            try:
                if User.query.filter_by(name=name).first():
                    logging.warning(f"Signup: Username already exists: {name}")
                    return jsonify({'error': 'name already exists'}), 409

                if User.query.filter_by(email=email).first():
                    logging.warning(f"Signup: Email already exists: {email}")
                    return jsonify({'error': 'Email already registered'}), 409
            except Exception as db_check_error:
                logging.error(f"Signup: Database check error: {db_check_error}")
                import traceback
                logging.error(traceback.format_exc())
                return jsonify({'error': 'Database error occurred'}), 500

            # Create new user
            try:
                password_hash = generate_password_hash(password)
                new_user = User(name=name, email=email, password_hash=password_hash)

                db.session.add(new_user)
                db.session.commit()
            except Exception as create_error:
                db.session.rollback()
                logging.error(f"Signup: User creation error: {create_error}")
                import traceback
                logging.error(traceback.format_exc())
                return jsonify({'error': 'Failed to create user'}), 500

            # Generate token
            try:
                token = generate_token(new_user.id)
            except Exception as token_error:
                logging.error(f"Signup: Token generation error: {token_error}")
                import traceback
                logging.error(traceback.format_exc())
                return jsonify({'error': 'Token generation failed'}), 500

            logging.info(f"Signup: User created successfully: {email} (id: {new_user.id})")
            return jsonify(
                {
                    'message': 'User created successfully',
                    'token': token,
                    'user': new_user.to_dict(),
                }
            ), 201

        except Exception as e:
            db.session.rollback()
            logging.error(f"Signup: Unexpected error: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return jsonify({'error': f'Server error: {str(e)}'}), 500

    @api_app.route('/api/auth/login', methods=['POST'])
    def login():
        """User login endpoint"""
        try:
            data = request.get_json()
            
            if not data:
                logging.warning("Login: No data provided")
                return jsonify({'error': 'No data provided'}), 400

            # Accept email (standard) or name (fallback)
            email = data.get('email', '').strip() if data.get('email') else ''
            name = data.get('name', '').strip() if data.get('name') else ''
            password = data.get('password', '')

            # Use email if provided, otherwise use name
            identifier = email if email else name

            if not identifier or not password:
                logging.warning(f"Login: Missing credentials - identifier={bool(identifier)}, password={bool(password)}")
                return jsonify({'error': 'Email/name and password are required'}), 400

            # Find user by email or name
            try:
                user = User.query.filter((User.email == identifier) | (User.name == identifier)).first()
            except Exception as db_error:
                logging.error(f"Login: Database query error: {db_error}")
                import traceback
                logging.error(traceback.format_exc())
                return jsonify({'error': 'Database error occurred'}), 500

            if not user:
                logging.warning(f"Login: User not found for identifier: {identifier}")
                return jsonify({'error': 'Invalid email/name or password'}), 401

            if not user.is_active:
                logging.warning(f"Login: Inactive account for identifier: {identifier}")
                return jsonify({'error': 'Account is inactive'}), 403

            # Check password
            try:
                password_valid = check_password_hash(user.password_hash, password)
            except Exception as pwd_error:
                logging.error(f"Login: Password check error: {pwd_error}")
                import traceback
                logging.error(traceback.format_exc())
                return jsonify({'error': 'Password verification error'}), 500

            if not password_valid:
                logging.warning(f"Login: Invalid password for identifier: {identifier}")
                return jsonify({'error': 'Invalid email/name or password'}), 401

            # Update last login
            try:
                user.last_login = datetime.now(timezone.utc)
                db.session.commit()
            except Exception as commit_error:
                logging.error(f"Login: Database commit error: {commit_error}")
                db.session.rollback()
                # Continue anyway - login can still succeed without updating last_login

            # Generate token
            try:
                token = generate_token(user.id)
            except Exception as token_error:
                logging.error(f"Login: Token generation error: {token_error}")
                import traceback
                logging.error(traceback.format_exc())
                return jsonify({'error': 'Token generation failed'}), 500

            logging.info(f"Login: Successful login for user: {user.email or user.name} (id: {user.id})")
            return jsonify(
                {
                    'message': 'Login successful',
                    'token': token,
                    'user': user.to_dict(),
                }
            ), 200

        except Exception as e:
            logging.error(f"Login: Unexpected error: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return jsonify({'error': f'Server error: {str(e)}'}), 500

    @api_app.route('/api/auth/verify', methods=['GET'])
    @token_required
    def verify_token_endpoint(user):
        """Verify token and return user information"""
        return jsonify({'user': user.to_dict()}), 200

    #
    # Account endpoints
    #
    @api_app.route('/api/account/balance', methods=['GET'])
    @token_required
    def get_account_balance(user):
        """Get account balance"""
        try:
            tk_app = get_tk_app()
            if not tk_app or not tk_app.connection:
                return jsonify({'error': 'Bot not initialized or not connected'}), 500

            conn = tk_app.connection

            total_cash = _get_account_value(conn, 'TotalCashBalance')
            net_liquidation = _get_account_value(conn, 'NetLiquidation')
            cash_balance = _get_account_value(conn, 'CashBalance')

            return jsonify(
                {
                    'totalCashBalance': total_cash,
                    'netLiquidation': net_liquidation,
                    'cashBalance': cash_balance,
                    'currency': 'USD',
                }
            ), 200
        except Exception as e:
            logging.error(f"API: Error getting account balance: {e}")
            return jsonify({'error': str(e)}), 500

    @api_app.route('/api/account/daily-profit', methods=['GET'])
    @token_required
    def get_daily_profit(user):
        """Get daily profit/loss"""
        try:
            tk_app = get_tk_app()
            if not tk_app or not tk_app.connection:
                return jsonify({'error': 'Bot not initialized or not connected'}), 500

            conn = tk_app.connection

            # Base value from Config
            daily_pnl = getattr(Config, 'currentPnl', 0.0)

            # Try to get from account values if available
            try:
                account_values = conn.getAccountValue()
                if account_values:
                    account = account_values[0].account if account_values else None
                    if account:
                        pnl_data = conn.ib.pnl(account=account)
                        if pnl_data and len(pnl_data) > 0:
                            val = pnl_data[0].dailyPnL
                            if val is not None and not math.isnan(val):
                                daily_pnl = float(val)
            except Exception as e:
                logging.warning(f"Could not get PnL from account: {e}, using cached value")

            return jsonify({'dailyPnL': daily_pnl, 'currency': 'USD'}), 200
        except Exception as e:
            logging.error(f"API: Error getting daily profit: {e}")
            return jsonify({'error': str(e)}), 500

    @api_app.route('/api/account/open-positions', methods=['GET'])
    @token_required
    def get_open_positions(user):
        """Get all open positions"""
        try:
            tk_app = get_tk_app()
            if not tk_app or not tk_app.connection:
                return jsonify({'error': 'Bot not initialized or not connected'}), 500

            conn = tk_app.connection
            positions = conn.getAllOpenPosition()

            formatted_positions = []
            if positions:
                for pos in positions:
                    formatted = _format_position(pos)
                    if formatted:
                        formatted_positions.append(formatted)

            return jsonify({'positions': formatted_positions, 'count': len(formatted_positions)}), 200
        except Exception as e:
            logging.error(f"API: Error getting open positions: {e}")
            return jsonify({'error': str(e)}), 500

    @api_app.route('/api/account/closed-positions', methods=['GET'])
    @token_required
    def get_closed_positions(user):
        """Get closed positions from order history"""
        try:
            tk_app = get_tk_app()
            if not tk_app or not tk_app.connection:
                return jsonify({'error': 'Bot not initialized or not connected'}), 500

            closed_positions = _get_closed_positions()

            return jsonify({'positions': closed_positions, 'count': len(closed_positions)}), 200
        except Exception as e:
            logging.error(f"API: Error getting closed positions: {e}")
            return jsonify({'error': str(e)}), 500

    @api_app.route('/api/account/positions', methods=['GET'])
    @token_required
    def get_all_positions(user):
        """Get both open and closed positions"""
        try:
            tk_app = get_tk_app()
            if not tk_app or not tk_app.connection:
                return jsonify({'error': 'Bot not initialized or not connected'}), 500

            conn = tk_app.connection
            
            # Get open positions
            open_positions = []
            try:
                positions = conn.getAllOpenPosition()
                if positions:
                    for pos in positions:
                        formatted = _format_position(pos)
                        if formatted:
                            formatted['status'] = 'open'
                            open_positions.append(formatted)
            except Exception as e:
                logging.error(f"API: Error getting open positions: {e}")

            # Get closed positions
            closed_positions = []
            try:
                closed_positions = _get_closed_positions()
                for pos in closed_positions:
                    pos['status'] = 'closed'
            except Exception as e:
                logging.error(f"API: Error getting closed positions: {e}")

            # Combine both
            all_positions = open_positions + closed_positions

            return jsonify({
                'positions': all_positions,
                'open': len(open_positions),
                'closed': len(closed_positions),
                'count': len(all_positions)
            }), 200
        except Exception as e:
            logging.error(f"API: Error getting all positions: {e}")
            import traceback
            logging.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500

    @api_app.route('/api/account/buying-power', methods=['GET'])
    @token_required
    def get_buying_power(user):
        """Get buying power"""
        try:
            tk_app = get_tk_app()
            if not tk_app or not tk_app.connection:
                return jsonify({'error': 'Bot not initialized or not connected'}), 500

            conn = tk_app.connection
            buying_power = _get_account_value(conn, 'BuyingPower')

            return jsonify({'buyingPower': buying_power, 'currency': 'USD'}), 200
        except Exception as e:
            logging.error(f"API: Error getting buying power: {e}")
            return jsonify({'error': str(e)}), 500

    @api_app.route('/api/account/summary', methods=['GET'])
    @token_required
    def get_account_summary(user):
        """Get complete account summary"""
        try:
            tk_app = get_tk_app()
            if not tk_app or not tk_app.connection:
                return jsonify({'error': 'Bot not initialized or not connected'}), 500

            conn = tk_app.connection

            # Balances
            total_cash = _get_account_value(conn, 'TotalCashBalance')
            net_liquidation = _get_account_value(conn, 'NetLiquidation')
            cash_balance = _get_account_value(conn, 'CashBalance')
            buying_power = _get_account_value(conn, 'BuyingPower')

            # Daily PnL
            daily_pnl = getattr(Config, 'currentPnl', 0.0)
            try:
                account_values = conn.getAccountValue()
                if account_values:
                    account = account_values[0].account if account_values else None
                    if account:
                        pnl_data = conn.ib.pnl(account=account)
                        if pnl_data and len(pnl_data) > 0:
                            val = pnl_data[0].dailyPnL
                            if val is not None and not math.isnan(val):
                                daily_pnl = float(val)
            except Exception as e:
                logging.warning(f"Could not get PnL from account: {e}, using cached value")

            # Open positions
            positions = conn.getAllOpenPosition()
            formatted_positions = []
            if positions:
                for pos in positions:
                    formatted = _format_position(pos)
                    if formatted:
                        formatted_positions.append(formatted)

            # Closed positions
            closed_positions = _get_closed_positions()

            return jsonify(
                {
                    'balance': {
                        'totalCashBalance': total_cash,
                        'netLiquidation': net_liquidation,
                        'cashBalance': cash_balance,
                        'currency': 'USD',
                    },
                    'dailyProfit': {'dailyPnL': daily_pnl, 'currency': 'USD'},
                    'buyingPower': {'buyingPower': buying_power, 'currency': 'USD'},
                    'openPositions': {'positions': formatted_positions, 'count': len(formatted_positions)},
                    'closedPositions': {'positions': closed_positions, 'count': len(closed_positions)},
                }
            ), 200
        except Exception as e:
            logging.error(f"API: Error getting account summary: {e}")
            return jsonify({'error': str(e)}), 500

