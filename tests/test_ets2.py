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


class WindowStyleTests(unittest.TestCase):
    def _app(self, **extra):
        import json, tempfile, os
        d = tempfile.mkdtemp()
        E.CFG_PATH = os.path.join(d, "c.json")
        E.STATE_PATH, E.CMD_PATH = E.CFG_PATH + ".state", E.CFG_PATH + ".cmd"
        json.dump(dict({"hide_idle": False, "win_convoy": True}, **extra), open(E.CFG_PATH, "w"))
        return E.App(demo=True)

    def test_styles_radius_and_mark(self):
        E.apply_theme("amber", "square")
        self.assertEqual(E.win_radius(), 0)
        E.apply_theme("amber", "soft")
        self.assertGreater(E.win_radius(), E.BASE_R)
        self.assertEqual(E.MARK, "#e5322d")
        E.apply_theme("amber", "minimal")
        self.assertEqual(E.MARK, "")
        E.apply_theme("amber", "accent")
        self.assertEqual(E.MARK, E.ACCENT)
        E.apply_theme("amber", "nonsense")               # неизвестный стиль — классика
        self.assertEqual(E.win_radius(), E.BASE_R)
        E.apply_theme("amber")

    def test_grip_zone(self):
        class Cv:
            def cget(self, k):
                return {"width": 300, "height": 200}[k]

        class Ev:
            def __init__(self, x, y):
                self.x, self.y = x, y
        z = lambda x, y: E.grip_zone(Cv(), Ev(x, y))     # noqa: E731
        self.assertEqual(z(299, 100), (1, 0))
        self.assertEqual(z(0, 100), (-1, 0))
        self.assertEqual(z(150, 199), (0, 1))
        self.assertEqual(z(150, 0), (0, -1))
        self.assertEqual(z(299, 199), (1, 1))
        self.assertEqual(z(0, 0), (-1, -1))
        self.assertIsNone(z(150, 100))
        self.assertIsNone(z(290, 10))                    # крестик закрытия

    def test_move_mode_panel_and_close(self):
        a = self._app(locked=False)
        try:
            a.root.update()
            self.assertIsNotNone(a.move_panel)
            n = len(a.wins)
            self.assertTrue(all(w.canvas.find_withtag("mv") for w in a.wins))      # зелёная рамка нарисована
            a.set_window("win_convoy", False)
            self.assertEqual(len(a.wins), n - 1)
            self.assertFalse(a.cfg["win_convoy"])
            a.set_window("win_convoy", True)
            self.assertEqual(len(a.wins), n)
            a.wins[0].visible = True
            a.toggle_lock()                                                        # закрепили: панель и рамки уходят
            a.root.update()
            self.assertIsNone(a.move_panel)
            a.tick()
            self.assertFalse(any(w.canvas.find_withtag("mv") for w in a.wins))
        finally:
            a.quit()

    def test_edge_resize_keeps_opposite_corner(self):
        a = self._app(locked=False, win_convoy=False)
        try:
            w = next(x for x in a.wins if x.KEY == "dash")
            w.top.geometry("+200+150")
            a.root.update()

            class Ev:
                pass
            e = Ev()
            e.x, e.y, e.x_root, e.y_root = 1, 40, 201, 190
            w._press(e)                                   # левый край
            self.assertEqual(w._rs, (-1, 0))
            e.x_root = 161                                # потянули влево на 40 px
            w._move(e)
            a.root.update()
            self.assertGreater(w.k, 1.0)
            self.assertEqual(w.top.winfo_x() + int(w.w * w.k), 200 + int(w.w * 1.0))   # правый край остался на месте
            w._release(e)
            self.assertAlmostEqual(a.cfg["scale"]["dash"], round(w.k, 3))
        finally:
            a.quit()


class LauncherLifecycle(unittest.TestCase):
    def test_pid_alive_and_adopt(self):
        import os, subprocess, sys, time
        import yassno_launcher as L
        self.assertTrue(L.pid_alive(os.getpid()))
        self.assertFalse(L.pid_alive(2 ** 22 + 12345))
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(L.pid_alive(p.pid))
            L.kill_pid(p.pid)
            p.wait(timeout=5)
            self.assertFalse(L.pid_alive(p.pid))
        finally:
            p.kill()

    def test_last_view_default_and_persist(self):
        import yassno_launcher as L
        self.assertEqual(L.default_launcher()["last_view"], "")


class SpeedColorTests(unittest.TestCase):
    def test_gradient(self):
        base = "#e8edf3"
        self.assertEqual(E.speed_col({"limit": 80, "speed": 70}, base), base)
        self.assertEqual(E.speed_col({"limit": 0, "speed": 200}, base), base)          # лимит неизвестен
        a = E.speed_col({"limit": 80, "speed": 88}, base)
        b = E.speed_col({"limit": 80, "speed": 100}, base)
        self.assertEqual(b, "#ff1a1a")
        self.assertNotEqual(a, base)
        self.assertGreater(int(a[1:3], 16), int(a[3:5], 16))                           # красный преобладает, но не на максимуме
        self.assertGreater(int(a[3:5], 16), int(b[3:5], 16))


class FrameLevelTests(unittest.TestCase):
    def test_level_parse(self):
        self.assertEqual(E.frame_level({}, "dash"), 100)
        self.assertEqual(E.frame_level({"frame_op": {"dash": 0}}, "dash"), 0)
        self.assertEqual(E.frame_level({"frame_op": {"dash": 60}}, "dash"), 50)       # к ближайшему уровню
        self.assertEqual(E.frame_level({"frame_op": {"dash": "x"}}, "dash"), 100)
        self.assertEqual(E.frame_level({"frame_op": 5}, "dash"), 100)

    def test_bayer_share(self):
        if not E.HAVE_PIL:
            self.skipTest("нет Pillow")
        for lvl in (25, 50, 75):
            m = E.bayer_mask(37, 21, lvl)
            self.assertEqual(m.size, (37, 21))
            share = sum(1 for v in m.getdata() if v) / (37 * 21.0)
            self.assertAlmostEqual(share, lvl / 100.0, delta=0.08)

    def test_panel_modes(self):
        w = WindowStyleTests()._app(locked=True, win_convoy=False)
        try:
            d = next(x for x in w.wins if x.KEY == "dash")
            c = d.canvas
            w.set_frame("dash", 0)
            c.delete("all")
            self.assertIsNone(d.panel(E.PANEL))                                      # без рамки: подложки нет
            w.set_frame("dash", 50)
            c.delete("all")
            it = d.panel(E.PANEL)
            self.assertEqual(c.itemcget(it, "stipple"), "gray50")                    # прозрачная подложка, содержимое не затронуто
            w.set_frame("dash", 100)
            c.delete("all")
            it = d.panel(E.PANEL)
            self.assertEqual(c.itemcget(it, "stipple"), "")
            self.assertEqual(w.cfg["frame_op"]["dash"], 100)
        finally:
            w.quit()


class FrameAllTests(unittest.TestCase):
    def test_all_overrides_and_expands(self):
        self.assertEqual(E.frame_level({"frame_all": 0, "frame_op": {"dash": 100}}, "dash"), 0)
        self.assertEqual(E.frame_level({"frame_all": -1, "frame_op": {"dash": 50}}, "dash"), 50)
        w = WindowStyleTests()._app(locked=True, win_convoy=False, frame_all=0)
        try:
            d = next(x for x in w.wins if x.KEY == "dash")
            self.assertEqual(d.level(), 0)
            w.set_frame("dash", 100)                      # одно окно вернули — остальные остались без рамки
            self.assertEqual(w.cfg["frame_all"], -1)
            self.assertEqual(E.frame_level(w.cfg, "dash"), 100)
            self.assertEqual(E.frame_level(w.cfg, "map"), 0)
        finally:
            w.quit()


class IntroTests(unittest.TestCase):
    def test_intro_draws_and_finishes(self):
        import tkinter as tk
        import yassno_launcher as L
        L.set_ui_theme(L.default_launcher()["theme"])
        r = tk.Tk()
        try:
            r.geometry("900x600+0+0")
            r.update()
            it = L.Intro(r, 900, 600)
            r.after_cancel(it.after_id)
            for t in (0.0, 0.4, 1.0, 1.6, it.DUR - 0.3, it.DUR - 0.01):
                it.draw(t)
                r.update()
            self.assertTrue(it.cv.find_all())
            it.finish()
            self.assertTrue(it.done)
            it.finish()                                   # повторный вызов безопасен
            self.assertAlmostEqual(float(r.attributes("-alpha")), 1.0, places=1)
        finally:
            r.destroy()

    def test_intro_default_on(self):
        import yassno_launcher as L
        self.assertTrue(L.default_launcher()["intro_anim"])
