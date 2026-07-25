"""FastAPI Web application factory."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from shared.domain.suggestion_response import SuggestionResponseService
from shared.runtime.guards import assert_service_runtime
from shared.runtime.settings import RuntimeMode, load_runtime_settings
from shared.storage import build_repositories
from web.backend.auth import AuthDependency
from web.backend.routes.suggestions import router as suggestions_router
from web.backend.settings import load_web_settings

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
_RUNTIME_SCRIPT_RE = re.compile(
    r"<script>\s*window\.__DCA_RUNTIME__\s*=.*?</script>",
    re.DOTALL,
)


def _runtime_payload(runtime_mode: RuntimeMode, settings: Any) -> dict[str, Any]:
    return {
        "mode": runtime_mode.value,
        "localAdminOid": settings.web_local_admin_oid
        if runtime_mode is RuntimeMode.development
        else None,
        "entra": None
        if runtime_mode is RuntimeMode.development
        else {
            "tenantId": settings.web_entra_tenant_id,
            "clientId": settings.web_entra_client_id,
            "audience": settings.web_entra_api_audience,
            "authority": settings.web_entra_authority
            or f"https://login.microsoftonline.com/"
            f"{settings.web_entra_tenant_id}",
        },
    }


def _runtime_script(runtime_mode: RuntimeMode, settings: Any) -> str:
    payload = json.dumps(_runtime_payload(runtime_mode, settings))
    return f"<script>window.__DCA_RUNTIME__={payload};</script>"


def _spa_html(runtime_mode: RuntimeMode, settings: Any) -> str:
    script = _runtime_script(runtime_mode, settings)
    index_file = FRONTEND_DIST / "index.html"
    if index_file.is_file():
        html = index_file.read_text(encoding="utf-8")
        if _RUNTIME_SCRIPT_RE.search(html):
            return _RUNTIME_SCRIPT_RE.sub(script, html, count=1)
        if "</head>" in html:
            return html.replace("</head>", f"{script}</head>", 1)
        return script + html
    return (
        "<!doctype html><html><head><meta charset='utf-8'/>"
        f"{script}</head><body><div id='root'></div></body></html>"
    )


def create_app() -> FastAPI:
    runtime = load_runtime_settings()
    assert_service_runtime(runtime, "web")
    settings = load_web_settings(runtime)
    repos = build_repositories("web", runtime)
    auth = AuthDependency(runtime_mode=runtime.mode, settings=settings)
    service = SuggestionResponseService(repos.suggestion, runtime_mode=runtime.mode)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        await repos.status.ensure_seeded()
        yield
        await auth.aclose()

    app = FastAPI(title="DiscordCombatAI Web", lifespan=lifespan)
    app.state.runtime = runtime
    app.state.settings = settings
    app.state.auth = auth
    app.state.suggestion_response_service = service
    app.state.repos = repos
    app.include_router(suggestions_router)

    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(_spa_html(runtime.mode, settings))

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> Any:
        if full_path.startswith("api/"):
            return HTMLResponse("Not Found", status_code=404)
        candidate = FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return HTMLResponse(_spa_html(runtime.mode, settings))

    return app


__all__ = ["create_app"]
