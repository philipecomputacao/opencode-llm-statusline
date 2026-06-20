"""Render aggregated token totals and cost into a statusline string.

The Claude Code statusline protocol expects the script to print a single line
to stdout. ANSI color codes are supported (the Claude Code TUI renders them).

Public entry point: :func:`render`. The function takes parsed token totals,
a cost breakdown, and a few display options, and returns the final string.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .parser import TokenTotals
from .pricing import CostBreakdown, ModelPrice
from .provider_quota import QuotaInfo

ColorMode = Literal["auto", "always", "never"]

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"

CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
MAGENTA = "\x1b[35m"
BLUE = "\x1b[34m"
GRAY = "\x1b[90m"


@dataclass(frozen=True, slots=True)
class DisplayOptions:
    """Toggles for which fields appear in the statusline."""

    show_provider: bool = True
    show_model: bool = True
    show_tokens: bool = True
    show_cost: bool = True
    show_duration: bool = True
    show_burn_rate: bool = True
    show_cache_pct: bool = True
    show_flags: bool = True
    show_both_currencies: bool = True
    show_provider_quota: bool = True
    quota_warn_pct: float = 60.0
    quota_alert_pct: float = 85.0
    verbose: bool = False
    color: ColorMode = "auto"
    cost_warn_brl: float = 0.50
    cost_alert_brl: float = 2.50
    burn_warn_per_min: int = 15000
    burn_alert_per_min: int = 50000
    fx_cache_ttl_seconds: float = 3600.0


@dataclass(frozen=True, slots=True)
class ContextInfo:
    """Sidebar context rendered with emoji markers (parity with legacy cc-statusline).

    All fields are optional; missing values are simply omitted from the line.
    """

    cwd: str | None = None
    cc_version: str | None = None
    context_used_pct: int | None = None  # 0-100, percent of context used
    session_duration_ms: int | None = None  # wall-clock since session started (from stdin)


# Emoji markers — kept for parity with the legacy ``~/.claude/statusline.sh``
# (community ``cc-statusline`` layout) so users do not lose the visual markers.
EMOJI_DIR = "\U0001F4C1"      # 📁
EMOJI_CC = "\U0001F4DF"       # 📟
EMOJI_CONTEXT = "\U0001F9E0"  # 🧠
EMOJI_QUOTA = "\u23F1"        # ⏱
EMOJI_CALENDAR = "\U0001F4C5" # 📅
EMOJI_TIMER = "\u231B"        # ⌛ (session duration; avoids colliding with ⏱ quota)

# Burn-rate visual states. The emoji tells the rate at a glance even when the
# terminal does not render ANSI colors (e.g. plain logs, some macOS themes).
BURN_EMOJI_LOW = "\U0001F9CA"   # 🧊 cold / calm usage
BURN_EMOJI_MID = "\u26A1"       # ⚡ active / busy
BURN_EMOJI_HIGH = "\U0001F525"  # 🔥 heavy / hot


# Emoji markers — kept for parity with the legacy ``~/.claude/statusline.sh``
# (community ``cc-statusline`` layout) so users do not lose the visual markers.
EMOJI_DIR = "\U0001F4C1"      # 📁
EMOJI_CC = "\U0001F4DF"       # 📟
EMOJI_CONTEXT = "\U0001F9E0"  # 🧠
EMOJI_QUOTA = "\u23F1"        # ⏱
EMOJI_CALENDAR = "\U0001F4C5" # 📅


FLAG_BR = "\U0001F1E7\U0001F1F7"  # Brazil
FLAG_US = "\U0001F1FA\U0001F1F8"  # United States


def _use_color(mode: ColorMode) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return True


def _colorize(text: str, color: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{color}{text}{RESET}"


def _format_tokens(n: int) -> str:
    """Format integer token count as compact ``1.2k`` / ``12k`` / ``3.4M``."""
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    if n < 1_000_000:
        return f"{n // 1000}k"
    return f"{n / 1_000_000:.1f}M"


def _format_cost(usd: float, fx: float) -> tuple[str, str]:
    """Return ``(usd_str, brl_str)`` formatted for compact display."""
    if usd < 0.01:
        usd_str = "$0.00"
    elif usd < 1:
        usd_str = f"${usd:.3f}"
    else:
        usd_str = f"${usd:.2f}"
    brl = usd * fx
    if brl < 0.01:
        brl_str = "R$0.00"
    elif brl < 1:
        brl_str = f"R${brl:.3f}"
    else:
        brl_str = f"R${brl:.2f}"
    return usd_str, brl_str


def _format_duration(start: str | None, end: str | None) -> str | None:
    """Return compact duration like ``18m`` / ``1h23m`` / ``42s``.

    ``start``/``end`` are ISO 8601 timestamps from the JSONL. The wall-clock
    session duration (preferred) is taken from
    :attr:`ContextInfo.session_duration_ms` instead — see :func:`render`.
    """
    if not start or not end:
        return None
    try:
        t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = t1 - t0
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return None
    return _format_seconds(total_seconds)


def _format_seconds(total_seconds: int) -> str:
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    hours, rem = divmod(total_seconds, 3600)
    return f"{hours}h{rem // 60}m"


def _resolve_duration(
    totals: TokenTotals,
    context: ContextInfo | None,
) -> str | None:
    """Pick the best duration source for the statusline.

    Prefers ``context.session_duration_ms`` (wall-clock since session start,
    from ``cost.total_duration_ms`` in the stdin payload) over the JSONL
    span between first and last assistant message — the JSONL span only
    advances once a second request is made, so it stays at ``0s`` for the
    first message of a session even though wall-clock has elapsed.
    """
    if context is not None and context.session_duration_ms is not None:
        seconds = int(context.session_duration_ms / 1000)
        if seconds >= 0:
            return _format_seconds(seconds)
    return _format_duration(totals.first_timestamp, totals.last_timestamp)


def _format_countdown(ms: int | None) -> str | None:
    """Format a millisecond duration as compact ``2h13m`` / ``45m`` / ``30s``."""
    if ms is None or ms <= 0:
        return None
    total_seconds = int(ms / 1000)
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    hours, rem = divmod(total_seconds, 3600)
    return f"{hours}h{rem // 60}m"


def _cache_segment(totals: TokenTotals, use_color: bool) -> str:
    """Render the prompt-cache segment as ``↻R45k ↻W5k``.

    Splits into the cheap read path (cache hits, green) and the expensive
    write path (cache creation, gray). Segments with zero tokens are dropped
    so a brand-new session does not show ``↻R0 ↻W0`` clutter.
    """
    parts: list[str] = []
    if totals.cache_read_tokens > 0:
        parts.append(
            _colorize(
                f"\u21bbR{_format_tokens(totals.cache_read_tokens)}",
                GREEN,
                use_color,
            )
        )
    if totals.cache_creation_tokens > 0:
        parts.append(
            _colorize(
                f"\u21bbW{_format_tokens(totals.cache_creation_tokens)}",
                GRAY,
                use_color,
            )
        )
    if not parts:
        # No cache activity in this session yet — keep the legacy single
        # ``↻0`` marker so the line is not missing a segment entirely.
        return _colorize("\u21bb0", GRAY, use_color)
    return " ".join(parts)


def _quota_color(used_pct: float | None, opts: DisplayOptions) -> str:
    """Color the quota segment by used percentage (green/yellow/red)."""
    if used_pct is None:
        return GRAY
    if used_pct >= opts.quota_alert_pct:
        return RED
    if used_pct >= opts.quota_warn_pct:
        return YELLOW
    return GREEN


def _render_quota_segment(
    quota: QuotaInfo,
    opts: DisplayOptions,
) -> str | None:
    """Render the generic ``⏱`` quota segment for any provider.

    Adapters push a ``status_label`` (short badge like ``"60% livre"`` or
    ``"$8.50 credits"``) and optional ``detail`` (e.g. ``"reset 2h48m"``).
    The segment is skipped entirely when the adapter reports ``source="error"``
    with no usable label, or when the upstream returned no useful data.
    """
    if quota is None:
        return None
    if not quota.status_label or quota.status_label == "?":
        return None

    color = _quota_color(quota.used_pct, opts)
    use_color = _use_color(opts.color)

    body = quota.status_label
    if quota.detail:
        body = f"{body} ({quota.detail})" if body else quota.detail
    label = f"{EMOJI_QUOTA} {body}".strip()
    return _colorize(label, color, use_color)


def _burn_rate_visual(rate: float, opts: DisplayOptions) -> tuple[str, str]:
    """Return ``(color, emoji)`` for the current burn rate.

    Three visual states keyed off ``opts.burn_warn_per_min`` and
    ``opts.burn_alert_per_min``:

    * ``rate < warn``   → 🧊 green
    * ``warn ≤ rate < alert`` → ⚡ yellow
    * ``rate ≥ alert``  → 🔥 red
    """
    if rate >= opts.burn_alert_per_min:
        return RED, BURN_EMOJI_HIGH
    if rate >= opts.burn_warn_per_min:
        return YELLOW, BURN_EMOJI_MID
    return GREEN, BURN_EMOJI_LOW


def _cost_color(brl: float, opts: DisplayOptions) -> str:
    if brl >= opts.cost_alert_brl:
        return RED
    if brl >= opts.cost_warn_brl:
        return YELLOW
    return GREEN

def render(
    totals: TokenTotals,
    cost: CostBreakdown,
    price: ModelPrice | None,
    opts: DisplayOptions | None = None,
    *,
    context: ContextInfo | None = None,
    quota: QuotaInfo | None = None,
) -> str:
    """Build the final statusline string."""
    if opts is None:
        opts = DisplayOptions()
    use_color = _use_color(opts.color)
    # Three logical groups, one per rendered line:
    #   parts_id   = model + cwd + cc version
    #   parts_uso  = tokens + context window % + MiniMax quota windows
    #   parts_custo = cost + duration + burn rate + verbose USD
    parts_id: list[str] = []
    parts_uso: list[str] = []
    parts_custo: list[str] = []


    if opts.show_model:
        model_label = price.display if price else (totals.last_model or "???")
        if totals.last_provider and totals.last_provider not in {"anthropic", "unknown"}:
            model_label = f"{model_label}·{totals.last_provider}"
        if cost.unknown_models:
            model_label = f"{model_label}?"
        parts_id.append(_colorize(f"[{model_label}]", CYAN, use_color))

    if opts.show_tokens:
        in_t = _format_tokens(totals.input_tokens)
        out_t = _format_tokens(totals.output_tokens)
        parts_uso.append(
            _colorize(f"\u2b06{in_t}", BLUE, use_color)
            + " "
            + _colorize(f"\u2b07{out_t}", MAGENTA, use_color)
            + " "
            + _cache_segment(totals, use_color)
        )

    if opts.show_cost and price is not None:
        usd_str, brl_str = _format_cost(cost.total_cost_usd, cost.fx_to_brl)
        billing = cost.billing_modes.get(price.display, "pay_as_you_go")
        brl_flag = f"{FLAG_BR} " if opts.show_flags else ""
        usd_flag = f"{FLAG_US} " if opts.show_flags else ""

        if billing == "free_tier":
            label = f"{brl_flag}R$0.00 (free)"
            parts_custo.append(_colorize(label, GRAY, use_color))
        elif billing == "token_plan":
            label = f"{brl_flag}R$0.00 (quota)"
            parts_custo.append(_colorize(label, DIM + YELLOW, use_color))
        else:
            color = _cost_color(cost.total_cost_brl, opts)
            if opts.show_both_currencies and cost.total_cost_usd >= 0.0001:
                label = f"{brl_flag}{brl_str} {usd_flag}{usd_str}"
            else:
                label = f"{brl_flag}{brl_str}"
            parts_custo.append(_colorize(label, color, use_color))

    duration = _resolve_duration(totals, context)
    if opts.show_duration and duration:
        parts_custo.append(_colorize(f"{EMOJI_TIMER} {duration}", GRAY, use_color))

    if opts.show_provider_quota and quota is not None and quota.source != "error":
        quota_segment = _render_quota_segment(quota, opts)
        if quota_segment:
            parts_uso.append(quota_segment)

    burn_rate: float | None = None
    if opts.show_burn_rate and duration and totals.total_tokens > 0:
        active_tokens = totals.input_tokens + totals.output_tokens
        if duration.endswith("s") and not duration.endswith("ms"):
            minutes = max(int(duration[:-1]) / 60, 1 / 60)
        elif duration.endswith("m"):
            minutes = max(int(duration[:-1]), 1)
        elif duration.endswith("h"):
            h = int(duration.split("h")[0])
            rem = duration.split("h")[1]
            minutes = h * 60 + (int(rem[:-1]) if rem.endswith("m") else 0)
        else:
            minutes = 1
        burn_rate = active_tokens / minutes if active_tokens > 0 else 0.0
        color, emoji = _burn_rate_visual(burn_rate, opts)
        rate_str = f"{emoji} {int(burn_rate)}t/m"
        parts_custo.append(_colorize(rate_str, color, use_color))

    if opts.verbose and price is not None:
        if not opts.show_both_currencies:
            usd_str, brl_str = _format_cost(cost.total_cost_usd, cost.fx_to_brl)
            parts_custo.append(_colorize(f"{FLAG_US} {usd_str}", DIM, use_color))
        if totals.cache_read_tokens + totals.cache_creation_tokens > 0:
            cache_total = totals.cache_read_tokens + totals.cache_creation_tokens
            pct = cache_total / max(totals.total_tokens, 1) * 100
            if opts.show_cache_pct and pct >= 50:
                parts_custo.append(
                    _colorize(f"cache:{pct:.0f}%", DIM + GREEN, use_color)
                )

    if context is not None:
        if context.cwd:
            short = context.cwd.replace(os.path.expanduser("~"), "~", 1)
            parts_id.append(_colorize(f"{EMOJI_DIR} {short}", DIM, use_color))
        if context.cc_version:
            parts_id.append(
                _colorize(f"{EMOJI_CC} v{context.cc_version}", DIM, use_color)
            )
        if context.context_used_pct is not None:
            used = max(0, min(100, context.context_used_pct))
            remaining = 100 - used
            label = f"{EMOJI_CONTEXT} {used}% usado ({remaining}% livre)"
            color = RED if used >= 90 else YELLOW if used >= 70 else GRAY
            parts_uso.append(_colorize(label, color, use_color))

    return _format_multiline(
        [parts_id, parts_uso, parts_custo],
        use_color=use_color,
    )


def _format_multiline(
    grouped_parts: list[list[str]],
    *,
    use_color: bool,
) -> str:
    """Join grouped parts into ``\\n``-separated lines, dropping empty groups."""
    separator = _colorize(" \u2022 ", DIM, use_color)
    rendered: list[str] = []
    for group in grouped_parts:
        if not group:
            continue
        joined = separator.join(group)
        if joined:
            rendered.append(joined)
    if not rendered:
        return _colorize("(empty)", DIM, use_color)
    return "\n".join(rendered)
