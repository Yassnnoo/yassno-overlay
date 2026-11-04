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
    def tracker(self, cur_speed, ref_speed=50.0, reach=1000.0, total=1000.0):
        t = O.DeltaTracker()
        t.ref_pair = ([0.0, total], [0.0, total / ref_speed])
        t.cur = [(d, d / cur_speed) for d in range(0, int(reach) + 1, 4)]
        return t

    def test_faster_lap_all_negative(self):
        vals, cur = self.tracker(55.0).micro(10)
        self.assertEqual(len(vals), 10)
        self.assertTrue(all(v is not None and v < 0 for v in vals))
        self.assertIsNone(cur)

    def test_slower_positive_and_unreached_none(self):
        vals, cur = self.tracker(45.0, reach=520.0).micro(10)
        self.assertTrue(all(v > 0 for v in vals[:5]))
        self.assertTrue(all(v is None for v in vals[5:]))
        self.assertEqual(cur, 5)

    def test_no_reference(self):
        t = O.DeltaTracker()
        self.assertEqual(t.micro(8), ([None] * 8, None))

    def test_sum_equals_total_delta(self):
        vals, _ = self.tracker(40.0).micro(20)
        self.assertAlmostEqual(sum(vals), 1000 / 40.0 - 1000 / 50.0, places=3)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
