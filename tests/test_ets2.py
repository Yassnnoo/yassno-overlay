import os
import ctypes, math, os, sys, tempfile, time, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yassno_ets2 as E
import yassno_launcher  # noqa: F401  (раньше любых Tk-тестов: иначе GC чужого Tk в импорте роняет процесс)


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


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import yassno_route as R
        cls.R = R
        cls.g = R.RoadGraph(os.path.dirname(os.path.dirname(os.path.abspath(R.__file__))) if False else os.path.dirname(os.path.abspath(R.__file__)))

    def test_cities_lookup(self):
        for n in ("Hannover", "BREMEN", "hannover", "Aalborg"):
            self.assertIsNotNone(self.g.city_px(n), n)
        self.assertIsNone(self.g.city_px("Нигдеград"))

    def test_hannover_bremen(self):
        R = self.R
        a, b = self.g.city_px("Hannover"), self.g.city_px("Bremen")
        r = self.g.route_px(a, b)
        self.assertIsNotNone(r)
        straight = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 * R.M_PER_PX / 1000
        self.assertGreater(r["km"], straight * 0.99)
        self.assertLess(r["km"], straight * 3.5)            # не должен петлять через пол-Европы
        self.assertGreater(len(r["pts"]), 20)

    def test_long_route_across_water(self):
        a, b = self.g.city_px("Calais"), self.g.city_px("London")
        r = self.g.route_px(a, b)
        self.assertIsNotNone(r)                              # через тоннель/паром связь есть

    def test_draw_route(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (200, 200), (40, 40, 40))
        pts = [(i * 20.0, i * 5.0) for i in range(100)]
        E.draw_route(ImageDraw.Draw(img), pts, 0.0, 0.0, 0.5, 100, 100, 0.7, 1.0)
        self.assertNotEqual(img.getpixel((100, 100)), (40, 40, 40))   # линия через центр нарисована
        E.draw_route(ImageDraw.Draw(img), [], 0, 0, 1, 100, 100, 0, 1)      # пустой маршрут не падает

    def test_manager_follows_and_recomputes(self):
        class A:
            cfg = {"show_route": True}
        m = E.RouteMgr(A())
        src = self.g.city_px("Hannover")
        R = self.R
        gx, gz = R.X1 + src[0] / R.S * R.XW, R.Y1 + src[1] / R.S * R.YH
        d = {"on_job": True, "city_dst": "Bremen", "city_dst_id": "bremen", "x": gx, "z": gz}
        m.update(d)
        t0 = time.time()
        while m.busy and time.time() - t0 < 20:
            time.sleep(0.05)
        pts = m.update(d)
        self.assertGreater(len(pts), 20)
        A.cfg["show_route"] = False
        self.assertEqual(m.update(d), [])
        A.cfg["show_route"] = True
        d2 = dict(d, on_job=False)
        self.assertEqual(m.update(d2), [])                   # без груза маршрута нет
        self.assertIsNone(m.key)


class MfdMapTab(unittest.TestCase):
    def test_seven_screens(self):
        M = E.MfdWin
        self.assertEqual(M.SCREENS[6], "КАРТА")
        self.assertEqual(len(M.TABS), len(M.SCREENS))
        self.assertEqual(len(M.NUMPAD), 7)
        self.assertEqual(len(M.CTRLF), 7)
        self.assertEqual(E.BIND_NAMES[6:], ("s6", "next", "prev"))
        self.assertTrue(callable(M.scr_6))

    def test_preview_draws_map_tab(self):
        import tkinter as tk
        root = tk.Tk()
        try:
            cfg = dict(E.DEFAULTS)
            pv = E.Preview(root, cfg, "mfd", 1.0)
            pv.draw(6)                                   # не должно падать; при наличии Pillow на холсте есть картинка карты
            if E.HAVE_PIL:
                self.assertTrue(any(pv.canvas.type(i) == "image" for i in pv.canvas.find_all()))
        finally:
            root.destroy()


class BackupPruneTests(unittest.TestCase):
    def test_keeps_last_three(self):
        import tempfile
        import yassno_launcher as L
        old = L.BACKUP_DIR
        with tempfile.TemporaryDirectory() as d:
            L.BACKUP_DIR = d
            try:
                for n in ("20261101-000000", "20261102-000000", "20261103-000000", "20261104-000000", "20261105-000000"):
                    os.makedirs(os.path.join(d, n))
                    open(os.path.join(d, n, "a.txt"), "w").write("x")
                self.assertEqual(L.prune_backups(3), 2)
                self.assertEqual(sorted(os.listdir(d)), ["20261103-000000", "20261104-000000", "20261105-000000"])
                self.assertEqual(L.prune_backups(3), 0)
            finally:
                L.BACKUP_DIR = old


class Ets2SaveTests(unittest.TestCase):
    def test_aes_vector_and_text_save(self):
        import tempfile
        import yassno_ets2_save as S
        with tempfile.TemporaryDirectory() as sd:
            os.makedirs(os.path.join(sd, "config", "avatarcache"))
            with open(os.path.join(sd, "config", "loginusers.vdf"), "w") as f:
                f.write('"users"\n{\n\t"76561198000000001"\n\t{\n\t\t"MostRecent"\t\t"0"\n\t}\n\t"76561198000000002"\n\t{\n\t\t"MostRecent"\t\t"1"\n\t}\n}\n')
            for n in ("76561198000000001", "76561198000000002"):
                open(os.path.join(sd, "config", "avatarcache", n + ".png"), "wb").write(b"x")
            self.assertTrue(S.steam_avatar([sd]).endswith("76561198000000002.png"))
        key = bytes(range(32))
        ct = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
        self.assertEqual(S.aes_cbc_decrypt(key, bytes(16), ct).hex(), "00112233445566778899aabbccddeeff")
        with tempfile.TemporaryDirectory() as d:
            sd = os.path.join(d, "Euro Truck Simulator 2", "profiles", "AA", "save", "1")
            os.makedirs(sd)
            with open(os.path.join(sd, "game.sii"), "w", encoding="utf-8") as f:
                f.write("SiiNunit\n{\neconomy : _nameless.1 {\n experience_points: 1234\n cancelled_job_count: 2\n"
                        " total_distances_by_mode[0]: 777\n total_fuel_litres: 99\n visited_cities_count: 5\n}\n"
                        "bank : _nameless.2 {\n money_account: 5000\n}\nbank_loan : _nameless.3 {\n amount: 100\n}\n}\n")
            sd2 = os.path.join(d, "Euro Truck Simulator 2", "profiles", "AA", "save", "2")
            os.makedirs(sd2)
            with open(os.path.join(sd2, "game.sii"), "w", encoding="utf-8") as f:
                f.write("SiiNunit\n{\neconomy : _nameless.1 {\n total_distances_by_mode[0]: 1\n}\nbank : _nameless.2 {\n money_account: 1\n}\n}\n")
            with open(os.path.join(sd, "info.sii"), "w") as f:
                f.write("SiiNunit\n{\nsave_container : _nameless.1 {\n file_time: 2000\n}\n}\n")
            with open(os.path.join(sd2, "info.sii"), "w") as f:
                f.write("SiiNunit\n{\nsave_container : _nameless.1 {\n file_time: 1000\n}\n}\n")
            cl = os.path.join(d, "Steam", "userdata", "123", "227300", "remote", "steam_profiles", "AA", "save", "autosave")
            os.makedirs(cl)
            with open(os.path.join(cl, "game.sii"), "w", encoding="utf-8") as f:
                f.write("SiiNunit\n{\neconomy : _nameless.1 {\n total_distances_by_mode[0]: 5\n}\nbank : _nameless.2 {\n money_account: 9\n}\n}\n")
            with open(os.path.join(cl, "info.sii"), "w") as f:
                f.write("SiiNunit\n{\nsave_container : _nameless.1 {\n file_time: 5000\n}\n}\n")
            self.assertEqual(S.load_stats([d], steam=[os.path.join(d, "Steam")])["money"], 9)          # Steam Cloud новее
            os.remove(os.path.join(cl, "game.sii"))
            st = S.load_stats([d], steam=[os.path.join(d, "Steam")])
            self.assertEqual((st["km"], st["money"], st["xp"], st["cancelled"], st["debt"], st["stale"]), (777, 5000, 1234, 2, 100, False))


class TripLogTests(unittest.TestCase):
    def test_job_lifecycle_and_km(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "logs", "s.json")
        E_ = E
        tl = E_.TripLog(p)
        base = {"sdk": True, "odo": 1000.0, "brand": "Scania", "truck": "R", "plate": "X", "cargo": "Бетон", "city_src": "A", "city_dst": "B",
                "comp_src": "", "comp_dst": "", "cargo_mass": 18000.0, "income": 5200, "cargo_damage": 0.01, "on_job": True, "route_km": 300.0}
        t = 1000.0
        tl.feed(dict(base), t)
        tl.feed(dict(base, odo=1002.5, route_km=297.5), t + 5)
        self.assertAlmostEqual(tl.data["km_total"], 2.5)
        self.assertEqual(tl.data["last_job"]["state"], "active")
        tl.feed(dict(base, odo=1300.0, route_km=2.0, on_job=True), t + 10)   # скачок больше 5 км не считается пробегом
        self.assertAlmostEqual(tl.data["km_total"], 2.5)
        tl.feed(dict(base, odo=1300.5, route_km=0.0, on_job=False), t + 20)
        self.assertEqual(tl.data["last_job"]["state"], "delivered")
        self.assertEqual(tl.data["jobs_done"], 1)
        self.assertEqual(tl.data["income_total"], 5200)
        self.assertTrue(os.path.isfile(p))
        again = E_.TripLog(p)                                   # данные переживают перезапуск оверлея
        self.assertEqual(again.data["jobs_done"], 1)
        tl.feed(dict(base, cargo="Дрова", route_km=200.0), t + 30)
        tl.feed(dict(base, cargo="Дрова", route_km=150.0, on_job=False), t + 40)
        self.assertEqual(tl.data["last_job"]["state"], "cancelled")
        self.assertEqual(tl.data["jobs_cancelled"], 1)

    def test_sdk_delivery_events(self):
        """С данными SDK рейс закрывается по смене выручки/штрафа, а не по эвристике: доход и повреждения берутся точные."""
        import tempfile
        tl = E.TripLog(os.path.join(tempfile.mkdtemp(), "s.json"))
        base = {"sdk": True, "odo": 500.0, "cargo": "Дрова", "city_src": "A", "city_dst": "B", "cargo_mass": 20000.0, "income": 4000,
                "cargo_damage": 0.0, "on_job": True, "route_km": 120.0, "deliv_revenue": 0, "cancel_penalty": 0, "deliv_km": 0.0,
                "deliv_damage": 0.0, "deliv_xp": 0}
        tl.feed(dict(base), 100.0)
        tl.feed(dict(base, route_km=50.0), 110.0)
        tl.feed(dict(base, on_job=False, route_km=50.0), 120.0)           # задание пропало далеко от цели, но выручка ещё не пришла
        self.assertEqual(tl.data["last_job"]["state"], "active")
        tl.feed(dict(base, on_job=False, route_km=50.0, deliv_revenue=4300, deliv_km=118.0, deliv_damage=0.02, deliv_xp=90), 121.0)
        lj = tl.data["last_job"]
        self.assertEqual(lj["state"], "delivered")                        # по SDK доставлен, хотя по эвристике «осталось 50 км» это была бы отмена
        self.assertEqual((lj["income"], lj["km"], lj["xp"]), (4300, 118.0, 90))
        self.assertEqual(tl.data["income_total"], 4300)
        tl.feed(dict(base, cargo="Уголь", deliv_revenue=4300), 200.0)
        tl.feed(dict(base, cargo="Уголь", on_job=False, deliv_revenue=4300, cancel_penalty=500), 210.0)
        self.assertEqual(tl.data["last_job"]["state"], "cancelled")
        self.assertEqual(tl.data["last_job"]["penalty"], 500)


class LauncherEts2Bar(unittest.TestCase):
    def test_bar_like_other_games(self):
        P = yassno_launcher.Ets2Page
        for n in ("toggle_move", "set_move_btn", "move_on", "save_click"):
            self.assertTrue(callable(getattr(P, n)), n)
        self.assertIn("s6", [k for k, _ in P.LABELS])


class UpdaterRef(unittest.TestCase):
    def test_raw_url_pins_commit(self):
        L = yassno_launcher
        self.assertIn("/main/version.json", L.raw_url("version.json"))
        self.assertIn("/abc123/version.json", L.raw_url("version.json", "abc123"))
        self.assertTrue(callable(L.head_sha))


class NoBlankFrame(unittest.TestCase):
    """Перерисовка окна ETS2 не оставляет пустого холста: старые элементы стираются только после рисования новых."""

    def test_deferred_delete(self):
        import tkinter as tk
        root = tk.Tk()
        try:
            c = E.ScaledCanvas(root, width=100, height=50)
            c.create_text(5, 5, text="47")
            c.create_rectangle(0, 0, 3, 3, tags="mv")
            old_text = c.find_withtag("all")[0]
            c.frame_begin()
            c.delete("all")
            self.assertEqual(len(c.find_all()), 2)              # внутри кадра старое ещё на месте
            c.create_text(5, 5, text="48")
            self.assertEqual(len(c.find_all()), 3)
            c.frame_end()
            ids = c.find_all()
            self.assertNotIn(old_text, ids)                     # старый текст ушёл
            self.assertEqual(len(ids), 2)                       # новый текст и рамка перемещения остались
            c.delete("all")                                     # вне кадра — обычное поведение
            self.assertEqual(len(c.find_all()), 0)
        finally:
            root.destroy()


class RunButtons(unittest.TestCase):
    def test_run_stop_toggle_and_stable_mfd(self):
        import time
        L = yassno_launcher
        L._PV_LOGOS.clear()                                     # картинки прошлых окон Tk недействительны
        app = L.App()
        try:
            root = app.root
            root.update()
            app.select_game("lmu")
            root.update()
            p = app.panels["lmu"]
            self.assertIn("Le Mans", str(p.btn_run.cget("text")))
            app.runner.running = lambda: True
            app.runner.game = "lmu"
            app.refresh_status()
            self.assertEqual(p.btn_run.cget("text"), "Остановить оверлей")
            app.runner.game = "rf2"                             # оверлей идёт для другой игры — здесь кнопка «Запустить»
            app.refresh_status()
            self.assertIn("Le Mans", str(p.btn_run.cget("text")))
            del app.runner.running
            app.show_ets2()
            ep = app.ets2_page
            ep.open_page("mfd")
            root.update()
            ys = set()
            for i in range(len(L.ETS2.MfdWin.TABS)):
                ep.set_screen(i)
                root.update()
                ys.add((ep.scr_btns[0].winfo_rooty(), ep.pwrap.winfo_height()))
            self.assertEqual(len(ys), 1, ys)                     # кнопки экранов МФД не прыгают
        finally:
            app.root.destroy()


class StatsClear(unittest.TestCase):
    def test_clear_stats_dir(self):
        import tempfile
        L = yassno_launcher
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "tel", "x"))
            for rel in ("a.json", "b.json", os.path.join("tel", "l1.json"), os.path.join("tel", "x", "l2.bin")):
                with open(os.path.join(d, rel), "wb") as f:
                    f.write(b"12345")
            self.assertEqual(L.stats_dir_usage(d), (4, 20))
            self.assertEqual(L.clear_stats_dir(d), 0)
            self.assertEqual(L.stats_dir_usage(d), (0, 0))
            self.assertTrue(os.path.isdir(d))                  # сама папка остаётся
            self.assertEqual(os.listdir(d), [])


class LauncherLook(unittest.TestCase):
    """Цвет под игру, сцена предпросмотра, чипы статуса и анимации лаунчера."""

    def test_accent_per_game(self):
        L = yassno_launcher
        old = L.UI_THEME_NAME
        try:
            for theme in ("dark", "light"):
                L.UI_THEME_NAME = theme
                base = L.accent_for("ac")
                self.assertEqual(base[0], L.UI_THEMES[theme]["ACCENT"])         # у плитки без своей палитры — цвет темы
                seen = set()
                for g in ("lmu", "rf2", "iracing", "ets2"):
                    a, h, f = L.accent_for(g)
                    self.assertTrue(all(L.valid_hex(c) for c in (a, h, f)), (g, theme))
                    self.assertNotEqual(a, base[0])
                    seen.add(a)
                    self.assertEqual(L.accent_for(g, by_game=False), base)
                self.assertEqual(len(seen), 4)
        finally:
            L.UI_THEME_NAME = old

    def test_general_page_no_slide(self):
        """«Общие и цвета» открывается без въезда, остальные страницы — с анимацией; перерисовка при смене размера с дебаунсом."""
        import tkinter as tk
        L = yassno_launcher
        app = L.App()
        try:
            app.root.update()
            self.assertEqual(app.view, "home")                  # по умолчанию открывается «Главная»
            tb = app.topbar
            self.assertTrue(tb.winfo_ismapped())
            self.assertEqual(tb.cur, "home")
            for view, fn, tab in (("stats", app.show_stats, "stats"), ("settings", app.show_settings, None)):
                fn()
                app.root.update()
                self.assertTrue(tb.winfo_ismapped(), view)         # верхняя панель на месте на любой странице
                self.assertEqual(tb.cur, tab, view)
            if L.ETS2 is not None:
                app.show_ets2()
                app.root.update()
                self.assertTrue(tb.winfo_ismapped())
                self.assertEqual(tb.cur, "overlay")
            app.open_panel_page("apps")
            self.assertEqual(tb.cur, "apps")
            app.root.update()
            self.assertTrue(app.apps_page.winfo_ismapped())            # «Программы» — отдельный экран, не страница панели
            app.open_panel_page("general", "Сетапы")
            app.root.update()
            self.assertEqual(tb.cur, "setups")
            self.assertTrue(app.setups_page.winfo_ismapped())
            self.assertFalse(app.navhost.winfo_ismapped())              # на этих экранах левого меню нет
            app.show_home()
            app.home_pick("ac")                                         # AC и ETS 2 выбираются на самой «Главной», без перехода на другую страницу
            app.root.update()
            self.assertEqual(app.view, "home")
            self.assertEqual(app.home_page.sel(), "ac")
            self.assertEqual(app.accent_key(), "ac")                    # цвет кнопок — у выбранной игры, а не у предыдущей
            self.assertEqual(app._accent_target, L.accent_for("ac", True))
            self.assertIn("EVO", " ".join(w.cget("text") for w in app.home_page.c_apps.winfo_children() if w.winfo_class() == "Label"))
            if L.ETS2 is not None:
                app.home_pick("ets2")
                app.root.update()
                self.assertEqual(app.view, "home")
                self.assertEqual(app.home_page.sel(), "ets2")
                hp = app.home_page
                hp._sv, hp._sv_t = {"km": 65810, "money": 462061, "xp": 86877, "cities": 97, "cancelled": 1, "debt": 5, "stale": True}, time.time()
                hp.fill_ets2()
                txt = " ".join(w.cget("text") for r in hp.c_set.winfo_children() for w in r.winfo_children() if w.winfo_class() == "Label")
                self.assertIn("65 810 км", txt)
                self.assertIn("462 061 €", txt)
                self.assertIn("копия .bak", txt)
                hp.currency, hp.eur_rate = (lambda: "rub"), (lambda: 100.0)
                hp.fill_ets2()
                txt = " ".join(w.cget("text") for r in hp.c_set.winfo_children() for w in r.winfo_children() if w.winfo_class() == "Label")
                self.assertIn("46 206 100 ₽", txt)
                del hp.currency, hp.eur_rate
            pp = app.panels.get("lmu")
            if pp is not None:
                self.assertEqual(pp.ed_primary("3440x1440@0,-1440,3440x2880", (0, -1440, 3440, 2880)), (0, 0, 3440, 1440))
                self.assertIsNone(pp.ed_primary("1920x1080@0,0,1920x1080", (0, 0, 1920, 1080)))
                self.assertIn("3440×1440", pp.ed_screen_label("3440x1440@0,-1440,3440x2880", (0, -1440, 3440, 2880)))
            app.upd_info = {"newer": True, "version": "9.9.9", "notes": "", "files": []}
            app.set_version_chip()
            self.assertIn("9.9.9", app.topbar.ver.text)
            self.assertEqual(app.topbar.ver.state, "wait")
            app.upd_info = None
            app.set_version_chip()
            self.assertEqual(app.topbar.ver.state, "off")
            app.pick_game("lmu")
            app.root.update()
            self.assertEqual(app.home_page.sel(), "lmu")
            app.show_home()
            self.assertEqual(app.panels[app.selected].extra_ui, {})     # в левом меню нет ни «Главной», ни статистики, ни настроек
            self.assertNotIn("apps", app.panels[app.selected].rows_ui)   # «Главной» в левом меню больше нет
            app.select_game("lmu")                              # лаунчер стартует на «Главной», страницы игры открываем явно
            app.root.update()
            p = app.panels["lmu"]
            p.show_page("fuel")
            self.assertIn("slide", getattr(p.scroll, "_anims", {}))
            p.show_page("general")
            self.assertEqual(list(map(float, p.scroll.coords(p._swin))), [0.0, 0.0])
            self.assertNotIn("slide", getattr(p.scroll, "_anims", {}))
            n = [0]
            raw = p._draw_preview_raw
            p._draw_preview_raw = lambda: (n.__setitem__(0, n[0] + 1), raw())
            p.anim_on = True
            p._pending = False
            p._anim_tick()                                      # на «Общие и цвета» живой предпросмотр не перерисовывается
            self.assertEqual(n[0], 0)
            p.show_page("fuel")
            n[0] = 0
            p._anim_tick()                                      # на странице конкретного оверлея — анимируется
            self.assertEqual(n[0], 1)
            p.show_page("general")
            p.fit_later()
            p.fit_later()                                       # дебаунс: два вызова подряд — одна отложенная перерисовка
            self.assertIsNotNone(p._fit_job)
            p.show_page("trackmap")                             # чёрный стиль карты: своя машина зелёная, соперники жёлтые
            p.v["map_style"].set(L.tr(dict(L.MODES["map_style"])["black"]))
            p.v["map_nums"].set(True)
            p._draw_preview_raw()
            fills = [p.canvas.itemcget(i, "fill") for i in p.canvas.find_all() if p.canvas.type(i) == "oval"]
            self.assertIn("#22c55e", fills)
            self.assertIn("#facc15", fills)
        finally:
            app.root.destroy()

    def test_retint_and_scene(self):
        import tkinter as tk
        L = yassno_launcher
        root = tk.Tk()
        try:
            a = tk.Label(root, text="x", fg="#123456", bg="#000000")
            keep = tk.Label(root, text="y", fg="#123456")
            keep._no_tint = True
            m = tk.Menu(root, activebackground="#123456", activeforeground="#000000")
            a.pack(); keep.pack()
            L.retint(root, {"#123456"}, "#abcdef", "#ffffff")
            self.assertEqual(str(a.cget("fg")), "#abcdef")
            self.assertEqual(str(keep.cget("fg")), "#123456")                    # помеченное не перекрашивается
            self.assertEqual((str(m.cget("activebackground")), str(m.cget("activeforeground"))), ("#abcdef", "#ffffff"))
            cv = tk.Canvas(root, width=400, height=300)
            for kind in ("track", "road"):
                for light in (False, True):
                    cv.delete("all")
                    L.draw_scene(cv, 400, 300, kind, light)
                    self.assertGreater(len(cv.find_withtag("scene")), 40)
            chip = L.StatusChip(root)
            chip.set("Оверлей: работает", "on")
            chip.pulse(0.5)
            self.assertEqual(chip.itemcget("ring", "state"), "normal")
            chip.set("Оверлей: остановлен", "off")
            chip.pulse(0.5)
            self.assertEqual(chip.itemcget("ring", "state"), "hidden")
        finally:
            root.destroy()

    def test_animate_off_is_instant(self):
        import tkinter as tk
        L = yassno_launcher
        root = tk.Tk()
        old = L.UI_ANIM
        try:
            L.UI_ANIM = False
            got, done = [], []
            L.animate(root, "t", 200, got.append, lambda: done.append(1))
            self.assertEqual((got, done), ([1.0], [1]))
            self.assertAlmostEqual(L.ease(0.0), 0.0)
            self.assertAlmostEqual(L.ease(1.0), 1.0)
        finally:
            L.UI_ANIM = old
            root.destroy()


class TrackPanelTests(unittest.TestCase):
    """Окно «Трасса» в телеметрии: камера и три вида на синтетическом круге."""

    @staticmethod
    def lap_raw(shift, T, no):
        import math
        L = yassno_launcher
        cols = {k: [0.0] * 600 for k in L.TEL_COLS}
        for i in range(600):
            f = i / 599
            a = 2 * math.pi * f
            r = 400 + 100 * math.sin(3 * a)
            cols["x"][i], cols["z"][i] = r * math.cos(a) + shift * math.sin(2 * a), 0.7 * r * math.sin(a)
            cols["t"][i], cols["d"][i] = T * f, 3000 * f
            cols["spd"][i] = 150 + 60 * math.sin(5 * a)
            cols["brk"][i] = 1.0 if math.sin(5 * a) < -0.8 else 0.0
            cols["gear"][i] = 4
        return {"lap": no, "time": T, "cols": cols}

    def test_camera_projection(self):
        L = yassno_launcher
        cam = L.Cam3(0, 2, 0, 0, 1, 0.0, 800, 600, 800)        # смотрим на +z (на карте «вверх»), +x справа
        a = cam.to(0, 2, 10)                       # прямо по курсу, на высоте камеры
        sx, sy = cam.scr(a)
        self.assertAlmostEqual(sx, 400, delta=0.5)
        self.assertAlmostEqual(sy, 300, delta=0.5)
        r = cam.scr(cam.to(5, 2, 10))              # справа от курса -> правее центра
        self.assertGreater(r[0], 400)
        self.assertIsNone(cam.line((0, 0, -5), (0, 0, -8)))  # всё позади камеры
        self.assertIsNotNone(cam.poly([(-1, 0, 5), (1, 0, 5), (1, 0, -5), (-1, 0, -5)]))  # режется near-плоскостью

    def test_three_views_and_playback(self):
        import gzip, json, tempfile, tkinter as tk
        L = yassno_launcher
        d = tempfile.mkdtemp()
        fps = []
        for i, (sh, T) in enumerate(((0.0, 90.0), (6.0, 91.5))):
            fp = os.path.join(d, f"{i}.json.gz")
            with gzip.open(fp, "wt") as f:
                json.dump(self.lap_raw(sh, T, i + 1), f)
            fps.append(fp)
        root = tk.Tk()
        try:
            h = tk.Frame(root)
            h.A, h.B, h.lap, h.cur_d, h.track = fps[0], fps[1], L.load_tlap, None, None
            h.draw_cursor = lambda: None
            h.items = [(1, fps[0], L.load_tlap(fps[0])), (2, fps[1], L.load_tlap(fps[1]))]
            h.close_track = lambda: None
            h.app = type("A", (), {"cfg": {}, "save_cfg": lambda s: None})()
            tw = L.TrackPanel(root, h)
            tw.pack(fill="both", expand=True)
            root.geometry("700x600")
            tw.update()
            self.assertAlmostEqual(tw.tmax, 91.5, delta=0.1)
            for m in ("top", "chase", "orbit"):
                tw.mode.set(m)
                tw.model.set("Квадрат" if m == "orbit" else "Авто")
                for t in (0.0, 45.0, 91.5):
                    tw.t = t
                    tw.draw()
                    self.assertGreater(len(tw.cv.find_all()), 20, (m, t))
            tw.t = 45.0
            a, b = tw.car("A"), tw.car("B")
            self.assertGreater(a["d"], b["d"])            # A быстрее: к 45 с уже дальше по трассе
            # лево/право: правый поворот на карте (+z вверх) должен выглядеть правым на экране
            tw.t = 10.0
            c1, c2 = tw.car("A"), tw.car("A")
            self.assertAlmostEqual(c1["x"], c2["x"])
            prev = None
            worst = 0.0
            for n in range(60):                                # ход на 1/30 с не даёт скачков позиции и курса
                tw.t = 20.0 + n / 30.0
                cur = tw.car("A")
                if prev:
                    worst = max(worst, math.hypot(cur["x"] - prev["x"], cur["z"] - prev["z"]))
                    self.assertGreater(cur["hx"] * prev["hx"] + cur["hz"] * prev["hz"], 0.99)
                prev = cur
            self.assertLess(worst, 4.0)
            tw.on_chart(1500)
            self.assertAlmostEqual(tw.car("A")["d"], 1500, delta=30)
            # дорога по всем кругам: шире минимальной там, где круги разъезжаются, и A не обязан быть по центру
            self.assertGreater(max(abs(a - b) for a, b in zip(tw.rib["L"][50], tw.rib["R"][50])), 0)
            tw.mode.set("orbit")
            tw.draw()
            before = tuple(tw.pan)
            tw.on_pan_press(type("E", (), {"x": 100, "y": 100})())
            tw.on_pan(type("E", (), {"x": 160, "y": 100})())     # правая кнопка: вид сдвигается вбок
            self.assertNotEqual(before, tuple(tw.pan))
            tw.reset_view()
            self.assertEqual(tw.pan, [0.0, 0.0])
            tw.shutdown()
        finally:
            root.destroy()


    def test_road_uses_other_sessions(self):
        import gzip, json, tempfile
        L = yassno_launcher
        d = tempfile.mkdtemp()
        old = (L.STATS_DIR, L.TEL_DIR)
        L.STATS_DIR, L.TEL_DIR = os.path.join(d, "stats"), os.path.join(d, "tel")
        L._ROAD_LAPS.clear()
        L._SESS_CACHE.clear()
        try:
            os.makedirs(L.STATS_DIR)
            for i, name in enumerate(("s1", "s2")):
                with open(os.path.join(L.STATS_DIR, name + ".json"), "w", encoding="utf-8") as f:
                    json.dump({"game": "lmu", "track": "Fuji", "laps": []}, f)
                os.makedirs(os.path.join(L.TEL_DIR, name))
                with gzip.open(os.path.join(L.TEL_DIR, name, "lap1.json.gz"), "wt") as f:
                    json.dump(self.lap_raw(3.0 * i, 90.0 + i, 1), f)
            got = L.other_track_laps(os.path.join(L.STATS_DIR, "s1.json"))
            self.assertEqual(len(got), 1)                      # круг второй сессии, своя сессия не считается
            self.assertEqual(L.other_track_laps(os.path.join(L.STATS_DIR, "s1.json")), got)   # из кэша
        finally:
            L.STATS_DIR, L.TEL_DIR = old
            L._ROAD_LAPS.clear()
            L._SESS_CACHE.clear()
