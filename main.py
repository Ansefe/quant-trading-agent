import argparse
import time
import ccxt
from utils.db import (
    insert_sentiment,
    insert_sr_levels,
    insert_rsi_divergences,
    insert_fvgs,
    insert_trade_confluences
)

# Importamos los módulos de los scripts
from fetch_data import fetch_market_data, send_telegram
from sr_scanner import scan_symbol as scan_sr
from rsi_divergence import scan_market as scan_rsi
from smc_scanner import scan_smc as scan_smc_levels

def get_current_price(symbol):
    try:
        exchange = ccxt.binance()
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except Exception as e:
        print(f"Error obteniendo precio de {symbol}: {e}")
        return None

def analyze_confluences(symbol, price, sentiment_list, sr_list, rsi_list, smc_list):
    confluences = []
    
    # Extraemos el sentimiento del activo si existe
    sentiment_data = next((s for s in sentiment_list if s['symbol'] == symbol), {})
    sentiment_status = sentiment_data.get('sentiment', 'Neutral')
    
    # 1. Filtramos Soportes y Resistencias cercanos (a menos del 2% del precio actual)
    soportes_cercanos = [s for s in sr_list if s['is_support'] and ((price - s['price_level'])/price) < 0.02]
    resistencias_cercanas = [r for r in sr_list if not r['is_support'] and ((r['price_level'] - price)/price) < 0.02]
    
    # 2. Filtramos Divergencias RSI ACTIVAS
    rsi_alcista = [r for r in rsi_list if 'ALCISTA' in r['type'] and 'ACTIVA' in r['state']]
    rsi_bajista = [r for r in rsi_list if 'BAJISTA' in r['type'] and 'ACTIVA' in r['state']]
    
    # 3. Filtramos FVGs como objetivos (Imanes por encima o por debajo)
    fvg_arriba = [f for f in smc_list if f['center_price'] > price] # Objetivo para Long
    fvg_abajo = [f for f in smc_list if f['center_price'] < price] # Objetivo para Short
    
    # --- LÓGICA DE CONFLUENCIA LONG (COMPRA) ---
    # Setup básico: El precio está en un soporte Y hay divergencia alcista Y el bot dice Alcista/Neutral
    if soportes_cercanos and rsi_alcista and sentiment_status != "Bajista":
        score = 8
        target = fvg_arriba[0]['center_price'] if fvg_arriba else price * 1.05
        if fvg_arriba: score = 10 # Santo Grial
        
        confluences.append({
            "symbol": symbol,
            "setup_type": "LONG",
            "target_price": target,
            "score": score,
            "details": {
                "support_level": soportes_cercanos[0]['price_level'],
                "support_tf": soportes_cercanos[0]['confluence'],
                "rsi_price": rsi_alcista[0]['price'],
                "rsi_tf": [r['timeframe'] for r in rsi_alcista],
                "sentiment": sentiment_status,
                "fvg_magnet": True if fvg_arriba else False
            }
        })
        
    # --- LÓGICA DE CONFLUENCIA SHORT (VENTA) ---
    if resistencias_cercanas and rsi_bajista and sentiment_status != "Alcista":
        score = 8
        target = fvg_abajo[0]['center_price'] if fvg_abajo else price * 0.95
        if fvg_abajo: score = 10 
        
        confluences.append({
            "symbol": symbol,
            "setup_type": "SHORT",
            "target_price": target,
            "score": score,
            "details": {
                "resistance_level": resistencias_cercanas[0]['price_level'],
                "resistance_tf": resistencias_cercanas[0]['confluence'],
                "rsi_price": rsi_bajista[0]['price'],
                "rsi_tf": [r['timeframe'] for r in rsi_bajista],
                "sentiment": sentiment_status,
                "fvg_magnet": True if fvg_abajo else False
            }
        })
        
    return confluences

def main():
    parser = argparse.ArgumentParser(description="Orquestador Maestro Quant")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT"], help="Símbolos a escanear")
    args = parser.parse_args()
    
    print("🚀 Iniciando Orquestador Maestro Quant...")
    
    # 1. Extraer Sentimiento Global (1 solo request)
    print("\n[Paso 1] Extrayendo Sentimiento Macro y de Noticias...")
    sentiment_data = fetch_market_data(send_alert=False)
    if sentiment_data:
        insert_sentiment(sentiment_data)
        
    all_confluences = []
    
    # 2. Iterar por cada activo pasando por los otros 3 Motores
    for symbol in args.symbols:
        print(f"\n==========================================")
        print(f"🔄 PROCESANDO ACTIVO: {symbol}")
        print(f"==========================================")
        
        current_price = get_current_price(symbol)
        if not current_price: continue
            
        print("\n[Paso 2] Escaneando Muros Cuantitativos ATR (por temporalidad)...")
        
        # ── Escaneo por temporalidad ──────────────────────────────────────────
        # Cada TF corre de forma independiente: sus top-10 S/R se guardan
        # con confluence=['15m'] (o lo que corresponda), así el frontend
        # puede filtrar "solo 1W" o "solo 4h" sin contaminación de otras TFs.
        # Después hacemos un escaneo multi-TF para detectar confluencias reales.
        
        TF_CONFIGS = {
            '15m': {'limit': 500, 'top_n': 10},
            '1h':  {'limit': 500, 'top_n': 10},
            '4h':  {'limit': 500, 'top_n': 10},
            '1d':  {'limit': 500, 'top_n': 10},
            '1w':  {'limit': 500, 'top_n': 10},
        }
        
        all_sr_data = []
        for tf, cfg in TF_CONFIGS.items():
            try:
                tf_data = scan_sr(symbol, [tf], cfg['limit'], cfg['top_n'])
                if tf_data:
                    all_sr_data.extend(tf_data)
                    print(f"   ✅ {tf}: {len(tf_data)} muros")
                time.sleep(0.5)   # pequeña pausa entre TFs
            except Exception as e:
                print(f"   ⚠️ Error en SR/{tf}: {e}")
        
        # Escaneo multi-TF para confluencias (el "Santo Grial")
        try:
            multi_tf_data = scan_sr(symbol, list(TF_CONFIGS.keys()), 1000, 5)
            if multi_tf_data:
                # Marcamos que estos tienen confluencia multi-TF real
                all_sr_data.extend(multi_tf_data)
                print(f"   🏆 Multi-TF: {len(multi_tf_data)} confluencias detectadas")
        except Exception as e:
            print(f"   ⚠️ Error en SR/multi-TF: {e}")
        
        if all_sr_data:
            insert_sr_levels(all_sr_data)
        
        time.sleep(1) # Rate limit protection

        
        print("\n[Paso 3] Escaneando Divergencias RSI...")
        rsi_data = scan_rsi([symbol], ['15m', '1h', '4h', '1d', '1w'], historical=False)
        if rsi_data: insert_rsi_divergences(rsi_data)
        
        time.sleep(1) # Rate limit protection
        
        print("\n[Paso 4] Escaneando Fair Value Gaps (SMC)...")
        smc_data = scan_smc_levels([symbol], ['15m', '1h', '4h', '1d', '1w'], limit=500)
        if smc_data: insert_fvgs(smc_data)
        
        print("\n[Paso 5] Buscando Confluencias de Alta Probabilidad...")
        confs = analyze_confluences(symbol, current_price, sentiment_data, all_sr_data or [], rsi_data or [], smc_data or [])
        
        if confs:
            all_confluences.extend(confs)
            for c in confs:
                print(f"🔥 ¡CONFLUENCIA {c['setup_type']} DETECTADA EN {c['symbol']}! Score: {c['score']}/10")
        else:
            print(f"💤 Ninguna confluencia fuerte en {symbol} en este momento.")
            
        time.sleep(2) # Rate limit prevention between symbols
        
    if all_confluences:
        insert_trade_confluences(all_confluences)
        
        # Enviar resumen maestro a Telegram
        mensaje = "👑 *ALERTA DEL ORQUESTADOR QUANT* 👑\n\n"
        for c in all_confluences:
            emoji = "🚀" if c['setup_type'] == "LONG" else "🩸"
            mensaje += f"{emoji} *{c['symbol']}* | SETUP: {c['setup_type']}\n"
            mensaje += f"Puntuación: {c['score']}/10\n"
            mensaje += f"Target (Imán FVG): ${c['target_price']:,.2f}\n"
            mensaje += f"Detalles: {c['details']}\n\n"
            
        send_telegram(mensaje)

if __name__ == "__main__":
    main()
