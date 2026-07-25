"""Web authentication: Entra JWT (prod) or local-admin header (dev)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKClient

from shared.runtime.settings import RuntimeMode
from web.backend.settings import WebSettings

logger = logging.getLogger(__name__)

LOCAL_ADMIN_HEADER = "X-DCA-Local-Admin-Oid"


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    oid: str
    upn: str | None
    groups: tuple[str, ...] = ()


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class EntraTokenVerifier:
    def __init__(self, settings: WebSettings) -> None:
        self._settings = settings
        tenant = settings.web_entra_tenant_id or ""
        self._issuer = (
            settings.web_entra_authority
            or f"https://login.microsoftonline.com/{tenant}/v2.0"
        )
        if not self._issuer.endswith("/v2.0"):
            self._issuer = self._issuer.rstrip("/") + "/v2.0"
        jwks_url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
        self._jwks = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
        self._http = httpx.AsyncClient(timeout=5.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def verify(self, token: str) -> AdminPrincipal:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except Exception:
            try:
                self._jwks = PyJWKClient(
                    f"https://login.microsoftonline.com/"
                    f"{self._settings.web_entra_tenant_id}/discovery/v2.0/keys",
                    cache_keys=True,
                    lifespan=3600,
                )
                signing_key = self._jwks.get_signing_key_from_jwt(token)
            except Exception as exc:  # noqa: BLE001
                raise AuthError(401, "invalid token") from exc
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.web_entra_api_audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "aud", "iss", "oid"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(401, "invalid token") from exc

        groups = claims.get("groups")
        if groups is None or "_claim_names" in claims or "_claim_sources" in claims:
            raise AuthError(403, "admin group claim missing or overage")
        if not isinstance(groups, list):
            raise AuthError(403, "admin group claim missing or overage")
        admin_group = self._settings.web_entra_admin_group_id
        if admin_group not in groups:
            raise AuthError(403, "forbidden")
        oid = str(claims.get("oid") or "")
        if not oid:
            raise AuthError(401, "invalid token")
        upn = claims.get("preferred_username") or claims.get("upn") or claims.get("email")
        return AdminPrincipal(
            oid=oid,
            upn=str(upn) if upn else None,
            groups=tuple(map(str, groups)),
        )


class AuthDependency:
    def __init__(self, *, runtime_mode: RuntimeMode, settings: WebSettings) -> None:
        self.runtime_mode = runtime_mode
        self.settings = settings
        self._entra: EntraTokenVerifier | None = None
        if runtime_mode is RuntimeMode.production:
            self._entra = EntraTokenVerifier(settings)

    async def aclose(self) -> None:
        if self._entra is not None:
            await self._entra.aclose()

    async def authenticate(
        self, authorization: str | None, local_oid: str | None
    ) -> AdminPrincipal:
        if self.runtime_mode is RuntimeMode.development:
            expected = self.settings.web_local_admin_oid
            if not local_oid or local_oid != expected:
                raise AuthError(401, "local admin required")
            return AdminPrincipal(oid=local_oid, upn="local-dev-admin@localhost")
        if local_oid:
            raise AuthError(401, "local admin header forbidden")
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthError(401, "bearer token required")
        token = authorization.split(" ", 1)[1].strip()
        assert self._entra is not None
        return await self._entra.verify(token)


__all__ = [
    "LOCAL_ADMIN_HEADER",
    "AdminPrincipal",
    "AuthDependency",
    "AuthError",
    "EntraTokenVerifier",
]
