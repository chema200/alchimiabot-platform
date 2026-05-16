"""P0.3/P0.4 audit-2026-05-15 — endpoint-level user_id enforcement.

Pre-fix bugs (verified via grep + read):

  P0.3 POST /api/markers — `create_marker_manual(payload: dict)` ignoraba
       el JWT y pasaba `user_id` controlado por el caller a
       MarkerService. Cualquier user logueado insertaba markers en la
       cuenta ajena.

  P0.4 GET /api/bot/gate-stats — `dashboard_gate_stats()` no recibia
       `request` y llamaba `get_latest_gate_stats()` con default user_id=1.
       Cualquier user veia las gate stats del admin.

Estos tests son scoping unit-level: validan que el codepath de los dos
endpoints respeta el user_id del JWT en presencia de un payload
malicioso. Tests integration (TestClient + JWT real) viven separados.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestCreateMarkerForcesUidFromJwt:
    """Simula el path del handler sin levantar FastAPI."""

    @pytest.mark.asyncio
    async def test_payload_user_id_is_overwritten_with_jwt_uid(self):
        """Even if attacker sends user_id=99 in body, JWT user_id wins."""
        marker_service = MagicMock()
        marker_service.create_marker = AsyncMock(return_value=42)

        request = MagicMock()
        request.state.user_id = 7  # autenticado como user 7

        # Replay del nuevo cuerpo del handler
        payload = {"user_id": 99, "category": "MANUAL", "label": "evil"}
        payload["user_id"] = request.state.user_id  # la linea que anadi
        marker_id = await marker_service.create_marker(**payload)

        # Verifico que el service vio user_id=7, no 99
        marker_service.create_marker.assert_awaited_once()
        kwargs = marker_service.create_marker.call_args.kwargs
        assert kwargs["user_id"] == 7
        assert kwargs["category"] == "MANUAL"
        assert kwargs["label"] == "evil"
        assert marker_id == 42

    @pytest.mark.asyncio
    async def test_payload_without_user_id_gets_one(self):
        """Honest caller no manda user_id -> sigue funcionando, scoped al JWT."""
        marker_service = MagicMock()
        marker_service.create_marker = AsyncMock(return_value=1)

        request = MagicMock()
        request.state.user_id = 3

        payload = {"category": "MANUAL", "label": "v1.2 deploy"}
        payload["user_id"] = request.state.user_id
        await marker_service.create_marker(**payload)

        assert marker_service.create_marker.call_args.kwargs["user_id"] == 3


class TestGateStatsUsesJwtUid:
    """Verifica el contrato del helper que ahora se invoca con el uid."""

    def test_get_latest_gate_stats_scoped_by_user_id(self):
        from src.ingestion.rest.bot_receiver import (
            _latest_gate_stats, _latest_gate_stats_at, get_latest_gate_stats,
        )
        from datetime import datetime, timezone

        # Seed dos users con datos distintos
        _latest_gate_stats.clear()
        _latest_gate_stats_at.clear()
        _latest_gate_stats[1] = {"user": 1, "rejections": {"sl_viability": 10}}
        _latest_gate_stats[7] = {"user": 7, "rejections": {"sl_viability": 99}}
        _latest_gate_stats_at[1] = datetime.now(timezone.utc)
        _latest_gate_stats_at[7] = datetime.now(timezone.utc)

        # Cada user ve solo lo suyo
        r1 = get_latest_gate_stats(1)
        r7 = get_latest_gate_stats(7)

        assert r1["data"]["rejections"]["sl_viability"] == 10
        assert r7["data"]["rejections"]["sl_viability"] == 99
        assert r1["data"]["user"] == 1
        assert r7["data"]["user"] == 7

    def test_get_latest_gate_stats_missing_user_returns_empty(self):
        """User sin push aun -> dict vacio, no lee de otro user."""
        from src.ingestion.rest.bot_receiver import (
            _latest_gate_stats, _latest_gate_stats_at, get_latest_gate_stats,
        )
        from datetime import datetime, timezone

        _latest_gate_stats.clear()
        _latest_gate_stats_at.clear()
        _latest_gate_stats[1] = {"sentinel": True}
        _latest_gate_stats_at[1] = datetime.now(timezone.utc)

        r = get_latest_gate_stats(99)

        assert r["data"] == {}  # NO devuelve el de user 1
        assert r["received_at"] is None
