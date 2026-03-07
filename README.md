# 📈 Motor Cuantitativo & Agente de Trading (Quant Trading Agent)

Motor de análisis cuantitativo y sentimiento de mercado en tiempo real. Sirve como backend para una terminal de trading institucional y como cinturón de herramientas para un agente autónomo de IA.

---

## ✨ Características Principales

1. **Live Paper Trading Engine (`live_engine.py`)** 🚀 **NUEVO**
   - Motor asíncrono en tiempo real usando websockets de Binance (`ccxt`, `websockets`).
   - Gestión de posiciones virtuales (Entry, TP, SL, Trailing Stop) y PnL en tiempo real.
   - Sistema de puntuación ("Scoring") que combina múltiples indicadores para entradas limpias.
   - Manejo de estrategias como Martingala y persistencia en memoria.

2. **Detector de Ondas de Elliott y Fibonacci (`elliott_scanner.py`)** 🌊 **NUEVO**
   - ZigZag adaptativo basado en ATR (Average True Range) para filtrar ruido dinámicamente.
   - Motor de Reglas estricto para validar Ondas Impulsivas (12345) y Correctivas (ABC).
   - Cálculo de objetivos de Fibonacci programáticos y niveles de invalidación en tiempo real.
   - Análisis Multi-Temporalidad (15m, 1h, 4h, 1d) concurrente.

3. **Escáner de Muros de Soporte y Resistencia (`sr_scanner.py`)**
   - Detección de zonas de liquidez usando agrupamiento espacial (clustering) de extremos fractales.
   - Puntuación por "toques" y confluencias en múltiples temporalidades y ATR dinámico.

4. **Escáner SMC y FVGs (`smc_scanner.py`)**
   - Detección de Fair Value Gaps (FVG) no mitigados alcistas y bajistas y medición de distancia al precio actual.

5. **Divergencias RSI (`rsi_divergence.py`)**
   - Algoritmo para detectar divergencias regulares entre la acción del precio y el RSI (Wilder's Smoothing/RMA).

6. **Análisis de Sentimiento IA (`analyze_sentiment.py`)**
   - Combina más de 11 indicadores técnicos (RSI, MACD, Bollinger, OBV, etc.) con escrutinio de noticias.
   - Utiliza OpenAI (GPT-4o) para emitir un veredicto técnico/fundamental.

7. **Persistencia y Base de Datos (`utils/db.py`)**
   - Integración nativa con **Supabase** para guardar señales, muros, divergencias y análisis de sentimiento histórico.

---

## ⚙️ Arquitectura Backend

El sistema se divide en procesos independientes y orquestadores:
- **API y Websockets (`api.py`)**: Sirve el feed de datos procesados en tiempo real al Frontend de Vue.
- **Workers Bateables**: Scripts como `main.py` o los `.sh` ejecutan escaneos periódicos o bajo demanda y guardan en Supabase.
- **Motor en Vivo (`live_engine.py`)**: Mantiene buffers en memoria de velas cerradas y ejecuta escáneres cuantitativos en paralelo mandando señales por Socket.

---

## 🚀 Setup Rápido

### 1. Variables de Entorno (`.env`)
```env
OPENAI_API_KEY="sk-TU_CLAVE"
TELEGRAM_TOKEN="TU_TOKEN_BOTFATHER"
TELEGRAM_CHAT_ID="TU_CHAT_ID"
SUPABASE_URL="https://xxx.supabase.co"
SUPABASE_KEY="tu_anon_key"
```

### 2. Entorno Local (Python)
```bash
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Ejecutar Live Engine (Websocket API)
```bash
cd backtesting
python api.py
```
> El servidor inicia en `http://0.0.0.0:8877` y expone el websocket de live-feed.

### 4. Scripts de Escaneo Individual / Docker
```bash
# Soportes y Resistencias
python sr_scanner.py --symbols BTC/USDT --tfs 15m 1h 4h

# Sentimiento IA On-Demand
./run_sentiment.sh --symbols BTC/USDT --timeframes 4h,1d
```

---

## 📁 Estructura del Proyecto
```
quant-trading-agent/
├── backtesting/
│   ├── api.py                   # Servidor FastAPI para UI
│   ├── live_engine.py           # Motor de paper trading y websockets
│   ├── elliott_scanner.py       # Algoritmo de Ondas de Elliott y Fibo
│   └── test_*.py                # Scripts de simulación y barridos
├── main.py                      # Orquestador cronjob
├── analyze_sentiment.py         # Análisis IA OpenAI
├── sr_scanner.py                # Soportes y Resistencias
├── smc_scanner.py               # Fair Value Gaps
├── rsi_divergence.py            # Divergencias RSI
├── utils/
│   └── db.py                    # Cliente Supabase
└── README.md
```

---

## 🗄️ Base de Datos (Supabase)

| Tabla | Descripción |
|---|---|
| `sentiment_analysis` | Sentimiento IA (triple: noticias/técnico/combinado) con semáforo de indicadores |
| `support_resistance_levels` | Muros S/R con toques, confluencias, y `source_run` |
| `rsi_divergences` | Divergencias RSI activas e históricas |
| `fair_value_gaps` | FVGs no mitigados |
| `trade_confluences` | Confluencias multi-indicador |

---

## 🚀 Roadmap Restante
- [x] Smart Money Concepts (FVGs)
- [x] Soportes/Resistencias con ATR dinámico
- [x] Divergencias RSI con lookback dinámico
- [x] Sentimiento IA con 11 indicadores técnicos
- [x] Frontend Trading Suite (Vue 3 + Lightweight Charts)
- [x] Motor Paper Trading Avanzado con Websockets
- [x] Algoritmo Ondas Elliott Multitemporal
- [ ] Integración Ondas de Elliott y Live Data a **Agente Autónomo OpenAI**
- [ ] Backtesting automatizado UI