# 011 - Control de acceso local de la PWA

## Estado

Completada el 2026-07-13 por la tarea 14.12.1.

## Objetivo

La PWA y todos los datos/comandos de negocio de `/api` son privados. El acceso usa usuarios locales y sesiones opacas durables en PostgreSQL; no usa una cuenta Vinted, JWT, Redis ni un bypass por entorno.

## Frontera publica

La frontera publica de runtime/negocio contiene solo:

- `GET /health`, con el JSON minimo de liveness y sin estado de negocio;
- `GET /api/auth/session`, que valida la cookie actual o crea una sesion preautenticada corta para obtener CSRF;
- `POST /api/auth/login`, que exige la sesion preautenticada, CSRF y un `Origin` exacto permitido;
- preflight `OPTIONS`, resuelto por CORS solo para origenes exactos permitidos.

Todas las demas rutas `/api`, incluidas lecturas, mutaciones, SSE, acciones deshabilitadas y tombstones `410`, exigen una sesion local autenticada y un usuario activo. Una dependencia PostgreSQL ausente o ilegible produce indisponibilidad; nunca existe un fallback autenticado. `/docs`, `/redoc` y `/openapi.json` son diagnosticos publicos solo en development/test; fuera de esos entornos OpenAPI, Swagger y ReDoc estan desactivados.

## Sesion y password

- El token de cookie es aleatorio, opaco y de 256 bits. PostgreSQL guarda solo SHA-256 del token.
- `user_sessions` conserva usuario nullable para preautenticacion, creacion, expiracion absoluta, autenticacion y revocacion. Redis y el frontend no guardan una copia.
- La preautenticacion dura 10 minutos por defecto. Una sesion autenticada dura 168 horas por defecto y no se desliza por uso.
- Login correcto revoca la sesion anterior y emite otro token, incluso si la cookie anterior ya estaba autenticada. Credenciales invalidas, usuario ausente e inactivo devuelven el mismo error.
- Logout requiere sesion, `Origin` y CSRF, revoca el estado servidor y elimina la cookie. La cookie capturada no vuelve a ser valida, tampoco tras reiniciar la API.
- Los passwords nuevos se validan y se guardan con Argon2 mediante `pwdlib`; no se siembran en migraciones, argumentos, variables versionadas ni logs. El aprovisionamiento es un comando interactivo sin registro publico.

La cookie se llama `vinted_monitor_session`, es host-only, `HttpOnly`, `SameSite=Strict`, `Path=/api` y no declara `Domain`. Usa `Secure` fuera de development/test. Login y logout emiten `Cache-Control: no-store`; el resto de respuestas `/api` tambien es no-store salvo el contrato SSE explicito `no-cache, no-transform`.

## CSRF y origen

- El token CSRF se deriva con HMAC de `APP_SECRET_KEY` y del token opaco. Se devuelve por `/api/auth/session`/login y vive solo en memoria de la PWA.
- `POST`, `PUT`, `PATCH` y `DELETE`, incluido login/logout, exigen `Origin` exacto de `BACKEND_CORS_ORIGINS`. Ausente, `null`, con otro esquema/host/puerto o con sufijo malicioso devuelve `403`.
- Toda mutacion autenticada exige ademas `X-CSRF-Token`. Login exige el token de la sesion preautenticada. Un rechazo ocurre antes de ejecutar logica de negocio y no se reintenta automaticamente.
- CORS permite credenciales solo para origenes exactos y nunca sustituye autenticacion o CSRF.

## PWA

1. El root consulta `/api/auth/session` con `cache: no-store`; mientras no haya respuesta no monta `DashboardApp` ni solicita datos de negocio.
2. Una sesion anonima muestra solo el formulario. Un fallo de red/servidor muestra acceso no verificable y permanece cerrado.
3. Login correcto conserva usuario y CSRF solo en memoria y entonces monta el dashboard. Todos los fetch usan credenciales same-origin y las mutaciones usan CSRF.
4. Cualquier `401` de negocio desmonta el dashboard, cierra EventSource y vuelve a comprobar la sesion. No conserva datos entre usuarios.
5. Logout desmonta primero el dashboard. Si no puede confirmar la revocacion mantiene una pantalla cerrada con reintento; no afirma que cerro sesion.
6. El backend emite el comentario keepalive y un evento nombrado `stream_heartbeat` sin ID ni avance de cursor cada 15 segundos idle. La PWA rearma con esa señal un watchdog de liveness de 22,5 segundos que tambien cubre `CONNECTING`; un error o silencio cierra la instancia actual, acota/cancela la comprobacion de auth y la revalida antes de una unica reconexion secuencial. El backend revalida durante el stream y lo termina como maximo dentro del heartbeat tras expiracion, revocacion o desactivacion.

## Despliegue

- Desarrollo publica PostgreSQL, Redis, API y Vite solo en `127.0.0.1`.
- El ejemplo productivo no publica puertos de contenedor: Traefik expone el frontend y `/api` bajo el mismo `APP_HOST`; `/health` permanece interno.
- Produccion deriva el unico origen permitido de `https://${APP_HOST}`. Una allowlist vacia, wildcard o no HTTPS es configuracion invalida fuera de development/test.
- No existe `AUTH_ENABLED` ni bypass de test. Una instalacion sin usuario activo queda cerrada y se aprovisiona por CLI antes de operar.

## Criterios de aceptacion

- Una matriz de rutas registrada demuestra que toda ruta de negocio sin cookie devuelve `401` antes de resolver IDs o ejecutar providers; no se llama a Vinted/proxy.
- PostgreSQL real demuestra bootstrap, Argon2, login, rotacion/fijacion, expiracion, usuario inactivo, revocacion, reinicio y logout.
- Cookie y CORS se comprueban en development y en un proceso production aislado. El QA valida flags/estado de forma estructurada y nunca imprime el `Set-Cookie`, token, CSRF ni password raw.
- Cookie valida mas CSRF ausente/incorrecto u Origin hostil devuelve `403` y no cambia filas/Redis. El token correcto permite una mutacion local inocua creada para QA y se limpia.
- SSE sin sesion no emite `stream_ready`; una sesion revocada cierra un stream abierto antes de 15 segundos y no entrega eventos posteriores. Un stream idle sano permanece unico mas alla de 22,5 segundos por `stream_heartbeat`; un transporte silencioso o atascado se cierra, revalida y no reconecta negocio antes de auth.
- Playwright prueba login invalido/valido, ausencia de cargas antes de auth, navegacion, reload/restart y logout contra PWA/API/PostgreSQL reales, bloqueando cualquier host externo.
- Alembic pasa desde cero y desde 0017. Ruff, suite enfocada, suite backend, lint y build pasan; QA elimina usuario/sesiones/filas propias y restaura servicios.

## Evidencia de cierre

- Alembic paso cero-a-head, 0017-a-0018, downgrade 0018-a-0017 y reupgrade en una base aislada eliminada despues; el stack vivo quedo en `0018_local_user_sessions (head)`.
- La matriz protegida, rotacion, expiracion, usuario inactivo, CSRF/Origin, cookie development/production, SSE revocado y HTTP/PostgreSQL real pasaron en 44 pruebas enfocadas. La suite completa paso `450` pruebas con una integracion HTTP opt-in omitida en esa ejecucion y ejecutada/pasada explicitamente contra el stack.
- Un proceso production aislado devolvio `404` para docs/OpenAPI, `401` para negocio sin sesion y cookie `Secure`/host-only. Compose renderizo cuatro puertos development solo en loopback, cero puertos production y un origen HTTPS exacto igual en API, worker y watchdog.
- Playwright/Chrome contra PWA/API/PostgreSQL reales hizo 38 requests locales: bloqueo antes de auth, login invalido/valido, reload, un unico SSE, salida/cursor, 24 segundos idle sin reconexion, restart real de API, `auth/session` antes de la unica reconexion y logout fallido/reintentado. Hubo tres streams totales y cero hosts externos.
- PostgreSQL detenido mantuvo `/health=200`, hizo fallar la admision privada con `503` y recupero `401` para la cookie invalida al volver sin reiniciar API. El primer full-suite con clave sintetica expuso la dependencia de un proxy host cifrado que posteriormente cerro 14.18 para el canary real seleccionado; la repeticion uso solo el contexto local del API y paso sin modificarlo ni mostrar la clave.
- Ruff, lint y build pasaron. El runner autocontenido retiro su usuario y sesiones exactas; quedaron cero usuarios/sesiones/filas o claves Redis QA, cero runs en curso, worker/watchdog detenidos y el Vite host preexistente intacto. No se llamo a Vinted ni a proxies.

## Fuera de alcance

- Login/rate limiting adaptativo y bloqueo por abuso: tarea 14.32.
- CSP y cabeceras de endurecimiento del documento PWA: tarea 14.33.
- Recuperacion de password, registro publico, multirol y autenticacion Vinted no forman parte del MVP actual.
- La identidad proxy 14.12.2 esta cerrada; la elegibilidad honesta 14.12.5 permanece en `Now`. Sentinel, corrupcion, credenciales y retencion son hardening condicional, y los ledgers de crash 14.12.9/14.12.10 no forman parte del MVP local.
