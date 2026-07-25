#!/bin/sh
set -eu
exec uv run --no-sync python -m bot.main
