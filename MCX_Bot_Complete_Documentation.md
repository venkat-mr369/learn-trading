# MCX Trading Bot - Complete Technical Documentation
## MCX_Bot_Complete1.py - In-Depth Analysis

---

# 📋 TABLE OF CONTENTS

1. [Overview](#1-overview)
2. [Instrument Configuration](#2-instrument-configuration)
3. [Database Architecture](#3-database-architecture)
4. [Core Trading Logic Flow](#4-core-trading-logic-flow)
5. [Renko Chart Calculation](#5-renko-chart-calculation)
6. [Signal Generation System](#6-signal-generation-system)
7. [Order Management System](#7-order-management-system)
8. [Risk Management Features](#8-risk-management-features)
9. [WebSocket & Data Feed](#9-websocket--data-feed)
10. [All Functions Reference](#10-all-functions-reference)
11. [Configuration Parameters](#11-configuration-parameters)
12. [Thread Architecture](#12-thread-architecture)

---

# 1. OVERVIEW

## 1.1 Purpose
MCX Trading Bot is an automated trading system specifically designed for MCX (Multi Commodity Exchange) commodities:
- **CRUDEOIL** - Crude Oil Futures
- **NATURALGAS** - Natural Gas Futures

## 1.2 Trading Strategy
- **Renko-based** trading using CLOSE price method
- **LONG ONLY** mode (no short selling)
- **Options trading** - Buys CE (Call) or PE (Put) options based on signals
- Uses **trailing stops** for profit protection

## 1.3 Trading Hours
```
MCX Trading Hours:
├── Start: 09:05 IST
├── End: 23:00 IST (no new trades)
└── Square-off: 23:15 IST (close all positions)
```

---

# 2. INSTRUMENT CONFIGURATION

## 2.1 CRUDEOIL Configuration
```python
"CRUDEOIL": {
    "exchange": "MCX",
    "brick_size": 5,          # 5 points per Renko brick
    "renko_method": "CLOSE",  # Uses close price only
    "lot_size": 100           # 100 units per lot
}
```

### CRUDEOIL Trading Logic:
1. **Brick Formation**: A new brick forms when price moves ±5 points
2. **BUY Signal (BUYEN)**: When price rises 2 bricks (10 points) from last low
3. **SELL Signal (SELEX)**: When price falls 2 bricks (10 points) from last high
4. **Stop Loss**: 4 bricks = 20 points below entry
5. **Trailing Stop**: Starts at 1.5 bricks profit, trails 0.5 bricks behind

## 2.2 NATURALGAS Configuration
```python
"NATURALGAS": {
    "exchange": "MCX",
    "brick_size": 0.8,        # 0.8 points per Renko brick
    "renko_method": "CLOSE",  # Uses close price only
    "lot_size": 1250          # 1250 units per lot
}
```

### NATURALGAS Trading Logic:
1. **Brick Formation**: A new brick forms when price moves ±0.8 points
2. **BUY Signal (BUYEN)**: When price rises 2 bricks (1.6 points) from last low
3. **SELL Signal (SELEX)**: When price falls 2 bricks (1.6 points) from last high
4. **Stop Loss**: 4 bricks = 3.2 points below entry
5. **Trailing Stop**: Starts at 1.5 bricks profit, trails 0.5 bricks behind

## 2.3 Helper Functions
```python
def get_mcx_brick_size(symbol: str) -> float:
    """Returns brick size: CRUDEOIL=5, NATURALGAS=0.8"""

def get_mcx_lot_size(symbol: str) -> int:
    """Returns lot size: CRUDEOIL=100, NATURALGAS=1250"""

def get_mcx_renko_method(symbol: str) -> str:
    """Returns 'CLOSE' for both instruments"""
```

---

# 3. DATABASE ARCHITECTURE

## 3.1 Databases Used

### Database 1: `tradebook_mcx.db` (Primary Trading Database)
```
Location: db/tradebook_mcx.db
URL: sqlite:///db/tradebook_mcx.db
Engine: SQLAlchemy with NullPool
```

**Tables in tradebook_mcx.db:**

| Table Name | Purpose | Key Columns |
|------------|---------|-------------|
| `trade_manager` | Active trades & orders | symbol, entry_price, order_status |
| `symbols_to_trade` | Instruments to monitor | exchange, symbol, brick_size |
| `ohlc_<symbol>` | OHLC candle data | timestamp, open, high, low, close |
| `renko_<symbol>` | Renko brick data | timestamp, Renko_Brick, Signal |
| `ohlc_fut_<symbol>` | Futures OHLC data | Same as ohlc |
| `renko_fut_<symbol>` | Futures Renko data | Same as renko |

### Database 2: `openalgo.db` (Symbol/Token Database)
```
Location: db/openalgo.db
URL: sqlite:///db/openalgo.db
Purpose: Symbol lookup, lot sizes, option chain
```

## 3.2 Table Schemas

### trade_manager Table
```sql
CREATE TABLE trade_manager (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT,              -- 'MCX'
    timestamp TEXT,             -- '2026-02-05 14:30:00'
    renko_signal TEXT,          -- 'BUYCL', 'BUYPT', 'SELST', 'SELSP'
    symbol TEXT,                -- 'CRUDEOIL19FEB26FUT'
    entry_price REAL,           -- Signal trigger price
    limit_entry_price REAL,     -- Limit order price (if any)
    exec_price REAL,            -- Actual execution price
    quantity REAL,              -- Number of lots
    order_status TEXT,          -- 'OPEN', 'INPOSITION', 'CLOSED', 'CANCELLED'
    orderid TEXT,               -- Broker order ID
    close_reason TEXT           -- 'STOPLOSS', 'SIGNAL_EXIT', 'SQUARE_OFF'
);
```

### symbols_to_trade Table
```sql
CREATE TABLE symbols_to_trade (
    exchange TEXT,      -- 'MCX'
    symbol TEXT,        -- 'CRUDEOIL' or 'NATURALGAS'
    brick_size REAL     -- 5.0 or 0.8
);
```

### renko_<symbol> Table (Dynamic)
```sql
CREATE TABLE renko_crudeoil (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    Renko_Brick REAL,   -- Renko close price
    zlema9 REAL,        -- 9-period ZLEMA indicator
    HH REAL,            -- Higher High swing
    HL REAL,            -- Higher Low swing
    LL REAL,            -- Lower Low swing
    LH REAL,            -- Lower High swing
    Signal TEXT,        -- 'BUYEN', 'SELEX', NULL
    Last_High REAL,
    Last_Low REAL
);
```

### ohlc_<symbol> Table (Dynamic)
```sql
CREATE TABLE ohlc_crudeoil (
    timestamp TEXT PRIMARY KEY,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    atr REAL            -- Average True Range
);
```

## 3.3 Database Functions

### Core Database Functions
```python
# Save DataFrame to SQLite
def save_to_csv(df: pd.DataFrame, file_name: str):
    """
    Saves DataFrame to SQLite table.
    Maps filename to table:
    - 'trade_manager.csv' → trade_manager table
    - 'symbols_to_trade.csv' → symbols_to_trade table  
    - '<symbol>_renko.csv' → renko_<symbol> table
    - '<symbol>_ohlc.csv' → ohlc_<symbol> table
    """

# Read from SQLite to DataFrame
def read_csv(file_name: str) -> pd.DataFrame:
    """
    Reads from SQLite table.
    Returns DataFrame with data from mapped table.
    """

# Append to trade_manager
def trade_manager_append(rows):
    """Append rows without deleting existing data"""

# Update trade by orderid
def trade_manager_update(orderid: str, **fields):
    """Update specific fields for an order"""
```

### Table Resolution Logic
```python
def _resolve_table(file_name: str):
    """
    Maps filename → SQLite table name
    
    Mappings:
    - 'symbols_to_trade.csv' → 'symbols_to_trade'
    - 'trade_manager.csv' → 'trade_manager'
    - 'CRUDEOIL_ohlc.csv' → 'ohlc_crudeoil'
    - 'CRUDEOIL_renko.csv' → 'renko_crudeoil'
    - 'CRUDEOIL_FUT_ohlc.csv' → 'ohlc_fut_crudeoil'
    - 'CRUDEOIL_FUT_renko.csv' → 'renko_fut_crudeoil'
    """
```

## 3.4 SQLite Configuration
```python
_engine = create_engine(
    "sqlite:///db/tradebook_mcx.db",
    future=True,
    poolclass=NullPool,  # No connection pooling
    pool_pre_ping=True,
    connect_args={"check_same_thread": False}
)

@event.listens_for(_engine, "connect")
def _sqlite_pragmas(dbapi_conn, connection_record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")      # Better concurrency
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA busy_timeout=5000;")     # Wait for locks
    cur.close()
```

---

# 4. CORE TRADING LOGIC FLOW

## 4.1 Complete Flow Diagram
```
┌─────────────────────────────────────────────────────────────────┐
│                     STARTUP SEQUENCE                             │
├─────────────────────────────────────────────────────────────────┤
│ 1. Initialize logging                                            │
│ 2. Load configuration                                            │
│ 3. Connect to OpenAlgo API                                       │
│ 4. Wait until 09:05 IST (smart_startup_sleep)                   │
│ 5. Generate symbols_to_trade (CRUDEOIL, NATURALGAS)             │
│ 6. Fetch instruments from broker                                 │
│ 7. Check for existing positions                                  │
│ 8. Start all threads                                             │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     THREAD ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────┤
│ Thread 1: websocket_thread_func    → Live price feed            │
│ Thread 2: fallback_thread_func     → REST API backup            │
│ Thread 3: update_index_ohlc_data   → OHLC/Renko calculation     │
│ Thread 4: ohlc_update_monitor      → Data freshness check       │
│ Thread 5: square_off_guard         → End-of-day square-off      │
│ Thread 6: order_management         → Order execution (MAIN)     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     DATA FLOW                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  [WebSocket] ──→ [LTP Dict] ──→ [OHLC Calculation]              │
│       │                              │                           │
│       │                              ↓                           │
│       │                    [Renko Brick Calculation]             │
│       │                              │                           │
│       │                              ↓                           │
│       │                    [Signal Generation]                   │
│       │                     BUYEN / SELEX                        │
│       │                              │                           │
│       │                              ↓                           │
│       │              [update_trade_manager_with_new_signals]     │
│       │                              │                           │
│       │                              ↓                           │
│       └──────────────→ [order_management]                        │
│                              │                                   │
│                              ↓                                   │
│                    [Execute Order via OpenAlgo API]              │
│                              │                                   │
│                              ↓                                   │
│                    [Update trade_manager]                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## 4.2 Signal Flow Detail
```
STEP 1: Price Update
├── WebSocket receives tick
├── Updates ltp_dict[symbol] = price
└── Triggers ltp_event_queue

STEP 2: OHLC Update (every minute)
├── Fetch 1-minute candles from broker
├── Calculate ATR indicator
├── Save to ohlc_<symbol> table
└── Trigger Renko calculation

STEP 3: Renko Calculation
├── Load OHLC data
├── Apply calculate_renko_close_traditional()
├── Generate Renko bricks
├── Calculate ZLEMA-9 on Renko
├── Identify swing points (HH, HL, LL, LH)
└── Save to renko_<symbol> table

STEP 4: Signal Generation
├── Apply generate_signals() to Renko data
├── Check for BUYEN signal (2-brick rise from low)
├── Check for SELEX signal (2-brick fall from high)
└── Update Signal column in Renko table

STEP 5: Trade Manager Update
├── Call update_trade_manager_with_new_signals()
├── For BUYEN → Create BUYCL entry with SELST stop
├── For SELEX → Create BUYPT entry with SELSP stop
└── Save to trade_manager table

STEP 6: Order Execution
├── order_management() reads OPEN orders
├── Check if LTP hits entry price
├── Get option symbol (CE/PE) from openalgo.db
├── Place order via OpenAlgo API
├── Update order_status to INPOSITION
└── Create stop loss order
```

---

# 5. RENKO CHART CALCULATION

## 5.1 CLOSE-Based Traditional Renko

The bot uses **CLOSE-based Traditional Renko** calculation, which:
- Uses only the **Close price** (ignores High/Low)
- Requires **1 brick** for continuation
- Requires **2 bricks** for reversal

```python
def calculate_renko_close_traditional(df: pd.DataFrame, brick_size: float):
    """
    CLOSE-based Traditional Renko (TradingView-like)
    
    Core Rules:
    1. ONLY Close price is used (High/Low ignored)
    2. Continuation: 1 brick movement
    3. Reversal: 2 bricks movement
    4. Multiple bricks can form at same timestamp
    """
```

## 5.2 Renko Calculation Logic

### For CRUDEOIL (brick_size = 5):
```
Initial Close: 5720
Last Renko Close = floor(5720/5) * 5 = 5720

Price Movement Examples:
├── 5720 → 5725 (+5): UP brick forms (close=5725)
├── 5725 → 5730 (+5): UP brick forms (close=5730)
├── 5730 → 5720 (-10): Needs -2 bricks for reversal
│   └── 5730 → 5720: 2 DOWN bricks form (5725, 5720)
└── Reversal needs: 5730 - (2 * 5) = 5720 or below
```

### For NATURALGAS (brick_size = 0.8):
```
Initial Close: 310.5
Last Renko Close = floor(310.5/0.8) * 0.8 = 310.4

Price Movement Examples:
├── 310.4 → 311.2 (+0.8): UP brick forms (close=311.2)
├── 311.2 → 312.0 (+0.8): UP brick forms (close=312.0)
├── 312.0 → 310.4 (-1.6): Needs -2 bricks for reversal
│   └── 312.0 → 310.4: 2 DOWN bricks form (311.2, 310.4)
└── Reversal needs: 312.0 - (2 * 0.8) = 310.4 or below
```

## 5.3 Renko DataFrame Structure
```python
renko_df columns:
├── timestamp      # When brick formed
├── open           # Brick open price
├── high           # max(open, close)
├── low            # min(open, close)
├── close          # Brick close price (Renko_Brick)
├── direction      # 1 (up) or -1 (down)
├── Renko_Brick    # Same as close
├── zlema9         # 9-period Zero-Lag EMA
├── HH             # Higher High swing point
├── HL             # Higher Low swing point
├── LL             # Lower Low swing point
├── LH             # Lower High swing point
├── Signal         # 'BUYEN', 'SELEX', or None
├── Last_High      # Tracking variable
└── Last_Low       # Tracking variable
```

---

# 6. SIGNAL GENERATION SYSTEM

## 6.1 Signal Types

| Signal | Meaning | Action | Option Type |
|--------|---------|--------|-------------|
| BUYEN | BUY Entry | Enter long position | Buy CE option |
| SELEX | SELL Exit | Exit long position | - |
| BUYCL | BUY CALL | Order to buy CE | CE option |
| BUYPT | BUY PUT | Order to buy PE | PE option |
| SELST | SELL Stop (CE) | Stop loss for CE | Sell CE |
| SELSP | SELL Stop (PE) | Stop loss for PE | Sell PE |

## 6.2 Signal Generation Logic

```python
def generate_signals(renko_df, brick_size):
    """
    Generate signals based on Renko bricks.
    
    BUYEN (Buy Entry):
    - Condition: price rises 2 bricks from last_low
    - Formula: brick >= last_low + (2 * brick_size)
    - Creates: BUYCL order (Buy Call option)
    
    SELEX (Sell Exit):
    - Condition: price falls 2 bricks from last_high
    - Formula: brick <= last_high - (2 * brick_size)
    - Creates: BUYPT order in LONG_ONLY mode
    """
```

### Signal Flow for CRUDEOIL:
```
Last Low = 5700
Current Brick = 5710 (5700 + 2*5 = 5710)
→ BUYEN Signal Generated!
→ Creates BUYCL order at 5710
→ Creates SELST (stop loss) at 5690 (5710 - 4*5)

Position Entered at 5710
Last High = 5730
Current Brick = 5720 (5730 - 2*5 = 5720)
→ SELEX Signal Generated!
→ Exit position
```

### Signal Flow for NATURALGAS:
```
Last Low = 309.6
Current Brick = 311.2 (309.6 + 2*0.8 = 311.2)
→ BUYEN Signal Generated!
→ Creates BUYCL order at 311.2
→ Creates SELST (stop loss) at 308.0 (311.2 - 4*0.8)

Position Entered at 311.2
Last High = 313.6
Current Brick = 312.0 (313.6 - 2*0.8 = 312.0)
→ SELEX Signal Generated!
→ Exit position
```

## 6.3 Trade Manager Signal Mapping

```python
def update_trade_manager_with_new_signals():
    """
    Maps Renko signals to trade_manager entries.
    
    For BUYEN signal:
    1. Create BUYCL entry (status='OPEN')
    2. Create SELST stop loss (status='OPEN')
    
    For SELEX signal:
    1. Create BUYPT entry (status='OPEN') - In LONG_ONLY mode
    """
```

---

# 7. ORDER MANAGEMENT SYSTEM

## 7.1 Order States

```
Order Status Flow:
┌────────┐    ┌───────────┐    ┌────────┐
│  OPEN  │ ──→│ INPOSITION│ ──→│ CLOSED │
└────────┘    └───────────┘    └────────┘
     │                              ↑
     │         ┌───────────┐       │
     └────────→│ CANCELLED │───────┘
               └───────────┘
```

## 7.2 Order Execution Logic

```python
def order_management(trade_manager, client, ltp_dict):
    """
    Main order execution loop.
    
    For each OPEN order:
    1. Get current LTP
    2. Check if execution condition met
    3. Get option symbol (CE/PE)
    4. Place order via broker API
    5. Update trade_manager status
    """
```

### Execution Conditions:

| Signal | Condition to Execute | New Status |
|--------|---------------------|------------|
| BUYCL | LTP <= entry_price | INPOSITION |
| BUYPT | LTP >= entry_price | INPOSITION |
| SELST | LTP <= stop_price | CLOSED |
| SELSP | LTP >= stop_price | CLOSED |

## 7.3 Option Symbol Selection

```python
def get_current_option_symbols(client, driver_symbol, exchange):
    """
    Get CE and PE option symbols for futures.
    
    For CRUDEOIL at LTP 5700:
    - ATM Strike = round(5700 / 100) * 100 = 5700
    - CE Strike = 5700 - 100 = 5600 (ITM)
    - PE Strike = 5700 + 100 = 5800 (ITM)
    
    For NATURALGAS at LTP 310:
    - ATM Strike = round(310 / 5) * 5 = 310
    - CE Strike = 310 - 5 = 305 (ITM)
    - PE Strike = 310 + 5 = 315 (ITM)
    """
```

## 7.4 Stop Loss Creation

```python
def create_stop_loss(trade, symbol, action, exec_price, brick_size, trade_manager, index, direction):
    """
    Create stop loss order.
    
    For BUYCL (CE position):
    - Stop Signal: SELST
    - Stop Price: entry_price - (STOP_LOSS_BRICKS * brick_size)
    - CRUDEOIL: entry - (4 * 5) = entry - 20
    - NATURALGAS: entry - (4 * 0.8) = entry - 3.2
    
    For BUYPT (PE position):
    - Stop Signal: SELSP
    - Stop Price: entry_price + (STOP_LOSS_BRICKS * brick_size)
    """
```

---

# 8. RISK MANAGEMENT FEATURES

## 8.1 Stop Loss (4 Bricks)
```python
STOP_LOSS_BRICKS = 4.0

# CRUDEOIL: 4 * 5 = 20 points stop
# NATURALGAS: 4 * 0.8 = 3.2 points stop
```

## 8.2 Trailing Stop
```python
ENABLE_TRAILING_STOP = True
TRAIL_BREAKEVEN_AFTER = 1.5   # Move to breakeven at 1.5 bricks profit
TRAIL_START_AFTER = 1.5       # Start trailing at 1.5 bricks
TRAIL_DISTANCE = 0.5          # Trail 0.5 bricks behind

# CRUDEOIL:
# - Breakeven after: 1.5 * 5 = 7.5 points profit
# - Trail distance: 0.5 * 5 = 2.5 points behind

# NATURALGAS:
# - Breakeven after: 1.5 * 0.8 = 1.2 points profit
# - Trail distance: 0.5 * 0.8 = 0.4 points behind
```

## 8.3 Daily Loss Limit
```python
ENABLE_DAILY_LOSS_LIMIT = True
MAX_DAILY_LOSS_POINTS = 500

def check_daily_loss_limit():
    """Stop trading if daily loss exceeds 500 points"""
```

## 8.4 Re-entry Cooldown
```python
ENABLE_REENTRY_COOLDOWN = True
REENTRY_COOLDOWN_MINUTES = 5

def check_reentry_cooldown(symbol, signal_type, price_level):
    """Wait 5 minutes after stop loss before re-entering"""
```

## 8.5 Square-Off Guard
```python
SQUARED_OFF_TIMES = {"MCX": (23, 15)}  # 11:15 PM

def square_off_guard(client, poll_seconds=60):
    """
    At 23:15 IST:
    1. Close all open positions
    2. Cancel all pending orders
    3. Disable new trading
    """
```

## 8.6 Stale Signal Detection
```python
def is_signal_stale(symbol, exchange, entry_price):
    """
    Cancel signal if price moved too far.
    
    For BUYCL: Stale if LTP < entry - (2 * brick_size)
    For BUYPT: Stale if LTP > entry + (2 * brick_size)
    """
```

---

# 9. WEBSOCKET & DATA FEED

## 9.1 Data Sources

### Primary: WebSocket
```python
def websocket_thread_func(client, symbols_to_subscribe_df):
    """
    Subscribe to live price feed via WebSocket.
    Updates ltp_dict on every tick.
    """
```

### Backup: REST API
```python
def fallback_thread_func(symbols_to_subscribe_df, poll_interval=5):
    """
    Poll REST API every 5 seconds as backup.
    Used when WebSocket fails or data is stale.
    """
```

## 9.2 LTP Management
```python
ltp_dict = {}  # {symbol: price}
ltp_timestamp_dict = {}  # {symbol: timestamp}

def safe_ltp_update(symbol: str, ltp: float, timestamp: float = None):
    """Thread-safe LTP update"""

def safe_ltp_get(symbol: str) -> tuple:
    """Thread-safe LTP retrieval: returns (ltp, timestamp)"""
```

## 9.3 Stale LTP Detection
```python
LTP_STALE_THRESHOLD = 10.0  # seconds

def get_ltp_stale_threshold(symbol: str) -> float:
    """
    MCX instruments: 30 seconds (markets less liquid)
    Index instruments: 10 seconds
    """
```

---

# 10. ALL FUNCTIONS REFERENCE

## 10.1 MCX Configuration Functions (Lines 96-122)
| Function | Purpose |
|----------|---------|
| `get_mcx_brick_size(symbol)` | Returns brick size for symbol |
| `get_mcx_lot_size(symbol)` | Returns lot size for symbol |
| `get_mcx_renko_method(symbol)` | Returns 'CLOSE' |

## 10.2 Logging & Shutdown (Lines 127-721)
| Function | Purpose |
|----------|---------|
| `setup_logging()` | Configure file and console logging |
| `graceful_shutdown()` | Handle Ctrl+C, save state |
| `is_shutdown_requested()` | Check if shutdown in progress |

## 10.3 Signal Tracking (Lines 248-665)
| Function | Purpose |
|----------|---------|
| `generate_signal_id()` | Create unique signal ID |
| `track_signal_for_outcome()` | Track signal for analysis |
| `is_duplicate_signal()` | Prevent duplicate signals |
| `check_and_increment_retry()` | Track order retries |

## 10.4 Database Functions (Lines 1821-2620)
| Function | Purpose |
|----------|---------|
| `save_to_csv()` | Save DataFrame to SQLite |
| `read_csv()` | Read SQLite to DataFrame |
| `trade_manager_append()` | Append to trade_manager |
| `trade_manager_update()` | Update by orderid |
| `_migrate_trade_manager()` | Schema migration |
| `_ensure_table_and_index()` | Create tables |

## 10.5 Renko Calculation (Lines 2755-2940, 8424-8602)
| Function | Purpose |
|----------|---------|
| `calculate_renko_close_traditional()` | Main Renko calculation |
| `calculate_renko_bricks()` | Alternative method |
| `identify_renko_swings()` | Find HH, HL, LL, LH |
| `generate_signals()` | Generate BUYEN/SELEX |

## 10.6 Filter Functions (Lines 2946-4130)
| Function | Purpose |
|----------|---------|
| `calculate_run_lengths()` | Analyze brick runs |
| `is_market_choppy()` | Detect choppy market |
| `detect_chop()` | Chop detection |
| `get_market_direction()` | Trend direction |
| `check_signal_proximity()` | Signal spacing |

## 10.7 Risk Management (Lines 4675-4810)
| Function | Purpose |
|----------|---------|
| `check_daily_loss_limit()` | Daily loss check |
| `check_time_filter()` | Trading hours check |
| `record_trade_pnl()` | Track P&L |
| `reset_daily_pnl()` | Reset daily counters |

## 10.8 Broker API Functions (Lines 4875-5080)
| Function | Purpose |
|----------|---------|
| `get_trade_book()` | Fetch executed trades |
| `get_positions_book()` | Fetch current positions |
| `get_order_book()` | Fetch pending orders |
| `get_net_qty()` | Get position quantity |

## 10.9 Stop Loss & Trailing (Lines 5275-5840)
| Function | Purpose |
|----------|---------|
| `ensure_initial_stop()` | Create initial stop |
| `maybe_trail_stop_after_bricks()` | Trail stop logic |
| `update_position_extreme()` | Track position high/low |
| `calculate_trailing_stop_price()` | Calculate trail price |
| `apply_trailing_stop_for_position()` | Apply trail to position |

## 10.10 Order Management (Lines 6028-6400, 9417-10140)
| Function | Purpose |
|----------|---------|
| `should_execute_order()` | Check execution condition |
| `create_stop_loss()` | Create stop order |
| `close_positions()` | Close trade entries |
| `order_management()` | Main order loop |

## 10.11 Option Functions (Lines 8967-9340)
| Function | Purpose |
|----------|---------|
| `get_current_option_symbols()` | Get CE/PE symbols |
| `get_position_symbol()` | Get position's option |
| `map_to_derivative_exchange()` | Map exchange |

## 10.12 OHLC Update Functions (Lines 8633-8880)
| Function | Purpose |
|----------|---------|
| `update_index_ohlc_data()` | Main OHLC update loop |
| `fetch_historical_data()` | Get candles from broker |

## 10.13 Square-Off Functions (Lines 10214-10465)
| Function | Purpose |
|----------|---------|
| `is_exchange_within_trading_hours()` | Check trading hours |
| `is_exchange_squared_off()` | Check if squared off |
| `square_off_guard()` | End-of-day square-off |

---

# 11. CONFIGURATION PARAMETERS

## 11.1 Trading Parameters
```python
# Trailing Stop
ENABLE_TRAILING_STOP = True
TRAIL_BREAKEVEN_AFTER = 1.5
TRAIL_START_AFTER = 1.5
TRAIL_DISTANCE = 0.5

# Stop Loss
STOP_LOSS_BRICKS = 4.0

# Entry
ENTRY_OFFSET_BRICKS = 0  # Enter at signal price

# Daily Limits
ENABLE_DAILY_LOSS_LIMIT = True
MAX_DAILY_LOSS_POINTS = 500
```

## 11.2 Time Parameters
```python
# Trading Hours
TRADING_START_TIMES = {"MCX": (9, 5)}    # 09:05
TRADING_END_TIMES = {"MCX": (23, 0)}     # 23:00
SQUARED_OFF_TIMES = {"MCX": (23, 15)}    # 23:15

# Cooldown
REENTRY_COOLDOWN_MINUTES = 5
```

## 11.3 Disabled Filters
```python
ENABLE_DIRECTION_FILTER = False
ENABLE_SIGNAL_PROXIMITY_FILTER = False
ENABLE_EXIT_ON_OPPOSITE_SIGNAL = False
ENABLE_CHOP_DETECTOR = False
ENABLE_BRICK_RATE_MONITOR = False
ENABLE_DIRECTION_STABILITY = False
ENABLE_TIME_FILTER = False  # MCX has different hours
ENABLE_QUALITY_SCORING = False
```

---

# 12. THREAD ARCHITECTURE

## 12.1 Thread Overview
```
Main Process
│
├── Thread 1: websocket_thread_func
│   └── Live price feed via WebSocket
│
├── Thread 2: fallback_thread_func  
│   └── REST API polling backup (5 sec)
│
├── Thread 3: update_index_ohlc_data
│   └── OHLC/Renko calculation (1 min)
│
├── Thread 4: ohlc_update_monitor
│   └── Data freshness monitoring
│
├── Thread 5: square_off_guard
│   └── End-of-day position closure (60 sec)
│
└── Thread 6: order_management (MAIN)
    └── Order execution loop (continuous)
```

## 12.2 Thread Safety
```python
# Locks
_shutdown_lock = threading.Lock()
_pnl_lock = threading.Lock()
_cooldown_lock = threading.Lock()
_direction_lock = threading.Lock()
_brick_rate_lock = threading.Lock()
_filter_log_lock = threading.Lock()
csv_file_lock = threading.Lock()

# Events
_shutdown_flag = threading.Event()
websocket_active = threading.Event()
ltp_event_queue = queue.Queue()
```

---

# SUMMARY

## Key Points:

1. **Two Instruments**: CRUDEOIL (brick=5, lot=100) and NATURALGAS (brick=0.8, lot=1250)

2. **Two Databases**: 
   - `tradebook_mcx.db` - Trading data (tables: trade_manager, symbols_to_trade, ohlc_*, renko_*)
   - `openalgo.db` - Symbol lookup (used for option chain)

3. **Signal Types**: BUYEN (entry) → BUYCL (buy call) → SELST (stop loss)

4. **LONG ONLY**: No short selling, only buys CE or PE options

5. **Risk Management**: 4-brick stop loss, 1.5-brick trailing, 500-point daily limit

6. **Trading Hours**: 09:05 - 23:00 IST, square-off at 23:15 IST

7. **~215 Functions**: Covering data, signals, orders, risk, and utilities

---

*Document Version: 1.0*
*Script Version: MCX_Bot_Complete1.py*
*Total Lines: 11,347*
*Total Functions: 215*
