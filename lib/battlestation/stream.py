"""Headless game-streaming host: a virtual output for Sunshine to capture.

Sunshine (`capture = wlr`) streams a Hyprland headless output, so streaming
works with the TV off, unplugged or in the wrong mode. Three states:

  off       no virtual output, Sunshine stopped, nothing listening
  armed     virtual output parked (switched off), Sunshine running
  attached  a client is connected: the virtual output takes the client's mode
            and the real displays are switched off, so the desktop moves over

attach/detach are Sunshine's global prep-cmd do/undo. Everything detach needs
is kept in the runtime state file, so it works with a broken config: a bad
config must never be able to leave the TV dark.

Sunshine documents wlr capture of Hyprland virtual outputs. It looks the output
up again for every session, after the prep-cmd has run, so the parked output
stays switched off and attach turns it on. Switched on, XWayland would count it
as a monitor and Wine games would take it for the primary one. The exception is
standby: with no real display on, Sunshine's encoder probe (which runs before
the prep-cmd) needs some output to open, so the parked one is lit off to the side.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import time
from contextlib import contextmanager

from . import fsutil, hypr, luagen, paths, scenes, tailscale
from .config import StreamConfig

STAY_AWAKE = ("omarchy-toggle-idle", "stay-awake")
ALLOW_IDLE = ("omarchy-toggle-idle", "allow-idle")


class StreamError(RuntimeError):
    pass


# -- switch and state ------------------------------------------------------------


def is_enabled() -> bool:
    return paths.stream_flag().exists()


def set_enabled(on: bool) -> None:
    flag = paths.stream_flag()
    if on:
        fsutil.atomic_write(flag, "")
    else:
        try:
            flag.unlink()
        except FileNotFoundError:
            pass


def load_state() -> dict:
    state = fsutil.read_json(paths.stream_state(), {})
    return state if isinstance(state, dict) else {}


def save_state(state: dict) -> None:
    fsutil.write_json(paths.stream_state(), state, mode=0o600)


@contextmanager
def _locked():
    """attach, detach, arm and the watchdog can all fire at once."""
    lock = paths.stream_lock()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# -- helpers ----------------------------------------------------------------------


def client_mode(env, st: StreamConfig) -> str:
    """The mode the client asked for, clamped to the config limits."""
    fallback = hypr.parse_mode(st.default_mode) or (1920, 1080, 60.0)

    def number(key: str, default: float, low: int, high: int) -> int:
        try:
            value = int(float(str(env.get(key, "")).strip()))
        except (ValueError, OverflowError):
            value = int(default)
        return max(low, min(value, high))

    width = number("SUNSHINE_CLIENT_WIDTH", fallback[0], 640, st.max_width)
    height = number("SUNSHINE_CLIENT_HEIGHT", fallback[1], 480, st.max_height)
    fps = number("SUNSHINE_CLIENT_FPS", fallback[2], 24, st.max_fps)
    # Encoders want even dimensions.
    return hypr.format_mode(width - width % 2, height - height % 2, fps)


def unit_state(unit: str) -> dict:
    try:
        proc = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState,MainPID,UnitFileState,LoadState"],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {"loaded": False, "active": False, "pid": 0, "enabled": False}
    props = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    return {
        "loaded": props.get("LoadState") == "loaded",
        "active": props.get("ActiveState") in ("active", "activating"),
        "pid": int(props.get("MainPID") or 0),
        "enabled": props.get("UnitFileState") == "enabled",
    }


def _systemctl(action: str, unit: str) -> None:
    try:
        proc = subprocess.run(["systemctl", "--user", action, unit], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StreamError(f"systemctl --user {action} {unit}: {exc}") from exc
    if proc.returncode != 0:
        raise StreamError(f"systemctl --user {action} {unit}: {proc.stderr.strip() or proc.stdout.strip()}")


def encoder_sessions() -> int | None:
    """Active NVENC sessions, or None when that cannot be read. Sunshine's
    traffic is unconnected UDP, so there is no socket to look for instead."""
    try:
        proc = subprocess.run(["nvidia-smi", "--query-gpu=encoder.stats.sessionCount", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=5)
        return sum(int(line) for line in proc.stdout.split()) if proc.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _real_display_on(output: str, monitors: list[dict]) -> bool:
    return any(m.get("name") != output and not m.get("disabled") for m in monitors)


def _parked_overlay(output: str, mode: str, scale: float, standby: bool) -> str:
    return luagen.render_stream(output, mode, scale, standby=standby)


def _output(name: str, monitors: list[dict] | None = None) -> dict | None:
    for mon in hypr.monitors() if monitors is None else monitors:
        if mon.get("name") == name:
            return mon
    return None


def _run_quiet(argv) -> None:
    try:
        subprocess.run(list(argv), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_hooks(commands: list[str]) -> None:
    for cmd in commands:
        _run_quiet(["bash", "-lc", cmd])


def _stay_awake_flag() -> bool:
    home = os.environ.get("XDG_STATE_HOME", "").strip() or os.path.expanduser("~/.local/state")
    return os.path.exists(os.path.join(home, "omarchy", "indicators", "stay-awake"))


def _home_workspace(monitors: list[dict], output: str) -> int | None:
    """The workspace the user is looking at on a real display, before we take over."""
    real = [m for m in monitors if m.get("name") != output and not m.get("disabled")]
    real.sort(key=lambda m: not m.get("focused"))
    for mon in real:
        ws = int((mon.get("activeWorkspace") or {}).get("id") or 0)
        if ws > 0:
            return ws
    return None


def _active_workspace(monitors: list[dict]) -> int:
    focused = next((m for m in monitors if m.get("focused") and not m.get("disabled")), None)
    return int(((focused or {}).get("activeWorkspace") or {}).get("id") or 0)


def _bring_windows_home(output: str, home_ws) -> None:
    """After the real displays are back, nothing may be left on the virtual one.

    Workspaces that came from a real display return by themselves. The parking
    workspace, and any workspace first opened during the stream, belong to the
    virtual output and would strand their windows 20000 pixels off-screen.
    """
    try:
        monitors = hypr.monitors()
        target = int(home_ws or 0) or _home_workspace(monitors, output)
        if not target or not any(m.get("name") != output and not m.get("disabled") for m in monitors):
            return  # no real display to come home to (TV off): they stay reachable over the next stream
        stranded = {w.get("id") for w in hypr.workspaces() if w.get("monitor") == output}
        stranded.discard(target)
        for client in hypr.clients():
            if (client.get("workspace") or {}).get("id") in stranded:
                hypr.move_window(str(client.get("address")), target)
        _park_workspace(output, target, monitors)
    except (hypr.HyprError, ValueError):
        pass


def _park_workspace(output: str, home_ws: int, monitors: list[dict]) -> None:
    """Leave the parked output on its private workspace. If it kept an ordinary
    one (say 3), SUPER+3 on the real display would jump to a screen nobody can see."""
    mon = _output(output, monitors)
    showing = int(((mon or {}).get("activeWorkspace") or {}).get("id") or 0)
    if mon is None or mon.get("disabled") or showing == luagen.PARK_WORKSPACE:
        return
    hypr.focus_workspace(luagen.PARK_WORKSPACE)  # bound to the virtual output by the overlay's workspace rule
    hypr.focus_workspace(home_ws)


def _apply_overlay(text: str | None) -> bool:
    """Write the stream overlay and make sure Hyprland accepted it."""
    target = paths.stream_lua()
    previous = fsutil.read_text(target)
    if previous == text:
        return False
    scenes.write_overlay(target, text)
    try:
        scenes.reload_checked(target, previous, "stream overlay")
    except scenes.SceneError as exc:
        raise StreamError(str(exc)) from exc
    return True


# -- arm / disarm -----------------------------------------------------------------


def arm(st: StreamConfig) -> dict:
    """Bring the host to `armed`. Safe to run repeatedly: the shell calls it on
    every start and plugin hot-reload, so it must never disturb a live stream."""
    if not is_enabled():
        return {"armed": False, "reason": "switched off"}
    with _locked():
        state = load_state()
        if state.get("attached"):
            reason = _stale_attachment(state)
            if reason is None:
                return {"armed": True, "attached": True, "kept": True}
            _detach_locked(state, reason)
            state = load_state()

        before = hypr.monitors()
        _apply_overlay(_parked_overlay(st.output, st.default_mode, st.scale,
                                       standby=not _real_display_on(st.output, before)))
        created = False
        if _output(st.output) is None:
            hypr.create_headless(st.output)  # the workspace rule is already in place, so it starts on its own workspace
            created = True

        unit = unit_state(st.unit)
        if st.manage_unit:
            if not unit["loaded"]:
                raise StreamError(f"{st.unit} is not installed; run `battlestation setup stream`")
            if not unit["active"]:
                _systemctl("start", st.unit)
            elif created:
                _systemctl("restart", st.unit)  # it started before the output existed
            unit = unit_state(st.unit)

        try:
            mons = hypr.monitors()
            home = _home_workspace(before, st.output) or _home_workspace(mons, st.output)
            if home:
                _park_workspace(st.output, home, mons)
                if created and _active_workspace(mons) != home:
                    hypr.focus_workspace(home)  # a new output takes focus, even one its rule switches straight off
        except hypr.HyprError:
            pass
        state.update({"armed": True, "attached": False, "output": st.output, "parkedMode": st.default_mode,
                      "scale": st.scale, "unit": st.unit, "instance": hypr.instance_signature() or ""})
        save_state(state)
        return {"armed": True, "attached": False, "created": created, "unit": unit}


def disarm(st: StreamConfig | None = None) -> dict:
    """Back to `off`: no virtual output, no Sunshine, no overlay file."""
    with _locked():
        state = load_state()
        if state.get("attached"):
            _detach_locked(state, "switched off")
            state = load_state()
        output = (st.output if st else "") or state.get("output") or "BS-STREAM"
        unit = (st.unit if st else "") or state.get("unit") or ""
        manage = st.manage_unit if st else True
        if manage and unit and unit_state(unit)["active"]:
            _systemctl("stop", unit)
        # Overlay first: Hyprland ignores `output remove` for a switched-off
        # output, which would then come back as an empty monitor on the reload.
        _apply_overlay(None)
        try:
            if _output(output) is not None:
                hypr.remove_output(output)
        except hypr.HyprError:
            pass
        try:
            paths.stream_state().unlink()
        except FileNotFoundError:
            pass
        return {"armed": False, "attached": False}


def _stale_attachment(state: dict) -> str | None:
    """Why a recorded attachment no longer holds, or None if it still does."""
    if state.get("instance") != (hypr.instance_signature() or ""):
        return "compositor restarted"
    unit = unit_state(state.get("unit") or "")
    if not unit["active"]:
        return "sunshine stopped"
    if state.get("unitPid") and unit["pid"] != state.get("unitPid"):
        return "sunshine restarted"
    return None


# -- attach / detach ----------------------------------------------------------------


def attach(st: StreamConfig, env=None) -> dict:
    env = os.environ if env is None else env
    mode = client_mode(env, st)
    with _locked():
        return _attach_locked(st, mode)


def _attach_locked(st: StreamConfig, mode: str) -> dict:
    instance = hypr.instance_signature()
    if not instance:
        raise StreamError("no running Hyprland session found")
    # A layout still on trial must not become what we restore to.
    if scenes.load_state().get("pendingRevert"):
        scenes.revert()

    monitors = hypr.monitors()
    if _output(st.output, monitors) is None:
        hypr.create_headless(st.output)
    disable = [str(m.get("name")) for m in monitors
               if m.get("name") != st.output and hypr.CONNECTOR_RE.match(str(m.get("name") or ""))]
    real_on = _real_display_on(st.output, monitors)

    state = load_state()
    idle_was_set = state.get("idleWasSet") if state.get("attached") else _stay_awake_flag()
    home_ws = state.get("homeWs") if state.get("attached") else _home_workspace(monitors, st.output)
    if state.get("attached"):
        real_on = bool(state.get("realOn", True))  # the real displays are already off by now
    _apply_overlay(luagen.render_stream(st.output, mode, st.scale, attached=True, disable=disable, instance=instance))
    # The workspaces have moved over, but the virtual output is still showing
    # its own empty parking workspace. Show the client the desktop instead,
    # so what they open lands on a workspace that goes home with them.
    if home_ws:
        try:
            hypr.focus_workspace(home_ws)
        except hypr.HyprError:
            pass

    set_idle = bool(st.idle_inhibit and not idle_was_set)
    if set_idle:
        _run_quiet(STAY_AWAKE)
    state.update({
        "armed": True, "attached": True, "output": st.output, "mode": mode, "parkedMode": st.default_mode,
        "scale": st.scale, "unit": st.unit, "unitPid": unit_state(st.unit)["pid"], "instance": instance,
        "disabled": disable, "attachedAt": int(time.time()), "idleWasSet": bool(idle_was_set),
        "idleSetByUs": set_idle or bool(state.get("idleSetByUs")), "onDetach": list(st.on_detach),
        "homeWs": home_ws, "realOn": real_on,
    })
    state.pop("idleSince", None)
    state.pop("resumable", None)
    save_state(state)
    _run_hooks(st.on_attach)
    return {"attached": True, "mode": mode, "disabled": disable}


def detach(reason: str = "client left", resumable: bool = False) -> dict:
    """Give the desktop back. Never needs the config and never raises for a
    reason the user cannot act on: this is the path that turns the TV back on."""
    with _locked():
        return _detach_locked(load_state(), reason, resumable)


def _detach_locked(state: dict, reason: str, resumable: bool = False) -> dict:
    if not state.get("attached"):
        text = fsutil.read_text(paths.stream_lua()) or ""
        if "Stream overlay: attached" in text:
            # State was lost but the overlay still blanks the displays. Dropping
            # the file is always safe; the next arm writes the parked form.
            _apply_overlay(None)
            return {"attached": False, "recovered": True}
        return {"attached": False, "changed": False}

    output = state.get("output") or "BS-STREAM"
    try:
        # Standby only if no real display was on to come back; the watchdog corrects a wrong guess.
        _apply_overlay(_parked_overlay(output, state.get("parkedMode") or "1920x1080@60",
                                       float(state.get("scale") or 1.0), standby=not state.get("realOn", True)))
    except (StreamError, hypr.HyprError):
        scenes.write_overlay(paths.stream_lua(), None)  # last resort: Hyprland reloads on the change by itself
    _bring_windows_home(output, state.get("homeWs"))
    if state.get("idleSetByUs"):
        _run_quiet(ALLOW_IDLE)
    hooks = [c for c in state.get("onDetach") or [] if isinstance(c, str)]
    last_mode = state.get("mode")
    for key in ("mode", "disabled", "attachedAt", "idleWasSet", "idleSetByUs", "onDetach", "idleSince", "unitPid",
                "homeWs", "realOn"):
        state.pop(key, None)
    state["attached"] = False
    state["lastDetach"] = {"reason": reason, "at": int(time.time())}
    if resumable and last_mode:
        state["resumable"] = last_mode  # Moonlight can resume a paused app without Sunshine re-running `do`
    else:
        state.pop("resumable", None)
    save_state(state)
    _run_hooks(hooks)
    return {"attached": False, "changed": True, "reason": reason}


def settle() -> dict:
    """After a display comes back: nothing left on the parked output.

    A TV in standby drops off HDMI, the virtual output is then the only one
    left, and Hyprland does not always hand every workspace back on reconnect.
    Reads the runtime state, never the config: the shell runs it on every
    hotplug. Under the lock so it cannot race detach bringing windows home.
    """
    with _locked():
        state = load_state()
        if state.get("attached"):
            return {"settled": False, "skipped": "attached"}
        if not state.get("armed"):
            return {"settled": False, "skipped": "not armed"}
        output = state.get("output") or "BS-STREAM"
        moved = scenes.reclaim_from_virtual(output)
        scenes.recover_cursor(output, only_if_lost=not moved)
        return {"settled": True, "moved": moved}


# -- watchdog and status -------------------------------------------------------------


def watchdog(st: StreamConfig, now: float | None = None) -> dict:
    """Sunshine does not always run `undo` (crash, client power-off, a paused
    app), and does not re-run `do` on resume. Put both right from the outside."""
    now = time.time() if now is None else now
    with _locked():
        state = load_state()
        sessions = encoder_sessions()
        if state.get("attached"):
            reason = _stale_attachment(state)
            if reason:
                return {"action": "detach", **_detach_locked(state, reason)}
            if sessions == 0:
                since = state.setdefault("idleSince", now)
                if now - since >= st.watchdog_idle_s:
                    return {"action": "detach", **_detach_locked(state, "no client", resumable=True)}
                save_state(state)
                return {"action": "waiting", "idleFor": int(now - since)}
            if state.pop("idleSince", None) is not None:
                save_state(state)
            return {"action": "none", "sessions": sessions}
        if state.get("resumable") and sessions and unit_state(st.unit)["active"]:
            return {"action": "attach", **_attach_locked(st, str(state["resumable"]))}
        if state.get("armed") and is_enabled():
            _standby_follows_displays(st)
        return {"action": "none", "sessions": sessions}


def _standby_follows_displays(st: StreamConfig) -> None:
    """Parked: lit only while no real display is on (the TV was switched off or
    unplugged), switched off again as soon as one is back."""
    try:
        monitors = hypr.monitors()
        if _output(st.output, monitors) is None:
            return
        _apply_overlay(_parked_overlay(st.output, st.default_mode, st.scale,
                                       standby=not _real_display_on(st.output, monitors)))
    except (StreamError, hypr.HyprError):
        pass  # the next tick tries again


def status(st: StreamConfig) -> dict:
    state = load_state()
    try:
        present = _output(st.output) is not None
    except hypr.HyprError:
        present = False
    unit = unit_state(st.unit)
    enabled = is_enabled()
    attached = bool(state.get("attached"))
    armed = enabled and present and (unit["active"] or not st.manage_unit)
    phase = "streaming" if attached else "armed" if armed else "arming" if enabled else "off"
    return {
        "available": st.configured, "enabled": enabled, "phase": phase, "armed": armed, "attached": attached,
        "mode": state.get("mode") or "", "output": st.output, "outputPresent": present, "unit": unit,
        "lastDetach": state.get("lastDetach"), "remote": tailscale.summary(),
    }
