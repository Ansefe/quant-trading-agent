import ccxt
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
import argparse

def fetch_ohlcv(symbol, timeframe, limit=300): # Aumentamos el límite para mejor backtesting
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

def check_divergences(df, order=5, historical=False):
    df['rsi'] = calculate_rsi(df['close'])
    df = df.dropna().reset_index(drop=True)

    local_max = argrelextrema(df['high'].values, np.greater_equal, order=order)[0]
    local_min = argrelextrema(df['low'].values, np.less_equal, order=order)[0]

    divergences = []
    current_idx = len(df) - 1

    # Escáner de Divergencias Bajistas (Oso)
    for i in range(len(local_max) - 1):
        idx1, idx2 = local_max[i], local_max[i+1]
        is_active = (current_idx - idx2 <= order + 2)
        
        # Si no queremos el histórico y la divergencia ya expiró, la saltamos
        if not historical and not is_active:
            continue
            
        if df['high'].iloc[idx2] > df['high'].iloc[idx1] and df['rsi'].iloc[idx2] < df['rsi'].iloc[idx1]:
            estado = "ACTIVA 🔥" if is_active else "HISTÓRICA 🕰️"
            divergences.append({
                "estado": estado,
                "tipo": "🔴 BAJISTA",
                "precio": df['high'].iloc[idx2],
                "rsi": df['rsi'].iloc[idx2],
                "fecha": df['timestamp'].iloc[idx2].strftime('%Y-%m-%d %H:%M')
            })

    # Escáner de Divergencias Alcistas (Toro)
    for i in range(len(local_min) - 1):
        idx1, idx2 = local_min[i], local_min[i+1]
        is_active = (current_idx - idx2 <= order + 2)
        
        if not historical and not is_active:
            continue
            
        if df['low'].iloc[idx2] < df['low'].iloc[idx1] and df['rsi'].iloc[idx2] > df['rsi'].iloc[idx1]:
            estado = "ACTIVA 🔥" if is_active else "HISTÓRICA 🕰️"
            divergences.append({
                "estado": estado,
                "tipo": "🟢 ALCISTA",
                "precio": df['low'].iloc[idx2],
                "rsi": df['rsi'].iloc[idx2],
                "fecha": df['timestamp'].iloc[idx2].strftime('%Y-%m-%d %H:%M')
            })

    # Ordenar por fecha más reciente
    return sorted(divergences, key=lambda x: x['fecha'], reverse=True)

def scan_market(symbols, timeframes, historical):
    for symbol in symbols:
        print(f"\n--- 🔎 Radar RSI para {symbol} ---")
        found_any = False
        
        for tf in timeframes:
            try:
                order = 3 if tf in ['15m', '1h'] else 5
                df = fetch_ohlcv(symbol, tf)
                divs = check_divergences(df, order=order, historical=historical)
                
                if divs:
                    found_any = True
                    print(f"\n[{tf}] Resultados:")
                    for d in divs:
                        print(f"  {d['estado']} | {d['tipo']} el {d['fecha']} | Precio: ${d['precio']:,.2f} | RSI: {d['rsi']:,.1f}")
                    
            except Exception as e:
                print(f"Error procesando {tf}: {e}")
                
        if not found_any:
            print("✅ No hay divergencias detectadas con los parámetros actuales.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Escáner Quant de Divergencias RSI")
    parser.add_argument("--symbols", nargs="+", required=True, help="Lista de símbolos, ej: BTC/USDT ETH/USDT")
    parser.add_argument("--tfs", nargs="+", default=['15m', '1h', '4h', '1d'], help="Temporalidades a escanear")
    parser.add_argument("--historical", action="store_true", help="Si se incluye, muestra el backtesting histórico")
    
    args = parser.parse_args()
    scan_market(args.symbols, args.tfs, args.historical)
