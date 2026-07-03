# Modelo de Datos

Tablas principales:

- `users`: acceso local.
- `search_sources`: monitores de oportunidad reutilizables; guardan URL, modo, cadencia, filtros/proxy opcionales y estado runtime. `archived_at` oculta un monitor sin borrar historico.
- `app_settings`: configuracion global mutable desde la PWA, como el estado UI del scheduler.
- `filter_rules`: filtros excluyentes nombrados y opcionales; determinan oportunidades, no la identidad del monitor.
- `monitor_sessions`: historico legacy de sesiones; el flujo principal usa monitores directamente.
- `session_item_state`: estado legacy de items evaluados por una sesion.
- `runs`: ejecuciones de monitor, con `trigger`, contadores de filtrado y metadatos runtime.
- `items`: articulos normalizados de Vinted; `vinted_item_id` define identidad de catalogo/cache.
- `source_seen_items`: trazabilidad de que monitor vio cada articulo; decide si el articulo es nuevo para ese monitor.
- `opportunities`: articulos vistos por un monitor que no fueron descartados; la unicidad notificable principal del flujo nuevo es por monitor e item.
- `proxy_profiles`: proxys configurables desde UI con secretos cifrados.
- `run_events`: eventos HTTP y operativos saneados para depurar monitores/runs.
- `action_requests`: acciones solicitadas por usuario.
- `action_executions`: resultado de acciones autenticadas futuras.
- `checkout_snapshots`: opciones de envio/pago futuras.
- `errors`: errores auditables.
