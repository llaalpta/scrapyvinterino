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
5. Cada tarea selecciona un perfil de navegador aleatorio coherente.
6. Se genera un UUID de sesion sticky para el proxy residencial y se inyecta con `PROXY_STICKY_USERNAME_TEMPLATE`.
7. Se crea una sesion `curl_cffi` con `impersonate` para falsificar TLS/JA3.
8. Se realiza un bootstrap anonimo para obtener cookies/tokens publicos (incluyendo datadome).
9. Se aplica un delay humano y se verifica que no haya challenge de DataDome.
10. Con el mismo cliente y la misma IP, se pide el catalogo JSON.
11. Se deduplican candidatos contra la cache Redis del monitor.
12. Se aplican filtros de exclusion y se crean oportunidades.
13. Se descarta la sesion, el proxy y las cookies al terminar la tarea.
14. La PWA muestra oportunidades y estado de ejecucion.
