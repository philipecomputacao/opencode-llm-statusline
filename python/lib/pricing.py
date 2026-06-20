"""Calculate session cost using a configurable price table.

Prices are stored in :file:`pricing.json` next to this module. Each model entry
holds per-million-token prices for input, output, cache_read, and cache_write
fields. Multi-provider sessions are supported — each request's cost is computed
against the price entry matching the model id observed in the JSONL.

For models on free tiers (NVIDIA NIM free tier, OpenRouter free, etc.) the
configured price is 0 and the ``billing_mode`` field is set to ``"free_tier"``
so the display layer can annotate the statusline accordingly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parser import TokenTotals, _strip_gateway_prefix


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Pricing entry for a single model."""

    model_id: str
    provider: str
    display: str
    input_per_million: float
    output_per_million: float
    cache_read_per_million: float
    cache_write_per_million: float
    billing_mode: str = "pay_as_you_go"
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Cost computed for one or more requests in a session."""

    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    cache_read_cost_usd: float = 0.0
    cache_write_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    currency: str = "USD"
    fx_to_brl: float = 5.20
    per_model: dict[str, float] = field(default_factory=dict)
    billing_modes: dict[str, str] = field(default_factory=dict)
    unknown_models: tuple[str, ...] = ()

    @property
    def total_cost_brl(self) -> float:
        return self.total_cost_usd * self.fx_to_brl


def load_pricing_table(path: Path) -> tuple[dict[str, ModelPrice], float]:
    """Load and validate the pricing JSON file.

    Returns ``(table, fx_to_brl)`` where ``table`` maps model id (or the
    sentinel key ``__fallback__``) to a :class:`ModelPrice`.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    fx = float(raw.get("fx_to_brl", 5.20))
    models_raw: dict[str, Any] = raw.get("models", {})
    table: dict[str, ModelPrice] = {}
    for model_id, entry in models_raw.items():
        table[model_id] = _entry_to_price(model_id, entry)
    fallback_raw = raw.get("fallback")
    if isinstance(fallback_raw, dict):
        table["__fallback__"] = _entry_to_price("__fallback__", fallback_raw)
    return table, fx


def _entry_to_price(model_id: str, entry: dict[str, Any]) -> ModelPrice:
    return ModelPrice(
        model_id=model_id,
        provider=str(entry.get("provider", "unknown")),
        display=str(entry.get("display", model_id.split("/")[-1])),
        input_per_million=float(entry.get("input", 0.0)),
        output_per_million=float(entry.get("output", 0.0)),
        cache_read_per_million=float(entry.get("cache_read", 0.0)),
        cache_write_per_million=float(entry.get("cache_write", 0.0)),
        billing_mode=str(entry.get("billing_mode", "pay_as_you_go")),
        notes=str(entry.get("notes", "")),
    )


def compute_cost(
    totals: TokenTotals,
    table: dict[str, ModelPrice],
    fx_to_brl: float,
) -> CostBreakdown:
    """Compute the cost for the given session totals using ``table``.

    Because :class:`TokenTotals` aggregates across models (the session may have
    switched providers), the price used for cost is the one matching the
    **last observed model**. If the user wants per-model costs they can extend
    the parser to track per-model totals in the future.

    For free-claude-code gateway IDs (``anthropic/minimax/MiniMax-M3``) the
    prefix is stripped before lookup so a price table keyed by the direct
    provider ref (``minimax/MiniMax-M3``) still resolves.
    """
    raw_last_model = totals.last_model
    last_model = raw_last_model or "__fallback__"
    price = table.get(last_model)
    if price is None and raw_last_model is not None:
        stripped = _strip_gateway_prefix(raw_last_model)
        if stripped != raw_last_model:
            price = table.get(stripped)
    if price is None:
        price = table.get("__fallback__")
    if price is None:
        return CostBreakdown(
            currency="USD",
            fx_to_brl=fx_to_brl,
            unknown_models=(raw_last_model,) if raw_last_model else (),
        )

    input_cost = totals.input_tokens / 1_000_000 * price.input_per_million
    output_cost = totals.output_tokens / 1_000_000 * price.output_per_million
    cache_read_cost = (
        totals.cache_read_tokens / 1_000_000 * price.cache_read_per_million
    )
    cache_write_cost = (
        totals.cache_creation_tokens / 1_000_000 * price.cache_write_per_million
    )
    total = input_cost + output_cost + cache_read_cost + cache_write_cost
    unknown: tuple[str, ...] = (
        () if price.model_id != "__fallback__"
        else ((raw_last_model,) if raw_last_model else ())
    )
    return CostBreakdown(
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        cache_read_cost_usd=cache_read_cost,
        cache_write_cost_usd=cache_write_cost,
        total_cost_usd=total,
        currency="USD",
        fx_to_brl=fx_to_brl,
        per_model={price.display: total},
        billing_modes={price.display: price.billing_mode},
        unknown_models=unknown,
    )
