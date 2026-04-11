# Dataset Convention v1

## File Format
- **Parquet** — all datasets stored as `.parquet`
- Compression: snappy
- Location: `data/datasets/{dataset_name}/`

## Naming Convention
```
{purpose}_{coins}_{date_from}_{date_to}_{version}.parquet
```
Examples:
- `training_all_20260404_20260420_v1.parquet`
- `validation_btc_eth_sol_20260421_20260425_v1.parquet`
- `signals_skipped_all_20260404_20260430_v1.parquet`

## Column Schema

### Feature columns
- Named exactly as in `features/contract.py`
- Prefix by group: `mom_`, `vol_`, `trend_`, `micro_`, `temp_`
- All floats unless contract says otherwise

### Context columns
| Column | Type | Description |
|---|---|---|
| `coin` | str | Coin symbol |
| `side` | str | LONG or SHORT |
| `timestamp` | datetime | Signal/entry time (UTC) |
| `price` | float | Price at signal time |
| `regime` | str | Regime at signal time |
| `mode` | str | Trading mode (SCALP/NORMAL/SWING) |
| `signal_score` | float | Composite signal score |
| `trend_score` | float | Trend component |
| `micro_score` | float | Micro component |
| `momentum_score` | float | Momentum component |
| `action` | str | ENTER, SKIP, BLOCKED |
| `reason` | str | Why skipped/blocked (null if ENTER) |

### Label columns (only for ENTER signals with outcomes)
| Column | Type | Description |
|---|---|---|
| `outcome` | str | WIN or LOSS |
| `net_pnl` | float | Net PnL in USD |
| `gross_pnl` | float | Gross PnL before fees |
| `fee` | float | Total fees |
| `hold_seconds` | int | Trade duration |
| `exit_reason` | str | SL, TP, ROI_x, TIMEOUT |
| `mfe_pct` | float | Max favorable excursion % |
| `mae_pct` | float | Max adverse excursion % |
| `tp_before_sl` | bool | Price hit TP before SL |
| `expectancy_bucket` | str | good, marginal, bad |
| `fee_killed` | bool | Gross positive but net negative |

### Metadata columns
| Column | Type | Description |
|---|---|---|
| `dataset_version` | str | v1, v2, etc. |
| `feature_version` | str | From contract.py |
| `generated_at` | datetime | When dataset was built |

## Train/Validation/Test Split

**Temporal split only** — never random shuffle.

```
|------- train -------|--- val ---|--- test ---|
     70%                  15%         15%
```

- Train: oldest data
- Validation: next chunk (for hyperparameter tuning)
- Test: most recent data (never touched during training)

Minimum data requirements:
- Train: >= 200 trades
- Validation: >= 50 trades
- Test: >= 50 trades

## Dataset Registry

Every generated dataset is registered in `dataset_registry` table:
- name, path, row_count
- feature_version, label_type
- date range, coins included
- generation parameters

## Versioning Rules

- Bump version when: feature contract changes, label definition changes, or data pipeline changes
- Old datasets are kept (never deleted)
- Model trained on v1 dataset must record `dataset_version=v1`
