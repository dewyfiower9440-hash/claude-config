"""A logger whose module is imported when something is logged.

The hooks log on failure paths only, and importing `logging` costs about 15 ms of every
hook run (a hook is a fresh process each time). `LazyLog("name")` stands where
`logging.getLogger("name")` stood: any attribute used on it (`warning`, `exception`, ...)
imports logging then and is the real logger's.
"""

from __future__ import annotations

from typing import Any


class LazyLog:
    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        import logging

        return getattr(logging.getLogger(self._name), attr)
