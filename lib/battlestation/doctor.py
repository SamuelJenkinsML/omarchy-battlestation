"""`battlestation doctor`: explain what works, what doesn't, and why."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config as config_mod
from . import controller, drmprops, edid, fsutil, hypr, mangohud, paths, pad, scenes, stream, sunshine, tailscale, tv


@dataclass
class Check:
    area: str
    status: str  # ok | warn | fail | info
    message: str

    def to_dict(self) -> dict:
        return {"area": self.area, "status": self.status, "message": self.message}


def run() -> list[Check]:
    checks: list[Check] = []
    add = lambda area, status, msg: checks.append(Check(area, status, msg))  # noqa: E731

    # Config
    cfg = None
    try:
        cfg = config_mod.load()
        add("config", "ok", f"{paths.config_file()} ({len(cfg.scenes)} scenes)")
    except config_mod.ConfigError as exc:
        for p in exc.problems:
            add("config", "fail", p)

    # Hyprland
    try:
        monitors = hypr.monitors()
    except hypr.HyprError as exc:
        add("hyprland", "fail", str(exc))
        monitors = []
    toggles = "/usr/share/omarchy/default/hypr/toggles.lua"
    try:
        with open(toggles, encoding="utf-8") as fh:
            loads_dir = "toggles/hypr" in fh.read()
    except OSError:
        loads_dir = False
    add("hyprland", "ok" if loads_dir else "fail",
        "Omarchy loads the toggles overlay directory" if loads_dir
        else "Omarchy's toggles loader was not found; scene files will not be applied")
    errors = hypr.config_errors() if monitors else []
    add("hyprland", "warn" if errors else "ok",
        ("config errors: " + "; ".join(errors)) if errors else "no Hyprland config errors")

    # Displays
    try:
        drm = drmprops.read()
    except OSError:
        drm = {}
    virtual = cfg.stream.output if cfg else "BS-STREAM"
    for mon in monitors:
        name = str(mon.get("name"))
        if name == virtual:
            continue  # reported under "stream"
        caps = edid.read_caps(name)
        w, h, hz = int(mon.get("width") or 0), int(mon.get("height") or 0), float(mon.get("refreshRate") or 0)
        bits = []
        bits.append("HDR10" if caps.hdr else "no HDR")
        bits.append("BT.2020" if caps.wide_gamut else "sRGB gamut")
        vrr_cap = (drm.get(name) or {}).get("vrr_capable")
        bits.append("VRR capable" if vrr_cap else ("VRR hinted by EDID" if caps.vrr_hint else "no VRR"))
        if caps.is_hdmi:
            link = f"HDMI {'2.1 FRL ' + str(caps.max_frl_gbps) + ' Gbps' if caps.max_frl_gbps else 'TMDS ' + str(caps.max_tmds_mhz or 340) + ' MHz'}"
            bits.append(link)
        bpc = edid.max_bpc_for_mode(caps, w, h, hz) if w else 8
        add("display", "info", f"{name} ({mon.get('description')}): {w}x{h}@{hz:.2f}, " + ", ".join(bits)
            + f"; max {bpc}-bit at this mode")
        if caps.hdr and bpc < 10:
            add("display", "warn", f"{name}: HDR at {w}x{h}@{hz:.0f} runs at 8-bit because the link cannot carry 10-bit RGB"
                + (" (enable the TV's enhanced HDMI mode, e.g. Samsung 'Input Signal Plus', and use an HDMI 2.1 port/cable for more)"
                   if caps.is_hdmi and not caps.max_frl_gbps else ""))

    if cfg:
        for scene in cfg.scenes.values():
            try:
                res = scenes.resolve(scene, scenes.real_monitors(cfg, monitors))
                status = "warn" if res.missing or res.notes else "ok"
                detail = "; ".join(res.notes + [f"not connected: {m}" for m in res.missing]) or "all monitors available"
                add("scene", status, f"{scene.name}: {detail}")
            except scenes.SceneError as exc:
                add("scene", "warn", f"{scene.name}: {exc}")

    # GPU
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            add("gpu", "ok", out or "nvidia-smi returned nothing")
        except (OSError, subprocess.TimeoutExpired) as exc:
            add("gpu", "warn", f"nvidia-smi failed: {exc}")
    else:
        add("gpu", "warn", "nvidia-smi not found; the GPU segment stays hidden (NVIDIA only for now)")

    # FPS
    pkgs = controller.installed_packages(["mangohud", "lib32-mangohud", "steam", "xpadneo-dkms", "xone-dkms"])
    if pkgs["mangohud"]:
        add("fps", "ok" if pkgs["lib32-mangohud"] else "warn",
            "mangohud installed" + ("" if pkgs["lib32-mangohud"] else "; lib32-mangohud missing (32-bit games show no FPS)"))
    else:
        add("fps", "fail", "mangohud not installed: sudo pacman -S mangohud lib32-mangohud")
    add("fps", "ok" if mangohud.override_active() else "warn",
        "Steam launcher starts games with MangoHud" if mangohud.override_active()
        else "run `battlestation setup mangohud` so Steam games log FPS")

    # Controller
    drivers = controller.loaded_drivers()
    add("controller", "info", "drivers loaded: " + (", ".join(drivers) or "none (no pad connected, or driver missing)")
        + f"; installed: xpadneo-dkms={'yes' if pkgs['xpadneo-dkms'] else 'no'}, xone-dkms={'yes' if pkgs['xone-dkms'] else 'no'}")
    bats = controller.batteries()
    if bats:
        for b in bats:
            level = f"{b['percent']}%" if b["percent"] is not None else (b["level"] or "?")
            add("controller", "ok", f"{b['model'] or b['source']}: battery {level}, {b['status']}")
    if cfg:
        try:
            codes = pad.chord_codes(cfg.controller.chord)
        except ValueError as exc:
            add("controller", "fail", str(exc))
            codes = frozenset()
        if pad.BTN["BTN_MODE"] in codes and controller.xpadneo_shift_mode_disabled() is False:
            add("controller", "warn", "chord uses the Guide button but xpadneo delays it until release; set "
                "`options hid_xpadneo disable_shift_mode=1` in /etc/modprobe.d/")

    # TV
    if cfg and cfg.tv.configured:
        state = tv.power_state(cfg.tv.host)
        add("tv", "ok" if state != "off" else "warn", f"{cfg.tv.host}: {state}"
            + ("" if cfg.tv.mac else "; no MAC set, so Wake-on-LAN from full power-off is impossible"))
        add("tv", "ok" if tv.read_token() else "warn",
            "paired" if tv.read_token() else "not paired: run `battlestation tv pair` and accept on the TV")
    else:
        add("tv", "info", "no [tv] section; TV wake/input switching disabled")

    if cfg:
        _stream_checks(cfg.stream, monitors, add)

    add("steam", "ok" if pkgs["steam"] else "warn", "steam installed" if pkgs["steam"] else "steam not installed")
    return checks


def _ufw_allows_streaming() -> bool | None:
    """None when the rules cannot be read (no ufw, or not world-readable)."""
    text = fsutil.read_text(Path("/etc/ufw/user.rules"))
    if text is None:
        return None
    return any("47989" in line and line.startswith("### tuple ### allow") for line in text.splitlines())


def _stream_checks(st, monitors: list[dict], add) -> None:
    if not st.configured:
        add("stream", "info", "no [stream] section; streaming host disabled (see `battlestation setup stream`)")
        return
    enabled = stream.is_enabled()
    add("stream", "info", "switched " + ("on" if enabled else "off") + " (panel switch, or `battlestation stream on|off`)")

    version = sunshine.installed_version()
    if not version:
        add("stream", "fail", "sunshine not installed; run `battlestation setup stream` for the steps")
        return
    add("stream", "ok" if sunshine.version_ok(version) else "fail",
        f"sunshine {version}" + ("" if sunshine.version_ok(version) else
                                 "; needs 2026.914.233613 or newer (virtual-output capture fix and a Linux "
                                 "security advisory): install lizardbyte/sunshine"))

    unit = stream.unit_state(st.unit)
    if not unit["loaded"]:
        add("stream", "fail", f"{st.unit} not found; set stream.unit to your Sunshine user unit")
    elif unit["enabled"] and st.manage_unit:
        add("stream", "warn", f"{st.unit} is enabled, so it can start before the virtual display exists and capture "
            f"the wrong screen: systemctl --user disable {st.unit}")
    else:
        add("stream", "ok", "Sunshine unit " + ("running" if unit["active"] else "stopped") + ", started on demand")

    missing = sunshine.missing_keys(st, fsutil.read_text(paths.sunshine_conf()))
    add("stream", "warn" if missing else "ok",
        (f"sunshine.conf is missing {', '.join(missing)}" +
         (" (its admin page is open to the network)" if "origin_web_ui_allowed" in missing else "") +
         ": battlestation setup stream --write-config") if missing
        else "sunshine.conf captures the virtual output and calls attach/detach")

    present = any(m.get("name") == st.output for m in monitors)
    if enabled:
        add("stream", "ok" if present else "warn",
            f"virtual output {st.output} " + ("present" if present else "missing; toggle streaming off and on"))
    state = stream.load_state()
    if state.get("attached"):
        add("stream", "info", f"a client is attached at {state.get('mode')}; the real displays are off until it leaves "
            "(`battlestation stream detach` gives them back)")

    add("stream", "ok" if os.access("/dev/uinput", os.W_OK) else "fail",
        "/dev/uinput writable (virtual controllers work)" if os.access("/dev/uinput", os.W_OK)
        else '/dev/uinput not writable, so the stream would have no input: sudo usermod -aG input "$USER"')

    allowed = _ufw_allows_streaming()
    if allowed is not None:
        add("stream", "ok" if allowed else "warn", "firewall allows the streaming ports" if allowed
            else "ufw has no rule for the streaming ports; see `battlestation setup stream`")

    _tailscale_checks(tailscale.status(), add)

    # Things that stop a headless host from being reachable at all.
    try:
        with open("/proc/cmdline", encoding="utf-8") as fh:
            cmdline = fh.read()
        encrypted = "cryptdevice=" in cmdline or "rd.luks" in cmdline
    except OSError:
        encrypted = False
    if encrypted:
        add("stream", "info", "encrypted root: after a reboot the machine waits for its passphrase and cannot be "
            "streamed to until someone types it. Leave it running.")
    try:
        proc = subprocess.run(["omarchy-toggle-enabled", "suspend-off"], capture_output=True, timeout=5)
        if proc.returncode != 0:
            add("stream", "warn", "Suspend is still in the system menu, and a suspended host is unreachable: omarchy-toggle-suspend")
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not shutil.which("nvidia-smi"):
        add("stream", "warn", "nvidia-smi not found: the watchdog cannot tell when a client vanished, only when Sunshine stops")


def _tailscale_checks(ts: dict, add) -> None:
    info = tailscale.summary(ts)
    if not ts["installed"]:
        add("stream", "info", "Tailscale not installed: streaming works on the home network only")
        return
    if not info["on"]:
        add("stream", "info", "Tailscale is off: streaming works on the home network only. " +
            (info["hint"] or "Switch on \"Stream away from home\" in the panel"))
        return
    add("stream", "ok", f"Tailscale up as {ts['name'] or ts['ip']} ({ts['ip']}); add that name in Moonlight")
    if not ts["operator"]:
        add("stream", "warn", f"the panel cannot switch Tailscale: {tailscale.OPERATOR_FIX}")
    days = tailscale.key_days_left(ts)
    if days is not None and days < 30:
        add("stream", "warn", f"this machine's Tailscale key expires in {max(days, 0)} days and remote streaming stops "
            "with it: disable key expiry for it at login.tailscale.com")
    for peer in ts["peers"]:
        if peer["online"] and peer["active"] and peer["relay"] and not peer["direct"]:
            add("stream", "warn", f"{peer['host']} is connected through a relay ({peer['relay']}), too slow to stream "
                f"over: battlestation stream remote-check {peer['host']}")


def format_text(checks: list[Check]) -> str:
    icon = {"ok": "✓", "warn": "!", "fail": "✗", "info": "·"}
    width = max((len(c.area) for c in checks), default=6)
    return "\n".join(f"{icon[c.status]} {c.area.ljust(width)}  {c.message}" for c in checks)
