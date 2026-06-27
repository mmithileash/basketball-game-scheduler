# PRD: Per-game policy (turnout-dependent start time & duration)

## Problem Statement

When a game is announced, players are told a single fixed start time ("10:00 AM") and a vaguely-worded tentative duration. In reality the organiser wants the game's **start time and length to depend on turnout** — a well-attended game starts earlier and runs longer, a thinly-attended one starts later and runs shorter. Today the system can't express that: the start time is a single global config value that is interpolated into every email, the parsed per-game time from the admin's email is silently discarded, and the duration is computed transiently inside the confirmation step and never stored. There is also no way for the organiser to override the time/length for a specific game (e.g. "this Saturday, 2 hours from 9am") — every game is implicitly the same.

## Solution

Each game carries its own **policy** — the rules that decide its start time, duration, the turnout threshold that switches between them, and the minimum players required — stored on the game record and seeded from the admin's own scheduling email.

- If the admin's scheduling email says nothing about time, the game uses the **default two-tier policy**: at or above the threshold of confirmed players it starts early and runs long; below the threshold it starts later and runs short.
- If the admin's email fully specifies a start time **and** a duration, the game becomes a **fixed game** at exactly those values, independent of turnout.
- If the admin specifies only one of the two (a time but no length, or a length but no time), the request is ambiguous: the system schedules **nothing from that batch** and emails the admin back asking them to resend the complete, unambiguous command.

Players receive an honest tentative announcement that describes how the time depends on turnout (two branches when the game is tiered, a single line when it's fixed). Once sign-ups close, the confirmation step **locks in** the actual start time and duration based on the confirmed count, freezes them onto the game record, and the final confirmation email shows that single resolved time.

## User Stories

1. As an organiser, I want a game's start time to depend on how many players confirm, so that a busy game starts early and a quiet one starts later.
2. As an organiser, I want a game's duration to depend on how many players confirm, so that we book the court for the right length.
3. As an organiser, I want the start time and duration to switch together at a single turnout threshold, so that the rule stays simple and predictable.
4. As an organiser, I want sensible default times and durations applied automatically when I don't specify any, so that I can schedule a normal game by just naming the day.
5. As an organiser, I want to override a specific game's start time and duration in my scheduling email ("Saturday, 2 hours from 9am"), so that one-off games can differ from the defaults without changing global configuration.
6. As an organiser, when I give a fully specified time and duration, I want that game to be fixed regardless of turnout, so that a special game runs exactly as I stated.
7. As an organiser, when my scheduling email mentions only a time or only a duration but not both, I want the system to email me back asking for the missing detail, so that I am never surprised by a half-applied guess.
8. As an organiser, when any game in a multi-game email is ambiguous, I want the whole batch held and nothing scheduled until I resend it cleanly, so that I am never left with a partially-applied request I have to reconcile.
9. As an organiser, I want the clarification email to restate what was understood and what was missing, so that I know exactly what to resend.
10. As an organiser, I want to resend the corrected command as a normal scheduling email, so that I don't have to learn a special reply syntax or thread.
11. As an organiser, I want the turnout threshold that switches between the two tiers to be part of each game's policy, so that I can tune it per game when I override the times.
12. As an organiser, I want the minimum number of players required to play to be part of each game's policy, so that the go/no-go floor travels with the game rather than being a hidden global.
13. As a player, I want the tentative announcement to explain that the start time and duration depend on how many of us sign up, so that I understand why no single time is stated yet.
14. As a player, I want the announcement to show both the well-attended and the thinly-attended scenarios with concrete times, so that I can plan for either outcome.
15. As a player, when a game has a fixed time (because the organiser set one), I want the announcement to show just that one time rather than two confusing branches, so that I'm not misled into thinking it's conditional.
16. As a player, I want the announcement to tell me the minimum number of players needed for the game to happen, so that I understand the game might not go ahead.
17. As a player, once sign-ups close, I want the confirmation email to state the single final start time and duration, so that I know exactly when to show up and for how long.
18. As a player, I want the final confirmed time to never silently change after I'm told it, so that I can rely on the time in my confirmation email even if other people join or drop later.
19. As an organiser, I want each game's confirmed start time and duration recorded on the game, so that the system has a single source of truth for what was decided.
20. As an organiser, I want the reminder and cancellation emails to reflect the game's actual minimum-players figure, so that the messaging is consistent with the game's policy rather than a hardcoded number.
21. As an organiser, I want the default policy values (tier times, durations, threshold, minimum players) to be deployment configuration, so that I can set the league's norms once.
22. As an organiser, I want the per-game policy seeded from those defaults at scheduling time, so that overriding one game never affects the defaults for others.

## Implementation Decisions

**Domain term — game policy.** Introduce a per-game **policy**: the bundle of decision values that govern a single game's timing and viability. It comprises `minPlayers`, a turnout `threshold`, and two **tiers** — `longGame` and `shortGame`, each a `{ startTime, durationHours }` pair. `startTime` is a display-ready string (e.g. "10:00 AM"); `durationHours` is an integer. The policy is stored as one map on the game's `gameStatus` record.

**One game per calendar date is retained.** A game is still uniquely identified by its `gameDate`. Time/duration are payload, never part of the sort key — explicitly rejected putting `gametime` into the SK because there is never more than one game per date, so it would add key length without adding distinguishing power.

**Uniform policy shape; fixed games are equal tiers.** Every game's policy always has the same shape. A "fixed" game is represented by setting `longGame` and `shortGame` to identical values — there is no separate non-tiered schema and no `tiered` flag. Consumers decide presentation by comparing the two tiers for equality rather than branching on shape.

**Policy is seeded from the admin scheduling email, with defaults from config.** The natural-language parser returns, per game, an optional start time and optional duration, each null when the admin did not mention it (the previous behaviour of defaulting an unspecified time to a placeholder value is removed — the parser reports only what was said, defaulting is not its job). The admin-command handler then classifies each game by how many of the two fields are present:
- **Neither present →** valid; seed a default two-tier policy from configuration.
- **Both present →** valid; seed a fixed policy with both tiers equal to the supplied `{ startTime, durationHours }`.
- **Exactly one present →** ambiguous/partial.

**Partial specs hold the whole batch.** If *any* game in a scheduling email is partial, the handler creates **no** games and starts **no** lifecycle executions for that email. Instead it sends a single clarification email that restates each game and the missing field, and asks the admin to resend the complete command. The resend is processed as an ordinary scheduling email — there is no persisted pending state, no threaded reply, and no special reply syntax (rejected a stateful pending-clarification store as disproportionate to a rare typo case).

**Threshold and minimum-players move onto the policy.** The turnout threshold and minimum-players floor are read from the game's policy at announce/confirm time, not from global config. Configuration retains these values only as **seeds** used when creating a game.

**Shared tier resolution.** A single shared function resolves a policy plus a confirmed count to the applicable tier (`longGame` when count ≥ threshold, else `shortGame`). It is used by both the announcement step and the confirmation step so there is one source of truth for the rule.

**Confirmation freezes the decision.** At the go/no-go step, the handler resolves the tier from the confirmed count and **persists** the resulting start time and duration onto the game record. The final confirmation email and the finalize step read these frozen values rather than re-resolving, so the time players were told can never be contradicted by later roster changes. The go/no-go gate uses the policy's minimum-players value.

**Announcement rendering.** The tentative announcement is driven by the game's policy: when the two tiers differ it shows both branches with concrete times and durations; when they are equal it shows a single time and duration line. The previous single fixed-time line is removed. The minimum-players figure shown comes from the policy.

**Configuration changes.** Add deployment configuration seeds for the long-game start/duration and short-game start/duration; retain the existing threshold and minimum-players seeds. Remove the obsolete single global game-time configuration entirely (config field, environment variable, and infrastructure variable), as nothing reads it after this change.

**Dead code removal.** Remove the legacy non-tiered announcement and confirmation email variants, which have no production callers and only referenced the removed global game-time value. Fix the hardcoded minimum-players numbers in the reminder and cancellation emails to use the game's policy value.

**No backward compatibility / migration.** The feature targets a pre-production system with no live game records, so readers may assume the policy block is always present. No defensive fallback accessor and no data migration are in scope.

## Testing Decisions

**What makes a good test here.** Tests assert externally observable behaviour — what records the system writes and what emails it sends in response to an input — not internal helper shapes. The shared tier-resolution rule is exercised *through* the handler seams rather than asserted directly, keeping tests coupled to behaviour rather than implementation.

**Seams under test (confirmed with the developer):**
1. **The admin-command handler** — the primary seam. Given admin email bodies (with the natural-language parser mocked): a fully-specified game writes a fixed policy; an unspecified game writes a default two-tier policy; any partial game in the batch results in no games created, no lifecycle executions started, and a single clarification email describing the missing fields.
2. **The confirm-or-cancel lifecycle task** — given a roster, the resolved tier is frozen onto the game record and the final-confirmation email shows that single time and duration; the go/no-go decision gates on the policy's minimum-players value.
3. **The announce lifecycle task** — equal tiers produce a single-line announcement; differing tiers produce a two-branch announcement with concrete times.

**Modules tested.** The admin-command handler, the announce task, and the confirm-or-cancel task — at their existing entry points.

**Prior art.** Existing unit tests already mock the natural-language parser and the email-send layer and use moto for DynamoDB at these same handler entry points (see the existing game-lifecycle and admin-processor unit tests, and the email-service unit tests). New tests follow that established pattern.

## Out of Scope

- Supporting more than one game on the same calendar date.
- Putting game time or duration into the DynamoDB sort key.
- A stateful, threaded clarification conversation (persisted pending specs, reply markers) — clarification is a stateless resend.
- More than two tiers, or a third tier, or start time and duration moving independently of each other.
- Backward-compatibility fallbacks or migration of existing game records.
- Time-zone handling or arithmetic on the game start time (it is a display string only).
- Changing the Step Functions lifecycle task scheduling/timing (the lifecycle hour offsets are unrelated to the game's tip-off time).
- A web/admin UI for editing policy — policy is set via the admin's email only.

## Further Notes

- The turnout threshold and minimum-players defaults already exist in configuration; this work repurposes them as per-game seeds rather than direct runtime reads.
- The clarification flow reuses the ordinary scheduling path for the resend, so no new intent or parser mode is required beyond reporting unmentioned fields as null.
- Whether the stored map is named `schedule` or `policy` is cosmetic; this PRD uses `policy` because it now also carries the minimum-players floor, not just timing.
