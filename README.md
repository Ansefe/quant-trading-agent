# 📈 Motor Cuantitativo & Agente de Trading (Quant Trading Agent)

Este repositorio contiene un conjunto de herramientas de análisis cuantitativo (*Quant*) y de sentimiento de mercado diseñadas para ejecutarse en contenedores Docker, optimizadas para arquitecturas ARM (Raspberry Pi) o entornos locales.

El objetivo final de este proyecto es servir como el "cinturón de herramientas" matemáticas y analíticas para un agente autónomo de Inteligencia Artificial (ej. OpenClaw), permitiéndole tomar decisiones basadas en datos institucionales reales.

---

## ⚙️ Arquitectura y Configuración Inicial

Todo el entorno está paquetizado en Docker para aislar las dependencias (Python, SciPy, NumPy, Pandas, CCXT) del sistema operativo anfitrión.

### 1. Clonar y Configurar
```bash
git clone [https://github.com/TU_USUARIO/quant-trading-agent.git](https://github.com/TU_USUARIO/quant-trading-agent.git)
cd quant-trading-agent
```

### 2. Variables de Entorno (Solo para fetch_data.py)
Crea un archivo oculto llamado `.env` en la raíz del proyecto (este archivo está ignorado en Git por seguridad) y añade tus credenciales:
```env
OPENAI_API_KEY="sk-TU_CLAVE"
TELEGRAM_TOKEN="TU_TOKEN_BOTFATHER"
TELEGRAM_CHAT_ID="TU_CHAT_ID"
```

### 3. Construir la Imagen de Docker
```bash
sudo docker build -t motor-trading .
```

---

## 🛠️ Descripción y Uso de los Scripts

*Nota: Todos los scripts de escaneo ajustan la hora internamente a la zona UTC-5 (Colombia) para facilitar la sincronización con TradingView.*

Para evitar reconstruir la imagen de Docker con cada cambio de código en desarrollo, utilizamos un volumen montado `-v $(pwd):/app` en la ejecución de los scripts matemáticos.

### 1. 🎯 sr_scanner.py - Escáner de Soportes y Resistencias Institucionales
Detecta zonas de liquidez utilizando un algoritmo de *clustering* unidimensional sobre picos y valles fractales. 
**Característica clave:** Incorpora **ATR Dinámico**. Se auto-calibra evaluando la volatilidad (Average True Range) del activo.

**Uso básico:**
```bash
sudo docker run --rm -v $(pwd):/app motor-trading python sr_scanner.py --symbols BTC/USDT ETH/USDT
```

**Parámetros configurables:**
* `--symbols` *(Obligatorio)*: Lista de pares a analizar.
* `--tfs` *(Opcional)*: Temporalidades a escanear. Default: `15m 1h 4h 1d 1w`.
* `--limit` *(Opcional)*: Cantidad de velas históricas a evaluar. Default: `1000`.
* `--max` *(Opcional)*: Top `N` de muros más cercanos a mostrar. Default: `5`.

### 2. 🔎 rsi_divergence.py - Radar de Divergencias RSI (Lookback Dinámico)
Escanea el mercado en busca de divergencias regulares comparando la estructura del precio con el momentum (RSI).
**Característica clave:** Utiliza un **Lookback Dinámico** para encontrar el *verdadero* máximo/mínimo macro histórico y evitar falsas señales.

**Uso básico (Modo Francotirador Diario):**
```bash
sudo docker run --rm -v $(pwd):/app motor-trading python rsi_divergence.py --symbols SOL/USDT XRP/USDT
```

**Parámetros configurables:**
* `--symbols` *(Obligatorio)*: Lista de pares a escanear.
* `--tfs` *(Opcional)*: Temporalidades. Default: `15m 1h 4h 1d`.
* `--historical` *(Opcional)*: Muestra el historial completo para realizar *backtesting* visual.

### 3. 🐋 smc_scanner.py - Cazador de Liquidez (Smart Money Concepts)
Detecta Ineficiencias de Mercado (Fair Value Gaps - FVG) históricas y filtra únicamente las zonas que no han sido mitigadas (rellenadas) por el precio actual. 
**Característica clave:** Mide la distancia porcentual exacta entre el precio actual y los vacíos institucionales abiertos que actúan como imanes de liquidez.

**Uso básico:**
```bash
sudo docker run --rm -v $(pwd):/app motor-trading python smc_scanner.py --symbols BTC/USDT ETH/USDT
```

**Parámetros configurables:**
* `--symbols` *(Obligatorio)*: Lista de pares a escanear.
* `--tfs` *(Opcional)*: Temporalidades (recomendado TFs altos). Default: `1h 4h 1d`.
* `--limit` *(Opcional)*: Cantidad de velas históricas a evaluar. Default: `500`.

### 4. 🧠 fetch_data.py - Motor de Sentimiento y Alertas
Descarga el contexto técnico del mercado y titulares de Google News, agrupándolos en un solo *prompt batch* que envía a GPT-4o-mini (OpenAI). 

**Uso:**
```bash
./run_bot.sh
```

---

## 🚀 Roadmap Quant
- [x] **Smart Money Concepts (SMC):** Módulo detector de ineficiencias de mercado (Fair Value Gaps - FVG) y Order Blocks institucionales.
- [ ] **Orquestación de Agente:** Integración de estas herramientas como *Skills* para ejecución autónoma mediante **OpenClaw**.
- [ ] **Integración con Base de Datos:** Guardado estructurado de los *outputs* en **Supabase** para alimentar métricas históricas de una *Trading Suite*.