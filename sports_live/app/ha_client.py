from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

log = logging.getLogger(__name__)


class HAClient:
    """Thin async wrapper around the Home Assistant Core REST API,
    reached via the Supervisor proxy at http://supervisor/core/api.
    """

    def __init__(self, supervisor_url: str, supervisor_token: str) -> None:
        self._base = f"{supervisor_url.rstrip('/')}/core/api"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            headers={
                "Authorization": f"Bearer {supervisor_token}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        r = await self._client.get(f"{self._base}/states/{entity_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def list_lights(self) -> list[dict[str, Any]]:
        r = await self._client.get(f"{self._base}/states")
        r.raise_for_status()
        return [s for s in r.json() if s["entity_id"].startswith("light.")]

    async def turn_on(
        self,
        entity_ids: Iterable[str],
        *,
        rgb: tuple[int, int, int] | None = None,
        brightness: int | None = None,
        transition_s: float | None = None,
    ) -> None:
        ids = list(entity_ids)
        if not ids:
            return
        data: dict[str, Any] = {"entity_id": ids}
        if rgb is not None:
            data["rgb_color"] = list(rgb)
        if brightness is not None:
            data["brightness"] = max(0, min(255, brightness))
        if transition_s is not None:
            data["transition"] = max(0.0, transition_s)
        log.debug("light.turn_on %s rgb=%s b=%s t=%s", ids, rgb, brightness, transition_s)
        r = await self._client.post(f"{self._base}/services/light/turn_on", json=data)
        r.raise_for_status()

    async def turn_off(self, entity_ids: Iterable[str], *, transition_s: float | None = None) -> None:
        ids = list(entity_ids)
        if not ids:
            return
        data: dict[str, Any] = {"entity_id": ids}
        if transition_s is not None:
            data["transition"] = max(0.0, transition_s)
        r = await self._client.post(f"{self._base}/services/light/turn_off", json=data)
        r.raise_for_status()

    async def capture_scene(self, entity_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Snapshot the current state of each light so we can restore it later.

        Stores `state` (on/off) and key attributes used by `light.turn_on`.
        """
        out: list[dict[str, Any]] = []
        for eid in entity_ids:
            s = await self.get_state(eid)
            if s is None:
                continue
            attrs = s.get("attributes", {}) or {}
            captured = {
                "entity_id": eid,
                "state": s.get("state"),
                "rgb_color": attrs.get("rgb_color"),
                "brightness": attrs.get("brightness"),
                "color_temp_kelvin": attrs.get("color_temp_kelvin"),
                "color_mode": attrs.get("color_mode"),
            }
            out.append(captured)
        return out

    async def restore_scene(self, captured: list[dict[str, Any]], *, transition_s: float = 0.5) -> None:
        for entry in captured:
            eid = entry["entity_id"]
            if entry["state"] == "off":
                await self.turn_off([eid], transition_s=transition_s)
                continue
            data: dict[str, Any] = {"entity_id": [eid], "transition": transition_s}
            if entry.get("rgb_color"):
                data["rgb_color"] = entry["rgb_color"]
            elif entry.get("color_temp_kelvin"):
                data["color_temp_kelvin"] = entry["color_temp_kelvin"]
            if entry.get("brightness") is not None:
                data["brightness"] = entry["brightness"]
            r = await self._client.post(f"{self._base}/services/light/turn_on", json=data)
            r.raise_for_status()
