"""Base feature interface and feature snapshot.

Every feature computer implements compute() which takes recent events
and returns a dict of named feature values. Features are versioned
so historical recomputation is reproducible.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import time


@dataclass(slots=True)
class FeatureSnapshot:
    """Point-in-time feature vector for a single coin."""
    coin: str
    timestamp_ms: int
    features: dict[str, float] = field(default_factory=dict)
    version: str = "v1"

    def get(self, name: str, default: float = 0.0) -> float:
        return self.features.get(name, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "coin": self.coin,
            "timestamp_ms": self.timestamp_ms,
            "version": self.version,
            **self.features,
        }


class FeatureComputer(ABC):
    """Base class for all feature computers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this feature group (e.g., 'momentum', 'volatility')."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """Version string for reproducibility."""
        ...

    @property
    @abstractmethod
    def feature_names(self) -> list[str]:
        """List of feature names this computer produces."""
        ...

    @abstractmethod
    def compute(self, coin: str, trades: list[dict], books: list[dict]) -> dict[str, float]:
        """Compute features from recent trades and book snapshots.

        Args:
            coin: Coin symbol
            trades: Recent trade events as dicts (price, size, side, timestamp_ms)
            books: Recent L2 book snapshots as dicts (bids, asks, timestamp_ms)

        Returns:
            Dict of feature_name -> value
        """
        ...
