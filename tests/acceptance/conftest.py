from __future__ import annotations

import os

os.environ.setdefault("APPLICATION_VERSION", "v0.1.0")
os.environ.setdefault("AI_WORKER_NODE_ID", "node-local")
os.environ.setdefault("AI_WORKER_RABBITMQ_USER", "discordcombatai")
os.environ.setdefault("AI_WORKER_RABBITMQ_PASS", "change-me-in-env")
