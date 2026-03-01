# Usamos una versión ligera de Python compatible con la arquitectura ARM de la Raspberry Pi
FROM python:3.11-slim

# Evita que Python escriba archivos .pyc y fuerza a que la salida de consola se vea en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Creamos un directorio de trabajo dentro del contenedor
WORKDIR /app

# Copiamos primero las dependencias (para optimizar la caché de Docker)
COPY requirements.txt .

# Instalamos las librerías financieras
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos todos los scripts y la carpeta utils
COPY . .

# Comando por defecto al encender el contenedor (ejecuta el Orquestador con BTC y ETH por defecto)
CMD ["python", "main.py", "--symbols", "BTC/USDT", "ETH/USDT"]
