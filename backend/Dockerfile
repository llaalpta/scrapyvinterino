FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY src ./src
COPY tests ./tests
RUN pip install --no-cache-dir -e ".[dev]"

ENV PYTHONPATH=/app/src

CMD ["uvicorn", "vinted_monitor.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
