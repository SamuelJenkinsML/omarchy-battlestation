"""DRM connector properties Hyprland does not expose: vrr_capable on the
connector, the live VRR_ENABLED flag on the CRTC driving it, and what is
actually on the wire (HDR_OUTPUT_METADATA, Colorspace, max bpc).

Adapted from edbron/omarchy-monitor-placement-refresh-rate
(bin/omarchy-monitor-drm-props, MIT, (c) 2026 edbron). Plain KMS ioctls on
/dev/dri/card*; no DRM master needed because logind grants the seated user an
ACL on the card nodes. Verified on nvidia-drm as well as i915.
"""
import ctypes, fcntl, glob, os, struct
DRM_IOCTL_MODE_GETRESOURCES     = 0xC04064A0
DRM_IOCTL_MODE_GETCONNECTOR     = 0xC05064A7
DRM_IOCTL_MODE_OBJ_GETPROPERTIES = 0xC02064B9
DRM_IOCTL_MODE_GETPROPERTY      = 0xC04064AA
DRM_IOCTL_MODE_GETENCODER       = 0xC01464A6
DRM_MODE_OBJECT_CONNECTOR       = 0xC0C0C0C0
DRM_MODE_OBJECT_CRTC            = 0xCCCCCCCC
DRM_MODE_PROP_ENUM              = 1 << 3
CONNECTOR_NAMES = {1:"VGA",2:"DVI-I",3:"DVI-D",4:"DVI-A",5:"Composite",6:"SVIDEO",7:"LVDS",8:"Component",
                   9:"DIN",10:"DP",11:"HDMI-A",12:"HDMI-B",13:"TV",14:"eDP",15:"Virtual",16:"DSI",17:"DPI",
                   18:"WRITEBACK",19:"SPI",20:"USB"}

def ioctl(fd, req, buf):
    fcntl.ioctl(fd, req, buf, True)

def u32_array(n):
    return (ctypes.c_uint32 * max(n, 1))()

def get_resources(fd):
    # struct drm_mode_card_res: 4 u64 ptrs, 4 u32 counts, 4 u32 min/max
    fmt = "QQQQIIIIIIII"
    buf = bytearray(struct.calcsize(fmt))
    ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, buf)
    _,_,_,_, nfb, ncrtc, nconn, nenc, *_ = struct.unpack(fmt, bytes(buf))
    conns = u32_array(nconn)
    packed = struct.pack(fmt, 0, 0, ctypes.addressof(conns), 0, 0, 0, nconn, 0, 0, 0, 0, 0)
    buf = bytearray(packed); ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, buf)
    return list(conns)[:nconn]

def get_connector(fd, cid):
    # struct drm_mode_get_connector
    fmt = "QQQQIIIIIIIIII"  # encoders_ptr, modes_ptr, props_ptr, prop_values_ptr, count_modes, count_props, count_encoders, encoder_id, connector_id, connector_type, connector_type_id, connection, mm_width, mm_height, subpixel(+pad)
    fmt = "QQQQIIIIIIIIIIII"
    size = struct.calcsize(fmt)
    buf = bytearray(struct.pack(fmt, 0,0,0,0, 0,0,0, 0, cid, 0,0,0,0,0,0,0))
    ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, buf)
    f = struct.unpack(fmt, bytes(buf))
    encoder_id, ctype, ctype_id, connection = f[7], f[9], f[10], f[11]
    return f"{CONNECTOR_NAMES.get(ctype, 'Unknown')}-{ctype_id}", connection, encoder_id

def get_encoder_crtc(fd, eid):
    if not eid:
        return 0
    fmt = "IIIII"  # encoder_id, encoder_type, crtc_id, possible_crtcs, possible_clones
    buf = bytearray(struct.pack(fmt, eid, 0, 0, 0, 0))
    ioctl(fd, DRM_IOCTL_MODE_GETENCODER, buf)
    return struct.unpack(fmt, bytes(buf))[2]

def get_enum_name(fd, prop_id, value):
    """Name of an enum property's current value, e.g. Colorspace -> "BT2020_RGB"."""
    pfmt = "QQII32sII"
    pbuf = bytearray(struct.pack(pfmt, 0, 0, prop_id, 0, b"", 0, 0))
    ioctl(fd, DRM_IOCTL_MODE_GETPROPERTY, pbuf)
    nvals, nenums = struct.unpack(pfmt, bytes(pbuf))[5:7]
    if not nenums:
        return None
    vals = (ctypes.c_uint64 * max(nvals, 1))()
    # struct drm_mode_property_enum: u64 value, char name[32]
    enums = (ctypes.c_uint8 * (40 * nenums))()
    pbuf = bytearray(struct.pack(pfmt, ctypes.addressof(vals), ctypes.addressof(enums), prop_id, 0, b"", nvals, nenums))
    ioctl(fd, DRM_IOCTL_MODE_GETPROPERTY, pbuf)
    return enum_name(bytes(enums), value)


def enum_name(raw: bytes, value):
    for off in range(0, len(raw) - 39, 40):
        v, name = struct.unpack_from("Q32s", raw, off)
        if v == value:
            return name.split(b"\0")[0].decode(errors="replace")
    return None


def get_props(fd, cid, obj_type=DRM_MODE_OBJECT_CONNECTOR, enums=()):
    """{name: value}; properties named in `enums` come back as their enum name."""
    fmt = "QQIIII"  # props_ptr, prop_values_ptr, count_props, obj_id, obj_type, pad
    buf = bytearray(struct.pack(fmt, 0, 0, 0, cid, obj_type, 0))
    ioctl(fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, buf)
    n = struct.unpack(fmt, bytes(buf))[2]
    ids = u32_array(n); vals = (ctypes.c_uint64 * max(n,1))()
    buf = bytearray(struct.pack(fmt, ctypes.addressof(ids), ctypes.addressof(vals), n, cid, obj_type, 0))
    ioctl(fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, buf)
    out = {}
    for i in range(n):
        # struct drm_mode_get_property: values_ptr, enum_blob_ptr, prop_id, flags, name[32], count_values, count_enum_blobs
        pfmt = "QQII32sII"
        pbuf = bytearray(struct.pack(pfmt, 0, 0, ids[i], 0, b"", 0, 0))
        ioctl(fd, DRM_IOCTL_MODE_GETPROPERTY, pbuf)
        _, _, _, flags, raw_name, _, _ = struct.unpack(pfmt, bytes(pbuf))
        name = raw_name.split(b"\0")[0].decode()
        value = vals[i]
        if name in enums and flags & DRM_MODE_PROP_ENUM:
            value = get_enum_name(fd, ids[i], value)
        out[name] = value
    return out

def read() -> dict:
    """{connector: {"vrr_capable", "vrr_enabled", "hdr_metadata", "colorspace", "max_bpc", "card"}}

    hdr_metadata is True while an HDR_OUTPUT_METADATA blob is attached, i.e. the
    display is being told it receives HDR; that holds for Hyprland's fullscreen
    passthrough too, which colorManagementPreset does not show.
    """
    result = {}
    for path in sorted(glob.glob("/dev/dri/card[0-9]*")):
        try:
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            for cid in get_resources(fd):
                try:
                    name, connection, encoder_id = get_connector(fd, cid)
                    if connection != 1:  # 1 = connected
                        continue
                    props = get_props(fd, cid, enums=("Colorspace",))
                    enabled = None
                    crtc = get_encoder_crtc(fd, encoder_id)
                    if crtc:
                        enabled = bool(get_props(fd, crtc, DRM_MODE_OBJECT_CRTC).get("VRR_ENABLED", 0))
                    bpc = props.get("max bpc")
                    result[name] = {"vrr_capable": bool(props.get("vrr_capable", 0)),
                                    "vrr_enabled": enabled,
                                    "hdr_metadata": bool(props.get("HDR_OUTPUT_METADATA", 0)),
                                    "colorspace": props.get("Colorspace") or None,
                                    "max_bpc": int(bpc) if bpc is not None else None,
                                    "card": os.path.basename(path)}
                except OSError:
                    continue
        except OSError:
            pass
        finally:
            os.close(fd)
    return result
