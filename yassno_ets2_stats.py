"""Yassno Overlay — статистика ETS 2 / ATS от плагина yassno_ets2_stats.dll (без сторонних библиотек).

Плагин живёт внутри игры и пишет файлы всегда, даже если лаунчер не запущен:
  %LOCALAPPDATA%\\YassnoOverlay\\ets2\\sessions\\<id>.json   итоги сессии (одна сессия = один запуск игры)
  %LOCALAPPDATA%\\YassnoOverlay\\ets2\\jobs.jsonl            по строке на рейс (доставлен / отменён / завершён)
  %LOCALAPPDATA%\\YassnoOverlay\\ets2\\events.jsonl          штрафы, сборы, заправки, удары, ремонт, паромы…
  %LOCALAPPDATA%\\YassnoOverlay\\ets2\\tracks\\<id>.csv      трек: unix_time,x,z,y,км/ч,курс
Лаунчер только читает эти файлы и показывает. Этот модуль: чтение, свод за всё время, рекорды, установка dll в игру.
"""
import hashlib
import json
import os
import time
import urllib.request

PLUGIN_DLL = "yassno_ets2_stats.dll"
PLUGIN_RAW = "https://raw.githubusercontent.com/Yassnnoo/yassno-overlay/main/plugins/" + PLUGIN_DLL
LIVE_SEC = 20.0                                       # сессия «идёт сейчас», если плагин обновлял файл не позже

# поля накопителя: как сводить несколько сессий
MAX_KEYS = ("v_max", "rpm_max", "max_decel", "max_accel", "max_lat", "brake_temp_max", "oil_temp_max", "water_temp_max",
            "over_max", "alt_max", "cargo_dmg_max")
MIN_KEYS = ("alt_min",)
LIST_KEYS = ("hist_s", "hist_km", "gear_s")
SKIP_KEYS = ("rest_min",)


def stats_dir(game="ets2"):
    base = os.environ.get("YASSNO_ETS2_STATS_DIR")
    if base:
        return base
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(root, "YassnoOverlay", game)


# ---------------------------------------------------------------------------------------------------- чтение файлов
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _read_lines(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue                                   # обрыв строки при выключении ПК — пропускаем
                if isinstance(d, dict):
                    out.append(d)
    except OSError:
        pass
    return out


def load_sessions(folder=None, now=None):
    """Сессии, новые сверху. Поле live: плагин обновлял файл недавно и не писал «active: false»."""
    folder = folder or stats_dir()
    now = time.time() if now is None else now
    d = os.path.join(folder, "sessions")
    out = []
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return out
    for n in names:
        s = _read_json(os.path.join(d, n))
        if not s or not isinstance(s.get("acc"), dict):
            continue
        s["live"] = bool(s.get("active")) and now - float(s.get("updated") or 0) < LIVE_SEC
        out.append(s)
    out.sort(key=lambda s: float(s.get("start") or 0), reverse=True)
    return out


def load_jobs(folder=None):
    """Рейсы, новые сверху."""
    jobs = _read_lines(os.path.join(folder or stats_dir(), "jobs.jsonl"))
    jobs.sort(key=lambda j: float(j.get("t1") or 0), reverse=True)
    return jobs


def load_events(folder=None, kinds=None, limit=None):
    ev = _read_lines(os.path.join(folder or stats_dir(), "events.jsonl"))
    if kinds:
        ev = [e for e in ev if e.get("e") in kinds]
    ev.sort(key=lambda e: float(e.get("t") or 0), reverse=True)
    return ev[:limit] if limit else ev


def load_track(session_id, folder=None, step=1):
    """Трек сессии: список (unix_time, x, z, y, км/ч, курс). step>1 — прореживание."""
    pts = []
    try:
        with open(os.path.join(folder or stats_dir(), "tracks", str(session_id) + ".csv"), "r", encoding="utf-8") as f:
            for i, ln in enumerate(f):
                if step > 1 and i % step:
                    continue
                p = ln.strip().split(",")
                if len(p) >= 6:
                    try:
                        pts.append(tuple(float(v) for v in p[:6]))
                    except ValueError:
                        pass
    except OSError:
        pass
    return pts


def live_session(sessions):
    """Идущая сейчас сессия или None."""
    for s in sessions:
        if s.get("live"):
            return s
    return None


_cache = {"key": None, "val": None}


def _signature(folder):
    """Что поменялось на диске: папка сессий (плагин пересоздаёт файл — меняется время папки), рейсы и события."""
    sig = []
    for rel in ("sessions", "jobs.jsonl", "events.jsonl"):
        try:
            st = os.stat(os.path.join(folder, rel))
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


def snapshot(folder=None, now=None):
    """Сессии + рейсы одним вызовом с кэшем: перечитывает файлы только если они изменились. Флаг live считается заново."""
    folder = folder or stats_dir()
    now = time.time() if now is None else now
    key = (folder, _signature(folder))
    if _cache["key"] != key:
        _cache["val"] = {"sessions": load_sessions(folder, now), "jobs": load_jobs(folder), "folder": folder, "sig": key}
        _cache["key"] = key
    val = _cache["val"]
    for s in val["sessions"]:
        s["live"] = bool(s.get("active")) and now - float(s.get("updated") or 0) < LIVE_SEC
    return val


# ---------------------------------------------------------------------------------------------------- свод
def sum_acc(accs):
    """Сводит накопители (суммы — складывает, максимумы — берёт максимум, массивы — поэлементно)."""
    tot = {}
    for a in accs:
        for k, v in a.items():
            if k in SKIP_KEYS:
                continue
            if k in LIST_KEYS:
                cur = tot.setdefault(k, [0.0] * len(v))
                if len(cur) < len(v):
                    cur.extend([0.0] * (len(v) - len(cur)))
                for i, x in enumerate(v):
                    cur[i] += float(x or 0)
            elif k in MAX_KEYS:
                tot[k] = max(tot.get(k, 0.0), float(v or 0))
            elif k in MIN_KEYS:
                if a.get("alt_max", 0) or a.get("alt_min", 0):
                    tot[k] = float(v) if k not in tot else min(tot[k], float(v))
            elif isinstance(v, (int, float)):
                tot[k] = tot.get(k, 0.0) + float(v)
    rests = [float(a["rest_min"]) for a in accs if a.get("rest_min", -1) is not None and float(a.get("rest_min", -1)) >= 0]
    tot["rest_min"] = min(rests) if rests else -1
    return tot


def _div(a, b, default=0.0):
    return a / b if b else default


def derived(acc):
    """Производные показатели накопителя: средняя скорость, расход, доли и условная оценка стиля вождения."""
    km = float(acc.get("km", 0))
    drive_h = float(acc.get("drive_s", 0)) / 3600.0
    over_share = _div(float(acc.get("over_s", 0)), float(acc.get("limit_s", 0)))
    over10_share = _div(float(acc.get("over10_s", 0)), float(acc.get("limit_s", 0)))
    hb100 = _div(float(acc.get("hard_brakes", 0)) * 100.0, km)
    col1000 = _div(float(acc.get("collisions", 0)) * 1000.0, km)
    corner = _div(float(acc.get("hard_corner_s", 0)), float(acc.get("drive_s", 0)))
    # 100 баллов, минус за резкие торможения на 100 км, превышение лимита, удары и резкие повороты (потолки у каждого штрафа)
    score = 100.0 - min(25.0, hb100 * 4.0) - min(30.0, over_share * 60.0 + over10_share * 40.0) - min(30.0, col1000 * 6.0) - min(15.0, corner * 150.0)
    if km < 1.0:
        score = None
    play = float(acc.get("play_s", 0))
    return {
        "avg_kmh": _div(km, drive_h),
        "drive_share": _div(float(acc.get("drive_s", 0)), play),
        "l100": _div(float(acc.get("fuel_used", 0)) * 100.0, km),
        "idle_l_h": _div(float(acc.get("fuel_idle", 0)), float(acc.get("idle_s", 0)) / 3600.0),
        "adblue_l100": _div(float(acc.get("adblue_used", 0)) * 100.0, km),
        "over_share": over_share, "over10_share": over10_share,
        "night_share": _div(float(acc.get("km_night", 0)), km),
        "loaded_share": _div(float(acc.get("km_loaded", 0)), km),
        "cruise_share": _div(float(acc.get("km_cruise", 0)), km),
        "avg_rpm": _div(float(acc.get("rpm_t", 0)), float(acc.get("drive_s", 0))),
        "avg_throttle": _div(float(acc.get("thr_t", 0)), float(acc.get("drive_s", 0))),
        "avg_brake": _div(float(acc.get("brk_t", 0)), float(acc.get("drive_s", 0))),
        "hard_brakes_100": hb100, "collisions_1000": col1000,
        "damage_total": sum(float(acc.get(k, 0)) for k in ("dmg_engine", "dmg_trans", "dmg_cabin", "dmg_chassis", "dmg_wheels", "dmg_trailer")),
        "style": None if score is None else max(0.0, min(100.0, score)),
    }


def job_view(j):
    """Рейс с готовыми полями для показа: длительность, средняя скорость, вовремя ли, расход."""
    acc = j.get("acc") or {}
    res = j.get("result") or {}
    d = derived(acc)
    g0, g1, dl = float(j.get("game0") or 0), float(j.get("game1") or 0), float(j.get("deadline") or 0)
    v = {
        "status": j.get("status", ""), "t0": float(j.get("t0") or 0), "t1": float(j.get("t1") or 0),
        "cargo": j.get("cargo", ""), "mass_t": float(j.get("mass") or 0) / 1000.0,
        "src": j.get("src_city", ""), "dst": j.get("dst_city", ""), "src_comp": j.get("src_comp", ""), "dst_comp": j.get("dst_comp", ""),
        "truck": j.get("truck", ""), "plate": j.get("plate", ""), "trailer": j.get("trailer", ""),
        "income": float(res.get("revenue") if res.get("revenue") is not None else j.get("income") or 0),
        "planned_km": float(j.get("planned_km") or 0), "km": float(res.get("distance_km") or acc.get("km") or 0),
        "drove_km": float(acc.get("km") or 0), "real_s": float(acc.get("play_s") or 0), "game_min": max(0.0, g1 - g0),
        "xp": int(res.get("xp") or 0), "cargo_damage": float(res.get("cargo_damage") if res.get("cargo_damage") is not None else acc.get("cargo_dmg_max") or 0),
        "penalty": float(res.get("penalty") or 0), "partial": bool(j.get("partial")),
        "on_time": (g1 <= dl) if (dl > 0 and j.get("status") == "delivered") else None,
        "avg_kmh": d["avg_kmh"], "l100": d["l100"], "fuel_l": float(acc.get("fuel_used") or 0),
        "fines": int(acc.get("fines") or 0), "fine_total": float(acc.get("fine_total") or 0), "tolls": float(acc.get("toll_total") or 0),
        "collisions": int(acc.get("collisions") or 0), "hard_brakes": int(acc.get("hard_brakes") or 0), "style": d["style"],
        "special": bool(j.get("special")), "auto_park": bool(res.get("auto_park")), "auto_load": bool(res.get("auto_load")),
    }
    return v


def summarize(sessions, jobs):
    """Общий свод за всё время + рекорды."""
    accs = [s["acc"] for s in sessions]
    tot = sum_acc(accs)
    done = [job_view(j) for j in jobs if j.get("status") == "delivered"]
    cancelled = [j for j in jobs if j.get("status") == "cancelled"]
    rec = {}
    if done:
        rec["best_income"] = max(done, key=lambda x: x["income"])
        rec["longest_job"] = max(done, key=lambda x: x["km"])
        rec["heaviest"] = max(done, key=lambda x: x["mass_t"])
        clean = [x for x in done if x["cargo_damage"] <= 0.0005 and x["km"] >= 100]
        if clean:
            rec["best_clean"] = max(clean, key=lambda x: x["km"])
        styled = [x for x in done if x["style"] is not None and x["drove_km"] >= 50]
        if styled:
            rec["best_style"] = max(styled, key=lambda x: x["style"])
    if sessions:
        rec["longest_session"] = max(sessions, key=lambda s: float(s["acc"].get("play_s") or 0))
        rec["longest_drive"] = max(sessions, key=lambda s: float(s["acc"].get("km") or 0))
    return {
        "sessions": len(sessions), "acc": tot, "derived": derived(tot),
        "jobs_done": len(done), "jobs_cancelled": len(cancelled), "jobs_total": len(jobs),
        "income": sum(x["income"] for x in done), "xp": sum(x["xp"] for x in done),
        "penalties": sum(float((j.get("result") or {}).get("penalty") or 0) for j in cancelled),
        "on_time": sum(1 for x in done if x["on_time"] is True), "late": sum(1 for x in done if x["on_time"] is False),
        "cities": sorted({c for x in done for c in (x["src"], x["dst"]) if c}),
        "cargos": _top([x["cargo"] for x in done]), "dests": _top([x["dst"] for x in done]), "srcs": _top([x["src"] for x in done]),
        "trucks": _truck_km(sessions), "records": rec,
    }


def _top(items, n=8):
    cnt = {}
    for it in items:
        if it:
            cnt[it] = cnt.get(it, 0) + 1
    return sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _truck_km(sessions):
    km = {}
    for s in sessions:
        for t in s.get("trucks") or []:
            nm = t.get("name") or ""
            if nm:
                km[nm] = km.get(nm, 0.0) + float(t.get("km") or 0)
    return sorted(km.items(), key=lambda kv: -kv[1])


def gear_table(acc, top=8):
    """[(название передачи, секунд)] по убыванию времени; индекс в массиве = передача + 4."""
    out = []
    for i, sec in enumerate(acc.get("gear_s") or []):
        if sec and sec > 0:
            g = i - 4
            out.append(("R" + str(-g) if g < 0 else "N" if g == 0 else "D" + str(g), float(sec)))
    out.sort(key=lambda kv: -kv[1])
    return out[:top]


def fine_events(folder=None, limit=50):
    return load_events(folder, ("fine",), limit)


# ---------------------------------------------------------------------------------------------------- установка dll
def plugins_dir(game_dir):
    return os.path.join(game_dir, "bin", "win_x64", "plugins")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def install_plugin(game_dir, here, say=print, download=True, fetch=None):
    """Кладёт yassno_ets2_stats.dll в plugins игры. Берёт файл из папки программы (here/plugins), иначе скачивает с GitHub.
    Возвращает (статус, текст): ok — уже стоит, installed / updated — записали, locked — игра запущена и держит файл, error."""
    try:
        if not game_dir or not os.path.isfile(os.path.join(game_dir, "bin", "win_x64", "eurotrucks2.exe")):
            return "error", "Не нашёл eurotrucks2.exe в bin\\win_x64 папки игры"
        data = None
        src = os.path.join(here, "plugins", PLUGIN_DLL)
        if os.path.isfile(src):
            with open(src, "rb") as f:
                data = f.read()
        if data is None and download:
            say("Скачиваю плагин статистики…")
            if fetch is None:
                def fetch(url):
                    req = urllib.request.Request(url, headers={"User-Agent": "yassno-ets2-stats"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        return r.read(8_000_000)
            data = fetch(PLUGIN_RAW)
        if not data or data[:2] != b"MZ":
            return "error", "Файл плагина статистики не найден (ни в папке программы, ни на GitHub)"
        d = plugins_dir(game_dir)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, PLUGIN_DLL)
        had = os.path.isfile(dst)
        if had:
            with open(dst, "rb") as f:
                if _sha(f.read()) == _sha(data):
                    return "ok", "Плагин статистики уже установлен"
        tmp = dst + ".new"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dst)
        except PermissionError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return "locked", ("Игра запущена и держит файл плагина — обновлю при следующем запуске лаунчера" if had
                              else "Нет прав записи в папку игры. Запустите лаунчер от имени администратора или скопируйте dll вручную.")
        return ("updated" if had else "installed"), ("Плагин статистики обновлён" if had else "Плагин статистики установлен")
    except Exception as e:                                # noqa: BLE001
        return "error", "Не получилось: " + str(e)


def plugin_state(game_dir, here):
    """Состояние для страницы ETS 2: 'missing' | 'outdated' | 'ok' | 'nogame'."""
    if not game_dir or not os.path.isdir(plugins_dir(game_dir)):
        return "nogame" if not game_dir or not os.path.isdir(game_dir) else "missing"
    dst = os.path.join(plugins_dir(game_dir), PLUGIN_DLL)
    if not os.path.isfile(dst):
        return "missing"
    src = os.path.join(here, "plugins", PLUGIN_DLL)
    try:
        if os.path.isfile(src):
            with open(src, "rb") as a, open(dst, "rb") as b:
                return "ok" if _sha(a.read()) == _sha(b.read()) else "outdated"
    except OSError:
        pass
    return "ok"
