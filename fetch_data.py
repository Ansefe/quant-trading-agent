import yfinance as yf
import ccxt
import pandas as pd
import numpy as np
import urllib.request
import xml.etree.ElementTree as ET
import os
import json
import requests
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ──────────────────────────────────────────────────────────────
# Technical Indicator Functions
# ──────────────────────────────────────────────────────────────

def calculate_rsi(series, period=14):
    """RSI using Wilder's RMA (matches TradingView / industry standard)."""
    delta = series.diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calculate_stochastic(df, k_period=14, d_period=3):
    low_min = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()
    k = 100 * (df['close'] - low_min) / (high_max - low_min)
    d = k.rolling(window=d_period).mean()
    return k, d

def calculate_bollinger(close, period=20, std_dev=2):
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return upper, sma, lower

def calculate_obv(close, volume):
    obv = [0]
    for i in range(1, len(close)):
        if close.iloc[i] > close.iloc[i-1]:
            obv.append(obv[-1] + volume.iloc[i])
        elif close.iloc[i] < close.iloc[i-1]:
            obv.append(obv[-1] - volume.iloc[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv, index=close.index)

def semaphore_signal(indicator, value, **kwargs):
    """Convert an indicator value to a traffic light signal."""
    price = kwargs.get('price', 0)
    
    if indicator == 'sma':
        return 'alcista' if price > value else 'bajista'
    elif indicator == 'ema':
        return 'alcista' if price > value else 'bajista'
    elif indicator == 'rsi':
        if value < 30: return 'alcista'     # oversold → buy opportunity
        if value > 70: return 'bajista'     # overbought → sell zone
        return 'neutral'
    elif indicator == 'macd':
        signal_val = kwargs.get('signal_line', 0)
        return 'alcista' if value > signal_val else 'bajista'
    elif indicator == 'stochastic':
        if value < 20: return 'alcista'
        if value > 80: return 'bajista'
        return 'neutral'
    elif indicator == 'bollinger':
        upper = kwargs.get('upper', 0)
        lower = kwargs.get('lower', 0)
        band_width = upper - lower if upper - lower > 0 else 1
        position = (price - lower) / band_width
        if position < 0.2: return 'alcista'
        if position > 0.8: return 'bajista'
        return 'neutral'
    elif indicator == 'obv':
        # Compare OBV trend (last 5 vs last 20)
        obv_short = kwargs.get('obv_short', 0)
        obv_long = kwargs.get('obv_long', 0)
        return 'alcista' if obv_short > obv_long else 'bajista'
    return 'neutral'

def build_indicators(df, price):
    """Calculate all technical indicators and return semaphore JSONB + text summary."""
    close = df['close']
    
    # Moving Averages
    sma_20  = close.rolling(20).mean().iloc[-1]
    sma_55  = close.rolling(55).mean().iloc[-1] if len(close) >= 55 else None
    sma_100 = close.rolling(100).mean().iloc[-1] if len(close) >= 100 else None
    sma_200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    ema_21  = close.ewm(span=21, adjust=False).mean().iloc[-1]
    
    # RSI
    rsi_val = calculate_rsi(close).iloc[-1]
    
    # MACD
    macd_line, signal_line, histogram = calculate_macd(close)
    macd_val = macd_line.iloc[-1]
    macd_sig = signal_line.iloc[-1]
    macd_hist = histogram.iloc[-1]
    
    # Stochastic
    stoch_k, stoch_d = calculate_stochastic(df)
    stoch_val = stoch_k.iloc[-1]
    
    # Bollinger Bands
    bb_upper, bb_mid, bb_lower = calculate_bollinger(close)
    
    # ATR
    high_low = df['high'] - df['low']
    high_close = (df['high'] - close.shift()).abs()
    low_close = (df['low'] - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr_val = tr.rolling(14).mean().iloc[-1]
    
    # OBV
    has_volume = 'volume' in df.columns and df['volume'].sum() > 0
    obv_signal = 'neutral'
    if has_volume:
        obv = calculate_obv(close, df['volume'])
        obv_short = obv.rolling(5).mean().iloc[-1]
        obv_long  = obv.rolling(20).mean().iloc[-1]
        obv_signal = semaphore_signal('obv', 0, obv_short=obv_short, obv_long=obv_long)
    
    # Build semaphore object
    indicators = {}
    
    indicators['sma_20'] = {
        'value': round(sma_20, 2),
        'signal': semaphore_signal('sma', sma_20, price=price)
    }
    indicators['ema_21'] = {
        'value': round(ema_21, 2),
        'signal': semaphore_signal('ema', ema_21, price=price)
    }
    if sma_55 is not None:
        indicators['sma_55'] = {
            'value': round(sma_55, 2),
            'signal': semaphore_signal('sma', sma_55, price=price)
        }
    if sma_100 is not None:
        indicators['sma_100'] = {
            'value': round(sma_100, 2),
            'signal': semaphore_signal('sma', sma_100, price=price)
        }
    if sma_200 is not None:
        indicators['sma_200'] = {
            'value': round(sma_200, 2),
            'signal': semaphore_signal('sma', sma_200, price=price)
        }
    
    indicators['rsi'] = {
        'value': round(rsi_val, 2),
        'signal': semaphore_signal('rsi', rsi_val)
    }
    indicators['macd'] = {
        'value': round(macd_val, 2),
        'signal_line': round(macd_sig, 2),
        'histogram': round(macd_hist, 2),
        'signal': semaphore_signal('macd', macd_val, signal_line=macd_sig)
    }
    indicators['stochastic'] = {
        'value': round(stoch_val, 2),
        'signal': semaphore_signal('stochastic', stoch_val)
    }
    indicators['bollinger'] = {
        'upper': round(bb_upper.iloc[-1], 2),
        'lower': round(bb_lower.iloc[-1], 2),
        'signal': semaphore_signal('bollinger', 0,
                     price=price, upper=bb_upper.iloc[-1], lower=bb_lower.iloc[-1])
    }
    indicators['atr'] = {
        'value': round(atr_val, 2)
    }
    indicators['obv_trend'] = {
        'signal': obv_signal
    }
    
    # Build text summary for the GPT prompt
    text_lines = []
    for key, data in indicators.items():
        val_str = f"${data['value']:,.2f}" if 'value' in data and data['value'] > 10 else str(data.get('value', ''))
        sig = data.get('signal', '-')
        emoji = '🟢' if sig == 'alcista' else '🔴' if sig == 'bajista' else '🟡'
        text_lines.append(f"  {emoji} {key.upper()}: {val_str} → {sig}")
    
    return indicators, '\n'.join(text_lines)


# ──────────────────────────────────────────────────────────────
# News
# ──────────────────────────────────────────────────────────────

def get_news(query, limit=7):
    try:
        q = query.replace(' ', '+')
        url = f"https://news.google.com/rss/search?q={q}+crypto+market&hl=en-US&gl=US&ceid=US:en"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        xml_data = urllib.request.urlopen(req).read()
        root = ET.fromstring(xml_data)
        headlines = [item.find('title').text for item in root.findall('.//item')[:limit]]
        return headlines if headlines else ["Sin noticias relevantes."]
    except:
        return ["Error obteniendo noticias."]

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ No Telegram credentials.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)


# ──────────────────────────────────────────────────────────────
# Main Data Fetching + AI Analysis
# ──────────────────────────────────────────────────────────────

def fetch_market_data(send_alert=True):
    print("--- 📡 Recolectando Datos de Mercado ---")
    
    # Build context blocks per asset
    asset_blocks = {}     # name -> {news_text, tech_text, indicators}
    
    # Traditional Markets (S&P, NASDAQ) — news + limited indicators
    traditionals = {'^GSPC': 'S&P 500', '^IXIC': 'NASDAQ'}
    for symbol, name in traditionals.items():
        try:
            data = yf.Ticker(symbol).history(period="250d")
            if not data.empty:
                df = data.rename(columns={'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'})
                price = df['close'].iloc[-1]
                news = get_news(f"{name} economy")
                indicators, tech_text = build_indicators(df, price)
                asset_blocks[name] = {
                    'news_text': '\n'.join([f"  - {h}" for h in news]),
                    'tech_text': tech_text,
                    'indicators': indicators,
                    'price': price,
                }
        except Exception as e:
            print(f"  ⚠️ Error {name}: {e}")

    # Crypto Assets — full indicators + news
    exchange = ccxt.binance()
    cryptos = {
        'BTC/USDT': 'Bitcoin', 'ETH/USDT': 'Ethereum',
        'BNB/USDT': 'Binance Coin', 'SOL/USDT': 'Solana', 'XRP/USDT': 'XRP'
    }
    
    for symbol, name in cryptos.items():
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=250)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            price = df['close'].iloc[-1]
            news = get_news(name)
            indicators, tech_text = build_indicators(df, price)
            asset_blocks[name] = {
                'news_text': '\n'.join([f"  - {h}" for h in news]),
                'tech_text': tech_text,
                'indicators': indicators,
                'price': price,
            }
        except Exception as e:
            print(f"  ⚠️ Error {name}: {e}")

    if not asset_blocks:
        print("No se obtuvieron datos de mercado.")
        return []

    # ── Build GPT Prompt ────────────────────────────────────────────────
    context_parts = []
    for name, block in asset_blocks.items():
        context_parts.append(
            f"═══ {name} (${block['price']:,.2f}) ═══\n"
            f"📰 NOTICIAS:\n{block['news_text']}\n"
            f"📊 INDICADORES TÉCNICOS:\n{block['tech_text']}\n"
        )
    
    market_context = '\n'.join(context_parts)
    
    print("--- 🧠 Consultando a GPT-4o-mini (Análisis Triple) ---")
    
    prompt = f"""
    Eres un analista cuantitativo institucional de ÉLITE. Tienes acceso a datos fundamentales (noticias) y un panel completo de indicadores técnicos para cada activo.

    DATOS DEL MERCADO:
    {market_context}
    
    INSTRUCCIONES ESTRICTAS:
    
    Para CADA activo, debes emitir TRES dictámenes separados:
    
    1. **sentiment_news** — Basado ÚNICAMENTE en las noticias/titulares:
       - Evalúa el tono general de los 7 titulares
       - No te dejes influir por 1 sola noticia extrema
       - Confianza más alta si los titulares son unánimes
    
    2. **sentiment_technical** — Basado ÚNICAMENTE en los indicadores técnicos:
       - Evalúa el semáforo completo (MAs, RSI, MACD, Estocástico, Bollinger, OBV)
       - Si precio > SMA200 y MACD alcista → fuerte señal técnica alcista
       - Si RSI > 70 y Estocástico > 80 → sobrecompra, señal de precaución
       - Si la mayoría de semáforos son 🟢 → alcista técnico
       - Pesa más las MAs de largo plazo (SMA 100, SMA 200) que las cortas
    
    3. **sentiment_combined** — Tu VEREDICTO FINAL combinando noticias + técnicos:
       - Este es el NORTE para operar el día
       - Si noticias y técnicos coinciden → alta confianza
       - Si divergen → baja la confianza y explica la discrepancia
    
    REGLAS DE ORO:
    - RSI entre 45-55 → momentum lateral (técnico neutral a menos que otros digan lo contrario)
    - Precio vs SMA200 es el indicador más importante para tendencia macro
    - MACD cruzando señal es el segundo más importante
    - Confianza 1-100: cuanto más indicadores apunten en la misma dirección, mayor confianza
    
    FORMATO DE RESPUESTA (JSON estricto):
    Devuelve un objeto donde las llaves sean los nombres de los activos exactamente como aparecen arriba.
    Cada valor debe tener esta estructura:
    {{
        "sentiment_news": "Alcista/Bajista/Neutral",
        "confidence_news": 1-100,
        "summary_news": "una línea explicando el veredicto de noticias",
        "sentiment_technical": "Alcista/Bajista/Neutral",
        "confidence_technical": 1-100,
        "summary_technical": "una línea explicando el veredicto técnico",
        "sentiment_combined": "Alcista/Bajista/Neutral",
        "confidence_combined": 1-100,
        "summary_combined": "una línea: tu veredicto FINAL para operar hoy"
    }}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        
        analisis_final = json.loads(response.choices[0].message.content)
        
        mensaje = "📊 *Reporte Quant Triple* 📊\n\n"
        db_data = []
        
        for activo, datos in analisis_final.items():
            block = asset_blocks.get(activo, {})
            
            # Emojis for combined verdict (the main one)
            emoji_comb = "🟢" if datos.get("sentiment_combined") == "Alcista" else "🔴" if datos.get("sentiment_combined") == "Bajista" else "🟡"
            emoji_news = "🟢" if datos.get("sentiment_news") == "Alcista" else "🔴" if datos.get("sentiment_news") == "Bajista" else "🟡"
            emoji_tech = "🟢" if datos.get("sentiment_technical") == "Alcista" else "🔴" if datos.get("sentiment_technical") == "Bajista" else "🟡"
            
            mensaje += f"{emoji_comb} *{activo}* | Combinado: {datos.get('sentiment_combined', '-')} ({datos.get('confidence_combined', '-')}%)\n"
            mensaje += f"  {emoji_news} Noticias: {datos.get('sentiment_news', '-')} ({datos.get('confidence_news', '-')}%)\n"
            mensaje += f"  {emoji_tech} Técnico: {datos.get('sentiment_technical', '-')} ({datos.get('confidence_technical', '-')}%)\n"
            mensaje += f"  _{datos.get('summary_combined', '')}_\n\n"
            
            db_data.append({
                "symbol": activo,
                # Legacy fields (combined = main sentiment)
                "sentiment": datos.get('sentiment_combined', 'Neutral'),
                "confidence": int(datos.get('confidence_combined', 50)),
                "summary": datos.get('summary_combined', ''),
                # News
                "sentiment_news": datos.get('sentiment_news', 'Neutral'),
                "confidence_news": int(datos.get('confidence_news', 50)),
                "summary_news": datos.get('summary_news', ''),
                # Technical
                "sentiment_technical": datos.get('sentiment_technical', 'Neutral'),
                "confidence_technical": int(datos.get('confidence_technical', 50)),
                "summary_technical": datos.get('summary_technical', ''),
                # Semaphore indicators
                "indicators": block.get('indicators', {}),
            })
            
        if send_alert:
            send_telegram(mensaje)
            print("¡Reporte triple enviado a Telegram!")
            
        return db_data
        
    except Exception as e:
        print(f"Error en IA: {e}")
        return []

if __name__ == "__main__":
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from utils.db import insert_sentiment
    
    data = fetch_market_data(send_alert=True)
    if data:
        insert_sentiment(data)
