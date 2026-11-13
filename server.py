#!/usr/bin/env python3
"""Registro de IPs del laboratorio.
Los agentes hacen POST /report; el cliente consulta GET /hosts.
Variables: LAB_TOKEN (obligatoria), LAB_DB (def. ./hosts.json)
Ejecutar con TLS:
  uvicorn server:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
"""
import ipaddress, json, os, re, secrets, tempfile, threading, time
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

TOKEN = os.environ["LAB_TOKEN"]
DB = os.environ.get("LAB_DB", "hosts.json")
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")
lock = threading.Lock()
app = FastAPI()


class Report(BaseModel):
    name: str
    ip: str


def auth(authorization: str | None):
    expected = f"Bearer {TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "token inválido")


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


@app.post("/report")
def report(r: Report, request: Request, authorization: str | None = Header(None)):
    auth(authorization)
    if not NAME_RE.match(r.name):
        raise HTTPException(400, "nombre inválido")
    try:
        ip = str(ipaddress.ip_address(r.ip))
    except ValueError:
        raise HTTPException(400, "IP inválida")
    with lock:
        data = load()
        prev = data.get(r.name, {}).get("ip")
        data[r.name] = {
            "ip": ip,
            "seen_from": request.client.host,  # IP vista por el servidor (útil si hay NAT)
            "updated": int(time.time()),
        }
        save(data)
    return {"ok": True, "changed": prev != ip}


@app.get("/hosts")
def hosts(authorization: str | None = Header(None)):
    auth(authorization)
    return load()


@app.get("/hosts/{name}")
def host(name: str, authorization: str | None = Header(None)):
    auth(authorization)
    h = load().get(name)
    if not h:
        raise HTTPException(404, "host desconocido")
    return {"name": name, **h}
