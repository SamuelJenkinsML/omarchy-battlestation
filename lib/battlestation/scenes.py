"""Scene resolution, apply with Keep/Revert, capture and init.

Apply pattern (write file -> hyprctl reload -> configerrors -> roll back) and the
persisted revert deadline follow IM0001GT/omarchy-screens (MIT, (c) 2026
IM0001GT). Re-enabling a disabled output needs a full reload rather than a
runtime rule, per PedroSilvaAlves/omarchy-monitor-handoff (MIT, (c) 2026 Pedro
Alves), which is why the generated file is the single source of truth.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import edid, fsutil, hypr, luagen, paths
from .config import Config, ConfigError, MonitorRule, Scene, monitor_toml, scene_toml


class SceneError(RuntimeError):
    pass


@dataclass
class Resolution:
    scene: str
    monitors: list[luagen.ResolvedMonitor] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


# -- state ---------------------------------------------------------------------


def load_state() -> dict:
    state = fsutil.read_json(paths.state_file(), {})
    return state if isinstance(state, dict) else {}


def save_state(state: dict) -> None:
    fsutil.write_json(paths.state_file(), state)


# -- resolution ------------------------------------------------------------------


def find_monitor(rule_match: str, monitors: list[dict]) -> dict | None:
    if rule_match.startswith("desc:"):
        want = rule_match[5:].strip()
        for mon in monitors:
            if str(mon.get("description") or "").strip() == want:
                return mon
        return None
    for mon in monitors:
        if mon.get("name") == rule_match:
            return mon
    return None


def _pick_mode(rule: MonitorRule, mon: dict, notes: list[str]) -> str:
    if rule.mode in ("preferred", "highres", "highrr"):
        return rule.mode
    if not mon.get("availableModes"):
        return rule.mode  # disabled outputs may not list modes; trust the config
    for candidate in [rule.mode, *rule.fallback_modes]:
        found = hypr.closest_available(mon, candidate)
        if found:
            if candidate != rule.mode:
                notes.append(f"{mon.get('name')}: {rule.mode} is not offered, using fallback {found}")
            return found
    notes.append(f"{mon.get('name')}: {rule.mode} is not offered and no fallback matched; using preferred")
    return "preferred"


def _mode_dims(mode: str, mon: dict) -> tuple[int, int, float]:
    parsed = hypr.parse_mode(mode)
    if parsed:
        return parsed
    return int(mon.get("width") or 1920), int(mon.get("height") or 1080), float(mon.get("refreshRate") or 60)


def resolve(scene: Scene, monitors: list[dict], caps_for=edid.read_caps) -> Resolution:
    res = Resolution(scene=scene.name)
    matched: set[str] = set()
    for rule in scene.monitors:
        mon = find_monitor(rule.match, monitors)
        if not mon:
            res.missing.append(rule.match)
            continue
        connector = str(mon.get("name") or "")
        if not hypr.CONNECTOR_RE.match(connector):
            res.notes.append(f"skipping monitor with unsafe connector name {connector!r}")
            continue
        matched.add(connector)
        caps = caps_for(connector)
        mode = _pick_mode(rule, mon, res.notes)
        w, h, hz = _mode_dims(mode, mon)
        if rule.bitdepth == "auto":
            bpc = edid.max_bpc_for_mode(caps, w, h, hz)
        else:
            bpc = int(rule.bitdepth)
        hdr = rule.hdr
        if hdr != "off" and not caps.hdr:
            res.notes.append(f"{connector}: EDID does not advertise HDR (ST2084); HDR left off")
            hdr = "off"
        if hdr != "off" and bpc < 10:
            res.notes.append(f"{connector}: {w}x{h}@{hz:g} cannot carry 10-bit RGB on this link; HDR will run at 8-bit")
        res.monitors.append(
            luagen.ResolvedMonitor(
                connector=connector,
                mode=mode,
                position=rule.position,
                scale=rule.scale,
                transform=rule.transform,
                vrr=rule.vrr,
                hdr=hdr,
                bpc=bpc,
                sdr_brightness=rule.sdr_brightness,
                wide_gamut=caps.wide_gamut,
                max_luminance=caps.max_luminance,
                max_avg_luminance=caps.max_avg_luminance,
            )
        )
    if scene.disable_unlisted:
        for mon in monitors:
            name = str(mon.get("name") or "")
            if name and name not in matched and hypr.CONNECTOR_RE.match(name):
                res.disabled.append(name)
    if not res.monitors:
        raise SceneError(
            f"scene '{scene.name}' matches none of the connected monitors "
            f"(wanted: {', '.join(res.missing) or 'nothing'}); refusing to leave you without a display"
        )
    return res


def render(scene: Scene, monitors: list[dict], caps_for=edid.read_caps) -> tuple[str, Resolution]:
    res = resolve(scene, monitors, caps_for)
    return luagen.render(scene.name, res.monitors, res.disabled), res


# -- apply / keep / revert ---------------------------------------------------------


def layout_body(text: str | None) -> str | None:
    """Scene Lua minus comments, so scenes sharing a layout compare equal."""
    if text is None:
        return None
    return "\n".join(line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("--"))


def write_overlay(target, text: str | None) -> None:
    """Write (or with None, remove) one of our generated Hyprland files.

    Hyprland reloads by itself when a file in the toggles directory changes, so
    the write has to be atomic: a half-written file would be loaded as-is.
    """
    if text is None:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    else:
        fsutil.atomic_write(target, text)


def reload_checked(target, previous: str | None, what: str = "scene") -> None:
    hypr.reload()
    time.sleep(0.4)
    errors = [e for e in hypr.config_errors() if "battlestation" in e.lower() or "toggles" in e.lower()]
    if errors:
        write_overlay(target, previous)
        hypr.reload()
        raise SceneError(f"Hyprland rejected the {what}, rolled back: " + "; ".join(errors))


def reclaim_from_virtual(output: str = "BS-STREAM") -> None:
    """Take back what Hyprland parked on the virtual stream output.

    When a scene switches a display off, Hyprland moves that display's
    workspaces (and focus) to another output, and the parked virtual output is
    as good a candidate as any. Its workspaces then sit 20000 pixels off-screen:
    SUPER+2 jumps there, the pointer vanishes and new windows open where nobody
    can see them. Only the parking workspace belongs there, and it stays empty
    until a client attaches.
    """
    try:
        mons = hypr.monitors(include_disabled=False)
        virtual = next((m for m in mons if m.get("name") == output), None)
        real = [m for m in mons if m.get("name") != output]
        if virtual is None or not real:
            return  # not streaming-capable, or attached (every real display is off)
        home = next((m for m in real if m.get("focused")), real[0])
        home_ws = int((home.get("activeWorkspace") or {}).get("id") or 0)
        park = luagen.PARK_WORKSPACE
        if int((virtual.get("activeWorkspace") or {}).get("id") or 0) != park:
            hypr.focus_workspace(park)  # bound to the virtual output by the stream overlay
        for ws in hypr.workspaces():
            wid = int(ws.get("id") or 0)
            if ws.get("monitor") == output and wid > 0 and wid != park:
                hypr.move_workspace(wid, str(home["name"]))
        if home_ws > 0:
            for client in hypr.clients():
                if (client.get("workspace") or {}).get("id") == park:
                    hypr.move_window(str(client.get("address")), home_ws)
            hypr.focus_workspace(home_ws)
    except (hypr.HyprError, KeyError, TypeError, ValueError):
        pass


def settle_desktop(output: str = "BS-STREAM") -> None:
    """After a layout change: nothing stranded off-screen, and a visible pointer."""
    reclaim_from_virtual(output)
    recover_cursor(output)


def recover_cursor(skip: str = "BS-STREAM") -> None:
    """Warp the pointer to the middle of the focused output after a layout change.

    On NVIDIA the hardware cursor can stay invisible after a modeset until
    something warps it (a workspace switch does, via warp_on_change_workspace).
    """
    try:
        mons = [m for m in hypr.monitors(include_disabled=False) if m.get("name") != skip]
        mon = next((m for m in mons if m.get("focused")), mons[0] if mons else None)
        if mon:
            scale = float(mon.get("scale") or 1) or 1
            hypr.warp_cursor(int(mon["x"] + mon["width"] / scale / 2),
                             int(mon["y"] + mon["height"] / scale / 2))
    except (hypr.HyprError, KeyError, TypeError, ValueError):
        pass


def _write_scene_file(text: str | None) -> None:
    write_overlay(paths.scene_lua(), text)


def _reload_checked(previous: str | None) -> None:
    reload_checked(paths.scene_lua(), previous)


def real_monitors(cfg: Config, monitors: list[dict] | None = None) -> list[dict]:
    """Monitors minus the virtual streaming output, which no scene should ever
    place, disable or capture: the stream overlay owns it."""
    monitors = hypr.monitors() if monitors is None else monitors
    return [m for m in monitors if m.get("name") != cfg.stream.output]


def apply(cfg: Config, name: str, confirm: bool = True) -> dict:
    """Apply a scene. Returns a JSON-able summary.

    confirm=True starts a Keep/Revert window when the layout actually changes.
    An unchanged layout skips the reload entirely, so switching between scenes
    that share a layout (TV desktop <-> TV gaming) only runs their actions.
    """
    scene = cfg.scene(name)
    state = load_state()
    if state.get("pendingRevert"):
        keep()  # applying a new scene implicitly accepts the one on trial
        state = load_state()

    text, res = render(scene, real_monitors(cfg))
    previous_text = fsutil.read_text(paths.scene_lua())
    previous_scene = state.get("active") or ""
    changed = layout_body(previous_text) != layout_body(text)

    if changed:
        fsutil.atomic_write(paths.revert_stash(), previous_text if previous_text is not None else "")
        _write_scene_file(text)
        _reload_checked(previous_text)
        settle_desktop(cfg.stream.output)

    state["previous"] = previous_scene if previous_scene != name else state.get("previous", "")
    state["active"] = name
    state["layout"] = {
        m.connector: {"hdr": m.hdr, "vrr": m.vrr, "bpc": m.bpc, "mode": m.mode} for m in res.monitors
    }
    state["notes"] = res.notes
    state["appliedAt"] = int(time.time())
    if changed and confirm and cfg.keep_seconds > 0:
        state["pendingRevert"] = {
            "deadline": time.time() + cfg.keep_seconds,
            "seconds": cfg.keep_seconds,
            "scene": name,
            "previousScene": previous_scene,
        }
    else:
        state.pop("pendingRevert", None)
    save_state(state)
    return {
        "scene": name,
        "previous": previous_scene,
        "changed": changed,
        "pendingRevert": state.get("pendingRevert"),
        "notes": res.notes,
        "missing": res.missing,
    }


def pick_auto(cfg: Config, monitors: list[dict]) -> str | None:
    """Most specific scene whose monitors are all connected (Mario-Mohar's rule)."""
    best = None
    for scene in cfg.scenes.values():
        if scene.monitors and all(find_monitor(rule.match, monitors) for rule in scene.monitors):
            if best is None or len(scene.monitors) > best[0]:
                best = (len(scene.monitors), scene.name)
    return best[1] if best else None


def keep() -> dict:
    state = load_state()
    had = bool(state.pop("pendingRevert", None))
    save_state(state)
    return {"kept": had, "scene": state.get("active", "")}


def revert() -> dict:
    state = load_state()
    pending = state.pop("pendingRevert", None)
    if not pending:
        return {"reverted": False, "scene": state.get("active", "")}
    stash = fsutil.read_text(paths.revert_stash())
    _write_scene_file(stash if stash else None)
    hypr.reload()
    time.sleep(0.4)
    settle_desktop()
    state["active"] = pending.get("previousScene", "")
    state.pop("layout", None)
    save_state(state)
    return {"reverted": True, "scene": state["active"]}


def revert_if_due(now: float | None = None) -> dict:
    state = load_state()
    pending = state.get("pendingRevert")
    if pending and (now or time.time()) >= float(pending.get("deadline", 0)):
        return revert()
    return {"reverted": False, "pending": bool(pending)}


def clear() -> dict:
    """Remove the scene overlay entirely and fall back to monitors.lua."""
    existed = paths.scene_lua().exists()
    _write_scene_file(None)
    if existed:
        hypr.reload()
    state = load_state()
    state.pop("pendingRevert", None)
    state["active"] = ""
    save_state(state)
    return {"cleared": existed}


# -- capture / init ------------------------------------------------------------------


def capture_rules(monitors: list[dict], caps_for=edid.read_caps, drm=None) -> list[MonitorRule]:
    drm = drm or {}
    rules = []
    for mon in monitors:
        if mon.get("disabled"):
            continue
        name = str(mon.get("name") or "")
        caps = caps_for(name)
        vrr_capable = bool((drm.get(name) or {}).get("vrr_capable")) or caps.vrr_hint
        rules.append(
            MonitorRule(
                match=hypr.identity(mon),
                mode=hypr.current_mode(mon),
                position=f"{int(mon.get('x') or 0)}x{int(mon.get('y') or 0)}",
                scale=float(mon.get("scale") or 1),
                transform=int(mon.get("transform") or 0),
                vrr=2 if vrr_capable else 0,
                hdr="auto" if caps.hdr else "off",
            )
        )
    return rules


def capture_scene(name: str, label: str | None = None, skip_output: str = "BS-STREAM", **kw) -> Scene:
    from . import drmprops

    monitors = [m for m in hypr.monitors() if m.get("name") != skip_output]
    try:
        drm = drmprops.read()
    except OSError:
        drm = {}
    rules = capture_rules(monitors, drm=drm)
    if not rules:
        raise SceneError("no enabled monitors to capture")
    scene = Scene(name=name, label=label or name.replace("-", " ").title(), monitors=rules, **kw)
    scene.disable_unlisted = len(monitors) > 1 or scene.disable_unlisted
    return scene


INIT_HEADER = """\
# Battlestation config. Reload happens automatically when this file is saved.
# Docs: https://github.com/SamuelJenkinsML/omarchy-battlestation#configuration

[general]
default_scene = "{default}"
# The controller chord toggles between these two scenes.
chord_scenes = {chord}
# Seconds to confirm a layout change before it reverts on its own.
keep_seconds = 15

[controller]
# Hold these together for hold_ms to switch scene (View + Menu on Xbox pads).
chord = ["BTN_SELECT", "BTN_START"]
hold_ms = 1000
ignore_vendor = ["28de"]  # Steam's virtual gamepad

# Uncomment to let scenes wake the TV and switch its input.
# Pair once with `battlestation tv pair` (accept the prompt on the TV).
# [tv]
# host = "192.168.1.50"
# mac = "aa:bb:cc:dd:ee:ff"
# input_key = "KEY_HDMI1"
# input_fallback = ["KEY_SOURCE", "KEY_RIGHT", "KEY_ENTER"]
# wake_timeout_s = 25

"""


def init_text() -> str:
    desktop = capture_scene("tv-desktop", "TV", icon="󰍹")
    gaming = Scene(
        name="tv-gaming",
        label="Gaming",
        icon="󰊗",
        monitors=list(desktop.monitors),
        disable_unlisted=desktop.disable_unlisted,
        steam="bigpicture",
        on_bigpicture_exit="ask",
        on_enter=["omarchy-toggle-idle stay-awake"],
        on_exit=["omarchy-toggle-idle allow-idle"],
    )
    header = INIT_HEADER.format(default=desktop.name, chord='["tv-desktop", "tv-gaming"]')
    gaming_text = scene_toml(gaming).replace(
        "steam = ", "# wake_tv = true   # needs [tv] above\nsteam = ", 1
    )
    return header + scene_toml(desktop) + "\n" + gaming_text


def append_scene_text(scene: Scene) -> str:
    return "\n" + scene_toml(scene)


def describe(cfg: Config) -> list[dict]:
    return [
        {
            "name": s.name,
            "label": s.label,
            "icon": s.icon,
            "steam": s.steam,
            "wakeTv": s.wake_tv,
            "monitors": [monitor_toml(m) for m in s.monitors],
        }
        for s in cfg.scenes.values()
    ]


__all__ = [
    "ConfigError",
    "SceneError",
    "apply",
    "keep",
    "revert",
    "revert_if_due",
    "clear",
    "resolve",
    "render",
    "capture_scene",
    "init_text",
]
