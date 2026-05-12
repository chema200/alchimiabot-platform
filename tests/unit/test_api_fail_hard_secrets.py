"""P2.3 audit-2026-05-11 — fail-hard en PLATFORM_ENV=prod si secrets vacios.

Pre-fix: si BOT_API_KEY o JWT_SECRET estaban vacios el platform arrancaba
con un error log y comportamiento degradado:
  - /api/bot/* aceptaba cualquier traffic (header vacio matches env vacia)
  - El platform no podia validar tokens del bot

En prod queremos fail-hard antes de exponer endpoints a usuarios. En dev
la rama legacy (warning) se mantiene para no romper "git clone + run".
"""
import importlib
import sys

import pytest


def _reload_api():
    """Recargar src.dashboard.api con el env actual.

    create_app() lee BOT_API_KEY y _JWT_SECRET a runtime, pero _JWT_SECRET
    es una variable a nivel de modulo. Hay que reload para que pille el
    env nuevo del test.
    """
    for mod in list(sys.modules):
        if mod.startswith("src.dashboard.api") or mod == "src.dashboard.api":
            del sys.modules[mod]
    return importlib.import_module("src.dashboard.api")


class TestProdFailHard:
    def test_prod_empty_bot_api_key_raises(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_ENV", "prod")
        monkeypatch.setenv("BOT_API_KEY", "")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        api = _reload_api()
        with pytest.raises(RuntimeError, match="BOT_API_KEY"):
            api.create_app()

    def test_prod_empty_jwt_secret_raises(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_ENV", "prod")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "")
        api = _reload_api()
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            api.create_app()

    def test_prod_uppercase_value_treated_as_prod(self, monkeypatch):
        # PLATFORM_ENV se compara case-insensitive (vimos "Prod", "PROD"
        # en deployments reales). Verificar.
        monkeypatch.setenv("PLATFORM_ENV", "PROD")
        monkeypatch.setenv("BOT_API_KEY", "")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        api = _reload_api()
        with pytest.raises(RuntimeError):
            api.create_app()

    def test_prod_with_all_secrets_set_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_ENV", "prod")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        api = _reload_api()
        # create_app() construye la app. Que no tire es suficiente —
        # no necesitamos verificar el conjunto completo de rutas aqui.
        app = api.create_app()
        assert app is not None


class TestDevDoesNotFail:
    def test_dev_empty_bot_api_key_does_not_raise(self, monkeypatch):
        # En dev el legacy fallback (log error + warn) sigue siendo valido.
        monkeypatch.setenv("PLATFORM_ENV", "dev")
        monkeypatch.setenv("BOT_API_KEY", "")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        api = _reload_api()
        # No raise -> ok.
        api.create_app()

    def test_dev_empty_jwt_secret_does_not_raise(self, monkeypatch):
        # En dev el JWT_SECRET vacio degrada a "no auth tokens", pero
        # el platform arranca para que el dev pueda usar el navegador
        # sin auth.
        monkeypatch.setenv("PLATFORM_ENV", "dev")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "")
        api = _reload_api()
        api.create_app()

    def test_unset_platform_env_defaults_to_dev(self, monkeypatch):
        # Sin PLATFORM_ENV seteado, el default es "dev" -> no raise.
        monkeypatch.delenv("PLATFORM_ENV", raising=False)
        monkeypatch.setenv("BOT_API_KEY", "")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        api = _reload_api()
        api.create_app()
