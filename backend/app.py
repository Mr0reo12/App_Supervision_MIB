# backend/app.py – Backend FastAPI pour l’API MIB (amélioré)
# • Retries sur /status avec backoff (tenacity)
# • Si /status échoue, sert le cache Redis stale
# • Si pas de cache, ne supprime plus silencieusement la VM : la marque en « Error »

from __future__ import annotations
from token_manager import token_mgr
from typing import List, Dict, Any, Optional
import os
import time
import json
import logging

import httpx
import redis
from fastapi import FastAPI, HTTPException, Query
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type


from dotenv import load_dotenv

load_dotenv()


# ─── Config API & cache ────────────────────────────────────────────────────────
MIB_BASE = os.getenv("MIB_BASE", "https://57.203.253.112:443")
ASSETS_SEARCH = f"{MIB_BASE}/api/v1/assets/search"
ASSET_STATUS = f"{MIB_BASE}/api/v1/assets/{{asset_id}}/status"

L2_SUPPORT_FILTER = "ATQIHF"

CACHE_TTL = int(os.getenv("CACHE_TTL", "0"))   # all_assets (RAM)
STATUS_TTL = int(os.getenv("STATUS_TTL", "60"))  # status VM  (Redis)
MACHINE_TTL = int(os.getenv("MACHINE_TTL", "300"))  # détail VM  (Redis)

rds = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    decode_responses=True
)

logger = logging.getLogger("backend")

# ─── Cache RAM pour /assets ────────────────────────────────────────────────────


class InMemoryTTLCache:
    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._exp: Dict[str, float] = {}

    def get(self, k: str) -> Optional[Any]:
        if CACHE_TTL == 0:
            return None
        exp = self._exp.get(k)
        if exp and exp > time.time():
            return self._store[k]
        self._store.pop(k, None)
        self._exp.pop(k, None)
        return None

    def set(self, k: str, v: Any):
        if CACHE_TTL == 0:
            return
        self._store[k] = v
        self._exp[k] = time.time() + CACHE_TTL


cache = InMemoryTTLCache()

# ─── Helpers Redis JSON ───────────────────────────────────────────────────────


def r_status_get(asset_id: str) -> Optional[list]:
    raw = rds.get(f"status:{asset_id}")
    return json.loads(raw) if raw else None


def r_status_set(asset_id: str, data: list):
    rds.setex(f"status:{asset_id}", STATUS_TTL, json.dumps(data))


def r_machine_get(asset_id: str) -> Optional[dict]:
    raw = rds.get(f"machine:{asset_id}")
    return json.loads(raw) if raw else None


def r_machine_set(asset_id: str, data: dict):
    rds.setex(f"machine:{asset_id}", MACHINE_TTL, json.dumps(data))

# ─── Token manager ─────────────────────────────────────────────────────────────


async def read_token() -> str:
    return await token_mgr.get_token()

# ─── Retry decorator pour /status fetch ────────────────────────────────────────


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type(httpx.HTTPError),
)
async def fetch_status(http: httpx.AsyncClient, asset_id: str, token: str) -> list:
    resp = await http.get(
        ASSET_STATUS.format(asset_id=asset_id),
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json().get("data", [])

# ─── Pagination + cache RAM pour /assets ──────────────────────────────────────


async def list_assets(http: httpx.AsyncClient, token: str) -> list[dict]:
    cached = cache.get("all_assets")
    if cached is not None:
        return cached

    page, per_page, all_assets = 1, 100, []
    while True:
        payload = {
            "pagination": {"page": page, "perPage": per_page},
            "filtering": [
                {"property": "l2Support", "rule": "eq", "value": L2_SUPPORT_FILTER}
            ],
        }
        r = await http.post(
            ASSETS_SEARCH,
            headers={"Authorization": f"Bearer {token}"},
            json=payload
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            break
        all_assets += data
        if len(data) < per_page:
            break
        page += 1

    cache.set("all_assets", all_assets)
    return all_assets

# ─── Normalisation des checks ──────────────────────────────────────────────────
STATUS_CRIT = {"critical", "ko", "error", "not ok"}
STATUS_WARN = {"warning", "warn"}


def normalize_check(item: dict) -> Dict[str, str]:
    return {
        "objectClass": item.get("objectClass") or "-",
        "parameter": item.get("parameter") or "-",
        "object": item.get("object") or "-",
        "status": (item.get("status") or "").capitalize() or "Unknown",
        "severity": item.get("severity") or "-",
        "lastChange": item.get("lastChange") or "Never",
        "description": item.get("description") or "",
    }


def build_status(monitored_by: List[dict]) -> Dict[str, Any]:
    services, crit, warn, unknown = {}, False, False, False
    for it in monitored_by:
        st = (it.get("status") or "").lower()
        desc = it.get("description") or it.get("instance", {}).get("instanceName", "Unknown")
        if st == "ok":
            services[desc] = "OK"
        elif st in STATUS_CRIT:
            services[desc] = "Critical"
            crit = True
        elif st in STATUS_WARN:
            services[desc] = "Warning"
            warn = True
        else:
            services[desc] = "Unknown"
            unknown = True

    if crit:
        global_status = "Critical"
    elif warn:
        global_status = "Warning"
    elif unknown:
        global_status = "Unknown"
    else:
        global_status = "OK"

    return {"monitored_services": services, "global_status": global_status}


# ─── FastAPI setup ────────────────────────────────────────────────────────────
app = FastAPI(title="MIB Backend – cache RAM + Redis")


@app.on_event("startup")
async def on_startup():
    await token_mgr.startup()

# ─── /assets endpoint ─────────────────────────────────────────────────────────


@app.get("/assets", summary="Liste des assets (filtrage par client)")
async def get_assets(client: Optional[str] = Query(None)):
    token = await read_token()
    async with httpx.AsyncClient(http2=True, timeout=15.0, verify=False) as http:
        assets = await list_assets(http, token)

    if client:
        assets = [a for a in assets if client.lower() in a.get("customerName", "").lower()]

    return {"data": assets}

# ─── /machine/{machine_name} endpoint amélioré ─────────────────────────────────


@app.get("/machine/{machine_name}", summary="Détail complet d’une VM")
async def get_machine(machine_name: str):
    token = await read_token()

    # 1) charger la liste des assets (cache RAM)
    async with httpx.AsyncClient(http2=True, timeout=15.0, verify=False) as http:
        assets = await list_assets(http, token)

    asset = next((a for a in assets if a.get("assetName") == machine_name), None)
    if not asset:
        raise HTTPException(404, "Machine not found")
    asset_id = asset["assetId"]

    # 2) fallback sur cache Redis complet
    stale = r_machine_get(asset_id)
    if stale is not None:
        return stale

    # 3) tenter de fetch /status avec retries
    try:
        async with httpx.AsyncClient(http2=True, timeout=15.0, verify=False) as http:
            monitored_by = await fetch_status(http, asset_id, token)
        # importer dans Redis
        r_status_set(asset_id, monitored_by)
    except Exception as exc:
        logger.error(f"Fetch status failed for {machine_name}: {exc}")
        # si on a un cache partiel de status, on le sert
        partial = r_status_get(asset_id)
        if partial is not None:
            # on construit un payload minimal
            status_summary = build_status(partial)
            monitoring_details = [normalize_check(it) for it in partial]
            payload = {
                "machine": machine_name,
                **status_summary,
                "monitoring_details": monitoring_details,
                **asset
            }
            return payload
        # sinon on crée une entrée « Error » unique
        error_payload = {
            "machine": machine_name,
            "monitored_services": {},
            "global_status": "Critical",
            "monitoring_details": [
                {
                    "objectClass": "-",
                    "parameter": "-",
                    "object": "-",
                    "status": "Error",
                    "severity": "-",
                    "lastChange": "Never",
                    "description": f"Fetch failed: {exc}"
                }
            ],
            **asset
        }
        # on cache quand même cette « erreur »
        r_machine_set(asset_id, error_payload)
        return error_payload

    # 4) cas nominal, build payload et cache complet
    status_summary = build_status(monitored_by)
    monitoring_details = [normalize_check(it) for it in monitored_by]
    vm_payload = {
        "machine": machine_name,
        **status_summary,
        "monitoring_details": monitoring_details,
        **asset
    }
    r_machine_set(asset_id, vm_payload)
    return vm_payload

# ─── Lancement local ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s: %(message)s"
    )
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5001, reload=True)
