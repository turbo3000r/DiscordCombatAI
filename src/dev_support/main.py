"""dev-support FastAPI entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from shared.runtime.guards import assert_service_runtime
from shared.runtime.settings import RuntimeMode, load_runtime_settings

from .api import router
from .grants import GrantPublisher
from .settings import DevSupportSettings
from .store import connect
from .store.repositories import DevStore

logger = logging.getLogger(__name__)


def create_app(
    settings: DevSupportSettings | None = None,
    *,
    enable_grants: bool = True,
) -> FastAPI:
    settings = settings or DevSupportSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = load_runtime_settings()
        if runtime.mode is not RuntimeMode.development:
            raise SystemExit("dev-support refuses to start unless DCA_RUNTIME_MODE=development")
        assert_service_runtime(runtime, "dev_support")

        conn = connect(settings.db_path)
        store = DevStore(conn)
        store.ensure_status_seeded()
        app.state.store = store
        app.state.settings = settings

        publisher: GrantPublisher | None = None
        if enable_grants:
            publisher = GrantPublisher(
                host=settings.mosquitto_host,
                port=settings.mosquitto_port,
                node_id=settings.node_id,
                ttl_sec=settings.grant_ttl_sec,
                renew_sec=settings.grant_renew_sec,
            )
            await publisher.start()
            app.state.grants = publisher
        try:
            yield
        finally:
            if publisher is not None:
                await publisher.stop()
            conn.close()

    app = FastAPI(title="DiscordCombatAI dev-support", lifespan=lifespan)
    app.include_router(router)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = DevSupportSettings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()


__all__ = ["create_app", "main"]
