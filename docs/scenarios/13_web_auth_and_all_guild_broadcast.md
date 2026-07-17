# S13 — Web authentication and all-guild broadcast

**Id:** `S13`  
**Canonical:** `contracts/web_auth.md`; `web/pages/webhook.md`; `contracts/guild_config.md` (webhook URL storage); `containers/web/components.md` (ConfirmDialog)

## Preconditions

- Web is reachable (may be internet-facing).
- Entra single-tenant app emits security group claims; admin group id = `WEB_ENTRA_ADMIN_GROUP_ID`.
- At least one guild has a Discord webhook URL stored via `/config` (allowlisted host).

## Ordered steps

### Auth boundary

1. Unauthenticated `GET/POST /api/*` → **401**.
2. Authenticated user whose token lacks admin group (or overage without `groups`) → **403**; static SPA shell may still load.
3. Admin completes MSAL.js Authorization Code + PKCE; browser sends `Authorization: Bearer` on API calls.
4. FastAPI validates issuer, audience, JWKS, `tid`, expiry, then group membership (`web_auth.md` §5).

### ALL-guild broadcast

1. Admin opens Webhook page, chooses destination `ALL`, passes UI `ConfirmDialog` (`web_auth.md` §7).
2. `POST /api/webhook/send` (or update) includes `Idempotency-Key` (UUID). Web re-validates webhook URLs (Discord-host HTTPS allowlist, no redirects) before POST.
3. Successful ALL broadcast starts per-`oid` cooldown `WEB_WEBHOOK_ALL_COOLDOWN_SEC` (default 60). Replay of the same idempotency key returns prior result **without** re-POSTing Discord.
4. Durable audit object written under `admin-audit` Blob container with actor `oid` / upn fields.
5. List/detail APIs never return raw `webhook_url` / secrets.

## Durable writes

- Blob admin-audit entry for the broadcast.
- No change to leadership / Bot grants (Web is Azure-standalone from the compose cluster).

## Timeouts

- Discord POST timeout/retry detail remains P1.6; scenario requires allowlist + idempotency + cooldown regardless.
- PubSub negotiate (if used on other pages) uses the same Bearer (`pubsub_live.md` §4) — out of scope for broadcast success but same auth boundary.

## User-visible result

- Non-admin: 401/403 UX per `web_auth.md` §4.
- Admin: confirmation required for ALL; success/error summary without leaking secrets; Discord guilds receive webhook content when POSTs succeed.
- Second ALL within cooldown rejected/limited per policy.

## Invariant checked

**Entra + admin group is the security boundary** (not private network). ALL broadcasts require confirm + idempotency + cooldown + SSRF allowlist + durable audit — no anonymous or non-admin broadcast path.
