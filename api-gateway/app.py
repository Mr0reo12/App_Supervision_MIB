# gateway.py – API Gateway asynchrone + cache Redis (amélioré)
# • Façade entre le frontend et le backend MIB (/assets & /machine)
# • Cache Redis pour soulager le backend
# • 3 endpoints : /api/status, /api/machine, /api/vmnames

from __future__ import annotations
import os
import asyncio
import json
from urllib.parse import quote

import httpx
import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

<<<<<<< HEAD
# ── Config / environnement ────────────────────────────────────────────────────
MIB_BACKEND = os.getenv("MIB_BACKEND_URL", "http://backend:5001")
REDIS_HOST  = os.getenv("REDIS_HOST", "redis")
REDIS_PORT  = int(os.getenv("REDIS_PORT", "6379"))
CACHE_TTL   = int(os.getenv("CACHE_TTL", "120"))  # secondes
=======
# ═════════════════════════════════════════════════════════════════════════════
# Paramètres / environnement
# ═════════════════════════════════════════════════════════════════════════════
MIB_BACKEND = os.getenv("MIB_BACKEND_URL", "http://backend:5001")
>>>>>>> origin/main

rds = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Control de concurrencia
MAX_CONCURRENCY = int(os.getenv("GATEWAY_CONCURRENCY", "10"))
semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

# ── FastAPI + CORS ────────────────────────────────────────────────────────────
app = FastAPI(title="MIB API-Gateway (async + Redis)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "API-Gateway MIB opérationnel."}

# ── Helpers Redis JSON ────────────────────────────────────────────────────────
def rget(key: str):
    val = rds.get(key)
    return json.loads(val) if val else None

def rset(key: str, obj):
    rds.setex(key, CACHE_TTL, json.dumps(obj))

# ── Retry decorator pour chaque fetch de VM ──────────────────────────────────
@retry(
    stop=stop_after_attempt(2),
    wait=wait_fixed(0.5),
    retry=retry_if_exception_type(httpx.HTTPError),
)
async def fetch_one(http: httpx.AsyncClient, name: str) -> dict:
    resp = await http.get(f"{MIB_BACKEND}/machine/{name}")
    resp.raise_for_status()
    return resp.json()

# ── Endpoint 1: /api/status/{client} ──────────────────────────────────────────
@app.get("/api/status/{client}")
async def get_assets_by_client(client: str):
    cache_key = f"status:{client}"
    cached = rget(cache_key)
    if cached is not None:
        return cached

    encoded = quote(client)
    async with httpx.AsyncClient(timeout=15.0) as http:
        # 1) lista assets
        r_assets = await http.get(f"{MIB_BACKEND}/assets?client={encoded}")
        r_assets.raise_for_status()
        assets = r_assets.json().get("data", [])

        # 2) fetch paralelo con semáforo + retry + payload de error limpio
        async def fetch_vm(asset):
            name = asset.get("assetName")
            if not name:
                return None
            async with semaphore:
                try:
                    return await fetch_one(http, name)
                except httpx.HTTPStatusError as exc:
                    desc = f"HTTP {exc.response.status_code}"
                except Exception:
                    desc = "Fetch failed"
                # payload mínimo de error
                return {
                    "machine": name,
                    "global_status": "Critical",
                    "monitoring_details": [{
                        "objectClass": "-",
                        "parameter":   "-",
                        "object":      "-",
                        "status":      "Error",
                        "severity":    "-",
                        "lastChange":  "Never",
                        "description": desc,
                    }],
                }

        enriched = await asyncio.gather(*(fetch_vm(a) for a in assets))

    result = {"data": enriched}
    rset(cache_key, result)
    return result

# ── Endpoint 2: /api/machine/{machine_name} ─────────────────────────────────
@app.get("/api/machine/{machine_name}")
async def get_machine(machine_name: str):
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.get(f"{MIB_BACKEND}/machine/{machine_name}")
            if r.status_code == 404:
                raise HTTPException(404, "Machine non trouvée")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        raise HTTPException(500, f"Erreur lors du fetch machine : {e}")

# ── Endpoint 3: /api/vmnames/{client} ────────────────────────────────────────
@app.get("/api/vmnames/{client}")
async def list_vm_names(client: str):
    cache_key = f"vmnames:{client}"
    cached = rget(cache_key)
    if cached is not None:
        return cached

    encoded = quote(client)
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.get(f"{MIB_BACKEND}/assets?client={encoded}")
        r.raise_for_status()
        names = [a["assetName"] for a in r.json().get("data", []) if a.get("assetName")]

    result = {"names": names}
    rset(cache_key, result)
    return result
