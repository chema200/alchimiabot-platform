"""SQLAlchemy models — operational data for the platform.

Raw data goes to Parquet. This is for structured, queryable state:
trade outcomes, signal evaluations, feature snapshots, experiments, etc.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, BigInteger, String, Float, DateTime, Integer, Text, Boolean, JSON, Index, ForeignKey
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ── Trade Outcomes ──────────────────────────────────────────────────────

class TradeOutcome(Base):
    """Every completed trade with full context — the core analytical table."""
    __tablename__ = "trade_outcomes"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    size = Column(Float, nullable=False)
    notional = Column(Float)
    leverage = Column(Integer, default=3)
    gross_pnl = Column(Float, default=0)
    fee = Column(Float, default=0)
    net_pnl = Column(Float, default=0)
    entry_tag = Column(String(50))
    exit_reason = Column(String(50))
    mode = Column(String(20))
    hold_seconds = Column(Integer)
    # Context at entry
    regime = Column(String(30))
    trend_score = Column(Float)
    micro_score = Column(Float)
    signal_score = Column(Float)
    ml_score = Column(Float)
    momentum_score = Column(Float)
    # Config tracking
    score_min_applied = Column(Float)
    config_version = Column(String(100))
    # Performance tracking
    mfe_pct = Column(Float)  # max favorable excursion
    mae_pct = Column(Float)  # max adverse excursion
    high_water_mark = Column(Float)
    # Features snapshot at entry (JSON blob for research)
    entry_features = Column(JSON)
    # Config snapshot at entry
    config_snapshot = Column(JSON)
    # Timestamps
    # Entry quality diagnostics (observability only)
    entry_quality_label = Column(String(20))
    late_entry_risk = Column(String(20))
    # Timestamps
    entry_time = Column(DateTime(timezone=True), nullable=False, index=True)
    exit_time = Column(DateTime(timezone=True), index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_trade_coin_entry", "coin", "entry_time"),
        Index("idx_trade_regime", "regime"),
        Index("idx_trade_mode", "mode"),
    )


# ── Trade Snapshots ────────────────────────────────────────────────────

class TradeSnapshot(Base):
    """Periodic position state snapshots (~30s) while a trade is open."""
    __tablename__ = "trade_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_id = Column(String(100), nullable=False)
    coin = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    mid_price = Column(Float)
    sl_price = Column(Float)
    tp_price = Column(Float)
    high_water_mark = Column(Float)
    entry_price = Column(Float)
    gross_pnl = Column(Float)
    pnl_pct = Column(Float)
    hold_seconds = Column(Integer)
    partial_closed = Column(Boolean, default=False)
    mfe_pct = Column(Float, default=0)
    mae_pct = Column(Float, default=0)

    __table_args__ = (
        Index("idx_trade_snapshots_trade_id", "trade_id"),
        Index("idx_trade_snapshots_timestamp", "timestamp"),
    )


# ── Trade Verdicts ─────────────────────────────────────────────────────

class TradeVerdict(Base):
    """Auto-generated analysis when a trade closes."""
    __tablename__ = "trade_verdicts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    trade_outcome_id = Column(BigInteger, ForeignKey("trade_outcomes.id"))
    coin = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    mode = Column(String(20))
    entry_time = Column(DateTime(timezone=True))
    exit_time = Column(DateTime(timezone=True))
    # Summary
    verdict = Column(String(20), nullable=False)  # GOOD, ACCEPTABLE, BAD, TERRIBLE
    verdict_reason = Column(Text)
    # Entry analysis
    entry_score = Column(Float)
    entry_quality = Column(String(10))
    entry_timing = Column(String(50))  # OPTIMAL, ACCEPTABLE, LATE, TOO_EARLY
    trend_aligned = Column(Boolean)
    # Execution analysis
    mfe_pct = Column(Float)
    mae_pct = Column(Float)
    mfe_capture_pct = Column(Float)  # how much of MFE was captured
    time_in_profit_pct = Column(Float)  # % of hold time in green
    sl_moved = Column(Boolean, default=False)  # did trailing move the SL?
    sl_moves_count = Column(Integer, default=0)
    max_sl_distance_pct = Column(Float)  # furthest SL was from entry
    min_sl_distance_pct = Column(Float)  # closest SL got
    # Result
    gross_pnl = Column(Float)
    fee = Column(Float)
    net_pnl = Column(Float)
    fee_killed = Column(Boolean, default=False)  # gross positive but net negative
    exit_reason = Column(String(50))
    # What could improve
    improvements = Column(JSON)  # array of suggestions
    counterfactual = Column(JSON)  # what-if scenarios
    # Meta
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_trade_verdicts_trade_outcome_id", "trade_outcome_id"),
        Index("idx_trade_verdicts_coin", "coin"),
    )


# ── Signal Evaluations ──────────────────────────────────────────────────

class SignalEvaluation(Base):
    """Every signal evaluated — taken or skipped. Critical for research."""
    __tablename__ = "signal_evaluations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    # Scores
    signal_score = Column(Float)
    trend_score = Column(Float)
    micro_score = Column(Float)
    momentum_score = Column(Float)
    ml_score = Column(Float)
    # Context
    regime = Column(String(30))
    mode = Column(String(20))
    price = Column(Float)
    # Decision
    action = Column(String(20), nullable=False)  # ENTER, SKIP, BLOCKED
    reason = Column(Text)
    # Full feature snapshot
    features = Column(JSON)
    # Config tracking
    score_min_applied = Column(Float)
    config_version = Column(String(100))
    # Config snapshot at signal time
    config_snapshot = Column(JSON)
    # Decision stage observability
    decision_stage = Column(String(30))  # PRE_CANDIDATE_REJECT, BLOCKED_POST_CANDIDATE, ENTER
    decision_trace = Column(JSON)
    diagnostic_trace = Column(JSON)
    # Entry quality diagnostics (observability only)
    entry_diagnostics = Column(JSON)
    entry_quality_label = Column(String(20))
    late_entry_risk = Column(String(20))
    # Link to trade if action=ENTER
    trade_outcome_id = Column(BigInteger)

    __table_args__ = (
        Index("idx_signal_coin_time", "coin", "timestamp"),
        Index("idx_signal_action", "action"),
    )


# ── Coin Profiles ───────────────────────────────────────────────────────

class CoinProfile(Base):
    """Per-coin performance profile — aggregated stats."""
    __tablename__ = "coin_profiles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    total_pnl = Column(Float, default=0)
    avg_pnl = Column(Float, default=0)
    win_rate = Column(Float, default=0)
    avg_hold_sec = Column(Integer, default=0)
    best_regime = Column(String(30))
    worst_regime = Column(String(30))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── Feature Snapshots ───────────────────────────────────────────────────

class FeatureSnapshotRecord(Base):
    """Persisted feature snapshots for offline analysis."""
    __tablename__ = "feature_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    version = Column(String(100))
    features = Column(JSON, nullable=False)

    __table_args__ = (
        Index("idx_feat_coin_time", "coin", "timestamp"),
    )


# ── Regime Labels ───────────────────────────────────────────────────────

class RegimeLabel(Base):
    """Historical regime classifications for each coin."""
    __tablename__ = "regime_labels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    regime = Column(String(30), nullable=False)
    confidence = Column(Float)
    trend_strength = Column(Float)
    volatility_level = Column(Float)
    details = Column(JSON)

    __table_args__ = (
        Index("idx_regime_coin_time", "coin", "timestamp"),
    )


# ── Dataset Registry ────────────────────────────────────────────────────

class DatasetRecord(Base):
    """Registry of generated datasets for ML training."""
    __tablename__ = "dataset_registry"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text)
    path = Column(String(500), nullable=False)  # file path to parquet/csv
    row_count = Column(Integer)
    feature_version = Column(String(100))
    label_type = Column(String(50))  # win_loss, pnl, expectancy
    date_from = Column(DateTime(timezone=True))
    date_to = Column(DateTime(timezone=True))
    coins = Column(JSON)  # list of coins included
    params = Column(JSON)  # generation parameters
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── Model Registry ──────────────────────────────────────────────────────

class ModelRecord(Base):
    """Registry of trained ML models."""
    __tablename__ = "model_registry"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    version = Column(String(50), nullable=False)
    model_type = Column(String(50))  # lightgbm, xgboost, etc.
    dataset_id = Column(BigInteger)  # FK to dataset_registry
    path = Column(String(500))  # file path to model artifact
    feature_version = Column(String(100))
    # Performance metrics
    metrics = Column(JSON)  # accuracy, auc, sharpe, etc.
    # Status
    status = Column(String(20), default="trained")  # trained, validated, promoted, retired
    promoted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    notes = Column(Text)

    __table_args__ = (
        Index("idx_model_name_version", "name", "version"),
    )


# ── Replay Runs ─────────────────────────────────────────────────────────

class ReplayRun(Base):
    """Record of each replay/backtest execution."""
    __tablename__ = "replay_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(200))
    run_type = Column(String(30))  # replay, backtest, walkforward
    date_from = Column(DateTime(timezone=True))
    date_to = Column(DateTime(timezone=True))
    coins = Column(JSON)
    params = Column(JSON)
    # Results
    total_trades = Column(Integer)
    net_pnl = Column(Float)
    win_rate = Column(Float)
    sharpe = Column(Float)
    max_drawdown = Column(Float)
    results = Column(JSON)
    # Meta
    events_processed = Column(Integer)
    elapsed_sec = Column(Float)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── Experiment Runs ─────────────────────────────────────────────────────

class ExperimentRun(Base):
    """Experiment tracking — hypothesis, params, results comparison."""
    __tablename__ = "experiment_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    hypothesis = Column(Text)
    params = Column(JSON)
    baseline_params = Column(JSON)
    status = Column(String(20), default="created")  # created, running, completed, promoted, rejected
    results = Column(JSON)
    baseline_results = Column(JSON)
    promoted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))
    notes = Column(Text)


# ── Audit System ────────────────────────────────────────────────────────

class AuditRun(Base):
    """Record of each audit check execution."""
    __tablename__ = "audit_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    audit_type = Column(String(50), nullable=False, index=True)  # integration, data_quality, storage, consistency
    status = Column(String(20), nullable=False)  # OK, WARNING, ERROR
    score = Column(Integer, default=100)  # 0-100 health score
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True))
    summary = Column(Text)
    details = Column(JSON)
    metrics = Column(JSON)

    __table_args__ = (
        Index("idx_audit_type_time", "audit_type", "started_at"),
    )


class ChangeMarker(Base):
    """Change markers — track changes and measure their impact before/after."""
    __tablename__ = "change_markers"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    category = Column(String(30), nullable=False)  # PRESET, PROFILE, OPTIMIZER, SHADOW, MANUAL, EVENT, MODE_CHANGE
    label = Column(String(200), nullable=False)
    description = Column(Text)
    source = Column(String(20), nullable=False)  # USER, OPTIMIZER, CRON, SYSTEM
    coin = Column(String(20))
    side = Column(String(10))
    mode = Column(String(20))
    parameter = Column(String(50))
    old_value = Column(Float)
    new_value = Column(Float)
    batch_id = Column(String(50))
    batch_label = Column(String(200))
    config_snapshot = Column(JSON)
    impact_status = Column(String(20), default="PENDING")  # PENDING, IMPROVED, NEUTRAL, WORSENED, INSUFFICIENT_DATA
    impact_data = Column(JSON)
    impact_calculated_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_markers_timestamp", "timestamp"),
        Index("idx_markers_category", "category"),
        Index("idx_markers_coin_side_mode", "coin", "side", "mode"),
        Index("idx_markers_batch", "batch_id"),
    )


class AuditFinding(Base):
    """Individual finding from an audit run."""
    __tablename__ = "audit_findings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    audit_run_id = Column(BigInteger, nullable=False, index=True)
    severity = Column(String(20), nullable=False)  # info, warning, error, critical
    code = Column(String(50), nullable=False)  # e.g., TRADE_COUNT_MISMATCH, NET_GT_GROSS
    message = Column(Text, nullable=False)
    entity_type = Column(String(30))  # trade, signal, snapshot, parquet
    entity_id = Column(String(100))
    payload = Column(JSON)
