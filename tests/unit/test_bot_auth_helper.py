"""P0.1 audit-2026-05-15 — _bot_auth helper centralizes admin credentials.

Pre-fix: 4 audit/health scripts hardcoded the same admin user+password in
source (and in git history). Compromise of the repo = full admin access.

Post-fix: credentials live in AUDIT_BOT_USERNAME / AUDIT_BOT_PASSWORD env
vars, the helper caches the JWT for 50min, and returns None (with a single
warning) when env vars are not configured so audit steps degrade gracefully.
"""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_helper():
    """Reload the module so it picks up the monkeypatched env vars at import."""
    for mod in list(sys.modules):
        if mod.endswith("_bot_auth"):
            del sys.modules[mod]
    return importlib.import_module(
        "src.observability.health._bot_auth"
    )


class TestCredentialsRequired:
    @pytest.mark.asyncio
    async def test_missing_username_returns_none(self, monkeypatch):
        monkeypatch.delenv("AUDIT_BOT_USERNAME", raising=False)
        monkeypatch.setenv("AUDIT_BOT_PASSWORD", "irrelevant")
        helper = _reload_helper()

        token = await helper.get_bot_token("http://localhost:8180")

        assert token is None

    @pytest.mark.asyncio
    async def test_missing_password_returns_none(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BOT_USERNAME", "audit-bot")
        monkeypatch.delenv("AUDIT_BOT_PASSWORD", raising=False)
        helper = _reload_helper()

        token = await helper.get_bot_token("http://localhost:8180")

        assert token is None

    @pytest.mark.asyncio
    async def test_both_blank_returns_none(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BOT_USERNAME", "")
        monkeypatch.setenv("AUDIT_BOT_PASSWORD", "")
        helper = _reload_helper()

        token = await helper.get_bot_token("http://localhost:8180")

        assert token is None


class TestCacheBehavior:
    def test_invalidate_cache_resets_state(self, monkeypatch):
        # Cache state should be zeroed after invalidate.
        monkeypatch.setenv("AUDIT_BOT_USERNAME", "u")
        monkeypatch.setenv("AUDIT_BOT_PASSWORD", "p")
        helper = _reload_helper()

        # Seed cache manually so we can verify the reset.
        helper._cached_token = "fake-token"
        helper._cached_at = 99999.0

        helper.invalidate_cache()

        assert helper._cached_token is None
        assert helper._cached_at == 0.0


class TestWarnsOnce:
    @pytest.mark.asyncio
    async def test_missing_creds_warning_only_once(self, monkeypatch, caplog):
        # Log noise control: if creds are missing we must emit a single
        # WARNING (not one per call) so logs stay readable when audit
        # scripts run on a cron.
        import logging
        monkeypatch.delenv("AUDIT_BOT_USERNAME", raising=False)
        monkeypatch.delenv("AUDIT_BOT_PASSWORD", raising=False)
        helper = _reload_helper()

        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                await helper.get_bot_token("http://localhost:8180")

        # Filter to the helper's logger to avoid counting unrelated noise.
        helper_warnings = [
            r for r in caplog.records
            if r.name == helper.__name__ and r.levelno == logging.WARNING
        ]
        assert len(helper_warnings) == 1
