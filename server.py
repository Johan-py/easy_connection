#!/usr/bin/env python3
"""Registro de IPs del laboratorio.

Dos actores:
  * Actor 2 (máquina del lab): detecta su IP y hace POST /report.
  * Actor 1 (este servidor): solo recibe, guarda y muestra. No enruta nada.

Variables de entorno:
  LAB_TOKEN    token compartido (obligatoria)
  LAB_DB       ruta del inventario JSON (def. ./hosts.json)
  LAB_HISTORY  cambios de IP a conservar por host (def. 20)

En Render el TLS lo termina su proxy, así que uvicorn escucha HTTP en $PORT:
  uvicorn server:app --host 0.0.0.0 --port $PORT \
      --proxy-headers --forwarded-allow-ips='*'
"""
import html
import ipaddress
import json
import os
import re
import secrets
import tempfile
import threading
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

TOKEN = os.environ.get("LAB_TOKEN", "")
if not TOKEN:
    raise RuntimeError("Falta LAB_TOKEN: define el token compartido del laboratorio")
DB = os.environ.get("LAB_DB", "hosts.json")
HISTORY = max(1, int(os.environ.get("LAB_HISTORY", "20")))
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")
lock = threading.Lock()
app = FastAPI(title="all_connect", version="0.2.0")


class Report(BaseModel):
    name: str
    ip: str


def auth(authorization: str | None):
    expected = f"Bearer {TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "token inválido")


def require_token(authorization: str | None = Header(None)):
    # Dependencia: se ejecuta antes de validar el cuerpo (401, no 422).
    auth(authorization)


def load() -> dict:
    try:
        with open(DB) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(data: dict):
    # escritura atómica: evita dejar el JSON corrupto si el proceso muere
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(DB)))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DB)


def age(updated: int | None) -> str:
    if not updated:
        return "—"
    seconds = max(0, int(time.time()) - int(updated))
    if seconds < 60:
        return f"hace {seconds}s"
    if seconds < 3600:
        return f"hace {seconds // 60}m"
    if seconds < 86400:
        return f"hace {seconds // 3600}h"
    return f"hace {seconds // 86400}d"


def fmt_time(updated: int | None) -> str:
    if not updated:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(updated)))


@app.get("/healthz")
def healthz():
    # Sin token: Render lo usa para saber si el servicio está vivo.
    return {"ok": True}


@app.post("/report", dependencies=[Depends(require_token)])
def report(r: Report, request: Request):
    if not NAME_RE.match(r.name):
        raise HTTPException(400, "nombre inválido")
    try:
        ip = str(ipaddress.ip_address(r.ip))
    except ValueError:
        raise HTTPException(400, "IP inválida")

    seen_from = request.client.host if request.client else ""
    now = int(time.time())
    with lock:
        data = load()
        prev = data.get(r.name)
        changed = (
            prev is None
            or prev.get("ip") != ip
            or prev.get("seen_from") != seen_from
        )
        entry = prev if prev is not None else {"history": []}
        entry["ip"] = ip
        entry["seen_from"] = seen_from  # IP de origen vista por el servidor (NAT)
        entry["updated"] = now
        if changed:
            entry.setdefault("history", []).append(
                {"ip": ip, "seen_from": seen_from, "at": now}
            )
            entry["history"] = entry["history"][-HISTORY:]
        data[r.name] = entry
        save(data)
    return {"ok": True, "changed": changed}


@app.get("/hosts", dependencies=[Depends(require_token)])
def hosts():
    return load()


@app.get("/hosts/{name}", dependencies=[Depends(require_token)])
def host(name: str):
    h = load().get(name)
    if not h:
        raise HTTPException(404, "host desconocido")
    return {"name": name, **h}


def login_page() -> str:
    return """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>all_connect — acceso</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; display: grid; place-items: center;
         min-height: 100vh; margin: 0; }
  form { display: flex; gap: .5rem; }
  input, button { font-size: 1rem; padding: .6rem .8rem; border-radius: .5rem;
                  border: 1px solid rgba(128,128,128,.5); }
  button { cursor: pointer; }
</style></head>
<body>
  <form method="get" action="/">
    <input type="password" name="token" placeholder="LAB_TOKEN" autofocus>
    <button>Entrar</button>
  </form>
</body></html>"""


def panel_page(data: dict) -> str:
    rows = []
    for name, entry in sorted(
        data.items(), key=lambda kv: kv[1].get("updated", 0), reverse=True
    ):
        history = entry.get("history") or []
        if history:
            items = "".join(
                f"<li><code>{html.escape(str(h.get('ip', '')))}</code>"
                f" · visto desde {html.escape(str(h.get('seen_from', '')))}"
                f" · {fmt_time(h.get('at'))}</li>"
                for h in reversed(history[-10:])
            )
            changes = (
                f"<details><summary>{len(history)} cambio(s)</summary>"
                f"<ul>{items}</ul></details>"
            )
        else:
            changes = "—"
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(name)}</strong></td>"
            f"<td><code>{html.escape(str(entry.get('ip', '')))}</code></td>"
            f"<td><code>{html.escape(str(entry.get('seen_from', '')))}</code></td>"
            f"<td>{fmt_time(entry.get('updated'))}<br>"
            f"<span class=\"muted\">{age(entry.get('updated'))}</span></td>"
            f"<td>{changes}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="empty">Sin reportes todavía.</td></tr>')

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="15">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>all_connect — hosts</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 1000px;
         padding: 0 1rem; }}
  h1 {{ font-size: 1.35rem; margin-bottom: .25rem; }}
  .muted {{ opacity: .6; font-size: .85em; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 1.25rem; }}
  th, td {{ text-align: left; padding: .55rem .6rem;
            border-bottom: 1px solid rgba(128,128,128,.25); vertical-align: top; }}
  th {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; opacity: .7; }}
  .empty {{ text-align: center; padding: 2rem; opacity: .6; }}
  details ul {{ margin: .4rem 0 0; padding-left: 1.1rem; }}
  code {{ font-size: .9em; }}
</style></head>
<body>
  <h1>all_connect <span class="muted">registro de IPs</span></h1>
  <p class="muted">{len(data)} máquina(s) · se refresca cada 15 s</p>
  <table>
    <thead><tr><th>Host</th><th>IP reportada</th><th>Visto desde</th>
      <th>Último reporte</th><th>Historial</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def panel(token: str | None = None):
    if not token or not secrets.compare_digest(token, TOKEN):
        return HTMLResponse(login_page(), status_code=401)
    return HTMLResponse(panel_page(load()))
