# Despliegue

Produccion se preparara en una fase posterior con `docker-compose.prod.yml`.

## Objetivo

- Desplegar detras de Traefik y Cloudflare.
- No exponer Postgres.
- Servir frontend en `/`.
- Servir API en `/api`.
- Soportar SSE o WebSocket para eventos en vivo.

## Desarrollo vs Produccion

Desarrollo usa puertos locales directos. Produccion usara labels de Traefik y red externa compartida con el reverse proxy.

## Configuracion Runtime

La configuracion no debe tener dos fuentes de verdad activas:

| Dueño | Uso |
| --- | --- |
| `.env` | Infraestructura, secretos, kill-switches, workers, runtime cache y evasion anti-bot: DB/Redis, CORS, `APP_SECRET_KEY`, `SCHEDULER_ENABLED`, `SEEN_CACHE_TTL_SECONDS`, `SEEN_PROCESSING_TTL_SECONDS`, `SEEN_CACHE_MAX_PER_MONITOR`, `WORKER_CONSUMER_COUNT`, `WORKER_MAX_RETRY_ATTEMPTS`, `VINTED_REQUEST_RETRIES`, `CURL_IMPERSONATE_BROWSER`, delays humanos, penalizacion DataDome y plantilla sticky de proxy. |
| PWA | Operacion diaria: habilitar scheduler en app, runs simultaneos, salida directa, limites por run, timeout HTTP, pausa de proxy tras fallo, parada de monitor tras fallos, y alta/test/pausa de proxys. |
| Backend | Limites duros de validacion y defaults seguros cuando no hay override operativo. |

Algunos valores `.env` tambien sirven como defaults cuando aun no existe override operativo en `app_settings.scheduler`; por ejemplo `VINTED_REQUEST_TIMEOUT_MS`. Una vez guardado desde la PWA, el valor persistido en DB es la fuente de verdad operativa.

Redis conserva AOF en un volumen Docker tanto en desarrollo como en el ejemplo de produccion. El worker se despliega como una unica instancia con varios consumidores internos: recupera las listas `vinted:task_queue:processing*` antes de iniciar scheduler y consumidores, y no se deben arrancar replicas independientes hasta incorporar ownership/visibility timeout distribuido.

Fuera de `development` y `test`, el backend rechaza al arrancar una `APP_SECRET_KEY` de menos de 32 caracteres o igual a cualquiera de los placeholders versionados. Cada despliegue debe generar y custodiar un valor aleatorio propio; cambiarlo exige recifrar previamente las credenciales de proxy y los contextos de sesion existentes.

## Outbound Vinted Proxy

El monitor debe funcionar sin proxy de salida por defecto cuando el ajuste global de acceso directo lo permite. El uso de proxy residencial o de otro proveedor es optativo y se configura en el pool global de proxys gestionado por la PWA; las credenciales se almacenan cifradas en base de datos.

La politica de secretos, redaccion y limites anti-bot vive en `docs/security.md`; la spec runtime vive en `docs/specs/008-scheduler.md`.

## Logs

- Backend y worker escriben logs de proceso a stdout/stderr; en desarrollo se consultan con `docker compose logs api worker`.
- `LOG_LEVEL` controla el nivel de esos logs de proceso. Para debugging local puede usarse `DEBUG`; para produccion deberia volver a `INFO` o un nivel mas restrictivo.
- Los logs operativos de monitores no son ficheros: se guardan como eventos redacted en la tabla `run_events`.
- La PWA lee esos eventos mediante `/api/runs/{run_id}/events`, `/api/monitors/{monitor_id}/events` y SSE `/api/monitors/events/stream`; el detalle de un monitor muestra la timeline acumulada aunque el monitor este detenido.
- El boton `Limpiar vista` de la PWA guarda en memoria de esa sesion los IDs de eventos visibles y los oculta localmente; no purga `run_events` ni afecta a la auditoria.
- Estado actual: no hay logger a fichero, politica de rotacion Docker, exportador externo ni job de retencion/purga de `run_events`.
