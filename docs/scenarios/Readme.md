# Scenarios — Architecture Acceptance Cases

> **Closes P0.8.** These are end-to-end architecture acceptance cases, not test-code prescriptions. Each file names preconditions, ordered steps (messages / state transitions), durable writes, timeouts, user-visible result, and the invariant checked.
>
> Prefer linking to canonical contracts and container docs over inventing new behavior. Where a detail remains a P1 product rule (especially Quick Battle numbers), the scenario stays at architecture level and notes that P1.1 applies.
>
> **Phase 2:** S03, S04, S05, S07, S08, and S10 each include a **Phase 2 acceptance ownership** matrix labeling steps `complete`, `integration-only`, or `deferred`. Do not claim Discord slash-command recovery before the command phases.
>
> **Product development:** S14 proves local isolation (`contracts/local_development.md`). It is **not** a substitute for Azure coordination scenarios S01–S10 or production suggestion/webhook scenarios S12–S13.

## Purpose

- Prove that documented contracts compose into coherent failover, outage, update, task, suggestion, and Web-admin flows.
- Give implementers a shared checklist of “what must remain true” without prescribing pytest structure or mock libraries.
- Stay short: one markdown file per scenario (or one file with clear invariant subsections when the product flow shares setup).

## Index

| Id | File | Focus |
|---|---|---|
| S01 | [01_cold_boot_no_leader.md](01_cold_boot_no_leader.md) | Cold boot; no leader yet; Bot stays inactive |
| S02 | [02_follower_race_failover.md](02_follower_race_failover.md) | Follower race; Blob Lease winner; successful failover |
| S03 | [03_leader_head_crash_bot_alive.md](03_leader_head_crash_bot_alive.md) | Leader Head crash while Bot process still alive |
| S04 | [04_mosquitto_partial_outage.md](04_mosquitto_partial_outage.md) | Mosquitto / control-plane partial outage |
| S05 | [05_rabbitmq_partial_outage.md](05_rabbitmq_partial_outage.md) | RabbitMQ partial outage |
| S06 | [06_azure_partial_outage.md](06_azure_partial_outage.md) | Azure coordination plane (Blob Lease + PubSub) partial outage |
| S07 | [07_planned_update_happy_path.md](07_planned_update_happy_path.md) | Planned update; clean drain |
| S08 | [08_planned_update_drain_timeout.md](08_planned_update_drain_timeout.md) | Planned update; drain timeout → hard-stop |
| S09 | [09_planned_update_rollback.md](09_planned_update_rollback.md) | Planned update failure / rollback path |
| S10 | [10_bot_or_ai_worker_restart_mid_task.md](10_bot_or_ai_worker_restart_mid_task.md) | Bot or AI Worker restart mid-task |
| S11 | [11_quick_battle_success_abort_timeout.md](11_quick_battle_success_abort_timeout.md) | Quick Battle success / abort / timeout |
| S12 | [12_suggestion_duplicate_or_lost_queue.md](12_suggestion_duplicate_or_lost_queue.md) | Suggestion respond; duplicate or lost Queue |
| S13 | [13_web_auth_and_all_guild_broadcast.md](13_web_auth_and_all_guild_broadcast.md) | Web Entra auth + ALL-guild webhook broadcast |
| S14 | [14_local_development_isolation.md](14_local_development_isolation.md) | Product-dev isolation (separate Discord app, local providers, no Azure/webhook egress) |

## Canonical references (do not redefine here)

| Concern | Doc |
|---|---|
| Leadership / grants / soft vs hard stop | `contracts/leadership_control.md` |
| Drain progress / update completion | `contracts/drain_status.md` |
| AI task wire / cancel / DLQ | `contracts/ai_task.md` |
| Suggestion ticket + claim | `contracts/suggestion.md` |
| Web admin auth + broadcast controls | `contracts/web_auth.md` |
| Head ↔ Launcher IPC | `contracts/launcher_ipc.md` |
| Live PubSub | `contracts/pubsub_live.md` |
| Local product-development isolation | `contracts/local_development.md` |
| High-level narrative | `architecture.md` (Scenarios 1–5; Environments section) |
