# Alchimia Bot — Multi-Tenant Architecture Design

> Documento de diseno (NO implementacion) para evolucionar `agentbot` + `agentbot-platform`
> de single-tenant (1 bot, 1 wallet) a una plataforma multi-usuario con roles,
> wallets independientes y capacidad para 50-100 usuarios concurrentes.

**Version:** 1.0
**Fecha:** 2026-04-10
**Estado:** Propuesta (no implementada)

---

## Resumen ejecutivo (TL;DR)

| Aspecto | Estado actual | Propuesta |
|---|---|---|
| Tenancy | Single (1 user) | Multi-tenant (50-100) |
| Engine | Singleton Spring (`HlDirectionalEngine`) | Service stateless + `Map<UserId, TradingSession>` |
| Wallet | 1 hardcoded en `.env` | Tabla `user_wallets`, encriptada AES-GCM |
| Market data | 1 cliente, 1 fetch | **Shared MarketDataService** (1 fetch para todos) |
| DB | Sin `user_id` en tablas core | `user_id` en cada tabla, indices + particionado |
| Roles | No existen | BASIC / PRO / PREMIUM con feature flags |
| Frontend | 1 sesion, 1 config visible | Sesion por usuario, datos aislados |
| Migracion | - | 4 fases incrementales sin downtime |

**Approach recomendado:** **Hibrido B con pattern MasterScanner + UserSession.**

Una pieza compartida (datos de mercado) + N piezas por usuario (logica + estado + ejecucion).
Eficiente, escalable y minimiza el cambio respecto al motor actual.

---

## Tabla de contenidos

1. [Estado actual](#1-estado-actual)
2. [Casos de uso a soportar](#2-casos-de-uso-a-soportar)
3. [Aproximaciones evaluadas](#3-aproximaciones-evaluadas)
4. [Arquitectura recomendada](#4-arquitectura-recomendada)
5. [Refactor del motor de trading](#5-refactor-del-motor-de-trading)
6. [Gestion de wallets](#6-gestion-de-wallets)
7. [Escalabilidad](#7-escalabilidad)
8. [Cambios en base de datos](#8-cambios-en-base-de-datos)
9. [Cambios en platform (front + back)](#9-cambios-en-platform)
10. [Seguridad](#10-seguridad)
11. [Sistema de roles](#11-sistema-de-roles)
12. [Observabilidad](#12-observabilidad)
13. [Riesgos](#13-riesgos)
14. [Plan de migracion por fases](#14-plan-de-migracion-por-fases)
15. [Decisiones abiertas](#15-decisiones-abiertas)

---

## 1. Estado actual

### Stack
- **Bot:** Java 21 + Spring Boot 3.3, JAR monolitico
- **Frontend bot:** Next.js 14 (`/agentbot/front`)
- **Platform:** Python (FastAPI) + dashboard estatico (`/agentbot-platform`)
- **DB bot:** PostgreSQL 16 (Flyway, V1-V26)
- **DB platform:** PostgreSQL 16 separada (Alembic)

### Single-tenant key facts

| Componente | Singleton/global hoy | Implicacion |
|---|---|---|
| `HlDirectionalEngine` | `@Component` Spring, 1 instancia | Mantiene `openPositions`, `closedPositions`, `priceHistory`, `cooldowns`, `usedBudget`, `gateStats`... como **estado de instancia** |
| `HlDirectionalConfig` | `@ConfigurationProperties` global | 1 sola config viva en memoria |
| `HyperliquidApiClient` | 1 instancia, lee `HL_PRIVATE_KEY` de `.env` | 1 sola wallet posible |
| `CoinProfileOptimizer` | Singleton, escribe a `hl_coin_profiles` global | Sin contexto de usuario |
| `PlatformBridge` | Singleton, 1 endpoint platform | OK pero envia sin `user_id` |
| Scheduler | 1 thread pool fijo (`scheduleAtFixedRate`) | 1 ciclo cada N segs para todos |
| Tablas DB | Sin `user_id` (excepto `auth_users` V18) | Toda la historia es del unico usuario |
| Logs | 1 archivo `hyperliquid.log` global | Sin separacion por usuario |
| `HlMarketScanner` | Singleton, lista de coins compartida | OK, candidato a compartir |

### Lo que ya esta listo para multi-tenant
- `auth_users` (V18) — JWT funcional, base para users
- `change_markers` (V25) — tiene `source` (USER/BOT_AUTO), puede extenderse con `user_id`
- `hl_mode_schedule_weekly` (V26) — facil añadir `user_id`
- Frontend con login, JWT en cookie

### Lo que NO esta listo
- Engine: estado mezclado, no separable sin refactor
- Wallet: hardcoded en `.env`
- Tablas: sin `user_id`
- Logs: sin contexto de usuario
- API client: 1 wallet
- Frontend: muestra "el bot", no "tu bot"

---

## 2. Casos de uso a soportar

### Funcionales
| # | Caso | Implica |
|---|---|---|
| 1 | Usuario A y B operando en HL a la vez | Aislamiento de positions, fills, PnL |
| 2 | Usuario A en SCALP, B en SWING | Config independiente |
| 3 | Usuario A con $100, B con $10k | Budget independiente |
| 4 | Riesgo conservador vs agresivo | Presets independientes |
| 5 | Distintos exchanges (futuro) | Exchange abstraction layer |
| 6 | Aislamiento total de history y senales | DB con `user_id` en todo |

### No-funcionales
- Escalabilidad: 50-100 usuarios concurrentes en 1 servidor mid-range
- Latencia: <100ms desde signal a orden enviada (similar al actual)
- Resilencia: crash de 1 sesion no debe afectar a las otras
- Seguridad: cero leak de credenciales, cero cross-user data
- Compliance: logs auditables, trazabilidad por usuario

---

## 3. Aproximaciones evaluadas

### Opcion A — Process per user (container-per-tenant)

**Idea:** cada usuario tiene su propia instancia JVM (Docker container o systemd service).

**Pros:**
- Aislamiento total y trivial — un crash no afecta a otros
- Seguridad maxima — la wallet vive en su propio proceso
- Migracion facil — el codigo actual no cambia, solo se replica
- Limites OS (cgroup, ulimits) faciles de aplicar
- Update rolling per-user

**Cons:**
- **Coste de recursos brutal** — 100 JVMs = 100 × 500MB = 50 GB RAM minimo
- 100 conexiones DB simultaneas (pool por proceso)
- 100 connections HL API
- 100 instancias del MarketScanner — duplicacion total
- **No comparte market data** — 30 coins × 100 procesos × 3s polling = 1000 calls/seg a HL
- Hyperliquid rate limits: ~1200 weight/min total → **rebasaria los limits con <20 usuarios**
- Coordinacion compleja (descubrimiento de procesos, balanceo)
- Operaciones (deploy, monitoring) por proceso

**Veredicto:** ❌ **Descartada.** Solo viable hasta 5-10 usuarios. No escala a 100.

---

### Opcion B — Single JVM, engine instance per user

**Idea:** una sola JVM, pero dentro un `Map<UserId, TradingSession>` donde cada sesion tiene su propio estado, config y wallet client.

**Pros:**
- Recursos compartidos (heap, classloader, threads, connection pool)
- **Market data se puede compartir** (1 fetch para todos)
- Refactor moderado (extraer estado del engine actual)
- Operacion sencilla (1 proceso, 1 deploy)
- Spring beans sirven como factories de sesiones
- Una sesion crashea → no tira el resto (con buen aislamiento de excepciones)

**Cons:**
- Refactor invasivo del `HlDirectionalEngine` (es donde mas codigo hay)
- Complejidad de threading: hay que coordinar N sesiones en pocos threads
- Blast radius: bug en el engine afecta a todos los usuarios
- Memory leak en una sesion afecta al heap global
- Spring lifecycle requiere un `SessionManager` cuidadoso

**Veredicto:** ✅ **Recomendada.** Es el punto medio optimo entre aislamiento y eficiencia.

---

### Opcion C — Single engine, user context per call

**Idea:** mantener el engine como singleton actual, pero pasar `userId` como parametro en cada metodo. El engine decide que data leer/escribir basandose en `userId`.

**Pros:**
- Cambio minimo en codigo (anadir parametro)
- Sin refactor estructural
- Eficiencia maxima

**Cons:**
- **Catastrofico para concurrencia.** El estado actual del engine son `List`s y `Map`s mutables. Compartirlos entre usuarios = race conditions y contaminacion de posiciones.
- Imposible aislar fallos
- Imposible logging por usuario sin MDC complejo
- Hace el test casi imposible
- Cualquier bug = leak entre usuarios

**Veredicto:** ❌ **Descartada.** Es lo que parece mas facil pero es lo mas peligroso.

---

### Opcion D — Hibrido B + worker threads dedicados

**Idea:** una variante de B donde cada `TradingSession` tiene su propio thread scheduler (no thread pool compartido).

**Pros sobre B puro:**
- Aislamiento de timing (un usuario lento no bloquea a otros)
- Mas facil debuggear (1 thread = 1 usuario)

**Cons sobre B puro:**
- 100 threads dedicados son demasiado para muchos kernels
- Context switching overhead

**Veredicto:** 🟡 **Considerar para Fase 4** si la carga lo justifica. Por defecto, B con thread pool compartido.

---

## 4. Arquitectura recomendada

### Diagrama logico

```
                   ┌──────────────────────────────────────┐
                   │     LOAD BALANCER / API GATEWAY      │
                   │    (Cloudflare Tunnel + Nginx)       │
                   └──────────────────┬───────────────────┘
                                      │
                ┌─────────────────────┴─────────────────────┐
                │                                           │
        ┌───────▼─────────┐                       ┌────────▼─────────┐
        │  Bot Backend    │                       │  Platform        │
        │  (Spring Boot)  │ ◄── PlatformBridge ──►│  (FastAPI)       │
        │                 │                       │  (Python)        │
        └───────┬─────────┘                       └────────┬─────────┘
                │                                          │
        ┌───────▼─────────┐                                │
        │ AuthService     │  ← JWT, roles, feature flags   │
        └───────┬─────────┘                                │
                │                                          │
        ┌───────▼─────────────────────────────────────┐    │
        │           SessionManager                    │    │
        │  (Map<UserId, TradingSession>)              │    │
        │                                             │    │
        │  ┌──────────┐  ┌──────────┐  ┌──────────┐  │    │
        │  │Session A │  │Session B │  │Session C │  │    │
        │  │ config A │  │ config B │  │ config C │  │    │
        │  │ positions│  │ positions│  │ positions│  │    │
        │  │ wallet A │  │ wallet B │  │ wallet C │  │    │
        │  └────┬─────┘  └────┬─────┘  └────┬─────┘  │    │
        │       │             │             │        │    │
        └───────┼─────────────┼─────────────┼────────┘    │
                │             │             │             │
        ┌───────▼─────────────▼─────────────▼─────────┐   │
        │      MarketDataService (SHARED)             │   │
        │  - 1 fetch de precios cada 3s               │   │
        │  - 1 trend service                          │   │
        │  - 1 microstructure WS                      │   │
        │  - sirve a todas las sessions               │   │
        └───────┬─────────────────────────────────────┘   │
                │                                          │
        ┌───────▼──────┐                                   │
        │ HL Public API│  (1 sola conexion para todos)     │
        └──────────────┘                                   │
                                                           │
        ┌─────────────────────────────────────────┐        │
        │   ExchangeClientFactory (per session)   │        │
        │   ┌────────────┐ ┌────────────┐ ┌────┐  │        │
        │   │HL Client A │ │HL Client B │ │... │  │ ←── execucion privada
        │   │ wallet A   │ │ wallet B   │ │    │  │       (1 client por wallet)
        │   └────────────┘ └────────────┘ └────┘  │        │
        └─────────────────────────────────────────┘        │
                │                                          │
        ┌───────▼──────────────────────────────────┐       │
        │       PostgreSQL (multi-tenant)          │ ◄─────┘
        │  - Todas las tablas con user_id          │
        │  - Indexes por user_id                   │
        │  - Particion por user_id (opcional)      │
        └──────────────────────────────────────────┘
```

### Componentes clave nuevos

| Componente | Responsabilidad | Aislamiento |
|---|---|---|
| **SessionManager** | Crea/destruye `TradingSession` por usuario, expone APIs scoped | Por usuario |
| **TradingSession** | Encapsula todo el estado mutable que hoy esta en `HlDirectionalEngine`: positions, config, gateStats, wallet client, profile cache | Por usuario (instancia dedicada) |
| **MarketDataService** | Polling de precios, trend service, microstructure → cache compartida | **Compartido** (singleton) |
| **ExchangeClientFactory** | Crea `HlClient`, `PolymarketClient`, etc desde credenciales del wallet | Por wallet |
| **WalletService** | CRUD de wallets, cifrado/descifrado, validacion | Global |
| **SessionScheduler** | 1 scheduler global que itera `Map<UserId, TradingSession>` y ejecuta el ciclo de cada uno | Global con per-user execution |
| **TenantContext** | Thread-local con `userId` actual para logging/queries | Por request thread |

---

## 5. Refactor del motor de trading

### El reto

`HlDirectionalEngine` (3053 lineas) es el componente con mas estado. Hoy todo es estado de instancia:

```java
// Estado global compartido (debe ser COMPARTIDO entre usuarios — market data)
private final Map<String, List<PricePoint>> priceHistory;  // ← share this

// Estado del usuario (debe ser POR USUARIO — trading state)
@Getter private final List<Position> openPositions;        // ← per user
@Getter private final List<Position> closedPositions;      // ← per user
private final Map<String, Instant> cooldowns;              // ← per user
@Getter private volatile double usedBudget = 0;            // ← per user
@Getter private volatile double totalRealizedPnl = 0;      // ← per user
private volatile int totalEntries = 0;                     // ← per user
private final GateStats gateStats;                          // ← per user
```

### Estrategia: Extract Class + Stateless Service

#### Paso 1: Crear `TradingSession` (POJO con todo el estado por usuario)

```java
public class TradingSession {
    private final long userId;
    private final long walletId;
    private final HlDirectionalConfig config;        // copia escalada del config
    private final ExchangeClient exchangeClient;     // cliente con la wallet del user

    // Trading state (mutable, per user)
    private final List<Position> openPositions;
    private final List<Position> closedPositions;
    private final Map<String, Instant> cooldowns;
    private double usedBudget;
    private double totalRealizedPnl;
    private double totalFees;
    private int totalEntries, totalTp, totalSl, totalTimeout;
    private GateStats gateStats;

    // Lifecycle
    private Instant startedAt;
    private String status; // RUNNING / STOPPED / PAUSED
    private boolean manualModeOverride;
}
```

**Lo que NO va dentro:** `priceHistory`, `trendService`, `marketScanner` — son compartidos.

#### Paso 2: `HlDirectionalEngine` se convierte en service stateless

```java
@Service
public class HlDirectionalEngine {
    // Inyectar lo compartido
    private final MarketDataService marketData;     // shared
    private final TrendService trendService;         // shared
    private final CoinProfileService profileService; // shared (con user_id en queries)

    // Metodos toman TradingSession como parametro
    public void runCycle(TradingSession session) {
        if (!session.isRunning()) return;
        updatePricesIntoSession(session);
        checkExits(session);
        if (session.openPositions().size() < session.config().getMaxPositions()) {
            scanForEntries(session);
        }
        // ... etc
    }

    public Position openPosition(TradingSession session, String coin, String side, ...) {
        // Usa session.exchangeClient() para placeIocOrder
        // Modifica session.openPositions()
        // Persiste en DB con user_id = session.userId()
    }
}
```

#### Paso 3: `SessionManager` orquesta

```java
@Service
public class SessionManager {
    private final Map<Long, TradingSession> sessions = new ConcurrentHashMap<>();
    private final HlDirectionalEngine engine;
    private final WalletService walletService;
    private final ScheduledExecutorService scheduler;

    public TradingSession startSession(long userId, long walletId, double budget) {
        if (sessions.containsKey(userId)) throw new AlreadyRunningException();

        var wallet = walletService.getWallet(userId, walletId);
        var client = ExchangeClientFactory.create(wallet); // decrypt + create client
        var config = configService.loadForUser(userId);

        var session = new TradingSession(userId, walletId, config, client, budget);
        sessions.put(userId, session);
        return session;
    }

    public void stopSession(long userId) {
        var s = sessions.remove(userId);
        if (s != null) engine.gracefulStop(s); // close positions, save state
    }

    @Scheduled(fixedDelay = 3000)
    public void runAllSessions() {
        sessions.values().parallelStream().forEach(session -> {
            try {
                engine.runCycle(session);
            } catch (Exception e) {
                log.error("[SESSION_ERROR] userId={}", session.userId(), e);
                // No mata las otras sesiones
            }
        });
    }
}
```

### Volumen del refactor (estimacion)

| Tarea | LOC modificadas |
|---|---|
| Extraer `TradingSession` | +500 |
| Engine: anadir `TradingSession` parameter | ~3053 (toca casi todo) |
| `SessionManager` nuevo | +300 |
| `MarketDataService` extraido | +400 |
| `ExchangeClientFactory` | +200 |
| Tests | +1000 |
| **Total** | **~5000-6000 LOC tocadas** |

**Esfuerzo:** 2-3 semanas a tiempo completo. **Es la pieza mas grande del proyecto.**

### Anti-patterns a evitar

| ❌ Mal | ✅ Bien |
|---|---|
| Pasar `userId` como string global | `TenantContext` thread-local + parameter |
| `Map<String, Object>` como context | Clase tipada `TradingSession` |
| Compartir `Position` entre threads | `CopyOnWriteArrayList` o sync explicito |
| Bloquear el scheduler en una sesion lenta | `parallelStream` o ejecutor con timeout |
| Spring `@Scope("request")` para sesiones | Map gestionado manualmente, no entra en lifecycle Spring |

---

## 6. Gestion de wallets

### Modelo de datos

```sql
CREATE TABLE user_wallets (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    exchange        VARCHAR(30) NOT NULL,    -- HYPERLIQUID / POLYMARKET / BINANCE
    label           VARCHAR(100),            -- "Mi wallet principal"
    address         VARCHAR(200) NOT NULL,   -- direccion publica
    -- credenciales encriptadas
    encrypted_secret BYTEA NOT NULL,         -- private key cifrada
    encryption_version INT NOT NULL DEFAULT 1, -- para rotacion de master key
    -- metadata
    is_active       BOOLEAN DEFAULT TRUE,
    is_default      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    UNIQUE (user_id, exchange, address)
);

CREATE INDEX idx_wallets_user ON user_wallets (user_id, is_active);
CREATE INDEX idx_wallets_exchange ON user_wallets (exchange);
```

### Cifrado

| Capa | Tecnica |
|---|---|
| **Algoritmo** | AES-256-GCM (autenticado, evita tampering) |
| **Master key** | En `.env` → variable `WALLET_MASTER_KEY` (32 bytes base64) o HSM si lo hay |
| **IV** | Aleatorio 12 bytes por wallet, prepended al ciphertext |
| **AAD** | `userId|walletId` para vincular el ciphertext a su dueno (rebind impossible) |
| **Rotacion** | `encryption_version` permite re-encriptar todo con nueva master sin downtime |
| **Decrypt timing** | Solo en memoria, justo antes de firmar tx, NUNCA en logs |

### Exchange abstraction

```java
public interface ExchangeClient {
    String name();
    boolean isConnected();
    void connect();
    void disconnect();

    // Account
    BigDecimal getAccountValue();
    List<ExternalPosition> getPositions();

    // Trading
    String placeMarketOrder(String coin, boolean isBuy, BigDecimal size, boolean reduceOnly);
    String placeLimitOrder(String coin, boolean isBuy, BigDecimal price, BigDecimal size, OrderType type);
    boolean cancelOrder(String oid);

    // Market data (puede delegar a MarketDataService compartido)
    BigDecimal getMarkPrice(String coin);

    // Fees
    FeeSchedule getFeeSchedule();
}

public class HyperliquidClient implements ExchangeClient { ... }
public class PolymarketClient implements ExchangeClient { ... }
// Futuro: BinanceClient, BybitClient
```

### ExchangeClientFactory

```java
@Service
public class ExchangeClientFactory {
    private final WalletService walletService;
    private final SecretsService secrets;

    public ExchangeClient create(UserWallet wallet) {
        String privateKey = secrets.decrypt(
            wallet.getEncryptedSecret(),
            wallet.getEncryptionVersion(),
            walletAad(wallet)
        );
        return switch (wallet.getExchange()) {
            case "HYPERLIQUID" -> new HyperliquidClient(wallet.getAddress(), privateKey);
            case "POLYMARKET"  -> new PolymarketClient(wallet.getAddress(), privateKey);
            default -> throw new UnsupportedExchangeException(wallet.getExchange());
        };
    }
}
```

---

## 7. Escalabilidad

### Estimacion de recursos para 100 usuarios

| Recurso | Calculo | Total |
|---|---|---|
| **Memory por sesion** | 50-100 MB (positions, history slice, gateStats) | **5-10 GB** |
| **Heap JVM** | + market data + caches | **8-16 GB** recomendado |
| **CPU** | <1% por sesion en idle, picos al evaluar | **4-8 cores** |
| **DB connections** | 1 pool de 50, compartido | **50** (no 100) |
| **Threads activos** | 1 scheduler + 1 pool de 16 workers | **~25** (no 100) |

**Servidor recomendado:** 8 cores / 32 GB RAM / SSD NVMe → soporta 100 usuarios con margen.

### Cuellos de botella criticos

#### #1: Hyperliquid API rate limits

**El cuello de botella mas serio.** HL tiene limites por IP/wallet:
- Public API: ~1200 weight/min
- Trading API: per wallet, mas generoso

**Sin shared market data:** 30 coins × 100 users × scan cada 3s = **60.000 calls/min** → imposible.

**Con shared market data:** 30 coins × 1 fetch cada 3s = **600 calls/min** ✅ totalmente viable.

**Esto es THE killer feature.** Sin compartir market data, no hay multi-tenant viable a esta escala.

#### #2: Decisiones por usuario

Cada sesion necesita evaluar 30 coins × 2 sides cada 3s. Calculo:
- 100 users × 30 coins × 2 sides = 6.000 evaluaciones cada 3s
- Cada evaluacion: ~0.1 ms (calculo de score, gates)
- Total CPU: 600 ms cada 3s = **~20% de 1 core**

✅ Viable. Pero hay que paralelizar (`parallelStream` con pool de 8 hilos).

#### #3: Order execution

Cuando hay senal, **cada usuario hace su propia llamada** a HL con su wallet.
- Picos: si BTC se mueve, 50 usuarios pueden querer entrar a la vez
- Cada call: 200-1000 ms
- Por wallet: rate limit relajado (~50 orders/seg)

**Mitigacion:** queue por usuario con timeout, no paralelo masivo. Si dos usuarios entran a la vez, la latencia adicional es de ms.

#### #4: DB writes

100 usuarios cerrando trades → spike de inserts. Calculo:
- 100 users × ~10 trades/dia = 1000 inserts/dia → trivial
- Picos: 5-10 inserts/seg → trivial con indices

✅ DB no es problema con tamaño actual.

#### #5: Memory leaks

Riesgo real con sesiones largas: si una posicion no se libera, acumulas memoria.

**Mitigacion:**
- Limite de `closedPositions` (ya esta: max 100)
- Cleanup periodico de sesiones inactivas
- Heap monitoring + alertas

### Arquitectura por fases del scanner

```
              [MarketDataService - SHARED]
              ┌───────────────────────────┐
              │ Cada 3s:                  │
              │ 1) Fetch HL prices (30)   │
              │ 2) Update priceHistory    │
              │ 3) Calc trends/macro      │
              │ 4) Notify subscribers     │
              └────────────┬──────────────┘
                           │
              ┌────────────▼──────────────┐
              │  Per-user evaluation      │
              │  (parallelStream pool 8)  │
              └────────────┬──────────────┘
                           │
              ┌────────────▼──────────────┐
              │  For each session:        │
              │   - apply user filters    │
              │   - check user score      │
              │   - decide entry          │
              │   - if entry: queue order │
              └────────────┬──────────────┘
                           │
              ┌────────────▼──────────────┐
              │  Order execution queue    │
              │  (per-user wallet client) │
              └────────────┬──────────────┘
                           │
              ┌────────────▼──────────────┐
              │  Persist to DB            │
              │  (with user_id)           │
              └───────────────────────────┘
```

---

## 8. Cambios en base de datos

### Tablas a anadir `user_id`

| Tabla | Cambio | Justificacion |
|---|---|---|
| `hl_trading_history` | `+ user_id BIGINT NOT NULL` | Cada trade es de 1 user |
| `hl_trading_presets` | Mantener globales + nueva `user_preset_overrides` | Defaults compartidos, overrides per-user |
| `hl_coin_profiles` | `+ user_id` (PK compuesta: user_id, coin, side, mode) | Aprendizaje per-user |
| `hl_coin_profile_stats` | `+ user_id` | idem |
| `hl_coin_profile_changes` | `+ user_id` | idem |
| `hl_coin_profile_proposals` | `+ user_id` | idem |
| `hl_dir_session` | `+ user_id` | Sesiones de trading per-user |
| `hl_alerts` | `+ user_id` | Alertas individuales |
| `hl_mode_schedule_weekly` | `+ user_id` | Cada user tiene su horario |
| **Platform DB** | | |
| `change_markers` | `+ user_id` | Cambios per-user |
| `trade_outcomes` | `+ user_id` | Trades enviados desde el bot |
| `signal_evaluations` | `+ user_id` | Senales evaluadas |
| `coin_profiles` (platform) | `+ user_id` | Vista del platform |

### Tablas nuevas

```sql
-- Roles
CREATE TABLE user_roles (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL UNIQUE REFERENCES auth_users(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL CHECK (role IN ('BASIC', 'PRO', 'PREMIUM', 'ADMIN')),
    granted_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ
);

-- Wallets (ya descrita arriba)
CREATE TABLE user_wallets ( ... );

-- Limites por tier
CREATE TABLE tier_limits (
    role          VARCHAR(20) PRIMARY KEY,
    max_wallets   INT NOT NULL,
    max_budget    NUMERIC(18,2) NOT NULL,
    max_positions INT NOT NULL,
    max_coins     INT NOT NULL,
    features      JSONB NOT NULL  -- {"horario_avanzado": true, "microstructure": false, ...}
);

-- Override de presets per-user
CREATE TABLE user_preset_overrides (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    mode        VARCHAR(20) NOT NULL,
    field_name  VARCHAR(60) NOT NULL,
    field_value VARCHAR(200) NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, mode, field_name)
);

-- Audit log para compliance
CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    user_id     BIGINT,
    action      VARCHAR(100) NOT NULL,
    resource    VARCHAR(100),
    ip_address  INET,
    user_agent  TEXT,
    details     JSONB
);
```

### Indices

```sql
-- Critico para performance
CREATE INDEX idx_trades_user_time ON hl_trading_history (user_id, entry_at DESC);
CREATE INDEX idx_profiles_user_coin ON hl_coin_profiles (user_id, coin, side, mode);
CREATE INDEX idx_alerts_user ON hl_alerts (user_id, created_at DESC);
-- ... etc
```

### Particionado (opcional, para escalado real)

Cuando la tabla `hl_trading_history` supere 10M filas, considerar particionar por `user_id MOD 16` o por mes:

```sql
CREATE TABLE hl_trading_history (...) PARTITION BY HASH (user_id);
CREATE TABLE hl_trading_history_p0 PARTITION OF hl_trading_history FOR VALUES WITH (MODULUS 16, REMAINDER 0);
-- ... 16 particiones
```

Beneficio: queries `WHERE user_id = ?` solo escanean 1/16 de la tabla.

---

## 9. Cambios en platform

### Backend (FastAPI)

| Cambio | Detalle |
|---|---|
| **Auth** | JWT ya existe. Anadir `role` claim. Middleware extrae `user_id` en cada request. |
| **Endpoints** | TODOS los `GET /api/...` filtran por `user_id` del JWT. NO permitir queries abiertas. |
| **Markers** | `user_id` en cada marker. `recent-impacts` filtra per user. |
| **Trades** | `/api/bot/trades` solo del usuario logueado |
| **Gate stats** | Per user (recibidas con `user_id` desde el bot) |
| **Recibidor del bot** | `POST /api/bot/marker` ahora requiere `user_id` en payload |

### Frontend (Next.js)

| Cambio | Detalle |
|---|---|
| **Login** | Mostrar roles del usuario, ocultar features no permitidas |
| **Header** | Selector de wallet activa (si tiene varias) |
| **Pestanas** | Visibilidad condicional segun rol |
| **Datos** | Toda llamada a `/api/...` ya viene scoped por JWT, no hace falta filtro extra |
| **Admin panel** | Nueva ruta `/admin` solo para rol ADMIN — gestion de users, wallets, limits |

### Dashboard del platform

| Cambio | Detalle |
|---|---|
| **Cambios** | Filtrar por usuario en JWT |
| **Bot Live** | Stats per user |
| **Conclusiones** | Resumen ejecutivo per user |
| **Admin overview** | Nueva pagina solo ADMIN: vista agregada de todos los usuarios |

---

## 10. Seguridad

### Threat model

| Threat | Mitigacion |
|---|---|
| **User A accede a positions de User B** | Cada query SQL incluye `WHERE user_id = ?`. Tests de regresion. Code review obligatorio para queries. |
| **Private key leak en logs** | Nunca log `wallet.encrypted_secret`. Secrets service con metodo `decryptForOneUse(...)` que limpia memoria tras uso. Static analysis. |
| **JWT robo** | Cookie HttpOnly + Secure + SameSite=strict. Expiracion corta (1h). Refresh token. |
| **SQL injection** | JPA parametrizado siempre. Nunca concatenar SQL. |
| **Race condition entre sesiones** | Cada `TradingSession` es accedida solo desde un thread (queue) o sync interno. |
| **Insider threat (operador roba keys)** | Master key en HSM o env separada. Audit log de accesos. Cifrado at-rest. |
| **Backup/restore con keys** | Backups encriptados. Master key NO en backups (separada). |
| **Compliance / KYC** | (Si aplica) modulo separado, integracion con proveedor KYC. Fuera de scope inicial. |
| **Rate limit abuse** | Por usuario y por endpoint. Bloqueo temporal en abuso. |
| **Webhook spoofing (PlatformBridge)** | HMAC signature en payloads. |

### Cifrado en reposo

- **Wallets:** AES-256-GCM como ya descrito
- **DB backups:** cifrado a nivel de filesystem (LUKS) o cifrado de pg_dump (gpg)
- **Logs:** rotacion + cifrado opcional para logs sensibles

### Cifrado en transito

- **API publica:** TLS 1.3 (Cloudflare Tunnel ya lo da)
- **DB local:** opcional, no necesario si DB en mismo host
- **Bot ↔ Platform:** localhost, no necesita TLS

### Gestion de secretos

| Secreto | Donde vive |
|---|---|
| `WALLET_MASTER_KEY` | `.env` del bot, NO en git, NO en backups |
| `JWT_SECRET` | `.env` del bot |
| `DB_PASSWORD` | `.env` |
| `TELEGRAM_BOT_TOKEN` | `.env` |
| Wallets de usuarios | Tabla `user_wallets`, encriptadas con master |

**Recomendacion futura:** migrar a HashiCorp Vault o AWS Secrets Manager.

---

## 11. Sistema de roles

### Definicion de roles

```
BASIC (gratis o low-tier)
├── Sesion (start/stop)
├── Trading (1 wallet, max 3 posiciones)
├── Historial
├── Senales (read-only)
├── Scanner
├── Horario (semanal basico)
└── Config (presets predefinidos, NO editables)

PRO (pago tier 1)
├── Todo BASIC
├── Horario semanal AVANZADO (cells editables)
├── Microstructure (panel WS)
├── Perfiles por coin (auto-learning)
├── Config editable per mode
├── Hasta 3 wallets
└── Hasta 8 posiciones

PREMIUM (pago tier 2)
├── Todo PRO
├── Quantum Platform (acceso al dashboard de research)
├── Markers manuales
├── Alertas avanzadas
├── Wallets ilimitadas
├── Posiciones ilimitadas
├── API access (futuro)
└── Multi-exchange (cuando este)

ADMIN (interno)
├── Todo PREMIUM
├── Gestion de usuarios
├── Vista global
├── Override manual
└── Auditoria
```

### Implementacion

#### Backend (Java)

```java
public enum Role { BASIC, PRO, PREMIUM, ADMIN }

public enum Feature {
    HORARIO_AVANZADO,
    MICROSTRUCTURE,
    COIN_PROFILES,
    QUANTUM_PLATFORM,
    MARKERS_MANUAL,
    MULTI_EXCHANGE,
    API_ACCESS,
    UNLIMITED_POSITIONS,
    UNLIMITED_WALLETS
}

// Configuracion declarativa
public static final Map<Role, Set<Feature>> ROLE_FEATURES = Map.of(
    Role.BASIC,   Set.of(),
    Role.PRO,     Set.of(HORARIO_AVANZADO, MICROSTRUCTURE, COIN_PROFILES),
    Role.PREMIUM, Set.of(HORARIO_AVANZADO, MICROSTRUCTURE, COIN_PROFILES,
                         QUANTUM_PLATFORM, MARKERS_MANUAL, UNLIMITED_POSITIONS,
                         UNLIMITED_WALLETS),
    Role.ADMIN,   Set.of(Feature.values())
);

// Annotation para endpoints
@RequiresFeature(Feature.MICROSTRUCTURE)
@GetMapping("/api/hl/trading/microstructure")
public Map<String, Object> getMicro() { ... }

// Aspect que valida
@Aspect @Component
public class FeatureGuardAspect {
    @Around("@annotation(requiresFeature)")
    public Object check(ProceedingJoinPoint pjp, RequiresFeature requiresFeature) {
        var user = SecurityContext.currentUser();
        if (!user.hasFeature(requiresFeature.value())) {
            throw new ForbiddenException("Feature " + requiresFeature.value() + " requires upgrade");
        }
        return pjp.proceed();
    }
}
```

#### Frontend

```tsx
// Hook para feature gating
const { hasFeature } = useUser();

return (
  <>
    {hasFeature("MICROSTRUCTURE") && <MicroTab />}
    {hasFeature("COIN_PROFILES") && <ProfilesTab />}
    {!hasFeature("MICROSTRUCTURE") && (
      <UpgradePrompt feature="Microstructure" />
    )}
  </>
);
```

#### Tier limits enforcement

```java
public Position openPosition(TradingSession session, ...) {
    var limits = tierService.getLimits(session.role());
    if (session.openPositions().size() >= limits.maxPositions()) {
        throw new TierLimitException("Max positions reached for " + session.role());
    }
    if (session.usedBudget() + notional > limits.maxBudget()) {
        throw new TierLimitException("Max budget reached");
    }
    // ... resto
}
```

---

## 12. Observabilidad

### Logging estructurado per usuario

```java
// MDC (Mapped Diagnostic Context) — Slf4j
public class TenantContextFilter implements Filter {
    public void doFilter(...) {
        try {
            MDC.put("userId", String.valueOf(currentUserId));
            chain.doFilter(req, res);
        } finally {
            MDC.clear();
        }
    }
}
```

`logback.xml`:
```xml
<pattern>%d %level [%X{userId:-system}] %logger - %msg%n</pattern>
```

Resultado:
```
2026-04-10 17:42:15 INFO [user=42] HlDirectionalEngine - [HL_DIR_ENTRY] coin=APT side=SHORT
2026-04-10 17:42:16 INFO [user=17] HlDirectionalEngine - [HL_DIR_EXIT] coin=ETH side=LONG reason=TP
```

### Metricas por usuario (Prometheus)

```java
@Component
public class TradingMetrics {
    private final MeterRegistry registry;

    public void recordTrade(long userId, String mode, double netPnl) {
        registry.counter("bot.trades.total",
            "user_id", String.valueOf(userId),
            "mode", mode,
            "outcome", netPnl >= 0 ? "win" : "loss"
        ).increment();

        registry.gauge("bot.pnl.realized",
            Tags.of("user_id", String.valueOf(userId)),
            netPnl);
    }
}
```

Grafana: dashboards filtrables por `user_id` con drill-down.

### Audit log

Cada accion sensible va a `audit_log`:
- Login/logout
- Crear/borrar wallet
- Cambio de config
- Iniciar/parar trading
- Acceso a panel admin

---

## 13. Riesgos

### Tecnicos

| # | Riesgo | Severidad | Mitigacion |
|---|---|---|---|
| 1 | Refactor del engine introduce bugs criticos | 🔴 Alto | Tests exhaustivos antes de merge. Feature flag para nuevo engine. Rollback plan. |
| 2 | State leak entre sesiones | 🔴 Alto | Code review estricto. Test con 10 usuarios paralelos en CI. Static analysis. |
| 3 | Memory leak con sesiones long-running | 🟡 Medio | Heap monitoring + alertas. Cleanup periodico. Limites en colecciones. |
| 4 | HL API rate limits con muchos usuarios | 🟡 Medio | Shared market data. Backoff exponencial. Monitoring de weight. |
| 5 | Race condition en order execution | 🟡 Medio | Queue por usuario, no paralelo masivo dentro de la misma wallet. |
| 6 | DB connection pool exhausting | 🟢 Bajo | Pool sizing correcto. Monitoring. |
| 7 | Crash de JVM mata 100 usuarios | 🟡 Medio | Healthcheck + auto-restart. Idempotencia. State recovery on startup. |

### Performance

| # | Riesgo | Severidad | Mitigacion |
|---|---|---|---|
| 1 | Latencia sube con N usuarios | 🟡 Medio | Profiling. Pool sizing. Async donde se pueda. |
| 2 | DB lenta con writes concurrentes | 🟢 Bajo | Indices correctos. Particionado si crece. |
| 3 | GC pauses con heap grande | 🟡 Medio | G1GC tuning. Heap ratio. |
| 4 | Network saturada en picos | 🟢 Bajo | Batch HTTP donde se pueda. |

### Seguridad

| # | Riesgo | Severidad | Mitigacion |
|---|---|---|---|
| 1 | **Leak de private keys** | 🔴 Critico | Cifrado obligatorio. Code review. Static analysis. Audit. |
| 2 | Cross-user data access | 🔴 Critico | Tests automaticos. Row-level security en DB (PostgreSQL RLS). |
| 3 | JWT theft | 🟡 Medio | HttpOnly + Secure. Expiracion corta. Refresh tokens. |
| 4 | Insider threat | 🟡 Medio | Audit log. Master key en HSM/separada. Rotacion. |
| 5 | Backups con secrets | 🔴 Alto | Master key NO en backups. Backups encriptados. |
| 6 | Compliance / regulacion | 🔴 Alto | **Asesoria legal antes de open beta.** Trading bot con dinero ajeno tiene implicaciones. |

### Operacionales

| # | Riesgo | Severidad | Mitigacion |
|---|---|---|---|
| 1 | Soporte tecnico 1:N | 🟡 Medio | Self-service docs. Status page. Support tickets. |
| 2 | Bugs visibles a 100 users a la vez | 🟡 Medio | Staging environment. Canary deploys. Feature flags. |
| 3 | Costes de infraestructura | 🟢 Bajo | 1 servidor hasta ~150 users. Vertical scaling barato. |
| 4 | Onboarding de usuarios complejo | 🟡 Medio | Wizard de setup. Tutoriales. Validacion de wallet en tiempo real. |

---

## 14. Plan de migracion por fases

### Fase 0 — Preparacion (1-2 semanas)

**Objetivo:** sentar las bases sin romper nada.

- [ ] Crear `WALLET_MASTER_KEY` en `.env` y `SecretsService`
- [ ] Migration: anadir `user_id BIGINT NULL` a todas las tablas core
- [ ] Backfill: `UPDATE ... SET user_id = (SELECT id FROM auth_users WHERE username='chema200')`
- [ ] Tests: cobertura >70% de los flujos criticos del engine actual
- [ ] CI con 1 test de "smoke multi-user" (futuro)
- [ ] Doc de arquitectura (este doc)

**Riesgo:** ✅ Bajo. El sistema sigue funcionando como single-tenant.

---

### Fase 1 — Multi-tenancy basico (2-3 semanas)

**Objetivo:** soportar 2-5 usuarios de prueba con wallets separadas.

- [ ] Tabla `user_wallets` con cifrado AES-GCM
- [ ] `WalletService` + `SecretsService` + `ExchangeClientFactory`
- [ ] Refactor `HlDirectionalEngine` → `TradingSession` + engine stateless
- [ ] `SessionManager` con `Map<UserId, TradingSession>`
- [ ] `MarketDataService` extraido (singleton compartido)
- [ ] Endpoints scoped por `userId` del JWT
- [ ] Frontend: muestra solo data del usuario logueado
- [ ] Tests: 5 usuarios paralelos sin contaminacion

**Riesgo:** 🔴 Alto. Es donde se rompen cosas. **Hacer en branch + feature flag.**

**Entrega:** beta cerrada con 2-3 usuarios reales.

---

### Fase 2 — Wallets multiples + UI (1-2 semanas)

**Objetivo:** un usuario puede gestionar varias wallets.

- [ ] UI para crear/editar/borrar wallet
- [ ] Selector de wallet activa en el header
- [ ] Validacion: la wallet existe en HL, balance > 0
- [ ] Cambio de wallet activa = stop session anterior + start nueva
- [ ] Markers en el platform muestran qué wallet hizo qué

**Riesgo:** 🟢 Bajo. Es UI sobre la base ya construida.

---

### Fase 3 — Roles y limits (1 semana)

**Objetivo:** sistema de tiers con feature gating.

- [ ] Tabla `user_roles` + `tier_limits`
- [ ] `Role` y `Feature` enums en backend
- [ ] `@RequiresFeature` annotation + aspect
- [ ] Frontend con `useUser().hasFeature(...)`
- [ ] Pestanas condicionales por rol
- [ ] Tier limits enforcement (max positions, budget, etc)
- [ ] Admin panel basico (asignar roles)

**Riesgo:** 🟡 Medio. Cuidado con bypasear feature flags.

---

### Fase 4 — Escalado real (2-4 semanas)

**Objetivo:** soportar 50-100 usuarios concurrentes.

- [ ] `MarketDataService` optimizado (1 fetch para todos)
- [ ] `parallelStream` con pool dimensionado
- [ ] DB connection pool tuning
- [ ] Particionado de `hl_trading_history` (si crece)
- [ ] Prometheus + Grafana con metricas por usuario
- [ ] Alertas: heap, threads, latencia, errores per user
- [ ] Load testing: simular 100 users concurrentes
- [ ] Auto-restart healthcheck
- [ ] Backup automatico de DB con cifrado

**Riesgo:** 🟡 Medio. Depende mucho del servidor.

---

### Fase 5 (opcional) — Multi-exchange

**Objetivo:** anadir Binance/Bybit como segundo venue.

- [ ] `BinanceClient implements ExchangeClient`
- [ ] Mapping de coins HL ↔ Binance
- [ ] User puede tener wallet HL + wallet Binance
- [ ] Engine elige venue por wallet activa o estrategia

**Riesgo:** 🟡 Medio. Es trabajo aislado, no toca el core.

---

### Total estimado

| Fase | Duracion | Personal/Tiempo |
|---|---|---|
| 0 | 1-2 sem | 1 dev |
| 1 | 2-3 sem | 1 dev |
| 2 | 1-2 sem | 1 dev |
| 3 | 1 sem | 1 dev |
| 4 | 2-4 sem | 1 dev + DevOps |
| 5 | 2-3 sem (opcional) | 1 dev |
| **TOTAL** | **9-15 semanas** | **~3 meses** |

---

## 15. Decisiones abiertas

Cosas que tienes que decidir antes de empezar:

| # | Decision | Impacto |
|---|---|---|
| 1 | **¿KYC?** ¿Necesitas verificar identidad de usuarios? | Legal + arquitectura |
| 2 | **Modelo de negocio:** ¿gratis, freemium, suscripcion, % de profit? | Roles y limits |
| 3 | **Alojar wallets ajenos:** ¿custodia o non-custodia? | LEGAL — esto es lo mas critico |
| 4 | **¿Deployment en cloud o servidor propio?** | Costes y escalabilidad |
| 5 | **¿Dominio publico o invitacion only?** | Marketing y soporte |
| 6 | **¿Soporte tecnico?** ¿chat, email, SLA? | Operaciones |
| 7 | **¿Backup y disaster recovery?** ¿RPO/RTO objetivo? | Coste de infraestructura |
| 8 | **¿Mantener single-user en paralelo?** Para migracion gradual | Complejidad temporal |

### La mas critica: legal

> **Operar trading bots con dinero de terceros tiene implicaciones legales serias en la mayoria de jurisdicciones.**
>
> - Si el usuario te entrega fondos para que tu los muevas → custodia → necesitas licencia
> - Si el usuario conecta su propia wallet (non-custodial) y tu solo das software → mas seguro pero todavia hay grises (asesoria de inversion?)
> - **Antes de open beta, consulta con un abogado de fintech.** Esto no es opcional.

---

## Apendices

### A. Glosario

- **Single-tenant**: 1 usuario por instancia
- **Multi-tenant**: N usuarios compartiendo la misma instancia
- **Tenant**: cada usuario en multi-tenant
- **Session**: instancia de trading activa de un usuario
- **TradingSession**: la clase POJO que encapsula el estado per-user
- **Wallet**: combinacion de address + private key para un exchange
- **Tier/Role**: nivel de acceso del usuario (BASIC, PRO, PREMIUM)
- **Feature**: funcionalidad concreta gatable por tier
- **MDC**: Mapped Diagnostic Context (logging contextual)
- **HSM**: Hardware Security Module para gestion de claves

### B. Metricas a monitorizar (post-launch)

| Metrica | Threshold de alerta |
|---|---|
| Sessions activas | > 100 |
| Heap usage | > 80% |
| GC pause time | > 500ms |
| HL API errors per min | > 5% |
| DB pool exhausting | < 5 conexiones libres |
| Latencia p95 entry | > 200ms |
| Latencia p95 exit | > 500ms |
| Crashes per day | > 0 |
| Failed auth attempts | > 100/h por IP |

### C. Tecnologias adicionales sugeridas

| Necesidad | Stack sugerido |
|---|---|
| Metrics | Prometheus + Grafana |
| Logs centralizados | Loki o ELK |
| Tracing | Jaeger o OpenTelemetry |
| Secrets | HashiCorp Vault (futuro) |
| Backups | pg_dump + rclone a S3/B2 cifrado |
| CI/CD | GitHub Actions + Docker |
| Status page | Statuspage.io o uptime-kuma |

---

## Conclusion

El sistema actual **NO esta preparado** para multi-tenant pero **SI es refactorizable** sin reescribir desde cero. La pieza clave es el refactor del `HlDirectionalEngine` para extraer su estado a `TradingSession` y compartir el motor + market data como singletons stateless.

El **approach hibrido B (single JVM, sesion per usuario, market data compartido)** es el mejor compromiso entre:
- Eficiencia de recursos
- Aislamiento entre usuarios
- Esfuerzo de migracion
- Escalabilidad realista a 100 usuarios

**Esfuerzo total estimado: 9-15 semanas (3 meses) de trabajo dedicado.**

**Riesgo principal: legal.** Antes de abrir a usuarios, asesorar con abogado de fintech sobre custodia de wallets.

**Ganancia esperada:**
- De 1 usuario → 100+ usuarios sin reescribir
- Modelo de negocio escalable (BASIC/PRO/PREMIUM)
- Plataforma diferenciada de otros bots porque mantienes tu motor propio

**Lo que NO debes hacer:**
- Saltar Fase 0 (preparacion). Sin tests, el refactor del engine es suicidio.
- Desplegar Fase 1 directamente a produccion. **Beta cerrada primero.**
- Ignorar el aspecto legal. Es el riesgo real que puede matarlo todo.

---

**Documento vivo. Actualizar conforme se tomen decisiones.**
