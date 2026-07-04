# Seguridad

- Secretos solo en `.env` o almacen local cifrado futuro.
- No commitear cookies, tokens, direcciones completas ni datos de pago.
- Login local obligatorio para la web.
- Cookies de sesion `HttpOnly`; `Secure` en produccion.
- Redaccion automatica de datos sensibles en logs.
- Mensajes de error persistidos deben pasar por redaccion antes de guardarse en `runs`, `errors` o campos de error de entidades.
- Proxies residenciales son opcionales; credenciales en `.env` o cifradas en `proxy_profiles` con clave local.
- No devolver ni registrar cookies anonimas de Vinted, tokens, credenciales de proxy, HTML ni payloads raw completos en logs o respuestas API.
- Los eventos de run pueden guardar metodo, fase, nivel, URL saneada, status, duracion, timeout, intento/retry, proxy, IP de salida, user-agent, fingerprints y errores de Vinted redacted/truncados.
- La API nunca devuelve passwords/tokens/cookies/proxy URLs completas con credenciales; solo valores masked o fingerprints.
- Los marcadores seguros de sesion pueden incluir nombre, longitud, mascara parcial y fingerprint corto. Si el valor es corto, la mascara no muestra ningun caracter.
- La redaccion de logs debe aplicarse de forma recursiva a `details`, incluyendo listas y objetos anidados.
- No implementar captcha solving ni bypass agresivo anti-bot.
- Acciones de compra futuras:
  - requeriran click explicito;
  - validaran precio, moneda y disponibilidad;
  - registraran auditoria redacted.
