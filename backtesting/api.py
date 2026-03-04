#!/usr/bin/env python3
"""
api.py — FastAPI server for the V2 backtesting engine.

Endpoints:
  POST /run-backtest  — Execute a backtest with given parameters
  GET  /datasets      — List available multi-TF dataset directories
"""

import os
import json
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional

from engine import run_backtest

# Suppress noisy uvicorn access logs
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

app = FastAPI(title="Quant Backtester API V2", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


class BacktestRequest(BaseModel):
    dataset_dir: str = Field(description="Dataset directory name (e.g. BTCUSDT_30d)")
    take_profit_pct: float = Field(default=2.0, description="Take profit %")
    stop_loss_pct: float = Field(default=1.0, description="Stop loss %")
    leverage: int = Field(default=5, description="Leverage multiplier")
    scan_interval: int = Field(default=10, description="Scanners every N candles")
    min_touches: int = Field(default=3, description="Minimum S/R touches")
    proximity_pct: float = Field(default=1.0, description="Max distance % to consider S/R level")
    require_divergence: str = Field(default="off", description="'off' or 'on'")
    divergence_max_tf: str = Field(default="any", description="Max TF for divergence: 15m, 1h, 4h, 1d, any")
    mode: str = Field(default="clean", description="'clean' or 'martingale'")
    # Martingale params
    total_capital: float = Field(default=500.0, description="Total capital USD")
    entries_count: int = Field(default=4, description="Number of DCA entries")
    entry_distance_pct: float = Field(default=1.5, description="Distance % between entries")
    entry_allocations: Optional[List[float]] = Field(default=None, description="Allocation % per entry")


@app.get("/datasets")
def list_datasets():
    """List available multi-TF dataset directories."""
    os.makedirs(DATA_DIR, exist_ok=True)
    datasets = []

    for name in sorted(os.listdir(DATA_DIR)):
        dir_path = os.path.join(DATA_DIR, name)
        if not os.path.isdir(dir_path):
            continue

        meta_path = os.path.join(dir_path, 'meta.json')
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)

                # Count total candles and get date range from clock TF
                tfs = meta.get('timeframes', {})
                tf_list = list(tfs.keys())
                total_candles = sum(t.get('candles', 0) for t in tfs.values())

                # Get simulation range from lowest TF
                clock_tf = '15m' if '15m' in tfs else (tf_list[0] if tf_list else '?')
                clock_info = tfs.get(clock_tf, {})

                datasets.append({
                    'name': name,
                    'symbol': meta.get('symbol', '?'),
                    'days': meta.get('days', '?'),
                    'timeframes': tf_list,
                    'total_candles': total_candles,
                    'clock_tf': clock_tf,
                    'date_range': f"{clock_info.get('start', '?')} → {clock_info.get('end', '?')}",
                    'downloaded_at': meta.get('downloaded_at', '?')
                })
            except Exception:
                datasets.append({
                    'name': name, 'symbol': '?', 'days': '?',
                    'timeframes': [], 'total_candles': 0,
                    'clock_tf': '?', 'date_range': '?',
                    'downloaded_at': '?'
                })

    return datasets


@app.post("/run-backtest")
def run(req: BacktestRequest):
    """Execute a V2 backtest."""
    dataset_path = os.path.join(DATA_DIR, req.dataset_dir)

    if not os.path.isdir(dataset_path):
        return {'error': f'Dataset directory not found: {req.dataset_dir}. Run download_history.py first.'}

    # Parse allocations
    allocations = None
    if req.entry_allocations:
        # Convert percentages to fractions if needed
        allocs = req.entry_allocations
        if any(a > 1 for a in allocs):
            allocs = [a / 100 for a in allocs]
        allocations = allocs

    print(f"\n🧪 Starting backtest: {req.dataset_dir} — {req.mode.upper()} mode")

    result = run_backtest(
        dataset_dir=dataset_path,
        tp_pct=req.take_profit_pct,
        sl_pct=req.stop_loss_pct,
        leverage=req.leverage,
        scan_interval=req.scan_interval,
        min_touches=req.min_touches,
        mode=req.mode,
        proximity_pct=req.proximity_pct,
        require_divergence=req.require_divergence,
        divergence_max_tf=req.divergence_max_tf,
        total_capital=req.total_capital,
        entries_count=req.entries_count,
        entry_distance_pct=req.entry_distance_pct,
        entry_allocations=allocations
    )

    return result


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8877)
