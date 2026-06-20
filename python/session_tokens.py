#!/usr/bin/env python3
"""Claude Code statusline entry point.

Reads the Claude Code session JSONL, aggregates token usage, computes cost
using a price table, and prints a one-line status to stdout. Designed to be
fast (<50ms) because Claude Code refreshes it periodically.

Environment variables (set by Claude Code):
- ``CLAUDE_PROJECT_DIR``: absolute path of the cwd of the Claude Code session.
- ``CLAUDE_SESSION_ID``: unique session id used to locate the JSONL file.
- ``CLAUDE_MODEL`` (optional): the active model id.

Stdin (Claude Code >=2.1): JSON object with current model + cost context.
This script does not depend on stdin to work; it is treated as a hint.

Configuration: see ``pricing.json`` next to this file and
``statusline.env.json`` (optional) for display toggles.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

from lib.display import ContextInfo, DisplayOptions, render  # noqa: E402
from lib.fx import DEFAULT_TTL_SECONDS, resolve_rate  # noqa: E402
from lib.parser import (  # noqa: E402
    TokenTotals,
    _provider_from_model,
    _strip_gateway_prefix,
    locate_latest_log,
    locate_session_log,
    parse_first_response_model,
)
from lib.provider_quota import fetch_quota  # noqa: E402
from lib.pricing import (  # noqa: E402
    CostBreakdown,
    ModelPrice,
    compute_cost,
    load_pricing_table,
)

DEFAULT_CLAUDE_DIR = Path.home() / ".claude"
PLACEHOLDER = "[statusline: inicializando]"


def _load_display_options(config_path: Path) -> DisplayOptions:
    """Load display toggles from ``statusline.env.json`` if present."""
    if not config_path.exists():
        return DisplayOptions()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DisplayOptions()
    allowed = {f for f in DisplayOptions.__dataclass_fields__}
    kwargs = {k: v for k, v in raw.items() if k in allowed}
    return DisplayOptions(**kwargs)


def _read_stdin() -> dict[str, Any]:
    """Read Claude Code's stdin JSON hint, returning ``{}`` on failure."""
    try:
        payload = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {}


def _safe_log_path(claude_dir: Path, project_dir: str, session_id: str) -> tuple[Path | None, str | None]:
    """Locate the JSONL path for the given session, falling back if needed."""
    path = locate_session_log(claude_dir, project_dir, session_id)
    if path is not None:
        return path, None
    fallback = locate_latest_log(claude_dir, project_dir)
    if fallback is None:
        return None, None
    fallback_model = parse_first_response_model(fallback)
    return fallback, fallback_model


# Statusline parses the JSONL on every refresh. To avoid re-reading a
# multi-MB file every 5 s (which can briefly blank the TUI), we memoize
# the result keyed on (path, mtime, size) for a short window. Claude Code
# only writes appendFileSync, so the file is append-only — the mtime/size
# tuple uniquely identifies the content of the tail we already parsed.
_AGGREGATE_CACHE: dict[tuple[str, int, int], tuple[float, "TokenTotals"]] = {}
_AGGREGATE_CACHE_TTL_SECONDS = 2.0


def _aggregate_cached(jsonl_path: Path, session_id: str | None) -> "TokenTotals":
    """Return aggregate_session(jsonl_path, session_id), cached for ~2 s.

    Cache key is (path, mtime_ns, size). When Claude Code appends a new
    assistant entry, the size grows and we re-parse only the tail. The
    2 s window aligns with the Claude Code statusline refresh interval
    (5 s) so consecutive refreshes reuse the parsed result.
    """
    from lib.parser import aggregate_session  # local import to avoid cycle

    try:
        st = jsonl_path.stat()
    except OSError:
        return TokenTotals()
    key = (str(jsonl_path), st.st_mtime_ns, st.st_size)
    now = time.monotonic()
    cached = _AGGREGATE_CACHE.get(key)
    if cached is not None:
        ts, totals = cached
        if now - ts < _AGGREGATE_CACHE_TTL_SECONDS:
            return totals
        # Stale entry — drop it and refetch.
        _AGGREGATE_CACHE.pop(key, None)
    # Opportunistic cache prune so the dict does not grow unbounded across
    # very long sessions that touch many JSONL files.
    for k, (ts, _) in list(_AGGREGATE_CACHE.items()):
        if now - ts > _AGGREGATE_CACHE_TTL_SECONDS * 4:
            _AGGREGATE_CACHE.pop(k, None)
    totals = aggregate_session(jsonl_path, session_id or "")
    _AGGREGATE_CACHE[key] = (now, totals)
    return totals


def _build_context_info(
    stdin_hint: dict[str, Any],
    project_dir_fallback: str,
) -> ContextInfo:
    """Build :class:`ContextInfo` from Claude Code's stdin payload.

    Falls back to ``CLAUDE_PROJECT_DIR`` for ``cwd`` when the stdin hint does
    not include ``workspace.current_dir``. The cc_version is sourced from
    ``version`` (no subprocess call needed).
    """
    cwd: str | None = None
    workspace = stdin_hint.get("workspace") if isinstance(stdin_hint, dict) else None
    if isinstance(workspace, dict):
        cwd = workspace.get("current_dir") or workspace.get("project_dir")
    if not cwd and isinstance(stdin_hint, dict):
        cwd = stdin_hint.get("cwd")
    if not cwd:
        cwd = project_dir_fallback or None

    cc_version: str | None = None
    if isinstance(stdin_hint, dict):
        version_field = stdin_hint.get("version")
        if isinstance(version_field, str) and version_field:
            cc_version = version_field

    context_used_pct: int | None = None
    if isinstance(stdin_hint, dict):
        context_window = stdin_hint.get("context_window")
        if isinstance(context_window, dict):
            pct = context_window.get("used_percentage")
            if isinstance(pct, (int, float)):
                context_used_pct = int(round(float(pct)))

    session_duration_ms: int | None = None
    if isinstance(stdin_hint, dict):
        cost_field = stdin_hint.get("cost")
        if isinstance(cost_field, dict):
            raw_ms = cost_field.get("total_duration_ms")
            if isinstance(raw_ms, (int, float)) and raw_ms >= 0:
                session_duration_ms = int(raw_ms)

    return ContextInfo(
        cwd=cwd,
        cc_version=cc_version,
        context_used_pct=context_used_pct,
        session_duration_ms=session_duration_ms,
    )


def _active_quota_provider(
    totals: TokenTotals,
    fallback_model: str | None,
    std_model: str | None,
    price: ModelPrice | None,
) -> str | None:
    """Return the provider_id that has a live quota adapter, or ``None``.

    Detection order:
      1. Provider prefix parsed from the model id (``openrouter/...``,
         ``deepseek/...``, ``mistral/...``, or bare ``MiniMax-M3``).
      2. The ``provider`` field on the resolved pricing entry (covers bare
         model names like ``deepseek-v4-flash`` that resolve to
         ``provider="deepseek"`` via ``pricing.json``).
      3. **Heuristic:** if the model id looks like a Codex model (``gpt-5``,
         ``gpt-5-codex``, ``o3``, ``o4-mini``, etc.) AND ``~/.codex/auth.json``
         is present (or ``$CODEX_ACCESS_TOKEN`` is set), return
         ``codex_chatgpt`` so the plan badge shows.
      4. **Heuristic:** same Codex-shaped model id but the Codex session is
         NOT active → if ``$OPENAI_API_KEY`` is set, return
         ``openai_dashboard`` so the credit-grants segment shows (admin
         keys only — non-admin keys surface an error and the segment
         is omitted).

    Returns ``None`` for providers without a wired-up adapter (the statusline
    omits the ``⏱`` segment entirely in that case).
    """
    from lib.provider_quota import get_quota_for_provider

    candidate = totals.last_model or fallback_model or std_model
    provider_id: str | None = None
    if candidate:
        derived = _provider_from_model(candidate)
        if derived not in {"anthropic", "unknown"} and get_quota_for_provider(derived):
            # Normalize aliases (e.g. ``codestral`` → ``mistral``).
            provider_id = _normalize_quota_provider_id(derived)
    if provider_id is None and price is not None:
        adapter = get_quota_for_provider(price.provider)
        if adapter is not None:
            provider_id = _normalize_quota_provider_id(price.provider)
    # Bare model id (e.g. ``deepseek-v4-pro``, ``mistral-large-latest``) often
    # resolves to a gateway (``opencode_go``, ``opencode``) in pricing.json —
    # even though the *direct* upstream is DeepSeek or Mistral. Detect the
    # family from the id prefix and re-route to the direct provider.
    if provider_id is None and candidate:
        direct = _direct_provider_for_bare_model(candidate)
        if direct is not None and get_quota_for_provider(direct):
            provider_id = _normalize_quota_provider_id(direct)
    if provider_id is None and _looks_like_codex_model(candidate):
        if _codex_session_active() and get_quota_for_provider("codex_chatgpt"):
            provider_id = "codex_chatgpt"
        elif (
            os.environ.get("OPENAI_API_KEY")
            and get_quota_for_provider("openai_dashboard")
        ):
            provider_id = "openai_dashboard"
    return provider_id


# Direct provider families whose quota API is reachable when the user uses
# the model name *without* a gateway prefix. ``provider`` is the canonical
# name registered in ``QUOTA_PROVIDERS``.
_DIRECT_PROVIDER_MODEL_FAMILIES: tuple[tuple[str, str], ...] = (
    # family_prefix -> provider_id
    ("deepseek-", "deepseek"),
    ("deepseek/", "deepseek"),  # legacy / defensive
    ("mistral-", "mistral"),
    ("mistral/", "mistral"),
    ("codestral-", "mistral"),  # codestral hits the Mistral backend
    ("codestral/", "mistral"),
)


def _direct_provider_for_bare_model(model_id: str) -> str | None:
    """Return the direct-upstream provider for a bare model id, or ``None``.

    The statusline uses this when the pricing entry routes the bare model
    through a gateway (e.g. ``opencode_go``) that has no quota API, while
    the *direct* upstream (DeepSeek, Mistral) does. The match is intentionally
    conservative: only well-known model families are re-routed.
    """
    if not model_id or "/" in model_id:
        # If the model already has a gateway prefix, ``_provider_from_model``
        # has already extracted the correct provider in the caller.
        return None
    model_lower = model_id.lower()
    for prefix, provider_id in _DIRECT_PROVIDER_MODEL_FAMILIES:
        if model_lower.startswith(prefix):
            return provider_id
    return None


def _normalize_quota_provider_id(provider_id: str) -> str:
    """Normalize a provider_id to its canonical quota registry name.

    Currently the only alias is ``codestral`` → ``mistral`` (the fcc-claude
    ``codestral`` gateway hits the same Mistral backend, so its usage is
    surfaced through the Mistral ``/v1/usage`` endpoint). Other ids pass
    through unchanged.
    """
    if provider_id == "codestral":
        return "mistral"
    return provider_id


_CODEX_MODEL_PREFIXES = ("gpt-5", "gpt-4", "o1", "o3", "o4", "o5")
# Codex may also serve bare or hyphenated variants of the form ``gpt-4o``,
# ``gpt-3.5-turbo`` etc. — match anything that starts with ``gpt-`` since all
# OpenAI GPT models are routable through the Codex backend today.
_CODEX_MODEL_FAMILY_PREFIXES = ("gpt-",)


def _looks_like_codex_model(model_id: str | None) -> bool:
    """True if ``model_id`` looks like a model the Codex CLI can serve.

    Recognised shapes: ``gpt-5``, ``gpt-5-codex``, ``gpt-5-mini``, ``gpt-4o``,
    ``gpt-3.5-turbo``, ``o1``, ``o3``, ``o4-mini``, and the bare ``o5`` etc.
    """
    if not model_id:
        return False
    model_lower = model_id.lower()
    if any(
        model_lower.startswith(prefix)
        for prefix in _CODEX_MODEL_FAMILY_PREFIXES
    ):
        return True
    return any(
        model_lower == prefix or model_lower.startswith(prefix + "-")
        for prefix in _CODEX_MODEL_PREFIXES
    )


def _codex_session_active() -> bool:
    """True if the Codex CLI has a usable auth file or env token available.

    An empty ``auth.json`` (``{}``) is treated as *inactive* — only files that
    contain a non-empty ``access_token`` (or ``id_token``) count. This avoids
    surfacing a broken CodexChatgptQuotaProvider when the user created an
    empty placeholder file for some other reason.
    """
    if os.environ.get("CODEX_ACCESS_TOKEN"):
        return True
    codex_home = Path.home() / ".codex"
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        return False
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    for key in ("access_token", "id_token"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _price_for_model(
    model_id: str | None,
    table: dict[str, ModelPrice],
) -> ModelPrice | None:
    """Look up the price entry for ``model_id`` with gateway fallback.

    For free-claude-code gateway IDs (``anthropic/minimax/MiniMax-M3``) the
    ``anthropic/`` prefix is stripped before lookup, so a price table keyed by
    the direct provider ref (``minimax/MiniMax-M3``) still resolves.
    """
    if model_id is None:
        return table.get("__fallback__")
    direct = table.get(model_id)
    if direct is not None:
        return direct
    stripped = _strip_gateway_prefix(model_id)
    if stripped != model_id:
        stripped_price = table.get(stripped)
        if stripped_price is not None:
            return stripped_price
    return table.get("__fallback__")


def main() -> int:
    started = time.perf_counter()
    claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", DEFAULT_CLAUDE_DIR))
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")

    pricing_path = THIS_DIR / "pricing.json"
    config_path = THIS_DIR / "statusline.env.json"

    if not pricing_path.exists():
        print(PLACEHOLDER + " (pricing.json ausente)", file=sys.stdout)
        return 0

    table, fallback_fx = load_pricing_table(pricing_path)
    opts = _load_display_options(config_path)
    fx_ttl = float(opts.fx_cache_ttl_seconds or DEFAULT_TTL_SECONDS)
    fx = resolve_rate(fallback_rate=fallback_fx, cache_ttl_seconds=fx_ttl)
    fx_source = fx.source

    stdin_hint = _read_stdin()
    std_model = None
    if isinstance(stdin_hint, dict):
        model_field = stdin_hint.get("model")
        if isinstance(model_field, dict):
            std_model = model_field.get("id") or model_field.get("display_name")
        elif isinstance(model_field, str):
            std_model = model_field
    if std_model is None:
        std_model = os.environ.get("CLAUDE_MODEL")

    log_path, fallback_model = _safe_log_path(claude_dir, project_dir, session_id)
    if log_path is None:
        price = _price_for_model(std_model, table)
        totals = TokenTotals()
        cost = CostBreakdown(fx_to_brl=fx.rate)
    else:
        from lib.parser import aggregate_session

        totals = _aggregate_cached(log_path, session_id or "")
        last_model = totals.last_model or fallback_model or std_model
        if fallback_model and totals.request_count == 0:
            last_model = fallback_model
        if last_model and last_model != totals.last_model:
            totals = TokenTotals(
                input_tokens=totals.input_tokens,
                output_tokens=totals.output_tokens,
                cache_creation_tokens=totals.cache_creation_tokens,
                cache_read_tokens=totals.cache_read_tokens,
                request_count=totals.request_count,
                first_timestamp=totals.first_timestamp,
                last_timestamp=totals.last_timestamp,
                last_model=last_model,
                last_provider=_provider_from_model(last_model),
            )
        price = _price_for_model(last_model, table)
        cost = compute_cost(totals, table, fx.rate)

    context = _build_context_info(stdin_hint, project_dir)
    quota = None
    quota_provider_id = _active_quota_provider(
        totals, fallback_model, std_model, price
    )
    if opts.show_provider_quota and quota_provider_id:
        quota = fetch_quota(quota_provider_id)

    # When the JSONL has no assistant entries yet, derive provider from the
    # active model id (stdin or env) so the model label shows the real
    # upstream provider, not "anthropic" from the gateway prefix.
    if totals.last_provider in (None, "anthropic", "unknown"):
        candidate = totals.last_model or std_model
        if candidate:
            derived = _provider_from_model(candidate)
            if derived and derived not in ("anthropic", "unknown"):
                totals = TokenTotals(
                    input_tokens=totals.input_tokens,
                    output_tokens=totals.output_tokens,
                    cache_creation_tokens=totals.cache_creation_tokens,
                    cache_read_tokens=totals.cache_read_tokens,
                    request_count=totals.request_count,
                    first_timestamp=totals.first_timestamp,
                    last_timestamp=totals.last_timestamp,
                    last_model=totals.last_model,
                    last_provider=derived,
                )

    line = render(totals, cost, price, opts, context=context, quota=quota)
    if fx_source == "fallback":
        line = line + " \x1b[2m(fx=fallback)\x1b[0m"
    elif fx_source == "cache" and fx.age_seconds > fx_ttl:
        line = line + f" \x1b[2m(fx={fx.age_seconds / 3600:.1f}h)\x1b[0m"
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms > 100:
        line = line + f"  \x1b[2m({elapsed_ms:.0f}ms)\x1b[0m"
    print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
