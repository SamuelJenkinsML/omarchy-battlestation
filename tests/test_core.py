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

from battlestation import config, edid, luagen, mangohud, pad, scenes, ws  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
