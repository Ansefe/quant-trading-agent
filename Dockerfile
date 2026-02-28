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

# Copiamos nuestro script (el fetch_data.py que creamos antes)
COPY fetch_data.py .

# Comando por defecto al encender el contenedor
CMD ["python", "fetch_data.py"]
