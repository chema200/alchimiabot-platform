"""P1.8 audit-2026-05-15 — security headers middleware en platform.

Verifica que TODA respuesta del platform lleve los headers de seguridad
(X-Frame-Options, X-Content-Type-Options, Referrer-Policy,
Permissions-Policy, Content-Security-Policy).

Test usa fastapi.testclient.TestClient sobre una app minima creada con
create_app() en modo dev, y consulta el endpoint de login (publico).
"""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_api():
    for mod in list(sys.modules):
        if mod.startswith("src.dashboard.api") or mod == "src.dashboard.api":
            del sys.modules[mod]
    return importlib.import_module("src.dashboard.api")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PLATFORM_ENV", "dev")
    monkeypatch.setenv("BOT_API_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-chars-long-padding")
    monkeypatch.setenv("BOT_API_KEY_USER_IDS", "1")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    api = _reload_api()
    app = api.create_app()
    from fastapi.testclient import TestClient
    return TestClient(app)


class TestSecurityHeaders:
    def test_x_frame_options_deny(self, client):
        # Login endpoint es publico (no necesita auth) y siempre responde.
        # Aunque devuelva 400/401 por body invalido, los headers DEBEN
        # estar.
        r = client.post("/api/platform/login", json={})
        assert r.headers.get("x-frame-options") == "DENY"

    def test_x_content_type_options_nosniff(self, client):
        r = client.post("/api/platform/login", json={})
        assert r.headers.get("x-content-type-options") == "nosniff"

    def test_referrer_policy_set(self, client):
        r = client.post("/api/platform/login", json={})
        assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy_locks_down_apis(self, client):
        r = client.post("/api/platform/login", json={})
        pp = r.headers.get("permissions-policy", "")
        # Debe denegar al menos camera/microphone/geolocation/payment.
        for restricted in ("camera=()", "microphone=()", "geolocation=()", "payment=()"):
            assert restricted in pp, f"Permissions-Policy missing {restricted}: {pp}"

    def test_csp_present_and_strict(self, client):
        r = client.post("/api/platform/login", json={})
        csp = r.headers.get("content-security-policy", "")
        assert csp, "CSP header missing"
        # Core directivas que necesitamos para defensa contra XSS / framing.
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "base-uri 'self'" in csp

    def test_csp_allows_stripe_and_turnstile(self, client):
        # CSP NO debe bloquear los servicios externos que la app usa
        # legitimamente — Stripe Checkout + Cloudflare Turnstile.
        r = client.post("/api/platform/login", json={})
        csp = r.headers.get("content-security-policy", "")
        assert "js.stripe.com" in csp
        assert "challenges.cloudflare.com" in csp
