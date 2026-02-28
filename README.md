# 📈 Motor Cuantitativo & Agente de Trading (Quant Trading Agent)

Este repositorio contiene un conjunto de herramientas de análisis cuantitativo (*Quant*) y de sentimiento de mercado diseñadas para ejecutarse en contenedores Docker, optimizadas para arquitecturas ARM (Raspberry Pi) o entornos locales.

El objetivo final de este proyecto es servir como el "cinturón de herramientas" matemáticas y analíticas para un agente autónomo de Inteligencia Artificial (ej. OpenClaw), permitiéndole tomar decisiones basadas en datos institucionales reales y no en alucinaciones del LLM.

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

Para evitar reconstruir la imagen de Docker con cada cambio de código en desarrollo, utilizamos un volumen montado `-v $(pwd):/app` en la ejecución de los scripts matemáticos.

### 1. 🎯 sr_scanner.py - Escáner de Soportes y Resistencias Institucionales
Detecta zonas de liquidez utilizando un algoritmo de *clustering* unidimensional sobre picos y valles fractales. 
**Característica clave:** Incorpora **ATR Dinámico**. Se auto-calibra evaluando la volatilidad (Average True Range) del activo, reduciendo el margen de agrupación para activos estables (BTC) y ampliándolo para altcoins volátiles (PEPE).

**Uso básico:**
```bash
sudo docker run --rm -v $(pwd):/app motor-trading python sr_scanner.py --symbols BTC/USDT ETH/USDT
```

**Parámetros configurables:**
* `--symbols` *(Obligatorio)*: Lista separada por espacios de los pares a analizar (ej. `BTC/USDT SOL/USDT`).
* `--tfs` *(Opcional)*: Temporalidades a escanear para buscar confluencia. Default: `15m 1h 4h 1d 1w`.
* `--limit` *(Opcional)*: Cantidad de velas históricas a evaluar por temporalidad. Default: `1000`.
* `--max` *(Opcional)*: Top `N` de muros más cercanos a mostrar (hacia arriba y abajo). Default: `5`.

**Ejemplo Avanzado (Solo temporalidades Macro):**
```bash
sudo docker run --rm -v $(pwd):/app motor-trading python sr_scanner.py --symbols BNB/USDT --tfs 1d 1w --limit 500 --max 3
```

### 2. 🔎 rsi_divergence.py - Radar de Divergencias RSI (Lookback Dinámico)
Escanea el mercado en busca de divergencias regulares (Alcistas y Bajistas) comparando la estructura del precio con el momentum (RSI).
**Característica clave:** Utiliza un **Lookback Dinámico** (ventana de memoria). En lugar de comparar solo el pico actual con el anterior, busca el *verdadero* máximo/mínimo macro dentro de las últimas 60 velas, ignorando el "ruido" y los falsos retrocesos intermedios.

**Uso básico (Modo Francotirador Diario):**
Muestra **únicamente** las divergencias que están activas hoy (que no han expirado).
```bash
sudo docker run --rm -v $(pwd):/app motor-trading python rsi_divergence.py --symbols SOL/USDT XRP/USDT
```

**Parámetros configurables:**
* `--symbols` *(Obligatorio)*: Lista de pares a escanear.
* `--tfs` *(Opcional)*: Temporalidades. Default: `15m 1h 4h 1d`.
* `--historical` *(Opcional)*: Flag (bandera). Si se incluye, el script imprimirá todo el historial reciente para realizar *backtesting* visual, etiquetando las divergencias como `ACTIVA 🔥` o `HISTÓRICA 🕰️`.

**Ejemplo Avanzado (Backtesting):**
```bash
sudo docker run --rm -v $(pwd):/app motor-trading python rsi_divergence.py --symbols BTC/USDT --historical
```

### 3. 🧠 fetch_data.py - Motor de Sentimiento y Alertas (Piloto Automático)
Script rígido diseñado para ejecutarse vía Cronjob. Descarga el contexto técnico del mercado y titulares de Google News, agrupándolos en un solo *prompt batch* que envía a GPT-4o-mini (OpenAI). La IA pondera la matemática y las noticias para emitir un dictamen estructural, el cual se formatea y se envía a Telegram.

**Uso:**
No requiere parámetros por consola. Para ejecutarlo manualmente con las variables de entorno cargadas:
```bash
./run_bot.sh
```
*(Asegúrate de haberle dado permisos de ejecución: `chmod +x run_bot.sh`)*.

---

## 🚀 Próximos Pasos (Roadmap)
- [ ] **Smart Money Concepts (SMC):** Módulo detector de ineficiencias de mercado (Fair Value Gaps - FVG) y Order Blocks institucionales.
- [ ] **Orquestación de Agente:** Integración de estas herramientas como *Skills* para ejecución autónoma mediante **OpenClaw**.