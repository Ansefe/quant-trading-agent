#!/usr/bin/env python3
"""
engine.py — Motor de backtesting event-driven vela a vela.

Reutiliza la lógica de los scanners existentes (sr_scanner, smc_scanner,
rsi_divergence) alimentándolos con slices de DataFrames locales para
evitar look-ahead bias.
"""

import os
import sys
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

# Add parent dir to path for scanner imports
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT_DIR)

from sr_scanner import get_fractal_extremes, cluster_levels, calculate_atr_pct
from smc_scanner import find_unmitigated_fvgs
from rsi_divergence import check_divergences

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────
FEE_LIMIT = 0.0002    # 0.02% per side
FEE_MARKET = 0.0005   # 0.05% per side
WARMUP_CANDLES = 200   # Need enough candles for indicators (SMA 200)


# ──────────────────────────────────────────────────────────────
# Scanner Wrappers (work with local DataFrames, no API calls)
# ──────────────────────────────────────────────────────────────

def scan_sr_local(df, timeframe):
    """Run SR scanner on a local DataFrame slice."""
    try:
        if len(df) < 30:
            return []

        # Dynamic ATR threshold
        atr_pct = calculate_atr_pct(df)
        threshold = atr_pct * 0.25

        # Determine fractal order based on TF
        order_map = {'15m': 20, '1h': 20, '4h': 10, '1d': 5, '1w': 3}
        order = order_map.get(timeframe, 10)

        supports, resistances = get_fractal_extremes(df, timeframe, order=order)
        levels = cluster_levels(supports + resistances, threshold_pct=threshold)

        current_price = df['close'].iloc[-1]
        result = []
        for lvl in levels:
            is_support = lvl['precio_linea'] < current_price
            result.append({
                'price_level': lvl['precio_linea'],
                'touches': lvl['toques'],
                'confluence': lvl['confluencia'],
                'is_support': is_support
            })
        return result
    except Exception:
        return []


def scan_fvg_local(df):
    """Run FVG scanner on a local DataFrame slice."""
    try:
        if len(df) < 5:
            return []
        fvgs = find_unmitigated_fvgs(df)
        current_price = df['close'].iloc[-1]
        result = []
        for fvg in fvgs:
            center = (fvg['techo'] + fvg['piso']) / 2
            result.append({
                'center_price': center,
                'top_price': fvg['techo'],
                'bottom_price': fvg['piso'],
                'type': fvg['tipo']
            })
        return result
    except Exception:
        return []


def scan_divergences_local(df):
    """Run RSI divergence scanner on a local DataFrame slice."""
    try:
        if len(df) < 30:
            return []
        order = 5 if len(df) > 100 else 3
        divs = check_divergences(df.copy(), order=order, historical=False, lookback_window=60)
        result = []
        for d in divs:
            result.append({
                'type': d['tipo'],
                'state': d['estado'],
                'price': d['precio'],
                'rsi': d['rsi']
            })
        return result
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
# Confluence Scoring (mirrors main.py analyze_confluences)
# ──────────────────────────────────────────────────────────────

def score_confluence(price, sr_levels, fvgs, divergences):
    """
    Score-based confluence detector.
    Returns list of signals: [{'type': 'LONG'|'SHORT', 'score': int, 'details': dict}]
    """
    proximity = 0.03
    signals = []

    supports = [s for s in sr_levels if s['is_support'] and abs(price - s['price_level']) / price < proximity]
    resistances = [r for r in sr_levels if not r['is_support'] and abs(r['price_level'] - price) / price < proximity]

    rsi_bull = [d for d in divergences if 'ALCISTA' in d['type'] and 'ACTIVA' in d['state']]
    rsi_bear = [d for d in divergences if 'BAJISTA' in d['type'] and 'ACTIVA' in d['state']]

    fvg_above = [f for f in fvgs if f['center_price'] > price]
    fvg_below = [f for f in fvgs if f['center_price'] < price]

    # LONG scoring
    long_score = 0
    long_details = {}
    if supports:
        best = max(supports, key=lambda s: s.get('touches', 1))
        long_score += 3 + min(2, len(best.get('confluence', [])))
        long_details['support'] = best['price_level']
    if rsi_bull:
        long_score += 3
        long_details['rsi_div'] = True
    if fvg_above:
        long_score += 2
        long_details['fvg_target'] = fvg_above[0]['center_price']

    if long_score >= 5:
        target = fvg_above[0]['center_price'] if fvg_above else price * 1.03
        signals.append({
            'type': 'LONG', 'score': min(10, long_score),
            'target': target, 'details': long_details
        })

    # SHORT scoring
    short_score = 0
    short_details = {}
    if resistances:
        best = max(resistances, key=lambda r: r.get('touches', 1))
        short_score += 3 + min(2, len(best.get('confluence', [])))
        short_details['resistance'] = best['price_level']
    if rsi_bear:
        short_score += 3
        short_details['rsi_div'] = True
    if fvg_below:
        short_score += 2
        short_details['fvg_target'] = fvg_below[0]['center_price']

    if short_score >= 5:
        target = fvg_below[0]['center_price'] if fvg_below else price * 0.97
        signals.append({
            'type': 'SHORT', 'score': min(10, short_score),
            'target': target, 'details': short_details
        })

    return signals


# ──────────────────────────────────────────────────────────────
# Position Manager
# ──────────────────────────────────────────────────────────────

class Position:
    def __init__(self, type_, entry_price, entry_date, tp_pct, sl_pct, leverage, notional=10.0):
        self.type = type_           # 'LONG' or 'SHORT'
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.tp_pct = tp_pct / 100  # Convert from percent
        self.sl_pct = sl_pct / 100
        self.leverage = leverage
        self.notional = notional    # USD position size
        self.closed = False
        self.exit_price = None
        self.exit_date = None
        self.exit_reason = None
        self.pnl_usd = 0.0

        # Calculate TP/SL prices
        if type_ == 'LONG':
            self.tp_price = entry_price * (1 + self.tp_pct / leverage)
            self.sl_price = entry_price * (1 - self.sl_pct / leverage)
        else:
            self.tp_price = entry_price * (1 - self.tp_pct / leverage)
            self.sl_price = entry_price * (1 + self.sl_pct / leverage)

    def check(self, candle):
        """Check if TP or SL hit by this candle. Returns True if closed."""
        if self.closed:
            return True

        high, low = candle['high'], candle['low']

        if self.type == 'LONG':
            if low <= self.sl_price:
                self._close(self.sl_price, candle['timestamp'], 'SL')
            elif high >= self.tp_price:
                self._close(self.tp_price, candle['timestamp'], 'TP')
        else:  # SHORT
            if high >= self.sl_price:
                self._close(self.sl_price, candle['timestamp'], 'SL')
            elif low <= self.tp_price:
                self._close(self.tp_price, candle['timestamp'], 'TP')

        return self.closed

    def _close(self, exit_price, exit_date, reason):
        self.closed = True
        self.exit_price = exit_price
        self.exit_date = exit_date
        self.exit_reason = reason

        # PnL calculation
        if self.type == 'LONG':
            raw_pnl_pct = (exit_price - self.entry_price) / self.entry_price
        else:
            raw_pnl_pct = (self.entry_price - exit_price) / self.entry_price

        # Apply leverage
        leveraged_pnl_pct = raw_pnl_pct * self.leverage

        # Apply fees (entry + exit, using limit fee)
        total_fee_pct = FEE_LIMIT * 2  # Both sides

        # PnL in USD
        self.pnl_usd = self.notional * (leveraged_pnl_pct - total_fee_pct)

    def to_dict(self):
        return {
            'type': self.type,
            'entry_date': self.entry_date,
            'entry_price': round(self.entry_price, 2),
            'exit_date': self.exit_date,
            'exit_price': round(self.exit_price, 2) if self.exit_price else None,
            'exit_reason': self.exit_reason,
            'pnl_usd': round(self.pnl_usd, 4),
            'tp_price': round(self.tp_price, 2),
            'sl_price': round(self.sl_price, 2),
            'score': getattr(self, 'score', 0)
        }


# ──────────────────────────────────────────────────────────────
# Main Engine
# ──────────────────────────────────────────────────────────────

def run_backtest(csv_path, timeframe, tp_pct, sl_pct, leverage,
                 scan_interval=10, notional=100.0):
    """
    Run the backtest engine.

    Args:
        csv_path: Path to CSV with OHLCV data
        timeframe: Timeframe string (e.g. '1h')
        tp_pct: Take profit percentage (e.g. 2.0 = 2%)
        sl_pct: Stop loss percentage (e.g. 1.0 = 1%)
        leverage: Leverage multiplier (e.g. 5)
        scan_interval: Run scanners every N candles (performance)
        notional: USD per trade
    """
    print(f"\n🚀 Backtesting: {csv_path}")
    print(f"   TP: {tp_pct}% | SL: {sl_pct}% | Leverage: {leverage}x | Notional: ${notional}")

    # Load data
    df = pd.read_csv(csv_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    total_candles = len(df)

    if total_candles < WARMUP_CANDLES + 50:
        return {'error': f'Not enough data. Need at least {WARMUP_CANDLES + 50} candles, got {total_candles}'}

    # State
    trades = []
    open_positions = []
    equity_curve = []
    balance = 0.0
    max_balance = 0.0
    max_drawdown = 0.0

    # Cache for scanner results (avoid running every candle)
    cached_sr = []
    cached_fvgs = []
    cached_divs = []

    print(f"   Total velas: {total_candles} | Warmup: {WARMUP_CANDLES} | Simulando {total_candles - WARMUP_CANDLES} velas...")

    for i in range(WARMUP_CANDLES, total_candles):
        candle = df.iloc[i]
        price = candle['close']
        ts = candle['timestamp'].isoformat() if hasattr(candle['timestamp'], 'isoformat') else str(candle['timestamp'])

        # 1. Check open positions
        still_open = []
        for pos in open_positions:
            was_closed = pos.check(candle)
            if was_closed:
                pos.exit_date = ts  # Ensure string format
                trades.append(pos.to_dict())
                balance += pos.pnl_usd
                max_balance = max(max_balance, balance)
                dd = max_balance - balance
                max_drawdown = max(max_drawdown, dd)
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2. Run scanners periodically (not every candle for performance)
        if i % scan_interval == 0:
            data_slice = df.iloc[:i + 1].copy()  # No look-ahead
            cached_sr = scan_sr_local(data_slice, timeframe)
            cached_fvgs = scan_fvg_local(data_slice)
            cached_divs = scan_divergences_local(data_slice)

        # 3. Score confluences and open new positions (max 1 open at a time)
        if not open_positions:
            signals = score_confluence(price, cached_sr, cached_fvgs, cached_divs)
            for sig in signals[:1]:  # Take best signal only
                pos = Position(
                    type_=sig['type'],
                    entry_price=price,
                    entry_date=ts,
                    tp_pct=tp_pct,
                    sl_pct=sl_pct,
                    leverage=leverage,
                    notional=notional
                )
                pos.score = sig['score']
                open_positions.append(pos)

        # Track equity
        equity_curve.append({'time': ts, 'balance': round(balance, 4)})

        # Progress
        if i % 500 == 0:
            pct = ((i - WARMUP_CANDLES) / (total_candles - WARMUP_CANDLES)) * 100
            print(f"   {pct:.0f}% — Vela {i}/{total_candles} | Trades: {len(trades)} | Balance: ${balance:.2f}", end='\r')

    # Close any remaining open positions at last price
    last_candle = df.iloc[-1]
    for pos in open_positions:
        pos._close(last_candle['close'], last_candle['timestamp'].isoformat() if hasattr(last_candle['timestamp'], 'isoformat') else str(last_candle['timestamp']), 'END')
        trades.append(pos.to_dict())
        balance += pos.pnl_usd

    # Metrics
    wins = [t for t in trades if t['pnl_usd'] > 0]
    losses = [t for t in trades if t['pnl_usd'] <= 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0
    total_profit = sum(t['pnl_usd'] for t in wins)
    total_loss = abs(sum(t['pnl_usd'] for t in losses))
    profit_factor = (total_profit / total_loss) if total_loss > 0 else float('inf')

    # Candles for chart (downsample if too many)
    step = max(1, len(df) // 5000)  # Max ~5000 candles for chart
    chart_candles = []
    for _, row in df.iloc[::step].iterrows():
        chart_candles.append({
            'time': int(row['timestamp'].timestamp()) if hasattr(row['timestamp'], 'timestamp') else int(pd.Timestamp(row['timestamp']).timestamp()),
            'open': round(row['open'], 2),
            'high': round(row['high'], 2),
            'low': round(row['low'], 2),
            'close': round(row['close'], 2)
        })

    result = {
        'metrics': {
            'win_rate': round(win_rate, 1),
            'pnl_total': round(balance, 2),
            'max_drawdown': round(max_drawdown, 2),
            'total_trades': len(trades),
            'wins': len(wins),
            'losses': len(losses),
            'profit_factor': round(profit_factor, 2),
            'avg_win': round(total_profit / len(wins), 4) if wins else 0,
            'avg_loss': round(-total_loss / len(losses), 4) if losses else 0,
        },
        'trades': trades,
        'candles': chart_candles,
        'equity_curve': equity_curve[::step]  # Downsample equity too
    }

    print(f"\n\n✅ Backtest completado!")
    print(f"   Trades: {len(trades)} | Win Rate: {win_rate:.1f}% | PnL: ${balance:.2f} | Max DD: ${max_drawdown:.2f}")

    return result
