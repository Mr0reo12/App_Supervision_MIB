# backend/token_manager.py  – versión FINAL (form-url-encoded)
"""
Gestion automatique du access-token pour l’API MIB
• POST /api/auth/login  (x-www-form-urlencoded) – au démarrage
• POST /api/auth/refresh – toutes les 15 min
• Fournit await token_mgr.get_token() au reste du backend
"""

from __future__ import annotations
import os
import asyncio
import datetime
import httpx
import logging
from pathlib import Path
from dotenv import load_dotenv

# ── .env ──────────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MIB_BASE = os.getenv("MIB_BASE", "https://57.203.253.112:443")
LOGIN_URL = f"{MIB_BASE}/api/auth/login"
REFRESH_URL = f"{MIB_BASE}/api/auth/refresh"

CAS_USER = os.getenv("CASIMIR_ACCOUNT")
CAS_PASS = os.getenv("CASIMIR_PASSWORD")
if not CAS_USER or not CAS_PASS:
    raise RuntimeError("CASIMIR_ACCOUNT ou CASIMIR_PASSWORD manquants")

logger = logging.getLogger("token_manager")


# ── Singleton ────────────────────────────────────────────────────────────────
class TokenManager:
    def __init__(self):
        self._token: str | None = None
        self._expiry: datetime.datetime | None = None
        self._lock = asyncio.Lock()

    # API publique ------------------------------------------------------------
    async def startup(self):
        try:
            await self._login()
        except Exception as e:
            logger.error(f"Login initial KO : {e} — nouvelle tentative à la demande")
        asyncio.create_task(self._refresher())

    async def get_token(self) -> str:
        async with self._lock:
            if not self._token or self._is_expired():
                await self._refresh_or_login()
            return self._token

    # Internes ----------------------------------------------------------------
    async def _login(self):
        """Appel /auth/login en x-www-form-urlencoded (obligatoire)."""
        data = {"userId": CAS_USER, "password": CAS_PASS}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        async with httpx.AsyncClient(verify=False, timeout=10) as http:
            r = await http.post(LOGIN_URL, data=data, headers=headers)
            r.raise_for_status()
            self._token = r.json()["accessToken"]
            self._expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=14)
            logger.info("✅  Nouveau token obtenu")

    async def _refresh(self):
        headers = {"Authorization": f"Bearer {self._token}"}
        async with httpx.AsyncClient(verify=False, timeout=10, headers=headers) as http:
            r = await http.post(REFRESH_URL)
            r.raise_for_status()
            self._token = r.json()["accessToken"]
            self._expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=14)
            logger.info("🔄  Token rafraîchi")

    async def _refresh_or_login(self):
        try:
            await self._refresh()
        except Exception as e:
            logger.warning(f"Refresh KO ({e}) — login complet")
            await self._login()

    def _is_expired(self) -> bool:
        return not self._expiry or self._expiry <= datetime.datetime.utcnow()

    async def _refresher(self):
        while True:
            await asyncio.sleep(900)  # 15 min
            async with self._lock:
                if self._is_expired():
                    await self._refresh_or_login()


# instance globale
token_mgr = TokenManager()
