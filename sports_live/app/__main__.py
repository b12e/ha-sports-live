from __future__ import annotations

import logging

import uvicorn

from . import __version__
from .api.server import create_app
from .settings import load

_LOG_LEVEL_MAP = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}


def main() -> None:
    settings = load()

    logging.basicConfig(
        level=_LOG_LEVEL_MAP.get(settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("sports_live")
    log.info("Sports Live v%s starting on :%d", __version__, settings.ingress_port)

    app = create_app(settings)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.ingress_port,
        log_level=("debug" if settings.log_level in ("trace", "debug") else "info"),
        access_log=False,
    )


if __name__ == "__main__":
    main()
