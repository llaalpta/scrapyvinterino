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

## Mapa de comandos de monitor

`search_sources.id` es la identidad estable del monitor. Crear, editar y archivar son comandos sincronos del proceso API; no arrancan el worker, no crean runs ni llaman a Vinted o a un proxy. Preparar una sesion, recalibrar, lanzar y detener pertenecen a otros ciclos de vida.

| Comando | PWA | API y limite PostgreSQL | Redis y lecturas derivadas | Resultado observable |
| --- | --- | --- | --- | --- |
| Crear | El formulario envia nombre y URL a `POST /api/monitors`. | La API recorta ambos valores, valida localmente HTTPS/host/ruta/filtros e inserta un monitor `manual`, inactivo y con blacklist vacia. El `201` se construye despues del commit. Un `422` de validacion no abre ninguna identidad. | La respuesta consulta, sin crear claves, si existe baseline para el nuevo ID/politica. Si Redis no esta disponible expone `baseline_ready=false` en vez de fallar. La PWA incorpora el monitor y despues solicita sus estadisticas. | La fila aparece en el listado con el mismo ID y sin run, sesion, evento u oportunidad derivados. |
| Editar | Con `is_active=false`, el detalle edita modo, cadencia, ventana, duracion y blacklist. La PWA aun no expone nombre o URL; la API si los admite. | `PATCH /api/monitors/{id}` bloquea la fila viva, conserva el ID, normaliza el payload y hace un commit. Devuelve `409` si `is_active=true`, `404` si falta o esta archivado y `422` si la configuracion es invalida; esos rechazos no persisten cambios. No comprueba si existe un run manual en curso. | La representacion vuelve a consultar el baseline y degrada una indisponibilidad Redis a `false`. Editar no encola ni crea trabajo. URL y blacklist forman parte del hash de politica: el hash resultante puede requerir calibracion salvo que su baseline aun exista en Redis. | La PWA sustituye la representacion del mismo monitor. Cambiar la URL recalcula tambien `normalized_query`. |
| Archivar | Un dialogo interno confirma `DELETE /api/monitors/{id}`. Solo tras el `204` la PWA retira fuente, draft, estadisticas/runs/eventos cargados e IDs ocultos; otros estados por monitor permanecen hoy en memoria. | La API bloquea la fila, marca `is_active=false`, borra `next_run_at`/`monitor_until`, fija `archived_at`, cierra la sesion de monitor e invalida las sesiones Vinted, purgando su contexto cifrado, antes del commit. Un ID inexistente da `404`; repetir el DELETE de uno ya archivado da `204`. | Antes del commit intenta cancelar una tarea `ready`, pero Redis no participa en la transaccion y el error se ignora actualmente. Si Redis cancela y el commit SQL falla, el monitor queda vivo sin esa tarea. No se crea ningun evento. | `GET /api/monitors` oculta la fila; un PATCH posterior da `404`, mientras PostgreSQL conserva historial y metadatos seguros. |

`is_active=false` solo significa que el scheduler no posee una sesion recurrente; no prueba que no haya un run manual ejecutandose. PATCH y el boton Guardar no excluyen hoy esa carrera, que pertenece a 14.25.

El commit HTTP es el limite del comando, no el de las recargas posteriores de la PWA. Tras crear, un fallo al cargar estadisticas puede mostrar error aunque la fila ya exista; tras archivar, puede fallar la recarga de oportunidades/runs/estadisticas aunque el `DELETE` ya se haya aplicado. El formulario de alta tampoco tiene aun exclusion mutua frente a dos envios rapidos. La reconciliacion honesta, el envio unico y la limpieza local completa estan acotados en 14.27; la carga inicial independiente de monitores, oportunidades, runs y proxies, en 14.28.

PostgreSQL decide si un monitor esta archivado. La implementacion actual no borra `monitor_started_at`, no cancela una tarea ya reservada o ejecutandose y no converge ambos sentidos del corte Redis/PostgreSQL al archivar; esos cierres pertenecen respectivamente a 14.30 y 14.31. La edicion PWA de nombre/URL pertenece a 14.26 y los invariantes de longitud, default SQL y `updated_at`, a 14.29.

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
