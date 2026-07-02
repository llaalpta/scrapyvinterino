# Especificacion

## Objetivo

Construir una aplicacion personal para monitorizar catalogos publicos de Vinted, detectar oportunidades nuevas y operar sobre ellas desde una web app privada.

## MVP

- Login local.
- Gestion de URLs de busqueda.
- Ejecucion manual de busquedas.
- Persistencia de articulos, ejecuciones y errores.
- Deteccion de nuevos articulos por fuente.
- Reglas de filtrado propias.
- Tabla de oportunidades en la web.
- Docker Compose local sin Traefik.

## Roadmap y specs de feature

- Roadmap canonico: `docs/roadmap.md`.
- Specs concretas: `docs/specs/`.
- Regla: el roadmap decide el orden; cada spec define que significa terminado.

Specs MVP iniciales:

- `docs/specs/001-search-sources.md`
- `docs/specs/002-vinted-catalog-research.md`
- `docs/specs/003-manual-run.md`
- `docs/specs/004-item-persistence.md`
- `docs/specs/005-deduplication-and-opportunities.md`
- `docs/specs/006-local-filters.md`
- `docs/specs/007-opportunities-table.md`

## Futuro

- Scheduler configurable.
- Favoritos autenticados.
- Descubrimiento de checkout: envio, pago, domicilio y puntos pickup.
- Precompra y compra explicita desde UI.
- Notificaciones PWA, Telegram, webhook, Discord o email.

## Limites

- El MVP no usa login de Vinted.
- Las compras futuras requeriran accion explicita del usuario.
- No se guardaran secretos en el repositorio.
