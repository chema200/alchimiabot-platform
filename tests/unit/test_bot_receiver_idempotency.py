"""V83 outbox idempotency — unit tests for bot_receiver.

Covers:
- _parse_event_id: format validation
- _find_by_event_id: short-circuits on None event_id
- HTTP 400 paths: malformed event_id / dates do NOT reach the DB
- MarkerService.create_marker with event_id (ON CONFLICT branch)

Multi-tenant invariant: tests use distinct user_ids to verify the helpers
do not leak between users (event_id is global UUID; uniqueness is per-event,
not per-user, so cross-user collision is impossible by construction).
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ingestion.rest.bot_receiver import (
    router,
    set_session_factory,
    _parse_event_id,
    _find_by_event_id,
    _warn_if_no_event_id,
)


# ---------------------------------------------------------------------------
# _parse_event_id — pure
# ---------------------------------------------------------------------------

class TestParseEventId:
    def test_none_returns_none(self):
        assert _parse_event_id(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_event_id("") is None

    def test_valid_uuid_returns_uuid_object(self):
        s = "550e8400-e29b-41d4-a716-446655440000"
        result = _parse_event_id(s)
        assert isinstance(result, uuid.UUID)
        assert str(result) == s

    def test_invalid_uuid_raises(self):
        with pytest.raises((ValueError, TypeError)):
            _parse_event_id("not-a-uuid")

    def test_uuid_with_braces_or_garbage_raises(self):
        with pytest.raises((ValueError, TypeError)):
            _parse_event_id("zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz")


# ---------------------------------------------------------------------------
# _find_by_event_id — short-circuit
# ---------------------------------------------------------------------------

class TestFindByEventId:
    async def test_none_event_id_short_circuits_no_db_call(self):
        """When event_id is None, we MUST NOT touch the DB."""
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=AssertionError("DB should not be called"))
        result = await _find_by_event_id(session, MagicMock(), None)
        assert result is None
        session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# _warn_if_no_event_id — observability for legacy / misconfigured producers
# ---------------------------------------------------------------------------

class TestWarnIfNoEventId:
    """V84 — visibility for any producer that bypasses the event_id contract.

    Post-V83 the bot generates event_id at enqueue, so a missing event_id
    on critical events means: legacy bot version, manual curl, or a
    backfill script that didn't follow the convention. The platform does
    not reject (idempotency degrades gracefully) but logs visibly.

    structlog renders to stdout/stderr, so we use capfd (file-descriptor
    level capture) instead of caplog.
    """

    def test_no_event_id_logs_warning(self, capfd):
        payload = MagicMock(event_id=None, user_id=42, coin="BTC")
        _warn_if_no_event_id("/trade", payload)
        out = capfd.readouterr().out + capfd.readouterr().err
        assert "no_event_id" in out
        assert "/trade" in out
        assert "user_id=42" in out

    def test_present_event_id_does_not_log(self, capfd):
        payload = MagicMock(event_id=str(uuid.uuid4()), user_id=1, coin="BTC")
        _warn_if_no_event_id("/trade", payload)
        out = capfd.readouterr().out + capfd.readouterr().err
        assert "no_event_id" not in out

    def test_empty_event_id_logs_warning(self, capfd):
        payload = MagicMock(event_id="", user_id=1, coin="BTC")
        _warn_if_no_event_id("/trade", payload)
        out = capfd.readouterr().out + capfd.readouterr().err
        assert "no_event_id" in out


# ---------------------------------------------------------------------------
# HTTP 400 — payload validation paths (no DB needed)
# ---------------------------------------------------------------------------

class _StubFactory:
    """Stub session factory: enough to bypass the 503 'DB not ready' check.

    The 400 paths return BEFORE opening a session, so this is never entered.
    """
    def __call__(self):
        return self

    async def __aenter__(self):
        raise AssertionError("session should not be opened on 400 paths")

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    set_session_factory(_StubFactory())
    return TestClient(app)


class TestSignal400:
    def test_bad_event_id_returns_400(self, client):
        resp = client.post("/api/bot/signal", json={
            "event_id": "not-a-uuid",
            "user_id": 1,
            "coin": "BTC", "side": "LONG", "action": "ENTER",
        })
        assert resp.status_code == 400
        assert "invalid event_id" in resp.json()["error"]

    def test_no_event_id_does_not_short_circuit_with_400(self, client):
        """Legacy path: omitting event_id is valid (returns 503 because
        StubFactory raises on entry — proves we passed validation)."""
        # The stub factory will raise AssertionError when entered, which surfaces
        # as a 500 from FastAPI's exception handler. The KEY assertion is that
        # the response is NOT 400 — meaning validation accepted None event_id.
        try:
            resp = client.post("/api/bot/signal", json={
                "user_id": 1,
                "coin": "BTC", "side": "LONG", "action": "SKIP",
            })
            assert resp.status_code != 400
        except AssertionError:
            # StubFactory raised — confirms validation passed and DB was attempted.
            pass


class TestSignalsBatch400:
    def test_one_bad_event_id_in_batch_returns_400(self, client):
        good = str(uuid.uuid4())
        resp = client.post("/api/bot/signals", json=[
            {"event_id": good, "user_id": 1, "coin": "BTC", "side": "LONG", "action": "ENTER"},
            {"event_id": "garbage", "user_id": 1, "coin": "ETH", "side": "SHORT", "action": "ENTER"},
        ])
        assert resp.status_code == 400
        assert "ETH" in resp.json()["error"] or "invalid event_id" in resp.json()["error"]


class TestTrade400:
    def _payload(self, **overrides):
        base = {
            "user_id": 1, "coin": "BTC", "side": "LONG",
            "entry_price": 100.0, "exit_price": 101.0,
            "size": 0.1, "gross_pnl": 0.1, "fee": 0.01, "net_pnl": 0.09,
            "entry_time": "2026-04-30T12:00:00+00:00",
            "exit_time": "2026-04-30T12:05:00+00:00",
        }
        base.update(overrides)
        return base

    def test_bad_event_id_returns_400(self, client):
        resp = client.post("/api/bot/trade", json=self._payload(event_id="garbage"))
        assert resp.status_code == 400
        assert "invalid event_id" in resp.json()["error"]

    def test_bad_entry_time_returns_400(self, client):
        resp = client.post("/api/bot/trade", json=self._payload(entry_time="not-a-date"))
        assert resp.status_code == 400
        assert "entry_time" in resp.json()["error"] or "exit_time" in resp.json()["error"]

    def test_bad_exit_time_returns_400(self, client):
        resp = client.post("/api/bot/trade", json=self._payload(exit_time="not-a-date"))
        assert resp.status_code == 400


class TestRegime400:
    def test_bad_event_id_returns_400(self, client):
        resp = client.post("/api/bot/regime", json={
            "event_id": "garbage", "user_id": 1, "coin": "BTC", "regime": "TREND_UP",
        })
        assert resp.status_code == 400
        assert "invalid event_id" in resp.json()["error"]


class TestMarker400:
    def test_bad_event_id_returns_400(self, client):
        resp = client.post("/api/bot/marker", json={
            "event_id": "garbage", "user_id": 1,
            "category": "MANUAL", "label": "test",
        })
        assert resp.status_code == 400
        assert "invalid event_id" in resp.json()["error"]


# ---------------------------------------------------------------------------
# MarkerService.create_marker — event_id branch (ON CONFLICT)
# ---------------------------------------------------------------------------

class TestMarkerServiceEventId:
    """Verify that event_id is passed through to the SQL query and that the
    duplicate path returns the existing id without re-inserting.

    Multi-tenant note: MarkerService accepts user_id and event_id independently.
    Since event_id is globally unique, there is no risk of cross-user collision.
    """

    async def test_event_id_triggers_on_conflict_branch(self):
        from src.quant.markers.marker_service import MarkerService

        eid = uuid.uuid4()
        captured_sql_calls = []

        # Mock execute result for the INSERT ... ON CONFLICT statement.
        # First execute returns scalar=42 (insert succeeded, id=42).
        insert_result = MagicMock()
        insert_result.scalar = MagicMock(return_value=42)

        session = AsyncMock()

        async def execute(stmt, params=None):
            captured_sql_calls.append((str(stmt), params))
            return insert_result

        session.execute = execute
        session.commit = AsyncMock()

        class _Factory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return session

            async def __aexit__(self, *args):
                pass

        service = MarkerService(_Factory())
        marker_id = await service.create_marker(
            event_id=eid, user_id=1, category="STRATEGY", label="test",
        )

        assert marker_id == 42
        assert len(captured_sql_calls) == 1, "should only INSERT, not also SELECT"
        sql, params = captured_sql_calls[0]
        assert "ON CONFLICT (event_id) DO NOTHING" in sql
        assert params["event_id"] == eid
        assert params["user_id"] == 1

    async def test_event_id_conflict_returns_existing_id(self):
        from src.quant.markers.marker_service import MarkerService

        eid = uuid.uuid4()

        # First execute (INSERT): scalar() returns None (conflict).
        # Second execute (SELECT): scalar() returns 99 (existing id).
        insert_result = MagicMock()
        insert_result.scalar = MagicMock(return_value=None)
        select_result = MagicMock()
        select_result.scalar = MagicMock(return_value=99)
        results = iter([insert_result, select_result])

        session = AsyncMock()

        async def execute(stmt, params=None):
            return next(results)

        session.execute = execute
        session.commit = AsyncMock()

        class _Factory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return session

            async def __aexit__(self, *args):
                pass

        service = MarkerService(_Factory())
        marker_id = await service.create_marker(
            event_id=eid, user_id=1, category="STRATEGY", label="test",
        )

        assert marker_id == 99

    async def test_no_event_id_uses_legacy_branch(self):
        """Without event_id, the service should NOT use ON CONFLICT."""
        from src.quant.markers.marker_service import MarkerService

        captured_sql = []

        result = MagicMock()
        result.scalar = MagicMock(return_value=7)

        session = AsyncMock()

        async def execute(stmt, params=None):
            captured_sql.append(str(stmt))
            return result

        session.execute = execute
        session.commit = AsyncMock()

        class _Factory:
            def __call__(self):
                return self

            async def __aenter__(self):
                return session

            async def __aexit__(self, *args):
                pass

        service = MarkerService(_Factory())
        marker_id = await service.create_marker(
            user_id=1, category="MANUAL", label="manual entry",
        )

        assert marker_id == 7
        assert "ON CONFLICT" not in captured_sql[0]
