"""Thin hyprctl wrappers. Hyprland 0.55+ with Omarchy's Lua config."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

CONNECTOR_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class HyprError(RuntimeError):
    pass


# -- instance discovery ---------------------------------------------------------
#
# HYPRLAND_INSTANCE_SIGNATURE is inherited, and a process started from an older
# session (or from Sunshine's prep-cmd) can carry one whose compositor is gone.
# hyprctl then fails outright, so find the instance that is actually alive.

_instance: str | None = None
_instance_known = False


def _instance_alive(path: Path) -> bool:
    if not (path / ".socket.sock").exists():
        return False
    try:
        pid = int((path / "hyprland.lock").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return False
    return Path(f"/proc/{pid}").exists()


def discover_instance(runtime: Path | None = None) -> str | None:
    """The inherited signature if its compositor is alive, else the newest live one."""
    if runtime is None:
        base = os.environ.get("XDG_RUNTIME_DIR", "").strip() or f"/run/user/{os.getuid()}"
        runtime = Path(base) / "hypr"
    inherited = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "").strip()
    if inherited and _instance_alive(runtime / inherited):
        return inherited
    try:
        candidates = [p for p in runtime.iterdir() if p.is_dir() and _instance_alive(p)]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime).name


def instance_signature() -> str | None:
    global _instance, _instance_known
    if not _instance_known:
        _instance, _instance_known = discover_instance(), True
    return _instance


def _run(args: list[str], timeout: float = 5.0) -> str:
    sig = instance_signature()
    argv = ["hyprctl", *(["-i", sig] if sig else []), *args]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
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


def workspaces() -> list[dict]:
    return _json(["workspaces"])


def focus_workspace(workspace: int) -> None:
    dispatch(f'hl.dsp.focus({{ workspace = "{int(workspace)}" }})')


def move_window(address: str, workspace: int) -> None:
    """Move a window to a workspace without following it there."""
    if not re.match(r"^0x[0-9a-fA-F]+$", str(address)):
        raise HyprError(f"unsafe window address {address!r}")
    dispatch(f'hl.dsp.window.move({{ workspace = "{int(workspace)}", follow = false, window = "address:{address}" }})')


def move_workspace(workspace: int, monitor: str) -> None:
    if not CONNECTOR_RE.match(monitor):
        raise HyprError(f"unsafe output name {monitor!r}")
    dispatch(f'hl.dsp.workspace.move({{ workspace = "{int(workspace)}", monitor = {lua_str(monitor)} }})')


def warp_cursor(x: int, y: int) -> None:
    dispatch(f"hl.dsp.cursor.move({{ x = {int(x)}, y = {int(y)} }})")


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


def create_headless(name: str) -> None:
    """Add a compositor-side virtual output. It lasts until removed or the session ends."""
    if not CONNECTOR_RE.match(name):
        raise HyprError(f"unsafe output name {name!r}")
    _run(["output", "create", "headless", name])


def remove_output(name: str) -> None:
    if not CONNECTOR_RE.match(name):
        raise HyprError(f"unsafe output name {name!r}")
    _run(["output", "remove", name])


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
