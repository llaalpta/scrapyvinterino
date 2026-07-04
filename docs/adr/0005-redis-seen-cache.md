# ADR 0005: Redis obligatorio para vistos de monitor

## Status

Accepted

## Context

El objetivo principal del producto es detectar oportunidades antes que otros compradores. Persistir todos los items encontrados en PostgreSQL como tabla de resultados ralentiza el camino caliente y mezcla auditoria con producto. Los resultados utiles para el usuario son las oportunidades.

## Decision

Redis es obligatorio para el estado runtime de vistos y procesamiento por monitor. Cada monitor comprueba Redis antes de pedir detalle o aplicar filtros. Si Redis no esta disponible, el run falla y el monitor no procesa candidatos.

PostgreSQL mantiene la verdad duradera de monitores, runs, items que llegaron a oportunidad, oportunidades, proxys, filtros y errores. Los candidatos descartados no se persisten como items.

## Consequences

- El camino caliente evita escrituras en PostgreSQL para candidatos repetidos o descartados.
- Un reinicio o vaciado de Redis puede provocar reprocesamiento, pero no corrompe oportunidades duraderas.
- La infraestructura local y futura produccion deben incluir Redis.
- La UI deja de exponer una tabla de todos los vistos y se centra en oportunidades.
