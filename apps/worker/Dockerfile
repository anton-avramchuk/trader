# Контекст сборки — корень репозитория (нужны libs/*): docker compose build worker
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
COPY libs/engine libs/engine
COPY libs/db libs/db
COPY libs/providers libs/providers
COPY apps/worker apps/worker

WORKDIR /app/apps/worker
RUN uv sync --frozen --no-dev
ENV PATH="/app/apps/worker/.venv/bin:$PATH"

CMD ["python", "-m", "trader_worker"]
