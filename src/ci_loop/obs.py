"""Observability: structured logging and lightweight run metrics.

Default output is human-readable lines on stdout. Set CI_LOOP_LOG_FORMAT=json
for one JSON object per line (machine-ingestable), including any counters
attached via the `fields` kwarg.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            entry.update(fields)
        return json.dumps(entry)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        fields = getattr(record, "fields", None)
        if fields:
            msg += " " + " ".join(f"{k}={v}" for k, v in fields.items())
        prefix = "" if record.levelno == logging.INFO else f"{record.levelname}: "
        return prefix + msg


def get_logger() -> logging.Logger:
    logger = logging.getLogger("ci_loop")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        if os.environ.get("CI_LOOP_LOG_FORMAT", "").lower() == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(PlainFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log(msg: str, level: int = logging.INFO, **fields) -> None:
    get_logger().log(level, msg, extra={"fields": fields} if fields else None)


class Metrics:
    """Simple counters plus wall-clock duration for one CLI run."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self._start = time.monotonic()

    def inc(self, name: str, n: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + n

    def summary(self) -> dict:
        return {**self.counters,
                "duration_s": round(time.monotonic() - self._start, 1)}

    def emit(self, command: str) -> None:
        log(f"[{command}] summary", **self.summary())
