# Arquitectura

## Servicios

- `frontend`: PWA React/Vite para configuracion y operacion.
- `api`: FastAPI para REST, login local y eventos.
- `worker`: productor (scheduler), consumidores (task workers), scraping, deduplicacion, filtros y acciones pendientes.
- `postgres`: persistencia.
- `redis`: cola fiable de tareas con reserva/ACK, cache de vistos/procesamiento y reintentos de detalle por monitor/politica.

## Modulos backend

- `api`: endpoints HTTP.
- `core`: configuracion y logging.
- `db`: modelos, sesiones y migraciones.
- `services`: logica de fuentes, items, filtros, cola de tareas y acciones.
- `providers`: proveedor Vinted con `curl_cffi`, perfiles de navegador y deteccion DataDome.
- `worker`: scheduler (productor), consumidores de tareas y ejecuciones manuales.

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
13. Solo un detalle que cumple la politica de campos requeridos pasa por filtros y puede persistir item/oportunidad. Fallos recuperables quedan en Redis con backoff sin marcar `seen`; resultados terminales actualizan `seen` despues del commit PostgreSQL.
14. Se guardan todas las URL firmadas de fotos publicas, no sus bytes. La PWA descarga las imagenes directamente desde el CDN de Vinted y muestra precios y disponibilidad publica.
15. La sesion Vinted del monitor se conserva cifrada para usos posteriores hasta caducar, agotar contador, alcanzar el limite opcional de usos del monitor o invalidarse por rechazo/challenge.
16. La PWA muestra oportunidades, estado de ejecucion y diagnosticos saneados de sesion en los logs del monitor.
17. `runs.task_id` permite reconocer una tarea redeliverada: los runs terminales no repiten trafico, los `finalizing` convergen Redis y un `running` huerfano se cierra antes del nuevo intento.
