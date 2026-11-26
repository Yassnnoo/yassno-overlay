import copy, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yassno_ets2_stats as ST

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "ets2stats")


class Ets2StatsFiles(unittest.TestCase):
    """Файлы настоящего ядра плагина (tests/fixtures/ets2stats — результат синтетической поездки)."""

    def test_load_fixture(self):
        ss = ST.load_sessions(FIX, now=1e12)
        self.assertEqual(len(ss), 1)
        s = ss[0]
        self.assertFalse(s["live"])
        self.assertEqual(s["truck"]["brand"], "Volvo")
        self.assertEqual(len(ST.load_jobs(FIX)), 1)
        self.assertTrue(len(ST.load_events(FIX)) >= 6)
        self.assertEqual([e["e"] for e in ST.load_events(FIX, ("fine",))], ["fine"])
        self.assertEqual(ST.fine_events(FIX)[0]["amount"], 500)

    def test_live_flag(self):
        s = ST.load_sessions(FIX, now=1e12)[0]
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sessions"))
            live = dict(s, active=True, updated=1000.0)
            with open(os.path.join(d, "sessions", "a.json"), "w") as f:
                json.dump(live, f)
            self.assertTrue(ST.load_sessions(d, now=1010.0)[0]["live"])
            self.assertIsNotNone(ST.live_session(ST.load_sessions(d, now=1010.0)))
            self.assertFalse(ST.load_sessions(d, now=1100.0)[0]["live"])       # плагин молчит — игра закрыта/упала

    def test_derived(self):
        a = ST.load_sessions(FIX, now=1e12)[0]["acc"]
        d = ST.derived(a)
        self.assertAlmostEqual(d["avg_kmh"], a["km"] / (a["drive_s"] / 3600), places=3)
        self.assertGreater(d["l100"], 0)
        self.assertTrue(0 <= d["over_share"] <= 1)
        self.assertGreaterEqual(d["hard_brakes_100"], 1)
        self.assertTrue(d["style"] is None or 0 <= d["style"] <= 100)
        self.assertIsNone(ST.derived({"km": 0.2})["style"])                    # меньше километра — оценки нет

    def test_job_view(self):
        j = ST.job_view(ST.load_jobs(FIX)[0])
        self.assertEqual((j["status"], j["income"], j["xp"]), ("delivered", 12000.0, 250))
        self.assertEqual((j["src"], j["dst"]), ("Bayonne", "Valladolid"))
        self.assertAlmostEqual(j["mass_t"], 9.0)
        self.assertIs(j["on_time"], True)
        self.assertEqual(j["fines"], 1)
        self.assertEqual(j["dst_comp"], 'Depot "A"')                           # кавычки в названии доехали целыми

    def test_sum_and_summary(self):
        ss = ST.load_sessions(FIX, now=1e12)
        s2 = copy.deepcopy(ss[0])
        s2["acc"]["v_max"] = 120.0
        s2["acc"]["km"] = 10.0
        s2["acc"]["rest_min"] = 50
        s2["start"] = ss[0]["start"] + 1000
        tot = ST.sum_acc([ss[0]["acc"], s2["acc"]])
        self.assertAlmostEqual(tot["km"], ss[0]["acc"]["km"] + 10.0, places=3)
        self.assertEqual(tot["v_max"], 120.0)                                   # максимум, а не сумма
        self.assertEqual(tot["rest_min"], 50)
        self.assertAlmostEqual(tot["hist_s"][4], 2 * ss[0]["acc"]["hist_s"][4], places=3)
        sm = ST.summarize([ss[0], s2], ST.load_jobs(FIX))
        self.assertEqual((sm["sessions"], sm["jobs_done"], sm["jobs_cancelled"]), (2, 1, 0))
        self.assertEqual(sm["income"], 12000)
        self.assertEqual(sm["cities"], ["Bayonne", "Valladolid"])
        self.assertEqual(sm["records"]["best_income"]["income"], 12000.0)
        self.assertEqual(sm["trucks"][0][0], "Volvo FH AB 123")
        self.assertEqual(ST.summarize([], [])["sessions"], 0)

    def test_gear_table(self):
        a = ST.load_sessions(FIX, now=1e12)[0]["acc"]
        g = ST.gear_table(a)
        self.assertEqual(g[0][0], "D12")                                        # передача = индекс - 4
        self.assertTrue(all(x[1] > 0 for x in g))

    def test_bad_lines_and_track(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "jobs.jsonl"), "w", encoding="utf-8") as f:
                f.write('{"status":"cancelled","t1":5,"result":{"penalty":700}}\n{оборванная строка\n\n{"status":"delivered","t1":9}\n')
            js = ST.load_jobs(d)
            self.assertEqual([j["status"] for j in js], ["delivered", "cancelled"])
            os.makedirs(os.path.join(d, "tracks"))
            with open(os.path.join(d, "tracks", "x.csv"), "w") as f:
                f.write("1,2.0,3.0,4.0,50,90\nмусор\n2,2.5,3.5,4.0,51,91\n")
            self.assertEqual(len(ST.load_track("x", d)), 2)
            self.assertEqual(ST.load_track("нет", d), [])
            self.assertEqual(ST.summarize([], js)["penalties"], 700)
        self.assertEqual(ST.load_sessions(os.path.join(FIX, "nope")), [])

    def test_install_plugin(self):
        with tempfile.TemporaryDirectory() as t:
            game, here = os.path.join(t, "game"), os.path.join(t, "app")
            os.makedirs(os.path.join(game, "bin", "win_x64"))
            open(os.path.join(game, "bin", "win_x64", "eurotrucks2.exe"), "wb").close()
            self.assertEqual(ST.install_plugin(game, here, download=False)[0], "error")      # ни своего файла, ни скачивания
            self.assertEqual(ST.plugin_state(game, here), "missing")
            os.makedirs(os.path.join(here, "plugins"))
            src = os.path.join(here, "plugins", ST.PLUGIN_DLL)
            open(src, "wb").write(b"MZ-v1")
            self.assertEqual(ST.install_plugin(game, here)[0], "installed")
            self.assertEqual(ST.plugin_state(game, here), "ok")
            self.assertEqual(ST.install_plugin(game, here)[0], "ok")
            open(src, "wb").write(b"MZ-v2")
            self.assertEqual(ST.plugin_state(game, here), "outdated")
            self.assertEqual(ST.install_plugin(game, here)[0], "updated")
            self.assertEqual(open(os.path.join(ST.plugins_dir(game), ST.PLUGIN_DLL), "rb").read(), b"MZ-v2")
            os.remove(src)                                                                    # из папки программы нет — качаем
            os.remove(os.path.join(ST.plugins_dir(game), ST.PLUGIN_DLL))
            got = []
            st, _ = ST.install_plugin(game, here, fetch=lambda u: got.append(u) or b"MZ-net")
            self.assertEqual((st, got), ("installed", [ST.PLUGIN_RAW]))
            self.assertEqual(ST.install_plugin(game, here, fetch=lambda u: b"<html>")[0], "error")   # не exe — не ставим
            self.assertEqual(ST.install_plugin(os.path.join(t, "none"), here)[0], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class Ets2RouteTests(unittest.TestCase):
    """Маршрут рейса: срез трека по времени и города по пути."""

    def _track(self, names, per=120, t0=1000.0):
        tab = {c[2]: c for c in ST.city_table()}
        cs = [tab[n] for n in names]
        pts, t = [], t0
        for a, b in zip(cs, cs[1:]):
            for i in range(per):
                f = i / per
                pts.append((t, a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, 0.0, 80.0, 0.0))
                t += 2
        return pts, t

    def test_city_table_names(self):
        names = {c[2] for c in ST.city_table()}
        self.assertTrue({"Calais", "Frankfurt am Main", "Łódź", "København", "Le Mans"} <= names)
        self.assertGreater(len(names), 250)

    def test_route_cities_order_and_names(self):
        pts, _ = self._track(["Calais", "Brussel", "Antwerpen", "Amsterdam"])
        got = [c[0] for c in ST.route_cities(pts, "Calais", "Amsterdam")]
        self.assertEqual(got[0], "Calais")
        self.assertEqual(got[-1], "Amsterdam")
        self.assertLess(got.index("Brussel"), got.index("Antwerpen"))
        self.assertEqual(len(got), len(set(got)))                              # город один раз, даже если трасса вернулась

    def test_route_job_name_wins(self):
        pts, _ = self._track(["Antwerpen", "Rotterdam"])
        got = [c[0] for c in ST.route_cities(pts, "Antwerp", "Rotterdam")]
        self.assertEqual(got[0], "Antwerp")                                    # название из рейса, а не из таблицы

    def test_route_far_city_not_included(self):
        pts, _ = self._track(["Calais", "Brussel"])
        got = [c[0] for c in ST.route_cities(pts)]
        self.assertNotIn("Amsterdam", got)
        self.assertEqual(ST.route_cities(pts[:1]), [])

    def test_job_track_slice(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "tracks"))
            with open(os.path.join(d, "tracks", "s1.csv"), "w") as f:
                for t in range(0, 1000, 10):
                    f.write(f"{t},{t},0,0,50,0\n")
            tr = ST.job_track({"session": "s1", "t0": 200.0, "t1": 400.0}, d, pad=0)
            self.assertEqual((tr[0][0], tr[-1][0]), (200.0, 400.0))
            self.assertEqual(ST.job_track({"t0": 1, "t1": 2}, d), [])          # рейс без сессии
            self.assertEqual(ST.job_track({"session": "nope", "t0": 1, "t1": 2}, d), [])


class Rf2CarsTests(unittest.TestCase):
    def test_tables(self):
        import yassno_rf2_cars as R
        self.assertEqual([n for n, _ in R.TABLES], ["Гран при Италии", "Гран при Бельгии"])
        for i in range(len(R.TABLES)):
            cs = R.cars(i)
            self.assertEqual(len(cs), 26)
            self.assertEqual(len({c["n"] for c in cs}), 26)
            self.assertEqual(cs[0]["n"], "7")                                  # сильнейший по прижиму на кг — McLaren #7
            self.assertTrue(all(abs(c["downforce"] / c["mass"] - c["per_kg"]) < 0.002 for c in cs))
        self.assertEqual(R.cars(2)[0], R.cars(0)[0])                            # индекс по кругу
        it, be = {c["n"]: c for c in R.cars(0)}, {c["n"]: c for c in R.cars(1)}
        self.assertEqual((it["4"]["mass"], be["4"]["mass"]), (615, 605))        # Tyrrell #4 стал легче
        self.assertTrue(it["4"]["tc"] and not be["4"]["tc"])
        self.assertEqual(R.short_name("Vladimir Zinoviev"), "V. Zinoviev")
        self.assertEqual(R.short_name("MaX / OldWolf"), "MaX / OldWolf")
