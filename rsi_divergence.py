import ccxt
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
import argparse

def fetch_ohlcv(symbol, timeframe, limit=300):
    exchange = ccxt.binance({'enableRateLimit': True})
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # Conversión de UTC a Hora Local (Colombia UTC-5)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('America/Bogota')
    
    return df

def calculate_rsi(series, period=14):
    """RSI using Wilder's RMA (matches TradingView / industry standard)."""
    delta = series.diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    # Wilder's smoothing: EMA with com=period-1 (equivalent to alpha=1/period)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def check_divergences(df, order=5, historical=False, lookback_window=60):
    """
    lookback_window=60: Miramos hasta 60 velas atrás para encontrar 
    el VERDADERO pico/valle institucional, ignorando el ruido del medio.
    """
    df['rsi'] = calculate_rsi(df['close'])
    df = df.dropna().reset_index(drop=True)

    local_max = argrelextrema(df['high'].values, np.greater_equal, order=order)[0]
    local_min = argrelextrema(df['low'].values, np.less_equal, order=order)[0]

    divergences = []
    current_idx = len(df) - 1

    # Detección Bajista (Oso)
    for i in range(1, len(local_max)):
        idx_current = local_max[i]
        is_active = (current_idx - idx_current <= order + 2)
        
        if not historical and not is_active:
            continue
            
        valid_prev_peaks = [p for p in local_max[:i] if (idx_current - p) <= lookback_window]
        if not valid_prev_peaks: continue
            
        idx_major = max(valid_prev_peaks, key=lambda p: df['high'].iloc[p])
        
        if df['high'].iloc[idx_current] > df['high'].iloc[idx_major] and df['rsi'].iloc[idx_current] < df['rsi'].iloc[idx_major]:
            estado = "ACTIVA 🔥" if is_active else "HISTÓRICA 🕰️"
            divergences.append({
                "estado": estado,
                "tipo": "🔴 BAJISTA (Macro)",
                "precio": df['high'].iloc[idx_current],
                "rsi": df['rsi'].iloc[idx_current],
                "fecha": df['timestamp'].iloc[idx_current].strftime('%Y-%m-%d %H:%M')
            })

    # Detección Alcista (Toro)
    for i in range(1, len(local_min)):
        idx_current = local_min[i]
        is_active = (current_idx - idx_current <= order + 2)
        
        if not historical and not is_active:
            continue
            
        valid_prev_valleys = [p for p in local_min[:i] if (idx_current - p) <= lookback_window]
        if not valid_prev_valleys: continue
            
        idx_major = min(valid_prev_valleys, key=lambda p: df['low'].iloc[p])
        
        if df['low'].iloc[idx_current] < df['low'].iloc[idx_major] and df['rsi'].iloc[idx_current] > df['rsi'].iloc[idx_major]:
            estado = "ACTIVA 🔥" if is_active else "HISTÓRICA 🕰️"
            divergences.append({
                "estado": estado,
                "tipo": "🟢 ALCISTA (Macro)",
                "precio": df['low'].iloc[idx_current],
                "rsi": df['rsi'].iloc[idx_current],
                "fecha": df['timestamp'].iloc[idx_current].strftime('%Y-%m-%d %H:%M')
            })

    unique_divs = {d['fecha']: d for d in divergences}.values()
    return sorted(unique_divs, key=lambda x: x['fecha'], reverse=True)

def scan_market(symbols, timeframes, historical):
    all_db_data = []
    for symbol in symbols:
        print(f"\n--- 🔎 Radar RSI (Lookback Dinámico) para {symbol} ---")
        found_any = False
        for tf in timeframes:
            try:
                order = 3 if tf in ['15m', '1h'] else 5
                df = fetch_ohlcv(symbol, tf)
                divs = check_divergences(df, order=order, historical=historical, lookback_window=60)
                
                if divs:
                    found_any = True
                    print(f"\n[{tf}] Resultados:")
                    for d in divs:
                        print(f"  {d['estado']} | {d['tipo']} el {d['fecha']} | Precio: ${d['precio']:,.2f} | RSI: {d['rsi']:,.1f}")
                        all_db_data.append({
                            "symbol": symbol,
                            "timeframe": tf,
                            "state": d['estado'],
                            "type": d['tipo'],
                            "price": float(d['precio']),
                            "rsi": float(d['rsi']),
                            "divergence_date": d['fecha']
                        })
            except Exception as e:
                print(f"Error procesando {tf}: {e}")
        if not found_any:
            print("✅ No hay divergencias macro detectadas con los parámetros actuales.")
            
    return all_db_data

if __name__ == "__main__":
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils.db import insert_rsi_divergences

    parser = argparse.ArgumentParser(description="Escáner Quant de Divergencias RSI")
    parser.add_argument("--symbols", nargs="+", required=True, help="Lista de símbolos, ej: BTC/USDT ETH/USDT")
    parser.add_argument("--tfs", nargs="+", default=['15m', '1h', '4h', '1d'], help="Temporalidades a escanear")
    parser.add_argument("--historical", action="store_true", help="Muestra el backtesting histórico")
    
    args = parser.parse_args()
    data = scan_market(args.symbols, args.tfs, args.historical)
    
    if data:
        insert_rsi_divergences(data)