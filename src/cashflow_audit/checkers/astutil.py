from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from cashflow_audit.parse.a1 import format_addr

ERROR_TOKENS = {
    "#REF!",
    "#DIV/0!",
    "#VALUE!",
    "#NAME?",
    "#N/A",
    "#NULL!",
    "#NUM!",
    "#GETTING_DATA!",
}


def parse_ast(cell: dict) -> dict[str, Any] | None:
    raw = cell.get("ast_json")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def walk(node: dict[str, Any] | None) -> Iterator[dict[str, Any]]:
    if not node:
        return
    yield node
    for key in ("left", "right", "expr", "start", "end"):
        child = node.get(key)
        if isinstance(child, dict):
            yield from walk(child)
    for arg in node.get("args") or []:
        if isinstance(arg, dict):
            yield from walk(arg)


def has_func(node: dict[str, Any] | None, names: set[str]) -> bool:
    upper = {n.upper() for n in names}
    return any(
        item.get("op") == "func" and str(item.get("name", "")).upper() in upper
        for item in walk(node)
    )


def sum_ranges(node: dict[str, Any] | None, sheet: str) -> list[str]:
    found: list[str] = []
    for item in walk(node):
        if item.get("op") != "func":
            continue
        if str(item.get("name", "")).upper() not in {"SUM", "СУММ"}:
            continue
        for arg in item.get("args") or []:
            if isinstance(arg, dict) and arg.get("op") == "range":
                target = _range_target(arg, sheet)
                if target:
                    found.append(target)
    return found


def _range_target(node: dict[str, Any], sheet: str) -> str | None:
    sh = node.get("sheet") or sheet
    if node.get("col_only"):
        return None
    if node.get("row_only"):
        return None
    start = node.get("start") or {}
    end = node.get("end") or {}
    if "col" not in start or "row" not in start or "col" not in end or "row" not in end:
        return None
    a = format_addr(int(start["col"]), int(start["row"]))
    b = format_addr(int(end["col"]), int(end["row"]))
    return f"{sh}!{a}:{b}"


def as_number(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    if not text or text in ERROR_TOKENS:
        return None
    try:
        return float(text)
    except ValueError:
        return None
