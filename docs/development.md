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

## Base de Datos Local

La base de datos local es descartable mientras el producto siga en desarrollo puro. No hay datos reales que conservar.

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

Para cerrar solo el Vite lanzado por el script:

```powershell
.\scripts\qa-pwa.ps1 stop
```

## Frontend Structure

The PWA should stay modular before new product flows are added. `frontend/src/App.tsx` is only the React root wrapper and should not own feature UI, API orchestration, or reusable components.

Accepted structure:

- `frontend/src/app/`: dashboard-level composition and navigation metadata.
- `frontend/src/components/`: reusable UI pieces shared by multiple features, such as pagination, item cells, row actions, and layout shells.
- `frontend/src/features/<feature>/`: feature-owned views and helpers. Current feature folders include `opportunities`, `sources`, `filters`, `settings`, and reusable `runs` activity components embedded in monitors.
- `frontend/src/hooks/`: reusable React state orchestration hooks, including dashboard controllers that coordinate API calls and feature state.
- `frontend/src/utils/`: generic formatting and pure helpers that do not know about feature state.
- `frontend/src/api.ts`: API types and HTTP client functions only.
- `frontend/src/styles/`: CSS split by responsibility and imported through `styles/index.css`.

Use Recharts for monitor performance charts instead of hand-built SVG charting.

Feature work should add or extend a feature module instead of growing the dashboard root. If a file starts mixing cross-feature state, feature rendering, reusable components, and formatting helpers, split it before adding more behavior.

### Frontend Baseline Acceptance

- The app root remains a thin wrapper.
- Dashboard state orchestration is separated from layout rendering.
- Cross-feature dashboard state is extracted into a hook instead of living directly in the composition component.
- Opportunities, sources, filters, settings, and reusable run activity components have feature-owned modules.
- Shared item rendering, row actions, and pagination are reusable components.
- CSS is imported from `styles/index.css` and split into focused files.
- Existing desktop and mobile dashboard behavior remains unchanged.

## Notas Windows

Si `python` no aparece tras instalarlo, abrir una nueva terminal o usar la ruta real de instalacion en `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
