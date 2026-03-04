#!/usr/bin/env python3
"""
engine.py — Motor de backtesting V2 con confluencia multi-TF y Martingale.

Flujo:
1. Carga datasets multi-TF desde directorio (15m, 1h, 4h, 1d, 1w)
2. Usa el TF más bajo como "reloj" de simulación
3. En cada scan interval:
   - Corre SR scanner por cada TF y hace merge multi-TF
   - Corre RSI divergencias por cada TF con ventana de actividad temporal
   - Corre FVG por cada TF con ponderación por importancia
4. Scoring de confluencia real (como main.py)
5. Soporta dos modos: Clean Entry y Martingale/DCA
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from datetime import datetime

# Add parent dir for scanner imports
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT_DIR)

from sr_scanner import get_fractal_extremes, cluster_levels, calculate_atr_pct
from smc_scanner import find_unmitigated_fvgs
from rsi_divergence import check_divergences

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────
FEE_LIMIT = 0.0002    # 0.02% per side (Binance Futures Limit)
WARMUP_CANDLES = 200   # Min candles before simulation starts


def ts_to_unix(ts):
    """Convert pandas Timestamp to Unix seconds (UTC).
    This MUST be the same conversion used for chart candles."""
    if hasattr(ts, 'timestamp'):
        return int(ts.timestamp())
    return int(pd.Timestamp(ts).timestamp())

# Fractal order per TF (same as live system)
ORDER_MAP = {'15m': 20, '1h': 20, '4h': 10, '1d': 5, '1w': 3}

# RSI activity window per TF (in hours)
RSI_ACTIVITY_HOURS = {'15m': 4, '1h': 24, '4h': 72, '1d': 168, '1w': 672}

# TF duration in milliseconds
TF_MS = {
    '15m': 15 * 60 * 1000,
    '1h':  60 * 60 * 1000,
    '4h':  4 * 60 * 60 * 1000,
    '1d':  24 * 60 * 60 * 1000,
    '1w':  7 * 24 * 60 * 60 * 1000,
}

# TF hierarchy (for sorting)
TF_RANK = {'15m': 0, '1h': 1, '4h': 2, '1d': 3, '1w': 4}


# ──────────────────────────────────────────────────────────────
# Multi-TF Data Loader
# ──────────────────────────────────────────────────────────────

def load_multi_tf_data(dataset_dir):
    """Load all TF CSVs from a dataset directory."""
    datasets = {}
    meta_path = os.path.join(dataset_dir, 'meta.json')

    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    else:
        meta = {}

    for tf in ORDER_MAP.keys():
        filepath = os.path.join(dataset_dir, f"{tf}.csv")
        if os.path.exists(filepath):
            df = pd.read_csv(filepath)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            datasets[tf] = df
            print(f"   ✅ {tf}: {len(df)} velas cargadas")
        else:
            print(f"   ⚠️ {tf}: archivo no encontrado")

    return datasets, meta


def get_tf_slice(df, current_time):
    """Get the slice of a TF DataFrame up to current_time (no look-ahead)."""
    return df[df['timestamp'] <= current_time].copy()


# ──────────────────────────────────────────────────────────────
# Multi-TF SR Scanner with Confluence Merge
# ──────────────────────────────────────────────────────────────

def scan_sr_multi_tf(datasets, current_time):
    """
    Run SR scanner on each TF independently, then merge to find
    cross-TF confluences (like main.py's multi-TF scan).
    """
    all_fractal_levels = []  # List of (price, tf_string)

    for tf, df in datasets.items():
        try:
            slice_df = get_tf_slice(df, current_time)
            if len(slice_df) < 30:
                continue

            order = ORDER_MAP.get(tf, 10)
            atr_pct = calculate_atr_pct(slice_df)
            threshold = atr_pct * 0.25

            supports, resistances = get_fractal_extremes(slice_df, tf, order=order)
            # Don't cluster yet — collect raw fractals for cross-TF merge
            all_fractal_levels.extend(supports + resistances)
        except Exception:
            continue

    if not all_fractal_levels:
        return []

    # Cluster ALL fractals across TFs using the clock TF's ATR
    clock_tf = min(datasets.keys(), key=lambda t: TF_RANK.get(t, 99))
    clock_df = get_tf_slice(datasets[clock_tf], current_time)
    if len(clock_df) < 30:
        return []

    atr_pct = calculate_atr_pct(clock_df)
    threshold = atr_pct * 0.5  # Wider threshold for cross-TF merge

    levels = cluster_levels(all_fractal_levels, threshold_pct=threshold)

    # Format results
    current_price = clock_df['close'].iloc[-1]
    result = []
    for lvl in levels:
        is_support = lvl['precio_linea'] < current_price
        result.append({
            'price_level': lvl['precio_linea'],
            'touches': lvl['toques'],
            'confluence': lvl['confluencia'],  # List of TFs like ['1h', '4h', '1d']
            'is_support': is_support
        })
    return result


# ──────────────────────────────────────────────────────────────
# Multi-TF RSI Divergence Scanner
# ──────────────────────────────────────────────────────────────

def scan_divergences_multi_tf(datasets, current_time):
    """
    Run RSI divergence scanner on each TF with time-based activity filter.
    Returns divergences with their source TF.
    """
    all_divs = []

    for tf, df in datasets.items():
        try:
            slice_df = get_tf_slice(df, current_time)
            if len(slice_df) < 30:
                continue

            order = ORDER_MAP.get(tf, 5)
            divs = check_divergences(slice_df.copy(), order=order, historical=True, lookback_window=60)

            # Time-based activity filter
            activity_hours = RSI_ACTIVITY_HOURS.get(tf, 24)
            activity_cutoff = current_time - pd.Timedelta(hours=activity_hours)

            for d in divs:
                # Parse the date from the divergence
                try:
                    div_date = pd.to_datetime(d['fecha'], format='%Y-%m-%d %H:%M')
                except Exception:
                    continue

                is_active = div_date >= activity_cutoff

                if is_active:
                    all_divs.append({
                        'type': d['tipo'],
                        'state': 'ACTIVA 🔥',
                        'price': d['precio'],
                        'rsi': d['rsi'],
                        'tf': tf
                    })
        except Exception:
            continue

    return all_divs


# ──────────────────────────────────────────────────────────────
# Multi-TF FVG Scanner
# ──────────────────────────────────────────────────────────────

def scan_fvg_multi_tf(datasets, current_time):
    """
    Run FVG scanner on each TF. Higher TFs get more weight.
    """
    all_fvgs = []

    for tf, df in datasets.items():
        try:
            slice_df = get_tf_slice(df, current_time)
            if len(slice_df) < 5:
                continue

            fvgs = find_unmitigated_fvgs(slice_df)
            for fvg in fvgs:
                center = (fvg['techo'] + fvg['piso']) / 2
                all_fvgs.append({
                    'center_price': center,
                    'top_price': fvg['techo'],
                    'bottom_price': fvg['piso'],
                    'type': fvg['tipo'],
                    'tf': tf,
                    'tf_rank': TF_RANK.get(tf, 0)
                })
        except Exception:
            continue

    return all_fvgs


# ──────────────────────────────────────────────────────────────
# Confluence Scoring V2 (Multi-TF aware)
# ──────────────────────────────────────────────────────────────

def score_confluence(price, sr_levels, fvgs, divergences, min_touches=3,
                     proximity_pct=3.0, require_divergence='off', divergence_max_tf='any'):
    """
    Score-based confluence detector with multi-TF awareness.
    Returns signals with limit_price at the SR level (not market price).

    Args:
        proximity_pct: Max distance % from price to consider an SR level
        require_divergence: 'off' = no requirement, 'on' = require RSI divergence
        divergence_max_tf: 'any', '15m', '1h', '4h', '1d' — max TF to consider for divergence filter
    """
    proximity = proximity_pct / 100
    signals = []

    # TF rank filter for divergence requirement
    TF_MAX_RANK = {'15m': 0, '1h': 1, '4h': 2, '1d': 3, 'any': 99}
    div_max_rank = TF_MAX_RANK.get(divergence_max_tf, 99)

    # Filter by min_touches
    quality_levels = [l for l in sr_levels if l.get('touches', 0) >= min_touches]

    supports = [s for s in quality_levels if s['is_support'] and abs(price - s['price_level']) / price < proximity]
    resistances = [r for r in quality_levels if not r['is_support'] and abs(r['price_level'] - price) / price < proximity]

    rsi_bull = [d for d in divergences if 'ALCISTA' in d['type'] and 'ACTIVA' in d['state']
                and TF_RANK.get(d.get('tf', '15m'), 0) <= div_max_rank]
    rsi_bear = [d for d in divergences if 'BAJISTA' in d['type'] and 'ACTIVA' in d['state']
                and TF_RANK.get(d.get('tf', '15m'), 0) <= div_max_rank]

    fvg_above = [f for f in fvgs if f['center_price'] > price]
    fvg_below = [f for f in fvgs if f['center_price'] < price]

    # ── LONG scoring ──
    long_score = 0
    long_details = {}

    if supports:
        best = max(supports, key=lambda s: len(s.get('confluence', [])) * 10 + s.get('touches', 0))
        conf_count = len(best.get('confluence', []))
        if conf_count >= 3:
            long_score += 5
        elif conf_count >= 2:
            long_score += 4
        else:
            long_score += 3
        long_details['support'] = round(best['price_level'], 2)
        long_details['touches'] = best.get('touches', 0)
        long_details['confluence'] = best.get('confluence', [])

    if rsi_bull:
        bull_tfs = set(d.get('tf', '?') for d in rsi_bull)
        long_score += 4 if len(bull_tfs) >= 2 else 3
        long_details['rsi_div'] = list(bull_tfs)

    if fvg_above:
        best_fvg = max(fvg_above, key=lambda f: f.get('tf_rank', 0))
        long_score += 3 if best_fvg.get('tf_rank', 0) >= 2 else 2
        long_details['fvg_target'] = round(best_fvg['center_price'], 2)
        long_details['fvg_tf'] = best_fvg.get('tf', '?')

    if long_score >= 6:
        # Divergence gate
        if require_divergence == 'on' and not rsi_bull:
            pass  # Skip — no divergence
        else:
            target = fvg_above[0]['center_price'] if fvg_above else price * 1.03
            limit_price = long_details.get('support', price)
            signals.append({
                'type': 'LONG', 'score': min(10, long_score),
                'target': target, 'details': long_details,
                'limit_price': limit_price
            })

    # ── SHORT scoring ──
    short_score = 0
    short_details = {}

    if resistances:
        best = max(resistances, key=lambda r: len(r.get('confluence', [])) * 10 + r.get('touches', 0))
        conf_count = len(best.get('confluence', []))
        if conf_count >= 3:
            short_score += 5
        elif conf_count >= 2:
            short_score += 4
        else:
            short_score += 3
        short_details['resistance'] = round(best['price_level'], 2)
        short_details['touches'] = best.get('touches', 0)
        short_details['confluence'] = best.get('confluence', [])

    if rsi_bear:
        bear_tfs = set(d.get('tf', '?') for d in rsi_bear)
        short_score += 4 if len(bear_tfs) >= 2 else 3
        short_details['rsi_div'] = list(bear_tfs)

    if fvg_below:
        best_fvg = max(fvg_below, key=lambda f: f.get('tf_rank', 0))
        short_score += 3 if best_fvg.get('tf_rank', 0) >= 2 else 2
        short_details['fvg_target'] = round(best_fvg['center_price'], 2)
        short_details['fvg_tf'] = best_fvg.get('tf', '?')

    if short_score >= 6:
        # Divergence gate
        if require_divergence == 'on' and not rsi_bear:
            pass  # Skip — no divergence
        else:
            target = fvg_below[0]['center_price'] if fvg_below else price * 0.97
            limit_price = short_details.get('resistance', price)
            signals.append({
                'type': 'SHORT', 'score': min(10, short_score),
                'target': target, 'details': short_details,
                'limit_price': limit_price
            })

    return signals


# ──────────────────────────────────────────────────────────────
# Position Managers
# ──────────────────────────────────────────────────────────────

class CleanPosition:
    """Single entry with fixed TP/SL."""

    def __init__(self, type_, entry_price, entry_date, tp_pct, sl_pct, leverage, notional, score=0):
        self.type = type_
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.tp_pct = tp_pct / 100
        self.sl_pct = sl_pct / 100
        self.leverage = leverage
        self.notional = notional
        self.score = score
        self.closed = False
        self.exit_price = None
        self.exit_date = None
        self.exit_reason = None
        self.pnl_usd = 0.0

        # TP/SL prices
        if type_ == 'LONG':
            self.tp_price = entry_price * (1 + self.tp_pct / leverage)
            self.sl_price = entry_price * (1 - self.sl_pct / leverage)
        else:
            self.tp_price = entry_price * (1 - self.tp_pct / leverage)
            self.sl_price = entry_price * (1 + self.sl_pct / leverage)

    def check(self, candle):
        if self.closed:
            return True
        high, low = candle['high'], candle['low']
        if self.type == 'LONG':
            if low <= self.sl_price:
                self._close(self.sl_price, candle['timestamp'], 'SL')
            elif high >= self.tp_price:
                self._close(self.tp_price, candle['timestamp'], 'TP')
        else:
            if high >= self.sl_price:
                self._close(self.sl_price, candle['timestamp'], 'SL')
            elif low <= self.tp_price:
                self._close(self.tp_price, candle['timestamp'], 'TP')
        return self.closed

    def _close(self, exit_price, exit_ts, reason):
        self.closed = True
        self.exit_price = exit_price
        self.exit_date = ts_to_unix(exit_ts)
        self.exit_reason = reason
        if self.type == 'LONG':
            raw_pnl = (exit_price - self.entry_price) / self.entry_price
        else:
            raw_pnl = (self.entry_price - exit_price) / self.entry_price
        self.pnl_usd = self.notional * (raw_pnl * self.leverage - FEE_LIMIT * 2)

    def to_dict(self):
        return {
            'type': self.type, 'mode': 'clean',
            'entry_date': ts_to_unix(self.entry_date),
            'entry_price': round(self.entry_price, 2),
            'exit_date': self.exit_date,
            'exit_price': round(self.exit_price, 2) if self.exit_price else None,
            'exit_reason': self.exit_reason,
            'pnl_usd': round(self.pnl_usd, 4),
            'tp_price': round(self.tp_price, 2),
            'sl_price': round(self.sl_price, 2),
            'score': self.score
        }


class MartingalePosition:
    """
    DCA/Martingale position with N entries at increasing distances.
    TP/SL calculated from weighted average price.
    """

    def __init__(self, type_, first_price, first_date, tp_pct, sl_pct, leverage,
                 total_capital, entries_count, entry_distance_pct, entry_allocations, score=0):
        self.type = type_
        self.tp_pct = tp_pct / 100
        self.sl_pct = sl_pct / 100
        self.leverage = leverage
        self.total_capital = total_capital
        self.entries_count = entries_count
        self.entry_distance_pct = entry_distance_pct / 100  # Convert to decimal
        self.entry_allocations = entry_allocations  # List of fractions [0.25, 0.25, ...]
        self.score = score
        self.closed = False
        self.exit_price = None
        self.exit_date = None
        self.exit_reason = None
        self.pnl_usd = 0.0

        # Sub-entries tracking
        self.entries = []  # [{price, date, notional, alloc_pct}]
        self.next_entry_idx = 0
        self.avg_price = 0.0
        self.total_notional = 0.0

        # Add first entry
        self._add_entry(first_price, first_date)

    def _add_entry(self, price, date):
        if self.next_entry_idx >= self.entries_count:
            return

        alloc = self.entry_allocations[self.next_entry_idx]
        notional = self.total_capital * alloc

        self.entries.append({
            'price': round(price, 2),
            'date': ts_to_unix(date),
            'notional': round(notional, 2),
            'alloc_pct': round(alloc * 100, 1),
            'entry_num': self.next_entry_idx + 1
        })

        # Recalculate weighted average price
        self.total_notional = sum(e['notional'] for e in self.entries)
        self.avg_price = sum(e['price'] * e['notional'] for e in self.entries) / self.total_notional

        # Recalculate TP/SL from average price
        if self.type == 'LONG':
            self.tp_price = self.avg_price * (1 + self.tp_pct / self.leverage)
            self.sl_price = self.avg_price * (1 - self.sl_pct / self.leverage)
        else:
            self.tp_price = self.avg_price * (1 - self.tp_pct / self.leverage)
            self.sl_price = self.avg_price * (1 + self.sl_pct / self.leverage)

        self.next_entry_idx += 1

    def _get_next_entry_price(self):
        """Calculate the price at which the next DCA entry triggers."""
        if not self.entries:
            return None
        first_price = self.entries[0]['price']
        distance = self.entry_distance_pct * self.next_entry_idx

        if self.type == 'LONG':
            return first_price * (1 - distance)
        else:
            return first_price * (1 + distance)

    def check(self, candle):
        if self.closed:
            return True

        high, low = candle['high'], candle['low']
        price = candle['close']
        ts = candle['timestamp']

        # Check for new DCA entries BEFORE checking TP/SL
        if self.next_entry_idx < self.entries_count:
            next_price = self._get_next_entry_price()
            if next_price:
                if self.type == 'LONG' and low <= next_price:
                    self._add_entry(next_price, ts)
                elif self.type == 'SHORT' and high >= next_price:
                    self._add_entry(next_price, ts)

        # Check TP/SL on average price
        if self.type == 'LONG':
            if low <= self.sl_price:
                self._close(self.sl_price, ts, 'SL')
            elif high >= self.tp_price:
                self._close(self.tp_price, ts, 'TP')
        else:
            if high >= self.sl_price:
                self._close(self.sl_price, ts, 'SL')
            elif low <= self.tp_price:
                self._close(self.tp_price, ts, 'TP')

        return self.closed

    def _close(self, exit_price, exit_ts, reason):
        self.closed = True
        self.exit_price = exit_price
        self.exit_date = ts_to_unix(exit_ts)
        self.exit_reason = reason

        # PnL from average price
        if self.type == 'LONG':
            raw_pnl = (exit_price - self.avg_price) / self.avg_price
        else:
            raw_pnl = (self.avg_price - exit_price) / self.avg_price

        self.pnl_usd = self.total_notional * (raw_pnl * self.leverage - FEE_LIMIT * 2 * len(self.entries))

    def to_dict(self):
        return {
            'type': self.type, 'mode': 'martingale',
            'entry_date': self.entries[0]['date'] if self.entries else None,
            'entry_price': self.entries[0]['price'] if self.entries else None,
            'avg_price': round(self.avg_price, 2),
            'exit_date': self.exit_date,
            'exit_price': round(self.exit_price, 2) if self.exit_price else None,
            'exit_reason': self.exit_reason,
            'pnl_usd': round(self.pnl_usd, 4),
            'tp_price': round(self.tp_price, 2),
            'sl_price': round(self.sl_price, 2),
            'score': self.score,
            'entries_filled': len(self.entries),
            'entries_detail': self.entries,
            'total_notional': round(self.total_notional, 2)
        }


# ──────────────────────────────────────────────────────────────
# Main Engine
# ──────────────────────────────────────────────────────────────

def run_backtest(dataset_dir, tp_pct, sl_pct, leverage,
                 scan_interval=10, min_touches=3, mode='clean',
                 proximity_pct=3.0, require_divergence='off', divergence_max_tf='any',
                 # Martingale params
                 total_capital=500.0, entries_count=4,
                 entry_distance_pct=1.5, entry_allocations=None):
    """
    Run the V2 backtest engine with multi-TF confluence.

    Args:
        dataset_dir: Path to dataset directory containing TF CSVs
        tp_pct: Take profit %
        sl_pct: Stop loss %
        leverage: Leverage multiplier
        scan_interval: Run scanners every N candles
        min_touches: Min touches for S/R levels
        mode: 'clean' or 'martingale'
        total_capital: Total capital for Martingale
        entries_count: Number of DCA entries for Martingale
        entry_distance_pct: Distance % between entries
        entry_allocations: List of allocation fractions per entry
    """
    # Default equal allocations
    if entry_allocations is None:
        entry_allocations = [1.0 / entries_count] * entries_count

    # Normalize allocations to ensure they sum to 1.0
    total_alloc = sum(entry_allocations)
    entry_allocations = [a / total_alloc for a in entry_allocations]

    # Load data
    print(f"\n🚀 Backtesting V2 — {mode.upper()} mode")
    print(f"   TP: {tp_pct}% | SL: {sl_pct}% | Leverage: {leverage}x | Min Touches: {min_touches}")
    if mode == 'martingale':
        print(f"   Capital: ${total_capital} | Entries: {entries_count} | Distance: {entry_distance_pct}%")
        print(f"   Allocations: {[f'{a*100:.0f}%' for a in entry_allocations]}")

    datasets, meta = load_multi_tf_data(dataset_dir)

    if not datasets:
        return {'error': 'No datasets found in directory'}

    # Clock TF = the lowest available
    clock_tf = min(datasets.keys(), key=lambda t: TF_RANK.get(t, 99))
    clock_df = datasets[clock_tf]
    total_candles = len(clock_df)

    if total_candles < WARMUP_CANDLES + 50:
        return {'error': f'Not enough data. Need {WARMUP_CANDLES + 50} candles, got {total_candles}'}

    # Notional for clean mode
    clean_notional = total_capital if mode == 'clean' else total_capital

    # State
    trades = []
    open_positions = []
    pending_order = None  # {type, limit_price, score, details, expiry_idx}
    balance = 0.0
    max_balance = 0.0
    max_drawdown = 0.0
    last_close_idx = -999
    COOLDOWN_CANDLES = scan_interval
    ORDER_EXPIRY = scan_interval * 3  # Pending orders expire after 3 scan cycles

    # Scanner caches
    cached_sr = []
    cached_fvgs = []
    cached_divs = []

    sim_candles = total_candles - WARMUP_CANDLES
    print(f"\n   Reloj: {clock_tf} | Total: {total_candles} | Warmup: {WARMUP_CANDLES} | Simulando: {sim_candles} velas\n")

    for i in range(WARMUP_CANDLES, total_candles):
        candle = clock_df.iloc[i]
        price = candle['close']
        current_time = candle['timestamp']

        # 1. Check open positions
        still_open = []
        for pos in open_positions:
            was_closed = pos.check(candle)
            if was_closed:
                trades.append(pos.to_dict())
                balance += pos.pnl_usd
                max_balance = max(max_balance, balance)
                dd = max_balance - balance
                max_drawdown = max(max_drawdown, dd)
                last_close_idx = i  # Record when we closed
            else:
                still_open.append(pos)
        open_positions = still_open

        # 2. Run scanners periodically
        if i % scan_interval == 0:
            cached_sr = scan_sr_multi_tf(datasets, current_time)
            cached_fvgs = scan_fvg_multi_tf(datasets, current_time)
            cached_divs = scan_divergences_multi_tf(datasets, current_time)

            # Debug: log scanner results every 10 scan cycles
            if (i // scan_interval) % 10 == 0:
                sr_conf = [l for l in cached_sr if len(l.get('confluence', [])) >= 2]
                fvg_by_tf = {}
                for f in cached_fvgs:
                    tf = f.get('tf', '?')
                    fvg_by_tf[tf] = fvg_by_tf.get(tf, 0) + 1
                div_by_tf = {}
                for d in cached_divs:
                    tf = d.get('tf', '?')
                    div_by_tf[tf] = div_by_tf.get(tf, 0) + 1
                print(f"   [Scan {i}] SR: {len(cached_sr)} ({len(sr_conf)} multi-TF) | FVG: {fvg_by_tf or 'ninguno'} | RSI: {div_by_tf or 'ninguno'}")

        # 3. Check pending limit orders
        if pending_order and not open_positions:
            po = pending_order
            filled = False
            if po['type'] == 'LONG' and candle['low'] <= po['limit_price']:
                filled = True
                fill_price = po['limit_price']
            elif po['type'] == 'SHORT' and candle['high'] >= po['limit_price']:
                filled = True
                fill_price = po['limit_price']

            if filled:
                if mode == 'clean':
                    pos = CleanPosition(
                        type_=po['type'], entry_price=fill_price,
                        entry_date=current_time,
                        tp_pct=tp_pct, sl_pct=sl_pct,
                        leverage=leverage, notional=clean_notional,
                        score=po['score']
                    )
                else:
                    pos = MartingalePosition(
                        type_=po['type'], first_price=fill_price,
                        first_date=current_time,
                        tp_pct=tp_pct, sl_pct=sl_pct,
                        leverage=leverage,
                        total_capital=total_capital,
                        entries_count=entries_count,
                        entry_distance_pct=entry_distance_pct,
                        entry_allocations=entry_allocations,
                        score=po['score']
                    )
                open_positions.append(pos)
                details_str = ', '.join(f"{k}={v}" for k, v in po['details'].items())
                print(f"   ✅ FILLED {po['type']} @ ${fill_price:,.2f} (score={po['score']}) — {details_str}")
                pending_order = None
            elif i >= po.get('expiry_idx', i + 1):
                pending_order = None  # Expired

        # 4. Generate new pending limit orders (if no position and no pending)
        if not open_positions and not pending_order and (i - last_close_idx) >= COOLDOWN_CANDLES:
            signals = score_confluence(price, cached_sr, cached_fvgs, cached_divs,
                                       min_touches=min_touches,
                                       proximity_pct=proximity_pct,
                                       require_divergence=require_divergence,
                                       divergence_max_tf=divergence_max_tf)

            for sig in signals[:1]:
                pending_order = {
                    'type': sig['type'],
                    'limit_price': sig['limit_price'],
                    'score': sig['score'],
                    'details': sig['details'],
                    'expiry_idx': i + ORDER_EXPIRY
                }
                print(f"   � PENDING {sig['type']} limit @ ${sig['limit_price']:,.2f} (score={sig['score']})")

        # Progress
        if i % 500 == 0:
            pct = ((i - WARMUP_CANDLES) / sim_candles) * 100
            print(f"   {pct:.0f}% — Vela {i}/{total_candles} | Trades: {len(trades)} | Balance: ${balance:.2f}", end='\r')

    # Close remaining positions
    last_candle = clock_df.iloc[-1]
    for pos in open_positions:
        pos._close(last_candle['close'], last_candle['timestamp'], 'END')
        trades.append(pos.to_dict())
        balance += pos.pnl_usd

    # Metrics
    wins = [t for t in trades if t['pnl_usd'] > 0]
    losses = [t for t in trades if t['pnl_usd'] <= 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0
    total_profit = sum(t['pnl_usd'] for t in wins)
    total_loss = abs(sum(t['pnl_usd'] for t in losses))
    profit_factor = (total_profit / total_loss) if total_loss > 0 else 9999.99

    # Chart candles (downsample)
    step = max(1, len(clock_df) // 5000)
    chart_candles = []
    for _, row in clock_df.iloc[::step].iterrows():
        ts = row['timestamp']
        chart_candles.append({
            'time': int(ts.timestamp()) if hasattr(ts, 'timestamp') else int(pd.Timestamp(ts).timestamp()),
            'open': float(round(row['open'], 2)),
            'high': float(round(row['high'], 2)),
            'low': float(round(row['low'], 2)),
            'close': float(round(row['close'], 2))
        })

    # Sanitize trades to ensure JSON-safe types
    def sanitize(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize(v) for v in obj]
        return obj

    trades = sanitize(trades)

    result = {
        'metrics': {
            'win_rate': round(win_rate, 1),
            'pnl_total': round(float(balance), 2),
            'max_drawdown': round(float(max_drawdown), 2),
            'total_trades': len(trades),
            'wins': len(wins),
            'losses': len(losses),
            'profit_factor': round(float(profit_factor), 2),
            'avg_win': round(float(total_profit / len(wins)), 4) if wins else 0,
            'avg_loss': round(float(-total_loss / len(losses)), 4) if losses else 0,
            'mode': mode,
        },
        'trades': trades,
        'candles': chart_candles,
    }

    print(f"\n\n✅ Backtest completado!")
    print(f"   Mode: {mode.upper()} | Trades: {len(trades)} | Win Rate: {win_rate:.1f}%")
    print(f"   PnL: ${balance:.2f} | Max DD: ${max_drawdown:.2f} | Profit Factor: {profit_factor:.2f}")

    return result
