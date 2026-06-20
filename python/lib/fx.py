"""Fetch USD->BRL exchange rate from a free public API with disk caching.

Uses AwesomeAPI by default (Brazilian, free, no auth). Caches the rate in
``~/.cache/claude-llm-quota-bar/fx.json`` for ``cache_ttl_seconds`` (default
1 hour) so we do not hit the network on every statusline refresh.

Falls back to ``fallback_rate`` from :mod:`pricing` if the network call fails.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_API_URL = "https://economia.awesomeapi.com.br/last/USD-BRL"
DEFAULT_TTL_SECONDS = 3600
DEFAULT_TIMEOUT_SECONDS = 2.0
CACHE_DIRNAME = "claude-llm-quota-bar"
CACHE_FILENAME = "fx.json"


@dataclass(frozen=True, slots=True)
class FxRate:
    """Resolved USD->BRL exchange rate."""

    rate: float
    source: str  # "cache", "live", or "fallback"
    fetched_at: float  # unix timestamp
    age_seconds: float  # how stale the rate is

    @property
    def is_stale(self) -> bool:
        return self.age_seconds > DEFAULT_TTL_SECONDS * 4


def _default_cache_dir() -> Path:
    if xdg := os.environ.get("XDG_CACHE_HOME"):
        return Path(xdg) / CACHE_DIRNAME
    return Path.home() / ".cache" / CACHE_DIRNAME


def _cache_path(directory: Path) -> Path:
    return directory / CACHE_FILENAME


def load_cached_rate(directory: Path) -> FxRate | None:
    """Read the cached rate from disk. Returns ``None`` if missing or invalid."""
    path = _cache_path(directory)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rate = raw.get("rate")
    fetched = raw.get("fetched_at")
    if not isinstance(rate, (int, float)) or not isinstance(fetched, (int, float)):
        return None
    return FxRate(
        rate=float(rate),
        source="cache",
        fetched_at=float(fetched),
        age_seconds=max(0.0, time.time() - float(fetched)),
    )


def _write_cache(directory: Path, rate: float) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"rate": rate, "fetched_at": time.time()}
        _cache_path(directory).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        # Cache write failure is non-fatal; statusline still works without cache.
        pass


def _fetch_live(api_url: str, timeout: float) -> float | None:
    """Call the FX API and return the USD->BRL rate, or ``None`` on failure."""
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "claude-llm-quota-bar/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    usd_brl = data.get("USDBRL")
    if not isinstance(usd_brl, dict):
        return None
    bid = usd_brl.get("bid")
    if not isinstance(bid, (str, int, float)):
        return None
    try:
        rate = float(bid)
    except (TypeError, ValueError):
        return None
    if rate <= 0 or rate > 1000:
        # Sanity bound; AwesomeAPI bid is always in 1-50 range for BRL.
        return None
    return rate


def resolve_rate(
    *,
    fallback_rate: float,
    cache_dir: Path | None = None,
    api_url: str = DEFAULT_API_URL,
    cache_ttl_seconds: float = DEFAULT_TTL_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> FxRate:
    """Return the best available USD->BRL rate, preferring live > cache > fallback."""
    directory = cache_dir or _default_cache_dir()
    cached = load_cached_rate(directory)
    if cached and cached.age_seconds < cache_ttl_seconds:
        return cached

    live = _fetch_live(api_url, timeout=timeout)
    if live is not None:
        _write_cache(directory, live)
        return FxRate(rate=live, source="live", fetched_at=time.time(), age_seconds=0.0)

    if cached is not None:
        return cached

    return FxRate(
        rate=fallback_rate,
        source="fallback",
        fetched_at=time.time(),
        age_seconds=0.0,
    )
