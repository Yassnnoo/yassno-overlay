#!/usr/bin/env python3
"""Чтение игр: LMU и rFactor 2 (разделяемая память), iRacing (irsdk). Вынесено из yassno_overlay.py.
Сам по себе не запускается — его подключает оверлей."""
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

HERE = os.path.dirname(os.path.abspath(__file__))

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
                "side_state": int(f("CarLeftRight")) if "CarLeftRight" in V and player else 0,
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
