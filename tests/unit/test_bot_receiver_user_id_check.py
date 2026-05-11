"""P1 multi-tenant: /api/bot/* user_id validation against API-key binding.

Pre-2026-05-12: when authenticated via X-Bot-Api-Key, payload.user_id was
trusted blindly. A rogue bot (or leaked key) could push data for any
user_id, polluting other tenants' analytics.

Fix: middleware attaches request.state.allowed_user_ids (from
BOT_API_KEY_USER_IDS env, or [uid] for JWT-auth). Each /api/bot/* endpoint
calls _check_user_id which returns a 403 JSONResponse if the payload's
user_id is outside the allowed set.

Backward compat: allowed_user_ids = None preserves legacy unrestricted
behaviour so single-user installs that haven't set BOT_API_KEY_USER_IDS
keep working.
"""
from unittest.mock import MagicMock

from fastapi import Request
from fastapi.responses import JSONResponse

from src.ingestion.rest.bot_receiver import _check_user_id


def _mk_request(allowed_user_ids):
    """Build a minimal Request stub with state.allowed_user_ids set."""
    req = MagicMock(spec=Request)
    req.state = MagicMock()
    req.state.allowed_user_ids = allowed_user_ids
    return req


class TestCheckUserIdBackwardCompat:
    def test_unrestricted_returns_none_for_any_user_id(self):
        # allowed_user_ids = None -> unrestricted (legacy / no env var set).
        # Any payload user_id passes through with no enforcement.
        assert _check_user_id(_mk_request(None), 1, "/signal") is None
        assert _check_user_id(_mk_request(None), 999, "/trade") is None
        assert _check_user_id(_mk_request(None), 0, "/marker") is None


class TestCheckUserIdEnforcement:
    def test_allowed_user_id_passes(self):
        # API key bound to users [1, 2]. Payload user_id=1 -> OK.
        resp = _check_user_id(_mk_request([1, 2]), 1, "/signal")
        assert resp is None

    def test_allowed_user_id_passes_when_second_in_list(self):
        # User_id=2 also allowed in the same binding.
        resp = _check_user_id(_mk_request([1, 2]), 2, "/trade")
        assert resp is None

    def test_disallowed_user_id_returns_403(self):
        # API key bound to [1, 2]. Payload user_id=99 -> rejected.
        resp = _check_user_id(_mk_request([1, 2]), 99, "/signal")
        assert resp is not None
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 403

    def test_single_user_binding_rejects_others(self):
        # Common single-user deployment: BOT_API_KEY_USER_IDS=1.
        # Anything other than user_id=1 must be rejected.
        assert _check_user_id(_mk_request([1]), 2, "/signal") is not None
        assert _check_user_id(_mk_request([1]), 0, "/marker") is not None
        # And user_id=1 still works.
        assert _check_user_id(_mk_request([1]), 1, "/regime") is None

    def test_empty_allowed_list_rejects_everything(self):
        # Edge case: explicitly empty list (= "no users allowed").
        # Behaves as strict deny — does NOT degrade to unrestricted (that's
        # what None is for).
        assert _check_user_id(_mk_request([]), 1, "/signal") is not None
        assert _check_user_id(_mk_request([]), 999, "/trade") is not None


class TestCheckUserIdMissingState:
    def test_missing_state_attribute_treated_as_unrestricted(self):
        # If somehow request.state.allowed_user_ids is not set (e.g. an
        # endpoint reached without going through the middleware in a
        # test scenario), fall back to unrestricted to avoid a 500 from
        # AttributeError. The middleware always sets it in real prod.
        req = MagicMock(spec=Request)
        req.state = MagicMock(spec=[])  # no attributes at all

        resp = _check_user_id(req, 1, "/signal")

        assert resp is None
