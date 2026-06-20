"""Provider quota tracking for fcc-claude providers.

Each provider in :data:`config.provider_catalog.PROVIDER_CATALOG` may expose a
quota API. The statusline renders a single ``⏱`` segment per active provider.
Providers without a live quota adapter contribute no segment — the line is
simply absent, not a misleading static placeholder.

Currently wired up:

* ``minimax`` → ``GET https://www.minimax.io/v1/token_plan/remains``
  (5h rolling + weekly windows, see the platform Token Plan docs).
* ``open_router`` → ``GET https://openrouter.ai/api/v1/credits``
  (total credits minus total usage).
* ``codex_chatgpt`` → reads ``~/.codex/auth.json`` and decodes the JWT
  to surface the user's ChatGPT plan + static rate-limit badge.
* ``deepseek`` → ``GET https://api.deepseek.com/user/balance``
  (USD balance split into granted vs topped-up).
* ``openai_dashboard`` → ``GET https://api.openai.com/v1/dashboard/billing/credit_grants``
  (admin key only — regular sk- keys return 403).
* ``mistral`` → ``GET https://api.mistral.ai/v1/usage``
  (cumulative tokens used in current billing period).

All other fcc-claude providers have no public quota API at this time. Add an
adapter below and register it in :data:`QUOTA_PROVIDERS` to enable the
segment.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

CACHE_DIRNAME = "claude-llm-quota-bar"
CACHE_FILENAME = "provider-quota.json"

DEFAULT_CACHE_TTL_SECONDS = 60.0
HTTP_TIMEOUT_SECONDS = 5.0

_FCC_ENV_PATH = Path.home() / ".fcc" / ".env"


@dataclass(frozen=True, slots=True)
class QuotaInfo:
    """Provider-agnostic quota snapshot."""

    provider_id: str
    # ``status_label`` is the short, human-readable badge. Examples:
    # ``"60% livre"``, ``"$8.50 credits"``, ``"free"``, ``"local"``.
    status_label: str
    # ``detail`` is optional extra context that follows the label, e.g. a
    # reset countdown like ``"reset 2h48m"``.
    detail: str | None = None
    # ``used_pct`` drives the line colour (green/yellow/red) when set.
    used_pct: float | None = None
    source: str = "static"  # ``"live"`` | ``"cache"`` | ``"static"`` | ``"error"``
    error: str | None = None
    fetched_at: datetime | None = None


class QuotaProvider(Protocol):
    """Adapter contract — implement one per provider that has a quota API."""

    provider_id: str

    def fetch(
        self,
        api_key: str | None = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        cache_path: Path | None = None,
        now: float | None = None,
    ) -> QuotaInfo:
        ...


# ---------------------------------------------------------------------------
# Cache helpers (shared between adapters)
# ---------------------------------------------------------------------------


def cache_dir() -> Path:
    return Path.home() / ".cache" / CACHE_DIRNAME


def _cache_path() -> Path:
    return cache_dir() / CACHE_FILENAME


def _read_cache(path: Path, ttl_seconds: float) -> dict[str, QuotaInfo] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    fetched_at = raw.get("_fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    if time.time() - fetched_at > ttl_seconds:
        return None
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, dict):
        return None
    parsed: dict[str, QuotaInfo] = {}
    for provider_id, entry in entries_raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            parsed[provider_id] = QuotaInfo(
                provider_id=provider_id,
                status_label=str(entry.get("status_label", "?")),
                detail=(
                    str(entry["detail"])
                    if isinstance(entry.get("detail"), str)
                    else None
                ),
                used_pct=(
                    float(entry["used_pct"])
                    if isinstance(entry.get("used_pct"), (int, float))
                    else None
                ),
                source="cache",
                error=None,
                fetched_at=datetime.fromtimestamp(fetched_at, tz=timezone.utc),
            )
        except (TypeError, ValueError):
            continue
    return parsed


def _write_cache(path: Path, entries: dict[str, dict[str, object]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"_fetched_at": time.time(), "entries": entries},
                default=str,
            ),
            encoding="utf-8",
        )
    except OSError:
        # Cache failure is non-fatal; quota tracking still works without cache.
        return


def _request_json(url: str, headers: dict[str, str]) -> tuple[int, dict[str, object] | str]:
    """GET ``url`` with ``headers``; return ``(status, body)`` where body is a
    parsed JSON dict when possible, otherwise the raw text."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        # ``HTTPError`` is also a file-like; read its body to surface auth/quota errors.
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        return exc.code, raw.decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"network error: {type(exc).__name__}: {exc}"
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# MiniMax — live 5h rolling + weekly windows
# ---------------------------------------------------------------------------


_MINIMAX_URL = "https://www.minimax.io/v1/token_plan/remains"
_MINIMAX_KEY_LINE_RE = __import__("re").compile(
    r"""^\s*MINIMAX_API_KEY\s*=\s*["']?([^"'#\r\n]+)["']?""",
    __import__("re").MULTILINE,
)


def _read_minimax_key_from_fcc_env(path: Path = _FCC_ENV_PATH) -> str | None:
    """Read ``MINIMAX_API_KEY`` from the fcc-claude managed ``~/.fcc/.env``."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _MINIMAX_KEY_LINE_RE.search(text)
    if not match:
        return None
    key = match.group(1).strip()
    return key or None


def _parse_minimax_payload(payload: object, preferred_kind: str = "general") -> QuotaInfo:
    """Parse the MiniMax Token Plan response into a :class:`QuotaInfo`."""
    if not isinstance(payload, dict):
        return QuotaInfo(
            provider_id="minimax",
            status_label="error",
            source="error",
            error="payload not object",
        )

    if "base_resp" in payload and isinstance(payload["base_resp"], dict):
        status = payload["base_resp"].get("status_code", 0)
        if status not in (0, None, "0", "success"):
            return QuotaInfo(
                provider_id="minimax",
                status_label="error",
                source="error",
                error=f"upstream status_code={status}",
            )

    entries_raw = payload.get("model_remains")
    if not isinstance(entries_raw, list) or not entries_raw:
        return QuotaInfo(
            provider_id="minimax",
            status_label="error",
            source="error",
            error="no model_remains in payload",
        )

    chosen: dict[str, object] | None = None
    for entry in entries_raw:
        if isinstance(entry, dict) and entry.get("model_name") == preferred_kind:
            chosen = entry
            break
    if chosen is None:
        for entry in entries_raw:
            if isinstance(entry, dict) and entry.get("model_name") in {
                "general",
                "text",
                "chat",
            }:
                chosen = entry
                break
    if chosen is None:
        for entry in entries_raw:
            if isinstance(entry, dict):
                chosen = entry
                break
    if chosen is None:
        return QuotaInfo(
            provider_id="minimax",
            status_label="error",
            source="error",
            error="no usable model_remains entry",
        )

    used = _as_int(chosen.get("current_interval_usage_count"))
    limit = _as_int(chosen.get("current_interval_total_count"))
    remaining_pct = _as_float(chosen.get("current_interval_remaining_percent"))
    reset_at = _parse_epoch_ms(chosen.get("end_time"))
    ms_until_reset = _as_int(chosen.get("remains_time"))

    label, used_pct = _format_minimax_window(remaining_pct, used, limit)
    detail = _format_minimax_detail(ms_until_reset, reset_at)

    return QuotaInfo(
        provider_id="minimax",
        status_label=label,
        detail=detail,
        used_pct=used_pct,
        source="live",
    )


def _format_minimax_window(
    remaining_pct: float | None,
    used: int | None,
    limit: int | None,
) -> tuple[str, float | None]:
    """Return ``(label, used_pct)`` for a MiniMax window.

    Label shows **used** and **free** percentages (e.g. ``"68% usado
    (32% livre)"``) so the statusline colour rule (green/yellow/red) maps
    intuitively on the used number while still showing how much is left.
    """
    if remaining_pct is not None:
        used_pct = max(0.0, min(100.0, 100.0 - remaining_pct))
        return f"{used_pct:.0f}% usado ({100 - used_pct:.0f}% livre)", used_pct
    if used is not None and limit:
        used_pct = float(used) / float(limit) * 100.0
        return f"{used_pct:.0f}% usado ({100 - used_pct:.0f}% livre)", used_pct
    return "?", None


def _format_minimax_detail(
    ms_until_reset: int | None,
    reset_at: datetime | None,
) -> str | None:
    if ms_until_reset and ms_until_reset > 0:
        seconds = int(ms_until_reset / 1000)
        if seconds < 60:
            countdown = f"{seconds}s"
        elif seconds < 3600:
            countdown = f"{seconds // 60}m"
        else:
            hours, rem = divmod(seconds, 3600)
            countdown = f"{hours}h{rem // 60}m"
        return f"reset {countdown}"
    return None


def _parse_epoch_ms(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


class MinimaxQuotaProvider:
    """Live 5h + weekly quota for MiniMax Token Plan."""

    provider_id = "minimax"

    def fetch(
        self,
        api_key: str | None = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        cache_path: Path | None = None,
        now: float | None = None,
    ) -> QuotaInfo:
        cache_p = cache_path or _cache_path()
        if not api_key:
            api_key = (
                _read_minimax_key_from_fcc_env()
                or os.environ.get("MINIMAX_API_KEY")
            )
        if not api_key:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="MINIMAX_API_KEY not set",
            )

        cached = _read_cache(cache_p, cache_ttl_seconds)
        if cached is not None and self.provider_id in cached:
            return cached[self.provider_id]

        status, body = _request_json(
            _MINIMAX_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        if status != 200:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error=f"upstream HTTP {status}: {str(body)[:120]}",
            )
        if not isinstance(body, dict):
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="payload not object",
            )

        info = _parse_minimax_payload(body)
        if info.source == "live":
            _write_cache(
                cache_p,
                {
                    self.provider_id: {
                        "status_label": info.status_label,
                        "detail": info.detail,
                        "used_pct": info.used_pct,
                        "fetched_at": now or time.time(),
                    },
                },
            )
            return QuotaInfo(
                provider_id=info.provider_id,
                status_label=info.status_label,
                detail=info.detail,
                used_pct=info.used_pct,
                source="live",
                fetched_at=datetime.fromtimestamp(now or time.time(), tz=timezone.utc),
            )
        return info


# ---------------------------------------------------------------------------
# OpenRouter — credits remaining
# ---------------------------------------------------------------------------


_OPENROUTER_URL = "https://openrouter.ai/api/v1/credits"


def _read_openrouter_key_from_fcc_env(path: Path = _FCC_ENV_PATH) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    import re

    match = re.search(
        r"""^\s*OPENROUTER_API_KEY\s*=\s*["']?([^"'#\r\n]+)["']?""",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    key = match.group(1).strip()
    return key or None


def _parse_openrouter_payload(payload: object) -> QuotaInfo:
    """Parse OpenRouter ``GET /api/v1/credits`` response.

    Tolerant of common shapes:

    * ``{"data": {"total_credits": 10, "total_usage": 2.5}}``
    * ``{"total_credits": 10, "total_usage": 2.5}``
    * ``{"data": {"limit": 10, "usage": 2.5}}``
    """
    if not isinstance(payload, dict):
        return QuotaInfo(
            provider_id="open_router",
            status_label="error",
            source="error",
            error="payload not object",
        )

    data: object = payload
    if "data" in payload and isinstance(payload["data"], dict):
        data = payload["data"]
    elif "data" in payload and isinstance(payload["data"], list) and payload["data"]:
        # Some shapes wrap a single dict inside a list.
        first = payload["data"][0]
        if isinstance(first, dict):
            data = first

    if not isinstance(data, dict):
        return QuotaInfo(
            provider_id="open_router",
            status_label="error",
            source="error",
            error="unrecognised credits payload shape",
        )

    total = _as_float(
        data.get("total_credits")
        or data.get("limit")
        or data.get("credit_limit")
        or data.get("balance")
    )
    used = _as_float(
        data.get("total_usage")
        or data.get("usage")
        or data.get("used")
        or data.get("consumed")
    )

    if total is None or used is None:
        return QuotaInfo(
            provider_id="open_router",
            status_label="?",
            source="error",
            error=f"missing credits fields: total={total} used={used}",
        )

    remaining = max(total - used, 0.0)
    used_pct = (used / total * 100.0) if total > 0 else None
    if used_pct is not None:
        # Used + free (mirrors the 🧠 context segment format).
        status_label = f"{used_pct:.0f}% usado ({100 - used_pct:.0f}% livre)"
    else:
        status_label = f"${remaining:.2f} credits"
    return QuotaInfo(
        provider_id="open_router",
        status_label=status_label,
        detail=f"${used:.2f} used of ${total:.2f}",
        used_pct=used_pct,
        source="live",
    )


class OpenRouterQuotaProvider:
    """Live credits remaining for OpenRouter."""

    provider_id = "open_router"

    def fetch(
        self,
        api_key: str | None = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        cache_path: Path | None = None,
        now: float | None = None,
    ) -> QuotaInfo:
        cache_p = cache_path or _cache_path()
        if not api_key:
            api_key = (
                _read_openrouter_key_from_fcc_env()
                or os.environ.get("OPENROUTER_API_KEY")
            )
        if not api_key:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="OPENROUTER_API_KEY not set",
            )

        cached = _read_cache(cache_p, cache_ttl_seconds)
        if cached is not None and self.provider_id in cached:
            return cached[self.provider_id]

        status, body = _request_json(
            _OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        if status != 200:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error=f"upstream HTTP {status}: {str(body)[:120]}",
            )
        if not isinstance(body, dict):
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="payload not object",
            )

        info = _parse_openrouter_payload(body)
        if info.source == "live":
            _write_cache(
                cache_p,
                {
                    self.provider_id: {
                        "status_label": info.status_label,
                        "detail": info.detail,
                        "used_pct": info.used_pct,
                        "fetched_at": now or time.time(),
                    },
                },
            )
            return QuotaInfo(
                provider_id=info.provider_id,
                status_label=info.status_label,
                detail=info.detail,
                used_pct=info.used_pct,
                source="live",
                fetched_at=datetime.fromtimestamp(now or time.time(), tz=timezone.utc),
            )
        return info


# ---------------------------------------------------------------------------
# Codex / ChatGPT — plan + rate limits from JWT in ``~/.codex/auth.json``
# ---------------------------------------------------------------------------


# Source: Codex CLI docs (https://developers.openai.com/codex) and OpenAI
# plan-rate-limit page. Values are best-effort and can change without
# notice — the statusline shows them as informational, not authoritative.
_CODEX_CHATGPT_PLAN_LIMITS: dict[str, tuple[str, str]] = {
    # plan_key -> (display_name, "limit text")
    "free": ("Free", "3 msgs / 40h"),
    "plus": ("Plus", "80 msgs / 3h"),
    "pro": ("Pro", "500 msgs / 3h"),
    "business": ("Business", "100 msgs / 3h"),
    "enterprise": ("Enterprise", "1000 msgs / 3h"),
    "edu": ("Edu", "50 msgs / 3h"),
    "team": ("Team", "100 msgs / 3h"),
}


def _read_codex_auth_file(path: Path = Path.home() / ".codex" / "auth.json") -> dict | None:
    """Read the Codex CLI auth file (``~/.codex/auth.json``).

    Codex persists the OAuth session in this file after a successful
    ``codex login`` flow. The file is JSON with at minimum an
    ``access_token`` field (a JWT). We do not modify the file; we only read
    the id_token claims to surface the user's ChatGPT plan.
    """
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _decode_jwt_payload(jwt: str) -> dict | None:
    """Decode a JWT payload without verifying its signature.

    We only read the ``https://api.openai.com/auth.chatgpt_plan_type`` claim
    for informational display; we never use the result for authorisation.
    JWT format is ``header.payload.signature`` with URL-safe base64 (no
    padding) in the payload segment.
    """
    if not isinstance(jwt, str) or not jwt:
        return None
    parts = jwt.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload_bytes = __import__("base64").urlsafe_b64decode(padded.encode("ascii"))
        return json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _extract_chatgpt_plan(jwt_payload: dict | None) -> str | None:
    """Pull ``chatgpt_plan_type`` out of the ``https://api.openai.com/auth``
    namespace, tolerating multiple shapes Codex has used historically."""
    if not isinstance(jwt_payload, dict):
        return None
    auth_ns = jwt_payload.get("https://api.openai.com/auth")
    if isinstance(auth_ns, dict):
        value = auth_ns.get("chatgpt_plan_type")
        if isinstance(value, str) and value:
            return value
    # Fallback: top-level claim (some Codex builds flatten this).
    value = jwt_payload.get("chatgpt_plan_type")
    if isinstance(value, str) and value:
        return value
    return None


class CodexChatgptQuotaProvider:
    """Plan + rate-limit display for ChatGPT Plus / Pro / Business via Codex.

    The provider reads the OAuth session that ``codex login`` wrote to
    ``~/.codex/auth.json`` and decodes the JWT payload to extract the
    ``chatgpt_plan_type`` claim. We do not call the OpenAI backend and we
    do not attempt to fetch live usage (OpenAI does not expose subscription
    quota via public API). The statusline shows the known limit for the
    detected plan as a static informational badge.

    Fallbacks: ``CODEX_ACCESS_TOKEN`` env var; ``id_token`` field if Codex
    persists the raw JWT there instead of in ``access_token``.
    """

    provider_id = "codex_chatgpt"

    def fetch(
        self,
        api_key: str | None = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        cache_path: Path | None = None,
        now: float | None = None,
    ) -> QuotaInfo:
        cache_p = cache_path or _cache_path()
        cached = _read_cache(cache_p, cache_ttl_seconds)
        if cached is not None and self.provider_id in cached:
            return cached[self.provider_id]

        jwt: str | None = None
        if api_key:
            jwt = api_key
        if not jwt:
            env_token = os.environ.get("CODEX_ACCESS_TOKEN")
            if env_token:
                jwt = env_token
        if not jwt:
            auth_data = _read_codex_auth_file()
            if isinstance(auth_data, dict):
                # Codex auth.json schema: {"access_token": "...", ...}
                # or sometimes {"id_token": "..."} depending on auth flow.
                token = auth_data.get("access_token")
                if isinstance(token, str) and token:
                    jwt = token
                else:
                    token = auth_data.get("id_token")
                    if isinstance(token, str) and token:
                        jwt = token
        if not jwt:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="",
                source="error",
                error=(
                    "Codex auth not found (looked in $CODEX_ACCESS_TOKEN and "
                    "~/.codex/auth.json)"
                ),
            )

        payload = _decode_jwt_payload(jwt)
        plan_key = _extract_chatgpt_plan(payload)
        if not plan_key:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="",
                source="error",
                error="chatgpt_plan_type claim not present in id_token",
            )

        plan_key_lower = plan_key.lower()
        display_name, limit_text = _CODEX_CHATGPT_PLAN_LIMITS.get(
            plan_key_lower,
            (plan_key.title(), "limite desconhecido"),
        )
        info = QuotaInfo(
            provider_id=self.provider_id,
            status_label=f"{display_name} ({limit_text})",
            detail="limite OpenAI pode mudar",
            used_pct=None,
            source="live",
        )
        _write_cache(
            cache_p,
            {
                self.provider_id: {
                    "status_label": info.status_label,
                    "detail": info.detail,
                    "used_pct": info.used_pct,
                    "fetched_at": now or time.time(),
                },
            },
        )
        return QuotaInfo(
            provider_id=info.provider_id,
            status_label=info.status_label,
            detail=info.detail,
            used_pct=info.used_pct,
            source="live",
            fetched_at=datetime.fromtimestamp(now or time.time(), tz=timezone.utc),
        )


# ---------------------------------------------------------------------------
# DeepSeek — USD balance (granted + topped-up)
# ---------------------------------------------------------------------------


_DEEPSEEK_URL = "https://api.deepseek.com/user/balance"


def _read_deepseek_key_from_fcc_env(path: Path = _FCC_ENV_PATH) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    import re

    match = re.search(
        r"""^\s*DEEPSEEK_API_KEY\s*=\s*["']?([^"'#\r\n]+)["']?""",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    key = match.group(1).strip()
    return key or None


def _parse_deepseek_payload(payload: object) -> QuotaInfo:
    """Parse ``GET /user/balance`` response.

    Shape: ``{"balance_infos": [{"currency": "USD", "total_balance": "4.50",
    "granted_balance": "5.00", "topped_up_balance": "0.00"}], "available":
    "4.50", "used": "0.50"}`` (values may be numeric or numeric strings).
    """
    if not isinstance(payload, dict):
        return QuotaInfo(
            provider_id="deepseek",
            status_label="error",
            source="error",
            error="payload not object",
        )
    entries = payload.get("balance_infos")
    if not isinstance(entries, list) or not entries:
        return QuotaInfo(
            provider_id="deepseek",
            status_label="error",
            source="error",
            error="no balance_infos in payload",
        )
    # Prefer USD; fall back to first entry.
    chosen: dict[str, object] | None = None
    for entry in entries:
        if isinstance(entry, dict) and (
            entry.get("currency") == "USD"
            or str(entry.get("currency", "")).upper() == "USD"
        ):
            chosen = entry
            break
    if chosen is None:
        for entry in entries:
            if isinstance(entry, dict):
                chosen = entry
                break
    if chosen is None:
        return QuotaInfo(
            provider_id="deepseek",
            status_label="error",
            source="error",
            error="no usable balance_infos entry",
        )

    total = _as_float(chosen.get("total_balance"))
    granted = _as_float(chosen.get("granted_balance"))
    topped = _as_float(chosen.get("topped_up_balance"))
    currency = str(chosen.get("currency", "USD")).upper() or "USD"

    if total is None:
        # Some responses only expose ``available`` / ``used`` at top level.
        available = _as_float(payload.get("available"))
        if available is not None:
            return QuotaInfo(
                provider_id="deepseek",
                status_label=f"${available:.2f} {currency}",
                detail="balance (sem breakdown)",
                used_pct=None,
                source="live",
            )
        return QuotaInfo(
            provider_id="deepseek",
            status_label="?",
            source="error",
            error="total_balance missing",
        )

    used_pct: float | None = None
    detail: str | None = None
    granted_eff = granted or 0.0
    topped_eff = topped or 0.0
    allocated = granted_eff + topped_eff
    if allocated > 0:
        used = max(allocated - total, 0.0)
        used_pct = used / allocated * 100.0
        if topped_eff > 0:
            detail = (
                f"usou ${used:.2f} de ${granted_eff:.2f} free "
                f"+ ${topped_eff:.2f} topup"
            )
        else:
            detail = f"usou ${used:.2f} de ${granted_eff:.2f} free"

    return QuotaInfo(
        provider_id="deepseek",
        status_label=f"${total:.2f} {currency}",
        detail=detail,
        used_pct=used_pct,
        source="live",
    )


class DeepSeekQuotaProvider:
    """Live USD balance for DeepSeek (``GET /user/balance``)."""

    provider_id = "deepseek"

    def fetch(
        self,
        api_key: str | None = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        cache_path: Path | None = None,
        now: float | None = None,
    ) -> QuotaInfo:
        cache_p = cache_path or _cache_path()
        if not api_key:
            api_key = (
                _read_deepseek_key_from_fcc_env()
                or os.environ.get("DEEPSEEK_API_KEY")
            )
        if not api_key:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="DEEPSEEK_API_KEY not set",
            )

        cached = _read_cache(cache_p, cache_ttl_seconds)
        if cached is not None and self.provider_id in cached:
            return cached[self.provider_id]

        status, body = _request_json(
            _DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        if status != 200:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error=f"upstream HTTP {status}: {str(body)[:120]}",
            )
        if not isinstance(body, dict):
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="payload not object",
            )

        info = _parse_deepseek_payload(body)
        if info.source == "live":
            _write_cache(
                cache_p,
                {
                    self.provider_id: {
                        "status_label": info.status_label,
                        "detail": info.detail,
                        "used_pct": info.used_pct,
                        "fetched_at": now or time.time(),
                    },
                },
            )
            return QuotaInfo(
                provider_id=info.provider_id,
                status_label=info.status_label,
                detail=info.detail,
                used_pct=info.used_pct,
                source="live",
                fetched_at=datetime.fromtimestamp(now or time.time(), tz=timezone.utc),
            )
        return info


# ---------------------------------------------------------------------------
# OpenAI dashboard — credit grants (admin keys only)
# ---------------------------------------------------------------------------


_OPENAI_DASHBOARD_URL = (
    "https://api.openai.com/v1/dashboard/billing/credit_grants"
)


def _read_openai_key_from_fcc_env(path: Path = _FCC_ENV_PATH) -> str | None:
    """Read ``OPENAI_API_KEY`` from ``~/.fcc/.env`` (admin or regular)."""
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    import re

    match = re.search(
        r"""^\s*OPENAI_API_KEY\s*=\s*["']?([^"'#\r\n]+)["']?""",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    key = match.group(1).strip()
    return key or None


def _parse_openai_dashboard_payload(payload: object) -> QuotaInfo:
    """Parse ``GET /v1/dashboard/billing/credit_grants`` response.

    Shape: ``{"object": "credit_summary", "total_granted": 100.0,
    "total_used": 2.5, "total_available": 97.5, "grants_data": {...}}``.
    """
    if not isinstance(payload, dict):
        return QuotaInfo(
            provider_id="openai_dashboard",
            status_label="error",
            source="error",
            error="payload not object",
        )

    total = _as_float(payload.get("total_granted") or payload.get("total_credit"))
    used = _as_float(payload.get("total_used"))
    if total is None or used is None:
        return QuotaInfo(
            provider_id="openai_dashboard",
            status_label="?",
            source="error",
            error=f"missing credit_grants fields: total={total} used={used}",
        )

    available = max(total - used, 0.0)
    used_pct = (used / total * 100.0) if total > 0 else None
    if used_pct is not None:
        # Used + free (mirrors the 🧠 context segment format).
        status_label = f"{used_pct:.0f}% usado ({100 - used_pct:.0f}% livre)"
    else:
        status_label = f"${available:.2f} / ${total:.2f}"
    return QuotaInfo(
        provider_id="openai_dashboard",
        status_label=status_label,
        detail=f"${used:.2f} used of ${total:.2f}",
        used_pct=used_pct,
        source="live",
    )


class OpenAIDashboardQuotaProvider:
    """Live credit-grants for OpenAI dashboard.

    Requires an **admin** OpenAI API key (regular ``sk-`` keys get HTTP 403
    on this endpoint). If a non-admin key is in use the adapter surfaces the
    upstream HTTP 403 in the ``error`` field; the statusline simply omits the
    ``⏱`` segment in that case.
    """

    provider_id = "openai_dashboard"

    def fetch(
        self,
        api_key: str | None = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        cache_path: Path | None = None,
        now: float | None = None,
    ) -> QuotaInfo:
        cache_p = cache_path or _cache_path()
        if not api_key:
            api_key = (
                _read_openai_key_from_fcc_env()
                or os.environ.get("OPENAI_API_KEY")
            )
        if not api_key:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="OPENAI_API_KEY not set",
            )

        cached = _read_cache(cache_p, cache_ttl_seconds)
        if cached is not None and self.provider_id in cached:
            return cached[self.provider_id]

        status, body = _request_json(
            _OPENAI_DASHBOARD_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        if status != 200:
            # 403 on this endpoint means the key is not an admin key.
            # We surface the error and let the statusline omit the segment.
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error=(
                    f"upstream HTTP {status} (admin key required?): "
                    f"{str(body)[:120]}"
                ),
            )
        if not isinstance(body, dict):
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="payload not object",
            )

        info = _parse_openai_dashboard_payload(body)
        if info.source == "live":
            _write_cache(
                cache_p,
                {
                    self.provider_id: {
                        "status_label": info.status_label,
                        "detail": info.detail,
                        "used_pct": info.used_pct,
                        "fetched_at": now or time.time(),
                    },
                },
            )
            return QuotaInfo(
                provider_id=info.provider_id,
                status_label=info.status_label,
                detail=info.detail,
                used_pct=info.used_pct,
                source="live",
                fetched_at=datetime.fromtimestamp(now or time.time(), tz=timezone.utc),
            )
        return info


# ---------------------------------------------------------------------------
# Mistral — cumulative usage in current billing period
# ---------------------------------------------------------------------------


_MISTRAL_URL = "https://api.mistral.ai/v1/usage"


def _read_mistral_key_from_fcc_env(path: Path = _FCC_ENV_PATH) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    import re

    match = re.search(
        r"""^\s*MISTRAL_API_KEY\s*=\s*["']?([^"'#\r\n]+)["']?""",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    key = match.group(1).strip()
    return key or None


def _parse_mistral_payload(payload: object) -> QuotaInfo:
    """Parse ``GET /v1/usage`` response.

    Shape: ``{"object": "list", "data": [{"timestamp": "...",
    "model": "mistral-large-latest", "prompt_tokens": 1234,
    "completion_tokens": 567, "total_tokens": 1801}, ...],
    "object": "usage"}`` — we sum ``total_tokens`` across the period.
    """
    if not isinstance(payload, dict):
        return QuotaInfo(
            provider_id="mistral",
            status_label="error",
            source="error",
            error="payload not object",
        )
    data = payload.get("data")
    if not isinstance(data, list):
        return QuotaInfo(
            provider_id="mistral",
            status_label="error",
            source="error",
            error="no data array in usage payload",
        )

    total_tokens = 0
    models_seen: set[str] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        tok = _as_int(entry.get("total_tokens"))
        if tok is not None:
            total_tokens += tok
        model = entry.get("model")
        if isinstance(model, str) and model:
            models_seen.add(model)

    if not data:
        return QuotaInfo(
            provider_id="mistral",
            status_label="0 tokens",
            detail="sem uso no período",
            used_pct=None,
            source="live",
        )

    if total_tokens >= 1_000_000:
        label = f"{total_tokens / 1_000_000:.1f}M tokens"
    elif total_tokens >= 1_000:
        label = f"{total_tokens / 1_000:.1f}k tokens"
    else:
        label = f"{total_tokens} tokens"

    detail: str | None = None
    if models_seen:
        # Cap to 3 most-likely-interesting model names.
        detail = "modelos: " + ", ".join(sorted(models_seen)[:3])
        if len(models_seen) > 3:
            detail += f", +{len(models_seen) - 3}"

    return QuotaInfo(
        provider_id="mistral",
        status_label=label,
        detail=detail,
        used_pct=None,
        source="live",
    )


class MistralQuotaProvider:
    """Live cumulative usage for Mistral (``GET /v1/usage``)."""

    provider_id = "mistral"

    def fetch(
        self,
        api_key: str | None = None,
        *,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        cache_path: Path | None = None,
        now: float | None = None,
    ) -> QuotaInfo:
        cache_p = cache_path or _cache_path()
        if not api_key:
            api_key = (
                _read_mistral_key_from_fcc_env()
                or os.environ.get("MISTRAL_API_KEY")
            )
        if not api_key:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="MISTRAL_API_KEY not set",
            )

        cached = _read_cache(cache_p, cache_ttl_seconds)
        if cached is not None and self.provider_id in cached:
            return cached[self.provider_id]

        status, body = _request_json(
            _MISTRAL_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        if status != 200:
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error=f"upstream HTTP {status}: {str(body)[:120]}",
            )
        if not isinstance(body, dict):
            return QuotaInfo(
                provider_id=self.provider_id,
                status_label="error",
                source="error",
                error="payload not object",
            )

        info = _parse_mistral_payload(body)
        if info.source == "live":
            _write_cache(
                cache_p,
                {
                    self.provider_id: {
                        "status_label": info.status_label,
                        "detail": info.detail,
                        "used_pct": info.used_pct,
                        "fetched_at": now or time.time(),
                    },
                },
            )
            return QuotaInfo(
                provider_id=info.provider_id,
                status_label=info.status_label,
                detail=info.detail,
                used_pct=info.used_pct,
                source="live",
                fetched_at=datetime.fromtimestamp(now or time.time(), tz=timezone.utc),
            )
        return info


# ---------------------------------------------------------------------------
# Registry — only providers with live quota APIs are listed.
# All other fcc-claude providers return ``None`` from
# :func:`get_quota_for_provider` and the statusline omits the ``⏱`` segment.
# ---------------------------------------------------------------------------


QUOTA_PROVIDERS: dict[str, QuotaProvider] = {
    "minimax": MinimaxQuotaProvider(),
    "open_router": OpenRouterQuotaProvider(),
    "codex_chatgpt": CodexChatgptQuotaProvider(),
    "deepseek": DeepSeekQuotaProvider(),
    "openai_dashboard": OpenAIDashboardQuotaProvider(),
    "mistral": MistralQuotaProvider(),
}


def get_quota_for_provider(provider_id: str | None) -> QuotaProvider | None:
    """Return the live quota adapter for ``provider_id``, or ``None`` if the
    provider has no quota API wired up in this version of the statusline.

    Aliases:
      * ``codestral`` → ``mistral`` (the fcc-claude ``codestral`` gateway hits
        the same Mistral backend, so the Mistral ``/v1/usage`` endpoint
        reports consumption for both).
    """
    if not provider_id:
        return None
    if provider_id == "codestral":
        provider_id = "mistral"
    return QUOTA_PROVIDERS.get(provider_id)


def fetch_quota(
    provider_id: str | None,
    api_key: str | None = None,
    **kwargs: object,
) -> QuotaInfo | None:
    """Convenience entry: look up the adapter for ``provider_id`` and fetch.

    Returns ``None`` when no adapter exists for the given provider. Callers
    should treat ``None`` as "show nothing for the quota segment".
    """
    adapter = get_quota_for_provider(provider_id)
    if adapter is None:
        return None
    return adapter.fetch(api_key, **kwargs)
