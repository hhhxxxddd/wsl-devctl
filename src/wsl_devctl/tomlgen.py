from __future__ import annotations

import json
from typing import Any

from .errors import DevctlError


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    raise DevctlError(f"cannot render TOML value: {value!r}")


def _table(lines: list[str], prefix: str, value: dict[str, Any]) -> None:
    scalar = [(key, item) for key, item in value.items() if not isinstance(item, dict)]
    nested = [(key, item) for key, item in value.items() if isinstance(item, dict)]
    if prefix:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{prefix}]")
    for key, item in scalar:
        lines.append(f"{key} = {_value(item)}")
    for key, item in nested:
        child = f"{prefix}.{key}" if prefix else key
        _table(lines, child, item)


def render_toml(value: dict[str, Any]) -> str:
    lines: list[str] = []
    _table(lines, "", value)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
