###############################################################################
# frontend/app.py – Tableau de bord ATQIHF (frontend complet)
# ─────────────────────────────────────────────────────────────────────────────
# • Sert les pages HTML (Jinja2) du dashboard.
# • Interroge l’API-Gateway (async httpx) pour récupérer les données.
###############################################################################
from __future__ import annotations
from typing import List, Dict, Optional
import asyncio
import httpx
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from zoneinfo import ZoneInfo

# ═════════════════════════════════════════════════════════════════════════════
# Paramètres globaux
# ═════════════════════════════════════════════════════════════════════════════
API_GATEWAY = "http://gateway:5000"
VALID_CLIENTS = [
    "ORANGE APPLICATIONS FOR BUSINESS",
    "CTRE HOSP UNIVERSITAIRE DE MONTPELLIER",
    "VERIFONE SYSTEMS FRANCE SAS",
]

# ═════════════════════════════════════════════════════════════════════════════
# Initialisation FastAPI
# ═════════════════════════════════════════════════════════════════════════════
app = FastAPI(title="Frontend – ATQIHF Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ═════════════════════════════════════════════════════════════════════════════
# Helpers couleur-santé des boutons   (seulement rouge ou vert)
# ═════════════════════════════════════════════════════════════════════════════
def compute_global_status(vms: List[Dict]) -> str:
    """
    Retourne 'KO_ANY' dès qu'un seul check est Ko/Critical dans *une* VM.
    Sinon 'ALL_OK'.
    """
    for vm in vms:
        for chk in vm.get("monitoring_details", []):
            if chk["status"].lower() in {"ko", "critical"}:
                return "KO_ANY"
    return "ALL_OK"


def status_to_color(tag: str) -> str:
    """
    Mappea a clases CSS puras definidas en HTML.
      • KO_ANY  → 'red'
      • ALL_OK  → 'green'
      • fallback → 'gray'
    """
    if tag == "KO_ANY":
        return "red"
    elif tag == "ALL_OK":
        return "green"
    else:
        return "gray"

# 1) Accueil – boutons clients
# ═════════════════════════════════════════════════════════════════════════════
@app.get("/")
async def index(request: Request):
    client_statuses: List[Dict] = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),  # timeout ajusté
    ) as http:
    
        async def fetch_client(c):
            try:
                r = await http.get(f"{API_GATEWAY}/api/status/{c}")
                data = r.json().get("data", [])
                color = status_to_color(compute_global_status(data))
            except Exception:
                color = "bg-gray-400 hover:bg-gray-500"
            client_statuses.append(
                {
                    "name": c,
                    "color": color,
                    "url": f"/status/{c}?all_ko=1",  # ← enlace directo a la tabla KO
                }
            )

        await asyncio.gather(*(fetch_client(c) for c in VALID_CLIENTS))
    paris_tz = ZoneInfo("Europe/Paris")
    last_update = datetime.now(paris_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    # ── 3) Rendre la page d'accueil avec les données collectées ─────────────
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "clients": client_statuses,
            "last_update": last_update,  # Date time de la dernière mise à jour
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2)  Vue client  – tableau complet des checks KO/Critical
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/status/{client}", response_class=HTMLResponse)
async def client_dashboard(request: Request, client: str):
    if client not in VALID_CLIENTS:
        raise HTTPException(404, "Client not found")

    # ── 1) Traer TODOS los assets+status de este cliente ────────────────────
    async with httpx.AsyncClient() as http:
        r = await http.get(f"{API_GATEWAY}/api/status/{client}")
        r.raise_for_status()
        vms = r.json().get("data", [])

    # ── 2) Aplanar todos los checks KO/Warning en una sola lista ─────────
    rows: List[dict] = []
    for vm in vms:
        for chk in vm.get("monitoring_details", []):
            st = chk.get("status", "").lower()
            if st != "ok":
                rows.append(
                    {
                        "client": client,
                        "vm": vm["machine"],
                        "objectClass": chk.get("objectClass") or "-",
                        "parameter": chk.get("parameter") or "-",
                        "object": chk.get("object") or "-",
                        "status": chk.get("status") or "Unknown",
                        "severity": chk.get("severity") or "-",
                        "lastChange": chk.get("lastChange") or "Never",
                        "description": chk.get("description") or "",
                    }
                )

    return templates.TemplateResponse(
        "client_dashboard.html",
        {
            "request": request,
            "client": client,
            "rows": rows,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# 3) Detalle de VM (sin cambios)
# ═════════════════════════════════════════════════════════════════════════════
@app.get("/machine/{machine_name}", response_class=HTMLResponse)
async def machine_details(request: Request, machine_name: str):
    try:
        async with httpx.AsyncClient() as http:
            r = await http.get(f"{API_GATEWAY}/api/machine/{machine_name}")
            if r.status_code == 404:
                raise HTTPException(404, "Machine not found")
            machine = r.json()
    except Exception as e:
        raise HTTPException(500, str(e))

    return templates.TemplateResponse(
        "machine_details.html",
        {
            "request": request,
            "machine": machine,
        },
    )


# 4) Agrégat Critical / Warning  – paramètre optionnel
# ═════════════════════════════════════════════════════════════════════════════
@app.get("/critical-assets", response_class=HTMLResponse)
async def show_critical_assets(
    request: Request, status: Optional[str] = Query(None)  # peut être absent
):
    # ─── cas 1 : pas de paramètre → afficher seulement les deux boutons ──────
    if status is None:
        return templates.TemplateResponse(
            "critical_assets.html",
            {
                "request": request,
                "rows": [],
                "status": "",
            },
        )

    # ─── cas 2 : validation du paramètre ─────────────────────────────────────
    status = status.capitalize()
    if status not in {"Critical", "Warning"}:
        raise HTTPException(400, "status doit être Critical ou Warning")

    rows: List[dict] = []

    async with httpx.AsyncClient() as http:

        async def gather_client(client_name):
            try:
                r = await http.get(f"{API_GATEWAY}/api/status/{client_name}")
                for vm in r.json().get("data", []):
                    if vm.get("global_status") != status:
                        continue
                    for chk in vm.get("monitoring_details", []):
                        chk_status = chk["status"].lower()
                        if status == "Critical" and chk_status in {"critical", "ko"}:
                            rows.append(
                                {"client": client_name, "vm": vm["machine"], **chk}
                            )
                        elif status == "Warning" and chk_status == "warning":
                            rows.append(
                                {"client": client_name, "vm": vm["machine"], **chk}
                            )
            except Exception:
                pass  # silencer les erreurs d’un client

        await asyncio.gather(*(gather_client(c) for c in VALID_CLIENTS))

    return templates.TemplateResponse(
        "critical_assets.html",
        {
            "request": request,
            "rows": rows,
            "status": status,
        },
    )
