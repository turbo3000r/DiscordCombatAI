FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    AI_WORKER_TRANSPORT_SHELL=false

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --extra ai-worker \
    && chmod +x /app/src/ai_worker/entrypoint.sh

USER 65532:65532

ENTRYPOINT ["/app/src/ai_worker/entrypoint.sh"]
