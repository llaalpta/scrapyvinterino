# Modelo de Datos

Tablas principales:

- `users`: acceso local.
- `search_sources`: monitores de oportunidad reutilizables; guardan URL, modo, cadencia, `filter_definition` con terminos excluyentes propios del monitor y estado runtime. `archived_at` oculta un monitor sin borrar historico.
- `app_settings`: configuracion global mutable desde la PWA, como el estado UI del scheduler.
- `monitor_sessions`: periodos historicos de lanzamiento de un monitor; los puntuales se cierran al terminar y los recurrentes quedan abiertos hasta parada, expiracion o fallo.
- `runs`: ejecuciones de monitor, con `trigger`, `monitor_session_id`, `task_id` indexado para redelivery idempotente, contadores de filtrado y metadatos runtime.
- `items`: articulos normalizados de Vinted que llegaron a oportunidad; `vinted_item_id` define identidad de catalogo/cache.
- `opportunities`: articulos vistos por un monitor que no fueron descartados; es la tabla principal de resultados utiles del producto y su unicidad notificable es por monitor e item.
- `proxy_profiles`: pool global de proxys configurables desde UI con secretos cifrados, tipo, capacidad y estado operativo.
- `vinted_sessions`: sesiones anonimas publicas de Vinted propiedad de un monitor y asociadas al proxy sticky usado; guardan cookies/tokens cifrados, contexto seguro, contador de uso, expiracion e invalidacion.
- `run_events`: eventos HTTP y operativos saneados para depurar monitores/runs.
- `action_requests`: acciones solicitadas por usuario.
- `action_executions`: resultado de acciones autenticadas futuras.
- `checkout_snapshots`: opciones de envio/pago futuras.
- `errors`: errores auditables.

Estado runtime no relacional:

- Redis mantiene la cola fiable ready/processing-por-consumidor/dead-letter, marcadores directo e inverso de la tarea pendiente por monitor, el cache obligatorio de vistos/procesamiento con ownership y la cola diferida de reintentos de detalle por monitor y politica de evaluacion. Si Redis no esta disponible, el monitor no procesa candidatos y el run no se confirma.
- `items.photos` conserva todas las URL publicas firmadas observadas; `availability_flags` conserva senales independientes, `state`, `reason_codes` y `source=public_snapshot`; los precios de proteccion, total sin envio y envio minimo usan las columnas de detalle existentes. `favorite_count` y el `view_count` nullable son snapshots del mismo catalogo; las visitas ausentes o invalidas permanecen null y no generan otra peticion.
- Los candidatos descartados por filtros no se persisten como items; quedan reflejados solo en contadores agregados del run.
