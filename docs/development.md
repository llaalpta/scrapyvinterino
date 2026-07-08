# Desarrollo Local

El entorno local no depende de Traefik ni del servidor remoto.

## Requisitos

- Python 3.12.
- Node 22.
- pnpm.
- Docker Desktop con Compose.

## Arranque

```powershell
copy .env.example .env
docker compose up --build
```

## Flujo de Ramas

El flujo de desarrollo SDD esta documentado en `docs/sdd-process.md`. Para trabajo no trivial, usa una rama corta por spec o fix, creada desde `develop`, y prepara PR de vuelta a `develop`.

Si `develop` no existe localmente, no sigas acumulando cambios en una rama larga por defecto. Confirma primero si hay que crear `develop`, traerlo de remoto o tratar el cambio como excepcion puntual.

## Base de Datos Local

La base de datos local es descartable mientras el producto siga en desarrollo puro. No hay datos reales que conservar.

Hasta la primera version de produccion no se mantiene compatibilidad hacia atras con desarrollos previos. Si un modelo, endpoint, payload, migracion o flujo de UI queda obsoleto, se elimina en vez de mantener adaptadores legacy.

Para regenerar el esquema desde cero:

```powershell
docker compose down -v
docker compose up -d --build
```

Las migraciones Alembic pueden compactarse o romper compatibilidad con datos locales anteriores cuando el cambio simplifique el modelo.

## Puertos

- `5173`: frontend Vite.
- `8000`: API FastAPI.
- `5432`: Postgres local.

## PWA QA estable

Para QA de la PWA, usa la ruta aislada:

```powershell
.\scripts\qa-pwa.ps1 start
```

El script levanta backend/worker/cache con Docker Compose, arranca Vite local en `http://127.0.0.1:5176`, configura `VITE_DEV_API_PROXY_TARGET=http://localhost:8000` y guarda PID/logs en `%TEMP%\scrapyvinterino-qa`. No mata procesos ajenos: si el puerto esta ocupado por otro proceso, falla con un mensaje claro.

Antes de recrear contenedores o arrancar otro Vite:

```powershell
.\scripts\qa-pwa.ps1 stop
docker compose ps
```

Usa una sola ruta para cada pasada de QA: frontend Docker en `5173` o QA aislada en `5176`. Si hay conflicto de puerto, identifica el proceso antes de relanzar servicios. No reconstruyas Postgres, Redis, API o worker solo para refrescar una pantalla si el cambio esta limitado al frontend.

Para cerrar solo el Vite lanzado por el script:

```powershell
.\scripts\qa-pwa.ps1 stop
```

## Frontend Structure

The PWA should stay modular before new product flows are added. `frontend/src/App.tsx` is only the React root wrapper and should not own feature UI, API orchestration, or reusable components.

Accepted structure:

- `frontend/src/app/`: dashboard-level composition and navigation metadata.
- `frontend/src/components/`: reusable UI pieces shared by multiple features, such as pagination, item cells, row actions, and layout shells.
- `frontend/src/features/<feature>/`: feature-owned views and helpers. Current feature folders include `opportunities`, `sources`, `settings`, and reusable `runs` activity components embedded in monitors.
- `frontend/src/hooks/`: reusable React state orchestration hooks, including dashboard controllers that coordinate API calls and feature state.
- `frontend/src/utils/`: generic formatting and pure helpers that do not know about feature state.
- `frontend/src/api.ts`: API types and HTTP client functions only.
- `frontend/src/styles/`: CSS split by responsibility and imported through `styles/index.css`.

Use Recharts for monitor performance charts instead of hand-built SVG charting. Interval bars should be drawn with Recharts scales when exact `bucket_start` to `bucket_end` geometry is required, because the built-in categorical `<Bar>` width is not exact enough for time buckets. Consider migrating monitor charts to visx only if future requirements add heavier interactions such as zoom, brushing, drill-down, multi-series overlays, or range selection.

Feature work should add or extend a feature module instead of growing the dashboard root. If a file starts mixing cross-feature state, feature rendering, reusable components, and formatting helpers, split it before adding more behavior.

### Frontend Baseline Acceptance

- The app root remains a thin wrapper.
- Dashboard state orchestration is separated from layout rendering.
- Cross-feature dashboard state is extracted into a hook instead of living directly in the composition component.
- Opportunities, sources, settings, and reusable run activity components have feature-owned modules.
- Shared item rendering, row actions, and pagination are reusable components.
- CSS is imported from `styles/index.css` and split into focused files.
- Existing desktop and mobile dashboard behavior remains unchanged.

## Notas Windows

Si `python` no aparece tras instalarlo, abrir una nueva terminal o usar la ruta real de instalacion en `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
