# all_connect — registro de IPs del laboratorio

Los equipos de un laboratorio suelen obtener su IP por DHCP, así que la dirección
cambia y los `ssh` a mano dejan de funcionar. Este proyecto resuelve eso:

1. Cada máquina ejecuta un **agente** que detecta cambios de IP y los reporta por HTTPS.
2. Un **servidor** central (FastAPI) guarda el nombre → IP en un JSON.
3. Un **cliente** (`labssh`) consulta el servidor y abre la sesión SSH por nombre.

```
┌──────────┐   POST /report (Bearer token)   ┌──────────────┐
│ agente   │ ──────────────────────────────> │  server.py   │
│ (vm1..N) │                                 │  hosts.json  │
└──────────┘                                 └──────┬───────┘
                                                    │ GET /hosts
                                             ┌──────▼───────┐
                                             │   labssh     │──> ssh user@ip
                                             └──────────────┘
```

## Componentes

| Archivo | Qué hace |
| --- | --- |
| `server.py` | API FastAPI: recibe reportes y expone el inventario de hosts. |
| `ip-reporter.sh` | Agente: vigila la IP local y la reporta cuando cambia (o cada `HEARTBEAT`). |
| `lab-agent-setup.sh` | Instalador idempotente del agente: prepara `sshd` y el servicio systemd. |
| `labssh` | Cliente: `list` muestra hosts; `<nombre>` lanza SSH a la IP actual. |
| `requirements.txt` | Dependencias de Python del servidor. |

## Requisitos

**Servidor**

- Python 3.12+ (desarrollado con 3.14)
- `fastapi`, `uvicorn`
- Un certificado TLS (se usa HTTPS; el token viaja en la cabecera `Authorization`)

**Agentes**

- Linux con `bash`, `ip` (iproute2), `curl`, `systemd`
- `ssh` para el acceso remoto
- `jq` solo en la máquina que use `labssh`

## Puesta en marcha del servidor

```bash
python3 -m venv env
env/bin/pip install -r requirements.txt

# Token compartido: guárdalo y repártelo a los agentes
export LAB_TOKEN="$(openssl rand -hex 32)"

# Certificado autofirmado (sustitúyelo por uno de tu CA si la tienes)
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout key.pem -out cert.pem -subj "/CN=lab-server"

env/bin/uvicorn server:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile key.pem --ssl-certfile cert.pem
```

La base de datos es `hosts.json` (se crea sola) y las escrituras son atómicas, así
que un corte no la deja corrupta.

### Servicio systemd (opcional)

```ini
# /etc/systemd/system/lab-server.service
[Unit]
Description=Registro de IPs del laboratorio
After=network-online.target

[Service]
WorkingDirectory=/opt/all_connect
Environment=LAB_TOKEN=pon-aqui-el-token
Environment=LAB_DB=/opt/all_connect/hosts.json
ExecStart=/opt/all_connect/env/bin/uvicorn server:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile /opt/all_connect/key.pem --ssl-certfile /opt/all_connect/cert.pem
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now lab-server
```

## Configurar un agente

Copia `lab-agent-setup.sh` e `ip-reporter.sh` al equipo y ejecuta como root:

```bash
LAB_TOKEN=... ./lab-agent-setup.sh \
  --user dev \
  --key-file ~/.ssh/id_ed25519.pub \
  --server-url https://lab-server:8443 \
  --name vm1 \
  --cacert ca.pem
```

| Opción | Descripción |
| --- | --- |
| `--user` | Usuario al que se le instala la clave pública. |
| `--key-file` | Clave pública a autorizar en `authorized_keys`. |
| `--key-only` | Desactiva autenticación por contraseña (solo clave). |
| `--server-url` | URL del servidor, p. ej. `https://lab-server:8443`. |
| `--name` | Nombre con el que se registra el host (por defecto, `hostname`). |
| `--cacert` | CA para validar el certificado del servidor. |
| `--no-reporter` | Solo configura SSH, no instala el reporter. |

El script es idempotente: se puede volver a ejecutar sin duplicar claves ni
directivas. Instala `/etc/lab-agent.conf` (modo `600`) y el servicio
`lab-ip-reporter`. El token se lee de `LAB_TOKEN`, no por argumento, para que no
quede visible en `ps`.

### Configuración del reporter

`/etc/lab-agent.conf`:

```ini
SERVER_URL=https://lab-server:8443
TOKEN=...
NAME=vm1
CACERT=/etc/lab-agent-ca.pem   # opcional
# IFACE=eth0                   # opcional: forzar interfaz
# HEARTBEAT=300                # reenvía aunque no cambie la IP
# POLL=30                      # reintento si no hay eventos de red
```

## Uso del cliente

`~/.config/labssh.conf`:

```ini
SERVER_URL=https://lab-server:8443
TOKEN=...
CACERT=/etc/lab-agent-ca.pem   # opcional
DEFAULT_USER=dev               # opcional
```

```bash
./labssh list              # nombre, IP y segundos desde el último reporte
./labssh vm1               # ssh dev@<ip actual>
./labssh vm1 root -p 2222  # usuario y argumentos ssh extra
```

## API

Todas las rutas requieren `Authorization: Bearer <LAB_TOKEN>`.

| Método | Ruta | Descripción |
| --- | --- | --- |
| `POST` | `/report` | Cuerpo `{"name": "...", "ip": "..."}`. Responde `{"ok": true, "changed": bool}`. |
| `GET` | `/hosts` | Todo el inventario: `{nombre: {ip, seen_from, updated}}`. |
| `GET` | `/hosts/{name}` | Un host; `404` si no existe. |

`seen_from` es la IP de origen vista por el servidor (útil detrás de NAT) y
`updated` es un timestamp Unix.

```bash
curl -fsS -H "Authorization: Bearer $TOKEN" \
  -d '{"name":"vm1","ip":"10.0.0.5"}' https://lab-server:8443/report
```

## Variables de entorno

| Variable | Componente | Por defecto | Descripción |
| --- | --- | --- | --- |
| `LAB_TOKEN` | servidor / instalador | — (obligatoria) | Token compartido para autenticar. |
| `LAB_DB` | servidor | `./hosts.json` | Ruta del inventario JSON. |
| `LAB_CONF` | reporter | `/etc/lab-agent.conf` | Config alternativa del agente. |
| `LABSSH_CONF` | cliente | `~/.config/labssh.conf` | Config alternativa del cliente. |
| `SERVER_URL`, `TOKEN`, `NAME` | reporter | — (obligatorias) | Destino, credencial y nombre del host. |

## Notas de seguridad

- No se aceptan nombres fuera de `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$` ni IPs inválidas.
- El token se compara en tiempo constante (`secrets.compare_digest`).
- El instalador deja `PermitRootLogin no` y, con `--key-only`, contraseñas desactivadas.
- `key.pem`, `cert.pem`, `hosts.json` y las configuraciones con token están en
  `.gitignore`: **no los subas al repositorio**.

## Estructura

```
all_connect/
├── server.py             # API FastAPI
├── ip-reporter.sh        # agente de IP
├── lab-agent-setup.sh    # instalador del agente
├── labssh                # cliente SSH por nombre
├── requirements.txt
├── env/                  # venv local (ignorado por git)
└── hosts.json            # inventario generado en ejecución (ignorado por git)
```
