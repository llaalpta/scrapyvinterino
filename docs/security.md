# Seguridad

- Secretos solo en `.env` o almacen local cifrado futuro.
- No commitear cookies, tokens, direcciones completas ni datos de pago.
- Login local obligatorio para la web.
- Cookies de sesion `HttpOnly`; `Secure` en produccion.
- Redaccion automatica de datos sensibles en logs.
- Mensajes de error persistidos deben pasar por redaccion antes de guardarse en `runs`, `errors` o campos de error de entidades.
- Proxies residenciales son opcionales; credenciales solo en `.env` o almacenamiento local ignorado.
- No persistir cookies anonimas de Vinted, tokens publicos, credenciales de proxy, HTML ni payloads raw en cache, DB, logs o respuestas API.
- No implementar captcha solving ni bypass agresivo anti-bot.
- Acciones de compra futuras:
  - requeriran click explicito;
  - validaran precio, moneda y disponibilidad;
  - registraran auditoria redacted.
