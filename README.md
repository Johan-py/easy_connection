# all_connect — registro de IPs del laboratorio

Los equipos de un laboratorio suelen obtener su IP por DHCP, así que la dirección
cambia y los `ssh` a mano dejan de funcionar. Este proyecto resuelve eso con dos
actores y **solo HTTP**:

1. **Actor 2 — máquina del lab**: detecta su IP y la reporta con `POST /report`.
2. **Actor 1 — servidor desplegado**: únicamente recibe, guarda y muestra las
   entradas. No enruta, no resuelve nombres, no abre puertos UDP ni se conecta de
   vuelta al laboratorio.

```
┌──────────┐   POST /report (Bearer token)   ┌──────────────┐
│ agente   │ ──────────────────────────────> │  server.py   │
│ (vm1..N) │                                 │  hosts.json  │
└──────────┘                                 └──────┬───────┘
                                                    │ GET / (panel)
                                                    │ GET /hosts (JSON)
                                             ┌──────▼───────┐
                                             │ navegador o  │
                                             │   labssh     │──> ssh user@ip
                                             └──────────────┘
```

## Componentes

| Archivo | Qué hace |
| --- | --- |
| `server.py` | API FastAPI: recibe reportes, guarda el historial y sirve el panel. |
| `render.yaml` | Blueprint de Render: un único web service HTTP. |
| `ip-reporter.sh` | Agente: vigila la IP local y la reporta cuando cambia (o cada `HEARTBEAT`). |
| `lab-agent-setup.sh` | Instalador idempotente del agente: prepara `sshd` y el servicio systemd. |
| `labssh` | Cliente: `list` muestra hosts; `<nombre>` lanza SSH a la IP actual. |
| `requirements.txt` | Dependencias de Python del servidor. |

## Desplegar en Render

### Opción A — Blueprint (recomendado)

1. Sube el repositorio a GitHub/GitLab.
2. En Render: **New → Blueprint** y elige el repo. Render lee `render.yaml` y crea
   el web service.
3. Cuando termine el deploy, entra en **Dashboard → allconnect → Environment** y
   copia el valor generado de **`LAB_TOKEN`**. Ese es el token que usarán los
   agentes y `labssh`.

### Opción B — manual

Si prefieres crear el servicio a mano:

| Campo | Valor |
| --- | --- |
| Runtime | Python |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn server:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'` |
| Health Check Path | `/healthz` |

Variables de entorno:

| Variable | Valor |
| --- | --- |
| `LAB_TOKEN` | un secreto largo (o deja que Render lo genere) |
| `LAB_DB` | `/tmp/hosts.json` |
| `LAB_HISTORY` | `20` |
| `PYTHON_VERSION` | `3.13.5` |

La URL queda como `https://allconnect-xxxx.onrender.com`. **El TLS lo termina el
proxy de Render**: el contenedor solo habla HTTP en `$PORT`, no configures
certificados ni `--ssl-keyfile`.

`--proxy-headers --forwarded-allow-ips='*'` hace que `seen_from` guarde la IP
pública real del agente (`X-Forwarded-For`) y no la del proxy.

### Límites del plan free (importante)

- El servicio **se duerme a los 15 minutos sin tráfico entrante**. El agente
  reenvía un latido cada `HEARTBEAT=300` segundos por defecto, así que mientras
  haya al menos una máquina reportando, el servicio sigue despierto.
- La **primera petición tras un periodo dormido tarda ~1 minuto** (Render arranca
  el contenedor). El agente reintenta solo, y el panel/navegador puede tardar en
  cargar la primera vez; las siguientes son inmediatas.
- El filesystem es **efímero**: `hosts.json` (en `/tmp`) se pierde al dormir,
  reiniciar o redeployar. Para este laboratorio es aceptable (los agentes vuelven
  a reportar en el siguiente latido). Si necesitas persistencia, adjunta un disco
  de pago en `/var/data` y usa `LAB_DB=/var/data/hosts.json`, o cambia a Postgres.
- Render puede reiniciar el servicio en cualquier momento (también borra el JSON).
- El plan free da 750 horas de instancia al mes; un servicio siempre despierto
  consume ~730 h, así que no conviene tener otros servicios free encendidos.
- Un solo proceso: no escales a varias instancias con `hosts.json` compartido.

## Configurar un agente (actor 2)

Copia `lab-agent-setup.sh` e `ip-reporter.sh` al equipo y ejecuta como root:

```bash
sudo LAB_TOKEN=<el-token-de-render> ./lab-agent-setup.sh \
  --user dev \
  --key-file ~/.ssh/id_ed25519.pub \
  --server-url https://allconnect-xxxx.onrender.com \
  --name vm1
```

| Opción | Descripción |
| --- | --- |
| `--user` | Usuario al que se le instala la clave pública. |
| `--key-file` | Clave pública a autorizar en `authorized_keys`. |
| `--key-only` | Desactiva autenticación por contraseña (solo clave). |
| `--server-url` | URL del servidor, p. ej. `https://allconnect-xxxx.onrender.com`. |
| `--name` | Nombre con el que se registra el host (por defecto, `hostname`). |
| `--cacert` | Solo si usas un servidor propio con CA privada; en Render **no**. |
| `--no-reporter` | Solo configura SSH, no instala el reporter. |

El script es idempotente: se puede volver a ejecutar sin duplicar claves ni
directivas. Instala `/etc/lab-agent.conf` (modo `600`) y el servicio
`lab-ip-reporter`. El token se lee de `LAB_TOKEN`, no por argumento, para que no
quede visible en `ps`.

### Configuración del reporter

`/etc/lab-agent.conf`:

```ini
SERVER_URL=https://allconnect-xxxx.onrender.com
TOKEN=<el-token-de-render>
NAME=vm1
# IFACE=eth0                   # opcional: forzar interfaz
# HEARTBEAT=300                # latido; < 900 s para no dormir Render
# POLL=30                      # reintento si no hay eventos de red
```

El reporter usa `ip monitor address route` para reaccionar a cambios de red y
`curl` para enviar. No necesita `jq`.

## Panel y API

Panel web:

```
https://allconnect-xxxx.onrender.com/?token=<el-token-de-render>
```

Muestra por máquina la IP reportada, la IP de origen vista por el servidor, la
antigüedad del último reporte y un desplegable con los últimos cambios.

Todas las rutas de datos requieren `Authorization: Bearer <LAB_TOKEN>`.

| Método | Ruta | Descripción |
| --- | --- | --- |
| `POST` | `/report` | Cuerpo `{"name": "...", "ip": "..."}`. Responde `{"ok": true, "changed": bool}`. |
| `GET` | `/hosts` | Inventario: `{nombre: {ip, seen_from, updated, history}}`. |
| `GET` | `/hosts/{name}` | Un host; `404` si no existe. |
| `GET` | `/healthz` | Sin token; usado por el health check de Render. |
| `GET` | `/` | Panel HTML; requiere `?token=<LAB_TOKEN>`. |

`seen_from` es la IP de origen vista por el servidor (útil detrás de NAT) y
`updated` es un timestamp Unix. `history` guarda los últimos `LAB_HISTORY`
cambios de IP o de origen.

```bash
curl -fsS -H "Authorization: Bearer $LAB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"vm1","ip":"10.0.0.5"}' \
  https://allconnect-xxxx.onrender.com/report
```

## Uso del cliente

`~/.config/labssh.conf`:

```ini
SERVER_URL=https://allconnect-xxxx.onrender.com
TOKEN=<el-token-de-render>
DEFAULT_USER=dev               # opcional
```

```bash
./labssh list              # nombre, IP y segundos desde el último reporte
./labssh vm1               # ssh dev@<ip actual>
./labssh vm1 root -p 2222  # usuario y argumentos ssh extra
```

`labssh` necesita `jq` y `curl` en la máquina desde la que te conectas.

## Servidor en local (alternativa)

Para probar sin Render:

```bash
python3 -m venv env
env/bin/pip install -r requirements.txt

export LAB_TOKEN="$(openssl rand -hex 32)"
export LAB_DB=./hosts.json

env/bin/uvicorn server:app --host 127.0.0.1 --port 8080
```

Abre `http://127.0.0.1:8080/?token=$LAB_TOKEN`. Si vas a exponerlo fuera de
`localhost`, ponlo detrás de un proxy TLS o usa `--ssl-keyfile`/`--ssl-certfile`
con un certificado propio, y no publiques el puerto sin token.

### Servicio systemd (opcional, local)

```ini
# /etc/systemd/system/lab-server.service
[Unit]
Description=Registro de IPs del laboratorio
After=network-online.target

[Service]
WorkingDirectory=/opt/all_connect
EnvironmentFile=/etc/lab-server.env
ExecStart=/opt/all_connect/env/bin/uvicorn server:app --host 0.0.0.0 --port 8080
Restart=always

[Install]
WantedBy=multi-user.target
```

`/etc/lab-server.env` (modo `600`):

```ini
LAB_TOKEN=pon-aqui-el-token
LAB_DB=/opt/all_connect/hosts.json
LAB_HISTORY=20
```

## Variables de entorno

| Variable | Componente | Por defecto | Descripción |
| --- | --- | --- | --- |
| `LAB_TOKEN` | servidor / instalador | — (obligatoria) | Token compartido para autenticar. |
| `LAB_DB` | servidor | `./hosts.json` | Ruta del inventario JSON. En Render: `/tmp/hosts.json`. |
| `LAB_HISTORY` | servidor | `20` | Cambios de IP conservados por host. |
| `LAB_CONF` | reporter | `/etc/lab-agent.conf` | Config alternativa del agente. |
| `LABSSH_CONF` | cliente | `~/.config/labssh.conf` | Config alternativa del cliente. |
| `SERVER_URL`, `TOKEN`, `NAME` | reporter | — (obligatorias) | Destino, credencial y nombre del host. |
| `PORT` | servidor (Render) | `10000` | Render lo inyecta; úsalo en el start command. |

## Notas de seguridad

- El token se compara en tiempo constante (`secrets.compare_digest`).
- Solo se aceptan nombres que cumplen `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$` e IPs válidas.
- En Render el tráfico va por HTTPS; no necesitas certificados propios.
- El token del panel viaja en la URL (`?token=`), así que aparece en el historial
  del navegador y en los logs de acceso de Render. Para un laboratorio es
  aceptable; si te preocupa, usa `labssh` y no abras el panel.
- `--forwarded-allow-ips='*'` confía en `X-Forwarded-For`. Es correcto detrás del
  proxy de Render; si expones uvicorn directamente, un cliente podría falsear
  `seen_from`.
- `key.pem`, `cert.pem`, `hosts.json`, `.env` y las configuraciones con token
  están en `.gitignore`: **no los subas al repositorio**.

## Estructura

```
all_connect/
├── server.py             # API FastAPI + panel
├── render.yaml           # despliegue en Render
├── requirements.txt
├── ip-reporter.sh        # agente de IP (actor 2)
├── lab-agent-setup.sh    # instalador del agente
├── labssh                # cliente SSH por nombre
├── .env.example          # variables para desarrollo local
├── env/                  # venv local (ignorado por git)
└── hosts.json            # inventario generado en ejecución (ignorado por git)
```
