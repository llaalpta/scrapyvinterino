# Modelo de Datos

Tablas principales:

- `users`: acceso local.
- `search_sources`: URLs de busqueda y configuracion.
- `filter_rules`: filtros propios por fuente.
- `runs`: ejecuciones.
- `items`: articulos normalizados de Vinted; `vinted_item_id` define identidad global y si un item ya fue detectado.
- `source_seen_items`: trazabilidad de que fuente vio cada articulo; no decide si el articulo es globalmente nuevo.
- `opportunities`: articulos globalmente nuevos que pasan filtros propios; la unicidad notificable es por `item_id` y `rule_id`, sin duplicarse entre fuentes solapadas.
- `action_requests`: acciones solicitadas por usuario.
- `action_executions`: resultado de acciones autenticadas futuras.
- `checkout_snapshots`: opciones de envio/pago futuras.
- `errors`: errores auditables.
