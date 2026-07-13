# Riesgos

## Tecnicos

- Vinted puede cambiar HTML, payloads o endpoints internos.
- Puede haber rate limits, bloqueos o captchas.
- Las acciones autenticadas seran mas fragiles que el scraping publico.

## Seguridad

- Cookies, tokens, direccion y datos de pago requieren redaccion y almacenamiento seguro.
- La compra futura debe tener confirmacion explicita y limites.

## Mantenimiento

- Aislar Vinted en adaptadores para minimizar cambios.
- Guardar fixtures sanitizados para tests.
- Versionar reglas y modelos.

## Operacion de servicios

- `/health` de API y el health del worker son señales parciales: no demuestran salud conjunta de PostgreSQL, Redis, colas y consumidores. Redis puede estar caido mientras ambos siguen verdes.
- Solo worker y watchdog tienen `restart`; watchdog no tiene healthcheck y no existe un contrato Compose comun de propagacion de señales, gracia o drain coordinado. El mapa y runbook vigentes viven en `docs/architecture.md` y `docs/deployment.md`.
- Los volumenes locales PostgreSQL/Redis contienen estado operativo; `docker compose down -v` es una perdida destructiva, no mantenimiento rutinario.
- Los diagnosticos manuales que contactan Vinted/proxies no son entrypoints de servicio. `scripts/check_datadome.py` y `scripts/inspect_vinted_session.py` requieren una tarea posterior de redaccion/output seguro antes de tratarlos como herramientas operativas generales; no deben ejecutarse ni generar artefactos versionables sin un presupuesto externo explicito.
