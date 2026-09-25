"""Tailscale, for streaming away from home.

The tailnet is the only way in from outside: Sunshine's ports are never
forwarded. tailscaled accepts everything arriving on tailscale0 ahead of ufw,
so host firewall rules do not scope it; a tailnet grant does (see `grant`).

Whether remote access is on is Tailscale's own state, not a flag of ours, so
`tailscale up` and `tailscale down` typed elsewhere show up in the panel. Both
run without root once the user is the operator (`tailscale set --operator`).
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
from datetime import datetime, timezone

OPERATOR_FIX = 'sudo tailscale set --operator="$USER"'
_PONG_RE = re.compile(r"pong from \S+ \(([^)]+)\) via (\S+) in ([\d.]+)\s*ms")


class TailscaleError(RuntimeError):
    pass


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["tailscale", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _json(args: list[str], timeout: float) -> dict:
    proc = _run(args, timeout)
    try:
        data = json.loads(proc.stdout) if proc and proc.returncode == 0 else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _user() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return ""


def _name(node: dict) -> str:
    return str(node.get("DNSName") or "").rstrip(".") or str(node.get("HostName") or "")


def _peer(node: dict) -> dict:
    return {"name": _name(node), "host": str(node.get("HostName") or ""), "os": str(node.get("OS") or ""),
            "online": bool(node.get("Online")), "active": bool(node.get("Active")),
            "direct": bool(node.get("CurAddr")), "relay": str(node.get("Relay") or "")}


def status() -> dict:
    """What the panel and doctor need. `state` is tailscaled's BackendState, or
    "" when the daemon does not answer."""
    if not shutil.which("tailscale"):
        return {"installed": False, "state": "", "operator": False, "ip": "", "name": "", "keyExpiry": "", "peers": []}
    raw = _json(["status", "--json"], 3)
    me = raw.get("Self") or {}
    ips = [ip for ip in me.get("TailscaleIPs") or [] if ":" not in ip]
    operator = os.getuid() == 0
    if raw and not operator:
        operator = _json(["debug", "prefs"], 3).get("OperatorUser") == _user()
    return {
        "installed": True, "state": str(raw.get("BackendState") or ""), "operator": operator,
        "ip": ips[0] if ips else "", "name": _name(me), "keyExpiry": str(me.get("KeyExpiry") or ""),
        "peers": [_peer(p) for p in (raw.get("Peer") or {}).values() if isinstance(p, dict)],
    }


def key_days_left(ts: dict, now: datetime | None = None) -> int | None:
    """Days until this machine drops off the tailnet. None when expiry is off."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", ts.get("keyExpiry") or "")
    if not m:
        return None
    expiry = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
    return (expiry - (now or datetime.now(timezone.utc))).days


def summary(ts: dict | None = None) -> dict:
    """The `remote` block of `battlestation state`."""
    ts = status() if ts is None else ts
    on = ts["state"] == "Running"
    if not ts["installed"]:
        hint = ""
    elif not ts["state"]:
        hint = "tailscaled is not running: sudo systemctl enable --now tailscaled"
    elif ts["state"] in ("NeedsLogin", "NeedsMachineAuth", "NoState"):
        hint = "Sign this machine in once: sudo tailscale up --operator=\"$USER\""
    elif not ts["operator"]:
        hint = f"One-time step so the switch needs no password: {OPERATOR_FIX}"
    else:
        hint = ""
    active = [p for p in ts["peers"] if p["online"] and p["active"] and (p["direct"] or p["relay"])] if on else []
    relayed = [p for p in active if not p["direct"]]
    return {
        "installed": ts["installed"], "available": bool(ts["installed"]) and not hint, "on": on, "hint": hint,
        "name": ts["name"] if on else "", "ip": ts["ip"] if on else "",
        "link": "relayed" if relayed else "direct" if active else "",
        "linkPeer": (relayed or active or [{"host": ""}])[0]["host"],
    }


def set_running(on: bool) -> dict:
    """`tailscale up` or `tailscale down`, as the operator. Bare `up` on purpose:
    with any flag it insists every non-default setting is repeated."""
    info = summary()
    if not info["installed"]:
        raise TailscaleError("Tailscale is not installed; see `battlestation setup stream`")
    if info["hint"]:
        raise TailscaleError(info["hint"])
    proc = _run(["up" if on else "down"], 15)
    if proc is None:
        raise TailscaleError("tailscale did not answer within 15 s")
    if proc.returncode != 0:
        text = (proc.stderr or proc.stdout).strip()
        if "denied" in text.lower() or "operator" in text.lower():
            text = f"not allowed to control Tailscale: {OPERATOR_FIX}"
        raise TailscaleError(text or "tailscale failed")
    return summary()


def _netcheck() -> dict:
    raw = _json(["netcheck", "--format=json"], 15)
    if not raw:
        return {}
    return {"udp": bool(raw.get("UDP")), "ipv6": bool(raw.get("IPv6")),
            "hardNat": bool(raw.get("MappingVariesByDestIP")),
            "portMapping": [k for k in ("UPnP", "PMP", "PCP") if raw.get(k)]}


def parse_ping(text: str) -> dict | None:
    """The last pong decides: `--until-direct` stops on the first direct one."""
    pongs = _PONG_RE.findall(text or "")
    if not pongs:
        return None
    ip, via, ms = pongs[-1]
    return {"ip": ip, "direct": not via.startswith("DERP"), "via": via, "latencyMs": float(ms)}


def check(peer: str | None) -> dict:
    """Can `peer` reach us directly? A relayed link is too slow to stream over."""
    ts = status()
    if ts["state"] != "Running":
        raise TailscaleError(summary(ts)["hint"] or "Tailscale is switched off")
    online = [p for p in ts["peers"] if p["online"]]
    if not peer:
        return {"name": ts["name"], "ip": ts["ip"], "peers": online,
                "note": "pass a peer to test the link: battlestation stream remote-check <peer>"}
    proc = _run(["ping", "--c", "5", "--until-direct", "--timeout", "3s", peer], 30)
    ping = parse_ping((proc.stdout + proc.stderr) if proc else "")
    net = _netcheck()
    result = {"peer": peer, "reachable": ping is not None, **(ping or {}), "network": net}
    if ping is None:
        result["hint"] = f"{peer} did not answer; is Tailscale up on it?"
    elif not ping["direct"]:
        result["hint"] = ("Relayed, which is too slow to stream over. Forward UDP 41641 to this machine on the "
                          "router, or enable UPnP/NAT-PMP there" +
                          ("; this network's NAT is the strict kind, so one of those is needed." if net.get("hardNat")
                           else "; then check the network the other device is on."))
    return result


def grant(tcp_ports: str, udp_ports: str, dst: str = "") -> str:
    """A tailnet policy grant that opens only the streaming ports to this host."""
    ports = [f"tcp:{p}" for p in tcp_ports.split(",")] + [f"udp:{p.replace(':', '-')}" for p in udp_ports.split(",")]
    return json.dumps({"src": ["autogroup:member"], "dst": [dst or "<this machine's 100.x address>"], "ip": ports})
