"""
gateway.py – API Gateway asynchrone + cache Redis
───────────────────────────────────────────────────────────────────────────────
• Sert de façade entre le frontend et le backend MIB (/assets & /machine)
• Met en cache Redis (TTL = CACHE_TTL) pour soulager le backend
• Trois endpoints :
      1. GET /api/status/<client>   → assets + checks, agrégé & mis en cache
      2. GET /api/machine/<vm>      → détail direct (pas de cache ici)
      3. GET /api/vmnames/<client>  → liste des noms de VM (auto-complétion)
"""

from __future__ import annotations
import os
import asyncio
import json
from urllib.parse import quote

import httpx
import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ═════════════════════════════════════════════════════════════════════════════
# Paramètres / environnement
# ═════════════════════════════════════════════════════════════════════════════
MIB_BACKEND = os.getenv("MIB_BACKEND_URL", "http://backend:5001")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")  # conteneur ou localhost
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "120"))  # secondes (2 min par défaut)

# Connexion Redis (decode_responses =True → str plutôt que bytes)
rds = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# ═════════════════════════════════════════════════════════════════════════════
# Initialisation FastAPI
# ═════════════════════════════════════════════════════════════════════════════
app = FastAPI(title="MIB API-Gateway (async + Redis)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 👉 à restreindre si besoin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "API-Gateway MIB opérationnel."}


# ═════════════════════════════════════════════════════════════════════════════
# Helpers Redis : lecture / écriture JSON
# ═════════════════════════════════════════════════════════════════════════════
def rget(key: str):
    """Lecture JSON → objet Python (None si absent)."""
    val = rds.get(key)
    return json.loads(val) if val else None


def rset(key: str, obj):
    """Écriture objet Python → JSON + TTL."""
    rds.setex(key, CACHE_TTL, json.dumps(obj))


# ═════════════════════════════════════════════════════════════════════════════
# 1)  /api/status/<client>  – liste des VM d’un client + checks
#     ↳ lourde : agrège un appel /assets + N appels /machine
#     ↳ résultat mis en cache Redis
# ═════════════════════════════════════════════════════════════════════════════
@app.get("/api/status/{client}")
async def get_assets_by_client(client: str):
    cache_key = f"status:{client}"
    cached = rget(cache_key)
    if cached is not None:  # → hit Redis
        return cached

    encoded = quote(client)  # encodage URL-safe
    async with httpx.AsyncClient(timeout=15.0) as http:
        # ───────────────── 1. récupérer les assets du client ────────────────
        r_assets = await http.get(f"{MIB_BACKEND}/assets?client={encoded}")
        r_assets.raise_for_status()
        assets = r_assets.json().get("data", [])

        # ───────────────── 2. détail de chaque VM en parallèle ──────────────
        async def fetch_vm(asset):
            name = asset.get("assetName")
            if not name:
                return None
            try:
                r = await http.get(f"{MIB_BACKEND}/machine/{name}")
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass  # on ignore les échecs isolés
            return None

        enriched = [
            vm for vm in await asyncio.gather(*(fetch_vm(a) for a in assets)) if vm
        ]

    result = {"data": enriched}
    rset(cache_key, result)  # → write cache
    return result


# ═════════════════════════════════════════════════════════════════════════════
# 2)  /api/machine/<vm>  – détail d’une VM (pas de cache ici)
# ═════════════════════════════════════════════════════════════════════════════
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


# ═════════════════════════════════════════════════════════════════════════════
# 3)  /api/vmnames/<client>  – liste des noms de VM (auto-complétion)
# ═════════════════════════════════════════════════════════════════════════════
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
        names = [
            a.get("assetName") for a in r.json().get("data", []) if a.get("assetName")
        ]

    result = {"names": names}
    rset(cache_key, result)
    return result
