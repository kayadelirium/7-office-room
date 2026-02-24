# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1: Build React client
# ═══════════════════════════════════════════════════════════════════════════════
FROM --platform=$BUILDPLATFORM node:20-slim AS client

WORKDIR /build/client

COPY client/package*.json ./
RUN npm install

COPY client/ .
RUN npm run build
# vite.config.js: outDir: '../static' → /build/static


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2: Python server
# ═══════════════════════════════════════════════════════════════════════════════
FROM python:3.12-slim

WORKDIR /app

# BlueZ + D-Bus — необходимо для BLE через bleak на Linux.
# На macOS Docker BLE недоступен (нет доступа к CoreBluetooth из VM).
RUN apt-get update && apt-get install -y --no-install-recommends \
    bluez \
    libdbus-1-3 \
    libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Исходный код сервера
COPY server.py scanner.py ./
COPY lights/   ./lights/
COPY speaker/  ./speaker/

# Собранный React-клиент (статика, раздаётся FastAPI)
COPY --from=client /build/static ./static

EXPOSE 8765

CMD ["python", "server.py"]
