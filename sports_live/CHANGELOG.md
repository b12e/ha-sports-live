# Changelog

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
