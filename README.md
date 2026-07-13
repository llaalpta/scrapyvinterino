# scrapyvinterino

Motor personal de monitorizacion de Vinted con backend Python, worker de scraping y web app/PWA privada.

El objetivo inicial es monitorizar URLs publicas de catalogo, detectar articulos nuevos, guardarlos en PostgreSQL, aplicar filtros propios y mostrarlos en una tabla operativa. Las acciones autenticadas como favoritos, precompra y compra manual quedan preparadas a nivel de arquitectura, pero no se implementan en el primer MVP.

## Stack

- Python 3.12, FastAPI, SQLAlchemy, Alembic.
- React, Vite, TypeScript, PWA.
- PostgreSQL.
- Docker Compose para desarrollo local.

## Desarrollo local

```powershell
copy .env.example .env
docker compose up -d --build postgres redis api
docker compose up -d frontend
```

Ese arranque no inicia ejecutores. Un `docker compose up` sin lista incluye tambien worker y watchdog; puede recuperar tareas Redis y generar trafico real. Consulta `docs/development.md` antes de arrancarlos o resetear volumenes.

Servicios actuales:

- Frontend: http://localhost:5173
- API: http://localhost:8000
- API docs: http://localhost:8000/docs
- Postgres: localhost:5432
- Redis: localhost:6379
- Worker: productor recurrente y consumidores, sin puerto HTTP.
- Scheduler watchdog: fail-stop de monitores recurrentes, sin puerto HTTP.

Docker Desktop debe instalarse aparte con permisos de administrador si no esta disponible en la maquina.

## Desarrollo de componentes sin Docker

Estos comandos no forman un stack completo: requieren PostgreSQL/Redis accesibles, una `.env` apta para el host y migraciones aplicadas. Tampoco arrancan worker ni watchdog.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn vinted_monitor.api.main:app --reload
```

```powershell
cd frontend
pnpm install
pnpm dev
```

## Estado

MVP privado en preproduccion con desarrollo SDD. Ver `docs/` para especificacion, arquitectura, despliegue y riesgos vigentes.

## Trabajo con SDD y agentes

Este proyecto mantiene el contexto de trabajo en el repositorio, no solo en conversaciones.

- `AGENTS.md`: instrucciones operativas para Codex/agentes.
- `docs/sdd-process.md`: flujo Spec Driven Development del proyecto.
- `docs/roadmap.md`: orden canonico de trabajo.
- `docs/specs/`: especificaciones concretas por feature.
- `docs/product-decisions.md`: decisiones de producto vigentes.
- `docs/adr/`: decisiones tecnicas y de arquitectura.

La documentacion se revisa, actualiza y mantiene. No se deben crear documentos nuevos que se solapen con otros existentes; antes de crear un archivo nuevo, hay que actualizar el documento canonico que ya posea ese tema.
