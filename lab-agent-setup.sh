#!/usr/bin/env bash
# Configura SSH (idempotente) e instala el reporter de IP como servicio systemd.
# Uso (root):
#   LAB_TOKEN=... ./lab-agent-setup.sh --user dev --key-file ~/.ssh/id_ed25519.pub \
#       --server-url https://easy-connection.onrender.com --name vm1 [--cacert ca.pem] [--key-only] [--no-reporter]
# El token se lee de la variable LAB_TOKEN (no por argumento: se vería en `ps`).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Ejecuta como root"; exit 1; }

USER_NAME="" KEY_FILE="" KEY_ONLY=0 REPORTER=1 SERVER_URL="" NAME="$(hostname)" CACERT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) USER_NAME="$2"; shift 2;;
    --key-file) KEY_FILE="$2"; shift 2;;
    --key-only) KEY_ONLY=1; shift;;
    --server-url) SERVER_URL="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;
    --cacert) CACERT="$2"; shift 2;;
    --no-reporter) REPORTER=0; shift;;
    *) echo "Opción desconocida: $1"; exit 1;;
  esac
done
HERE="$(cd "$(dirname "$0")" && pwd)"

# 1. openssh-server
if ! command -v sshd >/dev/null && [[ ! -x /usr/sbin/sshd ]]; then
  if command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y openssh-server
  elif command -v dnf >/dev/null; then dnf install -y openssh-server
  elif command -v pacman >/dev/null; then pacman -S --noconfirm openssh
  else echo "Gestor de paquetes no soportado"; exit 1; fi
fi
SSHD=$(command -v sshd || echo /usr/sbin/sshd)

# 2. clave pública
if [[ -n "$KEY_FILE" ]]; then
  [[ -n "$USER_NAME" ]] || { echo "--key-file requiere --user"; exit 1; }
  home=$(getent passwd "$USER_NAME" | cut -d: -f6)
  install -d -m 700 -o "$USER_NAME" "$home/.ssh"
  touch "$home/.ssh/authorized_keys"
  grep -qxFf "$KEY_FILE" "$home/.ssh/authorized_keys" || cat "$KEY_FILE" >> "$home/.ssh/authorized_keys"
  chown "$USER_NAME" "$home/.ssh/authorized_keys"; chmod 600 "$home/.ssh/authorized_keys"
fi
if [[ $KEY_ONLY -eq 1 && -z "$KEY_FILE" ]]; then
  echo "--key-only sin --key-file te dejaría sin acceso. Abortando."; exit 1
fi

# 3. config sshd vía drop-in (sshd usa la PRIMERA directiva que encuentra,
#    por eso el Include debe estar al inicio de sshd_config)
CFG=/etc/ssh/sshd_config; DROP=/etc/ssh/sshd_config.d/10-lab.conf
mkdir -p /etc/ssh/sshd_config.d
grep -Eq '^\s*Include\s+/etc/ssh/sshd_config\.d/' "$CFG" || sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' "$CFG"
cat > "$DROP" <<CONF
PubkeyAuthentication yes
PasswordAuthentication $([[ $KEY_ONLY -eq 1 ]] && echo no || echo yes)
PermitRootLogin no
CONF
if ! "$SSHD" -t; then echo "sshd -t falló; revierto"; rm -f "$DROP"; exit 1; fi

# 4. servicio (Debian/Ubuntu: ssh; resto: sshd)
SVC=sshd; systemctl cat ssh.service >/dev/null 2>&1 && SVC=ssh
systemctl enable --now "$SVC"; systemctl reload "$SVC"

# 5. firewall (solo si ya está activo)
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then ufw allow OpenSSH || ufw allow 22/tcp; fi
if command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-service=ssh && firewall-cmd --reload
fi

# 6. reporter
if [[ $REPORTER -eq 1 ]]; then
  : "${LAB_TOKEN:?Define LAB_TOKEN}"; : "${SERVER_URL:?Falta --server-url}"
  install -m 755 "$HERE/ip-reporter.sh" /usr/local/bin/ip-reporter.sh
  [[ -n "$CACERT" ]] && install -m 644 "$CACERT" /etc/lab-agent-ca.pem
  umask 077
  cat > /etc/lab-agent.conf <<CONF
SERVER_URL=$SERVER_URL
TOKEN=$LAB_TOKEN
NAME=$NAME
$([[ -n "$CACERT" ]] && echo "CACERT=/etc/lab-agent-ca.pem")
CONF
  cat > /etc/systemd/system/lab-ip-reporter.service <<UNIT
[Unit]
Description=Reporta cambios de IP al servidor del laboratorio
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/ip-reporter.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload; systemctl enable --now lab-ip-reporter.service
fi
echo "Listo. SSH activo; reporter: $([[ $REPORTER -eq 1 ]] && echo activo || echo omitido)"
