"""Logging that reads well both under systemd and on a terminal.

Under the unit, stderr is the journal, so plain lines are right. Interactively
we keep the same format -- there is no second format to get out of sync.
"""

from __future__ import annotations

import logging
import os
import sys

from . import const

_READY = False


def setup(verbose: bool = False) -> None:
    global _READY
    if _READY:
        return
    level = logging.DEBUG if (verbose or os.environ.get("ADBSHOT_DEBUG")) else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    # journald already stamps the time; a bare level+message avoids doubling it.
    fmt = "%(levelname)s %(name)s: %(message)s"
    if sys.stderr.isatty():
        fmt = "%(asctime)s " + fmt
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    root = logging.getLogger(const.APP_NAME)
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _READY = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"{const.APP_NAME}.{name}")
