# Home Assistant Add-on: Sports Live

Reactive lighting for live football matches — without a Hue Bridge.

## How it works

1. At kickoff, open the **Sports Live** panel in your Home Assistant sidebar.
2. Search for the match and pick the lights you want to use.
3. Adjust the **TV delay** slider so flashes line up with your broadcast.
4. Press **Start**. The add-on monitors the match and triggers light effects on goals, cards, and major events. Ambient color tracks the leading team (warm white on a tie). At full-time, your lights are restored to their pre-match state.

A **Stop** button is always visible — pressing it instantly restores the captured pre-match scene.

## Configuration

| Option | Description |
|---|---|
| `log_level` | Verbosity of add-on logs (`trace` … `fatal`). Default `info`. |
| `poll_interval_idle_s` | Seconds between data-source polls when no match is running. |
| `poll_interval_live_s` | Seconds between polls during live play. |
| `poll_interval_burst_s` | Seconds between polls for ~60s after a goal or card. |
| `default_tv_delay_s` | Pre-fill for the TV-delay slider. |

## Permissions

The add-on uses the **Supervisor token** (`homeassistant_api: true`) to call `light.turn_on` on the entities you select. It does not modify any other Home Assistant state.

## Data source

The add-on uses unofficial Sofascore endpoints to receive low-latency match events. Sofascore does not publish an official public API; this works at the time of writing but has no SLA and may break. The provider layer is pluggable so additional sources can be added.

## Compatible lights

Anything Home Assistant exposes as a `light.*` entity that supports RGB color: Hue (local integration), Zigbee2MQTT, ZHA, ESPHome, MQTT lights, LIFX, etc.
