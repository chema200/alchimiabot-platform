"""Temporal features: hour of day, day of week, session context.

Markets behave differently by time — Asian session vs US session,
weekends vs weekdays, market open/close effects.
"""

import math
from datetime import datetime, timezone
from ..base import FeatureComputer


class TemporalFeatures(FeatureComputer):
    name = "temporal"
    version = "v1"

    @property
    def feature_names(self) -> list[str]:
        return [
            "temp_hour_sin",        # cyclical hour encoding (sin)
            "temp_hour_cos",        # cyclical hour encoding (cos)
            "temp_dow_sin",         # cyclical day-of-week encoding (sin)
            "temp_dow_cos",         # cyclical day-of-week encoding (cos)
            "temp_is_weekend",      # 1 if Saturday or Sunday
            "temp_session",         # 0=Asia, 1=Europe, 2=US, 3=US+Asia overlap
            "temp_minutes_to_close",  # minutes until next 4-hour candle close
        ]

    def compute(self, coin: str, trades: list[dict], books: list[dict]) -> dict[str, float]:
        now = datetime.now(timezone.utc)
        hour = now.hour + now.minute / 60.0
        dow = now.weekday()  # 0=Monday

        features = {}

        # Cyclical encodings (ML-friendly: no discontinuity at midnight/week boundary)
        features["temp_hour_sin"] = round(math.sin(2 * math.pi * hour / 24), 4)
        features["temp_hour_cos"] = round(math.cos(2 * math.pi * hour / 24), 4)
        features["temp_dow_sin"] = round(math.sin(2 * math.pi * dow / 7), 4)
        features["temp_dow_cos"] = round(math.cos(2 * math.pi * dow / 7), 4)

        features["temp_is_weekend"] = 1.0 if dow >= 5 else 0.0

        # Trading session (UTC)
        if 0 <= now.hour < 8:
            features["temp_session"] = 0.0   # Asia
        elif 8 <= now.hour < 14:
            features["temp_session"] = 1.0   # Europe
        elif 14 <= now.hour < 21:
            features["temp_session"] = 2.0   # US
        else:
            features["temp_session"] = 3.0   # US+Asia overlap

        # Minutes to next 4h candle close (0, 4, 8, 12, 16, 20 UTC)
        next_4h = (now.hour // 4 + 1) * 4
        if next_4h >= 24:
            next_4h = 0
        minutes_to_close = ((next_4h - now.hour) % 24) * 60 - now.minute
        if minutes_to_close <= 0:
            minutes_to_close += 240
        features["temp_minutes_to_close"] = float(minutes_to_close)

        return features
