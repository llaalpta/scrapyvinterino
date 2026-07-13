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

El flujo SDD practico esta documentado en `docs/sdd-process.md`. Un plan amplio se divide primero en tareas con resultado propio y se registra como checklist ordenada en `docs/roadmap.md` mediante una rama documental `plan/<scope>` creada desde `develop`; esa rama se integra antes de abrir ramas de implementacion.

Cada tarea usa una rama corta creada desde un `develop` que ya contiene sus dependencias, implementa solo su slice, demuestra el comportamiento real, pasa self-review y auditoria independiente automatica, y se commitea por separado. Tras integrar la tarea se espera confirmacion explicita antes de abrir la rama o empezar el desarrollo siguiente.

Si `develop` no existe localmente, no sigas acumulando cambios en una rama larga por defecto. Confirma primero si hay que crear `develop`, traerlo de remoto o tratar el cambio como excepcion puntual.

## Verificacion de Integracion

La aceptacion prioriza el flujo real sobre el volumen de tests: contenedores y procesos reales, endpoint/API o accion PWA real, PostgreSQL, Redis/colas/cache, eventos/logs y estado visible. Los mocks, eventos sinteticos y suites unitarias cubren bordes deterministas, pero no sustituyen esa prueba de coordinacion.

Cuando el resultado dependa realmente de Vinted o de un proxy, el contrato de la tarea debe fijar antes un numero acotado de requests/runs y el estado final esperado. La prueba termina deteniendo el monitor y limpiando filas, sesiones, claves Redis, tareas y procesos QA. Los fallos de dependencias requeridas se muestran y detienen el flujo; no se crean fallbacks implicitos para obtener un verde.

La suite completa se ejecuta una vez cerca del cierre si el riesgo lo justifica. Durante desarrollo se usan checks focalizados para no convertir cada iteracion en una tarea enorme.

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

Para QA de la PWA con Playwright, usa la ruta aislada en `5176`:

```powershell
.\scripts\qa-pwa.ps1 stop
.\scripts\qa-pwa.ps1 start
.\scripts\qa-pwa.ps1 status
Invoke-WebRequest http://localhost:8000/health
Invoke-WebRequest http://127.0.0.1:5176
```

Abre Playwright contra `http://127.0.0.1:5176`. El script apaga el servicio Docker `frontend` de `5173`, levanta `postgres`, `redis`, `api` y `worker` con Docker Compose, arranca Vite local en `5176`, configura `VITE_DEV_API_PROXY_TARGET=http://localhost:8000` y guarda PID/logs en `%TEMP%\scrapyvinterino-qa`.

No uses `http://localhost:5173` para esta pasada. Ese puerto pertenece al frontend Docker y en Windows puede aparecer como publicado aunque el host no responda. `status` debe mostrar el Vite QA en `5176` y avisar si queda algo escuchando en `5173`.

Cada callback SSE debe pertenecer a una instancia concreta de `EventSource`. Antes de cambiar estado, cursor, eventos o temporizadores, el callback comprueba que su instancia sigue siendo la conexion actual; un `error` obsoleto nunca puede cerrar ni degradar el reemplazo. La conexion fallida se cierra y deja de ser actual antes de programar como maximo un timer de reconexion. Si el reemplazo tambien falla durante una caida prolongada puede programar el siguiente intento, pero nunca existen dos timers o conexiones actuales a la vez. Salir de Monitores invalida la instancia, cierra el stream y cancela el timer; volver crea una sola conexion con el ultimo cursor explicito.

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
