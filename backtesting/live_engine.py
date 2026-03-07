#!/usr/bin/env python3
"""
live_engine.py — Motor de Paper Trading en tiempo real V2.

Flujo:
1. Warmup: Descarga velas históricas por cada TF vía ccxt
2. Conecta al WebSocket de Binance (kline_15m stream)
3. Por cada tick: actualiza vela, check TP/SL
4. Por cada cierre de vela: corre scanners, score_confluence, abre ordenes
5. Emite eventos a clientes Vue via FastAPI WS
"""

import os
import sys
import json
import asyncio
import logging
import time as _time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable

import numpy as np
import pandas as pd
import ccxt
import websockets

# Add parent dir for scanner imports
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PARENT_DIR)

from sr_scanner import get_fractal_extremes, cluster_levels, calculate_atr_pct
from smc_scanner import find_unmitigated_fvgs
from rsi_divergence import check_divergences, calculate_rsi
from elliott_scanner import scan_elliott_waves

logger = logging.getLogger("live_engine")

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────
FEE_LIMIT = 0.0002
FEE_MARKET = 0.0005

TF_RANK = {'5m': 0, '15m': 1, '1h': 2, '4h': 3, '1d': 4, '1w': 5}
TF_WEIGHTS = {'15m': 1, '1h': 2, '4h': 3, '1d': 4, '1w': 5}

# How many 15m candles make one higher-TF candle
TF_AGGREGATION = {'1h': 4, '4h': 16, '1d': 96, '1w': 672}

# Warmup candle counts per TF
WARMUP_LIMITS = {
    '5m': 500,   # Display only
    '15m': 500,
    '1h': 200,
    '4h': 100,
    '1d': 100,
    '1w': 50
}

# RSI activity window (hours)
RSI_ACTIVITY_HOURS = {'15m': 4, '1h': 12, '4h': 48, '1d': 168, '1w': 720}
ORDER_MAP = {'15m': 3, '1h': 3, '4h': 5, '1d': 5, '1w': 5}


# ──────────────────────────────────────────────────────────────
# Technical Indicators
# ──────────────────────────────────────────────────────────────
def compute_indicators(df):
    """Compute EMA(20,50,200) and RSI(14) for a DataFrame."""
    if len(df) < 20:
        return df

    df = df.copy()
    close = df['close']

    # EMAs
    df['ema_20'] = close.ewm(span=20, adjust=False).mean()
    df['ema_50'] = close.ewm(span=50, adjust=False).mean()
    if len(df) >= 200:
        df['ema_200'] = close.ewm(span=200, adjust=False).mean()

    # RSI
    df['rsi'] = calculate_rsi(close, period=14)

    return df


# ──────────────────────────────────────────────────────────────
# Scanner Functions (reuse from engine.py logic)
# ──────────────────────────────────────────────────────────────
def scan_sr_from_buffers(buffers, current_price=0):
    """Run SR scanner on all TF buffers, return merged levels."""
    all_supports = []
    all_resistances = []
    analysis_tfs = ['15m', '1h', '4h', '1d', '1w']

    for tf in analysis_tfs:
        df = buffers.get(tf)
        if df is None or len(df) < 30:
            continue
        try:
            order = 3 if tf in ['15m', '1h'] else 5
            supports, resistances = get_fractal_extremes(df, tf, order=order)
            all_supports.extend(supports)
            all_resistances.extend(resistances)
        except Exception as e:
            logger.warning(f"SR scan error on {tf}: {e}")

    # Cluster all levels together
    all_levels_raw = all_supports + all_resistances
    if not all_levels_raw:
        return []

    # Calculate ATR from 15m for threshold
    df_15m = buffers.get('15m')
    if df_15m is not None and len(df_15m) >= 20:
        atr_pct = calculate_atr_pct(df_15m)
        threshold = atr_pct * 0.5
    else:
        threshold = 0.005  # Default 0.5%

    # If no current_price provided, use latest close from 15m
    if current_price <= 0 and df_15m is not None and len(df_15m) > 0:
        current_price = float(df_15m['close'].iloc[-1])

    clustered = cluster_levels(all_levels_raw, threshold_pct=threshold)

    # Map field names from sr_scanner output to live_engine format
    merged = []
    for lvl in clustered:
        price = lvl.get('precio_linea', 0)
        # Standard market definition: below price = support, above = resistance
        is_support = price < current_price

        merged.append({
            'price_level': float(price),
            'is_support': is_support,
            'touches': lvl.get('toques', 0),
            'confluence': lvl.get('confluencia', []),
            'touches_by_tf': lvl.get('touches_by_tf', {})
        })

    return merged


def scan_fvg_from_buffers(buffers, current_price):
    """Run FVG scanner on all TF buffers."""
    all_fvgs = []
    for tf in ['15m', '1h', '4h', '1d', '1w']:
        df = buffers.get(tf)
        if df is None or len(df) < 5:
            continue
        try:
            fvgs = find_unmitigated_fvgs(df)
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


def scan_divergences_from_buffers(buffers, current_time):
    """Run RSI divergence scanner on all TF buffers."""
    all_divs = []
    for tf in ['15m', '1h', '4h', '1d']:
        df = buffers.get(tf)
        if df is None or len(df) < 30:
            continue
        try:
            order = ORDER_MAP.get(tf, 5)
            divs = check_divergences(df.copy(), order=order, historical=True, lookback_window=60)

            activity_hours = RSI_ACTIVITY_HOURS.get(tf, 24)
            activity_cutoff = current_time - pd.Timedelta(hours=activity_hours)

            for d in divs:
                try:
                    div_date = pd.to_datetime(d['fecha'], format='%Y-%m-%d %H:%M')
                    if hasattr(div_date, 'tz') and div_date.tz is not None:
                        activity_cutoff_tz = activity_cutoff
                    else:
                        activity_cutoff_tz = activity_cutoff.tz_localize(None) if hasattr(activity_cutoff, 'tz') and activity_cutoff.tz else activity_cutoff
                    if div_date >= activity_cutoff_tz:
                        all_divs.append({
                            'type': d['tipo'],
                            'state': 'ACTIVA 🔥',
                            'price': d['precio'],
                            'rsi': d['rsi'],
                            'tf': tf
                        })
                except Exception:
                    continue
        except Exception:
            continue
    return all_divs


def score_confluence_live(price, sr_levels, fvgs, divergences, config):
    """Score-based confluence with institutional filtering — LIVE version."""
    global_min_touches = config.get('global_min_touches', 3)
    mandatory_tfs = config.get('mandatory_tfs', ['1h'])
    min_touches_by_tf = config.get('min_touches_by_tf', {'4h': 1, '1h': 2})
    proximity_pct = config.get('proximity_pct', 1.0)
    require_divergence = config.get('require_divergence', 'off')
    divergence_max_tf = config.get('divergence_max_tf', 'any')

    proximity = proximity_pct / 100
    signals = []

    TF_DIV_RANK = {'15m': 0, '1h': 1, '4h': 2, '1d': 3, 'any': 99}
    div_max_rank = TF_DIV_RANK.get(divergence_max_tf, 99)

    # Hard filtering
    quality_levels = []
    for lvl in sr_levels:
        if lvl.get('touches', 0) < global_min_touches:
            continue
        confluence = lvl.get('confluence', [])
        if not all(tf in confluence for tf in mandatory_tfs):
            continue
        touches_by_tf = lvl.get('touches_by_tf', {})
        if not all(touches_by_tf.get(tf, 0) >= cnt for tf, cnt in min_touches_by_tf.items()):
            continue
        quality_levels.append(lvl)

    supports = [s for s in quality_levels if s['is_support'] and abs(price - s['price_level']) / price < proximity]
    resistances = [r for r in quality_levels if not r['is_support'] and abs(r['price_level'] - price) / price < proximity]

    rsi_bull = [d for d in divergences if 'ALCISTA' in d['type'] and 'ACTIVA' in d['state']
                and TF_RANK.get(d.get('tf', '15m'), 0) <= div_max_rank]
    rsi_bear = [d for d in divergences if 'BAJISTA' in d['type'] and 'ACTIVA' in d['state']
                and TF_RANK.get(d.get('tf', '15m'), 0) <= div_max_rank]

    fvg_above = [f for f in fvgs if f['center_price'] > price]
    fvg_below = [f for f in fvgs if f['center_price'] < price]

    # LONG scoring
    long_score = 0
    long_details = {}
    if supports:
        best = max(supports, key=lambda s: sum(TF_WEIGHTS.get(tf, 1) for tf in s.get('confluence', [])) * 100 + s.get('touches', 0))
        tf_weight = sum(TF_WEIGHTS.get(tf, 1) for tf in best.get('confluence', []))
        long_score += 5 if tf_weight >= 7 else (4 if tf_weight >= 4 else 3)
        # Bonus for high touch count (strong institutional level)
        touches = best.get('touches', 0)
        if touches >= 8:
            long_score += 2
        elif touches >= 5:
            long_score += 1
        long_details.update({
            'support': round(best['price_level'], 2),
            'touches': best.get('touches', 0),
            'touches_by_tf': best.get('touches_by_tf', {}),
            'confluence': best.get('confluence', []),
            'tf_weight': tf_weight
        })

    if rsi_bull:
        bull_tfs = set(d.get('tf', '?') for d in rsi_bull)
        long_score += 4 if len(bull_tfs) >= 2 else 3
        long_details['rsi_div'] = list(bull_tfs)

    if fvg_above:
        best_fvg = max(fvg_above, key=lambda f: f.get('tf_rank', 0))
        long_score += 3 if best_fvg.get('tf_rank', 0) >= 2 else 2
        long_details['fvg_target'] = round(best_fvg['center_price'], 2)
        long_details['fvg_tf'] = best_fvg.get('tf', '?')

    if long_score >= 4 and supports:
        if require_divergence == 'on' and not rsi_bull:
            pass
        else:
            signals.append({
                'type': 'LONG', 'score': min(10, long_score),
                'target': fvg_above[0]['center_price'] if fvg_above else price * 1.03,
                'details': long_details,
                'limit_price': long_details.get('support', price)
            })

    # SHORT scoring
    short_score = 0
    short_details = {}
    if resistances:
        best = max(resistances, key=lambda r: sum(TF_WEIGHTS.get(tf, 1) for tf in r.get('confluence', [])) * 100 + r.get('touches', 0))
        tf_weight = sum(TF_WEIGHTS.get(tf, 1) for tf in best.get('confluence', []))
        short_score += 5 if tf_weight >= 7 else (4 if tf_weight >= 4 else 3)
        # Bonus for high touch count (strong institutional level)
        touches = best.get('touches', 0)
        if touches >= 8:
            short_score += 2
        elif touches >= 5:
            short_score += 1
        short_details.update({
            'resistance': round(best['price_level'], 2),
            'touches': best.get('touches', 0),
            'touches_by_tf': best.get('touches_by_tf', {}),
            'confluence': best.get('confluence', []),
            'tf_weight': tf_weight
        })

    if rsi_bear:
        bear_tfs = set(d.get('tf', '?') for d in rsi_bear)
        short_score += 4 if len(bear_tfs) >= 2 else 3
        short_details['rsi_div'] = list(bear_tfs)

    if fvg_below:
        best_fvg = max(fvg_below, key=lambda f: f.get('tf_rank', 0))
        short_score += 3 if best_fvg.get('tf_rank', 0) >= 2 else 2
        short_details['fvg_target'] = round(best_fvg['center_price'], 2)
        short_details['fvg_tf'] = best_fvg.get('tf', '?')

    if short_score >= 4 and resistances:
        if require_divergence == 'on' and not rsi_bear:
            pass
        else:
            signals.append({
                'type': 'SHORT', 'score': min(10, short_score),
                'target': fvg_below[0]['center_price'] if fvg_below else price * 0.97,
                'details': short_details,
                'limit_price': short_details.get('resistance', price)
            })

    return signals


# ──────────────────────────────────────────────────────────────
# Live Paper Engine
# ──────────────────────────────────────────────────────────────
class LivePaperEngine:
    """Async engine: Binance WS → Scanners → Paper Trading."""

    def __init__(self):
        self.running = False
        self.symbol = 'BTCUSDT'
        self.ccxt_symbol = 'BTC/USDT'
        self.config = {}

        # Candle buffers per TF (DataFrames)
        self.buffers: Dict[str, pd.DataFrame] = {}

        # Current forming candle (15m)
        self.current_candle = None

        # Position state
        self.balance = 0.0
        self.initial_capital = 500.0
        self.open_position = None   # dict: {type, entry_price, entry_time, tp, sl, notional, score, details}
        self.pending_order = None   # dict: {type, limit_price, score, details}
        self.trade_history = []
        self.signal_history = []   # All signals ever generated, with config snapshot

        # Scanner caches
        self.cached_sr = []
        self.cached_fvgs = []
        self.cached_divs = []

        # Counters
        self._candle_count_15m = 0  # For aggregating higher TFs
        self._last_scan_count = 0

        # Event callback
        self._broadcast: Optional[Callable] = None

        # WebSocket connection
        self._ws = None
        self._task = None

    async def start(self, config: dict, broadcast_fn: Callable):
        """Start the live engine with given config."""
        if self.running:
            return {'status': 'already_running'}

        self.config = config
        self._broadcast = broadcast_fn
        self.symbol = config.get('symbol', 'BTCUSDT').replace('/', '').upper()
        self.ccxt_symbol = config.get('symbol', 'BTC/USDT')
        self.initial_capital = config.get('total_capital', 500.0)
        self.balance = 0.0
        self.open_position = None
        self.pending_order = None
        self.trade_history = []
        self.signal_history = []
        self.cached_sr = []
        self.cached_fvgs = []
        self.cached_divs = []
        self._candle_count_15m = 0
        self._last_scan_count = 0

        logger.info(f"🚀 Starting Live Paper Engine for {self.ccxt_symbol}")

        # 1. Warmup — download historical candles
        await self._warmup()

        # 2. Start Binance WS
        self.running = True
        self._task = asyncio.create_task(self._binance_ws_loop())

        await self._emit('engine_started', {
            'symbol': self.ccxt_symbol,
            'balance': self.balance,
            'initial_capital': self.initial_capital,
            'buffers': {tf: len(df) for tf, df in self.buffers.items()}
        })

        return {'status': 'started', 'symbol': self.ccxt_symbol}

    async def stop(self):
        """Stop the engine gracefully."""
        self.running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Close open position at current price
        if self.open_position:
            reason = 'MANUAL_STOP'
            self._close_position(self.current_candle.get('close', 0) if self.current_candle else 0, reason)

        await self._emit('engine_stopped', {
            'balance': self.balance,
            'trades': len(self.trade_history)
        })
        logger.info("🛑 Live Paper Engine stopped")
        return {'status': 'stopped'}

    def update_config(self, new_config: dict):
        """Hot-update config without stopping the engine."""
        # Only update safe params (not symbol — that requires restart)
        safe_keys = [
            'take_profit_pct', 'stop_loss_pct', 'leverage', 'total_capital',
            'global_min_touches', 'mandatory_tfs', 'min_touches_by_tf',
            'proximity_pct', 'require_divergence', 'divergence_max_tf',
            'scan_interval', 'mode', 'entries_count', 'entry_distance_pct'
        ]
        updated = []
        for k in safe_keys:
            if k in new_config and new_config[k] != self.config.get(k):
                self.config[k] = new_config[k]
                updated.append(k)

        if updated:
            logger.info(f"🔧 Config updated: {', '.join(updated)}")
        return {'status': 'updated', 'changed': updated}

    def get_status(self):
        """Return current engine state."""
        return {
            'running': self.running,
            'symbol': self.ccxt_symbol,
            'balance': round(self.balance, 2),
            'initial_capital': self.initial_capital,
            'open_position': self.open_position,
            'pending_order': self.pending_order,
            'trade_count': len(self.trade_history),
            'trade_history': self.trade_history[-20:],  # Last 20
            'signal_history': self.signal_history[-30:],  # Last 30
            'sr_count': len(self.cached_sr),
            'fvg_count': len(self.cached_fvgs),
            'div_count': len(self.cached_divs),
            'buffers': {tf: len(df) for tf, df in self.buffers.items()},
            'candle_count_15m': self._candle_count_15m
        }

    # ── Warmup ────────────────────────────────────────────
    async def _warmup(self):
        """Download historical candles for all TFs."""
        logger.info("📦 Warming up — downloading historical candles...")
        await self._emit('status', {'message': 'Descargando velas históricas...'})

        exchange = ccxt.binance({'enableRateLimit': True})

        for tf, limit in WARMUP_LIMITS.items():
            try:
                bars = exchange.fetch_ohlcv(self.ccxt_symbol, timeframe=tf, limit=limit)
                df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                self.buffers[tf] = df
                logger.info(f"   ✅ {tf}: {len(df)} velas cargadas")
                await self._emit('status', {'message': f'Warmup {tf}: {len(df)} velas'})
                await asyncio.sleep(0.3)  # Rate limit
            except Exception as e:
                logger.error(f"   ❌ Error warmup {tf}: {e}")
                self.buffers[tf] = pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # Compute indicators on warmup data
        for tf in self.buffers:
            if len(self.buffers[tf]) >= 20:
                self.buffers[tf] = compute_indicators(self.buffers[tf])

        # Run initial scan
        await self._run_scanners()

        logger.info("✅ Warmup complete")
        await self._emit('status', {'message': 'Warmup completado — conectando a Binance...'})

    # ── Binance WebSocket ─────────────────────────────────
    async def _binance_ws_loop(self):
        """Connect to Binance kline WS and process ticks."""
        symbol_lower = self.symbol.lower()
        url = f"wss://stream.binance.com:9443/ws/{symbol_lower}@kline_15m"

        while self.running:
            try:
                logger.info(f"🔌 Connecting to Binance WS: {url}")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    await self._emit('status', {'message': 'Conectado a Binance — recibiendo datos en vivo'})

                    async for message in ws:
                        if not self.running:
                            break
                        try:
                            data = json.loads(message)
                            kline = data.get('k', {})
                            await self._process_kline(kline)
                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logger.error(f"Error processing kline: {e}")

            except websockets.ConnectionClosed:
                if self.running:
                    logger.warning("WS disconnected, reconnecting in 5s...")
                    await asyncio.sleep(5)
            except Exception as e:
                if self.running:
                    logger.error(f"WS error: {e}, reconnecting in 5s...")
                    await asyncio.sleep(5)

    async def _process_kline(self, kline):
        """Process a kline tick from Binance WS."""
        ts = int(kline['t'])
        o = float(kline['o'])
        h = float(kline['h'])
        l = float(kline['l'])
        c = float(kline['c'])
        v = float(kline['v'])
        is_closed = kline.get('x', False)

        self.current_candle = {
            'timestamp': ts,
            'open': o, 'high': h, 'low': l, 'close': c,
            'volume': v, 'is_closed': is_closed
        }

        # Emit candle update (for live chart)
        await self._emit('candle', {
            'time': ts // 1000,  # Lightweight charts expects seconds
            'open': o, 'high': h, 'low': l, 'close': c
        })

        # Check TP/SL on every tick
        if self.open_position:
            await self._check_tp_sl(h, l, c, ts)

        # Check pending order fill
        if self.pending_order and not self.open_position:
            await self._check_pending_fill(h, l, c, ts)

        # On candle close → scanners + confluence
        if is_closed:
            await self._on_candle_close(ts, o, h, l, c, v)

    async def _on_candle_close(self, ts, o, h, l, c, v):
        """Process a closed 15m candle."""
        self._candle_count_15m += 1
        close_time = pd.Timestamp(ts, unit='ms')

        # Append to 15m buffer
        new_row = pd.DataFrame([{
            'timestamp': close_time,
            'open': o, 'high': h, 'low': l, 'close': c, 'volume': v
        }])
        self.buffers['15m'] = pd.concat([self.buffers['15m'], new_row], ignore_index=True)

        # Keep buffer manageable (max 2000 candles per TF)
        for tf in self.buffers:
            if len(self.buffers[tf]) > 2000:
                self.buffers[tf] = self.buffers[tf].iloc[-1500:].reset_index(drop=True)

        # Aggregate higher TFs
        self._aggregate_higher_tfs(close_time, o, h, l, c, v)

        # Recompute indicators for 15m
        if len(self.buffers['15m']) >= 20:
            self.buffers['15m'] = compute_indicators(self.buffers['15m'])

        # Run scanners every 3 candle closes (~45 min)
        scan_interval = self.config.get('scan_interval', 3)
        if self._candle_count_15m % scan_interval == 0:
            await self._run_scanners()

            # Generate signals
            if not self.open_position and not self.pending_order:
                signals = score_confluence_live(c, self.cached_sr, self.cached_fvgs, self.cached_divs, self.config)
                if signals:
                    sig = signals[0]  # Best signal
                    self.pending_order = {
                        'type': sig['type'],
                        'limit_price': sig['limit_price'],
                        'score': sig['score'],
                        'details': sig['details'],
                        'created_at': ts
                    }

                    # Build serializable details
                    safe_details = {k: (v if not isinstance(v, (np.integer, np.floating)) else float(v))
                                   for k, v in sig['details'].items()}

                    # Store in signal history with config snapshot
                    signal_record = {
                        'type': sig['type'],
                        'limit_price': sig['limit_price'],
                        'score': sig['score'],
                        'details': safe_details,
                        'price_at_signal': c,
                        'time': ts // 1000,
                        'candle_num': self._candle_count_15m,
                        'config_snapshot': {
                            'tp_pct': self.config.get('take_profit_pct'),
                            'sl_pct': self.config.get('stop_loss_pct'),
                            'leverage': self.config.get('leverage'),
                            'mode': self.config.get('mode', 'clean'),
                            'global_min_touches': self.config.get('global_min_touches'),
                            'mandatory_tfs': self.config.get('mandatory_tfs', []),
                            'proximity_pct': self.config.get('proximity_pct'),
                            'require_div': self.config.get('require_divergence'),
                        },
                        'status': 'PENDING'  # PENDING → FILLED / EXPIRED
                    }
                    self.signal_history.append(signal_record)

                    logger.info(f"📋 PENDING {sig['type']} limit @ ${sig['limit_price']:,.2f} (score={sig['score']})")
                    await self._emit('signal', signal_record)

        # Emit balance update
        open_pnl = 0
        if self.open_position:
            pos = self.open_position
            if pos['type'] == 'LONG':
                open_pnl = (c - pos['entry_price']) / pos['entry_price'] * pos['notional'] * pos['leverage']
            else:
                open_pnl = (pos['entry_price'] - c) / pos['entry_price'] * pos['notional'] * pos['leverage']

        await self._emit('balance', {
            'balance': round(self.balance, 2),
            'open_pnl': round(open_pnl, 2),
            'total': round(self.balance + open_pnl, 2),
            'trades': len(self.trade_history)
        })

    def _aggregate_higher_tfs(self, close_time, o, h, l, c, v):
        """Aggregate 15m candles into higher TFs."""
        for tf, period in TF_AGGREGATION.items():
            if self._candle_count_15m % period != 0:
                # Update the current forming candle for this TF
                if tf in self.buffers and len(self.buffers[tf]) > 0:
                    last = self.buffers[tf].iloc[-1]
                    # Only update if this candle is still forming
                    # (we detect by checking if a full period hasn't elapsed)
                    pass
                continue

            # Full period elapsed — build a new candle from the last N 15m candles
            buf_15m = self.buffers.get('15m')
            if buf_15m is None or len(buf_15m) < period:
                continue

            chunk = buf_15m.iloc[-period:]
            new_candle = pd.DataFrame([{
                'timestamp': close_time,
                'open': chunk['open'].iloc[0],
                'high': chunk['high'].max(),
                'low': chunk['low'].min(),
                'close': chunk['close'].iloc[-1],
                'volume': chunk['volume'].sum()
            }])
            self.buffers[tf] = pd.concat([self.buffers[tf], new_candle], ignore_index=True)

            # Recompute indicators for this TF
            if len(self.buffers[tf]) >= 20:
                self.buffers[tf] = compute_indicators(self.buffers[tf])

    # ── Scanners ──────────────────────────────────────────
    async def _run_scanners(self):
        """Run all scanners on current buffers."""
        loop = asyncio.get_event_loop()

        # Run scanners in executor (they are CPU-bound)
        current_price = self.current_candle['close'] if self.current_candle else 0
        self.cached_sr = await loop.run_in_executor(
            None, lambda: scan_sr_from_buffers(self.buffers, current_price))
        self.cached_fvgs = await loop.run_in_executor(None, scan_fvg_from_buffers, self.buffers, current_price)

        current_time = pd.Timestamp.now()
        self.cached_divs = await loop.run_in_executor(None, scan_divergences_from_buffers, self.buffers, current_time)

        # ── Multi-TF Elliott Wave Scan ──
        elliott_results = {}
        for tf in ['15m', '1h', '4h', '1d', '1w']:
            if tf in self.buffers and len(self.buffers[tf]) > 50:
                df_tf = self.buffers[tf]
                payload = await loop.run_in_executor(
                    None, lambda d=df_tf: scan_elliott_waves(d, current_price, atr_multiplier=1.8))
                
                if payload:
                    elliott_results[tf] = payload

        if elliott_results:
            await self._emit('elliott_wave_update', elliott_results)

        sr_multi = len([l for l in self.cached_sr if len(l.get('confluence', [])) >= 2])
        logger.info(f"   📊 Scan #{self._candle_count_15m}: SR={len(self.cached_sr)} ({sr_multi} multi-TF) | FVG={len(self.cached_fvgs)} | DIV={len(self.cached_divs)}")

        await self._emit('scan_result', {
            'sr_count': len(self.cached_sr),
            'sr_multi_tf': sr_multi,
            'fvg_count': len(self.cached_fvgs),
            'div_count': len(self.cached_divs),
            'sr_levels': self.cached_sr[:50]  # Send levels for visualization
        })

    # ── Position Management ───────────────────────────────
    async def _check_pending_fill(self, high, low, price, ts):
        """Check if pending limit order should fill."""
        po = self.pending_order
        filled = False
        fill_price = po['limit_price']

        if po['type'] == 'LONG' and low <= fill_price:
            filled = True
        elif po['type'] == 'SHORT' and high >= fill_price:
            filled = True

        # Expire after ~3 hours (12 candles of 15m)
        age_ms = ts - po.get('created_at', ts)
        if age_ms > 3 * 60 * 60 * 1000:
            self.pending_order = None
            await self._emit('order_expired', {'type': po['type'], 'limit_price': fill_price})
            return

        if filled:
            tp_pct = self.config.get('take_profit_pct', 2.0) / 100
            sl_pct = self.config.get('stop_loss_pct', 1.0) / 100
            leverage = self.config.get('leverage', 5)

            if po['type'] == 'LONG':
                tp_price = fill_price * (1 + tp_pct)
                sl_price = fill_price * (1 - sl_pct)
            else:
                tp_price = fill_price * (1 - tp_pct)
                sl_price = fill_price * (1 + sl_pct)

            self.open_position = {
                'type': po['type'],
                'entry_price': fill_price,
                'entry_time': ts,
                'tp': round(tp_price, 2),
                'sl': round(sl_price, 2),
                'notional': self.initial_capital,
                'leverage': leverage,
                'score': po['score'],
                'details': po['details']
            }
            self.pending_order = None

            logger.info(f"✅ FILLED {po['type']} @ ${fill_price:,.2f} | TP: ${tp_price:,.2f} | SL: ${sl_price:,.2f}")
            await self._emit('trade_open', {
                'type': po['type'],
                'entry_price': fill_price,
                'tp': round(tp_price, 2),
                'sl': round(sl_price, 2),
                'score': po['score'],
                'time': ts // 1000
            })

    async def _check_tp_sl(self, high, low, price, ts):
        """Check if open position should close."""
        pos = self.open_position
        if not pos:
            return

        closed = False
        exit_price = 0
        reason = ''

        if pos['type'] == 'LONG':
            if high >= pos['tp']:
                closed, exit_price, reason = True, pos['tp'], 'TP'
            elif low <= pos['sl']:
                closed, exit_price, reason = True, pos['sl'], 'SL'
        else:  # SHORT
            if low <= pos['tp']:
                closed, exit_price, reason = True, pos['tp'], 'TP'
            elif high >= pos['sl']:
                closed, exit_price, reason = True, pos['sl'], 'SL'

        if closed:
            self._close_position(exit_price, reason, ts)

    def _close_position(self, exit_price, reason, ts=None):
        """Close the open position and record trade."""
        pos = self.open_position
        if not pos:
            return

        if pos['type'] == 'LONG':
            pnl_pct = (exit_price - pos['entry_price']) / pos['entry_price']
        else:
            pnl_pct = (pos['entry_price'] - exit_price) / pos['entry_price']

        pnl_usd = pnl_pct * pos['notional'] * pos['leverage']
        # Subtract fees
        pnl_usd -= pos['notional'] * pos['leverage'] * FEE_LIMIT * 2

        self.balance += pnl_usd

        trade = {
            'type': pos['type'],
            'entry_price': pos['entry_price'],
            'entry_time': pos.get('entry_time'),
            'exit_price': round(exit_price, 2),
            'exit_time': ts,
            'exit_reason': reason,
            'pnl_usd': round(pnl_usd, 2),
            'pnl_pct': round(pnl_pct * 100, 2),
            'score': pos.get('score', 0)
        }
        self.trade_history.append(trade)
        self.open_position = None

        icon = '🟢' if pnl_usd >= 0 else '🔴'
        logger.info(f"{icon} CLOSED {pos['type']} @ ${exit_price:,.2f} ({reason}) | PnL: ${pnl_usd:+.2f} | Balance: ${self.balance:.2f}")

        # Async emit — schedule it
        if self._broadcast:
            asyncio.ensure_future(self._emit('trade_close', {
                **trade,
                'balance': round(self.balance, 2),
                'time': (ts // 1000) if ts else int(_time.time())
            }))

    # ── Event Emitter ─────────────────────────────────────
    async def _emit(self, event_type: str, data: dict):
        """Broadcast event to connected WebSocket clients."""
        if self._broadcast:
            try:
                await self._broadcast(json.dumps({
                    'type': event_type,
                    'data': data,
                    'timestamp': int(_time.time() * 1000)
                }, default=str))
            except Exception as e:
                logger.warning(f"Broadcast error: {e}")

    # ── Buffer Access for Chart ───────────────────────────
    def _ts_to_seconds(self, ts):
        """Convert any timestamp format to UTC seconds for lightweight-charts."""
        if hasattr(ts, 'value'):
            # pd.Timestamp — .value is nanoseconds since epoch
            return int(ts.value // 10**9)
        elif isinstance(ts, (int, np.integer)):
            # Already ms or seconds
            return int(ts) // 1000 if ts > 1e12 else int(ts)
        elif isinstance(ts, float):
            return int(ts) if ts < 1e12 else int(ts / 1000)
        return int(ts)

    def get_candles(self, tf='15m', limit=500):
        """Get candle data for a specific TF (for chart rendering)."""
        df = self.buffers.get(tf)
        if df is None or len(df) == 0:
            return []

        df_slice = df.tail(limit)
        candles = []
        for _, row in df_slice.iterrows():
            candle = {
                'time': self._ts_to_seconds(row['timestamp']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close'])
            }
            candles.append(candle)

        return candles

    def get_indicators(self, tf='15m', limit=500):
        """Get indicator data for a specific TF."""
        df = self.buffers.get(tf)
        if df is None or len(df) == 0:
            return {'ema_20': [], 'ema_50': [], 'ema_200': [], 'rsi': []}

        df_slice = df.tail(limit)
        result = {'ema_20': [], 'ema_50': [], 'ema_200': [], 'rsi': []}

        for _, row in df_slice.iterrows():
            t = self._ts_to_seconds(row['timestamp'])
            if 'ema_20' in df_slice.columns and pd.notna(row.get('ema_20')):
                result['ema_20'].append({'time': t, 'value': round(float(row['ema_20']), 2)})
            if 'ema_50' in df_slice.columns and pd.notna(row.get('ema_50')):
                result['ema_50'].append({'time': t, 'value': round(float(row['ema_50']), 2)})
            if 'ema_200' in df_slice.columns and pd.notna(row.get('ema_200')):
                result['ema_200'].append({'time': t, 'value': round(float(row['ema_200']), 2)})
            if 'rsi' in df_slice.columns and pd.notna(row.get('rsi')):
                result['rsi'].append({'time': t, 'value': round(float(row['rsi']), 2)})

        return result


# Singleton instance
engine_instance = LivePaperEngine()
