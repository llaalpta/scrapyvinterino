# Despliegue

Produccion se preparara en una fase posterior con `docker-compose.prod.yml`.

## Objetivo

- Desplegar detras de Traefik y Cloudflare.
- No exponer Postgres.
- Servir frontend en `/`.
- Servir API en `/api`.
- Soportar SSE o WebSocket para eventos en vivo.

## Desarrollo vs Produccion

Desarrollo usa puertos locales directos. Produccion usara labels de Traefik y red externa compartida con el reverse proxy.

## Outbound Vinted Proxy

El monitor debe funcionar sin proxy de salida por defecto cuando el ajuste global de acceso directo lo permite. El uso de proxy residencial o de otro proveedor es optativo y se configura en el pool global de proxys gestionado por la PWA; las credenciales se almacenan cifradas en base de datos.

La politica de secretos, redaccion y limites anti-bot vive en `docs/security.md`; la spec runtime vive en `docs/specs/008-scheduler.md`.
