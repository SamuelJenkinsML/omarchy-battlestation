"""Thin hyprctl wrappers. Hyprland 0.55+ with Omarchy's Lua config."""

from __future__ import annotations

import json
import re
import subprocess

CONNECTOR_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class HyprError(RuntimeError):
    pass


def _run(args: list[str], timeout: float = 5.0) -> str:
    try:
        proc = subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HyprError(f"hyprctl {' '.join(args)}: {exc}") from exc
    if proc.returncode != 0:
        raise HyprError(f"hyprctl {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _json(args: list[str]):
    out = _run(["-j", *args])
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise HyprError(f"hyprctl -j {' '.join(args)} returned invalid JSON") from exc


def monitors(include_disabled: bool = True) -> list[dict]:
    return _json(["monitors", "all"] if include_disabled else ["monitors"])


def clients() -> list[dict]:
    return _json(["clients"])


def reload() -> None:
    _run(["reload"])


def config_errors() -> list[str]:
    try:
        data = _json(["configerrors"])
    except HyprError:
        return []
    if isinstance(data, list):
        return [str(e) for e in data if str(e).strip()]
    return []


def eval_lua(code: str) -> str:
    return _run(["eval", code])


def dispatch(expr: str) -> str:
    return _run(["dispatch", expr])


def lua_str(value) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def parse_mode(raw: str) -> tuple[int, int, float] | None:
    m = re.match(r"^\s*(\d+)x(\d+)(?:@([\d.]+)(?:Hz)?)?\s*$", str(raw or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), float(m.group(3) or 60)


def format_mode(width: int, height: int, hz: float) -> str:
    return f"{width}x{height}@{hz:g}"


def closest_available(mon: dict, wanted: str) -> str | None:
    """Return an availableModes entry matching `wanted` (to 0.5 Hz), else None."""
    target = parse_mode(wanted)
    if not target:
        return None
    w, h, hz = target
    best = None
    for raw in mon.get("availableModes") or []:
        parsed = parse_mode(raw)
        if not parsed or parsed[0] != w or parsed[1] != h:
            continue
        delta = abs(parsed[2] - hz)
        if delta <= 0.5 and (best is None or delta < best[0]):
            best = (delta, raw)
    return best[1].removesuffix("Hz") if best else None


def current_mode(mon: dict) -> str:
    w, h = int(mon.get("width") or 0), int(mon.get("height") or 0)
    hz = float(mon.get("refreshRate") or 60)
    return format_mode(w, h, round(hz, 2))


def identity(mon: dict) -> str:
    desc = str(mon.get("description") or "").strip()
    return f"desc:{desc}" if desc else str(mon.get("name") or "")
