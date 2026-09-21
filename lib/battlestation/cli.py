"""`battlestation` command line. Most subcommands print JSON for Service.qml."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import tomllib

from . import PLUGIN_ID, __version__
from . import config as config_mod
from . import controller, doctor, drmprops, edid, fsutil, hypr, mangohud, pad, paths, scenes, steam, stream, sunshine, tv


def out(obj) -> None:
    print(json.dumps(obj, indent=None, separators=(",", ":")))


def fail(message: str, code: int = 1) -> int:
    out({"ok": False, "error": message})
    return code


def notify(title: str, body: str = "", urgency: str = "normal", glyph: str = "󰊗") -> None:
    try:
        subprocess.Popen(["omarchy-notification-send", "-g", glyph, "-u", urgency, title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


# -- commands ---------------------------------------------------------------------


def cmd_init(args) -> int:
    target = paths.config_file()
    if target.exists() and not args.force:
        return fail(f"{target} already exists (use --force to overwrite)")
    text = scenes.init_text()
    config_mod.parse(tomllib.loads(text))  # never write an invalid file
    fsutil.atomic_write(target, text)
    out({"ok": True, "config": str(target)})
    return 0


def cmd_state(args) -> int:
    state = scenes.load_state()
    result = {
        "ok": True,
        "version": __version__,
        "active": state.get("active", ""),
        "previous": state.get("previous", ""),
        "pendingRevert": state.get("pendingRevert"),
        "layout": state.get("layout", {}),
        "notes": state.get("notes", []),
        "configPath": str(paths.config_file()),
        "stateFile": str(paths.state_file()),
    }
    try:
        cfg = config_mod.load()
        result.update({
            "scenes": [{"name": s.name, "label": s.label, "icon": s.icon, "steam": s.steam,
                        "wakeTv": s.wake_tv, "onBigPictureExit": s.on_bigpicture_exit}
                       for s in cfg.scenes.values()],
            "chordScenes": cfg.chord_scenes,
            "defaultScene": cfg.default_scene,
            "keepSeconds": cfg.keep_seconds,
            "autoSceneOnHotplug": cfg.auto_scene_on_hotplug,
            "controller": {"chord": cfg.controller.chord, "holdMs": cfg.controller.hold_ms,
                           "ignoreVendor": cfg.controller.ignore_vendor},
            "tvConfigured": cfg.tv.configured,
            "stream": stream.status(cfg.stream),
            "configErrors": [],
        })
    except config_mod.ConfigError as exc:
        result.update({"scenes": [], "chordScenes": [], "configErrors": exc.problems})
    out(result)
    return 0


def cmd_caps(args) -> int:
    try:
        drm = drmprops.read()
    except OSError:
        drm = {}
    result = {}
    for mon in hypr.monitors():
        name = str(mon.get("name"))
        if stream.load_state().get("output") == name:
            continue  # the virtual streaming output has no EDID or link to describe
        caps = edid.read_caps(name)
        w, h, hz = int(mon.get("width") or 0), int(mon.get("height") or 0), float(mon.get("refreshRate") or 0)
        d = caps.to_dict()
        d.update({
            "vrrCapable": bool((drm.get(name) or {}).get("vrr_capable")),
            "vrrEnabled": (drm.get(name) or {}).get("vrr_enabled"),
            "maxBpcAtMode": edid.max_bpc_for_mode(caps, w, h, hz) if w else None,
        })
        result[name] = d
    out({"ok": True, "monitors": result})
    return 0


def _load_cfg():
    return config_mod.load()


def cmd_scene_apply(args) -> int:
    cfg = _load_cfg()
    out({"ok": True, **scenes.apply(cfg, args.name, confirm=not args.no_confirm)})
    return 0


def cmd_scene_render(args) -> int:
    cfg = _load_cfg()
    text, res = scenes.render(cfg.scene(args.name), scenes.real_monitors(cfg))
    sys.stdout.write(text)
    for note in res.notes + [f"not connected: {m}" for m in res.missing]:
        sys.stderr.write(f"note: {note}\n")
    return 0


def _run_hooks(commands: list[str], label: str) -> list[dict]:
    results = []
    for cmd in commands:
        try:
            proc = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=15)
            results.append({"hook": label, "cmd": cmd, "rc": proc.returncode})
        except subprocess.TimeoutExpired:
            results.append({"hook": label, "cmd": cmd, "rc": None, "error": "timeout"})
    return results


def _wait_for_monitor(scene, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            mons = hypr.monitors()
        except hypr.HyprError:
            mons = []
        if any(scenes.find_monitor(rule.match, mons) for rule in scene.monitors):
            return True
        time.sleep(0.5)
    return False


def cmd_scene_enter(args) -> int:
    """Full scene switch: hooks, TV wake, layout (with Keep/Revert), input, Steam."""
    cfg = _load_cfg()
    scene = cfg.scene(args.name)
    state = scenes.load_state()
    previous = state.get("active", "")
    steps: list[dict] = []

    if previous and previous != scene.name and previous in cfg.scenes:
        steps += _run_hooks(cfg.scenes[previous].on_exit, f"{previous}.on_exit")

    if scene.wake_tv:
        try:
            woke = tv.wake(cfg.tv)
            steps.append({"step": "tv-wake", **woke})
        except tv.TvError as exc:
            notify("TV did not wake", str(exc), "critical", "󰔂")
            return fail(f"tv wake: {exc}")
        if not _wait_for_monitor(scene, cfg.tv.wake_timeout_s):
            notify("TV is on but no display appeared", "Check the TV input and cable.", "critical", "󰔂")
            return fail("scene monitor never appeared after waking the TV")

    applied = scenes.apply(cfg, scene.name, confirm=not args.no_confirm)
    steps.append({"step": "layout", **applied})
    for note in applied.get("notes", []):
        steps.append({"step": "note", "message": note})

    if scene.tv_input and cfg.tv.configured:
        try:
            keys = tv.select_input(cfg.tv)
            steps.append({"step": "tv-input", "keys": keys})
        except tv.TvError as exc:
            steps.append({"step": "tv-input", "error": str(exc)})

    steps += _run_hooks(scene.on_enter, f"{scene.name}.on_enter")

    if scene.steam == "bigpicture":
        try:
            how = steam.open_big_picture()
            steps.append({"step": "steam", "launch": how})
        except RuntimeError as exc:
            steps.append({"step": "steam", "error": str(exc)})

    out({"ok": True, "scene": scene.name, "previous": previous, "steps": steps,
         "pendingRevert": applied.get("pendingRevert")})
    return 0


def cmd_scene_auto(args) -> int:
    if stream.load_state().get("attached"):
        # Attaching switches the displays off, which looks like a hotplug. The
        # stream overlay owns the layout until the client leaves.
        out({"ok": True, "changed": False, "skipped": "streaming"})
        return 0
    cfg = _load_cfg()
    name = scenes.pick_auto(cfg, scenes.real_monitors(cfg))
    if not name:
        return fail("no scene matches the connected monitors")
    if name == scenes.load_state().get("active") and not args.force:
        out({"ok": True, "scene": name, "changed": False})
        return 0
    out({"ok": True, **scenes.apply(cfg, name, confirm=True)})
    return 0


def cmd_scene_keep(args) -> int:
    out({"ok": True, **scenes.keep()})
    return 0


def cmd_scene_revert(args) -> int:
    out({"ok": True, **scenes.revert()})
    return 0


def cmd_scene_revert_if_due(args) -> int:
    out({"ok": True, **scenes.revert_if_due()})
    return 0


def cmd_scene_clear(args) -> int:
    out({"ok": True, **scenes.clear()})
    return 0


def cmd_scene_capture(args) -> int:
    cfg_path = paths.config_file()
    existing = fsutil.read_text(cfg_path)
    if existing is None:
        return fail("no config yet; run `battlestation init` first")
    cfg = config_mod.load()
    if args.name in cfg.scenes:
        return fail(f"scene '{args.name}' already exists; pick another name or edit {cfg_path}")
    scene = scenes.capture_scene(args.name, args.label, skip_output=cfg.stream.output)
    new_text = existing.rstrip("\n") + "\n" + scenes.append_scene_text(scene)
    config_mod.parse(tomllib.loads(new_text))
    fsutil.atomic_write(cfg_path, new_text)
    out({"ok": True, "scene": args.name, "config": str(cfg_path)})
    return 0


def cmd_pad_daemon(args) -> int:
    try:
        cfg = config_mod.load()
        ctl = cfg.controller
        chord, hold, ignore = ctl.chord, ctl.hold_ms, ctl.ignore_vendor
    except config_mod.ConfigError:
        chord, hold, ignore = ["BTN_SELECT", "BTN_START"], 1000, ["28de"]
    pad.Daemon(chord, hold, ignore).run()
    return 0


def cmd_fps_tail(args) -> int:
    return mangohud.main_tail()


def cmd_tv(args) -> int:
    cfg = _load_cfg()
    if not cfg.tv.configured:
        return fail("add a [tv] section with host (and mac) to the config first")
    try:
        if args.action == "status":
            data = tv.info(cfg.tv.host)
            device = (data or {}).get("device") or {}
            out({"ok": True, "state": tv.power_state(cfg.tv.host), "name": device.get("name"),
                 "model": device.get("modelName"), "wifiMac": device.get("wifiMac"),
                 "paired": bool(tv.read_token())})
        elif args.action == "pair":
            sys.stderr.write("Accept the connection prompt on the TV...\n")
            tv.pair(cfg.tv)
            out({"ok": True, "paired": True})
        elif args.action == "wake":
            out({"ok": True, **tv.wake(cfg.tv)})
        elif args.action == "input":
            out({"ok": True, "keys": tv.select_input(cfg.tv)})
        elif args.action == "key":
            tv.send_keys(cfg.tv, args.keys)
            out({"ok": True, "keys": args.keys})
    except tv.TvError as exc:
        return fail(str(exc))
    return 0


def cmd_stream(args) -> int:
    action = args.action
    if action == "detach":
        # Sunshine's `undo`, the watchdog and the panic keybind all land here.
        # It turns the TV back on, so it must not depend on a valid config.
        try:
            out({"ok": True, **stream.detach(args.reason or "client left")})
        except Exception as exc:  # noqa: BLE001 - report, never fail the undo
            out({"ok": False, "error": str(exc)})
        return 0
    if action == "off":
        stream.set_enabled(False)
        try:
            st = config_mod.load().stream
        except config_mod.ConfigError:
            st = None
        out({"ok": True, **stream.disarm(st)})
        return 0

    cfg = _load_cfg()
    st = cfg.stream
    if action == "status":
        out({"ok": True, **stream.status(st)})
        return 0
    if not st.configured:
        return fail("add a [stream] section to the config first (see `battlestation setup stream`)")
    if action == "on":
        stream.set_enabled(True)
        result = stream.arm(st)
        notify("Streaming on", "Ready for Moonlight.", glyph="󰑈")
        out({"ok": True, **result})
    elif action == "arm":
        out({"ok": True, **stream.arm(st)})
    elif action == "disarm":
        out({"ok": True, **stream.disarm(st)})
    elif action == "attach":
        if not stream.is_enabled():
            out({"ok": True, "attached": False, "skipped": "switched off"})
            return 0
        out({"ok": True, **stream.attach(st)})
    elif action == "watchdog":
        if not stream.is_enabled():
            out({"ok": True, "action": "none", "skipped": "switched off"})
            return 0
        out({"ok": True, **stream.watchdog(st)})
    return 0


def cmd_steam(args) -> int:
    out({"ok": True, "launch": steam.open_big_picture(), "running": steam.running()})
    return 0


def cmd_setup_mangohud(args) -> int:
    if args.remove:
        out({"ok": True, **mangohud.remove()})
        return 0
    result = mangohud.install(args.style)
    if not mangohud.installed():
        result["warning"] = (result.get("warning", "") + " mangohud is not installed: "
                             "sudo pacman -S mangohud lib32-mangohud").strip()
    out({"ok": True, **result, "note": "Restart Steam so it picks up the new launcher."})
    return 0


def cmd_setup_stream(args) -> int:
    """Guided setup. Prints the root steps for you to run; never runs them."""
    import os

    cfg = _load_cfg()
    st = cfg.stream
    steps = sunshine.privileged_steps(st, uinput_ok=os.access("/dev/uinput", os.W_OK))
    conf_text = fsutil.read_text(paths.sunshine_conf())
    written = None
    if args.write_config:
        try:
            written = sunshine.write_conf(st)
        except ValueError as exc:
            return fail(str(exc))
        conf_text = fsutil.read_text(paths.sunshine_conf())
    missing = sunshine.missing_keys(st, conf_text)
    if args.json:
        out({"ok": True, "configured": st.configured, "steps": steps, "sunshineConf": str(paths.sunshine_conf()),
             "missingKeys": missing, "written": written})
        return 0

    print("Battlestation streaming setup. Nothing below is run for you.\n")
    if not st.configured:
        print(f"0. Add a [stream] section to {paths.config_file()} (an empty one is enough).\n")
    for i, step in enumerate(steps, 1):
        print(f"{i}. {step['title']}\n   {step['why']}")
        for cmd in step["commands"]:
            print(f"     {cmd}")
        print()
    if missing:
        print(f"Sunshine config ({paths.sunshine_conf()}) still needs: {', '.join(missing)}")
        print("   Run `battlestation setup stream --write-config` to merge them; other settings are kept.")
    else:
        print("Sunshine config: ready." + (" (just written)" if written and written["changed"] else ""))
    print("\nThen: set a login at https://localhost:47990, switch streaming on in the panel, pair Moonlight.")
    return 0


def cmd_controller(args) -> int:
    out({"ok": True, "batteries": controller.batteries(), "drivers": controller.loaded_drivers(),
         "xpadneoShiftModeDisabled": controller.xpadneo_shift_mode_disabled()})
    return 0


def cmd_doctor(args) -> int:
    checks = doctor.run()
    if args.json:
        out({"ok": True, "checks": [c.to_dict() for c in checks]})
    else:
        print(doctor.format_text(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


def cmd_ipc(args) -> int:
    """Forward to the running shell plugin: `battlestation ipc scene tv-gaming`."""
    return subprocess.call(["omarchy-shell", PLUGIN_ID, *args.rest])


# -- parser -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="battlestation", description="Gaming HUD and display scenes for Omarchy")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write a starter config from the connected displays")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    sub.add_parser("state", help="JSON: active scene, scenes, pending revert").set_defaults(func=cmd_state)
    sub.add_parser("caps", help="JSON: HDR/VRR/link capabilities per monitor").set_defaults(func=cmd_caps)

    sc = sub.add_parser("scene", help="apply, enter, capture and confirm scenes").add_subparsers(dest="scmd", required=True)
    a = sc.add_parser("apply", help="apply only the display layout")
    a.add_argument("name")
    a.add_argument("--no-confirm", action="store_true", help="skip the Keep/Revert window")
    a.set_defaults(func=cmd_scene_apply)
    e = sc.add_parser("enter", help="full switch: hooks, TV wake, layout, input, Steam")
    e.add_argument("name")
    e.add_argument("--no-confirm", action="store_true")
    e.set_defaults(func=cmd_scene_enter)
    r = sc.add_parser("render", help="print the Lua a scene would generate")
    r.add_argument("name")
    r.set_defaults(func=cmd_scene_render)
    c = sc.add_parser("capture", help="save the current layout as a new scene")
    c.add_argument("name")
    c.add_argument("--label")
    c.set_defaults(func=cmd_scene_capture)
    au = sc.add_parser("auto", help="apply the most specific scene whose monitors are all connected")
    au.add_argument("--force", action="store_true")
    au.set_defaults(func=cmd_scene_auto)
    sc.add_parser("keep").set_defaults(func=cmd_scene_keep)
    sc.add_parser("revert").set_defaults(func=cmd_scene_revert)
    sc.add_parser("revert-if-due").set_defaults(func=cmd_scene_revert_if_due)
    sc.add_parser("clear", help="remove the scene overlay; monitors.lua takes over").set_defaults(func=cmd_scene_clear)

    sub.add_parser("pad-daemon", help="(internal) controller reader for the shell").set_defaults(func=cmd_pad_daemon)
    sub.add_parser("fps-tail", help="(internal) follow MangoHud logs").set_defaults(func=cmd_fps_tail)

    t = sub.add_parser("tv", help="Samsung TV: pair, wake, input, status, key")
    t.add_argument("action", choices=["pair", "wake", "input", "status", "key"])
    t.add_argument("keys", nargs="*")
    t.set_defaults(func=cmd_tv)

    sub.add_parser("bigpicture", help="open Steam Big Picture").set_defaults(func=cmd_steam)

    st = sub.add_parser("stream", help="headless streaming host: on, off, status, detach")
    st.add_argument("action", choices=["on", "off", "status", "detach", "arm", "disarm", "attach", "watchdog"],
                    help="on/off switch the feature; detach gives the desktop back; "
                         "arm, disarm, attach and watchdog are (internal)")
    st.add_argument("--reason", default="")
    st.set_defaults(func=cmd_stream)

    su = sub.add_parser("setup", help="optional integrations").add_subparsers(dest="what", required=True)
    m = su.add_parser("mangohud", help="MangoHud profile + Steam launcher override for FPS")
    m.add_argument("--style", choices=sorted(mangohud.HUD_STYLES), default="minimal")
    m.add_argument("--remove", action="store_true")
    m.set_defaults(func=cmd_setup_mangohud)

    ss = su.add_parser("stream", help="print the one-time steps for the streaming host")
    ss.add_argument("--write-config", action="store_true", help="merge our keys into sunshine.conf")
    ss.add_argument("--json", action="store_true")
    ss.set_defaults(func=cmd_setup_stream)

    sub.add_parser("controller", help="JSON: controller batteries and drivers").set_defaults(func=cmd_controller)

    d = sub.add_parser("doctor", help="check the setup")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    i = sub.add_parser("ipc", help="call the running shell plugin")
    i.add_argument("rest", nargs=argparse.REMAINDER)
    i.set_defaults(func=cmd_ipc)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except config_mod.ConfigError as exc:
        return fail("config: " + "; ".join(exc.problems))
    except (scenes.SceneError, hypr.HyprError, stream.StreamError) as exc:
        return fail(str(exc))
    except BrokenPipeError:
        return 0
