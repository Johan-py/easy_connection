#!/usr/bin/env bash
# Detecta cambios de IP y los reporta por HTTPS. Config en /etc/lab-agent.conf:
#   SERVER_URL=https://easy-connection.onrender.com   TOKEN=...   NAME=maquina1
#   (opcional) IFACE=eth0  CACERT=/etc/lab-agent-ca.pem  HEARTBEAT=300  POLL=30
set -u
CONF="${LAB_CONF:-/etc/lab-agent.conf}"
# shellcheck disable=SC1090
source "$CONF"
: "${SERVER_URL:?}" "${TOKEN:?}" "${NAME:?}"
HEARTBEAT="${HEARTBEAT:-300}"   # reenvía aunque no cambie (indica que sigue viva)
POLL="${POLL:-30}"              # reintento máximo si no hay eventos de red
STATE_DIR="${STATE_DIR:-/var/lib/lab-agent}"
mkdir -p "$STATE_DIR"
STATE="$STATE_DIR/last_ip"

log() { echo "[ip-reporter] $*" >&2; }

get_ip() {
  local ip=""
  if [[ -n "${IFACE:-}" ]]; then
    ip=$(ip -4 -o addr show dev "$IFACE" scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)
  else
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
    # laboratorio aislado sin ruta por defecto: primera IP global
    [[ -z "$ip" ]] && ip=$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)
  fi
  echo "$ip"
}

send() {
  local ip="$1"
  curl -fsS --max-time 10 \
    ${CACERT:+--cacert "$CACERT"} \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"name\":\"$NAME\",\"ip\":\"$ip\"}" "$SERVER_URL/report" >/dev/null
}

last=$(cat "$STATE" 2>/dev/null || true)
last_sent=0
while true; do
  ip=$(get_ip)
  now=$(date +%s)
  if [[ -n "$ip" ]] && { [[ "$ip" != "$last" ]] || (( now - last_sent >= HEARTBEAT )); }; then
    if send "$ip"; then
      [[ "$ip" != "$last" ]] && log "IP reportada: ${last:-ninguna} -> $ip"
      last="$ip"; last_sent=$now; echo "$ip" > "$STATE"
    else
      log "fallo al enviar $ip; reintento en ${POLL}s"
    fi
  fi
  # espera un evento de red (cambio de dirección/ruta) o el timeout
  timeout "$POLL" ip monitor address route 2>/dev/null | head -n1 >/dev/null
  sleep 2   # debounce: DHCP genera varios eventos seguidos
done
