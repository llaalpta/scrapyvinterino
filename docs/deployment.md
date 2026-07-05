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
| `.env` | Infraestructura, secretos, kill-switches, workers y evasion anti-bot: DB/Redis, CORS, `APP_SECRET_KEY`, `SCHEDULER_ENABLED`, `WORKER_CONSUMER_COUNT`, `WORKER_MAX_RETRY_ATTEMPTS`, `VINTED_REQUEST_RETRIES`, `CURL_IMPERSONATE_BROWSER`, delays humanos, penalizacion DataDome y plantilla sticky de proxy. |
| PWA | Operacion diaria: habilitar scheduler en app, runs simultaneos, salida directa, limites por run, timeout HTTP, pausa de proxy tras fallo, parada de monitor tras fallos, y alta/test/pausa de proxys. |
| Backend | Limites duros de validacion y defaults seguros cuando no hay override operativo. |

Claves legacy de `app_settings.scheduler` como `max_runs_per_proxy` y `request_retries` se ignoran al leer y se podan en el siguiente guardado de ajustes.

Algunos valores `.env` tambien sirven como defaults cuando aun no existe override operativo en `app_settings.scheduler`; por ejemplo `VINTED_REQUEST_TIMEOUT_MS`. Una vez guardado desde la PWA, el valor persistido en DB es la fuente de verdad operativa.

## Outbound Vinted Proxy

El monitor debe funcionar sin proxy de salida por defecto cuando el ajuste global de acceso directo lo permite. El uso de proxy residencial o de otro proveedor es optativo y se configura en el pool global de proxys gestionado por la PWA; las credenciales se almacenan cifradas en base de datos.

La politica de secretos, redaccion y limites anti-bot vive en `docs/security.md`; la spec runtime vive en `docs/specs/008-scheduler.md`.
