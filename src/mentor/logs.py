"""Logging for the app and the worker (M10): one line per event on stdout, where Docker collects
(and rotates) it. Our own loggers and Procrastinate's log at `LOG_LEVEL`; other libraries only
from WARNING, since some log request URLs or payloads at INFO. Uvicorn keeps its own handlers
(access log included). What may be logged at all is D58: never model output or user text."""

import logging
import sys

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
OURS = ("mentor", "procrastinate")


def configure(level: str) -> None:
    logging.basicConfig(level=logging.WARNING, format=FORMAT, stream=sys.stdout)
    for name in OURS:
        logging.getLogger(name).setLevel(level)
