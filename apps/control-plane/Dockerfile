FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS api

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY backend ./backend

RUN uv sync --frozen --no-dev

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn onestep_control_plane_api.main:app --app-dir backend/src --host 0.0.0.0 --port 8000"]


FROM node:22-alpine AS frontend-build

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json

RUN corepack enable
RUN pnpm install --frozen-lockfile

COPY frontend ./frontend

RUN pnpm ui:build


FROM nginx:1.27-alpine AS frontend

ENV ONESTEP_CP_UI_API_BASE_URL=/

COPY docker/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY docker/nginx/write-runtime-config.sh /docker-entrypoint.d/40-write-runtime-config.sh
COPY --from=frontend-build /app/frontend/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
