"""
Balance History Management Module
Handles reading and writing balance history data to JSON file
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

# Get the directory where this script is located
BASE_DIR = Path(__file__).parent
BALANCE_HISTORY_FILE = BASE_DIR / 'balance_history.json'


def init_balance_history_file():
    """Initialize balance history file if it doesn't exist"""
    if not BALANCE_HISTORY_FILE.exists():
        BALANCE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BALANCE_HISTORY_FILE, 'w') as f:
            json.dump([], f)


def get_balance_history(user_id, days=30):
    """
    Read balance history from file
    
    Args:
        user_id: User identifier (email, id, etc.)
        days: Number of days of history to retrieve (default: 30)
    
    Returns:
        List of balance history entries sorted by timestamp
    """
    init_balance_history_file()
    
    with open(BALANCE_HISTORY_FILE, 'r', encoding='utf-8') as f:
        all_history = json.load(f)
    
    # Filter by userId and date range
    cutoff_date = datetime.now() - timedelta(days=days)
    
    filtered_history = [
        entry for entry in all_history
        if entry.get('userId') == user_id and 
        datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00')) >= cutoff_date
    ]
    
    # Sort by timestamp
    filtered_history.sort(key=lambda x: x['timestamp'])
    
    return filtered_history


def save_balance_snapshot(user_id, balance_data):
    """
    Save balance snapshot to history file
    
    Args:
        user_id: User identifier (email, id, etc.)
        balance_data: Dictionary containing balance information:
            - totalBalance
            - netLiquidation
            - cashBalance
            - buyingPower
            - dailyProfit
    
    Returns:
        The saved snapshot dictionary
    """
    init_balance_history_file()
    
    with open(BALANCE_HISTORY_FILE, 'r', encoding='utf-8') as f:
        history = json.load(f)
    
    snapshot = {
        'id': str(int(datetime.now().timestamp() * 1000)),
        'userId': user_id,
        'timestamp': datetime.now().isoformat(),
        'totalBalance': balance_data.get('totalBalance', 0),
        'netLiquidation': balance_data.get('netLiquidation', 0),
        'cashBalance': balance_data.get('cashBalance', 0),
        'buyingPower': balance_data.get('buyingPower', 0),
        'dailyProfit': balance_data.get('dailyProfit', 0),
    }
    
    history.append(snapshot)
    
    # Keep only last 90 days of data
    cutoff_date = datetime.now() - timedelta(days=90)
    filtered_history = [
        entry for entry in history
        if datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00')) >= cutoff_date
    ]
    
    with open(BALANCE_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(filtered_history, f, indent=2)
    
    return snapshot
