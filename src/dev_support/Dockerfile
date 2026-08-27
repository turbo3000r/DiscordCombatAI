FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --extra dev-support \
    && sed -i 's/\r$//' /app/src/dev_support/entrypoint.sh \
    && cp /app/src/dev_support/entrypoint.sh /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh \
    && mkdir -p /data \
    && chown 65532:65532 /data

USER 65532:65532

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
