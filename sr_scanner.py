import ccxt
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
import argparse

def fetch_ohlcv(symbol, timeframe, limit=1000):
    exchange = ccxt.binance({'enableRateLimit': True})
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # Conversión de UTC a Hora Local (Colombia UTC-5)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('America/Bogota')
    return df

def calculate_atr_pct(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    
    true_range = ranges.max(axis=1)
    atr = true_range.rolling(period).mean()
    atr_pct = atr.iloc[-1] / df['close'].iloc[-1]
    return atr_pct

def get_fractal_extremes(df, tf, order=10):
    local_max = argrelextrema(df['high'].values, np.greater_equal, order=order)[0]
    local_min = argrelextrema(df['low'].values, np.less_equal, order=order)[0]

    resistances = [(df['high'].iloc[i], tf) for i in local_max]
    supports = [(df['low'].iloc[i], tf) for i in local_min]
    return supports, resistances

def cluster_levels(levels, threshold_pct):
    if not levels: return []
    
    levels = sorted(levels, key=lambda x: x[0])
    clusters = []
    current_cluster = [levels[0]]

    for level in levels[1:]:
        precio_actual = level[0]
        mean_c = np.mean([item[0] for item in current_cluster])
        
        if abs(precio_actual - mean_c) / mean_c <= threshold_pct:
            current_cluster.append(level)
        else:
            clusters.append(current_cluster)
            current_cluster = [level]
    clusters.append(current_cluster)

    final_levels = []
    for c in clusters:
        if len(c) >= 2:
            precios = [item[0] for item in c]
            temporalidades = list(set([item[1] for item in c]))
            min_price = np.min(precios)
            max_price = np.max(precios)
            width_pct = ((max_price - min_price) / min_price) * 100
            
            final_levels.append({
                'precio_linea': np.mean(precios),
                'toques': len(c),
                'confluencia': temporalidades,
                'grosor_pct': width_pct,
                'tipo_zona': "Línea exacta" if width_pct <= (threshold_pct*100/2) else "Zona ancha"
            })
            
    return final_levels

def format_price(price):
    """Ajusta los decimales automáticamente según el valor de la moneda"""
    if price < 0.001:
        return f"{price:.8f}"
    elif price < 1:
        return f"{price:.4f}"
    else:
        return f"{price:,.2f}"

def scan_symbol(symbol, timeframes, limit, max_results):
    print(f"\n--- 🎯 Muros Cuantitativos Dinámicos (ATR) para {symbol} ---")
    
    try:
        daily_df = fetch_ohlcv(symbol, '1d', limit=60)
        volatilidad_diaria_pct = calculate_atr_pct(daily_df, period=14)
        dynamic_threshold = volatilidad_diaria_pct * 0.25 
        print(f"Volatilidad Diaria (ATR): {volatilidad_diaria_pct*100:.2f}% | Umbral de Agrupación: {dynamic_threshold*100:.2f}%")
    except Exception as e:
        print(f"Error calculando ATR: {e}. Usando default 0.8%")
        dynamic_threshold = 0.008

    print(f"Analizando confluencia en: {', '.join(timeframes)} | Velas por TF: {limit}")
    
    all_supports = []
    all_resistances = []
    
    for tf in timeframes:
        try:
            df = fetch_ohlcv(symbol, tf, limit=limit)
            if tf in ['15m', '1h']: order = 20
            elif tf == '4h': order = 10
            elif tf == '1d': order = 5
            elif tf == '1w': order = 3
            else: order = 5
                
            sup, res = get_fractal_extremes(df, tf, order=order)
            all_supports.extend(sup)
            all_resistances.extend(res)
        except Exception as e:
            print(f"Error extrayendo {tf}: {e}")

    key_levels = cluster_levels(all_supports + all_resistances, threshold_pct=dynamic_threshold)
    current_price = fetch_ohlcv(symbol, timeframes[0], limit=1)['close'].iloc[0]
    print(f"Precio Actual: ${format_price(current_price)}\n")
    
    print(f"🧱 TOP {max_results} RESISTENCIAS MÁS CERCANAS (Hacia arriba):")
    res_count = 0
    for lvl in sorted(key_levels, key=lambda x: x['precio_linea']):
        if lvl['precio_linea'] > current_price and res_count < max_results:
            distancia = ((lvl['precio_linea'] - current_price) / current_price) * 100
            tfs_str = ", ".join(lvl['confluencia'])
            print(f" 🔴 ${format_price(lvl['precio_linea'])} | A +{distancia:.1f}% | Toques: {lvl['toques']} | Confluencia: [{tfs_str}]")
            res_count += 1

    print(f"\n🛌 TOP {max_results} SOPORTES MÁS CERCANOS (Hacia abajo):")
    sup_count = 0
    for lvl in sorted(key_levels, key=lambda x: x['precio_linea'], reverse=True):
        if lvl['precio_linea'] < current_price and sup_count < max_results:
            distancia = ((current_price - lvl['precio_linea']) / current_price) * 100
            tfs_str = ", ".join(lvl['confluencia'])
            print(f" 🟢 ${format_price(lvl['precio_linea'])} | A -{distancia:.1f}% | Toques: {lvl['toques']} | Confluencia: [{tfs_str}]")
            sup_count += 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Escáner Quant de Soportes y Resistencias")
    parser.add_argument("--symbols", nargs="+", required=True, help="Lista de símbolos, ej: BTC/USDT ETH/USDT")
    parser.add_argument("--tfs", nargs="+", default=['15m', '1h', '4h', '1d', '1w'], help="Temporalidades a escanear")
    parser.add_argument("--limit", type=int, default=1000, help="Velas históricas a analizar")
    parser.add_argument("--max", type=int, default=5, help="Número máximo de muros a mostrar por lado")
    
    args = parser.parse_args()
    
    for symbol in args.symbols:
        scan_symbol(symbol, args.tfs, args.limit, args.max)