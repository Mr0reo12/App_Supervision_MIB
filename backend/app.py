###############################################################################
# backend/app.py – Backend FastAPI pour l’API MIB
# ─────────────────────────────────────────────────────────────────────────────
# • /assets                – liste paginée (cache RAM optionnel)
# • /machine/<vm>          – détail VM + checks
#     ↳ 2 niveaux de cache :
#         1) Redis  machine:<assetId>   TTL = MACHINE_TTL      ★ nouveau
#         2) Redis  status:<assetId>    TTL = STATUS_TTL
#         3) RAM    all_assets          TTL = CACHE_TTL
# • Token récupéré / rafraîchi toutes les 15 min (token_manager)
# • Filtre métier fixe : L2Support = “ATQIHF”
###############################################################################
from __future__ import annotations
from typing import List, Dict, Any, Optional
import os
import time
import json
import logging
import httpx, redis
from fastapi import FastAPI, HTTPException, Query
from pathlib import Path
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
# Variables d’environnement (.env à la racine)
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from .token_manager import token_mgr

# ════════════════════════════════════════════════════════════════════════════
# Configuration
# ════════════════════════════════════════════════════════════════════════════
MIB_BASE      = os.getenv("MIB_BASE", "https://57.203.253.112:443")
ASSETS_SEARCH = f"{MIB_BASE}/api/v1/assets/search"
ASSET_STATUS  = f"{MIB_BASE}/api/v1/assets/{{asset_id}}/status"

L2_SUPPORT_FILTER = "ATQIHF"

CACHE_TTL    = int(os.getenv("CACHE_TTL",   "0"))    # all_assets (RAM)
STATUS_TTL   = int(os.getenv("STATUS_TTL",  "60"))   # status VM  (Redis)
MACHINE_TTL  = int(os.getenv("MACHINE_TTL", "300"))  # détail VM  (Redis) ★ nouveau

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
rds = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

logger = logging.getLogger("backend")

# ════════════════════════════════════════════════════════════════════════════
# Cache RAM très léger (all_assets)
# ════════════════════════════════════════════════════════════════════════════
class InMemoryTTLCache:
    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._exp:   Dict[str, float] = {}

    def get(self, key: str):
        if CACHE_TTL == 0:
            return None
        exp = self._exp.get(key)
        if exp and exp > time.time():
            return self._store[key]
        self._store.pop(key, None); self._exp.pop(key, None)
        return None

    def set(self, key: str, val: Any):
        if CACHE_TTL == 0:
            return
        self._store[key] = val
        self._exp[key]   = time.time() + CACHE_TTL

cache = InMemoryTTLCache()

# ════════════════════════════════════════════════════════════════════════════
# Helpers Redis JSON
# ════════════════════════════════════════════════════════════════════════════
def r_status_get(asset_id: str) -> Optional[list]:
    raw = rds.get(f"status:{asset_id}")
    return json.loads(raw) if raw else None

def r_status_set(asset_id: str, data: list):
    rds.setex(f"status:{asset_id}", STATUS_TTL, json.dumps(data))

# --- nouveau : cache complet de /machine ------------------------------------
def r_machine_get(asset_id: str) -> Optional[dict]:                          # ★ nouveau
    raw = rds.get(f"machine:{asset_id}")
    return json.loads(raw) if raw else None

def r_machine_set(asset_id: str, data: dict):                                # ★ nouveau
    rds.setex(f"machine:{asset_id}", MACHINE_TTL, json.dumps(data))

# ════════════════════════════════════════════════════════════════════════════
# Fonctions HTTP → API MIB
# ════════════════════════════════════════════════════════════════════════════
async def read_token() -> str:
    return await token_mgr.get_token()

async def fetch_assets_page(http: httpx.AsyncClient, token: str,
                            page: int, per_page: int = 100) -> list[dict]:
    payload = {
        "pagination": {"page": page, "perPage": per_page},
        "filtering": [{"property": "l2Support", "rule": "eq",
                       "value": L2_SUPPORT_FILTER}],
    }
    r = await http.post(ASSETS_SEARCH,
                        headers={"Authorization": f"Bearer {token}"},
                        json=payload)
    r.raise_for_status()
    return r.json().get("data", [])

async def list_assets(http: httpx.AsyncClient, token: str) -> list[dict]:
    cached = cache.get("all_assets")
    if cached is not None:
        return cached

    page, per_page, assets = 1, 100, []
    while True:
        chunk = await fetch_assets_page(http, token, page, per_page)
        if not chunk:
            break
        assets.extend(chunk)
        if len(chunk) < per_page:
            break
        page += 1

    cache.set("all_assets", assets)
    return assets

# ════════════════════════════════════════════════════════════════════════════
# Normalisation des checks
# ════════════════════════════════════════════════════════════════════════════
STATUS_CRIT = {"critical", "ko", "error", "not ok"}
STATUS_WARN = {"warning", "warn"}

def normalize_check(item: dict) -> Dict[str, str]:
    return {
        "objectClass": item.get("objectClass") or "-",
        "parameter"  : item.get("parameter")   or "-",
        "object"     : item.get("object")      or "-",
        "status"     : (item.get("status") or "").capitalize() or "Unknown",
        "severity"   : item.get("severity")    or "-",
        "lastChange" : item.get("lastChange")  or "Never",
        "description": item.get("description") or "",
    }

def build_status(monitored_by: List[dict]) -> Dict[str, Any]:
    services, crit, warn, unknown = {}, False, False, False
    for it in monitored_by:
        raw  = (it.get("status") or "").lower()
        desc = it.get("description") or it.get("instance", {}).get("instanceName", "Unknown")
        if raw == "ok":
            services[desc] = "OK"
        elif raw in STATUS_CRIT:
            services[desc] = "Critical"; crit = True
        elif raw in STATUS_WARN:
            services[desc] = "Warning";  warn = True
        else:
            services[desc] = "Unknown";  unknown = True

    if crit:        global_status = "Critical"
    elif warn:      global_status = "Warning"
    elif unknown:   global_status = "Unknown"
    else:           global_status = "OK"

    return {"monitored_services": services, "global_status": global_status}

# ════════════════════════════════════════════════════════════════════════════
# FastAPI
# ════════════════════════════════════════════════════════════════════════════
app = FastAPI(title="MIB Backend – cache RAM + Redis")

@app.on_event("startup")
async def _startup():
    await token_mgr.startup()

# ─────────────────────────────────────────────────────────────────────────────
# /assets   – liste filtrable par client
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/assets", summary="Liste des assets (filtrage par client)")
async def get_assets(client: Optional[str] = Query(None)):
    token = await read_token()
    async with httpx.AsyncClient(http2=True, timeout=15, verify=False) as http:
        assets = await list_assets(http, token)

    if client:
        assets = [a for a in assets if client.lower()
                  in a.get("customerName", "").lower()]

    return {"data": assets}

# ─────────────────────────────────────────────────────────────────────────────
# /machine/<vm> – détail VM + checks (cache Redis complet)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/machine/{machine_name}", summary="Détail complet d’une VM")
async def get_machine(machine_name: str):
    token = await read_token()
    async with httpx.AsyncClient(http2=True, timeout=15, verify=False) as http:

        # 1) localiser l’asset correspondant
        assets = await list_assets(http, token)
        asset  = next((a for a in assets if a.get("assetName") == machine_name), None)
        if not asset:
            raise HTTPException(404, "Machine not found")
        asset_id = asset["assetId"]

        # 2) tenter de lire la VM complète en cache Redis ------------------- ★ nouveau
        cached_vm = r_machine_get(asset_id)
        if cached_vm:
            return cached_vm

        # 3) sinon → récupérer /status (éventuellement déjà cacheé)
        monitored_by = r_status_get(asset_id)
        if monitored_by is None:
            try:
                r = await http.get(
                    ASSET_STATUS.format(asset_id=asset_id),
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                monitored_by = r.json().get("data", [])
                r_status_set(asset_id, monitored_by)
            except httpx.HTTPStatusError as exc:
                raise HTTPException(502, f"MIB /status error {exc.response.status_code}")

    status_summary     = build_status(monitored_by)
    monitoring_details = [normalize_check(it) for it in monitored_by]

    vm_payload = {                                             # ★ nouveau
        "machine"      : machine_name,
        "assetType"    : asset.get("assetType"),
        "customerName" : asset.get("customerName"),
        "organization" : asset.get("organization"),
        "csuName"      : asset.get("csuName"),
        "L2Support"    : asset.get("l2Support"),
        **status_summary,
        "monitoring_details": monitoring_details,
    }

    r_machine_set(asset_id, vm_payload)                        # ★ nouveau
    return vm_payload

# ─────────────────────────────────────────────────────────────────────────────
# Lancement local
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s: %(message)s",
    )
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5001, reload=True)
