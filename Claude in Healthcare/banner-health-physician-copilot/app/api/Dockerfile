FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN uv pip install --system --no-cache .

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "banner_copilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
