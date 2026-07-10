# Contract: Localization (Bot UI + AI-Generated Content)

> **Why this lives in `contracts/`, not a single service doc:** the same guild-level locale value currently drives both `Bot`'s UI strings and the `language_locale` field consumed by `AI Worker`'s `environment`/`battle` graphs. Both sides need to agree on what the value means and where it comes from — per `docs/Readme.md`'s stated purpose for this folder, that agreement belongs here, not duplicated inside `discord_bot.md` and `environment.md`/`battle.md` separately.

---

## 1. Purpose

Defines, for the current (v1) design: what a guild's "locale" setting actually is, where it's configured, and which two independent systems it drives. Also records the project owner's stated direction for how this is likely to evolve, explicitly marked as **not yet decided/implemented** (§5), so that direction isn't confused with the current confirmed design.

---

## 2. The Two Systems This Value Drives (confirmed: same value, for now)

| | Consumes locale via | Example values seen in repo |
|---|---|---|
| **Bot UI strings** | `src/bot/localization/handler.py` + `src/bot/localization/lang/<locale>.json` | `en`, `es`, `ua` — only 3 files exist today |
| **AI-generated content** | `language_locale` field on `environment` (`graphs/environment.md` §2) and `battle` (`graphs/battle.md` §2) | Arbitrary strings per `environment_combiner.txt`'s own examples — `uk-UA`, `fr`, `de-DE` |

**Confirmed decision (project owner):** as of this revision, these are **the same underlying value** — one guild-level setting drives both. There is currently no mechanism to configure them independently.

> **Value-space mismatch (flagged, not yet resolved elsewhere):** Bot UI localization only has 3 concrete language files, while the AI-content side accepts a much wider range of locale strings (anything Gemini can plausibly write in). **Working assumption (confirmed for now):** if the configured locale has no matching file under `src/bot/localization/lang/`, the Bot UI falls back to `en.json`. The AI-content side is unaffected by this fallback — `language_locale` is passed through to the graphs as-configured regardless of whether a matching UI json exists.

---

## 3. Source: Guild-Level `/config` (confirmed)

| | |
|---|---|
| **Mechanism** | A `/config` command (owned by `Bot`, `bot/commands/config.md`) sets one locale value per guild. |
| **Scope** | Per-guild, not per-user, not per-battle, not per-lobby. Every `/quick-battle` invocation in a guild uses that guild's currently configured locale. |
| **Not automatic** | Deliberately **not** sourced from Discord's own `interaction.locale` — a guild must explicitly configure it via `/config`. **Resolved** — see §6: `contracts/guild_config.md` §5 confirms the default is `"en"` for any guild that hasn't run `/config` yet, set on `on_guild_join` (`bot/discord_bot.md` §6.2). |
| **Storage** | **Resolved** — the guild configuration document's `language` field, in `Azure Cosmos DB`'s `GuildConfigs` collection. Exact schema now defined in `contracts/guild_config.md` §3, not just inferred from `architecture.md`'s Data Storage table. |

---

## 4. Where This Value Reaches `AI Worker`

`Bot` reads the guild's configured locale when building the `ai_tasks` message for either the `environment` or `battle` graph, and sets it as the `language_locale` input field — per `graphs/environment.md` §2 and `graphs/battle.md` §2. Neither graph ever reads guild configuration directly; `language_locale` always arrives pre-resolved as a plain string in the task input.

---

## 5. Roadmap Note — Explicitly NOT Decided or Implemented

The project owner has indicated the following is a likely **future** direction, recorded here so it isn't lost, but it is **not** the current design and nothing below should be treated as confirmed:

- Bot UI locale and AI-content `language_locale` may eventually become **independently configurable** (e.g. a guild that runs the bot in Ukrainian but generates battle stories in English).
- The sourcing mechanism itself may evolve past a single guild-level `/config` value — e.g. a per-lobby or per-battle override, similar in shape to how `battle`'s `random_winner_mode` is scoped (`graphs/battle.md` §2, also currently guild-config-sourced with the same kind of future-scoping question — see that doc's §12).

Do not build against this section — it exists purely so this direction is discoverable when the actual redesign happens.

---

## 6. Open Items

- ~~The default locale for a guild that has never run `/config` is undocumented~~ — **resolved** (§3): `"en"`, per `contracts/guild_config.md` §5.
- ~~No guild-configuration contract/schema exists yet~~ — **resolved**: `contracts/guild_config.md` now defines it (§3).
- `bot/discord_bot.md` (now drafted) owns the guild-config document's write side generally; `bot/commands/config.md` owns `/config`'s own flow specifically. This doc only defines the `language`/`language_locale` value's meaning and where `AI Worker` reads it — not re-duplicated here.
- Whether `en.json` is really the right UI fallback (§2) vs., say, matching by language-only prefix (`de-DE` → `de.json` if it existed) is a shallow assumption, not deeply considered.
