# Arquitectura

## Servicios

- `frontend`: PWA React/Vite para configuracion y operacion.
- `api`: FastAPI para auth local, REST, SSE y comandos sincronos de monitor: run manual, run inicial de una activacion, baseline, preparacion de sesion y detail probe. PostgreSQL autentica cada admision `/api` de negocio; bootstrap/login son la frontera publica previa y no existe bypass por entorno.
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

## Mapa de acceso local

La sesion de usuario PWA es distinta de `monitor_sessions` y `vinted_sessions`. Su contrato propietario es `docs/specs/011-local-pwa-access-control.md`.

```text
PWA cerrada -> GET /api/auth/session
  -> cookie ausente/invalida -> user_sessions pre-auth corta + CSRF en memoria -> Login
  -> cookie auth valida      -> usuario + CSRF en memoria -> montar Dashboard

Login + Origin + CSRF + password Argon2
  -> lock/validar pre-auth -> revocar token A -> crear token B auth -> cookie B

request de negocio
  -> hash cookie -> PostgreSQL session vigente/no revocada + users.is_active
  -> mutacion: ademas Origin exacto + CSRF ligado a B
  -> fallo DB/auth/CSRF: detener antes de la logica de negocio

Logout -> desmontar Dashboard/SSE -> revocar B -> borrar cookie
SSE -> revalidar hash/usuario durante poll; comentario + stream_heartbeat cada 15 s idle
    -> watchdog PWA 22,5 s cubre CONNECTING/silencio; cierre -> auth acotada -> reconexion unica
    -> revocacion/expiry corta el stream <= 15 s
```

PostgreSQL conserva solo el hash del token opaco. El raw existe solo en cookie host-only `HttpOnly`; el CSRF derivado existe solo en memoria de la PWA. El dashboard no se monta durante bootstrap incierto, por lo que una shell PWA cacheada/offline no muestra datos anteriores. El aprovisionamiento de usuario es CLI interactivo y no hay registro HTTP.

## Mapa de comandos de monitor

`search_sources.id` es la identidad estable del monitor. Crear, editar y archivar son comandos sincronos del proceso API; no arrancan el worker, no crean runs ni llaman a Vinted o a un proxy. Preparar una sesion, recalibrar, lanzar y detener pertenecen a otros ciclos de vida.

| Comando | PWA | API y limite PostgreSQL | Redis y lecturas derivadas | Resultado observable |
| --- | --- | --- | --- | --- |
| Crear | El formulario envia nombre y URL a `POST /api/monitors`. | La API recorta ambos valores, valida localmente HTTPS/host/ruta/filtros e inserta un monitor `manual`, inactivo y con blacklist vacia. El `201` se construye despues del commit. Un `422` de validacion no abre ninguna identidad. | La respuesta consulta, sin crear claves, si existe baseline para el nuevo ID/politica. Si Redis no esta disponible expone `baseline_ready=false` en vez de fallar. La PWA incorpora el monitor y despues solicita sus estadisticas. | La fila aparece en el listado con el mismo ID y sin run, sesion, evento u oportunidad derivados. |
| Editar | Con `is_active=false`, el detalle edita modo, cadencia, ventana, duracion y blacklist. La PWA aun no expone nombre o URL; la API si los admite. | `PATCH /api/monitors/{id}` bloquea la fila viva, conserva el ID, normaliza el payload y hace un commit. Devuelve `409` si `is_active=true`, `404` si falta o esta archivado y `422` si la configuracion es invalida; esos rechazos no persisten cambios. No comprueba si existe un run manual en curso. | La representacion vuelve a consultar el baseline y degrada una indisponibilidad Redis a `false`. Editar no encola ni crea trabajo. URL y blacklist forman parte del hash de politica: el hash resultante puede requerir calibracion salvo que su baseline aun exista en Redis. | La PWA sustituye la representacion del mismo monitor. Cambiar la URL recalcula tambien `normalized_query`. |
| Archivar | Un dialogo interno confirma `DELETE /api/monitors/{id}`. Solo tras el `204` la PWA retira fuente, draft, estadisticas/runs/eventos cargados e IDs ocultos; otros estados por monitor permanecen hoy en memoria. | La API bloquea la fila, marca `is_active=false`, borra `next_run_at`/`monitor_until`, fija `archived_at`, cierra la sesion de monitor e invalida las sesiones Vinted, purgando su contexto cifrado, antes del commit. Un ID inexistente da `404`; repetir el DELETE de uno ya archivado da `204`. | Antes del commit intenta cancelar una tarea `ready`, pero Redis no participa en la transaccion y el error se ignora actualmente. Si Redis cancela y el commit SQL falla, el monitor queda vivo sin esa tarea. No se crea ningun evento. | `GET /api/monitors` oculta la fila; un PATCH posterior da `404`, mientras PostgreSQL conserva historial y metadatos seguros. |

`is_active=false` solo significa que el scheduler no posee una sesion recurrente; no prueba que no haya un run manual ejecutandose. PATCH y el boton Guardar no excluyen hoy esa carrera. La validacion de uso normal se absorbe en la futura edicion PWA 14.26; no se mantiene una tarea autonoma de serializacion adversarial para el modelo personal de un usuario.

El commit HTTP es el limite del comando, no el de las recargas posteriores de la PWA. Tras crear, un fallo al cargar estadisticas puede mostrar error aunque la fila ya exista; tras archivar, puede fallar la recarga de oportunidades/runs/estadisticas aunque el `DELETE` ya se haya aplicado. El formulario de alta tampoco tiene aun exclusion mutua frente a dos envios rapidos. La reconciliacion honesta, el envio unico y la limpieza local completa estan acotados en 14.27; la carga inicial independiente de monitores, oportunidades, runs y proxies, en 14.28.

PostgreSQL decide si un monitor esta archivado. La implementacion actual no borra `monitor_started_at` ni cancela una tarea ya reservada o ejecutandose. Para el MVP local, el operador debe detener, esperar el terminal y despues archivar. La carrera 14.30 solo se promueve si se reproduce en uso normal; la convergencia Redis/SQL exactly-once de 14.31 se retira. La edicion PWA de nombre/URL y sus invariantes utiles se agrupan en 14.26.

## Mapa de sesion publica anonima

Una sesion Vinted preparada es contexto publico anonimo; no contiene un login de Vinted. No es la sesion de monitor de `monitor_sessions` ni una sesion de usuario de la PWA. El estado durable vive en PostgreSQL y las copias de jar activas viven solo en memoria durante un run.

| Propietario | Estado y responsabilidad actuales |
| --- | --- |
| `search_sources` | El ID del monitor delimita la sesion. El row lock actual serializa seleccion/contador, creacion, refresh de contexto y archivo en sus callers normales. `mark_vinted_session_invalid()` no adquiere ese lock; cuando corresponde, el fail-stop revierte primero el trabajo parcial y despues persiste el fallo terminal, comprobando antes de devolver que la sesion quedo invalidada y purgada. Las rotaciones ordinarias de catalogo/probe aun pueden no persistirse; la durabilidad 14.12.4 se promueve solo si esas perdidas producen fallos repetidos. La carrera que puede preparar durante archive queda bajo la regla operativa detener-esperar-archivar y el trigger condicional 14.30. |
| `proxy_profiles` | Posee transporte, pais, disponibilidad y preset de locale/idioma/viewport/`x-screen`. Persiste un contador monotono y un fingerprint HMAC-SHA256 keyed por `APP_SECRET_KEY` sobre scheme/host/port/username/password, preset y template sticky; sesion/tarea conservan el token combinado `v1:<contador>:<digest>`. Manual y productor capturan ID+token. Antes del primer evento, el run toma un advisory lock transaccional compartido y revalida existencia, actividad, cooldown, pais/preset y token; varias ejecuciones admitidas pueden compartirlo. La edicion/reconciliacion toma el advisory exclusivo y `FOR NO KEY UPDATE`, por lo que gana antes del fence y deja cero construcciones/llamadas o espera hasta el primer commit durable posterior a la ultima llamada del provider. El cierre `finalizing` posterior ya no conserva el lock porque no vuelve a emitir trafico. |
| `vinted_sessions` | Una fila conserva monitor, proxy, generacion efectiva, sticky ID, perfil, contexto geografico, estado, contadores y tiempos. Cookies, CSRF y tokens se serializan en `context_encrypted`; la generacion opaca, fingerprint de contexto, IP/pais, errores saneados y lifecycle metadata quedan fuera del cifrado. Una edicion de identidad bloquea proxy y monitores afectados en orden estable e invalida/purga sus contextos; un cambio de template se detecta e invalida al primer fence/selector tras restart. El read model devuelve como maximo una fila canonica por proxy y monitor. No hay una fila en Redis equivalente. |
| `APP_SECRET_KEY` | Deriva la clave Fernet que cifra tanto contexto Vinted como passwords de proxy. Hoy no existe un sentinel que detecte una clave global incoherente al arrancar y un ciphertext/JSON aislado tampoco tiene estado fail-stop propio. 14.12.6/14.12.7 son hardening condicional para rotacion de clave, despliegue duradero o corrupcion reproducida; la operacion local usa una clave estable y reparacion manual visible. |
| `CurlCffiVintedCatalogProvider` | En modo serial, crea un provider/jar en memoria por run, carga el contexto descifrado, conserva el mismo sticky y comparte jar entre documento, API y detalles. Los modos explicitos canary/parallel clonan ese contexto en hasta dos providers de lane con el mismo proxy/sticky, adoptan el ultimo contexto exitoso y canary vuelve a validar catalogo. El diagnostico de egress usa otra sesion sin cookies. Al cerrar cada provider se descarta solo su copia en memoria. |
| API/PWA/eventos | `Preparar sesion` crea un run de auditoria `session_prepare`; logs y respuestas exponen IDs, flags, contadores y marcadores saneados, nunca `context_encrypted`. Monitores muestra la misma fila canonica que puede escoger el runtime, con `usable_now` y un motivo seguro; Ajustes se limita al estado operativo del proxy. La sesion local de usuario protege esta lectura/comando, pero es independiente del contexto Vinted anonimo. |

La seleccion efectiva no equivale a leer `status=ready`. Runtime y API comparten un unico evaluador que exige misma generacion efectiva, monitor y proxy, perfil/impersonation, pais, locale, `Accept-Language`, viewport y `x-screen`, TTL vigente, `request_count < max_requests`, payload descifrable y contexto requerido completo. Bajo los locks de proxy y monitor, las filas de otra generacion se invalidan y vacian antes de seleccionar o preparar. Entre candidatas elegibles por metadatos se elige la usada hace mas tiempo y, como desempate, la preparada mas antigua y el menor ID; no se ordena por `request_count`. Si esa fila canonica tiene contexto ilegible o incompleto, no se salta silenciosamente a otra fila. Al reutilizar, el contador sube antes del trafico pero solo queda durable con el commit terminal; bootstrap/probe tambien preceden a una fila aceptada. Una caida abrupta puede perder el contador o repetir preparacion y se acepta como riesgo visible del MVP local: no se implementaran los ledgers 14.12.9/14.12.10. `request_count` cuenta adquisiciones/preparaciones aceptadas, no peticiones HTTP individuales.

```text
sin candidata efectiva
  -> sticky nuevo -> bootstrap catalogo -> collector DataDome -> probe diagnostico
       -> accepted_json + contexto completo -> ready (uso 1)
       -> cualquier otro resultado          -> incomplete (uso 0)
       -> excepcion antes de guardar         -> run failed, sin fila `vinted_sessions`

ready -> seleccionar + incrementar uso -> cargar mismo jar/sticky -> catalogo/detalles
      -> rotacion detectada en detalle -> actualizar la misma fila y renovar TTL
      -> rotacion ordinaria en catalogo/probe -> puede no persistirse (14.12.4)
      -> primer challenge/rechazo/429 de catalogo -> run failed + invalid + payload cifrado vacio
      -> challenge de detalle -> mismo fail-stop; candidatos reclamados quedan para una tarea futura
      -> TTL o presupuesto agotado -> no seleccionable; hoy conserva status ready y payload cifrado

archivar monitor -> invalidar todas sus filas + sustituir cada payload por un objeto vacio
```

La preparacion explicita siempre crea otra fila y no retira una `ready` anterior. Como no hay unicidad, el evaluador escoge la fila canonica con el orden LRU anterior y, solo si ninguna fila supera los metadatos, conserva el intento creado mas recientemente como diagnostico. La PWA refresca el read model al entrar en Monitores, tras los terminales/preparacion existentes y una vez en el vencimiento utilizable mas cercano; no mantiene polling ni duplica el EventSource.

No hay recuperacion provocada por una respuesta anti-bot: el primer Cloudflare, DataDome, rechazo de sesion o `429` de catalogo registra el fallo, invalida el contexto y termina la entrega. El consumer confirma la reserva sin segunda llamada, espera, refresh, escalada ni requeue. Los candidatos de detalle ya reclamados se conservan para una tarea futura, no para repetir la actual. Las cookies que rotan en una respuesta ordinaria de detalle marcan el contexto para persistencia; catalogo/probe pueden perder una rotacion. Esa durabilidad 14.12.4 queda condicional a evidencia de fallos repetidos.

`incomplete`, caducidad, agotamiento y el limite de usos detienen o excluyen trabajo, pero no purgan por si mismos el contexto cifrado. Solo la invalidacion explicita y el archivo lo sustituyen por `{}`. En el MVP local se acepta esa retencion mientras el crecimiento sea pequeno; 14.12.11 solo se promueve si las filas muestran crecimiento relevante. `ready` es estado durable historico, no una afirmacion de usabilidad actual.

## Flujo MVP

1. El usuario crea un monitor con una URL de Vinted.
2. La API guarda el monitor.
3. El scheduler (productor) evalua tiempos, jitter y ventanas, y encola como maximo una tarea pendiente por monitor mediante una escritura Redis atomica.
4. Antes de arrancar los hilos, el worker devuelve a la cola cualquier reserva sin ACK de un proceso anterior. Cada consumidor usa transporte Redis binario, reserva FIFO con `BLMOVE` hacia su propia lista `processing:{consumer_id}`, recupera solo esa lista ante una respuesta ambigua y confirma al terminar; errores inesperados reencolan y payloads invalidos pasan a dead-letter.
5. Cada tarea usa el perfil de navegador configurado para runtime.
6. La API/worker resuelve la sesion Vinted canonicamente utilizable del monitor para el proxy residencial sticky seleccionado. Si no existe o no supera la elegibilidad efectiva, la ruta autorizada puede preparar una nueva antes de tocar el catalogo del run; un ciphertext ilegible detiene la operacion sin caer a otra fila.
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
