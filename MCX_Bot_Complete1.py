# ============================================
# MCX TRADING BOT - CRUDEOIL & NATURALGAS ONLY
# ============================================
# Instrument Configuration:
# ┌─────────────┬──────────┬────────────┬─────────┬─────────┐
# │ Instrument  │ Exchange │ Brick Size │ Renko   │ Lot     │
# ├─────────────┼──────────┼────────────┼─────────┼─────────┤
# │ CRUDEOIL    │ MCX      │ 5          │ CLOSE   │ 100     │
# │ NATURALGAS  │ MCX      │ 0.8        │ CLOSE   │ 1250    │
# └─────────────┴──────────┴────────────┴─────────┴─────────┘
#
# Database: tradebook_mcx.db
# Trading Hours: 09:05 - 23:00 IST
# Square-off: 23:15 IST
# ============================================

import os
import pandas as pd
from datetime import datetime, timedelta
from openalgo import api
import numpy as np
import time
import re
import ta
import threading
import math
import tulipy as ti
from scipy.signal import argrelextrema
from collections import deque
import glob
import queue
from collections import defaultdict
import logging
import sys
import shutil
import pdb
import signal
import atexit
from functools import wraps

from sqlalchemy import text
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from datetime import datetime
from zoneinfo import ZoneInfo
from datetime import date
import argparse, os, shutil, sqlite3
from datetime import datetime
# --- add near imports ---
from core.Controller import Controller
from config.Config import getBrokerAppConfig
from instruments.Instruments import Instruments
from ticker.Ticker import Ticker
# --- shared ticker imports
import queue
import threading
from utils.renkodf import Renko  # renkodf.py

from utils.Utils import Utils
from ohlcupdt.BaseOHLCUpdater import BaseOHLCUpdater
from utils.Utils import Utils
from utils.RenkoUtils import Renko
import ta
import numpy as np
import tulipy as ti
from config.Config import getServerConfig 
from ohlcupdt.BaseOHLCUpdater import BaseOHLCUpdater, RENKO_COL_ORDER
import utils.RenkoUtils as ru
from scipy.signal import argrelextrema
import tulipy as ti
import logging
import gc
import mplfinance as mpf
from ohlcupdt.OptionOHLCUpdater import OptionOHLCUpdater

# ============================================
# 🎯 MCX INSTRUMENT CONFIGURATION
# ============================================
MCX_INSTRUMENTS = {
    "CRUDEOIL": {
        "exchange": "MCX",
        "brick_size": 5,
        "renko_method": "CLOSE",
        "lot_size": 100
    },
    "NATURALGAS": {
        "exchange": "MCX",
        "brick_size": 0.8,
        "renko_method": "CLOSE",
        "lot_size": 1250
    }
}

def get_mcx_brick_size(symbol: str) -> float:
    """Get brick size for MCX symbol."""
    s = (symbol or "").upper().strip()
    if "CRUDEOIL" in s:
        return MCX_INSTRUMENTS["CRUDEOIL"]["brick_size"]
    elif "NATURALGAS" in s:
        return MCX_INSTRUMENTS["NATURALGAS"]["brick_size"]
    return 5  # Default

def get_mcx_lot_size(symbol: str) -> int:
    """Get lot size for MCX symbol."""
    s = (symbol or "").upper().strip()
    if "CRUDEOIL" in s:
        return MCX_INSTRUMENTS["CRUDEOIL"]["lot_size"]
    elif "NATURALGAS" in s:
        return MCX_INSTRUMENTS["NATURALGAS"]["lot_size"]
    return 1  # Default

def get_mcx_renko_method(symbol: str) -> str:
    """Get Renko method for MCX symbol."""
    s = (symbol or "").upper().strip()
    if "CRUDEOIL" in s:
        return MCX_INSTRUMENTS["CRUDEOIL"]["renko_method"]
    elif "NATURALGAS" in s:
        return MCX_INSTRUMENTS["NATURALGAS"]["renko_method"]
    return "CLOSE"  # Default


# ============================================
# 📝 PRODUCTION LOGGING SETUP
# ============================================
def setup_logging(log_to_file=True, log_level=logging.INFO):
    """
    Configure production-grade logging with file and console output.
    """
    # Create logs directory if it doesn't exist
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # Generate log filename with date
    log_filename = os.path.join(log_dir, f"mcx_trading_{datetime.now().strftime('%Y%m%d')}.log")
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Get root logger
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    # Clear existing handlers
    logger.handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (if enabled)
    if log_to_file:
        file_handler = logging.FileHandler(log_filename, mode='a')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        print(f"📝 Logging to: {log_filename}")
    
    return logger

# Initialize logger
_logger = setup_logging(log_to_file=True)

# ============================================
# 🛑 GRACEFUL SHUTDOWN HANDLING
# ============================================
_shutdown_flag = threading.Event()
_shutdown_lock = threading.Lock()

# Pre-declare thread safety locks (will be fully initialized later)
_pnl_lock = threading.Lock()
_loss_counter_lock = threading.Lock()
_cooldown_lock = threading.Lock()

# Pre-declare global counters (will be used by shutdown handler)
_daily_pnl_points = 0
_daily_trades = []
_consecutive_losses = 0
_reentry_cooldown_tracker = {}

# Morning pivots flag
MORNING_PIVOTS_DONE = False

# 🧠 SMART DIRECTION STABILITY TRACKER
# Tracks when direction last changed for each symbol
# Format: {symbol: {'direction': 'UP'/'DOWN', 'changed_at': timestamp}}
_direction_tracker = {}
_direction_lock = threading.Lock()

# 🚨 BRICK RATE MONITOR TRACKERS
# Tracks brick formation times and pauses for each symbol
# Format: {symbol: [list of (timestamp, direction) tuples]}
_brick_history = {}
# Format: {symbol: pause_until_timestamp}
_brick_rate_pause = {}
_brick_rate_lock = threading.Lock()

# 📊 FILTER LOGGING TRACKERS
# Tracks all signal decisions for analysis
_filter_log_lock = threading.Lock()
_filter_stats = {
    "date": None,
    "total_signals": 0,
    "allowed": 0,
    "blocked": 0,
    "blocked_by_chop": 0,
    "blocked_by_hybrid": 0,
    "blocked_by_min_net": 0,
    "blocked_by_signal_proximity": 0,
    "blocked_by_cooldown": 0,
    "blocked_by_brick_rate": 0,
    "winners": 0,
    "losers": 0,
}
_filter_log_data = []  # List of all logged signals today

# ============================================
# 📈 SIGNAL OUTCOME TRACKER - Track what WOULD have been profitable
# ============================================
# This tracks EVERY signal and simulates what would have happened
# to help identify which filters are helping vs hurting
#
# For each signal, we track:
#   - Entry price at signal time
#   - Max price reached after (MFE - Max Favorable Excursion)
#   - Min price reached after (MAE - Max Adverse Excursion)
#   - Whether it would have hit target (2 bricks)
#   - Whether it would have hit stop (2.5 bricks)
#   - Simulated P&L
#
# This data tells us:
#   - "Blocked signals that would have been profitable" = FILTER TOO STRICT
#   - "Taken signals that were losers" = FILTER NOT STRICT ENOUGH
#
ENABLE_OUTCOME_TRACKING = True           # Master switch
OUTCOME_TRACKING_WINDOW_MINUTES = 30     # Track price for 30 mins after signal
OUTCOME_TARGET_BRICKS = 2.0              # Target profit in bricks
OUTCOME_STOP_BRICKS = 2.5                # Stop loss in bricks
OUTCOME_LOG_DIR = "ohlcdata/outcomes"    # Directory for outcome logs

_outcome_tracker = {}  # {signal_id: {signal_data, prices_after, outcome}}
_outcome_tracker_lock = threading.Lock()

def generate_signal_id(symbol, signal_type, price, timestamp):
    """Generate unique ID for a signal."""
    ts_str = timestamp.strftime("%Y%m%d_%H%M%S") if hasattr(timestamp, 'strftime') else str(timestamp)
    return f"{symbol}_{signal_type}_{price}_{ts_str}"

def track_signal_for_outcome(symbol, signal_type, entry_price, brick_size, 
                              quality_score, decision, blocked_by, reason,
                              renko_pattern="", consecutive=0, net_bricks=0, actual_trend=""):
    """
    Register a signal for outcome tracking.
    Called for EVERY signal, whether taken or blocked.
    """
    if not ENABLE_OUTCOME_TRACKING:
        return
    
    now = pd.Timestamp.now()
    signal_id = generate_signal_id(symbol, signal_type, entry_price, now)
    
    signal_data = {
        'signal_id': signal_id,
        'timestamp': now,
        'symbol': symbol,
        'signal_type': signal_type,  # BUYCL or BUYPT
        'entry_price': entry_price,
        'brick_size': brick_size,
        'quality_score': quality_score,
        'decision': decision,         # ALLOWED or BLOCKED
        'blocked_by': blocked_by,     # Which filter blocked (or "-")
        'reason': reason,
        'renko_pattern': renko_pattern,
        'consecutive': consecutive,
        'net_bricks': net_bricks,
        'actual_trend': actual_trend,
        
        # Outcome tracking (filled in later)
        'prices_after': [],           # [(timestamp, price), ...]
        'max_favorable': 0,           # MFE in points
        'max_adverse': 0,             # MAE in points
        'mfe_bricks': 0,              # MFE in bricks
        'mae_bricks': 0,              # MAE in bricks
        'would_hit_target': False,    # Would it have reached target?
        'would_hit_stop': False,      # Would it have hit stop?
        'simulated_outcome': 'PENDING',  # WIN, LOSS, or PENDING
        'simulated_pnl_bricks': 0,    # Simulated P&L in bricks
        'tracking_complete': False,
    }
    
    with _outcome_tracker_lock:
        _outcome_tracker[signal_id] = signal_data
        
    # Log to console
    direction = "CALL" if signal_type == "BUYCL" else "PUT"
    status = "✅ TAKEN" if decision == "ALLOWED" else f"❌ BLOCKED ({blocked_by})"
    print(f"📝 TRACKING | {symbol} | {direction} @ {entry_price} | Score:{quality_score} | {status}")

def update_signal_outcomes(symbol, current_price):
    """
    Update outcome tracking for all pending signals of this symbol.
    Called whenever we get a new price tick.
    """
    if not ENABLE_OUTCOME_TRACKING:
        return
    
    now = pd.Timestamp.now()
    
    with _outcome_tracker_lock:
        for signal_id, data in _outcome_tracker.items():
            # Only update signals for this symbol that aren't complete
            if data['symbol'] != symbol or data['tracking_complete']:
                continue
            
            # Check if tracking window expired
            age_minutes = (now - data['timestamp']).total_seconds() / 60
            if age_minutes > OUTCOME_TRACKING_WINDOW_MINUTES:
                # Finalize this signal's outcome
                finalize_signal_outcome(signal_id)
                continue
            
            # Record price
            data['prices_after'].append((now, current_price))
            
            entry_price = data['entry_price']
            brick_size = data['brick_size']
            is_call = data['signal_type'] == 'BUYCL'
            
            # Calculate excursions
            if is_call:
                # CALL: profit when price goes UP
                favorable = current_price - entry_price
                adverse = entry_price - current_price
            else:
                # PUT: profit when price goes DOWN
                favorable = entry_price - current_price
                adverse = current_price - entry_price
            
            # Update max excursions
            if favorable > data['max_favorable']:
                data['max_favorable'] = favorable
                data['mfe_bricks'] = favorable / brick_size if brick_size > 0 else 0
            
            if adverse > data['max_adverse']:
                data['max_adverse'] = adverse
                data['mae_bricks'] = adverse / brick_size if brick_size > 0 else 0
            
            # Check if would hit target or stop
            if data['mfe_bricks'] >= OUTCOME_TARGET_BRICKS:
                data['would_hit_target'] = True
            if data['mae_bricks'] >= OUTCOME_STOP_BRICKS:
                data['would_hit_stop'] = True

def finalize_signal_outcome(signal_id):
    """
    Finalize the outcome of a signal after tracking window expires.
    Determines if it would have been a WIN or LOSS.
    """
    with _outcome_tracker_lock:
        if signal_id not in _outcome_tracker:
            return
        
        data = _outcome_tracker[signal_id]
        if data['tracking_complete']:
            return
        
        # Determine simulated outcome
        # Logic: Stop is checked FIRST (like real trading)
        # If stop would be hit before target, it's a LOSS
        # If target would be hit before stop, it's a WIN
        
        if data['would_hit_stop'] and not data['would_hit_target']:
            data['simulated_outcome'] = 'LOSS'
            data['simulated_pnl_bricks'] = -OUTCOME_STOP_BRICKS
        elif data['would_hit_target'] and not data['would_hit_stop']:
            data['simulated_outcome'] = 'WIN'
            data['simulated_pnl_bricks'] = OUTCOME_TARGET_BRICKS
        elif data['would_hit_target'] and data['would_hit_stop']:
            # Both would be hit - need to check which first
            # For now, assume stop hit first if MAE > MFE at any point
            # This is a simplification - real analysis needs tick-by-tick
            if data['mae_bricks'] >= OUTCOME_STOP_BRICKS:
                data['simulated_outcome'] = 'LOSS'
                data['simulated_pnl_bricks'] = -OUTCOME_STOP_BRICKS
            else:
                data['simulated_outcome'] = 'WIN'
                data['simulated_pnl_bricks'] = OUTCOME_TARGET_BRICKS
        else:
            # Neither hit - use MFE vs MAE
            if data['mfe_bricks'] > data['mae_bricks']:
                data['simulated_outcome'] = 'PARTIAL_WIN'
                data['simulated_pnl_bricks'] = data['mfe_bricks'] * 0.5  # Assume exit at half MFE
            else:
                data['simulated_outcome'] = 'PARTIAL_LOSS'
                data['simulated_pnl_bricks'] = -data['mae_bricks'] * 0.5
        
        data['tracking_complete'] = True

def get_outcome_summary():
    """Generate summary of signal outcomes for analysis."""
    with _outcome_tracker_lock:
        # Finalize all pending signals
        for signal_id in list(_outcome_tracker.keys()):
            finalize_signal_outcome(signal_id)
        
        # Categorize signals
        allowed_signals = [s for s in _outcome_tracker.values() if s['decision'] == 'ALLOWED']
        blocked_signals = [s for s in _outcome_tracker.values() if s['decision'] == 'BLOCKED']
        
        # Calculate stats for allowed signals
        allowed_wins = [s for s in allowed_signals if s['simulated_outcome'] in ['WIN', 'PARTIAL_WIN']]
        allowed_losses = [s for s in allowed_signals if s['simulated_outcome'] in ['LOSS', 'PARTIAL_LOSS']]
        
        # Calculate stats for blocked signals - THE KEY INSIGHT
        blocked_would_win = [s for s in blocked_signals if s['simulated_outcome'] in ['WIN', 'PARTIAL_WIN']]
        blocked_would_lose = [s for s in blocked_signals if s['simulated_outcome'] in ['LOSS', 'PARTIAL_LOSS']]
        
        summary = {
            'total_signals': len(_outcome_tracker),
            'allowed_count': len(allowed_signals),
            'blocked_count': len(blocked_signals),
            
            # Allowed signal outcomes
            'allowed_wins': len(allowed_wins),
            'allowed_losses': len(allowed_losses),
            'allowed_win_rate': len(allowed_wins) / len(allowed_signals) * 100 if allowed_signals else 0,
            'allowed_total_pnl': sum(s['simulated_pnl_bricks'] for s in allowed_signals),
            
            # Blocked signal outcomes - CRITICAL DATA
            'blocked_would_win': len(blocked_would_win),
            'blocked_would_lose': len(blocked_would_lose),
            'blocked_win_rate': len(blocked_would_win) / len(blocked_signals) * 100 if blocked_signals else 0,
            'blocked_missed_pnl': sum(s['simulated_pnl_bricks'] for s in blocked_would_win),  # Profit we missed!
            'blocked_avoided_loss': sum(abs(s['simulated_pnl_bricks']) for s in blocked_would_lose),  # Loss we avoided
            
            # By blocker analysis
            'blocked_by_analysis': {},
        }
        
        # Analyze each blocker
        blockers = set(s['blocked_by'] for s in blocked_signals if s['blocked_by'] != '-')
        for blocker in blockers:
            blocker_signals = [s for s in blocked_signals if s['blocked_by'] == blocker]
            blocker_wins = [s for s in blocker_signals if s['simulated_outcome'] in ['WIN', 'PARTIAL_WIN']]
            blocker_losses = [s for s in blocker_signals if s['simulated_outcome'] in ['LOSS', 'PARTIAL_LOSS']]
            
            summary['blocked_by_analysis'][blocker] = {
                'count': len(blocker_signals),
                'would_win': len(blocker_wins),
                'would_lose': len(blocker_losses),
                'accuracy': len(blocker_losses) / len(blocker_signals) * 100 if blocker_signals else 0,  # % correctly blocked
                'missed_profit': sum(s['simulated_pnl_bricks'] for s in blocker_wins),
                'avoided_loss': sum(abs(s['simulated_pnl_bricks']) for s in blocker_losses),
            }
        
        return summary

def save_outcome_report():
    """Save detailed outcome report to CSV for analysis."""
    if not ENABLE_OUTCOME_TRACKING:
        return
    
    os.makedirs(OUTCOME_LOG_DIR, exist_ok=True)
    
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    
    # Save detailed signal log
    with _outcome_tracker_lock:
        if not _outcome_tracker:
            print("📊 No signals to report")
            return
        
        # Convert to DataFrame
        records = []
        for signal_id, data in _outcome_tracker.items():
            finalize_signal_outcome(signal_id)
            records.append({
                'timestamp': data['timestamp'],
                'symbol': data['symbol'],
                'signal_type': data['signal_type'],
                'entry_price': data['entry_price'],
                'brick_size': data['brick_size'],
                'quality_score': data['quality_score'],
                'decision': data['decision'],
                'blocked_by': data['blocked_by'],
                'consecutive': data['consecutive'],
                'net_bricks': data['net_bricks'],
                'actual_trend': data['actual_trend'],
                'mfe_bricks': round(data['mfe_bricks'], 2),
                'mae_bricks': round(data['mae_bricks'], 2),
                'would_hit_target': data['would_hit_target'],
                'would_hit_stop': data['would_hit_stop'],
                'simulated_outcome': data['simulated_outcome'],
                'simulated_pnl_bricks': round(data['simulated_pnl_bricks'], 2),
            })
        
        df = pd.DataFrame(records)
        csv_path = os.path.join(OUTCOME_LOG_DIR, f"signal_outcomes_{today}.csv")
        df.to_csv(csv_path, index=False)
        print(f"📊 Signal outcomes saved to {csv_path}")
        
        # Generate and print summary
        summary = get_outcome_summary()
        
        print("\n" + "=" * 70)
        print("📊 SIGNAL OUTCOME ANALYSIS")
        print("=" * 70)
        print(f"\n📈 TOTAL SIGNALS: {summary['total_signals']}")
        print(f"   Allowed: {summary['allowed_count']} | Blocked: {summary['blocked_count']}")
        
        print(f"\n✅ ALLOWED SIGNALS ({summary['allowed_count']}):")
        print(f"   Wins: {summary['allowed_wins']} | Losses: {summary['allowed_losses']}")
        print(f"   Win Rate: {summary['allowed_win_rate']:.1f}%")
        print(f"   Total P&L: {summary['allowed_total_pnl']:+.1f} bricks")
        
        print(f"\n❌ BLOCKED SIGNALS ({summary['blocked_count']}):")
        print(f"   Would have won: {summary['blocked_would_win']} | Would have lost: {summary['blocked_would_lose']}")
        print(f"   (Blocked) Win Rate: {summary['blocked_win_rate']:.1f}%")
        print(f"   💰 Missed Profit: {summary['blocked_missed_pnl']:+.1f} bricks")
        print(f"   🛡️ Avoided Loss: {summary['blocked_avoided_loss']:.1f} bricks")
        
        net_filter_value = summary['blocked_avoided_loss'] - abs(summary['blocked_missed_pnl'])
        print(f"\n🎯 FILTER NET VALUE: {net_filter_value:+.1f} bricks")
        if net_filter_value > 0:
            print("   ✅ Filters are HELPING (avoiding more loss than missing profit)")
        else:
            print("   ❌ Filters are HURTING (missing more profit than avoiding loss)")
        
        print(f"\n📊 ANALYSIS BY BLOCKER:")
        for blocker, stats in summary['blocked_by_analysis'].items():
            print(f"\n   {blocker}:")
            print(f"      Blocked: {stats['count']} signals")
            print(f"      Would Win: {stats['would_win']} | Would Lose: {stats['would_lose']}")
            print(f"      Accuracy: {stats['accuracy']:.1f}% (higher = better blocker)")
            print(f"      Missed: {stats['missed_profit']:+.1f} | Avoided: {stats['avoided_loss']:.1f}")
            
            blocker_value = stats['avoided_loss'] - abs(stats['missed_profit'])
            if blocker_value > 0:
                print(f"      ✅ This filter HELPS: {blocker_value:+.1f} bricks")
            else:
                print(f"      ❌ This filter HURTS: {blocker_value:+.1f} bricks - CONSIDER DISABLING")
        
        print("\n" + "=" * 70)
        
        # Save summary to file
        summary_path = os.path.join(OUTCOME_LOG_DIR, f"outcome_summary_{today}.txt")
        with open(summary_path, 'w') as f:
            f.write(f"Signal Outcome Analysis - {today}\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Total Signals: {summary['total_signals']}\n")
            f.write(f"Allowed: {summary['allowed_count']} | Blocked: {summary['blocked_count']}\n\n")
            f.write(f"Allowed Win Rate: {summary['allowed_win_rate']:.1f}%\n")
            f.write(f"Allowed P&L: {summary['allowed_total_pnl']:+.1f} bricks\n\n")
            f.write(f"Blocked Win Rate: {summary['blocked_win_rate']:.1f}%\n")
            f.write(f"Missed Profit: {summary['blocked_missed_pnl']:+.1f} bricks\n")
            f.write(f"Avoided Loss: {summary['blocked_avoided_loss']:.1f} bricks\n\n")
            f.write(f"FILTER NET VALUE: {net_filter_value:+.1f} bricks\n")
        
        print(f"📊 Summary saved to {summary_path}")

# 🔄 SIGNAL DEDUPLICATION TRACKER
# Prevents same signal from being processed repeatedly (Jan 29 bug: 245 identical SELEX)
# Format: {symbol: {'signal': 'BUYEN/SELEX', 'price': float, 'timestamp': datetime, 'processed': bool}}
_processed_signals = {}
_processed_signals_lock = threading.Lock()
SIGNAL_DEDUP_MINUTES = 5  # Consider same signal at same price as duplicate for 5 mins

# 🛡️ ORDER RETRY TRACKING (Feb 1 fix - prevents infinite rejection loops)
# Format: {(symbol, action, entry_price): {'count': int, 'first_attempt': datetime}}
_order_retry_tracker = {}
_order_retry_lock = threading.Lock()
MAX_ORDER_RETRIES = 3  # Max attempts before cancelling order
ORDER_RETRY_WINDOW_MINUTES = 10  # Reset retry count after this window

def is_duplicate_signal(symbol, signal_type, price, brick_size):
    """
    Check if this signal was already processed recently.
    Prevents the same signal from being processed 245 times (Jan 29 bug).
    
    Returns:
        (is_duplicate: bool, reason: str)
    """
    global _processed_signals
    
    now = pd.Timestamp.now()
    key = f"{symbol}_{signal_type}"
    
    with _processed_signals_lock:
        if key in _processed_signals:
            last = _processed_signals[key]
            age_minutes = (now - last['timestamp']).total_seconds() / 60
            price_diff = abs(price - last['price'])
            
            # Same signal at same price (within 1 brick) within 5 minutes = duplicate
            if age_minutes < SIGNAL_DEDUP_MINUTES and price_diff <= brick_size:
                return True, f"Duplicate: same {signal_type} at {price} ({age_minutes:.1f} mins ago)"
        
        # Update tracker
        _processed_signals[key] = {
            'signal': signal_type,
            'price': price,
            'timestamp': now,
            'processed': True
        }
        return False, "New signal"

def clear_processed_signal(symbol, signal_type):
    """Clear a processed signal (call when position is opened/closed)."""
    global _processed_signals
    key = f"{symbol}_{signal_type}"
    with _processed_signals_lock:
        if key in _processed_signals:
            del _processed_signals[key]

def check_and_increment_retry(symbol, action, entry_price):
    """
    Check retry count for an order and increment it.
    Returns: (can_retry: bool, retry_count: int, reason: str)
    
    Feb 1 fix: Prevents infinite rejection loops when orders keep failing
    (e.g., insufficient funds causing 47 SENSEX orders)
    """
    global _order_retry_tracker
    now = pd.Timestamp.now()
    key = (symbol, action, entry_price)
    
    with _order_retry_lock:
        if key in _order_retry_tracker:
            tracker = _order_retry_tracker[key]
            age_minutes = (now - tracker['first_attempt']).total_seconds() / 60
            
            # Reset if outside window
            if age_minutes > ORDER_RETRY_WINDOW_MINUTES:
                _order_retry_tracker[key] = {'count': 1, 'first_attempt': now}
                return True, 1, "Window reset"
            
            # Increment count
            tracker['count'] += 1
            
            if tracker['count'] > MAX_ORDER_RETRIES:
                return False, tracker['count'], f"Exceeded max retries ({MAX_ORDER_RETRIES}) in {age_minutes:.1f} mins"
            
            return True, tracker['count'], f"Retry #{tracker['count']}"
        else:
            # First attempt
            _order_retry_tracker[key] = {'count': 1, 'first_attempt': now}
            return True, 1, "First attempt"

def clear_retry_tracker(symbol, action, entry_price):
    """Clear retry tracker when order succeeds or is closed."""
    global _order_retry_tracker
    key = (symbol, action, entry_price)
    with _order_retry_lock:
        if key in _order_retry_tracker:
            del _order_retry_tracker[key]
    # 🛡️ FEB 1 FIX: Also clear pending order tracking
    clear_pending_order(symbol, action, entry_price)

def graceful_shutdown(signum=None, frame=None):
    """Handle graceful shutdown on SIGINT/SIGTERM."""
    with _shutdown_lock:
        if _shutdown_flag.is_set():
            return  # Already shutting down
        _shutdown_flag.set()
    
    signal_name = signal.Signals(signum).name if signum else "MANUAL"
    _logger.warning(f"🛑 Shutdown signal received ({signal_name}). Cleaning up...")
    
    try:
        # Log final P&L
        with _pnl_lock:
            final_pnl = _daily_pnl_points
            trade_count = len(_daily_trades)
        _logger.info(f"📊 Final Daily P&L: {final_pnl:+.0f} pts ({trade_count} trades)")
        
        # 📊 Save filter statistics
        try:
            save_filter_log_to_csv()
            save_filter_summary()
            print_filter_stats()
        except Exception as fe:
            _logger.warning(f"⚠️ Error saving filter stats: {fe}")
        
        # 📈 Save signal outcome analysis - THE KEY DATA!
        try:
            print("\n" + "=" * 70)
            print("📈 SAVING SIGNAL OUTCOME ANALYSIS...")
            print("=" * 70)
            save_outcome_report()
        except Exception as oe:
            _logger.warning(f"⚠️ Error saving outcome report: {oe}")
        
        # Close any open positions (safety)
        _logger.info("🔄 Checking for open positions...")
        
        # Stop ticker if running
        global _ticker
        if '_ticker' in globals() and _ticker is not None:
            try:
                _ticker.stop()
                _logger.info("✅ Ticker stopped")
            except:
                pass
        
        _logger.info("✅ Shutdown complete. Goodbye!")
        
    except Exception as e:
        _logger.error(f"❌ Error during shutdown: {e}")
    
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)
atexit.register(lambda: _logger.info("🔚 Process exiting..."))

def is_shutdown_requested():
    """Check if shutdown has been requested."""
    return _shutdown_flag.is_set()

# ============================================
# 🔄 API RETRY DECORATOR
# ============================================
def retry_on_failure(max_retries=3, delay=1, backoff=2, exceptions=(Exception,)):
    """
    Decorator to retry API calls on failure with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries (seconds)
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch and retry
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        _logger.warning(f"⚠️ {func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}")
                        _logger.info(f"🔄 Retrying in {current_delay:.1f}s...")
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        _logger.error(f"❌ {func.__name__} failed after {max_retries + 1} attempts: {e}")
            
            raise last_exception
        return wrapper
    return decorator

# ============================================
# ✅ STARTUP VALIDATION
# ============================================
def validate_configuration():
    """
    Validate all configuration parameters at startup.
    Returns (is_valid: bool, errors: list)
    """
    errors = []
    warnings = []
    
    # Check brick sizes
    # MCX Only instruments
    required_instruments = ["CRUDEOIL", "NATURALGAS"]
    
    # Check direction filter settings
    if ENABLE_DIRECTION_FILTER:
        if DIRECTION_LOOKBACK < 5:
            warnings.append(f"DIRECTION_LOOKBACK={DIRECTION_LOOKBACK} is very low, recommend >= 5")
        if MIN_NET_BRICKS < 1:
            errors.append(f"MIN_NET_BRICKS={MIN_NET_BRICKS} must be >= 1")
    
    # Check daily loss limit
    if ENABLE_DAILY_LOSS_LIMIT:
        if MAX_DAILY_LOSS_POINTS <= 0:
            errors.append(f"MAX_DAILY_LOSS_POINTS={MAX_DAILY_LOSS_POINTS} must be > 0")
    
    # Check lot size
    if FIXED_NUM_LOTS is not None and FIXED_NUM_LOTS <= 0:
        errors.append(f"FIXED_NUM_LOTS={FIXED_NUM_LOTS} must be > 0 or None")
    
    # Log results
    if errors:
        for e in errors:
            _logger.error(f"❌ CONFIG ERROR: {e}")
    
    if warnings:
        for w in warnings:
            _logger.warning(f"⚠️ CONFIG WARNING: {w}")
    
    if not errors:
        _logger.info("✅ Configuration validation passed")
    
    return len(errors) == 0, errors

# ============================================
# 💓 HEALTH MONITORING
# ============================================
class HealthMonitor:
    """Monitor system health and connectivity."""
    
    def __init__(self):
        self.last_tick_time = None
        self.last_api_success = None
        self.consecutive_api_failures = 0
        self.lock = threading.Lock()
    
    def record_tick(self):
        """Record that we received a tick."""
        with self.lock:
            self.last_tick_time = datetime.now()
    
    def record_api_success(self):
        """Record successful API call."""
        with self.lock:
            self.last_api_success = datetime.now()
            self.consecutive_api_failures = 0
    
    def record_api_failure(self):
        """Record failed API call."""
        with self.lock:
            self.consecutive_api_failures += 1
            if self.consecutive_api_failures >= 5:
                _logger.error(f"🚨 ALERT: {self.consecutive_api_failures} consecutive API failures!")
    
    def is_healthy(self, tick_timeout_seconds=60):
        """Check if system is healthy."""
        with self.lock:
            # Check tick freshness
            if self.last_tick_time:
                tick_age = (datetime.now() - self.last_tick_time).total_seconds()
                if tick_age > tick_timeout_seconds:
                    _logger.warning(f"⚠️ No ticks for {tick_age:.0f}s (timeout: {tick_timeout_seconds}s)")
                    return False
            
            # Check API health
            if self.consecutive_api_failures >= 5:
                return False
            
            return True
    
    def get_status(self):
        """Get health status summary."""
        with self.lock:
            tick_age = None
            if self.last_tick_time:
                tick_age = (datetime.now() - self.last_tick_time).total_seconds()
            
            return {
                "last_tick_age_seconds": tick_age,
                "consecutive_api_failures": self.consecutive_api_failures,
                "is_healthy": self.is_healthy()
            }

# Initialize health monitor
_health_monitor = HealthMonitor()

# Change this to test different levels: False, True, 1, 2
VERBOSE_LEVEL = False

# Add these at module level
_stale_check_cache = {}
_STALE_CACHE_TIMEOUT = 60  # seconds

pd.set_option('display.max_columns', None)   # show all columns
pd.set_option('display.width', None)         # don't wrap based on width
pd.set_option('display.max_colwidth', None)  # show full text in cells

# Low-latency LTP cache & event queue
ltp_dict: dict[str, float] = {}          # if you already have one, keep that
ltp_timestamps: dict[str, float] = {}    # Track when each LTP was last updated
LTP_STALE_THRESHOLD = 10.0               # Seconds before LTP is considered stale (default)
ltp_event_queue: "queue.Queue[str]" = queue.Queue(maxsize=10000)

# 🛡️ FEB 1 FIX: Thread-safe locks for shared data structures
_ltp_lock = threading.Lock()             # Lock for ltp_dict and ltp_timestamps
_csv_lock = threading.Lock()             # Lock for CSV file read/write operations

# 🛡️ FEB 1 FIX: Pending order tracking (prevents duplicate orders)
# Format: {(symbol, action, entry_price): {'order_id': str, 'timestamp': datetime, 'status': str}}
_pending_orders = {}
_pending_orders_lock = threading.Lock()
PENDING_ORDER_TIMEOUT = 60  # Seconds before a pending order is considered stale

def get_ltp_stale_threshold(symbol: str) -> float:
    """
    Get symbol-specific LTP staleness threshold.
    Different instruments update at different frequencies.
    
    FEB 1 FIX: Previously used same threshold for all symbols.
    """
    u = (symbol or "").upper()
    
    # High-frequency index symbols - need fresh data
    if u in ("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"):
        return 5.0  # 5 seconds for pure index
    
    # Index options - slightly more tolerance
    if any(u.startswith(idx) for idx in ("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX")):
        return 10.0  # 10 seconds for index options
    
    # MCX commodities - update less frequently
    if any(x in u for x in ("CRUDE", "GOLD", "SILVER", "NATURAL")):
        return 30.0  # 30 seconds for commodities
    
    # MCX options
    if u.endswith(("CE", "PE")) and any(x in u for x in ("CRUDE", "GOLD", "SILVER")):
        return 45.0  # 45 seconds for commodity options
    
    # Default
    return 15.0

def safe_ltp_update(symbol: str, ltp: float, timestamp: float = None):
    """Thread-safe LTP update. FEB 1 FIX for race condition."""
    global ltp_dict, ltp_timestamps
    if timestamp is None:
        timestamp = time.time()
    with _ltp_lock:
        ltp_dict[symbol] = ltp
        ltp_timestamps[symbol] = timestamp

def safe_ltp_get(symbol: str) -> tuple:
    """Thread-safe LTP get. Returns (ltp, timestamp) or (None, 0)."""
    with _ltp_lock:
        ltp = ltp_dict.get(symbol)
        ts = ltp_timestamps.get(symbol, 0)
        return ltp, ts

def track_pending_order(symbol: str, action: str, entry_price: float, order_id: str):
    """Track a newly placed order to prevent duplicates."""
    key = (symbol, action, entry_price)
    with _pending_orders_lock:
        _pending_orders[key] = {
            'order_id': order_id,
            'timestamp': time.time(),
            'status': 'PENDING'
        }

def check_pending_order(symbol: str, action: str, entry_price: float) -> tuple:
    """
    Check if there's already a pending order for this signal.
    Returns: (has_pending: bool, order_id: str or None, reason: str)
    """
    key = (symbol, action, entry_price)
    with _pending_orders_lock:
        if key in _pending_orders:
            order_info = _pending_orders[key]
            age = time.time() - order_info['timestamp']
            
            # If order is old, consider it stale and allow new order
            if age > PENDING_ORDER_TIMEOUT:
                del _pending_orders[key]
                return False, None, "Previous order timed out"
            
            return True, order_info['order_id'], f"Order {order_info['order_id']} pending ({age:.1f}s old)"
        
        return False, None, "No pending order"

def clear_pending_order(symbol: str, action: str, entry_price: float):
    """Clear a pending order when it completes or fails."""
    key = (symbol, action, entry_price)
    with _pending_orders_lock:
        if key in _pending_orders:
            del _pending_orders[key]

# Shared ticker singleton
_ticker: Ticker | None = None
UPDATES = [
    # MCX Only - CRUDEOIL and NATURALGAS
    ("CRUDEOIL CE/PE → 100", """
        UPDATE symtoken
        SET lotsize = 100
        WHERE symbol LIKE '%CRUDEOIL%'
          AND (symbol LIKE '%CE' OR symbol LIKE '%PE');
    """),
    ("NATURALGAS CE/PE → 1250", """
        UPDATE symtoken
        SET lotsize = 1250
        WHERE symbol LIKE '%NATURALGAS%'
          AND (symbol LIKE '%CE' OR symbol LIKE '%PE');
    """),
]

# --- SELEX trailing rule ---
SELEX_TRAIL_AFTER_BRICKS = 7    # when profit ≥ this many bricks at SELEX…
SELEX_TRAIL_OFFSET_BRICKS = 1   # …move stop to (SELEX brick - this many bricks)

# --- Trailing-stop config (longs) ---
TRAIL_ENABLE_AFTER_BRICKS = 2     # start trailing once price ≥ entry + 2 bricks
TRAIL_OFFSET_BRICKS = 1           # trail stop = current up-brick - 1 brick

# ============================================
# 🎯 V5 UPGRADE: TRAILING STOP LOSS
# ============================================
# Moves stop loss as trade moves in your favor:
# - At +1 brick profit: Move stop to BREAKEVEN (entry price)
# - At +2 brick profit: Start trailing (stop = current - 1 brick)
# - Never moves stop backwards (only tightens)
#
# Benefits:
# - Locks in profits as trade moves
# - Prevents giving back gains on reversals
# - Reduces average loss size
# ============================================
ENABLE_TRAILING_STOP = True       # Master switch for trailing stop
TRAIL_BREAKEVEN_AFTER = 1.5       # 🆕 OPTIMIZED: Move to breakeven at 1.5 bricks
TRAIL_START_AFTER = 1.5           # 🆕 OPTIMIZED: Start trailing at 1.5 bricks profit
TRAIL_DISTANCE = 0.5              # 🆕 OPTIMIZED: Trail 0.5 brick behind (tight!)
# DATA: This config = 85.5% win rate, 6,064 pts vs 57.3%, 3,505 baseline!

# ============================================
# 🧠 SMART TIERED TRAILING STOP CONFIGURATION
# ============================================
# Adapts trail distance based on profit level - gives room early, locks profit later
ENABLE_SMART_TRAILING = False     # 🆕 DISABLED - Simple trailing is better!

# Tiered trail distances (not used when ENABLE_SMART_TRAILING = False)
SMART_TRAIL_TIERS = [
    (1.5, 0.5),  # Start tight immediately
    (3, 0.5),    # Stay tight
    (5, 0.5),    # Stay tight
]

# ============================================
# 🎯 ENTRY OFFSET - Wait for pullback/bounce before entry
# ============================================
# For CALL: Wait for price to drop (entry_price - offset × brick)
# For PUT:  Wait for price to bounce (entry_price + offset × brick)
#
# Values:
#   0.8 = Conservative (fewer trades, better entries)
#   0.5 = Balanced (recommended)
#   0.3 = Aggressive (more trades, riskier entries)
# ============================================
ENTRY_OFFSET_BRICKS = 0     # Enter at signal price (0 = no offset, immediate entry)

# ============================================
# 🛑 STOP LOSS - Initial stop loss distance
# ============================================
# Distance in bricks from entry price for initial stop loss
# CALL: Stop at entry - STOP_LOSS_BRICKS × brick_size
# PUT:  Stop at entry + STOP_LOSS_BRICKS × brick_size
# ============================================
STOP_LOSS_BRICKS = 4.0      # 🆕 OPTIMIZED: Wider stop = room to breathe
# DATA: 4.0 stop + tight trail = 85.5% win rate, 6,064 pts!

# 🔧 Global switch to enable/disable daily square-off entirely
ENABLE_SQUARE_OFF = True   # Set to True to enable, False to disable all square-off logic
# --- Global square-off kill switch (session-wide) ---
SQUARE_OFF_ACTIVE = True  # When True, we do not subscribe, update OHLC/RENKO, or select new symbols.
#simulation mode
# --- Simulation / Order confirmation control ---
SKIP_ORDER_CONFIRMATION = False   # Set to False for real trading

# ============================================
# 🎯 TRADE BIAS FILTER - Controls which direction trades are allowed
# ============================================
# Options:
#   "CALL" - Only CALL entries allowed (BUYCL), PUT entries blocked
#   "PUT"  - Only PUT entries allowed (BUYPT), CALL entries blocked
#   "BOTH" - Both directions allowed (no filter)
#   None   - Same as "BOTH", no filter applied
# ============================================
TRADE_BIAS = "BOTH"  # Change to "CALL" or "PUT" to filter trades

# ============================================
# 🎯 ADX TREND FILTER - Controls minimum trend strength for entries
# ============================================
ADX_THRESHOLD = 25
ADX_PERIOD = 14
ENABLE_ADX_FILTER = False  # Disabled - doesn't work with Renko

# ============================================
# 🎯 V5 MINIMAL FILTERS - AVOID WHIPSAW
# ============================================
# 
# PROBLEM: Trades hitting stop loss in 1-2 minutes
# CAUSE: Market is choppy, direction changes rapidly
#
# SOLUTION: Stricter filters

# ============================================
# 📈 DIRECTION FILTER - Requires STRONG trend
# ============================================
# ⚠️ DATA ANALYSIS (Feb 1): Hurts P&L for Close Renko!
# With Trend Only: 56.0% win but P&L drops from 3507 to 1327
# Close Renko already handles noise - no additional filter needed
#
ENABLE_DIRECTION_FILTER = False   # ❌ DISABLED - Close Renko doesn't need it!

# DIRECTION FILTER MODE:
#   "SMART"  = HYBRID check: Last 2-3 bricks + net direction must agree
#              If last 2 UP but net DOWN → CHOPPY (blocked)
#              Also requires MIN_NET_BRICKS strength (e.g., net ≥ +2 for CALL)
#   "STRICT" = Need MIN_NET_BRICKS over LOOKBACK (SLOWER, fewer trades)
DIRECTION_FILTER_MODE = "SMART"  # Not used when disabled

# Settings for direction filter (used in BOTH modes):
DIRECTION_LOOKBACK = 10  # Not used when disabled
MIN_NET_BRICKS = 2       # Not used when disabled

# ============================================
# 🚫 SIGNAL PROXIMITY FILTER - Avoid entering after opposite signal
# ============================================
# ⚠️ DATA ANALYSIS (Feb 1): Hurts P&L for Close Renko!
# Immediate Match filter: 53.6% win, P&L drops to 601 (from 3507)
#
ENABLE_SIGNAL_PROXIMITY_FILTER = False  # ❌ DISABLED - Close Renko doesn't need it!
MIN_BRICKS_SINCE_OPPOSITE_SIGNAL = 3  # Not used when disabled
SIGNAL_PROXIMITY_STRENGTH_OVERRIDE = 4  # Not used when disabled

# ============================================
# 🔄 EXIT ON OPPOSITE SIGNAL - True Renko Way
# ============================================
# When holding a position, if the OPPOSITE signal appears → EXIT immediately
# Don't wait for stop loss - trust the Renko signal!
#
# Example: Holding CALL, SELEX appears → EXIT (don't wait for stop)
# Example: Holding PUT, BUYEN appears → EXIT (don't wait for stop)
#
# Benefits:
# - Smaller losses (exit at ~2 bricks instead of 2.5 bricks stop)
# - Follows actual Renko signals
# - True Renko trading methodology
#
ENABLE_EXIT_ON_OPPOSITE_SIGNAL = False  # ❌ DISABLED - Exit when opposite signal appears

# ============================================
# 🔄 CHOP DETECTOR - Avoid whipsaw markets
# ============================================
# Counts direction REVERSALS in last N bricks
# If too many reversals → market is CHOPPY → DON'T TRADE
#
# Example: ↑↓↓↑↓↑↓ = 5 reversals in 7 bricks = CHOPPY!
# Example: ↑↑↑↓↓↓ = 1 reversal in 6 bricks = TRENDING!
#
# ❌ DISABLED - Redundant with SIGNAL_PROXIMITY filter
# SIGNAL_PROXIMITY catches the same scenarios by checking opposite signal distance
#
ENABLE_CHOP_DETECTOR = False  # ❌ DISABLED - SIGNAL_PROXIMITY is better
CHOP_LOOKBACK = 6           # Check last 6 bricks
MAX_REVERSALS = 2           # BALANCED: Allow some chop, block extreme (was 1)

# ============================================
# 🚨 BRICK RATE MONITOR - PROACTIVE Chop Detection
# ============================================
# Detects SUDDEN chop by monitoring brick formation rate.
# If alternating bricks form rapidly → Market is choppy → PAUSE trading
#
# This is PROACTIVE - catches chop BEFORE signals generate!
#
# Logic:
#   - Track timestamps when each brick forms
#   - If 3+ alternating bricks (↑↓↑ or ↓↑↓) form in 5 mins → CHOP DETECTED
#   - PAUSE trading for that symbol for 10 mins
#   - After pause, resume normally
#
# Example:
#   12:30:00 → UP brick
#   12:31:30 → DOWN brick (1st alternation)
#   12:33:00 → UP brick (2nd alternation)
#   12:34:00 → DOWN brick (3rd alternation) ← 4 bricks, 3 alternations in 4 mins!
#   → CHOP DETECTED! Trading paused until 12:44:00
#
ENABLE_BRICK_RATE_MONITOR = False  # ❌ DISABLED - Redundant with Chop Detector, 10-min pause too long
BRICK_RATE_WINDOW_MINUTES = 5      # Check bricks formed in last 5 minutes
BRICK_RATE_MIN_ALTERNATIONS = 3    # Minimum alternating bricks to trigger pause
BRICK_RATE_PAUSE_MINUTES = 10      # Pause trading for 10 minutes after chop detected

# ============================================
# 🧠 DIRECTION STABILITY - Prevent rapid flip-flop entries
# ============================================
# PROBLEM: Direction can flip from UP to DOWN in 3 minutes!
#          Current filters only check WHAT the direction is,
#          not HOW LONG it's been that direction.
#
# SOLUTION: Require direction to be STABLE for X minutes
#          before allowing entry in that direction.
#
# Example with 3-minute stability:
#   12:36:17 → Direction is UP for 5+ mins → CALL allowed ✅
#   12:39:17 → Direction flips to DOWN
#   12:39:17 → PUT signal generated
#   12:39:17 → ❌ BLOCKED: Direction only DOWN for 0 mins (need 3 mins)
#   12:42:17 → Direction still DOWN for 3 mins → PUT allowed ✅
#
ENABLE_DIRECTION_STABILITY = False  # ❌ DISABLED - Rely on HYBRID check instead
DIRECTION_STABLE_MINUTES = 3  # Not used when disabled

# ============================================
# ⏰ TIME FILTER - Block worst hour only
# ============================================
# ✅ DATA ANALYSIS (Feb 1): This is the ONLY filter that helps!
# Blocking 13:00-14:00: 57.4% win (vs 55.9% baseline), P&L 3497 (vs 3507)
# 13:00-14:00 alone: 45.7% win rate = WORST hour
#
ENABLE_TIME_FILTER = False  # ❌ DISABLED for MCX - different market hours

# Blocked trading periods (IST):
# ONLY block the WORST hour (lunch time low volume chop)
TIME_FILTER_BLOCKED_PERIODS = []  # MCX has different characteristics - no blocked periods

# Allowed trading hours (IST)
TRADING_START_TIME = "09:05"  # MCX opens at 9:05 AM
TRADING_END_TIME = "23:00"    # MCX closes at 11:30 PM, stop new trades at 11:00 PM

# Direction filter rules:
#   Net move >= +2 bricks  → TRENDING UP   → CALL only
#   Net move <= -2 bricks  → TRENDING DOWN → PUT only
#   Net move between -2 and +2 → CHOPPY   → Block both (wait)

# ============================================
# 💰 DAILY LOSS LIMIT - Keep as safety net
# ============================================
ENABLE_DAILY_LOSS_LIMIT = True
MAX_DAILY_LOSS_POINTS = 500  # Stop after losing 500 points

# ============================================
# ❌ ALL OTHER FILTERS - DISABLED (Close Renko doesn't need them)
# ============================================
# ⚠️ DATA ANALYSIS (Feb 1): Close Renko already filters noise!
# Adding more filters REDUCES P&L. Only time filter helps.
#
ENABLE_CHOP_FILTER = False        # ❌ Data shows it hurts P&L
ENABLE_REENTRY_COOLDOWN = True    # ✅ ENABLED - Wait after stop loss before re-entry!
# ENABLE_TIME_FILTER already set above
ENABLE_LOSS_PROTECTION = False    # Not needed - daily limit is enough
ENABLE_REGIME_FILTER = False      # ❌ Data shows it hurts P&L

# ============================================
# 📊 FILTER LOGGING - Track blocked vs allowed signals
# ============================================
# Records every signal decision for analysis:
# - Which filter blocked each signal
# - Daily summary stats
# - Helps tune filter parameters based on DATA
#
ENABLE_FILTER_LOGGING = True      # Master switch for filter logging
FILTER_LOG_DIR = "ohlcdata"       # Directory for filter logs
FILTER_LOG_TO_CONSOLE = True      # Print filter decisions to console
FILTER_LOG_VERBOSE = False        # Show detailed filter checks (noisy!)

# ============================================
# 🎯 QUALITY SCORING SYSTEM - Smart Signal Selection
# ============================================
# Instead of cascading blockers, we SCORE each signal (0-100)
# and only take HIGH QUALITY signals.
#
# Components:
#   - TREND STRENGTH (0-25): Consecutive bricks in same direction
#   - TREND ALIGNMENT (0-30): Signal matches dominant trend
#   - MOMENTUM (0-20): Recent bricks support signal direction
#   - BREAKOUT (0-15): Breaking recent highs/lows
#   - CLEAN MARKET (0-10): Few reversals = cleaner trend
#
# Benefits:
#   - Takes MORE trades that are HIGH PROBABILITY
#   - Blocks weak signals regardless of which filter would catch them
#   - Combines all factors into single quality metric
#
# ⚠️ DATA ANALYSIS (Feb 1): Close Renko already filters noise!
# Adding quality scoring REDUCES P&L from 3507 to ~2000 pts
# Close Renko = 55.9% win rate WITHOUT any filters
#
ENABLE_QUALITY_SCORING = False    # ❌ DISABLED - Close Renko doesn't need it!
QUALITY_SCORE_THRESHOLD = 70      # Not used when disabled
QUALITY_SCORE_MEDIUM = 60         # Not used when disabled
QUALITY_SCORE_LOG_ALL = False     # Not used when disabled

# Component weights for tuning (should sum to 100)
QS_WEIGHT_TREND_STRENGTH = 25     # Clean consecutive runs
QS_WEIGHT_TREND_ALIGNMENT = 30    # Signal matches dominant trend
QS_WEIGHT_MOMENTUM = 20           # Recent brick direction
QS_WEIGHT_BREAKOUT = 15           # Breaking recent high/low
QS_WEIGHT_CLEAN_MARKET = 10       # Low reversal count

# Lookback periods for quality scoring
QS_DOMINANT_TREND_LOOKBACK = 20   # Bricks for dominant trend calculation
QS_MOMENTUM_LOOKBACK = 5          # Bricks for momentum check
QS_BREAKOUT_LOOKBACK = 8          # Bricks for breakout detection
QS_CLEAN_MARKET_LOOKBACK = 10     # Bricks for reversal count

# Legacy settings (not used but kept for compatibility)
MIN_RUN_LENGTH_FOR_ENTRY = 2
MAX_SHORT_RUNS_PERCENT = 70
CHOP_LOOKBACK_RUNS = 10
AVG_RUN_LENGTH_THRESHOLD = 2.0
REENTRY_COOLDOWN_MINUTES = 5      # ✅ Wait 5 minutes after stop loss before re-entry
MARKET_OPEN_BUFFER_MINUTES = 0
MARKET_CLOSE_BUFFER_MINUTES = 0
NEW_TRADE_CUTOFF_HOUR = 23        # ✅ No new trades after 11:00 PM (23:00) for MCX
MAX_CONSECUTIVE_LOSSES = 99
MIN_TREND_STRENGTH = 0.50
REGIME_LOOKBACK_BRICKS = 10

# NOTE: Global counters and thread locks are declared earlier (after imports)
# _consecutive_losses, _daily_pnl_points, _daily_trades, _reentry_cooldown_tracker
# _pnl_lock, _loss_counter_lock, _cooldown_lock

# ============================================
# ⏰ PER-EXCHANGE TRADING TIMES CONFIGURATION (EARLY DEFINITION)
# ============================================
# These must be defined BEFORE check_time_filter() function
# MCX: 9:05 AM to 11:00 PM (new trades), Square-off at 11:15 PM
# NFO (NSE_INDEX): 9:20 AM to 3:00 PM (new trades), Square-off at 3:15 PM
# BFO (BSE_INDEX): 9:20 AM to 3:00 PM (new trades), Square-off at 3:15 PM
# ============================================

# Trading START times (when new entries are allowed)
# MCX-ONLY Trading Times
TRADING_START_TIMES = {
    "MCX": (9, 5),           # 9:05 AM - MCX starts
}

# Trading END times (no new entries after this, but existing positions managed)
# MCX-ONLY Trading End Times
TRADING_END_TIMES = {
    "MCX": (23, 0),          # 11:00 PM - No new MCX entries
}

FILTER_PATTERNS = [
    r"\] INSE_INDEX in",    # Filters any line containing '] INSE_INDEX in'
    r"'high': ",      # Filters lines with 'high':
    r" High: ",       # Filters lines with ' High: '
    r" Data successfully saved to ", 
    r"Positions book is empty",
    r"LTP NSE_INDEX ",
    r"\[DB\] Appended ",
    r"\[DB\] Upserted",
    r" into symbols_to_trade",
    r"🟡🟡 SELST ",
    r"🔴🔴💸",
    r"Using Renko file",
    r"🟢🟢🚀 BUYEN ",
    r"\[lotsize\] DB ",
    r" 117 trade_manager row",
    r"update_trade_manager_with_new_signals complete",
    r"Next data fetch scheduled",
    r"Next OHLC monitor check scheduled at",
    r"Updated brick_size for",
    r"ATR-based brick",
    r"\[normalize",
    r"Correcting DB lot",
    r"LTP MCX",
    r"LTP NSE_INDEX",
    r"Subscribing to",
    r"Subscription response",
    r"Subscribed to",
    r"Empty or None positions_book_df"
]
ENABLE_0920_SLEEP = False  # Disabled for MCX - starts at 9:05 AM
ENABLE_0916_SLEEP = False
ENABLE_0925_SLEEP = False

def sleep_until_0916_15_ist():
    """
    Sleep until exactly 9:16:15 AM IST, then return.
    Only works if ENABLE_0920_SLEEP is True.
    
    Note: Only sleeps if current time is between 00:00:00 and 9:16:15 IST.
          If called after 9:16:15 IST, it will not sleep (won't block for tomorrow).
    """
    if not ENABLE_0916_SLEEP:
        print("⏰ 9:16:15 AM sleep timer is disabled")
        return
    
    from datetime import datetime
    from zoneinfo import ZoneInfo
    
    # IST timezone
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    
    # Target time today at 9:16:15 AM
    target = now.replace(hour=9, minute=16, second=15, microsecond=0)
    
    # Only sleep if current time is before 9:16:15 today
    if now < target:
        sleep_seconds = (target - now).total_seconds()
        
        if sleep_seconds > 0:
            sleep_hours = int(sleep_seconds // 3600)
            sleep_minutes = int((sleep_seconds % 3600) // 60)
            sleep_sec = int(sleep_seconds % 60)
            
            print(
                f"⏰ Sleeping until {target.strftime('%H:%M:%S')} IST "
                f"({sleep_hours:02d}:{sleep_minutes:02d}:{sleep_sec:02d} from now)"
            )
            time.sleep(sleep_seconds)
            print("✅ Woke up at 9:16:15 AM IST, starting trading engine...")
    else:
        print(f"⏰ Already past 9:16:15 IST ({now.strftime('%H:%M:%S')}). No sleep needed.")

def sleep_until_0925_08_ist():
    """
    Sleep until exactly 9:25:08 AM IST, then return.
    Only works if ENABLE_0925_SLEEP is True.
    
    Note: Only sleeps if current time is between 00:00:00 and 9:25:08 IST.
          If called after 9:25:08 IST, it will not sleep (won't block for tomorrow).
    """
    if not ENABLE_0925_SLEEP:
        print("⏰ 9:25:08 AM sleep timer is disabled")
        return
    
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    
    # IST timezone
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    
    # Target time today at 9:25:08 AM
    target = now.replace(hour=9, minute=25, second=8, microsecond=0)
    
    # Only sleep if current time is before 9:25:08 today
    if now < target:
        # Calculate sleep duration
        sleep_seconds = (target - now).total_seconds()
        
        if sleep_seconds > 0:
            sleep_hours = int(sleep_seconds // 3600)
            sleep_minutes = int((sleep_seconds % 3600) // 60)
            sleep_sec = int(sleep_seconds % 60)
            
            print(f"⏰ Sleeping until {target.strftime('%H:%M:%S')} IST "
                  f"({sleep_hours:02d}:{sleep_minutes:02d}:{sleep_sec:02d} from now)")
            time.sleep(sleep_seconds)
            print("✅ Woke up at 9:25:08 AM IST, starting trading engine...")
    else:
        print(f"⏰ Already past 9:25:08 IST ({now.strftime('%H:%M:%S')}). No sleep needed.")
        
        
def sleep_until_0920_15_ist():
    """
    Sleep until exactly 9:20:15 AM IST, then return.
    Only works if ENABLE_0920_SLEEP is True.
    
    Note: Only sleeps if current time is between 00:00:00 and 9:20:15 IST.
          If called after 9:20:15 IST, it will not sleep (won't block for tomorrow).
    """
    if not ENABLE_0920_SLEEP:
        print("⏰ 9:20:15 AM sleep timer is disabled")
        return
    
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    
    # IST timezone
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    
    # Target time today at 9:20:15 AM
    target = now.replace(hour=9, minute=20, second=15, microsecond=0)
    
    # Only sleep if current time is before 9:20:15 today
    if now < target:
        # Calculate sleep duration
        sleep_seconds = (target - now).total_seconds()
        
        if sleep_seconds > 0:
            sleep_hours = int(sleep_seconds // 3600)
            sleep_minutes = int((sleep_seconds % 3600) // 60)
            sleep_sec = int(sleep_seconds % 60)
            
            print(f"⏰ Sleeping until {target.strftime('%H:%M:%S')} IST "
                  f"({sleep_hours:02d}:{sleep_minutes:02d}:{sleep_sec:02d} from now)")
            time.sleep(sleep_seconds)
            print("✅ Woke up at 9:20:15 AM IST, starting trading engine...")
    else:
        print(f"⏰ Already past 9:20:15 IST ({now.strftime('%H:%M:%S')}). No sleep needed.")


def smart_startup_sleep():
    """
    Sleep until the appropriate start time based on instruments being traded.
    - If MCX instruments are enabled: Start at 9:05 AM
    - If only NFO/BFO instruments: Start at 9:20 AM
    
    This function checks the symbols_to_trade.csv to determine which exchanges
    are being traded and adjusts the startup time accordingly.
    """
    if not ENABLE_0920_SLEEP:
        print("⏰ Startup sleep timer is disabled")
        return
        
    from zoneinfo import ZoneInfo
    
    # Check which instruments will be traded
    has_mcx = False
    has_nfo_bfo = False
    
    try:
        # Try to read existing symbols file to determine what's being traded
        if os.path.exists(symbols_file):
            symbols_df = pd.read_csv(symbols_file)
            if symbols_df is not None and not symbols_df.empty and 'exchange' in symbols_df.columns:
                exchanges = symbols_df['exchange'].str.upper().unique().tolist()
                has_mcx = 'MCX' in exchanges
                has_nfo_bfo = 'NSE_INDEX' in exchanges or 'BSE_INDEX' in exchanges
    except Exception as e:
        print(f"⚠️ Could not read symbols file for startup sleep: {e}")
        # Default to NFO/BFO timing
        has_nfo_bfo = True
    
    # If no file exists yet, check what will be generated
    if not has_mcx and not has_nfo_bfo:
        # Default to NFO/BFO timing
        has_nfo_bfo = True
    
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    
    # Determine target start time
    if has_mcx:
        # MCX starts at 9:05 AM
        target = now.replace(hour=9, minute=5, second=0, microsecond=0)
        target_name = "9:05:00 AM (MCX start)"
    else:
        # NFO/BFO starts at 9:20 AM
        target = now.replace(hour=9, minute=20, second=15, microsecond=0)
        target_name = "9:20:15 AM (NFO/BFO start)"
    
    if now < target:
        sleep_seconds = (target - now).total_seconds()
        if sleep_seconds > 0:
            hours = int(sleep_seconds // 3600)
            mins = int((sleep_seconds % 3600) // 60)
            secs = int(sleep_seconds % 60)
            print(f"⏰ Sleeping until {target_name} IST ({hours:02d}:{mins:02d}:{secs:02d} from now)")
            if has_mcx:
                print(f"   📊 MCX detected - starting early at 9:05 AM")
            time.sleep(sleep_seconds)
            print(f"✅ Woke up at {target_name} IST, starting trading engine...")
    else:
        print(f"⏰ Already past {target_name} ({now.strftime('%H:%M:%S')}). Starting immediately.")

        
class TeeLogger(object):
    def __init__(self, filename):
        import os
        os.makedirs("log", exist_ok=True)  # Ensure log directory exists
        self.terminal = sys.stdout
        # Use utf-8-sig to include BOM so Windows Notepad recognizes UTF-8
        self.log = open(filename, "a", encoding="utf-8-sig")
        self._buffer = ""
        self._lock = threading.RLock()

    def write(self, message):
        with self._lock:
            # ----- Console (terminal) write -----
            enc = getattr(self.terminal, "encoding", None) or "utf-8"
            try:
                # Try normal write first
                self.terminal.write(message)
            except UnicodeEncodeError:
                # If terminal encoding can't handle emojis, replace unencodable chars
                safe = message.encode(enc, errors="replace").decode(enc, errors="replace")
                try:
                    self.terminal.write(safe)
                except Exception:
                    pass  # skip console output if all else fails
            except Exception:
                pass  # catch-all to prevent thread crash on console write

            # ----- Log file (always UTF-8) -----
            self._buffer += message
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                line = line + "\n"
                if not any(re.search(pattern, line) for pattern in FILTER_PATTERNS):
                    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.log.write(f"{timestamp_str} {line}")

    def flush(self):
        """Ensure both console and log outputs are flushed."""
        with self._lock:
            try:
                self.terminal.flush()
            except Exception:
                pass
            try:
                self.log.flush()
                os.fsync(self.log.fileno())  # force flush to disk immediately
            except Exception:
                pass


try:
    script_name = os.path.splitext(os.path.basename(__file__))[0]
except NameError:
    script_name = "interactive"
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
log_filename = f"log/{script_name}_{timestamp}.txt" 

sys.stdout = sys.stderr = TeeLogger(log_filename)

subscribed_symbols = set()
_subscribed_symbols_lock = threading.Lock()  # 🛡️ FEB 1 FIX: Lock for thread-safe set operations

def safe_subscribe_add(symbol: str):
    """Thread-safe add to subscribed_symbols."""
    with _subscribed_symbols_lock:
        subscribed_symbols.add(symbol)

def safe_subscribe_remove(symbol: str):
    """Thread-safe remove from subscribed_symbols."""
    with _subscribed_symbols_lock:
        subscribed_symbols.discard(symbol)

def safe_subscribe_check(symbol: str) -> bool:
    """Thread-safe check if symbol is subscribed."""
    with _subscribed_symbols_lock:
        return symbol in subscribed_symbols

websocket_active = threading.Event()
# Note: ltp_dict, ltp_event_queue, and _ticker are already defined earlier (lines 767-773)
# DO NOT redefine them here to avoid data loss

# ltp_dict = {}
# ltp_event_queue = queue.Queue()
symbol_locks = defaultdict(threading.Lock)  # Note: threading.Lock (callable), NOT threading.Lock() (instance)

csv_file_lock = threading.Lock()
OHLC_UPDATE_TIMEOUT_SECONDS = 70  # for alert if no update in last 5:20
last_ohlc_update = defaultdict(lambda: datetime.now())
INVESTMENT_PERCENT = None  # <-- Change as needed
PF_PCT_INVESTMENT = True
ALLOWED_SLIPPAGE = 3.0  # <--- Add this
EXIT_ALLOWED_SLIPPAGE = 4.0  # <--- Add this
# ---- NEW: run once / manual-entry controls ----
ONE_AND_DONE = False                  # stop engine after first successful exit (SELEX/BUYEX)
AUTO_ENTRY_ENABLED = True           # ignore entries/stops; engine only acts on EXIT signals
ALLOW_SELEX_FOR_MANUAL_POSITION = False  # allow SELEX even if BUYEN wasn't placed by this engine
TRADING_ENABLED = True               # flipped to False internally after the first exit


SELEX_PROBE_SECONDS = 120  # how long to "try" conditional exit
_selex_pending = {}       # (EX,SYM) -> {"t0": float, "deadline": float, "entry": float}
_selex_lock = threading.RLock()

#Initial stop loss bricks
INITIAL_STOP_BRICKS = 2

# --- Profit gating for exits ---
MIN_PROFIT_BRICKS = 1  # require at least 4 bricks from entry before SELEX is allowed
# --- Breakeven rule ---
BREAKEVEN_AFTER_BRICKS = 2

# ---- NEW: global lot switch ----
# If set to an integer, engine always trades exactly this many lots for entries.
# Set to None to use PF_PCT_INVESTMENT or *_LOT_MULT fallbacks.
FIXED_NUM_LOTS = 1  # global fixed lot switch; set to None to disable
PORTFOLIO_STOP_LOSS_PCT = 10  # Portfolio stop loss (as percent of account balance)

# --- Zone gating config ---
NIFTY_ZONE_BAND = 24  # points around HL/LL/HH/LH to enforce CE/PE-only entries

FUT_LOT_MULT = 1 
OPT_LOT_MULT = 1

LONG_ONLY = True

BRICK_SIZE = 5  # MCX Default (CRUDEOIL brick size)
LTP_THRESHOLD = 100 # You can change this value as needed

# MCX-ONLY LTP THRESHOLDS

# CRUDEOIL
CRUDE_LTP_MIN = 1
CRUDE_LTP_MAX = 1000

# NATURAL GAS
NATGAS_LTP_MIN = 0
NATGAS_LTP_MAX = 500
symbols_file = "ohlcdata/symbols_to_trade.csv"
trade_manager_file = "ohlcdata/trade_manager.csv"


os.environ['DATABASE_URL'] = 'sqlite:///db/openalgo.db'
from database.symbol import enhanced_search_symbols

# NEW: dedicated DB for the trade book
os.environ['TRADE_DB_URL'] = os.environ.get('TRADE_DB_URL', 'sqlite:///db/tradebook_mcx.db')

# Load API key from environment variable
api_key = os.getenv("OPENALGO_API_KEY")
#api_key = '6669a6373c2191301143f2cdad4cb75709104917a5119227dc9b47503fc77267'
if not api_key:
    raise ValueError("Missing API key. Set OPENALGO_API_KEY as an environment variable.")

# Set the strategy details and trading parameters
strategy = "MCX Renko Python"
#exchange = "NSE_INDEX"
product = "NRML"
# quantity = 900

# Supertrend indicator inputs
atr_period = 14
atr_multiplier = 1.0

# sleep_until_0925_08_ist()
# sleep_until_0920_15_ist()  # Original single-time sleep
smart_startup_sleep()  # ⏰ NEW: Smart sleep based on MCX vs NFO/BFO instruments
# sleep_until_0916_15_ist()
# Set the API Key
client = api(api_key=api_key, host='http://127.0.0.1:5000', ws_url='ws://127.0.0.1:8765', verbose=VERBOSE_LEVEL)

def get_lotsize_on_exchange(exchange, symbol):
    """
    Get lot size for a symbol on a specific exchange.
    """
    # Map exchange for lot size lookup
    if symbol.upper().endswith(("CE", "PE")):
        if exchange == "NSE_INDEX":
            query_exchange = "NFO"
        elif exchange == "BSE_INDEX":
            query_exchange = "BFO"
        elif exchange == "MCX":
            query_exchange = "MCX"
        else:
            query_exchange = exchange
    else:
        query_exchange = exchange
    
    return get_lotsize(query_exchange, symbol) or 1

def normalize_exchange(ex: str | None) -> str:
    if not ex:
        return "MCX"  # Default to MCX for this bot
    e = str(ex).strip().upper()
    if e.startswith("MCX"):
        return "MCX"
    # Keep other mappings for compatibility but MCX is primary
    if e in {"NSE_INDEX", "NSE", "NSE-CASH", "NSE_SPOT"}:
        return "NSE_INDEX"
    if e in {"BSE_INDEX", "BSE", "BSE-CASH", "BSE_SPOT"}:
        return "BSE_INDEX"
    if e in {"NFO", "NSE-DERIV", "NSE_FUTOPT"}:
        return "NFO"
    if e in {"BFO", "BSE-DERIV", "BSE_FUTOPT"}:
        return "BFO"
    return e

def get_lotsize(exchange: str, symbol: str) -> int:
    """
    Resolve lot size using (1) normalized exchange/symbol, (2) DB lookup,
    then (3) safe fallbacks. Adds guardrails for common mismatches.
    """
    try:
        ex = normalize_exchange(exchange)
        sym = str(symbol).upper()
        
        # MCX lot sizes
        if "CRUDEOIL" in sym:
            print(f"[lotsize] Using default CRUDEOIL lot size: 100")
            return 100
        elif "NATURALGAS" in sym:
            print(f"[lotsize] Using default NATURALGAS lot size: 1250")
            return 1250
        
        # For other symbols, try DB lookup
        try:
            results = enhanced_search_symbols(sym, ex)
            if results and getattr(results[0], "lotsize", None):
                size = int(results[0].lotsize)
                print(f"[lotsize] DB {ex}:{sym} → {size}")
                return size    
        except Exception as db_err:
            print(f"[lotsize] DB lookup failed for {ex}:{sym} → {db_err}")

        # Unknown exchange → minimal safe fallback
        print(f"[lotsize] Unknown exchange {ex} for {sym} → fallback 1")
        return 1

    except Exception as e:
        print(f"[lotsize] Error for {exchange}:{symbol} → {e}")
        return 1
    
# def get_lotsize(exchange: str, symbol: str) -> int:
#     """
#     Resolve lot size using (1) normalized exchange/symbol, (2) DB lookup,
#     then (3) safe fallbacks. Adds guardrails for common mismatches.
#     """
#     try:
#         ex = normalize_exchange(exchange)
#         sym = str(symbol).upper()
#         # ex = (exchange or "").upper().strip()
#         sym = (symbol or "").upper().strip()

#         ex = normalize_exchange(exchange)
#         sym = str(symbol).upper()
#         try:
#             results = enhanced_search_symbols(sym, ex)
#             if results and getattr(results[0], "lotsize", None):
#                 size = int(results[0].lotsize)
#                 # ✅ Correct known mis-entry for GOLDM
#                 print(f"[lotsize] DB {ex}:{sym} → {size}")
#                 return size    
#         except Exception as db_err:
#             print(f"[lotsize] DB lookup failed for {ex}:{sym} → {db_err}")

#         # Unknown exchange → minimal safe fallback
#         print(f"[lotsize] Unknown exchange {ex} for {sym} → fallback 1")

#     except Exception as e:
#         print(f"[lotsize] Error for {exchange}:{symbol} → {e}")

# Create engine once (uses your existing DATABASE_URL)
_engine = create_engine(os.environ.get("TRADE_DB_URL", "sqlite:///db/tradebook.db"), future=True, poolclass=NullPool, pool_pre_ping=True, connect_args={"check_same_thread": False})
@event.listens_for(_engine, "connect")
def _sqlite_pragmas(dbapi_conn, connection_record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")     # better read/write concurrency
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA busy_timeout=5000;")    # wait for locks instead of failing fast
    cur.close()
_csv_file_lock = csv_file_lock  # reuse your existing lock

# Map a filename to (table_name, key_column, key_value)
# For OHLC/RENKO we scope rows by a stable "file_key" so each "virtual CSV"
# remains isolated inside its table.
_file_patterns = [
    # FUT first so it doesn’t collide with non-FUT
    (re.compile(r"^ohlcdata/(?P<key>.+)_FUT_ohlc\.csv$"), "ohlc_fut"),
    (re.compile(r"^ohlcdata/(?P<key>.+)_FUT_renko\.csv$"), "renko_fut"),
    (re.compile(r"^ohlcdata/(?P<key>.+)_ohlc\.csv$"), "ohlc"),
    (re.compile(r"^ohlcdata/(?P<key>.+)_renko\.csv$"), "renko"),
]

_whole_table = {
    "ohlcdata/symbols_to_trade.csv": "symbols_to_trade",
    "ohlcdata/trade_manager.csv": "trade_manager",
}

from sqlalchemy import inspect

_SQLITE_TYPE_FOR_DTYPE = {
    "float": "REAL",
    "int": "INTEGER",
    "bool": "INTEGER",
    "datetime": "TEXT",
    "object": "TEXT",
}


def concat_trade_row(df, new_row_dict):
    """
    Concatenate a new row to trade_manager DataFrame without FutureWarning.
    Handles empty/NA columns properly by suppressing the deprecation warning.
    """
    import warnings
    new_df = pd.DataFrame([new_row_dict])
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning, 
                                message=".*empty or all-NA entries.*")
        return pd.concat([df, new_df], ignore_index=True)


def add_trade(entry_price, renko_signal, symbol, exchange, timestamp,
              exec_price=None, quantity=None, order_status="OPEN", orderid=None):
    """
    Insert a new trade into trade_manager or update it if it already exists.
    Also ensure the symbol is saved into symbols_to_trade.csv.
    """
    row = {
        "exchange": exchange,
        "timestamp": pd.to_datetime(timestamp).strftime("%Y-%m-%d %H:%M:%S"),
        "renko_signal": renko_signal,
        "symbol": symbol,
        "entry_price": entry_price,
        "exec_price": exec_price,
        "quantity": quantity,
        "order_status": order_status,
        "orderid": orderid,
    }

    # --- Save to trade_manager ---
    cols = list(row.keys())
    conflict_key = ("symbol", "exchange", "renko_signal", "entry_price", "timestamp")
    update_cols = [c for c in cols if c not in conflict_key]

    cols_sql = ", ".join(cols)
    vals_sql = ", ".join([f":{c}" for c in cols])
    update_sql = ", ".join([f"{c}=excluded.{c}" for c in update_cols])

    sql = f"""
    INSERT INTO trade_manager ({cols_sql}) VALUES ({vals_sql})
    ON CONFLICT(symbol, exchange, renko_signal, entry_price, timestamp)
    DO UPDATE SET {update_sql};
    """
    with _engine.begin() as cn:
        cn.execute(text(sql), row)

    print(f"✅ Trade upserted for {symbol} [{renko_signal}]")

    # --- Ensure symbol is copied into symbols_to_trade.csv ---
    try:
        sym_df = read_csv(symbols_file)
        new_row = pd.DataFrame([{"exchange": exchange, "symbol": symbol, "brick_size": brick_for_runtime(symbol)}])
        if sym_df is None or sym_df.empty:
            save_to_csv(new_row, symbols_file)
        else:
            merged = (pd.concat([sym_df, new_row], ignore_index=True)
                      .drop_duplicates(subset=["symbol"]))
            save_to_csv(merged, symbols_file)
        print(f"📌 {symbol} added to {symbols_file}")
    except Exception as e:
        print(f"⚠️ Could not update symbols_to_trade for {symbol}: {e}")

def update_trade(orderid, **fields):
    """
    Update an existing trade by orderid with given fields.
    Example: update_trade("12345", order_status="CLOSED", exec_price=101.25)
    """
    if not fields:
        print("No fields provided to update.")
        return
    sets = ", ".join([f"{k} = :{k}" for k in fields.keys()])
    fields["orderid"] = orderid
    with _engine.begin() as cn:
        cn.execute(text(f"UPDATE trade_manager SET {sets} WHERE orderid = :orderid"), fields)
    print(f"✏️ Trade {orderid} updated with {fields}")

# # ==== Example usage ====

# # Add a trade
# add_trade(
#     exchange="NSE_INDEX",
#     timestamp=pd.Timestamp.now(),
#     renko_signal="BUYEN",
#     symbol="NIFTY14AUG2524450CE",
#     entry_price=110.05,
#     quantity=75
# )

# # Update an existing trade
# update_trade("123456789", order_status="CLOSED", exec_price=112.5)


def _migrate_trade_manager(cn):
    # Ensure table exists
    cn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS trade_manager (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange TEXT,
        timestamp TEXT,
        renko_signal TEXT,
        symbol TEXT,
        entry_price REAL,
        limit_entry_price REAL,
        exec_price REAL,
        quantity REAL,
        order_status TEXT,
        orderid TEXT,
        close_reason TEXT
    );
    """)
    # Add id if legacy schema lacked it
    cols = cn.exec_driver_sql("PRAGMA table_info(trade_manager)").fetchall()
    colnames = {r[1] for r in cols}
    if "id" not in colnames:
        cn.exec_driver_sql("ALTER TABLE trade_manager RENAME TO trade_manager__old;")
        cn.exec_driver_sql("""
        CREATE TABLE trade_manager (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT,
            timestamp TEXT,
            renko_signal TEXT,
            symbol TEXT,
            entry_price REAL,
            limit_entry_price REAL,
            exec_price REAL,
            quantity REAL,
            order_status TEXT,
            orderid TEXT,
            close_reason TEXT
        );
        """)
        cn.exec_driver_sql("""
        INSERT INTO trade_manager
        (exchange,timestamp,renko_signal,symbol,entry_price,exec_price,quantity,order_status,orderid)
        SELECT exchange,timestamp,renko_signal,symbol,entry_price,exec_price,quantity,order_status,orderid
        FROM trade_manager__old;
        """)
        cn.exec_driver_sql("DROP TABLE trade_manager__old;")
    # Add new columns if they don't exist (for existing databases)
    if "limit_entry_price" not in colnames:
        cn.exec_driver_sql("ALTER TABLE trade_manager ADD COLUMN limit_entry_price REAL;")
    if "close_reason" not in colnames:
        cn.exec_driver_sql("ALTER TABLE trade_manager ADD COLUMN close_reason TEXT;")
    # Unique index on orderid when present (lets us upsert by orderid)
    cn.exec_driver_sql("""
    CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_manager_orderid
    ON trade_manager(orderid) WHERE orderid IS NOT NULL AND orderid <> '';
    """)
    # Helpful index
    cn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_trade_manager_ts ON trade_manager(timestamp);")

def trade_manager_append(rows):
    """
    Append one or multiple entries to trade_manager *without* deleting existing rows.
    Accepts:
      - dict for a single row
      - list[dict]
      - pandas.DataFrame
    """
    import pandas as pd
    if isinstance(rows, dict):
        df = pd.DataFrame([rows])
    elif isinstance(rows, list) and (len(rows) == 0 or isinstance(rows[0], dict)):
        df = pd.DataFrame(rows)
    else:
        df = rows.copy()

    # Normalize timestamp to string
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

    with _engine.begin() as cn:
        _migrate_trade_manager(cn)
        _sync_table_schema(cn, "trade_manager", df)  # keeps any new custom columns
        # Pure append
        df.to_sql("trade_manager", con=cn, if_exists="append", index=False)
        print(f"🧾 [DB] Appended {len(df)} row(s) into trade_manager")

def trade_manager_update(orderid: str, **fields):
    """
    Update a trade_manager row by broker orderid.
    Example: trade_manager_update('FT12345', order_status='FILLED', exec_price=123.45)
    """
    if not orderid:
        print("⚠️ orderid is required for updates")
        return
    if not fields:
        print("ℹ️ No fields to update")
        return
    sets = ", ".join([f"{k} = :{k}" for k in fields.keys()])
    params = dict(fields)
    params["orderid"] = orderid
    with _engine.begin() as cn:
        _migrate_trade_manager(cn)
        cn.execute(text(f"UPDATE trade_manager SET {sets} WHERE orderid = :orderid"), params)
        print(f"✏️ [DB] Updated trade_manager where orderid={orderid}")
        
def _sanitize_table_name(name: str) -> str:
    # SQLite-friendly: letters, digits, underscore only; lowercased
    import re
    return re.sub(r'[^a-zA-Z0-9_]', '_', name).lower()

# Patterns to detect symbol from the "virtual CSV" filename
# Examples we saw in your logs:
#   ohlcdata/NIFTY14AUG2524450CE_ohlc.csv
#   ohlcdata/NIFTY14AUG2524450CE_renko.csv
#   ohlcdata/NIFTY14AUG2524600PE_ohlc.csv
#   ohlcdata/NIFTY14AUG2524600PE_renko.csv
# Also support FUT:
#   ohlcdata/<SYMBOL>_FUT_ohlc.csv
#   ohlcdata/<SYMBOL>_FUT_renko.csv
_rx_specs = [
    (re.compile(r"^ohlcdata/(?P<sym>.+)_FUT_ohlc\.csv$"),      lambda s: f"ohlc_fut_{s}"),
    (re.compile(r"^ohlcdata/(?P<sym>.+)_FUT_renko\.csv$"),     lambda s: f"renko_fut_{s}"),
    (re.compile(r"^ohlcdata/(?P<sym>.+)_ohlc\.csv$"),          lambda s: f"ohlc_{s}"),
    (re.compile(r"^ohlcdata/(?P<sym>.+)_renko\.csv$"),         lambda s: f"renko_{s}"),
]

_whole_table_fixed = {
    "ohlcdata/symbols_to_trade.csv": "symbols_to_trade",
    "ohlcdata/trade_manager.csv": "trade_manager",
}


def _resolve_table(file_name: str):
    # Normalize path for matching on any OS and any absolute/relative form
    fpath = os.path.normpath(str(file_name))
    base = os.path.basename(fpath).lower()

    # Fixed, non-symbol tables (match by basename only)
    if base == "symbols_to_trade.csv":
        return {"table": "symbols_to_trade", "mode": "whole"}
    if base == "trade_manager.csv":
        return {"table": "trade_manager", "mode": "whole"}

    # Symbol-scoped tables -> each symbol has its own physical table
    # normalize to forward slashes for regex
    fpath_fwd = fpath.replace("\\", "/")

    for rx, builder in _rx_specs:
        m = rx.match(fpath_fwd)
        if m:
            sym = m.group("sym")
            return {"table": _sanitize_table_name(builder(sym)), "mode": "whole"}

    # Fallback: derive a unique per-file table
    base_sanitized = _sanitize_table_name(base)
    return {"table": base_sanitized, "mode": "whole"}

from functools import wraps

_PANDAS_READ_CSV = pd.read_csv
_PANDAS_TO_CSV = pd.DataFrame.to_csv

def _is_special_csv(path):
    try:
        fpath = os.path.normpath(str(path))
    except Exception:
        return False
    base = os.path.basename(fpath).lower()
    return base in ("symbols_to_trade.csv", "trade_manager.csv")

def _read_csv_interceptor(func):
    @wraps(func)
    def wrapper(filepath_or_buffer, *args, **kwargs):
        if _is_special_csv(filepath_or_buffer):
            # Redirect to DB-backed read
            return read_csv(filepath_or_buffer)
        return func(filepath_or_buffer, *args, **kwargs)
    return wrapper

def _to_csv_interceptor(method):
    @wraps(method)
    def wrapper(self, path_or_buf=None, *args, **kwargs):
        if _is_special_csv(path_or_buf):
            # Redirect to DB-backed save (full replace semantics)
            save_to_csv(self, path_or_buf)
            return  # suppress actual file write
        return method(self, path_or_buf, *args, **kwargs)
    return wrapper

pd.read_csv = _read_csv_interceptor(_PANDAS_READ_CSV)
pd.DataFrame.to_csv = _to_csv_interceptor(_PANDAS_TO_CSV)

def _dtype_to_sqlite(dtype) -> str:
    if str(dtype).startswith(("float", "Float")):
        return _SQLITE_TYPE_FOR_DTYPE["float"]
    if str(dtype).startswith(("int", "Int")):
        return _SQLITE_TYPE_FOR_DTYPE["int"]
    if str(dtype).startswith(("bool", "Bool")):
        return _SQLITE_TYPE_FOR_DTYPE["bool"]
    if "datetime64" in str(dtype) or "datetimetz" in str(dtype):
        return _SQLITE_TYPE_FOR_DTYPE["datetime"]
    return _SQLITE_TYPE_FOR_DTYPE["object"]

def _column_exists(cn, table: str, column: str) -> bool:
    res = cn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    existing = {r[1] for r in res}  # name is index 1
    return column in existing


            
            


def _ensure_table_and_index(table: str):
    # decide by prefix
    with _engine.begin() as cn:
        if table.startswith("ohlc_fut_") or table.startswith("ohlc_"):
            # flexible OHLC layout; no unique constraint so evolving data doesn't break
            cn.exec_driver_sql(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    timestamp TEXT,
                    open REAL, high REAL, low REAL, close REAL,
                    atr REAL
                );
            """)
            cn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table}(timestamp);")

        elif table.startswith("renko_fut_") or table.startswith("renko_"):
            # allow duplicate timestamps; stable identity via autoincrement id
            cn.exec_driver_sql(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    Renko_Brick REAL,
                    zlema9 REAL,
                    HH REAL, HL REAL, LL REAL, LH REAL,
                    Signal TEXT,
                    Last_High REAL, Last_Low REAL
                );
            """)
            cn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table}(timestamp);")

        elif table == "symbols_to_trade":
            cn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS symbols_to_trade (
                    exchange TEXT,
                    symbol TEXT,
                    brick_size REAL
                );
            """)
        elif table == "trade_manager":
            cn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS trade_manager (
                    exchange TEXT,
                    timestamp TEXT,
                    renko_signal TEXT,
                    symbol TEXT,
                    entry_price REAL,
                    limit_entry_price REAL,
                    exec_price REAL,
                    quantity REAL,
                    order_status TEXT,
                    orderid TEXT,
                    close_reason TEXT
                );
            """)


        
        
def _table_has_pk_on_filekey_timestamp(cn, table: str) -> bool:
    # Returns True if (file_key, timestamp) is a composite PK or both are marked pk>0
    rows = cn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    col_pk = {r[1]: r[5] for r in rows}  # name -> pk (0/1/2 for position)
    return bool(col_pk.get("file_key", 0)) and bool(col_pk.get("timestamp", 0))

def _migrate_renko_like_table_allow_duplicates(cn, table: str):
    """
    Recreate `table` with AUTOINCREMENT PK and NO unique constraint on (file_key, timestamp).
    Safe to run multiple times: it only runs if the old unique-PK layout is detected.
    """
    # Only migrate if old PK exists
    if not _table_has_pk_on_filekey_timestamp(cn, table):
        return

    # Probe existing columns to carry them forward
    cols_info = cn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    cols = [r[1] for r in cols_info if r[1] not in ("id",)]  # exclude id if any
    # Build column defs, default to TEXT/REAL heuristic
    defs = []
    for r in cols_info:
        name = r[1]
        if name == "id":
            continue
        # keep simple typing; SQLite is flexible
        if name in ("file_key", "timestamp", "Signal"):
            coltype = "TEXT"
        else:
            coltype = "REAL"
        defs.append(f"{name} {coltype}")

    cn.exec_driver_sql(f"CREATE TABLE {table}__new (id INTEGER PRIMARY KEY AUTOINCREMENT, {', '.join(defs)});")
    cn.exec_driver_sql(f"CREATE INDEX ix_{table}__filekey_ts ON {table}__new(file_key, timestamp);")
    col_list = ", ".join(cols)
    cn.exec_driver_sql(f"INSERT INTO {table}__new ({col_list}) SELECT {col_list} FROM {table};")
    cn.exec_driver_sql(f"DROP TABLE {table};")
    cn.exec_driver_sql(f"ALTER TABLE {table}__new RENAME TO {table};")
    
    
# --- DB adapter for trade_manager (drop-in, minimal edits) ---


def _is_trade_manager(path: str) -> bool:
    import os as _os
    base = _os.path.basename(_os.path.normpath(str(path))).lower()
    return base == "trade_manager.csv"

def _ensure_tm_schema(cn):
    # Base schema (add columns later as needed)
    cn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS trade_manager (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exchange TEXT,
        timestamp TEXT,
        renko_signal TEXT,
        symbol TEXT,
        entry_price REAL,
        limit_entry_price REAL,
        exec_price REAL,
        quantity REAL,
        order_status TEXT,
        orderid TEXT,
        ltp REAL,
        close_reason TEXT
    );
    """)
    # Add new columns if they don't exist (for existing databases)
    cols = cn.exec_driver_sql("PRAGMA table_info(trade_manager)").fetchall()
    colnames = {r[1] for r in cols}
    if "limit_entry_price" not in colnames:
        cn.exec_driver_sql("ALTER TABLE trade_manager ADD COLUMN limit_entry_price REAL;")
    if "close_reason" not in colnames:
        cn.exec_driver_sql("ALTER TABLE trade_manager ADD COLUMN close_reason TEXT;")
    # Natural key for upserts: one row per (symbol, exchange, renko_signal, entry_price, timestamp)
    cn.exec_driver_sql("""
    CREATE UNIQUE INDEX IF NOT EXISTS ux_tm_natural
    ON trade_manager(symbol, exchange, renko_signal, entry_price, timestamp);
    """)
    cn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_tm_ts ON trade_manager(timestamp);")

def _colnames(cn, table: str):
    rows = cn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]

def _sync_table_schema(cn, table: str, df: pd.DataFrame):
    # Create minimal if missing
    cn.exec_driver_sql(f"""CREATE TABLE IF NOT EXISTS {table} (timestamp TEXT);""")
    existing = set(_colnames(cn, table))
    for col in df.columns:
        if col in existing:
            continue
        # Lightweight typing heuristic
        dt = str(df[col].dtype).lower()
        if "int" in dt:
            coltype = "INTEGER"
        elif "float" in dt:
            coltype = "REAL"
        elif "bool" in dt:
            coltype = "INTEGER"
        else:
            coltype = "TEXT"
        cn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {coltype};")

def save_to_csv(df: pd.DataFrame, file_name: str):
    """
    Thread-safe CSV/DB save operation.
    FEB 1 FIX: SQLite engine configured with thread-safe settings.
    """
    # --- trade_manager: UPSERT path ---
    if isinstance(df, pd.DataFrame) and "symbol" in df.columns:
        df["exchange"] = df.apply(
            lambda r: normalize_exchange_for_symbol(
                str(r.get("symbol", "")), str(r.get("exchange", ""))
            ),
            axis=1
        )    
    if _is_trade_manager(file_name):
        with _csv_lock:  # 🛡️ FEB 1 FIX: Lock for trade_manager writes
            to_write = df.copy()

            # normalize timestamp (if present)
            if "timestamp" in to_write.columns:
                to_write["timestamp"] = (
                    pd.to_datetime(to_write["timestamp"], errors="coerce")
                      .dt.tz_localize(None)
                      .dt.strftime("%Y-%m-%d %H:%M:%S")
                )

            # ensure schema first
            with _engine.begin() as cn:
                _ensure_tm_schema(cn)
                _sync_table_schema(cn, "trade_manager", to_write)

            # ⛳ nothing to do on empty frame
            if to_write.empty:
                # print("ℹ️ trade_manager: nothing to write (empty DataFrame) — skipped UPSERT")
                return

            # make sure conflict-key columns exist; set defaults if missing
            for k in ("symbol", "exchange", "renko_signal", "entry_price", "timestamp"):
                if k not in to_write.columns:
                    to_write[k] = None
            # default exchange so ON CONFLICT keys are not NULL
            to_write["exchange"] = to_write["exchange"].fillna("NSE_INDEX")

            # ✨ NEW: drop the autoincrement PK column before insert to avoid UNIQUE(id) errors
            if "id" in to_write.columns:
                to_write = to_write.drop(columns=["id"])

            cols = list(to_write.columns)
            conflict_key = ("symbol", "exchange", "renko_signal", "entry_price", "timestamp")
            update_cols = [c for c in cols if c not in conflict_key]

            cols_sql = ", ".join(cols)
            vals_sql = ", ".join([f":{c}" for c in cols])
            update_sql = ", ".join([f"{c}=excluded.{c}" for c in update_cols]) or "order_status=excluded.order_status"

            sql = f"""
            INSERT INTO trade_manager ({cols_sql}) VALUES ({vals_sql})
            ON CONFLICT(symbol, exchange, renko_signal, entry_price, timestamp)
            DO UPDATE SET {update_sql};
            """
            records = to_write.where(pd.notnull(to_write), None).to_dict(orient="records")
            with _engine.begin() as cn:
                cn.execute(text(sql), records)
                # print(f"🔁 [DB] Upserted {len(records)} trade_manager row(s) (no full replace)")
            return  # 🔴 important: stop here; don't fall through to other routes
    info = _resolve_table(file_name)
    table = info["table"]
    _ensure_table_and_index(table)

    to_write = df.copy()
    if "timestamp" in to_write.columns:
        to_write["timestamp"] = pd.to_datetime(to_write["timestamp"], errors="coerce") \
                                    .dt.tz_localize(None).dt.strftime("%Y-%m-%d %H:%M:%S")

    with _engine.begin() as cn:
        _sync_table_schema(cn, table, to_write)

        if table == "symbols_to_trade":
            cn.exec_driver_sql("""
                CREATE UNIQUE INDEX IF NOT EXISTS ux_symbols_to_trade
                ON symbols_to_trade(exchange, symbol);
            """)
            if to_write.empty:
                # print("ℹ️ symbols_to_trade: nothing to write")
                return
            # (optional safety) default exchange if missing
            if "exchange" not in to_write.columns:
                to_write["exchange"] = "NSE_INDEX"
            to_write["exchange"] = to_write["exchange"].fillna("NSE_INDEX")

            cols = list(to_write.columns)
            cols_sql = ", ".join(cols)
            vals_sql = ", ".join([f":{c}" for c in cols])
            update_sql = ", ".join([f"{c}=excluded.{c}" for c in cols if c not in ("exchange", "symbol")]) or "brick_size=excluded.brick_size"
            sql = f"""
            INSERT INTO symbols_to_trade ({cols_sql}) VALUES ({vals_sql})
            ON CONFLICT(exchange, symbol) DO UPDATE SET {update_sql};
            """
            cn.execute(text(sql), to_write.where(pd.notnull(to_write), None).to_dict(orient="records"))
            # print(f"🔁 [DB] Upserted {len(to_write)} row(s) into symbols_to_trade")
            return

        if table.startswith("ohlc_") or table.startswith("ohlc_fut_"):
            cn.exec_driver_sql(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{table}_ts ON {table}(timestamp);")
            cols = list(to_write.columns)
            cols_sql = ", ".join(cols)
            vals_sql = ", ".join([f":{c}" for c in cols])
            update_sql = ", ".join([f"{c}=excluded.{c}" for c in cols if c != "timestamp"]) or "open=excluded.open"
            sql = f"""
            INSERT INTO {table} ({cols_sql}) VALUES ({vals_sql})
            ON CONFLICT(timestamp) DO UPDATE SET {update_sql};
            """
            cn.execute(text(sql), to_write.where(pd.notnull(to_write), None).to_dict(orient="records"))
            # print(f"🔁 [DB] Upserted {len(to_write)} row(s) into {table} by timestamp")
            return

        if table.startswith("renko_") or table.startswith("renko_fut_"):
            if "timestamp" in to_write.columns and not to_write.empty:
                tmin = to_write["timestamp"].min()
                tmax = to_write["timestamp"].max()
                cn.execute(text(f"DELETE FROM {table} WHERE timestamp BETWEEN :tmin AND :tmax"),
                           {"tmin": tmin, "tmax": tmax})
            to_write.to_sql(table, con=cn, if_exists="append", index=False)
            # print(f"➕ [DB] Appended {len(to_write)} row(s) into {table} (overlap cleared)")
            return

        # fallback append
        to_write.to_sql(table, con=cn, if_exists="append", index=False)
        # print(f"➕ [DB] Appended {len(to_write)} row(s) into {table} (generic)")



def read_csv(file_name: str) -> pd.DataFrame:
    """
    DB-backed reads for all routed tables; orders by timestamp (and id for renko) when available.
    FEB 1 FIX: Added _csv_lock for trade_manager reads.
    """
    if _is_trade_manager(file_name):
        with _csv_lock:  # 🛡️ FEB 1 FIX: Lock for trade_manager reads
            with _engine.begin() as cn:
                _ensure_tm_schema(cn)  # ✅ make sure table exists in a fresh DB
                cols = set(_colnames(cn, "trade_manager"))
                order = "timestamp" + (", id" if "id" in cols else "")
                df = pd.read_sql_query(f"SELECT * FROM trade_manager ORDER BY {order}", con=cn)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            return df

    info = _resolve_table(file_name)
    table = info["table"]
    _ensure_table_and_index(table)

    with _engine.begin() as cn:
        # detect columns
        cols = set(_colnames(cn, table))
        if "timestamp" in cols:
            if table.startswith("renko_") or table.startswith("renko_fut_"):
                order = "timestamp" + (", id" if "id" in cols else "")
            else:
                order = "timestamp"
        else:
            order = "rowid"
        try:
            df = pd.read_sql_query(f"SELECT * FROM {table} ORDER BY {order}", con=cn)
        except Exception as e:
            print(f"⚠️ DB read error from {table}: {e}")
            return pd.DataFrame()

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def _any_nifty_option_inposition(tm: pd.DataFrame) -> bool:
    """True if ANY NIFTY CE/PE has an open position."""
    if tm is None or tm.empty:
        return False
    mask = (
        tm["renko_signal"].isin(["BUYEN", "BUYRE"]) &
        tm["order_status"].eq("INPOSITION") &
        tm["symbol"].astype(str).str.upper().str.startswith("NIFTY") &
        tm["symbol"].astype(str).str.upper().str.endswith(("CE", "PE"))
    )
    return bool(mask.any())


def write_symbols_to_csv(exchange, ce_symbol, pe_symbol):
    import os
    import pandas as pd
    import re
    from sqlalchemy import text

    os.makedirs("ohlcdata", exist_ok=True)
    os.makedirs("ohlcdata/outcomes", exist_ok=True)  # For signal outcome tracking
    os.makedirs("log", exist_ok=True)
    os.makedirs("db", exist_ok=True)
    file_path = symbols_file
    
    # --- 0) Expiry prefix (generic: e.g., NIFTY14OCT25, BANKNIFTY28OCT25, GOLD31OCT25, CRUDEOILM16OCT25, ZINCMINI31OCT25) ---
    u_ce = str(ce_symbol).upper()
    m = re.match(r'^([A-Z]+(?:M)?\d{2}[A-Z]{3}\d{2})', u_ce)
    prefix = m.group(1) if m else re.match(r'^[A-Z]+', u_ce).group(0)
    # --- 0) Expiry prefix (supports NIFTY & BANKNIFTY) ---
    
    # --- 1) CE/PE currently OPEN / INPOSITION (preserve only same-expiry, same-exchange) ---
    open_ce, open_pe = set(), set()
    tm = read_csv(trade_manager_file)
    if tm is not None and not tm.empty:
        def _same_expiry(s: str) -> bool:
            u = (s or "").upper().strip()
            return u.startswith(prefix) and u.endswith(("CE", "PE"))
    
        tm_open = tm[tm["order_status"].isin(["OPEN", "INPOSITION"])].copy()
        tm_open = tm_open[
            tm_open["exchange"].astype(str).str.upper().eq(str(exchange).upper())
            & tm_open["symbol"].astype(str).apply(_same_expiry)
        ]
    
        open_ce = set(
            tm_open.loc[
                tm_open["symbol"].astype(str).str.upper().str.endswith("CE"),
                "symbol"
            ].astype(str)
        )
        open_pe = set(
            tm_open.loc[
                tm_open["symbol"].astype(str).str.upper().str.endswith("PE"),
                "symbol"
            ].astype(str)
        )
    def _row(sym: str) -> dict:
        ex_norm = normalize_exchange_for_symbol(sym, exchange)  # MCX/NSE_INDEX normalization
        return {
            "exchange": ex_norm,
            "symbol": sym,
            # 🔁 Leave blank for MCX & SENSEX; ATR will fill once available
            "brick_size": _compute_startup_brick(ex_norm, sym),
        }
    new_rows = []
    if LONG_ONLY:
        new_rows.append(_row(next(iter(open_ce)) if open_ce else ce_symbol))
        new_rows.append(_row(next(iter(open_pe)) if open_pe else pe_symbol))
    else:
        new_rows.append(_row(ce_symbol))
        new_rows.append(_row(pe_symbol))

    new_symbols = pd.DataFrame(new_rows).drop_duplicates(subset=["symbol"])

    # --- 3) DELETE prior rows for same exchange + expiry prefix (CE/PE) ---
    try:
        existing_df = read_csv(file_path)
    except Exception:
        existing_df = None

    symbols_to_preserve = set()
    if LONG_ONLY:
        symbols_to_preserve |= open_ce
        symbols_to_preserve |= open_pe

    if existing_df is not None and not existing_df.empty:
        ex_mask = existing_df["exchange"].astype(str).str.upper().eq(str(exchange).upper())
        sym = existing_df["symbol"].astype(str)
        same_expiry_opt = sym.str.upper().str.startswith(prefix) & sym.str.upper().str.endswith(("CE", "PE"))
        candidates = existing_df.loc[ex_mask & same_expiry_opt, "symbol"].astype(str).tolist()
        to_delete = [s for s in candidates if s not in symbols_to_preserve]
    else:
        to_delete = []

    if to_delete:
        q = "DELETE FROM symbols_to_trade WHERE UPPER(exchange)=:ex AND symbol IN ({})".format(
            ", ".join([f":s{i}" for i in range(len(to_delete))])
        )
        params = {"ex": str(exchange).upper(), **{f"s{i}": s for i, s in enumerate(to_delete)}}
        with _engine.begin() as cn:
            cn.execute(text(q), params)
        print(f"🗑️ Deleted {len(to_delete)} old row(s) for expiry {prefix} on {exchange}: {to_delete}")
    else:
        if not symbols_to_preserve:
            q = """
                DELETE FROM symbols_to_trade
                WHERE UPPER(exchange)=:ex
                  AND UPPER(symbol) LIKE :pref
                  AND (UPPER(symbol) LIKE '%CE' OR UPPER(symbol) LIKE '%PE')
            """
            with _engine.begin() as cn:
                cn.execute(text(q), {"ex": str(exchange).upper(), "pref": f"{prefix.upper()}%"})
            print(f"🗑️ Deleted prior CE/PE rows for expiry {prefix} on {exchange} (pattern delete).")

    # --- 4) Insert the new rows (UPSERT is fine after delete) ---
    save_to_csv(new_symbols, file_path)
    print(f"✅ Inserted CE/PE for {prefix} on {exchange}: {new_symbols['symbol'].tolist()}")

    # Optional: show what’s tracked now for this exchange
    final_df = read_csv(file_path)
    if final_df is not None:
        ex_mask = final_df["exchange"].astype(str).str.upper().eq(str(exchange).upper())
        print(f"📌 Now tracking for {exchange}:",
              final_df.loc[ex_mask, "symbol"].astype(str).tolist())



def identify_swings_hilo_only(df, order: int = 5, K: int = 2):
    """
    Compute HH, HL, LL, LH strictly from High/Low values.
    This will be used only for NIFTY index OHLC.
    """
    out = df.copy()

    # Find swing points
    hh_rows = out.iloc[getHigherHighs(out['high'].values, order, K)]
    hl_rows = out.iloc[getHigherLows(out['low'].values,  order, K)]
    ll_rows = out.iloc[getLowerLows(out['low'].values,  order, K)]
    lh_rows = out.iloc[getLowerHighs(out['high'].values, order, K)]

    # Initialize swing columns
    out['HH'] = np.nan
    out['HL'] = np.nan
    out['LL'] = np.nan
    out['LH'] = np.nan

    # Map using timestamps
    if not hh_rows.empty:
        out['HH'] = out['timestamp'].map(hh_rows.groupby('timestamp')['high'].max())
    if not hl_rows.empty:
        out['HL'] = out['timestamp'].map(hl_rows.groupby('timestamp')['low'].min())
    if not ll_rows.empty:
        out['LL'] = out['timestamp'].map(ll_rows.groupby('timestamp')['low'].min())
    if not lh_rows.empty:
        out['LH'] = out['timestamp'].map(lh_rows.groupby('timestamp')['high'].max())

    return out


def fetch_historical_data(symbol, exchange, interval, start_date, end_date):
    """
    Fetch historical data for the specified symbol.
    """
    try:
        # Fetch 1-minute historical data using OpenAlgo
        df = client.history(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date
        )
        
        # Verify the data
        expected_columns = {'close', 'high', 'low', 'open'}
        missing_columns = expected_columns - set(df.columns)
        if missing_columns:
            raise KeyError(f"Missing columns in DataFrame: {missing_columns}")
        
        # Round numerical columns
        df['close'] = df['close'].round(2)
        df['high'] = df['high'].round(2)
        df['low'] = df['low'].round(2)
        df['open'] = df['open'].round(2)
        
        # add timestanp index as colum in database
        df = df.reset_index().rename(columns={"index": "timestamp"})

        # Remove the timezone information (+05:30)
        df['timestamp'] = df['timestamp'].dt.tz_localize(None)

        
        return df
    except Exception as e:
        print(f"Error fetching historical data: {str(e)}")
        return pd.DataFrame()

def renko_hilo_with_wicks(
    df: pd.DataFrame,
    brick_size: float,
    mode: str = "wicks",
    path: str = "auto"
) -> pd.DataFrame:
    """
    Build Renko using intrabar High/Low (not just closes), with wick drawing.
    - df must have columns: ['timestamp','open','high','low','close']
    - brick_size in price units
    - mode: one of {"wicks","nongap","normal","reverse-wicks","reverse-nongap","fake-r-wicks","fake-r-nongap"}
    - path:
        "auto"  -> bullish: O->H->L->C, bearish: O->L->H->C   (typical intrabar traversal)
        "ohlc"  -> O->H->L->C for every bar
        "olhc"  -> O->L->H->C for every bar

    Returns a Renko OHLC with: timestamp, open, high, low, close, direction, Renko_Brick
    """
    req = {"timestamp","open","high","low","close"}
    if not req.issubset(df.columns):
        missing = req - set(df.columns)
        raise ValueError(f"df missing columns: {missing}")

    x = df.loc[:, ["timestamp","open","high","low","close"]].copy()
    x["timestamp"] = pd.to_datetime(x["timestamp"])

    # --- Expand each bar into a tiny price path so Renko "sees" the high/low excursions ---
    # We keep the same minute timestamp but add nanosecond offsets for ordering.
    # For each row we append 4 points per chosen path.
    prices = []
    times  = []

    # Pre-allocate offsets: +0ns, +1ns, +2ns, +3ns so sort order is guaranteed
    ns_offsets = np.array([0, 1, 2, 3], dtype="timedelta64[ns]")

    for ts, o, h, l, c in x[["timestamp","open","high","low","close"]].itertuples(index=False):
        if path == "ohlc":
            seq = (o, h, l, c)
        elif path == "olhc":
            seq = (o, l, h, c)
        else:
            # auto: choose traversal by candle color
            seq = (o, h, l, c) if c >= o else (o, l, h, c)

        prices.extend(seq)
        # Same base time, tiny offsets to preserve order
        times.extend((ts + ns_offsets).tolist())

    expanded = pd.DataFrame({"timestamp": times, "close": np.array(prices, dtype=float)})

    # --- Build Renko from the expanded series ---
    r = Renko(expanded[["timestamp","close"]], brick_size=float(brick_size))
    renko_ohlc = r.renko_df(mode=mode)   # e.g., "wicks" (default) or "nongap" etc.

    out = renko_ohlc[["timestamp","open","high","low","close","direction"]].copy()
    out["Renko_Brick"] = out["close"]
    return out.reset_index(drop=True)



def calculate_renko_bricks(df, brick_size=8):
    if df is None or df.empty:
        return pd.DataFrame()
    
    if brick_size is None or brick_size <= 0:
        brick_size = 8  # Default fallback

    df = df.sort_values("timestamp").reset_index(drop=True)
    last_brick = math.floor(float(df.loc[0, "close"]) / brick_size) * brick_size
    rows = []

    for _, row in df.iterrows():
        hi, lo, ts = float(row["high"]), float(row["low"]), row["timestamp"]

        while hi >= last_brick + brick_size:
            last_brick += brick_size
            rows.append({"timestamp": ts, "Renko_Brick": round(last_brick, 2)})

        while lo <= last_brick - brick_size:
            last_brick -= brick_size
            rows.append({"timestamp": ts, "Renko_Brick": round(last_brick, 2)})

    return pd.DataFrame(rows, columns=["timestamp", "Renko_Brick"])

def generate_signals(renko_df, brick_size=BRICK_SIZE):
    """
    Generate BUY, SELL, CLOSE, and REVERSE signals based on Renko bricks and levels.
    - BUY: When price moves up by 2 bricks from the last low, position size is zero or SELL.
           Skip if near collected HH or LH levels.
    - SELL: When price falls by 2 bricks from the last high, position size is zero or BUY.
            Skip if near collected LL or HL levels.
    - CLOSE and REVERSE:
        - CLOSE BUY and open SELL when price falls by 2 bricks from the last high.
        - CLOSE SELL and open BUY when price rises by 2 bricks from the last low.
    """
    last_low = None
    last_high = None
    position = None  # Track position state: "BUY", "SELL", or None
    signals = []
    last_highs = []  # Store last high values
    last_lows = []   # Store last low values

    # Collect cumulative HH, HL, LL, LH levels up to the current row
    # collected_HH = list(set(renko_df['HH'].dropna().tolist()))
    # collected_HL = list(set(renko_df['HL'].dropna().tolist()))
    # collected_LL = list(set(renko_df['LL'].dropna().tolist()))
    # collected_LH = list(set(renko_df['LH'].dropna().tolist()))

    for i, row in renko_df.iterrows():
        brick = row['Renko_Brick']
        zlema = row['zlema9']
        # print("DEBUG : brick, last_low, last_high", brick, last_low, last_high )
        
        renko_slice_df = renko_df[0:i]
        # Extract the last non-NaN values of HH, HL, LL, LH
        last_hh = renko_slice_df['HH'].last_valid_index()
        last_hl = renko_slice_df['HL'].last_valid_index()
        last_ll = renko_slice_df['LL'].last_valid_index()
        last_lh = renko_slice_df['LH'].last_valid_index()

        hh_value = renko_slice_df.loc[last_hh, 'HH'] if last_hh is not None else None
        hl_value = renko_slice_df.loc[last_hl, 'HL'] if last_hl is not None else None
        ll_value = renko_slice_df.loc[last_ll, 'LL'] if last_ll is not None else None
        lh_value = renko_slice_df.loc[last_lh, 'LH'] if last_lh is not None else None

        last_two_hh_idx = renko_slice_df['HH'].dropna().index[-2:] if renko_slice_df['HH'].dropna().size >= 2 else renko_slice_df['HH'].dropna().index
        last_two_hl_idx = renko_slice_df['HL'].dropna().index[-2:] if renko_slice_df['HL'].dropna().size >= 2 else renko_slice_df['HL'].dropna().index
        last_two_ll_idx = renko_slice_df['LL'].dropna().index[-2:] if renko_slice_df['LL'].dropna().size >= 2 else renko_slice_df['LL'].dropna().index
        last_two_lh_idx = renko_slice_df['LH'].dropna().index[-2:] if renko_slice_df['LH'].dropna().size >= 2 else renko_slice_df['LH'].dropna().index
        # Fetch the values using .iloc
        last_two_hh_values = renko_df.loc[last_two_hh_idx, 'HH'].tolist()
        last_two_hl_values = renko_df.loc[last_two_hl_idx, 'HL'].tolist()
        last_two_ll_values = renko_df.loc[last_two_ll_idx, 'LL'].tolist()
        last_two_lh_values = renko_df.loc[last_two_lh_idx, 'LH'].tolist()

        # Fetch the values using .iloc
        # collected_HH = renko_slice_df.loc[last_two_hh_idx, 'HH'].tolist()
        # collected_HL = renko_slice_df.loc[last_two_hl_idx, 'HL'].tolist()
        # collected_LL = renko_slice_df.loc[last_two_ll_idx, 'LL'].tolist()
        # collected_LH = renko_slice_df.loc[last_two_lh_idx, 'LH'].tolist()
        
        collected_HH = list(set(renko_slice_df['HH'].dropna().tolist()))
        collected_HL = list(set(renko_slice_df['HL'].dropna().tolist()))
        collected_LL = list(set(renko_slice_df['LL'].dropna().tolist()))
        collected_LH = list(set(renko_slice_df['LH'].dropna().tolist()))

        # print("DEBUG last   HH LL values", brick, hh_value, lh_value ,ll_value, hl_value)
        # print("DEBUG last 2 HH LL values", brick, last_two_hh_values, last_two_lh_values ,last_two_ll_values, last_two_hl_values)


        # Helper function to check proximity to any collected level
        def is_near(collected_levels):
            return any(abs(brick - level) <= 2 * brick_size for level in collected_levels)
        def is_equal(collected_levels):
            return any(brick == level for level in collected_levels)


        if position is None:
#            if last_low is not None and brick >= last_low + 2 * brick_size and  brick > zlema:
            if last_low is not None and brick >= last_low + 2 * brick_size:
                
                signals.append("BUYEN")
                position = "BUY"
                last_high = brick
                last_low = None

            # 🛡️ LONG_ONLY: SELEN signals completely disabled
            # elif not LONG_ONLY and last_high is not None and brick <= last_high - 2 * brick_size  and  brick < zlema:
            #     signals.append("SELEN")
            #     position = 'SELL'
            #     last_low = brick
            #     last_high = None                      
                
            else:
                signals.append(None)
         
        # Exit signal
        elif position == "BUY":
            if brick <= last_high - 2 * brick_size:
                # 🛡️ LONG_ONLY: Always use SELEX (exit), never SELRE (reverse to short)
                signal_type = "SELEX"  # Always SELEX in LONG_ONLY mode
                signals.append(signal_type)
                position = None  # Exit position, don't go short
                last_low = brick
                last_high = None

            else:
                signals.append(None)     
                
        # 🛡️ LONG_ONLY: Remove SELL position handling completely
        # elif position == "SELL":
        #     if brick >= last_low + 2 * brick_size  and  brick > zlema:
        #         signals.append("BUYRE")
        #         position = 'BUY'
        #         last_low = None
        #         last_high = brick                
        #     else:
        #         signals.append(None)        
                
                
        
        '''
        # 🛡️ LONG_ONLY: SELEN_SW disabled - no short entries
        # if (
        #     (brick == hh_value if hh_value is not None else False) or
        #     (brick == lh_value if lh_value is not None else False) or
        #     (brick == ll_value if ll_value is not None else False) or
        #     (brick == hl_value if hl_value is not None else False)
        # ):
        #     signals.append("SELEN_SW")
        #     position = "SELL"  # Open SELL position
        #     last_low = brick  # Update last low
        #     last_high = None  # Reset last high
        #     print("DEBUG SELL", brick, last_high,  last_low, row['timestamp'])

        # BUYEN signal logic
        if (
            (brick == ll_value if ll_value is not None else False) or
            (brick == hl_value if hl_value is not None else False) or
            (brick == hh_value if hh_value is not None else False) or
            (brick == lh_value if lh_value is not None else False)
        ):
            signals.append("BUYEN_SW")
            position = "BUY"  # Open BUY position
            last_high = brick  # Update last high
            last_low = None  # Reset last low
            print("DEBUG BUY", brick, last_low,  last_high, row['timestamp'])
        '''

        # Update last low/high if no signals are generated
        if last_low is None or brick < last_low:
            last_low = brick
        if last_high is None or brick > last_high:
            last_high = brick
        
        # Store last high/low values (ensure tracking only when changed)
        last_highs.append(last_high)
        last_lows.append(last_low)

    
    # Add the signals column to the DataFrame
    renko_df['Signal'] = signals
    renko_df['Last_High'] = last_highs
    renko_df['Last_Low'] = last_lows

    return renko_df


# ============================================
# 🔄 CHOP DETECTION - Run length analysis functions
# ============================================
def calculate_run_lengths(renko_df, brick_size):
    """
    Calculate run lengths from Renko data.
    A run is a series of consecutive bricks in the same direction.
    Returns list of run lengths (positive = up, negative = down)
    """
    if renko_df is None or renko_df.empty or len(renko_df) < 2:
        return []
    
    runs = []
    current_run_length = 1
    prev_direction = None
    
    for i in range(1, len(renko_df)):
        try:
            prev_brick = float(renko_df['Renko_Brick'].iloc[i-1])
            curr_brick = float(renko_df['Renko_Brick'].iloc[i])
            
            if curr_brick > prev_brick:
                direction = 1  # Up
            elif curr_brick < prev_brick:
                direction = -1  # Down
            else:
                continue  # Same level, skip
            
            if prev_direction is None:
                prev_direction = direction
                current_run_length = 1
            elif direction == prev_direction:
                current_run_length += 1
            else:
                # Direction changed, save the run
                runs.append(current_run_length * prev_direction)
                current_run_length = 1
                prev_direction = direction
        except (ValueError, TypeError, IndexError):
            continue
    
    # Don't forget the last run
    if prev_direction is not None:
        runs.append(current_run_length * prev_direction)
    
    return runs


def is_market_choppy(renko_df, brick_size, lookback_runs=None, max_short_percent=None, avg_threshold=None):
    """
    Determine if market is choppy based on run length analysis.
    Choppy market indicators:
    1. High percentage of short runs (1-2 bricks)
    2. Low average run length
    3. Frequent direction changes
    
    Returns: (is_choppy: bool, chop_metrics: dict)
    """
    if lookback_runs is None:
        lookback_runs = CHOP_LOOKBACK_RUNS
    if max_short_percent is None:
        max_short_percent = MAX_SHORT_RUNS_PERCENT
    if avg_threshold is None:
        avg_threshold = AVG_RUN_LENGTH_THRESHOLD
    
    runs = calculate_run_lengths(renko_df, brick_size)
    
    if len(runs) < 3:
        # Not enough data to determine choppiness
        return False, {"reason": "insufficient_data", "run_count": len(runs)}
    
    # Take the most recent runs
    recent_runs = runs[-lookback_runs:] if len(runs) >= lookback_runs else runs
    
    # Calculate metrics
    abs_runs = [abs(r) for r in recent_runs]
    
    # 1. Short run percentage (1-2 bricks)
    short_runs = sum(1 for r in abs_runs if r <= 2)
    short_run_percent = (short_runs / len(abs_runs)) * 100
    
    # 2. Average run length
    avg_run_length = sum(abs_runs) / len(abs_runs)
    
    # 3. Direction change frequency
    direction_changes = sum(1 for i in range(1, len(recent_runs)) 
                           if (recent_runs[i] > 0) != (recent_runs[i-1] > 0))
    change_frequency = direction_changes / max(len(recent_runs) - 1, 1)
    
    chop_metrics = {
        "short_run_percent": round(short_run_percent, 1),
        "avg_run_length": round(avg_run_length, 2),
        "direction_change_freq": round(change_frequency, 2),
        "recent_runs": recent_runs[-5:],  # Last 5 runs for debugging
        "total_runs_analyzed": len(recent_runs)
    }
    
    # Determine if choppy
    is_choppy = (short_run_percent > max_short_percent) or (avg_run_length < avg_threshold)
    
    if is_choppy:
        chop_metrics["reason"] = []
        if short_run_percent > max_short_percent:
            chop_metrics["reason"].append(f"short_runs_{short_run_percent:.1f}%>{max_short_percent}%")
        if avg_run_length < avg_threshold:
            chop_metrics["reason"].append(f"avg_run_{avg_run_length:.2f}<{avg_threshold}")
    
    return is_choppy, chop_metrics


def get_current_trend_context(renko_df, brick_size, lookback=10):
    """
    Determine the current trend context from Renko data.
    Returns: ('BULLISH', 'BEARISH', 'NEUTRAL', metrics_dict)
    """
    if renko_df is None or renko_df.empty or len(renko_df) < lookback:
        return 'NEUTRAL', {"reason": "insufficient_data"}
    
    recent_df = renko_df.tail(lookback)
    
    try:
        first_brick = float(recent_df['Renko_Brick'].iloc[0])
        last_brick = float(recent_df['Renko_Brick'].iloc[-1])
        
        # Calculate net movement in bricks
        net_bricks = (last_brick - first_brick) / brick_size
        
        # Count up/down moves
        up_moves = 0
        down_moves = 0
        for i in range(1, len(recent_df)):
            prev = float(recent_df['Renko_Brick'].iloc[i-1])
            curr = float(recent_df['Renko_Brick'].iloc[i])
            if curr > prev:
                up_moves += 1
            elif curr < prev:
                down_moves += 1
        
        total_moves = up_moves + down_moves
        up_ratio = up_moves / max(total_moves, 1)
        
        metrics = {
            "net_bricks": round(net_bricks, 1),
            "up_moves": up_moves,
            "down_moves": down_moves,
            "up_ratio": round(up_ratio, 2)
        }
        
        # Determine trend
        if net_bricks >= 3 and up_ratio > 0.6:
            return 'BULLISH', metrics
        elif net_bricks <= -3 and up_ratio < 0.4:
            return 'BEARISH', metrics
        else:
            return 'NEUTRAL', metrics
            
    except (ValueError, TypeError, IndexError) as e:
        return 'NEUTRAL', {"error": str(e)}


def calculate_adx(df, period=14):
    """
    Calculate ADX (Average Directional Index) for trend strength.
    Returns ADX value or None if insufficient data.
    """
    try:
        if df is None or len(df) < period * 2:
            return None
        
        # Use tulipy for ADX calculation if available
        high = df['high'].astype(float).values if 'high' in df.columns else df['Renko_Brick'].astype(float).values
        low = df['low'].astype(float).values if 'low' in df.columns else df['Renko_Brick'].astype(float).values
        close = df['close'].astype(float).values if 'close' in df.columns else df['Renko_Brick'].astype(float).values
        
        # Calculate ADX using tulipy
        adx = ti.adx(high, low, close, period)
        
        if len(adx) > 0:
            return float(adx[-1])
        return None
    except Exception as e:
        # Fallback: return None if ADX calculation fails
        print(f"⚠️ ADX calculation error: {e}")
        return None


def should_block_for_chop_or_trend(renko_df, brick_size, signal_type):
    """
    Check if a signal should be blocked due to chop or adverse trend.
    
    Args:
        renko_df: Renko DataFrame
        brick_size: Brick size for the instrument
        signal_type: 'BUYCL' or 'BUYPT'
    
    Returns: (should_block: bool, reason: str)
    """
    # Check chop filter
    if ENABLE_CHOP_FILTER:
        is_choppy, chop_metrics = is_market_choppy(renko_df, brick_size)
        if is_choppy:
            reason = f"CHOP: {chop_metrics.get('reason', 'market_choppy')}"
            return True, reason
    
    # Check trend context for opposite trades
    trend, trend_metrics = get_current_trend_context(renko_df, brick_size)
    
    # Block CALL entries in strong bearish trend
    if signal_type == 'BUYCL' and trend == 'BEARISH':
        net_bricks = trend_metrics.get('net_bricks', 0)
        if net_bricks <= -4:  # Strong bearish
            return True, f"TREND: Strong bearish trend (net={net_bricks} bricks)"
    
    # Block PUT entries in strong bullish trend  
    if signal_type == 'BUYPT' and trend == 'BULLISH':
        net_bricks = trend_metrics.get('net_bricks', 0)
        if net_bricks >= 4:  # Strong bullish
            return True, f"TREND: Strong bullish trend (net={net_bricks} bricks)"
    
    # Check ADX filter
    if ENABLE_ADX_FILTER:
        adx = calculate_adx(renko_df, ADX_PERIOD)
        if adx is not None and adx < ADX_THRESHOLD:
            return True, f"ADX: Low trend strength (ADX={adx:.1f} < {ADX_THRESHOLD})"
    
    return False, None


# ============================================
# ⏰ TIME-BASED TRADING FILTER
# ============================================
def is_valid_trading_time():
    """
    Check if current time is within valid trading window.
    Avoids high-volatility periods at market open and close.
    """
    if not ENABLE_TIME_FILTER:
        return True, "Time filter disabled"
    
    from datetime import datetime
    from zoneinfo import ZoneInfo
    
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    
    # Market hours: 9:15 to 15:30
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    # Valid trading window
    valid_start = market_open + timedelta(minutes=MARKET_OPEN_BUFFER_MINUTES)
    valid_end = market_close - timedelta(minutes=MARKET_CLOSE_BUFFER_MINUTES)
    
    if now < valid_start:
        return False, f"Too early: waiting until {valid_start.strftime('%H:%M')}"
    elif now > valid_end:
        return False, f"Too late: trading stopped at {valid_end.strftime('%H:%M')}"
    
    return True, "Valid trading time"


# ============================================
# ⏰ RE-ENTRY COOLDOWN (Thread-Safe)
# ============================================
def check_reentry_cooldown(symbol, signal_type, price_level):
    """
    Check if a re-entry is allowed based on cooldown timer. Thread-safe.
    Returns (can_enter: bool, reason: str)
    """
    global _reentry_cooldown_tracker
    
    if not ENABLE_REENTRY_COOLDOWN:
        return True, "Cooldown disabled"
    
    direction = "CALL" if signal_type == "BUYCL" else "PUT"
    key = (symbol, direction, round(price_level, 2))
    
    with _cooldown_lock:
        if key in _reentry_cooldown_tracker:
            last_sl_time = _reentry_cooldown_tracker[key]
            elapsed_minutes = (pd.Timestamp.now() - last_sl_time).total_seconds() / 60
            
            if elapsed_minutes < REENTRY_COOLDOWN_MINUTES:
                remaining = REENTRY_COOLDOWN_MINUTES - elapsed_minutes
                return False, f"Cooldown active: {remaining:.1f} mins remaining"
    
    return True, "Cooldown cleared"


def get_original_entry_price(trade_manager, symbol, position_type):
    """
    Get the original entry price from BUYCL/BUYPT position.
    
    Args:
        trade_manager: DataFrame with trades
        symbol: Trading symbol
        position_type: 'CALL' or 'PUT'
    
    Returns:
        float or None: Original entry price
    """
    try:
        signal_type = "BUYCL" if position_type == "CALL" else "BUYPT"
        
        # Find the INPOSITION entry
        mask = (
            (trade_manager["symbol"] == symbol) &
            (trade_manager["renko_signal"] == signal_type) &
            (trade_manager["order_status"] == "INPOSITION")
        )
        
        matching = trade_manager[mask]
        if not matching.empty:
            return float(matching.iloc[0]["entry_price"])
        
        # If not found in INPOSITION, check CLOSED (might have just closed)
        mask_closed = (
            (trade_manager["symbol"] == symbol) &
            (trade_manager["renko_signal"] == signal_type) &
            (trade_manager["order_status"] == "CLOSED")
        )
        matching_closed = trade_manager[mask_closed]
        if not matching_closed.empty:
            # Get the most recent one
            return float(matching_closed.iloc[-1]["entry_price"])
        
        return None
    except Exception as e:
        print(f"⚠️ Error getting original entry price: {e}")
        return None


def is_stop_a_loss(position_type, original_entry, stop_price):
    """
    Determine if the stop that was hit resulted in a LOSS or PROFIT.
    
    For CALL:
      - Stop below entry = LOSS (initial stop hit)
      - Stop at/above entry = PROFIT/BREAKEVEN (trailing stop)
    
    For PUT:
      - Stop above entry = LOSS (initial stop hit)
      - Stop at/below entry = PROFIT/BREAKEVEN (trailing stop)
    
    Args:
        position_type: 'CALL' or 'PUT'
        original_entry: Original BUYCL/BUYPT entry price
        stop_price: The stop price that was hit (SELST/SELSP entry_price)
    
    Returns:
        bool: True if LOSS, False if PROFIT/BREAKEVEN
    """
    if original_entry is None or stop_price is None:
        # If we can't determine, assume it's a loss for safety
        return True
    
    if position_type == "CALL":
        # CALL: Loss if stop < entry
        return stop_price < original_entry
    else:
        # PUT: Loss if stop > entry
        return stop_price > original_entry


def record_stoploss_for_cooldown(symbol, signal_type, stop_price, original_entry_price=None):
    """
    Record a stop loss event for cooldown tracking. Thread-safe.
    
    ONLY records cooldown for ACTUAL LOSSES, not for trailing stop profits!
    
    Args:
        symbol: Trading symbol
        signal_type: 'SELST' or 'SELSP'
        stop_price: The stop price that was hit
        original_entry_price: Original BUYCL/BUYPT entry price (optional)
    """
    global _reentry_cooldown_tracker
    
    direction = "CALL" if signal_type in ["BUYCL", "SELST"] else "PUT"
    
    # Check if this stop was a LOSS or PROFIT
    if original_entry_price is not None:
        is_loss = is_stop_a_loss(direction, original_entry_price, stop_price)
        
        if not is_loss:
            # This was a trailing stop that locked in profit - NO COOLDOWN!
            print(f"✅ {direction} trailing stop hit at {stop_price:.2f} (entry was {original_entry_price:.2f}) - PROFIT, no cooldown needed!")
            return
        else:
            print(f"🛑 {direction} stop loss hit at {stop_price:.2f} (entry was {original_entry_price:.2f}) - LOSS, cooldown activated!")
    
    key = (symbol, direction, round(stop_price, 2))
    
    with _cooldown_lock:
        _reentry_cooldown_tracker[key] = pd.Timestamp.now()
        
        # Clean old entries (older than 1 hour)
        cutoff = pd.Timestamp.now() - pd.Timedelta(hours=1)
        _reentry_cooldown_tracker = {k: v for k, v in _reentry_cooldown_tracker.items() if v > cutoff}


# ============================================
# 🛡️ CONSECUTIVE LOSS PROTECTION (Thread-Safe)
# ============================================
def check_consecutive_losses():
    """Check if trading should be paused due to consecutive losses. Thread-safe."""
    global _consecutive_losses
    
    if not ENABLE_LOSS_PROTECTION:
        return True, "Loss protection disabled"
    
    with _loss_counter_lock:
        current_losses = _consecutive_losses
    
    if current_losses >= MAX_CONSECUTIVE_LOSSES:
        return False, f"Trading paused: {current_losses} consecutive losses"
    
    return True, f"Losses: {current_losses}/{MAX_CONSECUTIVE_LOSSES}"


def record_trade_result(is_profit):
    """Record trade result for consecutive loss tracking. Thread-safe."""
    global _consecutive_losses
    
    with _loss_counter_lock:
        if is_profit:
            _consecutive_losses = 0  # Reset on profit
        else:
            _consecutive_losses += 1
            if _consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
                print(f"⚠️🛑 CIRCUIT BREAKER: {_consecutive_losses} consecutive losses - Trading PAUSED")


def reset_loss_counter():
    """Manually reset the loss counter (e.g., at start of new day). Thread-safe."""
    global _consecutive_losses
    
    with _loss_counter_lock:
        _consecutive_losses = 0
    
    print("✅ Consecutive loss counter reset")


# ============================================
# 📈 V5: SIMPLE DIRECTION FILTER
# ============================================
# This is the ONLY filter you need!
# It answers: "What direction is the market moving?"

def get_market_direction(renko_df, lookback=None):
    """
    IMPROVED: Check direction of LAST FEW BRICKS (not net movement).
    
    This is FASTER and more responsive than checking net movement over 10 bricks.
    
    Args:
        renko_df: DataFrame with Renko_Brick column
        lookback: Number of bricks to analyze (default: DIRECTION_LOOKBACK)
    
    Returns:
        direction: 'UP', 'DOWN', or 'CHOPPY'
        net_bricks: Net movement in bricks (for logging)
    
    NEW Logic:
        - Look at last 3-5 bricks
        - Check the DIRECTION of each brick (up or down)
        - If last 2 bricks are BOTH UP → UP
        - If last 2 bricks are BOTH DOWN → DOWN
        - Otherwise → Allow trade (signal decides)
    """
    if lookback is None:
        lookback = DIRECTION_LOOKBACK
    
    if renko_df is None or len(renko_df) < 3:
        # Not enough data - allow all trades
        return 'UNKNOWN', 0
    
    # Get last few bricks
    recent = renko_df.tail(min(lookback, len(renko_df)))
    bricks = recent['Renko_Brick'].values
    
    if len(bricks) < 3:
        return 'UNKNOWN', 0
    
    # Calculate brick directions (up = +1, down = -1)
    directions = []
    for i in range(1, len(bricks)):
        if bricks[i] > bricks[i-1]:
            directions.append(1)  # UP brick
        elif bricks[i] < bricks[i-1]:
            directions.append(-1)  # DOWN brick
        # Equal bricks = same direction as previous (rare)
    
    if len(directions) < 2:
        return 'UNKNOWN', 0
    
    # Check last 2 brick directions
    last_2 = directions[-2:]
    
    # Calculate brick size from price differences (more robust)
    # Use median of absolute differences to handle outliers
    price_diffs = []
    for i in range(1, len(bricks)):
        diff = abs(bricks[i] - bricks[i-1])
        if diff > 0:
            price_diffs.append(diff)
    
    if price_diffs:
        # Use median to be robust against outliers
        price_diffs.sort()
        brick_size = price_diffs[len(price_diffs) // 2]
    else:
        brick_size = 10  # Fallback default
    
    # Calculate net movement in bricks
    net_bricks = (bricks[-1] - bricks[0]) / brick_size if brick_size > 0 else 0
    
    # Count consecutive same-direction bricks at the end
    consecutive_count = 1
    last_dir = directions[-1]
    for i in range(len(directions) - 2, -1, -1):
        if directions[i] == last_dir:
            consecutive_count += 1
        else:
            break
    
    # SIMPLE RULE: If last 2 bricks are same direction, that's the trend
    if all(d == 1 for d in last_2):
        short_term = 'UP'
    elif all(d == -1 for d in last_2):
        short_term = 'DOWN'
    else:
        # Mixed directions - use last brick
        short_term = 'UP' if directions[-1] == 1 else 'DOWN'
    
    # 🧠 HYBRID CHECK: Last 2 bricks vs Net direction must agree!
    # If last 2 are UP but net is strongly DOWN (< -1), it's a false signal
    # If last 2 are DOWN but net is strongly UP (> +1), it's a false signal
    if short_term == 'UP' and net_bricks < -1:
        # Short-term UP but overall DOWN - CONTRADICTION → CHOPPY
        return 'CHOPPY', net_bricks
    elif short_term == 'DOWN' and net_bricks > 1:
        # Short-term DOWN but overall UP - CONTRADICTION → CHOPPY
        return 'CHOPPY', net_bricks
    
    # 🧠 MINIMUM NET STRENGTH CHECK:
    # Even if direction matches, require at least MIN_NET_BRICKS net movement
    # This prevents weak signals like net=-1.0 for PUT or net=+1.0 for CALL
    # Uses global MIN_NET_BRICKS from config (default: 2)
    
    if short_term == 'UP' and net_bricks < MIN_NET_BRICKS:
        # UP direction but net is too weak (less than +2)
        return 'CHOPPY', net_bricks
    elif short_term == 'DOWN' and net_bricks > -MIN_NET_BRICKS:
        # DOWN direction but net is too weak (greater than -2)
        return 'CHOPPY', net_bricks
    
    return short_term, net_bricks


def get_market_direction_strict(renko_df, lookback=None):
    """
    STRICT version: Original logic requiring MIN_NET_BRICKS.
    Use this if you want to be more conservative.
    """
    if lookback is None:
        lookback = DIRECTION_LOOKBACK
    
    if renko_df is None or len(renko_df) < lookback:
        return 'UNKNOWN', 0
    
    recent = renko_df.tail(lookback)
    bricks = recent['Renko_Brick'].values
    
    # Ensure we have enough bricks
    if len(bricks) < 2:
        return 'UNKNOWN', 0
    
    first_brick = bricks[0]
    last_brick = bricks[-1]
    
    brick_size = abs(bricks[1] - bricks[0]) if len(bricks) > 1 else 10
    if brick_size == 0:
        for i in range(1, len(bricks)):
            diff = abs(bricks[i] - bricks[i-1])
            if diff > 0:
                brick_size = diff
                break
        if brick_size == 0:
            return 'CHOPPY', 0
    
    net_bricks = (last_brick - first_brick) / brick_size
    
    if net_bricks >= MIN_NET_BRICKS:
        return 'UP', net_bricks
    elif net_bricks <= -MIN_NET_BRICKS:
        return 'DOWN', net_bricks
    else:
        return 'CHOPPY', net_bricks


def detect_chop(renko_df):
    """
    CHOP DETECTOR: Detect choppy/whipsaw markets.
    
    Two detection methods:
    1. Count direction reversals - if too many, it's choppy
    2. Detect alternating pattern (↑↓↑↓) - immediate chop signal
    
    Example patterns:
        ↑↑↑↓↓↓ = 1 reversal = TRENDING ✅
        ↑↓↑↓↑↓ = 5 reversals + ALTERNATING = VERY CHOPPY ❌❌
        ↑↑↓↑↓↓ = 3 reversals = BORDERLINE
    
    Args:
        renko_df: DataFrame with Renko_Brick column
    
    Returns:
        (is_choppy: bool, reversals: int, reason: str)
    """
    if not ENABLE_CHOP_DETECTOR:
        return False, 0, "Chop detector disabled"
    
    if renko_df is None or len(renko_df) < CHOP_LOOKBACK:
        return False, 0, "Not enough data for chop detection"
    
    # Get last N bricks
    recent = renko_df.tail(CHOP_LOOKBACK)
    bricks = recent['Renko_Brick'].values
    
    if len(bricks) < 2:
        return False, 0, "Not enough bricks"
    
    # Build direction list
    directions = []
    for i in range(1, len(bricks)):
        if bricks[i] > bricks[i-1]:
            directions.append(1)  # UP
        elif bricks[i] < bricks[i-1]:
            directions.append(-1)  # DOWN
        # Equal = skip
    
    if len(directions) < 2:
        return False, 0, "Not enough direction changes"
    
    # Count reversals (direction changes)
    reversals = 0
    for i in range(1, len(directions)):
        if directions[i] != directions[i-1]:
            reversals += 1
    
    # SPECIAL CHECK: Detect alternating pattern (↑↓↑↓ or ↓↑↓↑)
    # This is the WORST kind of chop - every trade will be stopped out
    is_alternating = True
    for i in range(1, len(directions)):
        if directions[i] == directions[i-1]:
            is_alternating = False
            break
    
    # If last 4+ bricks are alternating = VERY CHOPPY
    if is_alternating and len(directions) >= 3:
        return True, reversals, f"🔄❌ ALTERNATING PATTERN: Every brick reversing! ({reversals} rev)"
    
    # Standard reversal check
    is_choppy = reversals > MAX_REVERSALS
    
    if is_choppy:
        return True, reversals, f"🔄 CHOPPY: {reversals} reversals in {CHOP_LOOKBACK} bricks (max: {MAX_REVERSALS})"
    else:
        return False, reversals, f"✅ Trending: {reversals} reversals"


# ============================================
# 🚫 SIGNAL PROXIMITY FILTER
# ============================================
# Checks if an opposite signal appeared within the last N bricks
# If BUYEN but recent SELEX → BLOCK (market just said SELL, now says BUY = confused)
# If SELEX but recent BUYEN → BLOCK (market just said BUY, now says SELL = confused)
# STRENGTH OVERRIDE: If net is strong enough, allow anyway!

def check_signal_proximity(renko_df, current_signal, lookback=None, net_bricks=None):
    """
    Check if an opposite signal appeared within the last N bricks.
    
    Args:
        renko_df: Renko DataFrame with signal column
        current_signal: Current signal ('BUYEN' or 'SELEX')
        lookback: Number of bricks to look back (default: MIN_BRICKS_SINCE_OPPOSITE_SIGNAL)
        net_bricks: Net direction in bricks (for strength override check)
    
    Returns:
        tuple: (is_blocked, bricks_since_opposite, message)
               - is_blocked: True if should block trade
               - bricks_since_opposite: How many bricks since opposite signal (None if not found)
               - message: Explanation string
    """
    if not ENABLE_SIGNAL_PROXIMITY_FILTER:
        return False, None, "Signal proximity filter disabled"
    
    if lookback is None:
        lookback = MIN_BRICKS_SINCE_OPPOSITE_SIGNAL
    
    if renko_df is None or len(renko_df) < 2:
        return False, None, "Not enough data"
    
    # Determine opposite signal
    if current_signal == "BUYEN":
        opposite_signal = "SELEX"
    elif current_signal == "SELEX":
        opposite_signal = "BUYEN"
    else:
        return False, None, f"Unknown signal: {current_signal}"
    
    # Get recent bricks (excluding current brick which has the signal)
    # We look at the last N bricks BEFORE the current one
    recent = renko_df.tail(lookback + 1).iloc[:-1]  # Exclude current brick
    
    if len(recent) == 0:
        return False, None, "No previous bricks to check"
    
    # Find signal column - could be 'Unnamed: 7', 'signal', or 'Renko_Signal'
    signal_col = None
    for col in ['Unnamed: 7', 'signal', 'Renko_Signal', 'Signal']:
        if col in renko_df.columns:
            signal_col = col
            break
    
    if signal_col is None:
        return False, None, "No signal column found"
    
    # Check each recent brick for opposite signal
    bricks_back = 0
    for idx in range(len(recent) - 1, -1, -1):  # Go backwards from most recent
        bricks_back += 1
        row = recent.iloc[idx]
        signal = row.get(signal_col, None)
        
        if signal == opposite_signal:
            # Found opposite signal within lookback!
            if bricks_back <= lookback:
                # Check for STRENGTH OVERRIDE
                if net_bricks is not None and abs(net_bricks) >= SIGNAL_PROXIMITY_STRENGTH_OVERRIDE:
                    return False, bricks_back, f"✅ STRENGTH OVERRIDE: {opposite_signal} was {bricks_back} brick(s) ago, but net={net_bricks:+.1f} >= {SIGNAL_PROXIMITY_STRENGTH_OVERRIDE}"
                else:
                    net_info = f", net={net_bricks:+.1f}" if net_bricks is not None else ""
                    return True, bricks_back, f"🚫 BLOCKED: {opposite_signal} was {bricks_back} brick(s) ago (min: {lookback}{net_info}, need ≥{SIGNAL_PROXIMITY_STRENGTH_OVERRIDE} to override)"
            else:
                return False, bricks_back, f"✅ OK: {opposite_signal} was {bricks_back} bricks ago"
    
    # No opposite signal found in lookback
    return False, None, f"✅ No {opposite_signal} in last {lookback} bricks"


# ============================================
# 🧠 DIRECTION STABILITY TRACKING
# ============================================
def update_direction_tracker(symbol, current_direction):
    """
    Track when direction changes for each symbol.
    Called whenever we check direction to keep tracker updated.
    
    Args:
        symbol: Trading symbol
        current_direction: 'UP' or 'DOWN'
    
    Returns:
        (direction_age_seconds: float, direction_changed: bool)
    """
    global _direction_tracker
    
    if not ENABLE_DIRECTION_STABILITY:
        return 9999, False  # Return large age if disabled
    
    now = pd.Timestamp.now()
    
    with _direction_lock:
        if symbol not in _direction_tracker:
            # First time seeing this symbol
            _direction_tracker[symbol] = {
                'direction': current_direction,
                'changed_at': now
            }
            return 0, True  # New tracking, age = 0
        
        tracked = _direction_tracker[symbol]
        old_direction = tracked['direction']
        
        if current_direction != old_direction:
            # Direction CHANGED!
            _direction_tracker[symbol] = {
                'direction': current_direction,
                'changed_at': now
            }
            return 0, True  # Just changed, age = 0
        else:
            # Direction SAME
            age_seconds = (now - tracked['changed_at']).total_seconds()
            return age_seconds, False


def check_direction_stability(symbol, signal_type, renko_df):
    """
    Check if direction has been stable long enough for entry.
    
    Args:
        symbol: Trading symbol
        signal_type: 'BUYCL' (CALL needs UP) or 'BUYPT' (PUT needs DOWN)
        renko_df: Renko DataFrame
    
    Returns:
        (is_stable: bool, age_minutes: float, reason: str)
    """
    if not ENABLE_DIRECTION_STABILITY:
        return True, 99, "Direction stability check disabled"
    
    # Get current direction
    if DIRECTION_FILTER_MODE == "SMART":
        current_direction, net_bricks = get_market_direction(renko_df)
    else:
        current_direction, net_bricks = get_market_direction_strict(renko_df)
    
    # Update tracker and get age
    age_seconds, direction_changed = update_direction_tracker(symbol, current_direction)
    age_minutes = age_seconds / 60
    
    # Check if direction matches signal type
    required_direction = 'UP' if signal_type == 'BUYCL' else 'DOWN'
    
    if current_direction != required_direction:
        return False, age_minutes, f"Direction is {current_direction}, not {required_direction}"
    
    # Check stability
    if age_minutes < DIRECTION_STABLE_MINUTES:
        remaining = DIRECTION_STABLE_MINUTES - age_minutes
        return False, age_minutes, f"🧠 Direction {current_direction} only {age_minutes:.1f} mins old (need {DIRECTION_STABLE_MINUTES} mins, wait {remaining:.1f} more)"
    
    return True, age_minutes, f"✅ Direction {current_direction} stable for {age_minutes:.1f} mins"


# ============================================
# 🚨 BRICK RATE MONITOR - PROACTIVE Chop Detection
# ============================================

def record_brick_formation(symbol, brick_direction):
    """
    Record when a new brick forms. Called when Renko chart updates.
    
    Args:
        symbol: Trading symbol
        brick_direction: 1 for UP, -1 for DOWN
    """
    global _brick_history
    
    if not ENABLE_BRICK_RATE_MONITOR:
        return
    
    now = pd.Timestamp.now()
    
    with _brick_rate_lock:
        if symbol not in _brick_history:
            _brick_history[symbol] = []
        
        # Add new brick
        _brick_history[symbol].append((now, brick_direction))
        
        # Keep only bricks from last 10 minutes (cleanup)
        cutoff = now - pd.Timedelta(minutes=10)
        _brick_history[symbol] = [(t, d) for t, d in _brick_history[symbol] if t > cutoff]


def check_brick_rate_pause(symbol):
    """
    Check if trading should be paused due to rapid alternating bricks.
    
    Args:
        symbol: Trading symbol
    
    Returns:
        (is_paused: bool, reason: str)
    """
    global _brick_rate_pause
    
    if not ENABLE_BRICK_RATE_MONITOR:
        return False, "Brick rate monitor disabled"
    
    now = pd.Timestamp.now()
    
    with _brick_rate_lock:
        # Check if currently paused
        if symbol in _brick_rate_pause:
            pause_until = _brick_rate_pause[symbol]
            if now < pause_until:
                remaining = (pause_until - now).total_seconds() / 60
                return True, f"🚨 BRICK RATE PAUSE: {remaining:.1f} mins remaining (rapid chop detected)"
            else:
                # Pause expired, remove it
                del _brick_rate_pause[symbol]
        
        # Check recent brick formation rate
        if symbol not in _brick_history or len(_brick_history[symbol]) < 3:
            return False, "Not enough brick history"
        
        # Get bricks in the window
        window_start = now - pd.Timedelta(minutes=BRICK_RATE_WINDOW_MINUTES)
        recent_bricks = [(t, d) for t, d in _brick_history[symbol] if t > window_start]
        
        if len(recent_bricks) < 3:
            return False, f"Only {len(recent_bricks)} bricks in last {BRICK_RATE_WINDOW_MINUTES} mins"
        
        # Count alternations
        alternations = 0
        for i in range(1, len(recent_bricks)):
            if recent_bricks[i][1] != recent_bricks[i-1][1]:
                alternations += 1
        
        # Check if chop threshold exceeded
        if alternations >= BRICK_RATE_MIN_ALTERNATIONS:
            # Trigger pause!
            pause_until = now + pd.Timedelta(minutes=BRICK_RATE_PAUSE_MINUTES)
            _brick_rate_pause[symbol] = pause_until
            
            # Log the pattern
            pattern = "".join(["↑" if d == 1 else "↓" for t, d in recent_bricks])
            print(f"🚨🚨 BRICK RATE CHOP DETECTED for {symbol}!")
            print(f"   Pattern: {pattern} ({len(recent_bricks)} bricks, {alternations} alternations in {BRICK_RATE_WINDOW_MINUTES} mins)")
            print(f"   Trading PAUSED until {pause_until.strftime('%H:%M:%S')} ({BRICK_RATE_PAUSE_MINUTES} mins)")
            
            return True, f"🚨 BRICK RATE CHOP: {alternations} alternations in {BRICK_RATE_WINDOW_MINUTES} mins ({pattern})"
        
        return False, f"Brick rate OK: {alternations} alternations (max: {BRICK_RATE_MIN_ALTERNATIONS})"


def update_brick_history_from_renko(symbol, renko_df):
    """
    Update brick history from Renko DataFrame.
    Called periodically to sync brick history with actual Renko chart.
    
    Args:
        symbol: Trading symbol
        renko_df: Renko DataFrame
    """
    global _brick_history
    
    if not ENABLE_BRICK_RATE_MONITOR:
        return
    
    if renko_df is None or len(renko_df) < 2:
        return
    
    # Get last few bricks
    last_bricks = renko_df.tail(5)
    
    with _brick_rate_lock:
        if symbol not in _brick_history:
            _brick_history[symbol] = []
        
        # Check if last brick is new (compare with our history)
        if len(_brick_history[symbol]) == 0:
            # Initialize with recent bricks
            for idx, row in last_bricks.iterrows():
                try:
                    timestamp = pd.Timestamp(row.get('Date', row.get('timestamp', pd.Timestamp.now())))
                    direction = 1 if row['Renko_Brick'] > 0 else -1
                    _brick_history[symbol].append((timestamp, direction))
                except:
                    pass
        else:
            # Check if there's a new brick by comparing last brick value
            last_recorded = _brick_history[symbol][-1] if _brick_history[symbol] else None
            current_brick = renko_df['Renko_Brick'].iloc[-1]
            current_direction = 1 if current_brick > 0 else -1
            
            # If direction changed or brick value changed significantly, record new brick
            if last_recorded is None or last_recorded[1] != current_direction:
                record_brick_formation(symbol, current_direction)


# ============================================
# 📊 FILTER LOGGING FUNCTIONS
# ============================================

def init_filter_log_for_today():
    """Initialize filter log for today's date."""
    global _filter_stats, _filter_log_data
    
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    
    with _filter_log_lock:
        if _filter_stats["date"] != today:
            # New day - reset stats
            _filter_stats = {
                "date": today,
                "total_signals": 0,
                "allowed": 0,
                "blocked": 0,
                "blocked_by_chop": 0,
                "blocked_by_hybrid": 0,
                "blocked_by_min_net": 0,
                "blocked_by_signal_proximity": 0,
                "blocked_by_cooldown": 0,
                "blocked_by_brick_rate": 0,
                "winners": 0,
                "losers": 0,
            }
            _filter_log_data = []
            print(f"📊 Filter logging initialized for {today}")


def log_filter_decision(symbol, signal_type, entry_price, decision, blocked_by, 
                        net_bricks, reversals, reason, renko_pattern=""):
    """
    Log a filter decision for analysis.
    
    Args:
        symbol: Trading symbol
        signal_type: 'BUYCL' or 'BUYPT'
        entry_price: Signal entry price
        decision: 'ALLOWED' or 'BLOCKED'
        blocked_by: Filter name that blocked (or '-' if allowed)
        net_bricks: Net brick movement
        reversals: Number of reversals in lookback
        reason: Detailed reason string
        renko_pattern: Last few brick directions (e.g., "↑↑↓↑↑")
    """
    global _filter_stats, _filter_log_data
    
    if not ENABLE_FILTER_LOGGING:
        return
    
    init_filter_log_for_today()
    
    timestamp = pd.Timestamp.now()
    
    log_entry = {
        "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "signal": signal_type,
        "entry_price": entry_price,
        "decision": decision,
        "blocked_by": blocked_by,
        "net_bricks": round(net_bricks, 1) if net_bricks is not None else 0,
        "reversals": reversals,
        "pattern": renko_pattern,
        "reason": reason,
    }
    
    with _filter_log_lock:
        _filter_log_data.append(log_entry)
        _filter_stats["total_signals"] += 1
        
        if decision == "ALLOWED":
            _filter_stats["allowed"] += 1
        else:
            _filter_stats["blocked"] += 1
            
            # Track which filter blocked
            if "CHOP" in blocked_by.upper():
                _filter_stats["blocked_by_chop"] += 1
            elif "HYBRID" in blocked_by.upper():
                _filter_stats["blocked_by_hybrid"] += 1
            elif "MIN_NET" in blocked_by.upper() or "CHOPPY" in blocked_by.upper():
                _filter_stats["blocked_by_min_net"] += 1
            elif "SIGNAL_PROXIMITY" in blocked_by.upper() or "PROXIMITY" in blocked_by.upper():
                _filter_stats["blocked_by_signal_proximity"] += 1
            elif "COOLDOWN" in blocked_by.upper():
                _filter_stats["blocked_by_cooldown"] += 1
            elif "BRICK_RATE" in blocked_by.upper():
                _filter_stats["blocked_by_brick_rate"] += 1
    
    # Console output
    if FILTER_LOG_TO_CONSOLE:
        direction = "CALL" if signal_type == "BUYCL" else "PUT"
        if decision == "ALLOWED":
            print(f"📊 FILTER: ✅ {direction} {symbol} @ {entry_price} → ALLOWED (net: {net_bricks:+.1f}, rev: {reversals})")
        else:
            print(f"📊 FILTER: ❌ {direction} {symbol} @ {entry_price} → BLOCKED by {blocked_by}")
            if FILTER_LOG_VERBOSE:
                print(f"   └── Reason: {reason}")
    
    # Save to CSV periodically (every 10 signals)
    if _filter_stats["total_signals"] % 10 == 0:
        save_filter_log_to_csv()


def save_filter_log_to_csv():
    """Save filter log to CSV file."""
    global _filter_log_data
    
    if not ENABLE_FILTER_LOGGING or not _filter_log_data:
        return
    
    try:
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        log_file = os.path.join(FILTER_LOG_DIR, f"filter_log_{today}.csv")
        
        os.makedirs(FILTER_LOG_DIR, exist_ok=True)
        
        with _filter_log_lock:
            df = pd.DataFrame(_filter_log_data)
            df.to_csv(log_file, index=False)
            
    except Exception as e:
        print(f"⚠️ Error saving filter log: {e}")


def get_filter_stats_summary():
    """Get a summary of filter statistics."""
    global _filter_stats
    
    with _filter_log_lock:
        stats = _filter_stats.copy()
    
    total = stats["total_signals"]
    if total == 0:
        return "No signals recorded yet."
    
    allowed_pct = (stats["allowed"] / total) * 100
    blocked_pct = (stats["blocked"] / total) * 100
    
    summary = f"""
════════════════════════════════════════════════════════════════
📊 FILTER STATS - {stats['date']}
════════════════════════════════════════════════════════════════

SIGNAL SUMMARY:
  Total Signals Generated:     {total}
  ✅ Allowed:                  {stats['allowed']} ({allowed_pct:.0f}%)
  ❌ Blocked:                  {stats['blocked']} ({blocked_pct:.0f}%)

BLOCKED BY FILTER:
  🔄 Chop Detector:            {stats['blocked_by_chop']}
  🧠 HYBRID Direction:         {stats['blocked_by_hybrid']}
  🎯 MIN_NET Strength:         {stats['blocked_by_min_net']}
  🚫 Signal Proximity:         {stats['blocked_by_signal_proximity']}
  ⏰ Re-entry Cooldown:        {stats['blocked_by_cooldown']}
  🚨 Brick Rate Monitor:       {stats['blocked_by_brick_rate']}

TRADE OUTCOMES (if tracked):
  ✅ Winners:                   {stats['winners']}
  ❌ Losers:                    {stats['losers']}

════════════════════════════════════════════════════════════════
"""
    return summary


def print_filter_stats():
    """Print filter statistics to console."""
    print(get_filter_stats_summary())


def save_filter_summary():
    """Save filter summary to file."""
    if not ENABLE_FILTER_LOGGING:
        return
    
    try:
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        summary_file = os.path.join(FILTER_LOG_DIR, f"filter_summary_{today}.txt")
        
        os.makedirs(FILTER_LOG_DIR, exist_ok=True)
        
        summary = get_filter_stats_summary()
        
        with open(summary_file, "w") as f:
            f.write(summary)
        
        print(f"📊 Filter summary saved to {summary_file}")
        
    except Exception as e:
        print(f"⚠️ Error saving filter summary: {e}")


def record_trade_outcome(symbol, signal_type, is_winner):
    """Record whether an allowed trade was a winner or loser."""
    global _filter_stats
    
    if not ENABLE_FILTER_LOGGING:
        return
    
    with _filter_log_lock:
        if is_winner:
            _filter_stats["winners"] += 1
        else:
            _filter_stats["losers"] += 1


def get_renko_pattern(renko_df, lookback=6):
    """Get the pattern of last N bricks as arrows."""
    if renko_df is None or len(renko_df) < 2:
        return ""
    
    recent = renko_df.tail(lookback)
    pattern = ""
    for _, row in recent.iterrows():
        if row.get("Renko_Brick", 0) > 0:
            pattern += "↑"
        else:
            pattern += "↓"
    return pattern


# ============================================
# 🎯 QUALITY SCORING SYSTEM - IMPROVED v2
# ============================================
# Fixes issues found in Jan 29 analysis:
# 1. Net direction was +1/-1 even in strong trends (lookback too long)
# 2. 245 SELEX vs 26 BUYEN in uptrend (counter-trend signals not rejected)
# 3. BUYEN blocked with net=+1 (threshold too strict)
#
# IMPROVEMENTS:
# - Shorter lookback (8 vs 20 bricks) for faster trend detection
# - IMMEDIATE direction check (last 3-5 bricks weighted heavily)
# - Counter-trend signal PENALTY (SELEX in uptrend = low score)
# - Consecutive brick BONUS (3+ same direction = strong)
# - Recent HIGH/LOW comparison for breakout detection

def calculate_signal_quality(renko_df, signal_type, symbol, brick_size=None):
    """
    Calculate quality score for a Renko signal (0-100).
    IMPROVED VERSION - fixes issues from Jan 29 analysis.
    
    Key changes:
    - Shorter lookback (8 bricks) for trend
    - Heavy weight on IMMEDIATE direction (last 3-5 bricks)
    - Counter-trend signals get penalized
    - Consecutive run bonus
    
    Returns:
        score (int): 0-100 quality score
        components (dict): Individual component scores
        recommendation (str): 'TAKE', 'MAYBE', or 'SKIP'
        reason (str): Human readable explanation
    """
    components = {
        'trend_strength': 0,      # Consecutive bricks (0-25)
        'trend_alignment': 0,     # Signal matches trend (0-30)
        'momentum': 0,            # Recent direction (0-20)
        'breakout': 0,            # Breaking highs/lows (0-15)
        'clean_market': 0         # Low reversals (0-10)
    }
    
    if renko_df is None or len(renko_df) < 8:
        return 0, components, 'SKIP', "Not enough data for quality scoring"
    
    # Get brick data
    bricks = renko_df['Renko_Brick'].values
    
    # Auto-detect brick size
    if brick_size is None:
        diffs = np.abs(np.diff(bricks))
        diffs = diffs[diffs > 0]
        brick_size = np.median(diffs) if len(diffs) > 0 else 10
    
    # Calculate brick directions
    directions = []
    for i in range(1, len(bricks)):
        if bricks[i] > bricks[i-1]:
            directions.append(1)   # UP
        elif bricks[i] < bricks[i-1]:
            directions.append(-1)  # DOWN
        else:
            directions.append(0)
    
    if len(directions) < 5:
        return 0, components, 'SKIP', "Not enough direction data"
    
    is_call = signal_type == 'BUYCL'
    is_put = signal_type == 'BUYPT'
    
    # ============================================
    # COMPONENT 1: TREND STRENGTH (0-25 pts)
    # Count consecutive bricks at the END in same direction
    # ============================================
    consecutive = 1
    last_dir = directions[-1]
    for i in range(len(directions) - 2, -1, -1):
        if directions[i] == last_dir:
            consecutive += 1
        else:
            break
    
    if consecutive >= 5:
        components['trend_strength'] = 25
    elif consecutive >= 4:
        components['trend_strength'] = 22
    elif consecutive >= 3:
        components['trend_strength'] = 18
    elif consecutive >= 2:
        components['trend_strength'] = 12
    else:
        components['trend_strength'] = 5
    
    # ============================================
    # COMPONENT 2: TREND ALIGNMENT (0-30 pts)
    # IMPROVED: Use SHORT lookback (8 bricks) + IMMEDIATE check
    # ============================================
    
    # Short-term trend (last 8 bricks or available)
    short_lookback = min(8, len(bricks))
    short_net = (bricks[-1] - bricks[-short_lookback]) / brick_size
    
    # IMMEDIATE trend (last 3 bricks) - for alignment score
    immediate_lookback = min(3, len(directions))
    immediate_dirs = directions[-immediate_lookback:]
    immediate_up = sum(1 for d in immediate_dirs if d == 1)
    immediate_down = sum(1 for d in immediate_dirs if d == -1)
    
    # 🛡️ FEB 1 FIX: DOMINANT trend (last 20 bricks) - for counter-trend penalty
    # This catches small bounces in larger trends!
    dominant_lookback = min(QS_DOMINANT_TREND_LOOKBACK, len(bricks))
    dominant_net = (bricks[-1] - bricks[-dominant_lookback]) / brick_size if dominant_lookback > 0 else 0
    
    # Determine DOMINANT trend from 20-brick lookback
    if dominant_net >= 2:
        dominant_trend = 'UP'
    elif dominant_net <= -2:
        dominant_trend = 'DOWN'
    else:
        dominant_trend = 'MIXED'
    
    # Determine ACTUAL (immediate) trend from 3-brick direction
    if immediate_up >= 2:
        actual_trend = 'UP'
    elif immediate_down >= 2:
        actual_trend = 'DOWN'
    else:
        actual_trend = 'MIXED'
    
    # Score based on signal alignment with ACTUAL trend
    # 🛡️ FEB 1 FIX: Also reduce score if dominant trend is against us
    if is_call:
        if actual_trend == 'UP':
            # CALL in uptrend = GOOD
            if short_net >= 3:
                components['trend_alignment'] = 30
            elif short_net >= 1:
                components['trend_alignment'] = 25
            elif short_net >= 0:
                components['trend_alignment'] = 20
            else:
                components['trend_alignment'] = 15  # Slight pullback OK
            # 🛡️ FEB 1 FIX: Reduce if dominant trend is DOWN (bounce in downtrend)
            if dominant_trend == 'DOWN':
                components['trend_alignment'] = max(0, components['trend_alignment'] - 15)
        elif actual_trend == 'MIXED':
            components['trend_alignment'] = 10 if dominant_trend != 'DOWN' else 5
        else:
            # CALL in downtrend = BAD (counter-trend)
            components['trend_alignment'] = 0
    
    elif is_put:
        if actual_trend == 'DOWN':
            # PUT in downtrend = GOOD
            if short_net <= -3:
                components['trend_alignment'] = 30
            elif short_net <= -1:
                components['trend_alignment'] = 25
            elif short_net <= 0:
                components['trend_alignment'] = 20
            else:
                components['trend_alignment'] = 15
            # 🛡️ FEB 1 FIX: Reduce if dominant trend is UP (dip in uptrend)
            if dominant_trend == 'UP':
                components['trend_alignment'] = max(0, components['trend_alignment'] - 15)
        elif actual_trend == 'MIXED':
            components['trend_alignment'] = 10 if dominant_trend != 'UP' else 5
        else:
            # PUT in uptrend = BAD (counter-trend)
            components['trend_alignment'] = 0
    
    # ============================================
    # COMPONENT 3: MOMENTUM (0-20 pts)
    # Check last 5 bricks for direction support
    # ============================================
    momentum_lookback = min(5, len(directions))
    recent_dirs = directions[-momentum_lookback:]
    
    if is_call:
        up_count = sum(1 for d in recent_dirs if d == 1)
        momentum_ratio = up_count / len(recent_dirs)
    else:
        down_count = sum(1 for d in recent_dirs if d == -1)
        momentum_ratio = down_count / len(recent_dirs)
    
    if momentum_ratio >= 0.8:
        components['momentum'] = 20
    elif momentum_ratio >= 0.6:
        components['momentum'] = 15
    elif momentum_ratio >= 0.4:
        components['momentum'] = 10
    else:
        components['momentum'] = 3
    
    # ============================================
    # COMPONENT 4: BREAKOUT (0-15 pts)
    # Check if breaking recent high/low
    # ============================================
    breakout_lookback = min(8, len(bricks) - 1)
    recent_bricks = bricks[-(breakout_lookback+1):-1]
    current_brick = bricks[-1]
    
    if len(recent_bricks) > 0:
        recent_high = max(recent_bricks)
        recent_low = min(recent_bricks)
        
        if is_call:
            if current_brick > recent_high:
                components['breakout'] = 15  # New high!
            elif current_brick >= recent_high - brick_size:
                components['breakout'] = 10  # Near high
            else:
                components['breakout'] = 5
        else:
            if current_brick < recent_low:
                components['breakout'] = 15  # New low!
            elif current_brick <= recent_low + brick_size:
                components['breakout'] = 10  # Near low
            else:
                components['breakout'] = 5
    
    # ============================================
    # COMPONENT 5: CLEAN MARKET (0-10 pts)
    # Count reversals - fewer = cleaner
    # ============================================
    clean_lookback = min(8, len(directions))
    recent_for_clean = directions[-clean_lookback:]
    
    reversals = 0
    for i in range(1, len(recent_for_clean)):
        if recent_for_clean[i] != recent_for_clean[i-1] and recent_for_clean[i] != 0:
            reversals += 1
    
    if reversals <= 1:
        components['clean_market'] = 10
    elif reversals <= 2:
        components['clean_market'] = 7
    elif reversals <= 3:
        components['clean_market'] = 4
    else:
        components['clean_market'] = 0
    
    # ============================================
    # BONUS: Counter-trend PENALTY
    # 🛡️ FEB 1 FIX: Use DOMINANT trend (20 bricks), not immediate trend!
    # This catches BUYCL signals during small bounces in major downtrends
    # ============================================
    counter_trend_penalty = 0
    if is_call and dominant_trend == 'DOWN':
        counter_trend_penalty = -15  # Heavy penalty for CALL in DOMINANT downtrend
        print(f"⚠️ COUNTER-TREND: BUYCL in dominant DOWN trend (net: {dominant_net:+.1f} over {dominant_lookback} bricks)")
    elif is_put and dominant_trend == 'UP':
        counter_trend_penalty = -15  # Heavy penalty for PUT in DOMINANT uptrend
        print(f"⚠️ COUNTER-TREND: BUYPT in dominant UP trend (net: {dominant_net:+.1f} over {dominant_lookback} bricks)")
    
    # ============================================
    # CALCULATE TOTAL SCORE
    # ============================================
    total_score = sum(components.values()) + counter_trend_penalty
    total_score = max(0, min(100, total_score))  # Clamp 0-100
    
    # ============================================
    # DETERMINE RECOMMENDATION
    # ============================================
    if total_score >= QUALITY_SCORE_THRESHOLD:
        recommendation = 'TAKE'
        reason = f"✅ HIGH QUALITY ({total_score}/100)"
    elif total_score >= QUALITY_SCORE_MEDIUM:
        recommendation = 'MAYBE'
        reason = f"⚠️ MEDIUM QUALITY ({total_score}/100)"
    else:
        recommendation = 'SKIP'
        reason = f"❌ LOW QUALITY ({total_score}/100)"
    
    # Add details
    reason += f" | Str:{components['trend_strength']} Align:{components['trend_alignment']} Mom:{components['momentum']} Brk:{components['breakout']} Cln:{components['clean_market']}"
    if counter_trend_penalty < 0:
        reason += f" | ⚠️ Counter-trend penalty: {counter_trend_penalty}"
    reason += f" | Net:{short_net:+.1f} Consec:{consecutive} Trend:{actual_trend} DomTrend:{dominant_trend}({dominant_net:+.1f})"
    
    return total_score, components, recommendation, reason


def should_take_trade(signal_type, renko_df, symbol):
    """
    Direction filter with two modes + QUALITY SCORING SYSTEM:
    
    When ENABLE_QUALITY_SCORING is True:
        - Uses quality score (0-100) to determine trade eligibility
        - Score >= QUALITY_SCORE_THRESHOLD (65): TAKE the trade
        - Score >= QUALITY_SCORE_MEDIUM (50): Take if appropriate
        - Score < 50: SKIP the trade
    
    When ENABLE_QUALITY_SCORING is False:
        - Falls back to legacy filter chain (direction, chop, proximity, etc.)
    
    Args:
        signal_type: 'BUYCL' (CALL) or 'BUYPT' (PUT)
        renko_df: Renko DataFrame
        symbol: Trading symbol
    
    Returns:
        (should_trade: bool, reason: str)
    """
    # Get entry price for logging
    entry_price = 0
    brick_size = 10  # Default
    if renko_df is not None and len(renko_df) > 0:
        entry_price = float(renko_df['Renko_Brick'].iloc[-1])
        # Detect brick size
        if len(renko_df) > 1:
            diffs = np.abs(np.diff(renko_df['Renko_Brick'].values))
            diffs = diffs[diffs > 0]
            if len(diffs) > 0:
                brick_size = np.median(diffs)
    
    # ============================================
    # 🔄 SIGNAL DEDUPLICATION CHECK (First!)
    # Prevents processing same signal 245 times (Jan 29 bug)
    # ============================================
    is_dup, dup_reason = is_duplicate_signal(symbol, signal_type, entry_price, brick_size)
    if is_dup:
        # Silently skip duplicates - don't flood logs
        return False, dup_reason
    
    # Get pattern for logging
    renko_pattern = get_renko_pattern(renko_df, 6)
    
    # ============================================
    # 🎯 QUALITY SCORING SYSTEM (PRIMARY)
    # ============================================
    if ENABLE_QUALITY_SCORING:
        score, components, recommendation, reason = calculate_signal_quality(
            renko_df, signal_type, symbol
        )
        
        # Extract additional info from reason for tracking
        consecutive = 0
        net_bricks = 0
        actual_trend = ""
        if "Consec:" in reason:
            try:
                consecutive = int(reason.split("Consec:")[1].split()[0])
            except:
                pass
        if "Net:" in reason:
            try:
                net_bricks = float(reason.split("Net:")[1].split()[0])
            except:
                pass
        if "Trend:" in reason:
            try:
                actual_trend = reason.split("Trend:")[1].split()[0]
            except:
                pass
        
        # Log quality score
        if QUALITY_SCORE_LOG_ALL:
            print(f"📊 QUALITY | {symbol} | {signal_type} | Score: {score}/100 | {recommendation}")
        
        if recommendation == 'TAKE':
            log_filter_decision(symbol, signal_type, entry_price, "ALLOWED", "-",
                              score, 0, reason, renko_pattern)
            # Track for outcome analysis
            track_signal_for_outcome(
                symbol=symbol,
                signal_type=signal_type,
                entry_price=entry_price,
                brick_size=brick_size,
                quality_score=score,
                decision="ALLOWED",
                blocked_by="-",
                reason=reason,
                renko_pattern=renko_pattern,
                consecutive=consecutive,
                net_bricks=net_bricks,
                actual_trend=actual_trend
            )
            return True, reason
        elif recommendation == 'MAYBE':
            # 🛡️ FEB 1 FIX: MAYBE signals should be BLOCKED, not allowed!
            # Score 60-69 is NOT high enough quality for entry
            log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "QUALITY_SCORE_MEDIUM",
                              score, 0, reason + " (medium quality - blocked)", renko_pattern)
            # Track for outcome analysis (to see if we're missing good trades)
            track_signal_for_outcome(
                symbol=symbol,
                signal_type=signal_type,
                entry_price=entry_price,
                brick_size=brick_size,
                quality_score=score,
                decision="BLOCKED",
                blocked_by="QUALITY_SCORE_MEDIUM",
                reason=reason + " (medium quality - blocked)",
                renko_pattern=renko_pattern,
                consecutive=consecutive,
                net_bricks=net_bricks,
                actual_trend=actual_trend
            )
            # BLOCK the trade - return False
            return False, f"MAYBE quality ({score}/100) - only HIGH quality (>={QUALITY_SCORE_THRESHOLD}) allowed"
        else:  # SKIP
            log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "QUALITY_SCORE",
                              score, 0, reason, renko_pattern)
            # Track BLOCKED signals too - this is the key insight!
            track_signal_for_outcome(
                symbol=symbol,
                signal_type=signal_type,
                entry_price=entry_price,
                brick_size=brick_size,
                quality_score=score,
                decision="BLOCKED",
                blocked_by="QUALITY_SCORE",
                reason=reason,
                renko_pattern=renko_pattern,
                consecutive=consecutive,
                net_bricks=net_bricks,
                actual_trend=actual_trend
            )
            return False, reason
    
    # ============================================
    # LEGACY FILTER CHAIN (Fallback when quality scoring disabled)
    # ============================================
    # Track filter results for logging
    net_bricks = 0
    reversals = 0
    
    if not ENABLE_DIRECTION_FILTER:
        log_filter_decision(symbol, signal_type, entry_price, "ALLOWED", "-", 
                           0, 0, "Direction filter disabled", renko_pattern)
        return True, "Direction filter disabled"
    
    # ============================================
    # STEP 0: BRICK RATE MONITOR (PROACTIVE - most important!)
    # ============================================
    # This catches SUDDEN chop based on rapid brick formation
    is_paused, pause_reason = check_brick_rate_pause(symbol)
    if is_paused:
        log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "BRICK_RATE",
                           0, 0, pause_reason, renko_pattern)
        return False, pause_reason
    
    # Update brick history from Renko data
    update_brick_history_from_renko(symbol, renko_df)
    
    # ============================================
    # STEP 1: CHOP DETECTOR (pattern-based)
    # ============================================
    is_choppy, reversals, chop_reason = detect_chop(renko_df)
    if is_choppy:
        log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "CHOP_DETECTOR",
                           0, reversals, chop_reason, renko_pattern)
        return False, chop_reason
    
    # ============================================
    # STEP 2: GET NET DIRECTION (needed for strength override)
    # ============================================
    # Choose direction function based on mode
    if DIRECTION_FILTER_MODE == "STRICT":
        direction, net_bricks = get_market_direction_strict(renko_df)
    else:
        direction, net_bricks = get_market_direction(renko_df)
    
    # ============================================
    # STEP 2.5: SIGNAL PROXIMITY FILTER (with strength override)
    # ============================================
    # Block if opposite signal appeared within last N bricks
    # BUT allow if net direction is strong enough (strength override)
    # BUYCL comes from BUYEN signal, BUYPT comes from SELEX signal
    raw_signal = "BUYEN" if signal_type == "BUYCL" else "SELEX"
    is_proximity_blocked, bricks_since, proximity_reason = check_signal_proximity(renko_df, raw_signal, net_bricks=net_bricks)
    if is_proximity_blocked:
        log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "SIGNAL_PROXIMITY",
                           net_bricks, reversals, proximity_reason, renko_pattern)
        return False, proximity_reason
    
    # ============================================
    # STEP 3: DIRECTION VALIDATION
    # ============================================
    if direction == 'UNKNOWN':
        log_filter_decision(symbol, signal_type, entry_price, "ALLOWED", "-",
                           net_bricks, reversals, "Not enough data - allowing trade", renko_pattern)
        return True, "Not enough data - allowing trade"
    
    if direction == 'CHOPPY':
        reason = f"🚫 CHOPPY market (net: {net_bricks:+.1f} bricks) - waiting for direction"
        log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "MIN_NET",
                           net_bricks, reversals, reason, renko_pattern)
        return False, reason
    
    # ============================================
    # STEP 3: DIRECTION STABILITY CHECK
    # ============================================
    is_stable, age_mins, stability_reason = check_direction_stability(symbol, signal_type, renko_df)
    if not is_stable:
        log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "STABILITY",
                           net_bricks, reversals, stability_reason, renko_pattern)
        return False, stability_reason
    
    # ============================================
    # FINAL CHECK: Direction vs Signal Type
    # ============================================
    if signal_type == 'BUYCL':
        if direction == 'UP':
            reason = f"✅ CALL allowed - UP ({reversals} rev, {net_bricks:+.1f} net, {age_mins:.1f} mins stable)"
            log_filter_decision(symbol, signal_type, entry_price, "ALLOWED", "-",
                               net_bricks, reversals, reason, renko_pattern)
            return True, reason
        else:
            reason = f"🚫 CALL blocked - DOWN ({reversals} rev, {net_bricks:+.1f} net)"
            log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "HYBRID_DIRECTION",
                               net_bricks, reversals, reason, renko_pattern)
            return False, reason
    
    elif signal_type == 'BUYPT':
        if direction == 'DOWN':
            reason = f"✅ PUT allowed - DOWN ({reversals} rev, {net_bricks:+.1f} net, {age_mins:.1f} mins stable)"
            log_filter_decision(symbol, signal_type, entry_price, "ALLOWED", "-",
                               net_bricks, reversals, reason, renko_pattern)
            return True, reason
        else:
            reason = f"🚫 PUT blocked - UP ({reversals} rev, {net_bricks:+.1f} net)"
            log_filter_decision(symbol, signal_type, entry_price, "BLOCKED", "HYBRID_DIRECTION",
                               net_bricks, reversals, reason, renko_pattern)
            return False, reason
    
    log_filter_decision(symbol, signal_type, entry_price, "ALLOWED", "-",
                       net_bricks, reversals, "Unknown signal type", renko_pattern)
    return True, "Unknown signal type"


def check_daily_loss_limit():
    """Check if daily loss limit exceeded (safety net). Thread-safe."""
    global _daily_pnl_points
    
    if not ENABLE_DAILY_LOSS_LIMIT:
        return True, "Daily loss limit disabled"
    
    with _pnl_lock:
        current_pnl = _daily_pnl_points
    
    if current_pnl <= -MAX_DAILY_LOSS_POINTS:
        return False, f"🛑 Daily loss limit hit: {current_pnl:.0f} pts"
    
    return True, f"Daily P&L: {current_pnl:+.0f} pts"


def check_time_filter(exchange: str = None):
    """
    Check if current time is within allowed trading hours.
    
    Args:
        exchange: Exchange to check (NSE_INDEX, BSE_INDEX, MCX, NFO, BFO).
                  If None, uses legacy global check.
    
    Returns:
        (can_trade: bool, reason: str)
    """
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    current_time = now.strftime("%H:%M")
    current_mins = now.hour * 60 + now.minute
    
    # If exchange specified, use per-exchange times
    if exchange:
        exchange_upper = exchange.upper()
        
        # Map derivative exchanges to their parent for time lookup
        time_exchange = exchange_upper
        if time_exchange in ("NFO",):
            time_exchange = "NSE_INDEX"
        elif time_exchange in ("BFO",):
            time_exchange = "BSE_INDEX"
        
        # Get exchange-specific times from the config dictionaries
        start_time = TRADING_START_TIMES.get(time_exchange)
        end_time = TRADING_END_TIMES.get(time_exchange)
        
        # Debug: Print if dictionary lookup fails
        if start_time is None or end_time is None:
            print(f"⚠️ DEBUG: check_time_filter - No timing config for {time_exchange}")
            print(f"   TRADING_START_TIMES keys: {list(TRADING_START_TIMES.keys())}")
            print(f"   TRADING_END_TIMES keys: {list(TRADING_END_TIMES.keys())}")
        
        if start_time and end_time:
            start_mins = start_time[0] * 60 + start_time[1]
            end_mins = end_time[0] * 60 + end_time[1]
            
            if current_mins < start_mins:
                return False, f"⏰ {exchange}: Too early (starts at {start_time[0]:02d}:{start_time[1]:02d})"
            
            if current_mins >= end_mins:
                return False, f"⏰ {exchange}: NO NEW TRADES (cutoff at {end_time[0]:02d}:{end_time[1]:02d})"
            
            return True, f"✅ {exchange}: Time OK ({current_time})"
        else:
            # If exchange timing not found, fall through to legacy check
            print(f"⚠️ WARNING: No per-exchange timing for '{time_exchange}', using legacy check")
    
    # ============================================
    # Legacy global check (for backward compatibility)
    # ============================================
    if NEW_TRADE_CUTOFF_HOUR is not None and now.hour >= NEW_TRADE_CUTOFF_HOUR:
        return False, f"⏰ NO NEW TRADES: Past {NEW_TRADE_CUTOFF_HOUR}:00 cutoff"
    
    if not ENABLE_TIME_FILTER:
        return True, "Time filter disabled"
    
    # Check if within blocked periods
    for start_str, end_str in TIME_FILTER_BLOCKED_PERIODS:
        start_h, start_m = map(int, start_str.split(":"))
        end_h, end_m = map(int, end_str.split(":"))
        
        blocked_start_mins = start_h * 60 + start_m
        blocked_end_mins = end_h * 60 + end_m
        
        if blocked_start_mins <= current_mins <= blocked_end_mins:
            return False, f"⏰ BLOCKED: {start_str}-{end_str} (volatile period)"
    
    # Check if within trading hours (legacy TRADING_START_TIME / TRADING_END_TIME)
    start_h, start_m = map(int, TRADING_START_TIME.split(":"))
    end_h, end_m = map(int, TRADING_END_TIME.split(":"))
    
    trading_start_mins = start_h * 60 + start_m
    trading_end_mins = end_h * 60 + end_m
    
    if current_mins < trading_start_mins:
        return False, f"⏰ TOO EARLY: Wait until {TRADING_START_TIME}"
    
    if current_mins > trading_end_mins:
        return False, f"⏰ TOO LATE: Trading ends at {TRADING_END_TIME}"
    
    return True, f"✅ Time OK: {current_time}"


def record_trade_pnl(entry_price, exit_price, position_type, brick_size):
    """Record P&L when trade closes. Thread-safe."""
    global _daily_pnl_points, _daily_trades
    
    if position_type in ["CALL", "BUYCL", "SELCL", "SELST"]:
        pnl = exit_price - entry_price
    else:
        pnl = entry_price - exit_price
    
    with _pnl_lock:
        _daily_pnl_points += pnl
        _daily_trades.append({'pnl': pnl, 'type': position_type})
        current_pnl = _daily_pnl_points
    
    print(f"📊 Trade P&L: {pnl:+.0f} pts | Daily: {current_pnl:+.0f} pts")
    return pnl


def reset_daily_pnl():
    """Reset at start of day. Thread-safe."""
    global _daily_pnl_points, _daily_trades, _consecutive_losses
    
    with _pnl_lock:
        _daily_pnl_points = 0
        _daily_trades = []
    
    with _loss_counter_lock:
        _consecutive_losses = 0
    
    print("✅ Daily P&L reset")


def getHigherHighs(data: np.array, order=5, K=2):
    """
    Return the index of the *confirmation bar* only (the latest in the K-run),
    not every point in the run.
    """
    high_idx = argrelextrema(data, np.greater, order=order)[0]  # ← uses the passed `order`
    highs = data[high_idx]

    confirmed = []
    ex_deque = deque(maxlen=K)
    for i, idx in enumerate(high_idx):
        if i > 0 and highs[i] < highs[i-1]:
            ex_deque.clear()
        ex_deque.append(idx)
        if len(ex_deque) == K:
            # record only the latest index in the K-sequence
            if not confirmed or confirmed[-1] != idx:
                confirmed.append(idx)
    return confirmed

def getHigherLows(data: np.array, order=5, K=2):
    low_idx = argrelextrema(data, np.less, order=order)[0]
    lows = data[low_idx]

    confirmed = []
    ex_deque = deque(maxlen=K)
    for i, idx in enumerate(low_idx):
        if i > 0 and lows[i] < lows[i-1]:
            ex_deque.clear()
        ex_deque.append(idx)
        if len(ex_deque) == K:
            if not confirmed or confirmed[-1] != idx:
                confirmed.append(idx)
    return confirmed

def getLowerHighs(data: np.array, order=5, K=2):
    high_idx = argrelextrema(data, np.greater, order=order)[0]
    highs = data[high_idx]

    confirmed = []
    ex_deque = deque(maxlen=K)
    for i, idx in enumerate(high_idx):
        if i > 0 and highs[i] > highs[i-1]:
            ex_deque.clear()
        ex_deque.append(idx)
        if len(ex_deque) == K:
            if not confirmed or confirmed[-1] != idx:
                confirmed.append(idx)
    return confirmed

def getLowerLows(data: np.array, order=5, K=2):
    low_idx = argrelextrema(data, np.less, order=order)[0]
    lows = data[low_idx]

    confirmed = []
    ex_deque = deque(maxlen=K)
    for i, idx in enumerate(low_idx):
        if i > 0 and lows[i] > lows[i-1]:
            ex_deque.clear()
        ex_deque.append(idx)
        if len(ex_deque) == K:
            if not confirmed or confirmed[-1] != idx:
                confirmed.append(idx)
    return confirmed
def get_trade_book(client):
    """
    Fetch the positions book from the client, validate the response, and return a DataFrame.
    If the response is invalid or an error occurs, print an appropriate error message and return None.
    
    :param client: The client instance to fetch the positions book.
    :return: A valid DataFrame if the response is successful, otherwise None.
    """
    try:
        # Fetch the positions book
        response = client.tradebook()
        
        # Check for error in response
        if response.get("status") == "error":
            error_message = response.get("message", "Unknown error occurred.")
            print(f"Error fetching trade book: {error_message}")
            return None
        
        # Check if the data is empty
        if response.get("data") == []:
            print("Trade book is empty.")
            return None
        
        # If data is valid, convert it to a DataFrame
        if "data" in response and isinstance(response["data"], list):
            trade_book_df = pd.DataFrame(response["data"])
            if trade_book_df.empty:
                print("Trade book contains no entries.")
                return None
            
            # Process the trade book: group by `orderid`
            processed_trade_book_df = (
                trade_book_df
                .groupby("orderid", as_index=False)
                .agg({
                    "action": "first",  # Retain the first action (BUY or SELL)
                    "exchange": "first",  # Retain the first exchange value
                    "product": "first",  # Retain the first product value
                    "symbol": "first",  # Retain the first symbol value
                    "timestamp": "first",  # Retain the first timestamp value
                    "average_price": "mean",  # Average the price
                    "quantity": "sum",  # Sum the quantity
                    "trade_value": "sum",  # Sum the trade value
                })
            )

            return processed_trade_book_df
        
        # If data format is unexpected
        print("Unexpected response format.")
        return None

    except Exception as e:
        # Handle any unexpected errors
        print(f"An error occurred while fetching the tradebook: {e}")
        return None


def get_positions_book(client):
    """
    Fetch the positions book from the client, validate the response, and return a DataFrame.
    If the response is invalid or an error occurs, print an appropriate error message and return None.
    
    :param client: The client instance to fetch the positions book.
    :return: A valid DataFrame if the response is successful, otherwise None.
    """
    try:
        # Fetch the positions book
        response = client.positionbook()
        
        # Check for error in response
        if response.get("status") == "error":
            error_message = response.get("message", "Unknown error occurred.")
            print(f"Error fetching positions book: {error_message}")
            return None
        
        # Check if the data is empty
        if response.get("data") == []:
            # print("Positions book is empty.")
            time.sleep(0.2)
            return None
        
        # If data is valid, convert it to a DataFrame
        if "data" in response and isinstance(response["data"], list):
            positions_book_df = pd.DataFrame(response["data"])
            if positions_book_df.empty:
                print("Positions book contains no entries.")
                return None
            return positions_book_df
        
        # If data format is unexpected
        print("Unexpected response format.")
        return None

    except Exception as e:
        # Handle any unexpected errors
        print(f"An error occurred while fetching the positions book: {e}")
        return None

def get_order_book(client):
    """
    Fetch the order book from the client, validate the response, and return a DataFrame.
    If the response is invalid or an error occurs, print an appropriate error message and return None.
    
    :param client: The client instance to fetch the order book.
    :return: A valid DataFrame if the response is successful, otherwise None.
    """
    try:
        # Fetch the order book
        response = client.orderbook()

        # Check for error in response
        if response.get("status") == "error":
            error_message = response.get("message", "Unknown error occurred.")
            print(f"Error fetching order book: {error_message}")
            return None

        # Extract the orders data
        orders_data = response.get("data", {}).get("orders", [])
        if not orders_data:
            print("Order book contains no entries.")
            return None

        # Convert orders data to a DataFrame
        order_book_df = pd.DataFrame(orders_data)

        # Check if the DataFrame is empty
        if order_book_df.empty:
            print("Order book DataFrame is empty.")
            return None

        return order_book_df

    except Exception as e:
        # Handle unexpected errors
        print(f"An error occurred while fetching the order book: {e}")
        return None
def get_net_qty_on_exchange(positions_book_df, symbol, exchange):
    """
    Get net quantity for a symbol on a specific exchange.
    """
    if positions_book_df is None or positions_book_df.empty:
        return 0
    
    # Map exchange if needed
    if symbol.upper().endswith(("CE", "PE", "FUT")):
        if exchange == "NSE_INDEX":
            query_exchange = "NFO"
        elif exchange == "BSE_INDEX":
            query_exchange = "BFO"
        else:
            query_exchange = exchange
    else:
        query_exchange = exchange
    
    # Find matching position
    for _, pos in positions_book_df.iterrows():
        pos_symbol = str(pos["symbol"]).upper()
        pos_exchange = str(pos.get("exchange", "")).upper()
        
        if pos_symbol == symbol.upper() and pos_exchange == query_exchange.upper():
            return int(pos.get("quantity", 0))
    
    return 0

def get_net_qty(positions_book_df, symbol, exchange=None):
    """
    Check if the symbol exists in the positions_book_df and return the Net Qty.
    Optionally filter by exchange.
    :param positions_book_df: DataFrame containing positions book details.
    :param symbol: The trading symbol to search for.
    :param exchange: Optional exchange to filter by.
    :return: Net Qty if the symbol exists, otherwise 0.
    """
    if positions_book_df is None or positions_book_df.empty:
        print("Empty or None positions_book_df passed to get_net_qty, returning quantity as 0")
        return 0
    
    # Filter by symbol
    mask = positions_book_df["symbol"] == symbol
    
    # If exchange is provided, also filter by exchange
    if exchange:
        mask = mask & (positions_book_df["exchange"].astype(str).str.upper() == exchange.upper())
    
    if mask.any():
        net_qty = positions_book_df.loc[mask, "quantity"].values[0]
        return net_qty
    else:
        return 0
    
# def get_net_qty(positions_book_df, symbol):
#     """
#     Check if the symbol exists in the positions_book_df and return the Net Qty.
#     :param positions_book_df: DataFrame containing positions book details.
#     :param symbol: The trading symbol to search for.
#     :return: Net Qty if the symbol exists, otherwise None.
#     """
#     if positions_book_df is None or positions_book_df.empty:
#         print("Empty or None positions_book_df passed to get_net_qty, returning quantity as 0")
#         return 0
#     if symbol in positions_book_df["symbol"].values:
#         net_qty = positions_book_df.loc[positions_book_df["symbol"] == symbol, "quantity"].values[0]
#         return net_qty
#     else:
#         return 0

def identify_swings(df):
    order = 5
    K = 2

    hh = df.iloc[getHigherHighs(df['high'].values, order, K)]
    df['HH'] = df['timestamp'].map(hh.groupby('timestamp')['high'].max())
    
    hl = df.iloc[getHigherLows(df['low'].values, order, K)]
    df['HL'] = df['timestamp'].map(hl.groupby('timestamp')['low'].min())
    
    ll = df.iloc[getLowerLows(df['low'].values, order, K)]
    df['LL'] = df['timestamp'].map(ll.groupby('timestamp')['low'].min())
    
    lh = df.iloc[getLowerHighs(df['high'].values, order, K)]
    df['LH'] = df['timestamp'].map(lh.groupby('timestamp')['high'].max()) 
    
    return df

def identify_renko_swings(df):
    order = 5
    K = 3

    hh = df.iloc[getHigherHighs(df['Renko_Brick'].values, order, K)]
    df['HH'] = df['timestamp'].map(hh.groupby('timestamp')['Renko_Brick'].max())
    
    hl = df.iloc[getHigherLows(df['Renko_Brick'].values, order, K)]
    df['HL'] = df['timestamp'].map(hl.groupby('timestamp')['Renko_Brick'].min())
    
    ll = df.iloc[getLowerLows(df['Renko_Brick'].values, order, K)]
    df['LL'] = df['timestamp'].map(ll.groupby('timestamp')['Renko_Brick'].min())
    
    lh = df.iloc[getLowerHighs(df['Renko_Brick'].values, order, K)]
    df['LH'] = df['timestamp'].map(lh.groupby('timestamp')['Renko_Brick'].max())
    
    return df

    
def get_renko_signal(renko_df_with_signals) :

    last_timestamp = renko_df_with_signals.iloc[-1].timestamp
    
    # Filter rows with the first timestamp
    rows_with_last_timestamp = renko_df_with_signals[renko_df_with_signals['timestamp'] == last_timestamp]

    # Check if any 'Signal' is not 'None'
    if (rows_with_last_timestamp[rows_with_last_timestamp['Signal'].isin(['SELEN', 'SELEX', 'SELRE', 'BUYEN', 'BUYEX', 'BUYRE'])]['Signal'].tolist() != []) :
        signal = rows_with_last_timestamp[rows_with_last_timestamp['Signal'].isin(['SELEN', 'SELEX', 'SELRE', 'BUYEN', 'BUYEX', 'BUYRE'])]['Signal'].tolist()[0]
        price  = rows_with_last_timestamp[rows_with_last_timestamp['Signal'].isin(['SELEN', 'SELEX', 'SELRE', 'BUYEN', 'BUYEX', 'BUYRE'])]['Renko_Brick'].tolist()[0]

    else : 
        signal = None
        price = None
        
    return [last_timestamp, signal, price]

def get_future_symbol(base_symbol: str) -> str:
    try:
        month_str = datetime.now().strftime('%b')  # e.g. "Jul"
        query = f"{base_symbol} FUT {month_str}"
        results = enhanced_search_symbols(query)
        # Expecting a non-empty list with objects having `.symbol`
        if results and getattr(results[0], "symbol", None):
            return results[0].symbol
    except Exception as e:
        # Log and fall back to original symbol
        print(f"[resolve_future_symbol] Failed for '{base_symbol}': {e}")
    return base_symbol  # fallback

# Add near your other global toggles
BLOCK_INDEX_FUTURES = True  # ✅ blocks FUT symbols in NSE_INDEX/BSE_INDEX

def _is_future_symbol(sym: str) -> bool:
    u = (sym or "").upper().strip()
    # safest for your current naming patterns
    return ("FUT" in u) or u.endswith("FUT")


def fetch_symbols_to_subscribe(file_name):
    try:
        symbols_df = read_csv(file_name)
        symbols_to_subscribe = symbols_df.copy()
        if not {'exchange', 'symbol', 'brick_size'}.issubset(symbols_df.columns):
            raise ValueError("CSV must contain 'exchange', 'symbol', and 'brick_size' columns.")

        # normalize exchange column based on symbol family
        symbols_to_subscribe['exchange'] = symbols_to_subscribe.apply(
            lambda r: normalize_exchange_for_symbol(r['symbol'], r['exchange']),
            axis=1
        )

        # ✅ 1) HARD BLOCK: remove any FUT symbols for NSE_INDEX/BSE_INDEX
        if BLOCK_INDEX_FUTURES:
            idx_ex_mask = symbols_to_subscribe['exchange'].astype(str).str.upper().isin(['NSE_INDEX', 'BSE_INDEX'])
            fut_mask = symbols_to_subscribe['symbol'].astype(str).apply(_is_future_symbol)
            drop_mask = idx_ex_mask & fut_mask

            if drop_mask.any():
                bad = symbols_to_subscribe.loc[drop_mask, ['exchange', 'symbol']]
                print(f"🚫 Blocking INDEX FUT symbols (NSE_INDEX/BSE_INDEX):\n{bad.to_string(index=False)}")

            symbols_to_subscribe = symbols_to_subscribe.loc[~drop_mask].copy()

        # NSE_INDEX rows
        nfo_mask = symbols_to_subscribe['exchange'].astype(str).str.upper().eq('NSE_INDEX')

        # Any option: ends with CE/PE (case-insensitive)
        is_option_mask = (
            symbols_to_subscribe['symbol']
            .astype(str).str.strip().str.upper().str.endswith(("CE", "PE"))
        )

        # ❌ 2) STOP converting NSE_INDEX spot roots to FUT when blocking is enabled
        if not BLOCK_INDEX_FUTURES:
            target_mask = nfo_mask & ~is_option_mask
            if target_mask.any():
                symbols_to_subscribe.loc[target_mask, 'symbol'] = (
                    symbols_to_subscribe.loc[target_mask, 'symbol']
                    .astype(str).str.strip()
                    .apply(get_future_symbol)
                )

        symbols_to_subscribe = symbols_to_subscribe.drop(columns=['brick_size'])
        return symbols_to_subscribe

    except Exception as e:
        print(f"Error reading symbols from CSV: {e}")
        return pd.DataFrame()

# def fetch_symbols_to_subscribe (file_name):
#     try:
#         symbols_df = read_csv(file_name)
#         symbols_to_subscribe = symbols_df.copy()
#         if not {'exchange', 'symbol', 'brick_size'}.issubset(symbols_df.columns):
#             raise ValueError("CSV must contain 'exchange', 'symbol', and 'brick_size' columns.")

#         # normalize exchange column based on symbol family
#         symbols_to_subscribe['exchange'] = symbols_to_subscribe.apply(
#             lambda r: normalize_exchange_for_symbol(r['symbol'], r['exchange']),
#             axis=1
#         )

#         # NSE_INDEX rows
#         nfo_mask = symbols_to_subscribe['exchange'].astype(str).str.upper().eq('NSE_INDEX')
# #AVH modified        
#         # # Case-sensitive: start with NIFTY or BANKNIFTY, end with CE or PE
#         # is_option_mask = (
#         #     symbols_to_subscribe['symbol']
#         #     .astype(str)
#         #     .str.strip()
#         #     .str.match(r'^(?:NIFTY|BANKNIFTY).*(?:CE|PE)$', na=False)
#         # )
#         # Any option: ends with CE/PE (case-insensitive)
#         is_option_mask = (
#             symbols_to_subscribe['symbol']
#             .astype(str).str.strip().str.upper().str.endswith(("CE", "PE"))
#         )        
#         # Only convert to FUT for NSE_INDEX symbols that are NOT options by this strict rule
#         target_mask = nfo_mask & ~is_option_mask

#         if target_mask.any():
#             symbols_to_subscribe.loc[target_mask, 'symbol'] = (
#                 symbols_to_subscribe.loc[target_mask, 'symbol']
#                 .astype(str)
#                 .str.strip()
#                 .apply(get_future_symbol)
#             )
#         symbols_to_subscribe = symbols_to_subscribe.drop(columns=['brick_size'])
#         return symbols_to_subscribe
        
#     except Exception as e:
#         print(f"Error reading symbols from CSV: {e}")
#         return pd.DataFrame()

def fetch_symbols_from_csv(file_name):
    try:
        symbols_df = read_csv(file_name)
        if not {'exchange', 'symbol', 'brick_size'}.issubset(symbols_df.columns):
            raise ValueError("CSV must contain 'exchange', 'symbol', and 'brick_size' columns.")
        return symbols_df
    except Exception as e:
        print(f"Error reading symbols from CSV: {e}")
        return pd.DataFrame()


def get_ltp(exchange, symbol):
    quote = client.quotes(symbol=symbol, exchange=exchange)
    ltp = quote.get('data', {}).get('ltp', None)
    if ltp is not None:
        return ltp
    else:
        print("LTP not found in the quote.")  
        

def ensure_initial_stop(trade_manager, csv_path="trade_manager.csv"):
    """
    Ensure that every BUYEN/BUYCL/BUYPT INPOSITION has an initial stop loss.
    - BUYEN/BUYCL (CALL) → SELST (stop BELOW entry)
    - BUYPT (PUT) → SELSP (stop ABOVE entry)
    If missing, create a new stop at the initial stop price.
    Finally, save the trade_manager to CSV.
    """
    changed = False
    trade_manager = read_csv(trade_manager_file)
    
    # ✅ FIX: Handle BOTH CALL and PUT positions
    active_positions = trade_manager[
        (trade_manager["renko_signal"].isin(["BUYEN", "BUYCL", "BUYPT"])) &
        (trade_manager["order_status"] == "INPOSITION")
    ]

    for _, pos_row in active_positions.iterrows():
        symbol   = str(pos_row["symbol"])
        exchange = str(pos_row.get("exchange", "NSE_INDEX"))
        signal_type = pos_row["renko_signal"]
        
        # Safe float conversion with None handling
        exec_price_val = pos_row.get("exec_price")
        entry_price_val = pos_row.get("entry_price")
        entry_px = exec_price_val if exec_price_val is not None else entry_price_val
        if entry_px is None:
            continue
        try:
            entry_px = float(entry_px)
        except (TypeError, ValueError):
            continue

        # ✅ FIX: Determine stop type based on position type
        if signal_type in ["BUYEN", "BUYCL"]:
            stop_signal = "SELST"
        elif signal_type == "BUYPT":
            stop_signal = "SELSP"
        else:
            continue

        # Check if stop already exists
        existing_sl = trade_manager[
            (trade_manager["symbol"] == symbol) &
            (trade_manager["exchange"] == exchange) &
            (trade_manager["renko_signal"] == stop_signal) &
            (trade_manager["order_status"].isin(["OPEN", "PLACED", "PENDING"]))
        ]
        if not existing_sl.empty:
            continue  # ✅ Stop already exists

        b = brick_for_runtime(symbol)
        qty = int(pos_row.get("quantity", 0)) or 0
        
        # ✅ FIX: Calculate stop price based on position type
        if stop_signal == "SELST":
            # CALL stop: BELOW entry
            sl_price = entry_px - INITIAL_STOP_BRICKS * b
        else:  # SELSP
            # PUT stop: ABOVE entry
            sl_price = entry_px + INITIAL_STOP_BRICKS * b
        
        new_sl = {
            "exchange": exchange,
            "timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "renko_signal": stop_signal,
            "entry_price": sl_price,
            "exec_price": None,
            "quantity": qty,
            "order_status": "OPEN",
            "orderid": None,
        }
        
        if stop_signal == "SELST":
            trade_manager = _replace_open_selst(
                trade_manager,
                symbol,
                exchange,
                float(new_sl["entry_price"]),
                qty_hint=qty
            )
        else:
            # For SELSP, append directly (similar logic to SELST)
            trade_manager = pd.concat([trade_manager, pd.DataFrame([new_sl])], ignore_index=True)
        
        changed = True
        print(f"🟢 Created initial {stop_signal} stop for {symbol} @ {sl_price}")

    # ✅ Always save CSV at the end
    trade_manager.to_csv(csv_path, index=False)
    if changed:
        print(f"💾 Trade manager saved to {csv_path} (initial stops ensured).")
    else:
        print(f"ℹ️ No new stops created. Trade manager saved to {csv_path}.")

    return trade_manager, changed

def _bricks_from_entry(entry_brick: float, cur_brick: float, brick_size: float) -> float:
    if entry_brick is None or cur_brick is None or brick_size in (None, 0):
        return 0.0
    return (cur_brick - entry_brick) / float(brick_size)

def maybe_trail_stop_after_bricks(
    trade_manager: pd.DataFrame,
    symbol: str,
    exchange: str,
    renko_df: pd.DataFrame,
    enable_after_bricks: float = TRAIL_ENABLE_AFTER_BRICKS,
    offset_bricks: float = TRAIL_OFFSET_BRICKS,
):
    """
    For an INPOSITION long (BUYEN), once price has moved ≥ enable_after_bricks from exec/entry,
    keep a trailing SELST at (latest up brick - offset_bricks * brick_size).
    On each new higher brick: cancel OPEN/PENDING/PLACED SELST and append a new one.
    Never move the stop down.
    """

    changed = False

    # Find the live long
    inpos = trade_manager[
        (trade_manager["symbol"] == symbol) &
        (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) &
        (trade_manager["renko_signal"] == "BUYEN") &
        (trade_manager["order_status"] == "INPOSITION")
    ].tail(1)

    if inpos.empty or renko_df is None or renko_df.empty:
        return trade_manager, changed

    # Base/brick context
    exec_px = first_valid_number(inpos.iloc[0].get("exec_price"), inpos.iloc[0].get("entry_price"))
    if exec_px is None:
        return trade_manager, changed

    brick_val = brick_for_runtime(symbol)
    if brick_val is None or brick_val == 0:
        _logger.warning(f"⚠️ Skipping trailing stop for {symbol}: brick_size is None/0")
        return trade_manager, changed
    b = float(brick_val)
    
    if renko_df.empty or len(renko_df) == 0:
        return trade_manager, changed
    cur_brick = float(renko_df["Renko_Brick"].iloc[-1])

    # Only begin trailing once ≥ N bricks in profit from exec/entry
    bricks_from_exec = (cur_brick - float(exec_px)) / b
    if bricks_from_exec < float(enable_after_bricks):
        return trade_manager, changed

    target_stop = round(cur_brick - offset_bricks * b, 2)

    # Check current open stop (if any)
    existing = trade_manager[
        (trade_manager["symbol"] == symbol) &
        (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) &
        (trade_manager["renko_signal"] == "SELST") &
        (trade_manager["order_status"].isin(["OPEN", "PLACED", "PENDING"]))
    ].tail(1)

    if not existing.empty:
        try:
            old_px = float(existing.iloc[0].get("entry_price"))
        except Exception:
            old_px = None
        # Do nothing if the new level isn't higher (never trail down)
        if (old_px is not None) and (target_stop <= old_px):
            return trade_manager, changed

    qty_hint = int(inpos.iloc[0].get("quantity", 0) or 0)
    trade_manager = _replace_open_selst(trade_manager, symbol, exchange, target_stop, qty_hint=qty_hint)
    print(f"🎯 Trailing SELST for {symbol}: bricks_from_exec={bricks_from_exec:.1f}, new_stop={target_stop:.2f}")
    return trade_manager, True


# ============================================
# 🎯 V5 UPGRADE: ENHANCED TRAILING STOP SYSTEM
# ============================================
# Tracks highest/lowest price since entry and adjusts stop accordingly

# Global tracker for highest/lowest prices since entry
_position_extremes = {}  # {(symbol, position_type): {'entry': price, 'extreme': price}}
_position_extremes_lock = threading.Lock()

def update_position_extreme(symbol, position_type, current_price, entry_price=None):
    """
    Track the highest (for CALL) or lowest (for PUT) price since entry.
    
    Args:
        symbol: Trading symbol
        position_type: 'CALL' or 'PUT'
        current_price: Current LTP
        entry_price: Entry price (only needed when opening new position)
    """
    key = (symbol, position_type)
    
    with _position_extremes_lock:
        if key not in _position_extremes:
            if entry_price is not None:
                _position_extremes[key] = {
                    'entry': entry_price,
                    'extreme': current_price
                }
        else:
            # Update extreme
            if position_type == 'CALL':
                # Track highest for CALL
                if current_price > _position_extremes[key]['extreme']:
                    _position_extremes[key]['extreme'] = current_price
            else:
                # Track lowest for PUT
                if current_price < _position_extremes[key]['extreme']:
                    _position_extremes[key]['extreme'] = current_price

def get_position_extreme(symbol, position_type):
    """Get the tracked extreme price for a position."""
    key = (symbol, position_type)
    with _position_extremes_lock:
        return _position_extremes.get(key)

def clear_position_extreme(symbol, position_type):
    """Clear tracking when position is closed."""
    key = (symbol, position_type)
    with _position_extremes_lock:
        if key in _position_extremes:
            del _position_extremes[key]

def calculate_trailing_stop_price(position_type, entry_price, current_price, extreme_price, brick_size):
    """
    Calculate the new trailing stop price.
    
    Logic:
    - At +1 brick profit: Move stop to BREAKEVEN (entry price)
    - At +2+ brick profit: Trail stop 1 brick behind extreme
    - Never move stop backwards
    
    Args:
        position_type: 'CALL' or 'PUT'
        entry_price: Original entry price
        current_price: Current LTP
        extreme_price: Highest (CALL) or Lowest (PUT) since entry
        brick_size: Renko brick size for this instrument
    
    Returns:
        (new_stop_price, reason) or (None, reason) if no change needed
    """
    # Validate brick_size to avoid division by zero
    if brick_size is None or brick_size <= 0:
        return None, "INVALID_BRICK_SIZE"
    
    def get_smart_trail_distance(profit_bricks):
        """Get trail distance based on profit level using tiered system."""
        if not ENABLE_SMART_TRAILING:
            return TRAIL_DISTANCE
        
        # Find the appropriate tier (highest tier that profit exceeds)
        trail_dist = TRAIL_DISTANCE  # default
        for min_profit, tier_distance in SMART_TRAIL_TIERS:
            if profit_bricks >= min_profit:
                trail_dist = tier_distance
        return trail_dist
    
    if position_type == 'CALL':
        # For CALL: profit when price goes UP
        profit_bricks = (extreme_price - entry_price) / brick_size
        
        if profit_bricks >= TRAIL_START_AFTER:
            # Smart tiered trail distance
            trail_dist = get_smart_trail_distance(profit_bricks)
            new_stop = extreme_price - (trail_dist * brick_size)
            return new_stop, f"TRAILING (+{profit_bricks:.1f} bricks, trail={trail_dist}, extreme={extreme_price:.2f})"
        elif profit_bricks >= TRAIL_BREAKEVEN_AFTER:
            # Move to breakeven
            return entry_price, f"BREAKEVEN (+{profit_bricks:.1f} bricks)"
        else:
            # Keep initial stop (1 brick below entry)
            return None, f"INITIAL (profit={profit_bricks:.1f} bricks, need {TRAIL_BREAKEVEN_AFTER})"
    
    elif position_type == 'PUT':
        # For PUT: profit when price goes DOWN
        profit_bricks = (entry_price - extreme_price) / brick_size
        
        if profit_bricks >= TRAIL_START_AFTER:
            # Smart tiered trail distance
            trail_dist = get_smart_trail_distance(profit_bricks)
            new_stop = extreme_price + (trail_dist * brick_size)
            return new_stop, f"TRAILING (+{profit_bricks:.1f} bricks, trail={trail_dist}, extreme={extreme_price:.2f})"
        elif profit_bricks >= TRAIL_BREAKEVEN_AFTER:
            # Move to breakeven
            return entry_price, f"BREAKEVEN (+{profit_bricks:.1f} bricks)"
        else:
            # Keep initial stop (1 brick above entry)
            return None, f"INITIAL (profit={profit_bricks:.1f} bricks, need {TRAIL_BREAKEVEN_AFTER})"
    
    return None, "UNKNOWN_POSITION_TYPE"


def apply_trailing_stop_for_position(trade_manager, symbol, exchange, position_type, 
                                      entry_price, current_ltp, brick_size):
    """
    Apply trailing stop logic for an active position.
    
    Args:
        trade_manager: DataFrame with trade data
        symbol: Trading symbol
        exchange: Exchange
        position_type: 'CALL' or 'PUT'
        entry_price: Original entry price
        current_ltp: Current last traded price
        brick_size: Renko brick size
    
    Returns:
        (updated_trade_manager, was_changed, new_stop_price)
    """
    if not ENABLE_TRAILING_STOP:
        return trade_manager, False, None
    
    # Update extreme tracker
    update_position_extreme(symbol, position_type, current_ltp, entry_price)
    
    # Get tracked extreme
    extreme_data = get_position_extreme(symbol, position_type)
    if extreme_data is None:
        return trade_manager, False, None
    
    extreme_price = extreme_data['extreme']
    
    # Calculate new stop
    new_stop, reason = calculate_trailing_stop_price(
        position_type, entry_price, current_ltp, extreme_price, brick_size
    )
    
    if new_stop is None:
        return trade_manager, False, None
    
    # Determine stop signal type
    stop_signal = "SELST" if position_type == "CALL" else "SELSP"
    
    # Find existing stop
    existing_stop = trade_manager[
        (trade_manager["symbol"] == symbol) &
        (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) &
        (trade_manager["renko_signal"] == stop_signal) &
        (trade_manager["order_status"].isin(["OPEN", "PLACED", "PENDING"]))
    ].tail(1)
    
    # Check if we should update
    should_update = False
    old_stop = None
    
    if not existing_stop.empty:
        try:
            old_stop = float(existing_stop.iloc[0].get("entry_price", 0))
        except:
            old_stop = None
        
        if old_stop is not None:
            if position_type == "CALL":
                # For CALL, only move stop UP (tighter)
                should_update = new_stop > old_stop
            else:
                # For PUT, only move stop DOWN (tighter)
                should_update = new_stop < old_stop
        else:
            should_update = True
    else:
        should_update = True
    
    if not should_update:
        return trade_manager, False, old_stop
    
    # Update the stop
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if not existing_stop.empty:
        # Update existing stop
        idx = existing_stop.index[-1]
        trade_manager.at[idx, "entry_price"] = round(new_stop, 2)
        trade_manager.at[idx, "timestamp"] = now
        _logger.info(f"🎯 TRAILING STOP {position_type} for {symbol}: {old_stop:.2f} → {new_stop:.2f} ({reason})")
    else:
        # Create new stop entry
        # Find the entry row to get quantity
        entry_signal = "BUYCL" if position_type == "CALL" else "BUYPT"
        entry_row = trade_manager[
            (trade_manager["symbol"] == symbol) &
            (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) &
            (trade_manager["renko_signal"] == entry_signal) &
            (trade_manager["order_status"] == "INPOSITION")
        ].tail(1)
        
        qty = int(entry_row.iloc[0].get("quantity", 75)) if not entry_row.empty else 75
        
        new_row = {col: np.nan for col in trade_manager.columns}
        new_row.update({
            "exchange": exchange,
            "timestamp": now,
            "symbol": symbol,
            "renko_signal": stop_signal,
            "entry_price": round(new_stop, 2),
            "exec_price": None,
            "quantity": qty,
            "order_status": "OPEN",
            "orderid": None,
        })
        trade_manager = concat_trade_row(trade_manager, new_row)
        _logger.info(f"🎯 TRAILING STOP {position_type} CREATED for {symbol}: {new_stop:.2f} ({reason})")
    
    print(f"🎯 Trailing {stop_signal} for {symbol}: {reason} → Stop @ {new_stop:.2f}")
    return trade_manager, True, round(new_stop, 2)


def process_trailing_stops_for_all_positions(trade_manager, get_ltp_func, brick_size_func):
    """
    Process trailing stops for all active CALL and PUT positions.
    Call this periodically (e.g., on each tick or every few seconds).
    
    Args:
        trade_manager: DataFrame with trade data
        get_ltp_func: Function to get LTP for a symbol
        brick_size_func: Function to get brick size for a symbol
    
    Returns:
        (updated_trade_manager, positions_count)
    """
    if not ENABLE_TRAILING_STOP:
        return trade_manager, 0
    
    changed = False
    positions_count = 0
    
    # Process CALL positions (BUYCL with INPOSITION status)
    call_positions = trade_manager[
        (trade_manager["renko_signal"] == "BUYCL") &
        (trade_manager["order_status"] == "INPOSITION")
    ]
    
    for _, pos in call_positions.iterrows():
        positions_count += 1
        try:
            symbol = str(pos["symbol"])
            exchange = str(pos.get("exchange", "NSE_INDEX"))
            entry_price = float(pos.get("entry_price", 0))
            
            if entry_price <= 0:
                continue
            
            # Get LTP with None check
            ltp_value = get_ltp_func(exchange, symbol)
            if ltp_value is None:
                _logger.warning(f"⚠️ Skipping trailing stop for CALL {symbol}: LTP is None")
                continue
            current_ltp = float(ltp_value)
            
            # Get brick size with None check
            brick_value = brick_size_func(symbol)
            if brick_value is None or brick_value == 0:
                _logger.warning(f"⚠️ Skipping trailing stop for CALL {symbol}: brick_size is None/0")
                continue
            brick_size = float(brick_value)
            
            trade_manager, was_changed, _ = apply_trailing_stop_for_position(
                trade_manager, symbol, exchange, "CALL",
                entry_price, current_ltp, brick_size
            )
            changed = changed or was_changed
        except Exception as e:
            _logger.warning(f"⚠️ Error processing trailing stop for CALL {symbol}: {e}")
    
    # Process PUT positions (BUYPT with INPOSITION status)
    put_positions = trade_manager[
        (trade_manager["renko_signal"] == "BUYPT") &
        (trade_manager["order_status"] == "INPOSITION")
    ]
    
    for _, pos in put_positions.iterrows():
        positions_count += 1
        try:
            symbol = str(pos["symbol"])
            exchange = str(pos.get("exchange", "NSE_INDEX"))
            entry_price = float(pos.get("entry_price", 0))
            
            if entry_price <= 0:
                continue
            
            # Get LTP with None check
            ltp_value = get_ltp_func(exchange, symbol)
            if ltp_value is None:
                _logger.warning(f"⚠️ Skipping trailing stop for PUT {symbol}: LTP is None")
                continue
            current_ltp = float(ltp_value)
            
            # Get brick size with None check
            brick_value = brick_size_func(symbol)
            if brick_value is None or brick_value == 0:
                _logger.warning(f"⚠️ Skipping trailing stop for PUT {symbol}: brick_size is None/0")
                continue
            brick_size = float(brick_value)
            
            trade_manager, was_changed, _ = apply_trailing_stop_for_position(
                trade_manager, symbol, exchange, "PUT",
                entry_price, current_ltp, brick_size
            )
            changed = changed or was_changed
        except Exception as e:
            _logger.warning(f"⚠️ Error processing trailing stop for PUT {symbol}: {e}")
    
    return trade_manager, positions_count


# def _replace_open_selst(trade_manager: pd.DataFrame,
#                         symbol: str,
#                         exchange: str,
#                         new_stop_price: float,
#                         qty_hint: int | None = None) -> pd.DataFrame:
#     exu = (exchange or "").upper()
#     now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

#     mask_open = (
#         trade_manager["symbol"].eq(symbol) &
#         trade_manager["exchange"].astype(str).str.upper().eq(exu) &
#         trade_manager["renko_signal"].eq("SELST") &
#         trade_manager["order_status"].eq("OPEN")
#     )

#     if mask_open.any():
#         # update-in-place (only move higher)
#         idx = trade_manager.index[mask_open][-1]
#         old = trade_manager.at[idx, "entry_price"]
#         if pd.isna(old) or float(new_stop_price) > float(old):
#             trade_manager.at[idx, "entry_price"] = float(new_stop_price)
#             trade_manager.at[idx, "timestamp"]   = now
#             print(f"🔁 Updated existing SELST → {new_stop_price:.2f} for {symbol}")
#         return trade_manager

#     # no OPEN stop exists → create exactly one
#     # choose qty from last BUYEN INPOSITION or hint
#     buyen = trade_manager[
#         (trade_manager["symbol"] == symbol) &
#         (trade_manager["exchange"].astype(str).str.upper() == exu) &
#         (trade_manager["renko_signal"] == "BUYEN") &
#         (trade_manager["order_status"] == "INPOSITION")
#     ].tail(1)
#     qty = int(buyen.iloc[0].get("quantity", 0)) if not buyen.empty else int(qty_hint or 0)
#     if qty <= 0:  # safe default per instrument
#         qty = 75

#     row = {col: np.nan for col in trade_manager.columns}
#     row.update({
#         "exchange": exchange,
#         "timestamp": now,
#         "symbol": symbol,
#         "renko_signal": "SELST",
#         "entry_price": float(new_stop_price),
#         "exec_price": None,
#         "quantity": qty,
#         "order_status": "OPEN",
#         "orderid": None,
#     })
#     if "id" in row:  # let SQLite autoincrement
#         row["id"] = np.nan

#     trade_manager = pd.concat([trade_manager, pd.DataFrame([row])], ignore_index=True)
#     print(f"🟢 Created initial SELST @ {new_stop_price:.2f} for {symbol}")
#     return trade_manager

def maybe_move_stop_to_breakeven(client, trade_manager):
    changed = False

    active_longs = trade_manager[
        (trade_manager["renko_signal"] == "BUYEN") &
        (trade_manager["order_status"] == "INPOSITION")
    ]

    if active_longs.empty:
        return trade_manager, False

    for _, long_row in active_longs.iterrows():
        symbol   = str(long_row["symbol"])
        exchange = str(long_row.get("exchange", "NSE_INDEX"))

        def first_valid_number(*vals):
            for v in vals:
                if v is None:
                    continue
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    continue
                if not pd.isna(f) and np.isfinite(f):
                    return f
            return None
        
        # entry_px = first_valid_number(long_row.get("exec_price"), long_row.get("entry_price"))
        entry_px = first_valid_number(long_row.get("entry_price"))
        if entry_px is None:
            print(f"⚠️ Skipping initial SELST for {symbol}: no valid entry/exec price on BUYEN row.")
            continue
        
        try:
            ltp_val = get_ltp(exchange, symbol)
            if ltp_val is None:
                continue
            ltp = float(ltp_val)
        except Exception:
            continue
        
        b = brick_for_runtime(symbol)
        if b is None or b == 0:
            continue
        b = float(b)
        
        # only trigger when price >= N bricks in profit
        if (ltp - entry_px) < (BREAKEVEN_AFTER_BRICKS * b):
            continue
        
        # --- AFTER (fixed) ---
        # desired new stop (one brick into profit)
        be_plus_one = round(entry_px + b, 2)
        
        # Upsert logic for SELST — update existing open stop instead of duplicating
        sel_mask = (
            (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) &
            (trade_manager["symbol"] == symbol) &
            (trade_manager["renko_signal"] == "SELST") &
            (trade_manager["order_status"] == "OPEN")
        )
        
        now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if sel_mask.any():
            idx = trade_manager.index[sel_mask][-1]
            old_px = trade_manager.at[idx, "entry_price"]
            # Only move up if higher
            if pd.isna(old_px) or be_plus_one > float(old_px):
                trade_manager.at[idx, "entry_price"] = be_plus_one
                trade_manager.at[idx, "timestamp"] = now
                print(f"🔁 Updated existing SELST → {be_plus_one} for {symbol}")
        else:
            qty = int(long_row.get("quantity", 0) or 0)
            # Build complete row to avoid FutureWarning on concat
            new_row = {col: np.nan for col in trade_manager.columns}
            new_row.update({
                "exchange": exchange,
                "timestamp": now,
                "renko_signal": "SELST",
                "symbol": symbol,
                "entry_price": be_plus_one,
                "exec_price": None,
                "quantity": qty,
                "order_status": "OPEN",
                "orderid": None,
                "ltp": np.nan,
            })
            # Leave id blank to let SQLite autoincrement
            if "id" in new_row:
                new_row["id"] = np.nan
            trade_manager = concat_trade_row(trade_manager, new_row)
            print(f"✅ Stop moved to entry+1 brick for {symbol}: {be_plus_one:.2f} (entry {entry_px:.2f}, LTP {ltp:.2f})")
        changed = True
    return trade_manager, changed

def _selex_gate(exchange: str, symbol: str, ltp: float, entry_price: float, inposition_qty: int) -> bool:
    """
    SELEX gate (LONGs):
      • During probe window (SELEX_PROBE_SECONDS):
          - Exit if LTP ≥ entry + 0.9 * brick_size  (your new rule)
          - OR if profit ≥ SELEX_TRAIL_AFTER_BRICKS and pullback ≥ SELEX_TRAIL_OFFSET_BRICKS
      • After window: exit unconditionally.
    """
    ex  = (exchange or "").upper().strip()
    sym = (symbol or "").upper().strip()
    ex = (exchange or "").upper()
    u  = (symbol or "").upper()
    # --- Brick size & multiplier (restore safe defaults) ---
    if u.startswith("NIFTY"):
        bs = 8.0
        buyen_mult = 0.5
    else:
        # Use your helper so non-index names still work
        brick_val = brick_for_runtime(symbol)
        bs = float(brick_val) if brick_val is not None and brick_val > 0 else 10.0
        buyen_mult = 1.0

    # If the long is gone, clear state and don't fire.
    if inposition_qty <= 0:
        with _selex_lock:
            _selex_pending.pop((ex, sym), None)
        return False


    now   = time.monotonic()
    entry = float(entry_price)
    ltp   = float(ltp)
    
    # Your new condition: ~+1.75 bricks profit before allowing exit (during probe)
    be_thresh = entry + 0.9 * bs

    with _selex_lock:
        rec = _selex_pending.get((ex, sym))
        if rec is None:
            rec = {
                "t0": now,
                "deadline": now + float(SELEX_PROBE_SECONDS),
                "entry": entry,
                "high": ltp,   # track best high since we started probing
            }
            _selex_pending[(ex, sym)] = rec
        else:
            if ltp > float(rec.get("high", ltp)):
                rec["high"] = ltp

        # ---- Profit giveback trailing (optional, still active) ----
        profit_bricks = (float(rec["high"]) - entry) / bs
        if profit_bricks >= float(SELEX_TRAIL_AFTER_BRICKS):
            pullback_ok = ltp <= float(rec["high"]) - float(SELEX_TRAIL_OFFSET_BRICKS) * bs
            if pullback_ok:
                _selex_pending.pop((ex, sym), None)
                return True

        # ---- Probe window logic (your 1.75-brick rule) ----
        if now <= float(rec["deadline"]):
            if ltp >= be_thresh:
                _selex_pending.pop((ex, sym), None)
                return True
            return False

        # ---- Window expired → force close ----
        _selex_pending.pop((ex, sym), None)
        return True
    
def stale_close_positions(trade_manager, symbol, renko_signal, entry_price, status_from, status_to, price_col="entry_price"):
    """
    Close only the rows that match:
      - symbol
      - renko_signal
      - order_status == status_from
      - entry_price == entry_price

    Returns: number of rows updated
    """
    mask = (
        (trade_manager["symbol"] == symbol) &
        (trade_manager["renko_signal"] == renko_signal) &
        (trade_manager["order_status"] == status_from) &
        (trade_manager[price_col] == entry_price)
    )

    updated = int(mask.sum())
    trade_manager.loc[mask, "order_status"] = status_to
    return updated
    
def should_execute_order(action, ltp, entry_price, inposition_qty, symbol, exchange):
    """
    Determine if an order should be executed based on the action, LTP, entry price, and position quantity.
    Handles all signal types: BUYCL, BUYPT, SELCL, SELPT, SELST, SELSP, BUYST, BUYEN, SELEX, etc.
    """
    # print(f"🔍 Checking {action} for {symbol}: LTP={ltp}, Entry={entry_price}, Position={inposition_qty}")
    
    # 🛡️ COOLDOWN CHECK - Prevent immediate re-entry after stop loss
    if action in ["BUYCL", "BUYPT"] and inposition_qty == 0:
        can_enter, cooldown_reason = check_reentry_cooldown(symbol, action, entry_price)
        if not can_enter:
            print(f"⏰🚫 {action} BLOCKED for {symbol}: {cooldown_reason}")
            # Log cooldown block
            if ENABLE_FILTER_LOGGING:
                log_filter_decision(symbol, action, entry_price, "BLOCKED", "COOLDOWN",
                                   0, 0, cooldown_reason, "")
            return False, 'OPEN'  # Keep as OPEN to retry later
    
    # NEW: First check if this signal is stale before anything else
    # if action in ["BUYCL", "BUYPT"]:
    #     if is_signal_stale(symbol, exchange, entry_price):
    #         print(f"⛔⛔ STALE SIGNAL BLOCKED {action} for {symbol}: too far from current Renko brick")
    #         return False, 'CLOSED'
        
    new_order_status = "INPOSITION"
    ex = (exchange or "").upper()
    # Get brick size for stale check
    u = (symbol or "").upper()
    bs = brick_for_runtime(symbol)
    
    # STALE SIGNAL CHECK for BUYCL/BUYPT with LTP
    if action in ["BUYCL", "BUYPT"] and inposition_qty == 0:
        if bs > 0:
            if action == "BUYCL":
                # CALL entry stale conditions with LTP
                # FIXED: Only stale if price went DOWN (missed the up move)
                # Price going UP is GOOD for CALL - that's the profitable direction!
                lower_stale_threshold = entry_price - (2 * bs)
                
                # REMOVED: ltp >= upper_stale_threshold check
                # When price rises above entry, CALL is profitable - NOT stale!
                
                if ltp <= lower_stale_threshold:
                    print(f"⛔⛔ STALE BUYCL BLOCKED for {symbol}: LTP {ltp} <= Entry {entry_price} - 2 bricks ({lower_stale_threshold}) - Price went DOWN, missed up move")
                    return False, 'CLOSED'
                # Note: If price rose (ltp > entry), CALL is working as intended - allow entry
                    
            elif action == "BUYPT":
                # PUT entry stale conditions with LTP
                # FIXED: Only stale if price went UP (missed the down move)
                # Price going DOWN is GOOD for PUT - that's the profitable direction!
                upper_stale_threshold = entry_price + (2 * bs)
                
                # REMOVED: ltp <= lower_stale_threshold check
                # When price drops below entry, PUT is profitable - NOT stale!
                
                if ltp >= upper_stale_threshold:
                    print(f"⛔⛔ STALE BUYPT BLOCKED for {symbol}: LTP {ltp} >= Entry {entry_price} + 2 bricks ({upper_stale_threshold}) - Price went UP, missed down move")
                    return False, 'CLOSED'
                # Note: If price dropped (ltp < entry), PUT is working as intended - allow entry
    
    # Calculate brick distance from entry using LTP
    brick_now = abs(ltp - entry_price) / bs if bs > 0 else 0
    
    # Get derivative exchange (uses module-level function)
    derivative_exchange = get_derivative_exchange_for_checking(exchange)
    
    # ============================================
    # GET CURRENT POSITIONS FOR CE AND PE SEPARATELY
    # ============================================
    positions_book_df = get_positions_book(client)
    ce_inposition_qty = 0
    pe_inposition_qty = 0
    
    if positions_book_df is not None and not positions_book_df.empty:
        # Get root of the symbol (e.g., NIFTY from NIFTY14NOV2524500CE)
        root = extract_root(symbol)
        
        for _, pos in positions_book_df.iterrows():
            pos_symbol = str(pos["symbol"]).upper()
            pos_qty = int(pos.get("quantity", 0))
            pos_exchange = str(pos.get("exchange", "")).upper()
            
            # Check if this position is for the same root and DERIVATIVE exchange
            if pos_qty != 0 and pos_symbol.startswith(root) and pos_exchange == derivative_exchange:
                if pos_symbol.endswith("CE"):
                    ce_inposition_qty += pos_qty
                elif pos_symbol.endswith("PE"):
                    pe_inposition_qty += pos_qty
    
    print(f"📊 Position Check for {symbol} on {derivative_exchange} - CE Qty: {ce_inposition_qty}, PE Qty: {pe_inposition_qty}")
    
    # ============================================
    # OPTION ENTRY SIGNALS (BUYCL, BUYPT) 🟢
    # ============================================
    if action == "BUYCL" and inposition_qty == 0:
        # 🎯 BIAS FILTER CHECK - Block CALL entries if bias is PUT only
        if TRADE_BIAS and TRADE_BIAS.upper() == "PUT":
            print(f"🚫🎯 BUYCL BLOCKED for {symbol}: TRADE_BIAS is set to 'PUT' - only PUT entries allowed")
            return False, 'CLOSED'
        
        # ============================================
        # 🔄 V5 FIX: RE-CHECK CHOP + DIRECTION AT EXECUTION TIME
        # ============================================
        # Market conditions might have changed since signal was generated!
        if ENABLE_DIRECTION_FILTER or ENABLE_CHOP_DETECTOR:
            try:
                # Get fresh Renko data
                renko_file = f"ohlcdata/{symbol}_renko.csv"
                if os.path.exists(renko_file):
                    renko_df = pd.read_csv(renko_file)
                    
                    # CHOP CHECK FIRST (most important!)
                    if ENABLE_CHOP_DETECTOR and len(renko_df) >= CHOP_LOOKBACK:
                        is_choppy, reversals, chop_reason = detect_chop(renko_df)
                        if is_choppy:
                            print(f"🚫🔄 BUYCL BLOCKED at execution for {symbol}: {chop_reason}")
                            return False, 'CLOSED'
                    
                    # DIRECTION CHECK
                    if ENABLE_DIRECTION_FILTER and len(renko_df) >= 3:
                        if DIRECTION_FILTER_MODE == "STRICT":
                            current_direction, net_bricks = get_market_direction_strict(renko_df)
                        else:
                            current_direction, net_bricks = get_market_direction(renko_df)
                        
                        if current_direction == 'DOWN':
                            print(f"🚫🔄 BUYCL BLOCKED at execution for {symbol}: Last bricks DOWN ({net_bricks:+.1f})")
                            return False, 'CLOSED'
                        elif current_direction == 'CHOPPY':
                            print(f"🚫🔄 BUYCL BLOCKED at execution for {symbol}: Market CHOPPY ({net_bricks:+.1f})")
                            return False, 'CLOSED'
                        else:
                            print(f"✅🔄 Execution checks passed for {symbol}: UP ({net_bricks:+.1f})")
            except Exception as e:
                print(f"⚠️ Execution re-check failed for {symbol}: {e}")
        
        # CALL entry: execute when LTP <= entry_price - offset * bs (buy at or below threshold)
        threshold = entry_price - ENTRY_OFFSET_BRICKS * bs
        print(f"🟢🟢 BUYCL Signal for {symbol} - LTP: {ltp}, Entry: {entry_price}, Threshold: {threshold}, Brick Dist: {brick_now:.2f}🟢🟢")
        
        # 🚫 NEW RULE: Don't allow CALL entry if PUT is in position
        if pe_inposition_qty > 0:
            print(f"🚫 BUYCL BLOCKED for {symbol}: PUT is already in position (Qty: {pe_inposition_qty}) on {derivative_exchange}")
            return False, 'OPEN'
        
        if ltp <= threshold:
            print(f"🟢🟢✅✅ BUYCL EXECUTED for {symbol} - Buying CALL at {ltp} (Threshold: {threshold}, Brick Dist: {brick_now:.2f})")
            return True, new_order_status
            
    elif action == "BUYPT" and inposition_qty == 0:
        # 🎯 BIAS FILTER CHECK - Block PUT entries if bias is CALL only
        if TRADE_BIAS and TRADE_BIAS.upper() == "CALL":
            print(f"🚫🎯 BUYPT BLOCKED for {symbol}: TRADE_BIAS is set to 'CALL' - only CALL entries allowed")
            return False, 'CLOSED'
        
        # ============================================
        # 🔄 V5 FIX: RE-CHECK CHOP + DIRECTION AT EXECUTION TIME
        # ============================================
        # Market conditions might have changed since signal was generated!
        if ENABLE_DIRECTION_FILTER or ENABLE_CHOP_DETECTOR:
            try:
                # Get fresh Renko data
                renko_file = f"ohlcdata/{symbol}_renko.csv"
                if os.path.exists(renko_file):
                    renko_df = pd.read_csv(renko_file)
                    
                    # CHOP CHECK FIRST (most important!)
                    if ENABLE_CHOP_DETECTOR and len(renko_df) >= CHOP_LOOKBACK:
                        is_choppy, reversals, chop_reason = detect_chop(renko_df)
                        if is_choppy:
                            print(f"🚫🔄 BUYPT BLOCKED at execution for {symbol}: {chop_reason}")
                            return False, 'CLOSED'
                    
                    # DIRECTION CHECK
                    if ENABLE_DIRECTION_FILTER and len(renko_df) >= 3:
                        if DIRECTION_FILTER_MODE == "STRICT":
                            current_direction, net_bricks = get_market_direction_strict(renko_df)
                        else:
                            current_direction, net_bricks = get_market_direction(renko_df)
                        
                        if current_direction == 'UP':
                            print(f"🚫🔄 BUYPT BLOCKED at execution for {symbol}: Last bricks UP ({net_bricks:+.1f})")
                            return False, 'CLOSED'
                        elif current_direction == 'CHOPPY':
                            print(f"🚫🔄 BUYPT BLOCKED at execution for {symbol}: Market CHOPPY ({net_bricks:+.1f})")
                            return False, 'CLOSED'
                        else:
                            print(f"✅🔄 Execution checks passed for {symbol}: DOWN ({net_bricks:+.1f})")
            except Exception as e:
                print(f"⚠️ Execution re-check failed for {symbol}: {e}")
        
        # PUT entry: execute when LTP >= entry_price + offset * bs (wait for BOUNCE)
        # V5 FIX: Wait for price to BOUNCE UP before buying PUT
        # This gives better entry - selling the bounce in a downtrend
        threshold = entry_price + ENTRY_OFFSET_BRICKS * bs
        print(f"🟣🟣 BUYPT Signal for {symbol} - LTP: {ltp}, Entry: {entry_price}, Bounce Threshold: {threshold}, Brick Dist: {brick_now:.2f}🟣🟣")
        
        # 🚫 NEW RULE: Don't allow PUT entry if CALL is in position
        if ce_inposition_qty > 0:
            print(f"🚫 BUYPT BLOCKED for {symbol}: CALL is already in position (Qty: {ce_inposition_qty}) on {derivative_exchange}")
            return False, 'OPEN'
        
        if ltp >= threshold:
            print(f"🟣🟣✅✅ BUYPT EXECUTED for {symbol} - Buying PUT at {ltp} (Bounced to: {threshold}, Brick Dist: {brick_now:.2f})")
            return True, new_order_status
            
    # ============================================
    # OPTION EXIT SIGNALS (SELCL, SELPT) 🔴
    # ============================================
    elif action == "SELCL" and inposition_qty > 0:
        # CALL exit: THREE CONDITIONS
        # Condition 1: Original profit-taking condition (if LTP >= entry_price)
        # Condition 2: Stop loss if price falls below entry by 1 brick
        # Condition 3: Time-based exit after 5 minutes
        
        print(f"🔴🔴 SELCL Signal for {symbol} - LTP: {ltp}, Entry: {entry_price}, Brick Dist: {brick_now:.2f}🔴🔴")
        
        # Check for trade timestamp to implement 5-minute rule
        try:
            trade_manager_df = read_csv(trade_manager_file)
            trade_row = trade_manager_df[
                (trade_manager_df["symbol"] == symbol) &
                (trade_manager_df["exchange"] == exchange) &
                (trade_manager_df["renko_signal"] == "BUYCL") &  # Find the BUYCL entry
                (trade_manager_df["order_status"] == "INPOSITION")
            ]
            
            if not trade_row.empty:
                entry_timestamp = pd.to_datetime(trade_row.iloc[0]["timestamp"])
                current_time = pd.Timestamp.now()
                time_diff_minutes = (current_time - entry_timestamp).total_seconds() / 60
                
                # CONDITION 1: Original profit-taking condition (price at or above entry)
                if ltp >= entry_price:
                    print(f"🔴🔴✅✅ SELCL EXECUTED (Profit Target) for {symbol} - Selling CALL at {ltp} (Above entry: {entry_price}, Brick Dist: {brick_now:.2f})🔴🔴")
                    clear_position_extreme(symbol, "CALL")  # Clear trailing stop tracker
                    return True, "CLOSED"
                
                # CONDITION 2: Stop loss if price falls 1 brick below entry
                threshold_condition2 = entry_price - bs  # 1 brick below entry
                if ltp <= threshold_condition2:
                    print(f"🔴🔴✅✅ SELCL EXECUTED (1-Brick Stop) for {symbol} - Selling CALL at {ltp} (Threshold: {threshold_condition2}, Brick Dist: {brick_now:.2f})🔴🔴")
                    clear_position_extreme(symbol, "CALL")  # Clear trailing stop tracker
                    return True, "CLOSED"
                
                # CONDITION 3: 5-minute timeout
                elif time_diff_minutes >= 5:
                    print(f"🔴🔴✅✅ SELCL EXECUTED (5-Minute Timeout) for {symbol} - Selling CALL at {ltp} (Time since entry: {time_diff_minutes:.1f} minutes)🔴🔴")
                    clear_position_extreme(symbol, "CALL")  # Clear trailing stop tracker
                    return True, "CLOSED"
                
                else:
                    print(f"⏳ SELCL waiting... {time_diff_minutes:.1f} minutes elapsed, need 5 minutes. Current: {ltp}, Entry: {entry_price}")
            else:
                print(f"⚠️ Could not find BUYCL entry for {symbol} in trade_manager")
        except Exception as e:
            print(f"⚠️ Error checking SELCL conditions for {symbol}: {e}")
            
    elif action == "SELPT" and inposition_qty > 0:
        # PUT exit: THREE CONDITIONS
        # Condition 1: Original profit-taking condition (if LTP <= entry_price)
        # Condition 2: Stop loss if price rises above entry by 1 brick
        # Condition 3: Time-based exit after 5 minutes
        
        print(f"🔴🔴 SELPT Signal for {symbol} - LTP: {ltp}, Entry: {entry_price}, Brick Dist: {brick_now:.2f}🔴🔴")
        
        # Check for trade timestamp to implement 5-minute rule
        try:
            trade_manager_df = read_csv(trade_manager_file)
            trade_row = trade_manager_df[
                (trade_manager_df["symbol"] == symbol) &
                (trade_manager_df["exchange"] == exchange) &
                (trade_manager_df["renko_signal"] == "BUYPT") &  # Find the BUYPT entry
                (trade_manager_df["order_status"] == "INPOSITION")
            ]
            
            if not trade_row.empty:
                entry_timestamp = pd.to_datetime(trade_row.iloc[0]["timestamp"])
                current_time = pd.Timestamp.now()
                time_diff_minutes = (current_time - entry_timestamp).total_seconds() / 60
                
                # CONDITION 1: Original profit-taking condition (price at or below entry)
                if ltp <= entry_price:
                    print(f"🔴🔴✅✅ SELPT EXECUTED (Profit Target) for {symbol} - Selling PUT at {ltp} (Below entry: {entry_price}, Brick Dist: {brick_now:.2f})🔴🔴")
                    clear_position_extreme(symbol, "PUT")  # Clear trailing stop tracker
                    return True, "CLOSED"
                
                # CONDITION 2: Stop loss if price rises 1 brick above entry
                threshold_condition2 = entry_price + bs  # 1 brick above entry
                if ltp >= threshold_condition2:
                    print(f"🔴🔴✅✅ SELPT EXECUTED (1-Brick Stop) for {symbol} - Selling PUT at {ltp} (Threshold: {threshold_condition2}, Brick Dist: {brick_now:.2f})🔴🔴")
                    clear_position_extreme(symbol, "PUT")  # Clear trailing stop tracker
                    return True, "CLOSED"
                
                # CONDITION 3: 5-minute timeout
                elif time_diff_minutes >= 5:
                    print(f"🔴🔴✅✅ SELPT EXECUTED (5-Minute Timeout) for {symbol} - Selling PUT at {ltp} (Time since entry: {time_diff_minutes:.1f} minutes)🔴🔴")
                    clear_position_extreme(symbol, "PUT")  # Clear trailing stop tracker
                    return True, "CLOSED"
                
                else:
                    print(f"⏳ SELPT waiting... {time_diff_minutes:.1f} minutes elapsed, need 5 minutes. Current: {ltp}, Entry: {entry_price}")
            else:
                print(f"⚠️ Could not find BUYPT entry for {symbol} in trade_manager")
        except Exception as e:
            print(f"⚠️ Error checking SELPT conditions for {symbol}: {e}")
            
    # ============================================
    # STOP LOSS SIGNALS (SELST, SELSP) 🛑
    # ============================================
    elif action == "SELST" and inposition_qty > 0:
        # CALL stop loss: execute when LTP <= entry_price
        # For LONG CALL position, stop loss triggers when price goes DOWN
        threshold = entry_price
        print(f"🟡🟡 SELST Stop Loss (CALL) for {symbol} - LTP: {ltp}, Entry: {entry_price}, Threshold: {threshold}, Brick Dist: {brick_now:.2f}🟡🟡")
        if ltp <= threshold:
            print(f"🔴🔴🟡🟡 SELST TRIGGERED for {symbol} - Stop Loss CALL at {ltp} (Threshold: {threshold}, Brick Dist: {brick_now:.2f})🟡🟡🔴🔴")
            clear_position_extreme(symbol, "CALL")  # Clear trailing stop tracker
            return True, "CLOSED"
            
    elif action == "SELSP" and inposition_qty > 0:
        # PUT stop loss: execute when LTP >= entry_price
        # For LONG PUT position, stop loss triggers when price goes UP (against put position)
        threshold = entry_price
        print(f"🔵🔵 SELSP Stop Loss (PUT) for {symbol} - LTP: {ltp}, Entry: {entry_price}, Threshold: {threshold}, Brick Dist: {brick_now:.2f}🔵🔵")
        if ltp >= threshold:
            print(f"🔴🔴🔵🔵 SELSP TRIGGERED for {symbol} - Stop Loss PUT at {ltp} (Threshold: {threshold}, Brick Dist: {brick_now:.2f})🔵🔵🔴🔴")
            clear_position_extreme(symbol, "PUT")  # Clear trailing stop tracker
            return True, "CLOSED"

    # print(f"❌ {action} Signal NOT executed for {symbol} - LTP: {ltp}, Entry/Trigger: {entry_price}, Position: {inposition_qty}, Brick Dist: {brick_now:.2f}")
    return False, 'OPEN'


def get_derivative_exchange_for_checking(exchange):
    """Map spot exchange to derivative exchange for options."""
    ex_upper = exchange.upper()
    if ex_upper in ["NSE_INDEX", "NSE-INDEX"]:
        return "NFO"
    elif ex_upper in ["BSE_INDEX", "BSE-INDEX"]:
        return "BFO"
    elif ex_upper == "MCX":
        return "MCX"
    else:
        return ex_upper


def get_call_put_position_qty(symbol, derivative_exchange):
    """Get current CALL and PUT position quantities for the same root."""
    positions_book_df = get_positions_book(client)
    ce_qty, pe_qty = 0, 0
    
    if positions_book_df is not None and not positions_book_df.empty:
        # Get root of the symbol
        root = extract_root(symbol)
        
        for _, pos in positions_book_df.iterrows():
            pos_symbol = str(pos["symbol"]).upper()
            pos_qty = int(pos.get("quantity", 0))
            pos_exchange = str(pos.get("exchange", "")).upper()
            
            # Check if this position is for the same root and DERIVATIVE exchange
            if pos_qty != 0 and pos_symbol.startswith(root) and pos_exchange == derivative_exchange:
                if pos_symbol.endswith("CE"):
                    ce_qty += pos_qty
                elif pos_symbol.endswith("PE"):
                    pe_qty += pos_qty
    
    return ce_qty, pe_qty



def get_exec_price(order_book, order_id):
    match = order_book[order_book["orderid"] == order_id]
    if not match.empty:
        return match.iloc[0]["price"]
    return None
def on_data_received(data):
    # User callback: always executed regardless of verbose mode
    print(f"MY CALLBACK: {data['symbol']} LTP: {data['data'].get('ltp')}")

print(f"\n=== Testing with verbose={VERBOSE_LEVEL} ===\n")
def subscribe_to_websocket(client, instruments_list):
    # client.subscribe_quote(instruments_list, on_data_received=on_data_received)
    client.subscribe_quote(instruments_list, on_data_received=on_quote_update)
    # client.subscribe_ltp(instruments_list, on_data_received=on_ltp_update)
    print("📡 Subscribing symbol for LTP:", instruments_list)

def calculate_trade_quantity(symbol, ltp, lot_size, account_balance):
    amount_to_invest = (INVESTMENT_PERCENT / 100.0) * account_balance
    lot_cost = ltp * lot_size
    num_lots = int(amount_to_invest // lot_cost)
    num_lots = max(num_lots, 1)
    quantity = num_lots * lot_size
    return quantity


def _stop_mult_for(exchange: str, symbol: str) -> float:
    """
    Exchange/symbol-specific stop-loss multiplier:
      - Used only for NSE_INDEX (index options/futures): 2.5 × brick
      - Other exchanges use 3 × brick_size rule directly in create_stop_loss()
    """
    ex = (exchange or "").upper().strip()
    sym = (symbol or "").upper().strip()
    if sym.startswith("NIFTY"):   # covers NIFTY options & futures
        return 2
    return 2

def brick_for_runtime(symbol: str, default=BRICK_SIZE) -> float:
    """
    Get brick size for a symbol from symbols_to_trade.csv.
    Falls back to default if not found.
    """
    try:
        # Read the symbols file
        symbols_df = read_csv(symbols_file)
        
        if symbols_df is not None and not symbols_df.empty:
            # Clean the symbol for matching
            clean_symbol = str(symbol).upper().strip()
            
            # Look for the symbol in the dataframe
            row = symbols_df[symbols_df["symbol"].astype(str).str.upper() == clean_symbol]
            
            if not row.empty:
                brick_size = row["brick_size"].iloc[0]
                # Return the brick size if valid
                if brick_size is not None and not pd.isna(brick_size) and float(brick_size) > 0:
                    return float(brick_size)
        
        # Fallback to default
        return float(default)
        
    except Exception as e:
        print(f"⚠️ Error getting brick size for {symbol}: {e}")
        return float(default)
    
# def brick_for_runtime(symbol: str, default=BRICK_SIZE) -> float:
#     s = (symbol or "").upper().strip()
#     ex_norm = normalize_exchange_for_symbol(s, "NSE_INDEX")

#     if ex_norm == "MCX":
#         # return cached once-per-session values if available
#         if s.startswith("CRUDEOIL"):
#             static_crude_bricks = getattr(sys.modules[__name__], "_CRUDE_BRICK_INIT_", None)
#             if static_crude_bricks is not None:
#                 return float(static_crude_bricks)
#         elif s.startswith("GOLDM"):
#             static_goldm_bricks = getattr(sys.modules[__name__], "_GOLDM_BRICK_INIT_", None)
#             if static_goldm_bricks is not None:
#                 return float(static_goldm_bricks)
#         elif s.startswith("SILVERM"):
#             static_silverm_bricks = getattr(sys.modules[__name__], "_SILVERM_BRICK_INIT_", None)
#             if static_silverm_bricks is not None:
#                 return float(static_silverm_bricks)
#         elif s.startswith("NATURALGAS"):
#             static_ng_bricks = getattr(sys.modules[__name__], "_NATURALGAS_BRICK_INIT_", None)
#             if static_ng_bricks is not None:
#                 return float(static_ng_bricks)

#         try:
#             ohlc_file = f"ohlcdata/{s}_ohlc.csv"
#             df = read_csv(ohlc_file)
#             if df is not None and not df.empty and "atr" in df.columns:
#                 atr_val = float(df["atr"].iloc[-1])

#                 if s.startswith("CRUDEOIL"):
#                     brick_val = 3
#                     setattr(sys.modules[__name__], "_CRUDE_BRICK_INIT_", brick_val)
#                     print(f"🧱 [INIT] CRUDEOIL brick_size = {brick_val} ")
#                     return float(brick_val)

#                 if s.startswith("GOLDM"):
#                     brick_val = 50
#                     setattr(sys.modules[__name__], "_GOLDM_BRICK_INIT_", brick_val)
#                     print(f"🧱 [INIT] GOLDM brick_size = {brick_val} ")
#                     return float(brick_val)

#                 if s.startswith("SILVERM"):
#                     brick_val = 10
#                     setattr(sys.modules[__name__], "_SILVERM_BRICK_INIT_", brick_val)
#                     print(f"🧱 [INIT] SILVERM brick_size = {brick_val} ")
#                     return float(brick_val)

#                 if s.startswith("NATURALGAS"):
#                     brick_val = 0.3
#                     setattr(sys.modules[__name__], "_NATURALGAS_BRICK_INIT_", brick_val)
#                     print(f"🧱 [INIT] NATURALGAS brick_size = {brick_val:.2f} ")
#                     return float(brick_val)

#                 # generic MCX fallback
#                 return float(round(atr_val) if atr_val > 2 else atr_val)

#         except Exception as e:
#             print(f"⚠️ Could not compute ATR brick for {s}: {e}")
#         return float(default)

#     # --- NSE_INDEX (Index Futures/Options) ---
#     if s.startswith("NIFTY"):
#         static_nifty_brick = getattr(sys.modules[__name__], "_NIFTY_BRICK_INIT_", None)
#         if static_nifty_brick is not None:
#             return float(static_nifty_brick)
#         try:
#             ohlc_file = f"ohlcdata/{s}_ohlc.csv"
#             df = read_csv(ohlc_file)
#             if df is not None and not df.empty and "atr" in df.columns:
#                 atr_val = float(df["atr"].iloc[-1])
#                 brick_val = 8
#                 setattr(sys.modules[__name__], "_NIFTY_BRICK_INIT_", brick_val)
#                 print(f"🧱 [INIT] NIFTY brick_size = {brick_val}")
#                 return float(brick_val)
#         except Exception as e:
#             print(f"⚠️ Could not compute ATR brick for {s}: {e}")
#         return 8.0  # fallback

#     if s.startswith("BANKNIFTY"):
#         static_bn_brick = getattr(sys.modules[__name__], "_BANKNIFTY_BRICK_INIT_", None)
#         if static_bn_brick is not None:
#             return float(static_bn_brick)
#         try:
#             ohlc_file = f"ohlcdata/{s}_ohlc.csv"
#             df = read_csv(ohlc_file)
#             if df is not None and not df.empty and "atr" in df.columns:
#                 atr_val = float(df["atr"].iloc[-1])
#                 brick_val = 16
#                 setattr(sys.modules[__name__], "_BANKNIFTY_BRICK_INIT_", brick_val)
#                 print(f"🧱 [INIT] BANKNIFTY brick_size = {brick_val} ")
#                 return float(brick_val)
#         except Exception as e:
#             print(f"⚠️ Could not compute ATR brick for {s}: {e}")
#         return 16.0  # fallback

#     # --- SENSEX special-case ---
#     if s.startswith("SENSEX"):
#         static_sensex_brick = getattr(sys.modules[__name__], "_SENSEX_BRICK_INIT_", None)
#         if static_sensex_brick is not None:
#             return float(static_sensex_brick)
#         try:
#             ohlc_file = f"ohlcdata/{s}_ohlc.csv"
#             df = read_csv(ohlc_file)
#             if df is not None and not df.empty and "atr" in df.columns:
#                 atr_val = float(df["atr"].iloc[-1])
#                 brick_val = 25
#                 setattr(sys.modules[__name__], "_SENSEX_BRICK_INIT_", brick_val)
#                 print(f"🧱 [INIT] SENSEX brick_size = {brick_val} ")
#                 return float(brick_val)
#         except Exception as e:
#             print(f"⚠️ Could not compute ATR brick for {s}: {e}")

#     # --- generic non-MCX fallback ---
#     try:
#         df = read_csv(symbols_file)
#         row = df[df["symbol"].astype(str).str.upper() == s].tail(1)
#         b = row["brick_size"].iloc[0] if not row.empty else None
#         if b is None or (isinstance(b, str) and b.strip() == ""):
#             return float(default)
#         return float(b)
#     except Exception:
#         return float(default)
    
def _replace_open_selst(trade_manager: pd.DataFrame,
                       symbol: str,
                       exchange: str,
                       new_stop_price: float,
                       qty_hint: int | None = None,
                       stop_signal: str = "SELST") -> pd.DataFrame:
    """
    Replace open stop orders for stop signals
    """
    exu = (exchange or "").upper()
    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    
    mask_open = (
        trade_manager["symbol"].eq(symbol) &
        trade_manager["exchange"].astype(str).str.upper().eq(exu) &
        trade_manager["renko_signal"].eq(stop_signal) &
        trade_manager["order_status"].eq("OPEN")
    )
    
    if mask_open.any():
        # Update existing stop
        idx = trade_manager.index[mask_open][-1]
        old = trade_manager.at[idx, "entry_price"]
        
        # Determine if we should update
        should_update = False
        if stop_signal in ["SELST", "SELSP"]:
            # For CALL stop (SELST) - only move up (higher price is better protection)
            # For PUT stop (SELSP) - only move down (lower price is better protection)
            if symbol.upper().endswith("CE"):  # CALL
                if pd.isna(old) or float(new_stop_price) > float(old):
                    should_update = True
            else:  # PUT
                if pd.isna(old) or float(new_stop_price) < float(old):
                    should_update = True
        
        if should_update:
            trade_manager.at[idx, "entry_price"] = float(new_stop_price)
            trade_manager.at[idx, "timestamp"] = now
            print(f"🔁 Updated existing {stop_signal} → {new_stop_price:.2f} for {symbol}")
        return trade_manager
    
    # Create new stop
    # Determine corresponding entry signal
    entry_map = {
        "SELST": "BUYCL",  # CALL stop
        "SELSP": "BUYPT",  # PUT stop
    }
    
    entry_signal = entry_map.get(stop_signal, "BUYEN")
    
    # Get quantity from entry position
    entry_row = trade_manager[
        (trade_manager["symbol"] == symbol) &
        (trade_manager["exchange"].astype(str).str.upper() == exu) &
        (trade_manager["renko_signal"] == entry_signal) &
        (trade_manager["order_status"] == "INPOSITION")
    ].tail(1)
    
    qty = int(entry_row.iloc[0].get("quantity", 0)) if not entry_row.empty else int(qty_hint or 0)
    if qty <= 0:
        qty = 75
    
    row = {col: np.nan for col in trade_manager.columns}
    row.update({
        "exchange": exchange,
        "timestamp": now,
        "symbol": symbol,
        "renko_signal": stop_signal,
        "entry_price": float(new_stop_price),
        "exec_price": None,
        "quantity": qty,
        "order_status": "OPEN",
        "orderid": None,
    })
    
    if "id" in row:
        row["id"] = np.nan
    
    trade_manager = concat_trade_row(trade_manager, row)
    print(f"🟢 Created initial {stop_signal} @ {new_stop_price:.2f} for {symbol}")
    return trade_manager

def create_stop_loss(trade, symbol, action, exec_price, brick_size, trade_manager, index, direction):
    """
    Create stop loss with specific signals for CALL and PUT positions:
    - BUYCL (CALL entry) → SELST (CALL stop loss)
    - BUYPT (PUT entry) → SELSP (PUT stop loss)
    Uses ENTRY_PRICE (Renko signal price) for stop calculation.
    """
    exchange = normalize_exchange_for_symbol(symbol, trade.get("exchange", "NSE_INDEX"))
    ex = (exchange or "").upper()
    
    brick_size = brick_for_runtime(symbol)
    
    # Use ENTRY PRICE for stop calculation (not execution price)
    entry_price = trade.get("entry_price")
    
    try:
        entry_px = float(entry_price)
    except Exception:
        print(f"⚠️ No valid entry price for stop on {symbol}; skipping stop creation")
        return trade_manager
    
    # Determine option type
    is_call = symbol.upper().endswith("CE")
    is_put = symbol.upper().endswith("PE")
    
    # Get the entry signal
    entry_signal = trade.get("renko_signal", "")
    
    # Determine stop signal and price
    stop_signal = None
    sl_price = None
    
    # Map entry signals to stop signals
    stop_map = {
        "BUYCL": "SELST",   # CALL entry → CALL stop loss
        "BUYPT": "SELSP",   # PUT entry → PUT stop loss
        "SELCL": "BUYST",   # CALL exit → CALL buy stop (short covering)
        "SELPT": "BUYST",   # PUT exit → PUT buy stop (short covering)
        "BUYEN": "SELST",   # Legacy signal (default to CALL stop)
        "SELEN": "BUYST",   # Legacy signal
    }
    
    # Get the appropriate stop signal
    stop_signal = stop_map.get(entry_signal)
    
    # Calculate stop price based on direction and option type
    # Uses global STOP_LOSS_BRICKS from config
    
    if stop_signal == "SELST":  # CALL stop loss
        # Stop below entry for CALL
        sl_price = entry_px - STOP_LOSS_BRICKS * brick_size
        print(f"📉 CALL Stop (SELST): entry={entry_px}, brick={brick_size}, stop={sl_price}")
    elif stop_signal == "SELSP":  # PUT stop loss
        # Stop above entry for PUT
        sl_price = entry_px + STOP_LOSS_BRICKS * brick_size
        print(f"📈 PUT Stop (SELSP): entry={entry_px}, brick={brick_size}, stop={sl_price}")
    elif stop_signal == "BUYST":  # Buy stop for short covering
        # For CALL short (SELCL): stop above entry
        # For PUT short (SELPT): stop below entry
        if entry_signal == "SELCL":  # CALL short covering
            sl_price = entry_px + STOP_LOSS_BRICKS * brick_size
            print(f"📈 CALL Buy Stop: entry={entry_px}, brick={brick_size}, stop={sl_price}")
        elif entry_signal == "SELPT":  # PUT short covering
            sl_price = entry_px - STOP_LOSS_BRICKS * brick_size
            print(f"📉 PUT Buy Stop: entry={entry_px}, brick={brick_size}, stop={sl_price}")
        else:
            sl_price = entry_px + STOP_LOSS_BRICKS * brick_size  # Default
    
    if stop_signal is None or sl_price is None:
        print(f"⚠️ Could not determine stop signal for {symbol} (entry_signal={entry_signal})")
        return trade_manager
    
    sl_price = round(sl_price, 2)
    
    # Create stop signal entry
    sl_signal = {
        "exchange": exchange,
        "timestamp": trade.get("timestamp", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")),
        "symbol": symbol,
        "renko_signal": stop_signal,
        "entry_price": sl_price,
        "exec_price": None,
        "quantity": int(trade_manager.loc[index, "quantity"]),
        "order_status": "OPEN",
        "orderid": None
    }
    
    # For stop loss signals (SELST, SELSP)
    if stop_signal in ["SELST", "SELSP"]:
        # Map to corresponding entry signal
        entry_map = {
            "SELST": "BUYCL",  # CALL stop for CALL entry
            "SELSP": "BUYPT",  # PUT stop for PUT entry
        }
        expected_entry = entry_map.get(stop_signal, "BUYEN")
        
        # Check if corresponding entry exists
        active_entry = trade_manager[
            (trade_manager["symbol"] == symbol) &
            (trade_manager["exchange"] == exchange) &
            (trade_manager["renko_signal"] == expected_entry) &
            (trade_manager["order_status"] == "INPOSITION")
        ]
        
        if active_entry.empty:
            print(f"↪️ Skipping {stop_signal} for {symbol}: no active {expected_entry} position")
            return trade_manager
        
        # Create or update stop
        qty_hint = int(trade_manager.loc[index, "quantity"])
        trade_manager = _replace_open_selst(trade_manager, symbol, exchange, sl_price, 
                                          qty_hint=qty_hint, stop_signal=stop_signal)
        print(f"🔴 Stop loss ({stop_signal}) added at {sl_price} for {symbol} (based on entry {entry_px})")
    
    # For buy stop signals (BUYST)
    elif stop_signal == "BUYST":
        # Map to corresponding short signal
        short_map = {
            "BUYST": ["SELCL", "SELPT"],  # Both CALL and PUT shorts
        }
        expected_short = ["SELCL", "SELPT"]  # Check for both
        
        # Check if corresponding short exists
        active_short = trade_manager[
            (trade_manager["symbol"] == symbol) &
            (trade_manager["exchange"] == exchange) &
            (trade_manager["renko_signal"].isin(expected_short)) &
            (trade_manager["order_status"] == "INPOSITION")
        ]
        
        if active_short.empty:
            print(f"↪️ Skipping {stop_signal} for {symbol}: no active short position")
            return trade_manager
        
        # Skip in LONG_ONLY mode
        if LONG_ONLY and stop_signal == "BUYST":
            print(f"🚫 Skipping {stop_signal} in LONG_ONLY mode for {symbol}")
            return trade_manager
        
        # Add buy stop order
        trade_manager = concat_trade_row(trade_manager, sl_signal)
        print(f"🔵 Buy stop ({stop_signal}) added at {sl_price} for {symbol} (based on entry {entry_px})")
    
    return trade_manager
# def create_stop_loss(trade, symbol, action, exec_price, brick_size, trade_manager, index, direction):
#     # Normalize exchange (CRUDEOIL→MCX, NIFTY→NSE_INDEX, etc.)
#     exchange = normalize_exchange_for_symbol(symbol, trade.get("exchange", "NSE_INDEX"))
#     ex = (exchange or "").upper()
#     # Always use the actual brick size from symbols_to_trade.csv
#     brick_size = brick_for_runtime(symbol)
#     # Derive a robust execution price for stop calc
#     if exec_price:
#         exec_px = exec_price
#     elif trade.get("exec_price"):
#         exec_px = trade.get("exec_price")
#     else:
#         exec_px = trade.get("entry_price")
#     try:
#         exec_px = float(exec_px)
#     except Exception:
#         print(f"⚠️ No valid exec price for stop on {symbol}; skipping stop creation")
#         return trade_manager
    
#     # --- exchange-specific stop logic ---
#     if ex == "NSE_INDEX":
#         # NSE_INDEX (index options/futures): multiplier-based
#         sl_multiplier = _stop_mult_for(exchange, symbol)
#         sl_offset = 2 * brick_size
#         if direction == "SELL":
#             sl_price = exec_px - sl_offset
#             stop_signal = "SELST"
#         else:
#             sl_price = exec_px + sl_offset
#             stop_signal = "BUYST"

#     elif ex in ("BSE_INDEX", "MCX"):
#         # BSE_INDEX (e.g., SENSEX) & MCX (commodities): ATR-derived bricks — use fixed 1.5× brick trail
#         sl_offset = 2 * brick_size
#         if direction == "SELL":
#             sl_price = exec_px - sl_offset   # long → place Sell Stop
#             stop_signal = "SELST"
#         else:
#             sl_price = exec_px + sl_offset   # short → place Buy Stop
#             stop_signal = "BUYST"

#     else:
#         # Fallback: behave like ATR-based markets
#         sl_offset = 2 * brick_size
#         if direction == "SELL":
#             sl_price = exec_px - sl_offset
#             stop_signal = "SELST"
#         else:
#             sl_price = exec_px + sl_offset
#             stop_signal = "BUYST"


#     sl_signal = {
#         "exchange": trade["exchange"],
#         "timestamp": trade["timestamp"],
#         "symbol": symbol,
#         "renko_signal": stop_signal,
#         "entry_price": sl_price,
#         "exec_price": None,
#         "quantity": int(trade_manager.loc[index, "quantity"]),
#         "order_status": "OPEN"
#     }
#     if stop_signal == "SELST":
#         active_long = trade_manager[
#             (trade_manager["symbol"] == symbol) &
#             (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) &
#             (trade_manager["renko_signal"] == "BUYEN") &
#             (trade_manager["order_status"] == "INPOSITION")
#         ]
#         if active_long.empty:
#             print(f"↪️ Skipping SELST creation for {symbol}: no active long.")
#             return trade_manager
    
#         qty_hint = int(trade_manager.loc[index, "quantity"])
#         trade_manager = _replace_open_selst(trade_manager, symbol, exchange, sl_price, qty_hint)
#         print(f"{'🔴' if stop_signal == 'SELST' else '🔵'} Stop loss ({stop_signal}) order added at {sl_price} for {symbol}")
#     else:
#         if LONG_ONLY and stop_signal == "BUYST":
#             print(f"🚫 Skipping BUYST creation in LONG_ONLY mode for {symbol}")
#             return trade_manager
#         trade_manager = pd.concat([trade_manager, pd.DataFrame([sl_signal])], ignore_index=True)
#         print(f"{'🔴' if stop_signal == 'SELST' else '🔵'} Stop loss ({stop_signal}) order added at {sl_price} for {symbol}")
#     return trade_manager

def close_positions(trade_manager, symbol, renko_signal, status_from, status_to, close_reason=None):
    mask = (
        (trade_manager["symbol"] == symbol) &
        (trade_manager["renko_signal"] == renko_signal) &
        (trade_manager["order_status"] == status_from)
    )
    trade_manager.loc[mask, "order_status"] = status_to
    if close_reason is not None:
        trade_manager.loc[mask, "close_reason"] = close_reason


def get_account_balance(client):
    try:
        response = client.funds()
#        print("FUNDS RESPONSE:", response)
        if response and response.get("status") == "success":
            return response["data"]["availablecash"]
    except Exception as e:
        print(f"Error fetching account balance: {e}")
    return None  # fallback



    
def on_ltp_update(msg):
    symbol = msg.get("symbol")
    ltp = msg.get("ltp")
    if not symbol or ltp is None:
        return
    safe_ltp_update(symbol, float(ltp))  # 🛡️ FEB 1 FIX: Thread-safe LTP update
    ltp_event_queue.put(symbol)

def on_quote_update(msg):
    symbol = msg.get("symbol")
    
    # Try multiple possible message formats
    # Format 1: LTP inside 'data' dict (most common)
    data = msg.get("data", {})
    ltp = data.get("ltp") or data.get("close")
    
    # Format 2: LTP at top level (some brokers)
    if ltp is None:
        ltp = msg.get("ltp") or msg.get("close")
    
    # Format 3: last_price field (some brokers)
    if ltp is None:
        ltp = data.get("last_price") or msg.get("last_price")
    
    if ltp is None:
        print(f"⚠️ No price found in tick for {symbol}! Raw msg: {msg}")
        return
    
    # Convert to float and validate
    try:
        ltp = float(ltp)
    except (ValueError, TypeError):
        print(f"⚠️ Invalid LTP value for {symbol}: {ltp}")
        return
    
    old_ltp, _ = safe_ltp_get(symbol)  # 🛡️ FEB 1 FIX: Thread-safe LTP read
    safe_ltp_update(symbol, ltp)  # 🛡️ FEB 1 FIX: Thread-safe LTP update
    
    # 📈 Update outcome tracking for this symbol's price movement
    try:
        update_signal_outcomes(symbol, ltp)
    except Exception as e:
        pass  # Don't let tracking errors affect trading
    
    # # Debug: Print when LTP actually changes
    # if old_ltp is not None and old_ltp != ltp:
    #     print(f"🔄 LTP UPDATE: {symbol} {old_ltp} → {ltp}")
    
    try:
        ltp_event_queue.put_nowait(symbol)
    except queue.Full:
        pass  # Queue full, skip notification
    # logging.info(msg)
    
# --- Utility: delete matching tables from SQLite DB ---
def delete_pattern_tables(patterns=("ohlc", "renko", "symbol")):
    """
    Delete all SQLite tables whose names start with any of the given prefixes.
    """
    from sqlalchemy import inspect
    insp = inspect(_engine)
    all_tables = insp.get_table_names()
    matches = [t for t in all_tables if any(t.startswith(p) for p in patterns)]

    if not matches:
        print(f"No matching tables found for patterns: {patterns}")
        return

    print(f"🗑️ Deleting tables: {matches}")
    with _engine.begin() as conn:
        for t in matches:
            conn.exec_driver_sql(f"DROP TABLE IF EXISTS {t};")
    print("✅ Tables deleted successfully.")

# Guard to prevent multiple cleanups
_cleanup_done = False

def clean_ohlc_files():
    """Clean OHLC files and tradebook. Only runs ONCE per session."""
    global _cleanup_done
    
    if _cleanup_done:
        print("⚠️ Cleanup already done this session, skipping...")
        return
    
    try:
        for entry in glob.glob("ohlcdata/*"):
            if os.path.isfile(entry):
                os.remove(entry)
                print(f"🗑️ Deleted file: {entry}")
            elif os.path.isdir(entry):
                shutil.rmtree(entry)
                print(f"🗑️ Deleted directory: {entry}")
                
        # Delete only the tradebook.db file
        tradebook_path = "db/tradebook.db"
        if os.path.exists(tradebook_path):
            os.remove(tradebook_path)
            print(f"🗑️ Deleted tradebook database: {tradebook_path}")
        
        _cleanup_done = True
        print("✅ Cleanup complete")

    except Exception as e:
        print(f"💀🔥 Error deleting files or directories: {e}")


def is_order_executed(client, order_id, strategy="Renko Python"):
    print(f"Checking for order execution: {order_id}")
    status_resp = client.orderstatus(order_id=order_id, strategy=strategy)
    data = status_resp.get("data", {})

    # Try both keys: 'order_status' or 'status'
    order_status = data.get("order_status") or data.get("status", "")
    order_status = order_status.lower()

    if order_status == "complete":
        print(f"✅ Order execution completed for: {order_id}, returning True")
        return True
    elif order_status == "rejected":
        print("💀🔥 Order was rejected. Exiting.")
        return False
    else:
        print(f"🕒 Order status is '{order_status}', not complete yet.")
        return False

def unsubscribe_low_ltp_symbols(client, ltp_dict, symbols_file, ltp_min, ltp_max, exchange="NSE_INDEX"):
    """
    Unsubscribe symbols not in symbols_file AND whose LTP is outside the [ltp_min, ltp_max] band.
    """
    allowed_symbols = set(read_csv(symbols_file)['symbol'].tolist())
    currently_subscribed = set(ltp_dict.keys())
    candidates = currently_subscribed - allowed_symbols

    to_unsubscribe = []
    for sym in candidates:
        ltp = ltp_dict.get(sym)
        if ltp is None:
            continue
        if (ltp < ltp_min) or (ltp > ltp_max):
            to_unsubscribe.append(sym)

    if not to_unsubscribe:
        print("No symbols to unsubscribe.")
        return

    to_unsubscribe  = [{"exchange": exchange, "symbol": sym} for sym in to_unsubscribe]
    try:
        if _ticker and to_unsubscribe:
            _ticker.unregisterSymbols(to_unsubscribe)
        for sym in to_unsubscribe:
            ltp_dict.pop(sym, None)            
            
        # client.unsubscribe_ltp(instruments_to_unsub)
        print(f"Unsubscribed from symbols (outside [{ltp_min}, {ltp_max}]): {to_unsubscribe}")
    except Exception as e:
        print(f"Error during unsubscribe: {e}")

    for sym in to_unsubscribe:
        ltp_dict.pop(sym, None)


def remove_symbol_from_csv(symbol, ltp, ltp_threshold, symbols_file=symbols_file):
    try:

        symbols_df = read_csv(symbols_file)
        
        if ltp < ltp_threshold:
            updated_df = symbols_df[symbols_df["symbol"] != symbol]            
            save_to_csv(updated_df, symbols_file)
            print(f"🗑️ Removed {symbol} from {symbols_file}")
    except Exception as e:
        print(f"⚠️ Error removing {symbol} from {symbols_file}: {e}")
def reconcile_manual_positions(client, trade_manager_path=trade_manager_file):
    """
    Backfill BUYEN/SELEN rows for positions opened outside the engine and
    attach the corresponding stop (SELST/BUYST).
    """
    tm = read_csv(trade_manager_path)
    pos_df = get_positions_book(client)
    if pos_df is None or pos_df.empty:
        return

    changed = False

    for _, p in pos_df.iterrows():
        symbol   = str(p["symbol"])
        exchange = str(p.get("exchange", "NSE_INDEX"))
        qty      = int(p["quantity"])
        avg_px   = float(p["average_price"])

        if qty == 0:
            continue
        
        # 🚫 LONG_ONLY: Skip short positions (negative qty)
        if qty < 0:
            print(f"⚠️ Skipping backfill for SHORT position {symbol}: qty={qty} (LONG_ONLY mode)")
            continue

        # Do we already have an in-position row for this side?
        side_signal = "BUYEN"  # Always BUYEN since we only allow longs
        exists = tm[
            (tm["symbol"] == symbol) &
            (tm["exchange"] == exchange) &
            (tm["renko_signal"] == side_signal) &
            (tm["order_status"] == "INPOSITION")
        ]
        if not exists.empty:
            continue  # already reconciled

        # Create synthetic entry row mirroring the live broker position
        entry_row = {
            "exchange": exchange,
            "timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "renko_signal": side_signal,
            "symbol": symbol,
            "entry_price": avg_px,          # store broker fill as entry ref
            "exec_price": avg_px,           # mark as executed
            "quantity": abs(qty),           # full net position
            "order_status": "INPOSITION",
            "orderid": None
        }
        tm = pd.concat([tm, pd.DataFrame([entry_row])], ignore_index=True)
        print(f"🧩 Reconciled manual {'LONG' if qty>0 else 'SHORT'} → {side_signal} @ {avg_px} for {symbol}")

        # Ensure we are subscribed & symbol present in symbols_to_trade
        try:
            subscribe_symbol(client, symbol, exchange)
        except Exception:
            pass
        try:
            # add (or keep) this symbol in symbols_to_trade with current BRICK_SIZE
            sym_df = read_csv(symbols_file)
            new_row = pd.DataFrame([{"exchange": exchange, "symbol": symbol, "brick_size": brick_for_runtime(symbol)}])
            if sym_df is None or sym_df.empty:
                save_to_csv(new_row, symbols_file)
            else:
                merged = (pd.concat([sym_df, new_row], ignore_index=True)
                          .drop_duplicates(subset=["symbol"]))
                save_to_csv(merged, symbols_file)
        except Exception as e:
            print(f"⚠️ Could not update symbols_to_trade for {symbol}: {e}")

        # Create stop against the reconciled exec price
        dirn = "SELL" if qty > 0 else "BUY"
        tm = create_stop_loss(entry_row, symbol, side_signal, avg_px, brick_for_runtime(symbol), tm, tm.index[-1], direction=dirn)

        changed = True

    if changed:
        save_to_csv(tm, trade_manager_path)

def resolve_renko_file_and_symbol(symbol: str, exchange: str):
    """
    Always return Renko file + DF for the INDEX/FUTURE, not the option.
    
    Cases handled:
      • OPTION (CE/PE) → redirect to index/future Renko (NIFTY, BANKNIFTY, SENSEX, BANKEX, MCX FUT)
      • FUTURE (…FUT) → use FUT renko
      • INDEX (NSE/BSE) → use index renko
      • Never use option Renko files
    """

    raw = sym = str(symbol).strip().upper()
    ex  = str(exchange).upper()

    # ---- 1. OPTION SYMBOL? (CE/PE) ----
    if sym.endswith(("CE", "PE")):
        # Extract index/future root: NIFTY / BANKNIFTY / CRUDEOILM / etc
        root = extract_root(sym)

        # Reconstruct the INDEX/FUT symbol
        # For NSE/BSE → index root
        if root in ("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"):
            target_symbol = root

        # For MCX → we must map root to nearest FUT (your DB already knows this)
        elif root in ("CRUDEOIL", "NATURALGAS", "GOLDM", "SILVERM"):
            fut = Utils.getNearestMCXFuture(root) 
            if fut:
                target_symbol = fut["tradingsymbol"].upper()
            else:
                return None, None, None
        else:
            return None, None, None

    # ---- 2. FUTURES (…FUT) ----
    elif sym.endswith("FUT"):
        target_symbol = sym

    # ---- 3. INDEX ROOTS ----
    elif sym in ("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"):
        target_symbol = sym

    # Unknown type → skip
    else:
        return None, None, None

    # ---- 4. Try renko file names for the resolved index/future ----
    # NOTE: Only include file patterns that actually exist and are supported by _rx_specs
    # The system writes to _renko.csv, not _renko_close.csv or _renko_hilo.csv
    candidates = [
        f"ohlcdata/{target_symbol}_renko.csv",      # Main Renko file (table: renko_{symbol})
        f"ohlcdata/{target_symbol}_FUT_renko.csv",  # Futures Renko file (table: renko_fut_{symbol})
    ]

    chosen_path, renko_df = None, None
    for path in candidates:
        df = read_csv(path)
        if df is not None and not df.empty:
            chosen_path, renko_df = path, df
            break

    if chosen_path is None:
        print(f"⚠️ No Renko file found for {target_symbol}")
        return None, None, None

    # ---- 5. n_symbol = ALWAYS index/future for renko ----
    n_symbol = target_symbol

    return chosen_path, renko_df, n_symbol

# def resolve_renko_file_and_symbol(symbol: str, exchange: str):
#     """
#     Pick the correct Renko file and the trading symbol.
#       - Options (…CE/PE): use <symbol>_renko.csv (or fallback to <symbol>_FUT_renko.csv)
#       - Futures (…FUT):   use <symbol>_renko.csv
#       - NSE_INDEX underlyings (not FUT, not options): map to FUT for trading, but read <symbol>_FUT_renko.csv if present.
#     Returns: (renko_path, renko_df, n_symbol) or (None, None, None)
#     """
#     sym = str(symbol).strip()
#     u = sym.upper()

#     is_option = u.endswith(("CE", "PE"))
#     is_future = u.endswith("FUT")

#     # try the common file names in order
#     candidates = [
#         f"ohlcdata/{sym}_renko.csv",
#         f"ohlcdata/{sym}_FUT_renko.csv",
#     ]

#     chosen_path, renko_df = None, None
#     for path in candidates:
#         df = read_csv(path)
#         if df is not None and not df.empty:
#             chosen_path, renko_df = path, df
#             break

#     if chosen_path is None:
#         return None, None, None

#     # normalized symbol used when placing orders
#     if is_option:
#         n_symbol = sym                        # trade the option as-is
#     elif exchange.upper() == "NSE_INDEX" and not is_future:
#         n_symbol = get_future_symbol(sym)     # map underlying to current FUT trading symbol
#     else:
#         n_symbol = sym                        # MCX futures, NSE_INDEX futures, etc.

#     return chosen_path, renko_df, n_symbol



def normalize_exchange_for_symbol(symbol: str, exchange: str) -> str:
    sym = (symbol or "").upper().strip()
    ex  = (exchange or "").upper().strip()

    # --- FIX: Options must ALWAYS inherit their original exchange ---
    if sym.endswith(("CE", "PE")):
        return ex

    # MCX futures & MCX roots
    if sym.startswith(("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER")):
        return "MCX"

    # NSE, BSE index roots
    if sym.startswith(("NIFTY", "BANKNIFTY")):
        return "NSE_INDEX"

    if sym.startswith(("SENSEX", "BANKEX")):
        return "BSE_INDEX"

    # Enforce canonical uppercase for spot index exchanges
    # 5) FUTURES: if symbol ends with FUT, use derivatives exchange
    if sym.endswith("FUT"):
        if sym.startswith(("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER")):
            return "MCX"
    if ex in {"NSE_INDEX", "BSE_INDEX"} or ex in {"NSE-INDEX", "BSE-INDEX"}:
        return "NSE_INDEX" if "NSE" in ex else "BSE_INDEX"
    
    return ex


# def normalize_exchange_for_symbol(symbol: str, exchange: str) -> str:
#     """
#     Force consistent exchange based on symbol family.
#     - CRUDEOIL → MCX
#     - NIFTY / BANKNIFTY → NSE_INDEX
#     - otherwise keep given exchange
#     """
#     sym = (symbol or "").upper().strip()
#     ex  = (exchange or "").upper().strip()

#     target = ex
#     if sym.startswith("CRUDEOIL") or sym.startswith("NATURALGAS"):
#         target = "MCX"
#     elif sym.startswith("NIFTY") or sym.startswith("BANKNIFTY"):
#         target = "NSE_INDEX"
#     elif sym.startswith("SENSEX") or sym.startswith("BANKEX"):
#         target = "BSE_INDEX"
#     if target != ex:
#         print(f"[normalize] {symbol}: {ex} → {target}")
#     return target

def first_valid_number(*vals):
    for v in vals:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not pd.isna(f) and np.isfinite(f):
            return f
    return None

def is_signal_stale(symbol: str, exchange: str, entry_price: float) -> bool:
    """
    Check if an OPEN signal is stale (too far from current Renko brick).
    Returns True if signal should be closed as stale.
    
    UPDATED LOGIC: Only mark stale if price moved AGAINST the trade direction.
    - BUYCL (CALL): Only stale if price DROPPED significantly (missed the up move)
    - BUYPT (PUT): Only stale if price ROSE significantly (missed the down move)
    
    If price moved IN FAVOR of the trade, that's actually GOOD - execute anyway!
    """
    try:
        # Get current Renko data
        _, renko_df, _ = resolve_renko_file_and_symbol(symbol, exchange)
        if renko_df is None or renko_df.empty:
            return False  # Can't determine, don't mark as stale
        
        current_renko_brick = renko_df['Renko_Brick'].iloc[-1] if not renko_df.empty else None
        brick_size = brick_for_runtime(symbol)
        
        if current_renko_brick is None or brick_size <= 0:
            return False
        
        # STALE_THRESHOLD: Number of bricks price must move AGAINST the trade
        STALE_THRESHOLD_BRICKS = 3
        
        # Check trade_manager to see what signal type this is
        trade_manager = read_csv(trade_manager_file)
        signal_row = trade_manager[
            (trade_manager["symbol"] == symbol) &
            (trade_manager["exchange"] == exchange) &
            (trade_manager["order_status"] == "OPEN") &
            (trade_manager["entry_price"] == entry_price)
        ]
        
        if not signal_row.empty:
            signal_type = signal_row.iloc[0]["renko_signal"]
            
            if signal_type == "BUYCL":
                # CALL: Only stale if price DROPPED more than threshold
                # If price went UP, that's good for a CALL - don't mark as stale!
                if current_renko_brick <= entry_price - (STALE_THRESHOLD_BRICKS * brick_size):
                    print(f"🧱 STALE CHECK: BUYCL {symbol} - Current {current_renko_brick} <= Entry {entry_price} - {STALE_THRESHOLD_BRICKS} bricks (DROPPED)")
                    return True
            
            elif signal_type == "BUYPT":
                # PUT: Only stale if price ROSE more than threshold
                # If price went DOWN, that's good for a PUT - don't mark as stale!
                if current_renko_brick >= entry_price + (STALE_THRESHOLD_BRICKS * brick_size):
                    print(f"🧱 STALE CHECK: BUYPT {symbol} - Current {current_renko_brick} >= Entry {entry_price} + {STALE_THRESHOLD_BRICKS} bricks (ROSE)")
                    return True
        
        return False
        
    except Exception as e:
        print(f"⚠️ Error in is_signal_stale for {symbol}: {e}")
        return False
    
def close_stale_open_signals():
    """
    Close OPEN BUYCL/BUYPT signals that are 2-3 bricks away from current Renko brick.
    """
    try:
        trade_manager = read_csv(trade_manager_file)
        if trade_manager.empty:
            print("ℹ️ Trade manager is empty, nothing to close.")
            return False
        
        open_signals = trade_manager[
            (trade_manager["renko_signal"].isin(["BUYCL", "BUYPT"])) &
            (trade_manager["order_status"] == "OPEN")
        ]
        
        if open_signals.empty:
            print("ℹ️ No OPEN BUYCL/BUYPT signals found.")
            return False
        
        changed = False
        closed_count = {"BUYCL": 0, "BUYPT": 0}
        
        for idx, signal in open_signals.iterrows():
            symbol = signal["symbol"]
            exchange = signal["exchange"]
            renko_signal = signal["renko_signal"]
            entry_price = float(signal["entry_price"])
            
            try:
                renko_file_name, renko_df, _ = resolve_renko_file_and_symbol(symbol, exchange)
                if renko_df is None or renko_df.empty:
                    print(f"⚠️ No Renko data for {symbol}, skipping...")
                    continue
                
                current_renko_brick = renko_df['Renko_Brick'].iloc[-1] if not renko_df.empty else None
                brick_size = brick_for_runtime(symbol)
                
                if current_renko_brick is None or brick_size <= 0:
                    print(f"⚠️ Invalid Renko data for {symbol}, skipping...")
                    continue
                
                should_close = False
                reason = ""
                
                # STALE_THRESHOLD: Number of bricks price must move AGAINST the trade
                STALE_THRESHOLD_BRICKS = 3
                
                if renko_signal == "BUYCL":
                    # CALL: Only stale if price DROPPED significantly (missed the up move)
                    # If price went UP, that's good for a CALL - don't close!
                    if current_renko_brick <= entry_price - (STALE_THRESHOLD_BRICKS * brick_size):
                        should_close = True
                        reason = f"Current brick {current_renko_brick} <= Entry {entry_price} - {STALE_THRESHOLD_BRICKS} bricks (DROPPED)"
                
                elif renko_signal == "BUYPT":
                    # PUT: Only stale if price ROSE significantly (missed the down move)
                    # If price went DOWN, that's good for a PUT - don't close!
                    if current_renko_brick >= entry_price + (STALE_THRESHOLD_BRICKS * brick_size):
                        should_close = True
                        reason = f"Current brick {current_renko_brick} >= Entry {entry_price} + {STALE_THRESHOLD_BRICKS} bricks (ROSE)"
                
                if should_close:
                    # Update the row in the database directly
                    with _engine.begin() as cn:
                        cn.execute(
                            text("""
                                UPDATE trade_manager 
                                SET order_status = 'CLOSED' 
                                WHERE symbol = :symbol 
                                  AND exchange = :exchange 
                                  AND renko_signal = :renko_signal 
                                  AND entry_price = :entry_price 
                                  AND order_status = 'OPEN'
                            """),
                            {
                                "symbol": symbol,
                                "exchange": exchange,
                                "renko_signal": renko_signal,
                                "entry_price": entry_price
                            }
                        )
                    
                    changed = True
                    closed_count[renko_signal] += 1
                    print(f"⛔⛔✅ Closed stale {renko_signal} for {symbol}: {reason}⛔⛔")
    
            except Exception as e:
                print(f"⚠️ Error processing {renko_signal} for {symbol}: {e}")
                continue
        
        if changed:
            print(f"💾 Closed {closed_count['BUYCL']} BUYCL and {closed_count['BUYPT']} BUYPT stale signals.")
            return True
        else:
            print("ℹ️ No stale signals to close.")
            return False
            
    except Exception as e:
        print(f"💥 Error in close_stale_open_signals: {e}")
        return False
    
# def close_stale_open_signals():
#     """
#     Close OPEN BUYCL/BUYPT signals that are 2-3 bricks away from current Renko brick.
#     Runs after update_trade_manager_with_new_signals() to clean up stale signals.
#     """
#     try:
#         trade_manager = read_csv(trade_manager_file)
#         if trade_manager.empty:
#             print("ℹ️ Trade manager is empty, nothing to close.")
#             return
        
#         # Get all OPEN BUYCL/BUYPT signals
#         open_signals = trade_manager[
#             (trade_manager["renko_signal"].isin(["BUYCL", "BUYPT"])) &
#             (trade_manager["order_status"] == "OPEN")
#         ]
        
#         if open_signals.empty:
#             print("ℹ️ No OPEN BUYCL/BUYPT signals found.")
#             return
        
#         changed = False
#         closed_count = {"BUYCL": 0, "BUYPT": 0}
#         condition_met = False
        
#         for idx, signal in open_signals.iterrows():
#             symbol = signal["symbol"]
#             exchange = signal["exchange"]
#             renko_signal = signal["renko_signal"]
#             entry_price = float(signal["entry_price"])
            
#             try:
#                 # Get current Renko data for this symbol
#                 renko_file_name, renko_df, _ = resolve_renko_file_and_symbol(symbol, exchange)
#                 if renko_df is None or renko_df.empty:
#                     print(f"⚠️ No Renko data for {symbol}, skipping...")
#                     continue
                
#                 # Get current brick and brick size
#                 current_renko_brick = renko_df['Renko_Brick'].iloc[-1] if not renko_df.empty else None
#                 brick_size = brick_for_runtime(symbol)
                
#                 if current_renko_brick is None or brick_size <= 0:
#                     print(f"⚠️ Invalid Renko data for {symbol}, skipping...")
#                     continue
                
#                 # Check conditions based on signal type
#                 should_close = False
#                 reason = ""
                
#                 if renko_signal == "BUYCL":
#                     # CALL entry: close if current brick is >= entry + 2 bricks OR <= entry - 3 bricks
#                     if current_renko_brick >= entry_price + (2 * brick_size):
#                         should_close = True
#                         condition_met = True
#                         reason = f"Current brick {current_renko_brick} >= Entry {entry_price} + 2 bricks"
#                     elif current_renko_brick <= entry_price - (3 * brick_size):
#                         should_close = True
#                         reason = f"Current brick {current_renko_brick} <= Entry {entry_price} - 3 bricks"
                
#                 elif renko_signal == "BUYPT":
#                     # PUT entry: close if current brick is <= entry - 2 bricks OR >= entry + 3 bricks
#                     if current_renko_brick <= entry_price - (2 * brick_size):
#                         should_close = True
#                         condition_met = True
#                         reason = f"Current brick {current_renko_brick} <= Entry {entry_price} - 2 bricks"
#                     elif current_renko_brick >= entry_price + (3 * brick_size):
#                         should_close = True
#                         condition_met = True
#                         reason = f"Current brick {current_renko_brick} >= Entry {entry_price} + 3 bricks"
                
#                 if should_close:
#                     trade_manager.loc[idx, "order_status"] = "CLOSED"
#                     changed = True
#                     condition_met = True
#                     closed_count[renko_signal] += 1
#                     print(f"⛔⛔✅ Closed stale {renko_signal} for {symbol}: {reason}⛔⛔")
    
#             except Exception as e:
#                 print(f"⚠️ Error processing {renko_signal} for {symbol}: {e}")
#                 continue
        
#         # Save if changes were made
#         if changed:
#             save_to_csv(trade_manager, trade_manager_file)
#             print(f"💾 Closed {closed_count['BUYCL']} BUYCL and {closed_count['BUYPT']} BUYPT stale signals.")
#         else:
#             # print("ℹ️ No stale signals to close.")
#             changed = False
#         # return changed         
#         # return changed             
#         # # Schedule: 1-minute cadence starting 09:15:08
#         # now = datetime.now()
#         # start_hour = 9
#         # start_minute = 15
#         # interval_minutes = 1

#         # next_time = now.replace(hour=start_hour, minute=start_minute, second=15, microsecond=0)
#         # while next_time <= now:
#         #     next_time += timedelta(minutes=interval_minutes)

#         # time_to_wait = (next_time - now).total_seconds()
#         # print(f"🕰️ Next data fetch scheduled at {next_time}")
#         # time.sleep(time_to_wait)
            
#     except Exception as e:
#         print(f"💥 Error in close_stale_open_signals: {e}")

        
def update_trade_manager_with_new_signals():
    """
    Generate signals and update trade_manager based on position quantities.
    Independent decisions for exits and entries:
    
    BUYEN signal:
    1. If PUT position exists for same root → exit PUT (SELPT) → independent decision
    2. If no CALL position for same root AND no PUT position → enter CALL (BUYCL) → independent decision
    
    SELEX signal:
    1. If CALL position exists for same root → exit CALL (SELCL) → independent decision  
    2. If no PUT position for same root AND no CALL position → enter PUT (BUYPT) → independent decision
    
    NEW RULE: If any position is active (CALL or PUT), don't allow opposite entry signal
    """
    global TRADING_ENABLED
    # Add throttle - update at most once per 10 seconds
    current_time = time.time()
    if hasattr(update_trade_manager_with_new_signals, '_last_run'):
        if current_time - update_trade_manager_with_new_signals._last_run < 10:
            return
    
    update_trade_manager_with_new_signals._last_run = current_time
    try:
        # Stop entirely once one-and-done flip has occurred
        if ONE_AND_DONE and not TRADING_ENABLED:
            return

        # ✅ FIX: Check if ANY exchange is open before processing signals
        # This prevents "No Renko file found" errors when market is closed
        any_exchange_open = False
        for ex in ["NSE_INDEX", "BSE_INDEX", "MCX"]:
            is_open, _ = is_exchange_within_trading_hours(ex)
            if is_open:
                any_exchange_open = True
                break
        
        if not any_exchange_open:
            # All markets closed - silently skip signal processing
            return

        trade_manager = read_csv(trade_manager_file)
        symbols_df = read_csv(symbols_file)
        
        # Get current positions
        positions_book_df = get_positions_book(client)
        
        # Build root-wise position counts
        root_positions = defaultdict(lambda: {"CALL": 0, "PUT": 0})
        root_position_symbols = defaultdict(lambda: {"CALL": None, "PUT": None})
        
        if positions_book_df is not None and not positions_book_df.empty:
            for _, pos in positions_book_df.iterrows():
                symbol = str(pos["symbol"])
                qty = int(pos.get("quantity", 0))
                if qty == 0:
                    continue
                    
                # Extract root (e.g., NIFTY from NIFTY14NOV2524500CE)
                root = extract_root(symbol)
                
                if symbol.upper().endswith("CE"):
                    root_positions[root]["CALL"] += qty
                    if root_position_symbols[root]["CALL"] is not None:
                        root_position_symbols[root]["CALL"] = symbol
                elif symbol.upper().endswith("PE"):
                    root_positions[root]["PUT"] += qty
                    if root_position_symbols[root]["PUT"] is not None:
                        root_position_symbols[root]["PUT"] = symbol

        for _, row in symbols_df.iterrows():
            symbol = row['symbol']
            exchange = normalize_exchange_for_symbol(symbol, row['exchange'])
            derivative_exchange = map_to_derivative_exchange(exchange)
            
            # ✅ FIX: Skip symbols whose exchange is CLOSED - no point fetching Renko
            is_open, reason = is_exchange_within_trading_hours(exchange)
            if not is_open:
                # Silently skip closed exchanges - no need to spam logs
                continue
            
            try:
                # --- find Renko file + normalized symbol for any index/commodity/options/futures ---
                renko_file_name, renko_df, n_symbol = resolve_renko_file_and_symbol(symbol, exchange)

                if renko_df is None or renko_df.empty:
                    print(f"⏳ Waiting for Renko for {symbol} ({exchange}) — tried _renko and _FUT_renko")
                    continue

                timestamp, renko_signal, entry_price = get_renko_signal(renko_df)
                
                # Get brick size for this symbol
                brick_size = brick_for_runtime(symbol)

                # Get current brick from Renko for closing conditions
                current_renko_brick = renko_df['Renko_Brick'].iloc[-1] if not renko_df.empty else None
                

                                 
                option_type = "CE" if renko_signal == "BUYEN" else "PE"  ### getting side
                
                ce_symbol, pe_symbol = get_current_option_symbols(client, symbol, exchange)
                if renko_signal == "BUYEN":
                    trading_symbol_ce = ce_symbol
                if renko_signal == "SELEX":
                    trading_symbol_pe = pe_symbol 
                
                if renko_signal is None:
                    continue
                
                # 🚫 Ignore raw stop signals from Renko — managed logic creates SELST/BUYST
                if renko_signal in {"SELST", "BUYST"}:
                    print(f"🔕 Ignoring raw {renko_signal} from Renko; managed stops will handle it.")
                    continue
                
                # 🚫 LONG_ONLY: Block all SHORT entry signals (SELEN, SELRE, SELEN_SW)
                if renko_signal in {"SELEN", "SELRE", "SELEN_SW"}:
                    print(f"🚫 Blocking {renko_signal} signal for {symbol}: LONG_ONLY mode - no shorting allowed")
                    continue
                
                # Extract root for position checking
                root = extract_root(n_symbol)
                call_qty = root_positions[root]["CALL"]
                put_qty = root_positions[root]["PUT"]
                
                print(f"🧬 ROOT POS | {root} | CALL={call_qty} | PUT={put_qty}")
                
                signals_to_add = []
                
                # ==========================================================
                # BUYEN SIGNAL PROCESSING (Two independent decisions)
                # ==========================================================
                if renko_signal == "BUYEN":
                    print(f"🔔 Processing BUYEN for {n_symbol}")
                    
                    # DECISION 1: Exit PUT if PUT position exists (independent)
                    if put_qty > 0:
                        put_symbol = symbol
                        if put_symbol:
                            print(f"🔻 EXIT PUT → SELPT for {put_symbol}")
                            
                            # Find the active PUT trade
                            active_put_trades = trade_manager[
                                (trade_manager["symbol"] == put_symbol) &
                                (trade_manager["exchange"] == exchange) &
                                (trade_manager["renko_signal"].isin(["BUYPT", "BUYEN"])) &
                                (trade_manager["order_status"] == "INPOSITION")
                            ]
                            
                            if not active_put_trades.empty:
                                active_trade = active_put_trades.iloc[0]
                                real_entry = float(active_trade.get("entry_price", 0))
                                
                                # EXIT ON OPPOSITE SIGNAL: Exit regardless of profit/loss
                                # This is the TRUE RENKO WAY - trust the signal!
                                if ENABLE_EXIT_ON_OPPOSITE_SIGNAL:
                                    exit_qty = int(active_trade.get("quantity", 0) or 0)
                                    pnl_bricks = (real_entry - entry_price) / brick_size  # For PUT: profit when price goes down
                                    pnl_type = "PROFIT" if entry_price <= real_entry else "LOSS"
                                    print(f"🔄 EXIT ON OPPOSITE SIGNAL: BUYEN appeared, exiting PUT ({pnl_type}: {abs(pnl_bricks):.1f} bricks)")
                                    signals_to_add.append({
                                        "signal": "SELPT",
                                        "symbol": put_symbol,
                                        "entry_price": entry_price,
                                        "quantity": exit_qty,
                                        "reason": f"PUT exit on BUYEN signal ({pnl_type})"
                                    })
                                # Original behavior: Only exit if profitable (for PUT: exit price ≤ entry)
                                elif entry_price <= real_entry:
                                    exit_qty = int(active_trade.get("quantity", 0) or 0)
                                    signals_to_add.append({
                                        "signal": "SELPT",
                                        "symbol": put_symbol,
                                        "entry_price": entry_price,
                                        "quantity": exit_qty,
                                        "reason": "PUT exit triggered by BUYEN"
                                    })
                                else:
                                    print(f"🛑 Skipping PUT exit: exit price {entry_price} ≥ entry {real_entry}")
                    # DECISION 2: Always enter CALL (without checking positions)
                    # Calculate limit_entry_price (1 brick below for BUYCL)
                    limit_entry_price_buycl = entry_price - brick_size
                    
                    # ============================================
                    # V5: FILTERS TO AVOID WHIPSAW
                    # ============================================
                    
                    # Check 0: Time filter (avoid volatile periods)
                    can_trade, time_reason = check_time_filter(exchange=exchange)
                    if not can_trade:
                        print(f"🚫⏰ BUYCL BLOCKED for {n_symbol}: {time_reason}")
                    
                    # Check 1: Daily loss limit (safety net)
                    elif True:  # Continue chain
                        can_trade, loss_reason = check_daily_loss_limit()
                        if not can_trade:
                            print(f"🚫💰 BUYCL BLOCKED for {n_symbol}: {loss_reason}")
                    
                        # Check 2: Direction filter (need STRONG trend)
                        elif ENABLE_DIRECTION_FILTER:
                            can_trade, direction_reason = should_take_trade('BUYCL', renko_df, n_symbol)
                            if not can_trade:
                                print(f"🚫📈 BUYCL BLOCKED for {n_symbol}: {direction_reason}")
                            else:
                                print(f"✅ {direction_reason}")
                                print(f"🟢 ENTER CALL → BUYCL for {n_symbol}")
                                lot = get_lotsize(derivative_exchange, trading_symbol_ce)
                                desired_lots = int(FIXED_NUM_LOTS) if (FIXED_NUM_LOTS is not None) else 1
                                if desired_lots <= 0:
                                    desired_lots = 1
                                qty_for_entry = desired_lots * max(1, int(lot or 1))
                                
                                signals_to_add.append({
                                    "signal": "BUYCL",
                                    "symbol": n_symbol,
                                    "entry_price": entry_price,
                                    "limit_entry_price": limit_entry_price_buycl,
                                    "quantity": qty_for_entry,
                                    "reason": "CALL entry triggered by BUYEN"
                                })
                        
                        # No direction filter - just trade
                        else:
                            print(f"🟢 ENTER CALL → BUYCL for {n_symbol} (no filters)")
                            lot = get_lotsize(derivative_exchange, trading_symbol_ce)
                            desired_lots = int(FIXED_NUM_LOTS) if (FIXED_NUM_LOTS is not None) else 1
                            if desired_lots <= 0:
                                desired_lots = 1
                            qty_for_entry = desired_lots * max(1, int(lot or 1))
                            
                            signals_to_add.append({
                                "signal": "BUYCL",
                                "symbol": n_symbol,
                                "entry_price": entry_price,
                                "limit_entry_price": limit_entry_price_buycl,
                                "quantity": qty_for_entry,
                                "reason": "CALL entry triggered by BUYEN"
                            })
               

                    # # DECISION 2: Enter CALL only if NO CALL position AND NO PUT position
                
                    # if call_qty == 0 and put_qty == 0:
                    #     print(f"🟢 ENTER CALL → BUYCL for {n_symbol}")
                    #     # lot = get_lotsize(exchange, n_symbol)
                    #     lot = get_lotsize(derivative_exchange, trading_symbol_ce)
                    #     desired_lots = int(FIXED_NUM_LOTS) if (FIXED_NUM_LOTS is not None) else 1
                    #     if desired_lots <= 0:
                    #         desired_lots = 1
                    #     qty_for_entry = desired_lots * max(1, int(lot or 1))
                        
                    #     signals_to_add.append({
                    #         "signal": "BUYCL",
                    #         "symbol": n_symbol,
                    #         "entry_price": entry_price,
                    #         "quantity": qty_for_entry,
                    #         "reason": "CALL entry triggered by BUYEN"
                    #     })
                    # elif call_qty > 0:
                    #     print(f"🚫 Skipping BUYCL: CALL position already active for {root} (qty={call_qty})")
                    # elif put_qty > 0:
                    #     print(f"🚫 Skipping BUYCL: PUT position active for {root} (qty={put_qty})")
                
                # ==========================================================
                # SELEX SIGNAL PROCESSING (Two independent decisions)
                # ==========================================================
                elif renko_signal == "SELEX":
                    print(f"🔔 Processing SELEX for {n_symbol}")
                    
                    # DECISION 1: Exit CALL if CALL position exists (independent)
                    if call_qty > 0:
                        call_symbol = symbol
                        if call_symbol:
                            print(f"🔻 EXIT CALL → SELCL for {call_symbol}")
                            
                            # Find the active CALL trade
                            active_call_trades = trade_manager[
                                (trade_manager["symbol"] == call_symbol) &
                                (trade_manager["exchange"] == exchange) &
                                (trade_manager["renko_signal"].isin(["BUYCL", "BUYEN"])) &
                                (trade_manager["order_status"] == "INPOSITION")
                            ]
                            
                            if not active_call_trades.empty:
                                active_trade = active_call_trades.iloc[0]
                                real_entry = float(active_trade.get("entry_price", 0))
                                
                                # EXIT ON OPPOSITE SIGNAL: Exit regardless of profit/loss
                                # This is the TRUE RENKO WAY - trust the signal!
                                if ENABLE_EXIT_ON_OPPOSITE_SIGNAL:
                                    exit_qty = int(active_trade.get("quantity", 0) or 0)
                                    pnl_bricks = (entry_price - real_entry) / brick_size
                                    pnl_type = "PROFIT" if entry_price >= real_entry else "LOSS"
                                    print(f"🔄 EXIT ON OPPOSITE SIGNAL: SELEX appeared, exiting CALL ({pnl_type}: {pnl_bricks:.1f} bricks)")
                                    signals_to_add.append({
                                        "signal": "SELCL",
                                        "symbol": call_symbol,
                                        "entry_price": entry_price,
                                        "quantity": exit_qty,
                                        "reason": f"CALL exit on SELEX signal ({pnl_type})"
                                    })
                                # Original behavior: Only exit if profitable
                                elif entry_price >= real_entry:
                                    exit_qty = int(active_trade.get("quantity", 0) or 0)
                                    signals_to_add.append({
                                        "signal": "SELCL",
                                        "symbol": call_symbol,
                                        "entry_price": entry_price,
                                        "quantity": exit_qty,
                                        "reason": "CALL exit triggered by SELEX"
                                    })
                                else:
                                    print(f"🛑 Skipping CALL exit: exit price {entry_price} ≤ entry {real_entry}")
                    
                    # DECISION 2: Always enter PUT (without checking positions)
                    # Calculate limit_entry_price (1 brick above for BUYPT)
                    limit_entry_price_buypt = entry_price + brick_size
                    
                    # ============================================
                    # V5: FILTERS TO AVOID WHIPSAW
                    # ============================================
                    
                    # Check 0: Time filter (avoid volatile periods)
                    can_trade, time_reason = check_time_filter(exchange=exchange)
                    if not can_trade:
                        print(f"🚫⏰ BUYPT BLOCKED for {n_symbol}: {time_reason}")
                    
                    # Check 1: Daily loss limit (safety net)
                    elif True:  # Continue chain
                        can_trade, loss_reason = check_daily_loss_limit()
                        if not can_trade:
                            print(f"🚫💰 BUYPT BLOCKED for {n_symbol}: {loss_reason}")
                    
                        # Check 2: Direction filter (need STRONG trend)
                        elif ENABLE_DIRECTION_FILTER:
                            can_trade, direction_reason = should_take_trade('BUYPT', renko_df, n_symbol)
                            if not can_trade:
                                print(f"🚫📈 BUYPT BLOCKED for {n_symbol}: {direction_reason}")
                            else:
                                print(f"✅ {direction_reason}")
                                print(f"🟢 ENTER PUT → BUYPT for {n_symbol}")
                                lot = get_lotsize(derivative_exchange, trading_symbol_pe)
                                desired_lots = int(FIXED_NUM_LOTS) if (FIXED_NUM_LOTS is not None) else 1
                                if desired_lots <= 0:
                                    desired_lots = 1
                                qty_for_entry = desired_lots * max(1, int(lot or 1))
                                
                                signals_to_add.append({
                                    "signal": "BUYPT",
                                    "symbol": n_symbol,
                                    "entry_price": entry_price,
                                    "limit_entry_price": limit_entry_price_buypt,
                                    "quantity": qty_for_entry,
                                    "reason": "PUT entry triggered by SELEX"
                                })
                        
                        # No direction filter - just trade
                        else:
                            print(f"🟢 ENTER PUT → BUYPT for {n_symbol} (no filters)")
                            lot = get_lotsize(derivative_exchange, trading_symbol_pe)
                            desired_lots = int(FIXED_NUM_LOTS) if (FIXED_NUM_LOTS is not None) else 1
                            if desired_lots <= 0:
                                desired_lots = 1
                            qty_for_entry = desired_lots * max(1, int(lot or 1))
                            
                            signals_to_add.append({
                                "signal": "BUYPT",
                                "symbol": n_symbol,
                                "entry_price": entry_price,
                                "limit_entry_price": limit_entry_price_buypt,
                                "quantity": qty_for_entry,
                                "reason": "PUT entry triggered by SELEX"
                            })

                
                    # # DECISION 2: Enter PUT only if NO PUT position AND NO CALL position
                    # if put_qty == 0 and call_qty == 0:
                    #     print(f"🟢 ENTER PUT → BUYPT for {n_symbol}")
                    #     # lot = get_lotsize(exchange, n_symbol)
                    #     lot = get_lotsize(derivative_exchange, trading_symbol_pe)
                    #     desired_lots = int(FIXED_NUM_LOTS) if (FIXED_NUM_LOTS is not None) else 1
                    #     if desired_lots <= 0:
                    #         desired_lots = 1
                    #     qty_for_entry = desired_lots * max(1, int(lot or 1))
                        
                    #     signals_to_add.append({
                    #         "signal": "BUYPT",
                    #         "symbol": n_symbol,
                    #         "entry_price": entry_price,
                    #         "quantity": qty_for_entry,
                    #         "reason": "PUT entry triggered by SELEX"
                    #     })
                    # elif put_qty > 0:
                    #     print(f"🚫 Skipping BUYPT: PUT position already active for {root} (qty={put_qty})")
                    # elif call_qty > 0:
                    #     print(f"🚫 Skipping BUYPT: CALL position active for {root} (qty={call_qty})")

                
            
                # Add all generated signals to trade_manager
                for signal_info in signals_to_add:
                    # Check if signal already exists with the same timestamp and is CLOSED
                    existing_closed_signal = trade_manager[
                        (trade_manager["symbol"] == signal_info["symbol"]) &
                        (trade_manager["exchange"] == exchange) &
                        (trade_manager["renko_signal"] == signal_info["signal"]) &
                        (trade_manager["order_status"] == "CLOSED") &
                        (trade_manager["timestamp"] == timestamp)
                    ]
                    
                    if not existing_closed_signal.empty:
                        print(f"⛔ Skipping {signal_info['signal']} for {signal_info['symbol']} — already CLOSED at same timestamp {timestamp}")
                        continue
                    
                    # Check if signal already exists and is still OPEN/INPOSITION
                    existing_open_signal = trade_manager[
                        (trade_manager["symbol"] == signal_info["symbol"]) &
                        (trade_manager["exchange"] == exchange) &
                        (trade_manager["renko_signal"] == signal_info["signal"]) &
                        (trade_manager["entry_price"] == signal_info["entry_price"]) &
                        (trade_manager["order_status"].isin(['OPEN', 'INPOSITION']))
                    ]
                    
                    if existing_open_signal.empty:
                        new_signal = {
                            "timestamp": timestamp,
                            "symbol": signal_info["symbol"],
                            "exchange": exchange,
                            "renko_signal": signal_info["signal"],
                            "entry_price": signal_info["entry_price"],
                            "limit_entry_price": signal_info.get("limit_entry_price"),  # 1 brick adjusted price for chop filter
                            "exec_price": None,
                            "quantity": signal_info["quantity"],
                            "order_status": "OPEN",
                            "orderid": None,
                            "close_reason": None  # Will be set when trade is closed (STOPLOSS or SIGNAL_EXIT)
                        }
                        
                        # Use helper function to avoid FutureWarning about empty/NA columns
                        trade_manager = concat_trade_row(trade_manager, new_signal)
                        print(f"✅ Generated {signal_info['signal']} for {signal_info['symbol']}: {signal_info['reason']}")
                    else:
                        print(f"⛔ Skipping {signal_info['signal']} for {signal_info['symbol']} — already exists and is {existing_open_signal.iloc[0]['order_status']}")

                # # ==========================================================
                # # NEW: Check for OPEN BUYCL/BUYPT positions to close based on brick condition
                # # ==========================================================
                # current_brick = renko_df['Renko_Brick'].iloc[-1] if not renko_df.empty else None
                
                # if current_brick is not None and brick_size > 0:
                #     # Track what we close in this timestamp
                #     closed_this_cycle = {"BUYCL": False, "BUYPT": False}   
                    
                #     # Check OPEN BUYCL positions for this symbol
                #     open_buycl_indices = trade_manager[
                #         (trade_manager["symbol"] == symbol) &
                #         (trade_manager["exchange"] == exchange) &
                #         (trade_manager["renko_signal"] == "BUYCL") &
                #         (trade_manager["order_status"] == "OPEN")
                #     ].index.tolist()
                    
                #     for idx in open_buycl_indices:
                #         buycl_entry = float(trade_manager.loc[idx, "entry_price"])
                #         # If current brick is >= entry + 2 bricks, close it
                #         if current_brick >= buycl_entry + (2 * brick_size):
                #             # Update the status directly in the DataFrame
                #             trade_manager.loc[idx, "order_status"] = "CLOSED"
                #             closed_this_cycle["BUYCL"] = True
                #             print(f"✅ Closed BUYCL for {symbol}: Current brick {current_brick} >= Entry {buycl_entry} + 2 bricks ({buycl_entry + (2 * brick_size)})")
                #         elif current_brick <= buycl_entry - (3 * brick_size):
                #             # Update the status directly in the DataFrame
                #             trade_manager.loc[idx, "order_status"] = "CLOSED"
                #             closed_this_cycle["BUYCL"] = True
                #             print(f"✅ Closed BUYCL for {symbol}: Current brick {current_brick} <= Entry {buycl_entry} - 3 bricks ({buycl_entry - (3 * brick_size)})")
                    
                #     # Check OPEN BUYPT positions for this symbol
                #     open_buypt_indices = trade_manager[
                #         (trade_manager["symbol"] == symbol) &
                #         (trade_manager["exchange"] == exchange) &
                #         (trade_manager["renko_signal"] == "BUYPT") &
                #         (trade_manager["order_status"] == "OPEN")
                #     ].index.tolist()
                    
                #     for idx in open_buypt_indices:
                #         buypt_entry = float(trade_manager.loc[idx, "entry_price"])
                #         # If current brick is <= entry - 2 bricks, close it
                #         if current_brick <= buypt_entry - (2 * brick_size):
                #             # Update the status directly in the DataFrame
                #             trade_manager.loc[idx, "order_status"] = "CLOSED"
                #             closed_this_cycle["BUYPT"] = True
                #             print(f"✅ Closed BUYPT for {symbol}: Current brick {current_brick} <= Entry {buypt_entry} - 2 bricks ({buypt_entry - (2 * brick_size)})")
                #         elif current_brick >= buypt_entry + (3 * brick_size):
                #             # Update the status directly in the DataFrame
                #             trade_manager.loc[idx, "order_status"] = "CLOSED"
                #             closed_this_cycle["BUYPT"] = True
                #             print(f"✅ Closed BUYPT for {symbol}: Current brick {current_brick} >= Entry {buypt_entry} + 3 bricks ({buypt_entry + (3 * brick_size)})")
                    
                #     # Save once after processing all positions for this symbol
                #     if closed_this_cycle["BUYCL"] or closed_this_cycle["BUYPT"]:
                #         save_to_csv(trade_manager, trade_manager_file)
                #         print(f"💾 Saved trade_manager after closing positions for {symbol}")

            except Exception as e:
                print(f"💀🔥 Error processing {symbol}: {e}")

        print("✅ update_trade_manager_with_new_signals complete; checking stop integrity...")
        
        # ============================================
        # 🎯 V5 UPGRADE: PROCESS TRAILING STOPS
        # ============================================
        if ENABLE_TRAILING_STOP:
            try:
                trade_manager, positions_count = process_trailing_stops_for_all_positions(
                    trade_manager, 
                    get_ltp_func=get_ltp,
                    brick_size_func=brick_for_runtime
                )
                # Only print if we have active positions
                if positions_count > 0:
                    print(f"✅ Trailing stops processed for {positions_count} position(s)")
            except Exception as trail_e:
                print(f"⚠️ Trailing stop processing error: {trail_e}")
        
        save_to_csv(trade_manager, trade_manager_file)

    except Exception as e:
        print(f"💥 update_trade_manager_with_new_signals failed: {e}")
        
    #             for signal_info in signals_to_add:
    #                 # Check if signal already exists
    #                 existing_signal = trade_manager[
    #                     (trade_manager["symbol"] == signal_info["symbol"]) &
    #                     (trade_manager["exchange"] == exchange) &
    #                     (trade_manager["renko_signal"] == signal_info["signal"]) &
    #                     (trade_manager["order_status"].isin(['OPEN', 'INPOSITION']))
    #                 ]
                    
    #                 if existing_signal.empty:
    #                     new_signal = {
    #                         "timestamp": timestamp,
    #                         "symbol": signal_info["symbol"],
    #                         "exchange": exchange,
    #                         "renko_signal": signal_info["signal"],
    #                         "entry_price": signal_info["entry_price"],
    #                         "exec_price": None,
    #                         "quantity": signal_info["quantity"],
    #                         "order_status": "OPEN",
    #                         "orderid": None
    #                     }
                        
    #                     trade_manager.loc[len(trade_manager)] = new_signal
    #                     print(f"✅ Generated {signal_info['signal']} for {signal_info['symbol']}: {signal_info['reason']}")
    #                 else:
    #                     print(f"⛔ Skipping {signal_info['signal']} for {signal_info['symbol']} — already exists")

    #         except Exception as e:
    #             print(f"💀🔥 Error processing {symbol}: {e}")

    #     print("✅ update_trade_manager_with_new_signals complete; checking stop integrity...")
       
    #     save_to_csv(trade_manager, trade_manager_file)

    # except Exception as e:
    #     print(f"💥 update_trade_manager_with_new_signals failed: {e}")
    
def websocket_thread_func(client, symbols_to_subscribe_df):
    global subscribed_symbols  # Make sure this set is accessible
    reconnect_delay = 5
    last_health_check = time.time()
    HEALTH_CHECK_INTERVAL = 60  # Log health every 60 seconds
    
    while True:
        if not websocket_active.is_set():
            print("⏳ Skipping WebSocket attempt (not active)...")
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 60)  # Exponential backoff
            continue

        try:
            client.connect()
            print("🔌 WebSocket connected")
            reconnect_delay = 5  # Reset on success
            instruments_list = symbols_to_subscribe_df.to_dict("records")
            
            # FILTER only unsubscribed symbols (🛡️ FEB 1 FIX: Thread-safe check)
            new_instruments = [instr for instr in instruments_list 
                               if not safe_subscribe_check(instr['symbol'])]
            if new_instruments:
                subscribe_to_websocket(client, new_instruments)
                # Add to subscribed set (🛡️ FEB 1 FIX: Thread-safe add)
                for instr in new_instruments:
                    safe_subscribe_add(instr['symbol'])

            while websocket_active.is_set():
                # Periodic health check - log stale symbols
                if time.time() - last_health_check > HEALTH_CHECK_INTERVAL:
                    last_health_check = time.time()
                    now = time.time()
                    stale_symbols = []
                    healthy_symbols = []
                    # 🛡️ FEB 1 FIX: Thread-safe iteration with lock
                    with _subscribed_symbols_lock:
                        symbols_snapshot = list(subscribed_symbols)
                    for sym in symbols_snapshot:
                        _, ltp_ts = safe_ltp_get(sym)  # Thread-safe
                        age = now - ltp_ts
                        threshold = get_ltp_stale_threshold(sym)  # Symbol-specific threshold
                        if age > threshold:
                            stale_symbols.append(f"{sym}({age:.0f}s)")
                        else:
                            healthy_symbols.append(sym)
                    if stale_symbols:
                        print(f"⚠️ WS HEALTH: STALE={stale_symbols} | HEALTHY={healthy_symbols}")
                    else:
                        print(f"✅ WS HEALTH: All {len(healthy_symbols)} symbols receiving updates")
                
                time.sleep(5)

        except Exception as e:
            print(f"💀🔥 WebSocket error: {e}")
            websocket_active.clear()
            print("⚠️ Switching to REST fallback")
            time.sleep(10)

def fallback_thread_func(symbols_to_subscribe_df, poll_interval=5):
    """
    Hybrid LTP updater - always runs alongside WebSocket.
    Polls REST API for any symbols with stale LTP (WebSocket may silently fail).
    """
    while True:
        try:
            now = time.time()
            stale_symbols = []
            
            # Check all symbols in the subscription list
            for _, row in symbols_to_subscribe_df.iterrows():
                symbol = row['symbol']
                exchange = row['exchange']
                _, ltp_ts = safe_ltp_get(symbol)  # 🛡️ FEB 1 FIX: Thread-safe
                ltp_age = now - ltp_ts
                
                # Poll if LTP is stale OR never received (use symbol-specific threshold)
                stale_threshold = get_ltp_stale_threshold(symbol)
                if ltp_age > stale_threshold:
                    stale_symbols.append((symbol, exchange, ltp_age))
            
            # Also check subscribed_symbols not in the CSV (dynamically added)
            # 🛡️ FEB 1 FIX: Thread-safe iteration with snapshot
            with _subscribed_symbols_lock:
                subscribed_snapshot = list(subscribed_symbols)
            for symbol in subscribed_snapshot:
                if symbol not in [r['symbol'] for _, r in symbols_to_subscribe_df.iterrows()]:
                    _, ltp_ts = safe_ltp_get(symbol)  # 🛡️ FEB 1 FIX: Thread-safe
                    ltp_age = now - ltp_ts
                    stale_threshold = get_ltp_stale_threshold(symbol)
                    if ltp_age > stale_threshold:
                        # Try to determine exchange from ltp_dict context or default
                        exchange = "NSE_INDEX"  # Default, will be corrected if needed
                        stale_symbols.append((symbol, exchange, ltp_age))
            
            if stale_symbols:
                print(f"🔄 REST backup polling {len(stale_symbols)} stale symbols...")
                for symbol, exchange, age in stale_symbols:
                    try:
                        ltp = get_ltp(exchange, symbol)
                        if ltp is not None:
                            old_ltp, _ = safe_ltp_get(symbol)  # 🛡️ FEB 1 FIX: Thread-safe
                            safe_ltp_update(symbol, float(ltp))  # 🛡️ FEB 1 FIX: Thread-safe
                            if old_ltp != ltp:
                                print(f"🔄 REST LTP: {symbol} {old_ltp} → {ltp} (was {age:.0f}s stale)")
                            try:
                                ltp_event_queue.put_nowait(symbol)
                            except queue.Full:
                                pass
                        time.sleep(0.5)  # Small delay between REST calls
                    except Exception as e:
                        print(f"⚠️ REST poll error for {symbol}: {e}")
            
            time.sleep(poll_interval)
            
        except Exception as e:
            print(f"💀 REST polling thread error: {e}")
            time.sleep(5)
  
def connect_with_retry(client, max_retries=None):
    retries = 0
    while True:
        try:
            client.connect()
            print("🔌 WebSocket connection established.")
            return True
        except Exception as e:
            retries += 1
            wait_time = min(60, 5 ** retries)  # Exponential backoff, max 60 seconds
            print(f"💀🔥 WebSocket connection failed: {e}")
            print(f"🔄 Retrying WebSocket connection in {wait_time} seconds... (Attempt #{retries})")
            time.sleep(wait_time)
            if max_retries is not None and retries >= max_retries:
                print("💀🔥 Max retries reached. Exiting.")
                return False

def calculate_renko_hl_wick_safe(
    df: pd.DataFrame,
    brick_size: float
) -> pd.DataFrame:
    """
    TradingView-style High/Low Renko where WICKS DO NOT IMPACT BRICKS.

    Core rules:
    -----------
    1. High/Low are used ONLY as thresholds.
    2. Brick close moves ONLY by fixed brick_size.
    3. Wicks are NEVER fed as prices.
    4. Only ONE direction per candle (no same-bar reversal).
    5. All logic is based on Renko close only.

    Input columns required:
        ['timestamp', 'open', 'high', 'low', 'close']

    Output columns:
        ['timestamp', 'open', 'high', 'low', 'close', 'direction']
    """

    required = {"timestamp", "open", "high", "low", "close"}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns: {required - set(df.columns)}")

    if df.empty or len(df) == 0:
        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'direction'])
    
    if brick_size is None or brick_size <= 0:
        raise ValueError(f"Invalid brick_size: {brick_size}")

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    bricks = []

    # --------------------------------------------------
    # Initial brick anchor (TradingView-style)
    # --------------------------------------------------
    first_mid = (df.iloc[0]["high"] + df.iloc[0]["low"]) / 2
    last_close = int(first_mid / brick_size) * brick_size

    # --------------------------------------------------
    # MAIN LOOP (WICK-SAFE)
    # --------------------------------------------------
    for _, row in df.iterrows():

        high = row["high"]
        low = row["low"]
        ts = row["timestamp"]

        moved = False  # 🔒 prevents wick-based reversal

        # -------------------------------
        # UP bricks (HIGH threshold only)
        # -------------------------------
        while high >= last_close + brick_size:
            open_ = last_close
            last_close = last_close + brick_size

            bricks.append({
                "timestamp": ts,
                "open": open_,
                "close": last_close,
                "direction": 1
            })
            moved = True

        # --------------------------------
        # DOWN bricks (LOW threshold only)
        # --------------------------------
        if not moved:
            while low <= last_close - brick_size:
                open_ = last_close
                last_close = last_close - brick_size

                bricks.append({
                    "timestamp": ts,
                    "open": open_,
                    "close": last_close,
                    "direction": -1
                })

    if not bricks:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "direction"]
        )

    renko_df = pd.DataFrame(bricks)

    # --------------------------------------------------
    # Wick-safe OHLC construction (VISUAL ONLY)
    # --------------------------------------------------
    renko_df["high"] = renko_df[["open", "close"]].max(axis=1)
    renko_df["low"]  = renko_df[["open", "close"]].min(axis=1)

    renko_df = renko_df[
        ["timestamp", "open", "high", "low", "close", "direction"]
    ]

    return renko_df.reset_index(drop=True)

def calculate_renko_close_traditional(
    df: pd.DataFrame,
    brick_size: float
) -> pd.DataFrame:
    """
    CLOSE-based Traditional Renko (TradingView-like reversal behavior).

    Core rules:
    -----------
    1. ONLY Close is used (High/Low ignored completely).
    2. Continuation needs 1 box.
    3. Reversal needs 2 boxes (traditional Renko behavior).
    4. When reversal triggers, bricks are printed sequentially (often 2 at same timestamp).
    5. All logic is based on Renko close only.

    Input columns required:
        ['timestamp', 'close']

    Output columns:
        ['timestamp', 'open', 'high', 'low', 'close', 'direction']
    """

    required = {"timestamp", "close"}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns: {required - set(df.columns)}")

    if df.empty or len(df) == 0:
        return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'direction'])
    
    if brick_size is None or brick_size <= 0:
        raise ValueError(f"Invalid brick_size: {brick_size}")

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    bricks = []

    # --------------------------------------------------
    # Initial brick anchor (close-based)
    # --------------------------------------------------
    first_close = df.iloc[0]["close"]
    last_close = int(first_close / brick_size) * brick_size

    direction = 0  # 0 = unknown, 1 = up, -1 = down

    # --------------------------------------------------
    # MAIN LOOP (CLOSE ONLY, TRADITIONAL REVERSAL)
    # --------------------------------------------------
    for _, row in df.iterrows():

        price = row["close"]
        ts = row["timestamp"]

        while True:

            # -------------------------
            # No direction yet
            # -------------------------
            if direction == 0:

                if price >= last_close + brick_size:
                    open_ = last_close
                    last_close = last_close + brick_size

                    bricks.append({
                        "timestamp": ts,
                        "open": open_,
                        "close": last_close,
                        "direction": 1
                    })
                    direction = 1
                    continue

                if price <= last_close - brick_size:
                    open_ = last_close
                    last_close = last_close - brick_size

                    bricks.append({
                        "timestamp": ts,
                        "open": open_,
                        "close": last_close,
                        "direction": -1
                    })
                    direction = -1
                    continue

                break

            # -------------------------
            # Uptrend
            # -------------------------
            if direction == 1:

                # Continuation up (1 box)
                if price >= last_close + brick_size:
                    open_ = last_close
                    last_close = last_close + brick_size

                    bricks.append({
                        "timestamp": ts,
                        "open": open_,
                        "close": last_close,
                        "direction": 1
                    })
                    continue

                # Reversal down requires 2 boxes
                if price <= last_close - (2 * brick_size):
                    while price <= last_close - brick_size:
                        open_ = last_close
                        last_close = last_close - brick_size

                        bricks.append({
                            "timestamp": ts,
                            "open": open_,
                            "close": last_close,
                            "direction": -1
                        })

                    direction = -1
                    continue

                break

            # -------------------------
            # Downtrend
            # -------------------------
            if direction == -1:

                # Continuation down (1 box)
                if price <= last_close - brick_size:
                    open_ = last_close
                    last_close = last_close - brick_size

                    bricks.append({
                        "timestamp": ts,
                        "open": open_,
                        "close": last_close,
                        "direction": -1
                    })
                    continue

                # Reversal up requires 2 boxes
                if price >= last_close + (2 * brick_size):
                    while price >= last_close + brick_size:
                        open_ = last_close
                        last_close = last_close + brick_size

                        bricks.append({
                            "timestamp": ts,
                            "open": open_,
                            "close": last_close,
                            "direction": 1
                        })

                    direction = 1
                    continue

                break

    if not bricks:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "direction"]
        )

    renko_df = pd.DataFrame(bricks)

    # --------------------------------------------------
    # OHLC construction (VISUAL ONLY)
    # --------------------------------------------------
    renko_df["high"] = renko_df[["open", "close"]].max(axis=1)
    renko_df["low"]  = renko_df[["open", "close"]].min(axis=1)

    renko_df = renko_df[
        ["timestamp", "open", "high", "low", "close", "direction"]
    ]

    return renko_df.reset_index(drop=True)

def clean_sym(s):
    """
    Normalize any symbol by removing invisible Unicode chars & spaces.
    Critical for matching DB symbols like BANKEX24DEC2567100PE correctly.
    """
    return (
        str(s).upper()
        .strip()
        .replace(" ", "")
        .replace("\u200b", "")    # zero-width space
        .replace("\u200c", "")    # zero-width non-joiner
        .replace("\u200d", "")    # zero-width joiner
        .replace("\ufeff", "")    # BOM
    )
def extract_root(symbol):
    """
    Extract base root (letters only until first digit).
    DO NOT MODIFY — your entire strategy depends on this rule.
    Examples:
        BANKEX24DEC2567100PE → BANKEX
        NIFTY14NOV2524500CE  → NIFTY
        SILVERM28NOV25FUT    → SILVERM
    """
    symbol = clean_sym(symbol)
    s = ""
    for c in symbol:
        if c.isdigit():
            break
        s += c
    return s     
def update_index_ohlc_data():
    """
    Update historical data for:
      • NSE/BSE spot indexes: NIFTY, BANKNIFTY, SENSEX, BANKEX
      • MCX 'indexes' (your minis via FUT). If a root (e.g., CRUDEOIL) is present,
        it will be resolved to the nearest FUT via get_future_symbol(...).

    Explicitly BLOCKS any CE/PE option symbols.
    """
    atr_period = 14
    global MORNING_PIVOTS_DONE
    while True:
        symbols_df = fetch_symbols_from_csv(symbols_file)
        if symbols_df.empty:
            print("No valid symbols found in the CSV file. Retrying in 5 minutes.")
        else:
            for _, row in symbols_df.iterrows():
             
                raw_symbol = str(row.get('symbol', '')).strip()
                exchange = str(row.get('exchange', '')).strip()
                ex_lc = exchange.upper()
                sym_u = raw_symbol.upper()
                
                # ⏰ Skip if exchange has completed its trading day
                # This allows MCX to continue fetching data after NFO/BFO are done
                if not should_fetch_data_for_exchange(ex_lc):
                    continue
                
                # ✅ extra guard: never process index futures
                if ex_lc in ("NSE_INDEX", "BSE_INDEX") and ("FUT" in sym_u or sym_u.endswith("FUT")):
                    continue                
                # 🚫 HARD FILTER: Never process CE/PE symbols in OHLC runner
                if sym_u.endswith(("CE", "PE")):
                    continue 
                
                # --- HARD FILTER: NEVER process MCX options ---
                if ex_lc == "MCX" and sym_u.endswith(("CE", "PE")):
                    continue

                # --- Block options (CE/PE) outright ---
                if sym_u.endswith(("CE", "PE")):
                    # Skip options
                    continue

                # --- NSE/BSE spot indexes ---
                if ex_lc in ("NSE_INDEX", "BSE_INDEX"):
                    # Accept only known index roots (no FUT suffix)
                    if sym_u not in {"NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"}:
                        continue
                    symbol_to_fetch = raw_symbol  # same
                    interval = "1m"
                    ohlc_file_name = f"ohlcdata/{symbol_to_fetch}_ohlc.csv"
                    renko_file_name = f"ohlcdata/{symbol_to_fetch}_renko.csv"
                    ndays = 4

                # --- MCX 'indexes' (minis via FUT) only ---
                elif ex_lc == "MCX":
                
                    # ---- ONLY THESE 4 FUTURES ARE ALLOWED ----
                    allowed_roots = {"CRUDEOIL", "GOLDM", "SILVERM", "NATURALGAS"}
                
                    root = extract_root(raw_symbol).upper()
                    driver_symbol = Utils.getNearestMCXFuture(root)               
                    # Skip all other MCX symbols (options, spot, other commodities)
                    if root not in allowed_roots:
                        continue
                
                    # Resolve nearest FUT from DB
                    fut = Utils.getNearestMCXFuture(root) 
                    if not fut:
                        print(f"⚠️ Cannot resolve FUT for {root}, skipping")
                        continue
                
                    # FUT symbol selected from DB
                    symbol_to_fetch = Utils.getNearestMCXFuture(root)     # e.g., CRUDEOILM18FEB25FUT
                
                    interval = "1m"
                    ohlc_file_name = f"ohlcdata/{symbol_to_fetch}_ohlc.csv"
                    renko_file_name = f"ohlcdata/{symbol_to_fetch}_renko.csv"
                    ndays = 4
                else:
                    # No NSE/BSE futures; no other exchanges
                    continue

                try:
                    # Load existing OHLC if any
                    existing_data = read_csv(ohlc_file_name)
                    if existing_data is not None and not existing_data.empty:
                        existing_data['timestamp'] = pd.to_datetime(existing_data['timestamp'])
                        last_timestamp = existing_data['timestamp'].max()
                        start_date = (last_timestamp + timedelta(minutes=1)).strftime("%Y-%m-%d")
                    else:
                        existing_data = pd.DataFrame()
                        start_date = (datetime.now() - timedelta(days=ndays)).strftime("%Y-%m-%d")

                    end_date = datetime.now().strftime("%Y-%m-%d")

                    # Fetch history
                    df = fetch_historical_data(
                        symbol=symbol_to_fetch,
                        exchange=exchange,   # keep exchange exactly as stored (NSE_INDEX/BSE_INDEX/MCX)
                        interval=interval,
                        start_date=start_date,
                        end_date=end_date
                    )
                    time.sleep(0.5)  ##to avoid rate limit
                    if df.empty:
                        print(f"⚠️ No data fetched for symbol: {symbol_to_fetch}")
                        continue

                    # Merge & dedupe
                    if not existing_data.empty:
                        df = (pd.concat([existing_data, df])
                              .drop_duplicates(subset=['timestamp'])
                              .reset_index(drop=True))

                    # ATR
                    df['atr'] = ta.volatility.AverageTrueRange(
                        df.high, df.low, df.close, window=atr_period, fillna=False
                    ).average_true_range().round(2)

                    # If brick missing, seed from ATR (MCX especially; NIFTY/BANKNIFTY typically pre-seeded)
                    brick_size = row.get('brick_size')
                    if brick_size in (None, "", np.nan) or (isinstance(brick_size, float) and pd.isna(brick_size)):
                        ATR = df.iloc[-1].atr
                        brick_size = round(ATR) if ATR > 2 else ATR

                    # Save OHLC with swings
                    df_w_swings = identify_swings(df)
                    save_to_csv(df_w_swings, ohlc_file_name)
                    last_ohlc_update[symbol_to_fetch] = datetime.now()

                    # # Ensure brick persisted in symbols file if blank
                    # maybe_set_initial_brick_from_atr(exchange, raw_symbol)
                    maybe_set_initial_brick_from_atr(exchange, symbol_to_fetch if ex_lc == "MCX" else raw_symbol)


                    # Confirm a valid brick exists before Renko
                    sym_df = read_csv(symbols_file)
                    b = None
                    if sym_df is not None and not sym_df.empty:
                        r2 = sym_df[sym_df["symbol"].astype(str).str.upper() == raw_symbol.upper()]
                        # If we stored FUT instead of root for MCX, fallback match
                        if r2.empty and ex_lc == "MCX":
                            r2 = sym_df[sym_df["symbol"].astype(str).str.upper() == symbol_to_fetch.upper()]
                        if not r2.empty:
                            b = r2["brick_size"].iloc[0]

                    if b is None or pd.isna(b) or b <= 0:
                        print(f"⏳ Waiting for valid brick for {raw_symbol} — skipping Renko this cycle.")
                        continue

                    b = float(b)

                    # Renko & signals
                    # renko_df = calculate_renko_bricks(df, brick_size=b)
                    # renko_df = calculate_renko_bricks_close_with_wicks(df, brick_size=float(brick_size))
                    # renko_df = renko_hilo_with_wicks(df, brick_size=b, mode="wicks", path="auto")
                    # Also persist both CSVs for your new workflow (exits from close, pivots from hilo)
                    # 🧩 Ensure symbol is preserved for Renko/pivots
                    df.attrs["symbol"] = symbol_to_fetch                      

                    # Process Renko bricks and signals for this symbol only when ready
                    # renko_df = calculate_renko_bricks(df, brick_size=brick_size)                        
                    # renko_df = renko_hilo_with_wicks(df, brick_size=b, mode="wicks", path="auto")
                    # renko_df = ru.calculate_renko_hl_wick_safe_5m(df, brick_size)
                    # renko_df = ru.calculate_renko_hl_wick_safe_5m_new(df, brick_size)
                    # renko_df = ru.calculate_renko_close_traditional(df, brick_size)

                    # 🛡️ FEB 1 FIX: Per-instrument Renko method
                    # INDEX + CRUDE/NATGAS: CLOSE Renko (filters noise, 80%+ win)
                    # GOLD + SILVER: HL Renko (captures more moves, massive P&L)
                    
                    # Determine Renko method for this instrument
                    base_symbol = symbol_to_fetch.replace("FUT", "").replace("_", "")
                    for key in ["GOLDM", "SILVERM", "GOLD", "SILVER"]:
                        if key in base_symbol.upper():
                            use_hl_renko = True
                            break
                    else:
                        use_hl_renko = False
                    
                    if use_hl_renko:
                        # GOLD/SILVER: Use HL Renko (60%+ win, MASSIVE P&L)
                        renko_df = calculate_renko_hl_wick_safe(df, brick_size)
                        _logger.info(f"🧱 {symbol_to_fetch}: Using HL Renko (GOLD/SILVER mode)")
                    else:
                        # INDEX/CRUDE/NATGAS: Use CLOSE Renko (80%+ win)
                        renko_df = calculate_renko_close_traditional(df, brick_size)
                        _logger.info(f"🧱 {symbol_to_fetch}: Using CLOSE Renko")                        
                    
                    # Keep your existing downstream code compatible:
                    # your identify_renko_swings() uses df['Renko_Brick']
                    renko_df["Renko_Brick"] = renko_df["close"].astype(float)

                    renko_df = identify_renko_swings(renko_df)                    
                    # Swing detection for CLOSE Renko
                    renko_df = identify_renko_swings(renko_df)
                    
                    # zlema(9)
                    z = ti.zlema(renko_df["Renko_Brick"].astype(float).values, 9)
                    pad = len(renko_df) - len(z)
                    renko_df["zlema9"] = np.r_[ [np.nan] * pad, z ].round(2)
                    
                    # Generate SIGNALS from CLOSE-based Renko
                    renko_df_with_signals = generate_signals(renko_df, brick_size=b)
                    
                    # Save the CLOSE-based Renko+signals for exit watchers
                    save_to_csv(renko_df_with_signals, renko_file_name)
                    
                    # # renko_df = identify_renko_swings(renko_df)
                    # renko_df = identify_renko_swings_wicks(renko_df)  # <- use wick highs/lows

                    # zlema9 = ti.zlema(np.array(renko_df['Renko_Brick'], dtype=np.float64), 9)
                    # pad_length = len(renko_df) - len(zlema9)
                    # zlema9_padded = np.concatenate((np.full(pad_length, np.nan), zlema9))
                    # renko_df['zlema9'] = pd.Series(zlema9_padded).round(2)
                    # renko_df_with_signals = rk_hilo
                    # renko_df_with_signals = generate_signals(renko_df, brick_size=b)
                    # # Use the enriched HILO Renko for the rest of this pipeline (already has swings/zlema/signals)
                    # save_to_csv(renko_df_with_signals, renko_file_name)
                    # # NEW: notify exit watchers
                    # renko_close_updated_event.set()


                except Exception as e:
                    print(f"💀🔥 Error processing data for symbol {raw_symbol}: {e}")
        update_trade_manager_with_new_signals()
        # close_stale_open_signals()
        # close_stale_open_signals()    

        # Schedule: 1-minute cadence starting 09:15:08
        now = datetime.now()
        start_hour = 9
        start_minute = 15
        interval_minutes = 1

        next_time = now.replace(hour=start_hour, minute=start_minute, second=8, microsecond=0)
        while next_time <= now:
            next_time += timedelta(minutes=interval_minutes)

        time_to_wait = (next_time - now).total_seconds()
        print(f"🕰️ Next data fetch scheduled at {next_time}")
             

        time.sleep(time_to_wait)


def _enough_bricks_from_entry(entry_brick: float, exit_brick: float, brick_size: float, min_bricks: int = MIN_PROFIT_BRICKS) -> bool:
    """
    Return True if exit_brick is at least `min_bricks` bricks above entry_brick.
    Used for long-only SELEX gating.
    """
    if entry_brick is None or exit_brick is None:
        return False
    diff = (exit_brick - entry_brick)
    # ✅ Allow exit at breakeven (diff == 0) or any profit (diff > 0)
    if diff >= 0:
        return True
    return False

#Indexes
def is_nifty_symbol(sym: str) -> bool:
    return (sym or "").upper().startswith("NIFTY")        

# ADD
def is_crude_symbol(s: str) -> bool:
    u = (s or "").upper().strip()
    return u.startswith("CRUDEOIL")

def is_banknifty_symbol(sym: str) -> bool:
    return (sym or "").upper().startswith("BANKNIFTY")


def is_sensex_symbol(sym: str) -> bool:
    # e.g., SENSEX23OCT2584400CE / SENSEX23OCT2584400PE
    return bool(re.match(r'^SENSEX\d{2}[A-Z]{3}\d{2}\d+(CE|PE)$', str(sym).upper()))

def is_silver_symbol(sym: str) -> bool:
    return (sym or "").upper().startswith(("SILVERM", "SILVER"))

def is_gold_symbol(sym: str) -> bool:
    return (sym or "").upper().startswith(("GOLDM", "GOLD"))

def is_naturalgas_symbol(sym: str) -> bool:
    return (sym or "").upper().startswith("NATURALGAS")
                
def _sim_inposition_qty(trade_manager, symbol, exchange):
    """When simulating (no broker confirms), infer net long from trade_manager."""
    if trade_manager is None or trade_manager.empty:
        return 0
    mask = (
        (trade_manager["symbol"] == symbol) &
        (trade_manager["exchange"] == exchange) &
        (trade_manager["order_status"] == "INPOSITION") &
        (trade_manager["renko_signal"].isin(["BUYEN", "BUYRE"]))
    )
    rows = trade_manager.loc[mask]
    if rows.empty:
        return 0
    q = rows.iloc[-1].get("quantity", 0) or 0
    return int(q)

def infer_inposition_qty(trade_manager, positions_book_df, symbol, exchange):
    """
    Use trade_manager for sim (SKIP_ORDER_CONFIRMATION or empty broker book),
    else use real broker net qty.
    """
    if SKIP_ORDER_CONFIRMATION or positions_book_df is None or getattr(positions_book_df, "empty", True):
        return _sim_inposition_qty(trade_manager, symbol, exchange)
    return int(get_net_qty(positions_book_df, symbol) or 0)



def get_derivative_exchange_for_spot(exchange):
    """
    Map spot exchange to derivative exchange for option lookup.
    """
    exchange_upper = exchange.upper()
    
    mapping = {
        "NSE_INDEX": "NFO",
        "NSE-INDEX": "NFO",
        "BSE_INDEX": "BFO", 
        "BSE-INDEX": "BFO",
        "MCX": "MCX",
        "NFO": "NFO",  # Already derivative
        "BFO": "BFO",  # Already derivative
    }
    
    derivative_exchange = mapping.get(exchange_upper, exchange_upper)
    return derivative_exchange


def get_current_option_symbols(client, driver_symbol, exchange="NSE_INDEX"):
    """
    Get current CE and PE option symbols for a given index/commodity.
    Returns (ce_symbol, pe_symbol) where ce_symbol is one step above ATM,
    pe_symbol is one step below ATM. On expiry day, move to next expiry.
    """
    try:
        # Remove FUT suffix if present (for MCX)
        # root = driver_symbol.replace("FUT", "").strip().upper()
        root = extract_root(driver_symbol)
        
        # For MCX, we need to get the futures contract for spot price
        if exchange.upper() == "MCX":
            # Get the nearest MCX futures contract for spot price
            fut_data = Utils.getNearestMCXFuture(root)
            if fut_data:
                driver_symbol_for_spot = fut_data
                # print(f"📊 Using MCX futures for spot: {driver_symbol_for_spot}")
            else:
                driver_symbol_for_spot = root + "FUT"
        if exchange.upper() == "MCX":
            if driver_symbol in ltp_dict:
                spot = ltp_dict[driver_symbol_for_spot]            
            # spot = get_ltp_generic(client, driver_symbol_for_spot, exchange)
            else:
                # Fallback to API with a delay
                time.sleep(1)  # Delay to avoid rate limit
                spot = get_ltp_generic(client, driver_symbol_for_spot, exchange)
        else:
            # Get spot price from the futures contract
            if driver_symbol in ltp_dict:
                spot = ltp_dict[driver_symbol]            
            else:
                # Fallback to API with a delay
                time.sleep(1)  # Delay to avoid rate limit
                spot = get_ltp_generic(client, driver_symbol, exchange)            
            # spot = get_ltp_generic(client, driver_symbol, exchange)
        
        if spot is None:
            print(f"⚠️ Could not get spot for {driver_symbol_for_spot}")
            return None, None
        
        # print(f"📍 {driver_symbol} Spot (from futures): {spot:.2f}")
        
        # Map to derivative exchange
        derivative_exchange = get_derivative_exchange_for_spot(exchange)
        # print(f"🔍 Searching on {derivative_exchange} exchange")
        
        # Default step sizes
        step_map = {
            "NIFTY": 100,       # NIFTY typically 50 point increments
            "BANKNIFTY": 200,   # BANKNIFTY typically 100 point increments
            "SENSEX": 500,      # SENSEX typically 100 point increments
            "BANKEX": 500,
            "CRUDEOIL": 100,    # Crude oil typically 10 point increments
            "GOLDM": 500,      # Gold typically 100 point increments
            "SILVERM": 500,    # Silver typically 100 point increments
            "NATURALGAS": 5,    # Natural gas typically 2 point increments
        }
        
        # Find matching root
        import re
        root_match = re.match(r'^([A-Z]+)', driver_symbol)
        root = root_match.group(1) if root_match else driver_symbol
        
        # Get base step
        step = step_map.get(root, 100)
        
        # Calculate nearest ATM strike
        atm_strike = round(spot / step) * step
        
        # Calculate strikes: CE is one step above ATM, PE is one step below ATM
        ce_strike = atm_strike - step  # One step above ATM for CE
        pe_strike = atm_strike + step  # One step below ATM for PE

        if exchange.upper() == "MCX":
            ce_strike = atm_strike
            pe_strike = atm_strike
            
        # print(f"🎯 ATM Strike: {atm_strike}, CE Strike: {ce_strike}, PE Strike: {pe_strike}")
        
        # Query symtoken for both CE and PE
        db_path = os.path.join(os.getcwd(), "db", "openalgo.db")
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        
        # Get current date
        today = datetime.now().date()
        
        # Helper function to get nearest valid expiry symbol
        def get_nearest_expiry_symbol(rows, option_type, strike):
            if not rows:
                return None
            
            # Group symbols by expiry date
            expiry_map = {}
            
            for r in rows:
                raw_expiry = r["expiry"]
                symbol = r["symbol"]
                
                # Parse expiry date
                if isinstance(raw_expiry, (int, float)):
                    expiry_date = datetime.fromtimestamp(int(raw_expiry) / 1000).date()
                elif isinstance(raw_expiry, str):
                    try:
                        expiry_date = datetime.strptime(raw_expiry, "%d-%b-%y").date()
                    except ValueError:
                        print(f"Invalid expiry format in symtoken: {raw_expiry}")
                        continue
                else:
                    continue
                
                # Store symbol by expiry date
                if expiry_date not in expiry_map:
                    expiry_map[expiry_date] = []
                expiry_map[expiry_date].append(symbol)
            
            # Sort expiry dates
            sorted_expiries = sorted(expiry_map.keys())
            
            # Find the nearest expiry that's NOT today (skip today's expiry)
            selected_expiry = None
            for expiry_date in sorted_expiries:
                if expiry_date > today:  # Skip today's expiry
                    selected_expiry = expiry_date
                    break
            
            # If no future expiry found (only today's expiry exists), use the nearest expiry
            if not selected_expiry and sorted_expiries:
                selected_expiry = sorted_expiries[0]
            
            # Return first symbol for selected expiry
            if selected_expiry and selected_expiry in expiry_map:
                symbol = expiry_map[selected_expiry][0]
                
                # HARD SAFETY CHECK
                if not symbol.startswith(root):
                    raise RuntimeError(
                        f"OPTION ROOT MISMATCH: driver={root}, {option_type}_option={symbol} (strike={strike})"
                    )
                return symbol
            
            return None
        
        # Get CE option (one step above ATM)
        ce_rows = cur.execute("""
            SELECT symbol, expiry
            FROM symtoken
            WHERE exchange = ?
              AND UPPER(symbol) LIKE ? || '%'
              AND UPPER(instrumenttype) = 'CE'
              AND strike = ?
            ORDER BY expiry ASC
        """, (derivative_exchange, root, ce_strike)).fetchall()
        
        # Get PE option (one step below ATM)
        pe_rows = cur.execute("""
            SELECT symbol, expiry
            FROM symtoken
            WHERE exchange = ?
              AND UPPER(symbol) LIKE ? || '%'
              AND UPPER(instrumenttype) = 'PE'
              AND strike = ?
            ORDER BY expiry ASC
        """, (derivative_exchange, root, pe_strike)).fetchall()
        
        con.close()
        
        # Get CE and PE symbols
        ce_symbol = get_nearest_expiry_symbol(ce_rows, "CE", ce_strike)
        pe_symbol = get_nearest_expiry_symbol(pe_rows, "PE", pe_strike)
        
        # Fallback to enhanced_search_symbols if DB query fails
        if not ce_symbol or not pe_symbol:
            print(f"⚠️ DB query incomplete, falling back to enhanced_search_symbols")
            
            # Search in derivative exchange first
            results = enhanced_search_symbols(driver_symbol, derivative_exchange)
            
            if not results:
                print(f"⚠️ No results in {derivative_exchange}, trying all exchanges")
                results = enhanced_search_symbols(driver_symbol, None)
            
            if results:
                # Try to find matching symbols
                for r in results:
                    sym = r.symbol.upper()
                    
                    # Check for CE at ce_strike
                    if not ce_symbol and sym.endswith("CE"):
                        # Extract strike from symbol (assuming format like NIFTY22JAN20000CE)
                        import re
                        strike_match = re.search(r'(\d+)CE$', sym)
                        if strike_match:
                            found_strike = int(strike_match.group(1))
                            if found_strike == ce_strike:
                                ce_symbol = r.symbol
                    
                    # Check for PE at pe_strike
                    if not pe_symbol and sym.endswith("PE"):
                        strike_match = re.search(r'(\d+)PE$', sym)
                        if strike_match:
                            found_strike = int(strike_match.group(1))
                            if found_strike == pe_strike:
                                pe_symbol = r.symbol
        
        if not ce_symbol:
            print(f"❌ Could not find CE option at strike {ce_strike}")
        
        if not pe_symbol:
            print(f"❌ Could not find PE option at strike {pe_strike}")
        
        # if ce_symbol and pe_symbol:
        #     print(f"✅ Found: CE={ce_symbol} (strike {ce_strike}), PE={pe_symbol} (strike {pe_strike})")
        # else:
        #     print(f"⚠️ Incomplete: CE={ce_symbol}, PE={pe_symbol}")
        
        return ce_symbol, pe_symbol
        
    except Exception as e:
        print(f"💀🔥 Error getting option symbols for {driver_symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None, None

def get_ltp_on_exchange(client, symbol, exchange):
    """
    Get LTP for a symbol on a specific exchange.
    Handles the exchange mapping internally.
    """
    try:
        # For options/futures, use derivative exchange
        if symbol.upper().endswith(("CE", "PE", "FUT")):
            # Map exchange if needed
            if exchange == "NSE_INDEX":
                query_exchange = "NFO"
            elif exchange == "BSE_INDEX":
                query_exchange = "BFO"
            else:
                query_exchange = exchange
        else:
            query_exchange = exchange
        
        quote = client.quotes(symbol=symbol, exchange=query_exchange)
        ltp = quote.get('data', {}).get('ltp', None)
        if ltp is None:
            print(f"⚠️ LTP not found for {symbol} on {query_exchange}")
        return ltp
    except Exception as e:
        print(f"💀🔥 Error getting LTP for {symbol} on {exchange}: {e}")
        return None
    
def get_ltp_generic(client, symbol, exchange="NSE_INDEX"):
    """
    Generic LTP getter that handles exchange mapping for derivatives.
    """
    try:
        # Map exchange for derivative lookup
        if symbol.upper().endswith(("CE", "PE")) or symbol.upper().endswith("FUT"):
            if exchange == "NSE_INDEX":
                query_exchange = "NFO"
            elif exchange == "BSE_INDEX":
                query_exchange = "BFO"
            else:
                query_exchange = exchange
        else:
            query_exchange = exchange
        
        quote = client.quotes(symbol=symbol, exchange=query_exchange)
        ltp = quote.get('data', {}).get('ltp', None)
        return ltp if ltp is not None else None
    except Exception as e:
        print(f"⚠️ Error getting LTP for {symbol} on {exchange}: {e}")
        return None

def get_position_symbol(client, driver_symbol, option_type, exchange="NSE_INDEX"):
    """
    Get the specific option symbol from broker positions for a given driver symbol and option type.
    Returns the symbol only (exchange is handled separately).
    """
    try:
        positions_book_df = get_positions_book(client)
        if positions_book_df is None or positions_book_df.empty:
            return None
        
        # Determine which exchanges to check
        exchanges_to_check = []
        if exchange == "NSE_INDEX":
            exchanges_to_check = ["NFO", "NSE_INDEX"]  # Check both
        elif exchange == "BSE_INDEX":
            exchanges_to_check = ["BFO", "BSE_INDEX"]
        else:
            exchanges_to_check = [exchange]
        
        # Filter positions for this driver symbol and option type
        for _, pos in positions_book_df.iterrows():
            pos_symbol = str(pos["symbol"]).upper()
            pos_exchange = str(pos.get("exchange", "")).upper()
            pos_qty = int(pos.get("quantity", 0))
            
            # Check if this is an option for our driver symbol and has non-zero quantity
            if (pos_qty != 0 and 
                driver_symbol.upper() in pos_symbol and
                pos_exchange in exchanges_to_check):
                
                if option_type == "CE" and pos_symbol.endswith("CE"):
                    return pos_symbol
                elif option_type == "PE" and pos_symbol.endswith("PE"):
                    return pos_symbol
        
        return None
        
    except Exception as e:
        print(f"💀🔥 Error getting position symbol for {driver_symbol} {option_type}: {e}")
        return None

def calculate_position_size(action, symbol, exchange, quantity, inposition_qty):
    """
    Calculate correct position size for smart orders.
    NOTE: This system is LONG-ONLY - no short positions allowed!
    """
    # For entries: use quantity from trade manager
    if action in ["BUYCL", "BUYPT"]:
        position = quantity
    
    # For exits/stops: close the entire position (position_size=0 means close)
    elif action in ["SELCL", "SELPT", "SELST", "SELSP"]:
        position = 0  # Position size 0 = close position
    
    # For short covering (not used in LONG_ONLY)
    elif action == "BUYST":
        position = abs(inposition_qty)
    
    else:
        position = quantity
    
    # 🛡️ SAFEGUARD: Never allow negative position (would create short)
    if position < 0:
        print(f"🛡️ WARNING: Prevented negative position {position} for {action} {symbol}")
        position = 0
    
    return position

def map_to_derivative_exchange(exchange):
    """Map spot exchange to derivative exchange."""
    ex = exchange.upper()
    if ex == "NSE_INDEX":
        return "NFO"
    elif ex == "BSE_INDEX":
        return "BFO"
    else:
        return ex

INDEX_UNIVERSE = [
    "NIFTY", "BANKNIFTY",      # NSE spot
    "SENSEX", "BANKEX",        # BSE spot
    "CRUDEOIL", "CRUDEOILM", "NATURALGAS", "GOLDM", "SILVERM"  # MCX (both variants)
]

def _default_index_exchange(sym: str) -> str:
    s = (sym or "").upper()
    if s in ("NIFTY", "BANKNIFTY"):
        return "NSE_INDEX"
    if s in ("SENSEX", "BANKEX"):
        return "BSE_INDEX"
    # MCX roots - accept both CRUDEOIL and CRUDEOILM
    if s.startswith("CRUDEOIL") or s in ("NATURALGAS", "GOLDM", "SILVERM"):
        return "MCX"


def idx_ltp(sym: str) -> float:
    """
    Return the current LTP for any supported index root.
    Auto-selects exchange: NSE_INDEX/BSE_INDEX/MCX.
    """
    ex = normalize_exchange_for_symbol(sym, _default_index_exchange(sym))
    try:
        q = client.quotes(symbol=sym, exchange=ex)
        ltp = q.get("data", {}).get("ltp")
        if ltp is None:
            # Try alternative exchange mapping for MCX
            if ex == "MCX" and not sym.endswith("FUT"):
                # Try adding FUT suffix
                fut_sym = sym + "FUT"
                q = client.quotes(symbol=fut_sym, exchange=ex)
                ltp = q.get("data", {}).get("ltp")
            
            if ltp is None:
                print(f"⚠️ No LTP found for {sym} on {ex}")
                return None
        
        return float(ltp) if ltp is not None else None
    except Exception as e:
        print(f"⚠️ Error getting LTP for {sym} on {ex}: {e}")
        return None


def is_signal_stale_cached(symbol, exchange, entry_price, signal_type):
    """
    Cached version of stale signal check.
    
    LOGIC: Only mark as stale if price moved AGAINST the trade direction.
    - BUYCL: Stale if price dropped significantly (missed the move up)
    - BUYPT: Stale if price rose significantly (missed the move down)
    
    If price moved IN FAVOR of the trade, that's GOOD - execute anyway!
    """
    cache_key = f"{symbol}_{exchange}_{entry_price}_{signal_type}"
    
    # Check cache
    if cache_key in _stale_check_cache:
        cache_time, result = _stale_check_cache[cache_key]
        if time.time() - cache_time < _STALE_CACHE_TIMEOUT:
            return result
    
    # Perform fresh check
    try:
        _, renko_df, _ = resolve_renko_file_and_symbol(symbol, exchange)
        if renko_df is None or renko_df.empty:
            return False
        
        current_renko_brick = renko_df['Renko_Brick'].iloc[-1]
        brick_size = brick_for_runtime(symbol)
        
        # STALE_THRESHOLD: Number of bricks price must move AGAINST the trade
        # to be considered stale. Increased from 1 to 3 bricks.
        STALE_THRESHOLD_BRICKS = 3
        
        if signal_type == "BUYCL":
            # CALL entry: Only stale if price DROPPED more than threshold
            # If price went UP, that's good - don't mark as stale!
            result = current_renko_brick <= entry_price - (STALE_THRESHOLD_BRICKS * brick_size)
            
        elif signal_type == "BUYPT":
            # PUT entry: Only stale if price ROSE more than threshold  
            # If price went DOWN, that's good - don't mark as stale!
            result = current_renko_brick >= entry_price + (STALE_THRESHOLD_BRICKS * brick_size)
        else:
            result = False
        
        # Update cache
        _stale_check_cache[cache_key] = (time.time(), result)
        return result
        
    except Exception as e:
        print(f"⚠️ Error in cached stale check: {e}")
        return False
    
def order_management(trade_manager, client, ltp_dict):
    """
    Event-based order management that converts index symbols to CE/PE options.
    Uses INDEX LTP for order triggers, OPTION LTP for position sizing.
    Runs continuously in an infinite loop - ticks at full speed, only sleeps when orders execute.
    """
    global TRADING_ENABLED

    # Track processed orders to avoid duplicates - MUST BE OUTSIDE LOOP!
    last_processed_time = {}
    MIN_PROCESS_INTERVAL = 1.5  # seconds - Feb 1 fix: reduced from 3.0 to 1.5 (retry limits protect us)
    
    while True:
        try:
            # Load fresh data - no sleep here, just read
            trade_manager = read_csv(trade_manager_file)
            positions_book_df = get_positions_book(client)
            
            # Process all OPEN orders
            open_orders = trade_manager[trade_manager["order_status"] == "OPEN"]
            
            if open_orders.empty:
                # No open orders - just wait for next tick
                try:
                    # Wait for next tick event without blocking CPU
                    _ = ltp_event_queue.get(timeout=1)
                    # Clear queue
                    while True:
                        try:
                            ltp_event_queue.get_nowait()
                        except queue.Empty:
                            break
                except queue.Empty:
                    continue  # No tick in 1 second, loop again
                continue
            
            # Process each open order
            for index, trade in open_orders.iterrows():
                try:
                    symbol = trade["symbol"]
                    exchange = trade["exchange"]
                    action = trade["renko_signal"]
                    # pdb.set_trace()   # <-- stops here every time (first iteration too)
                    
                    # Rate limiting - check time
                    current_time = time.time()
                    if symbol in last_processed_time:
                        if current_time - last_processed_time[symbol] < MIN_PROCESS_INTERVAL:
                            continue  # Skip if processed recently

                    # Subscribe to WebSocket if not already subscribed
                    if not safe_subscribe_check(symbol):  # 🛡️ FEB 1 FIX: Thread-safe check
                        print(f"📡 Subscribing {symbol} to WebSocket...")
                        subscribe_to_websocket(client, [{"exchange": exchange, "symbol": symbol}])
                        safe_subscribe_add(symbol)  # 🛡️ FEB 1 FIX: Thread-safe add
                        # Immediately fetch initial LTP via REST (don't wait for WebSocket)
                        initial_ltp = get_ltp(exchange, symbol)
                        if initial_ltp is not None:
                            safe_ltp_update(symbol, float(initial_ltp))  # 🛡️ FEB 1 FIX: Thread-safe
                            print(f"✅ Initial LTP for {symbol}: {initial_ltp}")
                    
                    index_ltp, ltp_ts = safe_ltp_get(symbol)  # 🛡️ FEB 1 FIX: Thread-safe
                    ltp_age = time.time() - ltp_ts
                    
                    # 🛡️ FEB 1 FIX: Use symbol-specific staleness threshold
                    stale_threshold = get_ltp_stale_threshold(symbol)
                    
                    # Check if LTP is stale (older than threshold) - might mean WebSocket not working
                    if index_ltp is not None and ltp_age > stale_threshold:
                        print(f"⚠️ STALE LTP detected for {symbol}: {index_ltp} (age: {ltp_age:.1f}s > {stale_threshold}s threshold) - Fetching fresh via REST")
                        # Re-subscribe to WebSocket in case subscription dropped
                        if ltp_age > 30:  # If very stale, try re-subscribing
                            print(f"🔄 Re-subscribing {symbol} to WebSocket (LTP very stale)...")
                            try:
                                subscribe_to_websocket(client, [{"exchange": exchange, "symbol": symbol}])
                            except Exception as e:
                                print(f"⚠️ Re-subscribe failed: {e}")
                        fresh_ltp = get_ltp(exchange, symbol)
                        if fresh_ltp is not None:
                            index_ltp = float(fresh_ltp)
                            safe_ltp_update(symbol, index_ltp)  # 🛡️ FEB 1 FIX: Thread-safe
                            print(f"✅ Fresh LTP for {symbol}: {index_ltp}")
                    
                    if index_ltp is None:
                        # Try REST API as last resort
                        print(f"📡 No cached LTP for {symbol}, fetching via REST...")
                        index_ltp = get_ltp(exchange, symbol)
                        if index_ltp is not None:
                            index_ltp = float(index_ltp)
                            safe_ltp_update(symbol, index_ltp)  # 🛡️ FEB 1 FIX: Thread-safe
                        else:
                            continue  # No LTP available, skip
                    
                    # Update last processed time
                    last_processed_time[symbol] = current_time
                    
                    # =============================================
                    # STEP 1: DETERMINE ACTUAL TRADING SYMBOL AND EXCHANGE
                    # =============================================
                    trading_symbol = symbol
                    trading_exchange = exchange
                    
                    # Check if it's an index symbol
                    is_index = symbol.startswith(("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"))
                    is_mcx_index = symbol.startswith(("CRUDEOIL", "GOLDM", "SILVERM", "NATURALGAS"))
                    
                    # Map exchange for derivatives lookup
                    derivative_exchange = map_to_derivative_exchange(exchange)
                    
                    # For MCX futures
                    if exchange.upper() == "MCX" and symbol.upper().endswith("FUT"):
                        root = extract_root(symbol)
                        
                        # For entry signals, get CE/PE options
                        if action in ["BUYCL", "BUYPT"]:
                            ce_symbol, pe_symbol = get_current_option_symbols(client, root, exchange)
                            
                            if action == "BUYCL" and ce_symbol:
                                trading_symbol = ce_symbol
                                trading_exchange = "MCX"
                                print(f"🔵 BUYCL → Selected CE option: {ce_symbol}")
                            elif action == "BUYPT" and pe_symbol:
                                trading_symbol = pe_symbol
                                trading_exchange = "MCX"
                                print(f"🟣 BUYPT → Selected PE option: {pe_symbol}")
                            else:
                                print(f"⚠️ Could not get MCX option for {root}")
                                print(f"   Action: {action}, CE: {ce_symbol}, PE: {pe_symbol}")
                                continue
                    
                    # For entry signals: Get current CE/PE symbol
                    if is_index and action in ["BUYCL", "BUYPT"]:
                        option_type = "CE" if action == "BUYCL" else "PE"
                        ce_symbol, pe_symbol = get_current_option_symbols(client, symbol, exchange)
                        
                        if option_type == "CE" and ce_symbol:
                            trading_symbol = ce_symbol
                            trading_exchange = derivative_exchange
                            print(f"🔵 BUYCL → Selected CE option: {ce_symbol}")
                        elif option_type == "PE" and pe_symbol:
                            trading_symbol = pe_symbol
                            trading_exchange = derivative_exchange
                            print(f"🟣 BUYPT → Selected PE option: {pe_symbol}")
                        else:
                            print(f"⚠️ Could not get {option_type} option for {symbol}")
                            print(f"   CE: {ce_symbol}, PE: {pe_symbol}")
                            continue
                    
                    # For exit/stop signals: Get symbol from broker positions
                    elif (is_index or is_mcx_index) and action in ["SELCL", "SELPT", "SELST", "SELSP"]:
                        if action in ["SELCL", "SELST"]:
                            option_type = "CE"
                        else:  # SELPT, SELSP
                            option_type = "PE"
                        
                        root_for_position = extract_root(symbol) if exchange.upper() == "MCX" else symbol
                        position_symbol = get_position_symbol(client, root_for_position, option_type, exchange)
                        
                        if position_symbol:
                            trading_symbol = position_symbol
                            trading_exchange = derivative_exchange
                        else:
                            # Clean up non-existent positions
                            # if action == 'SELST':
                            #     close_positions(trade_manager, symbol, "BUYCL", "INPOSITION", "CLOSED")
                            #     close_positions(trade_manager, symbol, "SELST", "OPEN", "CLOSED")
                            #     close_positions(trade_manager, symbol, "SELCL", "OPEN", "CANCELLED")
                            #     print(f"🛑🛑✅ SELST executed already, cleaned up trade manager - CALL position closed for {symbol}")
                                
                            # elif action == 'SELSP':
                            #     close_positions(trade_manager, symbol, "BUYPT", "INPOSITION", "CLOSED")
                            #     close_positions(trade_manager, symbol, "SELSP", "OPEN", "CLOSED")
                            #     close_positions(trade_manager, symbol, "SELPT", "OPEN", "CANCELLED")
                            #     print(f"🛑🛑✅ SELSP executed already, cleaned up trade manager - PUT position closed for {symbol}")
                            # save_to_csv(trade_manager, trade_manager_file)
                            print("🛑🛑🛑🛑 Open algo down check immediately 🛑🛑🛑🛑")
                            # Brief pause after successful execution
                            # time.sleep(1)
                            
                            continue
                    
                    # =============================================
                    # STEP 2: GET ENTRY PRICE AND QUANTITY
                    # =============================================
                    try:
                        entry_price = float(trade["entry_price"])
                    except (ValueError, TypeError):
                        print(f"⚠️ Invalid entry price for {symbol}, skipping")
                        continue
                    
                    # Get current position quantity
                    inposition_qty = 0
                    if positions_book_df is not None:
                        if trading_symbol.endswith(("CE", "PE")):
                            inposition_qty = get_net_qty_on_exchange(positions_book_df, trading_symbol, trading_exchange)
                        else:
                            inposition_qty = get_net_qty_on_exchange(positions_book_df, trading_symbol, exchange)
                    if inposition_qty == 0:
                        # Clean up non-existent positions
                        if action == 'SELST':
                            # Get original BUYCL entry price BEFORE closing
                            original_entry = get_original_entry_price(trade_manager, symbol, "CALL")
                            close_positions(trade_manager, symbol, "BUYCL", "INPOSITION", "CLOSED", close_reason="STOPLOSS")
                            close_positions(trade_manager, symbol, "SELST", "OPEN", "CLOSED")
                            close_positions(trade_manager, symbol, "SELCL", "OPEN", "CANCELLED")
                            print(f"🛑🛑✅ SELST executed already, cleaned up trade manager - CALL position closed for {symbol}")
                            # 🛡️ Record cooldown ONLY if it was a LOSS (not trailing profit)
                            record_stoploss_for_cooldown(symbol, "SELST", entry_price, original_entry)
                            
                        elif action == 'SELSP':
                            # Get original BUYPT entry price BEFORE closing
                            original_entry = get_original_entry_price(trade_manager, symbol, "PUT")
                            close_positions(trade_manager, symbol, "BUYPT", "INPOSITION", "CLOSED", close_reason="STOPLOSS")
                            close_positions(trade_manager, symbol, "SELSP", "OPEN", "CLOSED")
                            close_positions(trade_manager, symbol, "SELPT", "OPEN", "CANCELLED")
                            print(f"🛑🛑✅ SELSP executed already, cleaned up trade manager - PUT position closed for {symbol}")
                            # 🛡️ Record cooldown ONLY if it was a LOSS (not trailing profit)
                            record_stoploss_for_cooldown(symbol, "SELSP", entry_price, original_entry)
                        save_to_csv(trade_manager, trade_manager_file)
                        time.sleep(1)
                    
                                          
                    # =============================================
                    # STEP 3: CHECK IF ORDER SHOULD BE EXECUTED
                    # =============================================
                    if action in ["BUYCL", "BUYPT"]:
                        if is_signal_stale_cached(symbol, exchange, entry_price, action):
                 
                            if action == 'BUYPT':
                                stale_close_positions(trade_manager, symbol, "BUYPT", entry_price, "OPEN", "CLOSED")
                                print(f"🛑🛑✅ STALE BUYPT CANCELLED - PUT position closed for {symbol} for entry: {entry_price}")
                                # 💤 SLEEP - Save again after updates
                                save_to_csv(trade_manager, trade_manager_file)
                                
                                # Brief pause after successful execution
                                time.sleep(0.2)                             
                                continue
                            
                            elif action == 'BUYCL':
                                stale_close_positions(trade_manager, symbol, "BUYCL", entry_price, "OPEN", "CLOSED")
                                print(f"🛑🛑✅ STALE BUYCL CANCELLED - CALL signal closed for {symbol} for entry: {entry_price}")
                                # 💤 SLEEP - Save again after updates
                                save_to_csv(trade_manager, trade_manager_file)
                                
                                # Brief pause after successful execution
                                time.sleep(0.2)                             
                                continue                           
                            
                    # 🔄 RE-READ FRESH LTP right before execution decision (fixes race condition)
                    fresh_ltp = ltp_dict.get(symbol)
                    if fresh_ltp is not None:
                        index_ltp = fresh_ltp
                    
                    should_execute, new_status = should_execute_order(
                        action=action,
                        ltp=index_ltp,
                        entry_price=entry_price,
                        inposition_qty=inposition_qty,
                        symbol=symbol,
                        exchange=exchange
                    )
                    
                    # # =============================================
                    # # STEP 3: CHECK IF ORDER SHOULD BE EXECUTED
                    # # =============================================
                    # if action in ["BUYCL", "BUYPT"] and new_status == 'CLOSED':
                    #     # if is_signal_stale_cached(symbol, exchange, entry_price, action):
                 
                    #     if action == 'BUYPT':
                    #         stale_close_positions(trade_manager, symbol, "BUYPT", entry_price, "OPEN", "CLOSED")
                    #         print(f"🛑🛑✅ STALE BUYPT CANCELLED - PUT position closed for {symbol} for entry: {entry_price}")
                    #         # 💤 SLEEP - Save again after updates
                    #         save_to_csv(trade_manager, trade_manager_file)
                            
                    #         # Brief pause after successful execution
                    #         time.sleep(0.2)                             
                    #         continue
                        
                    #     elif action == 'BUYCL':
                    #         stale_close_positions(trade_manager, symbol, "BUYCL", entry_price, "OPEN", "CLOSED")
                    #         print(f"🛑🛑✅ STALE BUYCL CANCELLED - PUT position closed for {symbol} for entry: {entry_price}")
                    #         # 💤 SLEEP - Save again after updates
                    #         save_to_csv(trade_manager, trade_manager_file)
                            
                    #         # Brief pause after successful execution
                    #         time.sleep(0.2)                             
                    #         continue    
                            
                    if not should_execute:
                       
                        if action in ["SELST", "SELSP"] and abs(index_ltp - entry_price) < 2:
                            print(f"🟡 {action:<6} - {symbol:<25} : INDEX LTP {index_ltp:>7.2f} : Stop {entry_price:>7.2f} : Diff {(index_ltp-entry_price):>6.2f}")

                        continue
                    
                    print(f"✅ {action} for {symbol}: READY TO EXECUTE! (LTP={index_ltp}, Entry={entry_price})")
                    
                    # =============================================
                    # STEP 4: GET OPTION LTP FOR POSITION SIZING ONLY
                    # =============================================
                    option_ltp = None
                    if trading_symbol != symbol:  # This is an option
                        option_ltp, _ = safe_ltp_get(trading_symbol)  # 🛡️ FEB 1 FIX: Thread-safe
                        if option_ltp is None:
                            try:
                                option_ltp = get_ltp_on_exchange(client, trading_symbol, trading_exchange)
                                if option_ltp:
                                    safe_ltp_update(trading_symbol, float(option_ltp))  # 🛡️ FEB 1 FIX: Thread-safe
                            except Exception as e:
                                print(f"⚠️ Error getting OPTION LTP: {e}")
                                option_ltp = entry_price  # Fallback
                    else:
                        option_ltp = index_ltp
                    
                    # Calculate quantity
                    account_balance = float(get_account_balance(client)) or 1000000
                    lot_size = get_lotsize(derivative_exchange, trading_symbol)
                    row_qty = trade.get("quantity", None)
                    
                    if action in ["BUYCL", "BUYPT"]:
                        if row_qty is not None and row_qty > 0:
                            quantity = int(row_qty)
                        elif FIXED_NUM_LOTS is not None:
                            quantity = int(FIXED_NUM_LOTS) * lot_size
                        else:
                            if INVESTMENT_PERCENT:
                                amount_to_invest = (INVESTMENT_PERCENT / 100.0) * account_balance
                            else:
                                amount_to_invest = 10000
                            lot_cost = option_ltp * lot_size
                            num_lots = int(amount_to_invest // lot_cost) if lot_cost > 0 else 1
                            num_lots = max(num_lots, 1)
                            quantity = num_lots * lot_size
                    
                    elif action in ["SELCL", "SELPT", "SELST", "SELSP"]:
                        quantity = abs(inposition_qty)
                        if quantity == 0:
                            print(f"⚠️ No position to close for {trading_symbol}, skipping")
                            continue
                    else:
                        quantity = int(row_qty or 75)
                    
                    # =============================================
                    # STEP 5: PLACE ORDER WITH CORRECT EXCHANGE
                    # =============================================
                    # Calculate position size using actual trading parameters
                    position = calculate_position_size(action, trading_symbol, trading_exchange, 
                                                      quantity, inposition_qty)
        
                    # Determine order action
                    if action in ["BUYCL", "BUYPT", "BUYST"]:
                        order_action = "BUY"
                    elif action in ["SELCL", "SELPT", "SELST", "SELSP"]:
                        order_action = "SELL"
                        position = 0
                        
                        # 🚫 SAFEGUARD: Only allow SELL if we have a position to close (prevent shorting)
                        if inposition_qty <= 0:
                            print(f"🚫 BLOCKING {action} for {trading_symbol}: No position to close (qty={inposition_qty})")
                            print(f"   This would create a SHORT position - not allowed!")
                            trade_manager.loc[index, "order_status"] = "CANCELLED"
                            continue  # Skip this order
                            
                    else:
                        # For any other signal, only allow BUY (no shorting)
                        if action.startswith("BUY"):
                            order_action = "BUY"
                        elif action.startswith("SEL"):
                            # Check if we have a position to close
                            if inposition_qty <= 0:
                                print(f"🚫 BLOCKING {action} for {trading_symbol}: No position to close (qty={inposition_qty})")
                                trade_manager.loc[index, "order_status"] = "CANCELLED"
                                continue
                            order_action = "SELL"
                            position = 0
                        else:
                            print(f"⚠️ Unknown action {action} for {trading_symbol} - defaulting to BUY")
                            order_action = "BUY"
        
                    # Log order attempt
                    print(f"🎯 Executing {action} for {trading_symbol} on {trading_exchange}: "
                          f"INDEX LTP={index_ltp:.2f}, OPTION LTP={option_ltp:.2f}, "
                          f"Entry={entry_price:.2f}, Qty={quantity}, Position={position}")
        
                    # =====================================================
                    # 🛡️ FEB 1 FIX: RETRY LIMIT CHECK (prevents infinite rejection loops)
                    # =====================================================
                    if action in ["BUYCL", "BUYPT"]:
                        can_retry, retry_count, retry_reason = check_and_increment_retry(symbol, action, entry_price)
                        if not can_retry:
                            print(f"🚫⏰ {action} CANCELLED for {symbol}: {retry_reason}")
                            print(f"   → Entry: {entry_price}, This prevents infinite rejected order loops!")
                            trade_manager.loc[index, "order_status"] = "CLOSED"
                            trade_manager.loc[index, "close_reason"] = "MAX_RETRIES"
                            save_to_csv(trade_manager, trade_manager_file)
                            continue
                        elif retry_count > 1:
                            print(f"⚠️ {action} for {symbol}: Attempt #{retry_count} (max: {MAX_ORDER_RETRIES})")
                    
                    # =====================================================
                    # 🛡️ FEB 1 FIX: STALE ENTRY VALIDATION (prevents buying at bad prices)
                    # =====================================================
                    # Check if price has moved too far AGAINST the trade before executing
                    if action in ["BUYCL", "BUYPT"]:
                        bs = brick_for_runtime(symbol)
                        if bs > 0:
                            price_distance_bricks = abs(index_ltp - entry_price) / bs
                            
                            # For CALL: Price dropping 2+ bricks below entry = missed the up move
                            if action == "BUYCL" and index_ltp < entry_price - (2 * bs):
                                print(f"⛔🛑 STALE BUYCL CANCELLED for {symbol}: LTP {index_ltp:.2f} dropped {price_distance_bricks:.1f} bricks below entry {entry_price:.2f}")
                                print(f"   → Signal is stale! Would be buying at a losing price. Cancelling.")
                                trade_manager.loc[index, "order_status"] = "CLOSED"
                                trade_manager.loc[index, "close_reason"] = "STALE_ENTRY"
                                clear_retry_tracker(symbol, action, entry_price)
                                save_to_csv(trade_manager, trade_manager_file)
                                continue
                            
                            # For PUT: Price rising 2+ bricks above entry = missed the down move
                            if action == "BUYPT" and index_ltp > entry_price + (2 * bs):
                                print(f"⛔🛑 STALE BUYPT CANCELLED for {symbol}: LTP {index_ltp:.2f} rose {price_distance_bricks:.1f} bricks above entry {entry_price:.2f}")
                                print(f"   → Signal is stale! Would be buying at a losing price. Cancelling.")
                                trade_manager.loc[index, "order_status"] = "CLOSED"
                                trade_manager.loc[index, "close_reason"] = "STALE_ENTRY"
                                clear_retry_tracker(symbol, action, entry_price)
                                save_to_csv(trade_manager, trade_manager_file)
                                continue
        
                    # =====================================================
                    # 🛡️ BULLETPROOF SHORT PREVENTION - FINAL CHECK
                    # =====================================================
                    # This is the LAST LINE OF DEFENSE before placing an order
                    # NEVER allow a SELL order that would create a short position
                    if order_action == "SELL":
                        # Re-verify position exists RIGHT NOW before placing SELL
                        final_check_qty = 0
                        if positions_book_df is not None:
                            final_check_qty = get_net_qty_on_exchange(positions_book_df, trading_symbol, trading_exchange)
                        
                        if final_check_qty <= 0:
                            print(f"🛡️🚫 BULLETPROOF BLOCK: Preventing SELL for {trading_symbol}")
                            print(f"   → Current position: {final_check_qty} (need > 0 to SELL)")
                            print(f"   → This would create a SHORT position - ABSOLUTELY NOT ALLOWED!")
                            trade_manager.loc[index, "order_status"] = "CANCELLED"
                            save_to_csv(trade_manager, trade_manager_file)
                            continue  # Skip this order
                        
                        # Also ensure quantity doesn't exceed position
                        if quantity > final_check_qty:
                            print(f"🛡️⚠️ Reducing SELL qty from {quantity} to {final_check_qty} to prevent short")
                            quantity = final_check_qty
                    
                    # =====================================================
                    # 🛡️ FEB 1 FIX: PENDING ORDER CHECK (prevents duplicate orders)
                    # =====================================================
                    if action in ["BUYCL", "BUYPT"]:
                        has_pending, pending_order_id, pending_reason = check_pending_order(symbol, action, entry_price)
                        if has_pending:
                            print(f"⏳ Skipping {action} for {symbol}: {pending_reason}")
                            continue  # Don't place duplicate order
                    
                    # =====================================================
                    # 🛡️ FEB 1 FIX: FUND/MARGIN CHECK BEFORE BUY ORDERS
                    # =====================================================
                    # Prevents order rejections due to insufficient funds
                    if action in ["BUYCL", "BUYPT"] and order_action == "BUY":
                        try:
                            available_funds = float(get_account_balance(client) or 0)
                            required_margin = float(option_ltp or 0) * float(quantity)
                            
                            # Check if we have enough funds (with 10% buffer)
                            if required_margin > 0 and available_funds < required_margin * 1.1:
                                print(f"💰🚫 INSUFFICIENT FUNDS for {symbol}:")
                                print(f"   → Required margin: ₹{required_margin:,.2f}")
                                print(f"   → Available funds: ₹{available_funds:,.2f}")
                                print(f"   → Cancelling order to prevent rejection spam!")
                                trade_manager.loc[index, "order_status"] = "CLOSED"
                                trade_manager.loc[index, "close_reason"] = "INSUFFICIENT_FUNDS"
                                if action in ["BUYCL", "BUYPT"]:
                                    clear_retry_tracker(symbol, action, entry_price)
                                save_to_csv(trade_manager, trade_manager_file)
                                continue
                        except Exception as fund_err:
                            print(f"⚠️ Could not check funds for {symbol}: {fund_err}")
                            # Continue anyway - let broker handle it
                    
                    # Place smart order with CORRECT exchange
                    response = client.placesmartorder(
                        strategy=strategy,
                        symbol=trading_symbol,
                        action=order_action,
                        exchange=trading_exchange,  # Use derivative exchange for options
                        price_type="MARKET",  # Better price control
                        product=product,
                        quantity=int(quantity),
                        position_size=position,
                    )                 
                   
                    print(f"✅ {action} Order Response for {symbol}: {response.get('status', 'No status')}")
                    
                    if response.get("status") == "success":
                        order_id = str(response.get("orderid"))
                        
                        # 🛡️ FEB 1 FIX: Track this pending order to prevent duplicates
                        if action in ["BUYCL", "BUYPT"]:
                            track_pending_order(symbol, action, entry_price, order_id)
                        
                        # 💤 SLEEP ONLY HERE - wait for order confirmation
                        if SKIP_ORDER_CONFIRMATION:
                            order_executed = True
                            print(f"⚙️ [SIMULATION MODE] Skipping order confirmation for {action} ({symbol})")
                        else:
                            # Small sleep to allow order to be processed
                            order_executed = is_order_executed(client, order_id, strategy=strategy)
                            if order_executed:
                                time.sleep(0.5)
                            
                        if order_executed and new_status in ['INPOSITION', 'CLOSED']:
                            # Update trade manager
                            trade_manager.loc[index, "orderid"] = str(order_id)
                            trade_manager.loc[index, "order_status"] = new_status
                            trade_manager.loc[index, "index_ltp"] = float(index_ltp)
                            
                            # Get execution price - ✅ FIX: Extract ACTUAL fill price from broker
                            exec_price = None
                            trade_book_df = get_trade_book(client)
                            if trade_book_df is not None and order_id:
                                tb_row = trade_book_df.loc[trade_book_df["orderid"].astype(str) == str(order_id)]
                                if not tb_row.empty:
                                    try:
                                        # ✅ FIX: Try multiple column names for fill price
                                        row_data = tb_row.iloc[0]
                                        for price_col in ["avgprice", "price", "fillprice", "averageprice"]:
                                            if price_col in row_data and row_data[price_col] is not None:
                                                try:
                                                    exec_price = float(row_data[price_col])
                                                    if exec_price > 0:
                                                        print(f"✅ Got exec_price from trade_book[{price_col}]: {exec_price}")
                                                        break
                                                except (TypeError, ValueError):
                                                    continue
                                    except Exception as e:
                                        print(f"⚠️ Error extracting exec_price from trade_book: {e}")
                                        exec_price = None
                            
                            if exec_price is None:
                                order_book = get_order_book(client)
                                if order_book is not None and order_id:
                                    ob_row = order_book[order_book["orderid"].astype(str) == str(order_id)]
                                    if not ob_row.empty:
                                        try:
                                            # ✅ FIX: Try multiple column names for fill price
                                            row_data = ob_row.iloc[0]
                                            for price_col in ["avgprice", "price", "fillprice", "averageprice"]:
                                                if price_col in row_data and row_data[price_col] is not None:
                                                    try:
                                                        exec_price = float(row_data[price_col])
                                                        if exec_price > 0:
                                                            print(f"✅ Got exec_price from order_book[{price_col}]: {exec_price}")
                                                            break
                                                    except (TypeError, ValueError):
                                                        continue
                                        except Exception as e:
                                            print(f"⚠️ Error extracting exec_price from order_book: {e}")
                                            exec_price = None
                            
                            if exec_price is not None and exec_price <= 0:
                                exec_price = entry_price
                            
                            # ✅ FINAL FALLBACK: If exec_price is still None, use entry_price
                            if exec_price is None:
                                print(f"⚠️ Could not get exec_price from broker, using entry_price: {entry_price}")
                                exec_price = entry_price
                            
                            if exec_price is not None:
                                trade_manager.loc[index, "exec_price"] = float(exec_price)
                            
                            # 💤 SLEEP - Save trade manager to DB
                            save_to_csv(trade_manager, trade_manager_file)
                            
                            # Handle stop loss creation and closures
                            if action in ['BUYCL', 'BUYPT']:
                                trade_manager = create_stop_loss(trade, symbol, action, entry_price, 
                                                               brick_for_runtime(symbol), trade_manager, 
                                                               index, direction="SELL")
                            
                            # Handle status updates
                            if action == 'BUYCL' and new_status == 'INPOSITION':
                                trade_manager.at[index, "order_status"] = "INPOSITION"
                                print(f"✅ BUYCL order {order_id} executed for {trading_symbol}")
                                
                            elif action == 'BUYPT' and new_status == 'INPOSITION':
                                trade_manager.at[index, "order_status"] = "INPOSITION"
                                print(f"✅ BUYPT order {order_id} executed for {trading_symbol}")
                                
                            elif action == 'SELST' and new_status == 'CLOSED':
                                # Get original BUYCL entry price BEFORE closing
                                original_entry = get_original_entry_price(trade_manager, symbol, "CALL")
                                close_positions(trade_manager, symbol, "BUYCL", "INPOSITION", "CLOSED", close_reason="STOPLOSS")
                                close_positions(trade_manager, symbol, "SELST", "OPEN", "CLOSED")
                                close_positions(trade_manager, symbol, "SELCL", "OPEN", "CANCELLED")
                                print(f"🛑🛑✅ SELST executed - CALL position closed for {symbol}")
                                # 🛡️ Record cooldown ONLY if it was a LOSS (not trailing profit)
                                record_stoploss_for_cooldown(symbol, "SELST", entry_price, original_entry)
                                
                            elif action == 'SELSP' and new_status == 'CLOSED':
                                # Get original BUYPT entry price BEFORE closing
                                original_entry = get_original_entry_price(trade_manager, symbol, "PUT")
                                close_positions(trade_manager, symbol, "BUYPT", "INPOSITION", "CLOSED", close_reason="STOPLOSS")
                                close_positions(trade_manager, symbol, "SELSP", "OPEN", "CLOSED")
                                close_positions(trade_manager, symbol, "SELPT", "OPEN", "CANCELLED")
                                print(f"🛑🛑✅ SELSP executed - PUT position closed for {symbol}")
                                # 🛡️ Record cooldown ONLY if it was a LOSS (not trailing profit)
                                record_stoploss_for_cooldown(symbol, "SELSP", entry_price, original_entry)
                            
                            elif action == 'SELCL' and new_status == 'CLOSED':
                                close_positions(trade_manager, symbol, "BUYCL", "INPOSITION", "CLOSED", close_reason="SIGNAL_EXIT")
                                close_positions(trade_manager, symbol, "SELST", "OPEN", "CANCELLED")
                                close_positions(trade_manager, symbol, "SELCL", "OPEN", "CLOSED")
                                print(f"🛑🛑✅ SELCL executed - CALL position closed for {symbol}")
                                
                            elif action == 'SELPT' and new_status == 'CLOSED':
                                close_positions(trade_manager, symbol, "BUYPT", "INPOSITION", "CLOSED", close_reason="SIGNAL_EXIT")
                                close_positions(trade_manager, symbol, "SELSP", "OPEN", "CANCELLED")
                                close_positions(trade_manager, symbol, "SELPT", "OPEN", "CLOSED")
                                print(f"🛑🛑✅ SELPT executed - PUT position closed for {symbol}")
                                                     
                            # 💤 SLEEP - Save again after updates
                            save_to_csv(trade_manager, trade_manager_file)
                            
                            # Clear retry tracker on success
                            if action in ["BUYCL", "BUYPT"]:
                                clear_retry_tracker(symbol, action, entry_price)
                            
                            # 🔄 FEB 1 FIX: Refresh positions_book_df after successful order
                            # This prevents stale position data in subsequent iterations
                            positions_book_df = get_positions_book(client)
                            
                            # Brief pause after successful execution
                            time.sleep(0.2)
                        else:
                            # =====================================================
                            # 🛡️ FEB 1 FIX: PROPER REJECTED ORDER HANDLING
                            # =====================================================
                            # Order was not confirmed (could be rejected, pending, or failed)
                            # Check the actual status to determine action
                            try:
                                status_resp = client.orderstatus(order_id=order_id, strategy=strategy)
                                actual_status = status_resp.get("data", {}).get("order_status", "").lower()
                                
                                if actual_status == "rejected":
                                    # Order was REJECTED (e.g., insufficient funds)
                                    reject_reason = status_resp.get("data", {}).get("reject_reason", "Unknown")
                                    print(f"❌🚫 Order REJECTED for {symbol}: {reject_reason}")
                                    print(f"   → Closing this signal to prevent infinite retry loop!")
                                    trade_manager.loc[index, "order_status"] = "CLOSED"
                                    trade_manager.loc[index, "close_reason"] = f"REJECTED:{reject_reason[:30]}"
                                    if action in ["BUYCL", "BUYPT"]:
                                        clear_retry_tracker(symbol, action, entry_price)
                                    save_to_csv(trade_manager, trade_manager_file)
                                elif actual_status in ["cancelled", "canceled"]:
                                    print(f"❌ Order CANCELLED for {symbol}")
                                    trade_manager.loc[index, "order_status"] = "CANCELLED"
                                    if action in ["BUYCL", "BUYPT"]:
                                        clear_retry_tracker(symbol, action, entry_price)
                                    save_to_csv(trade_manager, trade_manager_file)
                                else:
                                    # Still pending or unknown - keep as OPEN but track retries
                                    print(f"⏳ Order status '{actual_status}' for {symbol}, keeping as OPEN for retry")
                                    trade_manager.loc[index, "order_status"] = "OPEN"
                            except Exception as status_err:
                                print(f"⚠️ Could not get order status for {symbol}: {status_err}")
                                # On error, keep as OPEN for retry (will hit retry limit eventually)
                                trade_manager.loc[index, "order_status"] = "OPEN"
                    
                    elif response.get("status") == "error":
                        error_msg = response.get('message', 'Unknown error')
                        print(f"❌ Order failed for {symbol}: {error_msg}")
                        
                        # Check if it's a fund-related error
                        if any(x in error_msg.lower() for x in ['fund', 'margin', 'insufficient', 'balance']):
                            print(f"   → FUND ERROR detected - closing signal to prevent retry spam!")
                            trade_manager.loc[index, "order_status"] = "CLOSED"
                            trade_manager.loc[index, "close_reason"] = "INSUFFICIENT_FUNDS"
                            if action in ["BUYCL", "BUYPT"]:
                                clear_retry_tracker(symbol, action, entry_price)
                        else:
                            trade_manager.loc[index, "order_status"] = "OPEN"
                        save_to_csv(trade_manager, trade_manager_file)
                
                except Exception as e:
                    print(f"💀🔥 Error processing trade for {symbol}: {e}")
                    import traceback
                    traceback.print_exc()
                    # Mark trade as ERROR status for manual review (Feb 1 fix)
                    try:
                        trade_manager.loc[index, "order_status"] = "ERROR"
                        trade_manager.loc[index, "close_reason"] = f"EXCEPTION:{str(e)[:40]}"
                        save_to_csv(trade_manager, trade_manager_file)
                    except:
                        pass
                    continue
            
            # Save trade manager if any changes were made
            save_to_csv(trade_manager, trade_manager_file)
            
            # ⏰ Event-driven wait - NO SLEEP HERE, use ticker events
            try:
                # Wait for next tick (max 100ms timeout to check for new orders)
                tick_symbol = ltp_event_queue.get(timeout=0.1)
                # Process any additional ticks in the queue
                while True:
                    try:
                        ltp_event_queue.get_nowait()
                    except queue.Empty:
                        break
            except queue.Empty:
                # No new ticks, continue loop immediately
                continue
            
        except Exception as e:
            print(f"💀🔥 Error managing orders: {e}")
            # Brief sleep on error
            time.sleep(0.5)
      
def ohlc_update_monitor():
    print("Started OHLC update monitor thread.")
    while True:
        now = datetime.now()
        # Schedule next check at next minute + 15 sec
        next_min = (now.minute // 1 + 1) * 1
        if next_min == 60:
            next_time = now.replace(hour=(now.hour + 1) % 24, minute=0, second=15, microsecond=0)
        else:
            next_time = now.replace(minute=next_min, second=15, microsecond=0)

        if next_time <= now:
            next_time += timedelta(minutes=1)

        sleep_secs = (next_time - now).total_seconds()
        print(f"🕰️ Next OHLC monitor check scheduled at {next_time.strftime('%H:%M:%S')}")
        time.sleep(sleep_secs)

        # 🚨 Check each symbol for staleness
        # 🔹 Clean up symbols no longer tracked in symbols_to_trade
        try:
            tracked = set(read_csv(symbols_file)["symbol"].astype(str).str.upper())
            for s in list(last_ohlc_update.keys()):
                if s.upper() not in tracked:
                    last_ohlc_update.pop(s, None)
        except Exception as e:
            print(f"⚠️ Could not prune last_ohlc_update: {e}")

        # 🔹 Now check only active symbols for staleness
        for symbol, last_update in list(last_ohlc_update.items()):
            time_since = (datetime.now() - last_update).total_seconds()
            u = symbol.upper()

            # --- Threshold mapping ---
            # Check if it's a pure index symbol (not options/futures)
            if u in ("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX"):
                threshold = 180  # 3 min for INDEX symbols
            elif u.startswith(("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX")):
                threshold = 310  # 5 min 10 sec for options
            elif u.endswith("FUT"):
                threshold = 1810  # 30 min 10 sec
            elif u.startswith(("GOLD", "SILVER", "CRUDE", "NATURALGAS")):
                threshold = 75  # relaxed 1.5 min for MCX options
            else:
                threshold = 65  # default 1 min

            if time_since > threshold:
                print(f"🚨 ALERT: No OHLC update for {symbol} in {int(time_since)}s (threshold: {threshold}s)")

# --- Square-off config ---
# ============================================
# ⏰ SQUARE-OFF TIMES CONFIGURATION
# ============================================
# Note: TRADING_START_TIMES and TRADING_END_TIMES are defined earlier (around line 1143)
# Only SQUARED_OFF_TIMES is defined here for square_off_guard function
# ============================================

# Square-off times (close ALL existing positions)
# MCX-ONLY Square-off Times
SQUARED_OFF_TIMES = {
    "MCX": (23, 15),         # 11:15 PM - Square off MCX positions
}

SQUARED_OFF_EXCHANGES = set()

# Keep track so we don't run twice in the same day per exchange
_last_sqoff_done = {"MCX": None}

# Track which exchanges have completed their trading day (for data fetch control)
_exchange_day_complete = {"MCX": False}
_exchange_day_complete_lock = threading.Lock()

def _now_ist():
    return datetime.now(ZoneInfo("Asia/Kolkata"))

def _time_reached(target_h, target_m):
    now = _now_ist()
    return now.hour > target_h or (now.hour == target_h and now.minute >= target_m)


# ============================================
# ⏰ PER-EXCHANGE TRADING TIME HELPER FUNCTIONS
# ============================================

def is_exchange_within_trading_hours(exchange: str) -> tuple:
    """
    Check if an exchange is within its trading window for NEW entries.
    
    Args:
        exchange: Exchange name (NSE_INDEX, BSE_INDEX, MCX, NFO, BFO)
    
    Returns:
        (is_active: bool, reason: str)
    """
    now = _now_ist()
    current_mins = now.hour * 60 + now.minute
    
    exchange_upper = exchange.upper()
    
    # Map derivative exchanges to their parent
    if exchange_upper in ("NFO",):
        exchange_upper = "NSE_INDEX"
    elif exchange_upper in ("BFO",):
        exchange_upper = "BSE_INDEX"
    
    # Get trading times for this exchange
    start_time = TRADING_START_TIMES.get(exchange_upper)
    end_time = TRADING_END_TIMES.get(exchange_upper)
    
    if start_time is None or end_time is None:
        return True, f"Unknown exchange {exchange}, allowing by default"
    
    start_mins = start_time[0] * 60 + start_time[1]
    end_mins = end_time[0] * 60 + end_time[1]
    
    if current_mins < start_mins:
        return False, f"⏰ {exchange}: Too early (starts at {start_time[0]:02d}:{start_time[1]:02d})"
    
    if current_mins >= end_mins:
        return False, f"⏰ {exchange}: NO NEW TRADES (cutoff at {end_time[0]:02d}:{end_time[1]:02d})"
    
    return True, f"✅ {exchange}: Within trading hours ({now.strftime('%H:%M')})"


def is_exchange_squared_off(exchange: str) -> bool:
    """
    Check if an exchange has passed its square-off time for today.
    """
    now = _now_ist()
    
    exchange_upper = exchange.upper()
    if exchange_upper in ("NFO",):
        exchange_upper = "NSE_INDEX"
    elif exchange_upper in ("BFO",):
        exchange_upper = "BSE_INDEX"
    
    sqoff_time = SQUARED_OFF_TIMES.get(exchange_upper)
    
    if sqoff_time is None:
        return False
    
    return now.hour > sqoff_time[0] or (now.hour == sqoff_time[0] and now.minute >= sqoff_time[1])


def should_fetch_data_for_exchange(exchange: str) -> bool:
    """
    Determine if we should fetch OHLC data for this exchange.
    Returns False if exchange has completed its trading day (squared off).
    
    This allows MCX to continue fetching data after NFO/BFO are done.
    """
    global _exchange_day_complete
    
    exchange_upper = exchange.upper()
    if exchange_upper in ("NFO",):
        exchange_upper = "NSE_INDEX"
    elif exchange_upper in ("BFO",):
        exchange_upper = "BSE_INDEX"
    
    with _exchange_day_complete_lock:
        # Check if already marked as complete for today
        if _exchange_day_complete.get(exchange_upper, False):
            return False
        
        # Check if squared off
        if is_exchange_squared_off(exchange_upper):
            _exchange_day_complete[exchange_upper] = True
            print(f"🛑 {exchange_upper}: Trading day complete - stopping data fetch")
            # Check if other exchanges are still active
            active_exchanges = [ex for ex, done in _exchange_day_complete.items() if not done]
            if active_exchanges:
                print(f"   ✅ Still active: {', '.join(active_exchanges)}")
            return False
    
    return True


def reset_exchange_day_complete():
    """Reset the day-complete flags at start of new trading day."""
    global _exchange_day_complete
    with _exchange_day_complete_lock:
        for key in _exchange_day_complete:
            _exchange_day_complete[key] = False
    print("✅ Exchange day-complete flags reset for new trading day")

def _disable_trading():
    # Flip the engine-wide kill switches
    global TRADING_ENABLED
    TRADING_ENABLED = False
    try:
        websocket_active.clear()  # stop live stream
    except Exception:
        pass


def _cancel_open_rows_for_exchange(trade_manager, exchange):
    """
    Marks all OPEN rows for the exchange as CANCELLED (including stops),
    then persists the sheet.
    """
    # Use .astype(str) to handle NaN or non-string values in exchange column
    mask = (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) & \
           (trade_manager["order_status"] == "OPEN")
    if mask.any():
        trade_manager.loc[mask, "order_status"] = "CANCELLED"
    save_to_csv(trade_manager, trade_manager_file)

def _close_broker_positions_for_exchange(client, exchange):
    """
    Closes ALL net positions at the broker for the given exchange by
    sending an opposite MARKET order for abs(quantity).
    """
    pos_df = get_positions_book(client)  # your existing helper
    if pos_df is None or pos_df.empty:
        return

    # Normalize and filter by exchange - use .astype(str) to handle non-string values
    rows = pos_df[pos_df["exchange"].astype(str).str.upper() == exchange.upper()]
    for _, row in rows.iterrows():
        symbol = row["symbol"]
        qty = int(row["quantity"] or 0)
        if qty == 0:
            continue
        action = "SELL" if qty > 0 else "BUY"
        try:
            print(f"🔒 Square-off: {exchange} | {symbol} | Opp:{action} | Qty:{abs(qty)}")
            client.placesmartorder(  # mirrors your normal market orders
                strategy="Square-Off",
                symbol=symbol,
                action=action,
                exchange=exchange,
                price_type="MARKET",
                product=product,
                quantity=abs(qty),
                position_size=0
            )
        except Exception as e:
            print(f"⚠️ Square-off close failed for {symbol}: {e}")


def _mark_inposition_rows_closed(trade_manager, exchange):
    """
    For bookkeeping: if we square-off at broker, reflect that by marking
    any INPOSITION entry rows as CLOSED and cancelling their stops.
    🛡️ LONG_ONLY: Only process BUY entries (BUYEN), not SELL entries
    """
    # Close BUYEN entries that are INPOSITION (LONG_ONLY - no SELEN/SELRE)
    # Use .astype(str) to handle NaN or non-string values in exchange column
    for sig in ("BUYEN", "BUYCL", "BUYPT"):
        mask = (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) & \
               (trade_manager["renko_signal"] == sig) & \
               (trade_manager["order_status"] == "INPOSITION")
        if mask.any():
            trade_manager.loc[mask, "order_status"] = "CLOSED"

    # Cancel any OPEN stops for that exchange
    for stop_sig in ("SELST", "SELSP", "SELCL", "SELPT"):
        mask = (trade_manager["exchange"].astype(str).str.upper() == exchange.upper()) & \
               (trade_manager["renko_signal"] == stop_sig) & \
               (trade_manager["order_status"] == "OPEN")
        if mask.any():
            trade_manager.loc[mask, "order_status"] = "CANCELLED"

    save_to_csv(trade_manager, trade_manager_file)

def square_off_guard(client, poll_seconds: int = 60):
    """
    Monitor for square-off times and execute position closing.
    Returns when square-off is completed for all exchanges.
    """
    print(f"🔒 Square-off guard started, checking every {poll_seconds} seconds")
    
    try:
        while SQUARE_OFF_ACTIVE:
            now_ist = _now_ist()
            today_date = now_ist.date()
            
            for exchange, (target_hour, target_minute) in SQUARED_OFF_TIMES.items():
                # Skip if already squared off today
                if _last_sqoff_done.get(exchange) == today_date:
                    continue
                
                # Check if it's time to square off (handles both current hour AND past hour)
                if now_ist.hour > target_hour or (now_ist.hour == target_hour and now_ist.minute >= target_minute):
                    print(f"🕒 Square-off time reached for {exchange}")
                    
                    try:
                        # 1. Cancel all open orders
                        trade_manager = read_csv(trade_manager_file)
                        _cancel_open_rows_for_exchange(trade_manager, exchange)
                        
                        # 2. Close broker positions
                        _close_broker_positions_for_exchange(client, exchange)
                        
                        # 3. Update trade manager
                        _mark_inposition_rows_closed(trade_manager, exchange)
                        
                        # 4. Mark as done for today
                        _last_sqoff_done[exchange] = today_date
                        SQUARED_OFF_EXCHANGES.add(exchange)
                        
                        # 5. Mark exchange as day-complete (stops data fetch)
                        with _exchange_day_complete_lock:
                            _exchange_day_complete[exchange] = True
                        
                        print(f"✅ Square-off completed for {exchange}")
                        
                        # Check if other exchanges still active
                        active = [ex for ex, done in _exchange_day_complete.items() if not done]
                        if active:
                            print(f"   📊 Still active exchanges: {', '.join(active)}")
                        
                    except Exception as e:
                        print(f"💀🔥 Square-off failed for {exchange}: {e}")
                        # Retry on next iteration
            
            # Sleep until next check
            time.sleep(poll_seconds)
            
    except Exception as e:
        print(f"💀🔥 Square-off guard crashed: {e}")
        raise
       
def check_ltp(exchange, symbol):
    """
    Simple function to fetch and print the Last Traded Price (LTP)
    for a given index or option symbol.
    """
    try:
        quote = client.quotes(symbol=symbol, exchange=exchange)
        ltp = quote.get('data', {}).get('ltp')
        if ltp is not None:
            print(f"LTP of {symbol} ({exchange}): {ltp}")
            return ltp
        else:
            print(f"⚠️ LTP not found for {symbol}")
            return None
    except Exception as e:
        print(f"Error fetching LTP for {symbol}: {e}")
        return None

def _latest_atr_from_csv(symbol_upper: str, min_valid: int = 1) -> float | None:
    """
    Return the most recent *valid* ATR from:
      ohlcdata/{SYMBOL}_FUT_ohlc.csv or ohlcdata/{SYMBOL}_ohlc.csv
    Only returns a float if the last ATR is not NaN and we have at least
    `min_valid` non-NaN ATR rows; else returns None.
    """
    import os
    paths = [
        f"ohlcdata/{symbol_upper}_FUT_ohlc.csv",
        f"ohlcdata/{symbol_upper}_ohlc.csv",
    ]
    for p in paths:
        df = read_csv(p)
        if df is None or df.empty or "atr" not in df.columns:
            continue
        atr_series = df["atr"].dropna()
        if len(atr_series) >= min_valid:
            try:
                val = float(atr_series.iloc[-1])
                if pd.notna(val) and val > 0:
                    return val
            except Exception:
                pass
    return None


def _compute_startup_brick(exchange: str, symbol: str) -> float | None:
    """
    Returns the initial brick to store into symbols_to_trade on *write*.
    Rules:
      • NIFTY: 8
      • BANKNIFTY: 16
      • MCX (incl. Silver/NatGas): defer to ATR from OHLC (set later by maybe_set_initial_brick_from_atr)
      • Others: None (leave blank → runtime will fall back)
    """
    u = (symbol or "").upper()
    ex = (exchange or "").upper()
    # if u.startswith("NIFTY"):
    #     return 8.0
    # if u.startswith("BANKNIFTY"):
    #     return 16.0
    if ex == "MCX":
        # leave blank initially; will be filled from ATR by maybe_set_initial_brick_from_atr()
        return None
    return None



def init_bricks_on_startup():
    """
    1) Load symbols_to_trade
    2) Compute brick_size per the rules above
    3) Save back so stop-loss logic uses these bricks immediately
    """
    try:
        df = read_csv(symbols_file)
        if df is None or df.empty:
            print("ℹ️ init_bricks_on_startup: no symbols to initialize.")
            return

        df = df.copy()
        if "brick_size" not in df.columns:
            df["brick_size"] = None

        changed = False
        for i, r in df.iterrows():
            ex = str(r.get("exchange", "NSE_INDEX"))
            sy = str(r.get("symbol", "")).strip()
            if not sy:
                continue
            new_b = _compute_startup_brick(ex, sy)
            old_b = r.get("brick_size")
            if new_b is not None and (pd.isna(old_b) or str(old_b).strip() == "" or float(old_b) != float(new_b)):
                df.at[i, "brick_size"] = float(new_b)
                print(f"Updated brick_size → {sy} ({ex}) = {new_b}")
                changed = True

        if changed:
            save_to_csv(df, symbols_file)
        else:
            print("ℹ️ init_bricks_on_startup: no changes needed.")
    except Exception as e:
        print(f"⚠️ init_bricks_on_startup failed: {e}")
        
def maybe_set_initial_brick_from_atr(exchange: str, symbol: str):
    """
    For MCX and SENSEX (BSE_INDEX):
      • CRUDEOIL brick is set only once *after first valid ATR appears*.
      • Other MCX symbols and SENSEX options also set brick once from ATR.
    NIFTY/BANKNIFTY skip (they’re fixed).
    """
    try:
        ex = normalize_exchange_for_symbol(symbol, exchange)
        u  = (symbol or "").upper()
        allow_atr = (
            ex == "MCX"
            or u.startswith("SENSEX")
            or u.startswith("NIFTY")
            or u.startswith("BANKNIFTY")
        )
        
        # allow_atr = (ex == "MCX") or u.startswith("SENSEX")
        if not allow_atr:
            return
        sym_df = read_csv(symbols_file)
        if sym_df is None or sym_df.empty:
            return
        mask = sym_df["symbol"].astype(str).str.upper().eq(symbol.upper())
        if not mask.any():
            return

        # check if already set — only do this once
        matching_rows = sym_df.loc[mask, "brick_size"]
        if matching_rows.empty:
            return
        existing = matching_rows.iloc[0]
        if pd.notna(existing) and str(existing).strip() != "":
            return  # already initialized

        # read valid ATR
        atr_val = _latest_atr_from_csv(symbol.upper(), min_valid=5)
        if atr_val is None or pd.isna(atr_val) or atr_val <= 0:
            return  # wait for valid ATR
        sym_upper = symbol.upper()
        
        # ✅ Assign fixed minimum brick sizes for each MCX instrument
        if symbol.upper().startswith("CRUDEOIL"):
            brick = 6
            setattr(sys.modules[__name__], "_CRUDE_BRICK_INIT_", brick)
            print(f"🧱 [ONE-TIME INIT] CRUDEOIL brick_size = {brick} ")

        elif symbol.upper().startswith("GOLDM"):
            brick = 60
            setattr(sys.modules[__name__], "_GOLDM_BRICK_INIT_", brick)
            print(f"🧱 [ONE-TIME INIT] GOLDM brick_size = {brick}")

        elif symbol.upper().startswith("SILVERM"):
            brick = 110
            setattr(sys.modules[__name__], "_SILVERM_BRICK_INIT_", brick)
            print(f"🧱 [ONE-TIME INIT] SILVERM brick_size = {brick} ")

        elif symbol.upper().startswith("NATURALGAS"):
            brick = 0.6
            setattr(sys.modules[__name__], "_NATURALGAS_BRICK_INIT_", brick)
            print(f"🧱 [ONE-TIME INIT] NATURALGAS brick_size = {brick} ")

        elif u.startswith("SENSEX"):
            brick = 30
            setattr(sys.modules[__name__], "_SENSEX_BRICK_INIT_", brick)
            print(f"🧱 [ONE-TIME INIT] SENSEX brick_size = {brick} ")

        elif u.startswith("NIFTY"):
            brick = 8
            setattr(sys.modules[__name__], "_NIFTY_BRICK_INIT_", brick)
            print(f"🧱 [ONE-TIME INIT] NIFTY brick_size = {brick} ")

        elif u.startswith("BANKNIFTY"):
            brick = 16
            setattr(sys.modules[__name__], "_BANKNIFTY_BRICK_INIT_", brick)
            print(f"🧱 [ONE-TIME INIT] BANKNIFTY brick_size = {brick} ")

        else:
            brick = round(atr_val) if atr_val > 2 else atr_val
            print(f"🧱 [ONE-TIME INIT] {symbol} brick_size = {brick} (from ATR)")

        # save once to symbols_to_trade
        sym_df.loc[mask, "brick_size"] = float(brick)
        save_to_csv(sym_df, symbols_file)
    except Exception as e:
        print(f"⚠️ maybe_set_initial_brick_from_atr failed for {symbol}: {e}")


def main(db_path: str):
    if not os.path.exists(db_path):
        raise SystemExit(f"Database not found: {db_path}")

    # 1) Backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{db_path}.bak_{ts}"
    shutil.copy2(db_path, backup)
    print(f"Backup created: {backup}")

    # 2) Connect and update
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")  # safer writes

    try:
        with con:
            # sanity check: table exists
            con.execute("SELECT 1 FROM symtoken LIMIT 1;")

            for label, sql in UPDATES:
                cur = con.execute(sql)
                print(f"{label}: {cur.rowcount} row(s) updated")

        print("All updates committed successfully.")
    except Exception as e:
        print("Error occurred; rolling back this transaction.")
        raise
    finally:
        con.close()

def derive_openalgo_path_from_env(env_var="TRADE_DB_URL", fallback="sqlite:///db/tradebook.db"):
    """
    Translate a SQLAlchemy-style SQLite URL into the sibling 'openalgo.db' path.
    Handles:
      - sqlite:///relative/path/tradebook.db   -> relative
      - sqlite:////abs/path/tradebook.db       -> absolute
    """
    url = os.environ.get(env_var, fallback).strip()
    prefix_rel = "sqlite:///"
    prefix_abs = "sqlite:////"

    if url.startswith(prefix_abs):
        # absolute
        trade_path = url[len(prefix_abs):].split("?", 1)[0]
        if not trade_path.startswith("/"):
            trade_path = "/" + trade_path
    elif url.startswith(prefix_rel):
        # relative (to cwd)
        trade_path = url[len(prefix_rel):].split("?", 1)[0]
        # normalize any accidental leading slash for relative
        trade_path = trade_path.lstrip("/")
    else:
        # unknown or non-sqlite URL → default assumption
        trade_path = "db/tradebook.db"

    basedir = os.path.dirname(trade_path) or "."
    return os.path.join(basedir, "openalgo.db")

def run_updates(db_path: str):
    if not os.path.exists(db_path):
        raise SystemExit(f"Database not found: {db_path}")

    # backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{db_path}.bak_{ts}"
    os.makedirs(os.path.dirname(backup) or ".", exist_ok=True)
    shutil.copy2(db_path, backup)
    print(f"Backup created: {backup}")

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("PRAGMA journal_mode = WAL;")

    try:
        with con:
            # sanity check: table exists
            con.execute("SELECT 1 FROM symtoken LIMIT 1;")
            for label, sql in UPDATES:
                cur = con.execute(sql)
                print(f"{label}: {cur.rowcount} row(s) updated")
        print("All updates committed successfully.")
    finally:
        con.close()

def start_shared_ticker():
    global _ticker
    if _ticker is not None:
        return _ticker
    Controller.handleBrokerLogin(getBrokerAppConfig()['broker'])
    Instruments.fetchInstruments()
    _ticker = Ticker()
    _ticker.startTicker()
    # def _on_tick(tick):
    #     try:
    #         sym = getattr(tick, "tradingSymbol", None)
    #         px  = float(getattr(tick, "lastTradedPrice", 0) or 0)
    #         if sym and px > 0:
    #             ltp_dict[sym] = px
    #             ltp_event_queue.put(sym)
    #             last_ohlc_update[sym] = datetime.now()
    #     except Exception:
    #         pass
    # _ticker.registerListener(_on_tick)
    # return _ticker

def _on_tick(tick):
    sym = getattr(tick, "tradingSymbol", None)
    if not sym:
        return
    px = float(getattr(tick, "lastTradedPrice", 0.0) or 0)
    if px <= 0:
        return
    safe_ltp_update(sym, px)  # 🛡️ FEB 1 FIX: Thread-safe LTP update
    last_ohlc_update[sym] = datetime.now()   # ← add this line
    try:
        ltp_event_queue.put_nowait(sym)
    except queue.Full:
        pass
def start_shared_ticker_once():
    global _ticker
    if _ticker is not None:
        return
    Controller.handleBrokerLogin(getBrokerAppConfig()["broker"])
    Instruments.fetchInstruments()                   # ← add this
    _ticker = Ticker()
    _ticker.startTicker()
    _ticker.registerListener(_on_tick)               # ← always the same listener


# --- replace existing subscribe_symbol()/unsubscribe if present ---
def subscribe_symbol(symbol, exchange="NSE_INDEX"):
    t = start_shared_ticker()
    t.registerSymbols([symbol])
    safe_subscribe_add(symbol)  # 🛡️ FEB 1 FIX: Thread-safe add

def unsubscribe_symbol(symbol, exchange="NSE_INDEX"):
    if _ticker:
        _ticker.unregisterSymbols([symbol])
    safe_subscribe_remove(symbol)  # 🛡️ FEB 1 FIX: Thread-safe remove


  
def getOptionFromSymtokenByStep(driver_symbol, optionType):
    """
    Strike-step based CE/PE selection from symtoken
    with WEEKLY + MONTHLY expiry rollover safeguard.
    Supports NSE (NSE_INDEX) and BSE (BSE_INDEX) index options correctly.
    """

    import sqlite3, os, re, logging
    from datetime import datetime
    from trademgmt.TradeManager import TradeManager

    optionType = optionType.upper()

    # --------------------------------------------------
    # 1️⃣ Get underlying LTP
    # --------------------------------------------------
    ltp = get_ltp_generic(client, driver_symbol)
    if ltp is None or ltp <= 0:
        return None

    # --------------------------------------------------
    # 2️⃣ Extract root (BANKEX / BANKNIFTY / etc.)
    # --------------------------------------------------

    m = re.match(r'^([A-Z]+)', driver_symbol)
    if not m:
        return None
    root = m.group(1)

    # --------------------------------------------------
    # 3️⃣ Decide option exchange (CRITICAL FIX)
    # --------------------------------------------------
    def _get_option_exchange(root):
        # MCX options
        if root in ("CRUDEOIL", "GOLDM", "SILVERM", "NATURALGAS"):
            return "MCX"

        # BSE indices
        if root in ("SENSEX", "BANKEX"):
            return "BSE_INDEX"

        # NSE indices
        if root in ("NIFTY", "BANKNIFTY"):
            return "NSE_INDEX"
    opt_ex = _get_option_exchange(root)

    # --------------------------------------------------
    # 4️⃣ Strike step
    # --------------------------------------------------
    STRIKE_STEP = {
        "CRUDEOIL": 100,
        "GOLDM": 1000,
        "SILVERM": 1000,
        "NATURALGAS": 5,
        "SENSEX": 500,
        "BANKEX": 500,
        "NIFTY":100,
        "BANKNIFTY":100,
        
    }
    if root == "CRUDEOIL":
        step = 100
    elif root == "GOLDM":
        step = 1000
    elif root == "SILVERM":
        step = 1000
    elif root == "NATURALGAS":
        step = 5
    elif root == "SENSEX":
        step = 500
    elif root == "BANKEX":
        step = 500
    else:
        step = 100  # default fallback

    step = STRIKE_STEP.get(root, 100)
    # --------------------------------------------------
    # 5️⃣ ATM & one-step-away
    # --------------------------------------------------
    atm = round(ltp / step) * step

    if opt_ex == "MCX" and root in ("SILVERM", "GOLDM", "NATURALGAS"):
        strike = atm
    else:
        strike = atm - step if optionType == "CE" else atm + step
    # --------------------------------------------------
    # 6️⃣ Query symtoken (EXCHANGE AWARE ✅)
    # --------------------------------------------------
    db_path = os.path.join(os.getcwd(), "db", "openalgo.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute("""
        SELECT symbol, expiry
        FROM symtoken
        WHERE exchange = ?
          AND UPPER(symbol) LIKE ? || '%'
          AND instrumenttype = ?
          AND strike = ?
        ORDER BY expiry ASC
    """, (opt_ex, root, optionType, strike)).fetchall()

    con.close()

    if not rows:
        return None

    # --------------------------------------------------
    # 7️⃣ Filter expiry (skip today's expiry)
    # --------------------------------------------------
    today = datetime.now().date()
    valid = []

    for r in rows:
        raw_expiry = r["expiry"]

        if isinstance(raw_expiry, (int, float)):
            expiry_date = datetime.fromtimestamp(int(raw_expiry) / 1000).date()
        elif isinstance(raw_expiry, str):
            try:
                expiry_date = datetime.strptime(raw_expiry, "%d-%b-%y").date()
            except ValueError:
                logging.error("Invalid expiry format in symtoken: %s", raw_expiry)
                continue
        else:
            continue

        if expiry_date > today:
            valid.append((expiry_date, r["symbol"]))

    # --------------------------------------------------
    # 8️⃣ Pick nearest valid expiry
    # --------------------------------------------------
    symbol = None
    if valid:
        valid.sort(key=lambda x: x[0])
        symbol = valid[0][1]
    else:
        symbol = rows[0]["symbol"]

    # --------------------------------------------------
    # 9️⃣ HARD SAFETY CHECK (MANDATORY)
    # --------------------------------------------------
    if not symbol.startswith(root):
        raise RuntimeError(
            f"OPTION ROOT MISMATCH: driver={root}, option={symbol}"
        )

    return symbol


def _get_ltp_retry(exchange: str, symbol: str, retries: int = 2, delay: float = 0.06):
    for _ in range(retries):
        px = get_ltp(exchange, symbol)
        if px is not None:
            return float(px)
        time.sleep(delay)
    return None


###INDEX#############
def generate_index_symbol(symbols_file="ohlcdata/symbols_to_trade.csv", 
                         instruments=None):
    import pandas as pd
    import os

    os.makedirs(os.path.dirname(symbols_file), exist_ok=True)

    # FIXED brick sizes - Based on expert recommendations
    # Source: Prashant Shah (CMT, CFTe, MFTA) Definedge - "Renko Chart Analysis"
    # 🆕 DATA-DRIVEN OPTIMIZATION (Feb 1, 2026 Analysis)
    # Based on backtest of 10,000+ data points per instrument
    
    # Per-instrument Renko method (CLOSE vs HL)
    # INDEX: CLOSE Renko (filters noise, 80%+ win rate)
    # GOLD/SILVER: HL Renko (captures more moves, massive P&L)
    # MCX-ONLY RENKO METHODS (CLOSE for both)
    RENKO_METHOD_MAP = {
        "CRUDEOIL": "CLOSE",     # CLOSE Renko as specified
        "NATURALGAS": "CLOSE",   # CLOSE Renko as specified
    }
    
    # MCX-ONLY BRICK SIZES (User Specified)
    FIXED_BRICKS = {
        "CRUDEOIL": 5,         # MCX CRUDEOIL: Brick Size 5, CLOSE Renko, Lot 100
        "NATURALGAS": 0.8,     # MCX NATURALGAS: Brick Size 0.8, CLOSE Renko, Lot 1250
    }
    
    
    # Default to MCX instruments only
    if instruments is None:
        instruments = ["CRUDEOIL", "NATURALGAS"]
    
    rows = []

    for instr in instruments:
        # Check if valid symbol
        if instr not in FIXED_BRICKS:
            print(f"⚠️ Skipping unknown instrument: {instr}")
            continue
            
        # MCX symbols only
        if instr in ["CRUDEOIL", "NATURALGAS"]:
            # It's an MCX symbol
            try:
                # Handle MCX naming - get the actual future symbol
                fut = Utils.getNearestMCXFuture(instr)
                rows.append({
                    "exchange": "MCX",
                    "symbol": fut,
                    "brick_size": FIXED_BRICKS[instr]
                })
                
            except Exception as e:
                print(f"⚠️ Could not resolve MCX FUT for {instr}: {e}")

    sym_df = pd.DataFrame(rows)
    save_to_csv(sym_df, symbols_file)
    print(f"✅ symbols_to_trade.csv generated with {len(rows)} symbols.")
    print(sym_df)
    return sym_df
    
#%%        
if __name__ == "__main__":
    # ============================================
    # 🚀 STARTUP SEQUENCE
    # ============================================
    
    # Step 1: Validate configuration
    _logger.info("=" * 60)
    _logger.info("🚀 TRADING SYSTEM V5 - STARTING UP")
    _logger.info("=" * 60)
    
    is_valid, errors = validate_configuration()
    if not is_valid:
        _logger.error("❌ Configuration validation failed! Exiting...")
        for e in errors:
            _logger.error(f"   - {e}")
        sys.exit(1)
    
    # Step 2: Reset daily counters
    reset_daily_pnl()
    reset_exchange_day_complete()  # ⏰ Reset exchange day-complete flags for new trading day
    
    # V5 Startup Message
    print("\n" + "=" * 60)
    print("🚀 MCX TRADING SYSTEM - CRUDEOIL & NATURALGAS")
    print("=" * 60)
    print("")
    print("🔧 IMPROVEMENTS (based on Jan 29 analysis):")
    print("   ✅ Shorter trend lookback (8 vs 20 bricks)")
    print("   ✅ IMMEDIATE direction check (last 3 bricks)")
    print("   ✅ Counter-trend signal PENALTY (-15 pts)")
    print("   ✅ Signal deduplication (no 245x repeats)")
    print("   ✅ Higher threshold (70 vs 65) for better accuracy")
    print("   ✅ FEB 1 FIX: Retry limit + Rejected order handling")
    print("   ✅ FEB 1 FIX: Stale entry validation before execution")
    print("")
    print("🛡️  PRODUCTION FEATURES:")
    print("   ✅ Thread-safe P&L tracking")
    print("   ✅ Graceful shutdown (Ctrl+C)")
    print("   ✅ File logging (logs/ folder)")
    print("   ✅ API retry with backoff")
    print("   ✅ Health monitoring")
    print("")
    
    # ============================================
    # OUTCOME TRACKING SYSTEM INFO
    # ============================================
    if ENABLE_OUTCOME_TRACKING:
        print("📈 SIGNAL OUTCOME TRACKING ACTIVE!")
        print("=" * 50)
        print("   Tracks what WOULD have been profitable:")
        print("")
        print("   📊 FOR EACH SIGNAL (taken or blocked):")
        print(f"      • Entry price at signal time")
        print(f"      • Max Favorable Excursion (MFE)")
        print(f"      • Max Adverse Excursion (MAE)")
        print(f"      • Would hit target ({OUTCOME_TARGET_BRICKS} bricks)?")
        print(f"      • Would hit stop ({OUTCOME_STOP_BRICKS} bricks)?")
        print(f"      • Simulated P&L")
        print("")
        print("   🎯 THIS TELLS YOU:")
        print("      • Blocked signals that would have WON → Filter too strict")
        print("      • Taken signals that LOST → Filter not strict enough")
        print("      • Which filters are HELPING vs HURTING")
        print("")
        print(f"   📁 Reports saved to: {OUTCOME_LOG_DIR}/")
        print("=" * 50)
        print("")
    
    # ============================================
    # QUALITY SCORING SYSTEM INFO
    # ============================================
    if ENABLE_QUALITY_SCORING:
        print("🎯 QUALITY SCORING SYSTEM v2 ACTIVE!")
        print("=" * 50)
        print("   Scores each signal 0-100 based on:")
        print("")
        print("   📊 QUALITY COMPONENTS:")
        print(f"      • Trend Strength:   0-25 pts (consecutive bricks)")
        print(f"      • Trend Alignment:  0-30 pts (signal vs IMMEDIATE trend)")
        print(f"      • Momentum:         0-20 pts (last 5 bricks direction)")
        print(f"      • Breakout:         0-15 pts (breaking highs/lows)")
        print(f"      • Clean Market:     0-10 pts (low reversals)")
        print(f"      • Counter-trend:    -15 pts PENALTY if against trend")
        print("")
        print(f"   🎯 ENTRY RULES:")
        print(f"      • Score >= {QUALITY_SCORE_THRESHOLD}: ✅ HIGH QUALITY → TAKE TRADE")
        print(f"      • Score >= {QUALITY_SCORE_MEDIUM}: ⚠️ MEDIUM → Take if appropriate")
        print(f"      • Score < {QUALITY_SCORE_MEDIUM}:  ❌ LOW QUALITY → SKIP")
        print("")
        print("   🔑 KEY CHANGE: IMMEDIATE trend (last 3 bricks) determines")
        print("      whether signal is trend-aligned or counter-trend.")
        print("      CALL in uptrend = high score, PUT in uptrend = low score")
        print("=" * 50)
    else:
        print("📋 LEGACY MODE: Using cascading filters")
        print("")
        print("🎯 SOLUTION: Direction filter with HYBRID validation")
        print("")
        if DIRECTION_FILTER_MODE == "SMART":
            print("   SMART + HYBRID MODE (Current):")
            print("   - Check direction of LAST 2-3 bricks")
            print("   - 🧠 ALSO check NET direction agrees!")
            print("   - If last 2 UP but net DOWN → CHOPPY (BLOCKED)")
            print("   - If last 2 DOWN but net UP → CHOPPY (BLOCKED)")
            print("   - 🎯 Require minimum ±2 net bricks for entry!")
            print("   - Prevents weak signals and false breakouts!")
        else:
            print("   STRICT MODE (Current) - Slower, fewer trades:")
            print(f"   - Look at last {DIRECTION_LOOKBACK} bricks")
            print(f"   - If net move ≥ +{MIN_NET_BRICKS} bricks → CALL only")
            print(f"   - If net move ≤ -{MIN_NET_BRICKS} bricks → PUT only")
            print(f"   - If net move between → CHOPPY → wait")
    print("")
    print("-" * 60)
    print("⚙️  ACTIVE FILTERS:")
    print(f"   🎯 Quality Scoring:   {'✅ ON' if ENABLE_QUALITY_SCORING else '❌ OFF'} (threshold: {QUALITY_SCORE_THRESHOLD}/100)")
    print(f"   📈 Direction Filter:  {'✅ ON' if ENABLE_DIRECTION_FILTER else '❌ OFF'} (Mode: {DIRECTION_FILTER_MODE})")
    print(f"   🔄 Chop Detector:     {'✅ ON' if ENABLE_CHOP_DETECTOR else '❌ OFF'}")
    if ENABLE_CHOP_DETECTOR:
        print(f"      → Check last:      {CHOP_LOOKBACK} bricks")
        print(f"      → Max reversals:   {MAX_REVERSALS} (block if more)")
        print(f"      → Alternating:     Auto-block (↑↓↑↓ pattern)")
        print(f"      → Re-check:        At signal AND execution time")
    print(f"   🚨 Brick Rate Monitor: {'✅ ON' if ENABLE_BRICK_RATE_MONITOR else '❌ OFF'} (Redundant - disabled)")
    print(f"   🧠 Direction Stability: {'✅ ON' if ENABLE_DIRECTION_STABILITY else '❌ OFF'}")
    if ENABLE_DIRECTION_STABILITY:
        print(f"      → Minimum stable:  {DIRECTION_STABLE_MINUTES} mins (blocks flip-flop entries)")
    print(f"   💰 Daily Loss Limit:  {'✅ ON' if ENABLE_DAILY_LOSS_LIMIT else '❌ OFF'} ({MAX_DAILY_LOSS_POINTS} pts)")
    print(f"   ⏰ Time Filter:       {'✅ ON' if ENABLE_TIME_FILTER else '❌ OFF'}")
    if ENABLE_TIME_FILTER:
        print(f"      → Trading hours:   {TRADING_START_TIME} - {TRADING_END_TIME}")
        print(f"      → Blocked periods: {TIME_FILTER_BLOCKED_PERIODS}")
    print(f"   🚫 New Trade Cutoff:  (Legacy: {NEW_TRADE_CUTOFF_HOUR}:00 - Now per-exchange)")
    print(f"   ⏰ Trading Window (MCX ONLY):")
    print(f"      MCX: {TRADING_START_TIMES['MCX'][0]:02d}:{TRADING_START_TIMES['MCX'][1]:02d} - {TRADING_END_TIMES['MCX'][0]:02d}:{TRADING_END_TIMES['MCX'][1]:02d} → Square-off {SQUARED_OFF_TIMES['MCX'][0]:02d}:{SQUARED_OFF_TIMES['MCX'][1]:02d}")
    print(f"   🎯 Trailing Stop:     {'✅ ON' if ENABLE_TRAILING_STOP else '❌ OFF'}")
    if ENABLE_TRAILING_STOP:
        print(f"      → Breakeven after: {TRAIL_BREAKEVEN_AFTER} brick profit")
        print(f"      → Trail after:     {TRAIL_START_AFTER} brick profit")
        print(f"      → Smart Tiered:    {'✅ ON' if ENABLE_SMART_TRAILING else '❌ OFF (fixed: ' + str(TRAIL_DISTANCE) + ' bricks)'}")
        if ENABLE_SMART_TRAILING:
            for min_profit, trail_dist in SMART_TRAIL_TIERS:
                print(f"         • {min_profit}+ bricks profit → trail {trail_dist} bricks behind")
    print(f"   📍 Entry Offset:      {ENTRY_OFFSET_BRICKS} bricks (0 = enter at signal price)")
    print(f"   🛑 Stop Loss:         {STOP_LOSS_BRICKS} bricks")
    print(f"   🎯 Min Net for Entry: ±{MIN_NET_BRICKS} bricks (blocks weak signals)")
    print(f"   🚫 Signal Proximity:  {'✅ ON' if ENABLE_SIGNAL_PROXIMITY_FILTER else '❌ OFF'}")
    print(f"   🔄 Exit on Opposite:  {'✅ ON' if ENABLE_EXIT_ON_OPPOSITE_SIGNAL else '❌ OFF'}")
    print(f"   ⏰ Re-entry Cooldown: {'✅ ON' if ENABLE_REENTRY_COOLDOWN else '❌ OFF'}")
    print(f"   📊 Filter Logging:    {'✅ ON' if ENABLE_FILTER_LOGGING else '❌ OFF'}")
    print("")
    print("="*60)
    print("🏆 MCX-ONLY OPTIMIZED (CRUDEOIL & NATURALGAS)")
    print("="*60)
    print()
    print("📊 MCX INSTRUMENTS CONFIGURATION:")
    print("   ┌─────────────┬──────────┬────────────┬─────────┬─────────┐")
    print("   │ Instrument  │ Exchange │ Brick Size │ Renko   │ Lot     │")
    print("   ├─────────────┼──────────┼────────────┼─────────┼─────────┤")
    print("   │ CRUDEOIL    │ MCX      │ 5          │ CLOSE   │ 100     │")
    print("   │ NATURALGAS  │ MCX      │ 0.8        │ CLOSE   │ 1250    │")
    print("   └─────────────┴──────────┴────────────┴─────────┴─────────┘")
    print()
    print("🎯 COMMON SETTINGS:")
    print(f"   → Stop Loss: {STOP_LOSS_BRICKS} bricks")
    print(f"   → Trail Start: {TRAIL_START_AFTER} bricks")
    print(f"   → Trail Distance: {TRAIL_DISTANCE} bricks")
    print()
    print("✅ ENABLED:")
    print("   → Time Filter: Block 13:00-14:00 (INDEX only)")
    print("   → Trailing Stop: Wide stop + tight trail")
    print("   → Per-instrument Renko method")
    print()
    print("❌ DISABLED (hurt P&L):")
    print("   → Quality Scoring, Direction Filter, Chop Filter")
    print("")
    print("=" * 60 + "\n")
    
    clean_ohlc_files()
   
    # ============================================
    # 🎯 MCX-ONLY INSTRUMENTS
    # ============================================
    # CRUDEOIL: Brick Size 5, CLOSE Renko, Lot 100
    # NATURALGAS: Brick Size 0.8, CLOSE Renko, Lot 1250
    
    generate_index_symbol(instruments=["CRUDEOIL", "NATURALGAS"])
    
    print("🔧 Initial symbols_to_trade.csv created.")     


    Instruments.fetchInstruments()
    
    trade_manager = read_csv(trade_manager_file)
    if trade_manager is None or trade_manager.empty:
        trade_manager = pd.DataFrame(columns=[
            "exchange", "timestamp", "renko_signal", "symbol", "entry_price", "limit_entry_price", "exec_price", "quantity", "order_status", "orderid", "close_reason"
        ])
        save_to_csv(trade_manager, trade_manager_file)
    else:
        print(f"💾 Loaded existing trade_manager table for {trade_manager_file}")
    
    # ============================================
    # 🛡️ STARTUP POSITION SAFETY CHECK
    # ============================================
    print("\n" + "=" * 60)
    print("🔍 STARTUP POSITION CHECK")
    print("=" * 60)
    
    try:
        broker_positions = get_positions_book(client)
        if broker_positions is not None and not broker_positions.empty:
            # Filter for non-zero positions
            open_positions = broker_positions[broker_positions['quantity'].astype(int) != 0]
            
            if not open_positions.empty:
                print(f"⚠️  FOUND {len(open_positions)} OPEN POSITION(S) AT BROKER:")
                print("-" * 60)
                
                for _, pos in open_positions.iterrows():
                    pos_symbol = pos.get('symbol', 'UNKNOWN')
                    pos_exchange = pos.get('exchange', 'UNKNOWN')
                    pos_qty = pos.get('quantity', 0)
                    pos_avg = pos.get('average_price', 0)
                    pos_ltp = pos.get('ltp', 0)
                    pos_pnl = pos.get('pnl', 0)
                    
                    print(f"   📊 {pos_symbol}")
                    print(f"      Exchange: {pos_exchange} | Qty: {pos_qty}")
                    print(f"      Avg: {pos_avg} | LTP: {pos_ltp} | P&L: {pos_pnl}")
                    
                    # Check if tracked in trade_manager
                    is_tracked = False
                    if not trade_manager.empty:
                        # Check for INPOSITION entries matching this symbol
                        tracked = trade_manager[
                            (trade_manager['order_status'] == 'INPOSITION')
                        ]
                        for _, t in tracked.iterrows():
                            if pos_symbol.upper().startswith(str(t.get('symbol', '')).upper().split('FUT')[0]):
                                is_tracked = True
                                break
                    
                    if is_tracked:
                        print(f"      ✅ TRACKED in trade_manager")
                    else:
                        print(f"      ⚠️  NOT TRACKED - Could be orphan position!")
                
                print("-" * 60)
                print("⚠️  WARNING: Untracked positions may cause unexpected P&L!")
                print("   Consider manually closing them before starting.")
                print("")
                
                # Ask user to confirm
                user_input = input("❓ Continue with these positions? (y/n): ").strip().lower()
                if user_input != 'y':
                    print("❌ Aborting startup. Please close positions manually.")
                    sys.exit(1)
                print("✅ Continuing with existing positions...")
            else:
                print("✅ No open positions at broker - Clean start!")
        else:
            print("✅ No positions data from broker - Clean start!")
    except Exception as e:
        print(f"⚠️ Could not check broker positions: {e}")
        print("   Continuing anyway...")
    
    print("=" * 60 + "\n")
    # ============================================
    # END STARTUP POSITION CHECK
    # ============================================
    
    # # 👇 NEW: backfill for any manual positions present at start
    # reconcile_manual_positions(client)
   
    # Start square-off watcher
    threading.Thread(target=square_off_guard, args=(client,), daemon=True).start()
    
    # # # Trade addition Entry
    # add_trade(
    #      exchange="MCX",
    #      timestamp=pd.Timestamp.now(),
    #      renko_signal="BUYEN",
    #     symbol="CRUDEOIL16OCT255300CE",   # e.g., NIFTY09SEP2524700CE
    #     entry_price=132,               # <-- your manual trigger price
    #     exec_price=132,
    #     quantity=100,                     # optional; see note below
    #     order_status="INPOSITION"
    # )
    
    # ##ADD stop loss automatically    
    # trade_manager, _ = ensure_initial_stop(trade_manager)

    # Start with WebSocket active
    websocket_active.set()
    symbols_to_subscribe_df  = fetch_symbols_to_subscribe(symbols_file)
    # After client init, before threads or loops
    init_bricks_on_startup()
    # Launch threads
    threading.Thread(target=websocket_thread_func, args=(client, symbols_to_subscribe_df), daemon=True).start()
    threading.Thread(target=fallback_thread_func, args=(symbols_to_subscribe_df,), daemon=True).start()
        
    
    update_option_thread = threading.Thread(target=update_index_ohlc_data, daemon=True)
    update_option_thread.start()

    # threading.Thread(target=schedule_nifty_30m_update, daemon=True).start()
    #monitor ohlc
    threading.Thread(target=ohlc_update_monitor, daemon=True).start()
    
    # update_fut_thread = threading.Thread(target=update_fut_ohlc_data, daemon=True)
    # update_fut_thread.start()
    

    
    order_thread = threading.Thread(target=order_management, args=(trade_manager, client, ltp_dict), daemon=True)
    order_thread.start()
    order_thread.join()


    
    # update_fut_thread.join()


