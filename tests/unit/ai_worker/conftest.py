from __future__ import annotations

import os

# Ensure AI Worker settings can load when tests import celery_app/tasks.
# Do not force transport_shell here — individual tests set it explicitly.
os.environ.setdefault("APPLICATION_VERSION", "v0.1.0")
os.environ.setdefault("AI_WORKER_NODE_ID", "node-local")
os.environ.setdefault("AI_WORKER_RABBITMQ_USER", "discordcombatai")
os.environ.setdefault("AI_WORKER_RABBITMQ_PASS", "change-me-in-env")
