# Contract: Web Admin Authentication (Microsoft Entra ID)

> **New this revision — closes P0.7.** Canonical source for Web's administrative security boundary: single-tenant Entra ID SPA (MSAL.js, Authorization Code + PKCE), Bearer JWT validation on FastAPI, admin-group authorization, route policy, secret redaction, webhook SSRF allowlist, audit fields, and broadcast rate/idempotency rules. `containers/web/web.md`, all `web/pages/*.md`, `contracts/pubsub_live.md` §4, and related Bot/config notes point here rather than inventing a second auth story.

---

## 1. Security Boundary (v1)

| | |
|---|---|
| **Audience** | Single-tenant Entra app. Only users in one Entra security group (e.g. `DiscordCombatAI-Admins`) are authorized after login. |
| **Pattern** | MSAL.js in the browser — Authorization Code + PKCE. Frontend attaches `Authorization: Bearer <access_token>` to every `/api/*` call. FastAPI validates the JWT; no cookie session and no BFF for v1. |
| **Deployment** | Web **may** be internet-reachable. **Entra ID + admin group is the security boundary**, not private-network-only. VPN / private network remains optional hardening, not the v1 minimum. |

Unauthenticated API → **401**. Authenticated but not in the admin group → **403**.

---

## 2. Entra App Registration

One **single-tenant** app registration serves both roles:

1. **SPA** — public client; redirect URIs for production Web origin and local/Vite origins (§8).
2. **API** — exposes one application scope used as the access-token audience/scope for admin calls.

| Setting | Convention |
|---|---|
| Supported account types | Single tenant (`WEB_ENTRA_TENANT_ID` only) |
| Application ID URI | `api://{WEB_ENTRA_CLIENT_ID}` |
| Exposed scope | `access_as_admin` — full scope string `api://{WEB_ENTRA_CLIENT_ID}/access_as_admin` |
| Group claims | Configure the app to **emit security group claims** in the access token (`groups` optional claim / “Emit groups as security groups”). |
| Client secret | **None** for the SPA (public client + PKCE). Do not add a confidential-client secret for browser auth. |

**Primary authorization path (v1):** keep the admin group small and authorize by `WEB_ENTRA_ADMIN_GROUP_ID ∈ token.groups`. Treat group-claim overage (`_claim_names` / `_claim_sources`) as an **ops constraint**: keep the group under Entra’s overage limit so `groups` is always present in the token. Graph `memberOf` fallback is **P2** — not required for v1; do not leave authorization undefined if overage occurs (reject with **403** and log that group claims were missing/overage until ops fixes emit-groups or group size).

---

## 3. Environment Variables (Web-owned)

Listed here and mirrored in `containers/web/web.md` §3. These are **not** Azure Service Principal vars (`azure.md` §3) — see §10.

| Variable | Required | Default | Description |
|---|---|---|---|
| `WEB_ENTRA_TENANT_ID` | Yes | — | Entra tenant ID (directory). |
| `WEB_ENTRA_CLIENT_ID` | Yes | — | SPA / app registration client ID (public; not a secret). |
| `WEB_ENTRA_API_AUDIENCE` | Yes | — | Expected JWT `aud` — typically `api://{WEB_ENTRA_CLIENT_ID}` (Application ID URI). |
| `WEB_ENTRA_ADMIN_GROUP_ID` | Yes | — | Object ID of the Entra security group whose members are Web admins. |
| `WEB_ENTRA_AUTHORITY` | No | `https://login.microsoftonline.com/{WEB_ENTRA_TENANT_ID}` | Authority URL; override only if a sovereign/cloud variant is required. |
| `WEB_WEBHOOK_ALL_COOLDOWN_SEC` | No | `60` | Minimum seconds between successful `destination: "ALL"` broadcasts **per admin `oid`**. |
| `WEB_WEBHOOK_SELECTED_RATE_PER_MIN` | No | `10` | Max successful `SELECTED` webhook sends/updates per admin `oid` per rolling minute. |
| `WEB_ADMIN_AUDIT_BLOB_CONTAINER` | No | `admin-audit` | Blob container for durable webhook broadcast audit objects (§7). |

---

## 4. Frontend (MSAL.js)

| Setting | Value |
|---|---|
| `clientId` | `WEB_ENTRA_CLIENT_ID` |
| `authority` | `WEB_ENTRA_AUTHORITY` |
| `redirectUri` (prod) | Web public origin (same origin as FastAPI static shell) |
| `redirectUri` (dev) | `http://localhost:{WEB_PORT}` when hitting the FastAPI shell; Vite HMR origin when using `docker-compose.dev.yml` / Vite proxy (both URIs registered on the Entra app) |
| `scopes` | `[ "{WEB_ENTRA_API_AUDIENCE}/access_as_admin" ]` — e.g. `api://{client_id}/access_as_admin` |

On every `/api/*` request, attach:

```http
Authorization: Bearer <access_token>
```

**PubSub negotiate** uses the **same** Bearer — no separate cookie or negotiate-only credential (`contracts/pubsub_live.md` §4).

### Token cache / CSRF

- APIs use **Bearer tokens**, not cookie sessions → **classic CSRF against cookie-auth is N/A**. **No CSRF token is required for v1.**
- Prefer MSAL’s default cache behavior / **sessionStorage** guidance from MSAL best practices; do not casually persist refresh tokens in `localStorage`.

### Error UX

| API status | Frontend behavior |
|---|---|
| **401** | Trigger MSAL login (redirect or popup); retry the call after acquiring a token. |
| **403** | Show a “not authorized” page (signed in but not in the admin group). Do not loop login. |

---

## 5. Backend JWT Validation & Authorization

FastAPI middleware (or dependency) on **all** `/api/*` routes:

1. Require `Authorization: Bearer …`; missing/malformed → **401**.
2. Validate JWT signature via Entra JWKS; reject on failure → **401**.
3. Validate claims:
   - `iss` = `https://login.microsoftonline.com/{WEB_ENTRA_TENANT_ID}/v2.0`
   - `aud` = `WEB_ENTRA_API_AUDIENCE`
   - `tid` matches `WEB_ENTRA_TENANT_ID`
   - `exp` / `nbf` within skew policy
4. Authorize: `WEB_ENTRA_ADMIN_GROUP_ID` must appear in the token’s `groups` array. Missing group / overage without resolvable `groups` → **403** (and log).
5. Attach identity to the request context for audit: **`oid` (required)**; `preferred_username` or `upn` / `email` when present (nullable audit fields).

### Route policy

| Surface | Auth |
|---|---|
| All `/api/*` | Valid Bearer **and** admin-group membership |
| Static shell — `GET /`, `GET /assets/*`, and other non-API SPA routes | **Public** (so the SPA can load and redirect to login) |

Optional: `GET /api/health` may remain unauthenticated for probes only if operators need it; if exposed, it must return **no** secrets and **no** admin data. Exact health contract remains P1.6 — if health stays under `/api/*` without an explicit public carve-out, it requires the same Bearer as every other API route.

---

## 6. Secret Redaction (Web APIs & Logs)

List/detail Web APIs and logs **must never** return or print:

| Secret | Rule |
|---|---|
| Guild `webhook_url` | Never in JSON responses — only `webhook_configured: bool` (derived). |
| Guild `api_key` | Never in Web APIs or logs. |
| Azure SP secrets, HMAC secrets, raw PubSub connection strings | Never in API responses or `INFO`+ logs. |
| PubSub client access URL | Negotiate may return the short-lived client access **URL** **only** to authenticated admins (join/leave-only roles already scoped). |

---

## 7. Webhook SSRF Allowlist & Broadcast Controls

### URL allowlist (config save + broadcast POST)

Allow **only** these exact hosts over HTTPS:

- `https://discord.com/api/webhooks/...`
- `https://discordapp.com/api/webhooks/...`

Reject: non-HTTPS, any other host, IP literals, userinfo tricks, and **do not follow redirects** when POSTing. Bot `/config` must apply the same allowlist on save (`bot/commands/config.md`); Web **re-validates** before every Discord POST even if Cosmos holds a legacy bad value.

### Confirmation, idempotency, rate limits

| Control | Rule |
|---|---|
| UI confirmation | Explicit confirm (`ConfirmDialog`, `components.md` §6) before any `destination: "ALL"` send/update. |
| Idempotency | `POST /api/webhook/send` and `POST /api/webhook/update` require `Idempotency-Key` (UUID) — header preferred; body field acceptable. Replay of the same key returns the prior result **without** re-POSTing Discord. |
| ALL cooldown | Max **1** successful ALL-broadcast per `WEB_WEBHOOK_ALL_COOLDOWN_SEC` (default **60**) per admin `oid`. |
| SELECTED rate | Max `WEB_WEBHOOK_SELECTED_RATE_PER_MIN` (default **10**) successful SELECTED sends per admin `oid` per rolling minute. |

### Durable audit (broadcasts)

Every successful or attempted webhook send/update writes a durable audit object (wide blast radius):

| | |
|---|---|
| **Store** | Blob under `WEB_ADMIN_AUDIT_BLOB_CONTAINER` (default `admin-audit`) |
| **Path** | `webhook/{yyyy}/{mm}/{request_id}.json` |
| **Body (min)** | `{ schema_version, acted_by_oid, acted_by_upn, acted_at, action: "webhook_send" \| "webhook_update", destination, guild_ids, request_id, idempotency_key }` |

Also log the same identity fields at **INFO**. `request_id` ties the audit row to the idempotency record.

### Suggestion / status audit

- Suggestion respond/done: persist `acted_by_oid`, `acted_by_upn` (nullable if claim missing), `acted_at` on the ticket — see `contracts/suggestion.md` §2.
- StatusDocument identity/catalog writes by Web: log actor `oid` at INFO; optional `updated_by_oid` on the document is allowed but not required for v1 (`contracts/status_document.md`).

---

## 8. Dev / Local

- Register redirect URIs for production Web origin, `http://localhost:{WEB_PORT}`, and the Vite dev origin when used.
- Same tenant/client IDs via `.env`; **never commit secrets**. The SPA client ID is not a secret; there is **no** client secret for public SPA PKCE.
- Local operators still need membership in `WEB_ENTRA_ADMIN_GROUP_ID` (or a dedicated dev group swapped via env) — there is no “auth off” switch in v1 docs.

---

## 9. Claims Used for Audit

| Claim | Required | Use |
|---|---|---|
| `oid` | Yes | Actor id; PubSub negotiate `user id`; audit fields; rate-limit key |
| `preferred_username` / `upn` / `email` | No | Stored as `acted_by_upn` when present |
| `groups` | Yes (for authz) | Must contain `WEB_ENTRA_ADMIN_GROUP_ID` |

---

## 10. Distinct from Azure Service Principal Auth

`WEB_AZURE_CLIENT_*` / `AZURE_TENANT_ID` in `azure.md` authenticate the **Web container** to Azure resources (Cosmos, Queue, PubSub negotiate minting, etc.). `WEB_ENTRA_*` authenticate **human admins** in the browser to the Web API. Do not conflate the two app registrations or reuse SP client secrets in MSAL.

---

## 11. Related

| Concern | Canonical doc |
|---|---|
| Web env + middleware | `containers/web/web.md` §3 / §6.3 |
| PubSub negotiate auth | `contracts/pubsub_live.md` §4 |
| Suggestion audit fields | `contracts/suggestion.md` §2 |
| Guild webhook redaction / allowlist note | `contracts/guild_config.md` |
| Webhook page UX / endpoints | `web/pages/webhook.md` |
| Bot webhook URL validation on save | `bot/commands/config.md` |
| Group-overage Graph fallback | P2 (`to_resolve.md`) |
