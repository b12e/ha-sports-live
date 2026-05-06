from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Position = Literal["left", "right", "both"]
PhysicalSide = Literal["left", "right"]


@dataclass
class LightSlot:
    """A light entity plus its physical position relative to the TV."""
    entity_id: str
    position: Position = "both"


def opposite(side: PhysicalSide) -> PhysicalSide:
    return "right" if side == "left" else "left"
