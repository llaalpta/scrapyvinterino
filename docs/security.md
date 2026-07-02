# Seguridad

- Secretos solo en `.env` o almacen local cifrado futuro.
- No commitear cookies, tokens, direcciones completas ni datos de pago.
- Login local obligatorio para la web.
- Cookies de sesion `HttpOnly`; `Secure` en produccion.
- Redaccion automatica de datos sensibles en logs.
- Acciones de compra futuras:
  - requeriran click explicito;
  - validaran precio, moneda y disponibilidad;
  - registraran auditoria redacted.
