# Modelo de Datos

Tablas principales:

- `users`: acceso local.
- `search_sources`: URLs de busqueda y configuracion base.
- `app_settings`: configuracion global mutable desde la PWA, como el estado UI del scheduler.
- `filter_rules`: filtros excluyentes nombrados que se snapshottean al lanzar sesiones.
- `monitor_sessions`: contexto operativo de una fuente, filtros, cadencia, proxy y metadatos runtime.
- `session_item_state`: estado minimo de cada item evaluado por una sesion para no repetir detalle/filtros.
- `runs`: ejecuciones de fuente o sesion, con `trigger`, contadores de filtrado y metadatos runtime.
- `items`: articulos normalizados de Vinted; `vinted_item_id` define identidad global y si un item ya fue detectado.
- `source_seen_items`: trazabilidad de que fuente vio cada articulo; no decide si el articulo es globalmente nuevo.
- `opportunities`: articulos vistos por una sesion que no fueron descartados; la unicidad notificable principal es por `session_id` y `item_id`.
- `proxy_profiles`: proxys configurables desde UI con secretos cifrados.
- `run_events`: eventos HTTP y operativos saneados para depurar sesiones/runs.
- `action_requests`: acciones solicitadas por usuario.
- `action_executions`: resultado de acciones autenticadas futuras.
- `checkout_snapshots`: opciones de envio/pago futuras.
- `errors`: errores auditables.
