"""Varyasyonlu sabit cümleler."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import config

_last_by_category: dict[str, str] = {}
_variants_cache: dict[str, list[str]] | None = None


def _variants_path() -> Path:
    return config.DATA_DIR / "phrase_variants.json"


def _load_variants() -> dict[str, list[str]]:
    global _variants_cache
    if _variants_cache is not None:
        return _variants_cache
    path = _variants_path()
    if not path.is_file():
        _variants_cache = {}
        return _variants_cache
    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)
    out: dict[str, list[str]] = {}
    for key, val in raw.items():
        if isinstance(val, list):
            out[key] = [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str) and val.strip():
            out[key] = [val.strip()]
    _variants_cache = out
    return out


def pick(category: str, *, fallback: str = "Tamam kanka.", avoid_repeat: bool = True) -> str:
    """Kategoriden rastgele cümle; mümkünse bir öncekiyle aynı olmaz."""
    items = _load_variants().get(category) or []
    if not items:
        return fallback
    if len(items) == 1:
        choice = items[0]
    elif avoid_repeat and category in _last_by_category:
        prev = _last_by_category[category]
        pool = [x for x in items if x != prev] or items
        choice = random.choice(pool)
    else:
        choice = random.choice(items)
    _last_by_category[category] = choice
    return choice
