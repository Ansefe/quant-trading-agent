#!/usr/bin/env python3
"""
download_history.py — Descarga datasets multi-TF para backtesting.

Un solo comando descarga TODAS las temporalidades (15m, 1h, 4h, 1d, 1w)
con sizing inteligente: TFs altos siempre descargan 500 velas,
TFs bajos descargan 500 warmup + velas de simulación.

Uso:
    python download_history.py --symbol BTC/USDT --days 30
"""

import os
import json
import time
import argparse
import ccxt
import pandas as pd
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

# Timeframes to download and their candle-per-day ratios
TIMEFRAMES = {
    '15m': {'candles_per_day': 96,  'min_download': 500},
    '1h':  {'candles_per_day': 24,  'min_download': 500},
    '4h':  {'candles_per_day': 6,   'min_download': 500},
    '1d':  {'candles_per_day': 1,   'min_download': 500},
    '1w':  {'candles_per_day': 1/7, 'min_download': 500},
}

# TF duration in milliseconds (for pagination)
TF_MS = {
    '15m': 15 * 60 * 1000,
    '1h':  60 * 60 * 1000,
    '4h':  4 * 60 * 60 * 1000,
    '1d':  24 * 60 * 60 * 1000,
    '1w':  7 * 24 * 60 * 60 * 1000,
}


def download_tf(exchange, symbol, timeframe, num_candles):
    """Download up to num_candles for a given TF with pagination."""
    all_data = []
    tf_ms = TF_MS[timeframe]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since = now_ms - (num_candles * tf_ms)
    limit_per_req = 1000  # Binance max

    while len(all_data) < num_candles:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit_per_req)
            if not ohlcv:
                break
            all_data.extend(ohlcv)
            since = ohlcv[-1][0] + tf_ms
            if len(ohlcv) < limit_per_req:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"      ⚠️ Error descargando {timeframe}: {e}")
            break

    if not all_data:
        return None

    df = pd.DataFrame(all_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df = df.drop_duplicates(subset='timestamp').sort_values('timestamp').reset_index(drop=True)
    return df


def main():
    parser = argparse.ArgumentParser(description='Descargar datasets multi-TF para backtesting')
    parser.add_argument('--symbol', default='BTC/USDT', help='Par de trading')
    parser.add_argument('--days', type=int, default=30, help='Días de simulación')
    args = parser.parse_args()

    symbol = args.symbol
    days = args.days
    safe_symbol = symbol.replace('/', '')

    # Create dataset directory
    dataset_dir = os.path.join(DATA_DIR, f"{safe_symbol}_{days}d")
    os.makedirs(dataset_dir, exist_ok=True)

    exchange = ccxt.binance({'enableRateLimit': True})

    print(f"\n📥 Descargando dataset multi-TF para {symbol} — {days} días de simulación\n")

    meta = {
        'symbol': symbol,
        'days': days,
        'timeframes': {},
        'downloaded_at': datetime.now(timezone.utc).isoformat()
    }

    for tf, cfg in TIMEFRAMES.items():
        # Calculate candles needed: warmup + simulation candles
        sim_candles = int(days * cfg['candles_per_day'])
        warmup = cfg['min_download']
        total_candles = warmup + sim_candles

        print(f"   📊 {tf:>3s}: descargando ~{total_candles} velas ({warmup} warmup + {sim_candles} sim)...", end=' ')

        df = download_tf(exchange, symbol, tf, total_candles)

        if df is not None and len(df) > 0:
            filepath = os.path.join(dataset_dir, f"{tf}.csv")
            df.to_csv(filepath, index=False)

            start_date = pd.to_datetime(df['timestamp'].iloc[0], unit='ms').strftime('%Y-%m-%d')
            end_date = pd.to_datetime(df['timestamp'].iloc[-1], unit='ms').strftime('%Y-%m-%d')

            meta['timeframes'][tf] = {
                'candles': len(df),
                'start': start_date,
                'end': end_date
            }
            print(f"✅ {len(df)} velas ({start_date} → {end_date})")
        else:
            print(f"❌ Sin datos")

        time.sleep(0.5)

    # Save metadata
    meta_path = os.path.join(dataset_dir, 'meta.json')
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"\n✅ Dataset completo guardado en: {dataset_dir}")
    print(f"   Archivos: {', '.join(tf + '.csv' for tf in meta['timeframes'])}")
    print(f"   Meta: meta.json\n")


if __name__ == '__main__':
    main()
