# Desarrollo Local

El entorno local no depende de Traefik ni del servidor remoto.

## Requisitos

- Python 3.12.
- Node 22.
- pnpm.
- Docker Desktop con Compose.

## Arranque

```powershell
copy .env.example .env
docker compose up --build
```

## Puertos

- `5173`: frontend Vite.
- `8000`: API FastAPI.
- `5432`: Postgres local.

## Notas Windows

Si `python` no aparece tras instalarlo, abrir una nueva terminal o usar la ruta real de instalacion en `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
