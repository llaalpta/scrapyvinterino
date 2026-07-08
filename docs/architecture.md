# Arquitectura

## Servicios

- `frontend`: PWA React/Vite para configuracion y operacion.
- `api`: FastAPI para REST, login local y eventos.
- `worker`: productor (scheduler), consumidores (task workers), scraping, deduplicacion, filtros y acciones pendientes.
- `postgres`: persistencia.
- `redis`: cola de tareas (patron productor-consumidor con LPUSH/BRPOP), cache de vistos/procesamiento por monitor.

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
3. El scheduler (productor) evalua tiempos, jitter y ventanas, y encola tareas en Redis.
4. Los workers consumidores escuchan la cola Redis via BRPOP.
5. Cada tarea usa el perfil de navegador configurado para runtime.
6. La API/worker exige una sesion Vinted `ready` preparada para el proxy residencial sticky seleccionado.
7. Se crea una sesion `curl_cffi` con `impersonate` para falsificar TLS/JA3 y se cargan cookies/tokens cifrados de `vinted_sessions`.
8. Se diagnostica egress con la misma IP/proxy y se valida pais, locale, viewport, Vinted `x-screen=catalog`, CSRF, anon id, `access_token_web`, DataDome y `v_udt`.
9. Si el contexto esta incompleto, el run falla antes de pedir `/api/v2/catalog/items`.
10. Con el mismo cliente, la misma IP y el mismo contexto anonimo, se pide el catalogo JSON.
11. Se deduplican candidatos contra la cache Redis del monitor.
12. Se aplican filtros de exclusion y se crean oportunidades.
13. La sesion Vinted preparada se conserva cifrada para usos posteriores hasta caducar, agotar contador o invalidarse por rechazo/challenge.
14. La PWA muestra oportunidades, estado de ejecucion y estado de preparacion de sesion por proxy.
