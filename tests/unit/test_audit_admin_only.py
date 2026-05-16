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
from fastapi import HTTPException

# NOTE: importamos los helpers dentro de cada test (no a module-level)
# porque otros tests del mismo run pueden reload src.dashboard.api
# (e.g. test_security_headers.py llama importlib.import_module despues
# de borrar el modulo del cache). Importar lazy garantiza que cada
# test referencia la version current del modulo, y monkeypatch sobre
# src.dashboard.api._decode_jwt afecta el codepath real.


class TestExtractRole:
    def test_no_jwt_returns_none(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt", lambda r: None)
        assert api_mod._extract_role(MagicMock()) is None

    def test_role_present_returns_uppercased(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt",
                            lambda r: {"uid": 1, "role": "admin"})
        assert api_mod._extract_role(MagicMock()) == "ADMIN"

    def test_role_missing_returns_none(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt",
                            lambda r: {"uid": 1})
        assert api_mod._extract_role(MagicMock()) is None


class TestRequireAdmin:
    def test_no_jwt_raises_401(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt", lambda r: None)
        with pytest.raises(HTTPException) as exc:
            api_mod._require_admin(MagicMock())
        assert exc.value.status_code == 401

    def test_basic_user_raises_403(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt",
                            lambda r: {"uid": 5, "role": "BASIC"})
        with pytest.raises(HTTPException) as exc:
            api_mod._require_admin(MagicMock())
        assert exc.value.status_code == 403
        assert exc.value.detail == "admin_only"

    def test_pro_user_raises_403(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt",
                            lambda r: {"uid": 5, "role": "PRO"})
        with pytest.raises(HTTPException) as exc:
            api_mod._require_admin(MagicMock())
        assert exc.value.status_code == 403

    def test_premium_user_raises_403(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt",
                            lambda r: {"uid": 5, "role": "PREMIUM"})
        with pytest.raises(HTTPException) as exc:
            api_mod._require_admin(MagicMock())
        assert exc.value.status_code == 403

    def test_admin_user_returns_uid(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt",
                            lambda r: {"uid": 1, "role": "ADMIN"})
        uid = api_mod._require_admin(MagicMock())
        assert uid == 1

    def test_admin_lowercase_also_passes(self, monkeypatch):
        from src.dashboard import api as api_mod
        monkeypatch.setattr(api_mod, "_decode_jwt",
                            lambda r: {"uid": 1, "role": "admin"})
        uid = api_mod._require_admin(MagicMock())
        assert uid == 1
