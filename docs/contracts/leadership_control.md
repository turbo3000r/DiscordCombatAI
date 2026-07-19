# Contract: Leadership Control and Bot Fencing

## 1. Scope and Safety Boundary

This is the canonical contract for:

- the Head-to-Head `leader_heartbeat` message;
- the local `Head` → `Bot` retained desired mode and non-retained activation grant;
- `Bot` acknowledgement and autonomous demotion behavior.

Azure Blob Lease is the authority used by `Head` to claim leadership. The grant defined here is a short-lived, local proof derived from that authority. Azure Web PubSub heartbeat is informational/liveness only and never authorizes `Bot`.

This design is **best-effort fencing, not a proof of strict at-most-one Gateway connection**. Network partitions, delayed failure detection, a final draining interval, or a failed stop acknowledgement can produce bounded dual-active overlap. The owner accepts that availability/safety limitation. Implementations must apply all safeguards below, but documentation and tests must not claim a universal at-most-one guarantee.

---

## 2. Terms and Ordering

- `head_instance_id`: UUID generated on each `Head` process start.
- `leadership_term`: UUID generated on each successful Blob Lease acquisition. It is an opaque identity, **not globally orderable**.
- `command_seq`: unsigned integer scoped to one `leadership_term`, starting at `1` and increasing for every desired-mode or grant command issued in that term.
- `grant_id`: UUID identifying one activation grant.

Within one term, `Bot` ignores a command whose `command_seq` is lower than or equal to the highest sequence it has accepted. A different `leadership_term` is not compared numerically or lexically. `Bot` accepts a new term only from its local `Head`, over a currently established local control connection, and only through a fresh non-retained grant. Retained data alone can never establish or change an active term.

---

## 3. MQTT Topics and Delivery

### 3.1 Retained desired mode

Topic: `control/bot/desired_state`

QoS: `1`  
Retain: `true`

```json
{
  "schema_version": 1,
  "state": "inactive",
  "head_instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
  "leadership_term": null,
  "command_seq": 0,
  "reason": "head_startup",
  "issued_at": "2026-07-15T17:00:00Z"
}
```

`state` is one of:

- `inactive`: safe default; no Gateway connection is authorized;
- `draining`: immediately reject new AI work and enter bounded drain;
- `stopped`: execute or remain in hard-stop.

There is deliberately **no retained `active` state**. On every process start, before election or lease acquisition, `Head` publishes retained `inactive` and confirms the MQTT publish. If it cannot do so, it must not activate `Bot`. An absent/malformed retained value is interpreted by `Bot` as `inactive`.

For commands in a leadership term, `leadership_term` is that term and `command_seq` is its next sequence. Startup `inactive` has `leadership_term: null` and `command_seq: 0`.

### 3.2 Non-retained activation grant

Topic: `control/bot/activation_grant`

QoS: `1`  
Retain: `false`

```json
{
  "schema_version": 1,
  "grant_id": "366ed38e-1e52-427f-a8c9-726a69628f69",
  "node_id": "node-a",
  "head_instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
  "leadership_term": "cd88086a-fd6d-48d4-8446-39523af2bf70",
  "command_seq": 3,
  "mode": "active",
  "ttl_sec": 45,
  "issued_at": "2026-07-15T17:01:00Z"
}
```

`mode` is `active` during normal service or `draining` for a bounded soft-stop. `ttl_sec` must be positive and bounded by configuration. `issued_at` is audit data; it is not used to run the countdown.

The documented defaults are a 45-second grant TTL, 15-second renewal cadence, and 45-second Bot-local failure-drain timeout. This makes grant expiry and the failure soft-stop bound converge on the same hard-stop deadline. Head's separate 120-second planned-update drain may continue only through renewed `draining` grants while Head remains alive and authoritative enough to coordinate it.

On receipt, `Bot` starts a local monotonic deadline of `receipt_monotonic + ttl_sec`. Sender and receiver wall clocks are therefore not part of ordinary grant validity. Grants are renewed before that deadline while `Head` remains confidently leader. Because grants are non-retained, an offline/restarting `Bot` cannot activate from stale MQTT state.

`Bot` may connect to or remain connected to the Gateway only while all are true:

1. its MQTT control connection is current;
2. it has accepted a fresh grant from the local `Head`;
3. the grant deadline has not expired;
4. the grant's `head_instance_id` and `leadership_term` match its accepted current term;
5. the grant sequence is newer than the last accepted sequence in that term.

A fresh `active` grant authorizes activation. A `draining` grant authorizes Gateway connectivity only for bounded drain and never authorizes new AI work.

### 3.3 Control acknowledgement

Topic: `status/bot/control_ack`

QoS: `1`  
Retain: `false`

```json
{
  "schema_version": 1,
  "node_id": "node-a",
  "head_instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
  "leadership_term": "cd88086a-fd6d-48d4-8446-39523af2bf70",
  "command_seq": 8,
  "state": "stopped",
  "gateway_connected": false,
  "observed_at": "2026-07-15T17:03:00Z"
}
```

`Bot` publishes an acknowledgement after applying `inactive`, entering `draining`, or completing the hard-stop sequence. `Head` correlates by term and sequence. Acknowledgement is best-effort; lack of acknowledgement never permits a grant to outlive its monotonic deadline.

---

## 4. Head-to-Head Heartbeat

Event discriminator: `leader_heartbeat`

```json
{
  "schema_version": 1,
  "type": "leader_heartbeat",
  "node_id": "node-a",
  "head_instance_id": "6b44781e-40f8-4807-9b4b-9087430c14b6",
  "leadership_term": "cd88086a-fd6d-48d4-8446-39523af2bf70",
  "lease_blob": {
    "container": "coordination",
    "name": "leader.lock"
  },
  "issued_at": "2026-07-15T17:01:00Z",
  "lease_expires_at": "2026-07-15T17:02:00Z",
  "application_version": "v1.4.0"
}
```

Followers use elapsed local monotonic time since receipt to detect heartbeat timeout. `issued_at` and `lease_expires_at` are observability fields only; followers do not infer lease authority from them. On heartbeat timeout, a follower must still acquire the named Blob Lease before creating a new leadership term. A heartbeat never activates `Bot`.

`application_version` is the required Compose-injected `APPLICATION_VERSION` and must match the exact release-tag grammar in `contracts/launcher_ipc.md` §4.

---

## 5. State Transitions and Failure Policy

### 5.1 Cold start and promotion

1. `Bot` starts `inactive`; no control connection and no current grant means no Gateway connection.
2. `Head` connects to Mosquitto and publishes retained `inactive` before election.
3. `Head` joins Web PubSub and acquires the Blob Lease.
4. On successful acquisition, it creates a new `leadership_term`.
5. Only while lease ownership is confidently current, it sends a fresh non-retained `active` grant and renews grants before expiry.
6. `Bot` accepts the new term from that live grant and connects.

Loss of Azure coordination prevents activation. A `Head` that cannot acquire/confirm the lease or cannot establish the control channel remains non-authoritative.

### 5.2 Soft-stop

Soft-stop means:

1. immediately enter `draining`;
2. remain Gateway-connected only to finish existing work;
3. reject all new AI work;
4. at the configured drain timeout, execute hard-stop unless safe leadership/control has been restored first.

When possible, `Head` publishes retained `draining` and one bounded `mode: "draining"` grant whose TTL does not exceed the remaining drain window. Restoration requires renewed confidence in the same lease term and a fresh `active` grant; a retained message cannot restore activity.

### 5.3 Hard-stop

Hard-stop uses the existing canonical sequence in `containers/bot/discord_bot.md` §6.5: purge queued AI work, terminate in-flight worker execution, notify affected Discord threads, then disconnect the Gateway. Grant expiry, `Head` watchdog/process disappearance, or an explicit `stopped` command causes hard-stop autonomously.

### 5.4 Failure matrix

| Failure | Required Bot outcome |
|---|---|
| `Head` process crash/disappearance | Active grant is no longer renewed; `Bot` hard-stops autonomously at grant/watchdog expiry. |
| Blob Lease renewal failure while Web PubSub remains available | Soft-stop immediately; retry/confirm authority only within the bounded drain. Resume active service only after the **same lease term** is confirmed and a fresh same-term grant is issued. Hard-stop at timeout otherwise; never treat acquisition of a new term as recovery of the old drain. |
| Web PubSub-only failure while Blob Lease renewal remains confirmed | Soft-stop immediately. Lease renewals may continue during bounded recovery, but no new AI work is accepted. Restore only with same-term confidence and fresh grant; otherwise hard-stop at timeout. |
| Mosquitto broker/control connection failure | `Bot` detects loss of its control connection and soft-stops immediately; grant expiry is the hard-stop backstop. |
| Simultaneous loss of Blob Lease coordination and Web PubSub | Immediate hard-stop; no drain allowance. |
| Missing, malformed, stale, replayed, or retained activation data | Ignore and remain/return inactive; non-retained fresh grant is mandatory. |
| Local `Head` learns it is not leader | Immediately command hard-stop and cease grants. `Bot` must not be left draining under a known non-leader. |

---

## 6. Voluntary Demotion and Update Ordering

The leader uses this order:

1. publish retained `draining` and issue only bounded draining grants;
2. wait for drain completion (`in_flight_workflows == 0`) or `HEAD_DRAIN_TIMEOUT_SEC`, per the drain-completion contract `contracts/drain_status.md` (P0.3, resolved);
3. publish retained `stopped`, stop issuing grants, and require `Bot` to run hard-stop;
4. wait best-effort for matching `state: "stopped", gateway_connected: false` acknowledgement, bounded by a configured acknowledgement timeout;
5. release the Blob Lease;
6. call local `Launcher`.

If acknowledgement is missing, the event is logged and the update may continue after the bounded timeout. The old grant deadline remains a backstop, but the accepted dual-active-overlap limitation in §1 applies. Releasing the lease before step 3 is forbidden.

Follower update handling does not involve a local active `Bot`: it keeps retained `inactive`/`stopped` and may call `Launcher` directly.

---

## 7. Validation and Logging Rules

- Reject unknown `schema_version`, enum values, missing IDs, non-positive/excessive TTLs, and malformed UUIDs.
- Never use UUID lexical ordering to decide which term is newer.
- Log term/grant/sequence identifiers, state transitions, deadlines, and acknowledgement timeout; do not log credentials.
- Wall-clock `issued_at`/`lease_expires_at` are ISO-8601 UTC for audit only. Local monotonic timers govern heartbeat and grant expiry.
