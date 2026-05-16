"""P1.3 + P1.4 audit-2026-05-15 — /api/audit/* gated to ADMIN role.

Pre-fix:
  - GET /api/audit/run -> cualquier user autenticado dispara
    run_all_audits (heavy DB scan). DoS interno trivial. Y GET
    permite CSRF-trigger via <img src=...> sin Bearer header
    (depende de cookie).
  - GET /api/audit/history -> SELECT global de audit_runs sin
    user_id filter. Cualquier user veia las metricas operacionales
    del platform — leak cross-tenant.
  - /api/audit/status y /api/audit/findings idem.

Post-fix:
  - run_all_audits ahora POST + _require_admin (role del JWT).
  - status, findings, history idem ADMIN-only.
  - _extract_role lee el claim "role" del JWT que el bot sign.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from src.dashboard.api import _extract_role, _require_admin


def _mk_request_with_jwt(jwt_payload):
    """Construct a Request stub whose _decode_jwt would return the given payload."""
    req = MagicMock(spec=Request)
    req.headers = {}
    req.cookies = {}
    # We can't trivially mock the module-level _decode_jwt without patching.
    # Instead the tests below use monkeypatch on _decode_jwt.
    return req


class TestExtractRole:
    def test_no_jwt_returns_none(self, monkeypatch):
        monkeypatch.setattr("src.dashboard.api._decode_jwt", lambda r: None)
        assert _extract_role(MagicMock()) is None

    def test_role_present_returns_uppercased(self, monkeypatch):
        monkeypatch.setattr("src.dashboard.api._decode_jwt",
                            lambda r: {"uid": 1, "role": "admin"})
        assert _extract_role(MagicMock()) == "ADMIN"

    def test_role_missing_returns_none(self, monkeypatch):
        # JWT valido pero sin claim "role" -> None (no asume ADMIN ni BASIC).
        monkeypatch.setattr("src.dashboard.api._decode_jwt",
                            lambda r: {"uid": 1})
        assert _extract_role(MagicMock()) is None


class TestRequireAdmin:
    def test_no_jwt_raises_401(self, monkeypatch):
        monkeypatch.setattr("src.dashboard.api._decode_jwt", lambda r: None)
        with pytest.raises(HTTPException) as exc:
            _require_admin(MagicMock())
        assert exc.value.status_code == 401

    def test_basic_user_raises_403(self, monkeypatch):
        # User autenticado pero no admin -> 403, no se cae al codigo
        # protegido.
        monkeypatch.setattr("src.dashboard.api._decode_jwt",
                            lambda r: {"uid": 5, "role": "BASIC"})
        with pytest.raises(HTTPException) as exc:
            _require_admin(MagicMock())
        assert exc.value.status_code == 403
        assert exc.value.detail == "admin_only"

    def test_pro_user_raises_403(self, monkeypatch):
        # Para no-admins explicitos (PRO, PREMIUM): tambien 403.
        # PREMIUM no es admin a efectos de operacion del platform.
        monkeypatch.setattr("src.dashboard.api._decode_jwt",
                            lambda r: {"uid": 5, "role": "PRO"})
        with pytest.raises(HTTPException) as exc:
            _require_admin(MagicMock())
        assert exc.value.status_code == 403

    def test_premium_user_raises_403(self, monkeypatch):
        monkeypatch.setattr("src.dashboard.api._decode_jwt",
                            lambda r: {"uid": 5, "role": "PREMIUM"})
        with pytest.raises(HTTPException) as exc:
            _require_admin(MagicMock())
        assert exc.value.status_code == 403

    def test_admin_user_returns_uid(self, monkeypatch):
        # Happy path: admin pasa el guard y recibe su uid.
        monkeypatch.setattr("src.dashboard.api._decode_jwt",
                            lambda r: {"uid": 1, "role": "ADMIN"})
        uid = _require_admin(MagicMock())
        assert uid == 1

    def test_admin_lowercase_also_passes(self, monkeypatch):
        # _extract_role normaliza a uppercase, asi que un JWT con "admin"
        # tambien pasa. Acomoda inconsistencia futura en el bot signer.
        monkeypatch.setattr("src.dashboard.api._decode_jwt",
                            lambda r: {"uid": 1, "role": "admin"})
        uid = _require_admin(MagicMock())
        assert uid == 1
