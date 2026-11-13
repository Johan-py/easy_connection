# Instrucciones — all_connect

**Servidor (actor 1):** <https://easy-connection.onrender.com>
**Repositorio:** <https://github.com/Johan-py/easy_connection>

## Modelo

- **Actor 2 — máquina del lab:** detecta su IP y la envía con `POST /report`.
- **Actor 1 — Render:** solo recibe, guarda y muestra las entradas. No enruta,
  no resuelve nombres, no abre UDP ni se conecta de vuelta al laboratorio.

```
Actor 2 (lab) ──POST /report──► Actor 1 (Render) ──panel/API──► consulta
```

## Credenciales

| Dato | Valor |
| --- | --- |
| URL | `https://easy-connection.onrender.com` |
| Token | `77********ef29` |
| Panel | `https://easy-connection.onrender.com/?token=<TOKEN>` |

> El token completo **no se escribe en este archivo**. Está en
> **Render → servicio `easy-connection` → Environment → `LAB_TOKEN`**.
> En los comandos de abajo sustituye `<TOKEN>` por ese valor.

---

## 1. Desplegar en Render (una sola vez)

### Opción A — Blueprint (recomendado)

1. Render → **New +** → **Blueprint**.
2. Elige el repo `Johan-py/easy_connection`, branch `main`.
3. **Apply**. El `render.yaml` crea el servicio `easy-connection`.
4. Cuando esté **Live**, copia `LAB_TOKEN` desde **Environment**.

> Si ya existe un servicio manual llamado `easy-connection`, **no apliques el
> Blueprint**: añade las variables a mano (opción B). Si quieres usar el
> Blueprint, borra antes el servicio manual para liberar el nombre.

### Opción B — manual

| Campo | Valor |
| --- | --- |
| Runtime | Python |
| Branch | `main` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn server:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'` |
| Health Check Path | `/healthz` |

Variables de entorno:

| Key | Value |
| --- | --- |
| `LAB_TOKEN` | `77********ef29` (el valor completo) |
| `LAB_DB` | `/tmp/hosts.json` |
| `LAB_HISTORY` | `20` |
| `PYTHON_VERSION` | `3.13.5` |

Guarda con **Save Changes** y espera el redeploy.

### Comprobar el despliegue

```bash
curl -fsS https://easy-connection.onrender.com/healthz
# {"ok":true}
```

Si responde `502` o tarda ~1 minuto, el servicio free estaba dormido: espera y
reintenta.

En los logs la primera línea debe mostrar:

```
[all_connect] Python 3.13.5 | LAB_DB=/tmp/hosts.json | LAB_HISTORY=20 | LAB_*= ['LAB_DB', 'LAB_HISTORY', 'LAB_TOKEN']
```

Si `LAB_*` aparece como `ninguna`, las variables están en otro servicio o no se
guardaron.

---

## 2. Preparar la máquina del lab (actor 2)

Requisitos: Linux con `systemd`, `curl`, `iproute2` (`ip`) y acceso a internet.

### 2.1 Traer los scripts

Opción A — clonar el repo:

```bash
sudo apt-get update
sudo apt-get install -y git curl iproute2
git clone https://github.com/Johan-py/easy_connection.git
cd easy_connection
chmod +x lab-agent-setup.sh ip-reporter.sh
```

Opción B — copiarlos desde tu máquina de desarrollo:

```bash
scp lab-agent-setup.sh ip-reporter.sh usuario@IP_DEL_LAB:/tmp/
```

Luego en el lab:

```bash
cd /tmp
chmod +x lab-agent-setup.sh ip-reporter.sh
```

> Los dos scripts deben estar en la **misma carpeta**: el instalador copia
> `ip-reporter.sh` desde su propio directorio.

### 2.2 Datos necesarios

| Dato | Ejemplo | Nota |
| --- | --- | --- |
| Token | `77********ef29` | El valor completo de Render. |
| Nombre | `lab-pc1` | Único por máquina; por defecto `hostname`. |
| Usuario SSH | `dev` | Debe existir ya en el lab. |

Comprueba:

```bash
id dev            # cambia "dev" por tu usuario real
hostname
ip -4 addr show scope global
ip -4 route get 1.1.1.1
```

Si el usuario no existe:

```bash
sudo useradd -m -s /bin/bash dev
sudo passwd dev
```

### 2.3 Instalar con clave SSH (recomendado)

En la máquina desde la que te vas a conectar (tu laptop):

```bash
ssh-keygen -t ed25519 -C "laptop"
cat ~/.ssh/id_ed25519.pub
```

Copia esa línea al lab, por ejemplo a `/tmp/lab-key.pub`, y ejecuta allí:

```bash
cd ~/easy_connection   # o /tmp, donde estén los scripts

export LAB_TOKEN='<TOKEN>'   # real: 77********ef29

sudo LAB_TOKEN="$LAB_TOKEN" ./lab-agent-setup.sh \
  --user dev \
  --key-file /tmp/lab-key.pub \
  --server-url https://easy-connection.onrender.com \
  --name lab-pc1
```

Añade `--key-only` si quieres desactivar el login por contraseña.

### 2.4 Instalar sin clave (login por contraseña)

```bash
export LAB_TOKEN='<TOKEN>'   # real: 77********ef29
sudo LAB_TOKEN="$LAB_TOKEN" ./lab-agent-setup.sh \
  --server-url https://easy-connection.onrender.com \
  --name lab-pc1
```

El usuario del lab debe tener contraseña. `PermitRootLogin` queda en `no`.

### 2.5 Qué hace el instalador

- Instala `openssh-server` si falta.
- Autoriza la clave pública indicada en `authorized_keys` del usuario.
- Crea `/etc/ssh/sshd_config.d/10-lab.conf` (solo clave si usas `--key-only`).
- Habilita y recarga `ssh`/`sshd`.
- Crea `/etc/lab-agent.conf` con modo `600`.
- Instala `/usr/local/bin/ip-reporter.sh`.
- Crea y arranca el servicio `lab-ip-reporter`.

### 2.6 Forzar una interfaz concreta

Si hay WiFi y Ethernet y quieres reportar una sola:

```bash
sudo nano /etc/lab-agent.conf
```

Añade:

```ini
IFACE=wlan0
```

Y reinicia:

```bash
sudo systemctl restart lab-ip-reporter
```

---

## 3. Verificar el agente

```bash
systemctl status lab-ip-reporter
journalctl -u lab-ip-reporter -n 20 --no-pager
```

Esperado:

```
[ip-reporter] IP reportada: ninguna -> 192.168.1.50
```

Panel:

```
https://easy-connection.onrender.com/?token=<TOKEN>
```

API desde el lab:

```bash
curl -s -H "Authorization: Bearer <TOKEN>" \
  https://easy-connection.onrender.com/hosts
```

Para forzar un envío inmediato:

```bash
sudo systemctl restart lab-ip-reporter
```

---

## 4. Añadir más máquinas

Repite la sección 2 en cada equipo cambiando `--name`:

```bash
sudo LAB_TOKEN="$LAB_TOKEN" ./lab-agent-setup.sh \
  --server-url https://easy-connection.onrender.com \
  --name lab-pc2
```

---

## 5. Panel y API

| Método | Ruta | Descripción |
| --- | --- | --- |
| `POST` | `/report` | Cuerpo `{"name":"...","ip":"..."}`. Requiere token. |
| `GET` | `/hosts` | Inventario completo. Requiere token. |
| `GET` | `/hosts/{name}` | Un host; `404` si no existe. |
| `GET` | `/healthz` | Sin token; health check de Render. |
| `GET` | `/` | Panel HTML; requiere `?token=<TOKEN>`. |

Ejemplo de reporte manual:

```bash
curl -s -X POST https://easy-connection.onrender.com/report \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name":"prueba","ip":"10.0.0.5"}'
```

---

## 6. Problemas frecuentes

| Síntoma | Causa / solución |
| --- | --- |
| `RuntimeError: Falta LAB_TOKEN` en el deploy | La variable no está en el servicio correcto. Revisa **Environment** del servicio `easy-connection` y guarda. |
| El log muestra Python 3.14 en vez de 3.13 | `PYTHON_VERSION` no se aplicó: misma causa que el token. |
| `401 token inválido` | El token del agente/`labssh` no coincide con `LAB_TOKEN`. |
| Primera petición tarda ~1 min | Servicio free dormido. El agente reintenta solo. |
| `/hosts` vacío tras redeploy | Filesystem efímero (`/tmp`). El agente repuebla en ≤ 300 s. |
| `lab-ip-reporter` no arranca | Sin `systemd` (contenedor/WSL). Ejecuta a mano: `sudo LAB_CONF=/etc/lab-agent.conf /usr/local/bin/ip-reporter.sh`. |
| Reporta la IP equivocada | Define `IFACE=...` en `/etc/lab-agent.conf`. |
| No puedo entrar por SSH | Firewall del lab, puerto 22 cerrado, o IP local sin ruta desde fuera. |
| `labssh: permission denied` | `chmod +x labssh`. |

---

## 7. Seguridad

- El token solo debe estar en **Render → Environment** y en
  `/etc/lab-agent.conf` (modo `600`). **No lo subas al repositorio.**
- En este documento aparece enmascarado (`77********ef29`).
- El token del panel viaja en la URL (`?token=`), así que queda en el historial
  del navegador y en los logs de Render. Para un laboratorio es aceptable.
- `--forwarded-allow-ips='*'` es correcto detrás del proxy de Render; si expones
  uvicorn directamente, un cliente podría falsear `seen_from`.
- `hosts.json`, `.env`, `env/` y los certificados están en `.gitignore`.

---

## 8. Diferencias entre IP local y pública

- El agente reporta la **IP local** de la máquina (normalmente `192.168.x.x`).
- El servidor guarda además `seen_from`: la IP pública desde la que llega el
  request (la de tu red/NAT).
- Para SSH dentro de la misma red del lab, usa la IP local.
- Para acceder desde fuera necesitas abrir o redirigir el puerto 22 en el router
  del laboratorio; el servidor de Render **no** hace ese enrutado.
