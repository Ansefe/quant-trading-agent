#!/usr/bin/env python3
"""
analyze_sentiment.py — On-demand sentiment analysis for OpenClaw.

Usage:
  python analyze_sentiment.py --symbols BTC/USDT --timeframes 4h,1d
  python analyze_sentiment.py --symbols BTC/USDT,ETH/USDT --timeframes 15m,1h,4h --telegram
  python analyze_sentiment.py  # defaults: BTC/USDT, all TFs
"""

import argparse
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET

import ccxt
import pandas as pd
import requests
from openai import OpenAI

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils.db import insert_sentiment

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

ALL_TIMEFRAMES = ['15m', '1h', '4h', '1d', '1w']
DEFAULT_SYMBOLS = ['BTC/USDT']

# How many candles to fetch per timeframe
TF_LIMITS = {'15m': 250, '1h': 250, '4h': 250, '1d': 250, '1w': 156}
# Friendly names for news search
ASSET_NAMES = {
    'BTC/USDT': 'Bitcoin', 'ETH/USDT': 'Ethereum',
    'BNB/USDT': 'Binance Coin', 'SOL/USDT': 'Solana', 'XRP/USDT': 'XRP'
}


# ──────────────────────────────────────────────────────────────
# Technical Indicator Functions
# ──────────────────────────────────────────────────────────────

def calculate_rsi(series, period=14):
    delta = series.diff(1)
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line

def calculate_stochastic(df, k_period=14, d_period=3):
    low_min = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()
    k = 100 * (df['close'] - low_min) / (high_max - low_min)
    return k, k.rolling(window=d_period).mean()

def calculate_bollinger(close, period=20, std_dev=2):
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    return sma + (std * std_dev), sma, sma - (std * std_dev)

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

def semaphore(indicator, value, **kw):
    price = kw.get('price', 0)
    if indicator in ('sma', 'ema'):
        return 'alcista' if price > value else 'bajista'
    elif indicator == 'rsi':
        return 'alcista' if value < 30 else ('bajista' if value > 70 else 'neutral')
    elif indicator == 'macd':
        return 'alcista' if value > kw.get('signal_line', 0) else 'bajista'
    elif indicator == 'stochastic':
        return 'alcista' if value < 20 else ('bajista' if value > 80 else 'neutral')
    elif indicator == 'bollinger':
        upper, lower = kw.get('upper', 0), kw.get('lower', 0)
        bw = upper - lower if upper - lower > 0 else 1
        pos = (price - lower) / bw
        return 'alcista' if pos < 0.2 else ('bajista' if pos > 0.8 else 'neutral')
    elif indicator == 'obv':
        return 'alcista' if kw.get('obv_short', 0) > kw.get('obv_long', 0) else 'bajista'
    return 'neutral'


def build_indicators(df, price):
    """Calculate all 11 indicators and return (semaphore_dict, text_summary)."""
    close = df['close']
    n = len(close)
    indicators = {}

    # Moving Averages
    for name, period in [('sma_20', 20), ('sma_55', 55), ('sma_100', 100), ('sma_200', 200)]:
        if n >= period:
            val = close.rolling(period).mean().iloc[-1]
            indicators[name] = {'value': round(val, 2), 'signal': semaphore('sma', val, price=price)}

    ema_21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    indicators['ema_21'] = {'value': round(ema_21, 2), 'signal': semaphore('ema', ema_21, price=price)}

    # RSI
    rsi_val = calculate_rsi(close).iloc[-1]
    indicators['rsi'] = {'value': round(rsi_val, 2), 'signal': semaphore('rsi', rsi_val)}

    # MACD
    macd_line, signal_line, histogram = calculate_macd(close)
    indicators['macd'] = {
        'value': round(macd_line.iloc[-1], 2),
        'signal_line': round(signal_line.iloc[-1], 2),
        'histogram': round(histogram.iloc[-1], 2),
        'signal': semaphore('macd', macd_line.iloc[-1], signal_line=signal_line.iloc[-1])
    }

    # Stochastic
    stoch_k, _ = calculate_stochastic(df)
    indicators['stochastic'] = {
        'value': round(stoch_k.iloc[-1], 2),
        'signal': semaphore('stochastic', stoch_k.iloc[-1])
    }

    # Bollinger
    bb_upper, _, bb_lower = calculate_bollinger(close)
    indicators['bollinger'] = {
        'upper': round(bb_upper.iloc[-1], 2),
        'lower': round(bb_lower.iloc[-1], 2),
        'signal': semaphore('bollinger', 0, price=price, upper=bb_upper.iloc[-1], lower=bb_lower.iloc[-1])
    }

    # ATR
    high_low = df['high'] - df['low']
    high_close = (df['high'] - close.shift()).abs()
    low_close = (df['low'] - close.shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    indicators['atr'] = {'value': round(tr.rolling(14).mean().iloc[-1], 2)}

    # OBV
    if 'volume' in df.columns and df['volume'].sum() > 0:
        obv = calculate_obv(close, df['volume'])
        obv_short = obv.rolling(5).mean().iloc[-1]
        obv_long = obv.rolling(20).mean().iloc[-1]
        indicators['obv_trend'] = {'signal': semaphore('obv', 0, obv_short=obv_short, obv_long=obv_long)}

    # Build text for GPT
    lines = []
    for key, data in indicators.items():
        val_str = f"${data['value']:,.2f}" if 'value' in data and data['value'] > 10 else str(data.get('value', ''))
        sig = data.get('signal', '-')
        emoji = '🟢' if sig == 'alcista' else '🔴' if sig == 'bajista' else '🟡'
        lines.append(f"  {emoji} {key.upper()}: {val_str} → {sig}")

    return indicators, '\n'.join(lines)


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
    except Exception:
        return ["Error obteniendo noticias."]


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ No Telegram credentials.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})


# ──────────────────────────────────────────────────────────────
# Main Analysis
# ──────────────────────────────────────────────────────────────

def analyze(symbols, timeframes, send_tg=False, include_news=True):
    exchange = ccxt.binance()
    all_db_data = []

    for symbol in symbols:
        name = ASSET_NAMES.get(symbol, symbol.replace('/USDT', ''))
        print(f"\n{'='*50}")
        print(f"📊 Analyzing {name} ({symbol})")
        print(f"{'='*50}")

        # Fetch news once per symbol (shared across TFs)
        news_text = ""
        if include_news:
            headlines = get_news(name)
            news_text = '\n'.join([f"  - {h}" for h in headlines])

        # Calculate indicators for each requested TF
        tf_blocks = {}  # tf -> {price, indicators, tech_text}
        for tf in timeframes:
            try:
                limit = TF_LIMITS.get(tf, 250)
                bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
                df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                price = df['close'].iloc[-1]
                indicators, tech_text = build_indicators(df, price)
                tf_blocks[tf] = {'price': price, 'indicators': indicators, 'tech_text': tech_text}
                print(f"  ✅ {tf}: {len(bars)} candles, price=${price:,.2f}")
            except Exception as e:
                print(f"  ❌ {tf}: {e}")

        if not tf_blocks:
            print(f"  ⚠️ No data for {name}, skipping.")
            continue

        # ── Build ONE GPT prompt for all TFs ────────────────────────
        context_parts = []
        for tf, block in tf_blocks.items():
            context_parts.append(
                f"── {tf.upper()} (precio: ${block['price']:,.2f}) ──\n"
                f"{block['tech_text']}"
            )

        tf_context = '\n\n'.join(context_parts)
        current_price = list(tf_blocks.values())[-1]['price']

        prompt = f"""
Eres un analista cuantitativo institucional de ÉLITE. Analiza {name} (${current_price:,.2f}).

{"📰 NOTICIAS:" + chr(10) + news_text + chr(10) if news_text else ""}
📊 INDICADORES TÉCNICOS POR TEMPORALIDAD:
{tf_context}

INSTRUCCIONES:
Para CADA temporalidad listada arriba ({', '.join(tf_blocks.keys())}), emite 3 dictámenes:

1. **sentiment_news** — Solo basado en noticias (igual para todos los TFs)
2. **sentiment_technical** — Basado SOLO en los indicadores de ESE timeframe específico
3. **sentiment_combined** — Combinación noticias + técnico de ese TF = veredicto final

REGLAS:
- RSI 45-55 → neutral técnico
- Precio vs SMA200 = tendencia macro más importante
- MACD cruzando señal = segundo más importante
- Si noticias y técnicos coinciden → alta confianza
- Si divergen → baja confianza y explica la discrepancia
- Confianza 1-100

FORMATO JSON estricto:
{{
  "{list(tf_blocks.keys())[0]}": {{
    "sentiment_news": "Alcista/Bajista/Neutral",
    "confidence_news": 1-100,
    "summary_news": "una línea",
    "sentiment_technical": "Alcista/Bajista/Neutral",
    "confidence_technical": 1-100,
    "summary_technical": "una línea del análisis técnico para este TF",
    "sentiment_combined": "Alcista/Bajista/Neutral",
    "confidence_combined": 1-100,
    "summary_combined": "veredicto final para operar en este TF"
  }}
}}
Repite la estructura para cada TF: {', '.join(tf_blocks.keys())}
"""

        print(f"\n  🧠 Calling GPT-4o-mini (1 call for {len(tf_blocks)} TFs)...")

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1
            )

            result = json.loads(response.choices[0].message.content)

            # Build Telegram message
            tg_msg = f"📊 *{name}* — Análisis On-Demand\n\n"

            for tf in tf_blocks:
                datos = result.get(tf, {})
                block = tf_blocks[tf]

                emoji_comb = "🟢" if datos.get("sentiment_combined") == "Alcista" else "🔴" if datos.get("sentiment_combined") == "Bajista" else "🟡"
                emoji_tech = "🟢" if datos.get("sentiment_technical") == "Alcista" else "🔴" if datos.get("sentiment_technical") == "Bajista" else "🟡"

                tg_msg += f"*{tf.upper()}* {emoji_comb} {datos.get('sentiment_combined', '-')} ({datos.get('confidence_combined', '-')}%)\n"
                tg_msg += f"  {emoji_tech} Tech: {datos.get('sentiment_technical', '-')} | 📰 News: {datos.get('sentiment_news', '-')}\n"
                tg_msg += f"  _{datos.get('summary_combined', '')}_\n\n"

                # Store each TF as separate row
                all_db_data.append({
                    "symbol": name,
                    "timeframe": tf,
                    # Combined = main
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
                    # Semaphore
                    "indicators": block['indicators'],
                })

            if send_tg:
                send_telegram(tg_msg)
                print("  📬 Telegram sent!")

            print(f"  ✅ {name}: {len(tf_blocks)} TFs analyzed")

        except Exception as e:
            print(f"  ❌ GPT error for {name}: {e}")

    # Insert all rows at once
    if all_db_data:
        insert_sentiment(all_db_data)
        print(f"\n✅ Total: {len(all_db_data)} sentiment rows stored in Supabase")

    return all_db_data


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='On-demand sentiment analysis for OpenClaw',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analyze_sentiment.py --symbols BTC/USDT --timeframes 4h,1d
  python analyze_sentiment.py --symbols BTC/USDT,ETH/USDT --timeframes 15m,1h,4h --telegram
  python analyze_sentiment.py  # defaults: BTC/USDT, all TFs
        """
    )
    parser.add_argument('--symbols', type=str, default='BTC/USDT',
                        help='Comma-separated symbols (default: BTC/USDT)')
    parser.add_argument('--timeframes', type=str, default=','.join(ALL_TIMEFRAMES),
                        help=f'Comma-separated timeframes (default: {",".join(ALL_TIMEFRAMES)})')
    parser.add_argument('--telegram', action='store_true',
                        help='Send results to Telegram')
    parser.add_argument('--no-news', action='store_true',
                        help='Skip news fetching (technical only)')

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(',')]
    timeframes = [t.strip() for t in args.timeframes.split(',')]

    # Validate timeframes
    invalid = [t for t in timeframes if t not in ALL_TIMEFRAMES]
    if invalid:
        print(f"❌ Invalid timeframes: {invalid}. Valid: {ALL_TIMEFRAMES}")
        sys.exit(1)

    print(f"🚀 Sentiment Analysis")
    print(f"   Symbols:    {symbols}")
    print(f"   Timeframes: {timeframes}")
    print(f"   Telegram:   {'✅' if args.telegram else '❌'}")
    print(f"   News:       {'❌ Skipped' if args.no_news else '✅'}")

    analyze(symbols, timeframes, send_tg=args.telegram, include_news=not args.no_news)


if __name__ == "__main__":
    main()
