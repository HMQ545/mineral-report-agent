"""Commodity registry: which venue, contract and source backs each commodity."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from ..common import config

REGISTRY_PATH = config.SOURCES_DIR / "price_sources.json"

ALIASES = {
    "cu": "copper",
    "铜": "copper",
    "铜价": "copper",
    "铝": "aluminum",
    "镍": "nickel",
    "锌": "zinc",
    "lme copper": "copper",
    "伦铜": "copper",
    "al": "aluminum",
    "aluminium": "aluminum",
    "lme aluminum": "aluminum",
    "伦铝": "aluminum",
    "ni": "nickel",
    "lme nickel": "nickel",
    "伦镍": "nickel",
    "zn": "zinc",
    "lme zinc": "zinc",
    "伦锌": "zinc",
    "lithium carbonate": "lithium_carbonate",
    "lithium_carbonate": "lithium_carbonate",
    "碳酸锂": "lithium_carbonate",
    "lc": "lithium_carbonate",
    "spodumene": "lithium_spodumene",
    "lithium spodumene": "lithium_spodumene",
    "锂辉石": "lithium_spodumene",
    "lithium_spodumene": "lithium_spodumene",
}


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"commodities": {}}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def reload_registry() -> None:
    _load.cache_clear()


def resolve(commodity: str) -> str | None:
    key = (commodity or "").strip().lower()
    if not key:
        return None
    if key in _load()["commodities"]:
        return key
    return ALIASES.get(key)


def config_for(commodity: str) -> dict[str, Any] | None:
    canonical = resolve(commodity)
    return _load()["commodities"].get(canonical) if canonical else None


def all_commodities() -> dict[str, Any]:
    return dict(_load()["commodities"])


def endpoints() -> dict[str, Any]:
    registry = _load()
    return {key: value for key, value in registry.items() if key != "commodities"}


__all__ = [
    "ALIASES",
    "REGISTRY_PATH",
    "all_commodities",
    "config_for",
    "endpoints",
    "reload_registry",
    "resolve",
]

