"""AI Worker process wrapper around the Celery worker CLI."""

from __future__ import annotations

import os
import sys


def main() -> None:
    # Import settings only after env is available in the container.
    from .settings import AiWorkerSettings

    settings = AiWorkerSettings()  # type: ignore[call-arg]
    concurrency = str(settings.celery_concurrency)
    os.execvp(
        sys.executable,
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "ai_worker.celery_app",
            "worker",
            "-Q",
            "ai_tasks",
            f"--concurrency={concurrency}",
        ],
    )


if __name__ == "__main__":
    main()
