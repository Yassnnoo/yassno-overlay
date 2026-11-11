import ctypes, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yassno_ets2 as E


class Layout(unittest.TestCase):
    def test_zones(self):
        for k, v in E.ZONES.items():
            self.assertEqual(getattr(E.TelemetryMap, k).offset, v, k)
        self.assertEqual(ctypes.sizeof(E.TelemetryMap), 4400)
        self.assertEqual(E.TelemetryMap.truck_f.offset + E._TruckF.speed.offset, 948)

    def test_parse_file(self):
        m = E.TelemetryMap()
        m.sdkActive = True
        m.scs_values.game = 1
        m.truck_f.speed = 25.0
        m.truck_f.engineRpm = 1500
        m.truck_f.fuel = 300.0
        m.truck_f.speedLimit = 22.2222
        m.truck_i.gearDashboard = 7
        m.config_f.engineRpmMax = 2300
        m.config_f.fuelCapacity = 600
        m.truck_dp.coordinateX, m.truck_dp.coordinateZ, m.truck_dp.rotationX = -1100.0, -8870.0, 0.25
        m.config_s.cityDst = b"Bremen"
        fd, path = tempfile.mkstemp()
        os.write(fd, bytes(m).ljust(32768, b"\0"))
        os.close(fd)
        os.environ["YASSNO_ETS2_FILE"] = path
        try:
            r = E.Ets2Reader()
            d = r.read()
            self.assertIsNotNone(d)
            self.assertAlmostEqual(d["speed"], 90.0, places=3)
            self.assertAlmostEqual(d["limit"], 80.0, places=2)
            self.assertEqual(d["gear"], 7)
            self.assertAlmostEqual(d["heading"], 90.0, places=3)
            self.assertEqual((d["x"], d["z"]), (-1100.0, -8870.0))
            self.assertEqual(d["city_dst"], "Bremen")
            r.close()
        finally:
            os.environ.pop("YASSNO_ETS2_FILE")
            os.remove(path)

    def test_inactive(self):
        m = E.TelemetryMap()
        fd, path = tempfile.mkstemp()
        os.write(fd, bytes(m).ljust(32768, b"\0"))
        os.close(fd)
        os.environ["YASSNO_ETS2_FILE"] = path
        try:
            self.assertIsNone(E.Ets2Reader().read())
        finally:
            os.environ.pop("YASSNO_ETS2_FILE")
            os.remove(path)


class Binds(unittest.TestCase):
    def test_parse_and_names(self):
        self.assertEqual(E.parse_bind("k:101"), ("k", 101))
        self.assertEqual(E.parse_bind("j:0:5"), ("j", 0, 5))
        self.assertIsNone(E.parse_bind("x"))
        self.assertIsNone(E.parse_bind(""))
        self.assertEqual(E.bind_name("k:101"), "Num5")
        self.assertEqual(E.bind_name("k:119"), "F8")
        self.assertEqual(E.bind_name("k:65"), "A")
        self.assertIn("6", E.bind_name("j:0:5"))
        self.assertEqual(E.bind_name(""), "—")

    def test_joy_down(self):
        self.assertTrue(E.bind_down("j:1:3", {(1, 3)}))
        self.assertFalse(E.bind_down("j:1:3", {(0, 3)}))
        self.assertFalse(E.bind_down("", set()))


class MapMath(unittest.TestCase):
    def test_px(self):
        t = E.TileMap(allow_net=False)
        x, y = t.to_px(t.x1, t.y1, 3)
        self.assertEqual((round(x), round(y)), (0, 0))
        x, y = t.to_px(t.x2, t.y2, 3)
        self.assertEqual((round(x), round(y)), (512 * 8, 512 * 8))
        self.assertEqual(t.level_for(t.ppu(5) - 1e-9), 5)
        self.assertEqual(t.level_for(1e9), 8)

    @unittest.skipUnless(E.HAVE_PIL, "нет PIL")
    def test_render(self):
        t = E.TileMap(allow_net=False)
        im = t.render(-1100, -8870, 3000, 300, 300)
        self.assertEqual(im.size, (300, 300))
        im2 = t.render(-1100, -8870, 3000, 300, 200, heading=37.0)
        self.assertEqual(im2.size, (300, 200))



class ScaleAndHide(unittest.TestCase):
    def test_cfg_scale_clamped(self):
        import json, tempfile, os
        old = E.CFG_PATH
        try:
            E.CFG_PATH = os.path.join(tempfile.mkdtemp(), "c.json")
            json.dump({"scale": {"mfd": 9, "map": 0.1, "x": "bad"}}, open(E.CFG_PATH, "w"))
            c = E.load_cfg()
            self.assertEqual(c["scale"], {"mfd": E.SCALE_MAX, "map": E.SCALE_MIN})
        finally:
            E.CFG_PATH = old

    def test_in_world(self):
        class A(E.App):
            def __init__(self):
                self.demo = False
                self._t_val = None
                self._t_chg = 0.0
        a = A()
        import time as T
        a._t_chg = T.monotonic()
        d = {"time": 5, "sdk": True, "paused": False}
        self.assertTrue(a.in_world(d))
        self.assertFalse(a.in_world(dict(d, paused=True)))
        self.assertFalse(a.in_world(None))
        a.in_world(d)
        a._t_chg = T.monotonic() - 5            # время стоит 5 с — меню/загрузка
        self.assertFalse(a.in_world(d))
        self.assertTrue(a.in_world(dict(d, time=6)))


class ConvoyTests(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(E.parse_convoy_ids("123, 456=Вася;abc,123\n789"), [("123", ""), ("456", "Вася"), ("789", "")])

    def test_parse_online(self):
        r = E.parse_online({"response": {"online": True, "server": {"name": "Sim 1"}, "location": {"realName": "Berlin", "x": 10.5, "y": -3}}})
        self.assertEqual((r["online"], r["city"], r["x"], r["y"], r["server"]), (True, "Berlin", 10.5, -3.0, "Sim 1"))
        r = E.parse_online({"response": {"online": False, "location": None, "server": None}})
        self.assertFalse(r["online"])
        self.assertIsNone(r["x"])
        r = E.parse_online({"response": {"online": True, "location": {"coordinates": {"x": 1, "y": 2}, "poi": {"name": "Hannover"}}}})
        self.assertEqual((r["x"], r["y"], r["city"]), (1.0, 2.0, "Hannover"))
        self.assertFalse(E.parse_online("мусор")["online"])

    def test_arrow(self):
        import math
        # смотрим на север (heading 0): цель на севере (z меньше) — вперёд, на востоке (x больше) — вправо
        self.assertAlmostEqual(E.rel_arrow(0, -100, 0), 0.0, places=6)
        self.assertAlmostEqual(E.rel_arrow(100, 0, 0), math.pi / 2, places=6)
        # смотрим на запад (heading 90, CCW от севера): цель на западе (x меньше) — вперёд
        self.assertAlmostEqual(E.rel_arrow(-100, 0, 90), 0.0, places=6)
        self.assertAlmostEqual(abs(E.rel_arrow(0, 100, 0)), math.pi, places=6)


class HotkeyTests(unittest.TestCase):
    def test_parse_and_load(self):
        import json
        self.assertEqual(E.parse_hotkey("Ctrl+Shift+L"), [0x11, 0x10, 0x4C])
        self.assertEqual(E.parse_hotkey("Alt+F5"), [0x12, 0x74])
        self.assertIsNone(E.parse_hotkey("мусор"))
        d = tempfile.mkdtemp()
        json.dump({"hotkeys": {"lock": "Ctrl+Alt+K", "quit": "плохо"}}, open(os.path.join(d, "lmu.json"), "w"))
        hk = E.load_hotkeys(d)
        self.assertEqual(hk["lock"], "Ctrl+Alt+K")
        self.assertEqual(hk["quit"], E.HOTKEY_DEFAULTS["quit"])
        self.assertEqual(E.load_hotkeys(os.path.join(d, "нет"))["hide"], E.HOTKEY_DEFAULTS["hide"])
