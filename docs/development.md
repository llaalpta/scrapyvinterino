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
docker compose up -d --build postgres redis api
docker compose ps
docker compose exec api python -m vinted_monitor.cli.create_user --email admin@example.local
```

Este es el arranque local seguro de infraestructura/API: no inicia ningun ejecutor. El comando de usuario solicita password y confirmacion sin mostrarlas ni pasarlas por argumentos. Para usar el frontend Docker, si `5173` esta libre, ejecuta `docker compose up -d frontend` y autentica en la PWA.

No hay perfiles Compose. `docker compose up` sin lista arranca tambien worker y `scheduler-watchdog`; el worker recupera reservas Redis y crea consumidores incluso si el scheduler esta deshabilitado, por lo que puede reanudar trafico persistido. Antes de habilitar ejecucion operativa comprueba monitores activos, runs, ready/processing y presupuesto de trafico. Arranca primero `worker`, verifica heartbeat/colas y despues `scheduler-watchdog`.

## Flujo de Ramas

El flujo SDD practico esta documentado en `docs/sdd-process.md`. Un plan amplio se divide primero en tareas con resultado propio y se registra como checklist ordenada en `docs/roadmap.md` mediante una rama documental `plan/<scope>` creada desde `develop`; esa rama se integra antes de abrir ramas de implementacion.

Cada tarea usa una rama corta creada desde un `develop` que ya contiene sus dependencias, implementa solo su slice, demuestra el comportamiento real, pasa self-review y auditoria independiente automatica, y se commitea por separado. Tras integrar la tarea se espera confirmacion explicita antes de abrir la rama o empezar el desarrollo siguiente.

Si `develop` no existe localmente, no sigas acumulando cambios en una rama larga por defecto. Confirma primero si hay que crear `develop`, traerlo de remoto o tratar el cambio como excepcion puntual.

## Verificacion de Integracion

La aceptacion prioriza el flujo real sobre el volumen de tests: contenedores y procesos reales, endpoint/API o accion PWA real, PostgreSQL, Redis/colas/cache, eventos/logs y estado visible. Los mocks, eventos sinteticos y suites unitarias cubren bordes deterministas, pero no sustituyen esa prueba de coordinacion.

Cuando el resultado dependa realmente de Vinted o de un proxy, el contrato de la tarea debe fijar antes un numero acotado de requests/runs y el estado final esperado. La prueba termina deteniendo el monitor y limpiando filas, sesiones, claves Redis, tareas y procesos QA. Los fallos de dependencias requeridas se muestran y detienen el flujo; no se crean fallbacks implicitos para obtener un verde.

La suite completa se ejecuta una vez cerca del cierre si el riesgo lo justifica. Durante desarrollo se usan checks focalizados para no convertir cada iteracion en una tarea enorme.

## Estado local y volumenes

El entorno sigue siendo preproduccion y no conserva compatibilidad con contratos de desarrollo obsoletos, pero sus volumenes ya contienen estado operativo valioso: monitores, oportunidades, historico, proxys, sesiones cifradas, app settings y Redis AOF con cola/cache. Se preservan por defecto.

Hasta la primera version de produccion no se mantiene compatibilidad hacia atras con desarrollos previos. Si un modelo, endpoint, payload, migracion o flujo de UI queda obsoleto, se elimina en vez de mantener adaptadores legacy.

`docker compose down` elimina contenedores/red y conserva los volumenes. `docker compose down -v` borra PostgreSQL y Redis de forma irreversible; solo se usa como reset deliberado despues de inspeccionar/respaldar el estado y obtener confirmacion explicita. No lo ejecutes como solucion automatica a un fallo de migracion.

Para un reset destructivo autorizado:

```powershell
docker compose ps -a
docker compose down -v
docker compose up -d --build postgres redis api
```

Las migraciones Alembic pueden compactarse o romper compatibilidad con datos locales anteriores cuando el cambio simplifique el modelo.

## Puertos

- `127.0.0.1:5173`: frontend Vite.
- `127.0.0.1:8000`: API FastAPI.
- `127.0.0.1:5432`: Postgres local.
- `127.0.0.1:6379`: Redis local.

Los binds loopback son parte de la frontera: exponer PostgreSQL/Redis en LAN permitiria saltarse el login de la API. `BACKEND_CORS_ORIGINS` enumera origenes exactos; el ejemplo incluye `http://localhost:5173` y el QA aislado `http://127.0.0.1:5176`.

## PWA QA estable

Antes de elegir ruta, captura `docker compose ps -a`, los listeners `5173/5176`, monitores activos y Redis ready/processing. El estado inicial manda sobre el cleanup.

### Vite aislado con worker detenido

Usa esta ruta cuando la aceptacion no autoriza ejecutores ni trafico externo. Si worker/watchdog estaban activos, no los interrumpas sin que el contrato lo permita; detente o acuerda primero esa parada.

```powershell
.\scripts\qa-pwa.ps1 stop
if (docker compose ps --status running -q worker scheduler-watchdog) {
    throw "Worker/watchdog siguen activos; no los detengas sin un contrato autorizado"
}
docker compose up -d postgres redis api
docker compose ps -a
$deadline = (Get-Date).AddSeconds(60)
$apiReady = $false
do {
    try {
        $apiReady = (Invoke-WebRequest http://localhost:8000/health -TimeoutSec 2).StatusCode -eq 200
    } catch {
        $apiReady = $false
    }
    if (-not $apiReady) { Start-Sleep -Seconds 1 }
} while (-not $apiReady -and (Get-Date) -lt $deadline)
if (-not $apiReady) { throw "La API no estuvo lista en 60 segundos" }
if (Get-NetTCPConnection -LocalPort 5176 -State Listen -ErrorAction SilentlyContinue) {
    throw "El puerto QA 5176 ya esta ocupado"
}
Push-Location frontend
$env:VITE_DEV_API_PROXY_TARGET = "http://localhost:8000"
try {
    pnpm exec vite --host 127.0.0.1 --port 5176 --strictPort
} finally {
    Remove-Item Env:VITE_DEV_API_PROXY_TARGET -ErrorAction SilentlyContinue
    Pop-Location
}
```

El Vite corre en foreground; abre Playwright contra `http://127.0.0.1:5176` y termina con `Ctrl+C`. Confirma despues que `5176` quedo libre y restaura API/PostgreSQL/Redis/worker/watchdog/frontend exactamente al snapshot inicial. No uses `http://localhost:5173` para esa pasada ni detengas un proceso preexistente que no pertenezca a la QA.

### Helper con worker autorizado

Usa el helper solo si el contrato incluye el worker, ya comprobaste monitores/colas y existe presupuesto para cualquier trafico externo que pudiera reanudarse:

```powershell
.\scripts\qa-pwa.ps1 stop
.\scripts\qa-pwa.ps1 start
.\scripts\qa-pwa.ps1 status
Invoke-WebRequest http://localhost:8000/health
Invoke-WebRequest http://127.0.0.1:5176
```

Abre Playwright contra `http://127.0.0.1:5176`. El script apaga el servicio Docker `frontend` de `5173`, levanta `postgres`, `redis`, `api` y `worker` con Docker Compose, no levanta el watchdog, arranca Vite local en `5176`, configura `VITE_DEV_API_PROXY_TARGET=http://localhost:8000` y guarda PID/logs en `%TEMP%\scrapyvinterino-qa`.

Para QA de auth no uses el helper con worker. `scripts/qa_local_auth_pwa.py` crea su usuario efimero por el servicio local, exige URLs loopback explicitas, bloquea service workers y todo host no local, y verifica antes/despues que worker/watchdog siguen detenidos. El runner recibe credenciales generadas solo en variables temporales del proceso: no hagas eco ni imprimas `Set-Cookie`, token hash, cookie, CSRF o password. Su `finally`/handler de salida elimina por ID/hash exactos el usuario y todas sus sesiones, incluida la preauth creada tras logout; confirma despues los contadores en PostgreSQL. Un fallo de auth/API debe mantener el dashboard desmontado; no uses un bypass de test en la aplicacion viva.

En la pasada creada por el helper no uses `http://localhost:5173`. Ese puerto pertenece normalmente al frontend Docker y en Windows puede aparecer como publicado aunque el host no responda. `status` debe mostrar el Vite QA en `5176` y avisar si queda algo escuchando en `5173`.

El helper no es simetrico: `stop` cierra solo su Vite local; no detiene worker/API ni restaura el frontend Docker. Restaura manualmente cada servicio al snapshot inicial. Nunca dejes dos Vite sobre el mismo puerto.

Cada callback SSE debe pertenecer a una instancia concreta de `EventSource`. Antes de cambiar estado, cursor, eventos o temporizadores, el callback comprueba que su instancia sigue siendo la conexion actual; un `error` obsoleto nunca puede cerrar ni degradar el reemplazo. `stream_heartbeat` es una señal JS sin cursor que rearma el watchdog PWA de 22,5 segundos; este se arma al construir la instancia para cubrir tambien `CONNECTING`. Error o silencio cierran la conexion antes de una revalidacion auth cancelable/acotada y de como maximo un timer de reconexion. Si el reemplazo tambien falla durante una caida prolongada puede programar el siguiente intento, pero nunca existen dos timers o conexiones actuales a la vez. Salir de Monitores invalida la instancia, cierra el stream y cancela los timers/fetch; volver crea una sola conexion con el ultimo cursor explicito.

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
