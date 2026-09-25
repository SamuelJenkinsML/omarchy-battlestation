import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from battlestation import config, edid, hypr, luagen, mangohud, pad, scenes, stream, sunshine, tailscale, tv, ws  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
TV_DESC = "Samsung Electric Company SAMSUNG 0x01000E00"
UW_DESC = "Samsung Electric Company Odyssey G9 HNAX000000"


def tv_caps(_connector):
    return edid.parse((FIXTURES / "samsung-tv-hdmi20.edid").read_bytes())


def dp_caps(_connector):
    return edid.EdidCaps(hdr=True, wide_gamut=False, deep_color_bpc=10, is_hdmi=False, freesync=True)


def mon(name, desc, w, h, hz, modes, disabled=False):
    return {
        "name": name, "description": desc, "width": w, "height": h, "refreshRate": hz,
        "availableModes": modes, "disabled": disabled, "x": 0, "y": 0, "scale": 1, "transform": 0,
    }


TV = mon("HDMI-A-1", TV_DESC, 3840, 2160, 60.0,
         ["3840x2160@60.00Hz", "3840x2160@59.94Hz", "2560x1440@60.00Hz", "1920x1080@120.00Hz"])
UW = mon("DP-1", UW_DESC, 3440, 1440, 165.0, ["3440x1440@165.00Hz", "3440x1440@100.00Hz"])


def caps_for(connector):
    return tv_caps(connector) if connector.startswith("HDMI") else dp_caps(connector)


class EdidTests(unittest.TestCase):
    def test_samsung_tv_today(self):
        c = tv_caps("HDMI-A-1")
        self.assertTrue(c.hdr)
        self.assertTrue(c.wide_gamut)
        self.assertTrue(c.is_hdmi)
        self.assertEqual(c.max_tmds_mhz, 600)
        self.assertEqual(c.max_frl_gbps, 0)
        self.assertFalse(c.vrr_hint)
        self.assertEqual(c.deep_color_bpc, 12)

    def test_link_budget(self):
        c = tv_caps("HDMI-A-1")
        self.assertEqual(edid.max_bpc_for_mode(c, 3840, 2160, 60), 8)
        self.assertEqual(edid.max_bpc_for_mode(c, 2560, 1440, 60), 10)
        frl = edid.EdidCaps(is_hdmi=True, deep_color_bpc=12, max_frl_gbps=40, max_tmds_mhz=600)
        self.assertEqual(edid.max_bpc_for_mode(frl, 3840, 2160, 120), 10)

    def test_short_edid(self):
        self.assertFalse(edid.parse(b"\x00" * 10).hdr)


def cfg_from(text):
    return config.parse(tomllib.loads(text))


SINGLE = f"""
[general]
chord_scenes = ["tv-desktop", "tv-gaming"]
[scene.tv-desktop]
label = "TV"
monitors = [{{ match = "desc:{TV_DESC}", mode = "3840x2160@60", scale = 1, hdr = "auto" }}]
[scene.tv-gaming]
steam = "bigpicture"
monitors = [{{ match = "desc:{TV_DESC}", mode = "3840x2160@60", scale = 1, hdr = "auto" }}]
"""

DESK_COUCH = f"""
[general]
chord_scenes = ["desk", "couch"]
[scene.desk]
disable_unlisted = true
monitors = [{{ match = "desc:{UW_DESC}", mode = "3440x1440@165", vrr = 2, hdr = "off" }}]
[scene.couch]
disable_unlisted = true
monitors = [{{ match = "desc:{TV_DESC}", mode = "3840x2160@120", fallback_modes = ["3840x2160@60"], vrr = 3, hdr = "auto" }}]
"""


class TvWakeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.TvConfig(host="192.168.1.133", mac="28:e6:a9:46:c3:4c")
        self.now = [1000.0]
        clock = mock.patch.object(tv.time, "time", side_effect=lambda: self.now[0])
        sleep = mock.patch.object(tv.time, "sleep", side_effect=lambda s: self.now.__setitem__(0, self.now[0] + s))
        clock.start(); sleep.start()
        self.addCleanup(clock.stop); self.addCleanup(sleep.stop)

    def wake(self, states):
        states = iter(states)
        with mock.patch.object(tv, "power_state", side_effect=lambda host: next(states, "off")), \
                mock.patch.object(tv, "send_wol") as send:
            try:
                return tv.wake(self.cfg), send
            except tv.TvError as exc:
                return exc, send

    def test_targets_cover_broadcast_subnet_and_unicast(self):
        self.assertEqual(tv.wol_targets(self.cfg), ["255.255.255.255", "192.168.1.133", "192.168.1.255"])

    def test_keeps_resending_while_the_tv_stays_dark(self):
        # Deep standby: unreachable (reads "off") for several seconds, never "standby".
        result, send = self.wake(["off"] * 7 + ["on"])
        self.assertTrue(result["woke"])
        bursts = send.call_count // len(tv.wol_targets(self.cfg))
        self.assertGreaterEqual(bursts, 3)

    def test_gives_up_after_timeout(self):
        result, send = self.wake([])
        self.assertIsInstance(result, tv.TvError)
        self.assertLessEqual(self.now[0] - 1000.0, self.cfg.wake_timeout_s + 1.5)

    def test_already_on_sends_nothing(self):
        result, send = self.wake(["on"])
        self.assertFalse(result["woke"])
        send.assert_not_called()

    def test_one_unreachable_target_does_not_abort(self):
        def flaky(mac, target):
            if target == "192.168.1.133":
                raise OSError("no route")
        with mock.patch.object(tv, "send_wol", side_effect=flaky):
            tv.send_wol_all(self.cfg)
        with mock.patch.object(tv, "send_wol", side_effect=OSError("down")):
            with self.assertRaises(tv.TvError):
                tv.send_wol_all(self.cfg)


class ConfigTests(unittest.TestCase):
    def test_single_tv(self):
        cfg = cfg_from(SINGLE)
        self.assertEqual(list(cfg.scenes), ["tv-desktop", "tv-gaming"])
        self.assertEqual(cfg.default_scene, "tv-desktop")
        self.assertEqual(cfg.scene("tv-gaming").steam, "bigpicture")

    def test_errors_are_collected(self):
        with self.assertRaises(config.ConfigError) as ctx:
            cfg_from('[scene.x]\nwake_tv = true\nmonitors = [{ match = "HDMI-A-1", vrr = 9, hdr = "maybe" }]')
        text = " ".join(ctx.exception.problems)
        self.assertIn("vrr", text)
        self.assertIn("hdr", text)
        self.assertIn("[tv] host", text)

    def test_shipped_example_parses(self):
        cfg = config.load(ROOT / "examples" / "desk-couch.toml")
        self.assertEqual(cfg.chord_scenes, ["desk", "couch"])
        self.assertTrue(cfg.scene("couch").wake_tv)

    def test_roundtrip_scene_toml(self):
        cfg = cfg_from(DESK_COUCH)
        text = "\n".join(config.scene_toml(s) for s in cfg.scenes.values())
        again = cfg_from('[general]\nchord_scenes = ["desk","couch"]\n' + text)
        self.assertEqual(again.scene("couch").monitors[0].fallback_modes, ["3840x2160@60"])


class ResolveTests(unittest.TestCase):
    def test_single_tv_hdr_auto_8bit(self):
        cfg = cfg_from(SINGLE)
        lua, res = scenes.render(cfg.scene("tv-desktop"), [TV], caps_for)
        self.assertIn('output = "HDMI-A-1"', lua)
        self.assertIn('mode = "3840x2160@60.00"', lua)
        self.assertIn("bitdepth = 8", lua)
        self.assertIn("supports_hdr = 1", lua)
        self.assertIn("cm_auto_hdr = 1", lua)
        self.assertNotIn("disabled", lua)
        self.assertTrue(any("8-bit" in n for n in res.notes))

    def test_identical_layouts_render_identically(self):
        cfg = cfg_from(SINGLE)
        a, _ = scenes.render(cfg.scene("tv-desktop"), [TV], caps_for)
        b, _ = scenes.render(cfg.scene("tv-gaming"), [TV], caps_for)
        strip = lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("--"))  # noqa: E731
        self.assertEqual(strip(a), strip(b))

    def test_couch_falls_back_and_disables_desk(self):
        cfg = cfg_from(DESK_COUCH)
        lua, res = scenes.render(cfg.scene("couch"), [TV, UW], caps_for)
        self.assertIn('mode = "3840x2160@60.00"', lua)
        self.assertIn('hl.monitor({ output = "DP-1", disabled = true })', lua)
        self.assertIn("vrr = 3 }", lua.replace("vrr = 3,", "vrr = 3 }"))
        self.assertTrue(any("fallback" in n for n in res.notes))

    def test_desk(self):
        cfg = cfg_from(DESK_COUCH)
        lua, _ = scenes.render(cfg.scene("desk"), [TV, UW], caps_for)
        self.assertIn('output = "DP-1", mode = "3440x1440@165.00"', lua)
        self.assertIn('hl.monitor({ output = "HDMI-A-1", disabled = true })', lua)
        self.assertIn("cm_auto_hdr = 0", lua)

    def test_pick_auto_prefers_most_specific(self):
        cfg = cfg_from(DESK_COUCH + f"""
[scene.both]
monitors = [{{ match = "desc:{UW_DESC}" }}, {{ match = "desc:{TV_DESC}", position = "3440x0" }}]
""")
        self.assertEqual(scenes.pick_auto(cfg, [TV, UW]), "both")
        self.assertEqual(scenes.pick_auto(cfg, [TV]), "couch")

    def test_never_disable_everything(self):
        cfg = cfg_from(DESK_COUCH)
        with self.assertRaises(scenes.SceneError):
            scenes.render(cfg.scene("desk"), [TV], caps_for)

    def test_hdr_always_on_non_wide_panel_uses_hdredid(self):
        m = luagen.ResolvedMonitor(connector="DP-1", mode="3440x1440@165", hdr="always", bpc=10, wide_gamut=False)
        self.assertIn('cm = "hdredid"', luagen.monitor_line(m))


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.tmp.name, "XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()
        self.reload = mock.patch("battlestation.hypr.reload").start()
        mock.patch("battlestation.hypr.config_errors", return_value=[]).start()
        mock.patch("battlestation.hypr.monitors", return_value=[TV]).start()
        self.warp = mock.patch("battlestation.hypr.warp_cursor").start()
        mock.patch("battlestation.edid.read_caps", side_effect=caps_for).start()
        mock.patch("battlestation.scenes.edid.read_caps", side_effect=caps_for).start()
        mock.patch("time.sleep").start()

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()
        self.tmp.cleanup()

    def test_apply_keep_revert_cycle(self):
        from battlestation import paths
        cfg = cfg_from(SINGLE)
        first = scenes.apply(cfg, "tv-desktop")
        self.assertTrue(first["changed"])
        self.assertIsNotNone(first["pendingRevert"])
        self.assertTrue(paths.scene_lua().exists())
        scenes.keep()
        # Same layout: no reload, no confirmation window.
        calls = self.reload.call_count
        second = scenes.apply(cfg, "tv-gaming")
        self.assertFalse(second["changed"])
        self.assertIsNone(second["pendingRevert"])
        self.assertEqual(self.reload.call_count, calls)
        self.assertEqual(scenes.load_state()["active"], "tv-gaming")
        # Only the real layout change warped the cursor, to the TV's centre.
        self.warp.assert_called_once_with(1920, 1080)

    def test_keep_seconds_zero_never_asks(self):
        cfg = cfg_from(SINGLE.replace("[general]\n", "[general]\nkeep_seconds = 0\n", 1))
        applied = scenes.apply(cfg, "tv-desktop")
        self.assertTrue(applied["changed"])
        self.assertIsNone(applied["pendingRevert"])
        self.assertNotIn("pendingRevert", scenes.load_state())

    def test_revert_restores_absence(self):
        from battlestation import paths
        cfg = cfg_from(SINGLE)
        scenes.apply(cfg, "tv-desktop")
        state = scenes.load_state()
        self.assertEqual(scenes.revert_if_due(now=state["pendingRevert"]["deadline"] - 1)["reverted"], False)
        result = scenes.revert_if_due(now=state["pendingRevert"]["deadline"] + 1)
        self.assertTrue(result["reverted"])
        self.assertFalse(paths.scene_lua().exists())

    def test_config_error_rolls_back(self):
        from battlestation import paths
        mock.patch("battlestation.hypr.config_errors",
                   return_value=["battlestation-scene.lua:4: bad field"]).start()
        cfg = cfg_from(SINGLE)
        with self.assertRaises(scenes.SceneError):
            scenes.apply(cfg, "tv-desktop")
        self.assertFalse(paths.scene_lua().exists())


class ChordTests(unittest.TestCase):
    def test_hold_fires_once(self):
        codes = pad.chord_codes(["VIEW", "MENU"])
        e = pad.ChordEngine(codes, 1.0)
        self.assertFalse(e.feed(0x13A, True, 0.0))
        self.assertFalse(e.feed(0x13B, True, 0.2))
        self.assertFalse(e.tick(1.1))
        self.assertTrue(e.tick(1.25))
        self.assertFalse(e.tick(5.0))  # no repeat while held
        e.feed(0x13A, False, 5.1)
        e.feed(0x13B, False, 5.1)
        e.feed(0x13A, True, 6.0)
        e.feed(0x13B, True, 6.0)
        self.assertTrue(e.tick(7.0))

    def test_early_release_cancels(self):
        e = pad.ChordEngine(pad.chord_codes(["BTN_SELECT", "BTN_START"]), 1.0)
        e.feed(0x13A, True, 0)
        e.feed(0x13B, True, 0)
        e.feed(0x13B, False, 0.5)
        self.assertFalse(e.tick(2.0))

    def test_unknown_button(self):
        with self.assertRaises(ValueError):
            pad.chord_codes(["TURBO"])


class MangoHudTests(unittest.TestCase):
    def test_csv_follower(self):
        f = mangohud.CsvFollower()
        head = "os,cpu,gpu,ram,kernel,driver,cpuscheduler\nArch,Ryzen,RTX 5080,64,7.2,610,none\n"
        cols = "fps,frametime,cpu_load,cpu_power,gpu_load,cpu_temp,gpu_temp,gpu_core_clock,gpu_mem_clock,gpu_vram_used,gpu_power,ram_used,swap_used,process_rss,cpu_mhz,elapsed\n"
        self.assertEqual(f.feed(head + cols), [])
        rows = f.feed("143.9,6.95,20,0,88,50,62,2700,14000,9.1,280,12,0,3,4800,1000\n120.0,8.3")
        self.assertEqual(rows, [{"fps": 143.9, "frametime": 6.95, "gpuLoad": 88.0}])
        rows = f.feed(",20,0,80,50,62,2700,14000,9.1,280,12,0,3,4800,2000\n")
        self.assertEqual(rows[0]["fps"], 120.0)

    def test_desktop_override(self):
        system = "[Desktop Entry]\nName=Steam\nExec=/usr/bin/steam %U\n[Desktop Action BigPicture]\nExec=/usr/bin/steam steam://open/bigpicture\n"
        text = mangohud.render_desktop_override(system, Path("/x/mangohud.conf"))
        self.assertIn(mangohud.MARKER, text)
        self.assertEqual(text.count("env MANGOHUD=1 MANGOHUD_CONFIGFILE=/x/mangohud.conf /usr/bin/steam"), 2)
        self.assertNotIn("no_display", mangohud.render_config())


class WebSocketTests(unittest.TestCase):
    def test_frame_roundtrip(self):
        for size in (5, 300, 70000):
            payload = os.urandom(size)
            frame = ws.encode_frame(payload, mask_key=b"\x01\x02\x03\x04")
            opcode, decoded, used = ws.decode_frame(frame)
            self.assertEqual((opcode, decoded, used), (ws.OP_TEXT, payload, len(frame)))

    def test_incomplete(self):
        frame = ws.encode_frame(b"hello")
        self.assertIsNone(ws.decode_frame(frame[:3]))

    def test_accept_key(self):
        # RFC 6455 section 1.3 example.
        self.assertEqual(ws.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_samsung_payload_shape(self):
        from battlestation import tv
        self.assertEqual(len(tv.magic_packet("aa:bb:cc:dd:ee:ff")), 102)
        with self.assertRaises(tv.TvError):
            tv.magic_packet("nope")


HEADLESS = mon("BS-STREAM", "", 1280, 800, 60.0, ["1920x1080@60.00Hz"])
STREAM_CFG = SINGLE + """
[stream]
default_mode = "1280x800@60"
max_fps = 90
"""


class ReclaimTests(unittest.TestCase):
    """A scene that switches a display off lets Hyprland hand its workspaces to the
    parked virtual output; they have to come back to a real one."""

    def setUp(self):
        self.calls = []
        self.monitors = [dict(UW, focused=False, activeWorkspace={"id": 1}),
                         dict(HEADLESS, focused=True, activeWorkspace={"id": 2})]
        mock.patch("battlestation.hypr.monitors", side_effect=lambda **_: self.monitors).start()
        mock.patch("battlestation.hypr.workspaces", return_value=[
            {"id": 1, "monitor": "DP-1"}, {"id": 2, "monitor": "BS-STREAM"}, {"id": 99, "monitor": "BS-STREAM"},
        ]).start()
        mock.patch("battlestation.hypr.clients", return_value=[
            {"address": "0xabc", "workspace": {"id": 99}}, {"address": "0xdef", "workspace": {"id": 1}},
        ]).start()
        for name in ("focus_workspace", "move_workspace", "move_window"):
            mock.patch(f"battlestation.hypr.{name}",
                       side_effect=lambda *a, n=name: self.calls.append((n, *a))).start()

    def tearDown(self):
        mock.patch.stopall()

    def test_workspaces_and_windows_come_home(self):
        scenes.reclaim_from_virtual("BS-STREAM")
        self.assertEqual(self.calls, [
            ("focus_workspace", 99),          # park the virtual output first so it does not spawn a new one
            ("move_workspace", 2, "DP-1"),
            ("move_window", "0xabc", 1),
            ("focus_workspace", 1),           # focus back on a display you can see
        ])

    def test_attached_stream_is_left_alone(self):
        self.monitors = [dict(HEADLESS, focused=True, activeWorkspace={"id": 1})]
        scenes.reclaim_from_virtual("BS-STREAM")
        self.assertEqual(self.calls, [])

    def test_a_settled_desktop_dispatches_nothing(self):
        # Runs on every display hotplug: a redundant focus would flip back and
        # forth under workspace_back_and_forth, and each dispatch is a hyprctl.
        self.monitors = [dict(UW, focused=True, activeWorkspace={"id": 1}),
                         dict(HEADLESS, focused=False, activeWorkspace={"id": 99})]
        with mock.patch("battlestation.hypr.workspaces", return_value=[
                {"id": 1, "monitor": "DP-1", "windows": 1}, {"id": 99, "monitor": "BS-STREAM", "windows": 0}]), \
                mock.patch("battlestation.hypr.clients") as clients:
            self.assertFalse(scenes.reclaim_from_virtual("BS-STREAM"))
            clients.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_focus_left_on_the_parked_output_comes_back(self):
        self.monitors = [dict(UW, focused=False, activeWorkspace={"id": 1}),
                         dict(HEADLESS, focused=True, activeWorkspace={"id": 99})]
        with mock.patch("battlestation.hypr.workspaces", return_value=[
                {"id": 1, "monitor": "DP-1", "windows": 1}, {"id": 99, "monitor": "BS-STREAM", "windows": 0}]):
            self.assertTrue(scenes.reclaim_from_virtual("BS-STREAM"))
        self.assertEqual(self.calls, [("focus_workspace", 1)])

    def test_reports_that_it_moved_something(self):
        self.assertTrue(scenes.reclaim_from_virtual("BS-STREAM"))


class InstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, name, pid, socket=True):
        d = self.runtime / name
        d.mkdir()
        (d / "hyprland.lock").write_text(f"{pid}\nwayland-1\n")
        if socket:
            (d / ".socket.sock").touch()
        return d

    def test_stale_inherited_signature_is_replaced_by_the_live_one(self):
        self.make("stale", 2 ** 22 + 12345)  # no such pid
        self.make("live", os.getpid())
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "stale"}):
            self.assertEqual(hypr.discover_instance(self.runtime), "live")

    def test_live_inherited_signature_is_trusted(self):
        # A nested dev session must keep talking to its own compositor.
        self.make("mine", os.getpid())
        newer = self.make("other", os.getpid())
        os.utime(newer, (2_000_000_000, 2_000_000_000))
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "mine"}):
            self.assertEqual(hypr.discover_instance(self.runtime), "mine")

    def test_no_socket_or_no_instances(self):
        self.make("half", os.getpid(), socket=False)
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": ""}):
            self.assertIsNone(hypr.discover_instance(self.runtime))
            self.assertIsNone(hypr.discover_instance(self.runtime / "missing"))


class StreamConfigTests(unittest.TestCase):
    def test_section_makes_it_available(self):
        self.assertFalse(cfg_from(SINGLE).stream.configured)
        st = cfg_from(STREAM_CFG).stream
        self.assertTrue(st.configured)
        self.assertEqual((st.output, st.default_mode, st.max_fps), ("BS-STREAM", "1280x800@60", 90))

    def test_rejects_unsafe_values(self):
        bad = SINGLE + '[stream]\noutput = "x\\"; os.exit()"\nunit = "evil; rm"\nmax_fps = 9000\ndefault_mode = "big"\n'
        with self.assertRaises(config.ConfigError) as ctx:
            cfg_from(bad)
        text = " ".join(ctx.exception.problems)
        for key in ("stream.output", "stream.unit", "stream.max_fps", "stream.default_mode"):
            self.assertIn(key, text)

    def test_client_mode_is_clamped_and_even(self):
        st = cfg_from(STREAM_CFG).stream
        env = {"SUNSHINE_CLIENT_WIDTH": "1281", "SUNSHINE_CLIENT_HEIGHT": "801", "SUNSHINE_CLIENT_FPS": "240"}
        self.assertEqual(stream.client_mode(env, st), "1280x800@90")
        self.assertEqual(stream.client_mode({}, st), "1280x800@60")
        junk = {"SUNSHINE_CLIENT_WIDTH": "$(reboot)", "SUNSHINE_CLIENT_HEIGHT": "inf", "SUNSHINE_CLIENT_FPS": "-5"}
        self.assertEqual(stream.client_mode(junk, st), "1280x800@24")
        huge = {"SUNSHINE_CLIENT_WIDTH": "99999", "SUNSHINE_CLIENT_HEIGHT": "99999"}
        self.assertEqual(stream.client_mode(huge, st), "3840x2160@60")


class StreamLuaTests(unittest.TestCase):
    def test_parked_touches_nothing_else(self):
        text = luagen.render_stream("BS-STREAM", "1280x800@60")
        self.assertIn("Stream overlay: parked", text)
        self.assertIn('position = "20000x0"', text)
        self.assertIn('hl.workspace_rule({ workspace = "99", monitor = "BS-STREAM", default = true })', text)
        self.assertNotIn("disabled", text)
        self.assertNotIn("os.getenv", text)

    def test_attached_is_guarded_by_the_instance(self):
        text = luagen.render_stream("BS-STREAM", "1280x800@90", attached=True, disable=["HDMI-A-1"], instance="sig_1")
        self.assertIn('mode = "1280x800@90", position = "0x0"', text)
        guard = text.index('if os.getenv("HYPRLAND_INSTANCE_SIGNATURE") == "sig_1" then')
        self.assertGreater(text.index('hl.monitor({ output = "HDMI-A-1", disabled = true })'), guard)
        self.assertTrue(text.rstrip().endswith("end"))
        with self.assertRaises(ValueError):
            luagen.render_stream("BS-STREAM", "1280x800@90", attached=True, disable=["HDMI-A-1"])

    def test_attached_with_no_real_display(self):
        text = luagen.render_stream("BS-STREAM", "1280x800@60", attached=True, disable=[], instance="sig_1")
        self.assertIn("Stream overlay: attached", text)
        self.assertNotIn("disabled", text)


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "XDG_STATE_HOME": self.tmp.name + "/state", "XDG_CONFIG_HOME": self.tmp.name + "/config",
            "XDG_RUNTIME_DIR": self.tmp.name + "/run"})
        self.env.start()
        self.monitors = [TV, HEADLESS]
        self.reload = mock.patch("battlestation.hypr.reload").start()
        self.errors = mock.patch("battlestation.hypr.config_errors", return_value=[]).start()
        mock.patch("battlestation.hypr.monitors", side_effect=lambda *a, **k: list(self.monitors)).start()
        mock.patch("battlestation.hypr.instance_signature", side_effect=lambda: self.sig).start()
        self.create = mock.patch("battlestation.hypr.create_headless").start()
        self.clients, self.workspaces = [], []
        mock.patch("battlestation.hypr.clients", side_effect=lambda: list(self.clients)).start()
        mock.patch("battlestation.hypr.workspaces", side_effect=lambda: list(self.workspaces)).start()
        self.dispatch = mock.patch("battlestation.hypr.dispatch").start()
        self.remove = mock.patch("battlestation.hypr.remove_output").start()
        self.unit = {"loaded": True, "active": True, "pid": 100, "enabled": False}
        mock.patch("battlestation.stream.unit_state", side_effect=lambda unit: dict(self.unit)).start()
        self.systemctl = mock.patch("battlestation.stream._systemctl").start()
        self.quiet = mock.patch("battlestation.stream._run_quiet").start()
        self.sessions = mock.patch("battlestation.stream.encoder_sessions", return_value=1).start()
        mock.patch("battlestation.edid.read_caps", side_effect=caps_for).start()
        mock.patch("battlestation.scenes.edid.read_caps", side_effect=caps_for).start()
        mock.patch("time.sleep").start()
        self.sig = "sig_1"
        self.cfg = cfg_from(STREAM_CFG)
        self.st = self.cfg.stream

    def tearDown(self):
        mock.patch.stopall()
        self.env.stop()
        self.tmp.cleanup()

    def overlay(self):
        from battlestation import paths
        return paths.stream_lua().read_text() if paths.stream_lua().exists() else None

    def test_switched_off_means_arm_does_nothing(self):
        self.assertEqual(stream.arm(self.st)["armed"], False)
        self.assertIsNone(self.overlay())
        self.systemctl.assert_not_called()

    def test_flag_round_trip(self):
        self.assertFalse(stream.is_enabled())
        stream.set_enabled(True)
        self.assertTrue(stream.is_enabled())
        stream.set_enabled(False)
        stream.set_enabled(False)
        self.assertFalse(stream.is_enabled())

    def test_arm_creates_the_output_after_the_rule_then_starts_sunshine(self):
        stream.set_enabled(True)
        self.monitors = [TV]
        self.unit["active"] = False
        order = []
        self.reload.side_effect = lambda: order.append("reload")
        self.create.side_effect = lambda name: order.append("create")
        self.systemctl.side_effect = lambda action, unit: order.append(action)
        result = stream.arm(self.st)
        self.assertTrue(result["created"])
        self.assertEqual(order, ["reload", "create", "start"])
        self.assertIn("parked", self.overlay())

    def test_arm_restarts_a_sunshine_that_started_before_the_output(self):
        stream.set_enabled(True)
        self.monitors = [TV]
        stream.arm(self.st)
        self.systemctl.assert_called_once_with("restart", self.st.unit)

    def test_attach_detach_leaves_the_scene_file_untouched(self):
        from battlestation import paths
        scenes.apply(self.cfg, "tv-desktop", confirm=False)
        scene_before = paths.scene_lua().read_bytes()
        self.assertNotIn("BS-STREAM", scene_before.decode())  # the virtual output is never a scene's business
        stream.set_enabled(True)
        stream.arm(self.st)
        env = {"SUNSHINE_CLIENT_WIDTH": "1280", "SUNSHINE_CLIENT_HEIGHT": "800", "SUNSHINE_CLIENT_FPS": "90"}
        result = stream.attach(self.st, env)
        self.assertEqual(result["disabled"], ["HDMI-A-1"])
        self.assertIn('"sig_1"', self.overlay())
        self.assertTrue(stream.load_state()["attached"])
        again = self.overlay()
        stream.attach(self.st, env)  # idempotent
        self.assertEqual(self.overlay(), again)
        self.assertTrue(stream.detach()["changed"])
        self.assertIn("parked", self.overlay())
        self.assertFalse(stream.detach()["changed"])  # idempotent
        self.assertEqual(paths.scene_lua().read_bytes(), scene_before)

    def test_attach_rolls_back_when_hyprland_rejects_the_overlay(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        parked = self.overlay()
        self.errors.return_value = ["battlestation-stream.lua:4: boom"]
        with self.assertRaises(stream.StreamError):
            stream.attach(self.st, {})
        self.assertEqual(self.overlay(), parked)
        self.assertFalse(stream.load_state().get("attached"))

    def test_idle_flag_is_only_cleared_if_we_set_it(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        with mock.patch("battlestation.stream._stay_awake_flag", return_value=True):
            stream.attach(self.st, {})
        stream.detach()
        self.quiet.assert_not_called()
        with mock.patch("battlestation.stream._stay_awake_flag", return_value=False):
            stream.attach(self.st, {})
        stream.detach()
        self.assertEqual([c.args[0] for c in self.quiet.call_args_list], [stream.STAY_AWAKE, stream.ALLOW_IDLE])

    def test_client_sees_the_users_workspace_and_stranded_windows_come_home(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        self.monitors = [dict(TV, focused=True, activeWorkspace={"id": 4}), HEADLESS]
        stream.attach(self.st, {})
        self.dispatch.assert_called_once_with('hl.dsp.focus({ workspace = "4" })')
        self.assertEqual(stream.load_state()["homeWs"], 4)
        # Big Picture opened on the parking workspace, a browser on one made mid-stream, a terminal on a real one.
        self.workspaces = [{"id": 99, "monitor": "BS-STREAM"}, {"id": 7, "monitor": "BS-STREAM"},
                           {"id": 4, "monitor": "HDMI-A-1"}]
        self.clients = [{"address": "0xaa", "workspace": {"id": 99}}, {"address": "0xbb", "workspace": {"id": 7}},
                        {"address": "0xcc", "workspace": {"id": 4}}]
        self.monitors = [dict(TV, activeWorkspace={"id": 4}), dict(HEADLESS, activeWorkspace={"id": 7})]
        self.dispatch.reset_mock()
        stream.detach()
        moved = [c.args[0] for c in self.dispatch.call_args_list]
        self.assertEqual(moved[2:], ['hl.dsp.focus({ workspace = "99" })', 'hl.dsp.focus({ workspace = "4" })'])
        self.assertEqual(moved[:2], [
            'hl.dsp.window.move({ workspace = "4", follow = false, window = "address:0xaa" })',
            'hl.dsp.window.move({ workspace = "4", follow = false, window = "address:0xbb" })'])

    def test_windows_stay_put_when_there_is_no_real_display_to_return_to(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        self.monitors = [HEADLESS]  # TV off and unplugged
        stream.attach(self.st, {})
        self.workspaces = [{"id": 99, "monitor": "BS-STREAM"}]
        self.clients = [{"address": "0xaa", "workspace": {"id": 99}}]
        self.dispatch.reset_mock()
        stream.detach()
        self.dispatch.assert_not_called()

    def _stranded_after_tv_reconnect(self):
        # The TV dropped out, Hyprland handed workspace 3 to the parked output,
        # and the TV came back without it.
        self.monitors = [dict(TV, focused=True, activeWorkspace={"id": 2}),
                         dict(HEADLESS, x=20000, focused=False, activeWorkspace={"id": 3})]
        self.workspaces = [{"id": 2, "monitor": "HDMI-A-1", "windows": 2},
                           {"id": 3, "monitor": "BS-STREAM", "windows": 1},
                           {"id": 99, "monitor": "BS-STREAM", "windows": 0}]

    def test_settle_brings_stranded_workspaces_home_and_finds_the_pointer(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        self._stranded_after_tv_reconnect()
        self.dispatch.reset_mock()
        with mock.patch("battlestation.hypr.cursor_pos", return_value=(20100, 50)):
            result = stream.settle()
        self.assertTrue(result["moved"])
        self.assertEqual([c.args[0] for c in self.dispatch.call_args_list], [
            'hl.dsp.focus({ workspace = "99" })',
            'hl.dsp.workspace.move({ workspace = "3", monitor = "HDMI-A-1" })',
            'hl.dsp.focus({ workspace = "2" })',
            'hl.dsp.cursor.move({ x = 1920, y = 1080 })'])

    def test_settle_on_a_settled_desktop_touches_nothing(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        self.monitors = [dict(TV, focused=True, activeWorkspace={"id": 2}),
                         dict(HEADLESS, x=20000, focused=False, activeWorkspace={"id": 99})]
        self.workspaces = [{"id": 2, "monitor": "HDMI-A-1", "windows": 2},
                           {"id": 99, "monitor": "BS-STREAM", "windows": 0}]
        self.dispatch.reset_mock()
        with mock.patch("battlestation.hypr.cursor_pos", return_value=(900, 1800)):
            self.assertFalse(stream.settle()["moved"])
        self.dispatch.assert_not_called()

    def test_settle_leaves_a_live_stream_alone(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        stream.attach(self.st, {})
        self.monitors = [dict(HEADLESS, focused=True, activeWorkspace={"id": 2})]
        self.dispatch.reset_mock()
        self.assertEqual(stream.settle()["skipped"], "attached")
        self.dispatch.assert_not_called()

    def test_settle_without_a_virtual_output_does_nothing(self):
        self._stranded_after_tv_reconnect()
        self.assertEqual(stream.settle()["skipped"], "not armed")
        self.dispatch.assert_not_called()

    def test_settle_needs_no_config(self):
        # The shell runs it on every hotplug; a broken config must not stop it.
        import contextlib
        import io
        from battlestation import cli, paths
        stream.set_enabled(True)
        stream.arm(self.st)
        paths.config_file().parent.mkdir(parents=True, exist_ok=True)
        paths.config_file().write_text("not [valid toml")
        self._stranded_after_tv_reconnect()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch("battlestation.hypr.cursor_pos", return_value=(0, 0)):
            self.assertEqual(cli.main(["stream", "settle"]), 0)
        self.assertEqual(json.loads(buf.getvalue()), {"ok": True, "settled": True, "moved": True})

    def test_window_addresses_are_validated(self):
        mock.patch.stopall()
        with self.assertRaises(hypr.HyprError):
            hypr.move_window('0x1" }) os.exit() --', 1)

    def test_detach_without_state_drops_an_overlay_that_still_blanks_the_tv(self):
        from battlestation import paths
        text = luagen.render_stream("BS-STREAM", "1280x800@60", attached=True, disable=["HDMI-A-1"], instance="sig_1")
        paths.stream_lua().parent.mkdir(parents=True)
        paths.stream_lua().write_text(text)
        self.assertTrue(stream.detach()["recovered"])
        self.assertIsNone(self.overlay())

    def test_arm_keeps_a_live_stream_but_clears_a_stale_one(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        stream.attach(self.st, {})
        self.assertTrue(stream.arm(self.st)["kept"])  # a plugin hot-reload must not end a game
        self.assertTrue(stream.load_state()["attached"])
        self.sig = "sig_2"  # compositor restarted
        stream.arm(self.st)
        self.assertFalse(stream.load_state()["attached"])
        self.assertIn("parked", self.overlay())

    def test_off_while_streaming_detaches_first_and_is_idempotent(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        stream.attach(self.st, {})
        stream.set_enabled(False)
        stream.disarm(self.st)
        self.assertIsNone(self.overlay())
        self.systemctl.assert_called_with("stop", self.st.unit)
        self.remove.assert_called_once_with("BS-STREAM")
        self.assertEqual(stream.load_state(), {})
        self.unit["active"] = False
        self.monitors = [TV]
        self.assertEqual(stream.disarm(None), {"armed": False, "attached": False})

    def test_watchdog_detaches_an_abandoned_stream_and_resumes_it(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        stream.attach(self.st, {"SUNSHINE_CLIENT_FPS": "90"})
        self.assertEqual(stream.watchdog(self.st, now=1000)["action"], "none")
        self.sessions.return_value = 0
        self.assertEqual(stream.watchdog(self.st, now=1000)["action"], "waiting")
        self.assertEqual(stream.watchdog(self.st, now=1000 + self.st.watchdog_idle_s)["action"], "detach")
        self.assertIn("parked", self.overlay())
        self.sessions.return_value = 1  # Moonlight resumed the paused app: Sunshine does not re-run `do`
        self.assertEqual(stream.watchdog(self.st, now=1100)["action"], "attach")
        self.assertIn('mode = "1280x800@90"', self.overlay())

    def test_watchdog_never_attaches_on_its_own(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        self.assertEqual(stream.watchdog(self.st)["action"], "none")  # some other NVENC user, e.g. a recording
        self.assertIn("parked", self.overlay())

    def test_watchdog_detaches_when_sunshine_dies(self):
        stream.set_enabled(True)
        stream.arm(self.st)
        stream.attach(self.st, {})
        self.unit["active"] = False
        self.assertEqual(stream.watchdog(self.st)["action"], "detach")

    def test_scenes_ignore_the_virtual_output(self):
        couch = STREAM_CFG.replace('label = "TV"', 'label = "TV"\ndisable_unlisted = true', 1)
        text, res = scenes.render(cfg_from(couch).scene("tv-desktop"), scenes.real_monitors(cfg_from(couch)), caps_for)
        self.assertEqual(res.disabled, [])
        self.assertNotIn("BS-STREAM", text)
        self.assertEqual([r.match for r in scenes.capture_rules(scenes.real_monitors(self.cfg), caps_for)],
                         [f"desc:{TV_DESC}"])


class SunshineConfTests(unittest.TestCase):
    PREP = {"do": "/x/battlestation stream attach", "undo": "/x/battlestation stream detach", "elevated": "false"}
    WANT = {"capture": "wlr", "encoder": "nvenc", "output_name": "BS-STREAM"}

    def test_admin_page_is_kept_to_this_machine(self):
        st = cfg_from(STREAM_CFG).stream
        self.assertEqual(sunshine.wanted_keys(st)["origin_web_ui_allowed"], "pc")
        self.assertIn("origin_web_ui_allowed", sunshine.missing_keys(st, "origin_web_ui_allowed = lan\n"))
        with mock.patch.object(sunshine.tailscale, "summary", return_value=tailscale.summary(TS_ABSENT)), \
                mock.patch.object(sunshine, "lan_cidr", return_value="10.0.0.0/24"), \
                mock.patch.object(sunshine, "installed_version", return_value="2026.914.233613-1"):
            steps = sunshine.privileged_steps(st)
        printed = "\n".join(c for step in steps for c in step["commands"]) + "".join(s.get("grant", "") for s in steps)
        self.assertNotIn("47990", printed)
        self.assertNotIn("tailscale0", printed)  # tailscaled accepts ahead of ufw; such rules scope nothing
        self.assertIn("--operator", printed)
        grant = json.loads(next(s["grant"] for s in steps if s.get("grant")))
        self.assertEqual(grant["ip"], ["tcp:47984", "tcp:47989", "tcp:48010", "udp:47998-48000", "udp:48010"])

    def test_tailscale_install_step_goes_away_once_usable(self):
        with mock.patch.object(sunshine.tailscale, "summary", return_value=tailscale.summary(ts_fixture())), \
                mock.patch.object(sunshine, "lan_cidr", return_value=""), \
                mock.patch.object(sunshine, "installed_version", return_value="2026.914.233613-1"):
            steps = sunshine.privileged_steps(cfg_from(STREAM_CFG).stream)
        self.assertFalse([s for s in steps if "pacman -S tailscale" in " ".join(s["commands"])])
        self.assertIn("100.101.102.103", next(s["grant"] for s in steps if s.get("grant")))

    def test_merge_keeps_foreign_settings_and_prep_commands(self):
        text = ('# mine\nsunshine_name = den\ncapture = kms\n'
                'global_prep_cmd = [{"do":"lights off","undo":"lights on"}]\n')
        merged = sunshine.merge_conf(text, self.WANT, self.PREP)
        conf = sunshine.parse_conf(merged)
        self.assertIn("# mine", merged)
        self.assertEqual((conf["sunshine_name"], conf["capture"], conf["output_name"]), ("den", "wlr", "BS-STREAM"))
        entries = json.loads(conf["global_prep_cmd"])
        self.assertEqual([e["do"] for e in entries], ["lights off", "/x/battlestation stream attach"])

    def test_merge_is_idempotent_and_replaces_a_moved_cli(self):
        once = sunshine.merge_conf("", self.WANT, self.PREP)
        self.assertEqual(sunshine.merge_conf(once, self.WANT, self.PREP), once)
        moved = dict(self.PREP, do="/y/battlestation stream attach")
        entries = json.loads(sunshine.parse_conf(sunshine.merge_conf(once, self.WANT, moved))["global_prep_cmd"])
        self.assertEqual([e["do"] for e in entries], ["/y/battlestation stream attach"])

    def test_merge_refuses_a_prep_cmd_it_cannot_read(self):
        with self.assertRaises(ValueError):
            sunshine.merge_conf("global_prep_cmd = not json\n", self.WANT, self.PREP)

    def test_missing_keys(self):
        st = cfg_from(STREAM_CFG).stream
        self.assertEqual(sunshine.missing_keys(st, None),
                         ["capture", "encoder", "output_name", "origin_web_ui_allowed", "global_prep_cmd"])
        self.assertEqual(sunshine.missing_keys(st, sunshine.merge_conf("", sunshine.wanted_keys(st), self.PREP)), [])

    def test_write_conf_preserves_mode(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            from battlestation import paths
            paths.sunshine_conf().parent.mkdir(parents=True)
            paths.sunshine_conf().write_text("sunshine_name = den\n")
            os.chmod(paths.sunshine_conf(), 0o600)
            st = cfg_from(STREAM_CFG).stream
            self.assertTrue(sunshine.write_conf(st)["changed"])
            self.assertFalse(sunshine.write_conf(st)["changed"])
            self.assertEqual(paths.sunshine_conf().stat().st_mode & 0o777, 0o600)

    def test_version_gate(self):
        self.assertTrue(sunshine.version_ok("2026.914.233613-1"))
        self.assertTrue(sunshine.version_ok("2027.101.1-1"))
        self.assertFalse(sunshine.version_ok("2026.516.143833-4.1"))
        self.assertFalse(sunshine.version_ok(""))


TS_ABSENT = {"installed": False, "state": "", "operator": False, "ip": "", "name": "", "keyExpiry": "", "peers": []}


def _proc(stdout="", returncode=0, stderr=""):
    return mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)


def ts_fixture(**over):
    """tailscale.status() as it parses the fixture, with us as the operator."""
    raw = (FIXTURES / "tailscale-status.json").read_text()

    def run(args, timeout):
        return _proc(raw) if args[0] == "status" else _proc(json.dumps({"OperatorUser": tailscale._user()}))

    with mock.patch.object(tailscale.shutil, "which", return_value="/usr/bin/tailscale"), \
            mock.patch.object(tailscale, "_run", side_effect=run):
        return {**tailscale.status(), **over}


class TailscaleTests(unittest.TestCase):
    def test_status_reads_self_and_peers(self):
        ts = ts_fixture()
        self.assertEqual((ts["state"], ts["ip"], ts["name"], ts["operator"]),
                         ("Running", "100.101.102.103", "battlestation.tail1234.ts.net", True))
        links = {p["host"]: (p["online"], p["direct"]) for p in ts["peers"]}
        self.assertEqual(links, {"steamdeck": (True, True), "phone": (True, False), "laptop": (False, False)})

    def test_status_survives_no_binary_no_daemon_and_garbage(self):
        with mock.patch.object(tailscale.shutil, "which", return_value=None):
            self.assertEqual(tailscale.status(), TS_ABSENT)
        for answer in (None, _proc("failed to connect", 1), _proc("not json"), _proc("[]")):
            with mock.patch.object(tailscale.shutil, "which", return_value="/usr/bin/tailscale"), \
                    mock.patch.object(tailscale, "_run", return_value=answer):
                ts = tailscale.status()
            self.assertEqual((ts["installed"], ts["state"], ts["peers"]), (True, "", []))
            self.assertIn("systemctl enable --now tailscaled", tailscale.summary(ts)["hint"])

    def test_summary_gates_the_switch(self):
        self.assertEqual(tailscale.summary(TS_ABSENT)["available"], False)
        self.assertEqual(tailscale.summary(TS_ABSENT)["hint"], "")  # the row is hidden, nothing to explain
        on = tailscale.summary(ts_fixture())
        self.assertEqual((on["available"], on["on"], on["name"]), (True, True, "battlestation.tail1234.ts.net"))
        self.assertEqual((on["link"], on["linkPeer"]), ("relayed", "phone"))  # the bad link is the one to surface
        off = tailscale.summary(ts_fixture(state="Stopped"))
        self.assertEqual((off["available"], off["on"], off["name"], off["link"]), (True, False, "", ""))
        self.assertIn("--operator", tailscale.summary(ts_fixture(operator=False))["hint"])
        login = tailscale.summary(ts_fixture(state="NeedsLogin"))
        self.assertEqual(login["available"], False)
        self.assertIn("tailscale up", login["hint"])

    def test_direct_only_when_every_active_peer_is(self):
        ts = ts_fixture()
        ts["peers"] = [p for p in ts["peers"] if p["host"] != "phone"]
        self.assertEqual(tailscale.summary(ts)["link"], "direct")

    def test_set_running_is_bare_up_and_down(self):
        calls = []

        def run(args, timeout):
            calls.append(args)
            return _proc()

        with mock.patch.object(tailscale, "status", return_value=ts_fixture(state="Stopped")), \
                mock.patch.object(tailscale, "_run", side_effect=run):
            tailscale.set_running(True)
            tailscale.set_running(False)
        self.assertEqual(calls, [["up"], ["down"]])  # a flag on `up` makes it demand every non-default setting

    def test_set_running_explains_what_is_missing(self):
        for ts, expect in ((TS_ABSENT, "not installed"), (ts_fixture(operator=False), "--operator"),
                           (ts_fixture(state="NeedsLogin"), "tailscale up")):
            with mock.patch.object(tailscale, "status", return_value=ts), \
                    mock.patch.object(tailscale, "_run") as run, self.assertRaises(tailscale.TailscaleError) as ctx:
                tailscale.set_running(True)
            self.assertIn(expect, str(ctx.exception))
            run.assert_not_called()
        with mock.patch.object(tailscale, "status", return_value=ts_fixture()), \
                mock.patch.object(tailscale, "_run", return_value=_proc("", 1, "Access denied: prefs write")), \
                self.assertRaises(tailscale.TailscaleError) as ctx:
            tailscale.set_running(False)
        self.assertIn("--operator", str(ctx.exception))

    def test_parse_ping(self):
        relayed = "pong from steamdeck (100.64.0.9) via DERP(lhr) in 48ms\n" * 5 + "direct connection not established\n"
        self.assertEqual(tailscale.parse_ping(relayed),
                         {"ip": "100.64.0.9", "direct": False, "via": "DERP(lhr)", "latencyMs": 48.0})
        direct = ("pong from steamdeck (100.64.0.9) via DERP(lhr) in 48ms\n"
                  "pong from steamdeck (100.64.0.9) via 203.0.113.7:41641 in 11.5ms\n")
        self.assertEqual(tailscale.parse_ping(direct),
                         {"ip": "100.64.0.9", "direct": True, "via": "203.0.113.7:41641", "latencyMs": 11.5})
        self.assertIsNone(tailscale.parse_ping("timed out\n"))

    def test_check_hints_at_the_router_when_relayed(self):
        def run(args, timeout):
            if args[0] == "ping":
                return _proc("pong from steamdeck (100.64.0.9) via DERP(lhr) in 48ms\n", 1)
            return _proc(json.dumps({"UDP": True, "IPv6": False, "MappingVariesByDestIP": True, "UPnP": False}))

        with mock.patch.object(tailscale, "status", return_value=ts_fixture()), \
                mock.patch.object(tailscale, "_run", side_effect=run):
            result = tailscale.check("steamdeck")
            listing = tailscale.check("")
        self.assertEqual((result["direct"], result["network"]["hardNat"]), (False, True))
        self.assertIn("UDP 41641", result["hint"])
        self.assertEqual([p["host"] for p in listing["peers"]], ["steamdeck", "phone"])

    def test_key_days_left(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 21, tzinfo=timezone.utc)
        self.assertEqual(tailscale.key_days_left(ts_fixture(), now), 14)
        self.assertIsNone(tailscale.key_days_left(ts_fixture(keyExpiry=""), now))

    def test_doctor_warns_about_relays_and_expiry(self):
        from battlestation import doctor
        found = []
        with mock.patch.object(tailscale, "key_days_left", return_value=14):
            doctor._tailscale_checks(ts_fixture(), lambda area, status, msg: found.append((status, msg)))
        warns = [msg for status, msg in found if status == "warn"]
        self.assertEqual(found[0][0], "ok")
        self.assertTrue(any("phone" in w and "relay" in w for w in warns))
        self.assertTrue(any("expires in 14 days" in w for w in warns))
        self.assertFalse(any("steamdeck" in w for w in warns))


class RemoteSwitchTests(unittest.TestCase):
    def run_cli(self, action, enabled):
        from battlestation import cli
        with mock.patch.object(cli.tailscale, "set_running", return_value=tailscale.summary(ts_fixture())) as running, \
                mock.patch.object(cli, "_load_cfg", return_value=cfg_from(STREAM_CFG)), \
                mock.patch.object(cli.stream, "is_enabled", return_value=enabled), \
                mock.patch.object(cli.stream, "set_enabled") as set_enabled, \
                mock.patch.object(cli.stream, "arm", return_value={"armed": True}) as arm, \
                mock.patch.object(cli, "notify"), mock.patch.object(cli, "out"):
            self.assertEqual(cli.main(["stream", action]), 0)
        return running, set_enabled, arm

    def test_remote_on_also_switches_streaming_on(self):
        running, set_enabled, arm = self.run_cli("remote-on", enabled=False)
        running.assert_called_once_with(True)
        set_enabled.assert_called_once_with(True)
        arm.assert_called_once()

    def test_remote_on_leaves_a_running_stream_alone(self):
        _, set_enabled, arm = self.run_cli("remote-on", enabled=True)
        set_enabled.assert_not_called()
        arm.assert_not_called()

    def test_remote_off_leaves_streaming_alone(self):
        running, set_enabled, arm = self.run_cli("remote-off", enabled=True)
        running.assert_called_once_with(False)
        set_enabled.assert_not_called()
        arm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
