"""Shared helper for audit/health scripts to obtain a JWT from the bot.

Audit/health background tasks need to call bot endpoints (login → JWT →
GET /api/hl/...) to compare bot's view with the platform's. Pre-2026-05-16
each caller hardcoded the same admin user/password — including in 4 files
committed to git. Compromise of the repo = full admin access.

This helper centralizes the credential lookup:

  1. Reads AUDIT_BOT_USERNAME / AUDIT_BOT_PASSWORD env vars at import time.
  2. Returns None (and logs a single WARNING) if either is missing.
  3. Caches the JWT for the configured TTL (default 50 min, well under the
     bot's 3h expiry) to avoid hammering /api/auth/login.

Usage:

    from src.observability.health._bot_auth import get_bot_token

    token = await get_bot_token(bot_url)
    if token is None:
        return  # cannot authenticate; logged once at startup
    headers = {"Authorization": f"Bearer {token}"}
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_USERNAME = os.getenv("AUDIT_BOT_USERNAME", "").strip()
_PASSWORD = os.getenv("AUDIT_BOT_PASSWORD", "").strip()
_TOKEN_TTL_SECONDS = 50 * 60  # well under bot's 3h JWT TTL

_cached_token: Optional[str] = None
_cached_at: float = 0.0
_warned_missing = False


def _credentials_available() -> bool:
    global _warned_missing
    if _USERNAME and _PASSWORD:
        return True
    if not _warned_missing:
        logger.warning(
            "AUDIT_BOT_USERNAME / AUDIT_BOT_PASSWORD env vars not set — "
            "audit / health scripts that need a bot JWT will be skipped. "
            "Set them in the platform .env to enable cross-check audits."
        )
        _warned_missing = True
    return False


async def get_bot_token(bot_url: str, *, timeout_seconds: float = 5.0) -> Optional[str]:
    """Return a cached or freshly-fetched JWT for audit operations.

    Returns None if the credentials are not configured or if the bot is
    unreachable. Callers should treat None as "skip this audit step"
    rather than retry — the credentials state is logged once at first
    failure.
    """
    global _cached_token, _cached_at

    if not _credentials_available():
        return None

    now = time.time()
    if _cached_token is not None and (now - _cached_at) < _TOKEN_TTL_SECONDS:
        return _cached_token

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{bot_url}/api/auth/login",
                json={"username": _USERNAME, "password": _PASSWORD},
            )
        if response.status_code != 200:
            logger.warning(
                "bot_auth.login_failed status=%s body=%s",
                response.status_code, response.text[:200],
            )
            return None
        token = response.json().get("token", "")
        if not token:
            logger.warning("bot_auth.login_returned_no_token")
            return None
        _cached_token = token
        _cached_at = now
        return token
    except Exception as exc:
        logger.warning("bot_auth.login_error err=%s", exc)
        return None


def invalidate_cache() -> None:
    """Force the next get_bot_token() to re-authenticate. For tests / on 401."""
    global _cached_token, _cached_at
    _cached_token = None
    _cached_at = 0.0
