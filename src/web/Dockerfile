FROM node:20-bookworm-slim AS frontend
WORKDIR /frontend
COPY src/web/frontend/package.json src/web/frontend/package-lock.json ./
RUN npm ci
COPY src/web/frontend/ ./
RUN npm test -- --run && npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY --from=frontend /frontend/dist /app/src/web/frontend/dist
RUN uv sync --frozen --no-dev --extra web \
    && chmod +x /app/src/web/entrypoint.sh
USER 65532:65532
ENTRYPOINT ["/app/src/web/entrypoint.sh"]
