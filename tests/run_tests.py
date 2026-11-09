#!/usr/bin/env python3
"""Проверки логики оверлея. Запуск из папки оверлея:  python tests/run_tests.py
Без экрана тесты с окнами пропускаются; полная самопроверка (все панели, три игры) идёт, если есть дисплей."""
import os
import struct
import subprocess
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yassno_overlay as O          # noqa: E402
import irfake as T                  # noqa: E402


def patch_raw(raw, **vals):
    """Меняет значения переменных в готовом буфере irsdk: CarIdxTrackSurface=(idx, value) и т.п."""
    raw = bytearray(raw)
    nvars, var_off = struct.unpack_from("<10i", raw, 0)[6:8]
    buf = struct.unpack_from("<ii", raw, 48)[1]
    for i in range(nvars):
        b = var_off + i * 144
        _, o, _ = struct.unpack_from("<iii", raw, b)
        name = raw[b + 16:b + 48].split(b"\0")[0].decode()
        if name in vals:
            idx, val, code = vals[name]
            struct.pack_into("<" + code, raw, buf + o + idx * 4, val)
    return bytes(raw)


def run_reader(steps, car=1):
    r = O.IracingReader(source=lambda: None)
    out = None
    for i, (surf, flags) in enumerate(steps):
        raw = patch_raw(T.build(5 + i, 0.3 + i * 0.01, 40.0), CarIdxTrackSurface=(car, surf, "i"), CarIdxSessionFlags=(car, flags, "I"))
        r._src = lambda raw=raw: raw
        out = r.read()
    return [c for c in out["cars"] if c["key"] == car]


class IracingRadio(unittest.TestCase):
    def test_radio_flag_on_talking_car(self):
        r = O.IracingReader(source=lambda: None)
        raw = patch_raw(T.build(5, 0.3, 40.0), RadioTransmitCarIdx=(0, 1, "i"))
        r._src = lambda: raw
        cars = {c["key"]: c for c in r.read()["cars"]}
        self.assertTrue(cars[1]["radio"])
        self.assertFalse(cars[2]["radio"])
        raw = patch_raw(T.build(6, 0.31, 40.0), RadioTransmitCarIdx=(0, -1, "i"))
        r._src = lambda: raw
        self.assertFalse(any(c["radio"] for c in r.read()["cars"]))


class IracingStatus(unittest.TestCase):
    def test_in_world_has_no_status(self):
        c = run_reader([(3, 0)] * 3)
        self.assertEqual(O.status_of(c[0]), ("", ""))

    def test_gone_in_race_is_out(self):
        c = run_reader([(3, 0), (3, 0), (-1, 0)])
        self.assertEqual(O.status_of(c[0])[0], "OUT")

    def test_checkered_is_finish_and_sticks(self):
        self.assertEqual(O.status_of(run_reader([(3, 0), (3, 1)])[0])[0], "FINISH")
        self.assertEqual(O.status_of(run_reader([(3, 0), (3, 1), (-1, 0)])[0])[0], "FINISH")

    def test_disqualified(self):
        self.assertEqual(O.status_of(run_reader([(3, 0), (3, 0x20000)])[0])[0], "DSQ")

    def test_never_seen_is_hidden(self):
        self.assertEqual(run_reader([(-1, 0)] * 2), [])


class Forecast(unittest.TestCase):
    def cars(self, ratings):
        return [{"key": i, "irating": r, "cls": "A", "place": i + 1, "garage": False} for i, r in enumerate(ratings)]

    def test_changes_sum_near_zero_and_order(self):
        f = O.ir_forecast(self.cars([3000, 2500, 2000, 1500, 1000]))
        self.assertAlmostEqual(sum(f.values()), 0.0, delta=60.0)
        self.assertGreater(f[0], 0.0)             # победитель в плюсе
        self.assertLess(f[4], 0.0)                # последний в минусе

    def test_favourite_winning_gains_less_than_underdog(self):
        a = O.ir_forecast(self.cars([3000, 1000]))
        b = O.ir_forecast([{"key": 0, "irating": 1000, "cls": "A", "place": 1, "garage": False},
                           {"key": 1, "irating": 3000, "cls": "A", "place": 2, "garage": False}])
        self.assertGreater(b[0], a[0])


class Radar(unittest.TestCase):
    def setUp(self):
        self.fake = types.SimpleNamespace(main=types.SimpleNamespace(cfg={}), CAR_L=4.8, range_m=lambda: 40.0)

    def car(self, k, ld, cls, best, me=False):
        return {"key": k, "player": me, "garage": False, "in_pits": False, "lap_dist": ld, "cls": cls, "best": best, "est_lap": best}

    def frame(self, others):
        return {"game": "iracing", "track_len": 5000.0, "side_state": 0, "cars": [self.car(0, 1000, "GT3", 100.0, True)] + others}

    def test_scan_ir_sides_and_distances(self):
        fr = self.frame([self.car(1, 1004, "GT3", 100.0), self.car(2, 1030, "GT3", 100.0), self.car(3, 972, "GT3", 100.0)])
        fr["side_state"] = 2                                                     # машина слева
        me, items = O.RadarWindow.scan_ir(self.fake, fr)
        kinds = {it["kind"]: it for it in items}
        self.assertEqual(set(kinds), {"L", "F", "B"})
        self.assertAlmostEqual(kinds["L"]["dy"], 4.0)
        self.assertGreater(kinds["F"]["d"], 20.0)
        self.assertAlmostEqual(kinds["B"]["dy"], -28.0)

    def test_blobs_built(self):
        items = [{"kind": "L", "dy": 3.0, "d": 0.0}, {"kind": "F", "dy": 20.0, "d": 15.0}]
        blobs = O.ir_radar_blobs(200, 200, 100, 100, 38, 18, items, 40.0)
        self.assertEqual(len(blobs), 2)
        yellow = next(b for b in blobs if b[4] == "#ffd23a")
        red = next(b for b in blobs if b[4] == "#ff3b30")
        self.assertGreater(yellow[6](yellow[2] + 1), 0.0)
        self.assertLessEqual(red[5](red[1] + 50), 0.0)


class Misc(unittest.TestCase):
    def test_session_clock(self):
        fr = {"label": "PRACTICE", "kind": "PRACTICE", "elapsed": 100.0, "end_et": 900.0, "max_laps": 0, "cars": []}
        old = O.game_now
        O.game_now = lambda: None
        try:
            self.assertEqual(O.session_clock(fr), ("PRACTICE", "0:13:20", "ОСТАЛОСЬ"))
        finally:
            O.game_now = old

    def test_parse_session_yaml(self):
        info = O.parse_ir_session(T.YAML)
        self.assertEqual(info["track"], "Circuit de Spa-Francorchamps")
        self.assertEqual([s["type"] for s in info["sessions"]], ["Practice", "Race"])
        self.assertIn(2, info["drivers"])

    def test_readers_module_names_visible(self):
        for name in ("IracingReader", "AutoReader", "FINISH_STATES", "_ir_val", "parse_ir_session"):
            self.assertTrue(hasattr(O, name), name)

    def test_lmu_rf2_reader_helpers(self):
        self.assertTrue(callable(O.class_from_name))


class LapLogTests(unittest.TestCase):
    @staticmethod
    def frame(laps, last, best=None, pit=False, temp=24.0, key="s1"):
        me = {"player": True, "laps": laps, "last": last, "best": best, "in_pits": pit, "garage": False}
        return {"cars": [me], "session_start": key, "track": "T", "kind": "race", "track_temp": temp}

    def feed(self, steps, key="s1"):
        ll = O.LapLog()
        for laps, last, kw in steps:
            ll.update(self.frame(laps, last, key=key, **kw))
        return ll

    def test_records_completed_laps_only(self):
        ll = self.feed([(3, 0, {}), (3, 0, {}), (4, 100.5, {}), (4, 100.5, {}), (5, 99.9, {"temp": 25.0})])
        self.assertEqual([l["n"] for l in ll.laps], [4, 5])
        self.assertEqual(ll.laps[1]["temp"], 25.0)

    def test_delta_prev_and_best(self):
        ll = self.feed([(1, 0, {}), (2, 100.0, {}), (3, 101.0, {}), (4, 99.5, {})])
        d = [r["d"] for r in ll.rows(5, "prev")]
        self.assertEqual(d[0], None)
        self.assertAlmostEqual(d[1], 1.0)
        self.assertAlmostEqual(d[2], -1.5)
        db = [r["d"] for r in ll.rows(5, "best")]
        self.assertAlmostEqual(db[2], -0.5)

    def test_count_best_and_pit_laps(self):
        ll = self.feed([(1, 0, {})] + [(1 + i, 100.0 + i, {"pit": i == 2}) for i in range(1, 8)])
        self.assertEqual(len(ll.rows(3)), 3)
        t, n = ll.best()
        self.assertAlmostEqual(t, 101.0)
        pit_row = next(r for r in ll.rows(8) if r["pit"])
        self.assertIsNone(pit_row["d"])

    def test_game_best_wins_and_session_reset(self):
        ll = self.feed([(1, 0, {}), (2, 100.0, {"best": 98.0})])
        self.assertEqual(ll.best(), (98.0, None))
        ll.update(self.frame(1, 0, key="s2"))
        self.assertEqual(ll.laps, [])


class CornerTests(unittest.TestCase):
    @staticmethod
    def rounded_rect(W=300, H=140, r=35, step=3.0):
        import math
        pts, x, y, h = [], 0.0, 0.0, 0.0
        for kind, v in (("L", W / 2 - r), ("A", 0), ("L", H - 2 * r), ("A", 0), ("L", W - 2 * r), ("A", 0), ("L", H - 2 * r), ("A", 0), ("L", W / 2 - r)):
            if kind == "L":
                for _ in range(int(v / step)):
                    pts.append((x, y)); x += math.cos(h) * step; y += math.sin(h) * step
            else:
                n = int(math.pi / 2 * r / step)
                for _ in range(n):
                    pts.append((x, y)); x += math.cos(h) * step; y += math.sin(h) * step; h += (math.pi / 2) / n
        return pts

    def test_four_corners_in_order(self):
        cs = O.detect_corners(self.rounded_rect())
        self.assertEqual([c[1] for c in cs], [1, 2, 3, 4])
        self.assertEqual([c[0] for c in cs], sorted(c[0] for c in cs))

    def test_gentle_bend_is_not_a_corner(self):
        import math
        R, n = 2000.0, 1200
        arc = [(R * math.cos(2 * math.pi * i / n), R * math.sin(2 * math.pi * i / n)) for i in range(n)]
        self.assertEqual(O.detect_corners(arc), [])


class FuelPitWarnTests(unittest.TestCase):
    def run_engine(self, laps_fuel, thr):
        eng = O.AlertEngine()
        me = {"player": True, "garage": False, "in_pits": False, "penalties": 0, "flag": 0, "wheels": None, "laps": 5}
        frame = {"cars": [me], "session_start": 1, "track": "T", "kind": "RACE", "yellow": 0, "game_phase": 5, "rain": 0.0, "max_laps": 0}
        old = O.fuel_calc.compute
        O.fuel_calc.compute = lambda *a, **k: {"laps_fuel": laps_fuel}
        try:
            eng.update(frame, {"fuel_pit_laps": thr})
        finally:
            O.fuel_calc.compute = old
        return eng.active()

    def test_pit_warning(self):
        it = self.run_engine(0.7, 1.0)
        self.assertEqual(it[0][0], 4)
        self.assertIn("ПИТ-СТОП", it[0][1])
        self.assertNotIn("ПИТ-СТОП", self.run_engine(1.4, 1.0)[0][1])      # 1.4 круга — ещё обычное «мало топлива»
        self.assertNotIn("ПИТ-СТОП", self.run_engine(0.7, 0)[0][1])         # порог 0 — выключено


class PitLogTests(unittest.TestCase):
    def test_stop_lap_and_duration(self):
        pl = O.PitLog()
        car = lambda pit, laps: {"session_start": 1, "kind": "RACE", "cars": [{"key": 7, "in_pits": pit, "laps": laps}]}
        pl.update(car(False, 23), now=100)
        self.assertIsNone(pl.info(7, now=100))
        pl.update(car(True, 23), now=110)                       # заехал на 24-м круге
        self.assertEqual(pl.info(7, now=115), (24, 5.0, True))
        pl.update(car(False, 24), now=142)                      # выехал: стоял 32 с
        self.assertEqual(pl.info(7, now=150), (24, 32.0, False))
        pl.update(car(True, 24), now=200); pl.update(car(False, 24), now=201)   # помеха 1 с — не стоп
        self.assertEqual(pl.info(7, now=300)[0:2], (24, 32.0))
        pl.update({"session_start": 1, "kind": "QUALIFY", "cars": []}, now=1)
        pl.update({"session_start": 2, "kind": "RACE", "cars": []}, now=2)
        self.assertIsNone(pl.info(7))                            # новая сессия — сброс


class MicroSectorTests(unittest.TestCase):
    L = 4000.0

    def lap(self, tr, speeds, start=0.0, others=None, dt=0.05, upto=None):
        """Один круг подряд (с продолжением с того же места); speeds — скорость на каждой четверти круга.
        others: список (ключ, класс, множитель времени)."""
        t = start
        st = tr.__dict__.setdefault("_h", {"d": 0.0, "o": {}})
        target = upto if upto is not None else (int(st["d"] // self.L) + 1) * self.L + 1.0
        while st["d"] < target:
            q = min(3, int((st["d"] % self.L) / (self.L / 4)))
            st["d"] += speeds[q] * dt
            cars = [{"key": 0, "cls": "HY", "player": True, "in_pits": False, "garage": False, "lap_dist": st["d"] % self.L, "vehicle": "v"}]
            for k, cl, m in (others or []):
                x = st["o"].get(k, 0.0)
                x += speeds[min(3, int((x % self.L) / (self.L / 4)))] / m * dt
                st["o"][k] = x
                cars.append({"key": k, "cls": cl, "player": False, "in_pits": False, "garage": False, "lap_dist": x % self.L, "vehicle": "o"})
            t += dt
            tr.update({"cars": cars, "track_len": self.L, "track": "T"}, t)
        return t

    def test_first_laps_colours(self):
        tr = O.MicroTracker()
        t = self.lap(tr, [50] * 4)                            # заход на круг: круг неполный, не считается
        self.assertEqual(len(tr.laps), 0)
        t = self.lap(tr, [50] * 4, t)
        self.assertEqual(len(tr.laps), 1)
        t = self.lap(tr, [50] * 4, t, upto=tr._h["d"] + self.L * 0.9)
        vals, cur = tr.mine(4)
        self.assertEqual(vals[:3], ["P", "P", "P"])           # история есть только своя, ничья лучше — равные куски

    def test_slower_is_yellow_faster_is_purple(self):
        tr = O.MicroTracker()
        t = self.lap(tr, [50] * 4)
        t = self.lap(tr, [50] * 4, t)
        t = self.lap(tr, [62, 50, 50, 40], t, upto=tr._h["d"] + self.L * 0.98)
        vals, cur = tr.mine(4)
        self.assertEqual(vals[0], "P")                        # быстрее всего, что было
        self.assertEqual(vals[1], "P")
        t = self.lap(tr, [62, 50, 50, 40], t)                 # круг закончили — он теперь лучший
        t = self.lap(tr, [50, 58, 50, 50], t, upto=tr._h["d"] + self.L * 0.8)
        vals, _ = tr.mine(4)
        self.assertEqual(vals[0], "Y")                        # хуже собственного лучшего куска
        self.assertEqual(vals[1], "P")                        # быстрее лучшего

    def test_pit_lap_not_counted(self):
        tr = O.MicroTracker()
        t = self.lap(tr, [50] * 4)
        n0 = len(tr.laps)
        tr.cars[0]["in_pits"] = True
        tr.update({"cars": [{"key": 0, "cls": "HY", "player": True, "in_pits": True, "garage": False, "lap_dist": tr._h["d"] % self.L, "vehicle": "v"}], "track_len": self.L, "track": "T"}, t + 0.05)
        t = self.lap(tr, [50] * 4, t + 0.05)
        self.assertEqual(len(tr.laps), n0)                    # круг с заездом в боксы не пишется

    def test_other_class_does_not_matter(self):
        tr = O.MicroTracker()
        t = self.lap(tr, [50] * 4, others=[(7, "GT3", 1.0)])
        t = self.lap(tr, [50] * 4, t, others=[(7, "GT3", 1.0)])
        self.assertTrue(any(c == "GT3" for c, _, _ in tr.laps))
        b = tr.ensure(4)
        self.assertIn("GT3", b["all"])
        self.assertIn("HY", b["all"])


class BrakeTrackerTests(unittest.TestCase):
    L = 4000.0

    def frame(self, lap, dist, brk, last=0.0, pit=False):
        return {"cars": [{"key": 0, "player": True, "cls": "HY", "vehicle": "v", "laps": lap, "lap_dist": dist % self.L, "in_pits": pit,
                          "garage": False, "last": last, "pos": (dist * 0.1, 5.0)}],
                "track_len": self.L, "track": "T", "inputs": {"brk": brk, "thr": 0.0}}

    def drive(self, tr, lap, last=0.0, brake_at=(500.0, 2000.0), pit=False):
        d = 0.0
        while d < self.L - 1:
            b = 0.9 if any(a <= d < a + 120 for a in brake_at) else 0.0
            tr.update(self.frame(lap, d, b, last, pit))
            d += 5.0

    def test_points_of_best_lap(self):
        tr = O.BrakeTracker()
        self.drive(tr, 1)
        self.drive(tr, 2, last=90.0)
        self.drive(tr, 3, last=89.0, brake_at=(800.0, 2500.0, 3300.0))
        tr.update(self.frame(4, 0.0, 0.0, last=88.0))
        self.assertEqual(len(tr.points), 3)                 # лучший круг — третий, у него три торможения
        self.assertAlmostEqual(tr.points[0][2], 800.0, delta=8)

    def test_slower_lap_does_not_replace(self):
        tr = O.BrakeTracker()
        self.drive(tr, 1)
        self.drive(tr, 2, last=90.0)
        self.drive(tr, 3, last=95.0, brake_at=(100.0,))
        tr.update(self.frame(4, 0.0, 0.0, last=95.0))
        self.assertEqual(len(tr.points), 2)

    def test_pit_lap_ignored(self):
        tr = O.BrakeTracker()
        self.drive(tr, 1, pit=True)
        self.drive(tr, 2, last=90.0, pit=True)
        tr.update(self.frame(3, 0.0, 0.0, last=90.0, pit=True))
        self.assertEqual(tr.points, [])


class SetupsTests(unittest.TestCase):
    PATHS = ["README.md", "Spa/BMW M4 Mid DF 5.2.svm", "Spa/SRA BMW M4 High DF LVL 4.5.svm", "Spa/HY BMW LDF Low DF level 4.1.svm",
             "Spa/SRA 720S evo High DF LVL1.6.svm", "Spa/LMP3 Ligier High DF lvl 3.1.svm", "Sebring/BMW M4 Mid DF 5.2.svm", "Spa/sub/BMW M4 x.svm"]

    def setUp(self):
        import yassno_setups as S
        self.S = S

    def test_tracks(self):
        S = self.S
        for name, folder in (("Sebring International Raceway", "Sebring"), ("Circuit de la Sarthe", "Lemans"), ("Spa-Francorchamps", "Spa"),
                             ("Autódromo José Carlos Pace", "Interlagos"), ("Circuit of the Americas", "Circuit Of The Americas"),
                             ("Autódromo Internacional do Algarve", "Portimao"), ("Losail International Circuit", "Qatar")):
            self.assertEqual(S.track_folder(name), folder, name)
        self.assertIsNone(S.track_folder("Какая-то трасса"))

    def test_plan_cars(self):
        S = self.S
        f, files, err = S.plan(self.PATHS, "Spa-Francorchamps", "BMW M4 GT3 #31")
        self.assertEqual((f, err), ("Spa", ""))
        self.assertEqual(files, ["Spa/BMW M4 Mid DF 5.2.svm", "Spa/SRA BMW M4 High DF LVL 4.5.svm"])      # без чужих машин и вложенных папок
        self.assertEqual(S.plan(self.PATHS, "Spa", "BMW M Hybrid V8 #15")[1], ["Spa/HY BMW LDF Low DF level 4.1.svm"])
        self.assertEqual(S.plan(self.PATHS, "Spa", "McLaren 720S GT3 Evo")[1], ["Spa/SRA 720S evo High DF LVL1.6.svm"])
        self.assertIn("нет", S.plan(self.PATHS, "Spa", "Ferrari 499P #50")[2])
        self.assertIn("не опознана", S.plan(self.PATHS, "Nowhere", "BMW M4 GT3")[2])

    def test_download_no_overwrite(self):
        import tempfile
        S = self.S
        tree = {"tree": [{"path": p, "type": "blob"} for p in self.PATHS]}
        body = b'VehicleClassSetting="X"\nUpgradeSetting=(1,0,0,0)\n'
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "spa"))                              # папка трассы уже есть в другом регистре
            mine = os.path.join(d, "spa", "BMW M4 Mid DF 5.2.svm")
            open(mine, "wb").write(b"MY OWN")
            res = S.download(d, "Spa-Francorchamps", "BMW M4 GT3", tree_fn=lambda u: tree, get_fn=lambda u: body)
            self.assertTrue(res["ok"])
            self.assertEqual(res["saved"], ["SRA BMW M4 High DF LVL 4.5.svm"])
            self.assertEqual(res["skipped"], ["BMW M4 Mid DF 5.2.svm"])
            self.assertEqual(open(mine, "rb").read(), b"MY OWN")                # свой файл не затёрт
            self.assertTrue(os.path.isfile(os.path.join(d, "spa", "SRA BMW M4 High DF LVL 4.5.svm")))

    def test_api_down_uses_snapshot(self):
        import tempfile
        S = self.S

        def boom(url):
            raise OSError("403")
        with tempfile.TemporaryDirectory() as d:
            res = S.download(d, "Sebring International Raceway", "BMW M4 GT3", tree_fn=boom, get_fn=lambda u: b'VehicleClassSetting="X"\n')
            self.assertTrue(res["ok"])
            self.assertEqual(len(res["saved"]), 2)

    def test_pack(self):
        import tempfile
        body = b'VehicleClassSetting="GT3 McLaren_720S_GT3_Evo WEC2026"\n'
        with tempfile.TemporaryDirectory() as d:
            r = self.S.download_pack(d, "Sebring International Raceway", "McLaren 720S GT3 Evo",
                                tree_fn=lambda u: (_ for _ in ()).throw(OSError("x")), get_fn=lambda u: body)
            self.assertEqual(len(r["saved"]), 6)
            r2 = self.S.download_pack(d, "Sebring", "McLaren 720S GT3 Evo", tree_fn=lambda u: (_ for _ in ()).throw(OSError("x")), get_fn=lambda u: body)
            self.assertEqual((len(r2["saved"]), len(r2["skipped"])), (0, 6))

    def test_local_folder(self):
        import tempfile
        S = self.S
        body = b'VehicleClassSetting="GT3 McLaren_720S_LMGT3_Evo WEC2026"\nUpgradeSetting=(1,0,0,0)\n'
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as game:
            for n in ("PACK 1.0 720S SEB CQ.svm", "PACK 1.0 720S SEB WR.svm", "PACK 1.0 720S SPA CQ.svm", "notes.txt"):
                open(os.path.join(src, n), "wb").write(body)
            open(os.path.join(src, "PACK 1.0 AMR SEB CQ.svm"), "wb").write(b'VehicleClassSetting="GT3 AstonMartin_Vantage_LMGT3 WEC2026"\nUpgradeSetting=(1,0,0,0)\n')
            os.makedirs(os.path.join(src, "monza"))
            open(os.path.join(src, "monza", "any name.svm"), "wb").write(body)       # трасса — по папке, машина — по заголовку внутри файла
            res = S.install_local(game, "Sebring International Raceway", "McLaren 720S GT3 Evo #7", [src])
            self.assertEqual(sorted(res["saved"]), ["PACK 1.0 720S SEB CQ.svm", "PACK 1.0 720S SEB WR.svm"])     # чужая трасса и чужая машина не берутся
            self.assertEqual(S.install_local(game, "Sebring International Raceway", "McLaren 720S GT3 Evo", [src])["saved"], [])
            self.assertEqual(len(S.install_local(game, "Autodromo Nazionale Monza", "McLaren 720S", [src])["saved"]), 1)
            self.assertEqual(S.car_key("PACK AMR SEB.svm"), "vantage")

    def test_rejects_non_setup(self):
        import tempfile
        S = self.S
        tree = {"tree": [{"path": p, "type": "blob"} for p in self.PATHS]}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                S.download(d, "Spa", "BMW M4 GT3", tree_fn=lambda u: tree, get_fn=lambda u: b"<html>rate limit</html>")


class PosEventTests(unittest.TestCase):
    def test_gain_and_loss(self):
        me = types.SimpleNamespace(_pe={}, POS_HOLD=O.Overlay.POS_HOLD)
        f = {"session_start": 1}
        O.Overlay.pos_events(me, f, "ALL", {"a": 1, "b": 2})                 # первый кадр — только запоминаем
        ev = O.Overlay.pos_events(me, f, "ALL", {"a": 2, "b": 1})            # поменялись местами
        self.assertEqual(ev, {"a": -1, "b": 1})
        ev = O.Overlay.pos_events(me, f, "ALL", {"a": 2, "b": 1})            # подсветка держится
        self.assertEqual(ev, {"a": -1, "b": 1})
        me._pe[("ALL", "a")][2] -= 60                                         # прошло больше POS_HOLD
        self.assertEqual(O.Overlay.pos_events(me, f, "ALL", {"a": 2, "b": 1}), {"b": 1})
        self.assertEqual(O.Overlay.pos_events(me, {"session_start": 2}, "ALL", {"a": 2, "b": 1}), {})   # новая сессия — сброс


@unittest.skipUnless(os.environ.get("DISPLAY") or os.name == "nt", "нет экрана — самопроверку панелей пропускаем")
class SelfTest(unittest.TestCase):
    def test_all_games(self):
        for g in ("iracing", "lmu", "rf2"):
            r = subprocess.run([sys.executable, os.path.join(ROOT, "yassno_overlay.py"), "--selftest", "--game", g],
                               cwd=ROOT, capture_output=True, timeout=90)
            out = r.stdout.decode("utf-8", "replace")
            self.assertIn("SELFTEST OK", out, f"{g}: {out[-600:]}")


class WetValue(unittest.TestCase):
    def test_full_wet_is_not_dry(self):
        import yassno_readers as R
        buf = bytearray(4096)
        rel = {"wet_min": 100, "wet_max": 108, "rain_intensity": 116}
        for mn, mx, want in ((0.0, 1.0, 1.0), (1.0, 1.0, 1.0), (0.2, 0.6, 0.6), (0.0, 0.0, 0.0)):
            struct.pack_into("<d", buf, 100, mn)
            struct.pack_into("<d", buf, 108, mx)
            self.assertAlmostEqual(R._wet_value(bytes(buf), 0, rel, "t"), want)


class DaytonaPack(unittest.TestCase):
    def test_daytona_ferrari_296(self):
        import yassno_setups as S
        self.assertEqual(S.track_folder("Daytona International Speedway"), "Daytonarc")
        self.assertEqual(S.car_key("Ferrari 296 GT3"), "296")
        self.assertEqual(S.file_track("HYMO2_1.4_296_DAY_EQ.svm"), "Daytonarc")
        self.assertEqual(S.car_key("HYMO2_1.4_296_DAY_EQ.svm"), "296")
        for n in ("P1Doks_FerrariGT3_Daytona_E_1.4.svm", "P1Doks_FerrariGT3_Daytona_Wet_E_1.4.svm"):
            self.assertEqual((S.file_track(n), S.car_key(n)), ("Daytonarc", "296"))
        self.assertEqual(S.car_key("Ferrari 499P"), "499p")
        for n in ("HYMO2_1.4.0_AMR_DAY_WR.svm", "P1Doks_AstonGT3_Daytona_1.4.1_Safe_R.svm"):
            self.assertEqual((S.file_track(n), S.car_key(n)), ("Daytonarc", "vantage"))
        self.assertEqual(S.car_key("Aston Martin Vantage GT3"), "vantage")
        for n in ("HYMO2_1.4_720S_DAY_WR.svm", "P1Doks_MclarenGT3_Daytona_Safe_R_1.4.1.svm"):
            self.assertEqual((S.file_track(n), S.car_key(n)), ("Daytonarc", "720s"))
        for n in ("HYMO2_1.4_RCF_DAY_WR.svm", "P1Doks_RCF_Daytona_SafeR.svm"):
            self.assertEqual((S.file_track(n), S.car_key(n)), ("Daytonarc", "rcf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
