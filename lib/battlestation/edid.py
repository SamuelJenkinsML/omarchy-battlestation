"""EDID capability parsing: HDR, wide gamut, VRR hints and HDMI link bandwidth.

The CTA-861 walk is modelled on IM0001GT/omarchy-screens (MIT, (c) 2026
IM0001GT), with the vendor OUI byte order, the HDMI VSDB field offsets and the
colorimetry bits corrected, and HDMI Forum link limits added so we can tell when
10-bit output will not fit through the cable.
"""

from __future__ import annotations

import glob
from dataclasses import asdict, dataclass

OUI_HDMI14 = 0x000C03
OUI_HDMI_FORUM = 0xC45DD8
OUI_AMD_FREESYNC = 0x00001A

# FRL rates in Gbit/s by the HF-VSDB Max_FRL_Rate code.
_FRL_GBPS = {0: 0, 1: 9, 2: 18, 3: 24, 4: 32, 5: 40, 6: 48}


@dataclass
class EdidCaps:
    name: str = ""
    hdr: bool = False                 # SMPTE ST2084 EOTF advertised
    hlg: bool = False
    wide_gamut: bool = False          # BT.2020 RGB colorimetry
    max_luminance: int | None = None
    max_avg_luminance: int | None = None
    min_luminance: float | None = None
    deep_color_bpc: int = 8           # best RGB/4:4:4 depth the sink accepts
    max_tmds_mhz: int | None = None   # HDMI TMDS character rate ceiling
    max_frl_gbps: int = 0             # HDMI 2.1 FRL ceiling, 0 = TMDS only
    vrr_min: int | None = None
    vrr_max: int | None = None
    freesync: bool = False
    range_min_hz: int | None = None   # base-block range limits descriptor
    range_max_hz: int | None = None
    is_hdmi: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def vrr_hint(self) -> bool:
        return bool(self.vrr_max) or self.freesync


def _nits_max(cv: int) -> int | None:
    return int(round(50.0 * (2.0 ** (cv / 32.0)))) if cv else None


def _nits_min(cv: int, max_nits: int | None) -> float | None:
    if not cv or not max_nits:
        return None
    return round(float(max_nits) * ((cv / 255.0) ** 2) / 100.0, 4)


def _oui(payload: bytes) -> int:
    # Vendor OUIs are stored least-significant byte first.
    return payload[0] | (payload[1] << 8) | (payload[2] << 16)


def _parse_base(data: bytes, caps: EdidCaps) -> None:
    for off in (54, 72, 90, 108):
        d = data[off:off + 18]
        if len(d) < 18 or d[0] != 0 or d[1] != 0:
            continue
        tag = d[3]
        if tag == 0xFC:
            caps.name = d[5:18].split(b"\n")[0].decode("ascii", "replace").strip()
        elif tag == 0xFD:
            offsets = d[4]
            vmin = d[5] + (255 if offsets & 0x01 else 0)
            vmax = d[6] + (255 if offsets & 0x02 else 0)
            if vmin and vmax:
                caps.range_min_hz, caps.range_max_hz = vmin, vmax


def _parse_cta(block: bytes, caps: EdidCaps) -> None:
    if len(block) < 5 or block[0] != 0x02:
        return
    dtd = block[2]
    end = dtd if 4 <= dtd <= len(block) else len(block)
    i = 4
    while i < end:
        header = block[i]
        if header == 0:
            break
        tag, length = header >> 5, header & 0x1F
        p = block[i + 1:i + 1 + length]
        i += 1 + length

        if tag == 3 and len(p) >= 3:
            oui = _oui(p)
            if oui == OUI_HDMI14:
                caps.is_hdmi = True
                if len(p) >= 6:
                    flags = p[5]
                    if flags & 0x40:
                        caps.deep_color_bpc = max(caps.deep_color_bpc, 16)
                    elif flags & 0x20:
                        caps.deep_color_bpc = max(caps.deep_color_bpc, 12)
                    elif flags & 0x10:
                        caps.deep_color_bpc = max(caps.deep_color_bpc, 10)
                if len(p) >= 7 and p[6] and caps.max_tmds_mhz is None:
                    caps.max_tmds_mhz = p[6] * 5
            elif oui == OUI_HDMI_FORUM:
                caps.is_hdmi = True
                if len(p) >= 5 and p[4]:
                    caps.max_tmds_mhz = p[4] * 5
                if len(p) >= 8:
                    caps.max_frl_gbps = _FRL_GBPS.get(p[7] >> 4, 0)
                if len(p) >= 11:
                    vmin = p[9] & 0x3F
                    vmax = ((p[9] & 0xC0) << 2) | p[10]
                    if vmax:
                        caps.vrr_min, caps.vrr_max = vmin, vmax
            elif oui == OUI_AMD_FREESYNC:
                caps.freesync = True
                if len(p) >= 7 and p[5] and p[6]:
                    caps.vrr_min = caps.vrr_min or p[5]
                    caps.vrr_max = caps.vrr_max or p[6]

        elif tag == 7 and p:
            ext = p[0]
            if ext == 5 and len(p) >= 2 and p[1] & 0x80:
                caps.wide_gamut = True
            elif ext == 6 and len(p) >= 2:
                eotf = p[1]
                caps.hdr = caps.hdr or bool(eotf & 0x04)
                caps.hlg = caps.hlg or bool(eotf & 0x08)
                if len(p) >= 4:
                    caps.max_luminance = _nits_max(p[3])
                if len(p) >= 5:
                    caps.max_avg_luminance = _nits_max(p[4])
                if len(p) >= 6:
                    caps.min_luminance = _nits_min(p[5], caps.max_luminance)


def parse(data: bytes) -> EdidCaps:
    caps = EdidCaps()
    if len(data) < 128:
        return caps
    _parse_base(data, caps)
    for start in range(128, len(data) - 127, 128):
        _parse_cta(data[start:start + 128], caps)
    if b"freesync" in data.lower():
        caps.freesync = True
    return caps


def edid_path(connector: str) -> str | None:
    matches = sorted(glob.glob(f"/sys/class/drm/card*-{connector}/edid"))
    return matches[0] if matches else None


def read_caps(connector: str) -> EdidCaps:
    path = edid_path(connector)
    if not path:
        return EdidCaps()
    try:
        with open(path, "rb") as fh:
            return parse(fh.read())
    except OSError:
        return EdidCaps()


# Typical CTA blanking overhead: 4400x2250 total for 3840x2160 active.
_BLANKING = (4400 * 2250) / (3840 * 2160)


def estimated_pixel_clock_mhz(width: int, height: int, hz: float) -> float:
    return width * height * hz * _BLANKING / 1e6


def max_bpc_for_mode(caps: EdidCaps, width: int, height: int, hz: float) -> int:
    """Highest RGB bits-per-component this mode can carry over the link.

    Only HDMI publishes a limit we can check. DisplayPort sinks return the EDID
    deep-colour figure (or 10 when unknown) and leave the rest to the driver.
    """
    wanted = max(8, min(caps.deep_color_bpc if caps.is_hdmi else max(caps.deep_color_bpc, 10), 10))
    if not caps.is_hdmi:
        return wanted
    if caps.max_frl_gbps:
        # FRL carries 16b/18b coded data; allow a little slack because the
        # blanking figure is an estimate (40 Gbit/s ports do run 4K120 10-bit).
        budget_gbps = caps.max_frl_gbps * 16 / 18 * 1.02
        need = estimated_pixel_clock_mhz(width, height, hz) * 3 * wanted / 1000
        return wanted if need <= budget_gbps else 8
    limit = caps.max_tmds_mhz or 340
    clock = estimated_pixel_clock_mhz(width, height, hz)
    for bpc in (wanted, 8):
        if clock * bpc / 8 <= limit + 1:
            return bpc
    return 8
