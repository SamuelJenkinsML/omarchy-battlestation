"""Samsung TV control over the local network: Wake-on-LAN plus the Tizen remote
control websocket on port 8002.

The protocol (endpoint, token pairing, key payload) is documented by
xchwarze/samsung-tv-ws-api; this is an independent standard-library
implementation, not a copy of that LGPL code.
"""

from __future__ import annotations

import base64
import json
import socket
import time
import urllib.error
import urllib.request

from . import fsutil, paths
from .config import TvConfig
from .ws import WebSocket, WebSocketError

APP_NAME = "Battlestation"
WOL_RESEND_S = 2.0


class TvError(RuntimeError):
    pass


def magic_packet(mac: str) -> bytes:
    hexmac = mac.replace(":", "").replace("-", "").lower()
    if len(hexmac) != 12:
        raise TvError(f"bad MAC address {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(hexmac) * 16


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            sock.sendto(packet, (broadcast, port))
            time.sleep(0.05)


def wol_targets(cfg: TvConfig) -> list[str]:
    """Where to send the magic packet. A TV on Wi-Fi in deep standby dozes its
    radio, and the access point only delivers broadcasts at DTIM beacons (some
    routers throttle or drop them), so also unicast to the TV's last address:
    the AP buffers unicast frames for a sleeping station."""
    targets = [cfg.broadcast, cfg.host]
    octets = cfg.host.split(".")
    if len(octets) == 4 and all(o.isdigit() for o in octets):
        targets.append(".".join(octets[:3] + ["255"]))  # directed broadcast, assuming a /24
    return list(dict.fromkeys(t for t in targets if t))


def send_wol_all(cfg: TvConfig) -> None:
    sent = False
    for target in wol_targets(cfg):
        try:
            send_wol(cfg.mac, target)
            sent = True
        except OSError:
            pass  # e.g. no neighbour entry for the unicast target; the broadcasts still go
    if not sent:
        raise TvError("could not send the Wake-on-LAN packet on any route")


def info(host: str, timeout: float = 2.0) -> dict | None:
    """Device info from the TV's REST endpoint, or None if unreachable."""
    try:
        with urllib.request.urlopen(f"http://{host}:8001/api/v2/", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def power_state(host: str) -> str:
    """'on', 'standby', or 'off' (unreachable)."""
    data = info(host)
    if data is None:
        return "off"
    state = str((data.get("device") or {}).get("PowerState") or "").lower()
    # Older firmware omits PowerState; answering at all means it is on.
    return state or "on"


def _url(token: str | None) -> str:
    name = base64.b64encode(APP_NAME.encode()).decode()
    url = f"/api/v2/channels/samsung.remote.control?name={name}"
    if token:
        url += f"&token={token}"
    return url


def read_token() -> str:
    return (fsutil.read_text(paths.tv_token_file()) or "").strip()


def _connect(cfg: TvConfig, timeout: float) -> tuple[WebSocket, str]:
    token = read_token()
    ws = WebSocket(f"wss://{cfg.host}:8002" + _url(token), timeout=timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = json.loads(ws.recv_text())
        event = msg.get("event", "")
        if event == "ms.channel.connect":
            new_token = str((msg.get("data") or {}).get("token") or "")
            if new_token and new_token != token:
                fsutil.atomic_write(paths.tv_token_file(), new_token + "\n", 0o600)
                token = new_token
            return ws, token
        if event == "ms.channel.unauthorized":
            ws.close()
            raise TvError("the TV refused the connection; accept the prompt on the TV, or set "
                          "Device Connection Manager > Access Notification to 'First Time Only'")
    ws.close()
    raise TvError("timed out waiting for the TV to accept the connection")


def pair(cfg: TvConfig, timeout: float = 45.0) -> str:
    try:
        ws, token = _connect(cfg, timeout)
    except (OSError, WebSocketError) as exc:
        raise TvError(f"could not reach {cfg.host}:8002 ({exc})") from exc
    ws.close()
    if not token:
        raise TvError("the TV accepted the connection but sent no token (older firmware works without one)")
    return token


def send_keys(cfg: TvConfig, keys: list[str], gap: float = 0.45) -> None:
    try:
        ws, _ = _connect(cfg, 10.0)
    except (OSError, WebSocketError) as exc:
        raise TvError(f"could not reach {cfg.host}:8002 ({exc})") from exc
    with ws:
        for key in keys:
            ws.send_text(json.dumps({
                "method": "ms.remote.control",
                "params": {"Cmd": "Click", "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"},
            }))
            time.sleep(gap)


def wake(cfg: TvConfig) -> dict:
    """Wake the TV and wait until it reports on. Returns timing info."""
    if not cfg.configured:
        raise TvError("[tv] host is not configured")
    started = time.time()
    if power_state(cfg.host) == "on":
        return {"state": "on", "woke": False, "seconds": 0}
    if not cfg.mac:
        if power_state(cfg.host) != "standby":
            raise TvError("TV is off and [tv] mac is not set, so Wake-on-LAN is impossible")
        # Network standby without a MAC: the power key over the websocket works.
        send_keys(cfg, ["KEY_POWER"])
    deadline = started + cfg.wake_timeout_s
    next_send = 0.0
    while time.time() < deadline:
        # Keep sending for the whole wait: a TV in deep standby answers nothing
        # (reads as "off") and can sleep through any single burst.
        if cfg.mac and time.time() >= next_send:
            send_wol_all(cfg)
            next_send = time.time() + WOL_RESEND_S
        if power_state(cfg.host) == "on":
            return {"state": "on", "woke": True, "seconds": round(time.time() - started, 1)}
        time.sleep(1.0)
    raise TvError(f"TV did not wake within {cfg.wake_timeout_s}s")


def select_input(cfg: TvConfig) -> list[str]:
    keys = [cfg.input_key] if cfg.input_key else []
    if not keys and cfg.input_fallback:
        keys = list(cfg.input_fallback)
    if not keys:
        raise TvError("set [tv] input_key (e.g. KEY_HDMI1) or input_fallback")
    try:
        send_keys(cfg, keys)
    except TvError:
        if cfg.input_key and cfg.input_fallback:
            send_keys(cfg, cfg.input_fallback)
            return cfg.input_fallback
        raise
    return keys


