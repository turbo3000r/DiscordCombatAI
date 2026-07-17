# Contract: Head ↔ Launcher IPC

## 1. Scope and Network Boundary

This is the canonical contract for both host/container IPC directions:

- `Head` container → host `Launcher`: `POST /v1/update` and optional `GET /v1/status`;
- host `Launcher` → `Head` container: `GET /v1/health`.

The transport is authenticated HTTP over host-reachable TCP. It is **not loopback in the Head → Launcher direction**:

- `Head` calls `http://host.docker.internal:${LAUNCHER_IPC_PORT}`;
- Docker Desktop on Windows provides `host.docker.internal`;
- Linux Compose must add `extra_hosts: ["host.docker.internal:host-gateway"]`;
- `Launcher` binds `LAUNCHER_IPC_BIND` (default `0.0.0.0`) so the container can reach it;
- the host firewall permits TCP `9700` only from the Docker bridge/private Docker Desktop network and denies LAN/public ingress.

For Launcher → Head, `Head` listens on `0.0.0.0:9800` inside its container and Compose publishes only `127.0.0.1:${HEAD_IPC_HOST_PORT}:9800`. `Launcher` therefore calls `http://127.0.0.1:${HEAD_IPC_HOST_PORT}`. Port 9800 must never be published on a non-loopback host address.

---

## 2. Configuration

| Owner | Variable | Default | Meaning |
|---|---|---|---|
| `Launcher` | `LAUNCHER_IPC_BIND` | `0.0.0.0` | Host bind address for the container-reachable listener. Safe only with the firewall rule in §1. |
| `Launcher` | `LAUNCHER_IPC_PORT` | `9700` | Host port for `/v1/update` and `/v1/status`. |
| `Launcher` | `LAUNCHER_IPC_SECRET_FILE` | required | Host path to a file containing a random secret of at least 32 bytes. |
| `Launcher` | `HEAD_IPC_HOST_PORT` | `9800` | Loopback-published host port used to reach `Head`. |
| `Head` | `HEAD_LAUNCHER_IPC_URL` | `http://host.docker.internal:9700` | Complete Launcher base URL; replaces the ambiguous port-only setting. |
| `Head` | `HEAD_IPC_BIND` | `0.0.0.0` | Container bind address for its health/status server. |
| `Head` | `HEAD_IPC_PORT` | `9800` | Container port; Compose maps it to host loopback only. |
| `Head` | `HEAD_LAUNCHER_IPC_SECRET_FILE` | `/run/secrets/launcher_ipc_secret` | Read-only in-container path to the same secret file. |

Generate the secret with a cryptographically secure random source (32 bytes minimum), store it with host-service-account-only permissions, and mount the same file read-only into `Head`. Do not put the raw secret in Compose YAML, command-line arguments, logs, error bodies, or source control. Secret rotation requires updating both readers and restarting them; no unauthenticated grace mode is permitted.

Minimum target Compose fragment:

```yaml
services:
  head:
    extra_hosts:
      - "host.docker.internal:host-gateway" # required on Linux; harmless where built-in
    ports:
      - "127.0.0.1:${HEAD_IPC_HOST_PORT:-9800}:9800"
    volumes:
      - "${LAUNCHER_IPC_SECRET_FILE}:/run/secrets/launcher_ipc_secret:ro"
```

Windows Docker Desktop uses the built-in host name. Linux requires Docker Engine 20.10+ for `host-gateway`. The firewall rule is host provisioning, not a Compose substitute.

---

## 3. Authentication, Replay Protection, and Limits

Every request in either direction is HMAC-SHA256 authenticated with:

- `X-DCA-Timestamp`: Unix seconds;
- `X-DCA-Request-ID`: UUID;
- `X-DCA-Signature`: lowercase hex HMAC-SHA256.

Canonical bytes:

```text
UPPERCASE_METHOD + "\n" +
PATH_WITH_QUERY + "\n" +
TIMESTAMP + "\n" +
REQUEST_ID + "\n" +
LOWERCASE_HEX_SHA256(EXACT_BODY_BYTES)
```

The signature is `HMAC-SHA256(secret, canonical_bytes)`. For GET, the body is empty. Receivers compare signatures in constant time, reject timestamps outside ±30 seconds, and retain accepted `(request_id, signature)` entries for 10 minutes. Reusing the same authenticated update request is allowed for idempotency (§4); reusing an ID with different canonical bytes is `401`. Invalid/missing authentication returns `401` with a generic body and is never retried as an application request.

Both servers cap request bodies at 16 KiB, require `Content-Type: application/json` when a body exists, redact all authentication headers, and never log the secret or full signature.

Every payload below carries `schema_version`; an unknown value is rejected as `400 Bad Request`/`422` rather than guessed at — the same uniform rule stated in `contracts/leadership_control.md` §7 and `contracts/drain_status.md` §5.

---

## 4. Head → Launcher: Update Request

### Request

`POST /v1/update`

```json
{
  "schema_version": 1,
  "request_id": "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
  "target_version": "v1.5.0",
  "reason": "auto_detected",
  "requested_at": "2026-07-15T17:05:00Z",
  "source_node_id": "node-a"
}
```

`reason` is one of `auto_detected`, `manual`, `rollback`, `reconcile`. Body `request_id` must equal `X-DCA-Request-ID`. `target_version` must be a configured valid release-tag format; shell fragments and arbitrary image references are rejected.

### Accepted/current response

`202 Accepted` when queued or already running:

```json
{
  "schema_version": 1,
  "request_id": "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
  "operation_id": "c39120a0-a554-447f-afc7-02c65e65e11d",
  "state": "accepted",
  "target_version": "v1.5.0"
}
```

The same target while busy returns `202` with the current `operation_id`, even when the new `request_id` differs. Replaying the same `request_id` returns the previously recorded response and never starts a second operation.

`200 OK` means the exact request already completed or the target is already current:

```json
{
  "schema_version": 1,
  "request_id": "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
  "operation_id": "c39120a0-a554-447f-afc7-02c65e65e11d",
  "state": "already_current",
  "target_version": "v1.5.0"
}
```

### Conflict/error response

- `409 Conflict`: Launcher is busy with a different target. Body includes `error.code: "update_busy"`, current `operation_id`, and current `target_version`.
- `400 Bad Request`: malformed JSON/schema or request/header ID mismatch.
- `422 Unprocessable Entity`: syntactically valid but unsupported/invalid target or reason.
- `401 Unauthorized`: authentication, skew, or replay-integrity failure.
- `500/503`: transient Launcher failure/unavailable.

Error bodies use:

```json
{
  "schema_version": 1,
  "request_id": "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
  "error": {
    "code": "update_busy",
    "message": "Launcher is processing another target."
  }
}
```

### Timeout, retry, and deduplication

`Head` uses a 2-second connect timeout and 5-second response timeout, with at most 3 attempts total and jittered exponential delays. Every retry uses the identical body, timestamp/signature pair while still inside the skew window, and the same request ID. If a retry would exceed the skew window, it creates a fresh timestamp/signature but retains the same body/request ID.

Retry only connection failures, response timeouts, `500`, and `503`. Do not retry `400`, `401`, `409`, or `422`. A response timeout is an unknown outcome, so the same request ID is mandatory.

`Launcher` persists accepted/completed request IDs and operation results in its state file before returning success, retaining them for at least 24 hours and across process restart. It also persists the active operation before execution. This deduplicates Head retries and Launcher restarts.

---

## 5. Launcher → Head: Liveness

`GET /v1/health`

`200 OK`:

```json
{
  "schema_version": 1,
  "status": "alive",
  "version": "v1.5.0",
  "instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
  "started_at": "2026-07-15T17:06:00Z"
}
```

`503 Service Unavailable` may be returned while the process exists but initialization is incomplete:

```json
{
  "schema_version": 1,
  "status": "initializing",
  "version": "v1.5.0",
  "instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
  "started_at": "2026-07-15T17:06:00Z"
}
```

This endpoint is authenticated and is **liveness only**. It does not assert leadership, lease ownership, Mosquitto connectivity, or Bot readiness. Launcher uses a 2-second connect timeout and 5-second response timeout per poll and continues polling according to `LAUNCHER_HEALTHCHECK_INTERVAL_SEC` and `LAUNCHER_HEALTHCHECK_TIMEOUT_SEC`. `401`, malformed payload, `503`, timeout, and connection failure are unsuccessful polls; they trigger rollback only when the overall verification window expires.

---

## 6. Launcher Status

`GET /v1/status` is authenticated and host/container-private. It returns:

```json
{
  "schema_version": 1,
  "state": "IDLE",
  "current_version": "v1.5.0",
  "previous_version": "v1.4.0",
  "operation_id": null,
  "target_version": null
}
```

It is diagnostic/idempotency visibility only and does not replace the asynchronous `POST /v1/update` response.
