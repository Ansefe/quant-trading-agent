import argparse
import time
import datetime
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
    
    # Proximity filter: 3% from current price (was 2% — too strict)
    proximity = 0.03
    
    soportes_cercanos = [s for s in sr_list if s['is_support'] and abs(price - s['price_level'])/price < proximity]
    resistencias_cercanas = [r for r in sr_list if not r['is_support'] and abs(r['price_level'] - price)/price < proximity]
    
    rsi_alcista = [r for r in rsi_list if 'ALCISTA' in r['type'] and 'ACTIVA' in r['state']]
    rsi_bajista = [r for r in rsi_list if 'BAJISTA' in r['type'] and 'ACTIVA' in r['state']]
    
    fvg_arriba = [f for f in smc_list if f['center_price'] > price]
    fvg_abajo = [f for f in smc_list if f['center_price'] < price]
    
    # --- LÓGICA DE CONFLUENCIA LONG (Score-based, no requirement for ALL conditions) ---
    long_score = 0
    long_details = {}
    
    if soportes_cercanos:
        best_support = max(soportes_cercanos, key=lambda s: s.get('touches', 1))
        long_score += 3 + min(2, len(best_support.get('confluence', [])))  # 3-5 pts for support
        long_details['support_level'] = best_support['price_level']
        long_details['support_tf'] = best_support.get('confluence', [])
        long_details['support_touches'] = best_support.get('touches', 1)
    
    if rsi_alcista:
        long_score += 3  # 3 pts for divergence
        long_details['rsi_price'] = rsi_alcista[0].get('price')
        long_details['rsi_tf'] = [r['timeframe'] for r in rsi_alcista]
    
    if sentiment_status == 'Alcista':
        long_score += 1
    elif sentiment_status == 'Bajista':
        long_score -= 1
    long_details['sentiment'] = sentiment_status
    
    if fvg_arriba:
        long_score += 2
        long_details['fvg_magnet'] = True
        long_details['fvg_target'] = fvg_arriba[0]['center_price']
    
    # Minimum 5 pts to report (support + anything else, or divergence + FVG)
    if long_score >= 5:
        target = fvg_arriba[0]['center_price'] if fvg_arriba else price * 1.03
        confluences.append({
            "symbol": symbol,
            "setup_type": "LONG",
            "target_price": target,
            "score": min(10, long_score),
            "details": long_details
        })
    
    # --- LÓGICA DE CONFLUENCIA SHORT ---
    short_score = 0
    short_details = {}
    
    if resistencias_cercanas:
        best_res = max(resistencias_cercanas, key=lambda r: r.get('touches', 1))
        short_score += 3 + min(2, len(best_res.get('confluence', [])))
        short_details['resistance_level'] = best_res['price_level']
        short_details['resistance_tf'] = best_res.get('confluence', [])
        short_details['resistance_touches'] = best_res.get('touches', 1)
    
    if rsi_bajista:
        short_score += 3
        short_details['rsi_price'] = rsi_bajista[0].get('price')
        short_details['rsi_tf'] = [r['timeframe'] for r in rsi_bajista]
    
    if sentiment_status == 'Bajista':
        short_score += 1
    elif sentiment_status == 'Alcista':
        short_score -= 1
    short_details['sentiment'] = sentiment_status
    
    if fvg_abajo:
        short_score += 2
        short_details['fvg_magnet'] = True
        short_details['fvg_target'] = fvg_abajo[0]['center_price']
    
    if short_score >= 5:
        target = fvg_abajo[0]['center_price'] if fvg_abajo else price * 0.97
        confluences.append({
            "symbol": symbol,
            "setup_type": "SHORT",
            "target_price": target,
            "score": min(10, short_score),
            "details": short_details
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
            '15m': {'limit': 1000, 'top_n': 10},
            '1h':  {'limit': 1000, 'top_n': 10},
            '4h':  {'limit': 1000, 'top_n': 10},
            '1d':  {'limit': 1000, 'top_n': 10},
            '1w':  {'limit': 1000, 'top_n': 10},  # Binance devuelve lo disponible si hay menos
        }
        
        all_sr_data = []
        for tf, cfg in TF_CONFIGS.items():
            try:
                tf_data = scan_sr(symbol, [tf], cfg['limit'], cfg['top_n'])
                if tf_data:
                    # Etiquetamos como escaneo aislado por TF
                    for row in tf_data:
                        row['source_run'] = 'per_tf'
                    all_sr_data.extend(tf_data)
                    print(f"   ✅ {tf}: {len(tf_data)} muros")
                time.sleep(0.5)
            except Exception as e:
                print(f"   ⚠️ Error en SR/{tf}: {e}")
        
        # Escaneo multi-TF para detectar confluencias inter-temporalidad
        try:
            multi_tf_data = scan_sr(symbol, list(TF_CONFIGS.keys()), 1000, 5)
            if multi_tf_data:
                for row in multi_tf_data:
                    row['source_run'] = 'multi_tf'
                all_sr_data.extend(multi_tf_data)
                print(f"   🏆 Multi-TF: {len(multi_tf_data)} confluencias")
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
        
    # ── Reporte final siempre se envía por Telegram ─────────────────────────
    # Así siempre sabes que el bot corrió aunque no haya confluencias.
    hora = datetime.datetime.now().strftime('%H:%M')
    
    if all_confluences:
        insert_trade_confluences(all_confluences)
        mensaje = f"\ud83d\udc51 *ALERTA DEL ORQUESTADOR QUANT* ({hora}) \ud83d\udc51\n\n"
        for c in all_confluences:
            emoji = "\ud83d\ude80" if c['setup_type'] == 'LONG' else "\ud83e\ude78"
            mensaje += f"{emoji} *{c['symbol']}* | {c['setup_type']} | Score {c['score']}/10\n"
            if c.get('target_price'):
                mensaje += f"   Target: ${c['target_price']:,.2f}\n"
            support_or_res = c['details'].get('support_level') or c['details'].get('resistance_level')
            if support_or_res:
                mensaje += f"   Zona: ${support_or_res:,.2f}\n"
            mensaje += f"   Sentimiento: {c['details'].get('sentiment', '-')}\n"
            tfs = c['details'].get('support_tf') or c['details'].get('resistance_tf') or []
            if tfs:
                mensaje += f"   TFs: {', '.join(tfs)}\n"
            mensaje += "\n"
    else:
        # Resumen de estado sin confluencias
        symbols_str = ', '.join(args.symbols)
        mensaje = (
            f"\ud83d\udcca *Reporte Quant* ({hora})\n"
            f"Activos: {symbols_str}\n"
            f"Estado: Sin confluencias fuertes en este momento.\n"
            f"Datos actualizados en Supabase \u2705"
        )
    
    send_telegram(mensaje)

if __name__ == "__main__":
    main()
