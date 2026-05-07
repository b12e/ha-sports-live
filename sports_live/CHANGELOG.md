# Changelog

## 0.1.5 (2026-05-07)

- **Multi-source aggregation: Sofascore + ESPN.** When you start a match,
  the addon now also tries to find the same fixture on ESPN and races
  the two sources. Whichever feed sees a goal/card/VAR first wins;
  duplicates from the slower source are silently dropped (matched on
  `kind + side + minute±1` within a 60s window). When Sofascore's
  upstream is laggy — as it was during recent Champions League knockouts
  — ESPN often beats it; when ESPN lags, Sofascore wins. The fastest of
  the two per-event reaches your lights.
- ESPN match lookup walks a curated set of league slugs (FIFA WC + WC
  qualifiers, UEFA Champions / Europa / Conference / Euro / Nations,
  Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie,
  Primeira Liga, Belgian Pro League). If no ESPN counterpart is found
  the addon falls back to Sofascore-only — same behavior as 0.1.4.
- Six unit tests cover the dedup window: same kind+side+minute drops the
  late one, same kind with minute±1 dedupes, different sides or kinds
  don't dedupe, the merge interleaves correctly, and either source can
  win.

## 0.1.4 (2026-05-07)

- Drop the **Mock (manual events)** and **Replay (JSONL file)** provider
  options from the UI dropdown — they were dev-only and confusing
  alongside the real options. The mock-injectors panel is gone too.
- Rename **Sofascore (live)** → **Sofascore**.
- API surface trimmed to match: `StartReq.provider` accepts only
  `sofascore` or `sofascore_replay`, `replay_path` is gone, the
  `POST /api/debug/inject` route is removed. The internal
  `MockProvider` / `ReplayProvider` classes stay for tests.

## 0.1.3 (2026-05-06)

- **Filter minor competitions out of the live picker.** Regional 4th-tier
  leagues, U17/U19/U21 youth tournaments, reserve squads, academy
  fixtures and small island cups no longer dominate the list. Filter
  combines Sofascore's `tournament.priority` (≥250) with a
  youth/reserve/academy regex on the tournament name, so the list now
  surfaces top continental + national leagues plus international
  tournaments.

## 0.1.2 (2026-05-06)

- **Live & upcoming match picker.** The Sofascore (live) provider now
  opens onto a visual list of currently-live football matches plus any
  kicking off in the next ~4 hours, instead of a search box. Each card
  shows the competition, both team names with color dots, the current
  score (live) or kickoff time + countdown (upcoming), and a status
  pill (LIVE / HT / ET / PEN). Click to pick. Auto-refreshes every 30s.
  "Search instead" still surfaces the old search box for finishing /
  lower-tier matches.
- New `GET /api/match/live` endpoint backs the picker.
- Drop `armv7` from the build matrix; `armv7` users can still install
  by adding the repo and letting HA build locally.
- Drop the unused `default_competition` add-on option (never read
  anywhere; the live picker replaces it).

## 0.1.1 (2026-05-06)

First end-to-end-tested release. Bug fixes and resilience improvements
on top of 0.1.0's scaffolding.

- Sofascore: `/incidents` was being skipped on every iteration where
  `/event` returned `304 Not Modified`, so live cards/goals could be
  missed silently. The loop now caches the last summary across 304s
  and an unconditional catch-up call seeds the seen-set so subscribing
  mid-match doesn't replay every past event as a flash.
- Effects: non-RGB lights now flash via brightness pulses instead of
  being ignored. The orchestrator probes each light's
  `supported_color_modes` at start and the runtime splits each step
  into rgb + brightness calls.
- Ambient repaint: edge-triggered cache no longer leaves bulbs stuck
  on an effect's final color when ambient is logically unchanged
  (yellow card, red card, repeat goals). After every event-driven
  effect we force a re-paint.
- Ambient race fix: `_handle_event` now awaits the event-effect task
  before re-asserting ambient, so ambient (priority 0) isn't dropped
  by the priority gate while a higher effect still holds the lights.
- Side mapping: lights tag as left/right/both relative to the TV.
  Goal/card/penalty/big-chance flashes target the benefiting team's
  current side; OWN_GOAL credits the opposite side. Sides auto-swap
  at the second-half kickoff (toggle to opt out) and a manual swap
  button is always available.
- Test flash: "Test home goal" / "Test away goal" buttons in the live
  panel run a real GOAL effect for verification without waiting for a
  match event.
- Light config persistence: previous light selection, positions,
  home-side and TV-delay restore automatically on UI load.
- Replay flow: paste a Sofascore event ID, preview the parsed
  incidents, run a finished match through the full pipeline at
  1×–60× speed for testing without a live broadcast.
- Dispatch loop: a single bad event no longer takes down the
  orchestrator — handler exceptions are logged and the loop keeps
  draining.
- Stale-queue guard: `start()` clears any leftover queue items from a
  prior session.
- CI: hadolint workflow ignores `DL3006` (base image is parameterized
  by HA builder).

## 0.1.0

Initial scaffolding: addon manifest, ingress UI shell, health check.
