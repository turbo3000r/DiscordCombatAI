# Page: Webhook

## 1. Purpose & Scope

Broadcast messages to guild-configured Discord webhooks: either a free-form announcement, or a structured release/update note (added/removed items + version bump). This is the one page that talks to Discord directly over plain HTTPS, bypassing both `Bot` and Azure entirely for the actual message delivery (`web.md` §5) — everything else on this page (loading the guild list, persisting a changelog, tracking version) does go through Azure. All actions require Entra admin auth (`contracts/web_auth.md`); ALL-destination broadcasts additionally require UI confirmation, idempotency keys, rate limits, and durable audit.

## 2. Route & Entry Point

| | |
|---|---|
| **Frontend route** | `/webhook` |
| **Frontend source** | `frontend/src/pages/WebhookPage.tsx` |
| **Nav label** | "Webhook" |

## 3. Layout & Components

A message-type toggle (Announcement vs. Update) switches between two form layouts:

- **Announcement:** title, author, message body, destination (All guilds / selected guilds via a checkbox list).
- **Update:** version (with suggested-next-version dropdown), version name, title, dynamically add/remove "Added" and "Removed" line items (each with optional comment), source-code link, additional message. Updates always broadcast to all guilds (no destination selector) — carried forward from legacy as-is.

**ConfirmDialog** (`components.md` §6) is required before any `destination: "ALL"` send (announcement ALL or update-to-all). Shared shell/nav only otherwise — the guild checkbox list and dynamic add/remove item rows stay page-local.

## 4. Data Sources

| Source | Channel | Format | Trigger |
|---|---|---|---|
| `pages/guilds.md` §5's `/api/guilds` endpoint | HTTPS `GET` | Guild list, for the destination checkboxes | On page load |
| This page's own backend (§5) | `GET /api/webhook/version` | Current version + suggested next versions | On page load |

## 5. Backend Endpoints (owned by this page)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `POST` | `/api/webhook/send` | Headers: `Authorization: Bearer …`, `Idempotency-Key: <uuid>`; Body: `{title, author, message, destination: "ALL"\|"SELECTED", guild_ids}` | `{success, message, results: {guild_id: bool}, request_id}` | Reads each target guild's webhook URL from Cosmos, **re-validates** Discord-host allowlist, POSTs without following redirects. Never returns raw URLs. Idempotent replay returns prior result. ALL cooldown / SELECTED rate per `contracts/web_auth.md` §7. Durable audit blob written. |
| `POST` | `/api/webhook/update` | Same auth + `Idempotency-Key`; Body: `{version, version_name, title, added: [{text, comment}], removed: [...], source_code, additional_message, destination: "ALL", guild_ids: []}` | `{success, message, results, version, request_id}` | Same SSRF/idempotency/rate/audit rules as send. See §9 for changelog/version persistence gaps vs. legacy. |
| `GET` | `/api/webhook/version` | Bearer required | `{current_version, suggested_versions}` | See §9 — same persistence gap applies to where "current version" is read from |

## 6. User Interactions & Actions

| Action | Effect |
|---|---|
| Message-type toggle | Switches the visible form (§3), no backend call |
| Destination radio (Announcement only) | Toggles visibility of the guild checkbox list |
| Add/remove item row (Update only) | Client-side only, mutates the in-progress form state |
| "Send Announcement" / "Send Update to All Guilds" | Validates required fields; if destination is ALL (or update-to-all), opens `ConfirmDialog` first; then calls the endpoint with a fresh `Idempotency-Key`; shows per-guild success/failure and clears the form on success |
| Version-suggestion dropdown | Fills the version input field with the selected suggestion, no backend call |

## 7. State & Refresh Behavior

- No polling, no live data — this is a pure form/action page. Guild list and version info are fetched once on load.
- In-progress form state (added/removed items, field values) is not persisted — a page reload loses an unsent draft, matching legacy.

## 8. Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| `/api/guilds` fails on load | Fetch rejects or non-2xx | Destination checkbox list stays empty; "ALL" destination is unaffected since it doesn't need the list |
| `/api/webhook/send` or `/api/webhook/update` fails outright | Non-2xx response | Error message shown, form is **not** cleared (so the admin doesn't lose their draft) |
| A specific guild's webhook POST fails (partial failure) | Reflected in the endpoint's own per-guild `results` map | Success message still shows, reporting `N/total` succeeded — matches legacy's own partial-success reporting, not treated as a hard failure |
| Webhook URL fails allowlist | Validation before POST | That guild marked failed in `results`; no outbound request to a non-allowlisted host |
| Rate limit / cooldown exceeded | **429** (or equivalent) | Form not cleared; error explains ALL cooldown or SELECTED rate |
| Duplicate `Idempotency-Key` | Prior result returned | No second Discord blast |

## 9. Dependencies

| Dependency | Used for | Notes |
|---|---|---|
| Azure Cosmos DB | Reading each guild's `webhook_url` from its config document (server-side only; never returned to the browser) | Direct `cosmos.py` access, per `web.md` §5; APIs expose `webhook_configured` only |
| Discord Webhook URLs (external, per-guild) | Actual message delivery | Direct HTTPS POST, allowlisted hosts, no redirects (`contracts/web_auth.md` §7) |
| Azure Blob Storage | Durable webhook admin audit | `WEB_ADMIN_AUDIT_BLOB_CONTAINER` / `contracts/web_auth.md` §7 |
| `contracts/web_auth.md` | Auth, SSRF allowlist, rate limits, idempotency, audit | Required for all mutating endpoints |
| **Changelog persistence — no equivalent decided.** Legacy wrote each update to a local `updates/<version>-<name>.md` file. `Web` has no filesystem to persist to across restarts/instances, and per `web.md`'s Addition note (`azure.md` §5), Blob Storage access was deliberately **not** added for `Web` yet. **Recommended (not implemented):** store the changelog as a Cosmos DB document (e.g. a `Releases` collection) instead of a file — `Web` already has full Cosmos access, so this avoids introducing a new Azure dependency just for this. Not confirmed. | | |
| **"Current version" tracking — same gap.** Legacy read/wrote a local `bot.json`'s `version` field directly. In this architecture, the coordinated release version is defined by whichever image tag `Launcher` deployed (`Launcher.md` §12), which `Web` has no access to (`Launcher` never talks to Azure, `Launcher.md` §5). `head.md`'s own "Centralized version visibility" open item (§5 there) already anticipates this exact problem from the other side: `Head` reading `Launcher`'s local `/status` and writing it to Cosmos/Table Storage itself. If that gets implemented, `/api/webhook/version`'s "current version" should read from wherever `Head` publishes it — not from anything `Web` sets itself, unlike legacy where the web panel was the source of truth for the version string. | | |

## 10. Open Items / Future Work

- Changelog persistence has no defined home (§9) — recommended direction given, not implemented.
- "Current version" as shown by `GET /api/webhook/version` has no defined source in this architecture at all (§9) — this endpoint cannot be faithfully implemented until `head.md`'s centralized version visibility open item is resolved. Until then, `suggested_versions` could still be computed from whatever the admin manually enters as "current," same as legacy's own fallback when `bot.json` didn't exist.
- ~~No confirmation / auth / SSRF for ALL broadcasts~~ — **resolved (P0.7):** `ConfirmDialog` + Entra + allowlist + idempotency + rate limits + durable audit (`contracts/web_auth.md`).
