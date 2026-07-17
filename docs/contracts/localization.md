# Contract: Localization (Bot UI + AI-Generated Content)

> **Why this lives in `contracts/`, not a single service doc:** the same guild-level locale value currently drives both `Bot`'s UI strings and the `language_locale` field consumed by `AI Worker`'s `environment`/`battle` graphs. Both sides need to agree on what the value means and where it comes from — per `docs/Readme.md`'s stated purpose for this folder, that agreement belongs here, not duplicated inside `discord_bot.md` and `environment.md`/`battle.md` separately.

---

## 1. Purpose

Defines, for the current (v1) design: what a guild's "locale" setting actually is, where it's configured, which two independent systems it drives, and the exact stored-value ↔ AI-content mapping (P1.9, resolved).

---

## 2. The Two Systems This Value Drives (confirmed: same stored setting, for now)

| | Consumes locale via | v1 values |
|---|---|---|
| **Bot UI strings** | `src/bot/localization/handler.py` + `src/bot/localization/lang/<ui_locale>.json` | File stems: `en`, `es`, `ua` only |
| **AI-generated content** | `language_locale` on `environment` / `battle` graph inputs | Canonical tags after Bot mapping (§4): `en`, `es`, `uk-UA` |

**Confirmed decision (project owner):** as of v1, these are **one guild-level setting** — there is no independent UI vs AI locale control. Future split remains P2 (§5).

---

## 3. Source: Guild-Level `/config` (confirmed)

| | |
|---|---|
| **Mechanism** | `/config` (`bot/commands/config.md`) sets one locale value per guild via `LanguageSelect`. |
| **Scope** | Per-guild. Every `/quick-battle` in that guild uses the current value. |
| **Not automatic** | Not sourced from Discord `interaction.locale`. Default for never-configured guilds: `"en"` (`contracts/guild_config.md` §5). |
| **Storage** | Guild document field `language` in Cosmos `GuildConfigs` (`contracts/guild_config.md` §3). |

### 3a. Allowed stored values (resolved, P1.9)

`language` is a **closed v1 enum**, not free-form BCP-47:

| Stored `language` | Meaning | UI lang file | Notes |
|---|---|---|---|
| `en` | English | `en.json` | Default |
| `es` | Spanish | `es.json` | |
| `ua` | Ukrainian (UI key) | `ua.json` | **Legacy internal UI key**, not ISO 639-1. ISO 639-1 for Ukrainian is `uk`. Do not rename the file or Select value in v1 without a migration. |

`/config` must only offer these three options. Reject any other stored value on read by falling back per §3b (treat as unknown).

### 3b. Bot UI fallback matching (resolved, P1.9)

When resolving UI strings for a locale key `L`:

1. If `lang/{L}.json` exists → use it.
2. Else if `L` contains `-` (or `_`), take the primary subtag before the separator (e.g. `de-DE` → `de`) and if `lang/{primary}.json` exists → use it.
3. Else → `en.json`.

For v1's closed enum, step 1 always hits for valid stored values. Steps 2–3 exist so unknown/legacy/corrupt Cosmos values and any future BCP-47 expansion behave deterministically.

---

## 4. Where This Value Reaches `AI Worker` (mapping resolved, P1.9)

`Bot` reads `GuildConfigDocument.language` when building an `ai_tasks` envelope and sets graph input `language_locale` as follows — **Bot maps; AI Worker never sees the raw UI key for Ukrainian:**

| Stored `language` | `language_locale` sent to graphs |
|---|---|
| `en` | `en` |
| `es` | `es` |
| `ua` | `uk-UA` |

Neither graph reads guild configuration directly; `language_locale` always arrives pre-resolved as a plain string. Prompt slots such as `{locale}` / `{LANGUAGE-LOCALE}` receive that mapped value.

Do **not** pass `ua` through unchanged to Gemini — that was the ambiguity P1.9 closed.

---

## 5. Roadmap Note — Explicitly NOT Decided or Implemented

The project owner has indicated the following is a likely **future** direction, recorded here so it isn't lost, but it is **not** the current design:

- Bot UI locale and AI-content `language_locale` may become **independently configurable**.
- Sourcing may evolve past a single guild-level `/config` value (per-lobby / per-battle overrides).

Do not build against this section.

---

## 6. Open Items

- ~~Default locale~~ — **resolved:** `"en"`.
- ~~Guild config schema~~ — **resolved:** `contracts/guild_config.md`.
- ~~Stored value / `ua` vs `uk` / AI mapping / UI fallback~~ — **resolved this revision (P1.9):** §3a/§3b/§4.
- Exact localization copy/key names remain P2 (implementation).
