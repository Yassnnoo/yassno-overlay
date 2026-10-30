#!/usr/bin/env python3
"""
Standings Overlay (v5) — таблица результатов поверх Le Mans Ultimate и rFactor 2.

Игра определяется сама. Данные читаются напрямую из разделяемой памяти:
  • Le Mans Ultimate — раздел LMU_Data, ничего ставить не нужно;
  • rFactor 2 — нужен плагин rF2SharedMemoryMapPlugin (rFactor2SharedMemoryMapPlugin64.dll),
    в rF2 колонка «Энергия» (VE) скрыта — её там нет.
Ничего больше устанавливать не нужно — только Python (Pillow по желанию, для логотипов).

Показывает: позицию и сколько отыграно, логотип марки, номер, шины, пилота,
отставание от машины впереди и от лидера (в классе или в общем зачёте), лучший
и последний круг, топливо (л), виртуальную энергию (VE), штрафы и статус пита.
В практике и квалификации таблица сама сортируется по лучшему кругу.

Запуск:     python yassno_overlay.py
Параметры:  --rows 12     сколько строк показывать
            --font 13     размер шрифта
            --alpha 0.94  прозрачность (0.3 ... 1.0)
            --hz 30       как часто читать память игры
            --game auto   auto / lmu / rf2 / iracing — какую игру читать (по умолчанию определяется сама)
            --logos DIR   папка с логотипами марок (png)
            --unlock      запустить в режиме перемещения
            --demo        показать тестовую гонку без игры (вместе с --game rf2 — вид для rFactor 2)
            --diagnose    напечатать, что видно в памяти игры, и выйти

Оверлей по умолчанию ЗАКРЕПЛЁН: мышь проходит сквозь него прямо в игру.
В гараже (машина стоит в боксе) он полностью прозрачный.

Горячие клавиши (работают поверх игры):
    Ctrl+Shift+L  — режим перемещения вкл/выкл (тяните мышью)
    Ctrl+Shift+R  — вернуть все окна на экран (если какое-то оказалось за краем)
    Ctrl+Shift+Q  — выйти из оверлея
    Ctrl+Shift+H  — спрятать / показать весь оверлей
    Ctrl+Shift+T  — следующий вид тайминга (таблица / компактная / бегущая строка)
    Ctrl+Shift+M  — следующий вид карты (полная / линейная / местная)
    Ctrl+Shift+D  — вид дельты (полная / полоска)
    Ctrl+Shift+Y  — вид шин (подробная / компактная / износ)
Нужен режим игры «Окно без рамки» (Borderless) или «В окне».
"""
APP_VERSION = "2.5.29"
import argparse
import bisect
import collections
import ctypes
import gzip
import json
import math
import os
import re
import struct
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "yassno_overlay.json")
CFG_VERSION = 4
CLOCK_GAIN = 0.05          # насколько мягко часы подстраиваются под игру

# =========================================================================
# 1. Разделяемая память LMU (раскладка из community reverse-engineering)
# =========================================================================
MAPPING_NAME, MAPPING_SIZE, MAX_VEHICLES = "LMU_Data", 324_820, 104

HEADER = {"startup_event": 8, "shutdown_event": 12, "session_start": 24,
          "scoring_event": 40, "telemetry_event": 44, "options_location": 96}
SCORING = {"track": 1_632, "session_type": 1_696, "current_et": 1_700, "end_et": 1_708,
           "max_laps": 1_716, "num_vehicles": 1_736, "game_phase": 1_740, "yellow_flag": 1_741,
           "start_light": 1_745, "player_name": 1_748, "rain_intensity": 1_852,
           "air_temp": 1_860, "track_temp": 1_868, "wind": 1_876, "wet_min": 1_900, "wet_max": 1_908, "red_lights": 1_746}
VEHICLE = {"base": 2_192, "stride": 584}
TELEMETRY = {"base": 128_468, "stride": 1_888, "elapsed": 12, "local_vel": 184, "fuel": 524,
             "fuel_capacity": 608, "front_tyre": 620, "rear_tyre": 638,
             "virtual_energy": 776, "model": 796, "class_code": 826}
TELEMETRY_ACTIVE = 128_464
TEL_INPUTS = {"rpm": 356, "max_rpm": 532, "gear": 352, "inputs": 388, "filtered_brake": 428, "sw_range": 692}   # газ, тормоз, руль, сцепление идут подряд

# --- rFactor 2 (плагин rF2SharedMemoryMapPlugin): два отдельных буфера ---
RF2_SCORING_NAME, RF2_TELEMETRY_NAME = "$rFactor2SMMP_Scoring$", "$rFactor2SMMP_Telemetry$"
RF2_SCORING_INFO, RF2_SCORING_VEH = 12, 560          # заголовок 12 байт + rF2ScoringInfo (548 байт)
RF2_SCORING_SIZE = RF2_SCORING_VEH + 128 * 584       # 75 312
RF2_VEH_CANDIDATES = (560, 552, 556, 564, 568, 548, 572, 544)   # на случай другой версии плагина
RF2_TEL_CANDIDATES = (16, 12, 20, 24, 8)
RF2_TEL_COUNT, RF2_TEL_VEH = 12, 16                  # заголовок 12 + число машин (i32), далее записи
RF2_TEL_SIZE = RF2_TEL_VEH + 128 * 1888              # 241 680
SCORING_REL = {k: v - 1632 for k, v in SCORING.items()}   # те же поля шапки, но от начала rF2ScoringInfo
RF2_IN_REALTIME = 115                                # u8: 1 — на трассе, 0 — в мониторе/гараже

CLASS_BY_CODE = {0: "HYPERCAR", 2: "LMP2", 3: "LMP2", 4: "LMP3", 5: "GTE", 6: "LMGT3", 8: "PACE"}
GAME_NAMES = {"lmu": "Le Mans Ultimate", "rf2": "rFactor 2", "iracing": "iRacing"}
SESSION_TYPES = {0: ("TEST", "TEST DAY"), 1: ("PRACTICE", "PRACTICE 1"), 2: ("PRACTICE", "PRACTICE 2"),
                 3: ("PRACTICE", "PRACTICE 3"), 4: ("PRACTICE", "PRACTICE 4"),
                 5: ("QUALIFYING", "QUALIFYING 1"), 6: ("QUALIFYING", "QUALIFYING 2"),
                 7: ("QUALIFYING", "QUALIFYING 3"), 8: ("QUALIFYING", "QUALIFYING 4"),
                 9: ("WARMUP", "WARM UP"), 10: ("RACE", "RACE"), 11: ("RACE", "RACE 2"),
                 12: ("RACE", "RACE 3"), 13: ("RACE", "RACE 4")}
TIMED_KINDS = {"TEST", "PRACTICE", "QUALIFYING", "WARMUP"}
PIT_STATES = {1: "PIT REQ", 2: "PIT IN", 3: "PIT", 4: "OUT LAP"}
FINISH_STATES = {1: "FINISH", 2: "DNF", 3: "DSQ", 4: "DNS", 5: "OUT"}
CLASS_ORDER = ("HYPERCAR", "LMP2", "LMGT3", "LMP3", "GTE", "PACE", "UNKNOWN")


RESULTS_STREAM = (62_928, 65_536)       # журнал гонки: строки «Score / Incident / Penalty …» (только LMU)
_STREAM_TABLE = bytes(10 if (b < 32 or b > 126) and b != 10 else b for b in range(256))


def stream_lines(raw):
    return raw.translate(_STREAM_TABLE).decode("ascii", "replace").lower().splitlines()


def penalty_label(car, lines):
    """DT / SG, если нашли в журнале гонки; иначе PEN (с числом, если штрафов несколько)."""
    n = car["penalties"]
    if n <= 0:
        return ""
    surname = car["driver"].split()[-1].lower() if car["driver"].strip() else ""
    if surname and lines:
        for line in lines:
            if surname not in line:
                continue
            if "drive" in line or " dt" in line:
                return "DT"
            if "stop" in line or " sg" in line:
                return "SG"
    return "PEN" if n == 1 else f"PEN {n}"


class SharedMemoryError(RuntimeError):
    pass


class NotRunning(SharedMemoryError):
    pass


class LayoutMismatch(SharedMemoryError):
    pass


def _text(raw):
    end = raw.find(b"\x00")
    raw = raw[:end] if end >= 0 else raw
    if not raw:
        return ""
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace").strip()


def _f64(buf, off):
    return struct.unpack_from("<d", buf, off)[0]


def _finite(x, lim=1e7):
    return x if (x == x and abs(x) < lim) else 0.0


def _sane(x, lo, hi):
    return x if (x == x and lo <= x < hi) else 0.0


def _f32(buf, off):
    return struct.unpack_from("<f", buf, off)[0]


def class_from_name(value):
    v = value.lower()
    if "hypercar" in v or "lmh" in v or "lmdh" in v:
        return "HYPERCAR"
    if "lmp2" in v:
        return "LMP2"
    if "lmp3" in v:
        return "LMP3"
    if "lmgt3" in v or "gt3" in v:
        return "LMGT3"
    if "gte" in v:
        return "GTE"
    if "pace" in v or "safety" in v:
        return "PACE"
    return "UNKNOWN"


def number_from(*candidates):
    for value in candidates:
        if not value:
            continue
        m = re.search(r"#\s*(\d{1,3})\b", value) or re.search(r"\bno\.?\s*(\d{1,3})\b", value, re.I)
        if m:
            return str(int(m.group(1)))
    return ""


class SharedMemoryMap:
    """Окно только на чтение в именованный раздел памяти (Windows)."""

    def __init__(self, name=MAPPING_NAME, size=MAPPING_SIZE):
        self.name, self.size = name, size
        self._handle = self._addr = None
        self._k32 = None

    @property
    def is_open(self):
        return self._addr is not None

    def open(self):
        if self._addr is not None:
            return
        if not sys.platform.startswith("win"):
            raise NotRunning("разделяемая память LMU есть только в Windows")
        k = self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenFileMappingW.restype = ctypes.c_void_p
        k.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.c_uint32, ctypes.c_size_t]
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k.OpenFileMappingW(0x0004, 0, self.name)
        if not handle:
            err = ctypes.get_last_error()
            if err in (2, 3):
                raise NotRunning("игра не запущена или вы ещё не в сессии")
            if err == 5:
                raise NotRunning("доступ запрещён: запустите оверлей от администратора")
            raise NotRunning(f"OpenFileMappingW: ошибка {err}")
        addr = k.MapViewOfFile(handle, 0x0004, 0, 0, self.size)
        if not addr:                                    # раздел меньше ожидаемого — узнаём его настоящий размер
            addr = k.MapViewOfFile(handle, 0x0004, 0, 0, 0)
            if addr:
                self.size = min(self.size, self._region_size(k, addr) or self.size)
        if not addr:
            k.CloseHandle(handle)
            raise NotRunning(f"MapViewOfFile: ошибка {ctypes.get_last_error()}")
        self._handle, self._addr = handle, addr

    @staticmethod
    def _region_size(k, addr):
        class MBI(ctypes.Structure):
            _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                        ("AllocationProtect", ctypes.c_uint32), ("PartitionId", ctypes.c_uint16),
                        ("RegionSize", ctypes.c_size_t), ("State", ctypes.c_uint32),
                        ("Protect", ctypes.c_uint32), ("Type", ctypes.c_uint32)]
        try:
            k.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
            mbi = MBI()
            return mbi.RegionSize if k.VirtualQuery(addr, ctypes.byref(mbi), ctypes.sizeof(mbi)) else 0
        except Exception:
            return 0

    def close(self):
        if self._addr is not None:
            self._k32.UnmapViewOfFile(self._addr)
            self._addr = None
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None

    def snapshot(self):
        if self._addr is None:
            raise NotRunning("память не открыта")
        return ctypes.string_at(self._addr, self.size)

    def counter(self):
        return struct.unpack_from("<I", ctypes.string_at(self._addr, 64), HEADER["scoring_event"])[0]


class LmuReader:
    def __init__(self):
        self.map = SharedMemoryMap()
        self._last_raw = self._last_frame = None
        self._seq = 0

    def read(self):
        self.map.open()
        for _ in range(6):
            before = self.map.counter()
            raw = self.map.snapshot()
            if struct.unpack_from("<I", raw, HEADER["scoring_event"])[0] != before:
                continue            # игра писала во время копирования — повторим
            return self._frame(raw)
        raise LayoutMismatch("данные постоянно менялись во время чтения")

    def _frame(self, raw):
        # Счётчики в заголовке игры нам не доверяем: переиспользуем кадр только если
        # ВСЕ байты памяти совпали с прошлым чтением, иначе разбираем заново.
        if self._last_raw is not None and raw == self._last_raw:
            return self._last_frame
        frame = parse_session(raw)
        self._seq += 1
        frame["_seq"] = self._seq
        self._last_raw, self._last_frame = raw, frame
        return frame

    def close(self):
        self.map.close()
        self._last_raw = self._last_frame = None


def _text_ok(t):
    return bool(t) and sum(ch.isprintable() for ch in t) / len(t) >= 0.95


def _plausible_vehicle(sc, b):
    """Похоже ли то, что лежит по смещению b, на запись машины (имя, класс, позиция, круги)."""
    if b + 584 > len(sc):
        return False
    drv, veh = _text(sc[b + 4:b + 36]), _text(sc[b + 36:b + 100])
    place, laps = sc[b + 199], struct.unpack_from("<h", sc, b + 100)[0]
    return _text_ok(drv) and (not veh or _text_ok(veh)) and 1 <= place <= 128 and 0 <= laps < 10000


def detect_rf2_vehicle_base(sc, count, player_name):
    """Находит, с какого байта начинаются машины в scoring-буфере (зависит от версии плагина)."""
    best = None
    for base in RF2_VEH_CANDIDATES:
        if not all(_plausible_vehicle(sc, base + i * 584) for i in range(min(count, 3))):
            continue
        if not player_name or any(_text(sc[base + i * 584 + 4:base + i * 584 + 36]) == player_name
                                  for i in range(count) if base + (i + 1) * 584 <= len(sc)):
            return base                      # и запись правдоподобна, и имя игрока нашлось
        best = best or base
    return best


def detect_rf2_telemetry_base(tl, tcount, ids):
    best, best_hits = RF2_TEL_VEH, 0
    for base in RF2_TEL_CANDIDATES:
        hits = sum(struct.unpack_from("<i", tl, base + i * 1888)[0] in ids
                   for i in range(min(tcount, 8)) if base + (i + 1) * 1888 <= len(tl))
        if hits > best_hits:
            best, best_hits = base, hits
    return best


def parse_rf2(sc, tl, cache=None):
    """Кадр из двух буферов плагина rF2SharedMemoryMapPlugin (sc — scoring, tl — telemetry или None)."""
    cache = cache if cache is not None else {}
    if len(sc) < RF2_SCORING_INFO + 548:
        raise LayoutMismatch(f"scoring-буфер rF2 слишком короткий: {len(sc)} байт")
    si = RF2_SCORING_INFO
    rel = SCORING_REL
    session_type = struct.unpack_from("<i", sc, si + rel["session_type"])[0]
    elapsed = _f64(sc, si + rel["current_et"])
    count = struct.unpack_from("<i", sc, si + rel["num_vehicles"])[0]
    track = _text(sc[si:si + 64])
    if not (0 <= count <= 128):
        raise LayoutMismatch(f"число машин {count} вне диапазона")
    if not (0.0 <= elapsed < 1_000_000.0):
        raise LayoutMismatch(f"время сессии {elapsed!r} вне диапазона")
    player_name = _text(sc[si + rel["player_name"]:si + rel["player_name"] + 32])
    vb = cache.get("veh")
    if count and (vb is None or not _plausible_vehicle(sc, vb)):
        vb = detect_rf2_vehicle_base(sc, count, player_name)
        if vb is None:
            raise LayoutMismatch("не нашла записи машин в scoring-буфере rF2 (другая версия плагина?) — "
                                 "запустите diagnose.cmd и пришлите вывод")
        cache["veh"] = vb
    vb = vb or RF2_SCORING_VEH
    if len(sc) < vb + count * 584:
        raise LayoutMismatch(f"scoring-буфер rF2 короче, чем нужно для {count} машин")
    tel, tb = {}, None
    if tl is not None and len(tl) >= RF2_TEL_VEH + 1888:
        tcount = max(0, min(struct.unpack_from("<i", tl, RF2_TEL_COUNT)[0], 128))
        ids = {struct.unpack_from("<i", sc, vb + i * 584)[0] for i in range(count)}
        tb = cache.get("tel")
        if tb is None and ids and tcount:
            tb = cache["tel"] = detect_rf2_telemetry_base(tl, tcount, ids)
        tb = tb or RF2_TEL_VEH
        tel = _parse_telemetry(tl, tb, min(tcount, (len(tl) - tb) // 1888))
    cars = [_parse_vehicle(sc, vb + i * 584, tel, raw_class=True) for i in range(count)]
    if count and not any(c["driver"] for c in cars):
        raise LayoutMismatch("имена пилотов пустые — раскладка не совпала с версией плагина?")
    kind, label = SESSION_TYPES.get(session_type, ("RACE", "RACE"))
    player = next((c for c in cars if c["player"]), None)
    for c in cars:
        c["penalty_label"] = penalty_label(c, None)   # в rF2 тип штрафа плагин не отдаёт — только число
    if player and tl is not None and player["key"] in tel and player.get("wheels") and not any(w["temp"] for w in player["wheels"]):
        _wheel_raw_dump(tl, tel[player["key"]]["_off"] + _WHEEL_STATE["base"], f"игрок id={player['key']} control={player.get('control')} laps={player['laps']}")
    inputs = read_inputs(tl, tel[player["key"]]["_off"]) if (player and tl is not None and player["key"] in tel) else None
    ptel = tel.get(player["key"]) if player else None
    return {
        "inputs": inputs, "player_pos": (ptel["pos"] if ptel and ptel["pos"] != (0.0, 0.0) else player["pos"] if player else None),
        "player_lap": (ptel["lap"] if ptel and ptel["lap"] >= 0 else player["laps"]) if player else None,
        "track": track, "session_type": session_type, "kind": kind, "label": label,
        "elapsed": elapsed, "end_et": _f64(sc, si + rel["end_et"]),
        "max_laps": struct.unpack_from("<i", sc, si + rel["max_laps"])[0],
        "game_phase": sc[si + rel["game_phase"]], "yellow": struct.unpack_from("<b", sc, si + rel["yellow_flag"])[0],
        "start_light": sc[si + rel["start_light"]], "red_lights": sc[si + rel["red_lights"]],
        "wet": (_sane(_f64(sc, si + rel["wet_min"]), 0.0, 1.0) + _sane(_f64(sc, si + rel["wet_max"]), 0.0, 1.0)) / 2,
        "player_name": player_name, "tel_count": len(tel),
        "wheel_dbg": (tel.get(player["key"]) or {}).get("_wdbg") if player else None,
        "layout": f"scoring: машины с {vb}, телеметрия с {tb}",
        "wind": (_finite(_f64(sc, si + rel["wind"])), _finite(_f64(sc, si + rel["wind"] + 16))),
        "air": _f64(sc, si + rel["air_temp"]), "track_temp": _f64(sc, si + rel["track_temp"]),
        "rain": _f64(sc, si + rel["rain_intensity"]), "track_len": _sane(_f64(sc, si + 88), 0.0, 1e6),
        "options_location": 3, "game": "rf2", "in_realtime": sc[si + RF2_IN_REALTIME] != 0,
        "session_start": 0,                                  # уточняется читателем (смена сессии)
        "scoring_event": struct.unpack_from("<I", sc, 0)[0],
        "player_elapsed": (tel.get(player["key"], {}).get("elapsed") if player else None),
        "cars": cars,
    }


class Rf2Reader:
    """Читает два буфера плагина rF2SharedMemoryMapPlugin."""

    def __init__(self):
        self.sc = SharedMemoryMap(RF2_SCORING_NAME, RF2_SCORING_SIZE)
        self.tl = SharedMemoryMap(RF2_TELEMETRY_NAME, RF2_TEL_SIZE)
        self._last_raw = self._last_frame = None
        self._seq = self._sess = 0
        self._sess_key = None
        self._last_et = 0.0
        self._cache = {}

    def _consistent(self, m):
        """Копия буфера, в которой версии begin/end совпали (кадр не «порван» записью игры)."""
        for _ in range(6):
            raw = m.snapshot()
            if struct.unpack_from("<II", raw, 0)[0] == struct.unpack_from("<II", raw, 0)[1]:
                return raw
        return raw

    def read(self):
        try:
            self.sc.open()
        except NotRunning as e:
            raise NotRunning(f"плагин разделяемой памяти не найден ({e})")
        try:
            self.tl.open()
        except NotRunning:
            pass                                  # без телеметрии таблица работает, не будет топлива/шин
        sc = self._consistent(self.sc)
        tl = self._consistent(self.tl) if self.tl.is_open else None
        if self._last_raw is not None and (sc, tl) == self._last_raw:
            return self._last_frame
        frame = parse_rf2(sc, tl, self._cache)
        key = (frame["track"], frame["session_type"])
        if key != self._sess_key or frame["elapsed"] < self._last_et - 5.0:
            self._sess_key, self._sess = key, self._sess + 1
        self._last_et = frame["elapsed"]
        frame["session_start"] = self._sess
        self._seq += 1
        frame["_seq"] = self._seq
        self._last_raw, self._last_frame = (sc, tl), frame
        return frame

    def close(self):
        self.sc.close()
        self.tl.close()
        self._last_raw = self._last_frame = None
        self._cache = {}


# ---------------------------------------------------------------------------
# iRacing: разделяемая память irsdk (Local\IRSDKMemMapFileName)
# ---------------------------------------------------------------------------
IR_MAP_NAME, IR_MAP_SIZE = "Local\\IRSDKMemMapFileName", 1164 * 1024
IR_TYPES = {0: ("c", 1), 1: ("?", 1), 2: ("i", 4), 3: ("I", 4), 4: ("f", 4), 5: ("d", 8)}


def _ir_val(buf, vars_, base, name, idx=0, default=0.0):
    v = vars_.get(name)
    if not v or idx >= v[2]:
        return default
    code, size = IR_TYPES.get(v[0], ("i", 4))
    try:
        return struct.unpack_from("<" + code, buf, base + v[1] + idx * size)[0]
    except struct.error:
        return default


def _ir_arr(buf, vars_, base, name):
    v = vars_.get(name)
    if not v:
        return []
    code, size = IR_TYPES.get(v[0], ("i", 4))
    try:
        return list(struct.unpack_from("<%d%s" % (v[2], code), buf, base + v[1]))
    except struct.error:
        return []


def _yget(text, key, default=""):
    m = re.search(r"^[ \t-]*%s:[ \t]*(.*?)[ \t]*$" % re.escape(key), text, re.M)
    return m.group(1).strip().strip("'\"") if m else default


def _yfloat(text, key, default=0.0):
    m = re.search(r"-?\d+(?:\.\d+)?", _yget(text, key, ""))
    return float(m.group(0)) if m else default


def _yblock(text, key):
    """Текст вложенного блока 'key:' до следующего ключа того же отступа."""
    m = re.search(r"^( *)%s:[ \t]*$" % re.escape(key), text, re.M)
    if not m:
        return ""
    ind = len(m.group(1))
    out = []
    for line in text[m.end():].split("\n")[1:]:
        if line.strip() and (len(line) - len(line.lstrip(" "))) <= ind and not line.lstrip().startswith("-"):
            break
        out.append(line)
    return "\n".join(out)


def parse_ir_session(text):
    """Нужное из yaml-описания сессии iRacing без внешних библиотек."""
    wk = _yblock(text, "WeekendInfo")
    di = _yblock(text, "DriverInfo")
    drivers = {}
    for chunk in re.split(r"\n[ \t]*- CarIdx:", "\n" + _yblock(di, "Drivers"))[1:]:
        head, _, rest = chunk.partition("\n")
        try:
            idx = int(head.strip())
        except ValueError:
            continue
        drivers[idx] = {"name": _yget(rest, "UserName"), "number": _yget(rest, "CarNumber"),
                        "cls": _yget(rest, "CarClassShortName"), "clsid": _yget(rest, "CarClassID"),
                        "car": _yget(rest, "CarScreenName"), "short": _yget(rest, "CarScreenNameShort"),
                        "irating": int(_yfloat(rest, "IRating", 0.0)), "lic": _yget(rest, "LicString"),
                        "lic_col": _yget(rest, "LicColor"),
                        "spec": _yget(rest, "IsSpectator") == "1", "pace": _yget(rest, "CarIsPaceCar") == "1"}
    sessions = []
    si = _yblock(text, "SessionInfo")
    for chunk in re.split(r"\n[ \t]*- SessionNum:", "\n" + _yblock(si, "Sessions"))[1:]:
        head, _, rest = chunk.partition("\n")
        try:
            num = int(head.strip())
        except ValueError:
            continue
        laps = _yget(rest, "SessionLaps")
        sessions.append({"num": num, "type": _yget(rest, "SessionType"), "name": _yget(rest, "SessionName"),
                         "laps": int(laps) if laps.isdigit() else 0})
    sectors = [float(x) for x in re.findall(r"SectorStartPct:[ \t]*([\d.]+)", text)]
    tl = _yfloat(wk, "TrackLength", 0.0)
    tid = _yget(wk, "TrackID")
    return {"track": _yget(wk, "TrackDisplayName") or _yget(wk, "TrackName"),
            "track_id": int(tid) if tid.isdigit() else 0, "track_slug": _yget(wk, "TrackName"),
            "track_len": tl * (1609.344 if "mi" in _yget(wk, "TrackLength").split()[-1:] else 1000.0),
            "air": _yfloat(wk, "TrackAirTemp"), "track_temp": _yfloat(wk, "TrackSurfaceTemp"),
            "me": int(_yfloat(di, "DriverCarIdx", -1)), "redline": _yfloat(di, "DriverCarRedLine", 8000.0),
            "drivers": drivers, "sessions": sessions, "sectors": sectors or [0.0, 0.33, 0.66]}


class IracingReader:
    """Читает кадры iRacing и приводит их к тому же виду, что у LMU и rFactor 2."""

    def __init__(self, source=None):
        self.map = SharedMemoryMap(IR_MAP_NAME, IR_MAP_SIZE)
        self._src = source                              # для проверки: функция, возвращающая байты памяти
        self.reset()

    def reset(self):
        self._vars, self._nvars, self._info, self._info_tick = {}, -1, None, -2
        self._seq = self._sess = 0
        self._sess_key, self._last_et = None, 0.0
        self._line, self._origin = {}, None
        self._fin = {}                                 # машина -> финиш/DSQ, раз получила — остаётся до конца сессии
        self._seen = {}                                # машина -> (доля круга, кругов) в последний раз, когда была в мире игры
        self._last_tick = None
        self._last_frame = None
        self._cs = {}                                  # по машинам: сектора, пит-стопы, скорость

    def _track_car(self, idx, p, t, surf, flags, sec, L):
        """Сектора, пит-стопы и скорость соперников считаем сами по положению на круге: игра их не отдаёт."""
        st = self._cs.get(idx)
        if st is None or t < st["t"] - 1.0:
            st = self._cs[idx] = {"p": p, "t": t, "t0": None, "c": {}, "s1": 0.0, "s2": 0.0, "stops": 0, "stall": False, "v": 0.0}
            return st
        dt = t - st["t"]
        if dt <= 0.0:
            return st
        pp = st["p"]
        d = p - pp
        wrapped = pp > 0.8 and p < 0.2
        if wrapped:
            d += 1.0
        if abs(d) > 0.25 or d < -0.02:                         # телепорт, возврат в боксы, сброс — серия сбивается
            st.update(p=p, t=t, t0=None, c={})
            return st
        if d > 0:
            st["v"] = 0.7 * st["v"] + 0.3 * (d * L / dt)
        marks = [(x, x) for x in sec[1:]]
        for k, x in enumerate(sec[1:3]):
            if (pp < x <= p) and not wrapped:
                st["c"][k] = t - dt * (p - x) / (p - pp)
        if wrapped:
            tc = t - dt * p / (d if d else 1.0)
            if st["t0"] is not None and 0 in st["c"] and 1 in st["c"]:
                a, b = st["c"][0] - st["t0"], st["c"][1] - st["t0"]
                if 0.0 < a < b < 3600.0:
                    st["s1"], st["s2"] = a, b
            st["t0"], st["c"] = tc, {}
        st["p"], st["t"] = p, t
        if surf == 1 and not st["stall"]:
            st["stops"] += 1
        st["stall"] = surf == 1
        return st

    def read(self):
        if self._src:
            raw = self._src()
        else:
            try:
                self.map.open()
            except NotRunning as e:
                raise NotRunning(f"iRacing не запущена или вы не в сессии ({e})")
            raw = self.map.snapshot()
        return self.parse(raw)

    def close(self):
        self.map.close()
        self.reset()

    def parse(self, raw):
        if len(raw) < 112:
            raise LayoutMismatch("буфер iRacing слишком короткий")
        ver, status, tick_rate, info_upd, info_len, info_off, nvars, var_off, nbuf, buf_len = struct.unpack_from("<10i", raw, 0)
        if not status & 1:
            raise NotRunning("iRacing: нет подключения к сессии")
        if not (1 <= nbuf <= 4) or buf_len <= 0 or nvars <= 0 or nvars > 4000:
            raise LayoutMismatch("заголовок iRacing не похож на irsdk")
        bufs = [struct.unpack_from("<ii", raw, 48 + 16 * i) for i in range(nbuf)]
        tick, base = max(bufs)
        if base <= 0 or base + buf_len > len(raw):
            raise LayoutMismatch("смещение буфера iRacing вне памяти")
        if tick == self._last_tick and self._last_frame is not None:
            return self._last_frame
        if nvars != self._nvars:                                  # таблица переменных
            self._vars = {}
            for i in range(nvars):
                o = var_off + i * 144
                t, off, cnt = struct.unpack_from("<iii", raw, o)
                name = raw[o + 16:o + 48].split(b"\0")[0].decode("ascii", "replace")
                self._vars[name] = (t, off, cnt)
            self._nvars = nvars
        if info_upd != self._info_tick or self._info is None:     # описание сессии (yaml)
            txt = raw[info_off:info_off + info_len].split(b"\0")[0].decode("cp1252", "replace")
            self._info = parse_ir_session(txt)
            self._info_tick = info_upd
        frame = self._frame(raw, base)
        self._seq += 1
        frame["_seq"] = self._seq
        self._last_tick, self._last_frame = tick, frame
        return frame

    # ---- кадр ----
    SESS_KIND = {"practice": (1, "PRACTICE", "PRACTICE"), "open qualify": (5, "QUALIFYING", "QUALIFYING"),
                 "lone qualify": (5, "QUALIFYING", "QUALIFYING"), "qualify": (5, "QUALIFYING", "QUALIFYING"),
                 "warmup": (9, "WARMUP", "WARM UP"), "race": (10, "RACE", "RACE"),
                 "offline testing": (0, "TEST", "TEST DAY"), "test": (0, "TEST", "TEST DAY")}

    def _frame(self, raw, base):
        V = self._vars
        f = lambda n, i=0, d=0.0: _ir_val(raw, V, base, n, i, d)
        info = self._info
        L = info["track_len"] or 0.0
        pidx = int(f("PlayerCarIdx", 0, info["me"]))
        et = float(f("SessionTime"))
        snum = int(f("SessionNum"))
        sess = next((s for s in info["sessions"] if s["num"] == snum), None) or {"type": "", "name": "", "laps": 0}
        stype, kind, label = self.SESS_KIND.get(sess["type"].lower(), (10, "RACE", sess["type"].upper() or "RACE"))
        key = (info["track"], snum)
        if key != self._sess_key or et < self._last_et - 5.0:
            self._sess_key, self._sess = key, self._sess + 1
            self._seen, self._fin = {}, {}
        self._last_et = et
        remain = float(f("SessionTimeRemain", 0, -1.0))
        end_et = et + remain if 0 <= remain < 400000 else 0.0
        state_ = int(f("SessionState"))
        flags = int(f("SessionFlags"))
        phase = {1: 2, 2: 3, 3: 3, 4: 5, 5: 8, 6: 8}.get(state_, 0)
        if flags & 0x00200000 or flags & 0x40000000:      # зелёный старт
            phase = 5
        start_light = 5 if flags & 0x20000000 else 2 if flags & 0x10000000 else 0
        if start_light:
            phase = 4
        yellow = 1 if flags & (0x4000 | 0x8000) else 0
        pct = _ir_arr(raw, V, base, "CarIdxLapDistPct")
        lapc = _ir_arr(raw, V, base, "CarIdxLapCompleted") or _ir_arr(raw, V, base, "CarIdxLap")
        surf = _ir_arr(raw, V, base, "CarIdxTrackSurface")
        onpit = _ir_arr(raw, V, base, "CarIdxOnPitRoad")
        posn = _ir_arr(raw, V, base, "CarIdxPosition")
        best = _ir_arr(raw, V, base, "CarIdxBestLapTime")
        last = _ir_arr(raw, V, base, "CarIdxLastLapTime")
        px, pz = self._player_xz(f)
        pct_me = pct[pidx] if 0 <= pidx < len(pct) else 0.0
        in_world = surf[pidx] != -1 if 0 <= pidx < len(surf) else True
        if in_world and px is not None and not f("IsInGarage") and pct_me >= 0:
            self._line[int(max(0.0, min(0.9999, pct_me)) * 2000)] = (px, pz)
        sec = info["sectors"]
        cflags = _ir_arr(raw, V, base, "CarIdxSessionFlags")
        f2 = _ir_arr(raw, V, base, "CarIdxF2Time")                # игра сама считает отставание от лидера в секундах
        clpos = _ir_arr(raw, V, base, "CarIdxClassPosition")
        player_speed = float(f("Speed"))
        cars = []
        ranked = []
        for idx, d in sorted(info["drivers"].items()):
            if d["spec"] or d["pace"] or idx >= len(pct):
                continue
            me_ = idx == pidx
            gone = False
            if surf and idx < len(surf) and surf[idx] == -1 and not me_:
                if stype == 10 and idx in self._seen:              # в гонке выбывшего не прячем, а помечаем OUT
                    gone = True
                else:
                    continue
            p = pct[idx] if pct[idx] >= 0 else 0.0
            lp = lapc[idx] if idx < len(lapc) else 0
            lp = max(int(lp), 0)
            if gone:
                p, lp = self._seen[idx]
            elif surf and idx < len(surf) and surf[idx] != -1 and p > 0:
                self._seen[idx] = (p, lp)
            ranked.append((lp + p, idx))
            cars.append({"idx": idx, "me": me_, "pct": p, "laps": lp, "d": d, "gone": gone})
        ranked.sort(reverse=True)
        total = {idx: t for t, idx in ranked}
        order = [idx for _, idx in ranked]
        out = []
        for c in cars:
            idx, d, p = c["idx"], c["d"], c["pct"]
            b = best[idx] if idx < len(best) and 0 < best[idx] < 3600 else 0.0
            ls = last[idx] if idx < len(last) and 0 < last[idx] < 3600 else 0.0
            lapt = b or ls or (L / 40.0 if L else 90.0)
            rank = order.index(idx)
            ahead = total[order[rank - 1]] - total[idx] if rank > 0 else 0.0
            lead = total[order[0]] - total[idx]
            gn, gl = (ahead % 1.0) * lapt if ahead < 1.0 else 0.0, (lead % 1.0) * lapt if lead < 1.0 else 0.0
            if f2 and idx < len(f2) and lead < 1.0:
                gl = max(0.0, float(f2[idx]))
                prev = order[rank - 1] if rank > 0 else None
                if prev is not None and ahead < 1.0 and prev < len(f2):
                    gn = max(0.0, gl - float(f2[prev]))
            place = int(posn[idx]) if idx < len(posn) and posn[idx] > 0 else rank + 1
            s = 1 + sum(1 for x in sec[1:] if p >= x)
            cs = self._track_car(idx, p, et, surf[idx] if idx < len(surf) else 3, cflags[idx] if idx < len(cflags) else 0, sec, L)
            fl = int(cflags[idx]) if idx < len(cflags) else 0
            fl_ = fl
            ox, oz = self._line.get(int(p * 2000), (0.0, 0.0)) if not c["me"] else (px or 0.0, pz or 0.0)
            ori = self._ori(p, c["me"], f)
            fin = 3 if fl_ & 0x20000 else 1 if fl_ & 0x0001 else 0                        # DSQ / клетчатый флаг
            if fin:
                self._fin[idx] = fin
            else:
                fin = self._fin.get(idx, 5 if c["gone"] else 0)                           # выбыл (пропал из мира)
            car = {"key": idx, "driver": d["name"].upper(), "vehicle": f"{d['car']} #{d['number']}", "model": d["short"] or d["car"],
                   "number": d["number"], "cls": (d["cls"] or "CLASS").upper()[:18], "laps": c["laps"], "finish": fin,
                   "irating": d.get("irating", 0), "lic": d.get("lic", ""), "lic_col": d.get("lic_col", ""),
                   "best": b, "last": ls, "last_s1": cs["s1"], "last_s2": cs["s2"], "tyre_wear": None, "wheels": None,
                   "place": place, "sector": s % 3, "player": c["me"],
                   "in_pits": bool(onpit[idx]) if idx < len(onpit) else False,
                   "garage": bool(f("IsInGarage")) or (surf[idx] == -1 if idx < len(surf) else False) if c["me"] else False,
                   "pit_state": 3 if (idx < len(onpit) and onpit[idx]) else 0, "control": 0 if c["me"] else 2, "flag": 0,
                   "pitstops": cs["stops"], "penalties": 1 if fl & (0x10000 | 0x80000) else 0,
                   "gap_next": gn, "laps_next": int(ahead) if ahead >= 1.0 else 0,
                   "gap_leader": gl, "laps_leader": int(lead) if lead >= 1.0 else 0,
                   "pos": (ox, oz), "ori": ori, "lap_dist": p * L, "est_lap": lapt, "has_tel": c["me"],
                   "front": "", "rear": "", "fuel": 0.0, "fuel_cap": 0.0, "ve": 0.0, "fuel_fraction": 0,
                   "speed": player_speed if c["me"] else max(0.0, cs["v"])}
            if c["me"]:
                fuel = float(f("FuelLevel"))
                fpct = float(f("FuelLevelPct"))
                car["fuel"] = fuel if 0.0 <= fuel <= 500.0 else 0.0
                car["fuel_cap"] = fuel / fpct if fpct > 0.01 else 0.0
                car["wheels"], car["tyre_wear"] = self._wheels(f)
            out.append(car)
        if not out:
            raise LayoutMismatch("в сессии iRacing нет машин")
        player = next((c for c in out if c["player"]), None)
        steer_max = float(f("SteeringWheelAngleMax", 0, 0.0))
        inputs = None
        if player:
            ang = float(f("SteeringWheelAngle"))
            inputs = {"water": float(f("WaterTemp")) or None, "oil": float(f("OilTemp")) or None,
                      "limiter": bool(int(f("EngineWarnings")) & 0x10), "rpm": float(f("RPM")),
                      "max_rpm": float(info["redline"] or 8000.0), "gear": int(f("Gear")),
                      "thr": max(0.0, min(1.0, float(f("Throttle")))), "brk": max(0.0, min(1.0, float(f("Brake")))),
                      "clu": max(0.0, min(1.0, 1.0 - float(f("Clutch", 0, 1.0)))),
                      "steer": max(-1.0, min(1.0, -ang / steer_max)) if steer_max > 0.01 else 0.0,
                      "range": math.degrees(steer_max * 2) if steer_max > 0.01 else 0.0,
                      "abs": bool(f("BrakeABSactive"))}
        wv = float(f("WindVel")) if "WindVel" in V else 0.0
        wd = float(f("WindDir")) if "WindDir" in V else 0.0
        wet = max(0.0, (int(f("TrackWetness", 0, 1)) - 1) / 6.0) if "TrackWetness" in V else 0.0
        return {"inputs": inputs, "player_pos": player["pos"] if player else None, "player_lap": int(f("Lap")) if player else None,
                "track": info["track"], "session_type": stype, "kind": kind, "label": label,
                "elapsed": et, "end_et": end_et, "max_laps": sess["laps"] if 0 < sess["laps"] < 32000 else 0,
                "game_phase": phase, "yellow": yellow, "start_light": start_light, "red_lights": 5,
                "wet": wet, "player_name": player["driver"] if player else "", "tel_count": 1 if inputs else 0,
                "wheel_dbg": None, "layout": "iRacing irsdk",
                "wind": (wv * math.sin(wd), wv * math.cos(wd)),
                "air": float(f("AirTemp", 0, info["air"])), "track_temp": float(f("TrackTempCrew", 0, info["track_temp"])),
                "rain": float(f("Precipitation")) if "Precipitation" in V else 0.0, "track_len": L,
                "options_location": 3, "game": "iracing", "in_realtime": bool(f("IsOnTrack")),
                "track_id": info.get("track_id", 0), "track_slug": info.get("track_slug", ""),
                "session_start": self._sess, "scoring_event": int(self._last_tick or 0),
                "player_elapsed": et if player else None, "cars": out}

    def _player_xz(self, f):
        lat, lon = float(f("Lat")), float(f("Lon"))
        if not (lat or lon):
            return None, None
        if self._origin is None:
            self._origin = (lat, lon)
        la0, lo0 = self._origin
        return 5000.0 + (lon - lo0) * 111320.0 * math.cos(math.radians(la0)), 5000.0 + (lat - la0) * 110574.0

    def _ori(self, p, is_me, f):
        i = int(p * 2000)
        a, b = self._line.get(i), self._line.get((i + 6) % 2000)
        if not a or not b:
            return (1.0, 0.0, 0.0, 1.0)
        n = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        fx, fz = (b[0] - a[0]) / n, (b[1] - a[1]) / n
        return (fz, -fx, fx, fz)

    def _wheels(self, f):
        wh, wear = [], []
        for t in ("LF", "RF", "LR", "RR"):
            temps = [float(f(f"{t}temp{s}")) for s in ("L", "M", "R")]
            if not any(temps):
                temps = [float(f(f"{t}tempC{s}")) for s in ("L", "M", "R")]
            w = [float(f(f"{t}wear{s}", 0, -1.0)) for s in ("L", "M", "R")]
            w = [x for x in w if x >= 0.0]
            wr = sum(w) / len(w) if w else 1.0
            wh.append({"temp": sum(temps) / 3.0, "press": float(f(f"{t}pressure")), "wear": wr, "brake": 0.0})
            wear.append(wr)
        return wh, tuple(wear)


class AutoReader:
    """Сам находит запущенную игру: Le Mans Ultimate или rFactor 2."""

    def __init__(self, game="auto"):
        self.readers = {"lmu": LmuReader(), "rf2": Rf2Reader(), "iracing": IracingReader()}
        self.order = {"lmu": ["lmu"], "rf2": ["rf2"], "iracing": ["iracing"]}.get(game, ["lmu", "rf2", "iracing"])
        self.active = None

    def read(self):
        if self.active:
            try:
                return self.readers[self.active].read()
            except Exception:
                self.readers[self.active].close()
                self.active = None
                raise
        errors = []
        for name in self.order:
            try:
                frame = self.readers[name].read()
                self.active = name
                return frame
            except Exception as e:
                self.readers[name].close()
                errors.append(f"{ {'lmu': 'LMU', 'rf2': 'rF2', 'iracing': 'iRacing'}[name] }: {e}")
        raise NotRunning("игра не найдена — " + " | ".join(errors))

    def close(self):
        for r in self.readers.values():
            r.close()


def parse_session(buf):
    if len(buf) < MAPPING_SIZE:
        raise LayoutMismatch(f"размер памяти {len(buf)}, ожидалось {MAPPING_SIZE}")
    session_type = struct.unpack_from("<i", buf, SCORING["session_type"])[0]
    elapsed = _f64(buf, SCORING["current_et"])
    count = struct.unpack_from("<i", buf, SCORING["num_vehicles"])[0]
    track = _text(buf[SCORING["track"]:SCORING["track"] + 64])
    if not (0 <= count <= MAX_VEHICLES):
        raise LayoutMismatch(f"число машин {count} вне диапазона")
    if not (0.0 <= elapsed < 1_000_000.0):
        raise LayoutMismatch(f"время сессии {elapsed!r} вне диапазона")
    if track and sum(ch.isprintable() for ch in track) / len(track) < 0.85:
        raise LayoutMismatch("название трассы не похоже на текст")

    tel = _parse_telemetry(buf, TELEMETRY["base"], min(buf[TELEMETRY_ACTIVE], MAX_VEHICLES))
    cars = [_parse_vehicle(buf, VEHICLE["base"] + i * VEHICLE["stride"], tel) for i in range(count)]
    if count and not any(c["driver"] for c in cars):
        raise LayoutMismatch("имена пилотов пустые — раскладка не совпала с версией игры?")
    kind, label = SESSION_TYPES.get(session_type, ("RACE", "RACE"))
    player = next((c for c in cars if c["player"]), None)
    lines = None
    for c in cars:
        if c["penalties"] > 0:                      # журнал разбираем только если у кого-то есть штраф
            if lines is None:
                lines = stream_lines(buf[RESULTS_STREAM[0]:RESULTS_STREAM[0] + RESULTS_STREAM[1]])
            c["penalty_label"] = penalty_label(c, lines)
    inputs = read_inputs(buf, tel[player["key"]]["_off"]) if player and player["key"] in tel else None
    ptel = tel.get(player["key"]) if player else None
    return {
        "inputs": inputs, "player_pos": (ptel["pos"] if ptel and ptel["pos"] != (0.0, 0.0) else player["pos"] if player else None),
        "player_lap": (ptel["lap"] if ptel and ptel["lap"] >= 0 else player["laps"]) if player else None,
        "track": track, "session_type": session_type, "kind": kind, "label": label,
        "elapsed": elapsed, "end_et": _f64(buf, SCORING["end_et"]),
        "max_laps": struct.unpack_from("<i", buf, SCORING["max_laps"])[0],
        "game_phase": buf[SCORING["game_phase"]], "yellow": struct.unpack_from("<b", buf, SCORING["yellow_flag"])[0],
        "start_light": buf[SCORING["start_light"]], "red_lights": buf[SCORING["red_lights"]],
        "wet": (_sane(_f64(buf, SCORING["wet_min"]), 0.0, 1.0) + _sane(_f64(buf, SCORING["wet_max"]), 0.0, 1.0)) / 2,
        "player_name": _text(buf[SCORING["player_name"]:SCORING["player_name"] + 32]),
        "wind": (_finite(_f64(buf, SCORING["wind"])), _finite(_f64(buf, SCORING["wind"] + 16))),
        "air": _f64(buf, SCORING["air_temp"]), "track_temp": _f64(buf, SCORING["track_temp"]),
        "rain": _f64(buf, SCORING["rain_intensity"]), "track_len": _sane(_f64(buf, 1632 + 88), 0.0, 1e6),
        "options_location": buf[HEADER["options_location"]], "game": "lmu", "in_realtime": None,
        "session_start": struct.unpack_from("<I", buf, HEADER["session_start"])[0],
        "scoring_event": struct.unpack_from("<I", buf, HEADER["scoring_event"])[0],
        "player_elapsed": (tel.get(player["key"], {}).get("elapsed") if player else None),
        "cars": cars,
    }


WHEELS = {"base": 848, "size": 260, "pressure": 120, "temp": 128, "wear": 152}     # rF2Wheel mWheels[4] в записи машины


_WHEEL_STATE = {"base": WHEELS["base"], "retry": 0.0}


def _alt_temps(buf, b):
    """Температуры шины из других полей rF2Wheel (поверхность, каркас, внутренний слой), если основные не подошли."""
    try:
        for off, n in ((204, 1), (212, 3), (128, 3)):
            v = [_f64(buf, b + off + k * 8) for k in range(n)]
            if all(v[k] == v[k] and 200.0 < v[k] < 700.0 for k in range(n)):
                return [x - 273.15 for x in v] * (3 // n)
            if all(v[k] == v[k] and 5.0 < v[k] < 250.0 for k in range(n)):
                return list(v) * (3 // n)
    except (struct.error, IndexError):
        pass
    return None


def _wheel_raw_dump(buf, b, tag=""):
    """Сырые значения колеса (32 числа типа double) в wheel-debug.txt — для разбора раскладки; не чаще раза в 30 с, до 10 раз."""
    st = _WHEEL_STATE
    if st.get("raw_n", 0) >= 10 or time.monotonic() < st.get("raw_t", 0.0):
        return
    st["raw_t"], st["raw_n"] = time.monotonic() + 30.0, st.get("raw_n", 0) + 1
    try:
        vals = " ".join(f"{k * 8}:{_f64(buf, b + k * 8):.5g}" for k in range(32))
        with open(os.path.join(HERE, "wheel-debug.txt"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%H:%M:%S") + f" RAW {tag} " + vals + "\n")
    except Exception:
        pass


def _wheel_at(buf, b, strict=True):
    """Одно колесо по смещению b: (износ 0..1, три температуры °C, давление, тормоз °C) или None."""
    w, p, br = _f64(buf, b + WHEELS["wear"]), _f64(buf, b + WHEELS["pressure"]), _f64(buf, b + 24)
    t = [_f64(buf, b + WHEELS["temp"] + k * 8) for k in range(3)]
    if not (w == w and -0.05 <= w <= 1.05):
        return None
    w = min(1.0, max(0.0, w))
    t = [v if v == v else 0.0 for v in t]
    if all(150.0 < v < 800.0 for v in t):
        c = [v - 273.15 for v in t]                       # Кельвины (так отдаёт плагин)
    elif all(-60.0 < v < 400.0 for v in t) and any(abs(v) > 1.0 for v in t):
        c = list(t)                                       # уже градусы Цельсия
    elif strict or w <= 0.0:
        return None
    else:
        c = _alt_temps(buf, b)                            # температуры по обычному месту не годятся — ищем в других полях колеса
        if c is None:
            c = [0.0, 0.0, 0.0]
    if br == br and br > 1000.0 and br < 3000.0 and br - 273.15 < 2000.0:
        br = br - 273.15
    return {"temp": sum(c) / 3.0, "press": p if p == p and 0.0 < p < 1000.0 else 0.0, "wear": w,
            "brake": br if br == br and 0.0 < br < 2000.0 else 0.0}


def _wheels_at(buf, s, base):
    try:
        got = [_wheel_at(buf, s + base + i * WHEELS["size"], False) for i in range(4)]
    except (struct.error, IndexError):
        return None
    ok = [g for g in got if g]
    if not ok:
        return None
    if len(ok) < 4:                                       # одно «битое» колесо не должно гасить все остальные
        mean = {k: sum(g[k] for g in ok) / len(ok) for k in ok[0]}
        got = [g or dict(mean) for g in got]
    return got


def _read_wheels(buf, s):
    """По каждому колесу: средняя температура шины °C, давление кПа, остаток протектора 0..1, температура тормоза °C.
    Если по обычному смещению ничего не подтвердилось — ищем раскладку колёс рядом (версии плагина отличаются)."""
    got = _wheels_at(buf, s, _WHEEL_STATE["base"])
    if got and sum(1 for g in got if g) == 4:
        return got
    now = time.monotonic()
    if now >= _WHEEL_STATE["retry"]:
        _WHEEL_STATE["retry"] = now + 2.0
        for base in sorted(range(600, 1200, 4), key=lambda x: abs(x - WHEELS["base"])):
            try:
                strict = [_wheel_at(buf, s + base + i * WHEELS["size"]) for i in range(4)]
            except (struct.error, IndexError):
                continue
            if all(strict):
                _WHEEL_STATE["base"] = base
                return strict
        if not got:
            _wheel_debug(buf, s)
    return got


def _wheel_debug(buf, s):
    """Колёса не прочитались: записываем сырые значения в wheel-debug.txt рядом со скриптом (не чаще раза в 20 с, до 30 строк)."""
    st = _WHEEL_STATE
    if st.get("dbg_n", 0) >= 30 or time.monotonic() < st.get("dbg_t", 0.0):
        return
    st["dbg_t"], st["dbg_n"] = time.monotonic() + 20.0, st.get("dbg_n", 0) + 1
    try:
        b = s + st["base"]
        row = [f"w{i}: wear={_f64(buf, b + i * 260 + 152):.4g} T={_f64(buf, b + i * 260 + 128):.4g}/{_f64(buf, b + i * 260 + 136):.4g}/"
               f"{_f64(buf, b + i * 260 + 144):.4g} p={_f64(buf, b + i * 260 + 120):.4g}" for i in range(4)]
        with open(os.path.join(HERE, "wheel-debug.txt"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%H:%M:%S") + f" off={s} base={st['base']} " + " | ".join(row) + "\n")
    except Exception:
        pass


def _read_wear(buf, s):
    """Износ четырёх шин (FL, FR, RL, RR) как доли 0..1 (1 = новая) или None, если колёса не прочитались."""
    w = _read_wheels(buf, s)
    return tuple(x["wear"] for x in w) if w else None


def _parse_telemetry(buf, base, count):
    out = {}
    stride = TELEMETRY["stride"]
    for i in range(count):
        s = base + i * stride
        car_id = struct.unpack_from("<i", buf, s)[0]
        vx, vy, vz = (_f64(buf, s + TELEMETRY["local_vel"] + k * 8) for k in range(3))
        _wh = _read_wheels(buf, s)
        out[car_id] = {
            "_off": s, "elapsed": _f64(buf, s + TELEMETRY["elapsed"]),
            "pos": (_finite(_f64(buf, s + 160)), _finite(_f64(buf, s + 176))),      # мировые x и z, 50 Гц
            "ori": (_f64(buf, s + 232), _f64(buf, s + 248), _f64(buf, s + 280), _f64(buf, s + 296)),
            "lap": struct.unpack_from("<i", buf, s + 20)[0],
            "front": _text(buf[s + 620:s + 638]), "rear": _text(buf[s + 638:s + 656]),
            "model": _text(buf[s + 796:s + 826]), "class_code": buf[s + 826],
            "fuel": _f64(buf, s + TELEMETRY["fuel"]), "fuel_cap": _f64(buf, s + TELEMETRY["fuel_capacity"]),
            "ve": _f32(buf, s + TELEMETRY["virtual_energy"]),
            "speed": math.sqrt(vx * vx + vy * vy + vz * vz),
            "wheels": _wh, "wear": tuple(x["wear"] for x in _wh) if _wh else None,
            "_wdbg": (_WHEEL_STATE["base"], _f64(buf, s + _WHEEL_STATE["base"] + WHEELS["wear"]),
                      _f64(buf, s + _WHEEL_STATE["base"] + WHEELS["temp"]), _f64(buf, s + _WHEEL_STATE["base"] + WHEELS["pressure"])),
        }
    return out


def read_inputs(buf, off):
    """Ввод водителя из записи телеметрии: передача, газ, тормоз, сцепление, руль, признак ABS."""
    try:
        gear = struct.unpack_from("<i", buf, off + TEL_INPUTS["gear"])[0]
        thr, brk, steer, clu = struct.unpack_from("<4d", buf, off + TEL_INPUTS["inputs"])
        fbrk = _f64(buf, off + TEL_INPUTS["filtered_brake"])
        rng = _f32(buf, off + TEL_INPUTS["sw_range"])
    except struct.error:
        return None
    if not (-1 <= gear <= 12) or any(v != v or not -0.05 <= v <= 1.05 for v in (thr, brk, clu)):
        return None
    if steer != steer or not -1.05 <= steer <= 1.05:
        return None
    clamp = lambda v: min(1.0, max(0.0, v))
    thr, brk, clu = clamp(thr), clamp(brk), clamp(clu)
    fb = clamp(fbrk) if fbrk == fbrk and -0.05 <= fbrk <= 1.05 else brk
    rpm, mrpm = _f64(buf, off + TEL_INPUTS["rpm"]), _f64(buf, off + TEL_INPUTS["max_rpm"])
    if not (rpm == rpm and 0.0 <= rpm < 40000.0):
        rpm = 0.0
    if not (mrpm == mrpm and 1000.0 < mrpm < 40000.0):
        mrpm = 0.0
    try:
        limiter = buf[off + 604] != 0                      # mSpeedLimiter: ограничитель скорости в боксах
    except IndexError:
        limiter = False
    def _temp(o):
        try:
            v = _f64(buf, off + o)
        except struct.error:
            return None
        return v if v == v and -40.0 < v < 300.0 and v != 0.0 else None
    return {"water": _temp(364), "oil": _temp(372), "limiter": limiter, "rpm": rpm, "max_rpm": mrpm, "gear": gear, "thr": thr, "brk": brk, "clu": clu, "steer": max(-1.0, min(1.0, steer)),
            "range": rng if 0.0 < rng < 3600.0 else 0.0,
            "abs": brk > 0.10 and (brk - fb) > 0.04}          # тормоз водителя сильнее, чем доходит до колёс


def _parse_vehicle(buf, b, tel, raw_class=False):
    car_id = struct.unpack_from("<i", buf, b)[0]
    driver, vehicle = _text(buf[b + 4:b + 36]), _text(buf[b + 36:b + 100])
    best, last = _f64(buf, b + 144), _f64(buf, b + 168)
    ls1, ls2 = _f64(buf, b + 152), _f64(buf, b + 160)           # прошлый круг: сектор 1 и сектор 1+2 (накопительно)
    gap_next, gap_leader = _f64(buf, b + 232), _f64(buf, b + 244)
    info = tel.get(car_id) or {}
    class_raw = _text(buf[b + 200:b + 232])
    if raw_class:                       # rFactor 2: показываем название класса ровно как в игре
        cls = " ".join(class_raw.replace("_", " ").split()).upper()[:18] or "UNKNOWN"
    else:
        cls = class_from_name(class_raw)
        if cls == "UNKNOWN" and info.get("class_code") in CLASS_BY_CODE:   # поле есть только в LMU
            cls = CLASS_BY_CODE[info["class_code"]]
    fuel, cap, ve = info.get("fuel", 0.0), info.get("fuel_cap", 0.0), info.get("ve", 0.0)
    return {
        "key": car_id, "driver": driver, "vehicle": vehicle, "model": info.get("model", ""),
        "number": number_from(vehicle, driver, _text(buf[b + 544:b + 576])), "cls": cls,
        "laps": max(struct.unpack_from("<h", buf, b + 100)[0], 0), "finish": buf[b + 103],
        "best": best if 0.0 < best < 3600.0 else 0.0, "last": last if 0.0 < last < 3600.0 else 0.0,
        "last_s1": ls1 if 0.0 < ls1 < 3600.0 else 0.0, "last_s2": ls2 if 0.0 < ls2 < 3600.0 else 0.0,
        "tyre_wear": info.get("wear"), "wheels": info.get("wheels"),
        "place": buf[b + 199], "sector": buf[b + 102], "player": buf[b + 196] != 0, "in_pits": buf[b + 198] != 0,
        "garage": buf[b + 507] != 0, "pit_state": buf[b + 457], "control": buf[b + 197], "flag": buf[b + 504],
        "pitstops": max(struct.unpack_from("<h", buf, b + 192)[0], 0),
        "penalties": max(struct.unpack_from("<h", buf, b + 194)[0], 0),
        "gap_next": gap_next if 0.0 <= gap_next < 100_000.0 else 0.0,
        "laps_next": max(struct.unpack_from("<i", buf, b + 240)[0], 0),
        "gap_leader": gap_leader if 0.0 <= gap_leader < 100_000.0 else 0.0,
        "laps_leader": max(struct.unpack_from("<i", buf, b + 252)[0], 0),
        "pos": (info["pos"] if info.get("pos") and info["pos"] != (0.0, 0.0)
                else (_finite(_f64(buf, b + 264)), _finite(_f64(buf, b + 280)))),  # мировые x и z: 50 Гц из телеметрии, иначе 5 Гц
        "ori": (info["ori"] if info.get("ori") else
                (_f64(buf, b + 336), _f64(buf, b + 352), _f64(buf, b + 384), _f64(buf, b + 400))),   # оси машины в мире (x,z): левая и задняя
        "lap_dist": _sane(_f64(buf, b + 104), 0.0, 1e6), "est_lap": _sane(_f64(buf, b + 472), 0.0, 1e5),
        "has_tel": bool(info), "front": info.get("front", ""), "rear": info.get("rear", ""),
        "fuel": fuel if 0.0 <= fuel <= 500.0 else 0.0, "fuel_cap": cap if 0.0 < cap <= 500.0 else 0.0,
        "ve": ve if 0.0 <= ve <= 1.0 else 0.0, "fuel_fraction": buf[b + 578],
        "speed": info.get("speed", 0.0),
    }


# =========================================================================
# 2. Общие данные и ровные часы
# =========================================================================
state = {"frame": None, "frame_ts": 0.0, "ok": False, "err": "", "ver": 0, "changes": [], "history": collections.deque(maxlen=4000),
         "clock": {"off": None, "last_et": None, "last_change": 0.0, "session": None}}
lock = threading.Lock()


def sync_clock(et, session_id):
    """Ровные локальные часы игрового времени: не прыгаем за каждым значением игры,
    а мягко подтягиваем свои часы к нему (вызывать под lock)."""
    if not isinstance(et, (int, float)):
        return
    c, m = state["clock"], time.monotonic()
    if c["session"] != session_id:
        c["off"], c["last_et"], c["session"] = None, None, session_id
    meas = et - m
    if c["off"] is None or abs(meas - c["off"]) > 3.0:      # старт или скачок
        c["off"] = meas
    else:
        c["off"] += CLOCK_GAIN * (meas - c["off"])
    if et != c["last_et"]:
        c["last_et"], c["last_change"] = et, m


def game_now():
    """Текущее игровое время; если оно перестало идти (пауза) — стоим."""
    with lock:
        c = state["clock"]
        if c["off"] is None:
            return None
        m = time.monotonic()
        if m - c["last_change"] > 2.5:
            return c["last_et"]
        return m + c["off"]


def poller(stop, hz, source=None, game="auto"):
    reader = None if source else AutoReader(game)
    interval = 1.0 / max(1.0, min(60.0, hz))
    last_seq, last_err, was_ok = None, "", False
    while not stop.is_set():
        wait = interval
        try:
            frame = source() if source else reader.read()
            seq = frame.get("_seq", state["ver"])
            now = time.time()
            if not was_ok:
                print("Подключено к", GAME_NAMES.get(frame.get("game"), "Le Mans Ultimate"), ":",
                      frame["track"] or "—", "|", frame["label"], "|", len(frame["cars"]), "машин")
                was_ok = True
            with lock:
                if seq != last_seq:                       # данные реально изменились
                    last_seq = seq
                    state["changes"].append(now)
                    try:
                        ir_svg_apply(frame)
                        mapper.update(frame)
                    except Exception:
                        pass                                      # карта не должна ронять чтение данных
                    inp = frame.get("inputs")
                    if inp:
                        state["history"].append((time.monotonic(), inp["thr"], inp["brk"], inp["clu"], inp["abs"], inp["steer"]))
                    while state["changes"] and now - state["changes"][0] > 2.0:
                        state["changes"].pop(0)
                state.update(frame=frame, frame_ts=now, ok=True, err="")
                et, pe = frame["elapsed"], frame.get("player_elapsed")
                if pe and abs(pe - et) < 2.0:       # телеметрия обновляется чаще; берём её, если она в ногу
                    et = max(et, pe)
                sync_clock(et, frame["session_start"])
                state["ver"] += 1
        except Exception as e:                      # игра закрыта / сессия не загружена
            if reader:
                reader.close()
            msg = f"{type(e).__name__}: {e}"[:140]
            if msg != last_err:
                print("Нет данных от игры:", msg)
                last_err = msg
            was_ok = False
            with lock:
                state.update(ok=False, err=str(e)[:90])
                state["ver"] += 1
            wait = 1.0
        stop.wait(wait)


# =========================================================================
# 3. Расчёт таблицы: порядок, классы, отставания
# =========================================================================
def order_cars(cars, timed):
    if timed:      # практика/квалификация: по лучшему кругу, без времени — в конец
        return sorted(cars, key=lambda c: (c["best"] <= 0, c["best"] if c["best"] > 0 else 0.0,
                                           c["place"] or 999, c["key"]))
    return sorted(cars, key=lambda c: (c["place"] <= 0, c["place"], -c["laps"], c["key"]))


def gap_behind(car, ref):
    """Отставание car от ref: (секунды | None, круги)."""
    if car is ref:
        return 0.0, 0
    laps = car["laps_leader"] - ref["laps_leader"]
    if laps > 0:
        return None, laps
    return max(0.0, car["gap_leader"] - ref["gap_leader"]), 0


def fmt_gap(sec, laps):
    if laps and laps > 0:
        return f"+{int(laps)}L"
    if sec is None or sec <= 0:
        return "-"
    if sec >= 60:
        m = int(sec // 60)
        return f"+{m}:{sec - m * 60:04.1f}"
    return f"+{sec:.1f}" if sec >= 10 else f"+{sec:.2f}"


def group_gaps(group, timed):
    """key -> (до передней: текст, секунды; до лидера: текст)."""
    out, prev = {}, None
    for i, c in enumerate(group):
        if i == 0:
            out[c["key"]] = ("", None, "ЛИДЕР")
        elif timed:
            lead = group[0]["best"]
            lead_txt = fmt_gap(c["best"] - lead, 0) if c["best"] > 0 and lead > 0 else "-"
            ahead = c["best"] - prev["best"] if c["best"] > 0 and prev["best"] > 0 else None
            out[c["key"]] = (fmt_gap(ahead, 0), ahead, lead_txt)
        else:
            lg, ll = gap_behind(c, group[0])
            pg, pl = gap_behind(c, prev)
            out[c["key"]] = (fmt_gap(pg, pl), pg if not pl else None, fmt_gap(lg, ll))
        prev = c
    return out


def fuel_known(car):
    """Топливо показываем только если оно правдоподобно. Игра иногда отдаёт у чужих машин
    «полный бак» без реального расхода: после круга без пит-стопа так быть не может."""
    if car["fuel"] <= 0:
        return False
    if (not car["player"] and car["fuel_cap"] > 0 and abs(car["fuel"] - car["fuel_cap"]) < 0.05
            and car["laps"] >= 1 and car["pitstops"] == 0):
        return False
    return True


def relative_cars(frame, ahead_n=3, behind_n=3):
    """Соседи игрока по трассе: (я, [впереди: дальняя -> ближняя], [позади: ближняя -> дальняя]).
    Каждый сосед — (машина, разрыв в секундах со знаком, на сколько кругов он впереди/позади в зачёте)."""
    cars = frame["cars"] if frame else []
    me = next((c for c in cars if c["player"]), None)
    length = frame.get("track_len", 0.0) if frame else 0.0
    if me is None or length < 500.0:
        return None
    ref = next((v for v in (me["est_lap"], me["best"], me["last"]) if 20.0 < v < 1200.0), length / 50.0)
    mine_total = me["laps"] + me["lap_dist"] / length
    ahead, behind = [], []
    for c in cars:
        if c is me or c["garage"] or c["finish"] in (2, 3, 4):
            continue
        d = (c["lap_dist"] - me["lap_dist"]) % length
        if d > length / 2:
            d -= length                                  # ближайшее направление: впереди или позади
        gap = d / length * ref
        # на сколько целых кругов он впереди (+) или позади (-) в зачёте, с учётом положения на трассе
        lap_delta = round((c["laps"] + c["lap_dist"] / length) - mine_total - d / length)
        item = (abs(d), gap, c, max(-1, min(1, lap_delta)))
        (ahead if d > 0 else behind).append(item)
    ahead.sort(key=lambda t: t[0])
    behind.sort(key=lambda t: t[0])
    return (me, [(c, g, f) for _, g, c, f in ahead[:ahead_n]][::-1],
            [(c, g, f) for _, g, c, f in behind[:behind_n]])


def compound_letter(value):
    v = value.lower()
    for word, letter in (("soft", "S"), ("medium", "M"), ("hard", "H"), ("inter", "I"), ("wet", "W")):
        if word in v:
            return letter
    return ""


def tyre_text(car):
    f, r = compound_letter(car["front"]), compound_letter(car["rear"])
    if f and r and f != r:
        return f"{f}/{r}"
    return f or r


def status_of(car, out_lap=False):
    """(текст статуса, ключ цвета). PIT — пит-лейн и гараж; OUT LAP — только первый круг после выезда."""
    if car["finish"] in FINISH_STATES:
        return FINISH_STATES[car["finish"]], "out"
    if car["in_pits"] or car["garage"] or car["pit_state"] in (2, 3, 4):
        return "PIT", "pit"
    if car["pit_state"] == 1:
        return "PIT REQ", "pit"                     # заказал пит-стоп, ещё на трассе
    if car["penalties"] > 0:
        return car.get("penalty_label") or "PEN", "pen"
    if out_lap:
        return "OUT LAP", "pit"
    return "", ""


# =========================================================================
# 3b. Карта трассы: рисуем по тому, как вы едете
# =========================================================================
MAP_DIR = os.path.join(HERE, "maps")
try:
    import yassno_svgmap as svgmap
    IR_SVG = svgmap.IrSvgMaps(os.path.join(MAP_DIR, "svg"))
except Exception:                                    # нет файла или сломан — работаем по траектории игрока
    svgmap, IR_SVG = None, None
RUNTIME = {"ir_svg": True}                           # переключатель «схема трассы iRacing» (ставится из настроек карты)


def ir_svg_apply(frame):
    """iRacing: если для трассы скачана схема с сайта iRacing, ставим машины на её линию и отдаём её карте."""
    if frame.get("game") == "iracing" and svgmap is not None and not RUNTIME.get("logos_started"):
        RUNTIME["logos_started"] = True                  # один раз за запуск: скачать логотипы всех марок iRacing в фоне
        threading.Thread(target=svgmap.prefetch_logos, args=(os.path.join(find_logo_dir() or os.path.join(HERE, "logos"), "ir"),),
                         daemon=True).start()
    if not (IR_SVG and RUNTIME.get("ir_svg", True)) or frame.get("game") != "iracing":
        return
    loop = IR_SVG.get(frame.get("track_id"), frame.get("track_slug"))
    L = frame.get("track_len") or 0.0
    if not loop or L < 500:
        return
    cache = IR_SVG.world.get(frame["track_id"])
    if cache is None or abs(cache[0] - L) > 1.0:
        cache = (L, svgmap.to_world(loop, L))
        IR_SVG.world[frame["track_id"]] = cache
    pts = cache[1]
    n = len(pts)
    frame["svg_pts"] = pts
    for car in frame["cars"]:
        i = int(max(0.0, min(0.99999, car["lap_dist"] / L)) * n)
        a, b = pts[i], pts[(i + 6) % n]
        car["pos"] = a
        d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        fx, fz = (b[0] - a[0]) / d, (b[1] - a[1]) / d
        car["ori"] = (fz, -fx, fx, fz)
        if car.get("player"):
            frame["player_pos"] = a


def polyline_length(pts, closed=False):
    total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    if closed and len(pts) > 1:
        total += math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
    return total


def resample_closed(pts, n):
    """Замкнутая ломаная -> n точек, равномерно по длине."""
    p = list(pts) + [pts[0]]
    cum = [0.0]
    for a, b in zip(p, p[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1] or 1.0
    out, j = [], 0
    for k in range(n):
        target = total * k / n
        while j < len(p) - 2 and cum[j + 1] < target:
            j += 1
        seg = (cum[j + 1] - cum[j]) or 1.0
        t = (target - cum[j]) / seg
        out.append((p[j][0] + (p[j + 1][0] - p[j][0]) * t, p[j][1] + (p[j + 1][1] - p[j][1]) * t))
    return out


def best_shift(ref, new, window):
    """Циклический сдвиг new, при котором он лучше всего ложится на ref (метки старта круга чуть плавают)."""
    n, best, best_s = len(ref), None, 0
    for s in range(-window, window + 1):
        err = 0.0
        for i in range(0, n, 4):
            a, b = ref[i], new[(i + s) % n]
            err += (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
        if best is None or err < best:
            best, best_s = err, s
    return best_s


class TrackMapper:
    """Собирает карту трассы по траектории игрока. Карта принимается по чистому кругу
    (без боксов, без телепортов, длина около длины трассы) и уточняется следующими кругами."""
    MAX_LAPS = 4          # сколько кругов усредняем
    STEP = 3.0            # метров между записанными точками

    def __init__(self, folder=MAP_DIR):
        self.folder = folder
        self.lock = threading.Lock()
        self.key = None
        self.reset_state()

    def reset_state(self):
        self.loop, self.laps, self.sources = None, 0, ""
        self.sec, self.sec_obs, self.prev_sec = None, {1: [], 2: []}, None
        self.cur, self.cur_clean, self.cur_started = [], False, False
        self.cur_lap, self.last_pt, self.length = None, None, 0.0
        self.status = "wait"

    def path(self, key):
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{key[0]}_{key[1]}").strip("_") or "track"
        return os.path.join(self.folder, name + ".json")

    def load(self, key):
        if key[0].startswith("DEMO"):
            return
        try:
            with open(self.path(key), encoding="utf-8") as f:
                d = json.load(f)
            pts = [tuple(p) for p in d["points"]]
            if len(pts) >= 50:
                self.loop, self.laps, self.sources, self.status = pts, int(d.get("laps", 1)), "файл", "ready"
            sc = d.get("sectors")
            if isinstance(sc, list) and len(sc) == 2 and 0.0 < sc[0] < sc[1] < 1.0:
                self.sec = [float(sc[0]), float(sc[1])]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save(self):
        if self.key[0].startswith("DEMO"):
            return
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(self.path(self.key), "w", encoding="utf-8") as f:
                json.dump({"track": self.key[0], "length": self.key[1], "laps": self.laps, "sectors": self.sec,
                           "points": [[round(x, 2), round(z, 2)] for x, z in self.loop]}, f)
        except OSError:
            pass

    def clear(self):
        """Стереть карту этой трассы и записать заново."""
        with self.lock:
            if self.key:
                try:
                    os.remove(self.path(self.key))
                except OSError:
                    pass
            key = self.key
            self.reset_state()
            self.key = key

    def update(self, frame):
        pos, lap, length = frame.get("player_pos"), frame.get("player_lap"), frame.get("track_len", 0.0)
        if not frame.get("track") or not pos or length < 500.0:
            return
        key = (frame["track"], int(round(length / 10.0) * 10))
        with self.lock:
            if key != self.key:
                self.key = key
                self.reset_state()
                self.load(key)
            self.length = length
            me = next((c for c in frame["cars"] if c["player"]), None)
            if me is None:
                return
            self._learn_sectors(me, length)
            ext = frame.get("svg_pts")
            if ext:                                      # готовая схема трассы iRacing: свою не записываем
                if self.sources != "схема iRacing":
                    self.loop, self.laps, self.sources, self.status = list(ext), self.MAX_LAPS, "схема iRacing", "ready"
                return
            if self.sources == "схема iRacing":          # схему выключили или не получили — возвращаемся к записи
                sec = self.sec
                self.reset_state()
                self.load(key)
                self.sec = self.sec or sec
            if self.laps >= self.MAX_LAPS:
                return                                   # карта готова, больше не пишем
            if lap is not None and self.cur_lap is not None and lap == self.cur_lap + 1:
                self._finish_lap(length)                 # пересекли линию: круг закончен
                self.cur, self.cur_clean, self.cur_started, self.last_pt = [], True, True, None
            elif lap is not None and self.cur_lap is not None and lap != self.cur_lap:
                self.cur, self.cur_clean, self.cur_started, self.last_pt = [], False, False, None   # сброс круга/сессии
            if lap is not None:
                self.cur_lap = lap
            if me["in_pits"] or me["garage"]:
                self.cur_clean = False                   # круг с боксами карту не строит
                return
            if self.last_pt is not None:
                d = math.hypot(pos[0] - self.last_pt[0], pos[1] - self.last_pt[1])
                if d > 200.0:                            # телепорт (сброс на пит-лейн, перезапуск)
                    self.cur_clean = False
                    self.last_pt = pos
                    return
                if d < self.STEP:
                    return
            self.cur.append((float(pos[0]), float(pos[1])))
            self.last_pt = pos
            if self.status != "ready":
                self.status = "recording"

    def _learn_sectors(self, me, length):
        """Границы секторов: доля круга в момент, когда игра переключает сектор 1->2 и 2->3."""
        raw = me.get("sector")
        s = {1: 1, 2: 2, 0: 3}.get(raw)
        if s is None or me["in_pits"] or me["garage"]:
            self.prev_sec = None
            return
        prev, self.prev_sec = self.prev_sec, s
        if prev is None or prev == s or (prev, s) not in ((1, 2), (2, 3)):
            return
        f = me["lap_dist"] / length if length > 0 else 0.0
        if not 0.02 < f < 0.98:
            return
        obs = self.sec_obs[prev]
        obs.append(f)
        del obs[:-5]
        if self.sec_obs[1] and self.sec_obs[2]:
            a, b = (sorted(v)[len(v) // 2] for v in (self.sec_obs[1], self.sec_obs[2]))
            if 0.0 < a < b < 1.0 and (self.sec is None or abs(a - self.sec[0]) > 0.002 or abs(b - self.sec[1]) > 0.002):
                self.sec = [a, b]
                if self.loop:
                    self.save()

    def _finish_lap(self, length):
        pts = self.cur
        if not (self.cur_clean and self.cur_started) or len(pts) < 40:
            return
        total = polyline_length(pts, closed=True)
        if not (0.95 * length <= total <= 1.05 * length):
            return                                       # круг не похож на круг (вылет, срезка)
        n = max(400, min(3000, int(length / 4)))
        new = resample_closed(pts, n)
        if self.loop is None or len(self.loop) != n:
            self.loop, self.laps = new, 1
        else:
            shift = best_shift(self.loop, new, max(4, n // 12))
            new = [new[(i + shift) % n] for i in range(n)]
            k = self.laps
            self.loop = [((a[0] * k + b[0]) / (k + 1), (a[1] * k + b[1]) / (k + 1)) for a, b in zip(self.loop, new)]
            self.laps = k + 1
        self.sources, self.status = "записана", "ready"
        self.save()

    def snapshot(self):
        """Копия для рисования: (карта | None, текущий неполный круг, статус, число кругов)."""
        with self.lock:
            return (list(self.loop) if self.loop else None, list(self.cur[-2500:]), self.status, self.laps,
                    self.key[0] if self.key else "", list(self.sec) if self.sec else None)


mapper = TrackMapper()


class FuelCalc:
    """Расход топлива игрока: по кругам, среднее за 5 чистых кругов, остаток до конца гонки."""
    KEEP = 5

    def __init__(self):
        self.reset(None)

    def reset(self, key):
        self.key = key
        self.laps = collections.deque(maxlen=20)          # (расход л, время круга с) чистых кругов
        self.cur_lap = None
        self.start_fuel = 0.0
        self.prev_fuel = None
        self.dirty = True
        self.ve_laps = collections.deque(maxlen=20)       # расход виртуальной энергии за круг (доля 0..1)
        self.start_ve = 0.0
        self.prev_ve = None

    def update(self, frame):
        me = next((c for c in frame["cars"] if c["player"]), None) if frame else None
        if not me or not me.get("has_tel") or not me["fuel"] > 0:
            return
        key = (frame.get("session_start"), frame.get("track"), frame.get("kind"))
        if key != self.key or (self.cur_lap is not None and me["laps"] < self.cur_lap):
            self.reset(key)
        fuel, lap = me["fuel"], me["laps"]
        ve = me.get("ve", 0.0)
        in_pit = bool(me["in_pits"] or me["garage"])
        if self.prev_fuel is not None and fuel > self.prev_fuel + 0.05:
            self.dirty = True                              # дозаправка — круг не считаем
        if self.prev_ve is not None and ve > self.prev_ve + 0.003:
            self.dirty = True                              # энергию восполнили в боксах
        self.prev_ve = ve
        self.prev_fuel = fuel
        if in_pit:
            self.dirty = True
        if self.cur_lap is None:
            self.cur_lap, self.start_fuel, self.start_ve, self.dirty = lap, fuel, ve, True      # вошли посреди круга
            return
        if lap > self.cur_lap:
            used = self.start_fuel - fuel
            if lap == self.cur_lap + 1 and not self.dirty and 0.05 < used < 500:
                self.laps.append((used, me["last"] if me["last"] > 0 else 0.0))
                vused = self.start_ve - ve
                if ve > 0 and self.start_ve > 0 and 0.0005 < vused < 1.0:
                    self.ve_laps.append(vused)
            self.cur_lap, self.start_fuel, self.start_ve, self.dirty = lap, fuel, ve, in_pit

    def compute(self, frame, reserve=0.0, keep=None):
        me = next((c for c in frame["cars"] if c["player"]), None) if frame else None
        if not me or not me["fuel"] > 0:
            return None
        recent = list(self.laps)[-max(1, int(keep or self.KEEP)):]
        avg = sum(u for u, _ in recent) / len(recent) if recent else None
        times = [t for _, t in recent if t > 0]
        tlap = sum(times) / len(times) if times else (me["last"] or me["best"] or None)
        out = {"fuel": me["fuel"], "cap": me["fuel_cap"], "last": self.laps[-1][0] if self.laps else None,
               "avg": avg, "n": len(recent), "laps_fuel": (me["fuel"] / avg) if avg else None,
               "left": None, "need": None, "add": None, "stops": None, "reserve": reserve}
        tl = frame.get("track_len") or 0
        p = max(0.0, min(1.0, me["lap_dist"] / tl)) if tl > 0 else 0.0
        left = None
        if 0 < frame["max_laps"] < 100_000:
            left = max(0.0, frame["max_laps"] - (me["laps"] + p))
        elif 0 < frame["end_et"] < 1e7 and tlap:
            gn = game_now()
            rem = frame["end_et"] - (gn if gn is not None else frame["elapsed"])
            rest = (1.0 - p) * tlap                         # до конца текущего круга
            if rem <= 0:
                left = 1.0 - p
            elif rem <= rest:
                left = 1.0 - p
            else:
                left = (1.0 - p) + math.ceil((rem - rest) / tlap)
        out["left"] = left
        out["ve"] = None
        if me.get("ve", 0.0) > 0 and frame.get("game", "lmu") not in ("rf2", "iracing"):
            rv = list(self.ve_laps)[-max(1, int(keep or self.KEEP)):]
            vavg = sum(rv) / len(rv) if rv else None
            ve = {"level": me["ve"] * 100.0, "last": self.ve_laps[-1] * 100.0 if self.ve_laps else None,
                  "avg": vavg * 100.0 if vavg else None, "n": len(rv), "laps": (me["ve"] / vavg) if vavg else None,
                  "need": None, "add": None}
            if left is not None and vavg:
                ve["need"] = (left + reserve) * vavg * 100.0
                ve["add"] = ve["need"] - me["ve"] * 100.0
            out["ve"] = ve
        if left is not None and avg:
            out["need"] = (left + reserve) * avg
            out["add"] = out["need"] - me["fuel"]
            out["stops"] = math.ceil(out["add"] / me["fuel_cap"]) if out["add"] > 0 and me["fuel_cap"] > 0 else 0
        return out


fuel_calc = FuelCalc()


STATS_DIR = os.path.join(HERE, "stats")
STATS_DEMO = False                                      # в демо статистику не пишем (включается только для проверки)


TEL_DIR = os.path.join(STATS_DIR, "tel")
TEL_HZ = 20.0
TEL_COLS = ("t", "d", "spd", "thr", "brk", "clu", "str", "gear", "rpm", "x", "z", "fuel", "wat", "oil",
            "tt0", "tt1", "tt2", "tt3", "tp0", "tp1", "tp2", "tp3", "bt0", "bt1", "bt2", "bt3")
_tel_flag = {"t": 0.0, "on": True}


def telemetry_enabled():
    """Включена ли запись телеметрии: переключатель в настройках лаунчера (launcher.json), проверяем раз в 3 секунды."""
    now = time.monotonic()
    if now - _tel_flag["t"] > 3.0:
        _tel_flag["t"] = now
        try:
            with open(os.path.join(HERE, "launcher.json"), encoding="utf-8") as f:
                _tel_flag["on"] = bool(json.load(f).get("telemetry", True))
        except (OSError, ValueError, AttributeError):
            _tel_flag["on"] = True
    return _tel_flag["on"]


class SessionRecorder:
    """Пишет результаты сессии: круги, сектора, расход топлива, износ шин, максимальная и минимальная скорость.
    Каждая сессия — отдельный json в папке stats; файл обновляется после каждого круга."""
    MIN_KMH = 15.0                                       # ниже — стоим/в боксах, в минимальную скорость не берём

    def __init__(self):
        self.reset()
        self.error_shown = False

    def reset(self):
        self.sid = None
        self.data = None
        self.path = None
        self.cur = None
        self.pending = None
        self.prev_laps = None
        self.prev_pits = 0
        self.last_before = 0.0
        self.res = {}
        self.res_t = 0.0
        self.tbuf, self.tpend, self.tlast = None, None, 0.0
        self.dstate = {}

    # ---- телеметрия: по файлу на круг в stats/tel/<сессия>/, 20 раз в секунду
    def _tel_new(self, me, track_len):
        first = {c: [] for c in TEL_COLS}
        part = bool(track_len and me["lap_dist"] > 0.03 * track_len and not me["in_pits"])
        return {"cols": first, "t0": time.monotonic(), "partial": part, "pit": False}

    def _tel_sample(self, frame, me):
        now = time.monotonic()
        if now - self.tlast < 1.0 / TEL_HZ:
            return
        self.tlast = now
        if me["garage"]:
            return
        if self.tbuf is None:
            self.tbuf = self._tel_new(me, frame.get("track_len") or 0.0)
        tb = self.tbuf
        inp = frame.get("inputs") or {}
        wh = me.get("wheels") or []
        row = {"t": round(now - tb["t0"], 2), "d": round(me["lap_dist"], 1), "spd": round(me["speed"] * 3.6, 1),
               "thr": round(inp.get("thr", 0.0), 3), "brk": round(inp.get("brk", 0.0), 3), "clu": round(inp.get("clu", 0.0), 3),
               "str": round(inp.get("steer", 0.0), 3), "gear": int(inp.get("gear", 0)), "rpm": int(inp.get("rpm", 0)),
               "x": round(me["pos"][0], 1) if me.get("pos") else 0.0, "z": round(me["pos"][1], 1) if me.get("pos") else 0.0,
               "fuel": round(me["fuel"], 2), "wat": round(inp.get("water", 0.0), 1), "oil": round(inp.get("oil", 0.0), 1)}
        for i in range(4):
            w = wh[i] if i < len(wh) and isinstance(wh[i], dict) else {}
            row[f"tt{i}"], row[f"tp{i}"], row[f"bt{i}"] = round(w.get("temp", 0.0), 1), round(w.get("press", 0.0), 1), int(w.get("brake", 0.0))
        for k in TEL_COLS:
            tb["cols"][k].append(row[k])
        if me["in_pits"]:
            tb["pit"] = True

    def _tel_write(self, lapno, tb, rec):
        if not tb or len(tb["cols"]["t"]) < 40 or not self.path:
            return
        folder = os.path.join(TEL_DIR, os.path.splitext(os.path.basename(self.path))[0])
        try:
            os.makedirs(folder, exist_ok=True)
            fp = os.path.join(folder, f"lap{lapno:03d}.json.gz")
            with gzip.open(fp + ".tmp", "wt", encoding="utf-8") as f:
                json.dump({"v": 1, "lap": lapno, "time": rec.get("time"), "pit": bool(rec.get("pit") or tb["pit"]),
                           "partial": tb["partial"], "hz": TEL_HZ, "cols": tb["cols"]}, f, separators=(",", ":"))
            os.replace(fp + ".tmp", fp)
        except OSError as e:
            if not self.error_shown:
                self.error_shown = True
                print("Телеметрия: не удалось записать файл:", e)

    # ---- вспомогательное
    @staticmethod
    def _slug(text):
        return "".join(ch if ch.isalnum() else "_" for ch in (text or "track"))[:40].strip("_") or "track"

    def _new_session(self, frame, me):
        self.flush()
        self.reset_laps()
        now = time.localtime()
        self.data = {"version": 1, "game": frame.get("game", "lmu"), "track": frame.get("track", ""),
                     "kind": frame.get("kind", ""), "label": frame.get("label", ""), "car": me.get("vehicle", ""),
                     "model": me.get("model", ""), "cls": me.get("cls", ""), "driver": me.get("driver", ""),
                     "track_len": frame.get("track_len") or 0.0, "started": time.strftime("%Y-%m-%d %H:%M:%S", now),
                     "laps": [], "results": [], "drivers": {}}
        self.dstate = {}
        name = f"{self.data['game']}_{time.strftime('%Y%m%d-%H%M%S', now)}_{self._slug(self.data['track'])}_{self.data['kind']}.json"
        self.path = os.path.join(STATS_DIR, name)

    def reset_laps(self):
        self.cur, self.pending, self.prev_laps, self.prev_pits = None, None, None, 0

    def _start_lap(self, me):
        return {"vmax": 0.0, "vmin": None, "pit": bool(me["in_pits"]), "fuel0": me["fuel"] if me["fuel"] > 0 else None,
                "fuel1": me["fuel"] if me["fuel"] > 0 else None, "refuel": False,
                "w0": me.get("tyre_wear"), "w1": me.get("tyre_wear")}

    def _sample(self, me):
        c = self.cur
        kmh = me["speed"] * 3.6
        if not me["garage"]:
            if kmh > c["vmax"]:
                c["vmax"] = kmh
            if kmh >= self.MIN_KMH and not me["in_pits"] and (c["vmin"] is None or kmh < c["vmin"]):
                c["vmin"] = kmh
        if me["in_pits"]:
            c["pit"] = True
        f = me["fuel"]
        if f > 0:
            if c["fuel1"] is not None and f > c["fuel1"] + 0.3:
                c["refuel"] = True                       # дозаправка — расход за этот круг не считаем
            c["fuel1"] = f
            if c["fuel0"] is None:
                c["fuel0"] = f
        w = me.get("tyre_wear")
        if w:
            if c["w1"] and max(abs(a - b) for a, b in zip(w, c["w1"])) > 0.15:
                c["w0"] = None                           # резкий скачок — сменили шины
            c["w1"] = w
            if c["w0"] is None:
                c["w0"] = w

    def _finish(self, lapno, cur, me):
        """Собирает запись круга по накопленным данным и текущим значениям игры."""
        t = me["last"] or None
        s1 = me["last_s1"] or None
        s2 = (me["last_s2"] - me["last_s1"]) if me["last_s2"] and me["last_s1"] and me["last_s2"] > me["last_s1"] else None
        s3 = (t - me["last_s2"]) if t and me["last_s2"] and t > me["last_s2"] else None
        fuel_used = None
        if cur["fuel0"] is not None and cur["fuel1"] is not None and not cur["refuel"] and not cur["pit"]:
            fuel_used = max(0.0, cur["fuel0"] - cur["fuel1"])
        wear_used = None
        if cur["w0"] and cur["w1"] and not cur["pit"]:
            wear_used = [abs(a - b) for a, b in zip(cur["w0"], cur["w1"])]
        return {"lap": lapno, "time": t, "s1": s1, "s2": s2, "s3": s3, "fuel_used": fuel_used, "fuel_left": cur["fuel1"],
                "tyres": list(cur["w1"]) if cur["w1"] else None, "tyre_used": wear_used,
                "vmax": cur["vmax"] or None, "vmin": cur["vmin"], "pit": cur["pit"],
                "at": time.strftime("%H:%M:%S")}

    def _results(self, frame):
        """Итоги сессии по всем машинам: помним и тех, кто уже уехал; обновляем раз в пару секунд."""
        now = time.monotonic()
        if now - self.res_t < 2.0:
            return
        self.res_t = now
        cars = order_cars(frame["cars"], frame["kind"] in TIMED_KINDS)
        for i, c in enumerate(cars):
            r = self.res.setdefault(c["key"], {})
            r.update({"key": c["key"], "driver": c.get("driver", ""), "number": c.get("number", ""), "cls": c.get("cls", ""),
                      "car": c.get("vehicle", ""), "pos": i + 1, "laps": c.get("laps", 0),
                      "best": c.get("best") or None, "last": c.get("last") or None, "present": True,
                      "player": bool(c.get("player")), "pit": bool(c.get("in_pits") or c.get("garage"))})
        seen = {c["key"] for c in cars}
        for k, r in self.res.items():
            if k not in seen:
                r["present"] = False
        rows = sorted(self.res.values(), key=lambda r: (not r["present"], r["pos"]))
        # место в классе
        cnt = {}
        for r in rows:
            cnt[r["cls"]] = cnt.get(r["cls"], 0) + 1
            r["cpos"] = cnt[r["cls"]]
        if self.data is not None:
            self.data["results"] = [dict(r) for r in rows]
            self.data["res_at"] = time.strftime("%H:%M:%S")
            if now - getattr(self, "_rsave", 0.0) > 10.0:
                self._rsave = now
                self.save()

    def _drivers(self, frame):
        """Круги всех машин: время, сектора, пит, топливо и износ шин на конец круга. Строка: [круг, время, s1, s2, s3, пит, топливо, износ, перед, зад]."""
        now = time.monotonic()
        if now - getattr(self, "_dtick", 0.0) < 0.5 or self.data is None:
            return
        self._dtick = now
        drv = self.data.setdefault("drivers", {})
        for c in frame["cars"]:
            k, laps = c["key"], c.get("laps", 0)
            st = self.dstate.get(k)
            if st is None or laps < st["laps"] or laps > st["laps"] + 1:
                st = self.dstate[k] = {"laps": laps, "last": c.get("last", 0.0), "pit": False, "pend": None}
            ent = drv.setdefault(k, {"laps": []})
            ent.update(driver=c.get("driver", ""), number=c.get("number", ""), cls=c.get("cls", ""), car=c.get("vehicle", ""))
            if c.get("in_pits"):
                st["pit"] = True
            if st["pend"] is not None:
                lapno, t0, pit = st["pend"]
                last = c.get("last", 0.0)
                if last != st["last"] or now - t0 > 2.5:
                    ok = last != st["last"] and last > 0
                    s1, ls2 = c.get("last_s1", 0.0), c.get("last_s2", 0.0)
                    s2 = (ls2 - s1) if ls2 and s1 and ls2 > s1 else None
                    s3 = (last - ls2) if ok and ls2 and last > ls2 else None
                    w = c.get("tyre_wear")
                    ent["laps"].append([lapno, round(last, 3) if ok else None, round(s1, 3) if s1 else None,
                                        round(s2, 3) if s2 else None, round(s3, 3) if s3 else None, 1 if pit else 0,
                                        round(c["fuel"], 2) if c.get("fuel") else None,
                                        [round(x, 3) for x in w] if w else None, c.get("front", ""), c.get("rear", "")])
                    st["last"], st["pend"] = last, None
            if laps == st["laps"] + 1:
                st["pend"] = (laps, now, st["pit"])
                st["laps"], st["pit"] = laps, bool(c.get("in_pits"))

    # ---- основной вызов (каждый тик оверлея)
    def update(self, frame, enabled=True):
        if not enabled or not frame or (frame.get("track") == "DEMO TRACK" and not STATS_DEMO):
            return
        me = next((c for c in frame["cars"] if c["player"]), None)
        if me is None or not me["has_tel"]:
            return
        sid = (frame.get("session_start"), frame.get("track"), frame.get("kind"))
        if sid != self.sid or self.data is None:
            self.sid = sid
            self._new_session(frame, me)
        if sid != getattr(self, "_rsid", None):
            self._rsid = sid
            self.res = {}
        self._results(frame)
        self._drivers(frame)
        laps = me["laps"]
        if self.prev_laps is None or laps < self.prev_laps or laps > self.prev_laps + 1:
            self.prev_laps, self.cur, self.pending = laps, self._start_lap(me), None   # старт, телепорт в боксы, сброс
            self.tbuf, self.tpend = None, None
        if self.pending is not None:                       # ждём, пока игра обновит время прошлого круга
            lapno, cur, t0 = self.pending
            if me["last"] != self.last_before or time.monotonic() - t0 > 2.5:
                rec = self._finish(lapno, cur, me)
                if self.tpend is not None and self.tpend[0] == lapno:
                    if rec["time"] is not None and telemetry_enabled():
                        self._tel_write(lapno, self.tpend[1], rec)
                    self.tpend = None
                if rec["time"] is not None or rec["vmax"]:
                    self.data["laps"].append(rec)
                    self.save()
                else:
                    self.save()
                self.pending = None
        if laps == self.prev_laps + 1:
            if self.pending is not None:                   # предыдущий круг так и не получил время
                self.pending = None
            self.pending = (laps, self.cur, time.monotonic())
            self.tpend, self.tbuf = ((laps, self.tbuf) if self.tbuf is not None else None), None
            self.last_before = me["last"]
            self.prev_laps = laps
            self.cur = self._start_lap(me)
        self._sample(me)
        if telemetry_enabled():
            self._tel_sample(frame, me)
        else:
            self.tbuf = None

    def save(self):
        if not self.data or not (self.data["laps"] or any(r.get("laps") for r in self.data.get("results", []))):
            return
        try:
            os.makedirs(STATS_DIR, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as e:
            if not self.error_shown:
                self.error_shown = True
                print("Статистика: не удалось записать файл:", e)

    def flush(self):
        self.save()


recorder = SessionRecorder()


# =========================================================================
# 4. Оформление
# =========================================================================
KEY = "#010203"            # служебный цвет: везде, где он, окно прозрачное
BG, HDR_BG = "#0b0e13", "#161b24"
ROW_A, ROW_B = "#11151c", "#0e1218"
ME_BG, ME_EDGE = "#1b3a63", "#4da3ff"
BORDER, TRACK, BADGE = "#2a3140", "#262d38", "#1a2029"
FG, DIM = "#eef1f5", "#8791a0"
ACCENT, GREEN, YELLOW, RED, PURPLE = "#f2b705", "#3ddc84", "#ffc933", "#ff5252", "#c77dff"
CLASS_COLORS = {"HYPERCAR": "#ff4d4d", "LMP2": "#3a8dff", "LMGT3": "#2fd27e", "LMP3": "#b04dff",
                "GTE": "#ff9f1c", "PACE": "#f2c94c", "UNKNOWN": "#8791a0"}
COMPOUND_COLORS = {"S": "#ff4d5e", "M": "#f6c85f", "H": "#e8eef8", "I": "#38d97b", "W": "#4a90ff"}

BRANDS = [(("ferrari",), "F", "#cd101a", "ferrari"), (("porsche",), "P", "#c41e3a", "porsche"),
          (("bmw",), "B", "#0066b1", "bmw"), (("mclaren",), "M", "#ff7a00", "mclaren"),
          (("aston",), "AM", "#00a080", "astonmartin"), (("corvette", "chevrolet"), "C", "#ebc300", "chevrolet"),
          (("lamborghini",), "L", "#cdaa46", "lamborghini"), (("ford", "mustang"), "F", "#2a62c8", "ford"),
          (("lexus",), "L", "#aab0ba", "lexus"), (("mercedes",), "M", "#96a0aa", "mercedes"),
          (("toyota",), "T", "#d71428", "toyota"), (("peugeot",), "P", "#0055a5", ""),
          (("cadillac",), "C", "#af9155", "cadillac"), (("alpine",), "A", "#005ac8", "alpine"),
          (("oreca", "ligier"), "O", "#6e7887", "")]

# ключ, заголовок, ширина (в «нулях» шрифта), выравнивание
COLS = [("pos", "ПОЗ", 2.6, "e"), ("delta", "", 2.8, "w"), ("logo", "", 2.8, "w"),
        ("num", "№", 3.8, "e"), ("tyre", "ШИНЫ", 3.8, "w"), ("name", "ПИЛОТ", 17, "w"), ("irat", "ЛИЦЕНЗИЯ / iR / ПРОГНОЗ", 17.5, "e"),
        ("ahead", "ВПЕРЕДИ", 7.5, "e"), ("lead", "ЛИДЕР", 7.5, "e"),
        ("best", "ЛУЧШИЙ", 8.6, "e"), ("last", "ПОСЛЕД.", 8.6, "e"),
        ("fuel", "ТОПЛ.", 5.6, "e"), ("ve", "ЭНЕРГИЯ", 12.5, "w"), ("status", "СТАТУС", 7.8, "e")]
TOGGLE_COLS = [("logo", "Логотип марки"), ("tyre", "Шины"), ("ahead", "Отставание от передней"),
               ("lead", "Отставание от лидера"), ("best", "Лучший круг"), ("last", "Последний круг"),
               ("fuel", "Топливо"), ("ve", "Энергия"), ("delta", "Позиции ▲▼"), ("status", "Статус"), ("irat", "iRating и лицензия")]


def class_color(name):
    if name in CLASS_COLORS:
        return CLASS_COLORS[name]
    known = class_from_name(name)                 # «Hypercar», «GT3 Pro» и т.п. красим как в LMU
    if known != "UNKNOWN":
        return CLASS_COLORS[known]
    palette = ["#3a8dff", "#ff9f1c", "#b04dff", "#2fd27e", "#ff4d4d", "#22c4d6", "#e056a0"]
    return palette[sum(map(ord, name)) % len(palette)]


def fmt_lap(t):
    if not isinstance(t, (int, float)) or t <= 0:
        return "-:--.---"
    m = int(t // 60)
    return f"{m}:{t - m * 60:06.3f}"


def cars_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} МАШИНА"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} МАШИНЫ"
    return f"{n} МАШИН"


def fmt_hms(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def ve_color(v):
    return GREEN if v >= 0.5 else YELLOW if v >= 0.2 else RED


IR_BRAND_ALIAS = {"vw": "volkswagen", "skip": "skipbarber", "formula": "formulavee", "super": "superformula", "mercedesamg": "mercedes",
                  "nascar": "nascar", "dirt": "dirtcarracing", "late": "dirtcarracing", "pro": "pro2truck", "cross": "crosscartn11"}


def brand_of(car):
    text = f"{car['model']} {car['vehicle']}".lower()
    for keys, mono, colour, slug in BRANDS:
        if any(k in text for k in keys):
            return mono, colour, slug
    letter = "".join(ch for ch in (car["model"] or car["vehicle"] or "") if ch.isalpha())[:1].upper() or "•"   # цифры в названии мода — не марка
    if "irating" in car:                                          # iRacing: логотип марки скачаем с сайта iRacing
        first = (re.split(r"[^a-z0-9]+", (car["model"] or car["vehicle"] or "").lower().strip()) or [""])[0]
        first = IR_BRAND_ALIAS.get(first, first)
        if first and not first.isdigit():
            return letter, "#6e7887", "ir:" + first
    return letter, "#6e7887", ""


DEFAULT_THEME = {"accent": "#f2b705", "text": "#eef1f5", "dim": "#8791a0", "panel": "#161b24",
                 "background": "#0b0e13", "border": "#2a3140", "me": "#4da3ff",
                 "throttle": "#2ee26d", "brake": "#ff3b30", "clutch": "#4a90ff"}
ELEMENTS = ("standings", "relative", "pedals", "shiftlight", "trackmap", "radar", "weather", "lights", "limiter", "fuel",
            "delta", "alerts", "tyres", "pitplan", "gaps", "engine", "sesstime", "clock", "speedo")
LAYOUT_POS = ("x", "y", "rel_x", "rel_y", "ped_x", "ped_y", "map_x", "map_y", "radar_x", "radar_y", "wx_x", "wx_y",
              "lights_x", "lights_y", "lim_x", "lim_y", "fuel_x", "fuel_y", "delta_x", "delta_y", "alerts_x", "alerts_y",
              "tyres_x", "tyres_y", "pitplan_x", "pitplan_y", "gaps_x", "gaps_y", "engine_x", "engine_y", "sesstime_x", "sesstime_y", "clock_x", "clock_y", "speedo_x", "speedo_y")
LAYOUT_SCALE = tuple("scale_" + e for e in ELEMENTS) + ("map_scale", "fuel_scale") + tuple("sx_" + e for e in ELEMENTS) + tuple("sy_" + e for e in ELEMENTS)
LAYOUT_KEYS = ELEMENTS + ("stand_mode", "map_mode", "delta_view", "delta_theme", "tyres_view", "engine_view", "speedo_view", "speedo_max") + LAYOUT_POS + LAYOUT_SCALE
SCREEN_KEYS = LAYOUT_POS + tuple("sx_" + e for e in ELEMENTS) + tuple("sy_" + e for e in ELEMENTS)


def screen_bounds(w):
    """Весь рабочий стол со всеми мониторами: (x0, y0, ширина, высота)."""
    if os.name == "nt":
        try:
            import ctypes
            u = ctypes.windll.user32
            x0, y0, cw, ch = (int(u.GetSystemMetrics(i)) for i in (76, 77, 78, 79))
            if cw > 0 and ch > 0:
                return x0, y0, cw, ch
        except Exception:
            pass
    return 0, 0, int(w.winfo_screenwidth()), int(w.winfo_screenheight())


def screen_signature(w):
    pw, ph = int(w.winfo_screenwidth()), int(w.winfo_screenheight())
    x0, y0, cw, ch = screen_bounds(w)
    return f"{pw}x{ph}@{x0},{y0},{cw}x{ch}"


def load_screen_layout(cfg, sig, bounds):
    """У каждого набора мониторов своя раскладка окон. Если такого набора ещё не было —
    берём раскладку прошлого и пересчитываем пропорционально."""
    sl = cfg.get("screen_layouts")
    if not isinstance(sl, dict):
        sl = cfg["screen_layouts"] = {}
    cur = sl.get(sig)
    if isinstance(cur, dict) and isinstance(cur.get("v"), dict):
        cfg.update({k: v for k, v in cur["v"].items() if k in SCREEN_KEYS})
    else:
        prev = sl.get(cfg.get("screen_last"))
        if isinstance(prev, dict) and isinstance(prev.get("b"), list) and len(prev["b"]) == 4 and cfg.get("screen_last") != sig:
            ox, oy, ow, oh = prev["b"]
            nx, ny, nw, nh = bounds
            if ow > 0 and oh > 0:
                for k in SCREEN_KEYS:
                    if k.endswith("_x") or k == "x":
                        if isinstance(cfg.get(k), (int, float)):
                            cfg[k] = int(nx + (cfg[k] - ox) * nw / ow)
                    elif k.endswith("_y") or k == "y":
                        if isinstance(cfg.get(k), (int, float)):
                            cfg[k] = int(ny + (cfg[k] - oy) * nh / oh)
    cfg["screen_last"] = sig


LAYOUT_CAT = {"TEST": "practice", "PRACTICE": "practice", "WARMUP": "practice", "QUALIFYING": "qualify", "RACE": "race"}


WIN_STYLES = {"classic": (1.0, "red"), "square": (0.0, "red"), "soft": (1.9, "red"),
              "minimal": (0.6, "none"), "accent": (1.0, "accent")}
MARK, STYLE_R = "#e5322d", 1.0                       # цвет метки у заголовка окна ("" — без метки) и множитель скругления


def apply_theme(theme, style="classic"):
    """Цвета и стиль окон из профиля (запуск через приложение-лаунчер). Вызывается один раз до создания окон."""
    global MARK, STYLE_R, BG, HDR_BG, ROW_A, ROW_B, ME_BG, ME_EDGE, BORDER, TRACK, FG, DIM, ACCENT
    t = dict(DEFAULT_THEME)
    t.update({k: v for k, v in (theme or {}).items() if isinstance(v, str) and len(v) == 7 and v.startswith("#")})
    ACCENT, FG, DIM, HDR_BG, BG, BORDER, ME_EDGE = (t["accent"], t["text"], t["dim"], t["panel"],
                                                    t["background"], t["border"], t["me"])
    ROW_A, ROW_B = _mix(BG, "#ffffff", 0.035), _mix(BG, "#ffffff", 0.015)
    ME_BG = _mix(BG, ME_EDGE, 0.30)
    TRACK = _mix(BORDER, BG, 0.2)
    STYLE_R, kind = WIN_STYLES.get(style, WIN_STYLES["classic"])
    MARK = {"red": "#e5322d", "none": "", "accent": ACCENT}[kind]
    if kind == "accent":
        BORDER = _mix(BG, ACCENT, 0.65)
    PedalsWindow.THR, PedalsWindow.BRK, PedalsWindow.CLU = t["throttle"], t["brake"], t["clutch"]
    WeatherWindow.CARD = _mix(HDR_BG, "#000000", 0.12)
    FuelWindow.CARD = WeatherWindow.CARD


def pick_family(root, wanted=None):
    avail = set(tkfont.families(root))
    if wanted and wanted in avail:
        return wanted
    for name in ("Bahnschrift", "Segoe UI Semibold", "Segoe UI", "Arial"):
        if name in avail:
            return name
    return "TkDefaultFont"


def find_logo_dir(explicit=None):
    for d in ([explicit] if explicit else []) + [os.path.join(HERE, "logos"), os.path.join(HERE, "web", "logos"),
                                                 os.path.join(HERE, "..", "web", "logos")]:
        if d and os.path.isdir(d):
            return d
    return None


# ---------- Windows: сквозные клики и горячие клавиши ----------
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80000, 0x80, 0x08000000


def set_click_through(root, enable):
    if os.name != "nt":
        return False
    try:
        u = ctypes.windll.user32
        u.GetAncestor.restype = ctypes.c_void_p
        u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        u.GetWindowLongW.restype = ctypes.c_long
        u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetWindowLongW.restype = ctypes.c_long
        u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        hwnd = u.GetAncestor(root.winfo_id(), 2)
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        flags = WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        style = (style | flags | WS_EX_LAYERED | WS_EX_TOOLWINDOW) if enable else (style & ~flags)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        return True
    except Exception:
        return False


def keys_down(*vks):
    if os.name != "nt":
        return False
    try:
        return all(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)
    except Exception:
        return False


VK_CTRL, VK_SHIFT, VK_L, VK_Q, VK_R = 0x11, 0x10, 0x4C, 0x51, 0x52
VK_H, VK_T, VK_M, VK_D, VK_Y = 0x48, 0x54, 0x4D, 0x44, 0x59
HOTKEY_DEFAULTS = {"lock": "Ctrl+Shift+L", "reset": "Ctrl+Shift+R", "quit": "Ctrl+Shift+Q", "hide": "Ctrl+Shift+H",
                   "tim": "Ctrl+Shift+T", "map": "Ctrl+Shift+M", "delta": "Ctrl+Shift+D", "tyres": "Ctrl+Shift+Y"}


def parse_hotkey(text):
    """'Ctrl+Shift+H' -> список кодов клавиш; None, если строка не разобралась."""
    mods = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12}
    vks, key = [], None
    for part in str(text).replace(" ", "").split("+"):
        low = part.lower()
        if low in mods:
            vks.append(mods[low])
        elif len(part) == 1 and part.isalnum():
            key = ord(part.upper())
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 12:
            key = 0x70 + int(low[1:]) - 1
        else:
            return None
    return vks + [key] if key and vks else None


def draw_rrect(canvas, x1, y1, x2, y2, r, fill="", outline=""):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, fill=fill, outline=outline)


GLOW = ("#2ee26d", "#1f9d4f", "#135c2e")


GRIP = 26                                                       # размер уголка растягивания, пикселей
SCALE_SPEC = {"trackmap": ("map_scale", 0.5, 3.0, 1.0), "radar": ("radar_scale", 0.3, 3.0, 1.0),
              "fuel": ("fuel_scale", 0.5, 2.0, 0.8), "standings": ("scale_standings", 0.5, 3.0, 1.0)}
OVERLAY_NAMES = {"standings": "Таблица", "relative": "Релатив", "pedals": "Ввод", "trackmap": "Карта", "radar": "Радар",
                 "weather": "Погода", "lights": "Огни", "limiter": "Лимитер", "fuel": "Топливо", "delta": "Дельта",
                 "alerts": "Предупреждения", "tyres": "Шины", "pitplan": "План пит-стопа", "gaps": "Отрывы",
                 "engine": "Двигатель", "sesstime": "Время сессии", "clock": "Часы", "speedo": "Спидометр"}


EDGE = 9                                                        # ширина полосы у края окна, за которую его растягивают
CURSORS = {(1, 0): "size_we", (-1, 0): "size_we", (0, 1): "size_ns", (0, -1): "size_ns",
           (1, 1): "size_nw_se", (-1, -1): "size_nw_se", (1, -1): "size_ne_sw", (-1, 1): "size_ne_sw"}


def grip_zone(canvas, e):
    """За какой край/угол схвачено окно: (sx, sy), где -1 — левый/верхний, 1 — правый/нижний, 0 — не затронут. None — не у края."""
    try:
        W, H = int(float(canvas.cget("width"))), int(float(canvas.cget("height")))
    except (tk.TclError, ValueError):
        return None
    if e.x >= W - 32 and e.y <= 32:                             # там крестик закрытия
        return None
    c = GRIP - 8                                                # в углах зона шире, чем вдоль сторон
    sx = -1 if e.x < (c if e.y < c or e.y >= H - c else EDGE) else 1 if e.x >= W - (c if e.y < c or e.y >= H - c else EDGE) else 0
    sy = -1 if e.y < (c if e.x < c or e.x >= W - c else EDGE) else 1 if e.y >= H - (c if e.x < c or e.x >= W - c else EDGE) else 0
    return (sx, sy) if (sx or sy) else None


def in_grip(canvas, e):
    return grip_zone(canvas, e) is not None


ASPECT_LOCKED = ("trackmap", "radar", "speedo")                  # круглое содержимое: тянется только пропорционально


def stretch_get(cfg, on):
    """Растяжение окна по ширине и высоте независимо (1.0 — как есть)."""
    try:
        fx, fy = float(cfg.get("sx_" + on, 1.0)), float(cfg.get("sy_" + on, 1.0))
    except (TypeError, ValueError):
        return 1.0, 1.0
    return max(0.4, min(6.0, fx)), max(0.4, min(6.0, fy))


def stretch_apply(canvas, cfg, on, bw, bh, scale_items):
    """Растянуть уже нарисованное и подогнать размер окна; bw, bh — размер без растяжения."""
    fx, fy = stretch_get(cfg, on)
    try:
        if scale_items and (fx != 1.0 or fy != 1.0):
            canvas.delete("mv")
            canvas.scale("all", 0, 0, fx, fy)
        if bw > 0 and bh > 0 and (fx != 1.0 or fy != 1.0):
            w, h = max(40, round(bw * fx)), max(30, round(bh * fy))
            if int(float(canvas.cget("width"))) != w:
                canvas.config(width=w)
            if int(float(canvas.cget("height"))) != h:
                canvas.config(height=h)
    except (tk.TclError, ValueError):
        pass


def set_edge_cursor(canvas, locked, e):
    """Курсор-стрелка у края окна в режиме перемещения."""
    z = None if locked else grip_zone(canvas, e)
    want = CURSORS.get(z, "") if z else ""
    if getattr(canvas, "_cur", None) != want:
        canvas._cur = want
        try:
            canvas.config(cursor=want)
        except tk.TclError:
            pass


def move_decor(canvas, locked, R, on_close):
    """Режим перемещения: окно светится зелёной рамкой, в углу — крестик закрытия. В обычном режиме ничего не рисуется."""
    if locked:
        if canvas.find_withtag("mv"):
            canvas.delete("mv")
        return
    try:
        W, H = int(float(canvas.cget("width"))), int(float(canvas.cget("height")))
    except (tk.TclError, ValueError):
        return
    if canvas.find_withtag("mv") and getattr(canvas, "_mv_size", None) == (W, H):
        return
    canvas.delete("mv")
    canvas._mv_size = (W, H)
    for i, col in enumerate(GLOW):                              # рамка с убывающим свечением внутрь
        o, r = 1 + i * 2, max(0, R - i * 2)
        x1, y1, x2, y2 = o, o, W - 1 - o, H - 1 - o
        if r > 1:
            pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                   x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
            canvas.create_polygon(pts, smooth=True, fill="", outline=col, width=2, tags="mv")
        else:
            canvas.create_rectangle(x1, y1, x2, y2, outline=col, width=2, tags="mv")
    bw = 22
    x0, y0 = W - bw - 7, 7
    canvas.create_rectangle(x0, y0, x0 + bw, y0 + bw, fill="#b3261e", outline="#ffffff", width=1, tags=("mv", "mvx"))
    canvas.create_line(x0 + 6, y0 + 6, x0 + bw - 6, y0 + bw - 6, fill="#ffffff", width=2, tags=("mv", "mvx"))
    canvas.create_line(x0 + bw - 6, y0 + 6, x0 + 6, y0 + bw - 6, fill="#ffffff", width=2, tags=("mv", "mvx"))
    canvas.tag_bind("mvx", "<Button-1>", lambda e: on_close())
    g = GRIP - 8                                                # уголки, за которые тоже можно растягивать
    for ax, ay, dx, dy in ((W - 2, H - 2, -1, -1), (2, H - 2, 1, -1), (2, 2, 1, 1)):
        canvas.create_polygon(ax, ay + dy * g, ax, ay, ax + dx * g, ay, fill="#2ee26d", outline="#0b3d1c", width=1, tags="mv")
    canvas.create_polygon(W - 2, H - g, W - 2, H - 2, W - g, H - 2, fill="#2ee26d", outline="#0b3d1c", width=1, tags="mv")
    canvas.tag_raise("mv")


def draw_irat(c, car, right, cy, font, rh, delta=None):
    """Как в iRacing: плашка лицензии в её цвете («A 4.12») и светлая плашка с iRating («3.2k») одного размера;
    right — правая граница колонки. delta — прогноз изменения iRating (число), рисуется справа."""
    ir, lic = car.get("irating", 0), (car.get("lic", "") or "").strip()
    if not (ir > 0 or lic):
        return
    try:
        v = int(str(car.get("lic_col", "")), 16)
        lcol = "#%06x" % v
        lum = (0.299 * (v >> 16 & 255) + 0.587 * (v >> 8 & 255) + 0.114 * (v & 255)) / 255
    except ValueError:
        lcol, lum = "#3a4250", 0.0
    h = int(rh * 0.30)
    pw = font.measure("A 4.99") + 12                      # обе плашки одной ширины и высоты
    x = right
    if delta is not None and ir > 0:
        d = int(round(delta))
        txt = ("+%d" % d) if d > 0 else ("\u2212%d" % -d) if d < 0 else "0"
        col = "#2ee26d" if d > 0 else "#ff5a4f" if d < 0 else "#8791a0"
        c.create_text(x, cy, text=txt, fill=col, font=font, anchor="e")
        x -= font.measure("+999") + 6
    if ir > 0:
        t = f"{ir / 1000:.1f}k" if ir >= 1000 else str(ir)
        draw_rrect(c, x - pw, cy - h, x, cy + h, 5, fill="#e9edf2", outline="")
        c.create_text(x - pw / 2, cy, text=t, fill="#10151c", font=font)
        x -= pw + 4
    if lic:
        draw_rrect(c, x - pw, cy - h, x, cy + h, 5, fill=lcol, outline="")
        c.create_text(x - pw / 2, cy, text=lic, fill="#10151c" if lum > 0.55 else "#ffffff", font=font)


def ir_forecast(cars):
    """Прогноз изменения iRating по текущим позициям (по классам): по формуле, близкой к iRacing. Это оценка, а не точное число."""
    out = {}
    B = 1600.0 / math.log(2.0)
    by_cls = {}
    for car in cars:
        if car.get("irating", 0) > 0 and not car.get("garage"):
            by_cls.setdefault(car.get("cls", ""), []).append(car)
    for grp in by_cls.values():
        n = len(grp)
        if n < 2:
            continue
        grp.sort(key=lambda x: x.get("place", 999))
        a = [math.exp(-x["irating"] / B) for x in grp]
        for i, car in enumerate(grp):
            exp_beaten = 0.0
            for j in range(n):
                if j != i:
                    pij = (1 - a[i]) * a[j] / ((1 - a[j]) * a[i] + (1 - a[i]) * a[j])     # шанс обойти j
                    exp_beaten += pij
            rank = i + 1
            fudge = (n / 2.0 - rank) / n
            out[car["key"]] = (n - rank - exp_beaten - fudge) * 200.0 / n
    return out


def fmt_rel_gap(g):
    """Разрыв до соседа: впереди «-1.8», позади «+2.4»."""
    a = abs(g)
    sign = "-" if g > 0 else "+"
    if a >= 60:
        return f"{sign}{int(a // 60)}:{a % 60:04.1f}"
    return f"{sign}{a:.1f}"


class CfgView:
    """Вид на конфигурацию, где размер шрифта умножен на масштаб окна; запись идёт в настоящий конфиг."""

    def __init__(self, cfg, scale):
        self._c, self._s = cfg, scale

    def __getitem__(self, k):
        v = self._c[k]
        return max(7, int(round(v * self._s))) if (k == "font" and self._s != 1.0) else v

    def get(self, k, d=None):
        try:
            return self[k]
        except KeyError:
            return d

    def __setitem__(self, k, v):
        self._c[k] = v

    def __contains__(self, k):
        return k in self._c

    def setdefault(self, k, d=None):
        return self._c.setdefault(k, d)


class ScaledMain:
    """Подменяет главное окно для вспомогательного окна: шрифты и размеры строк умножены на scale_<окно>."""
    _cache = {}

    def __init__(self, main, win):
        self._m, self._w = main, win

    @property
    def scale(self):
        try:
            return max(0.5, min(3.0, float(self._m.cfg.get("scale_" + self._w.ON, 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def _calc(self):
        m, sc = self._m, self.scale
        key = (m.family, m.cfg["font"], sc)
        got = ScaledMain._cache.get(key)
        if got is None:
            sz = max(7, int(round(m.cfg["font"] * sc)))
            fam = m.family
            font = tkfont.Font(family=fam, size=sz)
            rh = font.metrics("linespace") + 14
            got = {"font": font, "bold": tkfont.Font(family=fam, size=sz, weight="bold"),
                   "small": tkfont.Font(family=fam, size=max(7, sz - 3)),
                   "big": tkfont.Font(family=fam, size=sz + 5, weight="bold"),
                   "cw": font.measure("0"), "rh": rh, "R": int(rh * 0.42)}
            ScaledMain._cache[key] = got
        return got

    @property
    def cfg(self):
        return CfgView(self._m.cfg, self.scale)

    def __getattr__(self, n):
        if n in ("font", "bold", "small", "big", "cw", "rh", "R"):      # у вспомогательных окон шрифт свой, не зависит от размера таблицы
            return self._calc()[n]
        return getattr(self._m, n)


class RelativeWindow:
    """Отдельное окно «релатив»: 3 машины впереди по трассе, вы, 3 позади."""
    AHEAD, BEHIND = 3, 3
    POS, ON = ("rel_x", "rel_y"), "relative"        # ключи конфигурации: положение и «включено ли»
    COLS = [("pos", 2.6, "e"), ("num", 3.8, "e"), ("name", 16.0, "w"), ("irat", 13.5, "e"), ("gap", 7.0, "e"), ("tag", 4.4, "e")]

    def __init__(self, main):
        self.main, cfg = ScaledMain(main, self), main.cfg
        self.top = t = tk.Toplevel(main.root)
        t.title("Relative")
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.attributes("-alpha", cfg.get("op_" + self.ON, cfg["alpha"]))
        self.rounded = main.rounded
        t.configure(bg=KEY if self.rounded else BG)
        if self.rounded:
            try:
                t.attributes("-transparentcolor", KEY)
            except Exception:
                self.rounded = False
                t.configure(bg=BG)
        t.geometry(f"+{cfg[self.POS[0]]}+{cfg[self.POS[1]]}")
        self.canvas = tk.Canvas(t, width=420, height=300, bg=KEY if self.rounded else BG, highlightthickness=0)
        self.canvas.pack()
        self._sig = None
        self.build()
        if not getattr(self, "_wheel", False):          # колесо мыши — масштаб окна (как у карты)
            self._wheel = True
            self.canvas.bind("<MouseWheel>", lambda e, k="scale_" + self.ON: self.wheel(k, 0.05 if e.delta > 0 else -0.05))
        for w in (t, self.canvas):
            w.bind("<ButtonPress-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)
        self.canvas.bind("<Motion>", lambda e: set_edge_cursor(self.canvas, self.main.locked, e))
        self._stretch_hooks()
        if not cfg.get(self.ON, True):
            t.withdraw()

    def _stretch_hooks(self):
        """Независимое растяжение по ширине и высоте: после каждой перерисовки и перестройки подгоняем размер."""
        c = self.canvas
        od, ob, ot = c.delete, self.build, self.tick
        self._fresh = False

        def delete(*a, **k):
            if a and a[0] == "all":
                self._fresh = True
            return od(*a, **k)

        def build(*a, **k):
            r = ob(*a, **k)
            self.stretch_size()
            return r

        def tick(*a, **k):
            r = ot(*a, **k)
            if self._fresh:
                self._fresh = False
                self.stretch_items()
            return r
        c.delete, self.build, self.tick = delete, build, tick
        self.stretch_size()

    def stretch_size(self):
        stretch_apply(self.canvas, self.main._m.cfg, self.ON, self.width, self.height, False)

    def stretch_items(self):
        stretch_apply(self.canvas, self.main._m.cfg, self.ON, self.width, self.height, True)

    def wheel(self, key, d):
        m = self.main._m
        m.cfg[key] = round(max(0.5, min(3.0, float(m.cfg.get(key, 1.0)) + d)), 2)
        self.build()
        m.save()

    def opf(self):
        """Множитель прозрачности для окон со своей логикой прозрачности (радар, огни, лимитер)."""
        try:
            return max(0.1, min(1.0, float(self.main.cfg.get("op_" + self.ON, 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def build(self):
        m = self.main
        self.cw, self.rh, self.R = m.cw, m.rh, m.R if self.rounded else 0
        self.ahead = max(0, min(8, int(m.cfg.get("rel_ahead", self.AHEAD))))
        self.behind = max(0, min(8, int(m.cfg.get("rel_behind", self.BEHIND))))
        hide = set(m.cfg.get("rel_hide", []))
        if getattr(m, "game", "") != "iracing":
            hide.add("irat")                                    # iRating и лицензия есть только в iRacing
        self.h_title, self.h_foot = int(self.rh * 1.5), int(self.rh * 0.45)
        self.x_of, self.w_of, x = {}, {}, 14
        for key, w, _ in self.COLS:
            if key in hide:
                continue
            self.x_of[key], self.w_of[key] = x, int(w * self.cw)
            x += int(w * self.cw) + int(self.cw * 0.9)
        self.width = max(x + 6, int(self.cw * 24))
        self.height = self.h_title + self.rh * (self.ahead + 1 + self.behind) + self.h_foot
        self.canvas.config(width=self.width, height=self.height)
        self._sig = None

    def fit_on_screen(self):
        """Если окно оказалось за краем экрана — возвращаем его в видимую область."""
        try:
            bx, by, bw, bh = screen_bounds(self.top)
            x, y = int(self.main.cfg[self.POS[0]]), int(self.main.cfg[self.POS[1]])
        except (TypeError, ValueError, KeyError):
            return
        nx, ny = max(bx, min(x, bx + bw - self.width)), max(by, min(y, by + bh - self.height))
        if (nx, ny) != (x, y):
            self.main.cfg[self.POS[0]], self.main.cfg[self.POS[1]] = nx, ny
            self.top.geometry(f"+{nx}+{ny}")

    def drag_start(self, e):
        if self.main.locked:
            return
        z = grip_zone(self.canvas, e)
        self._resizing = z is not None
        if self._resizing:
            self.main._m.resize_begin(self.ON, self.canvas, e, self.top, z)
            return
        self._dx, self._dy = e.x_root - self.top.winfo_x(), e.y_root - self.top.winfo_y()

    def drag_move(self, e):
        if self.main.locked:
            return
        if getattr(self, "_resizing", False):
            self.main._m.resize_drag(e)
            return
        self.top.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def drag_end(self, e):
        if getattr(self, "_resizing", False):
            self._resizing = False
            self.main._m.resize_end()
        else:
            self.main.save()

    def panel(self, y1, y2, fill, top=False, bottom=False):
        W, R, c = self.width, self.R, self.canvas
        if R and (top or bottom):
            draw_rrect(c, 0, y1, W - 1, y2, R, fill=fill)
            if top and not bottom:
                c.create_rectangle(0, y1 + R, W, y2, fill=fill, outline="")
            elif bottom and not top:
                c.create_rectangle(0, y1, W, y2 - R, fill=fill, outline="")
        else:
            c.create_rectangle(0, y1, W, y2, fill=fill, outline="")

    def text(self, key, y, s, fill=FG, font=None):
        _, _, anchor = next(c for c in self.COLS if c[0] == key)
        x = self.x_of[key] + (self.w_of[key] if anchor == "e" else 0)
        self.canvas.create_text(x, y, text=s, fill=fill, anchor=anchor, font=font or self.main.font)

    def tick(self, frame, ver):
        sig = (ver, self.main.cfg["font"], self.main.locked, int(time.time() * 4))
        if sig != self._sig:
            self._sig = sig
            self.draw(frame)

    def draw(self, frame):
        m, c, rh, W = self.main, self.canvas, self.rh, self.width
        c.delete("all")
        rel = relative_cars(frame, self.ahead, self.behind)
        pos = {}
        if frame:
            for i, car in enumerate(order_cars(frame["cars"], frame["kind"] in TIMED_KINDS)):
                pos[car["key"]] = i + 1

        # ---------- шапка ----------
        self.panel(0, self.h_title, HDR_BG, top=True)
        cy = self.h_title // 2
        c.create_rectangle(14, cy - int(rh * 0.42), 18, cy + int(rh * 0.42), fill=MARK, outline="")
        c.create_text(28, cy, text="РЕЛАТИВ", fill=FG, anchor="w", font=m.bold)
        hint = "РЕЖИМ ПЕРЕМЕЩЕНИЯ" if not m.locked else "ПО ТРАССЕ"
        c.create_text(W - 14, cy, text=hint, fill=ACCENT if not m.locked else DIM, anchor="e", font=m.small)
        c.create_rectangle(0, self.h_title - 3, W, self.h_title, fill=ACCENT, outline="")

        # ---------- строки ----------
        slots = ([None] * (self.ahead - len(rel[1])) + list(rel[1]) + ["me"] + list(rel[2]) +
                 [None] * (self.behind - len(rel[2]))) if rel else [None] * (self.ahead + 1 + self.behind)
        y = self.h_title
        for i, slot in enumerate(slots):
            cyr = y + rh // 2
            is_me = slot == "me"
            c.create_rectangle(0, y, W, y + rh, fill=ME_BG if is_me else (ROW_A if i % 2 == 0 else ROW_B), outline="")
            if is_me:
                c.create_rectangle(0, y, W - 1, y + rh, outline=ME_EDGE)
            if slot is not None:
                car, gap, lapflag = (rel[0], 0.0, 0) if is_me else slot
                ccol = class_color(car["cls"])
                c.create_rectangle(0, y + 3, 5, y + rh - 3, fill=ccol, outline="")
                if "pos" in self.x_of:
                    self.text("pos", cyr, str(pos.get(car["key"], "")), FG, m.bold)
                if "num" in self.x_of:
                    nx = self.x_of["num"]
                    draw_rrect(c, nx - 4, y + 5, nx + self.w_of["num"] + 6, y + rh - 5, 5, fill=BADGE, outline=BORDER)
                    c.create_rectangle(nx - 4, y + 9, nx - 1, y + rh - 9, fill=ccol, outline="")
                    self.text("num", cyr, car["number"] or "-", FG, m.bold)
                self.text("name", cyr, car["driver"].upper()[:20], FG, m.bold if is_me else m.font)
                if "irat" in self.x_of:
                    draw_irat(c, car, self.x_of["irat"] + self.w_of["irat"], cyr, m.small, rh)
                if is_me:
                    self.text("gap", cyr, "ВЫ", ACCENT, m.bold)
                else:
                    gcol = "#ff7a7a" if lapflag > 0 else "#6ab7ff" if lapflag < 0 else FG   # красный — круг впереди, синий — позади
                    self.text("gap", cyr, fmt_rel_gap(gap), gcol)
                if "tag" in self.x_of and (car["in_pits"] or car["garage"]):
                    self.text("tag", cyr, "PIT", ACCENT, m.bold)
            y += rh

        # ---------- подвал / сообщения ----------
        self.panel(y, y + self.h_foot, HDR_BG, bottom=True)
        if rel is None:
            msg = "жду данные игры" if not frame else "вас нет на трассе"
            c.create_text(W // 2, self.h_title + rh * max(1, self.ahead) + rh // 2, text=msg, fill=DIM, font=m.small)
        if self.R:
            draw_rrect(c, 0, 0, W - 1, self.height - 1, self.R, outline=BORDER)
        else:
            c.create_rectangle(0, 0, W - 1, self.height - 1, outline=BORDER)


def wheel_geometry(cx, cy, r, angle_deg):
    """Точки для значка руля: концы трёх спиц и красной метки обода (угол — по часовой стрелке от верха)."""
    def pt(a, k):
        rad = math.radians(a)
        return (cx + r * k * math.sin(rad), cy - r * k * math.cos(rad))
    a = angle_deg
    return {"spokes": [(pt(a - 90, 0.86)), (pt(a + 90, 0.86)), (pt(a + 180, 0.86))],
            "mark": (pt(a, 0.80), pt(a, 1.0))}


class PedalsWindow(RelativeWindow):
    """Окно ввода: передача, скорость, руль, график газа/тормоза, столбики педалей."""
    POS, ON = ("ped_x", "ped_y"), "pedals"
    SECONDS = 8.0                                   # сколько секунд показывает график
    THR, BRK, ABS, CLU = "#2ee26d", "#ff3b30", "#ffc107", "#4a90ff"

    def build(self):
        m = self.main
        self.cw, self.rh, self.R = m.cw, m.rh, m.R if self.rounded else 0
        u, sz, fam = self.rh, m.cfg["font"], m.family
        self.u = u
        try:
            self.seconds = max(2.0, min(30.0, float(m.cfg.get("ped_seconds", self.SECONDS))))
        except (TypeError, ValueError):
            self.seconds = self.SECONDS
        self.f_gear = tkfont.Font(family=fam, size=int(sz * 3.2), weight="bold")
        self.f_speed = tkfont.Font(family=fam, size=int(sz * 1.7), weight="bold")
        self.led_h = int(u * 0.62) if m.cfg.get("shiftlight", True) else 0
        self.height = int(u * 3.3) + self.led_h
        self.gear_w, self.speed_w = int(u * 1.7), int(u * 2.5)
        self.wheel_d = int(self.height * 0.62)
        self.graph_w = int(u * 8.2)
        self.bar_w, self.bar_gap = int(u * 0.55), int(u * 0.14)
        hide = m.cfg.get("ped_hide", [])
        self.hide = set(hide) if isinstance(hide, list) else set()
        st = m.cfg.get("wheel_style", "round")
        self.style = st if st in ("round", "formula", "gt", "rally", "bar") else "round"
        self.wheel_w = int(self.wheel_d * 2.0) if self.style == "bar" else self.wheel_d
        w = 14
        if "gear" not in self.hide:
            w += self.gear_w
        if "speed" not in self.hide:
            w += self.speed_w
        if "wheel" not in self.hide:
            w += self.wheel_w + 20
        if "graph" not in self.hide:
            w += self.graph_w + 12
        if "bars" not in self.hide:
            w += 3 * self.bar_w + 2 * self.bar_gap
        self.width = max(w + 14, int(u * 7.0))
        self.canvas.config(width=self.width, height=self.height)
        self._sig = None

    def tick(self, frame, ver):
        sig = (ver, self.main.cfg["font"], self.main.locked, self.led_h, int(time.time() * 20), tuple(sorted(self.hide)), self.style)
        if sig != self._sig:
            self._sig = sig
            self.draw(frame)

    def draw(self, frame):
        m, c, W, H, u, R = self.main, self.canvas, self.width, self.height, self.u, self.R
        c.delete("all")
        if R:
            draw_rrect(c, 0, 0, W - 1, H - 1, R, fill=HDR_BG, outline=BORDER)
        else:
            c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline=BORDER)
        inp = frame.get("inputs") if frame else None
        me = next((x for x in frame["cars"] if x["player"]), None) if frame else None
        L = self.led_h
        cy = L + (H - L) // 2

        # ---------- шифт-лайт ----------
        if L:
            rpm, mx = (inp["rpm"], inp["max_rpm"]) if inp else (0.0, 0.0)
            frac = rpm / mx if mx else 0.0
            n, x0, x1 = 15, 14, W - 14
            gap = 4
            lw_ = (x1 - x0 - gap * (n - 1)) / n
            try:
                s0 = float(m.cfg.get("shift_start", 82)) / 100.0
                f0 = float(m.cfg.get("shift_flash", 98.5)) / 100.0
            except (TypeError, ValueError):
                s0, f0 = 0.82, 0.985
            f0 = max(f0, s0 + 0.02)
            blink = frac >= f0 and int(time.time() * 8) % 2 == 0
            for i in range(n):
                th = s0 + (f0 - 0.005 - s0) * i / (n - 1)
                col = "#2ee26d" if i < 5 else "#ff3b30" if i < 10 else "#4a90ff"
                lit = frac >= th
                if frac >= f0:
                    lit = blink
                lx = x0 + i * (lw_ + gap)
                c.create_rectangle(lx, 8, lx + lw_, L - 4, fill=col if lit else TRACK, outline="")

        hide = self.hide
        # ---------- передача и скорость ----------
        x = 14
        if "gear" not in hide:
            gear = "-" if not inp else "R" if inp["gear"] < 0 else "N" if inp["gear"] == 0 else str(inp["gear"])
            c.create_text(x + self.gear_w // 2, cy, text=gear, fill=ACCENT if inp else DIM, font=self.f_gear)
            x += self.gear_w
        if "speed" not in hide:
            mph = bool(m.cfg.get("speed_mph", False))
            kph = f"{me['speed'] * (2.23694 if mph else 3.6):.0f}" if me else "0"
            sx = x + self.speed_w // 2
            c.create_text(sx, cy - int(u * 0.28), text=kph, fill=FG, font=self.f_speed)
            c.create_line(sx - int(u * 0.5), cy + int(u * 0.22), sx + int(u * 0.5), cy + int(u * 0.22), fill=BORDER)
            c.create_text(sx, cy + int(u * 0.5), text="MPH" if mph else "KPH", fill=DIM, font=m.small)
            x += self.speed_w

        # ---------- руль ----------
        if "wheel" not in hide:
            rng = (inp["range"] if inp and inp["range"] else 540.0)
            angle = (inp["steer"] * rng / 2.0) if inp else 0.0
            self.draw_wheel(c, x, cy, angle, inp["steer"] if inp else 0.0, rng)
            x += self.wheel_w + 20

        # ---------- график ----------
        if "graph" not in hide:
            gx0, gx1, gy0, gy1 = x, x + self.graph_w, L + 10, H - 10
            c.create_rectangle(gx0, gy0, gx1, gy1, fill=BG, outline=BORDER)
            for k in range(1, 4):                                      # горизонтальные пунктиры 25/50/75 %
                yy = gy1 - (gy1 - gy0) * k / 4
                c.create_line(gx0, yy, gx1, yy, fill=BORDER, dash=(3, 4))
            for k in range(1, 4):                                      # вертикальные линии
                xx = gx0 + (gx1 - gx0) * k / 4
                c.create_line(xx, gy0, xx, gy1, fill=BORDER)
            now = time.monotonic()
            t0 = now - self.seconds
            with lock:
                hist = [h for h in state["history"] if h[0] >= t0 - 0.3]
            span = gx1 - gx0
            px = lambda t: gx0 + max(0.0, min(1.0, (t - t0) / self.seconds)) * span
            py = lambda v: gy1 - 2 - v * (gy1 - gy0 - 4)
            if len(hist) >= 2:
                c.create_line(*[v for h in hist for v in (px(h[0]), py(h[1]))], fill=self.THR, width=2)
                if any(h[3] > 0.02 for h in hist):                      # сцепление рисуем, только если им пользуются
                    c.create_line(*[v for h in hist for v in (px(h[0]), py(h[3]))], fill=self.CLU, width=2)
                run, run_abs = [], hist[0][4]
                for h in hist:
                    if h[4] != run_abs and run:                        # ABS меняет цвет участка тормозной линии
                        run.append((px(h[0]), py(h[2])))
                        if len(run) >= 2:
                            c.create_line(*[v for p in run for v in p], fill=self.ABS if run_abs else self.BRK, width=2)
                        run, run_abs = [], h[4]
                    run.append((px(h[0]), py(h[2])))
                if len(run) >= 2:
                    c.create_line(*[v for p in run for v in p], fill=self.ABS if run_abs else self.BRK, width=2)
            elif not inp:
                c.create_text((gx0 + gx1) // 2, cy, text="жду данные игры", fill=DIM, font=m.small)
            x = gx1 + 12

        # ---------- столбики: сцепление, тормоз, газ ----------
        if "bars" not in hide:
            vals = [(inp["clu"] if inp else 0.0, self.CLU), (inp["brk"] if inp else 0.0, self.BRK),
                    (inp["thr"] if inp else 0.0, self.THR)]
            top, bot = L + 10 + int(u * 0.55), H - 10
            for v, col in vals:
                c.create_rectangle(x, top, x + self.bar_w, bot, fill=TRACK, outline="")
                if v > 0.005:
                    c.create_rectangle(x, bot - int((bot - top) * v), x + self.bar_w, bot, fill=col, outline="")
                c.create_text(x + self.bar_w // 2, L + 10 + int(u * 0.27), text=f"{v * 100:.0f}",
                              fill=FG if v > 0.005 else DIM, font=m.small)
                x += self.bar_w + self.bar_gap

    def draw_wheel(self, c, x, cy, angle, steer, rng):
        """Руль выбранного вида; angle — поворот в градусах по часовой стрелке."""
        G = "#b9bcc2"
        r = self.wheel_d // 2
        lw = max(4, int(r * 0.17))
        if self.style == "bar":                                    # полоса: заливка от центра в сторону поворота
            w, h = self.wheel_w, max(8, int(r * 0.5))
            x0, x1 = x + 2, x + 2 + w
            mid = (x0 + x1) // 2
            c.create_rectangle(x0, cy - h // 2, x1, cy + h // 2, fill=TRACK, outline="")
            xe = mid + max(-1.0, min(1.0, steer)) * (w // 2)
            c.create_rectangle(min(mid, xe), cy - h // 2, max(mid, xe), cy + h // 2, fill="#e5322d", outline="")
            c.create_line(mid, cy - h // 2 - 3, mid, cy + h // 2 + 3, fill=G, width=2)
            c.create_text(mid, cy + h // 2 + int(self.u * 0.5), text=f"{angle:+.0f}°", fill=DIM, font=self.main.small)
            return
        wx = x + r + 2
        a = math.radians(angle)
        ca, sa = math.cos(a), math.sin(a)
        T = lambda px, py: (wx + r * (px * ca - py * sa), cy + r * (px * sa + py * ca))   # точка в долях радиуса -> экран

        def poly(pts, **kw):
            c.create_polygon(*[v for p in pts for v in T(*p)], **kw)

        S, HOLE = "#7c8088", HDR_BG

        def poly(pts, fill=S, smooth=False, outline=""):
            c.create_polygon(*[v for p in pts for v in T(*p)], fill=fill, outline=outline, smooth=smooth)

        def dot(px, py, rr, fill=HOLE):
            x0, y0 = T(px, py)
            c.create_oval(x0 - r * rr, y0 - r * rr, x0 + r * rr, y0 + r * rr, fill=fill, outline="")

        if self.style == "formula":
            # плоский формульный руль: корпус с хватами, экран, кнопки и поворотные переключатели
            poly([(-0.78, -0.62), (-0.45, -0.74), (0.45, -0.74), (0.78, -0.62), (0.95, -0.3), (1.0, 0.35), (0.96, 0.82),
                  (0.84, 0.88), (0.74, 0.8), (0.72, 0.5), (0.55, 0.46), (-0.55, 0.46), (-0.72, 0.5), (-0.74, 0.8),
                  (-0.84, 0.88), (-0.96, 0.82), (-1.0, 0.35), (-0.95, -0.3)], smooth=True)
            poly([(-0.42, -0.5), (0.42, -0.5), (0.42, -0.02), (-0.42, -0.02)], fill=HOLE)
            poly([(-0.36, -0.44), (0.36, -0.44), (0.36, -0.08), (-0.36, -0.08)], fill=S)
            poly([(-0.33, -0.41), (0.33, -0.41), (0.33, -0.11), (-0.33, -0.11)], fill=HOLE)
            for px, py in ((-0.7, -0.2), (-0.7, 0.12), (0.7, -0.2), (0.7, 0.12)):
                dot(px, py, 0.1)
            dot(-0.3, 0.24, 0.12)
            dot(0.3, 0.24, 0.12)
            dot(-0.68, -0.52, 0.05)
            dot(0.68, -0.52, 0.05)
        elif self.style == "gt":
            # GT: массивная пластина со срезом снизу, ступица и два больших окна
            pts = []
            for i in range(41):
                t = 2 * math.pi * i / 40
                pts.append((math.sin(t), min(0.7, -math.cos(t))))
            poly(pts, smooth=False)
            poly([(-0.88, -0.2), (-0.62, -0.58), (-0.28, -0.4), (-0.3, 0.12), (-0.5, 0.42), (-0.82, 0.36)], fill=HOLE, smooth=True)
            poly([(0.88, -0.2), (0.62, -0.58), (0.28, -0.4), (0.3, 0.12), (0.5, 0.42), (0.82, 0.36)], fill=HOLE, smooth=True)
            dot(0, 0.02, 0.27, S)
            dot(0, 0.02, 0.22, HOLE)
            dot(0, 0.02, 0.18, S)
            dot(0, -0.62, 0.05)
            dot(-0.12, 0.5, 0.05)
            dot(0.12, 0.5, 0.05)
        elif self.style == "rally":
            # ралли: круглый обод с жёлтой меткой сверху, ступица с четырьмя отверстиями и три спицы
            c.create_oval(wx - r, cy - r, wx + r, cy + r, outline=S, width=max(4, int(r * 0.24)))
            for ang in (-90, 90, 180):
                e = T(math.sin(math.radians(ang)) * 0.9, -math.cos(math.radians(ang)) * 0.9)
                c.create_line(wx, cy, *e, fill=S, width=max(4, int(r * 0.26)))
            poly([(-0.42, -0.3), (-0.2, -0.46), (0.2, -0.46), (0.42, -0.3), (0.42, 0.3), (0.2, 0.46), (-0.2, 0.46), (-0.42, 0.3)], smooth=True)
            dot(0, 0, 0.12)
            for px, py in ((-0.27, -0.27), (0.27, -0.27), (-0.27, 0.27), (0.27, 0.27)):
                dot(px, py, 0.05)
            poly([(-0.1, -1.04), (0.1, -1.04), (0.1, -0.78), (-0.1, -0.78)], fill="#ffb300")
        else:                                                      # классика
            geo = wheel_geometry(wx, cy, r, angle)
            c.create_oval(wx - r, cy - r, wx + r, cy + r, outline=G, width=lw)
            for (ex, ey) in geo["spokes"]:
                c.create_line(wx, cy, ex, ey, fill=G, width=max(3, lw - 1))
            c.create_oval(wx - 4, cy - 4, wx + 4, cy + 4, fill=G, outline="")
            (x1, y1), (x2, y2) = geo["mark"]
            c.create_line(x1, y1, x2, y2, fill="#e5322d", width=lw)
            return


class MapWindow(RelativeWindow):
    """Карта трассы: рисуется по вашему проезду, затем уточняется; на ней все машины.
    Три вида: полная карта, линейная (круг как полоса) и местная (участок вокруг вашей машины)."""
    POS, ON = ("map_x", "map_y"), "trackmap"

    def mode(self):
        v = self.main.cfg.get("map_mode", "full")
        return v if v in ("full", "linear", "local") else "full"

    def build(self):
        m = self.main
        self.cw, self.rh, self.R = m.cw, m.rh, m.R if self.rounded else 0
        self.h_title, self.h_foot = int(self.rh * 1.4), int(self.rh * 0.95)
        self.side = int(self.rh * 11.6 * max(0.5, min(3.0, float(m.cfg.get("map_scale", 1.0)))))
        self._mode = self.mode()
        self._bare = (not m.cfg.get("map_frame", True)) and self._mode != "linear"
        self.gx = 0 if self._bare else 14
        if self._bare:
            self.h_title = self.h_foot = 0
        self.f_num = tkfont.Font(family=m.family, size=max(8, int(m.cfg["font"] * 0.9)), weight="bold")
        if not getattr(self, "_wheel", False):
            self._wheel = True
            self.canvas.bind("<MouseWheel>", lambda e: m.scale_map(0.1 if e.delta > 0 else -0.1))
        if self._mode == "linear":
            self.width = int(self.side * 1.55) + 28
            self.lane_h = int(self.rh * 1.0)
            self.height = self.h_title + int(self.rh * 2.1) + 3 * self.lane_h + self.h_foot
        elif self._bare:
            self.width, self.height = self.side, self.side
        else:
            self.width = self.side + 28
            self.height = self.h_title + self.side + self.h_foot
        self.canvas.config(width=self.width, height=self.height)
        self._sig = None

    def tick(self, frame, ver):
        cf = self.main.cfg
        if self.mode() != self._mode or ((not cf.get("map_frame", True)) and self._mode != "linear") != self._bare:
            self.build()
        sig = (cf.get("map_style", "classic"), cf.get("map_one_color", False), cf.get("map_frame", True), ver // 2, cf["font"], self.main.locked, cf.get("map_rot", 0), cf.get("map_mirror", False),
               cf.get("map_radius", 500), cf.get("map_headup", True), int(time.time() * 8))
        if sig != self._sig:
            self._sig = sig
            self.draw(frame)

    def draw_wind(self, c, cx, cy, r, wind, rot, mirror):
        """Круглый компас ветра: стрелка показывает, куда дует, цвет и подпись — сила."""
        m = self.main
        c.create_oval(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3, fill="#12171f", outline=BORDER, width=2)
        for i in range(12):                                   # метки по кругу
            a = math.radians(30 * i)
            r1 = r - (4 if i % 3 else 7)
            c.create_line(cx + math.sin(a) * r, cy - math.cos(a) * r, cx + math.sin(a) * r1, cy - math.cos(a) * r1,
                          fill="#4b5563" if i % 3 else "#8b95a5", width=1 if i % 3 else 2)
        spd = 0.0
        if wind:
            wx, wz = wind
            spd = math.hypot(wx, wz)
        if spd < 0.3:
            c.create_text(cx, cy, text="штиль", fill=DIM, font=m.small)
            return
        (vx, vy), = self.transform([wind], rot, mirror)
        th = math.atan2(vx, -vy)                              # угол по часовой от «вверх»
        col = (_mix("#6cb8ff", "#5fe0a0", spd / 3.0) if spd < 3.0 else _mix("#5fe0a0", "#ffd23a", (spd - 3.0) / 5.0)
               if spd < 8.0 else _mix("#ffd23a", "#ff6a3d", (spd - 8.0) / 6.0))
        shape = [(0, -0.80), (0.32, -0.30), (0.13, -0.30), (0.13, 0.58), (0, 0.42), (-0.13, 0.58),
                 (-0.13, -0.30), (-0.32, -0.30)]
        ct, st = math.cos(th), math.sin(th)
        pts = []
        for px, py in shape:
            px, py = px * r, py * r
            pts += [cx + px * ct - py * st, cy + px * st + py * ct]
        c.create_polygon(*pts, fill=col, outline="#0d1117", width=1)
        c.create_text(cx, cy + r + 13, text=f"{spd:.1f} м/с", fill=col, font=m.small)

    def transform(self, pts, rot, mirror):
        """Мировые (x, z) -> экран: поворот на rot*90°, +z вверх, по желанию зеркально."""
        a = math.radians(90 * rot)
        ca, sa = math.cos(a), math.sin(a)
        out = []
        for x, z in pts:
            rx, rz = x * ca - z * sa, x * sa + z * ca
            out.append((-rx if mirror else rx, -rz))
        return out

    def draw_marks(self, c, sp, sec, visible=None, local_k=1.0):
        """Сектора (цветные участки и границы) и отметка старта/финиша на ломаной sp (экранные точки)."""
        m = self.main
        n_ = len(sp)
        ok = visible or (lambda i: True)

        def tick(i, half, col, wd):
            i = max(0, min(n_ - 2, i))
            (xa, ya), (xb, yb) = sp[i], sp[i + 1]
            dx, dy = xb - xa, yb - ya
            n = math.hypot(dx, dy) or 1.0
            px, py = -dy / n * half, dx / n * half
            c.create_line(xa - px, ya - py, xa + px, ya + py, fill=col, width=wd)
            return xa, ya, dx / n, dy / n
        if sec and m.cfg.get("map_sectors", True):
            cuts = [0, int(sec[0] * n_), int(sec[1] * n_), n_]
            for j in range(0 if self.black() else 3):               # участки секторов тонкой линией поверх трассы (в чёрном стиле не рисуем)
                run = []
                for i in range(cuts[j], min(n_, cuts[j + 1] + 1)):
                    if ok(i):
                        run.append(sp[i])
                    elif len(run) >= 2:
                        c.create_line(*[v for p in run for v in p], fill=SEC_COL[j], width=2, capstyle="round", joinstyle="round")
                        run = []
                    else:
                        run = []
                if len(run) >= 2:
                    c.create_line(*[v for p in run for v in p], fill=SEC_COL[j], width=2, capstyle="round", joinstyle="round")
            for j, f in enumerate(sec):
                i = int(f * n_)
                if ok(i):
                    xa, ya, ux, uy = tick(i, 11, SEC_COL[j + 1], 4)
                    c.create_text(xa - uy * 20, ya + ux * 20, text=f"S{j + 2}", fill=SEC_COL[j + 1], font=self.f_num)
        if ok(0):
            x0, y0, ux, uy = tick(0, 13, "#ffffff", 6)
            tick(0, 13, "#111111", 2)
            c.create_text(x0 + uy * 34, y0 - ux * 34, text="СТАРТ", fill=FG, font=self.f_num)

    def black(self):
        return self.main.cfg.get("map_style", "classic") == "black"

    def car_col(self, car):
        if self.black():
            return "#ffffff"
        return "#e8eaee" if self.main.cfg.get("map_one_color", False) else class_color(car["cls"])

    def track_line(self, c, flat, closed_pt=None):
        """Полотно трассы: классика (серое) или чёрная с белой обводкой."""
        black = self.black()
        extra = list(closed_pt) if closed_pt else []
        c.create_line(*flat, *extra, fill="#ffffff" if black else "#2f3745", width=16 if black else 14,
                      capstyle="round", joinstyle="round")
        c.create_line(*flat, *extra, fill="#1b1b1b" if black else "#c5cad3", width=13 if black else 5,
                      capstyle="round", joinstyle="round")

    def draw_cars(self, c, items, gx0, gy0, side, clip=None):
        """items: [(машина, x, y)]; игрок рисуется последним. Номер места — внутри точки."""
        m = self.main
        nums = m.cfg.get("map_nums", True)
        for car, sx, sy in sorted(items, key=lambda t: t[0]["player"]):
            if not (gx0 <= sx <= gx0 + side and gy0 <= sy <= gy0 + side):
                continue
            if clip and not clip(sx, sy):
                continue
            pl = car["place"] if car["place"] > 0 else None
            blk = self.black()
            if car["player"]:
                pr = int(m.cfg.get("map_dot", 6)) + (7 if blk else 4)
                if nums:
                    pr = max(pr, 19 if blk else 15)
                if blk:                                         # игрок — зелёный, кружок крупнее
                    c.create_oval(sx - pr, sy - pr, sx + pr, sy + pr, fill="#22c55e", outline="#ffffff", width=3)
                else:
                    c.create_oval(sx - pr, sy - pr, sx + pr, sy + pr, fill=FG, outline=ACCENT, width=3)
                if nums and pl:
                    c.create_text(sx, sy, text=str(pl), fill="#000000", font=self.f_num)
            else:
                r = int(m.cfg.get("map_dot", 6))
                if nums:
                    r = max(r, 15 if blk else 13)
                c.create_oval(sx - r, sy - r, sx + r, sy + r, fill=self.car_col(car), outline="#0a0a0a" if blk else BG, width=2)
                if nums and pl:
                    onw = blk or m.cfg.get("map_one_color", False)
                    c.create_text(sx, sy, text=str(pl), fill="#000000" if onw else "#ffffff", font=self.f_num)

    def draw(self, frame):
        m, c, W, H = self.main, self.canvas, self.width, self.height
        c.delete("all")
        panel = HDR_BG
        bare = self._bare
        if bare:
            if self.rounded:                                    # без рамки фон всегда прозрачный: служебный цвет окна не рисуется
                pass
            else:
                c.create_rectangle(0, 0, W, H, fill="#0a0a0a" if self.black() else BG, outline="")
        elif self.R:
            draw_rrect(c, 0, 0, W - 1, H - 1, self.R, fill=panel, outline=BORDER)
        else:
            c.create_rectangle(0, 0, W - 1, H - 1, fill=panel, outline=BORDER)
        RUNTIME["ir_svg"] = bool(m.cfg.get("map_ir_svg", True))
        loop, partial, status, laps, track_name, sec = mapper.snapshot()
        rot, mirror = int(m.cfg.get("map_rot", 0)) % 4, bool(m.cfg.get("map_mirror", False))
        mode = self._mode
        me = next((x for x in (frame["cars"] if frame else []) if x["player"]), None)

        # ---------- шапка ----------
        if not bare:
            cy = self.h_title // 2
            c.create_rectangle(14, cy - int(self.rh * 0.42), 18, cy + int(self.rh * 0.42), fill=MARK, outline="")
            title = {"full": "КАРТА", "linear": "КРУГ", "local": f"КАРТА  {int(m.cfg.get('map_radius', 500))} м"}[mode]
            c.create_text(28, cy, text=title, fill=FG, anchor="w", font=m.bold)
            name = (track_name or "").upper()
            c.create_text(W - 14, cy, text=name[:26], fill=DIM, anchor="e", font=m.small)
        if mode == "linear":
            self.draw_linear(c, frame, me, sec, W)
            return
        gx0, gy0 = self.gx, self.h_title
        side = self.side
        if not bare:
            c.create_rectangle(gx0, gy0, gx0 + side, gy0 + side, fill="#0a0a0a" if self.black() else BG, outline=BORDER)

        # ---------- расчёт масштаба ----------
        base = loop if loop else partial
        cars = [x for x in (frame["cars"] if frame else []) if not x["garage"] and x["pos"] != (0.0, 0.0)]
        if mode == "local" and len(base) >= 2 and me is not None and me["pos"] != (0.0, 0.0):
            self.draw_local(c, base, bool(loop), sec, cars, me, gx0, gy0, side, rot, mirror)
        elif len(base) >= 2:
            tp = self.transform(base, rot, mirror)
            xs, ys = [p[0] for p in tp], [p[1] for p in tp]
            minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
            margin = 14
            span = max(maxx - minx, maxy - miny, 1.0)
            k = (side - 2 * margin) / span
            ox = gx0 + side / 2 - (minx + maxx) / 2 * k
            oy = gy0 + side / 2 - (miny + maxy) / 2 * k
            to_screen = lambda pts: [(ox + x * k, oy + y * k) for x, y in pts]
            sp = to_screen(tp)
            flat = [v for p in sp for v in p]
            if loop:
                self.track_line(c, flat, sp[0])
                self.draw_marks(c, sp, sec)
            else:
                step = max(1, len(sp) // 700)
                c.create_line(*[v for p in sp[::step] for v in p], fill=ACCENT, width=3, capstyle="round", joinstyle="round")
            cp = to_screen(self.transform([x["pos"] for x in cars], rot, mirror)) if cars else []
            self.draw_cars(c, [(car, sx, sy) for car, (sx, sy) in zip(cars, cp)], gx0, gy0, side)
        else:
            msg = "жду данные игры" if not frame else "проедьте круг — карта нарисуется по вашей траектории"
            c.create_text(gx0 + side // 2, gy0 + side // 2, text=msg, fill=DIM, font=m.small, width=side - 30, justify="center")

        # ---------- ветер ----------
        if m.cfg.get("map_wind", True):
            self.draw_wind(c, gx0 + side - 30, gy0 + 30, 21, frame.get("wind") if frame else None, rot, mirror)

        # ---------- подвал ----------
        if bare:
            return
        fy = gy0 + side + self.h_foot // 2
        if status == "ready" and mapper.sources == "схема iRacing":
            txt, col = "схема трассы iRacing", DIM
        elif status == "ready":
            txt, col = f"карта готова  •  кругов в карте: {laps}/{TrackMapper.MAX_LAPS}", DIM
        elif status == "recording":
            txt, col = "запись карты: проедьте круг без боксов", ACCENT
        else:
            txt, col = "карта пока не записана", DIM
        c.create_text(gx0, fy, text=txt, fill=col, anchor="w", font=m.small)

    def draw_local(self, c, base, is_loop, sec, cars, me, gx0, gy0, side, rot, mirror):
        """Только участок вокруг игрока радиусом map_radius метров; можно крутить так, чтобы «вперёд» было вверх."""
        m = self.main
        R = max(100.0, float(m.cfg.get("map_radius", 500)))
        cx, cy = gx0 + side / 2, gy0 + side / 2
        rr = side / 2 - 8
        k = rr / R
        px, pz = me["pos"]
        headup = bool(m.cfg.get("map_headup", True))
        fx, fz = 0.0, 1.0
        if headup:                                              # направление — по движению машины (как у радара)
            last = getattr(self, "_hp", None)
            if last is None or math.hypot(px - last[0], pz - last[1]) > 120.0:
                self._hp = (px, pz)
            elif math.hypot(px - last[0], pz - last[1]) >= 1.5:
                dx, dz = px - last[0], pz - last[1]
                n = math.hypot(dx, dz)
                old = getattr(self, "_hd", None)
                nx, nz = dx / n, dz / n
                if old:
                    nx, nz = old[0] * 0.4 + nx * 0.6, old[1] * 0.4 + nz * 0.6
                    n2 = math.hypot(nx, nz) or 1.0
                    nx, nz = nx / n2, nz / n2
                self._hd, self._hp = (nx, nz), (px, pz)
            if getattr(self, "_hd", None):
                fx, fz = self._hd

        def proj(x, z):
            dx, dz = x - px, z - pz
            if headup:
                return cx + (dx * fz - dz * fx) * k, cy - (dx * fx + dz * fz) * k
            (tx, ty), = self.transform([(dx, dz)], rot, mirror)
            return cx + tx * k, cy + ty * k
        c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill="" if (self._bare and self.rounded) else ("#0a0a0a" if self.black() else "#0e1218"),
                    outline=BORDER, width=2)
        sp = [proj(x, z) for x, z in base]
        dist = [math.hypot(x - px, z - pz) for x, z in base]
        vis = lambda i: dist[i] <= R * 1.02
        if is_loop:
            n = len(sp)
            runs, run = [], []
            for i in range(n):
                if vis(i):
                    run.append(sp[i])
                else:
                    if len(run) >= 2:
                        runs.append(run)
                    run = []
            if len(run) >= 2:
                if runs and vis(0) and vis(n - 1):
                    runs[0] = run + runs[0]                        # участок через линию старта склеиваем
                else:
                    runs.append(run)
            for run in runs:
                self.track_line(c, [v for p in run for v in p])
            self.draw_marks(c, sp + sp[:2], sec, visible=lambda i: vis(i % n))
        else:
            run = [sp[i] for i in range(len(sp)) if vis(i)]
            if len(run) >= 2:
                c.create_line(*[v for p in run for v in p], fill=ACCENT, width=3, capstyle="round", joinstyle="round")
        items = []
        for car in cars:
            if car["player"]:
                items.append((car, cx, cy))
            elif math.hypot(car["pos"][0] - px, car["pos"][1] - pz) <= R:
                items.append((car, *proj(*car["pos"])))
        self.draw_cars(c, items, gx0, gy0, side)
        if headup:                                                  # стрелка «вперёд» у игрока
            c.create_polygon(cx, cy - 26, cx - 6, cy - 16, cx + 6, cy - 16, fill=ACCENT, outline="")

    def draw_linear(self, c, frame, me, sec, W):
        """Круг как горизонтальная полоса: сектора, старт/финиш и машины по пройденной доле круга, по полосам классов."""
        m = self.main
        x0, x1 = 22, W - 22
        L = frame.get("track_len", 0.0) if frame else 0.0
        by = self.h_title + int(self.rh * 0.75)
        bh = max(6, int(self.rh * 0.3))
        cuts = [0.0] + (list(sec) if sec and m.cfg.get("map_sectors", True) else []) + [1.0]
        for j in range(len(cuts) - 1):
            col = SEC_COL[j] if len(cuts) == 4 else "#c5cad3"
            c.create_rectangle(x0 + (x1 - x0) * cuts[j], by, x0 + (x1 - x0) * cuts[j + 1], by + bh, fill=col, outline="")
            if len(cuts) == 4:
                c.create_text(x0 + (x1 - x0) * (cuts[j] + cuts[j + 1]) / 2, by - int(self.rh * 0.35), text=f"S{j + 1}",
                              fill=SEC_COL[j], font=self.f_num)
        for xx, lab, anc in ((x0, "СТАРТ", "w"), (x1, "ФИНИШ", "e")):
            c.create_rectangle(xx - 2, by - 5, xx + 2, by + bh + 5, fill="#ffffff", outline="#111111")
        lane0 = by + bh + int(self.rh * 0.6)
        for ln in range(3):
            yy = lane0 + ln * self.lane_h + self.lane_h // 2
            c.create_line(x0, yy, x1, yy, fill="#232a36", dash=(2, 5))
        if not frame or L < 500 or not me:
            c.create_text(W // 2, lane0 + self.lane_h, text="жду данные игры", fill=DIM, font=m.small)
            return
        classes = [k for k in CLASS_ORDER if any(x["cls"] == k for x in frame["cars"])]
        classes += sorted({x["cls"] for x in frame["cars"]} - set(classes))
        nums = m.cfg.get("map_nums", True)
        r0 = max(int(m.cfg.get("map_dot", 6)), 12 if nums else 5)
        cars = [x for x in frame["cars"] if not x["garage"]]
        lane_of = lambda car: (car["place"] % 3 if len(classes) <= 1 else min(2, classes.index(car["cls"]) if car["cls"] in classes else 2))
        jit = {}
        for ln in range(3):                                          # близкие машины в одной полосе разводим по вертикали
            row = sorted((x for x in cars if lane_of(x) == ln), key=lambda t: t["lap_dist"])
            prev_x, flip = -1e9, 1
            for car in row:
                sx_ = x0 + (x1 - x0) * max(0.0, min(1.0, car["lap_dist"] / L))
                if sx_ - prev_x < 2 * r0 + 2:
                    jit[car["key"]] = flip * int(self.lane_h * 0.28)
                    flip = -flip
                else:
                    flip = 1
                prev_x = sx_
        for car in sorted(cars, key=lambda t: t["player"]):
            f = max(0.0, min(1.0, car["lap_dist"] / L))
            if len(classes) <= 1:
                ln = car["place"] % 3
            else:
                ln = min(2, classes.index(car["cls"]) if car["cls"] in classes else 2)
            sx = x0 + (x1 - x0) * f
            sy = lane0 + ln * self.lane_h + self.lane_h // 2 + jit.get(car["key"], 0)
            if car["player"]:
                c.create_line(sx, by + bh + 2, sx, lane0 + 3 * self.lane_h, fill=ACCENT, width=1)
                pr = max(r0 + 3, 15)
                c.create_oval(sx - pr, sy - pr, sx + pr, sy + pr, fill=FG, outline=ACCENT, width=3)
                if nums and car["place"] > 0:
                    c.create_text(sx, sy, text=str(car["place"]), fill="#000000", font=self.f_num)
            else:
                c.create_oval(sx - r0, sy - r0, sx + r0, sy + r0, fill=self.car_col(car), outline=BG, width=2)
                if nums and car["place"] > 0:
                    c.create_text(sx, sy, text=str(car["place"]), fill="#ffffff", font=self.f_num)
        fy = self.height - self.h_foot // 2
        s1, s2, last = me.get("last_s1", 0), me.get("last_s2", 0), me.get("last", 0)
        if s1 > 0 and s2 > s1 and last > s2:
            txt = f"прошлый круг:  S1 {s1:.2f}   S2 {s2 - s1:.2f}   S3 {last - s2:.2f}"
        else:
            txt = "сектора появятся после первого круга"
        c.create_text(14, fy, text=txt, fill=DIM, anchor="w", font=m.small)


def _mix(c1, c2, t):
    """Смесь двух цветов #rrggbb: t=0 -> c1, t=1 -> c2."""
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))


SEC_COL = ("#ffd400", "#3da9ff", "#ff5ad1")          # цвета секторов S1, S2, S3


class RadarWindow(RelativeWindow):
    """Круглый радар: моя машина в центре, соперники вокруг. Прозрачный, пока рядом никого нет."""
    POS, ON = ("radar_x", "radar_y"), "radar"
    OWN_ALPHA = True                                  # прозрачностью управляет сам радар
    RANGES = (15, 20, 30, 40, 60, 80, 120)
    CAR_L, CAR_W = 4.8, 2.0                           # размеры машины в метрах для рисунка

    def build(self):
        m = self.main
        self.cw, self.rh, self.R = m.cw, m.rh, 0
        self.diam = int(self.rh * 7.2 * max(0.3, min(3.0, float(m.cfg.get("radar_scale", 1.0)))))
        self.width = self.height = self.diam + 8
        self.canvas.config(width=self.width, height=self.height)
        self._sig = None
        self._alpha = 0.0
        self._ws_alpha = None
        self._hist, self._last_h = getattr(self, "_hist", {}), getattr(self, "_last_h", {})
        self.top.attributes("-alpha", 0.0)
        if not getattr(self, "_wheel", False):
            self._wheel = True
            self.canvas.bind("<MouseWheel>", lambda e: m.scale_radar(0.1 if e.delta > 0 else -0.1))

    def range_m(self):
        r = float(self.main.cfg.get("radar_range", 40))
        return r if r > 0 else 40.0

    def _track(self, frame):
        """Копим короткую историю позиций машин — по ней считаем, куда каждая едет."""
        now = time.monotonic()
        keep = {}
        for car in frame["cars"]:
            if car["garage"] or car["pos"] == (0.0, 0.0):
                continue
            h = self._hist.get(car["key"], [])
            if not h or h[-1][1:] != car["pos"]:
                h.append((now, car["pos"][0], car["pos"][1]))
            while h and now - h[0][0] > 1.0:
                h.pop(0)
            keep[car["key"]] = h
        self._hist = keep
        return now

    def _heading(self, key, now):
        """Единичный вектор движения машины (x, z) по её следу; None, если стоит."""
        h = self._hist.get(key)
        if not h or len(h) < 2:
            return self._last_h.get(key)
        t1, x1, z1 = h[-1]
        ref = next((p for p in h if p[0] <= now - 0.3), None) or next((p for p in reversed(h) if p[0] <= now - 0.1), None)
        if ref:
            dx, dz = x1 - ref[1], z1 - ref[2]
            n = math.hypot(dx, dz)
            if n >= 0.8:
                self._last_h[key] = (dx / n, dz / n)
        return self._last_h.get(key)

    def scan(self, frame):
        """Соперники в системе координат моей машины: направление «вперёд» берём из моего движения."""
        if not frame:
            return None, []
        me = next((x for x in frame["cars"] if x["player"]), None)
        if not me or me["garage"] or me["pos"] == (0.0, 0.0):
            return None, []
        now = self._track(frame)
        hm = self._heading(me["key"], now)
        if not hm:
            return None, []
        fx, fz = hm
        lx, lz = -fz, fx                                    # лево в левосторонней системе rF2 (x вправо, z вперёд)
        flip = 1.0
        items = []
        for car in frame["cars"]:
            if car["player"] or car["garage"] or car["pos"] == (0.0, 0.0):
                continue
            dx, dz = car["pos"][0] - me["pos"][0], car["pos"][1] - me["pos"][1]
            fwd = dx * fx + dz * fz
            left = dx * lx + dz * lz
            d = math.hypot(fwd, left)
            if d > 400:
                continue
            hc = self._heading(car["key"], now) or hm
            hf, hl = hc[0] * fx + hc[1] * fz, hc[0] * lx + hc[1] * lz
            items.append({"x": -left * flip, "y": fwd, "dx": -hl * flip, "dy": hf, "d": d, "car": car,
                          "side": abs(fwd) < 6.0 and abs(left) < 4.6})
        return me, items

    def tick(self, frame, ver):
        m = self.main
        me, items = self.scan(frame)
        rng = self.range_m()
        near = min((it["d"] for it in items), default=1e9)
        if m.hid(self.ON):
            target = 0.0
        elif not m.locked:
            target = 0.75                                    # при перемещении окна радар виден всегда
        elif me is None or near >= rng * 1.15:
            target = 0.0
        elif not m.cfg.get("radar_fade", True):
            target = 0.97                                    # без плавного проявления: виден, пока есть машины в радиусе
        else:
            p = 1.0 - near / (rng * 1.15)
            target = min(0.97, 0.12 + 0.9 * p ** 0.8)
        self._alpha += (target - self._alpha) * 0.4
        if abs(self._alpha - target) < 0.01:
            self._alpha = target
        a = round(self._alpha * self.opf(), 2)
        if a != self._ws_alpha:
            self._ws_alpha = a
            self.top.attributes("-alpha", a)
        if a > 0.0 or self._sig is None:
            sig = (ver, m.cfg["font"], m.locked, rng, int(time.time() * 30))
            if sig != self._sig:
                self._sig = sig
                self.draw(me, items)

    def _car_poly(self, cx, cy, k, ux, uy, hl, hw):
        """Прямоугольник: центр (cx, cy), ось вперёд (ux, uy) в экранных координатах (y вниз)."""
        vx, vy = -uy, ux
        pts = []
        for a, b in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
            pts += [cx + (ux * a * hl + vx * b * hw) * k, cy + (uy * a * hl + vy * b * hw) * k]
        return pts

    def draw(self, me, items):
        c, W = self.canvas, self.width
        c.delete("all")
        cx = cy = W / 2
        rr = self.diam / 2
        rng = self.range_m()
        k = (rr - 6) / rng
        ring, faint = "#7c8696", "#3b4350"
        if not self.main.locked:                             # подложка, чтобы окно можно было схватить мышью
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill="#10151d", outline="")
            c.create_text(cx, cy - rr * 0.55, text="тяните мышью", fill=DIM, font=self.main.small)
        c.create_oval(cx - rr + 3, cy - rr + 3, cx + rr - 3, cy + rr - 3, outline=ring, width=2)
        for f in (1 / 3, 2 / 3):
            r = (rr - 3) * f
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=faint, dash=(2, 5))
        c.create_line(cx - rr + 6, cy, cx + rr - 6, cy, fill=faint, dash=(2, 6))
        c.create_line(cx, cy - rr + 6, cx, cy + rr - 6, fill=faint, dash=(2, 6))
        if me is None:
            c.create_text(cx, cy + rr * 0.45, text="радар", fill=DIM, font=self.main.small)
        # машины рядом сбоку — красные дуги слева/справа
        left_busy = any(it["side"] and it["x"] < 0 for it in items)
        right_busy = any(it["side"] and it["x"] > 0 for it in items)
        box = (cx - rr + 3, cy - rr + 3, cx + rr - 3, cy + rr - 3)
        if left_busy and self.main.cfg.get("radar_arcs", True):
            c.create_arc(*box, start=180 - 38, extent=76, style="arc", outline="#ff3b30", width=7)
        if right_busy and self.main.cfg.get("radar_arcs", True):
            c.create_arc(*box, start=-38, extent=76, style="arc", outline="#ff3b30", width=7)
        # соперники
        for it in sorted(items, key=lambda t: -t["d"]):
            if it["d"] > rng * 1.05:
                continue
            sx, sy = cx + it["x"] * k, cy - it["y"] * k
            t = it["d"] / rng
            col = _mix("#ff3b30", "#ffd23a", t * 2) if t < 0.5 else _mix("#ffd23a", "#2ee26d", (t - 0.5) * 2)
            hx, hy = it["dx"], it["dy"]
            n = math.hypot(hx, hy)
            ux, uy = (hx / n, -hy / n) if n > 0.2 else (0.0, -1.0)
            poly = self._car_poly(sx, sy, k, ux, uy, self.CAR_L / 2, self.CAR_W / 2)
            c.create_polygon(*poly, fill=col, outline="#0d1117", width=1)
            nose = self._car_poly(sx, sy, k, ux, uy, self.CAR_L / 2, self.CAR_W / 2)
            c.create_line(nose[0], nose[1], nose[2], nose[3], fill="#0d1117", width=2)    # передняя кромка
        # моя машина в центре
        poly = self._car_poly(cx, cy, k, 0.0, -1.0, self.CAR_L / 2, self.CAR_W / 2)
        c.create_polygon(*poly, fill="#ffffff", outline=ACCENT, width=2)
        c.create_line(poly[0], poly[1], poly[2], poly[3], fill=ACCENT, width=3)


class WeatherWindow(RelativeWindow):
    """Погода: температура трассы и воздуха, влажность трассы, осадки."""
    POS, ON = ("wx_x", "wx_y"), "weather"
    CARD = "#141922"

    def build(self):
        m = self.main
        self.cw, self.rh, self.R = m.cw, m.rh, m.R if self.rounded else 0
        self.f_val = tkfont.Font(family=m.family, size=int(m.cfg["font"] * 1.5), weight="bold")
        self.card_h = int(self.rh * 2.5)
        self.gap = 8
        self.width = int(self.rh * 8.2)
        hide = set(m.cfg.get("wx_hide", []))
        self.cards = [k for k in ("track", "air", "wet", "rain") if k not in hide] or ["track"]
        n = len(self.cards)
        self._v = self.view()
        self.height = n * self.card_h + (n + 1) * self.gap
        if self._v == "compact":                                   # одна строка: трасса, воздух, влажность, осадки
            self.height = int(self.rh * 2.1)
            self.width = int(14 + sum(self.cw * {"track": 8.6, "air": 8.6, "wet": 15.0, "rain": 8.6}[k] for k in self.cards) + 8)
        self.canvas.config(width=self.width, height=self.height)
        self._sig = None

    def view(self):
        return "compact" if self.main.cfg.get("weather_view", "cards") == "compact" else "cards"

    @staticmethod
    def wet_label(w):
        return ("dry" if w < 0.03 else "damp" if w < 0.15 else "lightly wet" if w < 0.35
                else "wet" if w < 0.6 else "very wet" if w < 0.85 else "flooded")

    def tick(self, frame, ver):
        if self.view() != self._v:
            self.build()
        sig = (ver // 2, self.main.cfg["font"], int(time.time() * 2))
        if sig != self._sig:
            self._sig = sig
            self.draw(frame)

    def draw_compact(self, frame):
        m, c, W, H = self.main, self.canvas, self.width, self.height
        c.delete("all")
        if self.R:
            draw_rrect(c, 0, 0, W - 1, H - 1, self.R, fill=HDR_BG, outline=BORDER)
        else:
            c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline=BORDER)
        f = frame or {}
        ok = bool(frame)
        num = lambda v, lo, hi: isinstance(v, (int, float)) and lo < v < hi
        x = 14
        for k in self.cards:
            if k == "track":
                tag, val, col = "ТРАССА", (f"{f['track_temp']:.0f}°" if ok and num(f.get("track_temp"), -50, 90) else "—"), FG
            elif k == "air":
                tag, val, col = "ВОЗДУХ", (f"{f['air']:.0f}°" if ok and num(f.get("air"), -50, 90) else "—"), FG
            elif k == "wet":
                w = max(0.0, min(1.0, f.get("wet") or 0.0)) if ok else 0.0
                tag, val, col = "ВЛАЖН.", (self.wet_label(w) if ok else "—"), ("#3aa0ff" if w >= 0.03 else FG)
            else:
                r = f.get("rain")
                pr = max(0.0, min(1.0, r)) * 100 if ok and isinstance(r, (int, float)) else None
                tag, val, col = "ДОЖДЬ", (f"{pr:.0f}%" if pr is not None else "—"), ("#3aa0ff" if pr else FG)
            c.create_text(x, int(H * 0.30), text=tag, fill=DIM, anchor="w", font=m.small)
            c.create_text(x, int(H * 0.68), text=val, fill=col, anchor="w", font=m.bold)
            x += int(self.cw * {"track": 8.6, "air": 8.6, "wet": 15.0, "rain": 8.6}[k])

    def _card(self, name, title):
        c, W, h, g = self.canvas, self.width, self.card_h, self.gap
        y = g + self.cards.index(name) * (h + g)
        draw_rrect(c, 2, y, W - 3, y + h, max(8, self.R // 2), fill=self.CARD, outline=BORDER)
        c.create_text(16, y + int(h * 0.22), text=title, fill=DIM, anchor="w", font=self.main.small)
        return y

    def draw(self, frame):
        if self._v == "compact":
            return self.draw_compact(frame)
        m, c, W, h = self.main, self.canvas, self.width, self.card_h
        c.delete("all")
        f = frame or {}
        air, trk = f.get("air"), f.get("track_temp")
        wet, rain = f.get("wet"), f.get("rain")
        ok = bool(frame)
        fv = lambda v: f"{v:.0f} °C" if (ok and isinstance(v, (int, float)) and -50 < v < 90) else "—"
        on = self.cards
        if "track" in on:        # температура трассы (значок дороги)
            y = self._card("track", "TRACK TEMP")
            iy = y + int(h * 0.64)
            c.create_polygon(18, iy + 11, 42, iy + 11, 36, iy - 11, 24, iy - 11, fill="#8b95a5", outline="")
            c.create_line(30, iy - 9, 30, iy + 9, fill=self.CARD, dash=(3, 3), width=2)
            c.create_text(54, iy, text=fv(trk), fill=FG, anchor="w", font=self.f_val)
        if "air" in on:          # температура воздуха (термометр)
            y = self._card("air", "AIR TEMP")
            iy2 = y + int(h * 0.64) + 2
            c.create_oval(24, iy2, 36, iy2 + 12, fill="#8b95a5", outline="")
            c.create_rectangle(27, iy2 - 14, 33, iy2 + 4, fill="#8b95a5", outline="")
            c.create_text(54, y + int(h * 0.64), text=fv(air), fill=FG, anchor="w", font=self.f_val)
        if "wet" in on:          # влажность трассы
            self.draw_wet(frame, ok, wet)
        if "rain" in on:         # осадки (облако с каплями)
            self.draw_rain(ok, rain)

    def draw_wet(self, frame, ok, wet):
        m, c, W, h = self.main, self.canvas, self.width, self.card_h
        y = self._card("wet", "TRACK WETNESS")
        bx0, bx1, by = 18, W - 20, y + int(h * 0.50)
        c.create_line(bx0, by, bx1, by, fill=TRACK, width=6, capstyle="round")
        w = max(0.0, min(1.0, wet)) if (ok and isinstance(wet, (int, float))) else 0.0
        if w > 0.005:
            c.create_line(bx0, by, bx0 + (bx1 - bx0) * w, by, fill="#3aa0ff", width=6, capstyle="round")
        tx = bx0 + (bx1 - bx0) * w
        c.create_oval(tx - 5, by - 5, tx + 5, by + 5, fill=FG, outline="#3aa0ff", width=2)
        c.create_text(16, y + int(h * 0.80), text=self.wet_label(w) if ok else "—", fill=FG, anchor="w", font=m.bold)

    def draw_rain(self, ok, rain):
        m, c, W, h = self.main, self.canvas, self.width, self.card_h
        y = self._card("rain", "PRECIPITATION")
        iy = y + int(h * 0.62)
        c.create_oval(16, iy - 6, 32, iy + 6, fill="#8b95a5", outline="")
        c.create_oval(26, iy - 11, 42, iy + 3, fill="#8b95a5", outline="")
        c.create_oval(34, iy - 6, 50, iy + 6, fill="#8b95a5", outline="")
        for dx in (22, 31, 40):
            c.create_line(dx, iy + 9, dx - 2, iy + 14, fill="#3aa0ff", width=2)
        pr = (max(0.0, min(1.0, rain)) * 100) if (ok and isinstance(rain, (int, float))) else None
        c.create_text(64, iy, text=f"{pr:.0f} %" if pr is not None else "—", fill=FG, anchor="w", font=self.f_val)


class StartLightsWindow(RelativeWindow):
    """Стартовые огни: видны только на стартовой решётке, синхронно с игрой."""
    POS, ON = ("lights_x", "lights_y"), "lights"
    OWN_ALPHA = True
    GREEN_HOLD = 4.0                                   # сколько секунд показывать зелёный после старта

    def build(self):
        m = self.main
        self.cw, self.rh, self.R = m.cw, m.rh, m.R if self.rounded else 0
        self.lamp = int(self.rh * 1.7)
        self.n = 5
        self.width = int(self.n * self.lamp * 1.25 + self.lamp * 0.9)
        self.height = int(self.lamp * 1.9)
        self.canvas.config(width=self.width, height=self.height)
        self.top.attributes("-alpha", 0.0)
        self._sig = None
        self._prev_phase = None
        self._green_until = 0.0
        self._vis = None

    def state(self, frame):
        """(видно ли, сколько красных горит, всего красных, зелёный ли)."""
        now = time.monotonic()
        if not frame:
            return False, 0, 5, False
        phase, total = frame.get("game_phase"), int(frame.get("red_lights") or 5)
        total = total if 1 <= total <= 8 else 5
        me = next((x for x in frame["cars"] if x["player"]), None)
        on_grid = bool(me and not me["garage"] and me["speed"] < 8.0)
        prev, self._prev_phase = self._prev_phase, phase
        if phase == 4 and on_grid:
            self._green_until = 0.0
            return True, max(0, min(int(frame.get("start_light") or 0), total)), total, False
        if prev == 4 and phase == 5 and on_grid:         # огни погасли — игра дала зелёный
            self._green_until = now + float(self.main.cfg.get("lights_hold", self.GREEN_HOLD))
        if now < self._green_until:
            return True, 0, total, True
        return False, 0, total, False

    def tick(self, frame, ver):
        m = self.main
        show, lit, total, green = self.state(frame)
        if not m.locked:                                   # режим перемещения: показываем образец
            show, lit, total, green = True, 3, 5, False
        if m.hid(self.ON):
            show = False
        if total != self.n:
            self.n = total
            self.width = int(self.n * self.lamp * 1.25 + self.lamp * 0.9)
            self.canvas.config(width=self.width)
            self._sig = None
        key = (show, lit, total, green, m.locked)
        if key != self._sig:
            self._sig = key
            self.top.attributes("-alpha", 0.95 * self.opf() if show else 0.0)
            if show:
                self.draw(lit, total, green)

    def draw(self, lit, total, green):
        c, W, H, L = self.canvas, self.width, self.height, self.lamp
        c.delete("all")
        step = (W - L * 0.9) / total
        for i in range(total):
            cx, cy, r = L * 0.45 + step * (i + 0.5) + L * 0.0, H / 2, L * 0.46
            c.create_oval(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3, fill="#1a1f29", outline="#05070a", width=2)
            if green:
                col, glow = "#2ee26d", "#1b7a3c"
            elif i < lit:
                col, glow = "#ff2a22", "#7a1511"
            else:
                col, glow = "#2a2f39", "#2a2f39"
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=glow, outline="")
            c.create_oval(cx - r * 0.82, cy - r * 0.82, cx + r * 0.82, cy + r * 0.82, fill=col, outline="")
            if col not in ("#2a2f39",):
                c.create_oval(cx - r * 0.45, cy - r * 0.55, cx - r * 0.05, cy - r * 0.2, fill=_mix(col, "#ffffff", 0.45), outline="")


class PitLimiterWindow(RelativeWindow):
    """Пит-лимитер: зелёный — включён, красный — выключен (на пит-лейне). Вне пит-лейна без лимитера скрыт."""
    POS, ON = ("lim_x", "lim_y"), "limiter"
    OWN_ALPHA = True

    def build(self):
        m = self.main
        self.cw, self.rh, self.R = m.cw, m.rh, m.R if self.rounded else 0
        self.f_big = tkfont.Font(family=m.family, size=int(m.cfg["font"] * 1.3), weight="bold")
        self.height = int(self.rh * 2.1)
        self.width = int(self.rh * 7.6)
        self.canvas.config(width=self.width, height=self.height)
        self.top.attributes("-alpha", 0.0)
        self._sig = None

    def state(self, frame):
        """None — скрыт, True — лимитер включён, False — выключен, но мы на пит-лейне."""
        if not frame:
            return None
        me = next((x for x in frame["cars"] if x["player"]), None)
        inp = frame.get("inputs")
        on = bool(inp and inp.get("limiter"))
        if on:
            return True
        if me and me["in_pits"] and not me["garage"] and inp is not None:
            return False
        return None

    def tick(self, frame, ver):
        m = self.main
        st = self.state(frame)
        if not m.locked:
            st = True if st is None else st                  # образец при перемещении окна
        if m.hid(self.ON):
            st = None
        if st != self._sig:
            self._sig = st
            self.top.attributes("-alpha", 0.0 if st is None else 0.95 * self.opf())
            if st is not None:
                self.draw(st)

    def draw(self, on):
        c, W, H = self.canvas, self.width, self.height
        c.delete("all")
        col, glow = ("#2ee26d", "#17803f") if on else ("#ff3b30", "#8a1f19")
        draw_rrect(c, 0, 0, W - 1, H - 1, int(H * 0.4), fill="#0d1015", outline=glow)
        r = H * 0.30
        cx, cy = H * 0.55, H / 2
        c.create_oval(cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4, fill=glow, outline="")
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=col, outline="")
        c.create_oval(cx - r * 0.55, cy - r * 0.7, cx - r * 0.1, cy - r * 0.25, fill=_mix(col, "#ffffff", 0.5), outline="")
        c.create_text(H * 1.05, cy - H * 0.17, text="PIT LIMITER", fill=DIM, anchor="w", font=self.main.small)
        c.create_text(H * 1.05, cy + H * 0.17, text="ON" if on else "OFF", fill=col, anchor="w", font=self.f_big)


class FuelWindow(RelativeWindow):
    """Топливный калькулятор: уровень, расход (последний круг и среднее за 5), сколько нужно до конца."""
    POS, ON = ("fuel_x", "fuel_y"), "fuel"
    CARD = "#141922"

    def build(self):
        m = self.main
        sc = max(0.5, min(2.0, float(m.cfg.get("fuel_scale", 0.8))))
        sz = max(7, int(m.cfg["font"] * sc))
        fam = m.family
        self.cw, self.rh, self.R = int(m.cw * sc), int(m.rh * sc), int(m.R * sc) if self.rounded else 0
        self.f_val = tkfont.Font(family=fam, size=int(sz * 1.7), weight="bold")
        self.f_mid = tkfont.Font(family=fam, size=int(sz * 1.15), weight="bold")
        self.f_n = tkfont.Font(family=fam, size=sz)
        self.f_b = tkfont.Font(family=fam, size=sz, weight="bold")
        self.f_s = tkfont.Font(family=fam, size=max(6, sz - 3))
        self.width = int(self.rh * 9.2)
        self.height = int(self.rh * 11.2)
        self.height_ve = int(self.rh * 16.4)
        if not getattr(self, "_wheel", False):
            self._wheel = True
            self.canvas.bind("<MouseWheel>", lambda e: m.scale_fuel(0.05 if e.delta > 0 else -0.05))
        self.canvas.config(width=self.width, height=self.height)
        self._sig = None

    def tick(self, frame, ver):
        sig = (ver // 2, self.main.cfg["font"], self.main.cfg.get("fuel_reserve", 0.0), self.main.cfg.get("fuel_scale", 0.8), int(time.time() * 2))
        if sig != self._sig:
            self._sig = sig
            self.draw(frame)

    def draw(self, frame):
        m, c, W, rh = self.main, self.canvas, self.width, self.rh
        reserve = float(m.cfg.get("fuel_reserve", 0.0))
        d = fuel_calc.compute(frame, reserve, m.cfg.get("fuel_avg", 5)) if frame else None
        if d and not m.cfg.get("fuel_ve", True):
            d["ve"] = None
        H = self.height_ve if (d and d.get("ve")) else self.height
        if int(c.cget("height")) != H:
            c.config(height=H)
        c.delete("all")
        if self.R:
            draw_rrect(c, 0, 0, W - 1, H - 1, self.R, fill=HDR_BG, outline=BORDER)
        else:
            c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline=BORDER)
        # шапка
        cy = int(rh * 0.75)
        c.create_rectangle(14, cy - int(rh * 0.4), 18, cy + int(rh * 0.4), fill=MARK, outline="")
        c.create_text(28, cy, text="ТОПЛИВО", fill=FG, anchor="w", font=self.f_b)
        if reserve:
            c.create_text(W - 14, cy, text=f"запас +{reserve:g} кр.", fill=DIM, anchor="e", font=self.f_s)
        if not d:
            c.create_text(W // 2, H // 2, text="жду данные игры\n(топливо игрока)", fill=DIM, font=self.f_s, justify="center")
            return
        fuel, cap = d["fuel"], d["cap"]
        # уровень + полоска
        y = int(rh * 2.0)
        c.create_text(16, y, text=f"{fuel:.1f}", fill=FG, anchor="w", font=self.f_val)
        c.create_text(16 + self.f_val.measure(f"{fuel:.1f}") + 6, y + int(rh * 0.25), text="л", fill=DIM, anchor="w", font=self.f_n)
        if d["laps_fuel"] is not None:
            c.create_text(W - 14, y, text=f"{d['laps_fuel']:.1f} кр.", fill=ACCENT, anchor="e", font=self.f_mid)
        frac = max(0.0, min(1.0, fuel / cap)) if cap > 0 else 0.0
        col = "#2ee26d" if frac > 0.35 else "#ffc107" if frac > 0.15 else "#ff3b30"
        by, bh = int(rh * 3.05), max(8, int(rh * 0.32))
        c.create_rectangle(14, by, W - 14, by + bh, fill=TRACK, outline="")
        if frac > 0.005:
            c.create_rectangle(14, by, 14 + (W - 28) * frac, by + bh, fill=col, outline="")
        if cap > 0:
            c.create_text(14, by + bh + int(rh * 0.38), text=f"бак {cap:.0f} л", fill=DIM, anchor="w", font=self.f_s)
        # расход
        y0 = int(rh * 4.5)
        half = W // 2
        c.create_line(W // 2, y0, W // 2, y0 + int(rh * 2.0), fill=BORDER)
        c.create_text(16, y0 + int(rh * 0.2), text="ПОСЛЕДНИЙ КРУГ", fill=DIM, anchor="w", font=self.f_s)
        c.create_text(16, y0 + int(rh * 1.2), text=f"{d['last']:.2f} л" if d["last"] else "—", fill=FG, anchor="w", font=self.f_mid)
        c.create_text(half + 12, y0 + int(rh * 0.2), text=f"СРЕДНЕЕ ({d['n']} кр.)" if d["n"] else "СРЕДНЕЕ", fill=DIM, anchor="w", font=self.f_s)
        c.create_text(half + 12, y0 + int(rh * 1.2), text=f"{d['avg']:.2f} л" if d["avg"] else "—", fill=FG, anchor="w", font=self.f_mid)
        # до конца гонки
        y1 = int(rh * 7.0)
        c.create_line(14, y1 - int(rh * 0.3), W - 14, y1 - int(rh * 0.3), fill=BORDER)
        c.create_text(16, y1, text="ДО ФИНИША", fill=DIM, anchor="w", font=self.f_s)
        rows = [("кругов осталось", f"{d['left']:.1f}" if d["left"] is not None else "—", FG),
                ("нужно топлива", f"{d['need']:.1f} л" if d["need"] is not None else "—", FG)]
        for i, (lab, val, colr) in enumerate(rows):
            yy = y1 + int(rh * (0.95 + i * 0.85))
            c.create_text(16, yy, text=lab, fill=DIM, anchor="w", font=self.f_s)
            c.create_text(W - 14, yy, text=val, fill=colr, anchor="e", font=self.f_b)
        yy = y1 + int(rh * 2.85)
        if d["add"] is None:
            c.create_text(16, yy, text="ДОБАВИТЬ", fill=DIM, anchor="w", font=self.f_b)
            c.create_text(W - 14, yy, text="—", fill=DIM, anchor="e", font=self.f_mid)
        elif d["add"] > 0:
            c.create_text(16, yy, text="ДОБАВИТЬ", fill=FG, anchor="w", font=self.f_b)
            c.create_text(W - 14, yy, text=f"+{d['add']:.1f} л", fill="#ff5a4f", anchor="e", font=self.f_mid)
            if d["stops"]:
                c.create_text(W - 14, yy + int(rh * 0.8), text=f"заездов в боксы: {d['stops']}", fill=DIM, anchor="e", font=self.f_s)
        else:
            c.create_text(16, yy, text="ХВАТИТ", fill="#2ee26d", anchor="w", font=self.f_b)
            c.create_text(W - 14, yy, text=f"запас {-d['add']:.1f} л", fill="#2ee26d", anchor="e", font=self.f_mid)
        v = d.get("ve")
        if v:
            y2 = int(rh * 11.6)
            c.create_line(14, y2 - int(rh * 0.5), W - 14, y2 - int(rh * 0.5), fill=BORDER)
            c.create_text(16, y2, text="ВИРТ. ЭНЕРГИЯ", fill=DIM, anchor="w", font=self.f_s)
            vcol = "#2ee26d" if v["level"] > 35 else "#ffc107" if v["level"] > 15 else "#ff3b30"
            c.create_text(16, y2 + int(rh * 0.95), text=f"{v['level']:.1f}%", fill=FG, anchor="w", font=self.f_mid)
            if v["laps"] is not None:
                c.create_text(W - 14, y2 + int(rh * 0.95), text=f"{v['laps']:.1f} кр.", fill=ACCENT, anchor="e", font=self.f_mid)
            by2, bh2 = y2 + int(rh * 1.6), max(6, int(rh * 0.26))
            c.create_rectangle(14, by2, W - 14, by2 + bh2, fill=TRACK, outline="")
            if v["level"] > 0.5:
                c.create_rectangle(14, by2, 14 + (W - 28) * min(1.0, v["level"] / 100.0), by2 + bh2, fill=vcol, outline="")
            r0 = by2 + bh2 + int(rh * 0.75)
            c.create_text(16, r0, text="за круг", fill=DIM, anchor="w", font=self.f_s)
            c.create_text(W - 14, r0, text=(f"{v['last']:.2f}%" if v["last"] else "—") + "  /  ср. " + (f"{v['avg']:.2f}%" if v["avg"] else "—"),
                          fill=FG, anchor="e", font=self.f_n)
            r1 = r0 + int(rh * 0.85)
            if v["add"] is None:
                c.create_text(16, r1, text="до финиша", fill=DIM, anchor="w", font=self.f_s)
                c.create_text(W - 14, r1, text="—", fill=DIM, anchor="e", font=self.f_b)
            elif v["add"] > 0:
                c.create_text(16, r1, text=f"нужно {v['need']:.0f}%", fill=DIM, anchor="w", font=self.f_s)
                c.create_text(W - 14, r1, text=f"не хватает {v['add']:.0f}%", fill="#ff5a4f", anchor="e", font=self.f_b)
            else:
                c.create_text(16, r1, text=f"нужно {v['need']:.0f}%", fill=DIM, anchor="w", font=self.f_s)
                c.create_text(W - 14, r1, text=f"хватит, запас {-v['add']:.0f}%", fill="#2ee26d", anchor="e", font=self.f_b)


# =========================================================================
# 4b. Новые панели: дельта, предупреждения, шины и тормоза, пит-стоп и стинт, гэпы
# =========================================================================
class DeltaTracker:
    """Дельта к лучшему (или прошлому) кругу: время на каждом отрезке круга сравнивается с эталоном."""
    STEP = 4.0

    def __init__(self):
        self.reset(None)

    def reset(self, key):
        self.key = key
        self.best = self.last = None                  # (расстояния, времена)
        self.best_t = self.last_t = None
        self.cur, self.lap, self.t0, self.clean = [], None, None, False
        self.acc, self.prev = 0.0, None               # пройдено с начала круга (не зависит от того, где у игры «ноль» дистанции)
        self.cuts, self.best_cuts, self.last_cuts = [], None, None      # времена конца S1 и S2 (от начала круга)
        self.best_time, self.last_time, self.psec = None, None, None
        self.delta = self.pred = self.ref_t = None
        self.hist = collections.deque(maxlen=400)     # (время, дельта) — для мини-графика
        self.ref_name = ""

    def update(self, frame, ref="best"):
        me = next((c for c in frame["cars"] if c["player"]), None) if frame else None
        length = frame.get("track_len", 0.0) if frame else 0.0
        if not me or length < 500.0 or not frame.get("track"):
            return
        key = (frame["track"], me["vehicle"])
        if key != self.key:
            self.reset(key)
        gn = game_now()
        now = gn if gn is not None else frame["elapsed"]
        lap, dist = me["laps"], me["lap_dist"]
        pit = bool(me["in_pits"] or me["garage"])
        if self.lap is None or lap < self.lap:
            self.lap, self.t0, self.cur, self.clean = lap, now, [], False
            self.acc = 0.0
            self.cuts, self.psec = [], me.get("sector")
        elif lap > self.lap:
            if self.clean and len(self.cur) > 30 and lap == self.lap + 1:
                lt = me["last"] if me["last"] > 0 else now - self.t0
                if abs(lt - (now - self.t0)) >= 4.0:
                    lt = now - self.t0                       # время игры и наши часы разошлись — верим своим замерам
                if 15.0 < lt < 1800.0:
                    ds = [d for d, _ in self.cur] + [max(self.acc, self.cur[-1][0] + 1.0)]
                    ts = [t for _, t in self.cur] + [lt]
                    self.last, self.last_t = (ds, ts), lt
                    if len(self.cuts) == 2:
                        self.last_cuts, self.last_time = list(self.cuts), lt
                    if self.best_t is None or lt < self.best_t:
                        self.best, self.best_t = (ds, ts), lt
                        if len(self.cuts) == 2:
                            self.best_cuts, self.best_time = list(self.cuts), lt
            self.lap, self.t0, self.cur, self.clean = lap, now, [], not pit
            self.acc = 0.0
            self.cuts, self.psec = [], me.get("sector")
        sec = me.get("sector")
        if self.psec is not None and sec != self.psec and len(self.cuts) < 2 and now - self.t0 > 8.0:
            self.cuts.append(now - self.t0)              # граница сектора
        self.psec = sec
        # путь по кругу копим шагами: у игры «ноль» дистанции не всегда на линии таймера (особенно в rF2)
        step = 0.0
        if self.prev is not None:
            step = (dist - self.prev) % length
            if step > length / 2.0:
                step -= length
        self.prev = dist
        if pit:
            self.clean = False
            self.delta = None
            return
        if abs(step) >= 200.0:
            self.clean = False                              # телепорт/сброс
            step = 0.0
        if step > 0.0:
            self.acc += step
        if not self.cur:
            self.cur.append((0.0, 0.0))
        elif self.acc - self.cur[-1][0] >= self.STEP:
            self.cur.append((self.acc, now - self.t0))
        use, name = (self.best, "к лучшему") if ref == "best" else (self.last, "к прошлому")
        if ref == "best" and use is None and self.last is not None:
            use, name = self.last, "к прошлому"
        self.ref_name = name
        if use is None or not self.cur:
            self.delta = self.pred = self.ref_t = None
            return
        ds, ts = use
        pos = self.acc
        i = bisect.bisect_left(ds, pos)
        if i <= 0:
            rt = ts[0]
        elif i >= len(ds):
            rt = ts[-1]
        else:
            f = (pos - ds[i - 1]) / max(1e-6, ds[i] - ds[i - 1])
            rt = ts[i - 1] + (ts[i] - ts[i - 1]) * f
        self.ref_t = ts[-1]
        self.delta = (now - self.t0) - rt
        self.pred = self.ref_t + self.delta
        self.hist.append((now, self.delta))


class StintTracker:
    """Стинт, время в боксах и время за рулём у каждого пилота."""

    def __init__(self):
        self.reset(None)

    def reset(self, key):
        self.key = key
        self.drive = {}
        self.stint_t0 = self.stint_lap0 = None
        self.prev_pit = None
        self.pit_in_t, self.stand, self.last_stop, self.prev_t = None, 0.0, None, None
        self.in_stop = False
        self.now = 0.0
        self.stops = []

    def update(self, frame):
        me = next((c for c in frame["cars"] if c["player"]), None) if frame else None
        if not me:
            return
        key = (frame.get("session_start"), frame.get("track"), frame.get("kind"))
        if key != self.key:
            self.reset(key)
        gn = game_now()
        now = gn if gn is not None else frame["elapsed"]
        self.now = now
        dt = now - self.prev_t if self.prev_t is not None else 0.0
        self.prev_t = now
        if not 0.0 <= dt < 5.0:
            dt = 0.0
        pit = bool(me["in_pits"] or me["garage"])
        drv = me["driver"] or frame.get("player_name") or "—"
        if not pit and me["speed"] > 1.0:
            self.drive[drv] = self.drive.get(drv, 0.0) + dt
        if self.stint_t0 is None:
            self.stint_t0, self.stint_lap0 = now, me["laps"]
        if pit and not self.prev_pit:
            self.pit_in_t, self.stand, self.in_stop = now, 0.0, True
        if pit and self.in_stop and me["speed"] < 1.0:
            self.stand += dt
        if self.prev_pit and not pit and self.in_stop:
            self.last_stop = (now - (self.pit_in_t or now), self.stand)
            self.stops.append(self.last_stop)
            self.in_stop = False
            self.stint_t0, self.stint_lap0 = now, me["laps"]
        self.prev_pit = pit


class GapTracker:
    """Гэпы до соседей по классу и их изменение от круга к кругу."""

    def __init__(self):
        self.key, self.hist, self.lap = None, collections.deque(maxlen=8), None

    def neighbours(self, frame):
        me = next((c for c in frame["cars"] if c["player"]), None) if frame else None
        if not me or frame["kind"] in TIMED_KINDS:
            return None
        ordered = order_cars(frame["cars"], False)
        group = [c for c in ordered if c["cls"] == me["cls"]] if me["cls"] != "UNKNOWN" else ordered
        i = next((k for k, c in enumerate(group) if c["player"]), None)
        if i is None:
            return None
        gaps = group_gaps(group, False)
        ahead = group[i - 1] if i > 0 else None
        behind = group[i + 1] if i + 1 < len(group) else None
        ga = gaps[me["key"]][1] if ahead else None
        gb = gaps[behind["key"]][1] if behind else None
        num = lambda v: v if isinstance(v, (int, float)) else None
        return {"me": me, "ahead": ahead, "behind": behind, "ga": num(ga), "gb": num(gb), "pos": i + 1, "n": len(group),
                "ga_txt": gaps[me["key"]][0] if ahead else "", "gb_txt": gaps[behind["key"]][0] if behind else ""}

    def update(self, frame):
        nb = self.neighbours(frame)
        if not nb:
            return
        key = (frame.get("session_start"), frame.get("track"))
        if key != self.key:
            self.key, self.hist, self.lap = key, collections.deque(maxlen=8), None
        lap = nb["me"]["laps"]
        if self.lap is not None and lap == self.lap + 1:
            self.hist.append((lap, nb["ga"], nb["gb"], nb["ahead"]["key"] if nb["ahead"] else None,
                              nb["behind"]["key"] if nb["behind"] else None))
        self.lap = lap

    def trend(self, which, nb):
        """(изменение секунд за круг, список значений) для соседа 'a' (впереди) или 'b' (позади)."""
        idx, who = (1, "ahead") if which == "a" else (2, "behind")
        key = nb[who]["key"] if nb.get(who) else None
        vals = [h[idx] for h in self.hist if h[idx] is not None and h[3 if which == "a" else 4] == key]
        cur = nb["ga" if which == "a" else "gb"]
        if cur is not None:
            vals = vals + [cur]
        vals = vals[-5:]
        if len(vals) < 2:
            return None, vals
        return (vals[-1] - vals[0]) / (len(vals) - 1), vals


class AlertEngine:
    """Предупреждения: часть горит, пока условие верно, часть показывается несколько секунд."""

    def __init__(self):
        self.events, self.cond = {}, {}
        self.pen_seen, self.rain_prev, self.key = {}, 0.0, None

    def post(self, ident, text, level, hold, kind=None):
        self.events[ident] = (text, level, time.time() + hold, kind)

    def active(self):
        now = time.time()
        for k in [k for k, v in self.events.items() if v[2] < now]:
            del self.events[k]
        items = [(v[1], v[0], v[2] if len(v) > 2 else None) for v in self.cond.values()] + \
                [(v[1], v[0], v[3] if len(v) > 3 else None) for v in self.events.values()]
        items.sort(key=lambda t: -t[0])
        return items

    def update(self, frame, cfg):
        self.cond = {}
        me = next((c for c in frame["cars"] if c["player"]), None) if frame else None
        if not me or me["garage"] or frame.get("in_realtime") is False:
            return
        key = (frame.get("session_start"), frame.get("track"), frame.get("kind"))
        if key != self.key:
            self.key, self.pen_seen, self.rain_prev = key, {}, frame.get("rain", 0.0)
            self.events.clear()
        if frame.get("yellow", 0) > 0 or frame.get("game_phase") == 6:
            self.cond["yellow"] = ("ЖЁЛТЫЙ ФЛАГ", 2, "yellow")
        if me.get("flag") == 1:
            self.cond["blue"] = ("СИНИЙ ФЛАГ: ПРОПУСТИТЕ", 2, "blue")
        pen = me["penalties"]
        if pen > self.pen_seen.get("p", pen):
            self.post("pen", f"ШТРАФ  ({pen})", 3, 12.0, "red")
        self.pen_seen["p"] = pen
        rain = frame.get("rain", 0.0)
        if rain > 0.08 and self.rain_prev <= 0.03:
            self.post("rain", "НАЧИНАЕТСЯ ДОЖДЬ", 2, 12.0)
        self.rain_prev = rain
        if not me["in_pits"]:
            d = fuel_calc.compute(frame, float(cfg.get("fuel_reserve", 0.0)), cfg.get("fuel_avg", 5))
            if d and frame["kind"] not in TIMED_KINDS:
                lf = d.get("laps_fuel")
                if lf is not None and lf < 2.5:
                    self.cond["fuel"] = (("ЗАЕЗД В БОКСЫ: ТОПЛИВО ЗАКАНЧИВАЕТСЯ" if lf < 1.3 else f"МАЛО ТОПЛИВА: ~{lf:.1f} кр."), 3 if lf < 1.3 else 2)
                v = d.get("ve")
                if v and v.get("laps") is not None and v["laps"] < 2.5:
                    self.cond["ve"] = (("ЗАЕЗД В БОКСЫ: ЭНЕРГИЯ ЗАКАНЧИВАЕТСЯ" if v["laps"] < 1.3 else f"МАЛО ЭНЕРГИИ: ~{v['laps']:.1f} кр."), 3 if v["laps"] < 1.3 else 2)
        wh = me.get("wheels")
        if wh:
            if max(w["temp"] for w in wh) > 118.0:
                self.cond["tyre"] = ("ПЕРЕГРЕВ ШИН", 2)
            if max(w["brake"] for w in wh) >= 1100.0:
                self.cond["brake"] = ("ПЕРЕГРЕВ ТОРМОЗОВ", 2)
        if 0 < frame.get("max_laps", 0) < 100_000 and me["laps"] == frame["max_laps"] - 1 and frame["kind"] == "RACE":
            self.cond["last"] = ("ПОСЛЕДНИЙ КРУГ", 1, "white")


class WearTracker:
    """Износ каждого колеса за круг: разница остатка протектора между началом и концом круга."""
    KEEP = 8

    def __init__(self):
        self.reset(None)

    def reset(self, key):
        self.key, self.lap, self.start, self.hist = key, None, None, []

    def update(self, frame):
        me = next((c for c in frame["cars"] if c["player"]), None) if frame else None
        wh = me.get("wheels") if me else None
        if not wh:
            return
        key = (frame.get("session_start"), frame.get("track"), frame.get("kind"))
        if key != self.key:
            self.reset(key)
        w = [x["wear"] for x in wh]
        if self.lap is None:
            self.lap, self.start = me["laps"], w
            return
        if me["laps"] != self.lap:
            loss = [abs(a - b) * 100.0 for a, b in zip(self.start, w)]
            if me["laps"] == self.lap + 1 and max(loss) < 8.0 and not me["in_pits"]:      # скачок = смена шин, круг пропускаем
                self.hist.append(loss)
                self.hist = self.hist[-self.KEEP:]
            self.lap, self.start = me["laps"], w

    def per_lap(self, n=3):
        """Средний износ за круг по четырём колёсам (последние n кругов) или None."""
        if not self.hist:
            return None
        part = self.hist[-n:]
        return [sum(h[i] for h in part) / len(part) for i in range(4)]


delta_tracker, stint_tracker, gap_tracker, alert_engine = DeltaTracker(), StintTracker(), GapTracker(), AlertEngine()
wear_tracker = WearTracker()


def _card(win, title, sub=""):
    """Общая подложка и шапка небольшой панели."""
    m, c, W, H = win.main, win.canvas, win.width, win.height
    c.delete("all")
    if win.R:
        draw_rrect(c, 0, 0, W - 1, H - 1, win.R, fill=HDR_BG, outline=BORDER)
    else:
        c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline=BORDER)
    cy = int(win.rh * 0.75)
    c.create_rectangle(14, cy - int(win.rh * 0.38), 18, cy + int(win.rh * 0.38), fill=MARK, outline="")
    c.create_text(28, cy, text=title, fill=FG, anchor="w", font=m.bold)
    if sub:
        c.create_text(W - 14, cy, text=sub, fill=DIM, anchor="e", font=m.small)
    return c, cy


def _hm(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


class SimpleWindow(RelativeWindow):
    """Основа для новых панелей: размер задаёт WIDTH/HEIGHT в единицах строки, перерисовка по таймеру."""
    WIDTH, HEIGHT, HZ = 22.0, 6.0, 8
    TITLE = ""
    VIEW_KEY = None                      # ключ cfg с видом панели (если у неё несколько видов)

    def view(self):
        return self.main.cfg.get(self.VIEW_KEY, "full") if self.VIEW_KEY else ""

    def dims(self, view):
        return self.WIDTH, self.HEIGHT

    def build(self):
        m = self.main
        self.cw, self.rh, self.R = m.cw, m.rh, m.R if self.rounded else 0
        self._view = self.view()
        wd, ht = self.dims(self._view)
        self.width = int(self.cw * wd)
        self.height = int(self.rh * ht)
        self.f_huge = tkfont.Font(family=m.family, size=max(8, int(m.cfg["font"] * 2.1)), weight="bold")
        self.f_mid = tkfont.Font(family=m.family, size=max(8, int(m.cfg["font"] * 1.25)), weight="bold")
        if not getattr(self, "_wheel", False):
            self._wheel = True
            self.canvas.bind("<MouseWheel>", lambda e, k="scale_" + self.ON: self.wheel(k, 0.05 if e.delta > 0 else -0.05))
        self.canvas.config(width=self.width, height=self.height)
        self._sig = None

    def wheel(self, key, d):
        m = self.main._m
        m.cfg[key] = round(max(0.5, min(3.0, float(m.cfg.get(key, 1.0)) + d)), 2)
        self.build()
        m.save()

    def tick(self, frame, ver):
        if self.VIEW_KEY and self.view() != self._view:
            self.build()
        sig = (int(time.time() * self.HZ), self.main.cfg["font"], self.main.locked)
        if sig != self._sig:
            self._sig = sig
            self.draw(frame)

    def me(self, frame):
        return next((c for c in frame["cars"] if c["player"]), None) if frame else None


class DeltaWindow(SimpleWindow):
    POS, ON = ("delta_x", "delta_y"), "delta"
    WIDTH, HEIGHT, HZ = 26.0, 7.0, 25
    VIEW_KEY = "delta_view"

    THEMES = ("ring", "wide", "pill", "tick", "segments", "chevrons")

    def theme(self):
        t = self.main.cfg.get("delta_theme", "full")
        return t if t in self.THEMES else ""

    def view(self):
        th = self.theme()
        if th:
            cf = self.main.cfg
            return "t:" + th + ":" + "".join("1" if cf.get(k, True) else "0" for k in ("delta_show_session", "delta_show_mine", "delta_show_pred"))
        v = self.main.cfg.get("delta_theme", "full")
        if v not in ("full", "bar", "sectors"):
            v = self.main.cfg.get("delta_view", "full")
        if v == "sectors":
            return "sectors" if self.main.cfg.get("delta_sec_bar", True) else "sectors_nb"
        return "bar" if v == "bar" else "full"

    def dims(self, view):
        return {"bar": (26.0, 2.2), "sectors": (34.0, 9.9), "sectors_nb": (34.0, 7.6)}.get(view, (26.0, 7.0))

    @staticmethod
    def _lt(t):
        return f"{int(t) // 60}:{t - 60 * (int(t) // 60):06.3f}" if t >= 60 else f"{t:.3f}"

    # ---------- темы оформления ----------
    DARK, GRN, RED_, GOLD = "#060708", "#24e84e", "#ff4a40", "#f2b705"
    LABELS = {"session": "ЛУЧШИЙ СЕССИИ", "mine": "МОЙ ЛУЧШИЙ", "pred": "ПРОГНОЗ"}

    def build(self):
        super().build()
        if self._view.startswith("t:"):
            self._theme_size()

    def _theme_size(self):
        m, rh = self.main, self.rh
        _, th, flags = self._view.split(":")
        self.th_name = th
        self.show = [k for k, f in zip(("session", "mine", "pred"), flags) if f == "1"]
        fs = m.cfg["font"]
        self.f_tm = tkfont.Font(family=m.family, size=max(9, int(fs * 1.3)), weight="bold")
        self.f_num = tkfont.Font(family=m.family, size=max(10, int(fs * 1.8)), weight="bold")
        self.f_lb = tkfont.Font(family=m.family, size=max(6, int(fs * 0.62)), weight="bold")
        n, gap = len(self.show), 6
        lw = max([self.f_lb.measure(self.LABELS[k]) for k in self.show] + [0])
        self.card_w = max(self.f_tm.measure("00:00.000"), lw) + int(rh * 1.1)
        self.card_h = int(rh * 2.55)
        self.cards_w = n * self.card_w + max(0, n - 1) * gap
        self.gap = gap
        if th == "ring":
            self.ring_d = int(rh * 5.2)
            self.width = self.ring_d + (10 + self.cards_w if n else 0)
            self.height = self.ring_d
        else:
            self.bar_w = max(self.cards_w, int(rh * 13))
            self.bar_h = {"wide": int(rh * 1.9), "pill": int(rh * 1.2), "tick": int(rh * 0.7), "segments": int(rh * 1.9), "chevrons": int(rh * 1.9)}[th]
            top = int(rh * 1.6) if th in ("tick", "pill") else 0
            self.width = self.bar_w
            self.height = top + self.bar_h + (gap + 4 + self.card_h if n else 0)
        self.canvas.config(width=self.width, height=self.height)

    def _tm(self, t):
        return "--:--.---" if not t or t <= 0 else f"{int(t) // 60:02d}:{t - 60 * (int(t) // 60):06.3f}"

    def _theme_values(self, frame):
        dt, m = delta_tracker, self.main
        if not m.locked:
            return -0.85, {"session": 138.120, "mine": 138.120, "pred": 138.120}
        me = next((c for c in frame["cars"] if c["player"]), None) if frame else None
        allb = [c["best"] for c in frame["cars"] if c.get("best", 0) > 0] if frame else []
        mine = (me["best"] if me and me.get("best", 0) > 0 else None) or dt.best_t
        return dt.delta, {"session": min(allb) if allb else None, "mine": mine, "pred": dt.pred}

    def _cards(self, c, x0, y0, style):
        """Карточки с лучшими кругами: rect — прямоугольники, pill — капсулы, panel — одна панель."""
        vals = self._vals
        n, cw, ch, gap = len(self.show), self.card_w, self.card_h, self.gap
        if not n:
            return
        if style == "panel":
            draw_rrect(c, x0, y0, x0 + self.cards_w, y0 + ch, int(ch * 0.25), fill=self.DARK, outline="")
        for i, k in enumerate(self.show):
            x = x0 + i * (cw + gap)
            if style == "pill":
                draw_rrect(c, x, y0, x + cw, y0 + ch, ch // 2, fill=self.DARK, outline="")
            elif style == "rect":
                draw_rrect(c, x, y0, x + cw, y0 + ch, int(ch * 0.18), fill=self.DARK, outline="")
            v = vals.get(k)
            col = self.GRN if k == "pred" else FG
            c.create_text(x + cw / 2, y0 + ch * 0.40, text=self._tm(v), fill=col, font=self.f_tm)
            c.create_text(x + cw / 2, y0 + ch * 0.78, text=self.LABELS[k], fill="#d6dae2", font=self.f_lb)
            if i == 0:                                      # золотая метка у первой карточки
                if style == "pill":
                    for xx in (x + 9, x + cw - 9):
                        c.create_oval(xx - 4, y0 + ch / 2 - 6, xx + 4, y0 + ch / 2 + 6, fill=self.GOLD, outline="")
                elif self.th_name in ("chevrons", "segments"):
                    c.create_polygon(x + 3, y0 + ch * 0.3, x + 9, y0 + ch / 2, x + 3, y0 + ch * 0.7, fill=self.GOLD, outline="")
                else:
                    c.create_line(x + 6, y0 + ch * 0.18, x + 6, y0 + ch * 0.82, fill=self.GOLD, width=2)

    def draw_theme(self, frame):
        m, c = self.main, self.canvas
        c.delete("all")
        d, self._vals = self._theme_values(frame)
        th, W = self.th_name, self.width
        span = max(0.1, float(m.cfg.get("delta_range", 1.0)))
        f = 0.0 if d is None else max(-1.0, min(1.0, d / span))
        col = self.GRN if (d is None or d <= 0) else self.RED_
        txt = "—" if d is None else f"{d:+.2f}"
        rh = self.rh
        if th == "ring":
            D = self.ring_d
            c.create_oval(2, 2, D - 2, D - 2, fill=self.DARK, outline="")
            pad = int(D * 0.07)
            c.create_arc(pad, pad, D - pad, D - pad, start=0, extent=359.9, style="arc", outline="#2a2d31", width=max(6, int(D * 0.09)))
            if d is not None and abs(f) > 0.005:
                c.create_arc(pad, pad, D - pad, D - pad, start=90, extent=-f * 270, style="arc", outline=col, width=max(6, int(D * 0.09)))
            tx, ty = D / 2, pad
            c.create_polygon(tx, ty - 6, tx + 6, ty, tx, ty + 6, tx - 6, ty, fill="#bfc3ca", outline="#111")
            bw, bh = int(D * 0.74), int(D * 0.32)
            c.create_rectangle(D / 2 - bw / 2, D / 2 - bh / 2, D / 2 + bw / 2, D / 2 + bh / 2, fill="#000", outline="")
            c.create_text(D / 2, D / 2 - bh / 2 - 9, text="ДЕЛЬТА", fill="#7d838d", font=self.f_lb)
            c.create_text(D / 2, D / 2, text=txt, fill=col if d is not None else DIM, font=self.f_num)
            self._cards(c, D + 10, (D - self.card_h) / 2, "panel")
            return
        top = int(rh * 1.6) if th in ("tick", "pill") else 0
        bh, bw = self.bar_h, self.bar_w
        y0 = top
        cy = y0 + bh / 2
        numw = self.f_num.measure("-0.00") + 18
        if th == "chevrons":
            pts = [0, cy, bh / 2, y0, bw - bh / 2, y0, bw, cy, bw - bh / 2, y0 + bh, bh / 2, y0 + bh]
            c.create_polygon(*pts, fill=self.DARK, outline="")
            mid = bw / 2
            half = bw / 2 - bh * 0.8
            step = max(10, int(bh * 0.5))
            nchev = int(abs(f) * half / step)
            sgn = 1 if f < 0 else -1                      # быстрее (минус) — стрелки вправо
            for i in range(nchev):
                x = mid + sgn * (i * step + 6)
                a = bh * 0.26
                c.create_line(x - sgn * a * 0.6, cy - a, x + sgn * a * 0.6, cy, x - sgn * a * 0.6, cy + a, fill=col, width=3)
            nx = mid - sgn * 12 if d is not None else mid
            c.create_text(nx, cy, text=txt, fill=col if d is not None else DIM, anchor=("e" if sgn > 0 else "w") if d is not None else "center", font=self.f_num)
        elif th in ("wide", "segments"):
            draw_rrect(c, 0, y0, bw, y0 + bh, int(bh * 0.15), fill=self.DARK, outline="")
            ix0, ix1 = 10, bw - numw - 6
            mid = (ix0 + ix1) / 2
            half = (ix1 - ix0) / 2
            xe = mid - f * half
            if th == "wide":
                if d is not None:
                    c.create_rectangle(min(mid, xe), y0 + bh * 0.22, max(mid, xe), y0 + bh * 0.78, fill=col, outline="")
                c.create_line(mid, y0 + bh * 0.1, mid, y0 + bh * 0.9, fill="#9aa0a8", width=1)
            else:
                n = 44
                for i in range(n):
                    x = ix0 + (ix1 - ix0) * (i + 0.5) / n
                    on = d is not None and min(mid, xe) <= x <= max(mid, xe)
                    c.create_line(x, y0 + bh * 0.22, x, y0 + bh * 0.78, fill=col if on else "#3a3d42", width=2)
                c.create_line(mid, y0 + bh * 0.1, mid, y0 + bh * 0.9, fill="#9aa0a8", width=1)
            c.create_text(bw - 10, cy, text=txt, fill=col if d is not None else DIM, anchor="e", font=self.f_num)
        elif th == "tick":
            draw_rrect(c, 0, y0, bw, y0 + bh, bh // 2, fill=self.DARK, outline="")
            mid = bw / 2
            half = bw / 2 - 14
            xe = mid - f * half
            if d is not None:
                c.create_line(min(mid, xe), cy, max(mid, xe), cy, fill=col, width=3)
            c.create_polygon(mid, cy - 4, mid + 4, cy, mid, cy + 4, mid - 4, cy, fill="#8a8f97", outline="")
            tw_ = self.f_num.measure(txt) + 28
            draw_rrect(c, mid - tw_ / 2, 0, mid + tw_ / 2, top - 2, 8, fill=self.DARK, outline="")
            c.create_text(mid, (top - 2) / 2, text=txt, fill=col if d is not None else DIM, font=self.f_num)
        else:                                               # pill
            mid = bw / 2
            half = bw / 2 - 30
            c.create_line(8, cy, bw - 8, cy, fill="#101214", width=max(5, int(bh * 0.3)), capstyle="round")
            px = mid - f * half
            tw_ = self.f_num.measure(txt) + 22
            draw_rrect(c, px - tw_ / 2, 0, px + tw_ / 2, top - 2, (top - 2) // 2, fill=col if d is not None else "#2a2d31", outline="")
            c.create_text(px, (top - 2) / 2, text=txt, fill="#04210b" if d is not None else DIM, font=self.f_num)
        cy0 = top + bh + self.gap + 4
        style = "pill" if th == "pill" else "rect"
        self._cards(c, (self.width - self.cards_w) / 2, cy0, style)

    def draw_sectors(self, frame):
        """Время текущего круга, прогноз и сектора: время и разница с секторами лучшего круга."""
        m, rh, W = self.main, self.rh, self.width
        dt = delta_tracker
        c, cy = _card(self, "ДЕЛЬТА", "к лучшему" if m.cfg.get("delta_ref", "best") == "best" else "к прошлому")
        gn = game_now()
        now = gn if gn is not None else (frame["elapsed"] if frame else 0.0)
        demo = not m.locked
        lap_t = (now - dt.t0) if dt.t0 is not None else None
        cuts, best, last = list(dt.cuts), dt.best_cuts, dt.last_cuts
        if demo:
            lap_t, cuts, best, last = 41.37, [28.9], [29.1, 61.0], [29.0, 60.8]
        c.create_text(14, int(rh * 1.95), text="КРУГ", fill=DIM, anchor="w", font=m.small)
        c.create_text(14, int(rh * 2.95), text=self._lt(lap_t) if lap_t is not None else "—", fill=FG, anchor="w", font=self.f_huge)
        pred = dt.pred if dt.pred else None
        if demo:
            pred = 90.55
        d = dt.delta if not demo else 0.34
        c.create_text(W - 14, int(rh * 1.95), text="ПРОГНОЗ", fill=DIM, anchor="e", font=m.small)
        c.create_text(W - 14, int(rh * 2.95), text=self._lt(pred) if pred else "—", fill=FG, anchor="e", font=self.f_mid)

        def secs(cs, total):
            out = []
            prev = 0.0
            for x in list(cs) + ([total] if total is not None and len(cs) == 2 else []):
                out.append(x - prev)
                prev = x
            return out
        cur = secs(cuts, None)
        ref = secs(best, dt.best_time) if best and dt.best_time else []
        old = secs(last, dt.last_time) if last and dt.last_time else []
        if demo:
            ref, old = [29.1, 31.9, 29.0], [29.0, 31.8, 29.1]
        cell = (W - 28) // 3
        for i in range(3):
            x0 = 14 + i * cell
            live = i < len(cur)
            val = cur[i] if live else (old[i] if i < len(old) else None)
            mx = x0 + cell // 2 - 4
            c.create_text(mx, int(rh * 4.65), text=f"S{i + 1}", fill=DIM, font=m.small)
            c.create_text(mx, int(rh * 5.45), text=f"{val:.3f}" if val is not None else "—", fill=FG if live else DIM, font=m.small)
            if val is not None and i < len(ref):
                dd = val - ref[i]
                col = "#2ee26d" if dd <= 0 else "#ff5a4f"
                if not live:
                    col = "#4a7a5a" if dd <= 0 else "#8a4a46"
                c.create_text(mx, int(rh * 6.4), text=f"{dd:+.3f}", fill=col, font=self.f_mid)
            else:
                c.create_text(mx, int(rh * 6.4), text="—", fill=DIM, font=self.f_mid)
            if i:
                c.create_line(x0 - 4, int(rh * 4.3), x0 - 4, int(rh * 6.9), fill=BORDER)
        # внизу: число и полоска дельты к лучшему кругу (можно отключить в настройках)
        if self._view == "sectors_nb":
            if d is not None:
                c.create_text(W - 14, int(rh * 3.75), text=f"{d:+.2f}", fill="#2ee26d" if d <= 0 else "#ff5a4f", anchor="e", font=m.bold)
        elif d is None:
            c.create_text(W // 2, int(rh * 7.9), text="Δ  —", fill=DIM, font=self.f_mid)
        else:
            col = "#2ee26d" if d <= 0 else "#ff5a4f"
            c.create_text(W // 2, int(rh * 7.9), text=f"{d:+.2f}", fill=col, font=self.f_mid)
            by, bh = int(rh * 8.7), max(8, int(rh * 0.36))
            x0, x1, mid = 14, W - 14, W // 2
            c.create_rectangle(x0, by, x1, by + bh, fill=TRACK, outline="")
            span = max(0.1, float(m.cfg.get("delta_range", 1.0)))
            xe = mid + max(-1.0, min(1.0, d / span)) * (mid - x0)
            c.create_rectangle(min(mid, xe), by, max(mid, xe), by + bh, fill=col, outline="")
            c.create_line(mid, by - 3, mid, by + bh + 3, fill=FG, width=2)

    def draw_bar(self, frame):
        """Только полоска и число."""
        m, rh, W, H = self.main, self.rh, self.width, self.height
        c = self.canvas
        c.delete("all")
        if self.R:
            draw_rrect(c, 0, 0, W - 1, H - 1, self.R, fill=HDR_BG, outline=BORDER)
        else:
            c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline=BORDER)
        d = delta_tracker.delta
        if not m.locked:
            d = 0.34
        cy = H // 2
        if d is None:
            c.create_text(W // 2, cy, text="Δ  —", fill=DIM, font=self.f_mid)
            return
        col = "#2ee26d" if d <= 0 else "#ff5a4f"
        tw = int(self.cw * 6.4)
        c.create_text(12 + tw // 2, cy, text=f"{d:+.2f}", fill=col, font=self.f_mid)
        x0, x1 = 12 + tw + 4, W - 12
        mid = (x0 + x1) // 2
        bh = max(8, int(rh * 0.5))
        c.create_rectangle(x0, cy - bh // 2, x1, cy + bh // 2, fill=TRACK, outline="")
        span = max(0.1, float(m.cfg.get("delta_range", 1.0)))
        xe = mid + max(-1.0, min(1.0, d / span)) * (mid - x0)
        c.create_rectangle(min(mid, xe), cy - bh // 2, max(mid, xe), cy + bh // 2, fill=col, outline="")
        c.create_line(mid, cy - bh // 2 - 3, mid, cy + bh // 2 + 3, fill=FG, width=2)

    def draw(self, frame):
        if self._view.startswith("t:"):
            return self.draw_theme(frame)
        if self._view in ("sectors", "sectors_nb"):
            return self.draw_sectors(frame)
        if self._view == "bar":
            return self.draw_bar(frame)
        m, rh, W = self.main, self.rh, self.width
        ref = m.cfg.get("delta_ref", "best")
        c, cy = _card(self, "ДЕЛЬТА", delta_tracker.ref_name if delta_tracker.ref_name else "")
        d = delta_tracker.delta
        demo = not m.locked
        if demo:
            d = 0.34
        if d is None:
            c.create_text(W // 2, int(rh * 3.0), text="проедьте чистый круг —\nпотом появится дельта", fill=DIM, font=m.small, justify="center", width=W - 20)
            return
        col = "#2ee26d" if d <= 0 else "#ff5a4f"
        c.create_text(W // 2, int(rh * 2.2), text=f"{d:+.2f}", fill=col, font=self.f_huge)
        by, bh = int(rh * 3.55), max(8, int(rh * 0.36))
        x0, x1, mid = 14, W - 14, W // 2
        c.create_rectangle(x0, by, x1, by + bh, fill=TRACK, outline="")
        span = max(0.1, float(m.cfg.get("delta_range", 1.0)))
        f = max(-1.0, min(1.0, d / span))
        xe = mid + f * (mid - x0)
        c.create_rectangle(min(mid, xe), by, max(mid, xe), by + bh, fill=col, outline="")
        c.create_line(mid, by - 3, mid, by + bh + 3, fill=FG, width=2)
        # мини-график за последние секунды
        gy0, gh = int(rh * 4.3), int(rh * 1.4)
        c.create_line(x0, gy0 + gh // 2, x1, gy0 + gh // 2, fill=BORDER)
        if delta_tracker.hist and not demo:
            tn = delta_tracker.hist[-1][0]
            pts = [(x1 - (tn - t) / 12.0 * (x1 - x0), gy0 + gh / 2 - max(-1, min(1, dv / span)) * gh / 2) for t, dv in delta_tracker.hist if tn - t <= 12.0]
            if len(pts) >= 2:
                c.create_line(*[v for p in pts for v in p], fill=col, width=2)
        if delta_tracker.ref_t:
            c.create_text(14, int(rh * 6.0), text=f"эталон {fmt_lap(delta_tracker.ref_t)}", fill=DIM, anchor="w", font=m.small)
            if delta_tracker.pred and not demo:
                c.create_text(W - 14, int(rh * 6.0), text=f"прогноз {fmt_lap(delta_tracker.pred)}", fill=FG, anchor="e", font=m.small)


def session_clock(frame):
    """(название сессии, таймер, подпись) — то же, что в шапке таблицы: круг / осталось / прошло."""
    if not frame:
        return "", "", ""
    session = re.sub(r"(\D)(\d)", r"\1 \2", frame["label"]).upper()
    gn = game_now()
    now = gn if gn is not None else frame["elapsed"]
    end = frame["end_et"]
    if 0 < frame["max_laps"] < 100_000:
        lead_laps = max((x["laps"] for x in frame["cars"]), default=0)
        return session, f"{min(lead_laps + 1, frame['max_laps'])}/{frame['max_laps']}", "КРУГ"
    if 0 < end < 1e7:
        return session, fmt_hms(min(end, end - now)), "ОСТАЛОСЬ"
    return session, fmt_hms(now), "ПРОШЛО"


class _TimeCard(SimpleWindow):
    """Крупные часы в рамке: таймер сессии и локальное время. Размер подгоняется под шрифт."""
    WIDTH, HEIGHT, HZ = 17.0, 4.6, 4
    SAMPLE, TITLES = "0:00:00", ("PRACTICE 1", "ОСТАЛОСЬ")

    def build(self):
        super().build()
        m = self.main
        self.f_t = tkfont.Font(family=m.family, size=max(8, int(m.cfg["font"] * 1.7)), weight="bold")
        head = int(self.rh * 1.5)
        w = max(self.f_t.measure(self.SAMPLE) + 60,
                28 + m.bold.measure(self.TITLES[0]) + 20 + m.small.measure(self.TITLES[1]) + 44)
        self.width = int(w)
        self.height = head + self.f_t.metrics("linespace") + int(self.rh * 0.7)
        self.canvas.config(width=self.width, height=self.height)
        self._head = head

    def big_text(self, c, text, col=None):
        c.create_text(self.width // 2, self._head + (self.height - self._head) // 2, text=text, fill=col or FG, font=self.f_t)


class SessionTimerWindow(_TimeCard):
    POS, ON = ("sesstime_x", "sesstime_y"), "sesstime"

    def draw(self, frame):
        m = self.main
        session, timer, caption = session_clock(frame)
        if not frame or not m.locked:
            session, timer, caption = ("RACE 1", "1:23:45", "ОСТАЛОСЬ") if not m.locked else ("", "", "")
        c, cy = _card(self, session or "СЕССИЯ", caption)
        if timer:
            self.big_text(c, timer)
        else:
            c.create_text(self.width // 2, self._head + (self.height - self._head) // 2, text="ЖДУ ИГРУ", fill=DIM, font=m.bold)


class ClockWindow(_TimeCard):
    POS, ON = ("clock_x", "clock_y"), "clock"
    SAMPLE, TITLES = "00:00:00", ("ВРЕМЯ", "00.00")

    def draw(self, frame):
        m = self.main
        secs = bool(m.cfg.get("clock_seconds", True))
        c, cy = _card(self, "ВРЕМЯ", time.strftime("%d.%m"))
        self.big_text(c, time.strftime("%H:%M:%S" if secs else "%H:%M"))


class SpeedoWindow(SimpleWindow):
    """Спидометр: шесть видов — дуга, циферблат, тахометр, лента, цифровой, минимальный."""
    POS, ON = ("speedo_x", "speedo_y"), "speedo"
    WIDTH, HEIGHT, HZ = 12.0, 12.0, 30
    VIEW_KEY = "speedo_view"
    VIEWS = ("arc", "dial", "tacho", "led", "digital", "minimal")

    def view(self):
        v = self.main.cfg.get("speedo_view", "arc")
        return v if v in self.VIEWS else "arc"

    def build(self):
        super().build()
        m, rh = self.main, self.rh
        v = self._view
        S = int(rh * 11.5)
        self.W, self.H = {"arc": (S, S), "dial": (S, S), "tacho": (S, S), "led": (int(rh * 17), int(rh * 4.6)),
                          "digital": (int(rh * 10.5), int(rh * 6.2)), "minimal": (int(rh * 7.0), int(rh * 3.0))}[v]
        self.width, self.height = self.W, self.H
        self.canvas.config(width=self.W, height=self.H)
        u = min(self.W, self.H)
        self.u = u

        def F(px, w="bold"):
            return tkfont.Font(family=m.family, size=-max(8, int(px)), weight=w)
        k = {"arc": (0.34, 0.2, 0.065), "dial": (0.07, 0.05, 0.08), "tacho": (0.22, 0.2, 0.075),
             "led": (0.5, 0.42, 0.2), "digital": (0.5, 0.0, 0.1), "minimal": (0.66, 0.6, 0.2)}[v]
        self.f_g, self.f_s, self.f_t = F(u * k[0]), F(u * k[1]), F(u * k[2])
        self.f_big = F(u * (0.52 if v == "digital" else 0.2))
        self._vmax = 0.0

    # ---------- данные ----------
    def values(self, frame):
        m = self.main
        mph = bool(m.cfg.get("speed_mph", False))
        if not m.locked:
            mx = 8500.0
            return {"spd": 255.0 if not mph else 158.0, "gear": "5", "rpm": 6900.0, "max": mx, "thr": 0.8, "brk": 0.0,
                    "fuel": 16.6, "mph": mph, "live": True}
        inp = frame.get("inputs") if frame else None
        me = self.me(frame)
        d = None
        try:
            d = fuel_calc.compute(frame, float(m.cfg.get("fuel_reserve", 0.0)), m.cfg.get("fuel_avg", 5)) if frame else None
        except Exception:
            d = None
        g = inp["gear"] if inp else 0
        return {"spd": me["speed"] * (2.23694 if mph else 3.6) if me else 0.0,
                "gear": "-" if not inp else "R" if g < 0 else "N" if g == 0 else str(g),
                "rpm": inp["rpm"] if inp else 0.0, "max": (inp["max_rpm"] if inp else 0.0) or 1.0,
                "thr": inp["thr"] if inp else 0.0, "brk": inp["brk"] if inp else 0.0,
                "fuel": d["fuel"] if d else None, "mph": mph, "live": bool(inp)}

    def pt(self, cx, cy, r, ang):
        a = math.radians(ang)
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def rpm_col(self, f):
        s0 = float(self.main.cfg.get("shift_start", 82)) / 100.0
        return "#2ee26d" if f < s0 - 0.12 else "#ffc107" if f < s0 + 0.08 else "#ff3b30"

    def draw(self, frame):
        m, c = self.main, self.canvas
        c.delete("all")
        v = self.values(frame)
        self._vmax = max(self._vmax, v["spd"])
        W, H = self.W, self.H
        if self._view in ("arc", "dial", "tacho"):
            R = self.R
            if R:
                draw_rrect(c, 0, 0, W - 1, H - 1, R, fill=HDR_BG, outline=BORDER)
            else:
                c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline=BORDER)
        elif self._view != "minimal":
            if self.R:
                draw_rrect(c, 0, 0, W - 1, H - 1, self.R, fill=HDR_BG, outline=BORDER)
            else:
                c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline=BORDER)
        getattr(self, "draw_" + self._view)(c, v)

    # ---------- дуга оборотов ----------
    def draw_arc(self, c, v):
        W, u = self.W, self.u
        cx = cy = u / 2
        r = u * 0.43
        w = max(6, int(u * 0.075))
        f = max(0.0, min(1.0, v["rpm"] / v["max"]))
        box = (cx - r, cy - r, cx + r, cy + r)
        c.create_arc(*box, start=225, extent=-270, style="arc", outline=TRACK, width=w)
        if f > 0.004:
            blink = f >= float(self.main.cfg.get("shift_flash", 98.5)) / 100.0 and int(time.time() * 8) % 2 == 0
            c.create_arc(*box, start=225, extent=-270 * f, style="arc", outline="#ffffff" if blink else self.rpm_col(f), width=w)
        r2 = r - w * 1.25
        b2 = (cx - r2, cy - r2, cx + r2, cy + r2)
        w2 = max(3, int(u * 0.03))
        c.create_arc(*b2, start=245, extent=-110, style="arc", outline=TRACK, width=w2)
        c.create_arc(*b2, start=295, extent=110, style="arc", outline=TRACK, width=w2)
        if v["thr"] > 0.01:
            c.create_arc(*b2, start=245, extent=-110 * min(1, v["thr"]), style="arc", outline="#2ee26d", width=w2)
        if v["brk"] > 0.01:
            c.create_arc(*b2, start=295, extent=110 * min(1, v["brk"]), style="arc", outline="#ff3b30", width=w2)
        c.create_text(cx, cy - u * 0.2, text=v["gear"], fill=FG if v["live"] else DIM, font=self.f_g)
        c.create_text(cx, cy + u * 0.1, text=f"{v['spd']:.0f}", fill=FG, font=self.f_s)
        c.create_text(cx, cy + u * 0.23, text="MPH" if v["mph"] else "KPH", fill=DIM, font=self.f_t)
        if v["fuel"] is not None:
            c.create_text(cx, cy + u * 0.35, text=f"{v['fuel']:.1f} L", fill=FG, font=self.f_t)

    # ---------- аналоговый циферблат ----------
    def dial_face(self, c, cx, cy, r):
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#e4e6ea", outline="")
        r1 = r * 0.955
        c.create_oval(cx - r1, cy - r1, cx + r1, cy + r1, fill="#0d0e10", outline="#555")
        r2 = r * 0.9
        c.create_oval(cx - r2, cy - r2, cx + r2, cy + r2, fill="#0d0e10", outline=ACCENT)

    def draw_dial(self, c, v):
        u = self.u
        cx = cy = u / 2
        r = u * 0.47
        self.dial_face(c, cx, cy, r)
        try:
            top = float(self.main.cfg.get("speedo_max", 340))
        except (TypeError, ValueError):
            top = 340.0
        if v["mph"]:
            top = round(top * 0.62137 / 20) * 20
        top = max(100.0, top)
        step = 20 if top <= 220 else 40
        for s in range(0, int(top) + 1, 10):
            a = 225 - 270 * s / top
            big = s % step == 0
            x0, y0 = self.pt(cx, cy, r * (0.84 if big else 0.88), a)
            x1, y1 = self.pt(cx, cy, r * 0.93, a)
            c.create_line(x0, y0, x1, y1, fill=ACCENT, width=3 if big else 1)
            if big:
                tx, ty = self.pt(cx, cy, r * 0.68, a)
                c.create_text(tx, ty, text=str(s), fill=ACCENT, font=self.f_t)
        c.create_text(cx, cy + r * 0.55, text=f"{v['spd']:.0f}", fill=FG, font=self.f_big)
        c.create_text(cx + r * 0.42, cy + r * 0.28, text="mph" if v["mph"] else "km/h", fill=ACCENT, font=self.f_t)
        c.create_text(cx, cy + r * 0.82, text=f"макс {self._vmax:.0f}", fill=DIM, font=self.f_t)
        a = 225 - 270 * min(1.0, v["spd"] / top)
        xa, ya = self.pt(cx, cy, r * 0.86, a)
        xb, yb = self.pt(cx, cy, r * 0.12, a + 180)
        c.create_line(xb, yb, xa, ya, fill="#ffffff", width=max(3, int(u * 0.016)), capstyle="round")
        c.create_oval(cx - r * 0.1, cy - r * 0.1, cx + r * 0.1, cy + r * 0.1, fill="#1b1d20", outline="#555")

    # ---------- тахометр ----------
    def draw_tacho(self, c, v):
        u = self.u
        cx = cy = u / 2
        r = u * 0.47
        self.dial_face(c, cx, cy, r)
        mx = v["max"]
        top = max(1000.0, math.ceil(mx / 1000.0) * 1000.0)
        rz = 0.92
        a0 = 225 - 270 * (mx * rz / top)
        c.create_arc(cx - r * 0.97 * 0.9, cy - r * 0.97 * 0.9, cx + r * 0.97 * 0.9, cy + r * 0.97 * 0.9, start=a0,
                     extent=-(a0 - (225 - 270 * mx / top)), style="arc", outline="#c62828", width=max(3, int(u * 0.025)))
        for rpm in range(0, int(top) + 1, 500):
            a = 225 - 270 * rpm / top
            big = rpm % 1000 == 0
            x0, y0 = self.pt(cx, cy, r * (0.82 if big else 0.87), a)
            x1, y1 = self.pt(cx, cy, r * 0.93, a)
            c.create_line(x0, y0, x1, y1, fill=ACCENT if rpm <= mx * rz else "#ff3b30", width=3 if big else 1)
            if big:
                tx, ty = self.pt(cx, cy, r * 0.68, a)
                c.create_text(tx, ty, text=str(rpm // 1000), fill=FG, font=self.f_t)
        c.create_text(cx, cy - r * 0.1, text=v["gear"], fill=ACCENT if v["live"] else DIM, font=self.f_g)
        c.create_text(cx, cy + r * 0.38, text=f"{v['spd']:.0f}", fill=FG, font=self.f_s)
        c.create_text(cx, cy + r * 0.62, text="MPH" if v["mph"] else "KPH", fill=DIM, font=self.f_t)
        c.create_text(cx, cy + r * 0.82, text="x1000 rpm", fill=DIM, font=self.f_t)
        a = 225 - 270 * min(1.0, v["rpm"] / top)
        xa, ya = self.pt(cx, cy, r * 0.9, a)
        xb, yb = self.pt(cx, cy, r * 0.6, a)
        c.create_line(xb, yb, xa, ya, fill="#ff6a3d", width=max(3, int(u * 0.016)), capstyle="round")

    # ---------- лента светодиодов ----------
    def draw_led(self, c, v):
        W, H, u = self.W, self.H, self.u
        f = max(0.0, min(1.0, v["rpm"] / v["max"]))
        n, x0, x1, gap = 24, 10, W - 10, 3
        lw = (x1 - x0 - gap * (n - 1)) / n
        s0 = float(self.main.cfg.get("shift_start", 82)) / 100.0
        f0 = float(self.main.cfg.get("shift_flash", 98.5)) / 100.0
        blink = f >= f0 and int(time.time() * 8) % 2 == 0
        for i in range(n):
            th = s0 * (i + 1) / n * 0.0 + (f0 - 0.005) * (i + 1) / n
            col = "#2ee26d" if i < n * 0.45 else "#ff3b30" if i < n * 0.8 else "#4a90ff"
            lit = blink if f >= f0 else f >= th
            lx = x0 + i * (lw + gap)
            c.create_rectangle(lx, 8, lx + lw, 8 + H * 0.2, fill=col if lit else TRACK, outline="")
        cy = H * 0.62
        c.create_text(W * 0.09, cy, text=v["gear"], fill=ACCENT if v["live"] else DIM, font=self.f_g)
        c.create_text(W * 0.5, cy - 2, text=f"{v['spd']:.0f}", fill=FG, font=self.f_s)
        c.create_text(W * 0.5 + self.f_s.measure("000") / 2 + 8, cy + H * 0.16, text="MPH" if v["mph"] else "KPH", fill=DIM, anchor="w", font=self.f_t)
        for i, (val, col) in enumerate(((v["thr"], "#2ee26d"), (v["brk"], "#ff3b30"))):
            bx = W - 16 - i * 14
            c.create_rectangle(bx - 5, H * 0.4, bx + 5, H - 8, fill=TRACK, outline="")
            hh = (H - 8 - H * 0.4) * max(0.0, min(1.0, val))
            c.create_rectangle(bx - 5, H - 8 - hh, bx + 5, H - 8, fill=col, outline="")

    # ---------- цифровой ----------
    def draw_digital(self, c, v):
        W, H = self.W, self.H
        gw = W * 0.26
        draw_rrect(c, 8, 8, 8 + gw, H * 0.62, 6, fill=ACCENT if v["live"] else TRACK, outline="")
        c.create_text(8 + gw / 2, H * 0.35, text=v["gear"], fill="#111", font=self.f_g)
        c.create_text(W - 12, H * 0.31, text=f"{v['spd']:.0f}", fill=FG, anchor="e", font=self.f_big)
        c.create_text(W - 12, H * 0.57, text="MPH" if v["mph"] else "KPH", fill=DIM, anchor="e", font=self.f_t)
        f = max(0.0, min(1.0, v["rpm"] / v["max"]))
        by = H * 0.74
        c.create_rectangle(8, by, W - 8, by + H * 0.08, fill=TRACK, outline="")
        c.create_rectangle(8, by, 8 + (W - 16) * f, by + H * 0.08, fill=self.rpm_col(f), outline="")
        for i, (val, col) in enumerate(((v["thr"], "#2ee26d"), (v["brk"], "#ff3b30"))):
            yy = H * 0.86 + i * H * 0.06
            c.create_rectangle(8, yy, W - 8, yy + H * 0.045, fill=TRACK, outline="")
            c.create_rectangle(8, yy, 8 + (W - 16) * max(0.0, min(1.0, val)), yy + H * 0.045, fill=col, outline="")

    # ---------- минимальный ----------
    def draw_minimal(self, c, v):
        W, H = self.W, self.H
        c.create_rectangle(0, 0, W, H, fill=HDR_BG, outline="")
        c.create_text(W * 0.14, H / 2, text=v["gear"], fill=ACCENT if v["live"] else DIM, font=self.f_g)
        c.create_text(W * 0.74, H * 0.45, text=f"{v['spd']:.0f}", fill=FG, anchor="e", font=self.f_s)
        c.create_text(W * 0.76, H * 0.62, text="MPH" if v["mph"] else "KPH", fill=DIM, anchor="w", font=self.f_t)


class AlertsWindow(SimpleWindow):
    POS, ON = ("alerts_x", "alerts_y"), "alerts"
    OWN_ALPHA = True
    WIDTH, HEIGHT, HZ = 36.0, 2.3, 6
    COLORS = {3: ("#c62828", "#ffffff"), 2: ("#f2b705", "#111111"), 1: ("#1e5aa8", "#ffffff")}
    KIND_COLORS = {"red": ("#c62828", "#ffffff"), "yellow": ("#f2b705", "#111111"), "blue": ("#1e5aa8", "#ffffff"),
                   "white": ("#f4f4f4", "#111111")}

    def build(self):
        super().build()
        self._a = None

    def tick(self, frame, ver):
        m = self.main
        items = alert_engine.active() if frame else []
        if not m.locked:
            items = [(3, "ПРИМЕР: ШТРАФ", "red"), (2, "ЖЁЛТЫЙ ФЛАГ", "yellow"), (2, "СИНИЙ ФЛАГ", "blue"), (1, "ПОСЛЕДНИЙ КРУГ", "white")]
        show = bool(items) and not m._m.hid(self.ON)
        want = m.cfg.get("op_alerts", 1.0) if show else 0.0
        if want != self._a:
            self._a = want
            self.top.attributes("-alpha", want)
        if not show:
            return
        sig = (tuple(items), int(time.time() * 2), self.main.cfg["font"])
        if sig != self._sig:
            self._sig = sig
            lv, text, kind = items[0]
            bg, fg = self.KIND_COLORS.get(kind) or self.COLORS.get(lv, self.COLORS[1])
            if lv == 3 and int(time.time() * 2) % 2:
                bg = "#8e1b1b" if kind != "white" else bg
            c, W, H = self.canvas, self.width, self.height
            c.delete("all")
            if self.R:
                draw_rrect(c, 0, 0, W - 1, H - 1, self.R, fill=bg, outline=BORDER)
            else:
                c.create_rectangle(0, 0, W - 1, H - 1, fill=bg, outline=BORDER)
            c.create_text(W // 2, H // 2, text=text, fill=fg, font=self.f_mid)
            if len(items) > 1:
                c.create_text(W - 12, 6, text=f"+{len(items) - 1}", fill=fg, anchor="ne", font=m.small)


def _temp_color(t):
    return "#4da3ff" if t < 65 else "#2ee26d" if t < 105 else "#ffc107" if t < 118 else "#ff3b30"


class TyresWindow(SimpleWindow):
    POS, ON = ("tyres_x", "tyres_y"), "tyres"
    WIDTH, HEIGHT, HZ = 38.0, 12.0, 4
    VIEW_KEY = "tyres_view"

    def view(self):
        v = self.main.cfg.get("tyres_view", "table")
        return v if v in ("compact", "wear") else "table"

    def dims(self, view):
        return {"compact": (19.5, 12.4), "wear": (40.0, 6.0)}.get(view, (38.0, 12.0))

    def draw_wear(self, c, wh, W, H, rh):
        """Остаток протектора каждого колеса (число и полоса) и износ за круг справа от него."""
        m, cw = self.main, self.cw
        per = wear_tracker.per_lap()
        if not self.main.locked and per is None:
            per = [1.6, 0.8, 1.2, 1.1]                      # пример в режиме перемещения
        avg = (sum(per) / 4.0) if per else 0.0
        cell = (W - 28 - int(cw * 2)) // 2
        for i, w in enumerate(wh):
            x0 = 14 + (i % 2) * (cell + int(cw * 2))
            cy = int(rh * 2.3) + (i // 2) * int(rh * 2.2)
            pct = max(0.0, min(1.0, w["wear"]))
            wc = "#2ee26d" if pct > 0.5 else "#ffc107" if pct > 0.25 else "#ff3b30"
            vw = int(cw * 4.6)
            c.create_text(x0, cy, text=f"{pct * 100:.1f}", fill=wc, anchor="w", font=self.f_mid)
            bx0, bx1 = x0 + vw, x0 + cell - int(cw * 5.4)
            bh = max(6, int(rh * 0.55))
            c.create_rectangle(bx0, cy - bh // 2, bx1, cy + bh // 2, fill=TRACK, outline="")
            c.create_rectangle(bx0, cy - bh // 2, bx0 + (bx1 - bx0) * pct, cy + bh // 2, fill=wc, outline="")
            if per:
                v = per[i]
                lc = "#2ee26d" if v <= avg * 0.9 else "#ff5a4f" if v >= avg * 1.15 else "#ffc107"
                sq = max(5, int(rh * 0.38))
                c.create_rectangle(x0 + cell - int(cw * 4.8), cy - sq // 2, x0 + cell - int(cw * 4.8) + sq, cy + sq // 2, fill=lc, outline="")
                c.create_text(x0 + cell - int(cw * 4.8) + sq + 5, cy, text=f"{v:.1f}", fill=FG, anchor="w", font=m.small)
            else:
                c.create_text(x0 + cell - int(cw * 3.0), cy, text="—", fill=DIM, anchor="w", font=m.small)
        c.create_text(W - 14, H - int(rh * 0.55), text="остаток % и износ за круг", fill=DIM, anchor="e", font=m.small)

    def draw_compact(self, c, wh, W, H, rh):
        """Машина сверху: четыре шины, внутри каждой температура, давление, остаток и тормоз."""
        m, cw = self.main, self.cw
        tw, th = int(cw * 7.2), int(rh * 4.9)
        gap = W - 2 * 12 - 2 * tw
        top = int(rh * 1.5)
        # кузов между шинами
        bx0, bx1 = 12 + tw + int(gap * 0.22), 12 + tw + int(gap * 0.78)
        by0, by1 = top + int(th * 0.15), top + 2 * th + int(rh * 0.5) + int(th * 0.85)
        c.create_rectangle(bx0, by0, bx1, by1, fill="#161d27", outline=BORDER, width=2)
        c.create_line((bx0 + bx1) // 2, by0, (bx0 + bx1) // 2, by1, fill=BORDER)
        for i, w in enumerate(wh):
            left = (i % 2 == 0)
            x0 = 12 if left else W - 12 - tw
            y0 = top + (i // 2) * (th + int(rh * 0.5))
            col = _temp_color(w["temp"])
            c.create_rectangle(x0, y0, x0 + tw, y0 + th, fill="#0e131a", outline=col, width=3)
            # остаток протектора — вертикальная полоса по внешнему краю
            pct = max(0.0, min(1.0, w["wear"]))
            wc = "#2ee26d" if pct > 0.5 else "#ffc107" if pct > 0.25 else "#ff3b30"
            bw = max(4, int(cw * 0.5))
            ox = x0 + 5 if left else x0 + tw - 5 - bw
            c.create_rectangle(ox, y0 + 5, ox + bw, y0 + th - 5, fill=TRACK, outline="")
            c.create_rectangle(ox, y0 + th - 5 - (th - 10) * pct, ox + bw, y0 + th - 5, fill=wc, outline="")
            tx = x0 + tw // 2 + (bw // 2 + 2 if left else -(bw // 2 + 2))
            c.create_text(tx, y0 + int(th * 0.24), text=(f"{w['temp']:.0f}°" if w['temp'] else "—"), fill=col, font=self.f_mid)
            if w["press"]:
                c.create_text(tx, y0 + int(th * 0.50), text=f"{w['press'] / 100.0:.2f}", fill=FG, font=m.small)
            c.create_text(tx, y0 + int(th * 0.70), text=f"{pct * 100:.0f}%", fill=wc, font=m.small)
            if w["brake"] > 0:
                bcol = "#4da3ff" if w["brake"] < 250 else "#2ee26d" if w["brake"] < 800 else "#ffc107" if w["brake"] < 950 else "#ff3b30"
                c.create_text(tx, y0 + int(th * 0.88), text=f"т{w['brake']:.0f}", fill=bcol, font=m.small)

    def draw(self, frame):
        m, rh, W = self.main, self.rh, self.width
        me = self.me(frame)
        wh = me.get("wheels") if me else None
        comp = f"{compound_letter(me['front'])}/{compound_letter(me['rear'])}" if me and me.get("front") else ""
        c, cy = _card(self, {"compact": "ШИНЫ", "wear": "ИЗНОС ШИН"}.get(self._view, "ШИНЫ И ТОРМОЗА"), comp)
        if not wh:
            c.create_text(W // 2, self.height // 2, text="жду данные шин", fill=DIM, font=m.small)
            return
        if self._view == "compact":
            return self.draw_compact(c, wh, W, self.height, rh)
        if self._view == "wear":
            return self.draw_wear(c, wh, W, self.height, rh)
        names = ("ПЕРЕДНЯЯ ЛЕВАЯ", "ПЕРЕДНЯЯ ПРАВАЯ", "ЗАДНЯЯ ЛЕВАЯ", "ЗАДНЯЯ ПРАВАЯ")
        cw2 = (W - 28) // 2
        for i, w in enumerate(wh):
            x0 = 14 + (i % 2) * cw2
            y0 = int(rh * 1.75) + (i // 2) * int(rh * 4.9)
            c.create_text(x0, y0, text=names[i], fill=DIM, anchor="w", font=m.small)
            c.create_text(x0, y0 + int(rh * 1.0), text=(f"{w['temp']:.0f}°" if w['temp'] else "—"), fill=_temp_color(w["temp"]), anchor="w", font=self.f_mid)
            if w["press"]:
                c.create_text(x0 + cw2 - 16, y0 + int(rh * 1.0), text=f"{w['press'] / 100.0:.2f} бар", fill=FG, anchor="e", font=m.small)
            pct = max(0.0, min(1.0, w["wear"]))
            wc = "#2ee26d" if pct > 0.5 else "#ffc107" if pct > 0.25 else "#ff3b30"
            by = y0 + int(rh * 1.65)
            c.create_rectangle(x0, by, x0 + cw2 - 16, by + int(rh * 0.3), fill=TRACK, outline="")
            c.create_rectangle(x0, by, x0 + (cw2 - 16) * pct, by + int(rh * 0.3), fill=wc, outline="")
            c.create_text(x0, y0 + int(rh * 2.25), text=f"остаток {pct * 100:.0f}%", fill=DIM, anchor="w", font=m.small)
            if w["brake"] > 0:
                bcol = "#4da3ff" if w["brake"] < 250 else "#2ee26d" if w["brake"] < 800 else "#ffc107" if w["brake"] < 950 else "#ff3b30"
                c.create_text(x0, y0 + int(rh * 2.95), text=f"тормоз {w['brake']:.0f}°", fill=bcol, anchor="w", font=m.small)
        c.create_line(14, int(rh * 6.15), W - 14, int(rh * 6.15), fill=BORDER)


def _water_color(t):
    return "#4da3ff" if t < 70 else "#2ee26d" if t < 100 else "#ffc107" if t < 110 else "#ff3b30"


def _oil_color(t):
    return "#4da3ff" if t < 80 else "#2ee26d" if t < 120 else "#ffc107" if t < 135 else "#ff3b30"


class EngineWindow(SimpleWindow):
    """Температура воды и масла, расход топлива и остаток резины. Виды: обычный, компактный, циферблаты."""
    POS, ON = ("engine_x", "engine_y"), "engine"
    WIDTH, HEIGHT, HZ = 30.0, 12.4, 5
    VIEW_KEY = "engine_view"

    def view(self):
        v = self.main.cfg.get("engine_view", "table")
        return v if v in ("compact", "gauges") else "table"

    def dims(self, view):
        return {"compact": (68.0, 2.3), "gauges": (27.0, 11.4)}.get(view, (30.0, 12.4))

    def data(self, frame):
        m = self.main
        inp = frame.get("inputs") if frame else None
        me = self.me(frame)
        d = fuel_calc.compute(frame, float(m.cfg.get("fuel_reserve", 0.0)), m.cfg.get("fuel_avg", 5)) if frame else None
        wh = me.get("wheels") if me else None
        out = {"water": inp.get("water") if inp else None, "oil": inp.get("oil") if inp else None, "fuel": d,
               "wear": [x["wear"] for x in wh] if wh else (list(me["tyre_wear"]) if me and me.get("tyre_wear") else None)}
        if not m.locked:                                           # пример в режиме перемещения
            out["water"], out["oil"] = out["water"] or 91.0, out["oil"] or 108.0
            out["fuel"] = out["fuel"] or {"fuel": 31.4, "cap": 60.0, "last": 3.72, "avg": 3.69, "laps_fuel": 8.5}
            out["wear"] = out["wear"] or [0.91, 0.88, 0.84, 0.86]
        return out

    @staticmethod
    def _wear_col(p):
        return "#2ee26d" if p > 0.5 else "#ffc107" if p > 0.25 else "#ff3b30"

    def temp_bar(self, c, x0, x1, cy, val, lo, hi, col):
        bh = max(6, int(self.rh * 0.42))
        c.create_rectangle(x0, cy - bh // 2, x1, cy + bh // 2, fill=TRACK, outline="")
        if val is not None:
            f = max(0.0, min(1.0, (val - lo) / (hi - lo)))
            c.create_rectangle(x0, cy - bh // 2, x0 + (x1 - x0) * f, cy + bh // 2, fill=col, outline="")

    def draw_table(self, c, W, d):
        m, rh = self.main, self.rh
        y = int(rh * 1.8)
        for name, val, lo, hi, colf in (("ВОДА", d["water"], 40, 130, _water_color), ("МАСЛО", d["oil"], 40, 150, _oil_color)):
            c.create_text(14, y, text=name, fill=DIM, anchor="w", font=m.small)
            col = colf(val) if val is not None else DIM
            c.create_text(W - 14, y, text=f"{val:.0f}°" if val is not None else "—", fill=col, anchor="e", font=self.f_mid)
            self.temp_bar(c, 14 + int(self.cw * 6.5), W - 14 - int(self.cw * 4.6), y, val, lo, hi, col)
            y += int(rh * 1.25)
        c.create_line(14, y - int(rh * 0.5), W - 14, y - int(rh * 0.5), fill=BORDER)
        f = d["fuel"]
        c.create_text(14, y + int(rh * 0.2), text="ТОПЛИВО", fill=DIM, anchor="w", font=m.small)
        if f:
            c.create_text(W - 14, y + int(rh * 0.2), text=f"{f['fuel']:.1f} л", fill=FG, anchor="e", font=m.bold)
            per = f"{f['avg']:.2f}" if f.get("avg") else (f"{f['last']:.2f}" if f.get("last") else "—")
            laps = f"  •  хватит на {f['laps_fuel']:.1f} кр." if f.get("laps_fuel") else ""
            c.create_text(14, y + int(rh * 1.1), text=f"расход {per} л/кр{laps}", fill=FG, anchor="w", font=m.small)
        else:
            c.create_text(W - 14, y + int(rh * 0.2), text="—", fill=DIM, anchor="e", font=m.bold)
        y += int(rh * 2.0)
        c.create_line(14, y - int(rh * 0.45), W - 14, y - int(rh * 0.45), fill=BORDER)
        c.create_text(14, y + int(rh * 0.05), text="ОСТАТОК РЕЗИНЫ", fill=DIM, anchor="w", font=m.small)
        self.tyres_grid(c, W, y + int(rh * 1.1), d["wear"])

    def tyres_grid(self, c, W, y, w):
        """Остаток резины 2x2 как в панели «Износ шин»: число и полоса у каждого колеса."""
        m, rh, cw = self.main, self.rh, self.cw
        cell = (W - 28 - int(cw * 2)) // 2
        for i in range(4):
            x0 = 14 + (i % 2) * (cell + int(cw * 2))
            cy = y + (i // 2) * int(rh * 1.7)
            p = max(0.0, min(1.0, w[i])) if w else None
            col = self._wear_col(p) if p is not None else DIM
            c.create_text(x0, cy, text=f"{p * 100:.1f}" if p is not None else "—", fill=col, anchor="w", font=self.f_mid)
            bx0, bx1 = x0 + int(cw * 4.6), x0 + cell
            bh = max(6, int(rh * 0.55))
            c.create_rectangle(bx0, cy - bh // 2, bx1, cy + bh // 2, fill=TRACK, outline="")
            if p is not None:
                c.create_rectangle(bx0, cy - bh // 2, bx0 + (bx1 - bx0) * p, cy + bh // 2, fill=col, outline="")

    def draw_compact(self, c, W, H, d):
        """Одна строка: вода, масло, топливо и резина."""
        m, cy = self.main, H // 2
        x = 14
        for tag, val, colf in (("В", d["water"], _water_color), ("М", d["oil"], _oil_color)):
            c.create_text(x, cy, text=tag, fill=DIM, anchor="w", font=m.small)
            c.create_text(x + int(self.cw * 1.5), cy, text=f"{val:.0f}°" if val is not None else "—",
                          fill=colf(val) if val is not None else DIM, anchor="w", font=self.f_mid)
            x += int(self.cw * 7.2)
        c.create_line(x - 6, 6, x - 6, H - 6, fill=BORDER)
        f = d["fuel"]
        txt = "—"
        if f:
            per = f.get("avg") or f.get("last")
            txt = f"{f['fuel']:.1f} л" + (f"  {per:.2f}/кр" if per else "") + (f"  ~{f['laps_fuel']:.0f} кр" if f.get("laps_fuel") else "")
        c.create_text(x, cy, text="⛽", fill=DIM, anchor="w", font=m.small)
        c.create_text(x + int(self.cw * 2.2), cy, text=txt, fill=FG, anchor="w", font=m.font)
        x += int(self.cw * 20.0)
        c.create_line(x - 6, 6, x - 6, H - 6, fill=BORDER)
        w = d["wear"]
        c.create_text(x, cy, text="ШИНЫ", fill=DIM, anchor="w", font=m.small)
        x += int(self.cw * 5.0)
        for i in range(4):
            p = max(0.0, min(1.0, w[i])) if w else None
            c.create_text(x, cy, text=f"{p * 100:.0f}" if p is not None else "—", fill=self._wear_col(p) if p is not None else DIM,
                          anchor="w", font=m.font)
            x += int(self.cw * 3.6)

    def dial(self, c, cx, cy, r, val, lo, hi, col, title, unit="°"):
        a0, span = 225.0, 270.0                                   # дуга 270°, начало слева снизу
        c.create_arc(cx - r, cy - r, cx + r, cy + r, start=a0 - span, extent=span, style="arc", outline=TRACK, width=max(5, int(r * 0.18)))
        if val is not None:
            f = max(0.0, min(1.0, (val - lo) / (hi - lo)))
            if f > 0.005:
                c.create_arc(cx - r, cy - r, cx + r, cy + r, start=a0, extent=-span * f, style="arc", outline=col, width=max(5, int(r * 0.18)))
        c.create_text(cx, cy - int(r * 0.08), text=f"{val:.0f}{unit}" if val is not None else "—", fill=col if val is not None else DIM, font=self.f_mid)
        c.create_text(cx, cy + int(r * 0.52), text=title, fill=DIM, font=self.main.small)

    def draw_gauges(self, c, W, H, d):
        m, rh = self.main, self.rh
        r = int(min(rh * 1.9, W / 4 - 10))
        cy = int(rh * 3.55)
        self.dial(c, W // 4 + 2, cy, r, d["water"], 40, 130, _water_color(d["water"]) if d["water"] is not None else DIM, "ВОДА")
        self.dial(c, W * 3 // 4 - 2, cy, r, d["oil"], 40, 150, _oil_color(d["oil"]) if d["oil"] is not None else DIM, "МАСЛО")
        y = int(rh * 6.6)
        c.create_line(14, y - int(rh * 0.4), W - 14, y - int(rh * 0.4), fill=BORDER)
        f = d["fuel"]
        if f:
            per = f.get("avg") or f.get("last")
            c.create_text(14, y, text=f"{f['fuel']:.1f} л", fill=FG, anchor="w", font=m.bold)
            c.create_text(W - 14, y, text=(f"{per:.2f} л/кр" if per else "—") + (f"  ~{f['laps_fuel']:.0f} кр" if f.get("laps_fuel") else ""),
                          fill=DIM, anchor="e", font=m.small)
        else:
            c.create_text(14, y, text="топливо —", fill=DIM, anchor="w", font=m.small)
        self.tyres_grid(c, W, y + int(rh * 1.7), d["wear"])

    def draw(self, frame):
        W, H = self.width, self.height
        title = {"compact": "", "gauges": "ДВИГАТЕЛЬ"}.get(self._view, "ДВИГАТЕЛЬ И РАСХОД")
        d = self.data(frame)
        if self._view == "compact":
            c = self.canvas
            c.delete("all")
            if self.R:
                draw_rrect(c, 0, 0, W - 1, H - 1, self.R, fill=HDR_BG, outline=BORDER)
            else:
                c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline=BORDER)
            return self.draw_compact(c, W, H, d)
        c, cy = _card(self, title)
        if self._view == "gauges":
            return self.draw_gauges(c, W, H, d)
        return self.draw_table(c, W, d)


class PitPlanWindow(SimpleWindow):
    POS, ON = ("pitplan_x", "pitplan_y"), "pitplan"
    WIDTH, HEIGHT, HZ = 40.0, 12.4, 4

    def draw(self, frame):
        m, rh, W = self.main, self.rh, self.width
        c, cy = _card(self, "ПИТ-СТОП И СТИНТ")
        me = self.me(frame)
        if not me:
            c.create_text(W // 2, self.height // 2, text="жду данные игры", fill=DIM, font=m.small)
            return
        st = stint_tracker
        y = int(rh * 1.7)

        def row(label, value, col=FG, big=False):
            nonlocal y
            c.create_text(14, y, text=label, fill=DIM, anchor="w", font=m.small)
            c.create_text(W - 14, y, text=value, fill=col, anchor="e", font=m.bold if big else m.font)
            y += int(rh * 0.95)
        stint_t = (st.now - st.stint_t0) if st.stint_t0 is not None else 0.0
        row("Стинт", f"{_hm(stint_t)}  •  {max(0, me['laps'] - (st.stint_lap0 or 0))} кр.")
        d = fuel_calc.compute(frame, float(m.cfg.get("fuel_reserve", 0.0)), m.cfg.get("fuel_avg", 5)) if frame else None
        lf = d["laps_fuel"] if d else None
        v = d.get("ve") if d else None
        lv = v["laps"] if v else None
        row("Топлива хватит на", f"{lf:.1f} кр." if lf is not None else "—")
        if v:
            row("Энергии хватит на", f"{lv:.1f} кр." if lv is not None else "—")
        cands = [(x, n) for x, n in ((lf, "топливу"), (lv, "энергии")) if x is not None]
        if cands:
            x, why = min(cands)
            nxt = int(x) - 1
            txt, col = ((f"через {nxt} кр. (по {why})", FG) if nxt >= 1 else ("в этот круг (по " + why + ")", "#ff5a4f"))
            row("Заезд в боксы", txt, col, big=True)
        else:
            row("Заезд в боксы", "—", DIM)
        if d and d.get("stops") is not None:
            row("Стопов до финиша (топливо)", str(d["stops"]))
        y += int(rh * 0.2)
        c.create_line(14, y - int(rh * 0.3), W - 14, y - int(rh * 0.3), fill=BORDER)
        if st.in_stop and (me["in_pits"] or me["garage"]):
            row("В боксах сейчас", _hm(st.now - (st.pit_in_t or st.now)) , "#ffc107", big=True)
            row("Стоянка", f"{st.stand:.1f} с", "#ffc107")
        elif st.last_stop:
            row("Последний стоп: пит-лейн", f"{st.last_stop[0]:.1f} с")
            row("Последний стоп: стоянка", f"{st.last_stop[1]:.1f} с")
        else:
            row("Пит-стопов пока не было", "")
        if st.drive:
            y += int(rh * 0.15)
            for name, t in sorted(st.drive.items(), key=lambda kv: -kv[1])[:3]:
                row("За рулём " + name.split()[-1][:12], _hm(t), ACCENT if name == me["driver"] else FG)


class GapsWindow(SimpleWindow):
    POS, ON = ("gaps_x", "gaps_y"), "gaps"
    WIDTH, HEIGHT, HZ = 38.0, 7.4, 4

    def draw(self, frame):
        m, rh, W = self.main, self.rh, self.width
        nb = gap_tracker.neighbours(frame) if frame else None
        c, cy = _card(self, "СОПЕРНИКИ", f"P{nb['pos']}/{nb['n']}" if nb else "")
        if not nb:
            c.create_text(W // 2, self.height // 2, text="только в гонке", fill=DIM, font=m.small)
            return
        for idx, (which, car, txt, title) in enumerate((("a", nb["ahead"], nb["ga_txt"], "ВПЕРЕДИ"), ("b", nb["behind"], nb["gb_txt"], "ПОЗАДИ"))):
            y0 = int(rh * 1.5) + idx * int(rh * 3.05)
            c.create_text(14, y0, text=title, fill=DIM, anchor="w", font=m.small)
            if not car:
                c.create_text(14, y0 + int(rh * 1.0), text="—", fill=DIM, anchor="w", font=self.f_mid)
                continue
            c.create_text(14, y0 + int(rh * 1.0), text=f"#{car['number'] or '-'} {car['driver'].split()[-1][:12].upper()}", fill=FG, anchor="w", font=m.bold)
            c.create_text(W - 14, y0 + int(rh * 0.9), text=txt, fill=FG, anchor="e", font=self.f_mid)
            tr_, vals = gap_tracker.trend(which, nb)
            if tr_ is not None:
                good = (tr_ < 0) if which == "a" else (tr_ > 0)
                col = "#2ee26d" if good else "#ff5a4f"
                arrow = "▼" if tr_ < 0 else "▲"
                s = f"{arrow} {abs(tr_):.2f} с/кр."
                cur = nb["ga" if which == "a" else "gb"]
                if cur and ((which == "a" and tr_ < -0.02) or (which == "b" and tr_ < -0.02)):
                    n = cur / -tr_
                    if n < 60:
                        s += ("  догоните через ~" if which == "a" else "  догонит через ~") + f"{n:.0f} кр."
                c.create_text(14, y0 + int(rh * 1.85), text=s, fill=col, anchor="w", font=m.small)


class Overlay:
    def __init__(self, cfg, logo_dir=None):
        self.cfg = cfg
        for k, v in (("delta", True), ("alerts", True), ("tyres", False), ("pitplan", False), ("gaps", False), ("engine", False),
                     ("sesstime", False), ("clock", False), ("speedo", False), ("speedo_view", "arc"), ("speedo_max", 340), ("st_session", True), ("st_timer", True), ("st_clock", True),
                     ("delta_show_session", True), ("delta_show_mine", True), ("delta_show_pred", True)):
            cfg.setdefault(k, v)
        self.logo_dir = logo_dir
        self._logos = {}
        self._ir_tried = set()
        self._start_pos = {}
        self._session_seen = None
        self._fuel, self._fuel_session, self._fuel_live = {}, None, set()
        self._out, self._out_session = {}, None
        self.root = r = tk.Tk()
        self.screen_sig = screen_signature(r)
        self.screen_b = screen_bounds(r)
        load_screen_layout(cfg, self.screen_sig, self.screen_b)
        r.title("LMU Standings")
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", cfg.get("op_standings", cfg["alpha"]) if cfg.get("standings", True) else 0.0)
        r.configure(bg=KEY)
        r.geometry(f"+{cfg['x']}+{cfg['y']}")
        self.rounded = False
        try:                                  # цвет KEY станет прозрачным -> скруглённые углы
            r.attributes("-transparentcolor", KEY)
            self.rounded = True
        except Exception:
            r.configure(bg=BG)
        self.family = pick_family(r, cfg.get("family"))
        self.game = "lmu"
        self.canvas = tk.Canvas(r, width=800, height=400, bg=KEY if self.rounded else BG, highlightthickness=0)
        self.canvas.pack()
        self._h = 0
        self.rel = self.ped = self.map = self.radar = self.weather = self.lights = self.limiter = self.fuel = None
        self.delta = self.alerts = self.tyres = self.pitplan = self.gaps = self.engine = self.sesstime = self.clock = self.speedo = None
        self.subs = []                                   # дополнительные окна: релатив, ввод
        self.rebuild()
        cfg.setdefault("rel_x", cfg["x"] + self.width + 24)      # по умолчанию — справа от таблицы
        cfg.setdefault("rel_y", cfg["y"])
        self.locked = bool(cfg.get("locked", True))
        self.hidden = False
        self.user_hidden = False
        self._key_state = {"lock": False, "quit": False, "reset": False}

        self.menu = tk.Menu(r, tearoff=0)
        for label, what, delta in [("Строк больше", "rows", +2), ("Строк меньше", "rows", -2),
                                   ("Шрифт крупнее", "font", +1), ("Шрифт мельче", "font", -1),
                                   ("Менее прозрачное", "alpha", +0.1), ("Более прозрачное", "alpha", -0.1)]:
            self.menu.add_command(label=label, command=lambda w=what, d=delta: self.change(w, d))
        self.menu.add_separator()
        self.class_var = tk.BooleanVar(value=bool(cfg.get("class_only", False)))
        self.menu.add_checkbutton(label="Только мой класс", variable=self.class_var, command=self.toggle_class)
        self.rel_var = tk.BooleanVar(value=bool(cfg.get("relative", True)))
        self.menu.add_checkbutton(label="Релатив (3 впереди / 3 позади)", variable=self.rel_var, command=self.toggle_rel)
        self.map_var = tk.BooleanVar(value=bool(cfg.get("trackmap", True)))
        self.menu.add_checkbutton(label="Карта трассы", variable=self.map_var, command=self.toggle_map)
        smm = tk.Menu(self.menu, tearoff=0)
        self.smode_var = tk.StringVar(value=self.stand_mode())
        for v, lab in (("table", "таблица (как сейчас)"), ("compact", "компактная: топ-3 и соседи"), ("ticker", "бегущая строка")):
            smm.add_radiobutton(label=lab, variable=self.smode_var, value=v, command=self.set_smode)
        self.menu.add_cascade(label="Тайминг: вид", menu=smm)
        mm = tk.Menu(self.menu, tearoff=0)
        self.mmode_var = tk.StringVar(value=cfg.get("map_mode", "full"))
        for v, lab in (("full", "полная карта"), ("linear", "линейная (круг полосой)"), ("local", "местная (вокруг машины)")):
            mm.add_radiobutton(label=lab, variable=self.mmode_var, value=v, command=self.set_mmode)
        self.menu.add_cascade(label="Карта: вид", menu=mm)
        dm = tk.Menu(self.menu, tearoff=0)
        self.dview_var = tk.StringVar(value=cfg.get("delta_view", "full"))
        for v, lab in (("full", "полная панель"), ("bar", "только полоска"), ("sectors", "время круга и сектора")):
            dm.add_radiobutton(label=lab, variable=self.dview_var, value=v, command=self.set_dview)
        self.menu.add_cascade(label="Дельта: вид", menu=dm)
        tm = tk.Menu(self.menu, tearoff=0)
        self.tview_var = tk.StringVar(value=cfg.get("tyres_view", "table"))
        for v, lab in (("table", "подробная"), ("compact", "компактная (шины рисунком)"), ("wear", "износ: остаток и за круг")):
            tm.add_radiobutton(label=lab, variable=self.tview_var, value=v, command=self.set_tview)
        self.menu.add_cascade(label="Шины: вид", menu=tm)
        rm = tk.Menu(self.menu, tearoff=0)
        self.mrad_var = tk.IntVar(value=int(cfg.get("map_radius", 500)))
        for v in (200, 300, 500, 800, 1200):
            rm.add_radiobutton(label=f"радиус {v} м", variable=self.mrad_var, value=v, command=self.set_mrad)
        self.menu.add_cascade(label="Карта: радиус местной", menu=rm)
        self.headup_var = tk.BooleanVar(value=bool(cfg.get("map_headup", True)))
        self.menu.add_checkbutton(label="Карта: местная — «вперёд» вверх", variable=self.headup_var, command=self.set_headup)
        self.menu.add_command(label="Карта: повернуть на 90°", command=self.rotate_map)
        self.mirror_var = tk.BooleanVar(value=bool(cfg.get("map_mirror", False)))
        self.menu.add_checkbutton(label="Карта: отразить зеркально", variable=self.mirror_var, command=self.mirror_map)
        self.menu.add_command(label="Карта: крупнее", command=lambda: self.scale_map(+0.15))
        self.menu.add_command(label="Карта: мельче", command=lambda: self.scale_map(-0.15))
        self.menu.add_command(label="Карта: стереть и записать заново", command=mapper.clear)
        self.stand_var = tk.BooleanVar(value=bool(cfg.get("standings", True)))
        self.menu.add_checkbutton(label="Таблица (стендинг)", variable=self.stand_var, command=self.toggle_standings)
        self.weather_var = tk.BooleanVar(value=bool(cfg.get("weather", True)))
        self.menu.add_checkbutton(label="Погода", variable=self.weather_var, command=lambda: self._toggle_sub(self.weather, self.weather_var))
        self.fuel_var = tk.BooleanVar(value=bool(cfg.get("fuel", True)))
        self.menu.add_checkbutton(label="Топливный калькулятор", variable=self.fuel_var, command=lambda: self._toggle_sub(self.fuel, self.fuel_var))
        self.menu.add_command(label="Топливо: крупнее", command=lambda: self.scale_fuel(+0.1))
        self.menu.add_command(label="Топливо: мельче", command=lambda: self.scale_fuel(-0.1))
        resm = tk.Menu(self.menu, tearoff=0)
        self.res_var = tk.DoubleVar(value=float(cfg.get("fuel_reserve", 0.0)))
        for v in (0.0, 0.5, 1.0, 2.0):
            resm.add_radiobutton(label=f"запас {v:g} круга" if v else "без запаса", variable=self.res_var, value=v,
                                 command=self.set_reserve)
        self.menu.add_cascade(label="Топливо: запас", menu=resm)
        self.new_vars = {}
        for key, label in (("delta", "Дельта к лучшему кругу"), ("alerts", "Предупреждения"),
                           ("tyres", "Шины и тормоза"), ("pitplan", "Пит-стоп и стинт"), ("gaps", "Соперники: гэпы и тренд"),
                           ("engine", "Двигатель и расход"), ("sesstime", "Таймер сессии"), ("clock", "Локальное время"), ("speedo", "Спидометр")):
            self.new_vars[key] = tk.BooleanVar(value=bool(cfg.get(key)))
            self.menu.add_checkbutton(label=label, variable=self.new_vars[key],
                                      command=lambda k=key: self._toggle_sub(getattr(self, k), self.new_vars[k]))
        self.lim_var = tk.BooleanVar(value=bool(cfg.get("limiter", True)))
        self.menu.add_checkbutton(label="Пит-лимитер", variable=self.lim_var, command=lambda: self._toggle_sub(self.limiter, self.lim_var))
        self.lights_var = tk.BooleanVar(value=bool(cfg.get("lights", True)))
        self.menu.add_checkbutton(label="Стартовые огни", variable=self.lights_var, command=lambda: self._toggle_sub(self.lights, self.lights_var))
        self.radar_var = tk.BooleanVar(value=bool(cfg.get("radar", True)))
        self.menu.add_checkbutton(label="Радар", variable=self.radar_var, command=self.toggle_radar)
        self.menu.add_command(label="Радар: крупнее", command=lambda: self.scale_radar(+0.15))
        self.menu.add_command(label="Радар: мельче", command=lambda: self.scale_radar(-0.15))
        self.menu.add_command(label="Радар: дальность больше", command=lambda: self.range_radar(+1))
        self.menu.add_command(label="Радар: дальность меньше", command=lambda: self.range_radar(-1))
        self.ped_var = tk.BooleanVar(value=bool(cfg.get("pedals", True)))
        self.menu.add_checkbutton(label="Ввод: руль, педали, передача", variable=self.ped_var, command=self.toggle_ped)
        self.shift_var = tk.BooleanVar(value=bool(cfg.get("shiftlight", True)))
        self.menu.add_checkbutton(label="Ввод: шифт-лайт", variable=self.shift_var, command=self.toggle_shift)
        self.dref_var = tk.BooleanVar(value=cfg.get("delta_ref", "best") == "last")
        self.menu.add_checkbutton(label="Дельта: к прошлому кругу (иначе к лучшему)", variable=self.dref_var, command=self.set_dref)
        lm = tk.Menu(self.menu, tearoff=0)
        self.lmode_var = tk.StringVar(value=self.layout_mode())
        for v, lab in (("off", "одна для всех"), ("session", "по типу сессии (практика / квала / гонка)"), ("class", "по классу машины")):
            lm.add_radiobutton(label=lab, variable=self.lmode_var, value=v, command=self.set_layouts)
        self.menu.add_cascade(label="Раскладки окон", menu=lm)
        self.hide_var = tk.BooleanVar(value=bool(cfg.get("hide_garage", True)))
        self.menu.add_checkbutton(label="Скрывать в гараже", variable=self.hide_var, command=self.toggle_hide)
        cols = tk.Menu(self.menu, tearoff=0)
        self.col_vars = {}
        for key, label in TOGGLE_COLS:
            self.col_vars[key] = tk.BooleanVar(value=key not in cfg.get("hide_cols", []))
            cols.add_checkbutton(label=label, variable=self.col_vars[key], command=self.toggle_cols)
        self.menu.add_cascade(label="Колонки", menu=cols)
        self.menu.add_command(label="Закрепить на экране (" + self.hk_text("lock") + ")", command=self.toggle_lock)
        self.menu.add_command(label="Вернуть окна на экран (" + self.hk_text("reset") + ")", command=self.reset_layout)
        self.menu.add_separator()
        self.menu.add_command(label="Закрыть", command=self.close)
        for w in (r, self.canvas):
            w.bind("<ButtonPress-1>", self.drag_start)
            w.bind("<B1-Motion>", self.drag_move)
            w.bind("<ButtonRelease-1>", self.drag_end)
        self.canvas.bind("<Motion>", lambda e: set_edge_cursor(self.canvas, self.locked, e))
        _od = self.canvas.delete
        self._fresh = False

        def _delete(*a, **k):
            if a and a[0] == "all":
                self._fresh = True
            return _od(*a, **k)
        self.canvas.delete = _delete
        self.canvas.bind("<MouseWheel>", lambda e: self.scale_standings(0.05 if e.delta > 0 else -0.05))   # колесо — размер таблицы
        self.rel = RelativeWindow(self)
        self.rel.fit_on_screen()
        cfg.setdefault("ped_x", cfg["rel_x"])                    # окно ввода — под релативом (или над ним, если внизу нет места)
        cfg.setdefault("ped_y", self._below_or_above(self.rel, int(self.rh * 3.3)))
        self.ped = PedalsWindow(self)
        self.ped.fit_on_screen()
        cfg.setdefault("map_x", cfg["ped_x"])                    # карта — под окном ввода (или над ним)
        cfg.setdefault("map_y", self._below_or_above(self.ped, int(self.rh * 14.2)))
        self.map = MapWindow(self)
        self.map.fit_on_screen()
        cfg.setdefault("radar_x", min(cfg["rel_x"] + self.rel.width + 20, max(0, int(r.winfo_screenwidth()) - 300)))
        cfg.setdefault("radar_y", cfg["rel_y"])
        self.radar = RadarWindow(self)
        self.radar.fit_on_screen()
        cfg.setdefault("wx_x", min(cfg["radar_x"], max(0, int(r.winfo_screenwidth()) - 260)))
        cfg.setdefault("wx_y", cfg["radar_y"] + self.radar.height + 20)
        self.weather = WeatherWindow(self)
        self.weather.fit_on_screen()
        cfg.setdefault("lights_x", max(0, int(r.winfo_screenwidth()) // 2 - 150))
        cfg.setdefault("lights_y", 40)
        self.lights = StartLightsWindow(self)
        self.lights.fit_on_screen()
        cfg.setdefault("lim_x", cfg["lights_x"])
        cfg.setdefault("lim_y", cfg["lights_y"] + self.lights.height + 14)
        self.limiter = PitLimiterWindow(self)
        self.limiter.fit_on_screen()
        cfg.setdefault("fuel_x", cfg["map_x"])
        cfg.setdefault("fuel_y", self._below_or_above(self.map, int(self.rh * 11.2)))
        self.fuel = FuelWindow(self)
        self.fuel.fit_on_screen()
        sw_ = int(r.winfo_screenwidth())
        cfg.setdefault("delta_x", cfg["lim_x"])
        cfg.setdefault("delta_y", cfg["lim_y"] + self.limiter.height + 14)
        self.delta = DeltaWindow(self)
        self.delta.fit_on_screen()
        cfg.setdefault("alerts_x", max(0, sw_ // 2 - 260))
        cfg.setdefault("alerts_y", 120)
        self.alerts = AlertsWindow(self)
        self.alerts.fit_on_screen()
        cfg.setdefault("tyres_x", max(0, min(cfg["wx_x"] + 270, sw_ - 330)))
        cfg.setdefault("tyres_y", cfg["wx_y"])
        self.tyres = TyresWindow(self)
        self.tyres.fit_on_screen()
        cfg.setdefault("pitplan_x", cfg["tyres_x"])
        cfg.setdefault("pitplan_y", cfg["tyres_y"] + self.tyres.height + 16)
        self.pitplan = PitPlanWindow(self)
        self.pitplan.fit_on_screen()
        cfg.setdefault("gaps_x", cfg["delta_x"])
        cfg.setdefault("gaps_y", cfg["delta_y"] + self.delta.height + 16)
        self.gaps = GapsWindow(self)
        self.gaps.fit_on_screen()
        cfg.setdefault("engine_x", cfg["tyres_x"])
        cfg.setdefault("engine_y", cfg["pitplan_y"] + self.pitplan.height + 16)
        self.engine = EngineWindow(self)
        self.engine.fit_on_screen()
        cfg.setdefault("sesstime_x", cfg["x"])
        cfg.setdefault("sesstime_y", max(0, cfg["y"] - 90))
        self.sesstime = SessionTimerWindow(self)
        self.sesstime.fit_on_screen()
        cfg.setdefault("clock_x", cfg["sesstime_x"] + self.sesstime.width + 16)
        cfg.setdefault("clock_y", cfg["sesstime_y"])
        self.clock = ClockWindow(self)
        self.clock.fit_on_screen()
        cfg.setdefault("speedo_x", cfg["x"])
        cfg.setdefault("speedo_y", max(0, cfg["y"] - 330))
        self.speedo = SpeedoWindow(self)
        self.speedo.fit_on_screen()
        self.subs = [self.rel, self.ped, self.map, self.radar, self.weather, self.lights, self.limiter, self.fuel,
                     self.delta, self.alerts, self.tyres, self.pitplan, self.gaps, self.engine, self.sesstime, self.clock, self.speedo]
        r.update_idletasks()
        self.apply_lock()
        self._sig = None
        self.poll_keys()
        self.keep_on_top()
        self.tick()

    # ---------- размеры ----------
    def rebuild(self):
        try:
            st_sc = max(0.5, min(3.0, float(self.cfg.get("scale_standings", 1.0))))
        except (TypeError, ValueError):
            st_sc = 1.0
        sz, fam = max(7, int(round(self.cfg["font"] * st_sc))), self.family
        self.font = tkfont.Font(family=fam, size=sz)
        self.bold = tkfont.Font(family=fam, size=sz, weight="bold")
        self.small = tkfont.Font(family=fam, size=max(7, sz - 3))
        self.big = tkfont.Font(family=fam, size=sz + 5, weight="bold")
        self.cw = self.font.measure("0")
        self.rh = self.font.metrics("linespace") + 14
        self.h_title, self.h_sub = int(self.rh * 1.7), int(self.rh * 1.0)
        self.ch, self.h_group, self.h_foot = int(self.rh * 0.8), int(self.rh * 0.95), int(self.rh * 0.95)
        self.R = int(self.rh * 0.42 * STYLE_R) if self.rounded else 0
        hide = set(self.cfg.get("hide_cols", []))
        if self.game in ("rf2", "iracing"):
            hide.add("ve")                       # в rFactor 2 виртуальной энергии нет
        if self.game == "iracing":
            hide.add("fuel")                     # в таблице iRacing топливо и энергия не нужны
        if self.game != "iracing":
            hide.add("irat")                     # iRating и лицензия есть только в iRacing
        self.cols = [c for c in COLS if c[0] not in hide]
        self.x_of, self.w_of, x = {}, {}, 14
        for key, _, w, _ in self.cols:
            self.x_of[key], self.w_of[key] = x, int(w * self.cw)
            x += int(w * self.cw) + int(self.cw * 0.9)
        self.width = max(x + 6, 520)
        self._smode = self.stand_mode()
        if self._smode == "ticker":
            self.width = max(400, int(self.cfg.get("ticker_w", 1000)))
        self.canvas.config(width=self.width)
        self._logos.clear()
        self._h = 0
        self.stretch_stand(False)
        for w in self.subs:
            w.build()

    def stretch_stand(self, items):
        stretch_apply(self.canvas, self.cfg, "standings", self.width, getattr(self, "_h", 0), items)

    def stand_mode(self):
        v = self.cfg.get("stand_mode", "table")
        return v if v in ("table", "compact", "ticker") else "table"

    def change(self, what, delta):
        if what == "rows":
            self.cfg["rows"] = max(4, min(60, self.cfg["rows"] + int(delta)))
        elif what == "font":
            self.cfg["font"] = max(8, min(28, self.cfg["font"] + int(delta)))
            self.rebuild()
        else:
            self.cfg["alpha"] = round(max(0.3, min(1.0, self.cfg["alpha"] + delta)), 2)
            self.set_alpha(self.cfg["alpha"])
        self.save()

    def op(self, key, value):
        """Прозрачность окна: 0 — скрыто, иначе своя настройка op_<окно> или общая."""
        if not value:
            return 0.0
        try:
            return max(0.1, min(1.0, float(self.cfg.get("op_" + key, value))))
        except (TypeError, ValueError):
            return value

    def hid(self, on):
        """Скрыто ли окно <on> сейчас: по горячей клавише или из-за гаража (настройка hg_<окно>, у стендинга hide_garage)."""
        if self.user_hidden or getattr(self, "nogame", False):
            return True
        if not getattr(self, "garage", False):
            return False
        return bool(self.cfg.get("hide_garage" if on == "standings" else "hg_" + on, True))

    def set_alpha(self, value=None):
        base = value or self.cfg["alpha"]
        self.root.attributes("-alpha", 0.0 if (self.hid("standings") or not self.cfg.get("standings", True))
                             else self.op("standings", base))
        for w in self.subs:
            if not getattr(w, "OWN_ALPHA", False):
                w.top.attributes("-alpha", 0.0 if self.hid(w.ON) else self.op(w.ON, base))

    def _toggle_sub(self, sub, var):
        self.cfg[sub.ON] = bool(var.get())
        if self.cfg[sub.ON]:
            sub.top.deiconify()
            self.apply_lock()
        else:
            sub.top.withdraw()
        self.save()

    def toggle_rel(self):
        self._toggle_sub(self.rel, self.rel_var)

    def toggle_ped(self):
        self._toggle_sub(self.ped, self.ped_var)

    def set_smode(self):
        self.cfg["stand_mode"] = self.smode_var.get()
        self._sig = None
        self.save()

    def set_mmode(self):
        self.cfg["map_mode"] = self.mmode_var.get()
        self.save()

    def set_dview(self):
        self.cfg["delta_view"] = self.dview_var.get()
        self.save()

    def set_tview(self):
        self.cfg["tyres_view"] = self.tview_var.get()
        self.save()

    def set_mrad(self):
        self.cfg["map_radius"] = int(self.mrad_var.get())
        self.save()

    def set_headup(self):
        self.cfg["map_headup"] = bool(self.headup_var.get())
        self.save()

    def set_dref(self):
        self.cfg["delta_ref"] = "last" if self.dref_var.get() else "best"
        self.save()

    def layout_mode(self):
        m = self.cfg.get("layout_mode")
        if m not in ("off", "session", "class"):
            m = "session" if self.cfg.get("session_layouts", False) else "off"
        return m

    def set_layouts(self):
        self.cfg["layout_mode"] = self.lmode_var.get()
        self._layout_cat = None
        self.save()

    def layout_cat(self, frame):
        """Ключ раскладки: тип сессии или класс вашей машины (None — пока не определить)."""
        mode = self.layout_mode()
        if mode == "session":
            return LAYOUT_CAT.get(frame.get("kind"), "race")
        if mode == "class":
            me = next((c for c in frame.get("cars", []) if c.get("player")), None)
            cls = str(me.get("cls", "")).strip().upper() if me else ""
            return "cls:" + cls if cls and cls != "UNKNOWN" else None
        return None

    def capture_layout(self):
        """Запомнить текущие окна (положение, размеры, виды) под текущей категорией."""
        cat = getattr(self, "_layout_cat", None)
        if self.layout_mode() == "off" or not cat:
            return
        self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        for w in self.subs:
            self.cfg[w.POS[0]], self.cfg[w.POS[1]] = w.top.winfo_x(), w.top.winfo_y()
        self.cfg.setdefault("layouts", {})[cat] = {k: self.cfg.get(k) for k in LAYOUT_KEYS if k in self.cfg}

    def check_layout(self, frame):
        """При смене сессии или класса машины подставляем сохранённую для неё раскладку."""
        if not frame or self.layout_mode() == "off":
            return
        cat = self.layout_cat(frame)
        if cat is None or cat == getattr(self, "_layout_cat", None):
            return
        if getattr(self, "_layout_cat", None):
            self.capture_layout()                              # сохраняем то, что было у прежней категории
        self._layout_cat = cat
        lay = (self.cfg.get("layouts") or {}).get(cat)
        if isinstance(lay, dict):
            for k, v in lay.items():
                if k in LAYOUT_KEYS:
                    self.cfg[k] = v
            self.sync_ui()
            self.root.geometry(f"+{self.cfg['x']}+{self.cfg['y']}")
            for w in self.subs:
                try:
                    w.top.geometry(f"+{self.cfg[w.POS[0]]}+{self.cfg[w.POS[1]]}")
                    w.build()
                    w.fit_on_screen()
                except Exception:
                    pass
        else:
            self.capture_layout()                              # для нового класса пока берём текущую раскладку

    def sync_ui(self):
        """После смены раскладки: галочки меню, видимость окон, режимы."""
        cf = self.cfg
        pairs = {"standings": self.stand_var, "relative": self.rel_var, "pedals": self.ped_var, "shiftlight": self.shift_var,
                 "trackmap": self.map_var, "radar": self.radar_var, "weather": self.weather_var, "lights": self.lights_var,
                 "limiter": self.lim_var, "fuel": self.fuel_var, **self.new_vars}
        for k, var in pairs.items():
            var.set(bool(cf.get(k, True)))
        self.smode_var.set(self.stand_mode())
        self.mmode_var.set(cf.get("map_mode", "full"))
        self.dview_var.set(cf.get("delta_view", "full"))
        self.tview_var.set(cf.get("tyres_view", "table"))
        self.lmode_var.set(self.layout_mode())
        for w in self.subs:
            if cf.get(w.ON, True):
                w.top.deiconify()
            else:
                w.top.withdraw()
        self.ped.build()
        self.apply_lock()
        self.set_alpha(0.0 if self.hidden else cf["alpha"])
        self._sig = None

    def toggle_map(self):
        self._toggle_sub(self.map, self.map_var)

    def rotate_map(self):
        self.cfg["map_rot"] = (int(self.cfg.get("map_rot", 0)) + 1) % 4
        self.save()

    def scale_standings(self, delta):
        v = float(self.cfg.get("scale_standings", 1.0)) + delta
        self.cfg["scale_standings"] = round(max(0.5, min(3.0, v)), 2)
        self.rebuild()
        self.save()

    def scale_fuel(self, delta):
        v = float(self.cfg.get("fuel_scale", 0.8)) + delta
        self.cfg["fuel_scale"] = round(max(0.5, min(2.0, v)), 2)
        self.fuel.build()
        self.save()

    def decorate(self):
        """Зелёное свечение и крестик у каждого окна в режиме перемещения."""
        move_decor(self.canvas, self.locked, self.R, lambda: self.close_window("standings"))
        for w in self.subs:
            if self.cfg.get(w.ON, True):
                move_decor(w.canvas, self.locked, w.R, lambda on=w.ON: self.close_window(on))

    def close_window(self, on):
        """Крестик на окне: выключает этот оверлей (вернуть можно в меню или в лаунчере)."""
        if on == "standings":
            self.stand_var.set(False)
            self.toggle_standings()
            return
        var = {"relative": self.rel_var, "pedals": self.ped_var, "trackmap": self.map_var, "weather": self.weather_var,
               "fuel": self.fuel_var, "limiter": self.lim_var, "lights": self.lights_var, "radar": self.radar_var,
               **self.new_vars}.get(on)
        sub = next((w for w in self.subs if w.ON == on), None)
        if var is None or sub is None:
            return
        var.set(False)
        self._toggle_sub(sub, var)

    def toggle_standings(self):
        self.cfg["standings"] = bool(self.stand_var.get())
        self.set_alpha(0.0 if self.hidden else self.cfg["alpha"])
        self.save()

    def set_reserve(self):
        self.cfg["fuel_reserve"] = float(self.res_var.get())
        self.fuel._sig = None
        self.save()

    def toggle_radar(self):
        self._toggle_sub(self.radar, self.radar_var)

    def scale_radar(self, delta):
        v = float(self.cfg.get("radar_scale", 1.0)) + delta
        self.cfg["radar_scale"] = round(max(0.3, min(3.0, v)), 2)
        self.radar.build()
        self.save()

    def range_radar(self, step):
        rs = RadarWindow.RANGES
        cur = float(self.cfg.get("radar_range", 40))
        i = min(range(len(rs)), key=lambda j: abs(rs[j] - cur))
        self.cfg["radar_range"] = rs[max(0, min(len(rs) - 1, i + step))]
        self.radar._sig = None
        self.save()

    def scale_map(self, delta):
        v = float(self.cfg.get("map_scale", 1.0)) + delta
        self.cfg["map_scale"] = round(max(0.5, min(3.0, v)), 2)
        self.map.build()
        self.save()

    def toggle_shift(self):
        self.cfg["shiftlight"] = bool(self.shift_var.get())
        self.ped.build()
        self.save()

    def mirror_map(self):
        self.cfg["map_mirror"] = bool(self.mirror_var.get())
        self.save()

    def _below_or_above(self, ref, height):
        """Куда поставить окно высотой height рядом с ref: под ним, а если не влезает — над ним."""
        try:
            sh = int(self.root.winfo_screenheight())
        except (TypeError, ValueError):
            sh = 1080
        y = int(self.cfg[ref.POS[1]]) + ref.height + 20
        if y + height > sh:
            y = max(0, int(self.cfg[ref.POS[1]]) - height - 20)
        return y

    def reset_layout(self):
        """Вернуть все окна на экран: таблица слева сверху, рядом релатив, под ним ввод."""
        try:
            sw, sh = int(self.root.winfo_screenwidth()), int(self.root.winfo_screenheight())
        except (TypeError, ValueError):
            sw, sh = 1920, 1080
        x, y = 20, 20
        self.root.geometry(f"+{x}+{y}")
        rx = max(0, min(x + self.width + 24, sw - self.rel.width - 10))
        self.cfg.update(x=x, y=y, rel_x=rx, rel_y=y, ped_x=rx, map_x=rx)
        self.cfg["ped_y"] = self._below_or_above(self.rel, self.ped.height)
        self.cfg["map_y"] = self._below_or_above(self.ped, self.map.height)
        self.cfg.update(radar_x=min(rx + self.rel.width + 20, max(0, sw - self.radar.width - 10)), radar_y=y)
        self.cfg.update(wx_x=self.cfg["radar_x"], wx_y=y + self.radar.height + 20,
                        lights_x=max(0, sw // 2 - self.lights.width // 2), lights_y=40)
        self.cfg.update(lim_x=self.cfg["lights_x"], lim_y=40 + self.lights.height + 14)
        self.cfg.update(fuel_x=rx, fuel_y=self._below_or_above(self.map, self.fuel.height))
        self.cfg.update(delta_x=self.cfg["lights_x"], delta_y=40 + self.lights.height + self.limiter.height + 28,
                        alerts_x=max(0, sw // 2 - self.alerts.width // 2), alerts_y=130,
                        tyres_x=max(0, min(self.cfg["wx_x"] + 270, sw - self.tyres.width - 10)), tyres_y=self.cfg["wx_y"])
        self.cfg.update(pitplan_x=self.cfg["tyres_x"], pitplan_y=self.cfg["tyres_y"] + self.tyres.height + 16,
                        gaps_x=self.cfg["delta_x"], gaps_y=self.cfg["delta_y"] + self.delta.height + 16,
                        engine_x=self.cfg["tyres_x"], engine_y=self.cfg["pitplan_y"] + self.pitplan.height + 16,
                        sesstime_x=self.cfg["x"], sesstime_y=max(0, self.cfg["y"] - 90),
                        clock_x=self.cfg["x"] + self.sesstime.width + 16, clock_y=max(0, self.cfg["y"] - 90),
                        speedo_x=self.cfg["x"], speedo_y=max(0, self.cfg["y"] - 330))
        for w in self.subs:
            w.top.geometry(f"+{self.cfg[w.POS[0]]}+{self.cfg[w.POS[1]]}")
            w.fit_on_screen()
            if self.cfg.get(w.ON, True):
                w.top.deiconify()
        for win in [self.root] + [w.top for w in self.subs]:
            win.update_idletasks()               # пусть система применит новые координаты до сохранения
        self.save()

    def toggle_cols(self):
        self.cfg["hide_cols"] = [k for k, v in self.col_vars.items() if not v.get()]
        self.rebuild()
        self._sig = None
        self.save()

    def toggle_class(self):
        self.cfg["class_only"] = bool(self.class_var.get())
        self._sig = None
        self.save()

    def toggle_hide(self):
        self.cfg["hide_garage"] = bool(self.hide_var.get())
        self.save()

    # ---------- окно ----------
    def apply_lock(self):
        ok = set_click_through(self.root, self.locked)
        for w in self.subs:
            w.top.update_idletasks()
            set_click_through(w.top, self.locked)
        if self.locked and not ok and os.name == "nt":
            print("Не удалось включить сквозные клики — окно останется кликабельным.")

    def toggle_lock(self):
        self.locked = not self.locked
        self.cfg["locked"] = self.locked
        self.apply_lock()
        self._sig = None
        self.save()
        self.write_state()

    # ---------- окно режима перемещения: список оверлеев с кнопками вкл/выкл ----------
    def overlay_var(self, on):
        if on == "standings":
            return self.stand_var
        return {"relative": self.rel_var, "pedals": self.ped_var, "trackmap": self.map_var, "weather": self.weather_var,
                "fuel": self.fuel_var, "limiter": self.lim_var, "lights": self.lights_var, "radar": self.radar_var,
                **self.new_vars}.get(on)

    def set_overlay(self, on, state):
        var = self.overlay_var(on)
        if var is None:
            return
        var.set(bool(state))
        if on == "standings":
            self.toggle_standings()
        else:
            sub = next((w for w in self.subs if w.ON == on), None)
            if sub is not None:
                self._toggle_sub(sub, var)
        self.refresh_move_panel()

    def update_move_panel(self):
        """Окно со списком оверлеев есть только в режиме перемещения."""
        mp = getattr(self, "move_panel", None)
        if self.locked:
            if mp is not None:
                try:
                    mp.destroy()
                except tk.TclError:
                    pass
                self.move_panel = None
            return
        if mp is None:
            self.build_move_panel()
        elif time.monotonic() - getattr(self, "_mp_t", 0.0) > 0.7:
            self.refresh_move_panel()

    def build_move_panel(self):
        r = self.root
        mp = self.move_panel = tk.Toplevel(r)
        mp.overrideredirect(True)
        mp.attributes("-topmost", True)
        mp.configure(bg="#2ee26d")
        body = tk.Frame(mp, bg="#12171f")
        body.pack(padx=2, pady=2)
        f_s = tkfont.Font(family=self.family, size=9)
        f_b = tkfont.Font(family=self.family, size=9, weight="bold")
        l1 = tk.Label(body, text="РЕЖИМ ПЕРЕМЕЩЕНИЯ", bg="#12171f", fg="#2ee26d", font=f_b, cursor="fleur")
        l1.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        l2 = tk.Label(body, text="тяните окно · за край или угол — размер", bg="#12171f", fg="#8b95a5", font=f_s, cursor="fleur")
        l2.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 4))

        def d_start(e):
            mp._dx, mp._dy = e.x_root - mp.winfo_x(), e.y_root - mp.winfo_y()

        def d_move(e):
            mp.geometry(f"+{e.x_root - mp._dx}+{e.y_root - mp._dy}")
        for wdg in (l1, l2, body, mp):                          # окно можно перетащить за заголовок и за фон
            wdg.bind("<ButtonPress-1>", d_start)
            wdg.bind("<B1-Motion>", d_move)
        self.mp_btns = {}
        items = [("standings", OVERLAY_NAMES["standings"])] + [(w.ON, OVERLAY_NAMES.get(w.ON, w.ON)) for w in self.subs]
        for i, (on, name) in enumerate(items):
            b = tk.Label(body, text=name, font=f_s, width=15, pady=2, cursor="hand2")
            b.grid(row=2 + i // 3, column=i % 3, padx=2, pady=1)
            b.bind("<Button-1>", lambda e, k=on: self.set_overlay(k, not self.cfg.get(k, True)))
            self.mp_btns[on] = b
        done = tk.Label(body, text="Готово — закрепить окна", bg="#2ee26d", fg="#04210b", font=f_b, pady=3, cursor="hand2")
        done.grid(row=2 + (len(items) + 2) // 3, column=0, columnspan=3, sticky="ew", padx=4, pady=(5, 6))
        done.bind("<Button-1>", lambda e: self.toggle_lock())
        self.refresh_move_panel()
        mp.update_idletasks()
        w, h = mp.winfo_reqwidth(), mp.winfo_reqheight()
        mp.geometry(f"+{max(0, (r.winfo_screenwidth() - w) // 2)}+{max(0, (r.winfo_screenheight() - h) // 2)}")

    def refresh_move_panel(self):
        mp = getattr(self, "move_panel", None)
        if mp is None:
            return
        self._mp_t = time.monotonic()
        for on, b in self.mp_btns.items():
            if self.cfg.get(on, True):
                b.config(bg="#1f9d4f", fg="#ffffff")
            else:
                b.config(bg="#262d38", fg="#6b7585")
        try:
            mp.lift()
        except tk.TclError:
            pass

    def write_state(self):
        """Сообщаем лаунчеру, включён ли режим перемещения (файл рядом с профилем)."""
        try:
            with open(os.path.splitext(CFG_PATH)[0] + ".state", "w", encoding="utf-8") as f:
                json.dump({"locked": bool(self.locked), "pid": os.getpid(), "ts": time.time()}, f)
        except OSError:
            pass

    def poll_cmd(self):
        """Команды от лаунчера: файл рядом с профилем с текстом lock / unlock / toggle."""
        now = time.monotonic()
        if now - getattr(self, "_cmd_t", 0.0) < 0.5:
            return
        self._cmd_t = now
        if not getattr(self, "_state_written", False):
            self._state_written = True
            self.write_state()
        path = os.path.splitext(CFG_PATH)[0] + ".cmd"
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                cmd = f.read().strip().lower()
            os.remove(path)
        except OSError:
            return
        if cmd == "toggle" or (cmd == "unlock" and self.locked) or (cmd == "lock" and not self.locked):
            self.toggle_lock()

    def hk_text(self, name):
        user = self.cfg.get("hotkeys") if isinstance(self.cfg.get("hotkeys"), dict) else {}
        return user.get(name) if parse_hotkey(user.get(name, "")) else HOTKEY_DEFAULTS[name]

    def hotkey(self, name):
        """Ctrl+Shift+H/T/M/D/Y: спрятать оверлей и переключить виды панелей."""
        cf = self.cfg
        if name == "hide":
            self.user_hidden = not self.user_hidden
            self.hidden = self.user_hidden
            self.set_alpha(0.0 if self.hidden else cf["alpha"])
            return
        cyc = {"tim": ("stand_mode", ("table", "compact", "ticker"), self.smode_var, self.set_smode),
               "map": ("map_mode", ("full", "linear", "local"), self.mmode_var, self.set_mmode),
               "delta": ("delta_view", ("full", "bar", "sectors"), self.dview_var, self.set_dview),
               "tyres": ("tyres_view", ("table", "compact", "wear"), self.tview_var, self.set_tview)}[name]
        key, vals, var, fn = cyc
        cur = cf.get(key, vals[0])
        var.set(vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else vals[0])
        fn()

    def poll_keys(self):
        if getattr(self, "_hk", None) is None:
            user = self.cfg.get("hotkeys") if isinstance(self.cfg.get("hotkeys"), dict) else {}
            self._hk = {n: (parse_hotkey(user.get(n, "")) or parse_hotkey(d)) for n, d in HOTKEY_DEFAULTS.items()}
        for name, vks in self._hk.items():
            down = keys_down(*vks)
            if down and not self._key_state.get(name):
                if name == "lock":
                    self.toggle_lock()
                elif name == "reset":
                    self.reset_layout()
                elif name == "quit":
                    self.close()
                    return
                else:
                    self.hotkey(name)
            self._key_state[name] = down
        self.root.after(80, self.poll_keys)

    def keep_on_top(self):
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
            for w in self.subs:
                if self.cfg.get(w.ON, True):
                    w.top.attributes("-topmost", True)
                    w.top.lift()
            mp = getattr(self, "move_panel", None)
            if mp is not None:                                  # окно режима перемещения — всегда поверх оверлеев
                mp.attributes("-topmost", True)
                mp.lift()
        except Exception:
            pass
        self.root.after(2000, self.keep_on_top)

    def update_visibility(self, frame):
        """Прозрачный, когда машина стоит в боксе гаража; в режиме перемещения виден всегда."""
        hide = False
        now = time.monotonic()
        if frame is not None:
            self._frame_seen = now
        elif not hasattr(self, "_frame_seen"):
            self._frame_seen = now
        # нет данных игры (игра не запущена) — в обычном режиме оверлеи прячутся; в режиме перемещения видны всегда
        self.nogame = bool(self.locked and frame is None and now - self._frame_seen > 2.0)
        if frame and self.locked:
            me = next((c for c in frame["cars"] if c["player"]), None)
            hide = bool(me and me["garage"] and me["speed"] < 1.0
                        and (frame["kind"] != "RACE" or me["laps"] == 0))
            if frame.get("in_realtime") is False:          # rFactor 2: игрок в мониторе (экран гаража)
                hide = True
        self.garage = hide
        self.hidden = hide or self.user_hidden or self.nogame
        hs = (self.hid("standings"),) + tuple(self.hid(w.ON) for w in self.subs)
        if hs != getattr(self, "_hs", None):
            self._hs = hs
            self.set_alpha(self.cfg["alpha"])

    def drag_start(self, e):
        if self.locked:
            return
        z = grip_zone(self.canvas, e)
        self._resizing = z is not None
        if self._resizing:
            self.resize_begin("standings", self.canvas, e, self.root, z)
            return
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def drag_move(self, e):
        if self.locked:
            return
        if getattr(self, "_resizing", False):
            self.resize_drag(e)
            return
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def drag_end(self, e):
        if getattr(self, "_resizing", False):
            self._resizing = False
            self.resize_end()
        else:
            self.save()

    # ---------- растягивание окон за уголок ----------
    def resize_begin(self, on, canvas, e, top=None, zone=(1, 1)):
        key, lo, hi, dflt = SCALE_SPEC.get(on, ("scale_" + on, 0.5, 3.0, 1.0))
        try:
            s0 = float(self.cfg.get(key, dflt))
            w0, h0 = int(float(canvas.cget("width"))), int(float(canvas.cget("height")))
        except (TypeError, ValueError, tk.TclError):
            return
        try:
            wx, wy = top.winfo_x(), top.winfo_y()
        except (AttributeError, tk.TclError):
            top, wx, wy = None, 0, 0
        fx0, fy0 = stretch_get(self.cfg, on)
        free = on not in ASPECT_LOCKED and not (on == "delta" and self.cfg.get("delta_theme") == "ring")
        self._rs = {"on": on, "key": key, "lo": lo, "hi": hi, "s0": s0, "x0": e.x_root, "y0": e.y_root,
                    "free": free, "fx0": fx0, "fy0": fy0,
                    "w0": max(1, w0), "h0": max(1, h0), "t": 0.0, "last": s0, "canvas": canvas, "top": top,
                    "zone": zone, "right": wx + w0, "bottom": wy + h0, "wx": wx, "wy": wy}

    def resize_drag(self, e):
        rs = getattr(self, "_rs", None)
        if not rs:
            return
        sx, sy = rs["zone"]
        if rs["free"]:                                          # ширина и высота независимо: тянем только за ту сторону, за которую взялись
            fx = rs["fx0"] * max(0.1, (rs["w0"] + sx * (e.x_root - rs["x0"])) / rs["w0"]) if sx else rs["fx0"]
            fy = rs["fy0"] * max(0.1, (rs["h0"] + sy * (e.y_root - rs["y0"])) / rs["h0"]) if sy else rs["fy0"]
            fx, fy = round(max(0.4, min(6.0, fx)), 3), round(max(0.4, min(6.0, fy)), 3)
            now = time.monotonic()
            rs["want"] = (fx, fy)
            if now - rs["t"] < 0.05:
                return
            rs["t"] = now
            self.apply_stretch(rs, fx, fy)
            return
        rats = []
        if sx:
            rats.append((rs["w0"] + sx * (e.x_root - rs["x0"])) / rs["w0"])
        if sy:
            rats.append((rs["h0"] + sy * (e.y_root - rs["y0"])) / rs["h0"])
        ratio = max(rats) if rats else 1.0
        new = round(max(rs["lo"], min(rs["hi"], rs["s0"] * max(0.1, ratio))), 2)
        now = time.monotonic()
        if new == rs["last"] or now - rs["t"] < 0.06:
            rs["want"] = new
            return
        rs["t"], rs["last"] = now, new
        self.cfg[rs["key"]] = new
        self.apply_scale(rs["on"])
        self.resize_anchor(rs)

    def apply_stretch(self, rs, fx, fy):
        self.cfg["sx_" + rs["on"]], self.cfg["sy_" + rs["on"]] = fx, fy
        if rs["on"] == "standings":
            self.stretch_stand(False)
            self._sig = None
        else:
            sub = next((w for w in self.subs if w.ON == rs["on"]), None)
            if sub is not None:
                sub.stretch_size()
                sub._sig = None
        self.resize_anchor(rs)

    def resize_end(self):
        rs = getattr(self, "_rs", None)
        self._rs = None
        if rs and rs["free"]:
            fx, fy = rs.get("want", (rs["fx0"], rs["fy0"]))
            self.apply_stretch(rs, fx, fy)
        elif rs:
            new = rs.get("want", rs["last"])
            if new != self.cfg.get(rs["key"]) or new != rs["last"]:
                self.cfg[rs["key"]] = new
                self.apply_scale(rs["on"])
            self.resize_anchor(rs)
        self.save()

    def resize_anchor(self, rs):
        """Тянем за левый/верхний край — противоположный край окна остаётся на месте."""
        sx, sy = rs["zone"]
        top = rs.get("top")
        if top is None or (sx >= 0 and sy >= 0):
            return
        try:
            top.update_idletasks()
            w, h = int(float(rs["canvas"].cget("width"))), int(float(rs["canvas"].cget("height")))
            x = rs["right"] - w if sx < 0 else rs["wx"]
            y = rs["bottom"] - h if sy < 0 else rs["wy"]
            top.geometry(f"+{x}+{y}")
        except (tk.TclError, ValueError):
            pass

    def apply_scale(self, on):
        """Перестроить окно под новый масштаб."""
        if on == "standings":
            self.rebuild()
            return
        sub = next((w for w in self.subs if w.ON == on), None)
        if sub is not None:
            sub.build()
            sub._sig = None

    def save(self):
        self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        for w in self.subs:
            self.cfg[w.POS[0]], self.cfg[w.POS[1]] = w.top.winfo_x(), w.top.winfo_y()
        self.capture_layout()
        self.cfg.setdefault("screen_layouts", {})[self.screen_sig] = {
            "b": list(self.screen_b), "v": {k: self.cfg[k] for k in SCREEN_KEYS if k in self.cfg}}
        self.cfg["screen_last"] = self.screen_sig
        try:
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f)
        except OSError:
            pass

    def close(self):
        self.save()
        try:
            recorder.flush()
            os.remove(os.path.splitext(CFG_PATH)[0] + ".state")
        except OSError:
            pass
        self.root.destroy()

    # ---------- рисование ----------
    def visible_cars(self, cars):
        """Окно вокруг игрока; лидер всегда закреплён сверху."""
        n = self.cfg["rows"]
        if self.stand_mode() == "compact":
            me_i = next((i for i, c in enumerate(cars) if c["player"]), 0)
            idx = sorted(set(range(min(3, len(cars)))) | set(range(max(0, me_i - 2), min(len(cars), me_i + 3))))
            return [cars[i] for i in idx]
        if len(cars) <= n:
            return cars
        me = next((i for i, c in enumerate(cars) if c["player"]), 0)
        start = max(0, min(me - n // 2, len(cars) - n))
        if start <= 1:
            return cars[:n]
        return [cars[0]] + cars[start + 1:start + n]

    def rrect(self, x1, y1, x2, y2, r, fill="", outline=""):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, fill=fill, outline=outline)

    def panel_part(self, y1, y2, fill, top=False, bottom=False):
        """Прямоугольник во всю ширину; верхние/нижние углы скруглены, если нужно."""
        W, R = self.width, self.R
        if R and (top or bottom):
            self.rrect(0, y1, W - 1, y2, R, fill=fill)
            if top and not bottom:
                self.canvas.create_rectangle(0, y1 + R, W, y2, fill=fill, outline="")
            elif bottom and not top:
                self.canvas.create_rectangle(0, y1, W, y2 - R, fill=fill, outline="")
        else:
            self.canvas.create_rectangle(0, y1, W, y2, fill=fill, outline="")

    def pill(self, x_right, cy, text, fg, outline=None, fill="", font=None):
        font = font or self.small
        w, h = font.measure(text) + 18, int(self.rh * 0.62)
        x1 = x_right - w
        self.rrect(x1, cy - h // 2, x_right, cy + h // 2, 6, fill=fill, outline=outline or fg)
        self.canvas.create_text(x1 + w // 2, cy, text=text, fill=fg, font=font)
        return x1

    def text(self, key, y, s, fill=FG, font=None):
        anchor = next(c[3] for c in self.cols if c[0] == key)
        x = self.x_of[key] + (self.w_of[key] if anchor == "e" else 0)
        self.canvas.create_text(x, y, text=s, fill=fill, anchor=anchor, font=font or self.font)

    def ir_logo(self, name, size):
        """Логотип марки iRacing: берём из папки logos/ir, а если его нет — один раз скачиваем с сайта iRacing."""
        folder = os.path.join(self.logo_dir or os.path.join(HERE, "logos"), "ir")
        path = os.path.join(folder, name + ".png")
        key = ("ir:" + name, size)
        if key in self._logos:
            return self._logos[key]
        if not os.path.exists(path):
            if name not in self._ir_tried and svgmap is not None:
                self._ir_tried.add(name)
                threading.Thread(target=svgmap.download_logo, args=(name, path), daemon=True).start()
            return None                                              # скачается — нарисуем при следующем обновлении
        photo = None
        try:
            from PIL import Image, ImageTk
            im = Image.open(path).convert("RGBA")
            box = im.getbbox()
            if box:
                im = im.crop(box)
            k = min(size * 1.25 / im.width, size / im.height)
            photo = ImageTk.PhotoImage(im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS))
        except Exception:
            try:
                ph = tk.PhotoImage(file=path)
                photo = ph.subsample(max(1, round(max(ph.width(), ph.height()) / size)))
            except Exception:
                photo = None
        self._logos[key] = photo
        return photo

    def logo(self, slug, colour, size):
        if slug.startswith("ir:"):
            return self.ir_logo(slug[3:], size)
        key = (slug, colour, size)
        if key in self._logos:
            return self._logos[key]
        photo = None
        path = os.path.join(self.logo_dir, slug + ".png") if (self.logo_dir and slug) else None
        if path and os.path.exists(path):
            try:
                from PIL import Image, ImageTk
                im = Image.open(path).convert("RGBA")
                tint = Image.new("RGBA", im.size, colour)
                tint.putalpha(im.getchannel("A"))
                photo = ImageTk.PhotoImage(tint.resize((size, size), Image.LANCZOS))
            except Exception:
                try:
                    photo = tk.PhotoImage(file=path)
                    photo = photo.subsample(max(1, round(photo.width() / size)))
                except Exception:
                    photo = None
        self._logos[key] = photo
        return photo

    def out_laps(self, frame):
        """Машины на первом круге после выезда с пит-лейна (до пересечения линии)."""
        if frame["session_start"] != self._out_session:
            self._out_session, self._out = frame["session_start"], {}
        res = set()
        for c in frame["cars"]:
            in_pit = bool(c["in_pits"] or c["garage"] or c["pit_state"] in (2, 3, 4))
            t = self._out.setdefault(c["key"], {"pit": in_pit, "lap": None})
            if t["pit"] and not in_pit:
                t["lap"] = c["laps"]                    # только что выехал
            if in_pit:
                t["lap"] = None
            elif t["lap"] is not None:
                if c["laps"] > t["lap"]:
                    t["lap"] = None                     # пересёк линию — out lap закончился
                else:
                    res.add(c["key"])
            t["pit"] = in_pit
        return res

    def fuel_stuck(self, frame):
        """Машины, у которых топливо не меняется, хотя они проехали круг: игра не отдаёт им реальный расход."""
        if frame["session_start"] != self._fuel_session:
            self._fuel_session, self._fuel, self._fuel_live = frame["session_start"], {}, set()
        stuck = set()
        for c in frame["cars"]:
            t = self._fuel.get(c["key"])
            if t is not None and abs(c["fuel"] - t["fuel"]) >= 0.05:
                self._fuel_live.add(c["key"])           # значение действительно меняется — данные живые
            if t is None or c["pitstops"] != t["pit"] or abs(c["fuel"] - t["fuel"]) >= 0.05:
                self._fuel[c["key"]] = {"laps": c["laps"], "fuel": c["fuel"], "pit": c["pitstops"]}   # движется — заново
            elif c["laps"] - t["laps"] >= 1 and not c["player"]:
                stuck.add(c["key"])
        return stuck

    def track_positions(self, frame, scope_key, group):
        """Сколько позиций отыграно с момента, когда оверлей увидел сессию."""
        if frame["session_start"] != self._session_seen:
            self._session_seen, self._start_pos = frame["session_start"], {}
        out = {}
        for i, c in enumerate(group):
            start = self._start_pos.setdefault((scope_key, c["key"]), i + 1)
            out[c["key"]] = start - (i + 1)
        return out

    def draw(self):
        with lock:
            frame, ok, err, frame_ts = state["frame"], state["ok"], state["err"], state["frame_ts"]
        game = frame.get("game", "lmu") if frame else self.game
        if game != self.game:                     # сменилась игра -> другой набор колонок
            self.game = game
            self.rebuild()
        if self.stand_mode() != getattr(self, "_smode", None):
            self.rebuild()
        c, rh, W = self.canvas, self.rh, self.width
        c.delete("all")
        if frame is not None and not ok and time.time() - frame_ts > 6.0:
            frame = None                                  # давно нет данных — не показываем устаревшее
        self.update_visibility(frame)
        if self._smode == "ticker":
            self.draw_ticker(frame, ok)
            return

        cars = frame["cars"] if frame else []
        timed = bool(frame and frame["kind"] in TIMED_KINDS)
        ordered = order_cars(cars, timed)
        me = next((x for x in ordered if x["player"]), None)
        class_mode = bool(self.cfg.get("class_only", False) and me and me["cls"] != "UNKNOWN")
        if class_mode:
            group, group_name, scope = [x for x in ordered if x["cls"] == me["cls"]], me["cls"], me["cls"]
        else:
            group, group_name, scope = ordered, "ВСЕ МАШИНЫ", "ALL"
        vis = self.visible_cars(group)
        gaps = group_gaps(group, timed)
        cpos = {x["key"]: i + 1 for i, x in enumerate(group)}
        deltas = self.track_positions(frame, scope, group) if frame and not timed else {}
        stuck = self.fuel_stuck(frame) if frame else set()
        ir_pred = ir_forecast(cars) if (frame and self.game == "iracing" and frame.get("kind") == "RACE" and "irat" in self.x_of) else None
        out_laps = self.out_laps(frame) if frame else set()
        all_best = [x["best"] for x in cars if x["best"] > 0]
        best_all = min(all_best) if all_best else None

        H = self.h_title + self.h_sub + self.ch + self.h_group + rh * len(vis) + self.h_foot + 4
        if H != self._h:
            c.config(height=H)
            self._h = H

        # ---------- шапка ----------
        self.panel_part(0, self.h_title + self.h_sub, HDR_BG, top=True)
        cy = self.h_title // 2
        c.create_rectangle(14, cy - int(rh * 0.5), 18, cy + int(rh * 0.5), fill=MARK, outline="")
        logo_txt = {"rf2": "rF2", "iracing": "iR"}.get(self.game, "LMU")
        c.create_text(28, cy, text=logo_txt, fill=FG, anchor="w", font=self.big)
        c.create_text(28 + self.big.measure(logo_txt) + 10, cy + 3, text="STANDINGS", fill=DIM, anchor="w", font=self.small)

        timer, caption, session, flag, sub = "", "", "", "", ""
        if frame:
            session, timer, caption = session_clock(frame)
            phase = frame["game_phase"]
            flag = ("CHEQUERED" if phase == 8 else "YELLOW" if (phase == 6 or frame["yellow"] > 0) else
                    "START" if (phase in (3, 4) or frame["start_light"] > 0) else "GREEN" if phase == 5 else "")
            parts = [frame["track"].upper()] if frame["track"] else []
            parts.append(f"AIR {frame['air']:.0f}°")
            parts.append(f"TRACK {frame['track_temp']:.0f}°")
            sub = "   •   ".join(parts)
        cf = self.cfg
        show_t, show_s = cf.get("st_timer", True), cf.get("st_session", True)
        if timer:
            xl = W - 14
            if show_t:
                c.create_text(W - 14, cy, text=timer, fill=FG, anchor="e", font=self.big)
                xl = W - 14 - self.big.measure(timer) - 12
                c.create_text(xl, cy + 2, text=caption, fill=DIM, anchor="e", font=self.small)
                xl -= self.small.measure(caption) + 14
            if session and show_s:
                self.pill(xl, cy, session, ACCENT)
        elif not ok:
            c.create_text(W - 14, cy, text="ЖДУ ИГРУ (LMU / rFactor 2)", fill=DIM, anchor="e", font=self.bold)

        sy = self.h_title + self.h_sub // 2
        if not self.locked:
            c.create_text(14, sy, text="РЕЖИМ ПЕРЕМЕЩЕНИЯ: тяните мышью  •  " + self.hk_text("lock") + " — закрепить",
                          fill=ACCENT, anchor="w", font=self.small)
        else:
            c.create_text(14, sy, text=sub or (err if not ok else ""), fill=DIM, anchor="w", font=self.small)
        xr = W - 14
        if flag:
            fcol = {"GREEN": GREEN, "YELLOW": YELLOW, "START": ACCENT, "CHEQUERED": FG}.get(flag, DIM)
            xr = self.pill(W - 14, sy, flag, fcol, fill=BG) - 14
        if cf.get("st_clock", True):
            clock = time.strftime("%H:%M:%S")             # реальное время на вашем компьютере
            c.create_text(xr, sy, text=clock, fill=FG, anchor="e", font=self.bold)
            c.create_text(xr - self.bold.measure(clock) - 8, sy, text="ВРЕМЯ", fill=DIM, anchor="e", font=self.small)
        c.create_rectangle(0, self.h_title + self.h_sub - 3, W, self.h_title + self.h_sub, fill=ACCENT, outline="")

        # ---------- заголовки колонок и шапка класса ----------
        y0 = self.h_title + self.h_sub
        c.create_rectangle(0, y0, W, y0 + self.ch, fill=BG, outline="")
        for key, title, _, _ in self.cols:
            if title:
                self.text(key, y0 + self.ch // 2, title, DIM, self.small)
        y = y0 + self.ch
        gy = y + self.h_group // 2
        c.create_rectangle(0, y, W, y + self.h_group, fill=HDR_BG, outline="")
        c.create_rectangle(14, gy - 7, 18, gy + 7, fill=class_color(group_name) if class_mode else ACCENT, outline="")
        c.create_text(28, gy, text=group_name, fill=FG, anchor="w", font=self.bold)
        count_txt = cars_word(len(group))
        if len(cars) > len(group):                       # часть машин спрятана фильтром «только мой класс»
            count_txt += f"   •   ВСЕГО В СЕССИИ {len(cars)}"
        c.create_text(W - 14, gy, text=count_txt, fill=DIM, anchor="e", font=self.small)
        y += self.h_group

        # ---------- строки ----------
        for i, car in enumerate(vis):
            cy = y + rh // 2
            is_me = car["player"]
            c.create_rectangle(0, y, W, y + rh, fill=ME_BG if is_me else (ROW_A if i % 2 == 0 else ROW_B), outline="")
            if is_me:
                c.create_rectangle(0, y, W - 1, y + rh, outline=ME_EDGE)
            ccol = class_color(car["cls"])
            c.create_rectangle(0, y + 3, 5, y + rh - 3, fill=ccol, outline="")

            pos = cpos[car["key"]]
            leader = pos == 1
            ahead_txt, ahead_s, lead_txt = gaps[car["key"]]
            close = isinstance(ahead_s, (int, float)) and 0 < ahead_s < 1.0 and not timed
            status, skind = status_of(car, car["key"] in out_laps)
            for key, *_ in self.cols:
                if key == "pos":
                    self.text("pos", cy, str(pos), ACCENT if leader else FG, self.bold)
                elif key == "delta":
                    d = deltas.get(car["key"], 0)
                    if d:
                        self.text("delta", cy, ("▲" if d > 0 else "▼") + str(abs(d)), GREEN if d > 0 else RED, self.small)
                elif key == "logo":
                    mono, colour, slug = brand_of(car)
                    s = int(rh * 0.58)
                    lx = self.x_of["logo"] + self.w_of["logo"] // 2
                    img = self.logo(slug, colour, s)
                    if img is not None:
                        c.create_image(lx, cy, image=img, anchor="center")
                elif key == "num":
                    nx = self.x_of["num"]
                    self.rrect(nx - 4, y + 5, nx + self.w_of["num"] + 6, y + rh - 5, 5, fill=BADGE, outline=BORDER)
                    c.create_rectangle(nx - 4, y + 9, nx - 1, y + rh - 9, fill=ccol, outline="")
                    self.text("num", cy, car["number"] or "-", FG, self.bold)
                elif key == "tyre":
                    t = tyre_text(car)
                    if t:
                        col = COMPOUND_COLORS.get(t[0], DIM)
                        tx = self.x_of["tyre"]
                        tw = self.small.measure(t) + 14
                        self.rrect(tx, cy - int(rh * 0.27), tx + tw, cy + int(rh * 0.27), 5, outline=col)
                        c.create_text(tx + tw // 2, cy, text=t, fill=col, font=self.small)
                elif key == "name":
                    self.text("name", cy, car["driver"].upper()[:22], FG, self.bold if is_me else self.font)
                elif key == "irat":
                    draw_irat(c, car, self.x_of["irat"] + self.w_of["irat"], cy, self.small, rh,
                              ir_pred.get(car["key"]) if ir_pred is not None else None)
                elif key == "ahead":
                    self.text("ahead", cy, ahead_txt, YELLOW if close else FG)
                elif key == "lead":
                    self.text("lead", cy, lead_txt, ACCENT if leader else FG)
                elif key == "best":
                    top = best_all is not None and car["best"] > 0 and abs(car["best"] - best_all) < 1e-6
                    self.text("best", cy, fmt_lap(car["best"]), PURPLE if top else FG, self.bold if top else self.font)
                elif key == "last":
                    pb = car["best"] > 0 and car["last"] > 0 and abs(car["last"] - car["best"]) < 5e-4
                    self.text("last", cy, fmt_lap(car["last"]), GREEN if pb else FG)
                elif key == "fuel":
                    # rFactor 2 не отдаёт реальный расход чужих машин: верим, только если значение движется
                    live = car["player"] or self.game not in ("rf2", "iracing") or car["key"] in self._fuel_live
                    if live and fuel_known(car) and car["key"] not in stuck:
                        self.text("fuel", cy, f"{car['fuel']:.1f}", DIM)
                    elif car["fuel"] > 0:
                        self.text("fuel", cy, "—", DIM)
                    elif car["fuel_fraction"] > 0 and self.game not in ("rf2", "iracing"):
                        self.text("fuel", cy, f"{car['fuel_fraction'] / 255 * 100:.0f}%", DIM)
                elif key == "ve":
                    ve = car["ve"]
                    col, bw, bh = ve_color(ve), int(6.0 * self.cw), max(8, rh // 3)
                    x = self.x_of["ve"]
                    c.create_rectangle(x, cy - bh // 2, x + bw, cy + bh // 2, fill=TRACK, outline="")
                    c.create_rectangle(x, cy - bh // 2, x + max(2, int(bw * min(ve, 1.0))), cy + bh // 2, fill=col, outline="")
                    c.create_text(x + bw + 8, cy, text=f"{ve * 100:.0f}%", fill=col, anchor="w", font=self.bold)
                elif key == "status":
                    if status:
                        col = {"out": RED, "pit": ACCENT, "pen": RED}.get(skind, DIM)
                        self.text("status", cy, status, col, self.bold)
                    elif car["pitstops"] > 0:
                        self.text("status", cy, f"{car['pitstops']} ПИТ", DIM, self.small)
            y += rh
            if i == 0 and len(vis) > 1 and group.index(vis[1]) > 1:
                c.create_line(0, y, W, y, fill=ACCENT, dash=(3, 4))

        # ---------- подвал ----------
        fy = y + self.h_foot // 2
        self.panel_part(y, y + self.h_foot, HDR_BG, bottom=True)
        with lock:
            now_t = time.time()
            rate = sum(1 for t in state["changes"] if now_t - t <= 2.0) / 2.0
        if not ok:
            dot, live = RED, "НЕТ ДАННЫХ"
        elif rate <= 0.0:
            dot, live = YELLOW, "ДАННЫЕ НЕ МЕНЯЮТСЯ"
        else:
            dot, live = GREEN, f"LIVE  {rate:.0f} Гц"
        c.create_oval(14, fy - 4, 22, fy + 4, fill=dot, outline="")
        c.create_text(30, fy, text=live, fill=DIM, anchor="w", font=self.small)
        if len(group) > len(vis):
            c.create_text(W - 14, fy, text=f"ЕЩЁ +{len(group) - len(vis)}", fill=DIM, anchor="e", font=self.small)
        if self.R:
            self.rrect(0, 0, W - 1, H - 1, self.R, outline=BORDER)
        else:
            c.create_rectangle(0, 0, W - 1, H - 1, outline=BORDER)

    def draw_ticker(self, frame, ok):
        """Бегущая строка: позиции, номера, имена и интервалы едут справа налево; слева — позиция и таймер."""
        c, rh, W = self.canvas, self.rh, self.width
        H = int(rh * 1.9)
        if H != self._h:
            c.config(height=H)
            self._h = H
        cars = frame["cars"] if frame else []
        timed = bool(frame and frame["kind"] in TIMED_KINDS)
        ordered = order_cars(cars, timed)
        me = next((x for x in ordered if x["player"]), None)
        if self.cfg.get("class_only", False) and me and me["cls"] != "UNKNOWN":
            group = [x for x in ordered if x["cls"] == me["cls"]]
        else:
            group = ordered
        gaps = group_gaps(group, timed) if group else {}
        if self.R:
            self.rrect(0, 0, W - 1, H - 1, self.R, fill=HDR_BG)
        else:
            c.create_rectangle(0, 0, W - 1, H - 1, fill=HDR_BG, outline="")
        capw = int(self.cw * 21)
        cy = H // 2
        fnt, fb = self.font, self.bold
        items = []
        for i, car in enumerate(group):
            ahead_txt = gaps[car["key"]][0] if i > 0 else ("ЛИДЕР" if not timed else fmt_lap(car["best"]) if car["best"] else "")
            seg = [("bar", 5, class_color(car["cls"]), None), ("t", fb.measure(f"{i + 1}") + 8, ACCENT if i == 0 else FG, (f"{i + 1}", fb)),
                   ("t", fnt.measure("#" + (car["number"] or "-")) + 10, DIM, ("#" + (car["number"] or "-"), fnt)),
                   ("t", fb.measure(car["driver"].upper()[:14]) + 12, FG, (car["driver"].upper()[:14], fb if car["player"] else fnt)),
                   ("t", fnt.measure(ahead_txt) + 4, ACCENT if car["player"] else DIM, (ahead_txt, fnt))]
            items.append((car, seg, sum(w for _, w, *_ in seg) + int(self.cw * 4)))
        if items:
            total = sum(w for _, _, w in items)
            off = (time.time() * float(self.cfg.get("ticker_speed", 70))) % max(1, total)
            x = capw - off
            rounds = 0
            while x < W and rounds < 400:
                for car, seg, wd in items:
                    if x + wd >= capw and x < W - 12:
                        if car["player"]:
                            c.create_rectangle(max(capw, x - 4), 4, min(W - 12, x + wd - int(self.cw * 3)), H - 5, fill=ME_BG, outline=ME_EDGE)
                        xx = x
                        for kind, w, col, payload in seg:
                            if kind == "bar":
                                if xx >= capw and xx + w <= W - 12:
                                    c.create_rectangle(xx, 6, xx + 4, H - 7, fill=col, outline="")
                            elif xx >= capw and xx + w <= W - 12:
                                c.create_text(xx, cy, text=payload[0], fill=col, anchor="w", font=payload[1])
                            xx += w
                    x += wd
                    if x >= W:
                        break
                rounds += 1
        # левая и правая «шторки» закрывают края строки
        if self.R:
            self.rrect(0, 0, capw, H - 1, self.R, fill=HDR_BG)
            c.create_rectangle(self.R, 0, capw, H - 1, fill=HDR_BG, outline="")
            self.rrect(W - 14 - self.R, 0, W - 1, H - 1, self.R, fill=HDR_BG)
            c.create_rectangle(W - 14 - self.R, 0, W - 1 - self.R, H - 1, fill=HDR_BG, outline="")
        else:
            c.create_rectangle(0, 0, capw, H - 1, fill=HDR_BG, outline="")
            c.create_rectangle(W - 14, 0, W - 1, H - 1, fill=HDR_BG, outline="")
        c.create_rectangle(capw - 1, 4, capw, H - 5, fill=ACCENT, outline="")
        pos_txt = f"P{[x['key'] for x in group].index(me['key']) + 1}/{len(group)}" if me and group else ("ЖДУ ИГРУ" if not ok else "—")
        c.create_text(12, cy, text=pos_txt, fill=FG, anchor="w", font=self.bold)
        if frame:
            gn = game_now()
            now = gn if gn is not None else frame["elapsed"]
            end = frame["end_et"]
            if 0 < frame["max_laps"] < 100_000:
                lead = max((x["laps"] for x in cars), default=0)
                timer = f"{min(lead + 1, frame['max_laps'])}/{frame['max_laps']}"
            elif 0 < end < 1e7:
                timer = fmt_hms(min(end, end - now))
            else:
                timer = fmt_hms(now)
            c.create_text(capw - 10, cy, text=timer, fill=ACCENT, anchor="e", font=self.small)
        if self.R:
            self.rrect(0, 0, W - 1, H - 1, self.R, outline=BORDER)
        else:
            c.create_rectangle(0, 0, W - 1, H - 1, outline=BORDER)

    def tick(self):
        try:
            with lock:
                ver, frame = state["ver"], state["frame"]
                ok_, ts_ = state["ok"], state["frame_ts"]
            self.update_visibility(None if (frame is None or (not ok_ and time.time() - ts_ > 6.0)) else frame)   # скрытие/появление проверяем на каждом тике
            if frame:
                fuel_calc.update(frame)
                try:
                    delta_tracker.update(frame, self.cfg.get("delta_ref", "best"))
                    stint_tracker.update(frame)
                    wear_tracker.update(frame)
                    gap_tracker.update(frame)
                    alert_engine.update(frame, self.cfg)
                except Exception as e:
                    if not getattr(self, "_aux_err", False):
                        self._aux_err = True
                        print("Доп. панели: ошибка:", repr(e))
                try:
                    recorder.update(frame, self.cfg.get("stats", True))
                except Exception as e:                   # статистика не должна мешать отрисовке
                    if not recorder.error_shown:
                        recorder.error_shown = True
                        print("Статистика: ошибка записи:", repr(e))
            self.poll_cmd()
            for w in self.subs:
                if self.cfg.get(w.ON, True):
                    w.tick(frame, ver)
            gn = game_now()
            self.check_layout(frame)
            fast = 30 if self.stand_mode() == "ticker" else 4
            sig = (ver // 3, int(gn) if gn is not None else 0, int(time.time() * fast), self.locked,
                   self.cfg["rows"], self.cfg["font"], self.cfg.get("class_only", False))
            if sig != self._sig:
                self._sig = sig
                self.draw()
            if self._fresh:
                self._fresh = False
                self.stretch_stand(True)
            self.decorate()
            self.update_move_panel()
        except Exception as e:                   # оверлей не должен падать из-за одной кривой записи
            self.canvas.delete("all")
            self.canvas.create_text(10, 10, text=f"Ошибка отрисовки: {e}", fill=RED, anchor="nw")
        self.root.after(40, self.tick)


# =========================================================================
# 5. Демо и запуск
# =========================================================================
def make_demo_source(game="lmu"):
    t0 = time.monotonic()
    WARP, LEN = 10.0, 13626.0                         # в демо круг проезжается за ~20 секунд

    def shape(u):
        a = 2 * math.pi * u
        return (900 * math.cos(a) + 260 * math.cos(2 * a + 0.7) + 130 * math.sin(3 * a),
                520 * math.sin(a) + 190 * math.sin(2 * a) + 60 * math.cos(3 * a))
    raw_len = sum(math.hypot(*(b[i] - a[i] for i in (0, 1))) for a, b in
                  ((shape(i / 2000), shape((i + 1) / 2000)) for i in range(2000)))
    scale = LEN / raw_len
    world = lambda u: (shape(u % 1.0)[0] * scale, shape(u % 1.0)[1] * scale)
    names = [("HYPERCAR", "Ferrari 499P #50", "A. FUOCO"), ("HYPERCAR", "Toyota GR010 #8", "S. BUEMI"),
             ("HYPERCAR", "Porsche 963 #6", "K. ESTRE"), ("HYPERCAR", "Cadillac V-Series.R #12", "A. LYNN"),
             ("LMP2", "Oreca 07 #22", "F. ALBUQUERQUE"), ("LMP2", "Oreca 07 #28", "J. ALOMAR"),
             ("LMP2", "Oreca 07 #35", "P. KUBICA"),
             ("LMGT3", "Ferrari 296 GT3 #57", "VLADIMIR ZINOVIEV"), ("LMGT3", "Porsche 911 GT3 R #92", "R. LIETZ"),
             ("LMGT3", "BMW M4 GT3 #31", "D. HARPER"), ("LMGT3", "Corvette Z06 GT3.R #33", "N. CATSBURG"),
             ("LMGT3", "McLaren 720S GT3 #59", "N. MOLLER"), ("LMGT3", "Lexus RC F GT3 #87", "J. LOPEZ"),
             ("LMGT3", "Mercedes AMG GT3 #79", "M. CRESSONI")]

    def _demo_ori(world, f):
        import math as _mm
        (x0, z0), (x1, z1) = world(f), world(f + 0.0005)
        n = _mm.hypot(x1 - x0, z1 - z0) or 1.0
        fx, fz = (x1 - x0) / n, (z1 - z0) / n
        return (fz, -fx, fx, fz)                         # как в игре: первая ось вправо, третья вперёд

    def source():
        e = time.monotonic() - t0
        cars = []
        for i, (cls, veh, drv) in enumerate(names):
            lap = 96.0 + i * 0.35
            dist_i = e * WARP * (60.0 + i * 0.08) + i * 330.0
            lat_i = 0.0
            if i in (6, 8, 9):                                  # для демонстрации радара: три машины рядом с игроком
                dist_i = e * WARP * (60.0 + 7 * 0.08) + 7 * 330.0
                dist_i += {6: -(26.0 + 22.0 * math.sin(e * 0.7)), 8: 4.0 * math.sin(e * 0.5 + 1.0),
                           9: 18.0 + 22.0 * math.sin(e * 0.4)}[i]
                lat_i = {6: 0.0, 8: 3.4 * math.sin(e * 0.3) , 9: -2.0}[i]
            ori_i = _demo_ori(world, (dist_i % LEN) / LEN)
            px_i, pz_i = world((dist_i % LEN) / LEN)
            px_i, pz_i = px_i - lat_i * ori_i[0], pz_i - lat_i * ori_i[1]
            cars.append({
                "key": i + 1, "driver": drv, "vehicle": veh, "model": veh.split(" #")[0],
                "irating": 900 + (i * 733) % 5200, "lic": ("A 4.12", "B 3.40", "C 2.75", "R 0.95")[i % 4],
                "lic_col": ("0x0153db", "0x00c702", "0xfeec04", "0xfc0706")[i % 4],
                "number": veh.split("#")[1], "cls": cls, "laps": int(dist_i // LEN) + 20 - i // 3, "finish": 0,
                "best": lap + 0.2, "last": lap + (0.0 if i % 3 == 0 else 0.7), "place": i + 1,
                "player": drv.startswith("VLADIMIR"), "in_pits": i == 10 and int(e) % 40 > 30,
                "garage": False, "pit_state": 3 if (i == 10 and int(e) % 40 > 30) else 0,
                "pitstops": i % 3, "penalties": 1 if i == 5 else 0,
                "gap_next": 1.2 + i * 0.1, "laps_next": 0, "gap_leader": i * 4.7 + (e % 5) * 0.01,
                "laps_leader": 1 if i == 13 else 0, "front": "Medium", "rear": "Hard" if i % 4 == 0 else "Medium",
                "fuel": max(5.0, 85.0 - e * 0.12 - i), "fuel_cap": 90.0,
                "ve": 0.0 if game in ("rf2", "iracing") else max(0.04, 1.0 - (e * 0.004 + i * 0.03) % 1.0),
                "fuel_fraction": 200, "speed": 50.0, "has_tel": True,
                "wheels": [{"temp": 82 + 14 * math.sin(e * 0.3 + k) + (i % 3) * 3, "press": 168.0 + k * 2.5, "wear": max(0.05, 0.96 - e * 0.002 - 0.01 * k),
                            "brake": 480 + 380 * (0.5 + 0.5 * math.sin(e * 0.9 + k))} for k in range(4)],
                "lap_dist": dist_i % LEN, "est_lap": lap, "pos": (px_i, pz_i),
                "sector": (lambda f: 1 if f < 0.31 else 2 if f < 0.67 else 0)((dist_i % LEN) / LEN),
                "ori": ori_i})
        import math as _m
        phase = e * 1.1
        thr = max(0.0, min(1.0, 0.55 + 0.7 * _m.sin(phase)))
        brk = max(0.0, min(1.0, -0.2 - 1.1 * _m.sin(phase + 0.5)))
        player = next(c for c in cars if c["player"])
        player["speed"] = 22.0 + 28.0 * (0.5 + 0.5 * _m.sin(phase - 0.6))
        inputs = {"limiter": 44 <= e % 60 < 56, "rpm": 9000 + 6500 * (0.5 + 0.5 * _m.sin(e * 1.3)), "max_rpm": 15500.0,
                  "gear": 1 + int(4 * (0.5 + 0.5 * _m.sin(e * 0.5))), "thr": thr, "brk": brk,
                  "clu": 0.0, "steer": 0.7 * _m.sin(e * 0.9), "range": 540.0, "abs": brk > 0.75,
                  "water": 88.0 + 6.0 * _m.sin(e * 0.2), "oil": 104.0 + 9.0 * _m.sin(e * 0.15)}
        me = next(c for c in cars if c["player"])
        cyc = e % 40.0                                   # демо старта: формирующий круг, 5 красных, зелёный
        ph, sl = (3, 0) if cyc < 6 else (4, int(cyc - 6) + 1) if cyc < 11 else (5, 0)
        if cyc < 11:
            me["speed"] = 0.0
        if e % 60 >= 40:                                 # демо пит-лейна: красный -> зелёный (лимитер) -> красный
            me["in_pits"] = True
            me["speed"] = 22.0
        return {"inputs": inputs, "player_pos": me["pos"], "player_lap": me["laps"], "track": "DEMO TRACK",
                "session_type": 10, "kind": "RACE", "label": "RACE",
                "elapsed": 3600 + e, "end_et": 3600 + 1800.0, "max_laps": 0, "game_phase": ph, "yellow": 0,
                "start_light": sl, "red_lights": 5, "wet": 0.18 + 0.15 * _m.sin(e * 0.1), "player_name": "VLADIMIR ZINOVIEV", "air": 19.0, "track_temp": 31.0,
                "wind": (3.5 * _m.cos(e * 0.05), 2.2 * _m.sin(e * 0.05)),
                "rain": 0.04 + 0.03 * _m.sin(e * 0.2), "track_len": 13626.0, "options_location": 3, "game": game, "in_realtime": True, "session_start": 1, "scoring_event": int(e * 2),
                "player_elapsed": 3600 + e, "cars": cars}
    return source


def diagnose(game="auto"):
    print(f"разделы памяти: {MAPPING_NAME} (LMU), {RF2_SCORING_NAME} + {RF2_TELEMETRY_NAME} (rFactor 2)")
    reader = AutoReader(game)
    try:
        f = reader.read()
    except SharedMemoryError as e:
        print("статус: НЕТ ДАННЫХ -", e)
        return 1
    print("игра:", GAME_NAMES.get(f["game"], "Le Mans Ultimate"))
    if f.get("layout"):
        print("раскладка:", f["layout"])
    inp = f.get("inputs")
    print("ввод игрока:", "не прочитан (нет телеметрии игрока)" if not inp else
          f"передача {inp['gear']}, газ {inp['thr'] * 100:.0f}%, тормоз {inp['brk'] * 100:.0f}%, "
          f"сцепление {inp['clu'] * 100:.0f}%, руль {inp['steer'] * 100:+.0f}% (диапазон {inp['range']:.0f}°)")
    if f.get("wheel_dbg"):
        wb, ww, wt, wp = f["wheel_dbg"]
        me = next((c for c in f["cars"] if c["player"]), None)
        print(f"колёса игрока: смещение {wb}, износ(FL)={ww!r}, температура(FL)={wt!r}, давление(FL)={wp!r}, "
              f"прочитано={'да' if me and me.get('wheels') else 'НЕТ'}")
    me_ = next((c for c in f["cars"] if c["player"]), None)
    if me_ is not None and "control" in me_:
        print("кто управляет вашей машиной:", {0: "вы (локальный игрок)", 1: "ИИ (бот)", 2: "удалённый игрок", 3: "повтор"}.get(me_["control"], me_["control"]))
    if f.get("tel_count") is not None:
        print(f"телеметрия есть у {f['tel_count']} из {len(f['cars'])} машин")
    print(f"трасса: {f['track']!r}  сессия: {f['label']}  время: {f['elapsed']:.1f} с  конец: {f['end_et']:.1f}")
    print(f"воздух {f['air']:.1f}°  трасса {f['track_temp']:.1f}°  options_location={f['options_location']}")
    from collections import Counter
    print("всего машин в сессии:", len(f["cars"]), "| по классам:",
          ", ".join(f"{k} x{v}" for k, v in Counter(c["cls"] for c in f["cars"]).items()) or "-")
    for c in order_cars(f["cars"], f["kind"] in TIMED_KINDS):
        print(f"  P{c['place']:>3} {c['cls']:<9} #{c['number'] or '?':<3} {c['driver']:<24} круги={c['laps']:<3} "
              f"лучший={c['best']:7.3f} VE={c['ve'] * 100:5.1f}% топливо={c['fuel']:5.1f} "
              f"шины={tyre_text(c) or '-'} гараж={int(c['garage'])} игрок={int(c['player'])} "
              f"| бак {c['fuel']:6.2f}/{c['fuel_cap']:6.2f} л, скорость {c['speed'] * 3.6:5.0f} км/ч, "
              f"телем.={'да' if c['has_tel'] else 'нет'} пит-стопов={c['pitstops']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="LMU standings overlay")
    ap.add_argument("--config", help="файл профиля настроек (по умолчанию yassno_overlay.json рядом со скриптом)")
    ap.add_argument("--rows", type=int)
    ap.add_argument("--font", type=int)
    ap.add_argument("--alpha", type=float)
    ap.add_argument("--hz", type=float, default=30.0, help="как часто читать память игры (по умолчанию 30)")
    ap.add_argument("--game", choices=["auto", "lmu", "rf2", "iracing"], default="auto", help="какую игру читать")
    ap.add_argument("--logos", help="папка с логотипами марок (png)")
    ap.add_argument("--unlock", action="store_true", help="запустить в режиме перемещения")
    ap.add_argument("--demo", action="store_true", help="тестовая гонка без игры")
    ap.add_argument("--diagnose", action="store_true", help="напечатать, что видно в памяти игры")
    a = ap.parse_args()
    if a.diagnose:
        sys.exit(diagnose(a.game))
    global CFG_PATH
    if a.config:
        CFG_PATH = os.path.abspath(a.config)
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)

    cfg = {"x": 20, "y": 20, "rows": 12, "font": 13, "alpha": 0.94, "locked": True,
           "hide_garage": True, "class_only": False, "hide_cols": []}
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("ver") != CFG_VERSION:
            if saved.get("ver", 0) < 3:
                for k in ("font", "rows"):
                    saved.pop(k, None)           # новые значения по умолчанию для новой версии
            saved["class_only"] = False          # с версии 4 по умолчанию показываем все машины
        cfg.update(saved)
    except (OSError, ValueError):
        pass
    cfg["ver"] = CFG_VERSION
    apply_theme(cfg.get("theme"), cfg.get("win_style", "classic"))
    for k in ("rows", "font", "alpha"):
        if getattr(a, k) is not None:
            cfg[k] = getattr(a, k)
    if a.unlock:
        cfg["locked"] = False

    logo_dir = find_logo_dir(a.logos)
    print("Игра:", {"auto": "определяется сама (LMU, rFactor 2 или iRacing)", "lmu": "Le Mans Ultimate", "rf2": "rFactor 2", "iracing": "iRacing"}[a.game])
    print("Оверлей запущен.  Ctrl+Shift+L — режим перемещения,  Ctrl+Shift+R — вернуть окна на экран,  Ctrl+Shift+Q — выход.")
    print("Логотипы марок:", logo_dir or "не найдены (положите папку logos рядом со скриптом)")
    print("Это окно не закрывайте, пока пользуетесь оверлеем.")
    stop = threading.Event()
    threading.Thread(target=poller, args=(stop, a.hz, make_demo_source(a.game if a.game in ("rf2", "iracing") else "lmu") if a.demo else None, a.game),
                     daemon=True).start()
    ov = Overlay(cfg, logo_dir)
    try:
        sw, sh = ov.root.winfo_screenwidth(), ov.root.winfo_screenheight()
        print(f"Экран {sw}x{sh}. Окна: таблица ({cfg['x']},{cfg['y']}), релатив ({cfg['rel_x']},{cfg['rel_y']}), "
              f"ввод ({cfg['ped_x']},{cfg['ped_y']}), карта ({cfg['map_x']},{cfg['map_y']}).")
    except Exception:
        pass
    try:
        ov.root.mainloop()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
