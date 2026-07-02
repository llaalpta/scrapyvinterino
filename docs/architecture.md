# Arquitectura

## Servicios

- `frontend`: PWA React/Vite para configuracion y operacion.
- `api`: FastAPI para REST, login local y eventos.
- `worker`: scraping, deduplicacion, filtros, scheduler y acciones pendientes.
- `postgres`: persistencia.

## Modulos backend

- `api`: endpoints HTTP.
- `core`: configuracion y logging.
- `db`: modelos, sesiones y migraciones.
- `services`: logica de fuentes, items, filtros y acciones.
- `worker`: procesos periodicos y ejecuciones manuales.

## Flujo MVP

1. El usuario crea una fuente con una URL de Vinted.
2. La API guarda la fuente.
3. El worker ejecuta la fuente manualmente o por scheduler.
4. El proveedor Vinted obtiene datos publicos por HTTP directo.
5. La aplicacion normaliza y guarda articulos.
6. Se detectan nuevos articulos por fuente.
7. Se aplican filtros propios.
8. La PWA muestra oportunidades y estado de ejecucion.
