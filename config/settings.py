"""Platform configuration — structured by domain, no insecure defaults."""

from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr


class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5433
    name: str = "agentbot_platform"
    user: str = "platform"
    password: SecretStr = SecretStr("")  # must be set via env

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}@{self.host}:{self.port}/{self.name}"

    @property
    def sync_url(self) -> str:
        return f"postgresql+psycopg://{self.user}:{self.password.get_secret_value()}@{self.host}:{self.port}/{self.name}"

    model_config = {"env_prefix": "DB_", "extra": "ignore"}


class HyperliquidSettings(BaseSettings):
    ws_url: str = "wss://api.hyperliquid.xyz/ws"
    rest_url: str = "https://api.hyperliquid.xyz"
    address: str = ""  # must be set via env
    stale_timeout_sec: int = 30  # mark stream stale after N seconds without data
    health_check_interval_sec: int = 10

    model_config = {"env_prefix": "HL_", "extra": "ignore"}


class BinanceSettings(BaseSettings):
    ws_url: str = "wss://fstream.binance.com/ws"
    rest_url: str = "https://fapi.binance.com"
    enabled: bool = True
    max_coins: int = 15  # limit coins for context data

    model_config = {"env_prefix": "BINANCE_", "extra": "ignore"}


class ParquetSettings(BaseSettings):
    """Raw data lake configuration."""
    base_dir: str = "data/raw"
    flush_interval_sec: int = 60
    flush_size: int = 5000
    # Partitioning: {base_dir}/{event_type}/{coin}/{date}/{hour}.parquet
    partition_by_hour: bool = True
    compression: str = "snappy"
    # Retention
    retention_days: int = 90
    compaction_enabled: bool = False  # future: merge small files

    model_config = {"env_prefix": "PARQUET_", "extra": "ignore"}


class StorageSettings(BaseSettings):
    processed_dir: str = "data/processed"
    datasets_dir: str = "data/datasets"

    model_config = {"env_prefix": "STORAGE_", "extra": "ignore"}


class FeatureStoreSettings(BaseSettings):
    cache_ttl_ms: int = 3000
    max_trades_per_coin: int = 5000
    max_books_per_coin: int = 300

    model_config = {"env_prefix": "FEATURES_", "extra": "ignore"}


class ReplaySettings(BaseSettings):
    data_dir: str = "data/raw"
    default_speed: float = 0.0  # 0 = max speed
    enabled: bool = False  # only enable explicitly

    model_config = {"env_prefix": "REPLAY_", "extra": "ignore"}


class ObservabilitySettings(BaseSettings):
    metrics_retention_sec: int = 3600
    alert_cooldown_sec: int = 300
    log_level: str = "INFO"

    model_config = {"env_prefix": "OBS_", "extra": "ignore"}


class CoinUniverseSettings(BaseSettings):
    """Coins to track — separate from trading coins."""
    default_coins: list[str] = [
        "BTC", "ETH", "SOL", "DOGE", "AVAX", "SUI", "LINK", "ARB", "OP", "APT",
        "INJ", "TIA", "SEI", "NEAR", "ATOM", "AAVE", "RENDER", "FET", "WIF", "ONDO",
    ]
    refresh_interval_min: int = 5

    model_config = {"env_prefix": "COINS_", "extra": "ignore"}


class Settings:
    """Aggregates all config groups. Each loads its own env vars from .env."""
    def __init__(self) -> None:
        self.db = DatabaseSettings(_env_file=".env")
        self.hl = HyperliquidSettings(_env_file=".env")
        self.binance = BinanceSettings(_env_file=".env")
        self.parquet = ParquetSettings(_env_file=".env")
        self.storage = StorageSettings(_env_file=".env")
        self.features = FeatureStoreSettings(_env_file=".env")
        self.replay = ReplaySettings(_env_file=".env")
        self.observability = ObservabilitySettings(_env_file=".env")
        self.coins = CoinUniverseSettings(_env_file=".env")


settings = Settings()
