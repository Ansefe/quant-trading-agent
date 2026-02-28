import yfinance as yf
import ccxt
import pandas as pd
import urllib.request
import xml.etree.ElementTree as ET
import os
import json
import requests
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def calculate_rsi(series, period=14):
    delta = series.diff(1)
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# AUMENTAMOS EL LÍMITE DE NOTICIAS A 7
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

def fetch_market_data():
    print("--- 📡 Recolectando Datos Estabilizados ---")
    market_context = ""

    traditionals = {'^GSPC': 'S&P 500', '^IXIC': 'NASDAQ'}
    for symbol, name in traditionals.items():
        try:
            data = yf.Ticker(symbol).history(period="60d")
            if not data.empty:
                close = data['Close']
                rsi_14 = calculate_rsi(close).iloc[-1]
                sma_20 = close.rolling(window=20).mean().iloc[-1]
                news = get_news(f"{name} economy")
                market_context += f"Activo: {name} | Precio: ${close.iloc[-1]:.2f} | RSI(14): {rsi_14:.2f} | SMA(20): {sma_20:.2f}\nNoticias: {news}\n\n"
        except:
            pass

    exchange = ccxt.binance()
    cryptos = {'BTC/USDT': 'Bitcoin', 'ETH/USDT': 'Ethereum', 'BNB/USDT': 'Binance Coin', 'SOL/USDT': 'Solana', 'XRP/USDT': 'XRP'}
    
    for symbol, name in cryptos.items():
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=60)
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            close = df['close']
            rsi_14 = calculate_rsi(close).iloc[-1]
            sma_20 = close.rolling(window=20).mean().iloc[-1]
            news = get_news(name)
            market_context += f"Activo: {name} | Precio: ${close.iloc[-1]:.2f} | RSI(14): {rsi_14:.2f} | SMA(20): {sma_20:.2f}\nNoticias: {news}\n\n"
        except:
            pass

    print("--- 🧠 Consultando a GPT-4o-mini ---")
    
    # PROMPT ESTRICTO
    prompt = f"""
    Eres un analista cuantitativo institucional. Analiza este bloque de activos.
    Datos del mercado:
    {market_context}
    
    REGLAS DE ORO PARA TU DICTAMEN:
    1. Si el RSI está entre 45 y 55, asume que el mercado está lateral y tu dictamen DEBE ser "Neutral", a menos que las noticias sean unánimemente extremas.
    2. Compara el Precio con la SMA(20). Si el precio está por encima, hay fuerza alcista a corto plazo; si está por debajo, bajista.
    3. No reacciones exageradamente a 1 sola noticia. Haz un promedio del sentimiento de los 7 titulares.
    
    Devuelve estrictamente un único objeto JSON donde las llaves sean los nombres de los activos, y los valores sean:
    {{"sentimiento": "Alcista/Bajista/Neutral", "confianza": "1-100", "resumen": "tu conclusión técnica en 1 línea"}}
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1 # Bajamos la temperatura para que sea más analítico y menos creativo
        )
        
        analisis_final = json.loads(response.choices[0].message.content)
        
        mensaje = "📊 *Reporte Quant Estabilizado* 📊\n\n"
        for activo, datos in analisis_final.items():
            emoji = "🟢" if datos["sentimiento"] == "Alcista" else "🔴" if datos["sentimiento"] == "Bajista" else "🟡"
            mensaje += f"{emoji} *{activo}*\n"
            mensaje += f"Sentimiento: {datos['sentimiento']} ({datos['confianza']}%)\n"
            mensaje += f"Resumen: _{datos['resumen']}_\n\n"
            
        send_telegram(mensaje)
        print("¡Reporte enviado a Telegram!")
        
    except Exception as e:
        print(f"Error en IA: {e}")

if __name__ == "__main__":
    fetch_market_data()
