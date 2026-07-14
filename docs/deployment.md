# Despliegue

El runtime local ejecutable vive en `docker-compose.yml`. `docker-compose.prod.example.yml` es el ejemplo actual detras de Traefik; todavia requiere hardening antes de considerarse un despliegue de produccion cerrado.

## Objetivo

- Desplegar detras de Traefik y Cloudflare.
- No exponer Postgres.
- Servir frontend en `/`.
- Servir API en `/api`.
- Soportar SSE para eventos en vivo sin buffering del proxy.

## Desarrollo vs Produccion

Desarrollo publica frontend, API, PostgreSQL y Redis en el host. El ejemplo de produccion mantiene PostgreSQL/Redis en la red `internal`, conecta API y frontend a la red externa `traefik`, enruta `/api` a FastAPI y el resto a Nginx. Requiere definir `APP_HOST` y crear antes la red externa `traefik`.

El backend aplica la frontera local descrita en `docs/specs/011-local-pwa-access-control.md` a todas las rutas `/api` de negocio; Traefik no sustituye esa comprobacion. Desarrollo publica PostgreSQL, Redis, API y Vite solo en `127.0.0.1`. Produccion no publica puertos de contenedor: enruta frontend y `/api` bajo el mismo `APP_HOST`, mantiene `/health` interno y desactiva OpenAPI/Swagger/ReDoc.

Produccion fija `BACKEND_CORS_ORIGINS=https://${APP_HOST}` desde Compose. La configuracion fuera de development/test rechaza origenes vacios, wildcard, no HTTPS o con path. La cookie host-only usa `Path=/api`, `SameSite=Strict`, `HttpOnly` y `Secure`; no se comparte por subdominios ni se admite un modo cross-origin alternativo.

Las migraciones no siembran credenciales. Tras aplicar Alembic, crea el primer usuario con entrada interactiva dentro de la red del despliegue:

```powershell
docker compose exec api python -m vinted_monitor.cli.create_user --email admin@example.local
```

Para un despliegue nuevo cuyo API aun no este iniciado, ejecuta el mismo modulo mediante un `docker compose run --rm` despues de migrar. La password nunca se pasa como argumento o variable versionada. Sin usuario activo, salud puede responder pero login y toda operacion permanecen cerrados.

## Configuracion Runtime

La configuracion no debe tener dos fuentes de verdad activas:

| Dueño | Uso |
| --- | --- |
| `.env` | Infraestructura, secretos, auth local, kill-switches, workers, runtime cache y evasion anti-bot: DB/Redis, CORS exacto, `APP_SECRET_KEY`, TTL absoluto de pre-auth/auth, `SCHEDULER_ENABLED`, heartbeat del productor, intervalo/gracia del watchdog, `SEEN_CACHE_TTL_SECONDS`, `SEEN_PROCESSING_TTL_SECONDS`, `SEEN_CACHE_MAX_PER_MONITOR`, `WORKER_CONSUMER_COUNT`, `WORKER_MAX_RETRY_ATTEMPTS`, `VINTED_REQUEST_RETRIES`, `CURL_IMPERSONATE_BROWSER`, delays humanos, penalizacion DataDome y plantilla sticky de proxy. |
| PWA | Operacion diaria: habilitar scheduler en app, runs simultaneos, salida directa, limites por run, timeout HTTP, pausa de proxy tras fallo, parada de monitor tras fallos, y alta/test/pausa de proxys. |
| Backend | Limites duros de validacion y defaults seguros cuando no hay override operativo. |

Algunos valores `.env` tambien sirven como defaults cuando aun no existe override operativo en `app_settings.scheduler`; por ejemplo `VINTED_REQUEST_TIMEOUT_MS`. Una vez guardado desde la PWA, el valor persistido en DB es la fuente de verdad operativa.

Redis conserva AOF en un volumen Docker tanto en desarrollo como en el ejemplo de produccion. El worker se despliega como una unica instancia con varios consumidores internos: recupera las listas `vinted:task_queue:processing*` antes de iniciar scheduler y consumidores, y no se deben arrancar replicas independientes hasta incorporar ownership/visibility timeout distribuido.

La disponibilidad periodica no se infiere de la configuracion ni de la capacidad: el productor del scheduler persiste su heartbeat UTC en `app_settings.scheduler_worker_heartbeat`. La API y la PWA consideran el scheduler no disponible cuando la señal falta, es invalida o supera el timeout. Esa señal prueba progreso reciente del productor contra PostgreSQL. El supervisor del mismo proceso sondea Redis y sale ante perdida, por lo que deja de renovar la señal; aun existe una ventana obsoleta hasta que vence el timeout y la señal no prueba cada consumidor.

En Compose, la API es el unico servicio propietario de `alembic upgrade head`. Postgres y Redis deben estar sanos antes de iniciar la API; worker y `scheduler-watchdog` esperan despues a que `/health` confirme que la API termino su arranque y migraciones. Ninguno de esos dos servicios ejecuta Alembic.

Las migraciones que introducen trabajo transaccional nuevo para escritores existentes requieren una actualizacion sin versiones mezcladas. En particular, antes de aplicar `0017_run_event_outbox` deben estar detenidos el worker, el watchdog y cualquier API antigua que pueda insertar `run_events`; la API nueva aplica la migracion y solo despues se habilitan los productores. Asi el backfill no puede solaparse con una transaccion 0016 que confirme un evento sin outbox.

`0019_proxy_session_identity` es una migracion destructiva de contrato preproduccion: tanto upgrade como downgrade eliminan todas las filas de `vinted_sessions`, porque una fila sin el binding efectivo de identidad no se puede reutilizar de forma segura. Antes de aplicarla hay que detener API antigua, worker y watchdog, revisar y drenar o retirar de forma controlada las listas ready/processing y no conservar payloads anteriores sin `proxy_identity_generation`; el consumidor nuevo los considera malformados y los envia a dead-letter, sin adaptador legacy. Tras confirmar `0019` y arrancar solo procesos nuevos, las sesiones necesarias se preparan de nuevo mediante un comando explicito; no hay backfill ni fallback automatico.

El worker valida la configuracion y Redis antes de crear sus hilos y termina con error si fallan. Durante la ejecucion, el proceso principal sondea Redis en cada ciclo de supervision y vigila el heartbeat escrito exclusivamente por el productor. Un fallo Redis o un heartbeat caducado termina el proceso con error para que `restart: unless-stopped` lo reemplace; no hay reconnect interno ni fallback. Su healthcheck consulta solo el heartbeat y no sustituye el self-exit: Docker no reinicia un contenedor solo por marcarlo `unhealthy`.

El `scheduler-watchdog` es un proceso separado con `restart: unless-stopped`. Tras su gracia inicial, bloquea solo monitores recurrentes activos, relee el heartbeat y, si sigue ausente, confirma primero en PostgreSQL la parada, el cierre de sesion y el evento `scheduler_worker_unavailable`. Despues intenta retirar tareas aun preparadas en Redis. Un fallo Redis queda registrado pero no revierte la parada; un error inesperado termina el proceso para que Compose lo reinicie.

Fuera de `development` y `test`, el backend rechaza al arrancar una `APP_SECRET_KEY` de menos de 32 caracteres o igual a cualquiera de los placeholders versionados. Cada despliegue debe generar y custodiar un valor aleatorio propio; cambiarlo exige recifrar previamente las credenciales de proxy y los contextos de sesion existentes.

## Salud, reinicio y parada

| Servicio | Health declarado | Lo que no prueba | Reinicio automatico |
| --- | --- | --- | --- |
| PostgreSQL | `pg_isready` | Esquema actual o consultas de negocio. | No. |
| Redis | `redis-cli ping` | Integridad de las colas, reservas o AOF. | No. |
| API | HTTP `GET /health` incondicional. | PostgreSQL, Redis y revision Alembic actual una vez iniciado Uvicorn. | No. |
| Worker | Heartbeat reciente del productor en PostgreSQL. | Estado Redis instantaneo, consumidores individuales o capacidad real de encolar. El supervisor Redis es interno y la señal tarda hasta su timeout en caducar. | `unless-stopped`, solo si el proceso sale de forma no solicitada. |
| `scheduler-watchdog` | Ninguno. | Un hang puede quedar invisible. | `unless-stopped` si el proceso sale. |
| Frontend | Ninguno. | Disponibilidad real de API o rutas Traefik. | No. |

`depends_on` es un gate de arranque, no supervision continua. La API puede seguir `healthy` con PostgreSQL o Redis caidos; worker/watchdog pueden seguir ejecutandose si la API cae despues de arrancar. Un contenedor `unhealthy` tampoco se reinicia solo.

Worker y watchdog no implementan drain ni shutdown coordinado. Un consumidor interrumpido deja su reserva en Redis y el siguiente arranque del worker recupera `processing*` antes de crear hilos. El supervisor del worker usa salida inmediata cuando pierde al productor. La API de ambos Compose y el frontend local usan comandos mediante `sh -c`; Compose no añade `init`, `stop_signal` ni `stop_grace_period`. El frontend de produccion hereda el lifecycle de la imagen Nginx. No existe un contrato propio y verificado de propagacion y gracia para todo el stack. Por eso una parada planificada debe comprobar antes runs/colas y detener juntos watchdog y worker; detener solo el worker permite que el watchdog cierre monitores recurrentes cuando caduque el heartbeat.

Semantica de comandos:

- `docker compose start <servicio>` reanuda un contenedor detenido con la misma imagen/configuracion.
- `docker compose restart <servicio>` reinicia el contenedor existente; no incorpora cambios nuevos de imagen, Compose o `.env`.
- `docker compose up -d --build --force-recreate <servicio>` aplica imagen, configuracion y entorno nuevos.
- `docker compose stop` y `docker compose kill` son acciones manuales: el servicio queda detenido aunque use `unless-stopped`.
- `docker compose down` elimina contenedores/red pero conserva volumenes; `down -v` destruye PostgreSQL y Redis y nunca es mantenimiento rutinario.

## Arbol operativo

Empieza siempre sin imprimir entorno ni secretos:

```powershell
docker compose config --quiet
docker compose ps -a
docker compose logs --tail 200 api worker scheduler-watchdog frontend postgres redis
Invoke-WebRequest http://localhost:8000/health
```

El `200` de `/health` solo confirma el proceso API. Confirma tambien una ruta que use DB, el estado PostgreSQL/Redis y, para recurrencia, `GET /api/scheduler` mas los logs/colas.

```text
¿Arranque o recuperacion?
|
|-- PostgreSQL no esta healthy
|   -> detener worker + watchdog para evitar restart loops
|   -> recuperar PostgreSQL y verificar una consulta API con DB
|   -> revisar Alembic antes de reanudar procesos
|   -> antes del worker, detener recurrentes explicitamente o arrancar solo el watchdog
|      y esperar su fail-stop; una reactivacion posterior siempre es explicita
|
|-- Redis no esta healthy
|   -> el worker sale y Compose reintenta; /health API sigue sin probar Redis
|   -> esperar que worker_available caduque y dejar watchdog para persistir el fail-stop DB-first
|   -> en mantenimiento que deba conservar recurrentes, detener ambos de forma planificada
|   -> recuperar Redis/AOF, revisar ready/processing y confirmar el estado PostgreSQL
|   -> arrancar worker y comprobar recovery; arrancar watchdog si se habia detenido
|
|-- API esta caida o la migracion fallo
|   -> revisar logs de api; no arrancar worker saltando depends_on
|   -> corregir configuracion/migracion y arrancar api
|   -> esperar /health y comprobar una ruta DB; despues worker/watchdog/frontend
|
|-- Worker esta unhealthy o reiniciando
|   -> revisar config, PostgreSQL, Redis, heartbeat y recovery de processing
|   -> corregir la dependencia y arrancar worker
|   -> revisar si watchdog ya detuvo recurrentes; no reactivarlos implicitamente
|
|-- Watchdog esta ausente/reiniciando
|   -> revisar ps y logs (no tiene healthcheck)
|   -> corregir DB/config y arrancarlo solo despues de API/worker sanos
|
`-- Frontend esta caido
    -> comprobar primero API y proxy; reconstruir/recrear frontend si cambio imagen/config
```

Para una actualizacion con migracion, evita escritores mezclados:

1. Captura `ps -a`, monitores recurrentes, runs, cola/reservas y revision Alembic. Declara una ventana de mantenimiento y bloquea el ingress API/frontend, o confirma que no queda ningun cliente; mantenlo bloqueado hasta el ultimo paso.
2. Espera los runs que deban terminar; detiene juntos `frontend`, `scheduler-watchdog` y `worker`.
3. Detiene la API antigua. No uses un worker, una API ni un bundle frontend antiguos durante una migracion de contrato.
4. Ejecuta `docker compose up -d --build --force-recreate api`; su comando aplica Alembic y solo entonces inicia Uvicorn.
5. Verifica logs, `/health`, `docker compose exec -T api alembic current` y una ruta DB.
6. Ejecuta `docker compose up -d --build --force-recreate worker`; espera su health y confirma recovery, heartbeat y colas.
7. Ejecuta `docker compose up -d --build --force-recreate scheduler-watchdog frontend`; verifica sus logs y la ruta frontend.
8. Revisa monitores: una parada fail-stop persistida no se revierte ni reactiva automaticamente. Solo entonces retira el bloqueo de ingress o reabre clientes.

## Evidencia local 2026-07-13

- Ambos Compose renderizaron sin ejecutar trafico externo. La pasada de lifecycle anterior a 14.12.1 arranco API con el entonces vigente `0017 (head)` antes de worker/watchdog. La comprobacion de 14.12.1 aplico el entonces vigente `0018 (head)` con worker/watchdog detenidos; tambien paso cero-a-head y 0017-a-head en una base aislada eliminada despues.
- Para 14.12.2, una base PostgreSQL aislada paso de cero a `0019 (head)`. Otra pasada creo el esquema `0018`, sembro una sesion legacy y comprobo que el upgrade a `0019` la elimino, creo las tres columnas con sus restricciones/default y sustituyo el indice por `ix_vinted_sessions_source_proxy_identity_status`. Una fila nueva ligada a identidad tambien fue eliminada al bajar a `0018`; el indice anterior y la ausencia de las tres columnas quedaron restaurados antes del re-upgrade final a `0019`. Worker y watchdog permanecieron detenidos y no hubo trafico externo.
- Antes de 14.19, con Redis detenido, API, `worker_available` y worker permanecian `healthy` mientras los consumidores repetian errores. La pasada aislada de 14.19 sustituyo ese comportamiento: un worker Docker sobre PostgreSQL/Redis desechables salio con error, entro en restart, dejo vencer el heartbeat y la PWA mostro indisponibilidad; al volver Redis recupero disponibilidad sin fuentes, runs ni cola y sin cambiar fingerprints operativos.
- En la pasada de lifecycle previa a auth, PostgreSQL detenido hacia que `/api/monitors` devolviese `500` y reiniciaba worker/watchdog. Con 14.12.1 y ambos ejecutores detenidos, la prueba real actual mantuvo `/health=200`, devolvio `503` en la ruta privada y, al restaurar PostgreSQL sin reiniciar API, recupero el pool y devolvio el `401` esperado para la cookie invalida de prueba.
- Una API terminada manualmente quedo `exited` porque no tiene restart. Worker y watchdog continuaron sin restart, confirmando que `depends_on` no propaga la caida. `docker compose start api` volvio a ejecutar Alembic antes de Uvicorn.
- Una parada controlada `docker compose stop -t 5 api` agoto los cinco segundos, termino con codigo `137` y no produjo el shutdown de Uvicorn. El `sh -c` vigente no ofrece propagacion graceful demostrada; el `start` posterior volvio a aplicar Alembic y recupero `/health`.
- El estado final restauro API/PostgreSQL/Redis sanos, frontend Docker/worker/watchdog detenidos, heartbeat original, cero monitores activos, cero runs en curso y cero colas o claves de tarea; el Vite host preexistente quedo intacto. No se llamo a Vinted ni a proxies.

La ventana de heartbeat obsoleto, la ausencia de health del watchdog, el restart parcial y la falta de drain son limitaciones actuales, no promesas de autorrecuperacion. Solo la perdida Redis del worker tiene ahora el fail-stop acotado anterior; el resto requiere mantenimiento manual o una tarea promovida explicitamente.

## Outbound Vinted Proxy

El monitor debe funcionar sin proxy de salida por defecto cuando el ajuste global de acceso directo lo permite. El uso de proxy residencial o de otro proveedor es optativo y se configura en el pool global de proxys gestionado por la PWA. El password se almacena cifrado; el username permanece hoy en texto claro y 14.12.8 lo retira del contrato publico y lo cifra.

La politica de secretos, redaccion y limites anti-bot vive en `docs/security.md`; la spec runtime vive en `docs/specs/008-scheduler.md`.

## Logs

- API, worker, watchdog y frontend escriben logs de proceso a stdout/stderr; en desarrollo se consultan junto a sus dependencias con `docker compose logs api worker scheduler-watchdog frontend postgres redis`.
- `LOG_LEVEL` controla el nivel de esos logs de proceso. Para debugging local puede usarse `DEBUG`; para produccion deberia volver a `INFO` o un nivel mas restrictivo.
- Los logs operativos de monitores no son ficheros: se guardan como eventos redacted en la tabla `run_events`.
- La PWA lee esos eventos mediante `/api/runs/{run_id}/events`, `/api/monitors/{monitor_id}/events` y SSE `/api/monitors/events/stream`; el detalle de un monitor muestra la timeline acumulada aunque el monitor este detenido.
- El boton `Limpiar vista` de la PWA guarda en memoria de esa sesion los IDs de eventos visibles y los oculta localmente; no purga `run_events` ni afecta a la auditoria.
- Estado actual: no hay logger a fichero, politica de rotacion Docker, exportador externo ni job de retencion/purga de `run_events`.
