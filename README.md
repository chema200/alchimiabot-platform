# AgentBot Platform

[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue)](https://www.postgresql.org/)
[![Parquet](https://img.shields.io/badge/Storage-Parquet-purple)](https://parquet.apache.org/)
[![License](https://img.shields.io/badge/License-Private-red)]()

Quantitative trading platform for **research**, **replay**, and **ML** built around the [AgentBot](https://github.com/chema200/agentbot) live trading system. Ingests real-time market data from Hyperliquid and Binance, computes 48 features, detects market regimes, and provides a full replay/backtest pipeline with experiment tracking.

> **Live at:** [platform.alchimiabot.com](https://platform.alchimiabot.com) (dashboard)

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                          Data Ingestion                               │
│  ┌─────────────────────┐    ┌──────────────────────┐                 │
│  │  Hyperliquid WS     │    │  Binance WS          │                 │
│  │  20 coins            │    │  15 coins (context)  │                 │
│  │  trades + L2 book    │    │  trades + klines     │                 │
│  │  health + stale det. │    │                      │                 │
│  └─────────┬───────────┘    └──────────┬───────────┘                 │
│            │                           │                              │
│            ▼                           ▼                              │
│  ┌─────────────────────────────────────────────────┐                 │
│  │                   EventBus                       │                 │
│  │  async pub/sub, concurrent dispatch              │                 │
│  │  per-subscriber metrics, backpressure            │                 │
│  └─────────────────────┬───────────────────────────┘                 │
└────────────────────────┼─────────────────────────────────────────────┘
                         │
           ┌─────────────┼──────────────┐
           ▼             ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   Storage    │ │  Features    │ │   Regime     │
│              │ │              │ │  Detection   │
│  Parquet     │ │  48 features │ │              │
│  (append)    │ │  4 groups    │ │  5 regimes   │
│              │ │  versioned   │ │  persistence │
│  PostgreSQL  │ │  contract    │ │  per-regime  │
│  (Alembic)   │ │              │ │  evaluator   │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ▼                ▼                ▼
┌──────────────────────────────────────────────────────────────────┐
│                         Engine Pipeline                           │
│  SignalEngine → PolicyEngine → SizingEngine → RiskManager        │
│       → PositionManager → ExecutionSimulator                     │
└──────────────────────┬───────────────────────────────────────────┘
                       │
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   Replay     │ │  Backtest    │ │  Experiments │
│              │ │              │ │              │
│  End-to-end  │ │  Sharpe      │ │  Promote /   │
│  from Parquet│ │  Drawdown    │ │  Reject      │
│  full pipe   │ │  Profit fac. │ │  lifecycle   │
└──────────────┘ └──────────────┘ └──────────────┘
         │             │              │
         ▼             ▼              ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Research & Audit                             │
│  11 operational reports  |  4 audit checks  |  Daily report      │
│  WR by coin/side/hour    |  scheduler       |  12 sections       │
│  PnL by mode/tag/reason  |  health score    |  best/worst coins  │
│  Fee analysis             |  DB persistence  |  problems          │
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│                       Dashboard (FastAPI)                         │
│  port 8090  |  9 tabs  |  Cloudflare: platform.alchimiabot.com  │
│  Bot Live | Daily Report | Research | Audit | System | Capture   │
│  Features | Regimes | Contract                                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Key Features

### Data Ingestion
- **Hyperliquid WebSocket**: 20 coins, raw trades + L2 order book
- **Binance WebSocket**: 15 coins for cross-exchange context
- Per-coin **health monitoring** and **stale detection**
- Dynamic subscribe/unsubscribe based on coin activity

### EventBus
- Async pub/sub with concurrent dispatch
- Per-subscriber metrics and backpressure handling
- Decouples ingestion from downstream consumers

### Storage
- **Parquet**: append-only, partitioned by `event_type/coin/date/hour`
- **PostgreSQL**: structured data via Alembic migrations (port 5433)
- Live capture running: raw trades/book to Parquet, feature snapshots to PostgreSQL every 60s

### Feature Store (48 Features)
| Group | Count | Examples |
|-------|-------|---------|
| Momentum | 12 | RSI, ROC, MACD, Stochastic |
| Volatility | 10 | ATR, Bollinger width, realized vol, Garman-Klass |
| Trend | 12 | EMA cross, ADX, Aroon, supertrend |
| Microstructure | 8 | Book imbalance, trade flow, VWAP deviation |
| Temporal | 6 | Hour-of-day, day-of-week, session encoding |

Formal **feature contract** with versioning ensures reproducibility across replay and live.

### Regime Detection
| Regime | Description |
|--------|-------------|
| `trending_up` | Strong upward momentum confirmed |
| `trending_down` | Strong downward momentum confirmed |
| `choppy` | Range-bound, frequent reversals |
| `high_vol` | Elevated volatility, no clear direction |
| `quiet` | Low volatility, thin activity |

Regime persistence with cooldown prevents rapid flickering. Per-regime evaluator scores signal quality.

### Engine Pipeline
- **SignalEngine**: generates entry/exit signals from features + regime
- **PolicyEngine**: applies filters and confirms signal quality
- **SizingEngine**: position sizing based on volatility and risk budget
- **RiskManager**: enforces drawdown, exposure, and correlation limits
- **PositionManager**: tracks open positions and PnL
- **ExecutionSimulator**: models fills, slippage, and fees

### Replay
- Full end-to-end replay from Parquet through entire pipeline
- Reproduces exact feature computation and signal generation
- Backtest runner with Sharpe ratio, max drawdown, profit factor

### Experiment Tracker
- Promote/reject lifecycle for parameter changes
- Tracks configs, metrics, and outcomes per experiment run

### Audit System
- **4 modular checks**: integration, data_quality, storage, consistency
- **Scheduler**: runs checks at 5m / 15m / 1h / 6h intervals
- DB persistence of all audit runs and findings
- Aggregated **health score** per component

### Daily Report
- Automated 12-section audit template
- Scores, best/worst coins, detected problems
- Persisted to DB for historical comparison

### Research Reports (11 operational)
- Win rate by coin, side, hour
- PnL by mode, tag, close reason
- Fee analysis and impact
- Poison coins and rescuable coins identification

### Bot Integration
- REST receiver for live bot data:
  - `POST /api/bot/trade` -- trade executions desde AgentBot
  - `POST /api/bot/signal` -- evaluaciones de senales (con decision_trace, diagnostic_trace)
  - `POST /api/bot/signals` -- batch de senales
  - `POST /api/bot/regime` -- cambios de regimen
  - `POST /api/bot/snapshot` -- snapshots de posicion cada 30s (ON/OFF configurable)
- Fire-and-forget (no bloquea el bot)

### Quant Layer (analisis cuantitativo)
- **Metrics Engine**: WR, PF, expectancy, Sharpe, desglose por coin/side/modo/hora/exit
- **Experiment Engine**: simula cambios de config sobre trades historicos
- **Analysis Engine**: deteccion automatica de patrones, insights, warnings
- **Trade Analyzer**: analisis individual de cada trade con veredicto
- **Validation Runner**: 3 batches de experimentos con verdicts (TEST_LIVE, ADOPT, REJECT)
- **Counterfactual Analyzer**: que habria pasado con otros thresholds
- **Entry Quality Analyzer**: timing de entrada vs precio futuro
- **Config Analysis**: impacto de cada parametro
- **Score Parity**: consistencia del scoring entre coins/modos
- **Decision Engine**: recomendaciones accionables (HOLD, TEST, ADOPT, RETIRE)
- **Executive Summary**: resumen de alto nivel para la tab Conclusiones

### Trade Detail & Verdicts
- **trade_snapshots**: puntos intermedios cada 30s (precio, SL, TP, HWM, PnL)
- **trade_verdicts**: analisis automatico al cierre (GOOD/ACCEPTABLE/BAD/TERRIBLE)
  - Entry timing: OPTIMAL, ACCEPTABLE, LATE, TOO_EARLY
  - MFE capture %: cuanto del MFE se capturo
  - Time in profit %: % del tiempo en verde
  - SL movement tracking: cuantas veces se movio, min/max distance
  - Improvements: sugerencias concretas de mejora
  - Counterfactual: que habria pasado con SL mas ancho, TP mas corto, sin trailing
- **Limpieza**: borrado de snapshots antiguos por dias (boton en dashboard)

### ML (scaffolded)
- Label definitions v1 y dataset convention v1
- Model registry e inference (placeholder, no activo)

---

## Project Structure

```
agentbot-platform/
├── main.py                              # Application entry point
├── pyproject.toml                       # Python project config and dependencies
├── alembic.ini                          # Alembic migration config
├── alembic/                             # PostgreSQL migrations
│   └── versions/                        # Migration scripts
├── config/                              # YAML configuration files
├── src/
│   ├── ingestion/                       # Data ingestion layer
│   │   ├── ws/                          # WebSocket clients (HL, Binance)
│   │   └── rest/                        # REST API clients
│   ├── core/                            # EventBus, shared types, config
│   ├── storage/
│   │   ├── parquet/                     # Parquet append-only writer
│   │   ├── postgres/                    # SQLAlchemy models, repositories
│   │   └── duckdb/                      # DuckDB for analytical queries
│   ├── features/                        # Feature computation
│   │   ├── momentum/                    # RSI, ROC, MACD, etc.
│   │   ├── volatility/                  # ATR, Bollinger, realized vol
│   │   ├── trend/                       # EMA cross, ADX, supertrend
│   │   ├── microstructure/              # Book imbalance, trade flow
│   │   ├── temporal/                    # Time-based features
│   │   └── context/                     # Cross-exchange context
│   ├── regime/                          # Regime detection and persistence
│   ├── engine/                          # Trading engine pipeline
│   │   ├── signal/                      # SignalEngine
│   │   ├── policy/                      # PolicyEngine
│   │   ├── sizing/                      # SizingEngine
│   │   ├── risk/                        # RiskManager
│   │   ├── position/                    # PositionManager
│   │   └── execution/                   # ExecutionSimulator
│   ├── replay/                          # Replay from Parquet
│   ├── backtest/                        # Backtest runner and metrics
│   ├── experiments/                     # Experiment tracker
│   ├── audit/                           # Audit system
│   │   └── checks/                      # Modular audit checks
│   ├── research/                        # Research reports and analysis
│   │   ├── analysis/                    # 11 operational reports
│   │   └── notebooks/                   # Jupyter notebooks
│   ├── ml/                              # ML infrastructure
│   │   ├── training/                    # Training pipelines
│   │   ├── inference/                   # Model serving
│   │   └── registry/                    # Model registry
│   ├── observability/                   # System monitoring
│   │   ├── health/                      # Health checks
│   │   ├── metrics/                     # Prometheus metrics
│   │   └── alerts/                      # Alert rules
│   ├── dashboard/                       # FastAPI dashboard
│   │   └── static/                      # Frontend assets
│   └── assistant/                       # AI assistant integration
├── data/                                # Parquet data files (gitignored)
├── logs/                                # Application logs
├── scripts/                             # Utility scripts
├── tests/                               # Test suite
└── docs/                                # Documentation
```

---

## Database Schema

**PostgreSQL** (port 5433, Docker) with Alembic migrations. Key tables:

| Tabla | Proposito |
|-------|-----------|
| `trade_outcomes` | Cada trade con PnL, fees, scores, quality label, config snapshot |
| `signal_evaluations` | Cada senal evaluada con decision_trace, diagnostic_trace |
| `trade_snapshots` | Snapshots de posicion cada 30s (precio, SL, TP, HWM, PnL) |
| `trade_verdicts` | Analisis automatico por trade (veredicto, mejoras, counterfactual) |
| `feature_snapshots` | Estado de features por coin cada 60s |
| `regime_labels` | Clasificacion de regimen por coin |
| `coin_profiles` | Perfiles de comportamiento por coin |
| `dataset_registry` | Datasets registrados para ML |
| `model_registry` | Modelos entrenados (metadata y metricas) |
| `replay_runs` | Ejecuciones de replay/backtest |
| `experiment_runs` | Experimentos de config con promote/reject |
| `audit_runs` | Historial de ejecuciones de auditoria |
| `audit_findings` | Hallazgos individuales con severidad |

---

## Prerequisites

- **Python 3.12+**
- **Docker** and **Docker Compose** (for PostgreSQL)
- **Cloudflare Tunnel** (optional, for remote access)

## Quick Start

### 1. Install Dependencies

```bash
pip install -e .
```

### 2. Start PostgreSQL

```bash
docker-compose up -d
```

PostgreSQL will be available on port `5433`.

### 3. Run Migrations

```bash
alembic upgrade head
```

### 4. Configure

Edit files in `config/` for:
- Coin lists (Hyperliquid, Binance)
- Feature parameters
- Engine settings
- Audit schedule

### 5. Start the Platform

```bash
python main.py
```

Dashboard available at http://localhost:8090 or https://platform.alchimiabot.com

---

## Dashboard

FastAPI-based dashboard on port **8090** con 13 tabs:

| Tab | Contenido |
|-----|-----------|
| **Conclusiones** | Resumen ejecutivo: PnL, WR, que funciona, que falla, proximos pasos |
| **Bot Live** | Trades y posiciones en tiempo real del bot |
| **Trades** | Historial de trades con click para detalle completo (veredicto, snapshots, mejoras, counterfactual) |
| **Daily Report** | Informe diario automatico de 12 secciones |
| **Sistema** | EventBus, ingestion health, storage stats |
| **Captura** | Estado de captura de datos, tamano Parquet |
| **Research Lab** | 11 sub-vistas: metrics, experiments, analysis, entry quality, counterfactual, config, etc. |
| **Validation** | Framework de 3 batches de experimentos con verdicts |
| **Reports** | 11 reportes operativos (WR por coin/side/hora, PnL por modo/tag, fees) |
| **Features** | Valores de features por coin |
| **Regimenes** | Regimen actual por coin, historial |
| **Audit** | Resultados de auditoria, health scores, findings |
| **Contrato** | Feature contract viewer |

---

## Integration with AgentBot

This platform receives live data from [AgentBot](https://github.com/chema200/agentbot) via REST endpoints:

```
AgentBot (Java)                    AgentBot Platform (Python)
┌──────────────┐   fire & forget   ┌────────────────────┐
│ PlatformBridge├──────────────────►│ POST /api/bot/trade│
│              ├──────────────────►│ POST /api/bot/signal│
│              ├──────────────────►│ POST /api/bot/regime│
└──────────────┘                   └────────────────────┘
```

All data flows one-way. The platform never blocks or affects the live bot.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12 |
| Web framework | FastAPI, Uvicorn |
| Database | PostgreSQL 16 (Docker), Alembic migrations |
| Analytical storage | Parquet (append-only), DuckDB |
| Data ingestion | WebSocket (asyncio), REST |
| Feature computation | NumPy, Pandas |
| Deployment | Cloudflare Tunnel, Ubuntu server |
