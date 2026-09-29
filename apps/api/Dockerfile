# Контекст сборки — корень репозитория (нужны libs/*): docker compose build api
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
COPY libs/engine libs/engine
COPY libs/db libs/db
COPY apps/api apps/api

WORKDIR /app/apps/api
RUN uv sync --frozen --no-dev
ENV PATH="/app/apps/api/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "trader_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
