#!/usr/bin/env bash
# Cerebro grande de NOVA en un servidor Ubuntu ARM (Oracle Cloud Always Free, Ampere A1).
#
# Instala llama.cpp (compilación oficial para Linux ARM64), un modelo de 8B
# (Qwen3 8B, Q4_K_M, unos 5 GB) y Tailscale, y deja llama-server como servicio
# que solo escucha en la red privada de Tailscale, con clave.
#
# Uso, desde tu PC:
#   scp -i nova-cerebro.key instalar-cerebro.sh ubuntu@IP_PUBLICA:
#   ssh -i nova-cerebro.key ubuntu@IP_PUBLICA
#   bash instalar-cerebro.sh            (o: MODELO=14b bash instalar-cerebro.sh)
#
# Al final imprime la dirección y la clave que van en el config.json de NOVA.
set -euo pipefail

MODELO="${MODELO:-8b}"
DIR=/opt/nova-cerebro
PUERTO=8080

say() { printf '\n== %s\n' "$*"; }

if [ "$(uname -m)" != "aarch64" ] && [ "$(uname -m)" != "x86_64" ]; then
  echo "Arquitectura no soportada: $(uname -m)"; exit 1
fi
ARQ=$([ "$(uname -m)" = "aarch64" ] && echo arm64 || echo x64)

say "Paquetes del sistema"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates python3 libgomp1 iptables-persistent >/dev/null
sudo mkdir -p "$DIR/modelos"
sudo chown -R "$USER" "$DIR"

say "llama.cpp (Linux $ARQ)"
URL=$(curl -fsSL -H 'User-Agent: nova-cerebro' "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10" | python3 -c "
import json, re, sys
for r in json.load(sys.stdin):
    for a in r.get('assets', []):
        if re.fullmatch(r'llama-.*-bin-ubuntu-$ARQ\.tar\.gz', a['name']):
            print(a['browser_download_url']); sys.exit()
")
[ -n "$URL" ] || { echo "No encontré llama.cpp para Linux $ARQ"; exit 1; }
echo "   $URL"
curl -fL --retry 3 -o /tmp/llama.tar.gz "$URL"
rm -rf "$DIR/llama" && mkdir -p "$DIR/llama"
tar -xzf /tmp/llama.tar.gz -C "$DIR/llama" --strip-components=1
SERVER=$(find "$DIR/llama" -name llama-server -type f | head -1)
[ -x "$SERVER" ] || { echo "El paquete no trae llama-server"; exit 1; }
"$SERVER" --version 2>&1 | tail -1

say "Modelo Qwen3 $MODELO (Q4_K_M)"
case "$MODELO" in
  14b) REPO=Qwen/Qwen3-14B-GGUF; ARCHIVO=Qwen3-14B-Q4_K_M.gguf; CTX=6144 ;;
  *)   REPO=Qwen/Qwen3-8B-GGUF;  ARCHIVO=Qwen3-8B-Q4_K_M.gguf;  CTX=8192 ;;
esac
if [ ! -s "$DIR/modelos/$ARCHIVO" ]; then
  curl -fL --retry 3 -C - -o "$DIR/modelos/$ARCHIVO.parcial" "https://huggingface.co/$REPO/resolve/main/$ARCHIVO"
  mv "$DIR/modelos/$ARCHIVO.parcial" "$DIR/modelos/$ARCHIVO"
fi
head -c 4 "$DIR/modelos/$ARCHIVO" | grep -q GGUF || { echo "El modelo no es un GGUF válido"; exit 1; }

say "Tailscale (red privada entre tu PC y este servidor)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
if ! tailscale ip -4 >/dev/null 2>&1; then
  echo "   Abre el enlace que aparece abajo e inicia sesión con la MISMA cuenta que usarás en tu PC."
  sudo tailscale up --hostname nova-cerebro
fi
IP=$(tailscale ip -4 | head -1)
# Las imágenes de Ubuntu de Oracle bloquean todo menos SSH; se abre solo la red de Tailscale.
sudo iptables -C INPUT -i tailscale0 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT -i tailscale0 -j ACCEPT
sudo netfilter-persistent save >/dev/null

say "Servicio llama-server"
CLAVE_ARCHIVO="$DIR/clave"
[ -s "$CLAVE_ARCHIVO" ] || python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$CLAVE_ARCHIVO"
chmod 600 "$CLAVE_ARCHIVO"
HILOS=$(nproc)
sudo tee /etc/systemd/system/nova-cerebro.service >/dev/null <<SERVICIO
[Unit]
Description=Cerebro grande de NOVA (llama-server)
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
User=$USER
ExecStart=/bin/bash -c 'exec $SERVER -m $DIR/modelos/$ARCHIVO --host \$(tailscale ip -4 | head -1) --port $PUERTO -c $CTX -t $HILOS --alias nova-cerebro --reasoning-budget 0 -ctk q8_0 -ctv q8_0 -fa on --api-key-file $CLAVE_ARCHIVO'
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICIO
sudo systemctl daemon-reload
sudo systemctl enable --now nova-cerebro >/dev/null
printf '   esperando a que cargue el modelo'
for _ in $(seq 1 90); do
  if curl -fs "http://$IP:$PUERTO/health" >/dev/null 2>&1; then break; fi
  printf '.'; sleep 2
done
echo
curl -fs "http://$IP:$PUERTO/health" >/dev/null || { echo "No arrancó. Revisa: journalctl -u nova-cerebro -n 50"; exit 1; }

say "Listo"
cat <<FIN
El cerebro grande de NOVA corre en este servidor, visible solo en tu red de Tailscale.

En tu PC, agrega esto a %APPDATA%\\NOVA\\config.json (si ya tiene llaves, solo añade las dos líneas):

  "cerebro_endpoint": "http://$IP:$PUERTO",
  "cerebro_clave": "$(cat "$CLAVE_ARCHIVO")"

La clave es secreta: no la compartas ni la pegues en chats.
FIN
