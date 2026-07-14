# Modelo de Datos

Tablas principales:

- `users`: identidades locales aprovisionadas fuera de HTTP y estado activo. El nuevo CLI normaliza el email y genera un hash Argon2 antes de insertar; la tabla 0018 no transforma ni promete compatibilidad para filas de desarrollo creadas por mecanismos anteriores. No representa cuentas Vinted ni roles de acciones futuras.
- `user_sessions`: sesiones preautenticadas/autenticadas locales. Conserva solo hash SHA-256 unico del token opaco, usuario nullable, creacion, expiracion absoluta, autenticacion y revocacion. La cookie raw y el CSRF derivado nunca se persisten; Redis no participa en auth.
- `search_sources`: monitores de oportunidad reutilizables; guardan URL, modo, cadencia, `filter_definition` con terminos excluyentes propios del monitor y estado runtime. `archived_at` oculta un monitor sin borrar historico.
- `app_settings`: estado global con ownership por clave. `scheduler` contiene la configuracion operativa mutable desde la PWA; `scheduler_worker_heartbeat` contiene exclusivamente la ultima señal UTC escrita por el productor, cuya caducidad se configura en `.env`.
- `monitor_sessions`: periodos historicos de lanzamiento de un monitor. El baseline de cualquier inicio no pertenece a una sesion; tras su exito se abre una fila. En manual, todos los `Ejecutar ahora` posteriores la reutilizan; en automatico, la reutilizan los runs del scheduler hasta parada, expiracion o fail-stop.
- `runs`: ejecuciones de monitor, con `trigger`, `monitor_session_id`, `task_id` indexado para redelivery idempotente, contadores de filtrado y metadatos runtime.
- `items`: articulos normalizados de Vinted que llegaron a oportunidad; `vinted_item_id` define identidad de catalogo/cache.
- `opportunities`: articulos vistos por un monitor que no fueron descartados; es la tabla principal de resultados utiles del producto y su unicidad notificable es por monitor e item.
- `proxy_profiles`: pool global de proxys configurables desde UI con password cifrado, username actualmente en texto claro, tipo, capacidad y estado operativo. `identity_generation` es un contador monotono e `identity_fingerprint` el HMAC-SHA256 keyed actual de transporte, credenciales, preset y template sticky; no contienen el preimage. 14.12.8 unifica la clasificacion y el read model de ambas credenciales.
- `vinted_sessions`: sesiones anonimas publicas de Vinted propiedad de un monitor y asociadas al proxy sticky usado; guardan en `proxy_identity_generation` el token combinado `v1:<contador>:<digest>`, ademas de cookies/tokens cifrados, contexto seguro, contador de uso, expiracion e invalidacion. Puede haber historial de varias filas por monitor/proxy, pero runtime y API comparten el orden LRU y exponen como maximo una fila canonica con elegibilidad derivada; `status=ready` por si solo no afirma usabilidad actual. La migracion 0019 elimina las filas de desarrollo sin binding porque no puede reconstruir de forma fiable la identidad/template historicos; su downgrade tambien elimina filas generation-aware antes de retirar la columna para que el esquema 0018 no pueda reutilizarlas solo por ID. No se mantiene un adaptador preproduccion.
- `run_events`: eventos HTTP y operativos saneados para depurar monitores/runs.
- `run_event_outbox`: trabajo pendiente creado en la misma transaccion que cada `run_events` asociado a un monitor. Un rollback elimina ambos; un commit hace ambos visibles. La migracion 0017 incorpora tambien eventos historicos confirmados que aun no tienen publicacion.
- `run_event_publications`: cursor SSE global y monotono asignado una sola vez a cada evento confirmado. El publicador serializado inserta publicaciones y elimina sus filas outbox en una unica transaccion, por lo que un fallo no puede perder el pendiente ni producir dos cursores.
- `action_requests`: acciones solicitadas por usuario.
- `action_executions`: resultado de acciones autenticadas futuras.
- `checkout_snapshots`: opciones de envio/pago futuras.
- `errors`: errores auditables.

Estado runtime no relacional:

- Redis mantiene la cola fiable ready/processing-por-consumidor/dead-letter, marcadores directo e inverso de la tarea pendiente por monitor, el cache obligatorio de vistos/procesamiento con ownership y la cola diferida de reintentos de detalle por monitor y politica de evaluacion. Si Redis no esta disponible, el monitor no procesa candidatos y el run no se confirma.
- `items.photos` conserva todas las URL publicas firmadas observadas; `availability_flags` conserva senales independientes, `state`, `reason_codes` y `source=public_snapshot`; los precios de proteccion, total sin envio y envio minimo usan las columnas de detalle existentes. `favorite_count` y el `view_count` nullable son snapshots del mismo catalogo; las visitas ausentes o invalidas permanecen null y no generan otra peticion.
- Los candidatos descartados por filtros no se persisten como items; quedan reflejados solo en contadores agregados del run.
- Redis guarda en cada tarea proxy solo `proxy_profile_id` y el token opaco de contador+HMAC capturado; nunca endpoint, username, password, template ni contexto. PostgreSQL vuelve a resolver el perfil bajo el advisory fence compartido antes de proveedor y es la autoridad para elegibilidad.

El stream no descubre pendientes mediante un anti-join repetido sobre todo `run_events`: consume lotes indexados de `run_event_outbox`. Una conexion sin cursor toma una instantanea PostgreSQL repetible mientras mantiene el lock global de publicacion, drena solo los pendientes visibles en esa instantanea y empieza en su maximo `run_event_publications.position`. Incluso un evento con ID menor reservado antes pero confirmado despues queda fuera de ese cursor y recibe una posicion posterior. Los polls normales prueban el lock sin esperar para no bloquear heartbeat o deteccion de desconexion. Borrar un evento elimina por FK tanto su pendiente como su publicacion.
