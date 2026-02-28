import ccxt
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
import argparse

def fetch_ohlcv(symbol, timeframe, limit=300):
    exchange = ccxt.binance({'enableRateLimit': True})
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def calculate_rsi(series, period=14):
    delta = series.diff(1)
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
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

    # --- ESCÁNER BAJISTA CON LOOKBACK DINÁMICO ---
    for i in range(1, len(local_max)):
        idx_current = local_max[i]
        is_active = (current_idx - idx_current <= order + 2)
        
        if not historical and not is_active:
            continue
            
        # Filtramos los picos previos que estén dentro de la ventana de tiempo (ej. últimas 60 velas)
        valid_prev_peaks = [p for p in local_max[:i] if (idx_current - p) <= lookback_window]
        
        if not valid_prev_peaks: 
            continue
            
        # LA MAGIA: En lugar del pico anterior, buscamos el pico MÁS ALTO en esa ventana
        idx_major = max(valid_prev_peaks, key=lambda p: df['high'].iloc[p])
        
        # Comparamos el actual contra el titán histórico
        if df['high'].iloc[idx_current] > df['high'].iloc[idx_major] and df['rsi'].iloc[idx_current] < df['rsi'].iloc[idx_major]:
            estado = "ACTIVA 🔥" if is_active else "HISTÓRICA 🕰️"
            divergences.append({
                "estado": estado,
                "tipo": "🔴 BAJISTA (Macro)",
                "precio": df['high'].iloc[idx_current],
                "rsi": df['rsi'].iloc[idx_current],
                "fecha": df['timestamp'].iloc[idx_current].strftime('%Y-%m-%d %H:%M')
            })

    # --- ESCÁNER ALCISTA CON LOOKBACK DINÁMICO ---
    for i in range(1, len(local_min)):
        idx_current = local_min[i]
        is_active = (current_idx - idx_current <= order + 2)
        
        if not historical and not is_active:
            continue
            
        # Filtramos los valles previos dentro de la ventana
        valid_prev_valleys = [p for p in local_min[:i] if (idx_current - p) <= lookback_window]
        
        if not valid_prev_valleys: 
            continue
            
        # Buscamos el valle MÁS PROFUNDO en esa ventana
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

    # Evitamos duplicados y ordenamos por fecha más reciente
    unique_divs = {d['fecha']: d for d in divergences}.values()
    return sorted(unique_divs, key=lambda x: x['fecha'], reverse=True)

def scan_market(symbols, timeframes, historical):
    for symbol in symbols:
        print(f"\n--- 🔎 Radar RSI (Lookback Dinámico) para {symbol} ---")
        found_any = False
        
        for tf in timeframes:
            try:
                # Ajustamos el orden matemático según el TF para no tener falsos positivos
                order = 3 if tf in ['15m', '1h'] else 5
                df = fetch_ohlcv(symbol, tf)
                divs = check_divergences(df, order=order, historical=historical, lookback_window=60)
                
                if divs:
                    found_any = True
                    print(f"\n[{tf}] Resultados:")
                    for d in divs:
                        print(f"  {d['estado']} | {d['tipo']} el {d['fecha']} | Precio: ${d['precio']:,.2f} | RSI: {d['rsi']:,.1f}")
                    
            except Exception as e:
                print(f"Error procesando {tf}: {e}")
                
        if not found_any:
            print("✅ No hay divergencias macro detectadas con los parámetros actuales.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Escáner Quant de Divergencias RSI")
    parser.add_argument("--symbols", nargs="+", required=True, help="Lista de símbolos, ej: BTC/USDT ETH/USDT")
    parser.add_argument("--tfs", nargs="+", default=['15m', '1h', '4h', '1d'], help="Temporalidades a escanear")
    parser.add_argument("--historical", action="store_true", help="Muestra el backtesting histórico")
    
    args = parser.parse_args()
    scan_market(args.symbols, args.tfs, args.historical)
