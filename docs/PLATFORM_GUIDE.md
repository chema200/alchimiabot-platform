# Alchimia Platform — Guia Tecnica Completa

## Filosofia general

El platform es una **capa de analisis read-only** sobre los datos del bot. NO ejecuta trades, NO modifica el bot, NO aplica recomendaciones automaticamente. Todo lo que ves en el platform son datos que ya pasaron + analisis sobre esos datos.

**Regla de oro:** El bot envia datos al platform via PlatformBridge (fire-and-forget). El platform los almacena, los analiza, y te muestra conclusiones. **Nunca** vuelve nada del platform al bot. Si quieres aplicar un cambio sugerido, lo haces tu manualmente.

---

## Persistencia de datos: que se guarda y que no

### Datos PERMANENTES (no se borran automaticamente)
| Tabla | Que guarda | Crece? |
|---|---|---|
| `trade_outcomes` | Cada trade ejecutado | Si, indefinidamente |
| `trade_verdicts` | Analisis automatico de cada trade | Una vez por trade |
| `signal_evaluations` | Cada senal evaluada (ENTER/SKIP/BLOCKED) | Si, indefinidamente |
| `regime_labels` | Clasificacion de regimen por coin | Si, snapshots periodicos |
| `feature_snapshots` | Estado de features cada 60s | Si, indefinidamente |
| `audit_runs` + `audit_findings` | Resultados de auditorias | Si, ultimos 100 visibles |
| `trade_snapshots` | Snapshots de posicion cada 30s | **Si, mucho — limpieza manual** |

### Datos VOLATILES (no persisten)
- **Conclusiones, decisiones, recomendaciones** → se calculan al vuelo cada vez que abres la pestana
- **Reports operativos** → se calculan al vuelo
- **Daily report** → se genera al vuelo cuando lo pides
- **Validacion** → se ejecuta y se descarta (no se guarda)
- **Lab/Research** → se calcula al vuelo
- **Features/Regimes en vivo** → estado en memoria del feature store

### Datos que SI se borran manualmente
- **trade_snapshots**: en la pestana Trades hay botones "Limpiar +7d" y "Limpiar +3d" que borran snapshots antiguos. Es la unica tabla que crece mucho.

### Datos que NO se borran nunca
Por diseno, los trades, verdicts, signals y findings no tienen limpieza automatica. Si quieres limpiar, lo haces tu manualmente con SQL.

---

## Como funciona cada pestana

### 1. CONCLUSIONES — `ConclusionsTab`

**Que es:** Resumen ejecutivo del estado del sistema. Te dice "esto es lo que esta pasando, esto es lo que funciona, esto es lo que no, esto es lo que harias ahora si fueras yo".

**De donde sacan los datos:** `/api/quant/executive-summary` que combina:
- Trades reales de `trade_outcomes`
- Verdicts de `trade_verdicts`
- Signal evaluations
- Score parity analysis
- Decisions engine

**Que muestra:**
- **System status**: estado general del edge (POSITIVE/NEGATIVE/UNKNOWN), confianza, calidad de datos
- **Live evidence**: trades, WR, expectancy, PF, net PnL, max DD, Sharpe
- **Score parity**: cobertura de scores, anomalias
- **Joint conclusions**: clasifica la evidencia (CONFIRMED, LIKELY, CONFLICTING, INVALID)
- **What works / What fails / What to change / Don't touch**: 4 columnas con insights
- **Top recommendations**: decisiones priorizadas con impacto esperado
- **Next best action**: la accion mas urgente

**Como se actualiza:** Al cargar la pestana o al pulsar "Actualizar". Una sola peticion, sin polling.

**Se borra automaticamente?** NO. Las conclusiones se **recalculan** desde cero cada vez que las pides. No hay nada que limpiar — son resultado de procesar los datos crudos.

**Que pasa si yo aplico una recomendacion?** El platform no se entera. Los siguientes trades reflejaran tu cambio. Las conclusiones seguiran cambiando segun los datos que vayan llegando.

**Acciones del usuario:**
- **Refresh**: re-calcula todo
- **Export JSON**: descarga snapshot del momento

---

### 2. BOT LIVE — `BotLiveTab`

**Que es:** Monitor en tiempo real de lo que esta haciendo el bot AHORA.

**De donde sacan los datos:**
- `/api/bot/trades/stats` (resumen)
- `/api/bot/trades` (ultimos 100 trades)
- `/api/bot/signals/stats` (decisiones de senales)

**Que muestra:**
- Trades del dia, wins/losses, WR
- PnL bruto, fees, neto
- Avg win / avg loss
- Senales: ENTER, SKIP, BLOCKED counts
- Tabla de ultimos 100 trades

**Como se actualiza:** **Auto-polling cada 5 segundos**. Tu no haces nada, se refresca solo.

**Se borra?** No. Es una vista live de lo que esta en `trade_outcomes`. Los datos viven ahi para siempre, la pestana solo muestra los ultimos 100.

**Acciones del usuario:** Ninguna, es read-only.

---

### 3. TRADES — `TradesTab`

**Que es:** Historial detallado de todos los trades + analisis post-mortem por trade.

**De donde sacan los datos:**
- `/api/trades?limit=100&offset=X` — lista paginada
- `/api/trades/{id}` — detalle de un trade
- `/api/trades/snapshots/stats` — uso de disco
- `/api/trades/snapshots/cleanup?days=N` — limpia snapshots viejos

**Que muestra (lista):**
Tabla con: fecha, coin, side, modo, PnL neto, MFE, MAE, salida, veredicto, quality

**Que muestra (detalle):**
- Veredicto: GOOD / ACCEPTABLE / BAD / TERRIBLE con explicacion
- Entry timing, MFE captura %, tiempo en profit, fee killed
- Entry/exit prices, PnL desglosado
- Scores (signal, trend, micro)
- **Timeline con snapshots**: cada 30s del trade, precio, SL, HWM, PnL%
- Mejoras: lista de cosas que se podrian haber hecho mejor
- Counterfactual: que habria pasado con SL mas ancho, TP mas corto, etc.

**Como se actualiza:** Carga al abrir la pestana. Los detalles se cargan al hacer click en un trade.

**El veredicto es automatico?** Si. Si abres un trade que no tiene veredicto, el `TradeAnalyzer` lo genera al vuelo y lo guarda en `trade_verdicts`. Una vez generado, queda guardado para siempre.

**Se borra automaticamente?** 
- **Trades y veredicts**: NO, nunca
- **Snapshots**: tu los borras manualmente con los botones "Limpiar +7d" / "Limpiar +3d"

**Por que limpiar snapshots?** Cada trade genera ~30 snapshots (uno cada 30s). Con 100 trades al dia, son 3000 snapshots/dia. Si no limpias, la tabla crece sin parar. Los snapshots viejos solo sirven para revisar trades de mas de una semana atras — para el dia a dia no los necesitas.

**Acciones del usuario:**
- Click en trade → ver detalle + generar veredicto
- "Limpiar +7d" → borra snapshots de mas de 7 dias
- "Limpiar +3d" → borra snapshots de mas de 3 dias

---

### 4. DAILY REPORT — `DailyReportTab`

**Que es:** Informe diario automatico de 12 secciones con todo lo que paso ese dia.

**De donde sacan los datos:** `/api/daily-report/{date}` — se genera al vuelo consultando todas las tablas.

**Que muestra:**
1. **General**: modo dominante, trades, PnL bruto/fees/neto
2. **Sistema**: bot running, platform running, WS estable, CPU%, RAM%
3. **Integracion**: trades del bot vs trades del platform, diff, duplicados
4. **PnL**: wins, losses, WR, PF, avg W/L, fee killed count
5. **Senales**: ENTER, SKIP, BLOCKED counts
6. **Features**: snapshots del dia, coins con features
7. **Storage**: uso de disco, archivos parquet del dia
8. **Trading analysis**: mejores/peores coins
9. **Problemas**: issues detectados
10. **Exit reasons**: breakdown por tipo de salida
11. **Scores**: data quality, trading, system (0-10)
12. **Recomendaciones**: que hacer mañana

**Como se actualiza:** Al abrir la pestana muestra el dia de hoy. Puedes navegar a los ultimos 14 dias.

**Se guarda?** NO. Cada vez que abres la pestana se calcula desde cero. No hay tabla `daily_reports` — todo se computa de los datos crudos.

**Por que es asi?** Para que siempre refleje el estado mas actualizado. Si añades verdicts despues, el reporte de ayer mejora.

**Acciones del usuario:** Click en cualquier dia para verlo.

---

### 5. SISTEMA — `SystemTab`

**Que es:** Salud tecnica del sistema (no del trading).

**Datos:**
- `/api/status` — estado general
- `/api/system/disk` — disco

**Que muestra:**
- API status, coins tracking, eventos ingeridos
- Disk usage (total, usado, libre, %)
- Severidad disco (ok/warning/critical)
- Crecimiento estimado (24h, dias restantes)
- Desglose por directorio

**Auto-refresh:** Cada 3 segundos para status, cada 10 segundos para disco.

**Se persiste algo?** NO. Es un snapshot en vivo del sistema. No hay tabla.

---

### 6. CAPTURA — `CaptureTab`

**Que es:** Pipeline de ingestion de datos.

**Que muestra:**
- Eventos ingeridos del WebSocket
- Archivos parquet creados
- Tamaño de datos crudos (GB)
- Coins trackeados
- Computes de features
- Cache hits del feature store

**Auto-refresh:** Continuo.

**Se persiste:** Los eventos se guardan en parquet en disco. La pestana solo muestra contadores en memoria.

---

### 7. RESEARCH LAB — `LabTab` (12 sub-vistas)

**Que es:** El cerebro analitico. Aqui es donde realmente se analiza todo.

**Datos (todo via /api/quant/full y endpoints relacionados):**
- Trades enriched (trades + signals + features + regime)
- Metrics computados
- Analysis con patrones detectados
- Decisions generadas

**Sub-vistas:**

1. **Overview** — KPIs y decisiones principales
2. **Metrics** — Breakdown por coin/side/score/exit/mode
3. **Analysis** — Patrones detectados, fee impact, holding time
4. **Score** — Cobertura de scores
5. **Mode** — Performance por modo
6. **Config** — Performance por version de config
7. **Rejections** — Por que se rechazaron senales
8. **Counterfactual** — "Que habria pasado con threshold X"
9. **Diagnostic** — Pass rates de cada filtro
10. **Quality** — Analisis de quality labels
11. **Experiments** — Link a Validation
12. **Decisions** — Sugerencias automaticas

**Auto-refresh:** No. Manual con boton "Refresh".

**Se persiste:** Nada. Todo se calcula al vuelo desde los datos crudos.

**Por que es asi?** Para que siempre refleje los datos mas actualizados. Si añades trades nuevos, las metricas cambian al instante.

**Que hago si una decision me convence?** Tu la aplicas manualmente al bot. El platform no la aplica.

---

### 8. VALIDATION — `ValidationTab`

**Que es:** Sistema formal de validacion de cambios. Antes de aplicar un cambio al bot, lo pruebas aqui.

**Como funciona:**
1. Ejecuta 3 batches de experimentos sobre los trades historicos:
   - **score_threshold**: prueba diferentes minScores
   - **trailing_optimization**: prueba diferentes trailings
   - **sl_fees**: prueba diferentes SL/TP
2. Cada experimento simula como habrian ido los trades con esa config
3. Genera un veredicto por batch:
   - **ADOPT**: el cambio mejora claramente, aplicar
   - **TEST_LIVE**: prometedor, probar en shadow primero
   - **REJECT**: empeora, no aplicar
   - **INCONCLUSIVE**: muestra insuficiente

**Datos:** `/api/quant/validation?mode=live_trades` o `mode=replay_historical`

**Tarda:** ~30 segundos por ejecucion completa.

**Se persiste:** NO. Cada vez que ejecutas, se calcula desde cero.

**Acciones:**
- Toggle modo (live trades vs replay historico)
- "Run Full Validation" → ejecuta los 3 batches
- "Re-run" → vuelve a ejecutar
- Export JSON

**Importante:** Los veredictos son **advisory**. Si dice ADOPT, no se aplica nada solo. Tu decides si aplicar el cambio al bot.

---

### 9. REPORTS — `ResearchTab`

**Que es:** 11 reportes operativos pre-definidos.

**Reportes:**
1. daily_summary
2. wr_by_coin
3. wr_by_side
4. wr_by_hour
5. pnl_by_mode
6. pnl_by_tag
7. pnl_by_exit_reason
8. fee_analysis
9. poison_coins (coins que pierden consistentemente)
10. rescuable_coins (casi rentables)
11. signal_blocked_vs_entered

**Datos:** `/api/reports/full`

**Auto-refresh:** No. Manual.

**Se persiste:** NO. Calculado al vuelo.

---

### 10. FEATURES — `FeaturesTab`

**Que es:** Las 48 features que se calculan en vivo por coin.

**Que muestra:** Selector de coin → tabla de feature → valor.

**Datos:** `/api/features` o `/api/features/{coin}` desde el FeatureStore en memoria.

**Se persiste:** Si, snapshots cada 60s en `feature_snapshots`. Pero la pestana solo muestra el valor actual.

---

### 11. REGIMENES — `RegimesTab`

**Que es:** Clasificacion de regimen de mercado por coin.

**Regimenes posibles:** trending_up, trending_down, choppy, high_vol, quiet

**Datos:** `/api/regimes` desde el RegimeDetector. Se guarda historico en `regime_labels`.

**Auto-refresh:** Cada 5 segundos.

---

### 12. AUDIT — `AuditTab`

**Que es:** Auditorias automaticas de integridad y salud del sistema.

**Checks (ejecutan en background segun horario):**
- **integration** (cada 5 min): bot ↔ platform alineados?
- **data_quality** (cada 15 min): scores faltantes, inconsistencias?
- **storage** (cada 1 hora): disco lleno?
- **consistency** (cada 6 horas): integridad cross-tablas?

**Datos:**
- `/api/audit/status` — estado actual
- `/api/audit/findings` — findings activos
- `/api/audit/run` — ejecutar todos ahora
- `/api/audit/history` — historico

**Que muestra:**
- Health score global (0-100, minimo de los checks)
- Findings activos
- Tabla por check con: estado, score, findings, ultima ejecucion
- Tabla de findings: severidad, codigo, mensaje

**Se persiste:** SI, en `audit_runs` y `audit_findings`. Acumula todo.

**Se limpia automaticamente?** NO. Los findings se acumulan hasta que los borres manualmente.

**Acciones:** "Audit Now" → ejecuta todos los checks ya.

---

### 13. CONTRATO — `ContractTab`

**Que es:** Especificacion formal de las 48 features.

**Para que sirve:** Garantizar que live = replay = training tienen las mismas features.

**Datos:** `/api/feature-contract` desde codigo hardcoded en `contract.py`.

**Versionado:** Si, cada cambio incrementa version.

---

### 14. AYUDA — `HelpTab`

Texto estatico con guia de uso. No tiene datos.

---

## Que pasa cuando yo aplico una recomendacion?

**No pasa nada en el platform.** El platform no detecta que aplicaste el cambio. Lo que pasa es:

1. Tu cambias algo en el bot (config, profile, lo que sea)
2. El bot empieza a tradear con la nueva config
3. Los nuevos trades llegan al platform via PlatformBridge
4. Las pestanas del platform reflejan automaticamente los nuevos datos en el siguiente refresh
5. Las recomendaciones cambiaran cuando haya suficientes trades nuevos para que las metricas reflejen el cambio

**Por eso:** las recomendaciones del platform son **monoticas** — siempre muestran lo mejor que pueden con los datos que tienen. No "saben" que ya las aplicaste, simplemente trabajan con los datos.

---

## Como se borran/limpian las recomendaciones?

**No se borran porque no se almacenan.**

Las conclusiones, decisiones, recomendaciones, daily reports, validation results — todo se **calcula al vuelo** desde los datos crudos. No hay tabla que las guarde.

Si quieres "borrar" una recomendacion, la unica forma es **borrar los datos que la generan**. Pero eso es destructivo y no recomendado.

Si una recomendacion deja de aparecer, es porque los datos cambiaron y ya no la justifican.

---

## Auto-actions del platform (lo unico que se ejecuta solo)

1. **AuditRunner**: ejecuta los 4 checks en sus horarios. Guarda resultados en DB.
2. **TelegramSummaryService**: cada 4 horas envia resumen al Telegram. NO modifica nada.
3. **TradeAnalyzer.analyze()**: cuando abres un trade sin veredicto, lo genera y lo guarda. Una vez por trade.

**Eso es todo.** Ninguna otra cosa se ejecuta automaticamente. No hay scheduled jobs que apliquen recomendaciones, no hay limpieza automatica de tablas, no hay reentrenamiento de modelos.

---

## Resumen visual del flujo

```
BOT (Java)
  ↓ envia trades, signals, snapshots
PlatformBridge
  ↓
PostgreSQL (trade_outcomes, signal_evaluations, etc.)
  ↓
Endpoints /api/* leen datos
  ↓
Pestanas del dashboard MUESTRAN datos
  ↓
Tu LEES, decides, y aplicas cambios manualmente al bot
  ↓
(loop)
```

**El platform nunca toca el bot.** Es 100% read-only desde el punto de vista del bot.

---

## Limpieza practica

Si el disco se llena o quieres "empezar limpio":

```sql
-- Borrar snapshots viejos (la tabla que mas crece)
DELETE FROM trade_snapshots WHERE timestamp < NOW() - INTERVAL '7 days';

-- Borrar audit runs viejos
DELETE FROM audit_runs WHERE started_at < NOW() - INTERVAL '30 days';

-- Borrar findings viejos
DELETE FROM audit_findings WHERE created_at < NOW() - INTERVAL '30 days';

-- NUNCA borres trade_outcomes ni signal_evaluations sin estar muy seguro
-- Esos son tu historial para todo el analisis
```

O desde la UI: la pestana **Trades** tiene botones "Limpiar +7d" y "Limpiar +3d" que solo borran `trade_snapshots`.

---

## FAQ rapida

**P: Por que las conclusiones cambian cada vez que las abro?**
R: Porque se recalculan al vuelo. Mas trades = mas datos = mejor analisis.

**P: Como aplico una decision?**
R: Manualmente. El platform es advisory. Tu cambias el bot.

**P: Donde se guardan las decisiones?**
R: No se guardan. Se computan cada vez.

**P: Si reinicio el platform pierdo algo?**
R: No. Todo lo importante esta en PostgreSQL.

**P: Por que la pestana Trades muestra "Limpiar +7d"?**
R: Porque `trade_snapshots` crece mucho (1 cada 30s × cada trade). El resto de tablas no necesita limpieza.

**P: La validacion ADOPT, se aplica sola?**
R: NO. Tu lo aplicas manualmente al bot si te convence.

**P: Que pasa si el bot esta caido?**
R: El platform sigue funcionando con los datos historicos. No genera trades nuevos pero puedes analizar lo que ya tienes.

**P: Como se que un trade ya tiene veredicto?**
R: Aparece la columna "Verdict" en la lista de Trades. Si esta vacia, se genera al hacer click en el trade.
