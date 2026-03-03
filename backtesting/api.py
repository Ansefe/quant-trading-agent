#!/usr/bin/env python3
"""
api.py — FastAPI server for the backtesting engine.

Endpoints:
  POST /run-backtest  — Execute a backtest with given parameters
  GET  /datasets      — List available downloaded datasets
"""

import os
import glob
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from engine import run_backtest

# Suppress noisy uvicorn access logs (GET /datasets polling)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

app = FastAPI(title="Quant Backtester API", version="1.0")

# Allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


class BacktestRequest(BaseModel):
    symbol: str = Field(default="BTC/USDT", description="Trading pair")
    timeframe: str = Field(default="1h", description="Timeframe")
    take_profit_pct: float = Field(default=2.0, description="Take profit %")
    stop_loss_pct: float = Field(default=1.0, description="Stop loss %")
    leverage: int = Field(default=5, description="Leverage multiplier")
    scan_interval: int = Field(default=10, description="Run scanners every N candles")
    dataset_file: str = Field(default="", description="Exact CSV filename to use")


@app.get("/datasets")
def list_datasets():
    """List available CSV datasets."""
    os.makedirs(DATA_DIR, exist_ok=True)
    files = glob.glob(os.path.join(DATA_DIR, '*.csv'))
    datasets = []
    for f in files:
        basename = os.path.basename(f)
        parts = basename.replace('.csv', '').split('_')
        if len(parts) >= 2:
            # e.g. BTCUSDT_1h.csv → symbol=BTCUSDT, tf=1h
            symbol_raw = parts[0]
            tf = parts[1]
            # Reconstruct pair format
            for base in ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'AVAX']:
                if symbol_raw.startswith(base):
                    symbol = f"{base}/{symbol_raw[len(base):]}"
                    break
            else:
                symbol = symbol_raw

            import pandas as pd
            try:
                df = pd.read_csv(f)
                candles = len(df)
                start = pd.to_datetime(df['timestamp'].iloc[0], unit='ms').strftime('%Y-%m-%d')
                end = pd.to_datetime(df['timestamp'].iloc[-1], unit='ms').strftime('%Y-%m-%d')
            except Exception:
                candles = 0
                start = end = '?'

            datasets.append({
                'symbol': symbol,
                'timeframe': tf,
                'candles': candles,
                'date_range': f"{start} → {end}",
                'file': basename
            })
    return datasets


@app.post("/run-backtest")
def run(req: BacktestRequest):
    """Execute a backtest."""
    # If exact file specified, use it directly
    if req.dataset_file:
        csv_path = os.path.join(DATA_DIR, req.dataset_file)
        if not os.path.exists(csv_path):
            return {'error': f'File not found: {req.dataset_file}'}
    else:
        # Fallback: glob match
        safe_symbol = req.symbol.replace('/', '')
        pattern = os.path.join(DATA_DIR, f"{safe_symbol}_{req.timeframe}*.csv")
        matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if not matches:
            return {
                'error': f'No dataset found for {req.symbol} {req.timeframe}. '
                         f'Run: python download_history.py --symbol {req.symbol} --timeframe {req.timeframe}'
            }
        csv_path = matches[0]

    print(f"\n🧪 Using dataset: {os.path.basename(csv_path)}")

    result = run_backtest(
        csv_path=csv_path,
        timeframe=req.timeframe,
        tp_pct=req.take_profit_pct,
        sl_pct=req.stop_loss_pct,
        leverage=req.leverage,
        scan_interval=req.scan_interval
    )

    return result


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8877)
