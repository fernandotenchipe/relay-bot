# Deployment en Docker + VPS

## Preparación en tu máquina local

### 1. Crear archivo `.env` con tus datos reales
```bash
# En la carpeta telegram_relay/
nano .env
```

Agrega tus valores reales (no se commitea a Git):
```env
API_ID=123456789
API_HASH=abcdef1234567890xyz
PHONE=+34612345678
SOURCE_CHANNEL=123456789
DEST_CHANNEL=987654321
OPENAI_API_KEY=sk-proj-abc123xyz...
WORKERS=3
```

### 2. Construir la imagen Docker (local)
```bash
docker build -t telegram-relay:latest .
```

### 3. Probar localmente
```bash
docker-compose up
```

---

## Despliegue en VPS

### Opción A: Build en el VPS (recomendado para primeros despliegues)

#### 1. Conectar al VPS
```bash
ssh usuario@tu-vps-ip
```

#### 2. Instalar Docker
```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

#### 3. Clonar o copiar el proyecto
```bash
# Opción 1: Clonar desde Git (si tienes repo)
git clone https://github.com/tu-usuario/telegram-relay.git
cd telegram-relay

# Opción 2: Copiar archivos
scp -r /ruta/local/telegram_relay/ usuario@tu-vps:/home/usuario/telegram-relay
ssh usuario@tu-vps
cd ~/telegram-relay
```

#### 4. Crear archivo `.env` con tus variables reales
```bash
nano .env
```
Agrega tus valores:

#### 5. Construir y ejecutar
```bash
docker build -t telegram-relay:latest .
docker-compose up -d
```

#### 6. Ver logs
```bash
docker-compose logs -f telegram-relay
```

---

### Opción B: Push a Docker Hub (para múltiples VPS)

#### 1. En tu máquina local
```bash
# Build
docker build -t tu-usuario/telegram-relay:latest .

# Login en Docker Hub
docker login

# Push
docker push tu-usuario/telegram-relay:latest
```

#### 2. En el VPS
```bash
# Crear compose simplificado
mkdir telegram-relay
cd telegram-relay

# Crear docker-compose.yml que descargue la imagen
cat > docker-compose.yml << 'EOF'
version: '3.8'
services:
  telegram-relay:
    image: tu-usuario/telegram-relay:latest
    container_name: telegram-relay
    restart: unless-stopped
    environment:
      - API_ID=${API_ID}
      - API_HASH=${API_HASH}
      - PHONE=${PHONE}
      - SOURCE_CHANNEL=${SOURCE_CHANNEL}
      - DEST_CHANNEL=${DEST_CHANNEL}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - WORKERS=${WORKERS}
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
EOF

# Crear .env
nano .env
# (pega tus variables)

# Ejecutar
docker-compose up -d
```

---

## Mantenimiento

### Ver estado
```bash
docker-compose ps
docker-compose logs -f telegram-relay
```

### Reiniciar
```bash
docker-compose restart
```

### Detener
```bash
docker-compose down
```

### Actualizar código
```bash
# Si usas tu Dockerfile
docker build -t telegram-relay:latest .
docker-compose up -d

# Si usas Docker Hub
docker pull tu-usuario/telegram-relay:latest
docker-compose up -d
```

### Acceder a la sesión Telegram
La sesión se guarda en `./data/` dentro del contenedor y en tu máquina en `./logs/`

---

## Notas importantes

1. **Variable PHONE**: La primera vez que ejecutes, Docker puede pedir datos de 2FA. Necesitarás acceso interactivo:
   ```bash
   docker-compose run --rm telegram-relay python relay.py
   ```

2. **Persistencia**: Los volúmenes aseguran que la sesión y logs se guarden incluso si reinicia el contenedor

3. **Recursos**: El contenedor es ligero, funciona bien en VPS pequeños (1GB RAM mínimo)

4. **Auto-restart**: Si el contenedor falla, se reinicia automáticamente
