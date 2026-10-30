from dataclasses import dataclass, field
from typing import Any, Dict

from logging_config import suffix_scope, LoggersWrapper


@dataclass
class Resources:
    tm: Any
    layout: Any
    args: Any
    _cache: Dict[str, Any] = field(default_factory=dict)
    suffix = " (task_runtime:Resources)"
    loggers: LoggersWrapper | None = None

    def has(self, key) -> bool:
        return key in self._cache

    def get(self, key):
        if key in self._cache:
            return self._cache[key]
        else:
            with suffix_scope(self.suffix):
                loggers.raise_error(f"Uncached key \"{key}\"", KeyError)
            raise  # unreachable; for IDE inspection only.

    def set(self, key, value):
        with suffix_scope(self.suffix):
            if key in self._cache:
                self._cache[key] = value
                loggers.warning(f"Updated existing cache with key \"{key}\".")
            else:
                self._cache[key] = value
                loggers.info(f"Cached with new key \"{key}\".")