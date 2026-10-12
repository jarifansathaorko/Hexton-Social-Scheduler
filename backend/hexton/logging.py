"""Process-wide logging.

Configured once at startup by the composition root. Uses the standard library: a
local single-user application does not need a logging stack, and every extra
dependency in the secret path is one more thing to audit.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(*, log_dir: Path, level: int = logging.INFO, to_stderr: bool = True) -> None:
    """Log to a rotating file in the data directory and, optionally, to stderr.

    The file handler is the durable one: when a job goes wrong at 03:00 while the
    machine was waking from sleep, the file is what an operator can still read.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )

    file_handler = RotatingFileHandler(
        log_dir / "hexton.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if to_stderr:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # The HTTP access log is noise at INFO in a local app.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
