#!/usr/bin/env python3
"""Yassno ETS2 — оверлей для Euro Truck Simulator 2: приборы, рейс и карта с положением грузовика.

Данные берутся из плагина телеметрии SCS (RenCloud/scs-sdk-plugin, shared memory «Local\\SCSTelemetry»).
Карта: тайлы SCS_Map_Tiles (MIT). Низкие масштабы лежат в папке ets2_tiles, остальные докачиваются с GitHub и кэшируются.

  python yassno_ets2.py            обычный запуск
  python yassno_ets2.py --demo     тестовая поездка без игры
  python yassno_ets2.py --unlock   стартовать в режиме перемещения окон
"""
import argparse
import collections
import ctypes
import gc
import json
import math
import mmap
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "yassno_ets2.json")
CMD_PATH = os.path.join(HERE, "yassno_ets2.cmd")
STATE_PATH = os.path.join(HERE, "yassno_ets2.state")
TILE_DIR = os.path.join(HERE, "ets2_tiles")
TILE_URL = "https://raw.githubusercontent.com/Unicor-p/SCS_Map_Tiles/master/ets2/latest/Tiles/{z}/{x}/{y}.png"
MMF_NAME = "Local\\SCSTelemetry"
MMF_SIZE = 32 * 1024

BG, PANEL, BORDER = "#0f1319", "#161b23", "#2a3340"
FG, DIM = "#f2f4f8", "#8a96a8"
ACCENT, GREEN, YELLOW, RED = "#ff9f1a", "#2ee26d", "#ffc107", "#ff3b30"

SCALE_MIN, SCALE_MAX = 0.5, 3.0
GRIP = 20                                        # уголок для изменения размера, физ. пикселей

# темы оверлея: фон, панель, рамка, текст, приглушённый, акцент, дорожка полос, фон циферблата, выбранная вкладка
THEMES = {
    "amber": ("Тёмная, янтарный акцент", "#0f1319", "#161b23", "#2a3340", "#f2f4f8", "#8a96a8", "#ff9f1a", "#262d38", "#10151c", "#2a2210"),
    "blue": ("Тёмная, синий акцент", "#0d1117", "#141a22", "#27313f", "#f2f4f8", "#8a96a8", "#3aa0ff", "#242c38", "#0e141b", "#14263a"),
    "red": ("Тёмная, красный акцент", "#110e10", "#1a1517", "#3a2a2d", "#f4f1f2", "#9a8d90", "#ff4d4d", "#2e2427", "#150f11", "#3a1618"),
    "green": ("Тёмная, зелёный акцент", "#0d1210", "#131a17", "#25352e", "#f0f5f2", "#86998f", "#2ee26d", "#212b26", "#0d1411", "#12301f"),
    "black": ("Чёрная (AMOLED)", "#000000", "#06070a", "#20242b", "#ffffff", "#8a96a8", "#ff9f1a", "#1a1d22", "#000000", "#241a06"),
    "light": ("Светлая", "#e6eaf0", "#ffffff", "#b4bfcd", "#10141a", "#5a6676", "#d9780a", "#d5dbe4", "#f1f4f8", "#fbe6c8"),
}
THEME_ORDER = ("amber", "blue", "red", "green", "black", "light")
TRACK, DIALBG, TABSEL = "#262d38", "#10151c", "#2a2210"

# стили окон — те же, что в оверлеях остальных игр: (множитель скругления, метка у заголовка)
WIN_STYLES = {"classic": (1.0, "red"), "square": (0.0, "red"), "soft": (1.9, "red"),
              "minimal": (0.6, "none"), "accent": (1.0, "accent")}
WIN_STYLE_NAMES = (("classic", "Классика: красная метка"), ("square", "Строгий: прямые углы"),
                   ("soft", "Мягкий: сильно скруглённые"), ("minimal", "Минимализм: без цветной метки"),
                   ("accent", "Акцент: метка и рамка цвета акцента"))
KEY = "#010203"                                  # служебный цвет: везде, где он, окно прозрачное (скруглённые углы)
MARK, STYLE_R = "#e5322d", 1.0                   # цвет метки у заголовка ("" — без метки) и множитель скругления
BASE_R = 12                                      # скругление в базовых координатах при множителе 1.0
GLOW = ("#2ee26d", "#1f9d4f", "#135c2e")         # зелёное свечение рамки в режиме перемещения
EDGE = 9                                         # ширина полосы у края окна, за которую его растягивают
CURSORS = {(1, 0): "size_we", (-1, 0): "size_we", (0, 1): "size_ns", (0, -1): "size_ns",
           (1, 1): "size_nw_se", (-1, -1): "size_nw_se", (1, -1): "size_ne_sw", (-1, 1): "size_ne_sw"}
MOVE_NAMES = (("win_mfd", "МФД"), ("win_map", "Карта"), ("win_dash", "Приборы"), ("win_trip", "Рейс"), ("win_convoy", "Конвой"))


def _mix(a, b, t):
    """Смесь двух цветов #rrggbb: t=0 — a, t=1 — b."""
    try:
        ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    except (ValueError, TypeError):
        return a
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(ca, cb))


def apply_theme(name, style="classic"):
    """Переключает цвета и стиль окон модуля (окна берут их при каждой перерисовке)."""
    global BG, PANEL, BORDER, FG, DIM, ACCENT, TRACK, DIALBG, TABSEL, MARK, STYLE_R
    t = THEMES.get(name) or THEMES["amber"]
    BG, PANEL, BORDER, FG, DIM, ACCENT, TRACK, DIALBG, TABSEL = t[1:]
    STYLE_R, kind = WIN_STYLES.get(style, WIN_STYLES["classic"])
    MARK = {"red": "#e5322d", "none": "", "accent": ACCENT}[kind]
    if kind == "accent":
        BORDER = _mix(BG, ACCENT, 0.65)
    try:
        MfdWin.ORANGE = ACCENT
    except NameError:
        pass


def speed_col(d, base):
    """Цвет скорости: в пределах лимита — обычный, при превышении плавно краснеет (полностью красный при +20 км/ч)."""
    try:
        lim, sp = float(d["limit"]), abs(float(d["speed"]))
    except (TypeError, ValueError, KeyError):
        return base
    if lim <= 1 or sp <= lim + 0.5:
        return base
    return _mix(base, "#ff1a1a", min(1.0, (sp - lim) / 20.0))


FRAME_LEVELS = ((100, "Сплошной фон"), (75, "Прозрачность 25%"), (50, "Прозрачность 50%"), (25, "Прозрачность 75%"), (0, "Без рамки и фона"))
STIPPLES = {75: "gray75", 50: "gray50", 25: "gray25"}


def frame_level(cfg, key):
    """Непрозрачность фона и рамки окна, %: 100 — сплошной, 0 — без рамки. Содержимое окна всегда чёткое."""
    try:
        allv = int(cfg.get("frame_all", -1))
        v = allv if allv >= 0 else int(cfg.get("frame_op", {}).get(key, 100))
    except (TypeError, ValueError, AttributeError):
        return 100
    return min(FRAME_LEVELS, key=lambda it: abs(it[0] - v))[0]


def win_radius():
    """Скругление окон в базовых координатах."""
    return int(round(BASE_R * STYLE_R))


def corner_bg():
    """Чем залиты скруглённые углы: прозрачным ключевым цветом (Windows) или фоном темы."""
    return KEY if sys.platform == "win32" else BG


def rr_points(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def grip_zone(canvas, e):
    """За какой край/угол схвачено окно: (sx, sy), где -1 — левый/верхний, 1 — правый/нижний, 0 — не затронут. None — не у края."""
    try:
        W, H = int(float(canvas.cget("width"))), int(float(canvas.cget("height")))
    except (tk.TclError, ValueError):
        return None
    if e.x >= W - 32 and e.y <= 32:                  # там крестик закрытия
        return None
    c = GRIP + 2                                     # в углах зона шире, чем вдоль сторон
    near_y = e.y < c or e.y >= H - c
    near_x = e.x < c or e.x >= W - c
    sx = -1 if e.x < (c if near_y else EDGE) else 1 if e.x >= W - (c if near_y else EDGE) else 0
    sy = -1 if e.y < (c if near_x else EDGE) else 1 if e.y >= H - (c if near_x else EDGE) else 0
    return (sx, sy) if (sx or sy) else None


DEFAULTS = {"locked": False, "alpha": 0.94, "mph": False, "win_map": True, "win_dash": True, "win_trip": True,
            "map_span": 1600.0, "map_heading_up": True, "map_size": 360, "map_auto_zoom": True,
            "pos": {}, "game_dir": "", "autostart": False, "scale": {}, "hide_idle": True, "dash_style": "classic", "theme": "amber", "win_style": "classic", "show_route": True, "frame_op": {}, "frame_all": -1, "win_convoy": False, "convoy_ids": "",
            "win_mfd": True, "mfd_screen": 0, "mfd_keys": "numpad", "mfd_binds": {}}


# ---------------------------------------------------------------------------
# настройки
# ---------------------------------------------------------------------------
def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            for k, v in d.items():
                if k == "mfd_binds" and isinstance(v, dict):
                    cfg[k] = {str(a): str(b) for a, b in v.items() if isinstance(b, str)}
                elif k == "scale" and isinstance(v, dict):
                    cfg[k] = {str(a): max(SCALE_MIN, min(SCALE_MAX, float(b))) for a, b in v.items()
                              if isinstance(b, (int, float)) and not isinstance(b, bool)}
                elif k in DEFAULTS and (type(v) is type(DEFAULTS[k]) or (isinstance(DEFAULTS[k], float) and type(v) is int)):
                    cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg):
    try:
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CFG_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# общие горячие клавиши (те же, что у остальных игр: профили в profiles/*.json, ключ "hotkeys")
# ---------------------------------------------------------------------------
HOTKEY_DEFAULTS = {"lock": "Ctrl+Shift+L", "reset": "Ctrl+Shift+R", "quit": "Ctrl+Shift+Q", "hide": "Ctrl+Shift+H",
                   "tim": "Ctrl+Shift+T", "map": "Ctrl+Shift+M", "delta": "Ctrl+Shift+D", "tyres": "Ctrl+Shift+Y"}
PROFILES_DIR = os.path.join(HERE, "profiles")


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


def load_hotkeys(folder=PROFILES_DIR):
    """Сочетания из профилей лаунчера (раздел «Горячие клавиши оверлея» в настройках) поверх стандартных."""
    keys = dict(HOTKEY_DEFAULTS)
    try:
        names = sorted(os.listdir(folder), key=lambda n: (n != "ets2.json", n))
    except OSError:
        return keys
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, n), encoding="utf-8") as f:
                hk = json.load(f).get("hotkeys")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(hk, dict):
            for k in keys:
                if isinstance(hk.get(k), str) and parse_hotkey(hk[k]):
                    keys[k] = hk[k]
            break
    return keys


# ---------------------------------------------------------------------------
# свои кнопки: клавиатура и кнопки руля (winmm joystick API)
# ---------------------------------------------------------------------------
BIND_NAMES = ("s0", "s1", "s2", "s3", "s4", "s5", "next", "prev")


class _JoyInfoEx(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in ("dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos", "dwRpos", "dwUpos", "dwVpos",
                                               "dwButtons", "dwButtonNumber", "dwPOV", "dwReserved1", "dwReserved2")]


def joy_state(ids=range(16)):
    """{(id, номер кнопки)} нажатых кнопок джойстиков/рулей. Только Windows."""
    out = set()
    if sys.platform != "win32":
        return out
    try:
        winmm = ctypes.windll.winmm
        for i in ids:
            info = _JoyInfoEx()
            info.dwSize = ctypes.sizeof(_JoyInfoEx)
            info.dwFlags = 0x80                                  # JOY_RETURNALLBUTTONS
            if winmm.joyGetPosEx(i, ctypes.byref(info)) == 0:
                for b in range(32):
                    if info.dwButtons & (1 << b):
                        out.add((i, b))
    except Exception:                                            # noqa: BLE001
        pass
    return out


def parse_bind(spec):
    """'k:101' -> ('k', 101); 'j:0:5' -> ('j', 0, 5); иначе None."""
    try:
        p = str(spec).split(":")
        if p[0] == "k" and len(p) == 2:
            return ("k", int(p[1]))
        if p[0] == "j" and len(p) == 3:
            return ("j", int(p[1]), int(p[2]))
    except ValueError:
        pass
    return None


_VK_NAMES = {8: "Backspace", 9: "Tab", 13: "Enter", 19: "Pause", 27: "Esc", 32: "Пробел", 33: "PgUp", 34: "PgDn", 35: "End", 36: "Home",
             37: "←", 38: "↑", 39: "→", 40: "↓", 45: "Insert", 46: "Delete", 106: "Num*", 107: "Num+", 109: "Num−", 110: "Num.", 111: "Num/",
             186: ";", 187: "=", 188: ",", 189: "-", 190: ".", 191: "/", 192: "`", 219: "[", 220: "\\", 221: "]", 222: "'"}


def vk_name(vk):
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return f"Num{vk - 0x60}"
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return f"клавиша {vk}"


def bind_name(spec):
    b = parse_bind(spec)
    if not b:
        return "—"
    return vk_name(b[1]) if b[0] == "k" else f"кнопка руля {b[2] + 1} (устр. {b[1] + 1})"


def bind_down(spec, joy=None):
    b = parse_bind(spec)
    if not b:
        return False
    if b[0] == "k":
        if sys.platform != "win32":
            return False
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(b[1]) & 0x8000)
        except Exception:                                        # noqa: BLE001
            return False
    return (b[1], b[2]) in (joy if joy is not None else joy_state((b[1],)))


# ---------------------------------------------------------------------------
# установка плагина телеметрии в папку игры
# ---------------------------------------------------------------------------
PLUGIN_API = "https://api.github.com/repos/RenCloud/scs-sdk-plugin/releases/latest"
PLUGIN_PAGE = "https://github.com/RenCloud/scs-sdk-plugin/releases"
PLUGIN_DLL = "scs-telemetry.dll"
ETS2_PROCESS = "eurotrucks2.exe"


def looks_like_game_dir(d):
    return bool(d) and os.path.isfile(os.path.join(d, "bin", "win_x64", "eurotrucks2.exe"))


def find_game_dir(libs):
    """Папка Euro Truck Simulator 2 среди библиотек Steam."""
    for lib in libs:
        d = os.path.join(lib, "steamapps", "common", "Euro Truck Simulator 2")
        if looks_like_game_dir(d):
            return d
    return ""


def plugins_dir(game_dir):
    return os.path.join(game_dir, "bin", "win_x64", "plugins")


def plugin_installed(game_dir):
    d = plugins_dir(game_dir)
    try:
        return any(n.lower().startswith("scs-telemetry") and n.lower().endswith(".dll") for n in os.listdir(d))
    except OSError:
        return False


def _pick_asset(assets):
    """Нужный файл релиза: сначала прямой dll, потом zip."""
    best = None
    for a in assets:
        n = str(a.get("name", "")).lower()
        url = a.get("browser_download_url", "")
        if not url or a.get("size", 0) > 60_000_000:
            continue
        score = 3 if (n.endswith(".dll") and "telemetry" in n) else 2 if (n.endswith(".zip") and "src" not in n and "source" not in n) else 0
        if score and (best is None or score > best[0]):
            best = (score, a)
    return best[1] if best else None


def install_plugin(game_dir, say=print):
    """Скачивает последний релиз плагина и кладёт dll в bin/win_x64/plugins. Возвращает текст ошибки или None."""
    import io
    import zipfile
    try:
        if not looks_like_game_dir(game_dir):
            return "Не нашёл eurotrucks2.exe в bin\\win_x64 выбранной папки игры"
        say("Ищу последнюю версию плагина…")
        req = urllib.request.Request(PLUGIN_API, headers={"User-Agent": "yassno-ets2", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.loads(r.read().decode("utf-8"))
        asset = _pick_asset(rel.get("assets", []))
        if not asset:
            return "В релизе плагина нет подходящего файла. Скачайте вручную: " + PLUGIN_PAGE
        say("Скачиваю " + asset["name"] + "…")
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "yassno-ets2"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        dll = None
        if asset["name"].lower().endswith(".dll"):
            dll = data
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = [n for n in z.namelist() if os.path.basename(n).lower().startswith("scs-telemetry") and n.lower().endswith(".dll")]
                names.sort(key=lambda n: (("x64" not in n.lower() and "64" not in n.lower()), len(n)))
                if names:
                    dll = z.read(names[0])
        if not dll or dll[:2] != b"MZ":
            return "В скачанном архиве не нашёл scs-telemetry.dll. Поставьте плагин вручную: " + PLUGIN_PAGE
        d = plugins_dir(game_dir)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, PLUGIN_DLL), "wb") as f:
            f.write(dll)
        return None
    except PermissionError:
        return "Нет прав записи в папку игры. Запустите лаунчер от имени администратора или скопируйте dll вручную."
    except Exception as e:                               # noqa: BLE001
        return "Не получилось: " + str(e)


# ---------------------------------------------------------------------------
# формат shared memory плагина (RenCloud scs-sdk-plugin, rev 12): зеркало scs-telemetry-common.hpp
# ---------------------------------------------------------------------------
class _ScsValues(ctypes.Structure):
    _fields_ = [("telemetry_plugin_revision", ctypes.c_uint), ("version_major", ctypes.c_uint), ("version_minor", ctypes.c_uint),
                ("game", ctypes.c_uint), ("telemetry_version_game_major", ctypes.c_uint), ("telemetry_version_game_minor", ctypes.c_uint)]


class _CommonUi(ctypes.Structure):
    _fields_ = [("time_abs", ctypes.c_uint)]


class _ConfigUi(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("gears", "gears_reverse", "retarderStepCount", "truckWheelCount", "selectorCount",
                                              "time_abs_delivery", "maxTrailerCount", "unitCount", "plannedDistanceKm")]


class _TruckUi(ctypes.Structure):
    _fields_ = [("shifterSlot", ctypes.c_uint), ("retarderBrake", ctypes.c_uint), ("lightsAuxFront", ctypes.c_uint),
                ("lightsAuxRoof", ctypes.c_uint), ("truck_wheelSubstance", ctypes.c_uint * 16),
                ("hshifterPosition", ctypes.c_uint * 32), ("hshifterBitmask", ctypes.c_uint * 32)]


class _GameplayUi(ctypes.Structure):
    _fields_ = [("jobDeliveredDeliveryTime", ctypes.c_uint), ("jobStartingTime", ctypes.c_uint), ("jobFinishedTime", ctypes.c_uint)]


class _CommonI(ctypes.Structure):
    _fields_ = [("restStop", ctypes.c_int)]


class _TruckI(ctypes.Structure):
    _fields_ = [("gear", ctypes.c_int), ("gearDashboard", ctypes.c_int), ("hshifterResulting", ctypes.c_int * 32)]


class _GameplayI(ctypes.Structure):
    _fields_ = [("jobDeliveredEarnedXp", ctypes.c_int)]


class _CommonF(ctypes.Structure):
    _fields_ = [("scale", ctypes.c_float)]


class _ConfigF(ctypes.Structure):
    _fields_ = ([(n, ctypes.c_float) for n in ("fuelCapacity", "fuelWarningFactor", "adblueCapacity", "adblueWarningFactor",
                                              "airPressureWarning", "airPressurEmergency", "oilPressureWarning",
                                              "waterTemperatureWarning", "batteryVoltageWarning", "engineRpmMax",
                                              "gearDifferential", "cargoMass")]
                + [("truckWheelRadius", ctypes.c_float * 16), ("gearRatiosForward", ctypes.c_float * 24),
                   ("gearRatiosReverse", ctypes.c_float * 8), ("unitMass", ctypes.c_float)])


class _TruckF(ctypes.Structure):
    _fields_ = ([(n, ctypes.c_float) for n in (
        "speed", "engineRpm", "userSteer", "userThrottle", "userBrake", "userClutch", "gameSteer", "gameThrottle", "gameBrake",
        "gameClutch", "cruiseControlSpeed", "airPressure", "brakeTemperature", "fuel", "fuelAvgConsumption", "fuelRange", "adblue",
        "oilPressure", "oilTemperature", "waterTemperature", "batteryVoltage", "lightsDashboard", "wearEngine", "wearTransmission",
        "wearCabin", "wearChassis", "wearWheels", "truckOdometer", "routeDistance", "routeTime", "speedLimit")]
                + [(n, ctypes.c_float * 16) for n in ("truck_wheelSuspDeflection", "truck_wheelVelocity", "truck_wheelSteering",
                                                      "truck_wheelRotation", "truck_wheelLift", "truck_wheelLiftOffset")])


class _GameplayF(ctypes.Structure):
    _fields_ = [("jobDeliveredCargoDamage", ctypes.c_float), ("jobDeliveredDistanceKm", ctypes.c_float), ("refuelAmount", ctypes.c_float)]


class _JobF(ctypes.Structure):
    _fields_ = [("cargoDamage", ctypes.c_float)]


class _ConfigB(ctypes.Structure):
    _fields_ = [("truckWheelSteerable", ctypes.c_bool * 16), ("truckWheelSimulated", ctypes.c_bool * 16),
                ("truckWheelPowered", ctypes.c_bool * 16), ("truckWheelLiftable", ctypes.c_bool * 16),
                ("isCargoLoaded", ctypes.c_bool), ("specialJob", ctypes.c_bool)]


class _TruckB(ctypes.Structure):
    _fields_ = ([(n, ctypes.c_bool) for n in (
        "parkBrake", "motorBrake", "airPressureWarning", "airPressureEmergency", "fuelWarning", "adblueWarning", "oilPressureWarning",
        "waterTemperatureWarning", "batteryVoltageWarning", "electricEnabled", "engineEnabled", "wipers", "blinkerLeftActive",
        "blinkerRightActive", "blinkerLeftOn", "blinkerRightOn", "lightsParking", "lightsBeamLow", "lightsBeamHigh", "lightsBeacon",
        "lightsBrake", "lightsReverse", "lightsHazard", "cruiseControl")]
                + [("truck_wheelOnGround", ctypes.c_bool * 16), ("shifterToggle", ctypes.c_bool * 2), ("differentialLock", ctypes.c_bool),
                   ("liftAxle", ctypes.c_bool), ("liftAxleIndicator", ctypes.c_bool), ("trailerLiftAxle", ctypes.c_bool),
                   ("trailerLiftAxleIndicator", ctypes.c_bool)])


class _GameplayB(ctypes.Structure):
    _fields_ = [("jobDeliveredAutoparkUsed", ctypes.c_bool), ("jobDeliveredAutoloadUsed", ctypes.c_bool)]


class _ConfigFv(ctypes.Structure):
    _fields_ = ([(n, ctypes.c_float) for n in ("cabinPositionX", "cabinPositionY", "cabinPositionZ", "headPositionX", "headPositionY",
                                              "headPositionZ", "truckHookPositionX", "truckHookPositionY", "truckHookPositionZ")]
                + [("truckWheelPositionX", ctypes.c_float * 16), ("truckWheelPositionY", ctypes.c_float * 16),
                   ("truckWheelPositionZ", ctypes.c_float * 16)])


class _TruckFv(ctypes.Structure):
    _fields_ = [(n, ctypes.c_float) for n in (
        "lv_accelerationX", "lv_accelerationY", "lv_accelerationZ", "av_accelerationX", "av_accelerationY", "av_accelerationZ",
        "accelerationX", "accelerationY", "accelerationZ", "aa_accelerationX", "aa_accelerationY", "aa_accelerationZ",
        "cabinAVX", "cabinAVY", "cabinAVZ", "cabinAAX", "cabinAAY", "cabinAAZ")]


class _TruckFp(ctypes.Structure):
    _fields_ = [(n, ctypes.c_float) for n in (
        "cabinOffsetX", "cabinOffsetY", "cabinOffsetZ", "cabinOffsetrotationX", "cabinOffsetrotationY", "cabinOffsetrotationZ",
        "headOffsetX", "headOffsetY", "headOffsetZ", "headOffsetrotationX", "headOffsetrotationY", "headOffsetrotationZ")]


class _TruckDp(ctypes.Structure):
    _fields_ = [(n, ctypes.c_double) for n in ("coordinateX", "coordinateY", "coordinateZ", "rotationX", "rotationY", "rotationZ")]


class _ConfigS(ctypes.Structure):
    _fields_ = ([(n, ctypes.c_char * 64) for n in ("truckBrandId", "truckBrand", "truckId", "truckName", "cargoId", "cargo", "cityDstId",
                                                  "cityDst", "compDstId", "compDst", "citySrcId", "citySrc", "compSrcId", "compSrc")]
                + [("shifterType", ctypes.c_char * 16)]
                + [(n, ctypes.c_char * 64) for n in ("truckLicensePlate", "truckLicensePlateCountryId", "truckLicensePlateCountry")]
                + [("jobMarket", ctypes.c_char * 32)])


class _GameplayS(ctypes.Structure):
    _fields_ = ([("fineOffence", ctypes.c_char * 32)] + [(n, ctypes.c_char * 64) for n in (
        "ferrySourceName", "ferryTargetName", "ferrySourceId", "ferryTargetId", "trainSourceName", "trainTargetName",
        "trainSourceId", "trainTargetId")])


class _ConfigUll(ctypes.Structure):
    _fields_ = [("jobIncome", ctypes.c_ulonglong)]


class _GameplayLl(ctypes.Structure):
    _fields_ = [(n, ctypes.c_longlong) for n in ("jobCancelledPenalty", "jobDeliveredRevenue", "fineAmount", "tollgatePayAmount",
                                                  "ferryPayAmount", "trainPayAmount")]


class _SpecialB(ctypes.Structure):
    _fields_ = [(n, ctypes.c_bool) for n in ("onJob", "jobFinished", "jobCancelled", "jobDelivered", "fined", "tollgate", "ferry",
                                              "train", "refuel", "refuelPayed")]


class TelemetryMap(ctypes.Structure):
    """Первые 4400 байт карты (до подстановок и прицепов) — всё, что нужно оверлею."""
    _fields_ = [("sdkActive", ctypes.c_bool), ("placeHolder", ctypes.c_char * 3), ("paused", ctypes.c_bool), ("placeHolder2", ctypes.c_char * 3),
                ("time", ctypes.c_ulonglong), ("simulatedTime", ctypes.c_ulonglong), ("renderTime", ctypes.c_ulonglong),
                ("multiplayerTimeOffset", ctypes.c_longlong),
                ("scs_values", _ScsValues), ("common_ui", _CommonUi), ("config_ui", _ConfigUi), ("truck_ui", _TruckUi),
                ("gameplay_ui", _GameplayUi), ("buffer_ui", ctypes.c_char * 48),
                ("common_i", _CommonI), ("truck_i", _TruckI), ("gameplay_i", _GameplayI), ("buffer_i", ctypes.c_char * 56),
                ("common_f", _CommonF), ("config_f", _ConfigF), ("truck_f", _TruckF), ("gameplay_f", _GameplayF), ("job_f", _JobF),
                ("buffer_f", ctypes.c_char * 28),
                ("config_b", _ConfigB), ("truck_b", _TruckB), ("gameplay_b", _GameplayB), ("buffer_b", ctypes.c_char * 25),
                ("config_fv", _ConfigFv), ("truck_fv", _TruckFv), ("buffer_fv", ctypes.c_char * 60),
                ("truck_fp", _TruckFp), ("buffer_fp", ctypes.c_char * 152),
                ("truck_dp", _TruckDp), ("buffer_dp", ctypes.c_char * 52),
                ("config_s", _ConfigS), ("gameplay_s", _GameplayS), ("buffer_s", ctypes.c_char * 20),
                ("config_ull", _ConfigUll), ("buffer_ull", ctypes.c_char * 192),
                ("gameplay_ll", _GameplayLl), ("buffer_ll", ctypes.c_char * 52),
                ("special_b", _SpecialB), ("buffer_special", ctypes.c_char * 90)]


# смещения зон из заголовка плагина — проверяются тестом
ZONES = {"scs_values": 40, "common_i": 500, "common_f": 700, "config_b": 1500, "config_fv": 1640, "truck_fp": 2000,
         "truck_dp": 2200, "config_s": 2300, "config_ull": 4000, "gameplay_ll": 4200, "special_b": 4300}


def _txt(raw):
    return bytes(raw).split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def parse_map(buf):
    """bytes -> словарь значений в понятных единицах (скорость км/ч, расстояние км)."""
    m = TelemetryMap.from_buffer_copy(buf[:ctypes.sizeof(TelemetryMap)])
    f, b, dp = m.truck_f, m.truck_b, m.truck_dp
    head = dp.rotationX
    if abs(head) > 1.5:                                  # на всякий случай: если вдруг градусы
        head = head / 360.0
    gear = m.truck_i.gearDashboard
    return {
        "sdk": bool(m.sdkActive), "paused": bool(m.paused), "game": m.scs_values.game, "rev": m.scs_values.telemetry_plugin_revision,
        "game_minutes": m.common_ui.time_abs,
        "speed": f.speed * 3.6, "rpm": f.engineRpm, "rpm_max": m.config_f.engineRpmMax, "gear": gear,
        "cruise": f.cruiseControlSpeed * 3.6 if b.cruiseControl else 0.0, "limit": f.speedLimit * 3.6,
        "fuel": f.fuel, "fuel_cap": m.config_f.fuelCapacity, "fuel_range": f.fuelRange, "fuel_avg": f.fuelAvgConsumption * 100.0,
        "fuel_warn": bool(b.fuelWarning), "adblue": f.adblue, "adblue_cap": m.config_f.adblueCapacity,
        "wear": {"engine": f.wearEngine, "trans": f.wearTransmission, "cabin": f.wearCabin, "chassis": f.wearChassis, "wheels": f.wearWheels},
        "cargo_damage": m.job_f.cargoDamage,
        "route_km": f.routeDistance / 1000.0, "route_s": f.routeTime, "odo": f.truckOdometer,
        "park": bool(b.parkBrake), "engine_on": bool(b.engineEnabled), "beam_low": bool(b.lightsBeamLow), "beam_high": bool(b.lightsBeamHigh),
        "blink_l": bool(b.blinkerLeftOn), "blink_r": bool(b.blinkerRightOn), "hazard": bool(b.lightsHazard),
        "x": dp.coordinateX, "y": dp.coordinateY, "z": dp.coordinateZ, "heading": (head % 1.0) * 360.0,
        "cargo": _txt(m.config_s.cargo), "city_src": _txt(m.config_s.citySrc), "city_dst": _txt(m.config_s.cityDst), "city_dst_id": _txt(m.config_s.cityDstId),
        "comp_src": _txt(m.config_s.compSrc), "comp_dst": _txt(m.config_s.compDst), "brand": _txt(m.config_s.truckBrand),
        "truck": _txt(m.config_s.truckName), "on_job": bool(m.special_b.onJob), "income": m.config_ull.jobIncome,
        "cargo_mass": m.config_f.cargoMass, "delivery_minutes": m.config_ui.time_abs_delivery,
        "oil_temp": f.oilTemperature, "water_temp": f.waterTemperature, "oil_press": f.oilPressure, "air_press": f.airPressure,
        "battery": f.batteryVoltage, "brake_temp": f.brakeTemperature, "rest_min": m.common_i.restStop,
        "warn_oil": bool(b.oilPressureWarning), "warn_water": bool(b.waterTemperatureWarning), "warn_air": bool(b.airPressureWarning),
        "warn_batt": bool(b.batteryVoltageWarning), "warn_adblue": bool(b.adblueWarning), "gears": m.config_ui.gears,
        "plate": _txt(m.config_s.truckLicensePlate), "time": int(m.time),
    }


class Ets2Reader:
    """Читает карту плагина. На Windows область открывается ТОЛЬКО на чтение и только если она уже есть (создаёт её плагин игры).
    Нельзя создавать её самим: тогда плагин при старте игры не сможет получить запись и вернёт ошибку -7.
    Для проверок на другой системе — файл из YASSNO_ETS2_FILE."""

    def __init__(self):
        self.mm = None                                   # mmap файла (только для проверок)
        self.h = None                                    # дескриптор области (Windows)
        self.p = None                                    # адрес отображения (Windows)
        self.err = ""
        self._t = 0.0

    def open(self):
        path = os.environ.get("YASSNO_ETS2_FILE")
        if path:
            try:
                f = open(path, "rb")
                self.mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                return True
            except (OSError, ValueError):
                self.err = "нет файла проверки"
                return False
        if sys.platform != "win32":
            self.err = "чтение игры работает только в Windows"
            return False
        k = ctypes.windll.kernel32
        k.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.OpenFileMappingW.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
        k.MapViewOfFile.restype = ctypes.c_void_p
        FILE_MAP_READ = 0x0004
        h = k.OpenFileMappingW(FILE_MAP_READ, 0, MMF_NAME)      # не создаём: если плагин ещё не запущен — просто ждём
        if not h:
            self.err = "плагин телеметрии не запущен (игра закрыта или плагин не загрузился)"
            return False
        p = k.MapViewOfFile(h, FILE_MAP_READ, 0, 0, MMF_SIZE)
        if not p:
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            k.CloseHandle(h)
            self.err = "не удалось подключиться к данным плагина"
            return False
        self.h, self.p = h, p
        return True

    def read(self):
        """Словарь значений или None, если игра/плагин недоступны."""
        if self.mm is None and self.p is None:
            now = time.monotonic()
            if now - self._t < 1.0:
                return None
            self._t = now
            if not self.open():
                return None
        try:
            if self.mm is not None:
                self.mm.seek(0)
                raw = self.mm.read(ctypes.sizeof(TelemetryMap))
            else:
                raw = ctypes.string_at(self.p, ctypes.sizeof(TelemetryMap))
            d = parse_map(raw)
        except (OSError, ValueError):
            self.close()
            return None
        if not d["sdk"]:
            self.err = "игра запущена, но не отдаёт данные (загрузите профиль и выедьте на трассу)"
            return None
        self.err = ""
        return d

    def close(self):
        try:
            if self.mm is not None:
                self.mm.close()
            if self.p is not None:
                ctypes.windll.kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
                ctypes.windll.kernel32.UnmapViewOfFile(self.p)
            if self.h is not None:
                ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                ctypes.windll.kernel32.CloseHandle(self.h)
        except (OSError, ValueError, AttributeError):
            pass
        self.mm = self.h = self.p = None


class DemoReader:
    """Тестовая поездка: грузовик ездит по кольцу в районе Ганновера с изменением скорости."""
    CX, CZ, R = -1100.0, -8870.0, 2200.0

    def __init__(self):
        self.t0 = time.monotonic()
        self.err = ""

    def read(self):
        t = time.monotonic() - self.t0
        a = t * 0.05
        x, z = self.CX + self.R * math.cos(a), self.CZ + self.R * math.sin(a)
        dx, dz = -math.sin(a), math.cos(a)                          # направление движения по кольцу (по часовой стрелке на карте)
        heading = (math.degrees(math.atan2(-dx, -dz)) % 360.0)
        spd = 70 + 15 * math.sin(t * 0.4)
        gear = 9 if spd > 50 else 6
        return {"sdk": True, "paused": False, "game": 1, "rev": 12, "game_minutes": 1440 * 3 + 9 * 60 + int(t) % 600,
                "speed": spd, "rpm": 1150 + 350 * math.sin(t), "rpm_max": 2300, "gear": gear, "cruise": 80.0 if spd > 70 else 0.0,
                "limit": 80.0, "fuel": 420 - t * 0.2, "fuel_cap": 600, "fuel_range": 1100 - t, "fuel_avg": 31.5, "fuel_warn": False,
                "adblue": 58, "adblue_cap": 80,
                "wear": {"engine": 0.02, "trans": 0.01, "cabin": 0.05, "chassis": 0.12, "wheels": 0.31}, "cargo_damage": 0.04,
                "route_km": 312.4 - t * 0.02, "route_s": 12600 - t, "odo": 123456.0, "park": False, "engine_on": True,
                "beam_low": True, "beam_high": False, "blink_l": int(t) % 2 == 0, "blink_r": False, "hazard": False,
                "x": x, "y": 40.0, "z": z, "heading": heading,
                "cargo": "Бетонные блоки", "city_src": "Hannover", "city_dst": "Bremen", "comp_src": "Bau Land", "comp_dst": "Stokes",
                "brand": "Scania", "truck": "R", "on_job": True, "income": 5200, "cargo_mass": 18000.0, "delivery_minutes": 1440 * 3 + 16 * 60,
                "oil_temp": 92.0 + 6 * math.sin(t * 0.2), "water_temp": 88.0, "oil_press": 54.0, "air_press": 118.0, "battery": 27.8,
                "brake_temp": 140.0 + 60 * math.sin(t * 0.3), "rest_min": 540 - int(t), "warn_oil": False, "warn_water": False,
                "warn_air": False, "warn_batt": False, "warn_adblue": False, "gears": 12, "plate": "H-AN 2026"}


# ---------------------------------------------------------------------------
# конвой: игроки TruckersMP (плагин SCS другим игрокам не виден, берём из открытого API)
# ---------------------------------------------------------------------------
TRUCKY_URL = "https://api.truckyapp.com/v3/map/online?playerID={}"
TMP_URL = "https://api.truckersmp.com/v2/player/{}"


def parse_convoy_ids(text):
    """'123456, 789=Вася' -> [('123456', ''), ('789', 'Вася')] (до 12 штук, только цифры в ID)."""
    out, seen = [], set()
    for part in str(text).replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        pid, _, name = part.partition("=")
        pid = pid.strip()
        if pid.isdigit() and pid not in seen:
            seen.add(pid)
            out.append((pid, name.strip()))
    return out[:12]


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_online(js):
    """Разбор ответа Trucky: online, сервер, город и координаты (если API их отдаёт). Устойчив к разной форме ответа."""
    resp = js.get("response", js) if isinstance(js, dict) else {}
    if not isinstance(resp, dict):
        resp = {}
    loc = resp.get("location")
    x = y = None
    city = ""
    if isinstance(loc, dict):
        for src in (loc, loc.get("coordinates"), loc.get("position"), loc.get("pos")):
            if isinstance(src, dict):
                for kx, ky in (("x", "y"), ("X", "Y"), ("posX", "posY"), ("x", "z"), ("lng", "lat")):
                    a, b = _num(src.get(kx)), _num(src.get(ky))
                    if a is not None and b is not None:
                        x, y = a, b
                        break
            if x is not None:
                break
        poi = loc.get("poi")
        for v in (loc.get("realName"), loc.get("name"), poi.get("realName") if isinstance(poi, dict) else None,
                  poi.get("name") if isinstance(poi, dict) else poi, loc.get("city")):
            if isinstance(v, str) and v:
                city = v
                break
    elif isinstance(loc, str):
        city = loc
    srv = resp.get("server")
    if isinstance(srv, dict):
        srv = srv.get("name") or srv.get("shortname") or ""
    return {"online": bool(resp.get("online")), "x": x, "y": y, "city": city, "server": srv if isinstance(srv, str) else ""}


def rel_arrow(dx, dz, heading_deg):
    """Угол (рад, по часовой от «вперёд») на цель со смещением (dx, dz) в координатах игры (север = −z)."""
    th = math.radians(heading_deg)
    fx, fz = -math.sin(th), -math.cos(th)
    rx, rz = -fz, fx
    return math.atan2(dx * rx + dz * rz, dx * fx + dz * fz)


class ConvoyFeed:
    """Фоновый опрос участников конвоя раз в несколько секунд; результат — словарь id -> данные."""

    def __init__(self, ids_text="", period=5.0):
        self.members = parse_convoy_ids(ids_text)
        self.period = period
        self.data = {}
        self.names = {}
        self.err = ""
        self.alive = True
        self._lock = threading.Lock()
        if self.members:
            threading.Thread(target=self._run, daemon=True).start()

    @staticmethod
    def _get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "YassnoOverlay/ETS2"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _run(self):
        while self.alive:
            fail = 0
            for pid, own in self.members:
                if not self.alive:
                    return
                try:
                    info = parse_online(self._get(TRUCKY_URL.format(pid)))
                except Exception:                        # noqa: BLE001
                    fail += 1
                    continue
                if pid not in self.names:
                    nm = own
                    if not nm:
                        try:
                            nm = str(self._get(TMP_URL.format(pid)).get("response", {}).get("name") or "")
                        except Exception:                # noqa: BLE001
                            nm = ""
                    self.names[pid] = nm or f"ID {pid}"
                info["t"] = time.monotonic()
                with self._lock:
                    self.data[pid] = info
            self.err = "нет связи с TruckersMP/Trucky" if fail == len(self.members) else ""
            end = time.monotonic() + self.period
            while self.alive and time.monotonic() < end:
                time.sleep(0.2)

    def snapshot(self):
        with self._lock:
            return {pid: dict(v) for pid, v in self.data.items()}


class DemoConvoy:
    """Для демо-режима: четыре «водителя» вокруг игрока."""
    members = [("1", "Макс"), ("2", "Серый"), ("3", "Ольга"), ("4", "Кабан")]
    names = {"1": "Макс", "2": "Серый", "3": "Ольга", "4": "Кабан"}
    err = ""

    def __init__(self):
        self.alive = True
        self.t0 = time.monotonic()

    def snapshot(self):
        t = time.monotonic() - self.t0
        out = {}
        for i, (pid, _) in enumerate(self.members):
            a = t * 0.05 - (i + 1) * 0.05
            out[pid] = {"online": i != 3, "x": DemoReader.CX + DemoReader.R * math.cos(a), "y": DemoReader.CZ + DemoReader.R * math.sin(a),
                        "city": ("Hannover", "Hildesheim", "Celle", "")[i], "server": "Simulation 1", "t": time.monotonic()}
        return out


# ---------------------------------------------------------------------------
# карта: тайлы SCS_Map_Tiles
# ---------------------------------------------------------------------------
try:
    from PIL import Image, ImageChops, ImageDraw, ImageTk
    HAVE_PIL = True
except Exception:                                        # noqa: BLE001
    HAVE_PIL = False


_BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def bayer_mask(w, h, lvl):
    """Маска-«сито»: видна доля lvl% пикселей (упорядоченный дизеринг 4×4): карта просвечивает, как стеклянная."""
    on = int(round(lvl / 100.0 * 16))
    tile = Image.new("L", (4, 4))
    tile.putdata([255 if _BAYER[y][x] < on else 0 for y in range(4) for x in range(4)])
    row = tile
    while row.width < w:
        nr = Image.new("L", (row.width * 2, 4))
        nr.paste(row, (0, 0))
        nr.paste(row, (row.width, 0))
        row = nr
    out = row
    while out.height < h:
        no = Image.new("L", (out.width, out.height * 2))
        no.paste(out, (0, 0))
        no.paste(out, (0, out.height))
        out = no
    return out.crop((0, 0, w, h))


class TileMap:
    """Перевод игровых координат в пиксели тайлов и подгрузка тайлов (локально, затем с GitHub)."""

    def __init__(self, tile_dir=TILE_DIR, url=TILE_URL, allow_net=True):
        self.dir, self.url, self.allow_net = tile_dir, url, allow_net
        info = {"x1": -94621.8047, "x2": 79370.13, "y1": -80209.1641, "y2": 93782.77, "tileSize": 512, "maxZoom": 8}
        try:
            with open(os.path.join(tile_dir, "TileMapInfo.json"), encoding="utf-8") as f:
                m = json.load(f)["map"]
            info.update({k: m[k] for k in ("x1", "x2", "y1", "y2", "tileSize", "maxZoom") if k in m})
        except (OSError, ValueError, KeyError):
            pass
        self.x1, self.x2, self.y1, self.y2 = info["x1"], info["x2"], info["y1"], info["y2"]
        self.ts, self.maxz = int(info["tileSize"]), int(info["maxZoom"])
        self.width, self.height = self.x2 - self.x1, self.y2 - self.y1
        self.cache = collections.OrderedDict()           # (z,x,y) -> Image | False (нет тайла)
        self.pending = set()
        self.failed = {}
        self.q = queue.Queue()
        self.dirty = False                               # появился новый тайл — карту стоит перерисовать
        self.lock = threading.Lock()
        self.workers = []

    # --- координаты ---
    def ppu(self, z):
        """Пикселей уровня z на одну игровую единицу."""
        return self.ts * (2 ** z) / self.width

    def to_px(self, gx, gz, z):
        s = self.ts * (2 ** z)
        return (gx - self.x1) / self.width * s, (gz - self.y1) / self.height * s

    def level_for(self, px_per_unit):
        """Наименьший уровень, у которого тайлы не придётся растягивать."""
        for z in range(self.maxz + 1):
            if self.ppu(z) >= px_per_unit:
                return z
        return self.maxz

    # --- тайлы ---
    def _path(self, z, x, y):
        return os.path.join(self.dir, str(z), str(x), f"{y}.png")

    def _load_local(self, z, x, y):
        p = self._path(z, x, y)
        if os.path.isfile(p):
            try:
                im = Image.open(p)
                im.load()
                return im.convert("RGB")
            except Exception:                            # noqa: BLE001
                return None
        return None

    def _remember(self, key, val):
        self.cache[key] = val
        self.cache.move_to_end(key)
        while len(self.cache) > 400:
            self.cache.popitem(last=False)

    def tile(self, z, x, y):
        """Изображение тайла, если оно уже есть; иначе None (и ставит докачку)."""
        n = 2 ** z
        if not (0 <= x < n and 0 <= y < n):
            return None
        key = (z, x, y)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key] or None
        im = self._load_local(z, x, y)
        if im is not None:
            with self.lock:
                self._remember(key, im)
            return im
        if self.allow_net:
            self._want(key)
        return None

    def placeholder(self, z, x, y):
        """Размытый тайл из ближайшего родителя, пока настоящий не скачан."""
        for za in range(z - 1, -1, -1):
            k = 2 ** (z - za)
            par = self.tile_local_only(za, x // k, y // k)
            if par is not None:
                sub = self.ts // k
                if sub < 1:
                    continue
                ox, oy = (x % k) * sub, (y % k) * sub
                return par.crop((ox, oy, ox + sub, oy + sub)).resize((self.ts, self.ts), Image.BILINEAR)
        return None

    def tile_local_only(self, z, x, y):
        key = (z, x, y)
        with self.lock:
            if key in self.cache and self.cache[key]:
                return self.cache[key]
        im = self._load_local(z, x, y)
        if im is not None:
            with self.lock:
                self._remember(key, im)
        return im

    def _want(self, key):
        now = time.monotonic()
        with self.lock:
            if key in self.pending or now - self.failed.get(key, -999) < 90:
                return
            self.pending.add(key)
        self.q.put(key)
        if len(self.workers) < 3:
            t = threading.Thread(target=self._worker, daemon=True)
            self.workers.append(t)
            t.start()

    def _worker(self):
        while True:
            key = self.q.get()
            z, x, y = key
            ok = False
            try:
                req = urllib.request.Request(self.url.format(z=z, x=x, y=y), headers={"User-Agent": "yassno-ets2"})
                with urllib.request.urlopen(req, timeout=12) as r:
                    data = r.read()
                if data[:4] == b"\x89PNG":
                    p = self._path(z, x, y)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p + ".part", "wb") as f:
                        f.write(data)
                    os.replace(p + ".part", p)
                    ok = True
            except Exception:                            # noqa: BLE001
                ok = False
            with self.lock:
                self.pending.discard(key)
                if not ok:
                    self.failed[key] = time.monotonic()
                else:
                    self.cache.pop(key, None)
            if ok:
                self.dirty = True

    # --- отрисовка ---
    def render(self, gx, gz, span, w, h, heading=None, bg=(48, 48, 48)):
        """Картинка w×h с центром в (gx,gz); span — сколько игровых единиц помещается в ширину.
        heading=None — север вверху, иначе карта повёрнута так, что направление движения смотрит вверх."""
        s = w / float(span)                              # пикселей экрана на единицу
        d = int(math.ceil(math.hypot(w, h))) + 4 if heading is not None else None
        W, H = (d, d) if d else (w, h)
        z = self.level_for(s)
        f = s / self.ppu(z)                              # масштаб тайла уровня z на экран (<=1 — уменьшаем)
        ts_screen = self.ts * f
        img = Image.new("RGB", (W, H), bg)
        left, top = gx - W / 2.0 / s, gz - H / 2.0 / s
        cpx, cpy = self.to_px(left, top, z)
        tx0, ty0 = int(cpx // self.ts), int(cpy // self.ts)
        tx1, ty1 = int((cpx + W / f) // self.ts), int((cpy + H / f) // self.ts)
        n = 0
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                if n > 64:
                    break
                im = self.tile(z, tx, ty)
                if im is None and 0 <= tx < 2 ** z and 0 <= ty < 2 ** z:
                    im = self.placeholder(z, tx, ty)
                if im is None:
                    continue
                n += 1
                sx, sy = (tx * self.ts - cpx) * f, (ty * self.ts - cpy) * f
                size = max(1, int(round(ts_screen)))
                if size != im.size[0]:
                    im = im.resize((size, size), Image.BILINEAR if f < 1 else Image.BICUBIC)
                img.paste(im, (int(round(sx)), int(round(sy))))
        if heading is not None:
            img = img.rotate(-heading, resample=Image.BILINEAR, fillcolor=bg)
            ox, oy = (W - w) // 2, (H - h) // 2
            img = img.crop((ox, oy, ox + w, oy + h))
        return img


def fmt_hm(minutes):
    minutes = int(minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def fmt_dur(sec):
    sec = max(0, int(sec))
    h, m = sec // 3600, sec % 3600 // 60
    return f"{h}:{m:02d}" if h else f"{m} мин"


# ---------------------------------------------------------------------------
# окна
# ---------------------------------------------------------------------------
class ScaledCanvas(tk.Canvas):
    """Canvas, который сам умножает координаты, толщину линий и размер шрифтов на k: окна рисуются в «базовых» координатах."""
    k = 1.0
    _fc = None

    def _sc(self, args):
        k = self.k
        if k == 1.0:
            return args
        out = []
        for a in args:
            if isinstance(a, (int, float)):
                out.append(a * k)
            elif isinstance(a, (list, tuple)):
                out.append(type(a)(x * k for x in a) if all(isinstance(x, (int, float)) for x in a) else a)
            else:
                out.append(a)
        return out

    def _font(self, f):
        k = self.k
        if k == 1.0 or f is None:
            return f
        if self._fc is None:
            self._fc = {}
        if isinstance(f, tkfont.Font):
            fam, size, wt, sl = f.actual("family"), int(f.actual("size")), f.actual("weight"), f.actual("slant")
        elif isinstance(f, (tuple, list)) and len(f) >= 2:
            fam, size = f[0], int(f[1])
            rest = " ".join(str(x) for x in f[2:])
            wt, sl = ("bold" if "bold" in rest else "normal"), ("italic" if "italic" in rest else "roman")
        else:
            return f
        key = (fam, size, wt, sl, round(k, 3))
        if key not in self._fc:
            sz = int(round(abs(size) * k)) * (1 if size >= 0 else -1) or 1
            self._fc[key] = tkfont.Font(family=fam, size=sz, weight=wt, slant=sl)
        return self._fc[key]

    def _kw(self, kw):
        k = self.k
        if k != 1.0:
            for n in ("width",):
                if n in kw and isinstance(kw[n], (int, float)):
                    kw[n] = kw[n] * k
            if "font" in kw:
                kw["font"] = self._font(kw["font"])
        return kw

    def create_line(self, *a, **kw):
        return super().create_line(*self._sc(a), **self._kw(kw))

    def create_rectangle(self, *a, **kw):
        return super().create_rectangle(*self._sc(a), **self._kw(kw))

    def create_oval(self, *a, **kw):
        return super().create_oval(*self._sc(a), **self._kw(kw))

    def create_polygon(self, *a, **kw):
        return super().create_polygon(*self._sc(a), **self._kw(kw))

    def create_arc(self, *a, **kw):
        return super().create_arc(*self._sc(a), **self._kw(kw))

    def create_text(self, *a, **kw):
        return super().create_text(*self._sc(a), **self._kw(kw))


class Win:
    """Окно без рамки поверх всех: тянется мышью в режиме перемещения, в закреплённом — пропускает клики."""
    KEY, TITLE, W, H = "", "", 300, 150
    PERIOD = 0.05                                     # как часто перерисовывать (с); у карты — каждый тик

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        t = self.top
        t.title("Yassno ETS2 — " + self.TITLE)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=corner_bg())
        try:
            t.attributes("-alpha", app.cfg["alpha"])
        except tk.TclError:
            pass
        if sys.platform == "win32":
            try:
                t.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass
        pos = app.cfg["pos"].get(self.KEY)
        x, y = (pos if isinstance(pos, list) and len(pos) == 2 else self.default_pos())
        self.w, self.h = self.size()
        sc = app.cfg.get("scale", {}).get(self.KEY, 1.0)
        self.k = max(SCALE_MIN, min(SCALE_MAX, float(sc)))
        t.geometry(f"{int(self.w * self.k)}x{int(self.h * self.k)}+{int(x)}+{int(y)}")
        self.canvas = ScaledCanvas(t, width=int(self.w * self.k), height=int(self.h * self.k), bg=corner_bg(), highlightthickness=0, bd=0)
        self.canvas.k = self.k
        self.canvas.pack(fill="both", expand=True)
        self._drag = None
        self._rs = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Button-3>", self.menu)
        self.visible = True
        self.build()
        self.apply_lock()

    def size(self):
        return self.W, self.H

    def default_pos(self):
        return 40, 40

    def build(self):
        pass

    def _grip_hit(self, e):
        return (not self.app.cfg["locked"]) and grip_zone(self.canvas, e) is not None

    def panel(self, fill, outline=None, width=1, w=None, h=None):
        """Подложка окна: прямоугольник или скруглённая плашка (по стилю окон)."""
        c = self.canvas
        w, h = (w or self.w), (h or self.h)
        outline = BORDER if outline is None else outline
        lvl = self.level()
        if lvl <= 0 and self.app.cfg.get("locked", True):  # режим «без рамки»: подложки нет, остаётся только содержимое
            return None
        st = {"stipple": "gray12", "outlinestipple": "gray12"} if lvl <= 0 else {"stipple": STIPPLES[lvl], "outlinestipple": STIPPLES[lvl]} if lvl in STIPPLES else {}
        if not fill:
            st.pop("stipple", None)
        r = win_radius()
        if r > 1:
            return c.create_polygon(rr_points(0, 0, w - 1, h - 1, r), smooth=True, fill=fill, outline=outline, width=width, **st)
        return c.create_rectangle(0, 0, w - 1, h - 1, fill=fill, outline=outline, width=width, **st)

    def level(self):
        return frame_level(self.app.cfg, self.KEY)

    def set_scale(self, k, save=False):
        k = max(SCALE_MIN, min(SCALE_MAX, float(k)))
        if abs(k - self.k) > 1e-4:
            self.k = k
            self.canvas.k = k
            pw, ph = int(self.w * k), int(self.h * k)
            self.canvas.configure(width=pw, height=ph)
            self.top.geometry(f"{pw}x{ph}")
            self.on_scale()
        if save:
            self.app.cfg["scale"][self.KEY] = round(self.k, 3)
            save_cfg(self.app.cfg)

    def on_scale(self):
        self.last_draw = 0.0

    def draw_grip(self):
        """Режим перемещения: зелёная рамка со свечением, красный крестик закрытия и зелёные уголки. В закреплённом — ничего."""
        c = self.canvas
        if self.app.cfg["locked"] or not self.visible:
            if c.find_withtag("mv"):
                tk.Canvas.delete(c, "mv")
            return
        pw, ph = int(self.w * self.k), int(self.h * self.k)
        if c.find_withtag("mv") and getattr(c, "_mv_size", None) == (pw, ph):
            tk.Canvas.tag_raise(c, "mv")
            return
        tk.Canvas.delete(c, "mv")
        c._mv_size = (pw, ph)
        R = int(win_radius() * self.k)
        mk = lambda f, *a, **kw: f(c, *a, tags="mv", **kw)       # noqa: E731
        for i, col in enumerate(GLOW):                           # рамка с убывающим свечением внутрь
            o, r = 1 + i * 2, max(0, R - i * 2)
            x1, y1, x2, y2 = o, o, pw - 1 - o, ph - 1 - o
            if r > 1:
                mk(tk.Canvas.create_polygon, rr_points(x1, y1, x2, y2, r), smooth=True, fill="", outline=col, width=2)
            else:
                mk(tk.Canvas.create_rectangle, x1, y1, x2, y2, outline=col, width=2)
        bw = 22
        x0, y0 = pw - bw - 7, 7
        mk(tk.Canvas.create_rectangle, x0, y0, x0 + bw, y0 + bw, fill="#b3261e", outline="#ffffff", width=1)
        mk(tk.Canvas.create_line, x0 + 6, y0 + 6, x0 + bw - 6, y0 + bw - 6, fill="#ffffff", width=2)
        mk(tk.Canvas.create_line, x0 + bw - 6, y0 + 6, x0 + 6, y0 + bw - 6, fill="#ffffff", width=2)
        g = GRIP - 4                                             # уголки, за которые тоже растягивают
        for ax, ay, dx, dy in ((pw - 2, ph - 2, -1, -1), (2, ph - 2, 1, -1), (2, 2, 1, 1)):
            mk(tk.Canvas.create_polygon, ax, ay + dy * g, ax, ay, ax + dx * g, ay, fill="#2ee26d", outline="#0b3d1c", width=1)
        mk(tk.Canvas.create_polygon, pw - 2, ph - g, pw - 2, ph - 2, pw - g, ph - 2, fill="#2ee26d", outline="#0b3d1c", width=1)
        tk.Canvas.tag_raise(c, "mv")

    def _hover(self, e):
        z = None if (self.app.cfg["locked"] or self._rs or self._drag) else grip_zone(self.canvas, e)
        want = CURSORS.get(z, "") if z else ""
        if getattr(self.canvas, "_cur", None) != want:
            self.canvas._cur = want
            try:
                self.canvas.config(cursor=want)
            except tk.TclError:
                pass

    def _press(self, e):
        if self.app.cfg["locked"]:
            return
        pw = int(self.w * self.k)
        if e.x >= pw - 32 and e.y <= 32 and getattr(self, "CFG", ""):      # красный крестик: убрать это окно
            self.app.set_window(self.CFG, False)
            return
        z = grip_zone(self.canvas, e)
        if z:
            self._rs = z
            self._g0 = (e.x_root, e.y_root, self.top.winfo_x(), self.top.winfo_y(), pw, int(self.h * self.k), self.k)
            return
        self._drag = (e.x_root - self.top.winfo_x(), e.y_root - self.top.winfo_y())

    def _move(self, e):
        if self._rs:
            sx, sy = self._rs
            x0r, y0r, x0, y0, pw0, ph0, k0 = self._g0
            dx, dy = e.x_root - x0r, e.y_root - y0r
            f = []
            if sx:
                f.append((pw0 + sx * dx) / float(pw0))
            if sy:
                f.append((ph0 + sy * dy) / float(ph0))
            self.set_scale(k0 * max(0.05, sum(f) / len(f)))
            npw, nph = int(self.w * self.k), int(self.h * self.k)
            nx = x0 + (pw0 - npw) if sx < 0 else x0
            ny = y0 + (ph0 - nph) if sy < 0 else y0
            self.top.geometry(f"{npw}x{nph}+{nx}+{ny}")
            return
        if self._drag:
            self.top.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _release(self, e):
        if self._rs:
            self._rs = None
            self.set_scale(self.k, save=True)
            self.app.cfg["pos"][self.KEY] = [self.top.winfo_x(), self.top.winfo_y()]
            save_cfg(self.app.cfg)
            return
        if self._drag:
            self._drag = None
            self.app.cfg["pos"][self.KEY] = [self.top.winfo_x(), self.top.winfo_y()]
            save_cfg(self.app.cfg)

    def menu(self, e):
        m = tk.Menu(self.top, tearoff=0)
        m.add_command(label="Двигать окна" if self.app.cfg["locked"] else "Закрепить окна", command=self.app.toggle_lock)
        self.extend_menu(m)
        m.add_separator()
        sm = tk.Menu(m, tearoff=0)
        for pct in (50, 75, 100, 125, 150, 200, 250):
            sm.add_command(label=f"{pct}%" + (" ✓" if abs(self.k * 100 - pct) < 1 else ""), command=lambda p=pct: self.set_scale(p / 100.0, save=True))
        m.add_cascade(label="Размер окна (или тяните уголок)", menu=sm)
        fm = tk.Menu(m, tearoff=0)
        for lv, name in FRAME_LEVELS:
            fm.add_command(label=name + (" ✓" if self.level() == lv else ""), command=lambda v=lv: self.app.set_frame(self.KEY, v))
        m.add_cascade(label="Фон и рамка этого окна", menu=fm)
        m.add_command(label="Прятать, когда не в мире: " + ("вкл" if self.app.cfg["hide_idle"] else "выкл"), command=self.app.toggle_hide_idle)
        m.add_command(label="Закрыть оверлей", command=self.app.quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def extend_menu(self, m):
        pass

    def apply_lock(self):
        """Закреплённое окно не ловит мышь (WS_EX_TRANSPARENT), чтобы не мешать игре."""
        if sys.platform != "win32":
            return
        try:
            self.top.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.top.winfo_id()) or self.top.winfo_id()
            GWL_EXSTYLE, LAYERED, TRANSPARENT, TOOL, NOACTIVATE = -20, 0x80000, 0x20, 0x80, 0x08000000
            st = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | LAYERED | TOOL | NOACTIVATE
            st = (st | TRANSPARENT) if self.app.cfg["locked"] else (st & ~TRANSPARENT)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, st)
        except Exception:                                # noqa: BLE001
            pass

    def frame(self, title):
        c = self.canvas
        c.delete("all")
        self.panel(PANEL)
        r = win_radius()
        tx = 10 + (r // 3)
        if MARK:
            c.create_rectangle(tx, 8, tx + 3, 22, fill=MARK, outline="")
            tx += 10
        c.create_text(tx + 0, 15, text=title, fill=FG, anchor="w", font=("Segoe UI Semibold", 10))

    def waiting(self, text):
        self.frame(self.TITLE)
        self.canvas.create_text(self.w // 2, self.h // 2 + 6, text=text, fill=DIM, font=("Segoe UI", 9), justify="center", width=self.w - 30)

    def update(self, d):
        pass

    def destroy(self):
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class DashWin(Win):
    KEY, TITLE, W, H = "dash", "ПРИБОРЫ", 330, 168
    STYLES = (("classic", "Классика: цифры и полоса оборотов", (330, 168)),
              ("dial", "Циферблат: большой спидометр со стрелкой", (270, 270)),
              ("twin", "Два прибора: скорость и обороты", (470, 250)),
              ("digital", "Цифровой: широкая лента со светодиодами", (460, 112)),
              ("minimal", "Минимальный: скорость, передача, ограничение", (230, 78)))

    def size(self):
        return self.style_size(self.app.cfg.get("dash_style", "classic"))

    @classmethod
    def style_size(cls, name):
        for n, _, sz in cls.STYLES:
            if n == name:
                return sz
        return cls.STYLES[0][2]

    def build(self):
        self.big = tkfont.Font(family="Segoe UI Semibold", size=36)
        self._style = self.app.cfg.get("dash_style", "classic")

    def default_pos(self):
        return 40, 420

    def extend_menu(self, m):
        sm = tk.Menu(m, tearoff=0)
        for n, label, _ in self.STYLES:
            sm.add_command(label=label + (" ✓" if n == self._style else ""), command=lambda n=n: self.app.set_dash_style(n))
        m.add_cascade(label="Вид приборов", menu=sm)

    def sync_style(self):
        st = self.app.cfg.get("dash_style", "classic")
        if st != self._style:
            self._style = st
            self.w, self.h = self.style_size(st)
            pw, ph = int(self.w * self.k), int(self.h * self.k)
            self.canvas.configure(width=pw, height=ph)
            self.top.geometry(f"{pw}x{ph}")

    def update(self, d):
        self.sync_style()
        if d is None:
            return self.waiting(self.app.reason())
        getattr(self, "draw_" + self._style, self.draw_classic)(d)

    # ---- общие куски ----
    @staticmethod
    def gear_text(d):
        g = d["gear"]
        return "R" if g < 0 else "N" if g == 0 else str(g)

    def limit_sign(self, d, cx, cy, r, k, fs=15):
        lim = d["limit"] * k
        if lim > 1:
            over = abs(d["speed"]) > d["limit"] + 3
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#ffffff", outline="#ff0000" if over else RED, width=max(3, r // 5))
            self.canvas.create_text(cx, cy, text=f"{lim:.0f}", fill="#10141a", font=("Segoe UI Semibold", fs))

    @staticmethod
    def pol(cx, cy, r, f):
        a = math.radians(225.0 - 270.0 * max(0.0, min(1.0, f)))
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def dial(self, cx, cy, r, val, vmax, step, label_every, unit, col, scale=1.0, fmt=lambda v: f"{v:.0f}", red_from=None):
        """Круглый прибор: дуга, деления, подписи и стрелка (270° по часовой от нижнего левого угла)."""
        c = self.canvas
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=DIALBG, outline=BORDER, width=2)
        n = int(vmax / step)
        for i in range(n + 1):
            v = i * step
            f = v / float(vmax)
            major = i % label_every == 0
            x1, y1 = self.pol(cx, cy, r - 3, f)
            x2, y2 = self.pol(cx, cy, r - (13 if major else 7), f)
            hot = red_from is not None and v >= red_from
            c.create_line(x1, y1, x2, y2, fill=RED if hot else (FG if major else DIM), width=2 if major else 1)
            if major:
                tx, ty = self.pol(cx, cy, r - 25, f)
                c.create_text(tx, ty, text=fmt(v * scale), fill=DIM, font=("Segoe UI", 8))
        f = max(0.0, min(1.0, val / float(vmax)))
        nx, ny = self.pol(cx, cy, r - 10, f)
        c.create_line(cx, cy, nx, ny, fill=col, width=3)
        c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=col, outline=BG)
        c.create_text(cx, cy + r * 0.52, text=unit, fill=DIM, font=("Segoe UI", 8))

    def draw_dial(self, d):
        mph = self.app.cfg["mph"]
        k = 0.621371 if mph else 1.0
        c, W = self.canvas, self.w
        c.delete("all")
        self.panel(PANEL, w=W)
        sp = abs(d["speed"]) * k
        vmax = 180 if mph else 260
        self.dial(W / 2, 128, 112, sp, vmax, 10 if mph else 20, 2, "миль/ч" if mph else "км/ч", speed_col(d, ACCENT), red_from=None)
        c.create_text(W / 2, 96, text=f"{sp:.0f}", fill=speed_col(d, FG), font=("Segoe UI Semibold", 28))
        c.create_text(W / 2 - 40, 190, text=self.gear_text(d), fill=ACCENT, font=("Segoe UI Semibold", 22))
        rm = max(1000.0, d["rpm_max"] or 2500.0)
        c.create_text(W / 2 + 40, 190, text=f"{d['rpm'] / 1000.0:.1f}", fill=GREEN if d["rpm"] < rm * 0.8 else RED, font=("Segoe UI Semibold", 14))
        c.create_text(W / 2 + 40, 208, text="x1000", fill=DIM, font=("Segoe UI", 7))
        self.limit_sign(d, 34, 34, 17, k, 10)
        if d["cruise"] > 0:
            c.create_text(W - 14, 16, text=f"КРУИЗ {d['cruise'] * k:.0f}", fill=GREEN, anchor="e", font=("Segoe UI Semibold", 9))
        x = W - 14
        for txt, on, colr in (("P", d["park"], RED), ("▮▮", d["beam_high"], "#4aa3ff"), ("▮", d["beam_low"], GREEN)):
            if on:
                c.create_text(x, self.h - 14, text=txt, fill=colr, anchor="e", font=("Segoe UI Semibold", 11))
                x -= 28

    def draw_twin(self, d):
        mph = self.app.cfg["mph"]
        k = 0.621371 if mph else 1.0
        c, W = self.canvas, self.w
        c.delete("all")
        self.panel(PANEL, w=W)
        sp = abs(d["speed"]) * k
        vmax = 180 if mph else 260
        self.dial(122, 118, 104, sp, vmax, 10 if mph else 20, 2, "миль/ч" if mph else "км/ч", speed_col(d, ACCENT))
        c.create_text(122, 92, text=f"{sp:.0f}", fill=speed_col(d, FG), font=("Segoe UI Semibold", 24))
        rm = max(1000.0, d["rpm_max"] or 2500.0)
        top = 3000 if rm <= 3000 else int(math.ceil(rm / 500.0) * 500)
        self.dial(W - 100, 118, 84, d["rpm"], top, 250, 4, "об/мин ×100", GREEN if d["rpm"] < rm * 0.8 else RED, scale=0.1, red_from=rm * 0.9)
        c.create_text(W - 100, 92, text=self.gear_text(d), fill=ACCENT, font=("Segoe UI Semibold", 26))
        self.limit_sign(d, 252, 36, 20, k, 12)
        if d["cruise"] > 0:
            c.create_text(252, 66, text=f"КРУИЗ {d['cruise'] * k:.0f}", fill=GREEN, font=("Segoe UI Semibold", 9))
        # нижняя строка: топливо, температуры, давление воздуха
        y = 228
        fuel = d["fuel"] / d["fuel_cap"] if d["fuel_cap"] else 0.0
        items = (("ТОПЛ", f"{d['fuel']:.0f} л", GREEN if fuel > 0.15 else RED), ("МАСЛО", f"{d['oil_temp']:.0f}°", FG),
                 ("ВОДА", f"{d['water_temp']:.0f}°", RED if d["warn_water"] else FG), ("ВОЗД", f"{d['air_press']:.1f}", RED if d["warn_air"] else FG),
                 ("АКБ", f"{d['battery']:.1f} В", RED if d["warn_batt"] else FG))
        for i, (lab, val, colr) in enumerate(items):
            x = 12 + i * 90
            c.create_text(x, y - 7, text=lab, fill=DIM, anchor="w", font=("Segoe UI", 7))
            c.create_text(x, y + 6, text=val, fill=colr, anchor="w", font=("Segoe UI Semibold", 10))

    def draw_digital(self, d):
        mph = self.app.cfg["mph"]
        k = 0.621371 if mph else 1.0
        c, W = self.canvas, self.w
        c.delete("all")
        self.panel(PANEL, w=W)
        sp = abs(d["speed"]) * k
        c.create_text(14, 46, text=f"{sp:.0f}", fill=speed_col(d, FG), anchor="w", font=("Segoe UI Semibold", 34))
        c.create_text(14, 84, text="миль/ч" if mph else "км/ч", fill=DIM, anchor="w", font=("Segoe UI", 8))
        c.create_text(122, 46, text=self.gear_text(d), fill=ACCENT, anchor="w", font=("Segoe UI Semibold", 30))
        rm = max(1000.0, d["rpm_max"] or 2500.0)
        frac = max(0.0, min(1.0, d["rpm"] / rm))
        n, x0, seg, gap = 22, 166, 12, 3
        for i in range(n):
            on = (i + 0.5) / n <= frac
            colr = GREEN if i < n * 0.62 else YELLOW if i < n * 0.8 else RED
            c.create_rectangle(x0 + i * (seg + gap) * 0.62 * 1.0, 24, x0 + i * (seg + gap) * 0.62 + seg * 0.62, 24 + 26, fill=colr if on else TRACK, outline="")
        c.create_text(x0, 66, text=f"{d['rpm']:.0f} об/мин", fill=DIM, anchor="w", font=("Segoe UI", 9))
        if d["cruise"] > 0:
            c.create_text(x0 + 130, 66, text=f"КРУИЗ {d['cruise'] * k:.0f}", fill=GREEN, anchor="w", font=("Segoe UI Semibold", 9))
        self.limit_sign(d, W - 30, 34, 20, k, 12)
        x = W - 14
        for txt, on, colr in (("P", d["park"], RED), ("▮▮", d["beam_high"], "#4aa3ff"), ("▮", d["beam_low"], GREEN)):
            if on:
                c.create_text(x, 90, text=txt, fill=colr, anchor="e", font=("Segoe UI Semibold", 10))
                x -= 24
        blink = int(time.time() * 2.2) % 2 == 0
        if blink and (d["blink_l"] or d["hazard"]):
            c.create_polygon(8, 98, 18, 92, 18, 104, fill=GREEN, outline="")
        if blink and (d["blink_r"] or d["hazard"]):
            c.create_polygon(W - 8, 98, W - 18, 92, W - 18, 104, fill=GREEN, outline="")

    def draw_minimal(self, d):
        mph = self.app.cfg["mph"]
        k = 0.621371 if mph else 1.0
        c, W = self.canvas, self.w
        c.delete("all")
        self.panel(PANEL, w=W)
        sp = abs(d["speed"]) * k
        c.create_text(12, 40, text=f"{sp:.0f}", fill=speed_col(d, FG), anchor="w", font=("Segoe UI Semibold", 30))
        c.create_text(92, 52, text="миль/ч" if mph else "км/ч", fill=DIM, anchor="w", font=("Segoe UI", 8))
        c.create_text(150, 38, text=self.gear_text(d), fill=ACCENT, anchor="w", font=("Segoe UI Semibold", 26))
        self.limit_sign(d, W - 28, 30, 17, k, 10)
        rm = max(1000.0, d["rpm_max"] or 2500.0)
        frac = max(0.0, min(1.0, d["rpm"] / rm))
        c.create_rectangle(12, 66, W - 12, 71, fill=TRACK, outline="")
        c.create_rectangle(12, 66, 12 + (W - 24) * frac, 71, fill=GREEN if frac < 0.62 else YELLOW if frac < 0.8 else RED, outline="")

    def draw_classic(self, d):
        mph = self.app.cfg["mph"]
        k = 0.621371 if mph else 1.0
        self.frame(self.TITLE)
        c, W = self.canvas, self.w
        sp = abs(d["speed"]) * k
        fnt = self.big
        c.create_text(18, 78, text=f"{sp:.0f}", fill=speed_col(d, FG), anchor="w", font=fnt)
        c.create_text(18 + fnt.measure(f"{sp:.0f}") + 8, 92, text="миль/ч" if mph else "км/ч", fill=DIM, anchor="w", font=("Segoe UI", 10))
        g = d["gear"]
        gt = "R" if g < 0 else "N" if g == 0 else str(g)
        c.create_text(W - 20, 58, text=gt, fill=ACCENT, anchor="e", font=("Segoe UI Semibold", 34))
        c.create_text(W - 20, 88, text="передача", fill=DIM, anchor="e", font=("Segoe UI", 8))
        # знак ограничения
        lim = d["limit"] * k
        cx, cy, r = W - 92, 58, 24
        if lim > 1:
            over = abs(d["speed"]) > d["limit"] + 3 and d["limit"] > 1
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#ffffff", outline=RED if not over else "#ff0000", width=5)
            c.create_text(cx, cy, text=f"{lim:.0f}", fill="#10141a", font=("Segoe UI Semibold", 15))
        # круиз
        if d["cruise"] > 0:
            c.create_text(18, 112, text=f"КРУИЗ {d['cruise'] * k:.0f}", fill=GREEN, anchor="w", font=("Segoe UI Semibold", 10))
        # обороты
        rm = max(1000.0, d["rpm_max"] or 2500.0)
        frac = max(0.0, min(1.0, d["rpm"] / rm))
        y0, bw = 128, W - 36
        c.create_rectangle(18, y0, 18 + bw, y0 + 12, fill=TRACK, outline="")
        col = GREEN if frac < 0.62 else YELLOW if frac < 0.8 else RED
        c.create_rectangle(18, y0, 18 + bw * frac, y0 + 12, fill=col, outline="")
        c.create_text(18, y0 + 24, text=f"{d['rpm']:.0f} об/мин", fill=DIM, anchor="w", font=("Segoe UI", 9))
        # индикаторы
        x = W - 20
        for txt, on, colr in (("P", d["park"], RED), ("▮▮", d["beam_high"], "#4aa3ff"), ("▮", d["beam_low"], GREEN)):
            if on:
                c.create_text(x, y0 + 24, text=txt, fill=colr, anchor="e", font=("Segoe UI Semibold", 11))
                x -= 28
        blink = int(time.time() * 2.2) % 2 == 0
        if blink and (d["blink_l"] or d["hazard"]):
            c.create_polygon(112, 36, 128, 28, 128, 44, fill=GREEN, outline="")
        if blink and (d["blink_r"] or d["hazard"]):
            c.create_polygon(250, 36, 234, 28, 234, 44, fill=GREEN, outline="")


class TripWin(Win):
    PERIOD = 0.25
    KEY, TITLE, W, H = "trip", "РЕЙС И ТОПЛИВО", 330, 312

    def default_pos(self):
        return 390, 420

    def update(self, d):
        if d is None:
            return self.waiting(self.app.reason())
        self.frame(self.TITLE)
        c, W = self.canvas, self.w
        # топливо
        frac = d["fuel"] / d["fuel_cap"] if d["fuel_cap"] > 0 else 0.0
        col = GREEN if frac > 0.3 else YELLOW if frac > 0.15 else RED
        c.create_text(18, 44, text=f"{d['fuel']:.0f}", fill=FG, anchor="w", font=("Segoe UI Semibold", 22))
        c.create_text(18 + len(f"{d['fuel']:.0f}") * 17 + 10, 50, text="л", fill=DIM, anchor="w", font=("Segoe UI", 10))
        km = d["fuel_range"] * (0.621371 if self.app.cfg["mph"] else 1.0)
        c.create_text(W - 18, 44, text=f"хватит на {km:.0f} {'миль' if self.app.cfg['mph'] else 'км'}", fill=ACCENT, anchor="e", font=("Segoe UI Semibold", 11))
        c.create_rectangle(18, 62, W - 18, 72, fill=TRACK, outline="")
        c.create_rectangle(18, 62, 18 + (W - 36) * max(0.0, min(1.0, frac)), 72, fill=col, outline="")
        c.create_text(18, 86, text=f"расход {d['fuel_avg']:.1f} л/100 км", fill=DIM, anchor="w", font=("Segoe UI", 9))
        if d["adblue_cap"] > 0:
            c.create_text(W - 18, 86, text=f"AdBlue {d['adblue']:.0f}/{d['adblue_cap']:.0f} л", fill=DIM, anchor="e", font=("Segoe UI", 9))
        # навигация
        c.create_line(14, 100, W - 14, 100, fill=BORDER)
        c.create_text(18, 114, text="ДО ЦЕЛИ", fill=DIM, anchor="w", font=("Segoe UI", 8))
        rk = d["route_km"] * (0.621371 if self.app.cfg["mph"] else 1.0)
        c.create_text(18, 136, text=f"{rk:.0f} {'миль' if self.app.cfg['mph'] else 'км'}" if rk > 0 else "—", fill=FG, anchor="w", font=("Segoe UI Semibold", 15))
        c.create_text(W - 18, 136, text=fmt_dur(d["route_s"]) if d["route_s"] > 0 else "—", fill=FG, anchor="e", font=("Segoe UI Semibold", 15))
        c.create_text(W - 18, 114, text=f"время в игре {fmt_hm(d['game_minutes'])}", fill=DIM, anchor="e", font=("Segoe UI", 8))
        # груз
        c.create_line(14, 152, W - 14, 152, fill=BORDER)
        if d["on_job"] and (d["city_dst"] or d["cargo"]):
            c.create_text(18, 168, text=(d["cargo"] or "груз")[:28], fill=FG, anchor="w", font=("Segoe UI Semibold", 10))
            c.create_text(W - 18, 168, text=f"{d['cargo_mass'] / 1000.0:.1f} т", fill=DIM, anchor="e", font=("Segoe UI", 9))
            c.create_text(18, 186, text=f"{d['city_src'] or '?'} → {d['city_dst'] or '?'}", fill=ACCENT, anchor="w", font=("Segoe UI", 10))
            left = d["delivery_minutes"] - d["game_minutes"] if d["delivery_minutes"] else 0
            if left > 0:
                c.create_text(W - 18, 186, text=f"срок {fmt_dur(left * 60)}", fill=DIM, anchor="e", font=("Segoe UI", 9))
        else:
            c.create_text(18, 176, text="свободная поездка (задания нет)", fill=DIM, anchor="w", font=("Segoe UI", 9))
        # повреждения
        c.create_line(14, 204, W - 14, 204, fill=BORDER)
        c.create_text(18, 216, text="ПОВРЕЖДЕНИЯ", fill=DIM, anchor="w", font=("Segoe UI", 8))
        names = (("двигатель", "engine"), ("КПП", "trans"), ("кабина", "cabin"), ("рама", "chassis"), ("колёса", "wheels"))
        for i, (nm, key) in enumerate(names):
            v = max(0.0, min(1.0, d["wear"][key]))
            y = 236 + i * 14
            cc = GREEN if v < 0.15 else YELLOW if v < 0.4 else RED
            c.create_text(18, y, text=nm, fill=DIM, anchor="w", font=("Segoe UI", 8))
            c.create_rectangle(80, y - 4, W - 56, y + 4, fill=TRACK, outline="")
            c.create_rectangle(80, y - 4, 80 + (W - 136) * v, y + 4, fill=cc, outline="")
            c.create_text(W - 18, y, text=f"{v * 100:.0f}%", fill=FG, anchor="e", font=("Segoe UI", 8))


class MfdWin(Win):
    """Многофункциональный дисплей в духе Route Advisor: статус-бар, заголовок, экран и вкладки. Экраны листаются клавишами."""
    KEY, TITLE, W, H = "mfd", "МФД", 480, 372
    PERIOD = 0.1
    SCREENS = ("ОБЗОР", "ГРУЗ", "УРОН", "ТОПЛИВО", "ДВИГАТЕЛЬ", "РЕЙС")
    NUMPAD = (0x61, 0x62, 0x63, 0x64, 0x65, 0x66)               # Num1..Num6
    CTRLF = (0x74, 0x75, 0x76, 0x77, 0x78, 0x79)                # Ctrl+F5..F10
    ORANGE, DARK = "#ff9f1a", "#1a1200"
    DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    BODY_Y0, BODY_Y1, TAB_Y = 68, 312, 318

    def default_pos(self):
        return 40, 40

    def build(self):
        self.screen = max(0, min(len(self.SCREENS) - 1, int(self.app.cfg.get("mfd_screen", 0))))
        self.f_big = tkfont.Font(family="Segoe UI Semibold", size=26)
        self.f_mid = tkfont.Font(family="Segoe UI Semibold", size=14)
        self.f_n = tkfont.Font(family="Segoe UI", size=11)
        self.f_b = tkfont.Font(family="Segoe UI Semibold", size=11)
        self.f_s = tkfont.Font(family="Segoe UI", size=9)
        self._keys_down = [False] * 8

    def set_screen(self, i):
        i %= len(self.SCREENS)
        if i != self.screen:
            self.screen = i
            self.app.cfg["mfd_screen"] = i
            save_cfg(self.app.cfg)

    def key_names(self):
        mode = self.app.cfg.get("mfd_keys", "numpad")
        if mode == "custom":
            b = self.app.cfg.get("mfd_binds", {})
            return "свои (" + ", ".join(bind_name(b.get(n, "")) for n in ("next", "prev") if b.get(n)) + ")" if (b.get("next") or b.get("prev")) else "свои"
        return "Num1-6, Num+/-" if mode == "numpad" else "Ctrl+F5-F10"

    def poll_keys(self):
        """Глобальные клавиши (работают поверх игры). Вызывается из основного цикла."""
        if sys.platform != "win32":
            return
        u = ctypes.windll.user32
        down = lambda vk: bool(u.GetAsyncKeyState(vk) & 0x8000)
        try:
            if self.app.cfg.get("mfd_keys", "numpad") == "custom":
                binds = self.app.cfg.get("mfd_binds", {})
                ids = {b[1] for b in (parse_bind(binds.get(n, "")) for n in BIND_NAMES) if b and b[0] == "j"}
                joy = joy_state(ids) if ids else set()
                cur = [bind_down(binds.get(n, ""), joy) for n in BIND_NAMES]
            elif self.app.cfg.get("mfd_keys", "numpad") == "numpad":
                cur = [down(vk) for vk in self.NUMPAD] + [down(0x6B), down(0x6D)]       # Num+ и Num-
            else:
                ctrl = down(0x11)
                cur = [ctrl and down(vk) for vk in self.CTRLF] + [False, False]
        except Exception:                                # noqa: BLE001
            return
        for i, v in enumerate(cur):
            if v and not self._keys_down[i]:
                if i < 6:
                    self.set_screen(i)
                elif i == 6:
                    self.set_screen(self.screen + 1)
                else:
                    self.set_screen(self.screen - 1)
        self._keys_down = cur

    def _press(self, e):
        if not self._grip_hit(e) and e.y / self.k >= self.TAB_Y and not self.app.cfg["locked"]:
            self.set_screen(min(len(self.SCREENS) - 1, int(e.x / self.k // (self.w / len(self.SCREENS)))))
            return
        super()._press(e)

    def extend_menu(self, m):
        for i, n in enumerate(self.SCREENS):
            m.add_command(label=f"{i + 1}. {n}", command=lambda i=i: self.set_screen(i))

    # ---- рисование ----
    def kv(self, y, label, value, col=None, x0=22, x1=None, font=None):
        x1 = x1 or (self.w - 22)
        col = col or FG
        self.canvas.create_text(x0, y, text=label, fill=DIM, anchor="w", font=self.f_n)
        self.canvas.create_text(x1, y, text=value, fill=col, anchor="e", font=font or self.f_b)

    def bar(self, x0, y, x1, frac, col, h=9):
        c = self.canvas
        c.create_rectangle(x0, y - h // 2, x1, y + h // 2, fill=TRACK, outline="")
        c.create_rectangle(x0, y - h // 2, x0 + (x1 - x0) * max(0.0, min(1.0, frac)), y + h // 2, fill=col, outline="")

    def pill(self, x, y, text, col, on=True):
        c = self.canvas
        w = self.f_s.measure(text) + 14
        if on:
            c.create_rectangle(x, y - 9, x + w, y + 9, fill=col, outline="")
            c.create_text(x + w / 2, y, text=text, fill="#0b0f14", font=self.f_s)
        else:
            c.create_rectangle(x, y - 9, x + w, y + 9, outline=BORDER, fill="")
            c.create_text(x + w / 2, y, text=text, fill="#3b4656", font=self.f_s)
        return x + w + 6

    def chrome(self, d, title, right=""):
        c, W, H = self.canvas, self.w, self.h
        c.delete("all")
        self.panel(BG, width=2, w=W, h=H)
        # статус-бар
        k = 0.621371 if self.app.cfg["mph"] else 1.0
        sp = abs(d["speed"]) * k if d else 0
        c.create_text(14, 17, text=f"{sp:.0f} {'миль/ч' if self.app.cfg['mph'] else 'км/ч'}", fill=speed_col(d, FG) if d else FG, anchor="w", font=self.f_b)
        if d:
            g = d["gear"]
            c.create_text(118, 17, text="R" if g < 0 else "N" if g == 0 else f"D{g}", fill=ACCENT, anchor="w", font=self.f_b)
            x = 170
            x = self.pill(x, 17, "ТОПЛ", RED, d["fuel_warn"] or (d["fuel_cap"] > 0 and d["fuel"] / d["fuel_cap"] < 0.12))
            x = self.pill(x, 17, "ОТДЫХ", RED, 0 <= d["rest_min"] < 60)
            x = self.pill(x, 17, "КРУИЗ", GREEN, d["cruise"] > 0)
            x = self.pill(x, 17, "P", YELLOW, d["park"])
            gm = d["game_minutes"]
            c.create_text(W - 14, 17, text=f"{self.DAYS[(gm // 1440) % 7]} {fmt_hm(gm)}", fill=FG, anchor="e", font=self.f_b)
        if not self.app.cfg["locked"]:
            c.create_text(W // 2 + 90, 17, text="", fill=ACCENT)
        # заголовок экрана
        c.create_rectangle(10, 34, W - 10, 60, fill=self.ORANGE, outline="")
        c.create_text(20, 47, text=title, fill=self.DARK, anchor="w", font=self.f_b)
        if right:
            c.create_text(W - 20, 47, text=right, fill=self.DARK, anchor="e", font=self.f_b)
        # вкладки
        n = len(self.SCREENS)
        tw = (W - 20) / n
        c.create_line(10, self.TAB_Y - 4, W - 10, self.TAB_Y - 4, fill=BORDER)
        for i, name in enumerate(self.SCREENS):
            x0 = 10 + i * tw
            sel = i == self.screen
            if sel:
                c.create_rectangle(x0 + 2, self.TAB_Y, x0 + tw - 2, self.TAB_Y + 34, fill=TABSEL, outline=self.ORANGE)
            c.create_text(x0 + tw / 2, self.TAB_Y + 12, text=name, fill=self.ORANGE if sel else FG, font=self.f_s)
            mode = self.app.cfg.get("mfd_keys", "numpad")
            key = (bind_name(self.app.cfg.get("mfd_binds", {}).get(f"s{i}", "")) if mode == "custom"
                   else str(i + 1) if mode == "numpad" else f"F{5 + i}")
            c.create_text(x0 + tw / 2, self.TAB_Y + 26, text=key, fill=DIM, font=self.f_s)
        c.create_text(W // 2, H - 10, text=("клавиши: " + self.key_names() + ""),
                      fill=DIM, font=self.f_s)

    def update(self, d):
        self.poll_keys()
        name = self.SCREENS[self.screen]
        if d is None:
            self.chrome(None, name)
            self.canvas.create_text(self.w // 2, 180, text=self.app.reason(), fill=DIM, font=self.f_n, justify="center", width=self.w - 60)
            return
        k = 0.621371 if self.app.cfg["mph"] else 1.0
        unit = "миль" if self.app.cfg["mph"] else "км"
        right = ""
        if self.screen in (1, 5) and d["on_job"] and d["income"]:
            right = f"{d['income']:,}".replace(",", " ") + " €"
        elif self.screen == 3:
            right = f"{d['fuel']:.0f} л"
        elif self.screen == 0:
            right = (d["brand"] + " " + d["truck"]).strip()
        self.chrome(d, name, right)
        getattr(self, "scr_%d" % self.screen)(d, k, unit)

    # ---- экраны ----
    def scr_0(self, d, k, unit):
        c, W = self.canvas, self.w
        lim = d["limit"] * k
        c.create_text(22, 100, text=f"{abs(d['speed']) * k:.0f}", fill=speed_col(d, FG), anchor="w", font=self.f_big)
        c.create_text(22 + self.f_big.measure(f"{abs(d['speed']) * k:.0f}") + 8, 108, text="миль/ч" if self.app.cfg["mph"] else "км/ч", fill=DIM, anchor="w", font=self.f_n)
        if lim > 1:
            over = abs(d["speed"]) > d["limit"] + 3
            cx, cy, r = W - 60, 100, 26
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#ffffff", outline=RED if not over else "#ff0000", width=5)
            c.create_text(cx, cy, text=f"{lim:.0f}", fill="#10141a", font=self.f_mid)
        rm = max(1000.0, d["rpm_max"] or 2500.0)
        fr = max(0.0, min(1.0, d["rpm"] / rm))
        self.bar(22, 140, W - 22, fr, GREEN if fr < 0.62 else YELLOW if fr < 0.8 else RED, 10)
        rows = [("До цели", f"{d['route_km'] * k:.0f} {unit}" if d["route_km"] > 0 else "—", FG),
                ("Время в пути", fmt_dur(d["route_s"]) if d["route_s"] > 0 else "—", FG),
                ("Топливо", f"{d['fuel']:.0f} л  •  хватит на {d['fuel_range'] * k:.0f} {unit}", GREEN if d["fuel_range"] > d["route_km"] else YELLOW),
                ("Груз", (d["cargo"] or "—")[:26] if d["on_job"] else "нет задания", FG),
                ("Состояние", f"макс. повреждение {max(d['wear'].values()) * 100:.0f}%", GREEN if max(d['wear'].values()) < 0.15 else YELLOW if max(d['wear'].values()) < 0.4 else RED),
                ("Ограничение", f"{lim:.0f}" if lim > 1 else "—", FG)]
        for i, (a, b, col) in enumerate(rows):
            self.kv(168 + i * 24, a, b, col)

    def scr_1(self, d, k, unit):
        c, W = self.canvas, self.w
        if not d["on_job"] and not d["cargo"]:
            c.create_text(W // 2, 180, text="Задания нет — свободная поездка", fill=DIM, font=self.f_n)
            return
        c.create_text(22, 92, text=(d["cargo"] or "груз")[:30], fill=FG, anchor="w", font=self.f_mid)
        c.create_text(W - 22, 92, text=f"{d['cargo_mass'] / 1000.0:.1f} т", fill=ACCENT, anchor="e", font=self.f_mid)
        left = d["delivery_minutes"] - d["game_minutes"] if d["delivery_minutes"] else 0
        rows = [("Откуда", f"{d['city_src'] or '?'}  ({d['comp_src'] or '—'})", FG),
                ("Куда", f"{d['city_dst'] or '?'}  ({d['comp_dst'] or '—'})", FG),
                ("Стоимость доставки", (f"{d['income']:,}".replace(",", " ") + " €") if d["income"] else "—", GREEN),
                ("Расстояние до цели", f"{d['route_km'] * k:.0f} {unit}" if d["route_km"] > 0 else "—", FG),
                ("Срок доставки", fmt_dur(left * 60) if left > 0 else ("просрочено" if d["delivery_minutes"] else "—"),
                 RED if (d["delivery_minutes"] and left < 60) else FG),
                ("Повреждение груза", f"{d['cargo_damage'] * 100:.1f}%", GREEN if d["cargo_damage"] < 0.05 else YELLOW if d["cargo_damage"] < 0.2 else RED)]
        for i, (a, b, col) in enumerate(rows):
            self.kv(128 + i * 28, a, b, col)

    def scr_2(self, d, k, unit):
        names = (("Двигатель", d["wear"]["engine"]), ("Коробка передач", d["wear"]["trans"]), ("Кабина", d["wear"]["cabin"]),
                 ("Рама", d["wear"]["chassis"]), ("Колёса", d["wear"]["wheels"]), ("Груз", d["cargo_damage"]))
        for i, (n, v) in enumerate(names):
            y = 96 + i * 32
            col = GREEN if v < 0.15 else YELLOW if v < 0.4 else RED
            self.canvas.create_text(22, y, text=n, fill=FG, anchor="w", font=self.f_n)
            self.bar(170, y, self.w - 80, v, col, 12)
            self.canvas.create_text(self.w - 22, y, text=f"{v * 100:.0f}%", fill=col, anchor="e", font=self.f_b)
        avg = sum(d["wear"].values()) / 5.0
        self.kv(self.BODY_Y1 - 14, "Общее состояние грузовика", f"{(1 - avg) * 100:.0f}% исправен", GREEN if avg < 0.15 else YELLOW if avg < 0.4 else RED)

    def scr_3(self, d, k, unit):
        c, W = self.canvas, self.w
        fr = d["fuel"] / d["fuel_cap"] if d["fuel_cap"] > 0 else 0.0
        col = GREEN if fr > 0.3 else YELLOW if fr > 0.15 else RED
        c.create_text(22, 94, text=f"{d['fuel']:.0f} л", fill=FG, anchor="w", font=self.f_big)
        c.create_text(W - 22, 94, text=f"из {d['fuel_cap']:.0f} л", fill=DIM, anchor="e", font=self.f_n)
        self.bar(22, 124, W - 22, fr, col, 12)
        need = d["route_km"] * d["fuel_avg"] / 100.0 if d["route_km"] > 0 else 0.0
        margin = d["fuel"] - need
        rows = [("Запас хода", f"{d['fuel_range'] * k:.0f} {unit}", FG),
                ("Средний расход", f"{d['fuel_avg']:.1f} л/100 км", FG),
                ("До цели нужно", f"{need:.0f} л" if need > 0 else "—", FG),
                ("До цели", ("хватит, запас %.0f л" % margin) if need > 0 and margin >= 0 else ("не хватит, заправьтесь: ещё %.0f л" % -margin) if need > 0 else "—",
                 GREEN if margin >= 0 else RED),
                ("AdBlue", f"{d['adblue']:.0f} / {d['adblue_cap']:.0f} л" if d["adblue_cap"] > 0 else "—", RED if d["warn_adblue"] else FG)]
        for i, (a, b, colr) in enumerate(rows):
            self.kv(160 + i * 28, a, b, colr)
        if d["adblue_cap"] > 0:
            self.bar(22, 160 + 5 * 28 + 4, W - 22, d["adblue"] / d["adblue_cap"], "#4aa3ff", 8)

    def scr_4(self, d, k, unit):
        W = self.w
        fr = d["rpm"] / max(1000.0, d["rpm_max"] or 2500.0)
        rows = [("Обороты", f"{d['rpm']:.0f} / {d['rpm_max']:.0f}", fr, GREEN if fr < 0.62 else YELLOW if fr < 0.8 else RED),
                ("Температура воды", f"{d['water_temp']:.0f} °C", d["water_temp"] / 120.0, RED if d["warn_water"] or d["water_temp"] > 105 else GREEN),
                ("Температура масла", f"{d['oil_temp']:.0f} °C", d["oil_temp"] / 150.0, YELLOW if d["oil_temp"] > 120 else GREEN),
                ("Давление масла", f"{d['oil_press']:.0f} psi", d["oil_press"] / 100.0, RED if d["warn_oil"] else GREEN),
                ("Давление воздуха", f"{d['air_press']:.0f} psi", d["air_press"] / 150.0, RED if d["warn_air"] else GREEN),
                ("Тормоза", f"{d['brake_temp']:.0f} °C", d["brake_temp"] / 400.0, RED if d["brake_temp"] > 300 else YELLOW if d["brake_temp"] > 200 else GREEN),
                ("Аккумулятор", f"{d['battery']:.1f} В", d["battery"] / 30.0, RED if d["warn_batt"] else GREEN)]
        for i, (a, b, f, col) in enumerate(rows):
            y = 90 + i * 31
            self.canvas.create_text(22, y, text=a, fill=FG, anchor="w", font=self.f_n)
            self.bar(190, y, W - 110, f, col, 10)
            self.canvas.create_text(W - 22, y, text=b, fill=col, anchor="e", font=self.f_b)

    def scr_5(self, d, k, unit):
        gm = d["game_minutes"]
        eta = gm + d["route_s"] / 60.0 if d["route_s"] > 0 else None
        left = d["delivery_minutes"] - gm if d["delivery_minutes"] else 0
        rest = d["rest_min"]
        rows = [("Время в игре", f"{self.DAYS[(gm // 1440) % 7]} {fmt_hm(gm)}", FG),
                ("До цели", f"{d['route_km'] * k:.0f} {unit}  •  {fmt_dur(d['route_s'])}" if d["route_km"] > 0 else "—", FG),
                ("Прибытие", f"{self.DAYS[(int(eta) // 1440) % 7]} {fmt_hm(eta)}" if eta else "—", FG),
                ("Срок доставки", fmt_dur(left * 60) if left > 0 else "—", RED if (d["delivery_minutes"] and left * 1.0 < d["route_s"] / 60.0) else FG),
                ("До отдыха", fmt_dur(rest * 60) if 0 <= rest < 100000 else "—", RED if 0 <= rest < 60 else YELLOW if 0 <= rest < 180 else FG),
                ("Пробег грузовика", f"{d['odo'] * k:,.0f} {unit}".replace(",", " "), FG),
                ("Номер", d["plate"] or "—", FG)]
        for i, (a, b, col) in enumerate(rows):
            self.kv(96 + i * 30, a, b, col)


class ConvoyWin(Win):
    """Список участников конвоя: кто в сети, где (город) и как далеко от игрока, со стрелкой направления."""
    KEY, TITLE, W, H = "convoy", "КОНВОЙ", 300, 262
    PERIOD = 0.2
    ROWS = 8

    def default_pos(self):
        return 740, 420

    def update(self, d):
        self.frame(self.TITLE)
        c, W = self.canvas, self.w
        feed = self.app.convoy
        if not feed.members:
            c.create_text(W // 2, self.h // 2 + 6, text="Конвой пуст.\nДобавьте TruckersMP ID друзей\nна странице ETS2 в лаунчере", fill=DIM, font=("Segoe UI", 9), justify="center")
            return
        snap = feed.snapshot()
        rows = []
        for pid, own in feed.members:
            it = snap.get(pid)
            nm = feed.names.get(pid) or own or f"ID {pid}"
            dist = None
            if it and it["online"] and it["x"] is not None and d is not None:
                dx, dz = it["x"] - d["x"], it["y"] - d["z"]
                dist = math.hypot(dx, dz)
                ang = rel_arrow(dx, dz, d["heading"])
            else:
                ang = None
            rows.append((0 if (it and it["online"]) else 1, dist if dist is not None else 1e12, nm, it, dist, ang))
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        on = sum(1 for r in rows if r[0] == 0)
        c.create_text(W - 10, self.h - 10, text=f"{on}/{len(rows)} в сети", fill=DIM, anchor="e", font=("Segoe UI", 8))
        y = 40
        for _, _, nm, it, dist, ang in rows[:self.ROWS]:
            online = bool(it and it["online"])
            c.create_oval(12, y - 4, 20, y + 4, fill=GREEN if online else "#3a4350", outline="")
            c.create_text(28, y - 4, text=nm[:18], fill=FG if online else DIM, anchor="w", font=("Segoe UI Semibold", 10))
            sub = (it["city"] if it and it["city"] else ("в сети" if online else "не в сети"))
            c.create_text(28, y + 9, text=sub[:24], fill=DIM, anchor="w", font=("Segoe UI", 7))
            if dist is not None:
                txt = f"{dist / 1000.0:.1f} км" if dist >= 1000 else f"{dist:.0f} м"
                c.create_text(W - 38, y, text=txt, fill=ACCENT if dist > 400 else GREEN, anchor="e", font=("Segoe UI Semibold", 10))
                ax, ay, r = W - 20, y, 8
                pts = [(0, -r), (r * 0.7, r * 0.7), (0, r * 0.25), (-r * 0.7, r * 0.7)]
                sa, ca = math.sin(ang), math.cos(ang)
                flat = []
                for px, py in pts:
                    flat += [ax + px * ca - py * sa, ay + px * sa + py * ca]
                c.create_polygon(*flat, fill=ACCENT, outline="")
            elif online:
                c.create_text(W - 14, y, text="—", fill=DIM, anchor="e", font=("Segoe UI", 10))
            y += 26
        if feed.err:
            c.create_text(10, self.h - 10, text=feed.err, fill=YELLOW, anchor="w", font=("Segoe UI", 7))


def draw_route(dr, pts, gx, gz, s, cx, cy, a, kk):
    """Линия маршрута на кадре карты. pts — мировые координаты; s — пикселей на единицу; a — поворот карты (рад)."""
    ca, sa = math.cos(a), math.sin(a)
    lim = max(cx, cy) * 1.6
    line = []
    for wx, wz in pts:
        dx, dz = (wx - gx) * s, (wz - gz) * s
        x, y = cx + dx * ca - dz * sa, cy + dx * sa + dz * ca
        if abs(x - cx) > lim * 4 or abs(y - cy) > lim * 4:
            if len(line) > 1:
                break                                    # дальше маршрут ушёл за кадр
            line = []
            continue
        line.append((x, y))
    if len(line) > 1:
        dr.line(line, fill=(14, 22, 34), width=max(3, int(round(7 * kk))), joint="curve")
        dr.line(line, fill=(43, 217, 255), width=max(2, int(round(4 * kk))), joint="curve")
        ex, ey = line[-1]
        if abs(ex - cx) < lim and abs(ey - cy) < lim:
            r = 6 * kk
            dr.ellipse((ex - r, ey - r, ex + r, ey + r), fill=(43, 217, 255), outline=(14, 22, 34), width=2)


class RouteMgr:
    """Оптимальный маршрут по дорогам до города назначения. Граф и A* — в yassno_route.py, считаем в фоновом потоке."""

    OFF = 140.0                                          # дальше стольких игровых единиц от линии — считаем, что съехали

    def __init__(self, app):
        self.app = app
        self.pts = []                                    # [(gx, gz)]
        self.i = 0
        self.key = None
        self.busy = False
        self.graph = None
        self.err = ""
        self.off_since = 0.0
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.pts, self.i, self.key = [], 0, None

    def _graph(self):
        if self.graph is None:
            sys.path.insert(0, HERE) if HERE not in sys.path else None
            import yassno_route
            self.graph = yassno_route.RoadGraph(HERE)
        return self.graph

    def _job(self, key, name_a, name_b, gx, gz):
        was = gc.isenabled()
        gc.disable()                                     # сборка мусора в чужом потоке может добить Tk-объекты не в том потоке
        try:
            g = self._graph()
            import yassno_route as R
            dst = None
            for nm in (name_a, name_b):
                dst = g.city_px(nm) if nm else None
                if dst:
                    break
            r = g.route_px(R.world_to_px(gx, gz), dst) if dst else None
            pts = [(R.X1 + px / R.S * R.XW, R.Y1 + py / R.S * R.YH) for px, py in r["pts"]] if r else []
            self.err = "" if r else ("нет города на карте" if not dst else "путь не найден")
        except Exception as e:                           # noqa: BLE001
            pts, self.err = [], str(e)
        finally:
            if was:
                gc.enable()
        with self.lock:
            if self.key == key:
                self.pts, self.i = pts, 0
            self.busy = False

    def want(self, d):
        if not (self.app.cfg.get("show_route", True) and d and d.get("on_job")):
            return None
        a, b = d.get("city_dst_id", ""), d.get("city_dst", "")
        return (a, b) if (a or b) else None

    def update(self, d):
        """Вызывается из окна карты каждый кадр; возвращает точки маршрута от текущей позиции (или [])."""
        w = self.want(d)
        if w is None:
            if self.key is not None:
                self.reset()
            return []
        with self.lock:
            pts, i = self.pts, self.i
        now = time.monotonic()
        if pts:                                          # где мы на линии: ищем ближайший отрезок вперёд от прошлого индекса
            gx, gz = d["x"], d["z"]
            best, bi = 1e18, i
            for k in range(max(0, i - 3), min(len(pts) - 1, i + 60)):
                (ax, az), (bx, bz) = pts[k], pts[k + 1]
                vx, vz = bx - ax, bz - az
                L = vx * vx + vz * vz
                t = 0.0 if L <= 0 else max(0.0, min(1.0, ((gx - ax) * vx + (gz - az) * vz) / L))
                dd = (gx - ax - t * vx) ** 2 + (gz - az - t * vz) ** 2
                if dd < best:
                    best, bi = dd, k
            if best > self.OFF ** 2:
                if not self.off_since:
                    self.off_since = now
            else:
                self.off_since = 0.0
                with self.lock:
                    self.i = bi
        recalc = (self.key != w) or (pts and self.off_since and now - self.off_since > 2.5)
        if recalc and not self.busy:
            with self.lock:
                self.key = w
                self.busy = True
                if self.off_since:
                    self.off_since = 0.0
            threading.Thread(target=self._job, args=(w, w[0], w[1], d["x"], d["z"]), daemon=True).start()
        with self.lock:
            return self.pts[self.i:] if self.key == w else []


class MapWin(Win):
    KEY, TITLE = "map", "КАРТА"
    PERIOD = 0.0
    SPANS = (250.0, 500.0, 800.0, 1200.0, 1600.0, 2500.0, 4000.0, 7000.0, 12000.0, 25000.0, 60000.0, 150000.0)

    def size(self):
        s = int(self.app.cfg["map_size"])
        return s, s

    def default_pos(self):
        return 740, 40

    def build(self):
        self.photo = None
        self.item = None
        self.canvas.bind("<MouseWheel>", lambda e: self.zoom(-1 if e.delta > 0 else 1))
        self.canvas.bind("<Button-4>", lambda e: self.zoom(-1))
        self.canvas.bind("<Button-5>", lambda e: self.zoom(1))
        self.last_draw = 0.0
        self.smooth_span = float(self.app.cfg["map_span"])
        self.alive, self._req, self._res, self._drawn, self._done_req, self._shown = True, None, None, None, None, ""
        self._ev = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    def extend_menu(self, m):
        m.add_command(label="Приблизить", command=lambda: self.zoom(-1))
        m.add_command(label="Отдалить", command=lambda: self.zoom(1))
        m.add_command(label=("Север вверху" if self.app.cfg["map_heading_up"] else "Направление вверху"), command=self.toggle_mode)
        m.add_command(label="Маршрут на карте: " + ("выкл" if self.app.cfg.get("show_route", True) else "вкл"), command=self.toggle_route)
        m.add_command(label="Авто-масштаб по скорости: " + ("выкл" if self.app.cfg["map_auto_zoom"] else "вкл"), command=self.toggle_auto)

    def toggle_mode(self):
        self.app.cfg["map_heading_up"] = not self.app.cfg["map_heading_up"]
        save_cfg(self.app.cfg)

    def toggle_route(self):
        self.app.cfg["show_route"] = not self.app.cfg.get("show_route", True)
        save_cfg(self.app.cfg)

    def toggle_auto(self):
        self.app.cfg["map_auto_zoom"] = not self.app.cfg["map_auto_zoom"]
        save_cfg(self.app.cfg)

    def zoom(self, direction):
        cur = float(self.app.cfg["map_span"])
        spans = self.SPANS
        i = min(range(len(spans)), key=lambda k: abs(math.log(spans[k] / cur)))
        i = max(0, min(len(spans) - 1, i + direction))
        self.app.cfg["map_span"] = spans[i]
        save_cfg(self.app.cfg)

    def target_span(self, d):
        base = float(self.app.cfg["map_span"])
        if self.app.cfg["map_auto_zoom"] and d:
            return base * (1.0 + min(1.0, abs(d["speed"]) / 90.0) * 0.9)      # на скорости смотрим дальше
        return base

    def _worker(self):
        """Картинку карты рисуем в отдельном потоке, чтобы окно не подвисало: главный поток берёт последний готовый кадр."""
        while self.alive:
            self._ev.wait(0.5)
            self._ev.clear()
            req = self._req
            tm = self.app.tiles
            if req is None or (req == self._done_req and not tm.dirty):
                continue
            tm.dirty = False
            time.sleep(0.008)
            try:
                img = self.app.tiles.render(*req[:5], heading=req[5])
            except Exception:                            # noqa: BLE001
                continue
            self._done_req = req
            self._res = (req, img)

    def destroy(self):
        self.alive = False
        super().destroy()

    def update(self, d):
        if not HAVE_PIL:
            self.frame(self.TITLE)
            self.canvas.create_text(self.w // 2, self.h // 2, text="Для карты нужен Pillow:\npip install pillow", fill=YELLOW, font=("Segoe UI", 11), justify="center")
            return
        tm = self.app.tiles
        w, h = self.w, self.h
        kk = self.k
        pw, ph = int(w * kk), int(h * kk)
        if d is None:
            self._req = None
            self._res = None
            if self._shown != "wait":
                self._shown = "wait"
                self.canvas.delete("all")
                self.panel(PANEL)
                self.canvas.create_text(w // 2, h // 2, text=self.app.reason(), fill=DIM, font=("Segoe UI", 10), justify="center", width=w - 40)
            return
        self._shown = "map"
        tgt = self.target_span(d)
        self.smooth_span += (tgt - self.smooth_span) * 0.15
        span = self.smooth_span
        up = bool(self.app.cfg["map_heading_up"])
        rpts = self.app.route.update(d) if self.app.cfg.get("show_route", True) else []
        self._req = (d["x"], d["z"], span, pw, ph, d["heading"] if up else None, up, d["heading"])
        self._ev.set()
        res = self._res
        if res is None or res is self._drawn:
            return
        self._drawn = res
        req, img = res
        _, _, rspan, rpw, rph, _, rup, rhead = req
        if rpw != pw or rph != ph:                       # размер поменяли, пока кадр рисовался
            return
        dr = ImageDraw.Draw(img)
        cx, cy = pw // 2, ph // 2
        if rpts:
            draw_route(dr, rpts, d["x"], d["z"], rpw / float(rspan), cx, cy, math.radians(rhead) if rup else 0.0, kk)
        ang = 0.0 if rup else math.radians(rhead)                              # направление стрелки на экране (CCW от «вверх»)
        pts = []
        for px, py in ((0, -13), (9, 10), (0, 5), (-9, 10)):          # поворот на ang против часовой (экранные оси, y вниз)
            pts.append((cx + (px * math.cos(ang) + py * math.sin(ang)) * kk, cy + (-px * math.sin(ang) + py * math.cos(ang)) * kk))
        dr.polygon(pts, fill=(255, 159, 26), outline=(16, 20, 26))
        lvl = self.level()
        rpx = int(win_radius() * kk) if lvl > 0 else 0      # без рамки — прямая карта без скруглений
        if rpx > 1 or 0 < lvl < 100:                     # скругления и/или прозрачность: вне маски — ключевой цвет окна
            key = (pw, ph, rpx, lvl)
            mk = getattr(self, "_mask", None)
            if mk is None or mk[0] != key:
                m = Image.new("L", (pw, ph), 255)
                if rpx > 1:
                    m = Image.new("L", (pw * 3, ph * 3), 0)
                    ImageDraw.Draw(m).rounded_rectangle((0, 0, pw * 3 - 1, ph * 3 - 1), radius=rpx * 3, fill=255)
                    m = m.resize((pw, ph), Image.LANCZOS)
                if 0 < lvl < 100:
                    m = ImageChops.multiply(m, bayer_mask(pw, ph, lvl))
                self._mask = mk = (key, m)
            bgc = Image.new("RGB", (pw, ph), corner_bg())
            bgc.paste(img, (0, 0), mk[1])
            img = bgc
        self.photo = ImageTk.PhotoImage(img)
        c = self.canvas
        c.delete("all")
        c.create_image(0, 0, image=self.photo, anchor="nw")
        self.panel("", BORDER)
        if not self.app.cfg["locked"]:
            rr = win_radius()
            c.create_rectangle(rr, 1, w - rr, 20, fill=BG, outline="")
            c.create_text(rr + 4, 10, text=f"КАРТА • колесо — масштаб • ПКМ — меню", fill=ACCENT, anchor="w", font=("Segoe UI", 8))
        c.create_text(w - 8, h - 10, text=f"{rspan:.0f} м", fill=FG, anchor="e", font=("Segoe UI", 8))
        if rup:                                                                 # где на повёрнутой карте север
            a = math.radians(rhead)
            rr = min(w, h) / 2.0 - 14
            c.create_text(w / 2.0 + rr * math.sin(a), h / 2.0 - rr * math.cos(a), text="N", fill=ACCENT, font=("Segoe UI Semibold", 10))
        if tm.pending:
            c.create_text(8, h - 10, text="подгрузка карты…", fill=DIM, anchor="w", font=("Segoe UI", 8))


# ---------------------------------------------------------------------------
# предпросмотр в лаунчере: те же окна, нарисованные на обычном холсте по демо-данным
# ---------------------------------------------------------------------------
class _PreviewApp:
    def __init__(self, cfg):
        self.cfg = cfg
        self.convoy = DemoConvoy()
        self.tiles = None
        self.demo = True
        self.hk = dict(HOTKEY_DEFAULTS)

    def hk_label(self, name):
        return self.hk.get(name, "")

    def reason(self):
        return ""


def map_frame(tm, d, span, w, h, up=True):
    """Кадр карты для предпросмотра в лаунчере: тайлы вокруг точки d и стрелка грузовика."""
    img = tm.render(d["x"], d["z"], span, w, h, heading=d["heading"] if up else None)
    dr = ImageDraw.Draw(img)
    cx, cy = w // 2, h // 2
    ang = 0.0 if up else math.radians(d["heading"])
    pts = []
    for px, py in ((0, -13), (9, 10), (0, 5), (-9, 10)):
        pts.append((cx + px * math.cos(ang) + py * math.sin(ang), cy - px * math.sin(ang) + py * math.cos(ang)))
    dr.polygon(pts, fill=(255, 159, 26), outline=(16, 20, 26))
    return img


class Preview:
    """Предпросмотр окна оверлея внутри лаунчера. canvas кладёт вызывающий; draw() перерисовывает по текущим настройкам."""
    KINDS = (("dash", "Приборы", DashWin), ("mfd", "МФД", MfdWin), ("trip", "Рейс", TripWin), ("convoy", "Конвой", ConvoyWin))

    def __init__(self, parent, cfg, kind="dash", scale=1.0):
        self.cfg = cfg
        self.app = _PreviewApp(cfg)
        self.reader = DemoReader()
        self.parent = parent
        self.canvas = ScaledCanvas(parent, bg=BG, highlightthickness=0, bd=0)
        self.win = None
        self.scale = scale
        self.set_kind(kind)

    def set_kind(self, kind):
        cls = next((c for k, _, c in self.KINDS if k == kind), DashWin)
        w = cls.__new__(cls)
        w.app, w.canvas, w.k, w.visible, w._rs, w._drag = self.app, self.canvas, self.scale, True, False, None
        w.top = None
        w.w, w.h = w.size()
        w.build()
        if isinstance(w, MfdWin):
            w.poll_keys = lambda: None
            w.set_screen = lambda i, w=w: setattr(w, "screen", i % len(w.SCREENS))
        self.win = w
        self.canvas.k = self.scale
        self.canvas.configure(width=int(w.w * self.scale), height=int(w.h * self.scale))
        self.draw()

    def draw(self, screen=None):
        w = self.win
        if w is None:
            return
        self.app.cfg = dict(self.cfg, locked=True)
        apply_theme(self.cfg.get("theme", "amber"), self.cfg.get("win_style", "classic"))
        self.canvas.configure(bg=BG)
        if isinstance(w, DashWin):
            w._style = self.cfg.get("dash_style", "classic")
            w.w, w.h = w.size()
            self.canvas.configure(width=int(w.w * self.scale), height=int(w.h * self.scale))
        if screen is not None and isinstance(w, MfdWin):
            w.screen = screen % len(w.SCREENS)
        w.update(self.reader.read())


# ---------------------------------------------------------------------------
# приложение
# ---------------------------------------------------------------------------
class App:
    def __init__(self, demo=False, unlock=False):
        self.cfg = load_cfg()
        if unlock:
            self.cfg["locked"] = False
        self.root = tk.Tk()
        self.root.withdraw()
        self.demo = demo
        self.reader = DemoReader() if demo else Ets2Reader()
        self.tiles = TileMap() if HAVE_PIL else None
        self.route = RouteMgr(self)
        self.d = None
        self.wins = []
        self.convoy = DemoConvoy() if demo else ConvoyFeed(self.cfg["convoy_ids"])
        apply_theme(self.cfg.get("theme", "amber"), self.cfg.get("win_style", "classic"))
        self.hidden = False
        self.move_panel = None
        for key, cls in self.WINDOWS:
            if self.cfg[key]:
                self.make_window(key, cls)
        if not self.wins:
            self.cfg["win_dash"] = True
            self.make_window("win_dash", DashWin)
        self.hk = load_hotkeys()
        self._hk_vks = {n: parse_hotkey(t) for n, t in self.hk.items()}
        self._hk_down = {}
        self._hk_t = 0.0
        self.manual_hide = False
        self.running = True
        self._t_val = None
        self._t_chg = time.monotonic()
        self._out_since = None
        self._f8 = False
        self._cmd_t = 0.0
        self.write_state()
        self.sync_move_panel()
        self.tick()

    WINDOWS = (("win_mfd", MfdWin), ("win_map", MapWin), ("win_dash", DashWin), ("win_trip", TripWin), ("win_convoy", ConvoyWin))

    def make_window(self, key, cls):
        w = cls(self)
        w.CFG = key
        self.wins.append(w)
        return w

    def set_frame(self, key, lvl):
        """Фон/рамка окна: 100 сплошной … 25 почти прозрачный, 0 — без рамки."""
        fo = dict(self.cfg.get("frame_op") or {})
        if int(self.cfg.get("frame_all", -1)) >= 0:        # было «для всех»: разворачиваем в настройки окон и меняем одно
            fo = {k[4:]: int(self.cfg["frame_all"]) for k, _ in self.WINDOWS}
            self.cfg["frame_all"] = -1
        fo[key] = int(lvl)
        self.cfg["frame_op"] = fo
        save_cfg(self.cfg)
        for w in self.wins:
            if True:
                w.last_draw = 0.0
                w._t_upd = 0.0
                try:
                    w.canvas.delete("mv")
                except tk.TclError:
                    pass

    def set_window(self, key, on):
        """Включить/убрать окно на лету (крестик на окне, кнопки панели перемещения)."""
        on = bool(on)
        cur = [w for w in self.wins if getattr(w, "CFG", "") == key]
        if on == bool(cur):
            return
        self.cfg[key] = on
        save_cfg(self.cfg)
        if on:
            cls = dict(self.WINDOWS)[key]
            w = self.make_window(key, cls)
            if self.hidden:
                w.visible = False
                w.top.withdraw()
        else:
            for w in cur:
                self.wins.remove(w)
                w.destroy()
        self.refresh_move_panel()

    # ---- панель режима перемещения (как у остальных игр) ----
    def sync_move_panel(self):
        if self.cfg["locked"]:
            mp, self.move_panel = self.move_panel, None
            if mp is not None:
                try:
                    mp.destroy()
                except tk.TclError:
                    pass
        elif self.move_panel is None and not isinstance(self.root, type(None)):
            try:
                self.build_move_panel()
            except tk.TclError:
                self.move_panel = None

    def build_move_panel(self):
        mp = self.move_panel = tk.Toplevel(self.root)
        mp.overrideredirect(True)
        mp.attributes("-topmost", True)
        mp.configure(bg=GREEN)
        body = tk.Frame(mp, bg="#12171f")
        body.pack(padx=2, pady=2)
        f_s, f_b = ("Segoe UI", 9), ("Segoe UI", 9, "bold")
        l1 = tk.Label(body, text="РЕЖИМ ПЕРЕМЕЩЕНИЯ", bg="#12171f", fg=GREEN, font=f_b, cursor="fleur")
        l1.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        l2 = tk.Label(body, text="тяните окно · за край или угол — размер · красный крестик — убрать окно", bg="#12171f", fg="#8b95a5", font=f_s, cursor="fleur")
        l2.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 4), padx=6)

        def d_start(e):
            mp._dx, mp._dy = e.x_root - mp.winfo_x(), e.y_root - mp.winfo_y()

        def d_move(e):
            mp.geometry(f"+{e.x_root - mp._dx}+{e.y_root - mp._dy}")
        for wdg in (l1, l2, body, mp):                   # панель можно перетащить за заголовок и за фон
            wdg.bind("<ButtonPress-1>", d_start)
            wdg.bind("<B1-Motion>", d_move)
        self.mp_btns = {}
        for i, (key, name) in enumerate(MOVE_NAMES):
            b = tk.Label(body, text=name, font=f_s, width=12, pady=2, cursor="hand2")
            b.grid(row=2 + i // 3, column=i % 3, padx=2, pady=1)
            b.bind("<Button-1>", lambda e, k=key: self.set_window(k, not self.cfg.get(k, False)))
            self.mp_btns[key] = b
        done = tk.Label(body, text="Готово — закрепить окна", bg=GREEN, fg="#04210b", font=f_b, pady=3, cursor="hand2")
        done.grid(row=2 + (len(MOVE_NAMES) + 2) // 3, column=0, columnspan=3, sticky="ew", padx=4, pady=(5, 6))
        done.bind("<Button-1>", lambda e: self.toggle_lock())
        self.refresh_move_panel()
        mp.update_idletasks()
        w, h = mp.winfo_reqwidth(), mp.winfo_reqheight()
        mp.geometry(f"+{max(0, (self.root.winfo_screenwidth() - w) // 2)}+{max(0, (self.root.winfo_screenheight() - h) // 2)}")

    def refresh_move_panel(self):
        mp = self.move_panel
        if mp is None:
            return
        for key, b in self.mp_btns.items():
            if self.cfg.get(key, False):
                b.config(bg="#1f9d4f", fg="#ffffff")
            else:
                b.config(bg="#262d38", fg="#6b7585")
        try:
            mp.lift()
        except tk.TclError:
            pass

    def reason(self):
        if self.demo:
            return ""
        return "Жду ETS2…\n" + (getattr(self.reader, "err", "") or "запустите игру и загрузите профиль")

    def write_state(self):
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({"locked": bool(self.cfg["locked"]), "pid": os.getpid(), "connected": self.d is not None}, f)
        except OSError:
            pass

    def toggle_lock(self):
        self.cfg["locked"] = not self.cfg["locked"]
        save_cfg(self.cfg)
        for w in self.wins:
            w.apply_lock()
            w.last_draw = 0.0
            w._t_upd = 0.0
            w.draw_grip()
        self.write_state()
        self.sync_move_panel()

    def set_lock(self, val):
        if bool(val) != bool(self.cfg["locked"]):
            self.toggle_lock()

    def hk_label(self, name):
        return self.hk.get(name, HOTKEY_DEFAULTS.get(name, ""))

    def reset_positions(self):
        """Вернуть окна на экран: убрать сохранённые позиции и поставить стандартные."""
        self.cfg["pos"] = {}
        save_cfg(self.cfg)
        for w in self.wins:
            x, y = w.default_pos()
            w.top.geometry(f"+{int(x)}+{int(y)}")

    def toggle_manual_hide(self):
        self.manual_hide = not self.manual_hide

    def toggle_map_mode(self):
        self.cfg["map_heading_up"] = not self.cfg["map_heading_up"]
        save_cfg(self.cfg)

    def check_hotkey(self):
        if sys.platform != "win32":
            return
        now = time.monotonic()
        if now - self._hk_t < 0.03:
            return
        self._hk_t = now
        acts = {"lock": self.toggle_lock, "reset": self.reset_positions, "quit": self.quit, "hide": self.toggle_manual_hide,
                "tim": self.cycle_dash_style, "map": self.toggle_map_mode}
        gks = ctypes.windll.user32.GetAsyncKeyState
        for name, fn in acts.items():
            vks = self._hk_vks.get(name)
            if not vks:
                continue
            try:
                down = all(gks(v) & 0x8000 for v in vks)
            except Exception:                            # noqa: BLE001
                return
            if down and not self._hk_down.get(name):
                self._hk_down[name] = True
                fn()
                if not self.running:
                    return
            elif not down:
                self._hk_down[name] = False

    def check_cmd(self):
        now = time.monotonic()
        if now - self._cmd_t < 0.5:
            return
        self._cmd_t = now
        try:
            with open(CMD_PATH, encoding="utf-8") as f:
                cmd = f.read().strip().lower()
            os.remove(CMD_PATH)
        except OSError:
            return
        if cmd == "lock":
            self.set_lock(True)
        elif cmd == "unlock":
            self.set_lock(False)
        elif cmd == "reset":
            self.reset_positions()
        elif cmd == "quit":
            self.quit()

    def set_dash_style(self, name):
        self.cfg["dash_style"] = name
        save_cfg(self.cfg)

    def cycle_dash_style(self):
        names = [n for n, _, _ in DashWin.STYLES]
        cur = self.cfg.get("dash_style", "classic")
        self.set_dash_style(names[(names.index(cur) + 1) % len(names)] if cur in names else names[0])

    def toggle_hide_idle(self):
        self.cfg["hide_idle"] = not self.cfg["hide_idle"]
        save_cfg(self.cfg)

    def in_world(self, d):
        """Игрок в мире: плагин жив, игра не на паузе и кадры идут (в главном меню/загрузке время стоит)."""
        if self.demo:
            return True
        now = time.monotonic()
        if d is None:
            self._t_val = None
            return False
        t = d.get("time")
        if t != self._t_val:
            self._t_val, self._t_chg = t, now
        if not d.get("sdk", True) or d.get("paused"):
            return False
        return now - self._t_chg < 1.5

    def set_hidden(self, hide):
        if hide == self.hidden:
            return
        self.hidden = hide
        for w in self.wins:
            w.visible = not hide
            try:
                if hide:
                    w.top.withdraw()
                else:
                    w.top.deiconify()
                    w.top.attributes("-topmost", True)
                    w.apply_lock()
            except tk.TclError:
                pass

    def update_visibility(self, d):
        want_hide = self.manual_hide
        if want_hide:
            self._out_since = None
        elif self.cfg["hide_idle"] and self.cfg["locked"]:         # при перемещении окон всегда показываем
            if self.in_world(d):
                self._out_since = None
            else:
                now = time.monotonic()
                if self._out_since is None:
                    self._out_since = now
                want_hide = now - self._out_since > 0.6
        else:
            self._out_since = None
        self.set_hidden(want_hide)

    def tick(self):
        if not self.running:
            return
        try:
            d = self.reader.read()
            if (d is None) != (self.d is None):
                self.d = d
                self.write_state()
            self.d = d
            if self.tiles is not None and d is not None and self.tiles.allow_net:
                self.prefetch(d)
            self.update_visibility(d)
            if not self.hidden:
                now = time.monotonic()
                for w in self.wins:
                    if now - getattr(w, "_t_upd", 0.0) >= w.PERIOD:
                        w._t_upd = now
                        w.update(d)
                        w.draw_grip()
            self.check_hotkey()
            self.check_cmd()
            if self.move_panel is not None and time.monotonic() - getattr(self, "_mp_lift", 0.0) > 1.0:
                self._mp_lift = time.monotonic()
                self.move_panel.lift()
        except Exception as e:                           # noqa: BLE001
            print("ошибка:", repr(e), file=sys.stderr)
        self.root.after(15, self.tick)

    def prefetch(self, d):
        """Заранее просим тайлы вокруг грузовика на уровне, который понадобится карте."""
        now = time.monotonic()
        if now - getattr(self, "_pf", 0.0) < 1.0:
            return
        self._pf = now
        tm = self.tiles
        span = float(self.cfg["map_span"]) * 1.9
        z = tm.level_for(int(self.cfg["map_size"]) / span)
        px, py = tm.to_px(d["x"], d["z"], z)
        cx, cy = int(px // tm.ts), int(py // tm.ts)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                tm.tile(z, cx + dx, cy + dy)

    def quit(self):
        self.running = False
        self.convoy.alive = False
        try:
            os.remove(STATE_PATH)
        except OSError:
            pass
        for w in self.wins:
            w.destroy()
        if self.move_panel is not None:
            try:
                self.move_panel.destroy()
            except tk.TclError:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    ap = argparse.ArgumentParser(description="Yassno ETS2 — оверлей для Euro Truck Simulator 2")
    ap.add_argument("--demo", action="store_true", help="тестовая поездка без игры")
    ap.add_argument("--unlock", action="store_true", help="режим перемещения окон")
    ap.add_argument("--shot", help="сохранить скриншот окон через N секунд и выйти: путь.png (для проверки)")
    args = ap.parse_args()
    app = App(demo=args.demo, unlock=args.unlock)
    if args.shot:
        app.root.after(4000, lambda: (os.system(f"import -window root {args.shot}"), app.quit()))
    app.run()


if __name__ == "__main__":
    main()
