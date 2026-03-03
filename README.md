# 📈 Motor Cuantitativo & Agente de Trading (Quant Trading Agent)

Motor de análisis cuantitativo y sentimiento de mercado diseñado para ejecutarse en Docker (ARM/Raspberry Pi o local). Sirve como cinturón de herramientas para un agente autónomo de IA (OpenClaw).

---

## ⚙️ Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Container                      │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │ sr_scanner   │  │ rsi_diverge │  │ smc_scanner    │  │
│  │ (Muros ATR)  │  │ (Divergen.) │  │ (FVGs)         │  │
│  └──────┬───────┘  └──────┬──────┘  └───────┬────────┘  │
│         │                 │                  │           │
│  ┌──────┴─────────────────┴──────────────────┴────────┐  │
│  │              main.py (Orquestador)                 │  │
│  │         + analyze_sentiment.py (On-Demand)         │  │
│  └──────────────────────┬─────────────────────────────┘  │
│                         │                                │
│  ┌──────────────────────┴────────────────────────────┐   │
│  │              utils/db.py → Supabase               │   │
│  └───────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                          │
              ┌───────────┴──────────────┐
              │   Frontend (Vue 3)       │
              │   quant-trading-ui/      │
              └──────────────────────────┘
```

---

## 🚀 Setup Rápido

### 1. Variables de Entorno
Crea `.env` en la raíz:
```env
OPENAI_API_KEY="sk-TU_CLAVE"
TELEGRAM_TOKEN="TU_TOKEN_BOTFATHER"
TELEGRAM_CHAT_ID="TU_CHAT_ID"
SUPABASE_URL="https://xxx.supabase.co"
SUPABASE_KEY="tu_anon_key"
```

### 2. Construir Docker
```bash
sudo docker build -t motor-trading .
```

### 3. Ejecutar
```bash
# Orquestador completo (Muros + FVGs + Divergencias + Confluencias)
./run_bot.sh

# Análisis de sentimiento on-demand (1 llamada GPT)
./run_sentiment.sh --symbols BTC/USDT --timeframes 4h,1d --telegram
```

---

## 🛠️ Scripts Disponibles

### 🎯 `sr_scanner.py` — Muros de Soporte/Resistencia
Detecta zonas de liquidez con clustering sobre picos/valles fractales + ATR dinámico.
```bash
sudo docker run --rm --env-file .env -v $(pwd):/app motor-trading \
  python sr_scanner.py --symbols BTC/USDT --tfs 15m 1h 4h 1d 1w --limit 1000 --max 10
```

### 🔎 `rsi_divergence.py` — Divergencias RSI
Escanea divergencias regulares con lookback dinámico.
```bash
sudo docker run --rm --env-file .env -v $(pwd):/app motor-trading \
  python rsi_divergence.py --symbols BTC/USDT --tfs 15m 1h 4h 1d
```

### 🐋 `smc_scanner.py` — Fair Value Gaps (SMC)
Detecta FVGs no mitigados y mide distancia % al precio actual.
```bash
sudo docker run --rm --env-file .env -v $(pwd):/app motor-trading \
  python smc_scanner.py --symbols BTC/USDT --tfs 1h 4h 1d --limit 500
```

### 🧠 `fetch_data.py` — Sentimiento IA (Legacy)
Motor original de sentimiento: RSI + SMA20 + noticias → GPT-4o-mini. Ejecutado por `main.py`.

### 📊 `analyze_sentiment.py` — Sentimiento IA On-Demand ⭐ NUEVO
Análisis técnico avanzado con **11 indicadores** por temporalidad + 3 veredictos (Noticias / Técnico / Combinado).

```bash
# Un solo símbolo, TFs específicos
./run_sentiment.sh --symbols BTC/USDT --timeframes 4h,1d --telegram

# Múltiples símbolos
./run_sentiment.sh --symbols BTC/USDT,ETH/USDT --timeframes 15m,1h,4h

# Solo análisis técnico (sin noticias)
./run_sentiment.sh --symbols BTC/USDT --timeframes 1d --no-news

# Todos los TFs por defecto
./run_sentiment.sh --symbols BTC/USDT
```

**Indicadores calculados:**
| Categoría | Indicadores |
|---|---|
| Trend | SMA 20, 55, 100, 200 · EMA 21 |
| Momentum | RSI(14) · MACD(12,26,9) · Estocástico(14,3) |
| Volatilidad | Bollinger(20,2) · ATR(14) · OBV |

**Costos:** 1 sola llamada a GPT-4o-mini por invocación, sin importar cuántos TFs.

### 🎼 `main.py` — Orquestador
Ejecuta secuencialmente: SR Scanner → RSI Divergences → SMC Scanner → Confluencias → Telegram.
```bash
./run_bot.sh
# Equivale a: sudo docker run --rm --env-file .env motor-trading
```

---

## 📁 Estructura
```
quant-trading-agent/
├── main.py                  # Orquestador principal
├── analyze_sentiment.py     # Sentimiento on-demand (OpenClaw)
├── fetch_data.py            # Sentimiento legacy
├── sr_scanner.py            # Muros S/R con ATR
├── rsi_divergence.py        # Divergencias RSI
├── smc_scanner.py           # Fair Value Gaps
├── utils/
│   └── db.py                # Cliente Supabase
├── run_bot.sh               # Ejecuta el orquestador en Docker
├── run_sentiment.sh          # Ejecuta sentimiento on-demand en Docker
├── Dockerfile
├── requirements.txt
└── .env                     # Credenciales (gitignored)
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

## 🚀 Roadmap
- [x] Smart Money Concepts (FVGs)
- [x] Soportes/Resistencias con ATR dinámico
- [x] Divergencias RSI con lookback dinámico
- [x] Integración Supabase
- [x] Sentimiento IA con 11 indicadores técnicos
- [x] Frontend Trading Suite (Vue 3 + Lightweight Charts)
- [ ] Integración OpenClaw como agente autónomo
- [ ] Backtesting automatizado