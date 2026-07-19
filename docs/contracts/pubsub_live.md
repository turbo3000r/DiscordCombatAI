# Contract: Live Dashboard Web PubSub

> **Closes P0.6** (topology/budget) **and completes negotiate auth via P0.7.** Group topology, client vs service SDKs, negotiate response, always-stream policy, Free_F1 budget, and reconnect semantics. Live payload body: `contracts/telemetry.md` §5. Authentication boundary for negotiate: `contracts/web_auth.md`.

---

## 1. Group Topology

| Env var | Default | Purpose | Members |
|---|---|---|---|
| `HEAD_PUBSUB_CLUSTER_GROUP` | `cluster` | Leader heartbeat + `update_available` (existing) | Every Head (leader and followers), permanently joined |
| `HEAD_PUBSUB_DASHBOARD_GROUP` | `dashboard-live` | Live telemetry/logs for browsers | Leader Head **sends**; browsers **join** with join/leave-only tokens |

Browsers must **never** be given tokens for `cluster`. Mixing coordination traffic with dashboard clients is forbidden.

Hub name remains `AZURE_WEBPUBSUB_HUB_NAME` (default `discordcombatai`, `azure.md` §3).

---

## 2. Libraries and Roles

| Actor | Library / protocol | Role |
|---|---|---|
| `Head` (send to `dashboard-live`) | `azure-messaging-webpubsubservice` (service SDK) | `send_to_group` |
| `Head` (cluster join + receive heartbeats / broadcasts) | Web PubSub **client** WebSocket protocol (plus service SDK for token if needed) | Long-lived member of `cluster` |
| `Web` backend | Service SDK `get_client_access_token` only | Negotiates; **does not** listen to PubSub |
| Browser | WebSocket client to Azure Web PubSub | Joins `dashboard-live` with negotiated URL |

**Corrected architecture wording:** the **browser** connects to Web PubSub; the `Web` container only negotiates tokens. Overview text must not say the Web container “listens” to PubSub.

---

## 3. Always-Stream Policy (option A)

While this Head is **leader**, always publish `telemetry_live` to `dashboard-live` every `HEAD_TELEMETRY_LIVE_INTERVAL_SEC` (default `10`).

**Remove** all conditional “detect dashboard listener / subscribe event / stop streaming when empty” designs. There is no Web PubSub event-handler upstream or presence poll for this purpose in v1.

Followers do not stream (see `telemetry.md` §1).

### Free_F1 budget (verified fit)

Azure Web PubSub **Free_F1**: **20,000 messages/unit/day**, **20 concurrent connections**.

Approximate daily cost with defaults:

| Traffic | Rough msgs/day |
|---|---|
| 1 leader + 2 idle followers in `cluster`, heartbeats every 30s (fan-out to 3 connections) | ≈ **8.6k** |
| Always-stream to an **empty** `dashboard-live` group | ≈ **0 outbound** (no subscribers to bill for delivery) |
| + 1 browser viewer receiving 10s live ticks | ≈ **+8.6k** |
| **Total with 1 viewer** | ≈ **17.2k** — fits under 20k if live payloads stay small |

Live ticks are capped at 50 newest log lines and 65,536 serialized UTF-8 JSON bytes (`telemetry.md` §5), so oversized logs cannot blow the quota. Concurrent connections: 3 Heads + 1 browser ≪ 20.

---

## 4. Negotiate Endpoint

```
GET /api/pubsub/negotiate
Authorization: Bearer <Entra access_token>
→ 200
{
  "url": "wss://…",          // client access URL including access_token
  "expires_at": "2026-07-15T18:02:00Z",
  "group": "dashboard-live"
}
```

| Property | Value |
|---|---|
| Token roles | **Join and leave only** for `dashboard-live` — **no send** |
| TTL | Default **60 minutes** |
| User id | Entra **`oid`** from the validated access token (stable admin identity) |
| Auth | Same as every `/api/*` route: valid Bearer + admin-group membership (`contracts/web_auth.md`). **401** if unauthenticated; **403** if authenticated but not admin. |

The short-lived client access **URL** is returned only to authenticated admins. It must never appear in list/detail APIs unrelated to negotiate, or in logs at `INFO`+.

---

## 5. Reconnect and Ordering

1. On disconnect or token expiry, the browser calls negotiate again (with a fresh Bearer if needed) and reconnects.
2. Use `seq` from `telemetry_live` (`telemetry.md` §5) to ignore older/duplicate messages.
3. **No historical backfill** on the live channel — use `GET /api/metrics/history` for history. The live console starts empty (or from ticks after connect only).

---

## 6. Related

| Concern | Canonical doc |
|---|---|
| Live payload + caps | `contracts/telemetry.md` §5 |
| Head env / loops | `containers/head.md` §3/§5/§8 |
| Web negotiate | `containers/web/web.md` §6.1, `pages/dashboard.md` |
| Auth boundary | `contracts/web_auth.md` |
