# Despliegue

Produccion se preparara en una fase posterior con `docker-compose.prod.yml`.

## Objetivo

- Desplegar detras de Traefik y Cloudflare.
- No exponer Postgres.
- Servir frontend en `/`.
- Servir API en `/api`.
- Soportar SSE o WebSocket para eventos en vivo.

## Desarrollo vs Produccion

Desarrollo usa puertos locales directos. Produccion usara labels de Traefik y red externa compartida con el proxy.
