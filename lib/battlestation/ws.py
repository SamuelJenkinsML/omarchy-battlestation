"""Just enough RFC 6455 WebSocket client to talk to a Samsung TV.

Text frames, client-side masking, ping/pong and close. No extensions, no
fragmentation on send. Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(OSError):
    pass


def encode_frame(payload: bytes, opcode: int = OP_TEXT, mask_key: bytes | None = None) -> bytes:
    mask_key = mask_key if mask_key is not None else os.urandom(4)
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 1 << 16:
        header.append(0x80 | 126)
        header += struct.pack("!H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", n)
    header += mask_key
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


def decode_frame(buf: bytes) -> tuple[int, bytes, int] | None:
    """Parse one frame from buf. Returns (opcode, payload, consumed) or None if incomplete."""
    if len(buf) < 2:
        return None
    opcode = buf[0] & 0x0F
    masked = buf[1] & 0x80
    n = buf[1] & 0x7F
    pos = 2
    if n == 126:
        if len(buf) < 4:
            return None
        n = struct.unpack("!H", buf[2:4])[0]
        pos = 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n = struct.unpack("!Q", buf[2:10])[0]
        pos = 10
    key = b""
    if masked:
        if len(buf) < pos + 4:
            return None
        key = buf[pos:pos + 4]
        pos += 4
    if len(buf) < pos + n:
        return None
    payload = buf[pos:pos + n]
    if masked:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return opcode, payload, pos + n


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + _GUID).encode()).digest()).decode()


class WebSocket:
    def __init__(self, url: str, timeout: float = 10.0):
        parts = urlsplit(url)
        secure = parts.scheme == "wss"
        port = parts.port or (443 if secure else 80)
        raw = socket.create_connection((parts.hostname, port), timeout=timeout)
        if secure:
            ctx = ssl.create_default_context()
            # Samsung TVs present a self-signed certificate on 8002.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=parts.hostname)
        self.sock = raw
        self._buf = b""
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parts.hostname}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        head = self._read_until(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101 " not in status + " ":
            raise WebSocketError(f"handshake failed: {status}")
        if accept_key(key).encode() not in head:
            raise WebSocketError("handshake failed: bad Sec-WebSocket-Accept")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            self._buf += chunk
        head, self._buf = self._buf.split(marker, 1)
        return head

    def send_text(self, text: str) -> None:
        self.sock.sendall(encode_frame(text.encode()))

    def recv_text(self) -> str:
        while True:
            frame = decode_frame(self._buf)
            if frame is None:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise WebSocketError("connection closed")
                self._buf += chunk
                continue
            opcode, payload, used = frame
            self._buf = self._buf[used:]
            if opcode == OP_PING:
                self.sock.sendall(encode_frame(payload, OP_PONG))
            elif opcode == OP_CLOSE:
                raise WebSocketError("server closed the connection")
            elif opcode in (OP_TEXT, OP_CONT):
                return payload.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.sendall(encode_frame(b"", OP_CLOSE))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
