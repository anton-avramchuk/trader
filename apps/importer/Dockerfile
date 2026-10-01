# Контекст сборки — корень репозитория: docker compose build importer
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
COPY apps/importer apps/importer

WORKDIR /app/apps/importer
RUN uv sync --frozen --no-dev
ENV PATH="/app/apps/importer/.venv/bin:$PATH"

EXPOSE 8100
CMD ["uvicorn", "trader_importer.main:app", "--host", "0.0.0.0", "--port", "8100"]
