# Modelo de Datos

Tablas principales:

- `users`: acceso local.
- `search_sources`: monitores de oportunidad reutilizables; guardan URL, modo, cadencia, filtros opcionales y estado runtime. `archived_at` oculta un monitor sin borrar historico.
- `app_settings`: configuracion global mutable desde la PWA, como el estado UI del scheduler.
- `filter_rules`: filtros excluyentes nombrados y opcionales; determinan oportunidades, no la identidad del monitor.
- `monitor_sessions`: periodos historicos de lanzamiento de un monitor; los puntuales se cierran al terminar y los recurrentes quedan abiertos hasta parada, expiracion o fallo.
- `runs`: ejecuciones de monitor, con `trigger`, `monitor_session_id`, contadores de filtrado y metadatos runtime.
- `items`: articulos normalizados de Vinted que llegaron a oportunidad; `vinted_item_id` define identidad de catalogo/cache.
- `opportunities`: articulos vistos por un monitor que no fueron descartados; es la tabla principal de resultados utiles del producto y su unicidad notificable es por monitor e item.
- `proxy_profiles`: pool global de proxys configurables desde UI con secretos cifrados, tipo, capacidad y estado operativo.
- `run_events`: eventos HTTP y operativos saneados para depurar monitores/runs.
- `action_requests`: acciones solicitadas por usuario.
- `action_executions`: resultado de acciones autenticadas futuras.
- `checkout_snapshots`: opciones de envio/pago futuras.
- `errors`: errores auditables.

Estado runtime no relacional:

- Redis mantiene el cache obligatorio de vistos/procesamiento por monitor y politica de evaluacion. Si Redis no esta disponible, el monitor no procesa candidatos y el run falla.
- Los candidatos descartados por filtros no se persisten como items; quedan reflejados solo en contadores agregados del run.
