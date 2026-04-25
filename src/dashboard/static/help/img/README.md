# Capturas para la ayuda del platform

Coloca aquí las capturas con **exactamente estos nombres** para que la ayuda las monte automáticamente.
Si un fichero no existe, el bloque se renderiza como placeholder ("📷 Captura pendiente") en la UI.

## Convención de nombres

**Nombre base = nombre de la pestaña** (en español, sin tildes, con guión entre palabras):

- `conclusiones.png`, `bot-live.png`, `trades.png`, `shadow-mode.png`, `regimenes.png`, `cambios.png`, `daily-report.png`, `sistema.png`, `research-lab.png`, `validation.png`, `reports.png`, `features.png`, `audit.png`, `contrato.png`

**Páginas que requieren scroll:** añade sufijo numérico `-1`, `-2`, `-3`… según el orden de aparición al hacer scroll.
- `conclusiones.png` → parte superior
- `conclusiones-1.png` → al hacer scroll
- `conclusiones-2.png` → si hay aún más al final

**Vistas distintas dentro de la misma pestaña** (modales, drawers, formularios): sufijo descriptivo.
- `trades.png` → lista principal
- `trades-detalle.png` → drawer lateral del detalle
- `shadow-mode-form.png` → formulario de crear variante
- `shadow-mode-exits.png` → sección "Comparativa de salidas"

## Especificaciones comunes

- Resolución mínima: **1440×900** (preferible 1920×1080)
- Tema: **dark mode**
- Formato: **PNG** (capturas) o **MP4/WebM** (videos)
- Sin datos sensibles: tapa saldos absolutos y tu wallet/keys si aparecen (puedes pintar encima con cualquier editor)
- Si un panel está vacío, captura el "estado vacío" — la ayuda puede explicar qué mostraría con datos

---

## Tier 1 · esenciales

| Archivo(s) | Qué capturar |
|---|---|
| `conclusiones.png` (+ `conclusiones-1.png`, `conclusiones-2.png` si hay scroll) | Tab **Conclusiones** completa |
| `shadow-mode.png` | Tab **Shadow Mode** con la tabla de variantes |
| `shadow-mode-form.png` | **Formulario "Crear variante"** con la caja "Overrides de salida" visible |
| `shadow-mode-exits.png` | Sección **"Comparativa de salidas"** con winRate y Σ ΔPnL |
| `trades.png` (+ `trades-1.png` si hace falta) | Tab **Trades** con la lista |
| `trades-detalle.png` | Drawer/modal de detalle de un trade con timeline de snapshots |
| `research-lab.png` (+ `research-lab-1.png` si hay scroll) | **Research Lab** en vista Overview |

## Tier 2 · complementarios

| Archivo(s) | Qué capturar |
|---|---|
| `regimenes.png` | Tab **Regímenes**: grid con el régimen actual por coin |
| `cambios.png` (+ `cambios-1.png` si hay scroll) | Tab **Cambios**: un marker desplegado con before/after |
| `validation.png` (+ scroll si aplica) | Tab **Validation**: un batch con veredicto ADOPT/TEST_LIVE/REJECT |
| `daily-report.png` (+ `daily-report-1.png`) | Tab **Daily Report** con al menos 1 sección abierta |
| `audit.png` | Tab **Audit**: health score + findings |

## Tier 3 · bonus

| Archivo | Qué capturar |
|---|---|
| `bot-live.png` | **Bot Live** con gate breakdown |
| `features.png` | **Features** con grid de una coin |
| `sistema.png` | **Sistema** con disk + ingestion |
| `reports.png` | **Reports** con un informe abierto |
| `contrato.png` | **Contrato** con tabla de features |

## Videos (opcional, alto valor comercial)

| Archivo | Duración | Contenido |
|---|---|---|
| `tour.mp4` | ~60s | Recorrido por las 4 capas: Operations → Research → Governance → Ayuda |
| `shadow-workflow.mp4` | ~60s | Crear variante con template → explicar cómo leer el resultado |

## Branding

| Archivo | Descripción |
|---|---|
| `logo.svg` | Logo vectorial (preferible, fondo transparente) |
| `logo.png` | Fallback PNG si no hay SVG |
