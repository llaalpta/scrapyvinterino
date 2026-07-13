# Arquitectura

## Servicios

- `frontend`: PWA React/Vite para configuracion y operacion.
- `api`: FastAPI para REST, login local, SSE y comandos sincronos de monitor: run manual, run inicial de una activacion, baseline, preparacion de sesion y detail probe.
- `worker`: un proceso con el productor recurrente (scheduler) y consumidores de Redis para runs de monitor, scraping, deduplicacion y filtros.
- `scheduler-watchdog`: proceso fail-stop separado que detiene en PostgreSQL solo monitores recurrentes cuando expira el heartbeat del productor y despues intenta retirar su tarea ready de Redis.
- `postgres`: persistencia.
- `redis`: cola fiable de tareas con reserva/ACK, cache de vistos/procesamiento y reintentos de detalle por monitor/politica.

## Mapa de ciclo de vida de servicios

No hay perfiles Compose: un `docker compose up` sin lista de servicios intenta arrancar los seis. El grafo de arranque vigente es:

```text
postgres healthy + redis healthy
              -> api: alembic upgrade head -> uvicorn -> /health

postgres healthy + redis healthy + api healthy
              -> worker: productor + consumidores

postgres healthy + api healthy
              -> scheduler-watchdog (Redis no bloquea su arranque)

api service_started -> frontend Vite (desarrollo)
api healthy         -> frontend Nginx (ejemplo de produccion)
```

| Servicio | Entrypoint y estado que posee | Gate de arranque | Health y supervision actuales |
| --- | --- | --- | --- |
| `postgres` | Imagen oficial; volumen `postgres-data` con todo el estado SQL. | Ninguno. | `pg_isready`; sin politica `restart`. |
| `redis` | Redis con AOF `appendfsync=always`; volumen `redis-data` con cola/cache. | Ninguno. | `PING`; sin politica `restart`. |
| `api` | Solo el comando Compose ejecuta Alembic antes de Uvicorn. Los comandos sincronos de monitor y la publicacion SSE viven aqui. | PostgreSQL y Redis `healthy`. | `/health` solo demuestra que Uvicorn responde despues del comando Alembic; no vuelve a consultar DB, Redis ni revision. Sin `restart`. |
| `worker` | Valida configuracion, exige Redis, recupera reservas, inicia un productor y `WORKER_CONSUMER_COUNT` consumidores. El productor escribe el heartbeat PostgreSQL. | PostgreSQL, Redis y API `healthy`. | Health = heartbeat reciente en PostgreSQL, no salud de Redis ni de cada consumidor. `restart: unless-stopped`; un consumidor caido se recrea en proceso y la perdida del productor termina el contenedor. |
| `scheduler-watchdog` | Tras una gracia, relee heartbeat y detiene recurrentes primero en PostgreSQL; Redis es cleanup posterior y best-effort. | PostgreSQL y API `healthy`; no depende de Redis. | Sin healthcheck; un error no controlado termina el proceso y `unless-stopped` lo repone, pero un hang no se detecta. |
| `frontend` | Vite en desarrollo; build estatico Nginx en produccion. No posee estado de negocio. | API iniciada en desarrollo, `healthy` en produccion. | Sin healthcheck ni `restart`. |

`depends_on` solo ordena el arranque. Una dependencia que cae despues no detiene ni reinicia sus consumidores. Los comandos `docker compose stop` o `kill` son acciones manuales y dejan el servicio detenido; `unless-stopped` actua cuando el proceso sale por un fallo no solicitado.

La propiedad de Alembic no pertenece al `backend/Dockerfile`: su `CMD` generico arranca Uvicorn sin migrar. Solo los comandos de ambos archivos Compose establecen `alembic upgrade head && uvicorn`; un reload de codigo en desarrollo tampoco repite migraciones.

## Modulos backend

- `api`: endpoints HTTP.
- `core`: configuracion y logging.
- `db`: modelos, sesiones y migraciones.
- `services`: logica de fuentes, items, filtros, cola de tareas y acciones.
- `providers`: proveedor Vinted con `curl_cffi`, perfiles de navegador y deteccion DataDome.
- `worker`: scheduler recurrente, consumidores de tareas y watchdog; las ejecuciones sincronas enumeradas arriba pertenecen al proceso API.

## Flujo MVP

1. El usuario crea un monitor con una URL de Vinted.
2. La API guarda el monitor.
3. El scheduler (productor) evalua tiempos, jitter y ventanas, y encola como maximo una tarea pendiente por monitor mediante una escritura Redis atomica.
4. Antes de arrancar los hilos, el worker devuelve a la cola cualquier reserva sin ACK de un proceso anterior. Cada consumidor usa transporte Redis binario, reserva FIFO con `BLMOVE` hacia su propia lista `processing:{consumer_id}`, recupera solo esa lista ante una respuesta ambigua y confirma al terminar; errores inesperados reencolan y payloads invalidos pasan a dead-letter.
5. Cada tarea usa el perfil de navegador configurado para runtime.
6. La API/worker busca una sesion Vinted `ready` del monitor para el proxy residencial sticky seleccionado. Si no existe o caduco, intenta prepararla automaticamente antes de tocar el catalogo del run.
7. Se crea una sesion `curl_cffi` con `impersonate` para falsificar TLS/JA3. La preparacion navega el documento de catalogo, extrae contexto anonimo seguro, prueba la API de catalogo con la misma sesion y guarda cookies/tokens cifrados en `vinted_sessions`.
8. Se diagnostica egress con la misma IP/proxy y se valida pais, locale, viewport, Vinted `x-screen=catalog`, CSRF, anon id, `access_token_web`, `v_udt`, `__cf_bm` y DataDome. La validacion de IP/pais puede reutilizarse brevemente para la misma sesion/sticky id; un contexto preparado incompleto nunca se reutiliza.
9. Si falta contexto base o el probe no acepta JSON, el run falla antes de pedir `/api/v2/catalog/items` para el scraping.
10. Con el mismo proxy sticky y el contexto anonimo guardado, se pide el catalogo JSON.
11. Se reclaman primero los reintentos de detalle vencidos y despues los candidatos nuevos deduplicados contra Redis.
12. Cada candidato reclamado navega su documento publico `/items/...?...referrer=catalog`; el modo estable es secuencial y el canario permite ondas sobre dos lanes HTTP persistentes clonados desde el mismo contexto/sticky id. La red y el parser pueden ejecutarse en paralelo, pero PostgreSQL, Redis y los eventos persistidos se resuelven en orden en el hilo principal.
13. Solo un detalle que cumple la politica de campos requeridos pasa por la blacklist de descripcion y puede persistir item/oportunidad. El mismo GET puede cerrarse si una descripcion aislada de forma segura ya coincide; cualquier caso ambiguo continua hasta EOF. Fallos recuperables quedan en Redis con backoff sin marcar `seen`; resultados terminales actualizan `seen` despues del commit PostgreSQL.
14. Se guardan todas las URL firmadas de fotos publicas, no sus bytes. La PWA descarga las imagenes directamente desde el CDN de Vinted y muestra precios y disponibilidad publica.
15. La sesion Vinted del monitor se conserva cifrada para usos posteriores hasta caducar, agotar contador, alcanzar el limite opcional de usos del monitor o invalidarse por rechazo/challenge.
16. La PWA muestra oportunidades, estado de ejecucion y diagnosticos saneados de sesion en los logs del monitor.
17. `runs.task_id` permite reconocer una tarea redeliverada: los runs terminales no repiten trafico, los `finalizing` convergen Redis y un `running` huerfano se cierra antes del nuevo intento.
