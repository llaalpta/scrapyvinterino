# ADR 0006: curl_cffi para bypass de DataDome TLS fingerprint

## Status

Accepted

## Context

Vinted usa DataDome como WAF, que analiza la huella TLS/JA3 del ClientHello, el frame SETTINGS de HTTP/2, el orden de headers HTTP, y el User-Agent para detectar clientes no-browser. La libreria `httpx` produce una huella TLS de Python/Go que DataDome identifica y bloquea con challenges o 403.

El MVP usaba `httpx` porque DataDome no estaba bloqueando activamente al principio. A medida que la frecuencia de monitoreo aumenta, los bloqueos se vuelven inevitables.

## Decision

Migrar todo el trafico HTTP a `curl_cffi` con `impersonate` para falsificar la huella TLS/JA3 y HTTP/2 de un navegador real. Se elimina `httpx` como dependencia.

Adicionalmente:
- Cada sesion usa un perfil de navegador coherente (impersonate + User-Agent + Sec-Ch-Ua alineados).
- Se implementa deteccion de challenges de DataDome para descartar IPs comprometidas.
- Los proxies residenciales usan sesiones sticky con UUID dinamico por tarea.
- Se aplica timing humano entre requests para evitar deteccion por cadencia.

## Consequences

- `httpx` se elimina del proyecto. Los tests que usen `httpx.MockTransport` deben migrarse.
- `curl_cffi` requiere `libcurl` nativo; el Dockerfile necesita dependencias del sistema.
- La version de `impersonate` debe actualizarse periodicamente cuando Chrome sube de version.
- El pool de perfiles de navegador es un dato estatico que debe mantenerse coherente con las versiones de `curl_cffi`.
- El codigo de parsing (funciones puras) no cambia; solo la capa de transporte HTTP.
