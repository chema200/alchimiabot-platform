"""2026-05-18 — ActiveUsersCache replaces static BOT_API_KEY_USER_IDS env var.

Tests cubren los modos:
  - override mode: si BOT_API_KEY_USER_IDS esta seteada -> usar esa lista
    y no consultar al bot. Caso uso: emergencia operacional.
  - dynamic mode: sin override -> pull del bot /api/internal/active-user-ids
    cada 60s, cache entre llamadas.
  - failure: bot inalcanzable -> mantener cache previa, NO romper requests.
  - first request before bot is up: cache None -> allowed() devuelve None
    -> _check_user_id degrada elegante (trust mode transitorio).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.dashboard._active_users_cache import (
    ActiveUsersCache, _parse_override_ids,
)


class TestParseOverrideIds:
    def test_empty_returns_none(self):
        assert _parse_override_ids("") is None
        assert _parse_override_ids("   ") is None

    def test_valid_csv(self):
        assert _parse_override_ids("1,2,3") == [1, 2, 3]
        assert _parse_override_ids("1, 2, 3") == [1, 2, 3]
        assert _parse_override_ids("7") == [7]

    def test_malformed_returns_none(self):
        # Numero invalido -> None, no excepcion (mejor degradar a
        # dynamic mode que romper el arranque del platform).
        assert _parse_override_ids("1,abc,3") is None
        assert _parse_override_ids("not-numbers") is None


class TestOverrideMode:
    @pytest.mark.asyncio
    async def test_override_skips_bot_query(self):
        # Si la env var esta seteada el cache NO debe consultar al bot.
        # Caso uso: emergencia "solo user 1 mientras debugeo un leak".
        cache = ActiveUsersCache(
            bot_url="http://bot-no-existe:99999",
            bot_api_key="key",
            override_ids=[1, 5],
        )
        result = await cache.refresh()
        assert result == {1, 5}
        assert cache.allowed(1) is True
        assert cache.allowed(5) is True
        assert cache.allowed(99) is False
        # Hot path debe ser sync O(1)
        assert cache.cached_count == 2

    def test_has_override(self):
        cache_with = ActiveUsersCache("http://bot", "key", [1])
        cache_without = ActiveUsersCache("http://bot", "key", None)
        assert cache_with.has_override() is True
        assert cache_without.has_override() is False


class TestDynamicMode:
    @pytest.mark.asyncio
    async def test_refresh_pulls_from_bot(self):
        # Mock del HTTP get
        cache = ActiveUsersCache("http://bot:8180", "test-key", None)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"count": 3, "user_ids": [1, 2, 5]}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await cache.refresh()

        assert result == {1, 2, 5}
        # Verifica que llamo al endpoint con X-Bot-Api-Key
        call_args = mock_client.get.call_args
        assert "/api/internal/active-user-ids" in call_args.args[0]
        assert call_args.kwargs["headers"]["X-Bot-Api-Key"] == "test-key"

    @pytest.mark.asyncio
    async def test_bot_failure_keeps_previous_cache(self):
        # Si el bot devuelve 5xx, mantener la cache previa (graceful
        # degradation). No tirar None encima de un cache bueno.
        cache = ActiveUsersCache("http://bot:8180", "key", None)
        # Seed cache previa
        cache._cached_ids = {1, 2}
        cache._cached_at = 999.0

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await cache.refresh()

        # Refresh devuelve None pero la cache previa NO se borra
        assert result is None
        assert cache._cached_ids == {1, 2}, "previous cache should persist on failure"

    @pytest.mark.asyncio
    async def test_network_error_does_not_crash(self):
        # ConnectionError / timeout -> log warning + None, NO raise.
        cache = ActiveUsersCache("http://bot:99999", "key", None)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("conn refused"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            result = await cache.refresh()
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_api_key_does_not_query(self):
        # Si BOT_API_KEY no esta, no tiene sentido consultar al bot
        # (el bot rechazaria con 403). Refresh devuelve None sin hacer
        # network call.
        cache = ActiveUsersCache("http://bot:8180", "", None)

        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await cache.refresh()

        assert result is None
        mock_client_cls.assert_not_called()


class TestCacheTtl:
    @pytest.mark.asyncio
    async def test_ensure_fresh_no_op_when_cache_warm(self):
        cache = ActiveUsersCache("http://bot:8180", "key", None, cache_ttl=60.0)
        cache._cached_ids = {1, 2}
        import time
        cache._cached_at = time.monotonic()  # justo ahora

        # ensure_fresh debe NO llamar refresh porque cache es fresca
        with patch.object(cache, "refresh", AsyncMock()) as mock_refresh:
            await cache.ensure_fresh()
            mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_fresh_refreshes_when_stale(self):
        cache = ActiveUsersCache("http://bot:8180", "key", None, cache_ttl=60.0)
        cache._cached_ids = {1}
        cache._cached_at = 0.0  # muy antigua

        with patch.object(cache, "refresh", AsyncMock()) as mock_refresh:
            await cache.ensure_fresh()
            mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_fresh_concurrent_refresh_locked(self):
        # 5 requests concurrentes con cache stale -> solo 1 refresh.
        cache = ActiveUsersCache("http://bot:8180", "key", None, cache_ttl=60.0)
        cache._cached_ids = None  # nunca poblada

        call_count = 0

        async def slow_refresh():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # simula latencia
            cache._cached_ids = {1}
            import time
            cache._cached_at = time.monotonic()

        with patch.object(cache, "refresh", side_effect=slow_refresh):
            await asyncio.gather(*[cache.ensure_fresh() for _ in range(5)])

        assert call_count == 1, "lock should serialise refreshes; only 1 should fire"


class TestAllowedSync:
    """allowed() es sync O(1) — no debe agregar latencia al request path."""

    def test_cache_unpopulated_returns_none(self):
        # Indica al caller que use fallback (trust mode transitorio).
        cache = ActiveUsersCache("http://bot:8180", "key", None)
        assert cache.allowed(1) is None

    def test_populated_returns_true_or_false(self):
        cache = ActiveUsersCache("http://bot:8180", "key", None)
        cache._cached_ids = {1, 7}
        assert cache.allowed(1) is True
        assert cache.allowed(7) is True
        assert cache.allowed(99) is False
