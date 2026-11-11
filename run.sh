#!/usr/bin/env bash
# Arranca el servidor (y opcionalmente el túnel) en segundo plano.
set -e
cd "$(dirname "$0")"
PORT=${PORT:-8765}
(cd app && exec nohup python3 -m uvicorn main:app --host 127.0.0.1 --port $PORT --proxy-headers --forwarded-allow-ips='*' > ../server.log 2>&1) & echo $! > server.pid
echo "Servidor en http://127.0.0.1:$PORT (PID $(cat server.pid))"
if [ "$1" = "--tunel" ]; then
  nohup ./bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT > tunnel.log 2>&1 & echo $! > tunnel.pid
  sleep 10; grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" tunnel.log | head -1
fi
