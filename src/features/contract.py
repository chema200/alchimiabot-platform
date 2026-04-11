"""Feature Contract: formal definition of every feature in the platform.

Ensures the SAME feature is computed identically in:
  - live (FeatureStore)
  - replay (ReplayEngine → FeatureStore)
  - training (dataset generation)

Each feature has: name, version, group, window, source, dtype, description.
The contract is the single source of truth for what a feature means.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class FeatureSource(str, Enum):
    TRADES = "trades"
    BOOK = "book"
    COMPUTED = "computed"  # derived from other features
    TEMPORAL = "temporal"  # clock-based


class FeatureDtype(str, Enum):
    FLOAT = "float"
    INT = "int"
    BOOL = "bool"


@dataclass(frozen=True)
class FeatureDefinition:
    """Immutable definition of a single feature."""
    name: str
    group: str          # momentum, volatility, trend, microstructure, temporal
    version: str        # v1, v2, etc.
    source: FeatureSource
    window_sec: int     # time window used (0 = point-in-time)
    dtype: FeatureDtype = FeatureDtype.FLOAT
    description: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.group}.{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "version": self.version,
            "source": self.source.value,
            "window_sec": self.window_sec,
            "dtype": self.dtype.value,
            "description": self.description,
        }


# ── THE CONTRACT ────────────────────────────────────────────────────────
# Every feature computed by the platform MUST be listed here.
# If it's not here, it doesn't exist.

FEATURE_CONTRACT: list[FeatureDefinition] = [
    # ── Momentum ──
    FeatureDefinition("mom_ret_1m", "momentum", "v1", FeatureSource.TRADES, 60, description="Price return over 1 min"),
    FeatureDefinition("mom_ret_2m", "momentum", "v1", FeatureSource.TRADES, 120, description="Price return over 2 min"),
    FeatureDefinition("mom_ret_5m", "momentum", "v1", FeatureSource.TRADES, 300, description="Price return over 5 min"),
    FeatureDefinition("mom_ret_10m", "momentum", "v1", FeatureSource.TRADES, 600, description="Price return over 10 min"),
    FeatureDefinition("mom_ret_30m", "momentum", "v1", FeatureSource.TRADES, 1800, description="Price return over 30 min"),
    FeatureDefinition("mom_vwap_ret_1m", "momentum", "v1", FeatureSource.TRADES, 60, description="VWAP return over 1 min"),
    FeatureDefinition("mom_vwap_ret_2m", "momentum", "v1", FeatureSource.TRADES, 120, description="VWAP return over 2 min"),
    FeatureDefinition("mom_vwap_ret_5m", "momentum", "v1", FeatureSource.TRADES, 300, description="VWAP return over 5 min"),
    FeatureDefinition("mom_vwap_ret_10m", "momentum", "v1", FeatureSource.TRADES, 600, description="VWAP return over 10 min"),
    FeatureDefinition("mom_vwap_ret_30m", "momentum", "v1", FeatureSource.TRADES, 1800, description="VWAP return over 30 min"),
    FeatureDefinition("mom_trades_1m", "momentum", "v1", FeatureSource.TRADES, 60, FeatureDtype.INT, "Trade count in 1 min"),
    FeatureDefinition("mom_trades_2m", "momentum", "v1", FeatureSource.TRADES, 120, FeatureDtype.INT, "Trade count in 2 min"),
    FeatureDefinition("mom_trades_5m", "momentum", "v1", FeatureSource.TRADES, 300, FeatureDtype.INT, "Trade count in 5 min"),
    FeatureDefinition("mom_trades_10m", "momentum", "v1", FeatureSource.TRADES, 600, FeatureDtype.INT, "Trade count in 10 min"),
    FeatureDefinition("mom_trades_30m", "momentum", "v1", FeatureSource.TRADES, 1800, FeatureDtype.INT, "Trade count in 30 min"),
    FeatureDefinition("mom_acceleration", "momentum", "v1", FeatureSource.COMPUTED, 300, description="2m ret - 5m ret scaled"),
    FeatureDefinition("mom_buy_pressure", "momentum", "v1", FeatureSource.TRADES, 120, description="Buy volume / total volume in 2 min"),

    # ── Volatility ──
    FeatureDefinition("vol_realized_1m", "volatility", "v1", FeatureSource.TRADES, 60, description="Realized vol (std log returns) 1 min"),
    FeatureDefinition("vol_realized_5m", "volatility", "v1", FeatureSource.TRADES, 300, description="Realized vol 5 min"),
    FeatureDefinition("vol_realized_30m", "volatility", "v1", FeatureSource.TRADES, 1800, description="Realized vol 30 min"),
    FeatureDefinition("vol_range_1m", "volatility", "v1", FeatureSource.TRADES, 60, description="High-low range 1 min (%)"),
    FeatureDefinition("vol_range_5m", "volatility", "v1", FeatureSource.TRADES, 300, description="High-low range 5 min (%)"),
    FeatureDefinition("vol_atr_5m", "volatility", "v1", FeatureSource.TRADES, 300, description="ATR proxy over 5 min"),
    FeatureDefinition("vol_ratio_1m_5m", "volatility", "v1", FeatureSource.COMPUTED, 300, description="Short vol / long vol ratio"),
    FeatureDefinition("vol_parkinson", "volatility", "v1", FeatureSource.TRADES, 300, description="Parkinson volatility estimator"),

    # ── Trend ──
    FeatureDefinition("trend_ema_cross", "trend", "v1", FeatureSource.TRADES, 0, description="Fast EMA vs slow EMA (%)"),
    FeatureDefinition("trend_slope_2m", "trend", "v1", FeatureSource.TRADES, 120, description="Linear regression slope 2 min"),
    FeatureDefinition("trend_slope_5m", "trend", "v1", FeatureSource.TRADES, 300, description="Linear regression slope 5 min"),
    FeatureDefinition("trend_r2_5m", "trend", "v1", FeatureSource.TRADES, 300, description="R-squared of 5 min regression"),
    FeatureDefinition("trend_consistency", "trend", "v1", FeatureSource.TRADES, 300, description="% of intervals in dominant direction"),
    FeatureDefinition("trend_higher_highs", "trend", "v1", FeatureSource.TRADES, 300, FeatureDtype.INT, "Count of higher highs in 5 min"),
    FeatureDefinition("trend_strength", "trend", "v1", FeatureSource.COMPUTED, 300, description="Composite trend score [-1, 1]"),

    # ── Microstructure ──
    FeatureDefinition("micro_spread_bps", "microstructure", "v1", FeatureSource.BOOK, 0, description="Current bid-ask spread in bps"),
    FeatureDefinition("micro_bid_depth", "microstructure", "v1", FeatureSource.BOOK, 0, description="Total bid size top 5 levels"),
    FeatureDefinition("micro_ask_depth", "microstructure", "v1", FeatureSource.BOOK, 0, description="Total ask size top 5 levels"),
    FeatureDefinition("micro_imbalance", "microstructure", "v1", FeatureSource.BOOK, 0, description="(bid-ask)/total depth [-1,1]"),
    FeatureDefinition("micro_depth_ratio", "microstructure", "v1", FeatureSource.BOOK, 0, description="bid_depth / ask_depth"),
    FeatureDefinition("micro_trade_imbalance", "microstructure", "v1", FeatureSource.TRADES, 60, description="Net buy vol / total vol 1 min"),
    FeatureDefinition("micro_large_trade_ratio", "microstructure", "v1", FeatureSource.TRADES, 60, description="% volume from trades > 2x median"),
    FeatureDefinition("micro_intensity", "microstructure", "v1", FeatureSource.TRADES, 60, description="Trades per second 1 min"),
    FeatureDefinition("micro_vpin", "microstructure", "v1", FeatureSource.TRADES, 60, description="Volume-sync PIN toxicity proxy"),

    # ── Temporal ──
    FeatureDefinition("temp_hour_sin", "temporal", "v1", FeatureSource.TEMPORAL, 0, description="Cyclical hour sin encoding"),
    FeatureDefinition("temp_hour_cos", "temporal", "v1", FeatureSource.TEMPORAL, 0, description="Cyclical hour cos encoding"),
    FeatureDefinition("temp_dow_sin", "temporal", "v1", FeatureSource.TEMPORAL, 0, description="Cyclical day-of-week sin"),
    FeatureDefinition("temp_dow_cos", "temporal", "v1", FeatureSource.TEMPORAL, 0, description="Cyclical day-of-week cos"),
    FeatureDefinition("temp_is_weekend", "temporal", "v1", FeatureSource.TEMPORAL, 0, FeatureDtype.BOOL, "1 if Saturday or Sunday"),
    FeatureDefinition("temp_session", "temporal", "v1", FeatureSource.TEMPORAL, 0, FeatureDtype.INT, "0=Asia 1=Europe 2=US 3=overlap"),
    FeatureDefinition("temp_minutes_to_close", "temporal", "v1", FeatureSource.TEMPORAL, 0, FeatureDtype.INT, "Minutes to next 4h candle close"),
]

# Build lookup dict
FEATURE_BY_NAME: dict[str, FeatureDefinition] = {f.name: f for f in FEATURE_CONTRACT}
FEATURE_NAMES: list[str] = [f.name for f in FEATURE_CONTRACT]
FEATURE_VERSION = "+".join(sorted(set(f"{f.group}:{f.version}" for f in FEATURE_CONTRACT)))


def validate_snapshot(features: dict[str, float]) -> list[str]:
    """Check a feature snapshot against the contract. Returns list of errors."""
    errors = []
    for f in FEATURE_CONTRACT:
        if f.name not in features:
            errors.append(f"missing: {f.name}")
    for name in features:
        if name not in FEATURE_BY_NAME:
            errors.append(f"unknown: {name}")
    return errors


def get_contract_summary() -> dict:
    """Summary of the feature contract."""
    groups = {}
    for f in FEATURE_CONTRACT:
        groups.setdefault(f.group, []).append(f.name)
    return {
        "total_features": len(FEATURE_CONTRACT),
        "version": FEATURE_VERSION,
        "groups": {g: len(names) for g, names in groups.items()},
        "features": [f.to_dict() for f in FEATURE_CONTRACT],
    }
