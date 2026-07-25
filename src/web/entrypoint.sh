#!/bin/sh
set -eu
exec uv run uvicorn web.backend.main:create_app --factory --host 0.0.0.0 --port "${WEB_PORT:-8080}"
