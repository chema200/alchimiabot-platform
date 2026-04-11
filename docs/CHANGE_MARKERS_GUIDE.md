# Change Markers — Guia completa

> Sistema para registrar cambios en el bot y medir su impacto real (antes / despues) sobre los trades.

---

## Por que existe

Cada vez que cambias algo en el bot (un parametro, un modo, aceptas una proposal del optimizer, salta una proteccion…), el efecto se diluye en las metricas globales. Si miras "Conclusiones" ves un win-rate medio de los ultimos 7 dias y nunca sabes si el cambio que hiciste **ayer** mejoro o empeoro las cosas.

Los **change markers** son eventos timestamped que se guardan en la DB del platform con dos cosas:
- **Que cambio** (categoria, etiqueta, parametro, valor antes / despues, contexto coin/side/mode)
- **Cual fue el impacto** (metricas de los 20 trades anteriores vs los 20 trades posteriores al evento)

Esto permite ver, en tiempo real y de forma aislada, **si una decision concreta mejoro el rendimiento** o no.

---

## Que se marca automaticamente

El bot Java emite markers sin que tengas que hacer nada en estos eventos:

| Categoria | Cuando se dispara | Donde en el codigo |
|---|---|---|
| `MODE_CHANGE` | Cambio manual de modo (boton SCALP/NORMAL/SWING) | `HlDirectionalEngine.setManualMode()` |
| `MODE_CHANGE` | Cambio automatico por horario (schedule) | `HlDirectionalEngine.checkModeSchedule()` |
| `MODE_CHANGE` | Modo defensivo de fin de semana (SWING auto) | mismo |
| `PRESET_EDIT` | Pulsar **"Aplicar cambios"** en la pestana Config Trading (con &gt;= 1 parametro modificado) | `HyperliquidController.updateTradingConfig()` |
| `PROPOSAL_ACCEPTED` | Cuando aceptas una proposal del optimizer en shadow mode | `CoinProfileOptimizer.acceptProposal()` |
| `PROTECTION` | Cuando salta StoplossGuard o MaxDrawdown | `HlDirectionalEngine.scanForEntries()` |

**No hay marker manual independiente.** Los cambios se marcan **automaticamente al pulsar "Aplicar cambios"** del Config. Si solo quieres registrar un evento descriptivo sin tocar params, edita un campo intrascendente del config (o vuelve a aplicar el mismo valor de un campo: si no hay diff, no se emite marker).

Cada marker incluye automaticamente:
- timestamp en UTC
- `config_snapshot` con el estado completo del config en ese momento
- contexto cuando aplica: coin / side / mode / parameter / old_value / new_value

---

## Como se generan en la practica

### Flujo normal: editar Config Trading
1. Abre la pestana **Hyperliquid** en `bot.alchimiabot.com`.
2. Ve a la seccion **"Config Trading"**.
3. Cambia los parametros que quieras (uno o varios).
4. Pulsa **"Aplicar cambios"**.
5. El backend detecta automaticamente que parametros cambiaron, los persiste en DB **y crea un marker `PRESET_EDIT`** con la etiqueta `Preset {MODE}: {N} params changed` y la lista completa de cambios en la descripcion.
6. Si solo cambio 1 parametro, la etiqueta es mas precisa: `Preset NORMAL: minScoreTotal 60.0 -> 56.0`.

> Si pulsas "Aplicar cambios" sin haber modificado nada (mismo valor que ya tenia), **no se emite marker** porque no hay diff.

### Otros eventos automaticos
- **Cambio de modo manual** (botones SCALP/NORMAL/SWING) → marker `MODE_CHANGE`
- **Aceptar proposal del optimizer** → marker `PROPOSAL_ACCEPTED`
- **StoplossGuard / MaxDrawdown** → marker `PROTECTION`
- **Schedule horario** cambia el modo → marker `MODE_CHANGE` source=BOT_AUTO

### Solo testing (curl directo)
```bash
curl -X POST http://localhost:8090/api/bot/marker \
  -H "Content-Type: application/json" \
  -d '{"category":"TEST","label":"smoke test","source":"USER"}'
```

---

## Como se calcula el impacto

Cuando un marker tiene mas de 30 minutos de antigueedad, el platform calcula automaticamente su `impact_data`. Tambien puedes forzar el recalculo desde la pestana **Cambios** con el boton "Recalcular".

### Logica del calculo

1. **Buscar trades anteriores**: los **20 trades** anteriores al timestamp del marker.
   - Si el marker tiene contexto (`coin`, `side`, `mode`), solo cuenta trades de ese contexto.
2. **Buscar trades posteriores**: los **20 trades** posteriores al timestamp del marker, mismo filtro.
3. **Fallback**: si en cualquiera de las dos ventanas hay menos de **10 trades**, se cambia a **6 horas antes / 6 horas despues** del timestamp (en lugar de count-based).
4. **Si tras el fallback siguen siendo &lt; 10 trades en cualquier ventana** → status `INSUFFICIENT_DATA`.

### Metricas calculadas (antes y despues)

| Campo | Significado |
|---|---|
| `trades` | numero de trades en la ventana |
| `wins` / `losses` | trades con net_pnl &gt; 0 / &lt; 0 |
| `wr` | win rate (%) |
| `expectancy` | media de net_pnl por trade |
| `pf` | profit factor (gross_wins / gross_losses) |
| `sl_rate` | % de trades cerrados con exit_reason = "SL" |
| `total_pnl` | suma de net_pnl |
| `avg_pnl` | igual a expectancy |

### Calculo del estado (`impact_status`)

Se calcula un score ponderado:

```
score = (Δ_expectancy * 0.40)
      + (Δ_wr / 100 * 0.30)
      + (Δ_pf * 0.20)
      + (-Δ_sl_rate / 100 * 0.10)
```

| score | impact_status | color UI |
|---|---|---|
| `> 0.10` | `IMPROVED` | verde |
| `< -0.10` | `WORSENED` | rojo |
| `[-0.10, 0.10]` | `NEUTRAL` | amarillo |
| (sin datos suficientes) | `INSUFFICIENT_DATA` | gris |

> Nota: el threshold 0.10 es deliberadamente generoso para no marcar como "mejora" cualquier ruido pequeno. Ajustable en `marker_service.py:191`.

---

## Donde verlos en el platform

### Pestana **Cambios** (Operations / Cambios)

- Lista completa de los markers de los ultimos 7/30/90 dias (selector arriba a la derecha).
- Filtros por categoria (MANUAL, MODE_CHANGE, PRESET_EDIT, etc.).
- Tabla con: timestamp, categoria, etiqueta, contexto, estado, WR antes/despues, expectancy antes/despues, Δ expectancy, SL rate antes/despues.
- Boton **"Recalcular"** por fila para forzar re-calculo del impacto.

### Pestana **Conclusiones** (executive summary)

- Nueva seccion **"Cambios Recientes con Impacto"** justo antes de "Cola de Acciones".
- Muestra los **5 markers mas recientes** que ya tienen impacto calculado.
- Resaltado de Δ verde / rojo segun mejore o empeore.

---

## Esquema de la tabla `change_markers`

```sql
id                   bigint PRIMARY KEY
timestamp            timestamptz NOT NULL DEFAULT NOW()  -- cuando ocurrio el evento
category             varchar(30) NOT NULL                -- MANUAL, MODE_CHANGE, PRESET_EDIT, etc.
label                varchar(200) NOT NULL               -- titulo corto
description          text                                -- detalles opcionales
source               varchar(20) NOT NULL                -- USER | BOT_AUTO
coin                 varchar(20)                         -- contexto opcional
side                 varchar(10)                         -- contexto opcional
mode                 varchar(20)                         -- contexto opcional
parameter            varchar(50)                         -- nombre del param tocado
old_value            float                               -- valor anterior (numerico)
new_value            float                               -- valor nuevo (numerico)
batch_id             varchar(50)                         -- para agrupar markers relacionados
batch_label          varchar(200)
config_snapshot      jsonb                               -- snapshot completo del config
impact_status        varchar(20) DEFAULT 'PENDING'       -- PENDING|IMPROVED|WORSENED|NEUTRAL|INSUFFICIENT_DATA
impact_data          jsonb                               -- before/after metrics + deltas
impact_calculated_at timestamptz
created_at           timestamptz DEFAULT NOW()

-- Indices
idx_markers_timestamp        (timestamp DESC)
idx_markers_category         (category)
idx_markers_coin_side_mode   (coin, side, mode)
idx_markers_batch            (batch_id)
```

---

## Endpoints REST

Todos protegidos por auth (cookie `platform_token` o header `Authorization: Bearer <token>`), excepto `/api/bot/marker` que es interno.

| Metodo | Path | Descripcion |
|---|---|---|
| `GET` | `/api/markers?limit=50&days=90` | Lista de markers (mas recientes primero) |
| `GET` | `/api/markers/recent-impacts?limit=10` | Lista de markers con impacto pre-calculado (calcula on-demand si tiene &gt; 30 min) |
| `GET` | `/api/markers/{id}` | Detalle de un marker |
| `POST` | `/api/markers` | Crear marker manual desde el dashboard |
| `POST` | `/api/markers/{id}/recalculate` | Forzar recalculo del impacto |
| `POST` | `/api/bot/marker` | **Interno** — usado por el bot Java (PlatformBridge.sendMarker) |

---

## Buenas practicas

1. **Marca cualquier cambio que hagas a mano** que pueda afectar al rendimiento. Incluso si crees que es trivial. Es la unica forma de saber a posteriori si fue buena idea.
2. **Espera al menos 20 trades posteriores** antes de juzgar el impacto de un cambio. Si pones `INSUFFICIENT_DATA` con &lt; 10 trades, no significa que el cambio no haya servido — significa que no se puede medir aun.
3. **Si haces varios cambios a la vez**, los markers se van a solapar y atribuirse impacto cruzado. Para evitarlo:
   - O bien haces los cambios de uno en uno y esperas 20-30 trades entre cada uno.
   - O bien marcas un solo marker con `batch_label` describiendo todo el bloque.
4. **Recalcula** un marker antiguo cuando quieras una vision actualizada (la primera calculacion se hace a los 30 min — antes de eso solo tienes los trades de la primera media hora despues).
5. **No borres markers** aunque el resultado sea malo. La historia es la base de aprendizaje.

---

## Ejemplo: ciclo completo

Hoy 10 abril, 11:00:
1. Bajas `minScore` de 60 → 56 desde la pestana Config del bot.
2. Pulsas **"Aplicar cambios"**.
3. El controller emite automaticamente un marker `PRESET_EDIT` con label `Preset NORMAL: minScoreTotal 60.0 -> 56.0` y descripcion con el diff completo.
4. El bot sigue trading. Pasan 30 trades en las siguientes 4 horas.
5. A las 15:00 vas a la pestana **Cambios** del platform y pulsas **Actualizar**. Ves:
   - El marker `PRESET_EDIT` con `IMPROVED`, Δ wr `+8.0%`, Δ expectancy `+0.12`.
   - Confirmas que la decision fue buena.
6. Si el resultado fuese `WORSENED`, sabes que tienes que revertirlo o ajustar mas.

---

## Roadmap (no implementado todavia)

- **Auto-revert** opcional: si un marker tiene `WORSENED` con score &lt; -0.20 tras 50 trades, sugerir revertir el cambio automaticamente.
- **Comparacion entre markers**: superponer dos markers para ver cual fue mas efectivo.
- **Notificacion Telegram**: cuando un cambio importante (ej PRESET_EDIT) acumula 20 trades posteriores, enviar el resultado del impacto.
- **Boton crear desde el dashboard**: ahora solo puedes crear markers desde el bot frontend o curl. Anadir un boton "+" en la pestana Cambios.

---

**Archivos relevantes:**
- Migracion: `alembic/versions/c1f8a2e5d9b4_change_markers.py`
- Modelo ORM: `src/storage/postgres/models.py` → `class ChangeMarker`
- Servicio: `src/quant/markers/marker_service.py`
- Receptor del bot: `src/ingestion/rest/bot_receiver.py:313` `POST /api/bot/marker`
- Endpoints dashboard: `src/dashboard/api.py:807-832`
- UI Cambios + integracion Conclusiones: `src/dashboard/static/index.html` (busca `CambiosTab` y `recentMarkers`)
- Bridge Java: `back/src/main/java/com/agentbot/hyperliquid/PlatformBridge.java` → `sendMarker()`
- Helper engine: `back/src/main/java/com/agentbot/hyperliquid/HlDirectionalEngine.java` → `markChange()`
