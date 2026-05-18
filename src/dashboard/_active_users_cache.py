"""Active user_id allow-list — dynamic version of BOT_API_KEY_USER_IDS.

2026-05-18 — replaces the static env-var approach with a periodic query
to the bot's /api/internal/active-user-ids endpoint.

**Motivation:** self-service user registration means new user_ids are
created continuously. Forcing the operator to edit the platform .env
each time doesn't scale. This module:

  1. Pulls the allow-list from the bot every {CACHE_TTL_SECONDS} (60s).
  2. Falls back to the cached list when the bot is unreachable.
  3. Falls back to the env-var override BOT_API_KEY_USER_IDS if set —
     useful for emergency lockdown (e.g., "only user 1 can push while
     I debug a leak").
  4. Falls back to "unrestricted" only in dev (PLATFORM_ENV != prod).

**Threat model:** the BOT_API_KEY is the gate. A leaked key + ability to
forge user_id in payload would mean an attacker can pollute any user's
analytics. The allow-list narrows the blast to "any user_id that exists
in bot's auth_users". A leaked key still allows attacks targeting REAL
users, but blocks pollution targeting non-existent users (sanity check).
For a proper defence per-user signed payloads are required (future P1.7-style).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0
HTTP_TIMEOUT_SECONDS = 3.0


class ActiveUsersCache:
    """Thread-/async-safe cache of allowed user_ids.

    Singleton via module-level get_instance(); created lazily on first use.
    All methods are async-safe but the cache reads (allowed()) are sync
    O(1) so they don't add latency to the request path.
    """

    def __init__(
        self,
        bot_url: str,
        bot_api_key: str,
        override_ids: Optional[list[int]],
        cache_ttl: float = CACHE_TTL_SECONDS,
    ):
        self._bot_url = bot_url.rstrip("/")
        self._bot_api_key = bot_api_key
        self._override_ids = override_ids  # None = no override
        self._cache_ttl = cache_ttl
        self._cached_ids: Optional[set[int]] = None
        self._cached_at: float = 0.0
        self._refresh_lock = asyncio.Lock()

    def has_override(self) -> bool:
        return self._override_ids is not None

    async def refresh(self) -> Optional[set[int]]:
        """Fetch the current list from the bot. Returns None on failure
        (caller keeps using the previous cached value or override).

        If an override is configured, we don't even query the bot —
        operational lockdown wins."""
        if self._override_ids is not None:
            self._cached_ids = set(self._override_ids)
            self._cached_at = time.monotonic()
            return self._cached_ids
        if not self._bot_api_key:
            logger.warning("active_users.refresh skipped: BOT_API_KEY not set")
            return None
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    f"{self._bot_url}/api/internal/active-user-ids",
                    headers={"X-Bot-Api-Key": self._bot_api_key},
                )
            if resp.status_code != 200:
                logger.warning(
                    "active_users.refresh_failed status=%s body=%s",
                    resp.status_code, resp.text[:200],
                )
                return None
            data = resp.json()
            ids = set(int(x) for x in data.get("user_ids", []))
            self._cached_ids = ids
            self._cached_at = time.monotonic()
            logger.info("active_users.refreshed count=%d", len(ids))
            return ids
        except Exception as exc:
            logger.warning("active_users.refresh_error err=%s", exc)
            return None

    async def ensure_fresh(self) -> None:
        """If cache is stale (or never populated), refresh it. Holds a
        lock so concurrent requests don't hammer the bot."""
        now = time.monotonic()
        if self._cached_ids is not None and (now - self._cached_at) < self._cache_ttl:
            return
        async with self._refresh_lock:
            # Double-check under lock
            now = time.monotonic()
            if self._cached_ids is not None and (now - self._cached_at) < self._cache_ttl:
                return
            await self.refresh()

    def allowed(self, user_id: int) -> Optional[bool]:
        """Sync O(1) check. Returns:
          - True if user_id is allowed.
          - False if cache is populated and user_id is NOT in it.
          - None if cache is not yet populated (caller decides fallback).
        """
        if self._cached_ids is None:
            return None
        return user_id in self._cached_ids

    @property
    def cached_count(self) -> int:
        return len(self._cached_ids) if self._cached_ids else 0


# ── Singleton wiring ──────────────────────────────────────────────────

_instance: Optional[ActiveUsersCache] = None


def _parse_override_ids(raw: str) -> Optional[list[int]]:
    """Parse the legacy BOT_API_KEY_USER_IDS env var. Returns None if
    unset/malformed (caller falls back to dynamic mode)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        logger.warning("BOT_API_KEY_USER_IDS malformed (%s) — ignoring", raw)
        return None


def get_instance() -> ActiveUsersCache:
    """Return the singleton, initialising on first call from env vars."""
    global _instance
    if _instance is None:
        bot_url = os.getenv("BOT_API_URL", "http://localhost:8180")
        bot_api_key = os.getenv("BOT_API_KEY", "")
        override_ids = _parse_override_ids(os.getenv("BOT_API_KEY_USER_IDS", ""))
        _instance = ActiveUsersCache(bot_url, bot_api_key, override_ids)
        mode = "override" if override_ids is not None else "dynamic"
        logger.info("active_users.init mode=%s bot_url=%s", mode, bot_url)
    return _instance


def reset_instance_for_tests() -> None:
    """Test hook — clear the singleton so the next get_instance() reads
    fresh env vars."""
    global _instance
    _instance = None
