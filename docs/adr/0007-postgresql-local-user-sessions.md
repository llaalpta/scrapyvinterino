# ADR 0007 - Sesiones locales opacas en PostgreSQL

## Estado

Aceptada para 14.12.1.

## Contexto

La PWA privada necesita revocacion inmediata, supervivencia a reinicios de API y cierre de SSE tras logout. Ya existe `users` en PostgreSQL. Redis posee colas/cache operativos y puede perder disponibilidad de forma independiente; un JWT autocontenido seguiria siendo valido hasta expirar y complicaria la revocacion.

## Decision

- Usar un token aleatorio opaco de 256 bits en una cookie host-only `HttpOnly`.
- Guardar solo SHA-256 del token en `user_sessions`, con usuario, expiracion absoluta y revocacion.
- Usar una sesion preautenticada corta para ligar CSRF al login; el login revoca esa identidad y crea otra autenticada.
- Derivar CSRF con HMAC de `APP_SECRET_KEY` y el token, sin persistirlo ni guardarlo en almacenamiento web.
- Revalidar PostgreSQL al admitir cada request y durante SSE. Un fallo PostgreSQL es indisponibilidad fail-closed.
- Guardar passwords nuevos con Argon2 mediante `pwdlib`; retirar `passlib`/bcrypt porque no hay contrato legacy de login que conservar.

## Consecuencias

- Logout, desactivacion y expiracion son observables por todos los procesos y sobreviven reinicios.
- La base de datos participa en cada admision y en polls SSE; la tarea 14.19 podra mejorar readiness, pero nunca introducir un cache autenticado como fallback.
- `APP_SECRET_KEY` liga CSRF y ya protege otros secretos. Su sentinel global sigue perteneciendo a 14.12.6.
- No hay autenticacion si PostgreSQL no esta disponible, aunque `/health` de liveness pueda seguir respondiendo.

## Alternativas descartadas

- JWT: revocacion tardia o una segunda fuente de verdad.
- Redis: mezcla acceso de usuario con cache/colas y pierde la autoridad durable requerida.
- Basic auth o secreto compartido: no ofrece logout servidor, CSRF ni identidad de usuario.
- Cookie firmada con todo el estado: conserva el mismo problema de revocacion que JWT.
