# Alchimiabot — Estrategia de Datos para Multi-Tenant (HL only)

> Que guardamos, que tiramos, que se multiplica × N usuarios y como sobrevivir
> a la curva exponencial de datos sin convertir el bot en un agujero negro de espacio.

**Producto:** Alchimiabot
**Beta cerrada:** 10 amigos
**Target:** 100 usuarios maximo
**Exchange:** solo Hyperliquid (Polymarket pausado, otros exchanges futuros)
**Modelo:** non-custodial con API Wallets de HL
**Fecha:** 2026-04-10

---

## TL;DR — Lo que tienes que saber en 60 segundos

| Tabla | Hoy (1 user) | × 10 (beta) | × 100 (target) | Decision |
|---|---|---|---|---|
| `hl_trade_micro_features` | 1.363 rows | 14k | 140k | 🔴 **TIRAR** la mitad |
| `feature_snapshots` (platform) | 134k rows / **140 MB** | 1.3M / 1.4 GB | 13M / **14 GB** | 🔴 **TTL 7 dias** |
| `signal_evaluations` (platform) | 28k rows / **56 MB** | 280k / 560 MB | 2.8M / **5.6 GB** | 🔴 **Sampling + TTL 30 dias** |
| `hl_alerts` | 1.447 rows | 14k | 145k | 🟡 **TTL 30 dias** |
| `hl_trading_history` | 315 rows | 3.150 | 31.500 | ✅ KEEP all |
| `hl_coin_profiles` (updates) | 24k UPDATEs | 240k | 2.4M | 🟡 Optimizar batch |
| `hl_mode_schedule_weekly` | 168 rows fixed | 1.680 | 16.800 | ✅ KEEP, indice |

**El cuello de botella real:** **`feature_snapshots` y `signal_evaluations` del platform.** Hoy ya consumen 196 MB con 1 usuario. ×100 = **20 GB**. Esto **mata el sistema** si no se ataca.

**Tablas zombi para borrar:** 11 tablas legacy nunca usadas (~10 MB libres pero limpieza mental).

**Coste estimado de DB con 100 usuarios:** ~30-50 GB total. Manejable en 1 servidor con SSD NVMe.

---

## 1. Inventario completo (datos REALES extraidos hoy)

### DB del bot (`agentbot`)

```
Tabla                          Rows    Inserts   Updates   Tamano
hl_trade_micro_features        1363    1363      0         224 KB
hl_alerts                      1447    1447      0          64 KB
hl_trading_history              315     315      0         104 KB
real_session_fills              231     231      0         136 KB
hl_coin_profiles                156     169      24.426    104 KB  ← muchos UPDATEs
hl_mode_schedule_weekly         168     168      10         88 KB
hl_coin_profile_stats           141     141      315        80 KB
hl_coin_profile_changes          58      58      0          32 KB
hl_dir_sessions                  23      23      0          32 KB
hl_trading_presets                3       3      87         40 KB  ← 3 rows fijas
auth_users                        1       1      0          40 KB
```

**Tablas legacy sin uso:**
```
backtest_runs        0 rows  ← borrar
shadow_snapshots     0 rows  ← borrar
shadow_runs          0 rows  ← borrar
monte_carlo_runs     0 rows  ← borrar
hl_session_fills     0 rows  ← borrar
hl_sessions          0 rows  ← borrar
orders               0 rows  ← borrar
pnl                  0 rows  ← borrar
fills                0 rows  ← borrar
redeem_history       0 rows  ← borrar
hl_mode_schedule     3 rows  ← borrar (sustituida por _weekly en V26)
trading_config_*     5 rows  ← borrar (legacy)
real_session_fills   231     ← borrar (Polymarket pausado)
real_sessions        5       ← borrar (Polymarket pausado)
```

**Total a limpiar: 13 tablas, ~10 MB recuperables.** Mas importante por mental clarity que por espacio.

### DB del platform (`agentbot_platform`)

```
Tabla                  Rows      Inserts   Tamano
feature_snapshots      134.600   134.600   140 MB   ← MASIVO
signal_evaluations      28.340    28.340    56 MB   ← MASIVO
audit_runs               1.966     1.966   1072 KB
trade_snapshots            393       393    392 KB
trade_outcomes             238       238    392 KB
audit_findings             443       443     48 KB
change_markers              16        16    144 KB
trade_verdicts               7         7     64 KB
```

**El verdadero monstruo:**
- `feature_snapshots`: **140 MB con 1 usuario en 1 dia** → este es TU problema

---

## 2. Que se multiplica × N usuarios

### Datos que escalan LINEAL con usuarios (× N)

| Tabla | Por usuario / dia | × 10 / dia | × 100 / dia | × 100 / mes |
|---|---|---|---|---|
| `hl_trading_history` | ~30 trades | 300 | 3.000 | 90.000 |
| `hl_dir_sessions` | ~3 sesiones | 30 | 300 | 9.000 |
| `hl_coin_profile_changes` | ~6 changes | 60 | 600 | 18.000 |
| `change_markers` | ~5 markers | 50 | 500 | 15.000 |
| `audit_log` (nueva) | ~50 events | 500 | 5.000 | 150.000 |

→ **Manejable.** Ninguna de estas explota.

### Datos que escalan LINEAL con usuarios pero ya son grandes (× N)

| Tabla | Por usuario / dia | × 10 / dia | × 100 / dia | × 100 / **6 meses** |
|---|---|---|---|---|
| `hl_trade_micro_features` | ~1.300 | 13.000 | **130.000** | **23 millones** |
| `feature_snapshots` (platform) | ~135.000 | 1.350.000 | **13.500.000** | **2.400 millones** ⚠️ |
| `signal_evaluations` (platform) | ~28.000 | 280.000 | **2.800.000** | **500 millones** ⚠️ |

**Estas son las bombas.** En particular `feature_snapshots`:
- 134k inserts / dia / usuario = ~1.5 inserts/seg / usuario
- × 100 usuarios = **150 inserts/seg sostenido**
- En 6 meses: **2.400 millones de filas** → **~2.5 TB** de datos
- **Imposible mantener.**

### Datos que NO escalan con usuarios (compartidos)

| Tabla | Tamano | Justificacion |
|---|---|---|
| `hl_mode_schedule_weekly` | 168 rows × N | Cada user tiene su grid (× 100 = 16.800, trivial) |
| `hl_trading_presets` | 3 rows globales | Defaults compartidos |
| `auth_users` | 1 row × N | × 100 = 100, trivial |
| `tier_limits` (nueva) | 4 rows fixed | Roles |
| `regime_labels` (platform) | shared | Datos de mercado, no per-user |

✅ **Sin problema.**

---

## 3. Las 2 tablas que matan el sistema (y que hacer)

### 🔴 #1: `feature_snapshots` (platform) — el asesino #1

**Que es:** snapshots de cada posicion enviados desde el bot al platform cada 30s.

**Por que existe:** para reconstruir trades en el platform y hacer analisis MFE/MAE/drawdown.

**Volumen actual:** 134.600 rows / dia / 1 usuario = **140 MB / dia / user**.

**Volumen × 100 / 6 meses:** 2.400 millones rows = **~2.5 TB**.

**Es viable mantenerlo asi?** **NO.** Es completamente inviable.

### Opciones para arreglarlo

#### Opcion A — Agregacion antes de guardar (RECOMENDADA)

En lugar de guardar 1 snapshot cada 30s, guardar **1 snapshot agregado por trade** con todas las metricas calculadas:

```sql
CREATE TABLE trade_snapshots_agg (
    trade_id        VARCHAR(100) PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    coin            VARCHAR(20),
    side            VARCHAR(10),
    -- snapshots agregados
    snapshots_count  INT,                  -- 20 puntos en lugar de 200
    mfe_pct          NUMERIC(8,4),         -- max favorable
    mae_pct          NUMERIC(8,4),         -- max adverse
    avg_pnl_pct      NUMERIC(8,4),
    final_pnl_pct    NUMERIC(8,4),
    -- timeline en JSONB compacto (10-20 puntos)
    timeline         JSONB                 -- [[t1,price1,pnl1],[t2,price2,pnl2],...]
);
```

**Reduccion:** de **134k filas/dia/user → ~30 filas/dia/user** (1 row por trade).

**× 100 / 6 meses:** ~540.000 filas → **~500 MB**. **5000x menos espacio.**

**Trade-off:** pierdes resolucion intra-trade. Pero es lo unico viable a escala.

**Implementacion:** el bot mantiene una lista en memoria con los snapshots del trade actual. Al cerrar, los agrega y envia 1 sola row al platform.

#### Opcion B — TTL agresivo (parche temporal)

Mantener el modelo actual pero **borrar todo lo > 7 dias**.

```sql
DELETE FROM feature_snapshots WHERE timestamp < NOW() - INTERVAL '7 days';
-- Cron cada noche
```

**Volumen × 100 a 7 dias:** ~95 millones rows = **~95 GB**. Sigue siendo enorme pero manejable.

**Cuando usarla:** como parche mientras refactorizas a Opcion A.

#### Opcion C — Eliminar feature_snapshots completamente

**¿Realmente necesitas el detalle intra-trade?** Hoy lo usas para:
- Mostrar evolucion de la posicion en el dashboard
- Calcular MFE/MAE post-trade

**Pero** MFE/MAE ya estan en `hl_trading_history` (campos `mfe_pct`, `mae_pct`).
Y el dashboard puede mostrar la evolucion en tiempo real desde el bot, no desde el platform.

**Veredicto:** quizas no necesitas esta tabla en absoluto. Solo el agregado del trade final.

#### Recomendacion: **A + B (transicion)**

1. **Hoy:** TTL 7 dias en `feature_snapshots` (1 hora de trabajo)
2. **Fase 1 multi-tenant:** refactor a `trade_snapshots_agg` (1 dia de trabajo)
3. **Despues:** eliminar `feature_snapshots` original

---

### 🔴 #2: `signal_evaluations` (platform) — el asesino #2

**Que es:** cada evaluacion de cada coin × side en cada ciclo (cada 3s) enviada al platform.

**Volumen actual:** 28.340 rows / dia / 1 usuario = **56 MB / dia**.

**Volumen × 100 / 6 meses:** 500 millones rows = **~1 TB**.

**El problema:** el bot evalua **30 coins × 2 sides cada 3s = 60 evaluaciones cada 3s = 20/s = 1.7M/dia**. Hoy solo manda al platform los que tienen score >50 o accion notable. Pero aun asi son 28k.

### Opciones para arreglarlo

#### Opcion A — Solo guardar las accionables (RECOMENDADA)

**No guardar todas las evaluaciones, solo:**
1. Las que generan trade (`action=ENTER`)
2. Las bloqueadas por filtros con razon especifica (`action=BLOCKED, reason=X`)
3. Una muestra del 1% del resto para analisis estadistico

```sql
-- En lugar de guardar todas:
INSERT INTO signal_evaluations (...) VALUES (...);  -- siempre

-- Guardar solo si:
IF action IN ('ENTER', 'BLOCKED') OR random() < 0.01 THEN
    INSERT INTO signal_evaluations (...) VALUES (...);
END IF;
```

**Reduccion:** de 28k → ~500-1000 / dia / user (los ENTER + BLOCKED + sample).

**× 100 / 6 meses:** ~18M rows = **~36 GB**. Manejable.

#### Opcion B — Particionado por mes + compresion

Mantener el modelo actual pero particionar la tabla por mes y comprimir las particiones viejas:

```sql
CREATE TABLE signal_evaluations (...) PARTITION BY RANGE (timestamp);
CREATE TABLE signal_evaluations_2026_04 PARTITION OF signal_evaluations
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
-- ... etc

-- Compresion via TimescaleDB hypertable o pg_partman
```

**Coste:** sigue siendo grande pero queries sobre el mes actual son rapidas.

#### Recomendacion: **A** (opcion limpia)

Cambiar el bot para que solo envie al platform los signals que valen la pena. **Esto es 2 lineas de codigo en el bot.**

---

## 4. Resto del analisis tabla por tabla

### Tablas que deberian llevar `user_id` y son OK

| Tabla | Cambio | Volumen × 100 |
|---|---|---|
| `hl_trading_history` | + user_id, + indice (user_id, entry_at DESC) | 31k/dia → 11M/anio. Trivial. |
| `hl_dir_sessions` | + user_id | 300/dia. Trivial. |
| `hl_alerts` | + user_id, **TTL 30 dias** | 145k al mes. Con TTL queda en 4M activos. Manejable. |
| `change_markers` (platform) | + user_id, + indice | 500/dia. Trivial. |
| `trade_outcomes` (platform) | + user_id, + indice | 3k/dia. Trivial. |

### Tabla con UPDATE intensivo: `hl_coin_profiles`

**Hoy:** 156 rows pero **24.426 UPDATEs**. Eso es ~150 UPDATEs / row / dia.
**× 100 usuarios:** 156 × 100 = 15.600 rows × 150 UPDATEs = **2.4M UPDATEs / dia** = **28 UPDATEs/seg**.

**¿Es problema?** En PostgreSQL con WAL fsync, 28 UPDATEs/seg sostenido es **trivial**. El problema seria si fuesen miles/seg.

**Optimizacion:** la mayoria de updates son recalculo de stats EW (`ew_win_rate`, `avg_win_pnl`, etc). Se podrian:
1. Batch en memoria + flush cada N segundos
2. Solo UPDATE si cambio > epsilon

Pero **no es prioridad**. Es manejable.

### Tablas legacy a borrar (13 tablas)

```sql
-- Polymarket pausado
DROP TABLE real_sessions;
DROP TABLE real_session_fills;
DROP TABLE redeem_history;

-- Backtest legacy
DROP TABLE backtest_runs;
DROP TABLE monte_carlo_runs;

-- Shadow legacy (lo nuevo es shadow_mode en coin_profiles)
DROP TABLE shadow_snapshots;
DROP TABLE shadow_runs;

-- Sesiones HL legacy
DROP TABLE hl_session_fills;
DROP TABLE hl_sessions;

-- Orders/fills/pnl legacy (ahora todo en hl_trading_history)
DROP TABLE orders;
DROP TABLE fills;
DROP TABLE pnl;

-- Schedule viejo (sustituido por hl_mode_schedule_weekly en V26)
DROP TABLE hl_mode_schedule;

-- Configs antiguas
DROP TABLE trading_config_presets;
DROP TABLE trading_config_active;
```

**Cuando hacerlo:** en una migration `V27__cleanup_legacy.sql` antes de la Fase 1 multi-tenant. Asi empezamos con la DB limpia.

**Riesgo:** verificar primero que ninguna parte del codigo Java las referencia.

---

## 5. Calculos de espacio total proyectados

### Escenario: 100 usuarios activos durante 6 meses

| Tabla | Sin optimizar | Optimizada | Diferencia |
|---|---|---|---|
| **agentbot** | | | |
| `hl_trading_history` | 11 GB | 11 GB | - |
| `hl_trade_micro_features` | 240 GB | 240 GB | (revisar utilidad) |
| `hl_coin_profile_changes` | 5 GB | 5 GB | - |
| `hl_alerts` | 50 GB | 4 GB (TTL) | -46 GB |
| `hl_dir_sessions` | 1 GB | 1 GB | - |
| Otros agentbot | 5 GB | 5 GB | - |
| **Subtotal agentbot** | **312 GB** | **266 GB** | -46 GB |
| **agentbot-platform** | | | |
| `feature_snapshots` | **2.500 GB** | **0.5 GB** | **-2.499 GB** ⚠️ |
| `signal_evaluations` | **1.000 GB** | **36 GB** | **-964 GB** ⚠️ |
| `change_markers` | 0.5 GB | 0.5 GB | - |
| `trade_outcomes` | 5 GB | 5 GB | - |
| Otros platform | 2 GB | 2 GB | - |
| **Subtotal platform** | **3.507 GB** | **44 GB** | **-3.463 GB** |
| **TOTAL** | **3.819 GB** | **310 GB** | **-3.509 GB (92% menos)** |

**Conclusion:**
- **Sin optimizar:** ~4 TB despues de 6 meses con 100 usuarios. **Inviable.**
- **Con optimizaciones:** ~310 GB. **Viable** en 1 servidor con SSD NVMe de 1 TB.

**El 92% del ahorro viene de UN solo cambio:** agregar `feature_snapshots` por trade en lugar de cada 30s.

---

## 6. Que tabla hace cada cosa (mapeo conceptual)

### Datos OPERACIONALES (necesarios para el bot funcionar)

| Tabla | Proposito | TTL? |
|---|---|---|
| `auth_users` | Login | NO |
| `user_wallets` (nueva) | Credenciales encriptadas | NO |
| `user_roles` (nueva) | Tier basic/pro/premium | NO |
| `hl_trading_presets` | Defaults globales por modo | NO |
| `user_preset_overrides` (nueva) | Overrides per user | NO |
| `hl_mode_schedule_weekly` | Schedule per user | NO |
| `hl_coin_profiles` | Perfiles auto-learning per user | NO |
| `hl_coin_profile_stats` | Stats EW per profile | NO |
| `hl_coin_profile_proposals` | Proposals shadow mode | 7 dias |

### Datos HISTORICOS (necesarios para analisis)

| Tabla | Proposito | TTL? |
|---|---|---|
| `hl_trading_history` | Cada trade ejecutado | NO (es la fuente de verdad) |
| `hl_dir_sessions` | Cada sesion start/stop | NO |
| `hl_coin_profile_changes` | Auditoria de cambios del optimizer | 90 dias |
| `change_markers` (platform) | Marcadores de cambios para analisis | NO |
| `trade_outcomes` (platform) | Espejo de trades para platform | NO (mismo que history) |

### Datos TELEMETRICOS (alta cardinalidad, alto volumen)

| Tabla | Hoy | Que hacer |
|---|---|---|
| `feature_snapshots` (platform) | 140 MB/dia/user | **AGREGAR a `trade_snapshots_agg`** |
| `signal_evaluations` (platform) | 56 MB/dia/user | **SOLO ENTER + BLOCKED + sample 1%** |
| `hl_trade_micro_features` | 1.3k/dia/user | **TTL 30 dias** + revisar utilidad |
| `hl_alerts` | 1.4k/dia/user | **TTL 30 dias** |

### Datos LEGACY (nadie los usa)

13 tablas → DROP en migration V27.

---

## 7. Acciones concretas por fase

### Fase 0 — Cleanup pre-multi-tenant (1 dia de trabajo)

**Objetivo:** dejar la DB lista para empezar el refactor.

1. **Migration V27** — drop de tablas legacy:
   ```sql
   -- Verificar primero que el codigo no las referencia
   DROP TABLE IF EXISTS hl_mode_schedule, real_sessions, real_session_fills,
                         redeem_history, backtest_runs, monte_carlo_runs,
                         shadow_snapshots, shadow_runs, hl_session_fills,
                         hl_sessions, orders, fills, pnl,
                         trading_config_presets, trading_config_active;
   ```

2. **TTL cron job** — limpiar datos historicos:
   ```sql
   -- En el platform, programar via pg_cron o systemd timer:
   DELETE FROM feature_snapshots WHERE timestamp < NOW() - INTERVAL '7 days';
   DELETE FROM signal_evaluations WHERE timestamp < NOW() - INTERVAL '30 days';

   -- En el bot:
   DELETE FROM hl_alerts WHERE created_at < NOW() - INTERVAL '30 days';
   DELETE FROM hl_trade_micro_features WHERE created_at < NOW() - INTERVAL '30 days';
   DELETE FROM hl_coin_profile_changes WHERE created_at < NOW() - INTERVAL '90 days';
   ```

3. **Cambio en el bot** — solo enviar a platform los signals accionables:
   ```java
   // En HlDirectionalEngine, donde envia signal:
   if (action.equals("ENTER") || action.equals("BLOCKED") || Math.random() < 0.01) {
       platformBridge.sendSignal(signalMap);
   }
   ```

### Fase 1 — Multi-tenancy con `user_id` (incluida en el doc anterior)

**Objetivo:** anadir `user_id` a las tablas core para soporte multi-tenant.

```sql
-- Migration V28
ALTER TABLE hl_trading_history       ADD COLUMN user_id BIGINT;
ALTER TABLE hl_dir_sessions          ADD COLUMN user_id BIGINT;
ALTER TABLE hl_coin_profiles         ADD COLUMN user_id BIGINT;
ALTER TABLE hl_coin_profile_stats    ADD COLUMN user_id BIGINT;
ALTER TABLE hl_coin_profile_changes  ADD COLUMN user_id BIGINT;
ALTER TABLE hl_coin_profile_proposals ADD COLUMN user_id BIGINT;
ALTER TABLE hl_alerts                ADD COLUMN user_id BIGINT;
ALTER TABLE hl_trade_micro_features  ADD COLUMN user_id BIGINT;
ALTER TABLE hl_mode_schedule_weekly  ADD COLUMN user_id BIGINT;

-- Backfill: todos los datos actuales son del usuario admin (user_id=1)
UPDATE hl_trading_history SET user_id = 1;
-- ... etc

-- Make NOT NULL
ALTER TABLE hl_trading_history ALTER COLUMN user_id SET NOT NULL;
-- ... etc

-- Indices criticos (cada query de un user filtra por user_id)
CREATE INDEX idx_trades_user_time ON hl_trading_history (user_id, entry_at DESC);
CREATE INDEX idx_profiles_user ON hl_coin_profiles (user_id, coin, side, mode);
CREATE INDEX idx_alerts_user ON hl_alerts (user_id, created_at DESC);
-- ... etc
```

### Fase 2 — Refactor `trade_snapshots_agg`

**Objetivo:** matar `feature_snapshots` reemplazandolo por agregados por trade.

1. Crear `trade_snapshots_agg` con campos agregados
2. En el bot, mantener lista en memoria de snapshots del trade activo
3. Al cerrar trade, calcular agregados + JSONB compacto
4. Enviar 1 row al platform en lugar de 200
5. Cron diario que borra `feature_snapshots` antiguas

**Esfuerzo:** 1 dia.

### Fase 3 — Indices y particionado

Cuando llegues a ~50 usuarios y empieces a notar lentitud:

```sql
-- Indices compuestos para queries comunes
CREATE INDEX idx_history_user_coin ON hl_trading_history (user_id, coin);
CREATE INDEX idx_history_user_mode ON hl_trading_history (user_id, mode);
CREATE INDEX idx_markers_user_ts ON change_markers (user_id, timestamp DESC);

-- Particionado por user_id (opcional, solo si la tabla > 10M rows)
ALTER TABLE hl_trading_history ... PARTITION BY HASH (user_id);
```

---

## 8. Estimacion final con 10 amigos (beta)

Tu primer paso son 10 usuarios. Calculo realista:

| Recurso | Estimacion |
|---|---|
| Trades / dia totales | ~300 |
| Inserts / segundo | <1 |
| Updates / segundo | <5 |
| DB total despues de 1 mes | ~1 GB |
| DB total despues de 3 meses | ~3 GB |
| DB total despues de 6 meses | ~6 GB |
| RAM JVM bot | ~3 GB |
| RAM Postgres | ~1 GB |
| **Servidor minimo** | **2 cores / 4 GB RAM / 50 GB SSD** |

**Esto cabe en cualquier VPS de 10€/mes.** Para beta cerrada con 10 amigos, no hace falta optimizar nada todavia. Lo que SI hay que hacer:

1. ✅ Cleanup de tablas legacy (V27) — claridad mental
2. ✅ TTL cron jobs — para que no crezca infinito
3. ✅ Cambio en signals (solo enviar accionables) — 2 lineas
4. ⏸️ Refactor `feature_snapshots` → `trade_snapshots_agg` — opcional para 10, **obligatorio para 100**

---

## 9. Estimacion final con 100 usuarios (target)

| Recurso | Sin optimizar | Optimizado |
|---|---|---|
| DB despues de 6 meses | **~4 TB** ⚠️ | **~300 GB** ✅ |
| Inserts / seg | ~150 | ~10 |
| RAM JVM | 16 GB (heap) | 8-12 GB |
| RAM Postgres | 8-16 GB | 4-8 GB |
| CPU | 4-8 cores | 4 cores OK |
| **Servidor recomendado** | **n/a (inviable)** | **8 cores / 32 GB / 1 TB SSD NVMe** |
| **Coste mensual cloud** | ~500€ | ~80-120€ |

**La diferencia es 4-5x en coste.** Sin las optimizaciones, **el proyecto es inviable economicamente** con 100 usuarios.

---

## 10. Decisiones que necesito que tomes

| # | Decision | Mi recomendacion |
|---|---|---|
| 1 | ¿Drop de las 13 tablas legacy? | ✅ **SI**, en V27 antes de Fase 1 |
| 2 | ¿TTL en `feature_snapshots`? | ✅ **SI**, 7 dias inicial |
| 3 | ¿Refactor a `trade_snapshots_agg`? | ✅ **SI**, antes de los 50 usuarios |
| 4 | ¿Sampling 1% en `signal_evaluations`? | ✅ **SI**, en Fase 0 |
| 5 | ¿TTL `hl_alerts` 30 dias? | ✅ **SI**, en Fase 0 |
| 6 | ¿Mantener `hl_trade_micro_features`? | 🟡 **Revisar** que hace y si vale la pena |
| 7 | ¿Polymarket vuelve algun dia? | Si NO → drop tablas. Si SI → mantenerlas. |
| 8 | ¿Necesitas backups full o solo crit? | Crit minimum: `auth_users`, `user_wallets`, `hl_trading_history`, `change_markers`. El resto es regenerable. |

---

## 11. Lo que NO tienes que guardar

Esta es la parte mas importante: **¿que hay hoy que es ruido?**

### `feature_snapshots` con 30s de granularidad
**Para que sirve:** ver evolucion intra-trade en el platform.
**¿Lo usas hoy?** Probablemente no. El dashboard del platform muestra datos por trade, no intra-trade.
**Veredicto:** ❌ no aporta. Reemplazar por agregado por trade.

### `signal_evaluations` con todas las evaluaciones
**Para que sirve:** auditoria completa de senales.
**¿Lo usas hoy?** Solo para debugging puntual.
**Veredicto:** ❌ no aporta a ese volumen. Solo guardar accionables + sample.

### `hl_trade_micro_features`
**Para que sirve:** features de microestructura (orderbook, tape) por trade.
**¿Lo usas hoy?** Para training ML futuro.
**Veredicto:** 🟡 mantener pero con TTL 30 dias. Si nunca usas para ML, drop completo.

### `hl_alerts`
**Para que sirve:** alertas generadas por el bot.
**¿Lo usas hoy?** UI las muestra.
**Veredicto:** ✅ mantener pero TTL 30 dias.

### `audit_runs`, `audit_findings`
**Para que sirve:** auditoria del platform.
**¿Lo usas hoy?** Solo en investigaciones puntuales.
**Veredicto:** ✅ mantener (es poco volumen).

---

## 12. Checklist de implementacion

**Fase 0 — Cleanup (~1 dia)**
- [ ] Audit del codigo Java: ¿alguna referencia a las 13 tablas legacy?
- [ ] Crear migration V27 con DROP TABLE
- [ ] Probar migration en DB de desarrollo
- [ ] Cron job para TTL `feature_snapshots`
- [ ] Cron job para TTL `signal_evaluations`
- [ ] Cron job para TTL `hl_alerts`
- [ ] Cambio en bot: filtrar signals enviados al platform
- [ ] Build clean + restart + verificar

**Fase 1 — Multi-tenancy schema (~1 dia)**
- [ ] Migration V28: anadir `user_id` a todas las tablas core
- [ ] Backfill: `UPDATE ... SET user_id = 1`
- [ ] Indices compuestos
- [ ] Codigo Java: pasar `user_id` en TODAS las queries
- [ ] Code review: zero query sin filtro `user_id`

**Fase 2 — Refactor agregado (~1-2 dias)**
- [ ] Crear `trade_snapshots_agg`
- [ ] En bot: lista en memoria por trade
- [ ] Calculo de agregados al cerrar
- [ ] Enviar al platform
- [ ] Decommission de `feature_snapshots` (mantener 30 dias por seguridad)
- [ ] Drop de `feature_snapshots` cuando confirme que el agregado funciona

---

## 13. Resumen ejecutivo

### Lo que tienes que recordar

1. **El cuello de botella real es `feature_snapshots`** del platform. Sin agregar, no hay multi-tenant viable.
2. **Drop de 13 tablas legacy** → claridad mental + 10 MB libres.
3. **TTL en alertas y telemetria** → evita crecimiento infinito.
4. **Filtrar signals enviados** → 28k/dia → 500/dia, 56x menos.
5. **`user_id` en todo** → Fase 1 multi-tenant.

### Lo que NO necesitas hacer (mito derribado)

- ❌ NO necesitas particionar tablas con < 10M filas
- ❌ NO necesitas otra base de datos (MongoDB, Cassandra, etc) — Postgres aguanta de sobra
- ❌ NO necesitas sharding — eso es a partir de 1000+ usuarios
- ❌ NO necesitas Kafka / queues — el volumen real es bajo
- ❌ NO necesitas Redis para cache — solo si llegas a problemas reales de latencia

**Postgres + 1 servidor + buenas indices + TTLs = aguanta perfectamente 100 usuarios.**

### El plan de 10 amigos → 100 usuarios

**Fase Beta (10 amigos):**
- Servidor: VPS 4 GB / 2 cores / 50 GB → 10€/mes
- DB: 1-3 GB en 3 meses
- Cleanup: V27 + cron TTLs (Fase 0)
- Multi-tenant minimo: `user_id` en tablas (Fase 1 del doc anterior)
- Sin tocar `feature_snapshots` todavia

**Fase Pre-launch (50 usuarios):**
- Servidor: bare metal o VPS dedicado 8 GB / 4 cores / 100 GB
- DB: 10-20 GB
- Refactor `trade_snapshots_agg` (Fase 2)
- Indices afinados
- Empezar monitoring serio

**Fase Production (100 usuarios):**
- Servidor: dedicado 32 GB / 8 cores / 1 TB SSD NVMe
- DB: 100-300 GB
- Particionado opcional
- Backups diarios cifrados a S3
- Alertas Prometheus + Grafana
- ~80-120€/mes en cloud o ~50€/mes bare metal

---

**Documento vivo. Actualizar conforme cambien las decisiones.**
