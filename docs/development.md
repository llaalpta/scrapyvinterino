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
```

Este es el arranque local seguro de infraestructura/API: no inicia ningun ejecutor. Si el `.env` local contiene juntas `LOCAL_DEV_USER_EMAIL` y `LOCAL_DEV_USER_PASSWORD`, la API garantiza ese usuario despues de migrar y antes de servir; el par es idempotente, no se imprime y no es valido fuera de `development`. Sin esas variables, crea el usuario manualmente con `docker compose exec api python -m vinted_monitor.cli.create_user --email admin@example.local`, que solicita password y confirmacion sin mostrarlas. Para usar el frontend Docker, si `5173` esta libre, ejecuta `docker compose up -d frontend` y autentica en la PWA.

No hay perfiles Compose. `docker compose up` sin lista arranca tambien worker y `scheduler-watchdog`; el worker recupera reservas Redis y crea consumidores incluso si el scheduler esta deshabilitado, por lo que puede reanudar trafico persistido. Antes de habilitar ejecucion operativa comprueba monitores activos, runs, ready/processing y presupuesto de trafico. Arranca primero `worker`, verifica heartbeat/colas y despues `scheduler-watchdog`.

## Flujo de Ramas

El flujo SDD practico esta documentado en `docs/sdd-process.md`. Solo un programa, un cambio amplio de prioridad/dependencias o un plan de trafico externo necesita una rama documental `plan/<scope>`; una tarea estandar ya definida en el roadmap abre directamente su rama de implementacion desde `develop`.

Cada tarea usa una rama corta creada desde un `develop` que ya contiene sus dependencias, implementa solo su slice y demuestra el comportamiento real. Despues pasa self-review y auditoria independiente automatica; los hallazgos se corrigen y re-auditan hasta obtener veredicto positivo o bloquear/dividir la tarea. Solo tras el veredicto positivo se hace el commit de cierre, push normal y apertura automatica de la PR; cuando el remoto la marca mergeable y pasan sus checks requeridos configurados, se integra a `develop` con merge commit sin borrar la rama. Empezar la tarea siguiente sigue requiriendo confirmacion explicita.

Si `develop` no existe localmente, no sigas acumulando cambios en una rama larga por defecto. Confirma primero si hay que crear `develop`, traerlo de remoto o tratar el cambio como excepcion puntual.

## Verificacion de Integracion

La aceptacion prioriza el flujo real sobre el volumen de tests: contenedores y procesos reales, endpoint/API o accion PWA real, PostgreSQL, Redis/colas/cache, eventos/logs y estado visible. Los mocks, eventos sinteticos y suites unitarias cubren bordes deterministas, pero no sustituyen esa prueba de coordinacion.

Cuando el resultado dependa realmente de Vinted o de un proxy, el contrato de la tarea debe fijar antes un numero acotado de requests/runs y el estado final esperado. La prueba termina deteniendo el monitor y limpiando filas, sesiones, claves Redis, tareas y procesos QA. Los fallos de dependencias requeridas se muestran y detienen el flujo; no se crean fallbacks implicitos para obtener un verde.

La suite completa se ejecuta una vez cerca del cierre si el riesgo lo justifica. Durante desarrollo se usan checks focalizados para no convertir cada iteracion en una tarea enorme.

### Aceptacion manual real acotada

La tarea 14.37 no pulsa `Preparar sesion`: `Iniciar sesion` prepara el contexto anonimo y reutiliza el probe JSON como baseline. Usa una sola fuente y un usuario QA temporales sobre la PWA/API operativos, con worker y watchdog detenidos, un unico proxy activo elegible y Redis 0 inicialmente vacio. El navegador aborta imagenes/CDN y cualquier otro destino no loopback.

La secuencia autorizada es exactamente un start, un run manual inmediato, un stop y un rechazo local posterior al stop. Con `catalog_per_page=5`, limite de detalle `5`, `VINTED_REQUEST_RETRIES=1`, modo serial, sesion preparada obligatoria y acceso directo deshabilitado, admite como maximo `19` operaciones externas logicas: dos preparaciones completas como caso conservador, dos intentos de catalogo y cinco detalles. No se ejecutan test de proxy, segundo run, retry de detalle ni preparacion independiente. Los redirects de egress/DataDome son un riesgo residual de transporte, no permiso para mas operaciones iniciadas por la aplicacion.

Antes y despues se comprueban fuentes activas, runs no terminales, sesiones abiertas, worker/watchdog y Redis. El cleanup se limita al grafo SQL del token QA y a `*monitor:{source_id}:*`; no borra ni restaura telemetria real del proxy ya existente. Debe demostrar cero filas/sesiones/keys QA y no eliminar ni sobrescribir estado ajeno; la telemetria normal producida por el uso real del proxy se conserva como tal. Si start o run falla, se muestra el error real, se intenta el stop solo cuando exista sesion y se ejecuta igualmente el cleanup; no se repite trafico para obtener un verde.

El gate de 2026-07-15 completo `baseline 5/0/0 -> manual 5/0/0 -> stop -> 409`, reutilizo la sesion preparada y consumio seis operaciones logicas externas. El cleanup dejo Redis 0 vacio y cero filas QA, monitores activos, runs no terminales o sesiones abiertas. La autenticacion operativa de Vite usa `http://localhost:5173`; navegar por el bind `127.0.0.1:5173` no convierte ese origen en equivalente para CSRF/CORS.

### Aceptacion recurrente real acotada

La tarea 14.38 usa una fuente continua QA con intervalo `60` y jitter `10%`, el API/Vite ya operativos y worker/watchdog de Compose. Antes comprueba cero trabajo activo y Redis 0 vacio. El preflight compara config hashes, mounts y dependencias: si los ejecutores estan atrasados, se reconstruyen/recrean solo esos dos servicios desde el Compose vigente; no se reinician API, Vite, PostgreSQL, Redis ni frontend.

Con `SCHEDULER_ENABLED=true`, mientras los ejecutores siguen detenidos y el heartbeat no esta disponible, un start autenticado debe devolver el rechazo local sin run, sesion, Redis ni trafico. No existe un gate global en Ajustes. Despues se arranca worker, se espera su healthcheck/heartbeat y solo entonces watchdog. La pasada positiva permite un start y tres vencimientos reales; exige baseline previo a activacion, primer deadline `60..66`, cero run inmediato, tres ACK, misma monitor/Vinted session sin reprepare, al menos una oportunidad posterior al baseline y stop antes de un cuarto vencimiento.

El presupuesto duro es `45` operaciones externas logicas: una preparacion de seis operaciones en start y hasta `3 * (6 preparacion + 2 catalogo + 5 detalle)`. La trayectoria valida no debe re-preparar y normalmente queda por debajo de `30`; cualquier run fallido se detiene localmente sin esperar otro vencimiento. No se pulsa preparar sesion, probar proxy, ejecutar manualmente ni recargar oportunidades fuera de la PWA. El navegador bloquea todo host no loopback.

El cleanup detiene watchdog antes que worker, deja que el ultimo heartbeat real caduque sin falsear su valor y mantiene los dos contenedores ya convergidos parados. Elimina por ownership el usuario/preauth, fuente, runs, eventos/outbox/publicaciones, errores, Vinted/monitor sessions, oportunidades, items nuevos que queden huerfanos y todas las claves Redis de fuente/cola. Conserva filas previas y la telemetria normal del proxy; API, PostgreSQL, Redis, Vite y frontend Docker vuelven a su snapshot inicial.

La pasada de 2026-07-16 supero el rechazo local `503` y la convergencia/heartbeat de los ejecutores, pero no el baseline positivo: el diagnostico de egress requerido agoto su timeout de `15` segundos, el collector se omitio por contexto base incompleto y la probe `200 accepted_json` no pudo compensar la falta de pais validado y `datadome`. Se detuvieron los ejecutores tras tres operaciones logicas externas, sin retry ni vencimiento recurrente. El cleanup devolvio Redis y todos los contadores QA/activos a cero, con el item previo intacto. En ese punto la aceptacion quedo bloqueada hasta recuperar el diagnostico requerido; no se relajo el contrato para obtener un verde.

La pasada final de 2026-07-17 reutilizo el rechazo local ya aceptado y emitio un unico start positivo desde la PWA. Completo baseline `5/0/0`, primer deadline a `60.0` segundos, tres runs recurrentes `success`, la misma sesion preparada con usos `1 -> 4`, tres enqueue/receive/ACK y ocho oportunidades cuyos HMAC no estaban en el baseline. Consumio `22/45` operaciones logicas, detuvo desde la PWA tras el tercer terminal y comprobo cero cuarto run despues del vencimiento anterior. El cleanup devolvio todos los fingerprints SQL/Redis estables, item previo, contenedores y Vite a su snapshot; conservo solo la telemetria ordinaria real del proxy, dejo expirar el heartbeat y termino sin proceso Playwright residual.

#### Desbloqueo operativo del diagnostico de egress

Antes de repetir 14.38 se valida un unico endpoint configurado, sin tocar Vinted ni DataDome. El preflight mantiene worker/watchdog detenidos y exige Redis 0 vacio, cero fuentes activas, runs no terminales y sesiones abiertas. Usa una identidad sticky temporal del unico proxy ES elegible y sesiones HTTP aisladas, sin cookies ni estado persistido.

La tarea admite como maximo dos `GET` HTTPS logicos, ambos sin redirects: una repeticion de `https://ipwho.is/` y, solo si falla, una prueba condicional de `https://ipapi.co/json/`. Cada respuesta permanece en memoria y la salida se limita a status/duracion y booleanos de JSON, IP, codigo de pais y coincidencia con ES; no se muestran payload, IP, ASN, organizacion, URL de proxy, usuario, password, cookies ni excepciones crudas. Un endpoint es valido solo con HTTP `<400`, objeto JSON, `ip|query`, `country_code|countryCode` y coincidencia ES mediante el parser real. La alternativa es una seleccion explicita de deployment, no un fallback de runtime.

Si el endpoint actual valida, no cambia configuracion. Si falla y la alternativa valida, se fija solo `EGRESS_DIAGNOSTIC_URL` en el `.env` local, se comprueba `docker compose config --quiet` y se recrea sin build la API; worker/watchdog se recrean detenidos para que su siguiente arranque use el mismo entorno. La API debe volver healthy, cargar localmente el hostname elegido y conservar PostgreSQL/Redis/Vite. Si ambos endpoints fallan, no se cambia configuracion y 14.38 permanece bloqueada. El estado final exige cero mutacion SQL/Redis QA, ejecutores detenidos y ningun cambio en secretos, datos ajenos o telemetria del proxy.

El preflight de 2026-07-16 consumio las dos llamadas autorizadas. Tanto el endpoint configurado como el candidato terminaron en `ProxyError` antes de recibir status o JSON, cada uno en aproximadamente `8` segundos. No se selecciono endpoint, no se modifico `.env`, no se recreo servicio alguno y no hubo trafico Vinted/DataDome. El bloqueo no puede atribuirse ya solo a `ipwho.is`: hace falta recuperar primero la conectividad/autenticacion del proxy o esperar a que su servicio vuelva a estar disponible; no se amplian endpoints ni retries dentro de este gate.

#### Diagnostico DNS/proxy desde el worker

El gate posterior de 2026-07-17 uso un `docker compose run --rm --no-deps -T worker` con codigo efimero por stdin: comparte imagen, entorno, bind mount, red y resolver del worker, pero no inicia productor, consumidores ni heartbeat. Primero rechazo un hostname `.invalid` sin HTTP; despues resolvio el gateway configurado y completo un unico GET al diagnostico de egress mediante una identidad sticky nueva construida por el runtime. La respuesta fue 2xx, JSON, con IP y pais presentes, coincidencia ES, sin redirect ni cookies; no se imprimieron host, direcciones, credenciales, URL autenticada, payload, headers o excepcion raw.

API y worker no mostraron diferencias de entorno, capas, red, resolver o construccion de URL que expliquen el curl code 5 anterior. No se fija DNS publico, no se agrega retry y no cambia `.env` ni producto: el episodio queda como fallo transitorio de Docker DNS/red. El one-off se elimino, Redis siguio vacio, el flujo no invoco escritores SQL/telemetria y worker/watchdog permanecieron detenidos. En ese momento la repeticion real de 14.38 seguia siendo obligatoria; la pasada final descrita arriba aporto despues Vinted, DataDome, tres runs y la prueba de oportunidad posterior al baseline.

### Bootstrap PWA por superficie

Al montar el dashboard autenticado, las colecciones visibles de monitores, oportunidades y proxys empiezan en `loading` y se resuelven de forma independiente. Solo una respuesta valida, aunque contenga cero filas, confirma `ready`; si una coleccion nunca se ha confirmado y su lectura falla, queda `unavailable`. Los contadores y estados vacios se muestran solo en `ready`, y el aviso global identifica las cargas incompletas sin inutilizar las otras superficies.

Una coleccion ya confirmada no se degrada ni pierde datos por un fallo de refresco posterior: conserva su ultimo snapshot y muestra el error. Mientras monitores o proxys no estan confirmados se bloquean solo sus formularios de mutacion; si faltan monitores, el filtro dependiente de Oportunidades queda deshabilitado, pero el resto de filtros y las colecciones independientes siguen operativos. La lista global de runs, sin consumidor en la navegacion actual, no forma parte del bootstrap; cada detalle de monitor carga sus runs bajo demanda y mantiene sus acciones bloqueadas hasta conocer ese estado.

El escenario cerrado `pwa-bootstrap-isolation` usa API, Vite, autenticacion y PostgreSQL aislados, siembra un monitor inactivo y falla localmente cada lectura inicial visible por separado. Comprueba un estado `unavailable` sin falso cero, la recuperacion posterior de Monitores sin corromper su borrador, la continuidad de las superficies independientes y que un fallo de refresco posterior a un snapshot valido conserva el monitor y sus controles. Worker y watchdog permanecen detenidos, todo host no loopback se bloquea y el usuario, sus sesiones y el monitor QA se eliminan al terminar.

### Backend aislado

La tarea 14.18 acepta un runner focalizado con estos criterios:

- ejecuta el escenario seleccionado desde `backend/`, donde no existe `.env`, con configuracion de test explicita, un rol y una base PostgreSQL nuevos y el indice Redis 15 reservado;
- exige worker y watchdog detenidos y solo admite escenarios locales cerrados: identidad recorre scheduler/cola/consumer con una trampa de proveedor; fail-stop usa respuestas locales; prepared-session, monitor-identity-edit, session-start y session-stop levantan API `8001` y Vite `5176` propios para Playwright; session-stop usa ademas scheduler/cola/consumer reales dentro del test; worker-redis conecta un worker y Redis desechables por una red Docker interna, prueba su restart y usa la misma API/Vite; full recorre la suite en la misma base aislada y separa el modulo que exige destinos loopback;
- dos ciclos consecutivos terminan sin la base ni el rol temporales y con Redis 15 vacio; si ese indice ya contiene datos, el runner se niega a ejecutar y no los elimina.

Con PostgreSQL y Redis ya levantados y los ejecutores detenidos:

```powershell
.\scripts\qa-backend-integration.ps1
.\scripts\qa-backend-integration.ps1 -Scenario catalog-fail-stop
.\scripts\qa-backend-integration.ps1 -Scenario prepared-session-read-model -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario pwa-monitor-command-state -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario pwa-bootstrap-isolation -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario manual-session-start-baseline -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario monitor-session-proxy-traffic -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario recurring-session-start-baseline -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario proxy-only-regression -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario proxy-cooldown -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario session-stop-drain -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario worker-redis-availability -Repeat 1
.\scripts\qa-backend-integration.ps1 -Scenario full -Repeat 1
```

`monitor-session-proxy-traffic` ejecuta primero su matriz agregadora, construye el bundle actual sin service worker de QA y lo sirve por preview hacia API `8001`; contrasta PostgreSQL, API, PWA activa y ultima sesion cerrada con observaciones de transferencia controladas.

El comando predeterminado migra dos bases nuevas y ejecuta dos veces el canary real de identidad. `-Scenario` traduce `identity`, `catalog-fail-stop`, `proxy-only-regression`, `proxy-cooldown`, `prepared-session-read-model`, `monitor-identity-edit`, `pwa-monitor-command-state`, `pwa-bootstrap-isolation`, `manual-session-start-baseline`, `monitor-session-proxy-traffic`, `recurring-session-start-baseline`, `session-stop-drain`, `worker-redis-availability` y `full` a targets cerrados; nunca acepta un target arbitrario. `proxy-only-regression` migra una base aislada y ejecuta el archivo completo de runs manuales con todos los destinos externos bloqueados a loopback; `proxy-cooldown` revalida configuracion completa, migracion, fallos clasificados, fences y admision sin levantar la PWA. Prepared-session, monitor-identity-edit, los dos escenarios PWA, los tres escenarios session-start/traffic y session-stop usan API/Vite/Playwright reales, bloquean hosts no loopback y cierran solo sus procesos. Monitor-identity-edit prueba un PATCH valido, rechazo sin mutacion y bloqueo activo sobre la misma fila sin iniciar trafico de catalogo. Pwa-monitor-command-state retrasa solo la entrega de respuestas reales a React para demostrar exclusion inmediata ante doble submit, fallos derivados honestos y descarte de un snapshot de fuentes obtenido antes del archivo; UI, API y PostgreSQL deben conservar el resultado ya confirmado. Pwa-bootstrap-isolation falla localmente monitores, oportunidades y proxys por separado, recupera un fallo inicial de Monitores sin vaciar su borrador y conserva el snapshot ante un fallo posterior, siempre con PostgreSQL inmutable. Los escenarios de sesion arrancan un ASGI exclusivo de tests que inyecta por fichero temporal un proveedor controlado solo en la frontera Vinted; no añaden endpoint, flag ni fallback de produccion. Recurrente y session-stop mantienen worker y watchdog operativos detenidos, pero recorren el `SchedulerRunner`, la cola Redis y el `TaskConsumer` reales dentro del proceso de prueba, con una clave de cola exclusiva, y limpian todo el grafo SQL/Redis al terminar. Session-stop bloquea un provider despues de confirmar el run para probar el drain y, en una segunda fase, reserva otra tarea antes del stop para demostrar cero run/provider tras la admision autoritativa. Worker-redis usa imagenes locales, una red `--internal`, ownership por token y una base/Redis desechables; desactiva el restart antes de retirar el worker y restaura exactamente las redes del PostgreSQL operativo. Full conserva las URLs contractuales para unitarios falsos y ejecuta aparte, con destinos loopback, el modulo que lo exige; los dos grupos comparten la misma base efimera. `-Repeat` se limita a `1..3`. El runner localiza los contenedores sin arrancar servicios Compose ni cargar la `.env` raiz, sanea el entorno heredado y bloquea egress mediante proxys de entorno a loopback. Una lease reserva Redis 15; el cleanup solo hace `FLUSHDB` si conserva esa lease y elimina exclusivamente el rol/base generados. Antes y despues compara fingerprints sin valores visibles de PostgreSQL operativo y Redis 0; una diferencia falla de forma visible y nunca intenta restaurar datos automaticamente.

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

- `127.0.0.1:5173`: bind local de Vite; con la configuracion predeterminada navega por `http://localhost:5173`.
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
