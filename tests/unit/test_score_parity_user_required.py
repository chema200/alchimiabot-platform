"""P1.10 audit-2026-05-15 — score_parity.analyze requires user_id.

Pre-fix:
  - Signature was `analyze(self, user_id: int | None = None)`.
  - When called without args (executive_summary.py), the SQL ran with
    `uf = ""` and `uf_where = ""` -> aggregated across ALL tenants.
    Every user saw the global score coverage of the entire deployment.
  - SQL filter built with f-string: `f"AND user_id = {user_id}"`.
    Hoy seguro (user_id viene del JWT como int), pero un refactor que
    aceptase str llevaba a SQLi trivial.

Post-fix:
  - user_id obligatorio (raises TypeError si no es int).
  - Filtros usan bind param `:uid` en lugar de f-string. SQLi impossible
    independientemente del tipo del user_id (sqlalchemy castea o falla
    en bind, no en concat).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.quant.analysis.score_parity import ScoreParityAnalyzer


class TestUserIdRequired:
    @pytest.mark.asyncio
    async def test_no_user_id_raises(self):
        analyzer = ScoreParityAnalyzer(session_factory=MagicMock())
        with pytest.raises(TypeError):
            await analyzer.analyze()  # type: ignore[call-arg]

    @pytest.mark.asyncio
    async def test_non_int_user_id_raises(self):
        analyzer = ScoreParityAnalyzer(session_factory=MagicMock())
        with pytest.raises(TypeError, match="must be int"):
            await analyzer.analyze(user_id="1")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_none_user_id_raises(self):
        # Defensive: explicit None debe seguir tirando aunque sea ergonomicamente
        # mas natural que llamar sin el arg.
        analyzer = ScoreParityAnalyzer(session_factory=MagicMock())
        with pytest.raises(TypeError):
            await analyzer.analyze(user_id=None)  # type: ignore[arg-type]


class TestBindParamsUsed:
    """Verifica que las queries pasen user_id como bind, no como f-string."""

    @pytest.mark.asyncio
    async def test_session_execute_called_with_uid_bind(self):
        # Mock con total>0 para que analyze() llegue al final sin tropezar
        # con el codepath que asume metricas computadas. Solo nos importa
        # verificar que CADA execute pase {"uid": 7}.
        mock_row = {
            "total": 10,
            "has_signal_score": 5, "has_trend_score": 5, "has_micro_score": 5,
            "all_zero": 2, "all_present": 5, "has_snapshot": 5,
        }
        mock_result = MagicMock()
        mock_result.mappings.return_value.first.return_value = mock_row

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        sf = MagicMock(return_value=mock_session)

        analyzer = ScoreParityAnalyzer(session_factory=sf)
        await analyzer.analyze(user_id=7)

        # Every execute call must have second arg dict with "uid": 7.
        assert mock_session.execute.await_count >= 6, "expected 6 queries (trades + signals + pd_trades + pd_signals + 2 legacy)"
        for call in mock_session.execute.await_args_list:
            args, kwargs = call
            assert len(args) >= 2, f"execute called without bind params: {args}"
            params = args[1]
            assert isinstance(params, dict)
            assert params.get("uid") == 7
