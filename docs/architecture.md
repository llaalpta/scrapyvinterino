# Arquitectura

## Servicios

- `frontend`: PWA React/Vite para configuracion y operacion.
- `api`: FastAPI para auth local, REST, SSE y comandos sincronos de monitor: inicio de sesion con baseline interno, `Ejecutar ahora` manual, preparacion de sesion y detail probe. PostgreSQL autentica cada admision `/api` de negocio; bootstrap/login son la frontera publica previa y no existe bypass por entorno.
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
| `worker` | Valida configuracion, exige Redis, recupera reservas, inicia un productor y `WORKER_CONSUMER_COUNT` consumidores. El productor escribe el heartbeat PostgreSQL. | PostgreSQL, Redis y API `healthy`. | Health = heartbeat reciente en PostgreSQL. El supervisor sondea Redis y termina el proceso ante perdida; `restart: unless-stopped` lo repone. Un consumidor caido por otro motivo se recrea en proceso. La señal conserva una ventana obsoleta hasta vencer su timeout y no demuestra cada consumidor. |
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

`search_sources.id` es la identidad estable del monitor. Crear, editar y archivar son comandos sincronos del proceso API; no arrancan el worker, no crean runs ni llaman a Vinted o a un proxy. Preparar contexto Vinted, iniciar una sesion, ejecutar y detener pertenecen a otros ciclos de vida.

| Comando | PWA | API y limite PostgreSQL | Redis y lecturas derivadas | Resultado observable |
| --- | --- | --- | --- | --- |
| Crear | El formulario envia nombre y URL a `POST /api/monitors` bajo el unico comando de monitor local. | La API recorta ambos valores, valida localmente HTTPS/host/ruta/filtros e inserta un monitor `manual`, inactivo y con blacklist vacia. El `201` se construye despues del commit. Un `422` de validacion no abre ninguna identidad. | La respuesta no consulta Redis ni expone estado interno de baseline. Tras el `201`, la PWA incorpora el monitor y limpia el formulario antes de solicitar sus estadisticas; un fallo de esa lectura conserva el alta y muestra un aviso de refresco incompleto. | La fila aparece en el listado con el mismo ID y sin run, sesion, evento u oportunidad derivados. |
| Editar | Con `is_active=false` y sin comando/run no terminal, el detalle edita nombre, URL, modo, cadencia, ventana, duracion y blacklist. Todos los controles mutadores de Monitores quedan bloqueados mientras cualquier comando local esta en curso. | `PATCH /api/monitors/{id}` bloquea la fila viva, conserva el ID, rechaza `is_active=true` o cualquier run `running/finalizing`, normaliza el payload y hace un commit. Devuelve `409` durante sesion, baseline o drain, `404` si falta o esta archivado y `422` si la configuracion es invalida; esos rechazos no persisten cambios. | Editar no consulta Redis, encola ni crea trabajo. URL y blacklist forman parte del hash de politica: el siguiente inicio de sesion siembra ese hash. | La PWA sustituye la representacion del mismo monitor. Cambiar la URL recalcula tambien `normalized_query`. |
| Archivar | Un dialogo interno confirma `DELETE /api/monitors/{id}`. Tras el `204`, la PWA retira la fuente y todos los drafts, estadisticas, runs, eventos, rangos, mensajes, pending markers y generaciones locales indexados por su ID; el cambio del conjunto de IDs remonta la vista y descarta sus caches internos. | La API bloquea la fila, marca `is_active=false`, borra `next_run_at`/`monitor_until`, fija `archived_at`, cierra la sesion de monitor e invalida las sesiones Vinted, purgando su contexto cifrado, antes del commit. Un ID inexistente da `404`; repetir el DELETE de uno ya archivado da `204`. | Antes del commit intenta cancelar una tarea `ready`, pero Redis no participa en la transaccion y el error se ignora actualmente. Si Redis cancela y el commit SQL falla, el monitor queda vivo sin esa tarea. Un fallo de las lecturas derivadas posteriores al `204` conserva el archivo local y muestra un aviso explicito. No se crea ningun evento. | `GET /api/monitors` oculta la fila; un PATCH posterior da `404`, mientras PostgreSQL conserva historial y metadatos seguros. |

Todo inicio conserva `is_active=false` mientras obtiene el baseline y solo lo cambia junto con la apertura de sesion tras el exito. La parada tambien hace inactiva la fuente antes de que termine un run admitido. Por ello `is_active=false` por si solo no prueba ausencia de trabajo: un baseline o un drain se distinguen por su run no terminal y, en el segundo caso, por una `monitor_sessions` aun abierta. La PWA bloquea configuracion, archivo y comandos en ambos intervalos; PATCH replica el gate en PostgreSQL. Archive desde otra pestaña o cliente API conserva el riesgo de uso personal condicionado a evidencia en 14.30.

### Ciclo de sesion manual

```text
PWA Iniciar sesion -> POST /api/monitors/{id}/start
  -> source FOR UPDATE + validacion local/capacidad
  -> Run(trigger=baseline, session_id=null, running)
  -> proveedor/catalogo + Redis mark_seen/mark_baseline
  -> mismo commit SQL terminal: baseline success + source active + next_run_at=null + MonitorSession abierta
  -> fallo operacional: Run failed + source inactiva + ninguna MonitorSession

PWA Ejecutar ahora -> POST /api/monitors/{id}/runs
  -> exige source manual activa + MonitorSession abierta + single-flight
  -> Run(trigger=manual, session_id=<sesion abierta>)
  -> misma dedupe/filtros/persistencia que scheduler; terminal success mantiene la sesion
  -> marker ausente/Redis no disponible/fallo manual: Run failed + sesion cerrada + source inactiva

PWA Detener sesion -> POST /api/monitors/{id}/stop
  -> source FOR NO KEY UPDATE; PostgreSQL queda inactivo y sin deadlines antes del cleanup Redis
  -> sin run no terminal: cierra MonitorSession con reason=stopped en el mismo commit
  -> run(es) de sesion running/finalizing: devuelve 200, deja la sesion abierta y la PWA muestra Deteniendo...
  -> cada terminal conserva su resultado; el ultimo terminal normal cierra esa sesion con reason=stopped
  -> fail-stop fuerte conserva su razon diagnostica; un baseline sin sesion sigue devolviendo 409
  -> una tarea ya reservada revalida la source bajo el mismo lock y, mientras siga inactiva, no crea run/proveedor y hace ACK
```

El factory de admision usado por consumer, la parada y el terminal usan `FOR NO KEY UPDATE`: esos escritores se excluyen entre si, pero la parada no espera los locks `KEY SHARE` que los FK de eventos mantienen durante el I/O del proveedor. El comando manual conserva un gate exterior `FOR UPDATE`, que se libera al confirmar el run antes del I/O. Ningun flujo cambia `search_sources.id`.

### Ciclo de sesion automatica

```text
PWA Iniciar sesion -> POST /api/monitors/{id}/start
  -> admision scheduler/capacidad + seleccion de egress antes de trafico
  -> Run(trigger=baseline, session_id=null) mientras source sigue inactiva
  -> proveedor/catalogo + Redis mark_seen/mark_baseline; nunca crea oportunidades
  -> segunda admision serializada
     -> exito: mismo commit SQL terminal abre MonitorSession, activa source y fija next_run_at futuro
     -> 503/409: confirma solo el baseline success; source/sesion/deadline siguen ausentes

SchedulerRunner al vencer next_run_at
  -> PostgreSQL prevalece sobre el espejo temporal; encola una MonitorTask unica en Redis
  -> TaskConsumer reserva, ejecuta Run(trigger=scheduler, session_id=<sesion abierta>) y hace ACK terminal
  -> mismos IDs: no-op; un ID nuevo: una oportunidad; repeticion: ninguna

PWA Detener sesion sigue el mismo drain que manual
  -> el commit inactivo impide nuevas admisiones y deadlines antes de cancelar ready best-effort
  -> los runs ya admitidos terminan sin cancelacion de red; el ultimo terminal normal cierra MonitorSession
```

El commit HTTP es el limite del comando, no el de las recargas posteriores de la PWA. Un mutex inmediato respaldado por `ref` admite un solo comando de monitor por instancia PWA, antes del primer `await`; el estado renderizado deshabilita a la vez el alta y todas las mutaciones de detalle. Alta, inicio, run, diagnosticos y archivo aplican primero la respuesta confirmada y distinguen despues cualquier lectura derivada incompleta; stop conserva su aviso especifico equivalente. Las lecturas de la lista de fuentes llevan una generacion monotona y cada `201`/`204` que cambia su conjunto la invalida antes de añadir o retirar el ID, por lo que un snapshot anterior no puede ocultarlo o reinsertarlo. Esta exclusion es local a una instancia, no un lock entre pestañas o clientes API. Al montar el dashboard, monitores, oportunidades, runs y proxies aplican sus respuestas iniciales por separado; un fallo enumera solo su superficie y no retiene los resultados validos de las demas.

PostgreSQL decide si un monitor esta archivado. La implementacion actual no cancela trabajo ya reservado o ejecutandose desde otro cliente. Para el MVP local, la PWA bloquea archivo durante su comando y el operador debe detener, esperar el terminal y despues archivar. La carrera multiventana/API 14.30 solo se promueve si se reproduce en uso normal; la convergencia Redis/SQL exactly-once de 14.31 se retira. La edicion PWA de nombre/URL y sus invariantes utiles se agrupan en 14.26.

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
