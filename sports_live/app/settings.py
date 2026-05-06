from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

OPTIONS_PATH = Path("/data/options.json")
DATA_DIR = Path("/data")

LogLevel = Literal["trace", "debug", "info", "notice", "warning", "error", "fatal"]


class Settings(BaseModel):
    log_level: LogLevel = "info"
    poll_interval_idle_s: int = Field(10, ge=5, le=60)
    poll_interval_live_s: int = Field(8, ge=2, le=30)
    poll_interval_burst_s: int = Field(2, ge=1, le=10)
    default_tv_delay_s: int = Field(0, ge=0, le=60)

    supervisor_token: str = ""
    supervisor_url: str = "http://supervisor"
    ingress_port: int = 8099


def load() -> Settings:
    raw: dict = {}
    if OPTIONS_PATH.exists():
        try:
            raw = json.loads(OPTIONS_PATH.read_text())
        except json.JSONDecodeError:
            raw = {}
    raw["supervisor_token"] = os.environ.get("SUPERVISOR_TOKEN", "")
    return Settings(**raw)
