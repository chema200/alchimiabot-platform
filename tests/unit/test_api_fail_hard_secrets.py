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
        # P1.11: en prod tambien hay que setear BOT_API_KEY_USER_IDS.
        monkeypatch.setenv("BOT_API_KEY_USER_IDS", "1")
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


class TestBotApiKeyUserIdsBehavior:
    """2026-05-18 — BOT_API_KEY_USER_IDS ya no es obligatorio. El platform
    pulla la allow-list dinamicamente del bot (/api/internal/active-user-ids
    cada 60s). El env var queda como OPCIONAL override operacional para
    emergencias ("solo user 1 mientras debugeo")."""

    def test_prod_without_user_ids_does_not_raise_anymore(self, monkeypatch):
        # Pre-fix tiraba RuntimeError. Post-fix arranca normal en modo dinamico.
        monkeypatch.setenv("PLATFORM_ENV", "prod")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        monkeypatch.delenv("BOT_API_KEY_USER_IDS", raising=False)
        api = _reload_api()
        # Reset singleton del cache para que pille los env vars nuevos
        from src.dashboard._active_users_cache import reset_instance_for_tests
        reset_instance_for_tests()
        app = api.create_app()
        assert app is not None

    def test_prod_with_user_ids_still_works_as_override(self, monkeypatch):
        # El override sigue funcional cuando se setea explicitamente.
        monkeypatch.setenv("PLATFORM_ENV", "prod")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        monkeypatch.setenv("BOT_API_KEY_USER_IDS", "1,2,3")
        api = _reload_api()
        from src.dashboard._active_users_cache import reset_instance_for_tests
        reset_instance_for_tests()
        app = api.create_app()
        assert app is not None
        # El cache singleton creado por create_app debe tener override
        from src.dashboard._active_users_cache import get_instance
        cache = get_instance()
        assert cache.has_override() is True

    def test_dev_without_user_ids_does_not_raise(self, monkeypatch):
        # En dev el legacy "trust mode" se mantiene para no romper "git clone + run".
        monkeypatch.setenv("PLATFORM_ENV", "dev")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        monkeypatch.delenv("BOT_API_KEY_USER_IDS", raising=False)
        api = _reload_api()
        api.create_app()


class TestCorsAllowlist:
    """P1.12 — CORS allowlist correcto: prod sin localhost, dev con."""

    def test_prod_excludes_localhost(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_ENV", "prod")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        monkeypatch.setenv("BOT_API_KEY_USER_IDS", "1")
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        api = _reload_api()
        app = api.create_app()
        # Localizamos el CORSMiddleware en la stack
        cors = next(
            (m for m in app.user_middleware
             if "CORSMiddleware" in str(getattr(m, "cls", m))),
            None
        )
        assert cors is not None
        origins = cors.kwargs.get("allow_origins", [])
        # Cero localhost en prod
        assert not any("localhost" in o for o in origins), origins
        # Sí dominios reales
        assert any("alchimiabot.com" in o for o in origins), origins

    def test_dev_includes_localhost(self, monkeypatch):
        monkeypatch.setenv("PLATFORM_ENV", "dev")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        monkeypatch.delenv("BOT_API_KEY_USER_IDS", raising=False)
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        api = _reload_api()
        app = api.create_app()
        cors = next(
            (m for m in app.user_middleware
             if "CORSMiddleware" in str(getattr(m, "cls", m))),
            None
        )
        assert cors is not None
        origins = cors.kwargs.get("allow_origins", [])
        assert any("localhost:3001" in o for o in origins), origins

    def test_explicit_cors_origins_env_wins(self, monkeypatch):
        # Si el operador setea CORS_ORIGINS, gana sobre los defaults.
        monkeypatch.setenv("PLATFORM_ENV", "prod")
        monkeypatch.setenv("BOT_API_KEY", "valid-key")
        monkeypatch.setenv("JWT_SECRET", "valid-secret")
        monkeypatch.setenv("BOT_API_KEY_USER_IDS", "1")
        monkeypatch.setenv("CORS_ORIGINS", "https://custom.example.com")
        api = _reload_api()
        app = api.create_app()
        cors = next(
            (m for m in app.user_middleware
             if "CORSMiddleware" in str(getattr(m, "cls", m))),
            None
        )
        origins = cors.kwargs.get("allow_origins", [])
        assert origins == ["https://custom.example.com"]
