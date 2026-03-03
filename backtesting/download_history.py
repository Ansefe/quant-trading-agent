#!/usr/bin/env python3
"""
download_history.py — Descarga datos OHLCV históricos via CCXT y los guarda como CSV local.

Uso:
  python download_history.py --symbol BTC/USDT --timeframe 1h --days 365
  python download_history.py --symbol ETH/USDT --timeframe 4h --days 180
"""

import argparse
import os
import time
import ccxt
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

# Miliseconds per timeframe unit
TF_MS = {
    '1m': 60_000, '5m': 300_000, '15m': 900_000,
    '1h': 3_600_000, '4h': 14_400_000,
    '1d': 86_400_000, '1w': 604_800_000
}


def download(symbol, timeframe, days):
    os.makedirs(DATA_DIR, exist_ok=True)
    exchange = ccxt.binance({'enableRateLimit': True})

    tf_ms = TF_MS.get(timeframe, 3_600_000)
    since = exchange.milliseconds() - (days * 86_400_000)
    all_bars = []

    print(f"📥 Descargando {symbol} {timeframe} — últimos {days} días...")

    while True:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not bars:
            break
        all_bars.extend(bars)
        since = bars[-1][0] + tf_ms  # next page from last candle + 1
        print(f"   {len(all_bars)} velas descargadas...", end='\r')
        if len(bars) < 1000:
            break
        time.sleep(exchange.rateLimit / 1000)

    df = pd.DataFrame(all_bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df.drop_duplicates(subset='timestamp', inplace=True)
    df.sort_values('timestamp', inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Save
    safe_symbol = symbol.replace('/', '')
    filename = f"{safe_symbol}_{timeframe}_{days}d.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath, index=False)

    print(f"\n✅ {len(df)} velas guardadas en {filepath}")
    print(f"   Rango: {pd.to_datetime(df['timestamp'].iloc[0], unit='ms')} → {pd.to_datetime(df['timestamp'].iloc[-1], unit='ms')}")
    return filepath


def main():
    parser = argparse.ArgumentParser(description='Descarga datos OHLCV históricos')
    parser.add_argument('--symbol', type=str, default='BTC/USDT', help='Par de trading')
    parser.add_argument('--timeframe', type=str, default='1h', help='Temporalidad (1m, 5m, 15m, 1h, 4h, 1d, 1w)')
    parser.add_argument('--days', type=int, default=365, help='Días de historial a descargar')
    args = parser.parse_args()

    download(args.symbol, args.timeframe, args.days)


if __name__ == '__main__':
    main()
