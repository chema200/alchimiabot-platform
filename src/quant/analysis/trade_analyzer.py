"""Trade Analyzer: auto-analyzes each closed trade and generates a verdict.

Uses trade outcome data, signal evaluation context, and position snapshots
to produce a structured verdict with entry timing, MFE capture, SL analysis,
improvements, and counterfactual what-if scenarios.
"""

from datetime import datetime, timezone
from typing import Any


class TradeAnalyzer:
    """Analyze a single closed trade and produce a verdict dict."""

    async def analyze(
        self,
        trade: dict,
        signal: dict | None,
        snapshots: list[dict],
    ) -> dict:
        """Generate complete trade analysis with verdict.

        Args:
            trade: row from trade_outcomes table
            signal: matched row from signal_evaluations (or None)
            snapshots: rows from trade_snapshots ordered by timestamp

        Returns:
            dict matching trade_verdicts table columns
        """
        net_pnl = float(trade.get("net_pnl") or 0)
        gross_pnl = float(trade.get("gross_pnl") or 0)
        fee = float(trade.get("fee") or 0)
        mfe_pct = float(trade.get("mfe_pct") or 0)
        mae_pct = float(trade.get("mae_pct") or 0)
        notional = float(trade.get("notional") or 0)
        hold_seconds = int(trade.get("hold_seconds") or 0)
        exit_reason = trade.get("exit_reason") or ""
        entry_quality_label = trade.get("entry_quality_label") or (signal.get("entry_quality_label") if signal else None) or ""
        late_entry_risk = trade.get("late_entry_risk") or (signal.get("late_entry_risk") if signal else None) or ""

        # ── SL analysis from snapshots ──
        sl_analysis = self._analyze_sl(trade, snapshots)
        sl_moves = sl_analysis["sl_moves_count"]
        time_in_profit_pct = sl_analysis["time_in_profit_pct"]

        # ── MFE capture ──
        mfe_capture_pct = self._calc_mfe_capture(net_pnl, mfe_pct, notional)

        # ── Fee killed ──
        fee_killed = gross_pnl > 0 and net_pnl <= 0

        # ── Verdict ──
        verdict, verdict_reason = self._classify_verdict(
            net_pnl, mfe_pct, mfe_capture_pct, fee
        )

        # ── Entry timing ──
        entry_timing = self._classify_entry_timing(
            late_entry_risk, entry_quality_label, mfe_pct, mae_pct, trade
        )

        # ── Improvements ──
        improvements = self._generate_improvements(
            net_pnl, gross_pnl, mfe_pct, mae_pct, fee_killed,
            sl_moves, hold_seconds, exit_reason, late_entry_risk,
            time_in_profit_pct,
        )

        # ── Counterfactual ──
        counterfactual = self._generate_counterfactual(
            trade, mae_pct, mfe_pct, sl_analysis["sl_moved"], exit_reason
        )

        # ── Build result dict ──
        return {
            "trade_outcome_id": trade.get("id"),
            "coin": trade.get("coin"),
            "side": trade.get("side"),
            "mode": trade.get("mode"),
            "entry_time": trade.get("entry_time"),
            "exit_time": trade.get("exit_time"),
            # Summary
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            # Entry analysis
            "entry_score": float(trade.get("signal_score") or 0),
            "entry_quality": entry_quality_label or None,
            "entry_timing": entry_timing,
            "trend_aligned": self._check_trend_aligned(trade, signal),
            # Execution analysis
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "mfe_capture_pct": round(mfe_capture_pct, 2) if mfe_capture_pct is not None else None,
            "time_in_profit_pct": round(time_in_profit_pct, 2),
            "sl_moved": sl_analysis["sl_moved"],
            "sl_moves_count": sl_moves,
            "max_sl_distance_pct": sl_analysis["max_sl_distance_pct"],
            "min_sl_distance_pct": sl_analysis["min_sl_distance_pct"],
            # Result
            "gross_pnl": gross_pnl,
            "fee": fee,
            "net_pnl": net_pnl,
            "fee_killed": fee_killed,
            "exit_reason": exit_reason,
            # Suggestions
            "improvements": improvements,
            "counterfactual": counterfactual,
        }

    # ── Verdict classification ─────────────────────────────────────────

    def _classify_verdict(
        self,
        net_pnl: float,
        mfe_pct: float,
        mfe_capture_pct: float | None,
        fee: float,
    ) -> tuple[str, str]:
        """Classify trade into GOOD / ACCEPTABLE / BAD / TERRIBLE."""
        if net_pnl > 0 and mfe_capture_pct is not None and mfe_capture_pct > 40:
            return "GOOD", f"Profit con {mfe_capture_pct:.0f}% de MFE capturado"
        if net_pnl > 0:
            return "ACCEPTABLE", "Profit pero capturo poco del MFE disponible"
        if net_pnl > -abs(fee) and mfe_pct > 0.1:
            return "ACCEPTABLE", f"Perdida minima ({net_pnl:.4f}) con MFE de {mfe_pct:.2f}%"
        if net_pnl < 0 and mfe_pct > 0.15:
            return "BAD", f"Perdida con MFE de {mfe_pct:.2f}% — tuvo oportunidad pero la perdio"
        if net_pnl < 0 and mfe_pct < 0.05:
            return "TERRIBLE", "Perdida sin oportunidad — el precio nunca se movio a favor"
        # Fallback: loss with low MFE
        if net_pnl < 0:
            return "BAD", f"Perdida con MFE bajo ({mfe_pct:.2f}%)"
        return "ACCEPTABLE", "Resultado neutro"

    # ── Entry timing ───────────────────────────────────────────────────

    def _classify_entry_timing(
        self,
        late_entry_risk: str,
        entry_quality_label: str,
        mfe_pct: float,
        mae_pct: float,
        trade: dict,
    ) -> str:
        """Classify entry as OPTIMAL / ACCEPTABLE / LATE / TOO_EARLY."""
        quality_upper = entry_quality_label.upper() if entry_quality_label else ""
        risk_upper = late_entry_risk.upper() if late_entry_risk else ""

        if risk_upper == "LOW" and quality_upper in ("A", "A_PLUS"):
            return "OPTIMAL"
        if risk_upper == "HIGH":
            return "LATE"

        # TOO_EARLY: MFE tiny and MAE > half the SL distance
        config_snap = trade.get("config_snapshot") or {}
        sl_pct = config_snap.get("sl_pct") or config_snap.get("stop_loss_pct") or 0.3
        if mfe_pct < 0.03 and mae_pct > sl_pct * 0.5:
            return "TOO_EARLY"

        if risk_upper == "MEDIUM" or quality_upper == "B":
            return "ACCEPTABLE"

        return "ACCEPTABLE"

    # ── SL analysis ────────────────────────────────────────────────────

    def _analyze_sl(self, trade: dict, snapshots: list[dict]) -> dict:
        """Analyze SL movement from snapshots."""
        result: dict[str, Any] = {
            "sl_moved": False,
            "sl_moves_count": 0,
            "max_sl_distance_pct": None,
            "min_sl_distance_pct": None,
            "time_in_profit_pct": 0.0,
        }
        if not snapshots:
            return result

        entry_price = float(trade.get("entry_price") or 0)
        side = (trade.get("side") or "").upper()
        if entry_price <= 0:
            return result

        sl_prices: list[float] = []
        profit_count = 0
        total_count = len(snapshots)

        prev_sl: float | None = None
        sl_moves = 0

        for snap in snapshots:
            sl = snap.get("sl_price")
            pnl_pct = snap.get("pnl_pct") or 0
            if pnl_pct > 0:
                profit_count += 1

            if sl is not None:
                sl_f = float(sl)
                sl_prices.append(sl_f)
                if prev_sl is not None and abs(sl_f - prev_sl) > 1e-10:
                    sl_moves += 1
                prev_sl = sl_f

        result["time_in_profit_pct"] = (profit_count / total_count * 100) if total_count > 0 else 0.0
        result["sl_moves_count"] = sl_moves
        result["sl_moved"] = sl_moves > 0

        if sl_prices and entry_price > 0:
            distances = []
            for sl_p in sl_prices:
                if side == "LONG":
                    dist = (entry_price - sl_p) / entry_price * 100
                else:
                    dist = (sl_p - entry_price) / entry_price * 100
                distances.append(dist)
            result["max_sl_distance_pct"] = round(max(distances), 4) if distances else None
            result["min_sl_distance_pct"] = round(min(distances), 4) if distances else None

        return result

    # ── MFE capture ────────────────────────────────────────────────────

    def _calc_mfe_capture(
        self, net_pnl: float, mfe_pct: float, notional: float
    ) -> float | None:
        """Calculate what % of MFE was captured as net PnL."""
        if mfe_pct <= 0 or notional <= 0:
            return None
        mfe_usd = mfe_pct / 100 * notional
        if mfe_usd == 0:
            return None
        return net_pnl / mfe_usd * 100

    # ── Trend alignment ────────────────────────────────────────────────

    def _check_trend_aligned(self, trade: dict, signal: dict | None) -> bool | None:
        """Check if entry was aligned with trend."""
        trend_score = trade.get("trend_score")
        if trend_score is None and signal:
            trend_score = signal.get("trend_score")
        if trend_score is None:
            return None
        side = (trade.get("side") or "").upper()
        if side == "LONG":
            return float(trend_score) > 0
        elif side == "SHORT":
            return float(trend_score) < 0
        return None

    # ── Improvements ───────────────────────────────────────────────────

    def _generate_improvements(
        self,
        net_pnl: float,
        gross_pnl: float,
        mfe_pct: float,
        mae_pct: float,
        fee_killed: bool,
        sl_moves: int,
        hold_seconds: int,
        exit_reason: str,
        late_entry_risk: str,
        time_in_profit_pct: float,
    ) -> list[str]:
        """Generate concrete improvement suggestions based on trade data."""
        suggestions: list[str] = []

        if fee_killed:
            suggestions.append(
                "SL floor deberia cubrir fees — el trade gano bruto pero las fees lo mataron"
            )

        if mfe_pct > 0.3 and net_pnl < 0:
            suggestions.append(
                f"El trade tuvo MFE de {mfe_pct:.2f}% pero se perdio — "
                "trailing demasiado agresivo o SL demasiado cerca"
            )

        if mfe_pct < 0.05:
            suggestions.append(
                "Entrada sin momentum real — el precio nunca se movio a favor"
            )

        if sl_moves > 3 and net_pnl < 0:
            suggestions.append(
                f"El SL se movio {sl_moves} veces pero el trade acabo en perdida — "
                "trailing puede estar apretando demasiado"
            )

        if hold_seconds < 60 and exit_reason.upper() == "SL":
            suggestions.append(
                "Trade cerro en SL en menos de 1 min — posible falsa ruptura"
            )

        if late_entry_risk.upper() == "HIGH" and net_pnl < 0:
            suggestions.append(
                "Entrada tardia (HIGH risk) — entro cuando el movimiento ya estaba avanzado"
            )

        if time_in_profit_pct > 70 and net_pnl < 0:
            suggestions.append(
                f"Estuvo en profit el {time_in_profit_pct:.0f}% del tiempo pero cerro en perdida — "
                "trailing/SL perdio la oportunidad"
            )

        return suggestions

    # ── Counterfactual ─────────────────────────────────────────────────

    def _generate_counterfactual(
        self,
        trade: dict,
        mae_pct: float,
        mfe_pct: float,
        sl_moved: bool,
        exit_reason: str,
    ) -> dict[str, Any]:
        """Generate what-if scenarios."""
        config_snap = trade.get("config_snapshot") or {}
        sl_pct = config_snap.get("sl_pct") or config_snap.get("stop_loss_pct") or 0.3
        tp_pct = config_snap.get("tp_pct") or config_snap.get("take_profit_pct") or 0.5

        scenarios: dict[str, Any] = {}

        # Wider SL: would it have survived?
        wider_sl = sl_pct * 1.5
        survived = mae_pct < wider_sl
        scenarios["wider_sl"] = {
            "description": "Con SL 50% mas ancho, habria sobrevivido?",
            "original_sl_pct": round(sl_pct, 3),
            "wider_sl_pct": round(wider_sl, 3),
            "mae_pct": round(mae_pct, 4),
            "survived": survived,
        }

        # Tighter TP: would it have closed in profit?
        tighter_tp = tp_pct * 0.5
        would_tp = mfe_pct > tighter_tp
        scenarios["tighter_tp"] = {
            "description": "Con TP 50% mas corto, habria cerrado en profit?",
            "original_tp_pct": round(tp_pct, 3),
            "tighter_tp_pct": round(tighter_tp, 3),
            "mfe_pct": round(mfe_pct, 4),
            "would_have_tp": would_tp,
        }

        # No trailing: would it have closed better?
        if sl_moved and exit_reason.upper() == "SL":
            scenarios["no_trailing"] = {
                "description": "Sin trailing, habria cerrado mejor?",
                "sl_moved": True,
                "exit_was_sl": True,
                "likely_better": True,
                "note": "El trailing movio el SL y luego el precio lo toco — sin trailing podria haber aguantado",
            }

        return scenarios
