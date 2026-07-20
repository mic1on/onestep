FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS api-base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY backend ./backend
COPY scripts ./scripts

RUN uv sync --frozen --no-dev


FROM public.ecr.aws/docker/library/node:22-alpine AS frontend-build

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json

RUN corepack enable
RUN pnpm install --frozen-lockfile

COPY frontend ./frontend

RUN pnpm ui:build


FROM api-base AS plane

ENV ONESTEP_CP_UI_DIST_DIR=/app/frontend/dist \
    ONESTEP_CP_UI_API_BASE_URL=/

COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

EXPOSE 8000

CMD ["uvicorn", "onestep_control_plane_api.main:app", "--app-dir", "backend/src", "--host", "0.0.0.0", "--port", "8000"]
