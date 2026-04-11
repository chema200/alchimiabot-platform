# Alchimiabot — Plan Maestro Multi-Tenant

> Documento maestro de referencia para evolucionar Alchimiabot de single-tenant
> a plataforma multi-usuario con 10 amigos en beta cerrada y target de 100
> usuarios maximo. Solo Hyperliquid en la primera fase.
>
> **Este documento NO implementa nada. Es solo recopilacion para tomar decisiones.**

**Producto:** Alchimiabot
**Modelo:** Non-custodial via API Wallets de Hyperliquid
**Beta:** 10 amigos cerrada
**Target:** 100 usuarios maximo
**Exchanges:** solo Hyperliquid (otros futuros)
**Fecha:** 2026-04-10
**Estado:** Recopilando, no implementado

---

## 📋 Indice

1. [Vision general](#1-vision-general)
2. [Decisiones tomadas](#2-decisiones-tomadas)
3. [Decisiones pendientes](#3-decisiones-pendientes)
4. [Como se conectan los usuarios (non-custodial)](#4-como-se-conectan-los-usuarios)
5. [Arquitectura tecnica](#5-arquitectura-tecnica)
6. [Estrategia de datos](#6-estrategia-de-datos)
7. [Sistema de roles](#7-sistema-de-roles)
8. [Seguridad](#8-seguridad)
9. [Plan por fases](#9-plan-por-fases)
10. [Riesgos](#10-riesgos)
11. [Costes estimados](#11-costes-estimados)
12. [Documentos relacionados](#12-documentos-relacionados)

---

## 1. Vision general

### El verdadero "por que" del proyecto

**Alchimiabot NO es multi-tenant para vender mas.**
**Alchimiabot ES multi-tenant para APRENDER mas rapido.**

#### El problema real

Hoy el bot esta single-user. Eso significa:
- 1 sola configuracion probandose en cualquier momento
- 1 sola persona (tu) decidiendo que parametros tocar
- A/B testing **en serie**: cambias 1 parametro, esperas dias, ves resultado, cambias otro
- Muestra estadistica ridicula (pocos trades/dia)
- 6 meses para descubrir lo que se podria descubrir en 2 semanas si fueras 10 personas

**El bot no es malo. Lo que falta es el conocimiento de los parametros optimos para cada condicion de mercado.**

Y ese conocimiento **no se descubre con 1 usuario tradeando**. Se descubre con **muchos usuarios probando configs distintas en paralelo**, comparando resultados, y dejando que los datos hablen.

#### La solucion: A/B testing distribuido

Con 10 amigos en multi-tenant:

```
Usuario A: SCALP minScore=56, trail=0.12  →  10 trades/dia
Usuario B: SCALP minScore=58, trail=0.10  →  8 trades/dia
Usuario C: NORMAL confirm=2min            →  6 trades/dia
Usuario D: NORMAL confirm=3min            →  5 trades/dia
Usuario E: SWING SL=0.50                  →  3 trades/dia
...
Usuario J: NORMAL custom mix              →  7 trades/dia

Total: ~50-60 trades/dia con 10 configs DISTINTAS
       en mercado SIMULTANEO (mismo BTC, mismo flow)
```

**Eso es oro estadistico.** Con 10 amigos en 1 mes tienes **mas data valida que tu solo en 6 meses**, porque:
- Mismo mercado, configs distintas → comparacion limpia
- Variantes que tu nunca probarias porque pierdes dinero solo
- El sistema puede aprender que configs ganan en que condiciones

**Esto es exactamente lo que hacen los hedge funds quants:** corren miles de variantes en paralelo para descubrir que funciona.

### Cambio de mentalidad fundamental

| Vision vieja | Vision nueva |
|---|---|
| "Tengo que validar el bot solo antes de venderlo" | "Necesito muchos usuarios para validar el bot" |
| "Beta = QA antes de cobrar" | "Beta = experimento estadistico que hace el bot mejor" |
| "Si el bot pierde, no puedo cobrar" | "El bot pierde porque estoy optimizando solo. Con 10 amigos descubro los parametros y ganamos todos" |
| Single-user → multi-user despues de validar | **Multi-user PORQUE single-user no escala el aprendizaje** |
| Roadmap secuencial: optimizar → vender | Roadmap paralelo: multi-tenant Y optimizar a la vez |

### El verdadero arma competitiva: aprendizaje colectivo

Hoy el `CoinProfileOptimizer` aprende per-usuario (per-coin+side+mode). Eso es bueno **pero limitado** porque cada usuario tiene poca muestra.

**En multi-tenant** anades un nivel mas:

```
CoinProfileOptimizer per-user (lo que tienes hoy)
              ↓
GlobalLearningService (NUEVO, futuro)
  - Lee resultados de TODOS los usuarios
  - Compara configs vs PnL agregado
  - Sugiere "best config per coin" basado en N usuarios
  - El Quantum Platform muestra:
    "Los usuarios que usan SL=0.20 en NORMAL ganan +0.15/trade mas
     que los que usan SL=0.25 (n=247 trades, p<0.01)"
```

**Esto es lo que diferencia Alchimiabot de cualquier otro bot.** No es solo un wrapper de HL. Es **un sistema que aprende colectivamente**.

### El nuevo objetivo

Convertir el bot en una **plataforma non-custodial de inteligencia colectiva** donde:
- Cada usuario conecta su propia wallet de Hyperliquid via API Wallet
- El bot opera con la API Wallet del usuario (no con su wallet principal)
- Cada usuario tiene su propia config, perfiles, historia, schedule
- Multiples usuarios operan simultaneamente sin contaminacion
- **Los datos cruzados entre usuarios alimentan un sistema de aprendizaje global**
- Sistema de roles BASIC / PRO / PREMIUM con feature gating
- **El producto MEJORA con cada usuario nuevo (network effect real)**

### Por que non-custodial

> **Nunca pides la private key principal del usuario.**

El usuario genera una **API Wallet** dentro de Hyperliquid (sub-key especifica para tradear) y te entrega esa. Esa key:
- Solo puede tradear, **no puede retirar fondos**
- Es independiente de la wallet principal del usuario
- Si tu sistema se hackea, los fondos del usuario estan a salvo
- El usuario puede revocarla en HL en 1 click

**Es el modelo que usan TODOS los bots profesionales** (3Commas, Hummingbot, etc).

### Lo que hace especial a Alchimiabot

- Motor propio refinado con experiencia real (no copia de open source)
- Schedule semanal granular (V26)
- Sistema de markers para medir impacto de cambios (V25)
- Coin profile auto-learning per usuario
- Shadow mode con proposals
- Microestructura via WebSocket
- Protecciones (StoplossGuard, MaxDrawdown, LossStreak)

---

## 2. Decisiones tomadas

| # | Decision | Razon |
|---|---|---|
| 1 | **Non-custodial via API Wallets** | Cero responsabilidad legal sobre fondos. Estandar de industria. |
| 2 | **Solo Hyperliquid en Fase 1** | Donde el bot esta optimizado. Conoces la API. Sin KYC. |
| 3 | **Single JVM + sessions per user** | Escalable a 100 users. Mejor balance recursos/aislamiento. |
| 4 | **MarketDataService compartido** | Sin esto, HL rate limits matan el sistema con >20 users. |
| 5 | **PostgreSQL solo (no MongoDB ni nada raro)** | Aguanta de sobra el volumen. KISS. |
| 6 | **Beta cerrada con 10 amigos primero** | Validar el modelo antes de invertir mucho. |
| 7 | **Producto se llama Alchimiabot** | Continuidad de marca. |
| 8 | **No custodiar wallets ajenas** | Legal claro: cero zona gris. |
| 9 | **Refactor incremental, no rewrite** | Reusar el motor actual, no empezar de cero. |
| 10 | **Tiempo no es un problema** | Hacerlo bien aunque tarde 3-4 meses. |

---

## 3. Decisiones pendientes

Cosas que necesitas decidir antes de empezar:

| # | Decision | Implicacion |
|---|---|---|
| 1 | **¿Modelo de negocio?** (gratis / freemium / suscripcion / % profit) | Define los tiers y la motivacion del usuario |
| 2 | **¿Borrar las 13 tablas legacy?** (Polymarket pausado, backtest viejo, etc) | Cleanup mental + 10 MB recuperados |
| 3 | **¿Mantener `hl_trade_micro_features`?** | Solo si planeas ML futuro |
| 4 | **¿Polymarket vuelve algun dia?** | Si NO → drop tablas. Si SI → mantenerlas dormidas. |
| 5 | **¿Servidor propio o cloud?** | Costes y mantenimiento |
| 6 | **¿Backups: full o solo criticos?** | Criticos minimum: `auth_users`, `user_wallets`, `hl_trading_history`, `change_markers` |
| 7 | **¿Soporte tecnico?** (chat, email, foro privado, ninguno) | Operaciones a futuro |
| 8 | **¿Asesoria legal previa a beta?** | **Recomendado SI** aunque sea non-custodial. ~200-500€ una vez. |
| 9 | **¿Sistema de invitacion o abierto?** | Beta cerrada → invitacion. Despues, abierto. |
| 10 | **¿Grafico publico de performance?** | Marketing potencial pero exposicion |

---

## 4. Como se conectan los usuarios

### El flujo de onboarding (no custodial)

```
PASO 1 — USUARIO en Hyperliquid
─────────────────────────────────
1. Conecta MetaMask en https://app.hyperliquid.xyz
2. Ve sus fondos ($X)
3. Va a https://app.hyperliquid.xyz/API
4. Click "Generate" → HL crea una API Wallet
5. HL le da:
   - Address: 0x123abc...
   - Private Key: 0xabc123...
6. Esa API Wallet es una sub-cuenta:
   - Hereda los fondos de la principal
   - SOLO puede tradear (NO retirar)
   - Independiente, revocable
   - El usuario puede generar otra cuando quiera

PASO 2 — USUARIO en Alchimiabot
─────────────────────────────────
1. Login en bot.alchimiabot.com
2. Va a "Conectar Wallet HL"
3. Modal con:
   - Tutorial visual de como crear la API Wallet
   - Boton "Ya tengo mi API Wallet"
4. Pega:
   - Address: [0x123abc...]
   - Private Key: [        ******     ]
5. Tu sistema valida:
   - La address existe en HL
   - La private key firma correctamente
   - El balance > 0 (cuenta usable)
   - NO puede hacer withdrawals (test dummy)
6. Si todo OK: cifra con AES-GCM, guarda en DB
7. UI muestra: balance, address, "Listo para tradear"

PASO 3 — USUARIO empieza a tradear
─────────────────────────────────
1. Va a la pestana "Trading"
2. Selecciona budget
3. Click "Iniciar"
4. SessionManager crea su TradingSession
5. ExchangeClient con su API Wallet
6. Bot opera con sus fondos, sus reglas, su config
7. Stop cuando quiera
```

### Lo que ve el usuario (UX critica)

```
┌────────────────────────────────────────────────────┐
│  Conectar tu cuenta de Hyperliquid                 │
│                                                    │
│  ⚠️ NUNCA te pediremos tu private key principal    │
│  ⚠️ Alchimiabot NO custodia tus fondos             │
│  ⚠️ Solo necesitamos una API Wallet                │
│                                                    │
│  Como crear tu API Wallet (5 minutos):             │
│  1. Ve a https://app.hyperliquid.xyz/API           │
│  2. Click "Generate API Wallet"                    │
│  3. Tu wallet principal NO se ve afectada          │
│  4. Copia la dirección y la clave                  │
│  5. Pegalas aqui abajo                             │
│                                                    │
│  [📺 Ver tutorial en video]                        │
│                                                    │
│  Address de la API Wallet:                         │
│  ┌──────────────────────────────────────────────┐  │
│  │ 0x...                                        │  │
│  └──────────────────────────────────────────────┘  │
│                                                    │
│  API Wallet Private Key:                           │
│  ┌──────────────────────────────────────────────┐  │
│  │ ******                              [👁]     │  │
│  └──────────────────────────────────────────────┘  │
│                                                    │
│  ☑ He leido la guia y entiendo que esta clave      │
│    SOLO puede tradear, NO retirar fondos           │
│                                                    │
│              [ Validar y guardar ]                 │
└────────────────────────────────────────────────────┘
```

### Modelo en DB

```sql
CREATE TABLE user_wallets (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    exchange        VARCHAR(30) NOT NULL,    -- "HYPERLIQUID"
    label           VARCHAR(100),            -- "Mi wallet principal"
    public_id       VARCHAR(200) NOT NULL,   -- address (0x123...)
    encrypted_secret BYTEA NOT NULL,         -- private key cifrada AES-GCM
    encryption_version INT NOT NULL DEFAULT 1,
    permissions     JSONB,                    -- {"trade":true,"withdraw":false}
    is_active       BOOLEAN DEFAULT TRUE,
    is_default      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    UNIQUE (user_id, exchange, public_id)
);
```

### Por que esto es seguro

| Escenario | Que pasa |
|---|---|
| Tu servidor se hackea, atacante roba la DB | Encuentra la `encrypted_secret`, pero sin `WALLET_MASTER_KEY` no la puede descifrar |
| Atacante consigue ambas cosas | Puede tradear con esa API Wallet pero **NO puede retirar fondos del usuario** |
| El usuario sospecha algo | Va a HL, revoca la API Wallet en 1 click, el atacante queda fuera |
| El usuario cambia de bot | Genera otra API Wallet, te da la nueva, revoca la vieja |
| Tu te vas de vacaciones | No pasa nada, los usuarios pueden parar/iniciar el bot ellos mismos |

---

## 5. Arquitectura tecnica

### Aproximacion elegida: Hibrido B

**Single JVM, una `TradingSession` per usuario, market data compartido.**

#### Por que no las otras

| Opcion | Por que NO |
|---|---|
| 1 proceso por usuario | 100 JVMs = 50 GB RAM. Inviable. |
| 1 engine, userId param | Race conditions. Catastrofico. |
| Vault HL nativo | Cambia el modelo de negocio. Fondos comingled. Sin configs por user. |

#### Por que SI Hibrido B

- 4-8 GB RAM para 100 usuarios
- Aislamiento via `TradingSession` POJO dedicado per user
- Market data compartido = no rebasa rate limits HL
- Refactor incremental del motor actual
- 1 proceso, 1 deploy, 1 monitoring

### Componentes nuevos a crear

| Componente | Responsabilidad |
|---|---|
| **SessionManager** | Crea/destruye `TradingSession` per usuario |
| **TradingSession** | POJO con todo el estado mutable per user (positions, config, gateStats, wallet client) |
| **MarketDataService** | Singleton compartido: 1 fetch de precios para todos |
| **ExchangeClientFactory** | Crea HL client desde credenciales descifradas |
| **WalletService** | CRUD de wallets + cifrado |
| **SecretsService** | AES-GCM encrypt/decrypt |
| **TenantContext** | ThreadLocal con `userId` actual para logging |
| **FeatureGuardAspect** | Validacion de permisos per role |

### Componentes a refactorizar

| Componente | Cambio |
|---|---|
| **HlDirectionalEngine** | De singleton con estado → service stateless que recibe `TradingSession` como parametro |
| **HyperliquidApiClient** | De singleton con 1 wallet → instancia per `TradingSession` |
| **CoinProfileOptimizer** | Anadir `user_id` a queries y persistencia |
| **HyperliquidController** | Endpoints scoped por `user_id` del JWT |
| **PlatformBridge** | Anadir `user_id` a payloads |

### Diagrama logico

```
                  [Cloudflare Tunnel]
                         │
                ┌────────┴────────┐
                │                 │
        ┌───────▼───────┐ ┌──────▼──────┐
        │  Bot Spring   │ │  Platform   │
        │  Boot 8080    │ │  FastAPI    │
        └───────┬───────┘ └──────┬──────┘
                │                │
        ┌───────▼───────┐        │
        │  AuthService  │        │
        │  JWT + roles  │        │
        └───────┬───────┘        │
                │                │
        ┌───────▼─────────────────────────────┐
        │       SessionManager                │
        │  Map<UserId, TradingSession>        │
        │  ┌────────┐ ┌────────┐ ┌────────┐  │
        │  │Sess A  │ │Sess B  │ │Sess C  │  │
        │  └────┬───┘ └────┬───┘ └────┬───┘  │
        └───────┼──────────┼──────────┼──────┘
                │          │          │
        ┌───────▼──────────▼──────────▼──────┐
        │      MarketDataService SHARED      │
        │  - 1 fetch de precios cada 3s      │
        │  - 1 trend service                 │
        │  - 1 microstructure WS             │
        └───────┬────────────────────────────┘
                │
        ┌───────▼──────┐
        │ HL Public API│ (1 conexion total)
        └──────────────┘

        ┌─────────────────────────────┐
        │ ExchangeClientFactory       │
        │ ┌──────┐ ┌──────┐ ┌──────┐ │
        │ │HL A  │ │HL B  │ │HL C  │ │ ← 1 client per session
        │ └──────┘ └──────┘ └──────┘ │
        └─────────────────────────────┘

        ┌────────────────────────────┐
        │  PostgreSQL multi-tenant   │
        │  Tablas con user_id        │
        └────────────────────────────┘
```

---

## 6. Estrategia de datos

### Lo que importa: que se multiplica × 100

**Lo critico:** la mayoria de tablas escalan linealmente, **pero hay 2 que matan el sistema** si no se atacan:

#### 🔴 `feature_snapshots` (platform)

```
Hoy con 1 user: 134.600 rows / dia = 140 MB / dia
× 100 users:    13.5 millones rows / dia = 14 GB / dia
6 meses:        ~2.500 GB = 2.5 TB
```

**Inviable.** Es el asesino #1.

**Fix:** reemplazar por `trade_snapshots_agg` con 1 row por trade (no 1 por 30s). **5000x menos espacio.**

#### 🔴 `signal_evaluations` (platform)

```
Hoy:           28k / dia
× 100:         2.8M / dia
6 meses:       ~1 TB
```

**Fix:** solo guardar `ENTER` + `BLOCKED` + sample 1% del resto. **56x menos.**

### Comparativa total con 100 usuarios / 6 meses

| Escenario | DB total | Servidor | Coste/mes |
|---|---|---|---|
| **Sin optimizar** | **~4 TB** ⚠️ inviable | Imposible | ~500€ |
| **Optimizado** | **~310 GB** ✅ | 8c/32GB/1TB SSD | ~80-120€ |

**El 92% del ahorro viene de UN solo cambio:** agregar `feature_snapshots` por trade.

### Tablas legacy a borrar (13)

```sql
-- Polymarket pausado
DROP TABLE real_sessions, real_session_fills, redeem_history;

-- Backtest legacy
DROP TABLE backtest_runs, monte_carlo_runs;

-- Shadow legacy (sustituido por shadow_mode en coin_profiles)
DROP TABLE shadow_snapshots, shadow_runs;

-- HL legacy
DROP TABLE hl_session_fills, hl_sessions, orders, fills, pnl;

-- V26 sustituye V14
DROP TABLE hl_mode_schedule;

-- Configs legacy
DROP TABLE trading_config_presets, trading_config_active;
```

Cuando: en `V27__cleanup_legacy.sql` antes de Fase 1.

### TTLs propuestos

```sql
-- Daily cron en el bot:
DELETE FROM hl_alerts WHERE created_at < NOW() - INTERVAL '30 days';
DELETE FROM hl_trade_micro_features WHERE created_at < NOW() - INTERVAL '30 days';
DELETE FROM hl_coin_profile_changes WHERE created_at < NOW() - INTERVAL '90 days';

-- Daily cron en el platform:
DELETE FROM feature_snapshots WHERE timestamp < NOW() - INTERVAL '7 days';
DELETE FROM signal_evaluations WHERE timestamp < NOW() - INTERVAL '30 days';
```

### Tablas que llevan `user_id` nuevo

| Tabla bot | Tabla platform |
|---|---|
| `hl_trading_history` | `change_markers` |
| `hl_dir_sessions` | `trade_outcomes` |
| `hl_coin_profiles` | `signal_evaluations` |
| `hl_coin_profile_stats` | `feature_snapshots` (mientras exista) |
| `hl_coin_profile_changes` | `trade_snapshots_agg` (futura) |
| `hl_coin_profile_proposals` | |
| `hl_alerts` | |
| `hl_trade_micro_features` | |
| `hl_mode_schedule_weekly` | |

### Tablas nuevas

```sql
CREATE TABLE user_wallets (...);          -- credenciales encriptadas
CREATE TABLE user_roles (...);            -- BASIC/PRO/PREMIUM
CREATE TABLE tier_limits (...);           -- limites por rol
CREATE TABLE user_preset_overrides (...); -- override de presets per user
CREATE TABLE audit_log (...);             -- compliance + debugging
```

### Estimacion para 10 amigos (beta)

Buenas noticias:

```
DB despues de 1 mes:      ~1 GB
DB despues de 3 meses:    ~3 GB
DB despues de 6 meses:    ~6 GB
Servidor minimo:          VPS 4 GB / 2 cores / 50 GB → 10€/mes
```

**Para beta de 10, no necesitas casi optimizar nada.** Solo cleanup + TTLs.

---

## 7. Sistema de roles y modelo de negocio

### Posicionamiento del producto

> **Alchimiabot NO es "un bot que gana dinero".**
> **Alchimiabot ES "una herramienta profesional que te ayuda a tradear en Hyperliquid".**

Esta distincion es **critica** y atraviesa todo el diseno:

| Aspecto | "Bot ganador" | **"Herramienta de trading"** |
|---|---|---|
| Expectativa del usuario | Espera ganar SI o SI | Espera mejorar su trading |
| Si pierde dinero | Culpa al bot | Asume parte de la responsabilidad |
| Legal | Riesgo alto (parece promesa) | Riesgo bajo (eres software vendor) |
| Marketing | "Gana 10% al mes" → falso | "Automatiza tu estrategia" → verdad |
| Nicho | Personas buscando dinero facil | Traders reales con criterio |
| Retencion | Baja (se van si pierden 1 mes) | Alta (es su herramienta diaria) |
| Soporte | Pesado (cada perdida = ticket) | Ligero (entienden el mercado) |
| Posicionamiento | Vendedores de bots magicos cripto | TradingView, 3Commas, Hummingbot |

**Implicacion arquitectural:** BASIC NO es "intocable y a prueba de balas". BASIC ES **una herramienta usable** con lo esencial. La diferenciacion con PRO/PREMIUM es **profundidad de control**, no **acceso a controlar**.

### El motor: identico para todos

```
              ┌─── HlDirectionalEngine (mismo codigo) ───┐
              │                                          │
   ┌──────────▼──────────┐  ┌──────────▼──────────┐  ┌──▼──────────────┐
   │   BASIC user        │  │   PRO user          │  │  PREMIUM user   │
   │                     │  │                     │  │                 │
   │  Lo esencial        │  │  Control profundo   │  │  Control total  │
   │  Config simple      │  │  Config completa    │  │  + Research     │
   │  Modos basicos      │  │  Modos + auto-learn │  │  + Quantum      │
   │                     │  │                     │  │                 │
   └─────────────────────┘  └─────────────────────┘  └─────────────────┘
```

**Mismo motor.** Mismos filtros. Misma calidad de signals. Misma ejecucion. Mismo edge.

Lo que cambia es **la superficie de control que el usuario tiene encima** y **la profundidad del analisis que ve**.

Es como vender el mismo coche con distintos paquetes:
- BASIC = aire acondicionado, radio, navegador basico
- PRO = sensores, asientos electricos, modos de conduccion
- PREMIUM = piloto automatico, suspension adaptativa, telemetria

**El motor que mueve las ruedas es identico.**

---

### 🟢 BASIC — "Empieza a tradear con criterio"

**Mensaje al usuario:** "Conecta tu wallet, elige modo y budget, y empieza. Lo esencial para tradear en HL con una estrategia probada."

**Filosofia:** el usuario tiene **conocimientos basicos** de trading, sabe que es un SL y un TP, pero no quiere meterse en parametros avanzados. Quiere una **herramienta funcional** que le ayude a tradear con un sistema.

**Precio:** **30€/mes** (sin tier gratis — Alchimiabot es producto premium).

**Lo que tiene:**

| Funcion | Nivel BASIC |
|---|---|
| **Wallets** | 1 wallet HL conectada |
| **Budget maximo** | $1.000 (o $200 si es plan gratis) |
| **Posiciones simultaneas** | hasta 3 |
| **Modos disponibles** | **SCALP, NORMAL, SWING** (puede elegir) |
| **Schedule** | Configurable basico (puede elegir un modo fijo o usar el horario default) |
| **Config trading** | **Editable basica:** SL, TP, threshold, max positions, cooldown, modo activo |
| **Filtros de calidad** | Editables ON/OFF (lateral, score, late entry) |
| **Coin profiles** | Auto-learning ON, ve sus stats |
| **Trades visibles** | Sus propios trades en tiempo real |
| **Historial** | Ultimos 30 dias |
| **Metricas** | PnL, WR, profit factor, exit reasons |
| **Alertas Telegram** | Entradas/salidas, resumen diario, cambios de modo |
| **Soporte** | FAQ + comunidad (Discord/Telegram publico) |
| **Pause/Reset proteccion** | Si (manual) |

**Lo que NO tiene BASIC:**
- ❌ Schedule semanal granular (grid 7×24)
- ❌ Microstructure panel (WS orderbook/tape)
- ❌ Coin profiles **per usuario** (usa los compartidos)
- ❌ Shadow mode con proposals
- ❌ Parametros avanzados (ATR multiplier, partial close %, score weights, fee cover, etc)
- ❌ Multiples wallets
- ❌ Acceso al Quantum Platform (research)
- ❌ Markers manuales
- ❌ API access

**Por que este diseno:**
- Es **una herramienta usable**, no un producto bloqueado
- El usuario puede aprender y experimentar con los controles esenciales
- Si quiere mas profundidad → upgrade natural a PRO
- Cubre el 80% de los casos de uso reales
- Es defendible legalmente: "te damos las herramientas, tu decides"

---

### 🟣 PRO — "Toma el control completo"

**Mensaje al usuario:** "Configura el bot a tu medida. Edita cada parametro. Aprende que funciona en tus monedas favoritas con auto-learning per usuario."

**Filosofia:** trader con **experiencia real**, sabe lo que es ATR, microstructure, profit factor. Quiere **personalizacion profunda** y datos avanzados.

**Precio:** **90€/mes**.

**Lo que tiene** (todo lo de BASIC mas):

| Funcion | Nivel PRO |
|---|---|
| **Wallets** | hasta 3 wallets HL |
| **Budget maximo** | $10.000 |
| **Posiciones simultaneas** | hasta 8 |
| **Schedule semanal granular** | **Grid 7×24 editable** (V26 completo) |
| **Config trading** | **TODOS los parametros editables**: ATR, partial close %, trailing activation/distance, score weights, fee cover, ROI table, late entry multiplier, etc |
| **Filtros avanzados** | SL viability filter (V25) editable, score excess filter, price position filter |
| **Coin profiles per usuario** | Auto-learning **propio**, no compartido. Ve evolucion de cada (coin, side, mode) |
| **Microstructure panel** | Visible (orderbook imbalance, tape signals, score) |
| **Override manual de modo** | Si (badge MANUAL/AUTO en horario) |
| **Historial** | Ultimos 90 dias |
| **Metricas avanzadas** | MFE/MAE per trade, profit factor, expectancy, gate stats por filtro |
| **Dashboard del bot** | Acceso completo a todas las pestanas operacionales |
| **Quantum Platform** | Acceso parcial: Bot Live, Trades, Daily Report, Cambios (read-only) |
| **Markers** | NO crea, pero VE los automaticos en pestana Cambios |
| **Alertas Telegram** | Avanzadas: cambios de modo, locks, protecciones, optimizer changes |
| **Soporte** | Email priority (24-48h respuesta) |

**Lo que NO tiene PRO:**
- ❌ Conclusiones del Quantum Platform (research avanzado)
- ❌ Validation, Lab, Reports
- ❌ Markers manuales
- ❌ Shadow mode con proposals
- ❌ Mas de 3 wallets
- ❌ API access
- ❌ Webhooks personalizados
- ❌ Multi-exchange (cuando llegue)

**Por que este diseno:**
- PRO es el **sweet spot comercial** del producto
- Cubre todas las necesidades reales de un trader serio
- El precio (25-30€/mes) es razonable comparado con TradingView Pro (15€), 3Commas Pro (49€), etc
- **Aqui es donde se concentra la mayoria del revenue**

---

### 🟡 PREMIUM — "Acceso completo + Quantum Platform"

**Mensaje al usuario:** "Mismo nivel que el creador. Quantum Platform completo, shadow mode, markers manuales, multi-wallet, API access. Para traders profesionales."

**Filosofia:** trader **profesional** o quant que usa Alchimiabot como una herramienta mas en su stack. Quiere **todo**.

**Precio:** **200€/mes** flat O modelo % profit alternativo (0€/mes + 20% del profit mensual).

**Lo que tiene** (todo lo de PRO mas):

| Funcion | Nivel PREMIUM |
|---|---|
| **Wallets** | Ilimitadas |
| **Budget maximo** | Sin limite (lo que aguante HL) |
| **Posiciones simultaneas** | Sin limite |
| **Modos** | Todos + posibilidad de definir presets propios con nombre custom |
| **Schedule semanal** | Editable + simulador what-if (futuro) |
| **Config trading** | TODO editable (incluyendo experimentales) |
| **Coin profiles** | Auto-learning + **shadow mode** (proposals revisables) |
| **Microstructure** | Panel completo + alertas WS |
| **Quantum Platform** | **Acceso COMPLETO**: Conclusiones, Markers, Cambios, Daily Report, Validation, Lab, Reports, Features, Regimenes |
| **Markers manuales** | Si, puede crear desde Config y desde el platform |
| **Shadow mode** | Si, con UI para aceptar/rechazar proposals del optimizer |
| **Backtesting personalizado** | Si (cuando lo construyas) |
| **API access** | Si (futuro) — para integrar con otros sistemas |
| **Webhooks** | Notificaciones a webhook propio (Discord, Slack, custom) |
| **Histórico** | Ilimitado |
| **Soporte** | Chat directo (telegram privado o slack) |
| **Multi-exchange** | Si cuando llegue (Binance, Bybit) |
| **Acceso anticipado a features** | Si (early access a refactors antes que PRO) |

**Por que este diseno:**
- PREMIUM es para **traders serios** que ya saben lo que hacen
- El precio justifica el acceso a research que tu mismo usas
- El modelo **% profit alternativo** atrae a usuarios con mucho capital que no quieren pagar fijo sin garantias
- Pocos usuarios pero **muy alto revenue per user**

---

### 🔴 ADMIN (interno, no vendible)

Solo tu y posibles colaboradores futuros.

| Funcion | Nivel ADMIN |
|---|---|
| Todo PREMIUM | ✅ |
| Vista global de TODOS los usuarios | ✅ |
| Gestion de roles | ✅ asignar tiers a usuarios |
| Reset de protecciones global | ✅ |
| Force-close de cualquier posicion | ✅ |
| Audit log completo | ✅ |
| Metricas agregadas del sistema | ✅ |
| Pause global (kill switch) | ✅ |
| Modificacion de tier_limits | ✅ |
| Acceso directo a DB | ✅ |

---

### Tabla resumen ejecutiva

| Feature | BASIC | PRO | PREMIUM |
|---|---|---|---|
| **Precio** | **30€/mes** | **90€/mes** | **200€/mes** (o % profit) |
| **Wallets** | 1 | 3 | ∞ |
| **Budget max** | $1.000 | $10.000 | ∞ |
| **Posiciones max** | 3 | 8 | ∞ |
| **Modos** | SCALP+NORMAL+SWING | + auto-learning per user | + custom presets |
| **Schedule** | Basico (modo fijo o default) | Grid 7×24 editable | + simulador |
| **Config trading editable** | Esenciales (SL/TP/threshold/score) | TODOS los parametros | TODOS + experimentales |
| **Filtros** | Editables ON/OFF | Editables completos | + experimentales |
| **Coin profiles per user** | ❌ (compartidos) | ✅ Auto-learning | ✅ + Shadow mode |
| **Microstructure** | ❌ | Panel visible | Panel + alertas |
| **Bot dashboard** | Operacional | Completo | Completo |
| **Quantum Platform** | ❌ | Parcial (4 pestanas) | Completo (todas) |
| **Markers** | ❌ | Solo lectura | Crear manuales |
| **Histórico** | 30 dias | 90 dias | Ilimitado |
| **Multi-exchange** | ❌ | ❌ | ✅ (cuando exista) |
| **API access** | ❌ | ❌ | ✅ |
| **Webhooks custom** | ❌ | ❌ | ✅ |
| **Soporte** | FAQ + comunidad | Email | Chat directo |
| **Telegram alerts** | Basico | Avanzado | + webhooks |

---

### Pricing definitivo (Alchimiabot premium)

```
BASIC:     30€/mes
PRO:       90€/mes
PREMIUM:  200€/mes  (o 0€/mes + 20% del profit alternativo)
```

**No hay tier gratis.** Alchimiabot es producto premium dirigido a traders con capital real.

**Justificacion del posicionamiento premium:**

1. **Es un producto profesional**, no un juguete
2. **Edge real probado** (no es un wrapper de TradingView ni una copia open source)
3. **Soporte serio** y desarrollo activo
4. **Comparables del mercado:**
   - 3Commas Pro: 59€/mes (producto generico)
   - 3Commas Expert: 99€/mes
   - Cryptohopper Hero: 99€/mes
   - HaasOnline Beginner: 79€/mes
   - HaasOnline Simple: 199€/mes
   - HaasOnline Advanced: 999€/mes
   - **Alchimiabot esta en linea con productos premium del sector**

### Calculos de rentabilidad

#### Beta (10 amigos)

```
Si los 10 amigos pagan BASIC (30€):
  Revenue:  300€/mes
  Costes:    10€/mes (VPS pequeño)
  Margen:   290€/mes ✅
```

**Pero los amigos no pagan en beta** — son tu QA. La beta NO es para revenue, es para validar y pulir.

#### Realista (35 usuarios pagando, mix tipico)

Distribucion tipica de un producto SaaS premium:
- 60% BASIC = 21 usuarios × 30€ = **630€/mes**
- 30% PRO   = 10 usuarios × 90€ = **900€/mes**
- 10% PREMIUM = 4 usuarios × 200€ = **800€/mes**

```
Total revenue:  2.330€/mes
Costes infra:      50€/mes
Costes legal:     ~50€/mes (amortizado)
Margen mensual: ~2.230€/mes ✅
```

#### Target (100 usuarios)

Con la misma distribucion 60/30/10:
- 60 BASIC × 30€    = 1.800€/mes
- 30 PRO × 90€      = 2.700€/mes
- 10 PREMIUM × 200€ = 2.000€/mes

```
Total revenue:  6.500€/mes  (~78.000€/año)
Costes infra:     150€/mes
Costes operativos: 200€/mes
Margen mensual: ~6.150€/mes ✅
Margen anual:  ~73.000€/año
```

**Esto es un producto SaaS rentable.** Con 100 usuarios pagando, generas ~73k€/año limpio.

### Alternativas de pricing a considerar

#### Trial gratuito (recomendado anadir)

Sin tier gratis permanente, pero **trial de 7-14 dias** para que la gente pruebe:

```
Trial: 14 dias gratis con BASIC features
       (limit $500 budget para que no se desmadre)
       Despues: convertir a BASIC pagando o desactivar
```

**Por que:** sin trial, los precios premium asustan. Con trial, el usuario prueba el producto y ve el valor antes de pagar.

#### % del profit como opcion en PREMIUM

```
PREMIUM: 200€/mes flat
   O bien
PREMIUM: 0€/mes + 20% del profit mensual realizado
```

**Por que:** atrae a usuarios con mucho capital ($50k+) que no quieren riesgo de pagar 200€ sin garantias. Si el bot gana 1.000€ → tu cobras 200€ (igual que el flat). Si el bot pierde → tu no cobras nada (vs perder 200€).

**Aviso:** este modelo solo funciona si **confias en tu edge**. Si el bot pierde sistematicamente, no cobras nada.

#### Descuento anual

```
Mensual:  30€ / 90€ / 200€
Anual:    300€ / 900€ / 2.000€
          (equivale a 10 meses → 2 meses gratis)
```

**Por que:** mejora cashflow, reduce churn, hace que el usuario se comprometa.

#### Descuentos de lanzamiento (early adopters)

Para los primeros 20-50 usuarios despues de la beta cerrada:

```
Lifetime 50% off:
  BASIC:    15€/mes para siempre
  PRO:      45€/mes para siempre
  PREMIUM: 100€/mes para siempre
```

**Por que:** crear una base de usuarios fieles que tambien hagan marketing boca a boca.

### Lo que estos precios IMPLICAN (importante)

A 30/90/200€/mes, el producto **TIENE** que aportar valor real. No puede ser:
- ❌ Un wrapper de TradingView
- ❌ Un copy de un bot open source
- ❌ Algo que el usuario podria hacer en Excel

**Tiene que aportar:**
- ✅ Un edge real (que tu motor produce ganancias en condiciones normales de mercado)
- ✅ Una experiencia profesional pulida (UX, dashboards, métricas)
- ✅ Soporte serio (no puedes desaparecer 1 semana)
- ✅ Desarrollo activo (cambios visibles cada semana o mes)
- ✅ Documentacion completa
- ✅ Reliability alta (uptime > 99%)

**Es un compromiso operativo grande**. No puedes lanzar a estos precios y luego desentenderte. Cada usuario pagando 90-200€/mes espera **respuestas** y **mejoras**.

### Pre-requisitos antes de fijar estos precios

Para vender Alchimiabot a 30-200€/mes con honestidad necesitas tener:

1. ✅ **Track record demostrable** del bot ganando (al menos 3 meses positivos consistentes)
2. ✅ **UX pulida** (no errores, no glitches, no "esto esta en beta")
3. ✅ **Documentacion completa** (FAQ, guias, tutoriales)
4. ✅ **Soporte real** (canal de soporte que respondas en <24h)
5. ✅ **Status page publica** (uptime visible)
6. ✅ **Onboarding facil** (que un usuario pueda empezar en <30 min)
7. ✅ **Refund policy** (aunque sea solo para el primer mes)
8. ✅ **Asesoria legal** sobre el posicionamiento ("herramienta", no "promesa")

**Si NO tienes estas 8 cosas, los precios premium se vuelven en tu contra** porque generan expectativas que no puedes cumplir.

---

### Por que este diseno funciona comercialmente

1. **BASIC es funcional, no roto.** Un usuario novato puede tradear de verdad con BASIC. No es una version castrada, es una version simplificada.

2. **PRO es donde esta el dinero.** La mayoria de usuarios tecnicos van a PRO. Es el sweet spot 80/20.

3. **PREMIUM es para los que saben.** Pocos usuarios, alto revenue. Tambien funciona como "validacion social" — si hay traders pro usandolo, el producto se valida.

4. **Los upgrades son naturales:**
   - "Quiero editar el ATR multiplier" → upgrade a PRO
   - "Quiero ver el research del Quantum Platform" → upgrade a PREMIUM
   - "Quiero conectar tambien Binance" → upgrade a PREMIUM

5. **Es defendible legalmente:**
   - "Te vendemos una herramienta, no resultados"
   - "Tu eliges la configuracion"
   - "Tu wallet, tu dinero, tu decision"

### El "downgrade" path importa

Cuando un usuario PRO baja a BASIC (no paga):
- Sesion activa se para
- Mantiene wallet pero no puede anadir nuevas
- Mantiene historial pero solo ve ultimos 30 dias
- Sus configs avanzadas se pausan (no se borran)
- Sus coin profiles per-user se pausan (vuelve a usar los compartidos)
- Si vuelve a pagar, todo se reactiva

**No le borras nada**, solo le restringes el acceso. Es importante para que los usuarios sientan que pueden volver sin perder su trabajo.

### Implementacion

```sql
CREATE TABLE user_roles (
    user_id BIGINT PRIMARY KEY REFERENCES auth_users(id),
    role    VARCHAR(20) NOT NULL CHECK (role IN ('BASIC','PRO','PREMIUM','ADMIN')),
    granted_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE TABLE tier_limits (
    role          VARCHAR(20) PRIMARY KEY,
    max_wallets   INT NOT NULL,
    max_budget    NUMERIC(18,2) NOT NULL,
    max_positions INT NOT NULL,
    history_days  INT NOT NULL,
    features      JSONB NOT NULL  -- {"horario_avanzado":true,"microstructure":false,...}
);

-- Datos iniciales:
INSERT INTO tier_limits VALUES
  ('BASIC',   1, 1000,    3, 30,
   '{"schedule_grid":false,"microstructure":false,"coin_profiles_per_user":false,
     "shadow_mode":false,"quantum_full":false,"markers_create":false,
     "advanced_params":false,"api_access":false,"webhooks":false}'),
  ('PRO',     3, 10000,   8, 90,
   '{"schedule_grid":true,"microstructure":true,"coin_profiles_per_user":true,
     "shadow_mode":false,"quantum_full":false,"markers_create":false,
     "advanced_params":true,"api_access":false,"webhooks":false}'),
  ('PREMIUM', 999, 999999999, 999, 999999,
   '{"schedule_grid":true,"microstructure":true,"coin_profiles_per_user":true,
     "shadow_mode":true,"quantum_full":true,"markers_create":true,
     "advanced_params":true,"api_access":true,"webhooks":true}');
```
```

```java
// Annotation declarativa
@RequiresFeature(Feature.MICROSTRUCTURE)
@GetMapping("/api/hl/trading/microstructure")
public Map<String,Object> getMicro() { ... }

// Aspect que valida
@Aspect
public class FeatureGuardAspect {
    @Around("@annotation(requiresFeature)")
    public Object check(...) {
        if (!user.hasFeature(...)) throw new ForbiddenException();
        return pjp.proceed();
    }
}
```

```tsx
// Frontend
const { hasFeature } = useUser();

return (
  <>
    {hasFeature("MICROSTRUCTURE") && <MicroTab />}
    {!hasFeature("MICROSTRUCTURE") && <UpgradePrompt />}
  </>
);
```

---

## 8. Seguridad

### Threat model

| Amenaza | Mitigacion |
|---|---|
| **Leak de private keys de usuarios** | AES-256-GCM. Master key separada en `.env`. NUNCA en logs. NUNCA en backups. |
| **Cross-user data access** | TODA query SQL incluye `WHERE user_id = ?`. Code review obligatorio. Tests automaticos. |
| **JWT robo** | Cookie HttpOnly + Secure + SameSite=strict. Expiracion 1h. Refresh token. |
| **SQL injection** | JPA parametrizado siempre. Nunca concatenar SQL. |
| **Race condition entre sesiones** | Cada `TradingSession` accedida desde un thread (queue) o sync. |
| **Insider threat (operador)** | Audit log de accesos. Master key separada. |
| **Backups con secrets** | Master key NO en backups. Backups encriptados. |
| **Compliance / regulacion** | Asesoria legal antes de open beta. |
| **Rate limit abuse** | Por usuario y endpoint. Bloqueo temporal en abuso. |
| **Webhook spoofing PlatformBridge** | HMAC signature en payloads. |

### Cifrado

| Capa | Tecnica |
|---|---|
| **Algoritmo wallets** | AES-256-GCM (autenticado, sin tampering) |
| **Master key** | `WALLET_MASTER_KEY` en `.env` (32 bytes base64) |
| **IV** | Aleatorio 12 bytes per wallet |
| **AAD** | `userId|walletId` (rebind impossible) |
| **Rotacion** | `encryption_version` permite re-encrypt sin downtime |
| **Decrypt timing** | Solo en memoria, justo antes de firmar tx |

### Donde vive cada secreto

| Secreto | Donde |
|---|---|
| `WALLET_MASTER_KEY` | `.env` del bot, NO en git, NO en backups |
| `JWT_SECRET` | `.env` del bot |
| `DB_PASSWORD` | `.env` |
| `TELEGRAM_BOT_TOKEN` | `.env` |
| Wallets de usuarios | `user_wallets`, encriptadas con master |

---

## 8b. Estrategia de aprendizaje colectivo (el "alma" del proyecto)

### El concepto

Hoy el bot aprende **per usuario** (CoinProfileOptimizer). Maniana debe aprender **cross-user**.

```
NIVEL 1 — Per usuario (HOY)
─────────────────────────────
CoinProfileOptimizer ya hace esto:
- (user_id, coin, side, mode) → stats EW → ajustes propios
- Cada usuario tiene SU vision del coin
- Lock automatico en perdidas
- Auto-tuning de SL/TP/cooldown

NIVEL 2 — Cross usuario (FUTURO, pero diseñado desde Fase 1)
─────────────────────────────
GlobalLearningService:
- Agrega resultados de TODOS los usuarios anonimamente
- Compara configs vs PnL real
- Calcula "que config funciona mejor" para cada (coin, mode, regime)
- Publica "best practices" en el Quantum Platform
- BASIC users pueden importar "config recomendada del sistema"
- PRO/PREMIUM users contribuyen al aprendizaje y reciben sugerencias
```

### Como funciona el GlobalLearningService

```sql
-- Vista materializada con stats agregadas anonimas
CREATE MATERIALIZED VIEW global_config_performance AS
SELECT
    config_snapshot->>'mode' AS mode,
    coin,
    side,
    -- Buckets de configs (para no dispersar demasiado)
    width_bucket((config_snapshot->>'stopLossPct')::numeric, 0.10, 0.80, 14) AS sl_bucket,
    width_bucket((config_snapshot->>'takeProfitPct')::numeric, 0.10, 2.00, 19) AS tp_bucket,
    width_bucket((config_snapshot->>'minScoreTotal')::numeric, 40, 80, 8) AS score_bucket,
    -- Metricas agregadas
    COUNT(*) AS sample_size,
    AVG(net_pnl) AS avg_pnl,
    AVG(mfe_pct) AS avg_mfe,
    AVG(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS win_rate,
    STDDEV(net_pnl) AS pnl_stddev,
    COUNT(DISTINCT user_id) AS unique_users
FROM hl_trading_history
WHERE entry_at >= NOW() - INTERVAL '30 days'
GROUP BY mode, coin, side, sl_bucket, tp_bucket, score_bucket
HAVING COUNT(*) >= 20;  -- minimo 20 trades para considerar valido

-- Refresh diario
REFRESH MATERIALIZED VIEW CONCURRENTLY global_config_performance;
```

**Resultado:** una tabla con `(mode, coin, side, SL bucket, TP bucket, score bucket) → avg_pnl, win_rate, n`.

**Querys utiles:**

```sql
-- "¿Cual es la mejor config global para SCALP en SOL LONG?"
SELECT * FROM global_config_performance
WHERE mode = 'SCALP' AND coin = 'SOL' AND side = 'LONG'
  AND sample_size >= 30
  AND unique_users >= 3
ORDER BY avg_pnl DESC LIMIT 5;

-- "¿Que config esta perdiendo dinero consistentemente?"
SELECT * FROM global_config_performance
WHERE avg_pnl < -0.20 AND sample_size >= 50
ORDER BY avg_pnl ASC;
```

### Privacidad y anonimato

**Critico:** los datos cruzados son anonimos.
- No se guarda quien tradeo que (en la vista materializada)
- Solo agregados con sample_size minimo (>=20 trades, >=3 usuarios)
- Imposible re-identificar a un usuario por sus resultados
- El usuario puede optar por **no contribuir** al pool global (opt-out)

### Fases del aprendizaje colectivo

| Fase | Cuando | Que hace |
|---|---|---|
| **Fase A — Recopilacion** | Beta cerrada (10 amigos) | Solo recopilar datos. No actuar. |
| **Fase B — Insights** | 20+ usuarios | Mostrar en Quantum Platform "estos parametros parecen funcionar mejor" |
| **Fase C — Recomendaciones** | 50+ usuarios | Usuario BASIC puede importar "config recomendada del sistema" con 1 click |
| **Fase D — Auto-aplicacion** | 100+ usuarios | Optimizer per-user **incorpora** sugerencias del global como prior bayesiano |

**Por que esto es brutal:**
- Cada usuario contribuye al sistema sin saberlo
- El sistema mejora **continuamente** sin que tu hagas nada
- Los nuevos usuarios entran y reciben **el conocimiento acumulado** de toda la comunidad
- Es **el unico bot del mercado** que aprende asi a esta escala (3Commas, Cryptohopper, Hummingbot NO hacen esto)

### Esto justifica TOTALMENTE los precios premium

A 30/90/200€/mes, un usuario no esta pagando solo "un bot". Esta pagando:
- Acceso a un sistema que **mejora cada dia**
- Beneficiarse del aprendizaje de **N usuarios** sin que cuente como custodia
- Una **comunidad de inteligencia** donde su data contribuye anonimamente

**Network effect real:** mientras mas usuarios, mejor el sistema, mas valor por usuario.

---

## 9. Plan por fases

### Fase 0 — Preparacion y cleanup (1-2 semanas)

**Sin tocar el motor todavia. Solo dejar la base lista.**

- [ ] Decidir las 10 decisiones pendientes (seccion 3)
- [ ] Asesoria legal (reunion 1h)
- [ ] Backup completo de DB actual
- [ ] Migration V27: drop de 13 tablas legacy
- [ ] TTLs cron job (feature_snapshots, signal_evaluations, hl_alerts)
- [ ] Cambio en bot: filtrar signals enviados al platform (solo ENTER/BLOCKED + sample)
- [ ] Tests: cobertura >70% del flujo critico actual
- [ ] Documentar arquitectura actual para referencia

**Riesgo:** ✅ Bajo. El sistema sigue funcionando como single-tenant.

### Fase 1 — Multi-tenant basico schema + engine (2-3 semanas)

**El bloque grande. Donde esta el riesgo.**

- [ ] Migration V28: anadir `user_id BIGINT NULL` a todas las tablas core
- [ ] Backfill: `UPDATE ... SET user_id = (SELECT id FROM auth_users WHERE username='chema200')`
- [ ] Make `user_id NOT NULL`
- [ ] Indices compuestos (user_id, ...)
- [ ] Crear `user_wallets` con cifrado
- [ ] `WalletService` + `SecretsService` + `ExchangeClientFactory`
- [ ] **Refactor `HlDirectionalEngine`** → extraer estado a `TradingSession`
- [ ] Crear `SessionManager` con `Map<UserId, TradingSession>`
- [ ] Crear `MarketDataService` extraido (singleton compartido)
- [ ] Endpoints scoped por `user_id` del JWT
- [ ] Frontend: muestra solo data del usuario logueado
- [ ] Tests: 5 usuarios paralelos sin contaminacion

**Riesgo:** 🔴 Alto. Es donde se rompen cosas. **Hacer en branch + feature flag.**

**Entrega:** beta privada con 2-3 usuarios reales.

### Fase 2 — Wallets multiples + UI onboarding (1-2 semanas)

- [ ] UI "Conectar wallet HL" con modal y tutorial
- [ ] Validacion read-only call al guardar
- [ ] Test withdrawal dummy (debe fallar) → confirma permisos
- [ ] Selector de wallet activa en el header
- [ ] Cambio de wallet activa = stop session + start nueva
- [ ] Ver posiciones reales en HL desde la UI

**Riesgo:** 🟢 Bajo. Es UI sobre la base ya construida.

**Entrega:** beta cerrada con 5-10 amigos.

### Fase 3 — Sistema de roles + tiers (1 semana)

- [ ] Tabla `user_roles` + `tier_limits`
- [ ] `Role` y `Feature` enums en backend
- [ ] `@RequiresFeature` annotation + aspect
- [ ] Frontend con `useUser().hasFeature(...)`
- [ ] Pestanas condicionales por rol
- [ ] Tier limits enforcement (max positions, budget, etc)
- [ ] Admin panel basico (asignar roles)
- [ ] UI de "upgrade" para features bloqueadas

**Riesgo:** 🟡 Medio. Cuidado con bypasear feature flags.

### Fase 4 — Refactor de datos masivos (1-2 semanas)

**Solo cuando empiezas a notar el volumen (>20 users activos).**

- [ ] Crear `trade_snapshots_agg`
- [ ] En bot: lista en memoria de snapshots por trade
- [ ] Calculo de agregados al cerrar trade
- [ ] Enviar 1 row al platform en lugar de 200
- [ ] Decommission de `feature_snapshots` (mantener 30 dias por seguridad)
- [ ] Drop de `feature_snapshots` cuando confirme

**Riesgo:** 🟡 Medio. Es un cambio importante en el bridge bot↔platform.

### Fase 5 — Escalado para 100 users (2-4 semanas)

**Cuando pases de 30-50 usuarios reales.**

- [ ] `parallelStream` con pool dimensionado
- [ ] DB connection pool tuning
- [ ] Particionado de `hl_trading_history` (si crece >10M filas)
- [ ] Prometheus + Grafana con metricas per user
- [ ] Alertas: heap, threads, latencia, errores per user
- [ ] Load testing: simular 100 users concurrentes
- [ ] Auto-restart healthcheck
- [ ] Backup automatico de DB con cifrado a S3/B2
- [ ] Status page publico

**Riesgo:** 🟡 Medio. Depende del servidor.

### Fase 6 (futuro) — Multi-exchange

- [ ] `BinanceClient implements ExchangeClient`
- [ ] Symbol mapper HL ↔ Binance
- [ ] Adaptar gates por exchange
- [ ] UI: usuario puede tener wallet HL + wallet Binance

**Riesgo:** 🟡 Medio. Trabajo aislado.

### Total estimado

| Fase | Duracion | Prioridad |
|---|---|---|
| 0 — Preparacion | 1-2 sem | Critico |
| 1 — Multi-tenant + engine | 2-3 sem | Critico |
| 2 — Wallets + UI | 1-2 sem | Critico |
| 3 — Roles | 1 sem | Importante |
| 4 — Refactor datos | 1-2 sem | Importante (a partir de 30 users) |
| 5 — Escalado | 2-4 sem | Cuando lo necesites |
| 6 — Multi-exchange | 2-3 sem | Futuro |
| **TOTAL realista** | **9-15 semanas** | **~3 meses** |

---

## 10. Riesgos

### Tecnicos

| # | Riesgo | Severidad | Mitigacion |
|---|---|---|---|
| 1 | **Refactor del motor introduce bugs** | 🔴 Alto | Tests exhaustivos. Feature flag. Branch separada. Rollback plan. |
| 2 | **State leak entre sesiones** | 🔴 Alto | Code review estricto. Tests automaticos con 10 users paralelos. |
| 3 | Memory leak con sesiones long-running | 🟡 Medio | Heap monitoring. Cleanup periodico. Limites en colecciones. |
| 4 | HL API rate limits con muchos users | 🟡 Medio | Shared market data. Backoff exponencial. |
| 5 | Race condition en order execution | 🟡 Medio | Queue per user, no paralelo masivo dentro de wallet. |
| 6 | Crash de JVM mata 100 usuarios | 🟡 Medio | Healthcheck + auto-restart. State recovery. |

### Seguridad

| # | Riesgo | Severidad | Mitigacion |
|---|---|---|---|
| 1 | **Leak de private keys** | 🔴 Critico | Cifrado obligatorio. Code review. Static analysis. |
| 2 | **Cross-user data access** | 🔴 Critico | Tests. Row-level security en DB (PostgreSQL RLS). |
| 3 | JWT theft | 🟡 Medio | HttpOnly + Secure. Expiracion corta. |
| 4 | Insider threat | 🟡 Medio | Audit log. Master key en HSM/separada. |
| 5 | Backups con secrets | 🔴 Alto | Master key NO en backups. Backups encriptados. |
| 6 | Compliance / legal | 🟡 Medio | Modelo non-custodial reduce mucho. Pero **asesoria legal recomendada**. |

### Operacionales

| # | Riesgo | Severidad | Mitigacion |
|---|---|---|---|
| 1 | Soporte tecnico 1:N | 🟡 Medio | Self-service docs. Status page. FAQ. |
| 2 | Bugs visibles a 100 users a la vez | 🟡 Medio | Staging. Canary deploys. Feature flags. |
| 3 | Onboarding complejo | 🟡 Medio | Tutorial visual. Validacion en tiempo real. |
| 4 | Usuarios pierden dinero y echan culpa | 🔴 Alto | **Disclaimer legal obligatorio**. Documentar limitaciones. |

---

## 11. Costes estimados

### Beta (10 amigos, 1-3 meses)

```
Infraestructura
─────────────────
VPS 4GB / 2 cores / 50GB     10€/mes
Dominio (ya tienes)            -
Cloudflare (free tier)         -
Total infra:                  10€/mes

Dev: tu tiempo
Total monetario beta:         ~10€/mes
```

### Escalado a 50 usuarios

```
Infraestructura
─────────────────
VPS 8GB / 4 cores / 100GB     30-40€/mes
Backups S3/B2                  5-10€/mes
Monitoring (Grafana cloud)     0-10€/mes
Total infra:                  ~50€/mes
```

### Produccion 100 usuarios

```
Infraestructura
─────────────────
Servidor 32GB / 8c / 1TB SSD  80-150€/mes
(VPS dedicado o bare metal)
Backups con cifrado            10-20€/mes
Monitoring                     0-30€/mes
Email transaccional             5€/mes
Total infra:                  100-200€/mes
```

### Costes one-off

```
Asesoria legal inicial         200-500€
Auditoria de seguridad          0-1000€ (opcional)
Logo / branding (si quieres)   0-200€
```

### Resumen

> **Para llegar a 100 usuarios solo necesitas ~150€/mes en infraestructura.**
>
> Si cobras solo 5€/mes por usuario PRO/PREMIUM, **rentable con 30 usuarios pagando**.
> Si cobras 10€/mes, **rentable con 15 usuarios pagando**.

---

## 12. Documentos relacionados

| Documento | Que contiene |
|---|---|
| [`MULTITENANT_ARCHITECTURE.md`](./MULTITENANT_ARCHITECTURE.md) | Diseno tecnico detallado del refactor del engine, sessions, market data |
| [`MULTITENANT_DATA_STRATEGY.md`](./MULTITENANT_DATA_STRATEGY.md) | Analisis profundo de tablas, volumenes, optimizaciones, TTLs |
| [`CHANGE_MARKERS_GUIDE.md`](./CHANGE_MARKERS_GUIDE.md) | Sistema de markers para medir impacto de cambios (V25) |
| [`PLATFORM_GUIDE.md`](./PLATFORM_GUIDE.md) | Documentacion del platform actual |

---

## 13. La conversacion completa de hoy en sintesis

### Lo que descubrimos

1. **Tu bot funciona y aprende.** Ya hicimos:
   - V25 SL viability filter (parametrizado por modo)
   - V26 schedule semanal (7×24 cells, sin hardcode del weekend)
   - Trailing distance fix en SCALP (0.35 → 0.12)
   - SL viability filter exposed via gate stats al platform
   - Markers automaticos en MODE_CHANGE, PRESET_EDIT, PROTECTION, PROPOSAL_ACCEPTED

2. **El sistema esta sano tecnicamente** pero **tiene problemas reales** en mercado lateral:
   - Slippage del SL (4.8x en algunos casos)
   - Late entries
   - Mercado lateral
   - Optimizer demasiado conservador (necesita 10 trades para ajustar TP/SL)

3. **Trades reales analizados hoy:**
   - APT SHORT cerro a +$0.48 cuando llego a +$1.50 en peak (slippage del cierre)
   - INJ LONG cerro perfectamente con +$0.83 (partial close + ROI)
   - RENDER SHORT cerro con +$0.35 (ROI_0.20)
   - 3 winners totales del dia en SCALP post-V25/V26

4. **El tema legal:** non-custodial via API Wallets es la unica via correcta. NUNCA pides la private key principal del usuario. **Hyperliquid soporta esto nativamente.**

5. **Lo que mata el sistema:** `feature_snapshots` con 134k inserts/dia/user. × 100 = 2.5 TB. **El refactor de esto es el cambio mas critico para escalar.**

### Lo que decidimos

1. ✅ Multi-tenant via Hibrido B (single JVM, sessions per user, market data shared)
2. ✅ Solo Hyperliquid en Fase 1
3. ✅ Non-custodial via API Wallets de HL
4. ✅ Beta cerrada con 10 amigos primero
5. ✅ Target final 100 usuarios
6. ✅ Producto se llama Alchimiabot
7. ✅ Refactor incremental, no rewrite
8. ✅ Tiempo no es problema (3-4 meses)
9. ✅ Cleanup de tablas legacy en V27 antes de empezar
10. ✅ Refactor de `feature_snapshots` → `trade_snapshots_agg` antes de pasar de 30 users

### Lo que NO empezamos

**Modo recopilacion. NADA implementado todavia.** Este documento es la fuente de verdad para cuando decidas arrancar.

---

## 14. Checklist antes de empezar a implementar

Cuando estes listo para arrancar la Fase 0:

- [ ] Has leido este documento entero
- [ ] Has tomado las 10 decisiones de la seccion 3
- [ ] Has hablado con un abogado de fintech (~1h, ~200€)
- [ ] Tienes backup completo de DB actual
- [ ] Has identificado 2-3 amigos para beta privada inicial
- [ ] Sabes que vas a hacer cuando se rompa algo
- [ ] Has definido tu modelo de negocio
- [ ] Tienes claro que esto es un proyecto de 3-4 meses

---

## 15. Notas finales

> Este documento NO se autodestruye. Es la **fuente de verdad** para Alchimiabot multi-tenant.
> Actualizar conforme tomes decisiones y descubras cosas nuevas.

**Filosofia:**
- KISS: PostgreSQL + 1 servidor + buenos indices = aguanta 100 users
- No reinventar: usar lo que ya funciona en otros bots (API wallets)
- Incremental: refactor en branches, feature flags, rollback siempre posible
- Honesto: el riesgo principal es legal, no tecnico

**El mantra correcto del proyecto:**

> **El bot no es malo. Lo que falta es el conocimiento de los parametros optimos.**
> **Y ese conocimiento solo se descubre con muchos usuarios probando configs en paralelo.**
>
> Por eso multi-tenant NO es "para vender mas" sino **"para aprender mas rapido"**.
> El single-user NO escala el aprendizaje. Multi-user SI.
>
> El plan no es: "primero rentable, luego multi-tenant".
> El plan es: **"multi-tenant PORQUE single-tenant no me hace rentable nunca"**.

**El verdadero edge del producto:** no es el motor (otros tienen motores), es **el aprendizaje colectivo** que solo funciona con muchos usuarios.

**Network effect real:** mas usuarios → mas data → mejores configs → mas valor → mas usuarios.

---

**Fin del documento maestro. Modo recopilacion activo. Cero implementacion.**
