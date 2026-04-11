# Label Definitions v1

## Target Labels

### Primary: `outcome` (categorical)
- **WIN**: net_pnl > 0 at real close
- **LOSS**: net_pnl <= 0 at real close
- Used for: classification models, win rate prediction

### Secondary: `net_pnl` (continuous)
- Actual net PnL in USD after fees
- Used for: regression models, expected value estimation

### Auxiliary labels (computed per trade)
| Label | Type | Definition |
|---|---|---|
| `mfe_pct` | float | Maximum Favorable Excursion — best unrealized profit % |
| `mae_pct` | float | Maximum Adverse Excursion — worst unrealized drawdown % |
| `tp_before_sl` | bool | Did price hit TP level before SL level? |
| `expectancy_bucket` | category | good (>0.1%), marginal (0-0.1%), bad (<0%) |
| `hold_bucket` | category | fast (<2m), normal (2-10m), slow (>10m) |
| `fee_killed` | bool | gross_pnl > 0 but net_pnl <= 0 |

## Label Horizons

Labels are computed **at real trade close** (not at fixed time horizons).
The trade close happens via SL, TP, ROI table, or timeout.

Future v2 may add forward-looking labels:
- `fwd_5m_ret`: return 5 min after signal
- `fwd_15m_ret`: return 15 min after signal
- `fwd_30m_ret`: return 30 min after signal

These require raw price data aligned to signal timestamps.

## Training Row Definition

Each row in the training dataset = **one signal evaluation**.

| Source | What it captures |
|---|---|
| Signal ENTER → trade outcome | Signal that was taken — has real outcome |
| Signal SKIP/BLOCKED | Signal that was NOT taken — outcome unknown, but features recorded |

### For ENTER signals (supervised):
- Features: snapshot at entry time
- Label: trade outcome (win/loss, net_pnl, mfe, mae)

### For SKIP/BLOCKED signals (semi-supervised):
- Features: snapshot at signal time
- Label: unknown (but we can compute `fwd_Xm_ret` from raw data later)
- Value: tells the model "what we chose not to do"

## What Constitutes a "Good Signal"

A signal is good if:
1. `net_pnl > 0` (profitable after fees)
2. `mfe_pct > 0.3%` (had meaningful upside)
3. `mae_pct > -0.2%` (didn't go too far against us)
4. `tp_before_sl = true` (direction was correct)

A signal is bad if:
1. `net_pnl < -0.3%` of notional
2. `mae_pct < -0.5%` (deep drawdown)
3. `hold_seconds > timeout * 3` (stuck trade)
