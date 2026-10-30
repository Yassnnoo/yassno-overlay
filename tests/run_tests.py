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

    def test_fast_class_behind_warns(self):
        w = O.RadarWindow.fast_behind(self.fake, self.frame([self.car(1, 800, "GTP", 92.0)]))
        self.assertEqual(w[0], "GTP")
        self.assertAlmostEqual(w[1], 200.0)

    def test_same_class_slower_far_or_off_do_not_warn(self):
        fb = lambda fr: O.RadarWindow.fast_behind(self.fake, fr)
        self.assertIsNone(fb(self.frame([self.car(1, 800, "GT3", 92.0)])))      # свой класс
        self.assertIsNone(fb(self.frame([self.car(1, 800, "GTP", 99.0)])))      # почти такой же темп
        self.assertIsNone(fb(self.frame([self.car(1, 100, "GTP", 90.0)])))      # далеко (900 м)
        self.fake.main.cfg["radar_fast"] = False
        self.assertIsNone(fb(self.frame([self.car(1, 800, "GTP", 90.0)])))      # выключено в настройках

    def test_wraps_around_start_line(self):
        fr = self.frame([self.car(1, 4900, "GTP", 90.0)])
        fr["cars"][0]["lap_dist"] = 100.0
        self.assertAlmostEqual(O.RadarWindow.fast_behind(self.fake, fr)[1], 200.0)

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
