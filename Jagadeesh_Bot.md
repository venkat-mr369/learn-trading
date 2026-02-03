# Jagadeesh_Bot.py - Comprehensive Trading Bot Documentation

## 📋 Table of Contents

1. [Overview](#overview)
2. [Topics Covered](#topics-covered)
3. [Instrument-Specific Logic](#instrument-specific-logic)
   - [MCX Commodities](#mcx-commodities)
   - [NSE/BSE Index Options](#nsebse-index-options)
4. [Database Architecture](#database-architecture)
5. [Core Trading Logic](#core-trading-logic)
6. [Signal Generation & Filters](#signal-generation--filters)
7. [Order Management](#order-management)
8. [Risk Management](#risk-management)

---

## Overview

**Jagadeesh_Bot.py** is a sophisticated **Renko-based automated trading system** designed for Indian markets. It trades:
- **NSE Index Options**: NIFTY, BANKNIFTY
- **BSE Index Options**: SENSEX, BANKEX
- **MCX Commodity Options**: CRUDEOIL, GOLDM, SILVERM, NATURALGAS

The bot uses **Renko charts** (brick-based price charts) to generate buy/sell signals and manages positions with trailing stops, quality scoring, and multiple safety filters.

---

## Topics Covered

| # | Topic | Description |
|---|-------|-------------|
| 1 | **Renko Chart Analysis** | Price-based brick charts for signal generation |
| 2 | **Options Trading** | Buying CALL (BUYCL) and PUT (BUYPT) options |
| 3 | **Multi-Exchange Support** | NSE_INDEX, BSE_INDEX, MCX exchanges |
| 4 | **SQLite Database** | Trade management & symbol token storage |
| 5 | **WebSocket Ticker** | Real-time price feeds |
| 6 | **Signal Quality Scoring** | 0-100 score for entry validation |
| 7 | **Trailing Stop Loss** | Dynamic stop loss management |
| 8 | **Time-Based Filters** | Exchange-specific trading hours |
| 9 | **Chop Detection** | Avoid choppy/sideways markets |
| 10 | **Position Management** | Square-off, cooldown, loss limits |
| 11 | **Thread-Safe Operations** | Concurrent processing with locks |
| 12 | **Logging & Monitoring** | Production-grade logging system |

---

## Instrument-Specific Logic

### MCX Commodities

#### 🛢️ CRUDEOIL

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Exchange** | MCX | Multi Commodity Exchange |
| **Brick Size** | 4 points | Data-driven: 79.2% win rate |
| **Renko Method** | CLOSE | Filters noise better |
| **Lot Size** | 100 | Per lot |
| **Strike Step** | 100 | For option selection |
| **Trading Hours** | 9:05 AM - 11:00 PM | Square-off at 11:15 PM |
| **LTP Range** | 1 - 1000 | Valid price range |

**Logic Flow:**
```
1. Fetch CRUDEOIL Futures data (nearest expiry)
2. Calculate CLOSE Renko bricks (4 pts)
3. Generate BUYEN/SELEX signals
4. On BUYEN → Buy CALL option
5. On SELEX → Buy PUT option
6. Apply trailing stop (4 bricks stop, 0.5 trail)
```

**Symbol Resolution:**
```python
# Gets nearest MCX Future
fut = Utils.getNearestMCXFuture("CRUDEOIL")  # e.g., CRUDEOILM18FEB25FUT

# Option selection (ATM)
ce_strike = atm_strike  # Same as ATM for MCX
pe_strike = atm_strike
```

---

#### 🥇 GOLDM (Mini Gold)

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Exchange** | MCX | Multi Commodity Exchange |
| **Brick Size** | 60 points | Data-driven: 62.8% win, 2M+ P&L |
| **Renko Method** | HL (High-Low) | Captures more moves |
| **Lot Size** | 10 | Per lot |
| **Strike Step** | 1000 | For option selection |
| **Trading Hours** | 9:05 AM - 11:00 PM | Square-off at 11:15 PM |
| **LTP Range** | 900 - 15000 | Valid price range |

**Why HL Renko for GOLD?**
```
- Close Renko: Filters too much, misses opportunities
- HL Renko: Lower win rate (62.8%) BUT massive P&L (2M+)
- Gold trends well → HL captures the full move
```

**Logic Flow:**
```python
# Determine Renko method
if symbol.startswith("GOLDM"):
    use_hl_renko = True
    renko_df = calculate_renko_hl_wick_safe(df, brick_size=60)
```

---

#### ⚪ SILVERM (Mini Silver)

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Exchange** | MCX | Multi Commodity Exchange |
| **Brick Size** | 350 points | Data-driven: 60.5% win, 2M+ P&L |
| **Renko Method** | HL (High-Low) | Captures more moves |
| **Lot Size** | 5 | Per lot |
| **Strike Step** | 1000 | For option selection |
| **Trading Hours** | 9:05 AM - 11:00 PM | Square-off at 11:15 PM |
| **LTP Range** | 1 - 15000 | Valid price range |

**Similar to GOLD:**
- Uses HL Renko (not CLOSE)
- Lower win rate but higher P&L
- Larger brick size due to higher volatility

---

#### 🔥 NATURALGAS

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Exchange** | MCX | Multi Commodity Exchange |
| **Brick Size** | 0.8 points | Data-driven: 83.7% win rate |
| **Renko Method** | CLOSE | Filters noise |
| **Lot Size** | 1250 | Per lot |
| **Strike Step** | 5 | For option selection |
| **Trading Hours** | 9:05 AM - 11:00 PM | Square-off at 11:15 PM |
| **LTP Range** | 0 - 500 | Valid price range |

**Smallest Brick Size:**
- Natural Gas is low-priced (₹200-500 range)
- 0.8 points = ~0.3% move per brick
- High win rate (83.7%) with CLOSE Renko

---

### NSE/BSE Index Options

#### 📈 NIFTY

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Exchange** | NSE_INDEX | Index spot for data |
| **Derivative Exchange** | NFO | For options trading |
| **Brick Size** | 8 points | Data-driven: 83.4% win rate |
| **Renko Method** | CLOSE | Filters noise |
| **Lot Size** | 75 | Per lot (updated 2024) |
| **Strike Step** | 100 | For option selection |
| **Trading Hours** | 9:20 AM - 3:00 PM | Square-off at 3:15 PM |
| **LTP Range** | 90 - 250 | For option premium |

**Option Symbol Resolution:**
```python
# Get spot price
ltp = get_ltp_generic(client, "NIFTY")  # e.g., 24500

# Calculate ATM strike
step = 100
atm_strike = round(ltp / step) * step  # e.g., 24500

# CE is 1 step below ATM (for ITM)
ce_strike = atm_strike - step  # 24400

# PE is 1 step above ATM (for ITM)
pe_strike = atm_strike + step  # 24600

# Query database for option symbol
# Result: NIFTY06FEB2524400CE, NIFTY06FEB2524600PE
```

**Signal Flow:**
```
BUYEN Signal → Buy CALL (BUYCL) at entry_price - 1 brick
SELEX Signal → Buy PUT (BUYPT) at entry_price + 1 brick (wait for bounce)
```

---

#### 🏦 BANKNIFTY

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Exchange** | NSE_INDEX | Index spot for data |
| **Derivative Exchange** | NFO | For options trading |
| **Brick Size** | 20 points | Data-driven: 82.6% win rate |
| **Renko Method** | CLOSE | Filters noise |
| **Lot Size** | 35 | Per lot (updated 2024) |
| **Strike Step** | 100 | For option selection |
| **Trading Hours** | 9:20 AM - 3:00 PM | Square-off at 3:15 PM |
| **LTP Range** | 120 - 800 | For option premium |

**Larger Brick Size than NIFTY:**
- BANKNIFTY is more volatile
- 20 pts vs NIFTY's 8 pts
- Similar win rate (~82%)

---

#### 📊 SENSEX

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Exchange** | BSE_INDEX | Index spot for data |
| **Derivative Exchange** | BFO | BSE F&O segment |
| **Brick Size** | 25 points | Data-driven: 83.9% win rate |
| **Renko Method** | CLOSE | Filters noise |
| **Lot Size** | 20 | Per lot |
| **Strike Step** | 500 | For option selection |
| **Trading Hours** | 9:20 AM - 3:00 PM | Square-off at 3:15 PM |
| **LTP Range** | 150 - 1000 | For option premium |

**BSE Specifics:**
- Different exchange (BFO vs NFO)
- Larger strike step (500 vs 100)
- Smaller lot size (20 vs 75 for NIFTY)

---

#### 🏛️ BANKEX

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Exchange** | BSE_INDEX | Index spot for data |
| **Derivative Exchange** | BFO | BSE F&O segment |
| **Brick Size** | 25 points | Similar to SENSEX |
| **Renko Method** | CLOSE | Filters noise |
| **Lot Size** | 30 | Per lot |
| **Strike Step** | 500 | For option selection |
| **Trading Hours** | 9:20 AM - 3:00 PM | Square-off at 3:15 PM |
| **LTP Range** | 150 - 1000 | For option premium |

---

## Database Architecture

### Database Files

```
db/
├── openalgo.db      # Main symbol/token database
└── tradebook.db     # Trade history database
```

### Key Tables

#### 1. `symtoken` (Symbol Token Table)
```sql
CREATE TABLE symtoken (
    symbol TEXT,
    token TEXT,
    exchange TEXT,        -- NSE_INDEX, BSE_INDEX, MCX, NFO, BFO
    instrumenttype TEXT,  -- CE, PE, FUT, EQ
    strike REAL,
    expiry TEXT,
    lotsize INTEGER
);
```

**Usage:**
```python
# Lookup option by strike
rows = cur.execute("""
    SELECT symbol, expiry FROM symtoken
    WHERE exchange = 'NFO'
      AND symbol LIKE 'NIFTY%'
      AND instrumenttype = 'CE'
      AND strike = 24400
    ORDER BY expiry ASC
""").fetchall()
```

#### 2. `trade_manager` (Trade Management)
```sql
CREATE TABLE trade_manager (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange TEXT,         -- NSE_INDEX, BSE_INDEX, MCX
    timestamp TEXT,
    renko_signal TEXT,     -- BUYEN, SELEX, BUYCL, BUYPT, SELST, SELSP, SELCL, SELPT
    symbol TEXT,           -- Option symbol
    entry_price REAL,
    limit_entry_price REAL,
    exec_price REAL,
    quantity REAL,
    order_status TEXT,     -- OPEN, PLACED, INPOSITION, CLOSED, CANCELLED
    orderid TEXT,
    close_reason TEXT      -- STOPLOSS, SIGNAL_EXIT, SQUARE_OFF
);
```

**Signal Types:**
| Signal | Meaning |
|--------|---------|
| BUYEN | Buy Entry (from Renko UP brick) |
| SELEX | Sell Exit (from Renko DOWN brick) |
| BUYCL | Buy CALL option |
| BUYPT | Buy PUT option |
| SELST | Sell Stop (CALL stop loss) |
| SELSP | Sell Stop Put (PUT stop loss) |
| SELCL | Sell CALL (profit exit) |
| SELPT | Sell PUT (profit exit) |

#### 3. `symbols_to_trade` (CSV/DB Hybrid)
```
ohlcdata/symbols_to_trade.csv
├── exchange    (NSE_INDEX, BSE_INDEX, MCX)
├── symbol      (NIFTY, BANKNIFTY, CRUDEOILM18FEB25FUT)
└── brick_size  (8, 20, 4, 60, etc.)
```

### Database Operations

**Lot Size Updates (Startup):**
```python
UPDATES = [
    ("BANKNIFTY CE/PE → 35", """
        UPDATE symtoken SET lotsize = 35
        WHERE symbol LIKE '%BANKNIFTY%' AND (symbol LIKE '%CE' OR symbol LIKE '%PE');
    """),
    ("NIFTY CE/PE → 75", """
        UPDATE symtoken SET lotsize = 75
        WHERE symbol LIKE '%NIFTY%' AND symbol NOT LIKE '%BANKNIFTY%'
          AND (symbol LIKE '%CE' OR symbol LIKE '%PE');
    """),
    ("CRUDEOIL CE/PE → 100", """
        UPDATE symtoken SET lotsize = 100
        WHERE symbol LIKE '%CRUDEOIL%' AND (symbol LIKE '%CE' OR symbol LIKE '%PE');
    """),
    # ... more updates
]
```

**Trade Upsert:**
```python
def add_trade(exchange, timestamp, renko_signal, symbol, entry_price, ...):
    sql = """
    INSERT INTO trade_manager (...) VALUES (...)
    ON CONFLICT(symbol, exchange, renko_signal, entry_price, timestamp)
    DO UPDATE SET ...;
    """
    with _engine.begin() as cn:
        cn.execute(text(sql), row)
```

---

## Core Trading Logic

### Renko Brick Calculation

#### CLOSE Renko (INDEX, CRUDE, NATGAS)
```python
def calculate_renko_close_traditional(df, brick_size):
    """
    Traditional CLOSE-based Renko:
    - Only uses closing prices
    - New brick forms when price moves brick_size from last brick
    - Filters intraday noise
    """
    # Brick forms when: close >= last_brick + brick_size (UP)
    # Brick forms when: close <= last_brick - brick_size (DOWN)
```

#### HL Renko (GOLD, SILVER)
```python
def calculate_renko_hl_wick_safe(df, brick_size):
    """
    High-Low Renko with wicks:
    - Uses both high and low prices
    - Captures more price action
    - Better for trending markets like GOLD/SILVER
    """
    # UP brick: high >= last_brick + brick_size
    # DOWN brick: low <= last_brick - brick_size
```

### Signal Generation

```python
def generate_signals(renko_df, brick_size):
    """
    BUYEN: Direction changes from DOWN to UP
    SELEX: Direction changes from UP to DOWN
    """
    for i in range(1, len(renko_df)):
        prev_brick = renko_df['Renko_Brick'].iloc[i-1]
        curr_brick = renko_df['Renko_Brick'].iloc[i]
        
        if curr_brick > prev_brick:  # UP brick
            if prev_direction == 'DOWN':
                signal = 'BUYEN'  # Direction changed → Entry signal
        elif curr_brick < prev_brick:  # DOWN brick
            if prev_direction == 'UP':
                signal = 'SELEX'  # Direction changed → Exit signal
```

### Trade Entry Logic

```python
# BUYEN Signal Processing
if renko_signal == "BUYEN":
    # 1. Exit any existing PUT (opposite position)
    if put_qty > 0:
        signals_to_add.append({"signal": "SELPT", ...})
    
    # 2. Check filters
    can_trade, time_reason = check_time_filter(exchange)
    can_trade, loss_reason = check_daily_loss_limit()
    can_trade, direction_reason = should_take_trade('BUYCL', renko_df, symbol)
    
    # 3. If all filters pass, create BUYCL signal
    if can_trade:
        signals_to_add.append({
            "signal": "BUYCL",
            "symbol": option_symbol,
            "entry_price": brick_price,
            "limit_entry_price": brick_price - brick_size,  # 1 brick below
            "quantity": lots * lot_size
        })

# SELEX Signal Processing (mirror logic for PUT)
if renko_signal == "SELEX":
    # 1. Exit any existing CALL
    # 2. Check filters
    # 3. Create BUYPT signal
```

### Trade Execution

```python
def order_management(trade_manager, client, ltp_dict):
    """Main order processing loop"""
    
    for index, trade in open_trades.iterrows():
        action = trade["renko_signal"]
        ltp = get_ltp(exchange, symbol)
        
        # BUYCL Execution: LTP <= threshold (buy on dip)
        if action == "BUYCL" and inposition_qty == 0:
            threshold = entry_price - ENTRY_OFFSET_BRICKS * brick_size
            if ltp <= threshold:
                execute_order("BUY", symbol, quantity)
        
        # BUYPT Execution: LTP >= threshold (buy on bounce)
        elif action == "BUYPT" and inposition_qty == 0:
            threshold = entry_price + ENTRY_OFFSET_BRICKS * brick_size
            if ltp >= threshold:
                execute_order("BUY", symbol, quantity)
        
        # Stop Loss Execution (SELST/SELSP)
        elif action == "SELST":  # CALL stop
            if ltp <= entry_price:  # Stop hit
                execute_order("SELL", symbol, quantity)
        
        elif action == "SELSP":  # PUT stop
            if ltp >= entry_price:  # Stop hit
                execute_order("SELL", symbol, quantity)
```

---

## Signal Generation & Filters

### Quality Scoring System (0-100)

```python
def calculate_signal_quality(renko_df, signal_type, symbol):
    """
    Scores each signal based on multiple factors:
    """
    components = {
        'trend_strength': 0,     # 0-25 pts: Consecutive bricks
        'trend_alignment': 0,    # 0-30 pts: Signal vs dominant trend
        'momentum': 0,           # 0-20 pts: Last 5 bricks direction
        'breakout': 0,           # 0-15 pts: Breaking recent highs/lows
        'clean_market': 0,       # 0-10 pts: Low reversal count
    }
    
    # Trend Strength (consecutive same-direction bricks)
    if consecutive >= 4:
        components['trend_strength'] = 25
    elif consecutive >= 3:
        components['trend_strength'] = 20
    # ...
    
    # Counter-trend Penalty
    if is_call and dominant_trend == 'DOWN':
        counter_trend_penalty = -15
    
    total_score = sum(components.values()) + counter_trend_penalty
    
    if total_score >= 70:
        return 'TAKE', "HIGH QUALITY"
    elif total_score >= 60:
        return 'MAYBE', "MEDIUM QUALITY"
    else:
        return 'SKIP', "LOW QUALITY"
```

### Filter Chain (When Quality Scoring Disabled)

```
1. Time Filter → Check trading hours
2. Daily Loss Limit → Check if exceeded
3. Chop Detector → Check for reversal patterns
4. Direction Filter → Check trend alignment
5. Signal Proximity → Check recent opposite signals
6. Cooldown Check → Check if stopped out recently
```

### Direction Filter Logic

```python
def get_market_direction(renko_df, lookback=10):
    """
    Determines market direction from last N bricks
    """
    bricks = renko_df['Renko_Brick'].tail(lookback).values
    
    # Calculate direction of each brick
    directions = []
    for i in range(1, len(bricks)):
        if bricks[i] > bricks[i-1]:
            directions.append(1)   # UP
        elif bricks[i] < bricks[i-1]:
            directions.append(-1)  # DOWN
    
    # Net movement
    net_bricks = (bricks[-1] - bricks[0]) / brick_size
    
    # Last 2 bricks determine short-term direction
    last_2 = directions[-2:]
    if all(d == 1 for d in last_2):
        short_term = 'UP'
    elif all(d == -1 for d in last_2):
        short_term = 'DOWN'
    
    # HYBRID CHECK: Short-term vs Net must agree
    if short_term == 'UP' and net_bricks < -1:
        return 'CHOPPY', net_bricks  # Contradiction!
    
    # Minimum net strength check
    if short_term == 'UP' and net_bricks < MIN_NET_BRICKS:
        return 'CHOPPY', net_bricks  # Too weak
    
    return short_term, net_bricks
```

### Chop Detector

```python
def detect_chop(renko_df):
    """
    Counts reversals in last N bricks
    """
    bricks = renko_df['Renko_Brick'].tail(CHOP_LOOKBACK).values
    
    # Count reversals
    reversals = 0
    for i in range(2, len(bricks)):
        prev_dir = 1 if bricks[i-1] > bricks[i-2] else -1
        curr_dir = 1 if bricks[i] > bricks[i-1] else -1
        if curr_dir != prev_dir:
            reversals += 1
    
    # Alternating pattern detection (↑↓↑↓)
    is_alternating = True
    for i in range(2, len(directions)):
        if directions[i] == directions[i-1]:
            is_alternating = False
            break
    
    if is_alternating:
        return True, reversals, "ALTERNATING PATTERN"
    
    if reversals > MAX_REVERSALS:
        return True, reversals, "TOO MANY REVERSALS"
    
    return False, reversals, "TRENDING"
```

---

## Order Management

### Stop Loss Management

```python
# Initial Stop Loss
STOP_LOSS_BRICKS = 4.0  # 4 bricks from entry

# For CALL (BUYCL):
stop_price = entry_price - STOP_LOSS_BRICKS * brick_size
stop_signal = "SELST"

# For PUT (BUYPT):
stop_price = entry_price + STOP_LOSS_BRICKS * brick_size
stop_signal = "SELSP"
```

### Trailing Stop Logic

```python
ENABLE_TRAILING_STOP = True
TRAIL_BREAKEVEN_AFTER = 1.5   # Move to breakeven at 1.5 bricks profit
TRAIL_START_AFTER = 1.5       # Start trailing at 1.5 bricks profit
TRAIL_DISTANCE = 0.5          # Trail 0.5 brick behind

def calculate_trailing_stop(position_type, entry_price, current_price, brick_size):
    """
    Dynamic trailing stop:
    1. At +1.5 bricks: Move stop to breakeven
    2. At +1.5+ bricks: Trail 0.5 brick behind current
    3. Never move stop backwards
    """
    if position_type == 'CALL':
        profit_bricks = (current_price - entry_price) / brick_size
        
        if profit_bricks >= TRAIL_START_AFTER:
            # Trail stop
            new_stop = current_price - TRAIL_DISTANCE * brick_size
            return max(new_stop, entry_price)  # Never below entry
        
        elif profit_bricks >= TRAIL_BREAKEVEN_AFTER:
            # Move to breakeven
            return entry_price
    
    # Similar logic for PUT (inverted)
```

### Square-Off Logic

```python
SQUARED_OFF_TIMES = {
    "NSE_INDEX": (15, 15),  # 3:15 PM
    "BSE_INDEX": (15, 15),  # 3:15 PM
    "MCX": (23, 15),        # 11:15 PM
}

def square_off_guard(client, poll_seconds=60):
    """
    Monitor for square-off times and close positions
    """
    while SQUARE_OFF_ACTIVE:
        now = datetime.now(IST)
        
        for exchange, (hour, minute) in SQUARED_OFF_TIMES.items():
            if now.hour >= hour and now.minute >= minute:
                # 1. Cancel open orders
                # 2. Close broker positions
                # 3. Update trade manager
                _close_broker_positions_for_exchange(client, exchange)
        
        time.sleep(poll_seconds)
```

---

## Risk Management

### Daily Loss Limit

```python
ENABLE_DAILY_LOSS_LIMIT = True
MAX_DAILY_LOSS_POINTS = 500  # Stop trading after losing 500 points

_daily_pnl_points = 0  # Thread-safe counter

def check_daily_loss_limit():
    with _pnl_lock:
        if _daily_pnl_points <= -MAX_DAILY_LOSS_POINTS:
            return False, "Daily loss limit hit"
    return True, "OK"

def record_trade_pnl(entry_price, exit_price, position_type):
    global _daily_pnl_points
    
    if position_type == "CALL":
        pnl = exit_price - entry_price
    else:
        pnl = entry_price - exit_price
    
    with _pnl_lock:
        _daily_pnl_points += pnl
```

### Re-Entry Cooldown

```python
ENABLE_REENTRY_COOLDOWN = True
REENTRY_COOLDOWN_MINUTES = 5

def record_stoploss_for_cooldown(symbol, signal_type, stop_price, original_entry):
    """
    Only record cooldown for ACTUAL LOSSES (not trailing stop profits)
    """
    is_loss = is_stop_a_loss(direction, original_entry, stop_price)
    
    if is_loss:
        key = (symbol, direction, round(stop_price, 2))
        _reentry_cooldown_tracker[key] = pd.Timestamp.now()

def check_reentry_cooldown(symbol, signal_type, price_level):
    """
    Prevent re-entry at same price level within cooldown period
    """
    key = (symbol, direction, round(price_level, 2))
    
    if key in _reentry_cooldown_tracker:
        age = (now - _reentry_cooldown_tracker[key]).total_seconds() / 60
        if age < REENTRY_COOLDOWN_MINUTES:
            return False, f"Cooldown: {REENTRY_COOLDOWN_MINUTES - age:.1f} mins remaining"
    
    return True, "OK"
```

### Order Retry Limits

```python
MAX_ORDER_RETRIES = 3
ORDER_RETRY_WINDOW_MINUTES = 10

def check_and_increment_retry(symbol, action, entry_price):
    """
    Prevents infinite rejection loops (e.g., insufficient funds)
    """
    key = (symbol, action, entry_price)
    
    if key in _order_retry_tracker:
        if tracker['count'] > MAX_ORDER_RETRIES:
            return False, "Exceeded max retries"
    
    return True, "OK"
```

---

## Summary: Per-Instrument Configuration

| Instrument | Exchange | Brick | Renko | Lot | Stop | Trail | Win Rate |
|------------|----------|-------|-------|-----|------|-------|----------|
| NIFTY | NSE_INDEX | 8 | CLOSE | 75 | 4B | 0.5B | 83.4% |
| BANKNIFTY | NSE_INDEX | 20 | CLOSE | 35 | 4B | 0.5B | 82.6% |
| SENSEX | BSE_INDEX | 25 | CLOSE | 20 | 4B | 0.5B | 83.9% |
| BANKEX | BSE_INDEX | 25 | CLOSE | 30 | 4B | 0.5B | ~82% |
| CRUDEOIL | MCX | 4 | CLOSE | 100 | 4B | 0.5B | 79.2% |
| NATURALGAS | MCX | 0.8 | CLOSE | 1250 | 4B | 0.5B | 83.7% |
| GOLDM | MCX | 60 | HL | 10 | 4B | 0.5B | 62.8% |
| SILVERM | MCX | 350 | HL | 5 | 4B | 0.5B | 60.5% |

**Key Insights:**
- INDEX instruments use **CLOSE Renko** → Higher win rate (80%+)
- GOLD/SILVER use **HL Renko** → Lower win rate but higher P&L
- MCX trades until 11:15 PM, NSE/BSE until 3:15 PM
- All instruments use 4-brick stop, 0.5-brick trail

---

## File Structure

```
project/
├── Jagadeesh_Bot.py          # Main bot script
├── db/
│   ├── openalgo.db           # Symbol database
│   └── tradebook.db          # Trade history
├── ohlcdata/
│   ├── symbols_to_trade.csv  # Active instruments
│   ├── trade_manager.csv     # Current trades
│   ├── NIFTY_ohlc.csv        # OHLC data
│   ├── NIFTY_renko.csv       # Renko data
│   └── filter_log_*.csv      # Filter decisions
├── logs/
│   └── v5_trading_*.log      # Daily logs
└── core/
    ├── Controller.py
    ├── Config.py
    ├── Instruments.py
    └── Ticker.py
```

---

*Generated from Jagadeesh_Bot.py analysis - Version 5.2*
