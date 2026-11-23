"""Validated data-driven strategy catalog shared with the Next.js frontend.

A catalog entry is metadata, not permission to execute untrusted code.
Adding a new strategy requires an explicit audited Python implementation.
"""
from __future__ import annotations
import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[2] / "strategies" / "catalog.json"
ALLOWED_STATUSES = {"live", "research", "planned"}
ALLOWED_KINDS = {"number", "integer"}
IMPLEMENTED_LIVE = {"netnet_v1"}


def load_catalog(path: Path = CATALOG_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("Strategy catalog must be a nonempty array")
    ids: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            raise ValueError("Invalid strategy manifest entry")
        identifier = row.get("id")
        if (not isinstance(identifier,str) or
                not identifier or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in identifier)):
            raise ValueError("Invalid strategy identifier")
        if identifier in ids:
            raise ValueError(f"Duplicate strategy ID {identifier}")
        ids.add(identifier)
        if row.get("status") not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid strategy status {identifier}")
        if row["status"] == "live" and identifier not in IMPLEMENTED_LIVE:
            raise ValueError("Catalog must not claim an unimplemented strategy is live")
        for key in ("version", "name", "family", "description", "universe", "notes"):
            if not isinstance(row.get(key),str) or not row[key].strip():
                raise ValueError(f"{identifier}: missing {key}")
        requirements = row.get("requirements")
        if not isinstance(requirements,list) or not all(
            isinstance(x,str) and x for x in requirements
        ):
            raise ValueError(f"{identifier}: invalid requirements")
        params = row.get("parameters")
        if not isinstance(params,list):
            raise ValueError(f"{identifier}: missing parameters")
        seen_params = set()
        for param in params:
            if not isinstance(param,dict) or param.get("type") not in ALLOWED_KINDS:
                raise ValueError(f"{identifier}: invalid numeric parameter")
            key = param.get("key")
            if not isinstance(key,str) or not key or key in seen_params:
                raise ValueError(f"{identifier}: duplicate or missing parameter name")
            seen_params.add(key)
            value = param.get("default")
            if (not isinstance(value,(int,float)) or isinstance(value,bool) or
                    not isinstance(param.get("min"),(int,float)) or
                    not isinstance(param.get("max"),(int,float)) or
                    not param["min"] <= value <= param["max"]):
                raise ValueError(f"{identifier}: invalid default or range for {key}")
            if param["type"] == "integer" and not isinstance(value,int):
                raise ValueError(f"{identifier}: integer default expected for {key}")
    return data


def by_id(identifier: str) -> dict:
    for strategy in load_catalog():
        if strategy["id"] == identifier:
            return strategy
    raise KeyError(f"Unknown strategy identifier: {identifier}")


def live_strategy_ids() -> set[str]:
    return {item["id"] for item in load_catalog() if item["status"] == "live"}
