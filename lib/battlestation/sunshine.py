"""Sunshine's side of streaming: its config file, its version, the root steps.

We merge a handful of keys into sunshine.conf and leave the rest of the file,
and every other file in that directory (credentials, pairing state), alone.
Nothing here runs with privileges: `privileged_steps` only returns the commands
for the user to run, in keeping with the rest of the plugin.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import fsutil, paths, tailscale
from .config import StreamConfig

# First release with the multi-plane DMA-BUF fix for wlroots capture (Sunshine
# PR #5699) and the fix for GHSA-fp6g-27w5-489j.
MIN_VERSION = (2026, 914, 233613)
PREP_KEY = "global_prep_cmd"
_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$")

# 47990, the admin page, is deliberately not here: it answers on this machine only.
TCP_PORTS = "47984,47989,48010"
UDP_PORTS = "47998:48000,48010"


def cli_path() -> str:
    """What Sunshine should run. Its unit does not have ~/.local/bin on PATH."""
    return shutil.which("battlestation") or str(paths.plugin_root() / "bin" / "battlestation")


def wanted_keys(st: StreamConfig) -> dict[str, str]:
    # Sunshine counts Tailscale's 100.64.0.0/10 as LAN, so its default would
    # show the admin page to every device on the tailnet.
    return {"capture": "wlr", "encoder": "nvenc", "output_name": st.output, "origin_web_ui_allowed": "pc"}


def prep_entry(cli: str | None = None) -> dict:
    cli = cli or cli_path()
    return {"do": f"{cli} stream attach", "undo": f"{cli} stream detach", "elevated": "false"}


def _is_ours(entry) -> bool:
    text = str(entry.get("do", "")) if isinstance(entry, dict) else ""
    return "battlestation" in text and "stream attach" in text


def parse_conf(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if m and not line.lstrip().startswith("#"):
            found[m.group(1)] = m.group(2)
    return found


def merge_conf(text: str, wanted: dict[str, str], prep: dict) -> str:
    """Set our keys, keep every other line, and keep other people's prep commands."""
    current = parse_conf(text)
    try:
        entries = json.loads(current.get(PREP_KEY) or "[]")
        if not isinstance(entries, list):
            raise ValueError
    except ValueError:
        raise ValueError(f"{PREP_KEY} in sunshine.conf is not a JSON list; fix or remove that line first") from None
    values = dict(wanted)
    values[PREP_KEY] = json.dumps([e for e in entries if not _is_ours(e)] + [prep], separators=(",", ":"))

    lines, seen = [], set()
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        key = m.group(1) if m and not line.lstrip().startswith("#") else None
        if key in values:
            if key not in seen:
                lines.append(f"{key} = {values[key]}")
                seen.add(key)
            continue  # a repeated key would shadow ours
        lines.append(line)
    lines += [f"{key} = {value}" for key, value in values.items() if key not in seen]
    return "\n".join(lines).rstrip("\n") + "\n"


def missing_keys(st: StreamConfig, text: str | None) -> list[str]:
    current = parse_conf(text or "")
    missing = [k for k, v in wanted_keys(st).items() if current.get(k) != v]
    try:
        entries = json.loads(current.get(PREP_KEY) or "[]")
    except ValueError:
        entries = []
    if not any(_is_ours(e) for e in entries if isinstance(entries, list)):
        missing.append(PREP_KEY)
    return missing


def write_conf(st: StreamConfig) -> dict:
    target = paths.sunshine_conf()
    real = Path(os.path.realpath(target))  # keep a dotfiles symlink a symlink
    existing = fsutil.read_text(real)
    merged = merge_conf(existing or "", wanted_keys(st), prep_entry())
    if merged == existing:
        return {"config": str(target), "changed": False}
    mode = (real.stat().st_mode & 0o777) if existing is not None else 0o644
    fsutil.atomic_write(real, merged, mode)
    return {"config": str(target), "changed": True}


# -- version ----------------------------------------------------------------------


def parse_version(raw: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d{4})\.(\d+)\.(\d+)", raw or "")
    return tuple(int(g) for g in m.groups()) if m else None


def installed_version() -> str:
    try:
        proc = subprocess.run(["pacman", "-Q", "sunshine"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    parts = proc.stdout.split()
    return parts[1] if proc.returncode == 0 and len(parts) > 1 else ""


def version_ok(raw: str) -> bool:
    version = parse_version(raw)
    return bool(version) and version >= MIN_VERSION


# -- guided setup -------------------------------------------------------------------


def lan_cidr() -> str:
    """The subnet the default route sits on, for the firewall rules."""
    try:
        route = json.loads(subprocess.run(["ip", "-j", "-4", "route", "get", "1.1.1.1"],
                                          capture_output=True, text=True, timeout=5).stdout)[0]
        addrs = json.loads(subprocess.run(["ip", "-j", "-4", "addr", "show", "dev", route["dev"]],
                                          capture_output=True, text=True, timeout=5).stdout)
        for info in addrs[0].get("addr_info", []):
            if info.get("local") == route.get("prefsrc"):
                return str(ipaddress.ip_interface(f"{info['local']}/{info['prefixlen']}").network)
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired):
        pass
    return ""


def privileged_steps(st: StreamConfig, uinput_ok: bool = True) -> list[dict]:
    """The one-time root steps, as text. Each: title, why, commands."""
    cidr = lan_cidr() or "192.168.1.0/24"
    steps = []
    if not version_ok(installed_version()):
        steps.append({
            "title": "Install Sunshine from LizardByte's pacman repo",
            "why": "Needs 2026.914.233613 or newer: earlier builds cannot capture a Hyprland virtual output "
                   "and carry a high-severity Linux advisory. Packages in this repo are not signature-checked.",
            "commands": [
                "printf '\\n[lizardbyte]\\nSigLevel = Optional\\nServer = https://github.com/LizardByte/"
                "pacman-repo/releases/latest/download\\n' | sudo tee -a /etc/pacman.conf",
                "sudo pacman -Sy && sudo pacman -S lizardbyte/sunshine",
            ],
        })
    steps.append({
        "title": "Open the streaming ports to your home network only",
        "why": "Sunshine's admin page (47990) stays closed: it is only needed on this machine.",
        "commands": [f"sudo ufw allow from {cidr} to any port {TCP_PORTS} proto tcp",
                     f"sudo ufw allow from {cidr} to any port {UDP_PORTS} proto udp"],
    })
    remote = tailscale.summary()
    if not remote["available"]:
        steps.append({
            "title": "Streaming away from home: Tailscale",
            "why": "Nothing is exposed publicly and no ports are forwarded. --operator lets the panel switch turn "
                   "it on and off without a password. Then, at login.tailscale.com, disable key expiry for this "
                   "machine so it does not drop off the tailnet while you are away.",
            "commands": ["sudo pacman -S tailscale && sudo systemctl enable --now tailscaled",
                         'sudo tailscale up --ssh=false --operator="$USER"'],
        })
    steps.append({
        "title": "Optional: let the tailnet reach only the streaming ports",
        "why": "tailscaled accepts tailnet traffic ahead of ufw, so ufw rules do not scope it; the tailnet policy "
               "does. Worth it when other people or devices share your tailnet. Replacing the default allow-all "
               "policy with this grant closes everything else between your devices, so add grants for those too.",
        "commands": ["# login.tailscale.com > Access controls, inside \"grants\":"],
        "grant": tailscale.grant(TCP_PORTS, UDP_PORTS, remote["ip"]),
    })
    if not uinput_ok:
        steps.append({
            "title": "Let Sunshine create virtual controllers",
            "why": "/dev/uinput is not writable by your user, so video would work but input would not.",
            "commands": ['sudo usermod -aG input "$USER"   # then log out and back in'],
        })
    steps.append({
        "title": "Do not enable the Sunshine service",
        "why": "Battlestation starts it after the virtual display exists. Started earlier, it has nothing to capture.",
        "commands": [f"systemctl --user disable {st.unit}"],
    })
    steps.append({
        "title": "Keep the host awake",
        "why": "A suspended or rebooted machine cannot be reached, and an encrypted disk needs its passphrase at boot.",
        "commands": ["omarchy-toggle-suspend   # removes Suspend from the system menu"],
    })
    return steps
