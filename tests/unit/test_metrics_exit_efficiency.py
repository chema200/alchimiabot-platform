"""Fase 5 #26 (2026-04-30) — exit efficiency robust ratio.

Pre-fix: ``_compute_execution`` calculaba ``mean(pnl_pct / mfe_pct)`` sin
filtro ni clamp. Un trade con ``mfe=0.001%`` y ``pnl=-1%`` generaba ratio
``-1000`` que contaminaba el reporte.

Post-fix: ``_compute_exit_efficiency`` (classmethod, pure):
  1. Filtra trades con ``mfe < EXIT_EFF_MIN_MFE_PCT`` (info no confiable).
  2. Clamp ratio a ``[CLAMP_LO, CLAMP_HI]``.
  3. Reporta ``mean`` + ``median`` + ``count`` + ``filtered_out``.

Tests cubren: filtro, clamp, agregados, edge cases (vacíos, todos
filtrados), thresholds en frontera y resultado integrado vía
``_compute_execution``.
"""
from __future__ import annotations

import pytest

from src.quant.metrics.engine import MetricsEngine


def _trade(mfe_pct: float, pnl_pct: float, **extra) -> dict:
    """Trade mínimo para tests del helper."""
    base = {"mfe_pct": mfe_pct, "pnl_pct": pnl_pct}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Constants — guardia para que ningún cambio silencioso rompa el contrato
# ---------------------------------------------------------------------------

class TestConstants:
    def test_min_mfe_threshold_is_5bps(self):
        # Justifica filtrar fees round-trip; documentado en engine.py.
        assert MetricsEngine.EXIT_EFF_MIN_MFE_PCT == 0.05

    def test_clamp_bounds_symmetric(self):
        assert MetricsEngine.EXIT_EFF_CLAMP_LO == -2.0
        assert MetricsEngine.EXIT_EFF_CLAMP_HI == 2.0


# ---------------------------------------------------------------------------
# Filter: trades con MFE muy pequeño NO entran al ratio
# ---------------------------------------------------------------------------

class TestFilterMinMfe:
    def test_below_threshold_filtered_out(self):
        # mfe=0.001% con pnl=-1% generaría ratio -1000 — debe filtrarse.
        trades = [_trade(mfe_pct=0.001, pnl_pct=-1.0)]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 0
        assert result["filtered_out"] == 1
        assert result["mean"] == 0.0
        assert result["median"] == 0.0

    def test_at_threshold_included(self):
        # mfe == MIN_MFE_PCT no filtrado (filter es strict <).
        trades = [_trade(mfe_pct=0.05, pnl_pct=0.025)]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 1
        assert result["filtered_out"] == 0
        assert result["mean"] == 0.5

    def test_just_below_threshold_filtered(self):
        trades = [_trade(mfe_pct=0.0499, pnl_pct=0.025)]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 0
        assert result["filtered_out"] == 1

    def test_zero_mfe_filtered(self):
        # mfe=0 → división por cero evitada por el filtro.
        trades = [_trade(mfe_pct=0.0, pnl_pct=-0.5)]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 0
        assert result["filtered_out"] == 1

    def test_negative_mfe_filtered(self):
        # mfe negativo (no debería ocurrir pero defensive) — también
        # cae bajo threshold positivo y se filtra.
        trades = [_trade(mfe_pct=-0.1, pnl_pct=0.5)]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 0
        assert result["filtered_out"] == 1


# ---------------------------------------------------------------------------
# Clamp: ratios extremos se acotan a [-2, 2]
# ---------------------------------------------------------------------------

class TestClamp:
    def test_extreme_negative_clamped_to_lo(self):
        # mfe pasa filtro pero pnl_pct/mfe = -10 → clamp a -2.
        trades = [_trade(mfe_pct=0.1, pnl_pct=-1.0)]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["mean"] == -2.0
        assert result["median"] == -2.0

    def test_extreme_positive_clamped_to_hi(self):
        trades = [_trade(mfe_pct=0.1, pnl_pct=1.0)]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["mean"] == 2.0

    def test_at_clamp_boundary_unchanged(self):
        trades = [_trade(mfe_pct=0.1, pnl_pct=0.2)]  # ratio = 2.0
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["mean"] == 2.0

    def test_within_bounds_preserved(self):
        trades = [_trade(mfe_pct=0.5, pnl_pct=0.3)]  # ratio = 0.6
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["mean"] == 0.6


# ---------------------------------------------------------------------------
# Aggregates: mean, median, count
# ---------------------------------------------------------------------------

class TestAggregates:
    def test_mean_and_median_simple(self):
        # ratios: 0.5, 0.7, 0.9 → mean=0.7, median=0.7.
        trades = [
            _trade(mfe_pct=1.0, pnl_pct=0.5),
            _trade(mfe_pct=1.0, pnl_pct=0.7),
            _trade(mfe_pct=1.0, pnl_pct=0.9),
        ]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 3
        assert result["filtered_out"] == 0
        assert result["mean"] == 0.7
        assert result["median"] == 0.7

    def test_median_robust_vs_mean(self):
        # Aunque clamp acota, el median sigue siendo más robusto que el
        # mean en muestras asimétricas. ratios: -2, 0.5, 0.5, 0.5, 0.5
        # → mean = -0.0, median = 0.5.
        trades = [
            _trade(mfe_pct=0.1, pnl_pct=-1.0),  # clamp → -2
            _trade(mfe_pct=1.0, pnl_pct=0.5),
            _trade(mfe_pct=1.0, pnl_pct=0.5),
            _trade(mfe_pct=1.0, pnl_pct=0.5),
            _trade(mfe_pct=1.0, pnl_pct=0.5),
        ]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 5
        assert result["median"] == 0.5
        # mean es ((-2) + 0.5*4) / 5 = 0.0
        assert result["mean"] == 0.0

    def test_mixed_filter_and_clamp(self):
        # 1 filtrado + 2 clamp + 1 normal.
        trades = [
            _trade(mfe_pct=0.001, pnl_pct=-1.0),  # filtered
            _trade(mfe_pct=0.1, pnl_pct=-1.0),    # clamp lo → -2
            _trade(mfe_pct=0.1, pnl_pct=1.0),     # clamp hi → 2
            _trade(mfe_pct=1.0, pnl_pct=0.4),     # 0.4
        ]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 3
        assert result["filtered_out"] == 1
        # ratios: -2, 2, 0.4 → mean = 0.4/3 ≈ 0.1333, median = 0.4
        assert result["mean"] == pytest.approx(0.1333, abs=0.001)
        assert result["median"] == 0.4


# ---------------------------------------------------------------------------
# Edge cases: empty / all filtered / missing fields
# ---------------------------------------------------------------------------

class TestEdges:
    def test_empty_trades(self):
        result = MetricsEngine._compute_exit_efficiency([])
        assert result == {"mean": 0.0, "median": 0.0, "count": 0, "filtered_out": 0}

    def test_all_trades_filtered(self):
        trades = [
            _trade(mfe_pct=0.001, pnl_pct=-1.0),
            _trade(mfe_pct=0.0, pnl_pct=0.0),
            _trade(mfe_pct=0.04, pnl_pct=0.02),
        ]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 0
        assert result["filtered_out"] == 3
        assert result["mean"] == 0.0
        assert result["median"] == 0.0

    def test_missing_fields_default_to_zero_then_filtered(self):
        # Si un trade no trae mfe_pct, se trata como 0 → filtrado.
        trades = [{}, {"pnl_pct": 0.5}]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 0
        assert result["filtered_out"] == 2

    def test_none_fields_treated_as_zero(self):
        trades = [{"mfe_pct": None, "pnl_pct": None}]
        result = MetricsEngine._compute_exit_efficiency(trades)
        assert result["count"] == 0
        assert result["filtered_out"] == 1


# ---------------------------------------------------------------------------
# Integration vía _compute_execution: las nuevas claves aparecen
# ---------------------------------------------------------------------------

class TestExecutionIntegration:
    def test_execution_exposes_new_keys(self):
        trades = [_trade(mfe_pct=1.0, pnl_pct=0.5, duration_seconds=30, mae_pct=-0.2)]
        engine = MetricsEngine()
        out = engine._compute_execution(trades)
        assert "avg_exit_efficiency" in out
        assert "median_exit_efficiency" in out
        assert "exit_efficiency_sample_size" in out
        assert "exit_efficiency_filtered_out" in out
        assert out["exit_efficiency_sample_size"] == 1
        assert out["exit_efficiency_filtered_out"] == 0
        assert out["avg_exit_efficiency"] == 0.5
        assert out["median_exit_efficiency"] == 0.5

    def test_execution_with_outlier_no_longer_contaminates(self):
        # El bug que justifica la fase: un outlier ya no rompe el mean.
        trades = [
            _trade(mfe_pct=0.001, pnl_pct=-1.0),  # antes: ratio -1000
            _trade(mfe_pct=1.0, pnl_pct=0.5),
            _trade(mfe_pct=1.0, pnl_pct=0.6),
        ]
        engine = MetricsEngine()
        out = engine._compute_execution(trades)
        assert out["exit_efficiency_sample_size"] == 2
        assert out["exit_efficiency_filtered_out"] == 1
        # mean = (0.5 + 0.6) / 2 = 0.55. Limpio, sin outlier.
        assert out["avg_exit_efficiency"] == 0.55
