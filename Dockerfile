FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema si es necesario
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la aplicación
COPY relay.py .
COPY get_ids.py .

# Crear directorio para logs y sesiones
RUN mkdir -p /app/data

# Variables de entorno por defecto (serán sobrescritas)
ENV WORKERS=3

# Ejecutar la aplicación
CMD ["python", "relay.py"]
