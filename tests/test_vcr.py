"""Повторы rFactor 2: разбор и пересборка .vcr на синтетическом повторе (настоящие файлы по 200 МБ в репозиторий не кладём)."""
import os, struct, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yassno_vcr as V

MARK = b"\x01\x00\x00\x00\x0b\x00\x00\x00\x03\x00\x00\x00"


def ev(did, g, sub, payload, b=0):
    h = did | (len(payload) << 8) | (sub << 17) | (g << 29)
    return struct.pack("<I", h) + bytes([b]) + payload


def cp(did, laptime, et, lap, sector):
    return ev(did, 3, 6, struct.pack("<ffB", laptime, et, lap) + bytes([sector << 6, 7]) + bytes(5) + b"\x00", b=3)


def loc(did, sub=8, seed=0):
    return ev(did, 0, sub, bytes((seed + i) & 255 for i in range(65)))


def driver(did, name, veh):
    out = bytes([did, len(name)]) + name.encode() + b"\x00"
    for s in ("AOS_RP_94", "1.0", "ab" * 32):
        out += struct.pack("<H", len(s)) + s.encode()
    out += veh.encode().ljust(32, b"\0") + bytes(range(56))
    return out


def make_replay():
    """два пилота (id 3 и 7): у каждого один круг. Пилот 3: линия на 1.0 с, круг 6.0 с; пилот 7: линия на 2.0 с, круг 7.0 с."""
    head = b"//[[test]] [[x]]\nIRSR" + struct.pack("<f", 1.1) + b"x" * 0x120 + b"\x00\x0a\xff\xff" + MARK
    body = head + struct.pack("<I", 2) + driver(3, "Ann One", "CAR_A.VEH") + driver(7, "Bob Two", "CAR_B.VEH")
    slices = []
    t = 0.0
    n = 0
    plan = {  # время среза -> события
        1.0: [cp(3, -1.0, 1.0, 0, 0)], 3.0: [cp(3, 2.0, 3.0, 1, 1)], 5.0: [cp(3, 4.0, 5.0, 1, 2)], 7.0: [cp(3, 6.0, 7.0, 1, 0)],
        2.0: [cp(7, -1.0, 2.0, 0, 0)], 4.0: [cp(7, 2.5, 4.5, 1, 1)], 6.0: [cp(7, 4.5, 6.5, 1, 2)], 9.0: [cp(7, 7.0, 9.0, 1, 0)],
    }
    while t < 10.05:
        evs = []
        if n == 0:
            evs += [ev(3, 2, 16, bytes([1, 1, 1, 1]), b=40), ev(7, 2, 16, bytes([2, 2, 2, 2]), b=41)]
        evs += [loc(3, 8 + n % 3, n), loc(7, 9 + n % 3, n + 1)]
        evs += plan.get(round(t, 3), [])
        slices.append(struct.pack("<fH", t, len(evs)) + b"".join(evs))
        n += 1
        t = n * 0.5 / 5      # 0.1 с
        t = round(t, 6)
    ne = 0
    for s in slices:
        ne += struct.unpack_from("<H", s, 4)[0]
    return body + struct.pack("<iiff", len(slices), ne, 0.0, 10.0) + b"".join(slices)


class VcrTests(unittest.TestCase):
    def setUp(self):
        self.buf = make_replay()
        self.rep = V.Replay(self.buf)

    def test_parse(self):
        r = self.rep
        self.assertEqual([d["id"] for d in r.drivers], [3, 7])
        self.assertEqual(r.drivers[0]["name"], "Ann One")
        self.assertEqual(r.drivers[1]["veh"], "CAR_B.VEH")
        self.assertEqual(r.nslices_real, r.nslices)
        self.assertAlmostEqual(r.version, 1.1, 3)

    def test_laps(self):
        ls = {L.name: L for L in self.rep.laps}
        self.assertEqual(len(ls), 2)
        a, b = ls["Ann One"], ls["Bob Two"]
        self.assertEqual((a.start, a.end), (1.0, 7.0))
        self.assertAlmostEqual(a.time, 6.0, 3)
        self.assertAlmostEqual(b.time, 7.0, 3)
        self.assertEqual((a.s1, a.s2), (2.0, 4.0))

    def test_build_roundtrip(self):
        la, lb = sorted(self.rep.laps, key=lambda L: L.driver)
        out = self.rep.build([lb, la])                       # порядок кругов = порядок новых машин
        r2 = V.Replay(out)
        self.assertEqual([(d["id"], d["name"]) for d in r2.drivers], [(0, "Bob Two"), (1, "Ann One")])
        self.assertEqual(r2.nslices_real, r2.nslices)
        total = sum(struct.unpack_from("<H", out, p + 4)[0] for p in r2.slice_pos)
        self.assertEqual(total, r2.nevents)
        self.assertEqual(r2.slice_t[0], 0.0)
        self.assertEqual(r2.t0, 0.0)
        self.assertAlmostEqual(r2.duration, 7.0, 2)          # 7.0 с — самый долгий круг (округление вверх до шага 0.05)
        # время в новом повторе считается от начала круга
        ls = sorted(r2.laps, key=lambda L: L.driver)
        self.assertEqual([L.name for L in ls], ["Bob Two", "Ann One"])
        self.assertAlmostEqual(ls[1].time, 6.0, 3)
        # событие «результаты»: первым идёт машина с лучшим временем (Ann — 1)
        found = None
        for p in r2.slice_pos[:1]:
            q = p + 6
            for _ in range(struct.unpack_from("<H", out, p + 4)[0]):
                h = struct.unpack_from("<I", out, q)[0]
                if h >> 29 == 3 and (h >> 17) & 0x3f == 5 and h & 255 == 255:
                    found = out[q + 5:q + 5 + ((h >> 8) & 0x1ff)]
                q += 5 + ((h >> 8) & 0x1ff)
        self.assertIsNotNone(found)
        self.assertEqual((found[0], list(found[-2:])), (2, [1, 0]))

    def test_events_are_copied_verbatim(self):
        la = [L for L in self.rep.laps if L.driver == 3][0]
        out = self.rep.build([la])
        r2 = V.Replay(out)
        # все события положения (группа 0) в новом файле — побайтово из исходного, меняется только номер машины
        src = set()
        b = self.buf
        for p in self.rep.slice_pos:
            q = p + 6
            for _ in range(struct.unpack_from("<H", b, p + 4)[0]):
                h = struct.unpack_from("<I", b, q)[0]
                sz = (h >> 8) & 0x1ff
                if h >> 29 == 0 and (h & 255) == 3:
                    src.add(bytes(b[q + 4:q + 5 + sz]))
                q += 5 + sz
        n = 0
        for p in r2.slice_pos:
            q = p + 6
            for _ in range(struct.unpack_from("<H", out, p + 4)[0]):
                h = struct.unpack_from("<I", out, q)[0]
                sz = (h >> 8) & 0x1ff
                if h >> 29 == 0:
                    self.assertEqual(h & 255, 0)
                    self.assertIn(bytes(out[q + 4:q + 5 + sz]), src)
                    n += 1
                q += 5 + sz
        self.assertGreater(n, 30)

    def test_gzip_files(self):
        la = self.rep.laps[0]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.Vcr")
            self.rep.build([la], p)
            with open(p, "rb") as f:
                self.assertEqual(f.read(2), b"\x1f\x8b")
            r2 = V.open_replay(p)
            self.assertEqual(len(r2.drivers), 1)

    def test_errors(self):
        with self.assertRaises(V.VcrError):
            V.Replay(b"not a replay" * 100)
        with self.assertRaises(V.VcrError):
            self.rep.build([])
