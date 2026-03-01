import ccxt
import pandas as pd
import argparse

def fetch_ohlcv(symbol, timeframe, limit=500):
    exchange = ccxt.binance({'enableRateLimit': True})
    bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # Conversión de UTC a Hora Local (Colombia UTC-5)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['timestamp'] = df['timestamp'].dt.tz_localize('UTC').dt.tz_convert('America/Bogota')
    
    return df

def find_unmitigated_fvgs(df):
    fvgs = []
    
    # 1. Escanear todo el historial buscando los huecos de 3 velas
    for i in range(2, len(df)):
        vela1_high = df['high'].iloc[i-2]
        vela1_low = df['low'].iloc[i-2]
        vela2_time = df['timestamp'].iloc[i-1]
        vela3_high = df['high'].iloc[i]
        vela3_low = df['low'].iloc[i]
        
        # Condición FVG Alcista (Toro): El bajo de la vela 3 no alcanza a tocar el alto de la vela 1
        if vela3_low > vela1_high:
            fvgs.append({
                'tipo': '🟢 FVG ALCISTA',
                'techo': vela3_low,
                'piso': vela1_high,
                'fecha': vela2_time,
                'idx_formacion': i,
                'mitigado': False
            })
            
        # Condición FVG Bajista (Oso): El alto de la vela 3 no alcanza a tocar el bajo de la vela 1
        elif vela3_high < vela1_low:
            fvgs.append({
                'tipo': '🔴 FVG BAJISTA',
                'techo': vela1_low,
                'piso': vela3_high,
                'fecha': vela2_time,
                'idx_formacion': i,
                'mitigado': False
            })

    # 2. Comprobar si el mercado ya rellenó (mitigó) el hueco con velas posteriores
    unmitigated_fvgs = []
    for fvg in fvgs:
        mitigado = False
        # Escaneamos desde la vela donde se formó hasta el precio actual
        for j in range(fvg['idx_formacion'] + 1, len(df)):
            if fvg['tipo'] == '🟢 FVG ALCISTA':
                # Si el precio bajó más allá del piso del gap, está rellenado
                if df['low'].iloc[j] <= fvg['piso']:
                    mitigado = True
                    break
            elif fvg['tipo'] == '🔴 FVG BAJISTA':
                # Si el precio subió más allá del techo del gap, está rellenado
                if df['high'].iloc[j] >= fvg['techo']:
                    mitigado = True
                    break
                    
        if not mitigado:
            unmitigated_fvgs.append(fvg)
            
    return unmitigated_fvgs

def format_price(price):
    if price < 0.001: return f"{price:.8f}"
    elif price < 1: return f"{price:.4f}"
    else: return f"{price:,.2f}"

def scan_smc(symbols, timeframes, limit):
    all_db_data = []
    for symbol in symbols:
        print(f"\n--- 🐋 Radar SMC (Fair Value Gaps) para {symbol} ---")
        
        for tf in timeframes:
            try:
                df = fetch_ohlcv(symbol, tf, limit=limit)
                current_price = df['close'].iloc[-1]
                active_fvgs = find_unmitigated_fvgs(df)
                
                if not active_fvgs:
                    continue
                    
                print(f"\n[{tf}] Precio Actual: ${format_price(current_price)}")
                
                # Ordenamos para mostrar los más cercanos al precio actual primero
                # Calculamos el punto medio del FVG para medir la distancia
                for fvg in active_fvgs:
                    fvg['centro'] = (fvg['techo'] + fvg['piso']) / 2
                    fvg['distancia_pct'] = ((fvg['centro'] - current_price) / current_price) * 100

                active_fvgs.append({"divisor": True}) # Solo para formateo en consola
                
                # Filtrar y ordenar los FVGs por encima del precio (Imanes hacia arriba)
                fvgs_arriba = [f for f in active_fvgs if f.get('centro', 0) > current_price]
                fvgs_arriba = sorted(fvgs_arriba, key=lambda x: x['distancia_pct'])[:3]
                
                # Filtrar y ordenar los FVGs por debajo del precio (Imanes hacia abajo)
                fvgs_abajo = [f for f in active_fvgs if f.get('centro', float('inf')) < current_price and not f.get('divisor')]
                fvgs_abajo = sorted(fvgs_abajo, key=lambda x: x['distancia_pct'], reverse=True)[:3]

                for f in fvgs_arriba:
                    print(f"  {f['tipo']} | Creado: {f['fecha'].strftime('%Y-%m-%d %H:%M')} | Hueco: ${format_price(f['piso'])} - ${format_price(f['techo'])} | A +{f['distancia_pct']:.2f}%")
                    all_db_data.append({
                        "symbol": symbol,
                        "timeframe": tf,
                        "type": f['tipo'],
                        "top_price": float(f['techo']),
                        "bottom_price": float(f['piso']),
                        "fvg_date": f['fecha'].strftime('%Y-%m-%d %H:%M:%S'),
                        "mitigated": False,
                        "center_price": float(f['centro']),
                        "distance_pct": float(f['distancia_pct'])
                    })
                
                if fvgs_arriba and fvgs_abajo:
                    print("  ------------------------------------------------")

                for f in fvgs_abajo:
                    print(f"  {f['tipo']} | Creado: {f['fecha'].strftime('%Y-%m-%d %H:%M')} | Hueco: ${format_price(f['piso'])} - ${format_price(f['techo'])} | A {f['distancia_pct']:.2f}%")
                    all_db_data.append({
                        "symbol": symbol,
                        "timeframe": tf,
                        "type": f['tipo'],
                        "top_price": float(f['techo']),
                        "bottom_price": float(f['piso']),
                        "fvg_date": f['fecha'].strftime('%Y-%m-%d %H:%M:%S'),
                        "mitigated": False,
                        "center_price": float(f['centro']),
                        "distance_pct": float(f['distancia_pct'])
                    })
                    
            except Exception as e:
                print(f"Error procesando {tf}: {e}")
                
    return all_db_data

if __name__ == "__main__":
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils.db import insert_fvgs

    parser = argparse.ArgumentParser(description="Escáner Quant SMC - Fair Value Gaps")
    parser.add_argument("--symbols", nargs="+", required=True, help="Lista de símbolos, ej: BTC/USDT")
    parser.add_argument("--tfs", nargs="+", default=['1h', '4h', '1d'], help="Temporalidades a escanear")
    parser.add_argument("--limit", type=int, default=500, help="Velas históricas a analizar")
    
    args = parser.parse_args()
    data = scan_smc(args.symbols, args.tfs, args.limit)
    
    if data:
        insert_fvgs(data)